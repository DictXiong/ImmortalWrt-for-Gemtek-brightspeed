#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -Eeuo pipefail
umask 022
cd "$(dirname "$0")/../.."

layout=legacy
jobs=2
prepare_only=0
reuse_feeds=0
allow_dirty=0
while (($#)); do
 case "$1" in
  --layout) layout=${2:?}; shift 2;;
  --jobs) jobs=${2:?}; shift 2;;
  --prepare-only) prepare_only=1; shift;;
  --reuse-feeds) reuse_feeds=1; shift;;
  --allow-dirty) allow_dirty=1; shift;;
  *) echo 'usage: build-full.sh [--layout legacy|ubi] [--jobs N] [--prepare-only] [--reuse-feeds] [--allow-dirty]' >&2; exit 2;;
 esac
done
[[ $layout == legacy || $layout == ubi ]] || { echo 'Unknown layout' >&2; exit 2; }
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid jobs' >&2; exit 2; }
# This public build never consumes local deployment bundles or rootfs overlays.
[[ ! -d files ]] || { echo 'Refusing a files/ overlay in the public full build' >&2; exit 1; }
[[ $allow_dirty == 1 || -z $(git status --porcelain) ]] || {
 echo 'Commit the source first; --allow-dirty is only for explicitly uncommitted local trials' >&2
 exit 1
}
export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
cp configs/dicttech-feeds.conf feeds.conf
: "${WRTIX_SOURCE:?Set WRTIX_SOURCE to the clean wrtix checkout matching configs/wrtix-source.json}"
python3 scripts/dicttech/wrtix-source.py prepare --source "$WRTIX_SOURCE" \
 --lock configs/wrtix-source.json --output tmp/wrtix-sources --feeds-file feeds.conf \
 --metadata bin/dicttech-wrtix-source.json
if [[ $reuse_feeds == 0 ]]; then
 ./scripts/feeds update -a
else
 ./scripts/feeds update wrtix
fi
python3 scripts/dicttech/check-build.py --feeds-only >/dev/null
FEED_PATCHES_STRICT=1 bash scripts/apply-feed-patches.sh
# cp -p in feed install rules preserves source modes. Git does not report
# writable non-executable files as dirty, so prepare modes explicitly before
# packaging, including reused, pinned feed checkouts.
python3 scripts/dicttech/prepare-source-permissions.py
# Reused checkouts can be newly added feeds without an index (e.g. Glass).
# Source Makefile patches must also be reflected in generated feed metadata.
./scripts/feeds update -i -a
./scripts/feeds install -a
python3 scripts/dicttech/check-ui-source.py
bash scripts/fix-stale-golang-host.sh
cp configs/xr1710g-full.config .config
if [[ $layout == ubi ]]; then
 sed -i '/^CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g=y$/d' .config
 printf '%s\n' 'CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g-ubi=y' >>.config
fi
revision="$(git rev-parse --short=12 HEAD)"
profile_version=$(sed -n 's/^PKG_VERSION:=//p' package/dicttech/xr1710g-full/Makefile)
release_version=$(python3 - "$profile_version" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('xr_release', 'scripts/dicttech/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
version = json.load(open('configs/xr1710g-release.json'))['version']
if release.package_version(version) != sys.argv[1]:
    raise SystemExit('Full edition package version differs from the release lock')
print(version)
PY
)
version="$release_version-$revision"
[[ -z $(git status --porcelain) ]] || version+="-dirty"
printf 'CONFIG_VERSION_NUMBER="%s"\nCONFIG_VERSION_CODE="%s"\n' "$version" "$revision" >>.config
make defconfig RECURSIVE_DEP_IS_ERROR=1
python3 scripts/xr1710g/test-squeezelite-metadata.py --feed-revision "$(git -C feeds/packages rev-parse HEAD)"
python3 scripts/dicttech/test-cli-feed-metadata.py
python3 scripts/dicttech/check-build.py --layout "$layout" --output "bin/dicttech-$layout-build.json"
if [[ $prepare_only == 1 ]]; then exit 0; fi
make download -j"$jobs"
make world -j"$jobs" || make world -j1 V=s
python3 scripts/dicttech/check-ui-source.py --jsmin staging_dir/hostpkg/bin/jsmin \
 --output bin/targets/airoha/an7581/dicttech-ui-source.json
python3 scripts/dicttech/check-build.py --layout "$layout" --output "bin/targets/airoha/an7581/dicttech-$layout-build.json"
profile=gemtek_xr1710g
[[ $layout != ubi ]] || profile+=-ubi
prefix="xr1710g-full-$version-airoha-an7581-$profile"
image="bin/targets/airoha/an7581/$prefix-squashfs-sysupgrade.itb"
[[ -f $image ]] || { echo 'Expected current-build image is missing' >&2; exit 1; }
audit=$(mktemp -d "$PWD/tmp/dicttech-image.XXXXXX")
trap 'rm -rf -- "$audit"' EXIT
cp bin/dicttech-wrtix-source.json bin/dicttech-wrtix-source.tar bin/targets/airoha/an7581/
python3 scripts/dicttech/check-image.py --layout "$layout" --image "$image" \
 --rootfs-image-output "$audit/rootfs.squashfs" \
 --output "${image%.itb}.audit.json"
# /dev is mounted at runtime; audit the code/data without requiring mknod on
# the build host. The FIT/rootfs bytes (including /dev) were verified above.
staging_dir/host/bin/unsquashfs4 -no-progress -no-xattrs -excludes \
 -d "$audit/rootfs" "$audit/rootfs.squashfs" dev >"$audit/unsquashfs.log"
python3 scripts/dicttech/check-rootfs.py --rootfs "$audit/rootfs" \
 --manifest "bin/targets/airoha/an7581/$prefix.manifest" \
 --output "${image%.itb}.rootfs.audit.json"
python3 scripts/dicttech/check-image.py --layout "$layout" --image "$image" \
 --rootfs "$audit/rootfs" --output "${image%.itb}.audit.json"
python3 scripts/dicttech/check-packages.py \
 --manifest "bin/targets/airoha/an7581/$prefix.manifest" \
 --package-root bin/targets/airoha/an7581/packages \
 --package-root bin/packages/aarch64_cortex-a53 \
 --rootfs "$audit/rootfs" --apk staging_dir/host/bin/apk \
 --output-dir "$audit/packages" --output "${image%.itb}.packages.audit.json"
package_release="bin/targets/airoha/an7581/dicttech-packages/$prefix"
mkdir -p "${package_release%/*}"
# This path is reserved for generated, verified release packages, never inputs.
rm -rf -- "$package_release"
mv "$audit/packages" "$package_release"
python3 - "${image%.itb}.packages.audit.json" "$package_release" "$image" <<'PY'
import hashlib, json, sys
from pathlib import Path
report = Path(sys.argv[1])
data = json.loads(report.read_text())
data['release_package_directory'] = str(Path(sys.argv[2]).resolve())
data['image_sha256'] = hashlib.sha256(Path(sys.argv[3]).read_bytes()).hexdigest()
report.write_text(json.dumps(data, indent=2) + '\n')
PY
(
 cd bin/targets/airoha/an7581
 sha256sum -- "$prefix"-*.itb >dicttech-sha256sums
)
