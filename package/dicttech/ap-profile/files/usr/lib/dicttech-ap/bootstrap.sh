#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Runs before netifd/dropbear/uhttpd start. Setup identity is random and persistent.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

DEFAULTS=/usr/share/dicttech-ap/defaults.json
MARKER=/etc/dicttech-ap/device.json
STATE=/etc/dicttech-ap/bootstrap-state.json
IDENTITY=/etc/dicttech-ap/bootstrap-identity.json
SETUP=/usr/lib/dicttech-ap/setup
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

valid_marker() {
 local hash name section material ssid_rows iface
 [ -f "$MARKER" ] && [ ! -L "$MARKER" ] && [ -f "$DEFAULTS" ] || return 1
 hash=$(sha256sum "$DEFAULTS"); hash=${hash%% *}
 jq -e --arg hash "$hash" '
   type=="object" and .schema_version==2 and .profile=="dicttech-ap-v2" and
   .defaults_sha256==$hash and
   (.device_id|type=="string" and length>=1 and length<=64) and
   (.config_fingerprint|type=="string" and length==64) and
   (.ssids|type=="array" and length>=3 and length<=17) and
   .ssids[0].key=="main" and .ssids[1].key=="guest" and .ssids[-1].key=="iot" and
   ([.ssids[].key]|length==(unique|length)) and
   ([.ssids[].section]|length==(unique|length)) and
   ([.ssids[].iface]|length==(unique|length)) and
   (.ssids|all(.[];
      (.key|type=="string" and length>=1 and length<=48) and
      (.section|type=="string" and length>=1 and length<=48 and
        (explode|all(.[]; (.>=48 and .<=57) or (.>=65 and .<=90) or (.>=97 and .<=122) or .==95))) and
      (.iface|type=="string" and length>=1 and length<=15 and
        (explode|all(.[]; (.>=48 and .<=57) or (.>=65 and .<=90) or (.>=97 and .<=122) or .==95 or .==45))) and
      (.mlo|type=="boolean") and
      (.bands|type=="array" and length>=1 and length<=3 and length==(unique|length) and
        all(.[]; .=="2g" or .=="5g" or .=="6g")) and
      (if .mlo then (.bands|length)>=2 else (.bands|length)==1 end)))
 ' "$MARKER" >/dev/null 2>&1 || return 1
 # A marker alone is not a complete deployment. Missing/truncated config must
 # never fall through to upstream router defaults on the next boot.
 for name in network wireless firewall dhcp system dropbear uhttpd xr_roaming fan cpufreq airoha_npu; do
  [ -s "/etc/config/$name" ] && [ ! -L "/etc/config/$name" ] || return 1
 done
 for name in /etc/dicttech-ap/root-password-hash /etc/xr-guest-vlan192.nft /etc/sysctl.d/13-nf-bridge.conf; do
  [ -s "$name" ] && [ ! -L "$name" ] || return 1
 done
 [ "$(wc -l < /etc/dicttech-ap/root-password-hash | tr -d ' ')" = 1 ] || return 1
 material=$(cat /etc/dicttech-ap/root-password-hash)
 case "$material" in '$6$'*|'$5$'*|'$y$'*) ;; *) return 1;; esac
 case "$material" in *:*|*' '*|*"$(printf '\t')"*) return 1;; esac
 cmp -s /etc/xr-guest-vlan192.nft /usr/share/dicttech-ap/templates/guest-guard.nft || return 1
 cmp -s /etc/sysctl.d/13-nf-bridge.conf /usr/share/dicttech-ap/templates/13-nf-bridge.conf || return 1
 [ "$(uci -q get network.lan.proto)" = dhcp ] || return 1
 [ "$(uci -q get network.lan.device)" = br-lan.1 ] || return 1
 [ "$(uci -q get network.bridge.vlan_filtering)" = 1 ] || return 1
 [ "$(uci -q get network.guest_vlan.vlan)" = 192 ] || return 1
 [ "$(uci -q get network.guest.proto)" = none ] || return 1
 [ "$(uci -q get network.ap_maintenance.proto)" = static ] || return 1
 [ "$(uci -q get firewall.guest.input)" = DROP ] || return 1
 ssid_rows=$(jq -r '.ssids[]|[.section,.iface]|@tsv' "$MARKER") || return 1
 while IFS="$(printf '\t')" read -r section iface; do
  [ "$(uci -q get "wireless.$section")" = wifi-iface ] || return 1
  [ "$(uci -q get "wireless.$section.ifname")" = "$iface" ] || return 1
 done <<SSID_ROWS
$ssid_rows
SSID_ROWS
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
   .schema_version==2 and .profile=="dicttech-ap-v2" and
   .device_id==$marker[0].device_id and
   .defaults_sha256==$marker[0].defaults_sha256 and
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
  '{schema_version,profile,device_id,defaults_sha256,config_fingerprint,manifest_sha256:$manifest,validated_boot_id:$boot}' "$MARKER" > "$tmp"
 chmod 600 "$tmp"
 mv "$tmp" "$dest"
}

validate_manifest() {
 local manifest=${1:-$MANIFEST} base=${2:-} rows name expected actual
 [ -s "$manifest" ] && [ ! -L "$manifest" ] || return 1
 jq -e '
   .schema_version==2 and .profile=="dicttech-ap-v2" and
   (.files|type=="array" and length>=15 and length<=16) and
   (.files|all(.[]; (.path|type=="string") and (.sha256|type=="string" and length==64))) and
   ([.files[].path]|length==(unique|length)) and
   (["etc/config/network","etc/config/wireless","etc/config/firewall","etc/config/dhcp",
     "etc/config/system","etc/config/dropbear","etc/config/uhttpd","etc/config/xr_roaming",
     "etc/config/fan","etc/config/cpufreq","etc/config/airoha_npu",
     "etc/sysctl.d/13-nf-bridge.conf","etc/xr-guest-vlan192.nft",
     "etc/dicttech-ap/device.json","etc/dicttech-ap/root-password-hash"]-[.files[].path]|length==0)
 ' "$manifest" >/dev/null || return 1
 rows=$(jq -r '.files[]|[.path,.sha256]|@tsv' "$manifest") || return 1
 printf '%s\n' "$rows" | while IFS="$(printf '\t')" read -r name expected; do
  case "$name" in
   etc/config/network|etc/config/wireless|etc/config/firewall|etc/config/dhcp|etc/config/system|etc/config/dropbear|etc/config/uhttpd|etc/config/xr_roaming|etc/config/fan|etc/config/cpufreq|etc/config/airoha_npu|etc/sysctl.d/13-nf-bridge.conf|etc/xr-guest-vlan192.nft|etc/dicttech-ap/device.json|etc/dicttech-ap/root-password-hash|etc/dropbear/authorized_keys) ;;
   *) return 1;;
  esac
  case "$expected" in *[!0-9a-f]*) return 1;; esac
  [ -f "$base/$name" ] && [ ! -L "$base/$name" ] || return 1
  # An explicitly empty authorized_keys file revokes key access by intent.
  [ "$name" = etc/dropbear/authorized_keys ] || [ -s "$base/$name" ] || return 1
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
 local directory=/tmp/.uci name
 [ ! -L "$directory" ] || return 1
 [ -e "$directory" ] || return 0
 [ -d "$directory" ] || return 1
 # C7 uci revert can return success after an internal I/O error. Clear only
 # the default savedir files that the final boot-time global commit consumes.
 # No alternate savedir or unrelated package is touched.
 for name in network wireless firewall dhcp system dropbear uhttpd xr_roaming fan cpufreq airoha_npu; do
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

valid_setup_identity() {
 private_file "$IDENTITY" || return 1
 jq -e '
  type=="object" and (keys==["hostname","mac","schema_version"]) and .schema_version==1 and
  (.mac|type=="string" and length==17 and
    (split(":")|length==6 and .[0]=="02" and
      all(.[];length==2 and (explode|all(.[];(.>=48 and .<=57) or (.>=97 and .<=102)))))) and
  .hostname==("xr1710g-setup-"+(.mac|split(":")|join("")))
 ' "$IDENTITY" >/dev/null 2>&1
}

setup_identity() {
 local hex mac tmp=$IDENTITY.$$.tmp
 if [ -e "$IDENTITY" ] || [ -L "$IDENTITY" ]; then valid_setup_identity; return; fi
 # A failed random source has no fixed-address fallback. The isolated profile
 # remains available without exposing a cloned identity to the upstream LAN.
 hex=$(hexdump -v -n 5 -e '1/1 "%02x"' /dev/urandom) || return 1
 [ "${#hex}" = 10 ] || return 1
 case "$hex" in *[!0-9a-f]*) return 1;; esac
 mac=02:$(printf '%s' "$hex" | sed 's/../&:/g;s/:$//')
 [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
 (set -C; umask 077; jq -cn --arg mac "$mac" \
  '{schema_version:1,mac:$mac,hostname:("xr1710g-setup-"+($mac|split(":")|join("")))}' > "$tmp") || return 1
 chmod 600 "$tmp" || return 1
 mv "$tmp" "$IDENTITY" || return 1
 valid_setup_identity
}

setup_template() {
 local name=$1 mode=$2 mac hostname
 if [ "$mode" = dhcp ]; then
  case "$name" in
   network)
    valid_setup_identity || return 1
    mac=$(jq -r .mac "$IDENTITY"); hostname=$(jq -r .hostname "$IDENTITY")
    sed -e "s/@BOOTSTRAP_MAC@/$mac/g" -e "s/@BOOTSTRAP_HOSTNAME@/$hostname/g" "$SETUP/network-dhcp"
    return;;
   dropbear|firewall) cat "$SETUP/$name-dhcp"; return;;
  esac
 fi
 cat "$SETUP/$name"
}

setup_state() {
 jq -cn --arg reason "$1" --arg mode "$2" \
  '{schema_version:1,state:"unprovisioned",reason:$reason,mode:$mode,maintenance_address:"192.168.250.1",maintenance_port:"lan4"}'
}

setup_files_match() {
 local name mode reason expected sections section
 private_file "$STATE" || return 1
 mode=$(jq -er '.mode|select(.=="dhcp" or .=="isolated")' "$STATE") || return 1
 reason=$(jq -er '.reason|select(.=="no_marker" or .=="invalid_marker" or .=="boot_snapshot_failed" or .=="invalid_boot_snapshot" or .=="identity_invalid")' "$STATE") || return 1
 [ "$mode" != dhcp ] || [ "$reason" = no_marker ] || return 1
 expected=$(setup_state "$reason" "$mode") || return 1
 printf '%s\n' "$expected" | cmp -s - "$STATE" || return 1
 for name in network firewall dhcp dropbear uhttpd; do
  private_file "/etc/config/$name" || return 1
  expected=$(setup_template "$name" "$mode") || return 1
  printf '%s\n' "$expected" | cmp -s - "/etc/config/$name" || return 1
 done
 [ -f /etc/config/wireless ] && [ ! -L /etc/config/wireless ] || return 1
 sections=$(uci -q show wireless) || return 1
 for section in $(printf '%s\n' "$sections" | sed -n 's/^wireless\.\([^.=]*\)=wifi-\(device\|iface\)$/\1/p'); do
  [ "$(uci -q get "wireless.$section.disabled")" = 1 ] || return 1
 done
}

check_setup() {
 # Read-only proof: neither the boot stamp nor STATE alone grants initialization.
 local mode mac= dhcp_ready=false status actual expected
 setup_files_match || return 1
 [ ! -e "$MARKER" ] && [ ! -L "$MARKER" ] || return 1
 [ -f /tmp/dicttech-ap-bootstrap-isolated ] && [ ! -L /tmp/dicttech-ap-bootstrap-isolated ] || return 1
 [ ! -e /sys/class/net/br-lan ] && [ ! -L /sys/class/net/lan4/master ] || return 1
 ubus -t 2 call network.interface.ap_maintenance status | jq -e '
  .up==true and .proto=="static" and .l3_device=="lan4" and
  (."ipv4-address"|type=="array" and length==1 and .[0].address=="192.168.250.1" and .[0].mask==24)
 ' >/dev/null || return 1
 mode=$(jq -r .mode "$STATE")
 if [ "$mode" = dhcp ]; then
  valid_setup_identity || return 1
  mac=$(jq -r .mac "$IDENTITY")
  [ "$(cat /sys/class/net/br-setup/address)" = "$mac" ] || return 1
  [ "$(cat /sys/class/net/br-setup.1/address)" = "$mac" ] || return 1
  status=$(ubus -t 2 call network.interface.bootstrap status) || return 1
  printf '%s' "$status" | jq -e '
   .proto=="dhcp" and ((.l3_device // .device)=="br-setup.1") and
   ((."ipv6-address" // [])|length==0) and
   (if .up==true then (."ipv4-address"|type=="array" and length>=1)
    else .up==false and ((."ipv4-address" // [])|length==0) end)
  ' >/dev/null || return 1
  # Pending DHCP is valid for completely offline LAN4 enrollment. There must
  # still be no unexpected static IPv4 address on either setup bridge device.
  actual=$(ip -4 address show dev br-setup) || return 1
  [ -z "$(printf '%s\n' "$actual" | awk '$1=="inet" {print $2}')" ] || return 1
  actual=$(ip -4 address show dev br-setup.1) || return 1
  actual=$(printf '%s\n' "$actual" | awk '$1=="inet" {print $2}' | sort)
  expected=$(printf '%s' "$status" | jq -r '(."ipv4-address" // [])[]|.address+"/"+(.mask|tostring)' | sort) || return 1
  [ "$actual" = "$expected" ] || return 1
  dhcp_ready=$(printf '%s' "$status" | jq -r .up)
  ubus -t 2 call network.device status '{"name":"br-setup"}' | jq -e --arg mac "$mac" '
   .up==true and .macaddr==$mac and (."bridge-members"|sort==["lan2","lan3","wan"])
  ' >/dev/null || return 1
 else
  [ ! -e /sys/class/net/br-setup ] || return 1
 fi
 jq -cn --arg mode "$mode" --arg mac "$mac" --argjson ready "$dhcp_ready" \
  '{mode:$mode,bridge_mac:$mac,dhcp_ready:$ready,ssh_port:"12022"}'
}

isolate() {
 local reason=$1 mode=isolated name tmp
 [ ! -L /etc/dicttech-ap ] || return 1
 umask 077
 mkdir -p /etc/dicttech-ap
 if [ "$reason" = no_marker ]; then
  if setup_identity; then mode=dhcp; else reason=identity_invalid; fi
 fi
 clear_managed_deltas || { emergency_lock; return 1; }
 for name in network firewall dhcp dropbear uhttpd; do
  tmp=/etc/config/$name.dicttech-setup.$$
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  setup_template "$name" "$mode" > "$tmp" || { emergency_lock; return 1; }
  chmod 600 "$tmp" || { emergency_lock; return 1; }
  mv "$tmp" "/etc/config/$name" || { emergency_lock; return 1; }
 done
 disable_wireless || { emergency_lock; return 1; }
 /usr/lib/dicttech-ap/services.sh setup || { emergency_lock; return 1; }
 tmp=$STATE.$$.tmp
 [ ! -L "$STATE" ] && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || { emergency_lock; return 1; }
 setup_state "$reason" "$mode" > "$tmp" || { emergency_lock; return 1; }
 chmod 600 "$tmp" || { emergency_lock; return 1; }
 mv "$tmp" "$STATE" || { emergency_lock; return 1; }
 if [ "$reason" = no_marker ]; then key_lifecycle fresh; else key_lifecycle retire; fi
 : > /tmp/dicttech-ap-bootstrap-isolated
 note "$mode setup: LAN4 192.168.250.1/24, SSH 12022; Wi-Fi disabled"
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
