#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -Eeuo pipefail
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
  *) echo 'usage: build-ap.sh [--layout legacy|ubi] [--jobs N] [--prepare-only] [--reuse-feeds] [--allow-dirty]' >&2; exit 2;;
 esac
done
[[ $layout == legacy || $layout == ubi ]] || { echo 'Unknown layout' >&2; exit 2; }
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid jobs' >&2; exit 2; }
# This public build never consumes local deployment bundles or rootfs overlays.
[[ ! -d files ]] || { echo 'Refusing a files/ overlay in the public AP build' >&2; exit 1; }
[[ $allow_dirty == 1 || -z $(git status --porcelain) ]] || {
 echo 'Commit the source first; --allow-dirty is only for explicitly uncommitted local trials' >&2
 exit 1
}
export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
cp configs/dicttech-feeds.conf feeds.conf
if [[ $reuse_feeds == 0 ]]; then
 ./scripts/feeds update -a
fi
python3 scripts/dicttech/check-build.py --feeds-only >/dev/null
FEED_PATCHES_STRICT=1 bash scripts/apply-feed-patches.sh
# Reused checkouts can be newly added feeds without an index (e.g. Glass).
# Source Makefile patches must also be reflected in generated feed metadata.
./scripts/feeds update -i -a
./scripts/feeds install -a
python3 scripts/dicttech/check-ui-source.py
bash scripts/fix-stale-golang-host.sh
cp configs/dicttech-xr1710g-ap.config .config
if [[ $layout == ubi ]]; then
 sed -i '/^CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g=y$/d' .config
 printf '%s\n' 'CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g-ubi=y' >>.config
fi
revision="$(git rev-parse --short=12 HEAD)"
version="1.0.0-$revision"
[[ -z $(git status --porcelain) ]] || version+="-dirty"
printf 'CONFIG_VERSION_NUMBER="%s"\nCONFIG_VERSION_CODE="%s"\n' "$version" "$revision" >>.config
make defconfig
python3 scripts/dicttech/check-build.py --layout "$layout" --output "bin/dicttech-$layout-build.json"
if [[ $prepare_only == 1 ]]; then exit 0; fi
make download -j"$jobs"
make world -j"$jobs"
python3 scripts/dicttech/check-build.py --layout "$layout" --output "bin/targets/airoha/an7581/dicttech-$layout-build.json"
profile=gemtek_xr1710g
[[ $layout != ubi ]] || profile+=-ubi
prefix="dicttech-ap-$version-airoha-an7581-$profile"
image="bin/targets/airoha/an7581/$prefix-squashfs-sysupgrade.itb"
[[ -f $image ]] || { echo 'Expected current-build image is missing' >&2; exit 1; }
audit=$(mktemp -d "$PWD/tmp/dicttech-image.XXXXXX")
trap 'rm -rf -- "$audit"' EXIT
python3 scripts/dicttech/check-ap-image.py --layout "$layout" --image "$image" \
 --rootfs-image-output "$audit/rootfs.squashfs" \
 --output "${image%.itb}.audit.json"
staging_dir/host/bin/unsquashfs4 -no-progress -no-xattrs \
 -d "$audit/rootfs" "$audit/rootfs.squashfs" >"$audit/unsquashfs.log"
python3 scripts/dicttech/check-ap-rootfs.py --rootfs "$audit/rootfs" \
 --manifest "bin/targets/airoha/an7581/$prefix.manifest" \
 --output "${image%.itb}.rootfs.audit.json"
python3 scripts/dicttech/check-ap-image.py --layout "$layout" --image "$image" \
 --rootfs "$audit/rootfs" --output "${image%.itb}.audit.json"
(
 cd bin/targets/airoha/an7581
 sha256sum -- "$prefix"-*.itb >dicttech-sha256sums
)
