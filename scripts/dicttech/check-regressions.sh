#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/../.."
export WRTMESH_SOURCE="${WRTMESH_SOURCE:-$PWD/../wrtmesh}"

usage() {
 printf '%s\n' 'usage: check-regressions.sh host | rootfs ROOTFS REPORT_DIRECTORY' >&2
 exit 2
}
[[ $(id -u) == 0 ]] || { echo 'Run the isolated regression fixtures as root.' >&2; exit 2; }

case ${1:-} in
 host)
  [[ $# == 1 ]] || usage
  for test in \
   scripts/dicttech/tests/test_release.py \
   scripts/dicttech/tests/test_full_defaults.py \
   scripts/dicttech/tests/test_full_cli.py \
   scripts/dicttech/tests/test_wrtix_source.py \
   scripts/dicttech/tests/test_image.py \
   scripts/dicttech/tests/test_rootfs.py \
   scripts/dicttech/tests/test_ui_source.py \
   scripts/dicttech/tests/test_packages.py \
   scripts/dicttech/tests/test_source_permissions.py \
   "$WRTMESH_SOURCE/tests/check-xr-roaming-package.py" \
   "$WRTMESH_SOURCE/tests/test-xr-roaming.py"; do
   printf 'Running %s\n' "$test"
   python3 "$test"
  done
  ;;
 rootfs)
  [[ $# == 3 ]] || usage
  rootfs=$(realpath "$2")
  reports=$(realpath -m "$3")
  [[ -d $rootfs && -x $rootfs/usr/bin/ucode ]] || { echo 'A built target rootfs is required.' >&2; exit 2; }
  mkdir -p "$reports"
  ucode=$(python3 - "$rootfs" <<'PY'
import shlex, sys
root = sys.argv[1]
print(shlex.join(['qemu-aarch64', '-L', root, root + '/usr/bin/ucode', '-L', root + '/usr/lib/ucode']))
PY
  )
  python3 scripts/dicttech/tests/wifihistory/test_history.py --ucode "$ucode" \
   2>&1 | tee "$reports/dicttech-history-tests.log"
  python3 "$WRTMESH_SOURCE/tests/test-xr-mesh.py" --c7-rootfs "$rootfs" \
   2>&1 | tee "$reports/dicttech-wrtmesh-tests.log"
  python3 scripts/dicttech/tests/factory/test_factory.py --rootfs "$rootfs" \
   2>&1 | tee "$reports/dicttech-factory-tests.log"
  python3 scripts/dicttech/tests/umdns/test_config.py --rootfs "$rootfs" \
   2>&1 | tee "$reports/dicttech-umdns-tests.log"
  ;;
 *) usage;;
esac
