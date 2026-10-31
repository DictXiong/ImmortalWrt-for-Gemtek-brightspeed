#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/../.."

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
   package/dicttech/cli-profile/tests/test_cli_profile.py \
   scripts/dicttech/test-ap-config.py \
   scripts/dicttech/tests/test_config_schema.py \
   scripts/dicttech/test-ap-bootstrap.py \
   scripts/dicttech/tests/test_managed_backup.py \
   scripts/dicttech/tests/ap-deploy/test_transaction.py \
   scripts/dicttech/tests/ap-deploy/test_export_identity.py \
   scripts/dicttech/test-ap-verify.py \
   scripts/dicttech/tests/test_ap_image.py \
   scripts/dicttech/tests/test_ap_rootfs.py \
   scripts/dicttech/tests/test_ui_source.py \
   scripts/dicttech/tests/test_ap_packages.py \
   scripts/dicttech/tests/test_source_permissions.py \
   scripts/dicttech/check-xr-roaming-package.py \
   scripts/dicttech/test-xr-roaming.py; do
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
  python3 scripts/dicttech/test-ap-bootstrap-c7.py --c7-rootfs "$rootfs" \
   | tee "$reports/dicttech-bootstrap-c7-tests.json"
  ucode=$(python3 - "$rootfs" <<'PY'
import shlex, sys
root = sys.argv[1]
print(shlex.join(['qemu-aarch64', '-L', root, root + '/usr/bin/ucode', '-L', root + '/usr/lib/ucode']))
PY
  )
  python3 scripts/dicttech/tests/wifihistory/test_history.py --ucode "$ucode" \
   2>&1 | tee "$reports/dicttech-history-tests.log"
  python3 scripts/dicttech/tests/factory/test_factory.py --rootfs "$rootfs" \
   2>&1 | tee "$reports/dicttech-factory-tests.log"
  ;;
 *) usage;;
esac
