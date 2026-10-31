#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Runs before netifd/dropbear/uhttpd start. Setup identity is random and persistent.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

CONTRACT=/usr/share/dicttech-ap/contract.json
MARKER=/etc/dicttech-ap/device.json
STATE=/etc/dicttech-ap/bootstrap-state.json
IDENTITY=/etc/dicttech-ap/bootstrap-identity.json
FACTORY=/usr/lib/dicttech-ap/factory
ACCEPTED=/etc/dicttech-ap/accepted.json
BOOT_VALIDATED=/tmp/dicttech-ap-firstboot-validated.json
MANIFEST=/etc/dicttech-ap/managed-files.json
BOOT_SNAPSHOT=/tmp/dicttech-ap-firstboot-snapshot
BOOT_DEFER=/tmp/dicttech-ap-bootstrap-defer
KEYLIFE=/usr/libexec/dicttech-ap/bootstrap-key

key_lifecycle() {
 [ ! -x "$KEYLIFE" ] || "$KEYLIFE" "$1"
}

note() { printf '%s\n' "dicttech-ap: $*" >&2; }

activate_root_shell() {
 # CLI setup is optional to recovery. A missing/failed customization must not
 # prevent installing or restoring the wired setup network.
 if [ -x /usr/libexec/dicttech-cli/activate-root-shell ]; then
  timeout 5 /usr/libexec/dicttech-cli/activate-root-shell || note 'CLI root shell activation failed; wired recovery remains available'
 fi
}

defer_current_boot() {
 local boot deferred
 [ -f "$BOOT_DEFER" ] && [ ! -L "$BOOT_DEFER" ] || return 1
 IFS= read -r boot < /proc/sys/kernel/random/boot_id
 IFS= read -r deferred < "$BOOT_DEFER" || return 1
 [ "$deferred" = "$boot" ]
}

prepare_boot() {
 local boot tmp=$BOOT_DEFER.$$.tmp
 [ ! -L "$BOOT_DEFER" ] && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
 IFS= read -r boot < /proc/sys/kernel/random/boot_id
 umask 077
 printf '%s\n' "$boot" > "$tmp" || return 1
 chmod 600 "$tmp" || return 1
 mv "$tmp" "$BOOT_DEFER" || return 1
 prepare
}

finalize_boot() {
 # S13 is the only boot-time accept point. Older exported archives may still
 # contain a S10 uci-defaults shim invoking ordinary finalize; it is deferred.
 [ ! -L "$BOOT_DEFER" ] || return 1
 rm -f "$BOOT_DEFER" || return 1
 finalize
}

managed_path() {
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
valid_marker() {
 local hash board section material ssid_rows iface rows name expected actual
 private_file "$MARKER" && [ -f "$CONTRACT" ] && [ ! -L "$CONTRACT" ] || return 1
 hash=$(sha256sum "$CONTRACT"); hash=${hash%% *}
 board=$(cat /tmp/sysinfo/board_name) || return 1
 jq -e --arg hash "$hash" --arg board "$board" '
   type=="object" and .schema_version==3 and .profile=="openwrt-config-v3" and
   .contract_sha256==$hash and
   (.platform.board_names|type=="array" and length>=1 and length<=64 and
      all(.[];type=="string") and index($board)!=null) and
   (.device_id|type=="string" and length>=1 and length<=64) and
   (.config_fingerprint|type=="string" and length==64) and
   (.ssids|type=="array" and length<=256) and
   ([.ssids[].key]|length==(unique|length)) and
   ([.ssids[].section]|length==(unique|length)) and
   ([.ssids[].iface]|length==(unique|length)) and
   (.services|type=="object") and
   (.ssids|all(.[];
      (.key|type=="string" and length>=1 and length<=48) and
      (.section|type=="string" and length>=1 and length<=255 and
        (explode|all(.[]; (.>=48 and .<=57) or (.>=65 and .<=90) or (.>=97 and .<=122) or .==95))) and
      (.iface|type=="string" and length>=1 and length<=15 and
        (explode|all(.[]; (.>=48 and .<=57) or (.>=65 and .<=90) or (.>=97 and .<=122) or .==95 or .==45 or .==46 or .==58))) and
      (.mlo|type=="boolean") and
      (.bands|type=="array" and length>=1 and length<=3 and length==(unique|length) and
        all(.[]; .=="2g" or .=="5g" or .=="6g")) and
      (if .mlo then (.bands|length)>=2 else (.bands|length)==1 end)))
 ' "$MARKER" >/dev/null 2>&1 || return 1
 # A producer ledger defines the resource set. No SSID role, VLAN, template,
 # service list or minimum file inventory is implied by the firmware.
 validate_manifest_shape "$MANIFEST" || return 1
 # Lifecycle metadata is immutable even when accepted UCI/content drift is
 # allowed. A copied fingerprint string cannot authenticate changed policy.
 rows=$(jq -r '.files[]|select(.path=="etc/dicttech-ap/device.json" or .path=="etc/dicttech-ap/root-password-hash")|[.path,.sha256]|@tsv' "$MANIFEST") || return 1
 while IFS="$(printf '\t')" read -r name expected; do
  actual=$(sha256sum "/$name"); actual=${actual%% *}
  [ "$actual" = "$expected" ] || return 1
 done <<IMMUTABLE_ROWS
$rows
IMMUTABLE_ROWS
 private_file /etc/dicttech-ap/root-password-hash || return 1
 [ "$(wc -l < /etc/dicttech-ap/root-password-hash | tr -d ' ')" = 1 ] || return 1
 material=$(cat /etc/dicttech-ap/root-password-hash)
 case "$material" in '$6$'*|'$5$'*|'$y$'*) ;; *) return 1;; esac
 case "$material" in *:*|*' '*|*"$(printf '\t')"*) return 1;; esac
 ssid_rows=$(jq -r '.ssids[]|[.section,.iface]|@tsv' "$MARKER") || return 1
 while IFS="$(printf '\t')" read -r section iface; do
  [ -n "$section" ] || continue
  [ "$(uci -q get "wireless.$section")" = wifi-iface ] || return 1
  [ "$(uci -q get "wireless.$section.ifname")" = "$iface" ] || return 1
 done <<SSID_ROWS
$ssid_rows
SSID_ROWS
 # A factory fallback must never become accepted merely because it recreated
 # a missing file. Existing acceptance grants apply only outside setup state.
 if [ -f /tmp/dicttech-ap-bootstrap-isolated ]; then validate_manifest; return; fi
 binding_matches "$ACCEPTED" || binding_matches "$BOOT_VALIDATED" || validate_manifest
}

binding_matches() {
 local digest boot require_boot=${2:-false}
 [ -s "$1" ] && [ ! -L "$1" ] || return 1
 [ -s "$MANIFEST" ] && [ ! -L "$MANIFEST" ] || return 1
 digest=$(sha256sum "$MANIFEST"); digest=${digest%% *}
 IFS= read -r boot < /proc/sys/kernel/random/boot_id
 [ "$1" != "$BOOT_VALIDATED" ] || require_boot=true
 jq -e --slurpfile marker "$MARKER" --arg manifest "$digest" --arg boot "$boot" --argjson require_boot "$require_boot" '
   .schema_version==3 and .profile=="openwrt-config-v3" and
   .device_id==$marker[0].device_id and
   .contract_sha256==$marker[0].contract_sha256 and
   .config_fingerprint==$marker[0].config_fingerprint and
   .manifest_sha256==$manifest and
   ($require_boot==false or .validated_boot_id==$boot)
 ' "$1" >/dev/null 2>&1
}

write_binding() {
 local dest=$1 tmp=$1.$$.tmp digest boot
 [ ! -L "$dest" ] && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
 umask 077
 digest=$(sha256sum "$MANIFEST"); digest=${digest%% *}
 IFS= read -r boot < /proc/sys/kernel/random/boot_id
 jq --arg manifest "$digest" --arg boot "$boot" \
  '{schema_version,profile,device_id,contract_sha256,config_fingerprint,manifest_sha256:$manifest,validated_boot_id:$boot}' "$MARKER" > "$tmp"
 chmod 600 "$tmp"
 mv "$tmp" "$dest"
}

validate_manifest_shape() {
 local manifest=$1 base=${2:-} rows name mode expected actual
 private_file "$manifest" || return 1
 jq -e '
   .schema_version==3 and .profile=="openwrt-config-v3" and
   (.files|type=="array" and length>=2 and length<=128) and
   (.files|all(.[]; (.path|type=="string" and length>=1 and length<=4096) and (.mode as $mode|["600","644","700","755"]|index($mode))!=null and
      (.sha256|type=="string" and length==64 and (explode|all(.[];(.>=48 and .<=57) or (.>=97 and .<=102)))))) and
   ([.files[].path]|length==(unique|length)) and
   (["etc/dicttech-ap/device.json","etc/dicttech-ap/root-password-hash"]-[.files[].path]|length==0)
 ' "$manifest" >/dev/null || return 1
 rows=$(jq -r '.files[]|[.path,.mode]|@tsv' "$manifest") || return 1
 while IFS="$(printf '\t')" read -r name mode; do
  managed_path "$name" || return 1
  [ -f "$base/$name" ] && [ ! -L "$base/$name" ] || return 1
  case "$mode" in 600) expected=-rw-------:0;; 644) expected=-rw-r--r--:0;; 700) expected=-rwx------:0;; 755) expected=-rwxr-xr-x:0;; *) return 1;; esac
  actual=$(ls -nd "$base/$name" | awk '{print $1 ":" $3}') || return 1
  [ "$actual" = "$expected" ] || return 1
 done <<RESOURCE_ROWS
$rows
RESOURCE_ROWS
}
validate_manifest() {
 local manifest=${1:-$MANIFEST} base=${2:-} rows name expected actual
 validate_manifest_shape "$manifest" "$base" || return 1
 rows=$(jq -r '.files[]|[.path,.sha256]|@tsv' "$manifest") || return 1
 printf '%s\n' "$rows" | while IFS="$(printf '\t')" read -r name expected; do
  actual=$(sha256sum "$base/$name"); actual=${actual%% *}
  [ "$actual" = "$expected" ] || return 1
 done
}

snapshot_ready() {
 local dir=${1:-$BOOT_SNAPSHOT}
 [ -d "$dir" ] && [ ! -L "$dir" ] || return 1
 binding_matches "$dir/binding.json" true || return 1
 cmp -s "$MANIFEST" "$dir/managed-files.json" || return 1
 validate_manifest "$dir/managed-files.json" "$dir/files"
}

save_boot_snapshot() {
 local tmp=$BOOT_SNAPSHOT.$$.tmp rows name
 snapshot_ready && return 0
 validate_manifest || return 1
 [ ! -L "$BOOT_SNAPSHOT" ] && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
 umask 077
 mkdir -p "$tmp/files"
 chmod 700 "$tmp" "$tmp/files"
 rows=$(jq -r '.files[].path' "$MANIFEST") || return 1
 for name in $rows; do
  mkdir -p "$tmp/files/${name%/*}"
  cp -p "/$name" "$tmp/files/$name" || return 1
 done
 cp -p "$MANIFEST" "$tmp/managed-files.json"
 write_binding "$tmp/binding.json"
 snapshot_ready "$tmp" || { rm -rf "$tmp"; return 1; }
 [ ! -d "$BOOT_SNAPSHOT" ] || rm -rf "$BOOT_SNAPSHOT"
 mv "$tmp" "$BOOT_SNAPSHOT"
 write_binding "$BOOT_VALIDATED"
}

restore_boot_snapshot() {
 local rows name destination temp
 snapshot_ready || return 1
 rows=$(jq -r '.files[].path' "$BOOT_SNAPSHOT/managed-files.json") || return 1
 # Check every destination before mutating the first one.
 for name in $rows; do [ ! -L "/$name" ] || return 1; done
 clear_managed_deltas || return 1
 for name in $rows; do
  destination=/$name; temp=$destination.dicttech-firstboot.$$
  [ ! -e "$temp" ] && [ ! -L "$temp" ] || return 1
  mkdir -p "${destination%/*}" || return 1
  cp -p "$BOOT_SNAPSHOT/files/$name" "$temp" || return 1
  mv "$temp" "$destination" || return 1
 done
 validate_manifest
}

clear_managed_deltas() {
 local directory=/tmp/.uci name paths
 [ ! -L "$directory" ] || return 1
 [ -e "$directory" ] || return 0
 [ -d "$directory" ] || return 1
 # Only packages named by the producer ledger are cleared. Unrelated saved
 # UCI deltas never become part of a managed boot commit.
 if [ -f "$MANIFEST" ] && [ ! -L "$MANIFEST" ]; then
  paths=$(jq -r '.files[]?.path|select(startswith("etc/config/"))' "$MANIFEST") || return 1
 else
  # Factory bootstrap is a separate, fixed uninitialized recovery contract.
  load_factory || return 1
  paths=$(factory_config_paths) || return 1
 fi
 for name in $paths; do
  managed_path "$name" || return 1
  name=${name#etc/config/}; case "$name" in ''|*/*) return 1;; esac
  rm -f "$directory/$name" || return 1
  [ ! -e "$directory/$name" ] && [ ! -L "$directory/$name" ] || return 1
 done
}

quarantine() {
 local parent=/root/dicttech-ap-quarantine boot tmp dest
 [ ! -L "$parent" ] || return 1
 umask 077
 mkdir -p "$parent"
 chmod 700 "$parent"
 IFS= read -r boot < /proc/sys/kernel/random/boot_id
 dest=$parent/$boot.tar.gz
 if [ ! -e "$dest" ]; then
  tmp=$parent/.$boot.$$.tmp
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  set -- etc/config etc/dicttech-ap etc/shadow
  [ ! -d /etc/dropbear ] || set -- "$@" etc/dropbear
  if [ -e /tmp/.uci ] || [ -L /tmp/.uci ]; then set -- "$@" tmp/.uci; fi
  if ! tar -czf "$tmp" -C / "$@"; then rm -f "$tmp"; return 1; fi
  chmod 600 "$tmp"
  mv "$tmp" "$dest"
 fi
 [ -f "$dest" ] && [ ! -L "$dest" ] || return 1
 note "invalid managed marker; original configuration saved in private quarantine"
}

emergency_lock() {
 # A failed backup must never silently expose an invalid preseed. Keep originals
 # untouched and block L2/L3 traffic. This exceptional state requires repair.
 printf '%s\n' quarantine_failed > /tmp/dicttech-ap-bootstrap-error
 for table in 'inet dicttech_bootstrap_lock' 'bridge dicttech_bootstrap_lock'; do
  # Both arguments are fixed constants, never configuration data.
  set -- $table
  nft list table "$1" "$2" >/dev/null 2>&1 && continue
  nft -f - <<EOF
table $1 $2 {
 chain input { type filter hook input priority -400; policy drop; }
 chain forward { type filter hook forward priority -400; policy drop; }
 chain output { type filter hook output priority -400; policy drop; }
}
EOF
 done
 note 'quarantine failed; emergency traffic block installed, manual repair required'
}

disable_wireless() {
 local section sections
 [ -f /etc/config/wireless ] || printf '# DictTech: new radios remain disabled until provisioned.\n' > /etc/config/wireless
 # wifi config runs before uci-defaults; disable every generated device/interface.
 sections=$(uci -q show wireless) || return 1
 for section in $(printf '%s\n' "$sections" | sed -n 's/^wireless\.\([^.=]*\)=wifi-\(device\|iface\)$/\1/p'); do
  uci -q set "wireless.$section.disabled=1" || return 1
 done
 uci -q commit wireless
}

private_file() {
 [ -f "$1" ] && [ ! -L "$1" ] &&
  [ "$(ls -nd "$1" | awk '{print $1 ":" $3}')" = '-rw-------:0' ]
}

load_factory() {
 # The image package selects immutable platform code/assets. A producer cannot
 # replace this adapter through managed resources. Check board compatibility
 # only for factory setup; a valid configured preseed has no such dependency.
 [ -f "$FACTORY/adapter.sh" ] && [ ! -L "$FACTORY/adapter.sh" ] || return 1
 . "$FACTORY/adapter.sh"
 factory_supported || { note 'factory adapter does not support this board'; return 1; }
}

setup_files_match() { load_factory && factory_files_match; }
check_setup() { load_factory && factory_check_setup; }
isolate() {
 load_factory || { emergency_lock; return 1; }
 factory_isolate "$1"
}

prepare() {
 activate_root_shell
 [ ! -f /tmp/dicttech-ap-bootstrap-isolated ] || return 0
 if [ -e "$MARKER" ] || [ -L "$MARKER" ]; then
  key_lifecycle managed
  if valid_marker; then
   # Save the exact intended bytes before stock migrations normalize ULA/DUID.
   if ! binding_matches "$ACCEPTED" && ! save_boot_snapshot; then
    quarantine || { emergency_lock; return 1; }
    isolate boot_snapshot_failed
    return 1
   fi
   return 0
  fi
  quarantine || { emergency_lock; return 1; }
  isolate invalid_marker
 else
  isolate no_marker
 fi
}

finalize() {
 defer_current_boot && return 0
 activate_root_shell
 if ! binding_matches "$ACCEPTED" && binding_matches "$BOOT_VALIDATED"; then
  if ! restore_boot_snapshot; then
   quarantine || { emergency_lock; return 1; }
   isolate invalid_boot_snapshot
   return 1
  fi
 fi
 if valid_marker; then
  key_lifecycle managed
  rm -f /tmp/dicttech-ap-bootstrap-isolated
  if ! binding_matches "$ACCEPTED"; then
   /usr/libexec/dicttech-ap/transaction sync-root-password
   write_binding "$ACCEPTED"
  fi
  /usr/lib/dicttech-ap/services.sh links
  rm -f "$BOOT_VALIDATED"
  [ ! -d "$BOOT_SNAPSHOT" ] || rm -rf "$BOOT_SNAPSHOT"
  return
 fi
 # A corrupt managed candidate must not inherit the original no-marker grant,
 # even when the setup stamp still exists from before apply.
 if [ -e "$MARKER" ] || [ -L "$MARKER" ]; then key_lifecycle retire; fi
 if [ -f /tmp/dicttech-ap-bootstrap-isolated ]; then
  # S10 uci-defaults may normalize setup network bytes after S08. Restore the
  # selected setup contract at S13, before any networking/management consumer.
  # Healthy repeated finalization remains read-only.
  if ! setup_files_match; then
   local reason
   reason=$(jq -r '.reason // "invalid_marker"' "$STATE" 2>/dev/null || true)
   case "$reason" in no_marker|invalid_marker|boot_snapshot_failed|invalid_boot_snapshot|identity_invalid) ;; *) reason=invalid_marker;; esac
   isolate "$reason" || return 1
  fi
  return 0
 fi
 prepare
 [ ! -f /tmp/dicttech-ap-bootstrap-isolated ] || return 0
 valid_marker || return 1
 /usr/libexec/dicttech-ap/transaction sync-root-password
 write_binding "$ACCEPTED"
 /usr/lib/dicttech-ap/services.sh links
 rm -f "$BOOT_VALIDATED"
 [ ! -d "$BOOT_SNAPSHOT" ] || rm -rf "$BOOT_SNAPSHOT"
}

case "${1:-}" in
 check-setup) check_setup;;
 prepare) prepare;;
 finalize) finalize;;
 prepare-boot) prepare_boot;;
 finalize-boot) finalize_boot;;
 *) note 'usage: bootstrap.sh prepare|finalize|prepare-boot|finalize-boot|check-setup'; exit 2;;
esac
