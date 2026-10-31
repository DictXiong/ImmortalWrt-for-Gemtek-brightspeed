#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Extend sysupgrade's existing backup list with producer-owned resource paths.
# Source-only hook: it never changes the selected configuration or firmware.

dicttech_managed_fail() {
 printf '%s\n' 'dicttech-config: invalid managed resource inventory; refusing incomplete backup' >&2
 exit 1
}
dicttech_managed_path() {
 local rel=$1 rest next current=
 [ "${#rel}" -le 4096 ] || return 1
 case "$rel" in ''|/*|*/|*//*|*[!A-Za-z0-9_./+-]*) return 1;; esac
 rest=$rel
 while [ -n "$rest" ]; do
  next=${rest%%/*}; [ "${#next}" -le 255 ] || return 1; case "$next" in .|..) return 1;; esac
  current=$current/$next; [ ! -L "$current" ] || return 1
  case "$rest" in */*) rest=${rest#*/};; *) rest=;; esac
 done
 case "$rel" in
  etc/shadow|etc/passwd|etc/group|etc/gshadow|etc/resolv.conf|etc/ssh|etc/ssh/*|etc/dropbear/dropbear_*|etc/rc.d|etc/rc.d/*|etc/openwrt-deploy|etc/openwrt-deploy/*|etc/dicttech-ap-bootstrap-key|etc/dicttech-ap-bootstrap-key/*) return 1;;
  etc/dicttech-ap/device.json|etc/dicttech-ap/root-password-hash|etc/dropbear/authorized_keys) return 0;;
  etc/dicttech-ap|etc/dicttech-ap/*) return 1;;
  etc/init.d/openwrt-managed-*|usr/libexec/openwrt-managed/*) return 0;;
  etc/init.d/*) return 1;;
  etc/*) return 0;;
  *) return 1;;
 esac
}
dicttech_add_managed_conffiles() {
 local destination=$1 ledger=/etc/dicttech-ap/managed-files.json marker=/etc/dicttech-ap/device.json rows name mode expected actual temporary
 if [ ! -e "$ledger" ] && [ ! -L "$ledger" ]; then
  [ ! -e "$marker" ] && [ ! -L "$marker" ] || dicttech_managed_fail
  return 0
 fi
 [ -f "$destination" ] && [ ! -L "$destination" ] || dicttech_managed_fail
 [ -f "$ledger" ] && [ ! -L "$ledger" ] && [ "$(ls -nd "$ledger" | awk '{print $1 ":" $3}')" = '-rw-------:0' ] || dicttech_managed_fail
 jq -e '
  .schema_version==3 and .profile=="openwrt-config-v3" and
  (.files|type=="array" and length>=2 and length<=128) and
  (.files|all(.[]; (.path|type=="string" and length>=1 and length<=4096) and
   (.mode as $mode|["600","644","700","755"]|index($mode))!=null and
   (.sha256|type=="string" and length==64 and (explode|all(.[];(.>=48 and .<=57) or (.>=97 and .<=102)))))) and
  ([.files[].path]|length==(unique|length)) and
  (["etc/dicttech-ap/device.json","etc/dicttech-ap/root-password-hash"]-[.files[].path]|length==0)
 ' "$ledger" >/dev/null || dicttech_managed_fail
 rows=$(jq -r '.files[]|[.path,.mode]|@tsv' "$ledger") || dicttech_managed_fail
 temporary=$(mktemp) || dicttech_managed_fail
 while IFS="$(printf '\t')" read -r name mode; do
  dicttech_managed_path "$name" && [ -f "/$name" ] && [ ! -L "/$name" ] || { rm -f "$temporary"; dicttech_managed_fail; }
  case "$mode" in 600) expected=-rw-------:0;; 644) expected=-rw-r--r--:0;; 700) expected=-rwx------:0;; 755) expected=-rwxr-xr-x:0;; *) rm -f "$temporary"; dicttech_managed_fail;; esac
  actual=$(ls -nd "/$name" | awk '{print $1 ":" $3}') || { rm -f "$temporary"; dicttech_managed_fail; }
  [ "$actual" = "$expected" ] || { rm -f "$temporary"; dicttech_managed_fail; }
  printf '/%s\n' "$name" >> "$temporary"
 done <<DICTTECH_RESOURCE_ROWS
$rows
DICTTECH_RESOURCE_ROWS
 # Validation is complete before the caller's backup list is touched. Drifted
 # resource content is deliberately preserved, just like standard UCI backups.
 printf '%s\n' "$ledger" >> "$temporary" || { rm -f "$temporary"; dicttech_managed_fail; }
 while IFS= read -r name; do
  if ! grep -Fxq -- "$name" "$destination" && ! grep -Fxq -- "${name#/}" "$destination"; then
   printf '%s\n' "$name" >> "$destination" || { rm -f "$temporary"; dicttech_managed_fail; }
  fi
 done < "$temporary"
 rm -f "$temporary"
}
[ -z "${sysupgrade_init_conffiles:-}" ] || append sysupgrade_init_conffiles dicttech_add_managed_conffiles
