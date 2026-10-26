#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Runs before netifd/dropbear/uhttpd start. No device identity is guessed here.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

DEFAULTS=/usr/share/dicttech-ap/defaults.json
MARKER=/etc/dicttech-ap/device.json
STATE=/etc/dicttech-ap/bootstrap-state.json
SETUP=/usr/lib/dicttech-ap/setup
ACCEPTED=/etc/dicttech-ap/accepted.json
BOOT_VALIDATED=/tmp/dicttech-ap-firstboot-validated.json
MANIFEST=/etc/dicttech-ap/managed-files.json
BOOT_SNAPSHOT=/tmp/dicttech-ap-firstboot-snapshot

note() { printf '%s\n' "dicttech-ap: $*" >&2; }

valid_marker() {
 local hash name section material
 [ -f "$MARKER" ] && [ ! -L "$MARKER" ] && [ -f "$DEFAULTS" ] || return 1
 hash=$(sha256sum "$DEFAULTS"); hash=${hash%% *}
 jq -e --arg hash "$hash" '
   type=="object" and .schema_version==1 and .profile=="dicttech-ap-v1" and
   .defaults_sha256==$hash and
   (.device_id|type=="string" and length>=1 and length<=64) and
   (.config_fingerprint|type=="string" and length==64)
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
 for section in dicttech_mlo default_radio1 guest_mlo wifinet1; do
  [ "$(uci -q get "wireless.$section")" = wifi-iface ] || return 1
 done
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
   .schema_version==1 and .profile=="dicttech-ap-v1" and
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
   .schema_version==1 and .profile=="dicttech-ap-v1" and
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

isolate() {
 local reason=$1 name tmp
 [ ! -L /etc/dicttech-ap ] || return 1
 umask 077
 mkdir -p /etc/dicttech-ap
 clear_managed_deltas || { emergency_lock; return 1; }
 for name in network firewall dhcp dropbear uhttpd; do
  tmp=/etc/config/$name.dicttech-setup.$$
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  cp "$SETUP/$name" "$tmp" || { emergency_lock; return 1; }
  chmod 600 "$tmp" || { emergency_lock; return 1; }
  mv "$tmp" "/etc/config/$name" || { emergency_lock; return 1; }
 done
 disable_wireless || { emergency_lock; return 1; }
 /usr/lib/dicttech-ap/services.sh setup || { emergency_lock; return 1; }
 printf '{"schema_version":1,"state":"unprovisioned","reason":"%s","maintenance_address":"192.168.250.1","maintenance_port":"lan4"}\n' "$reason" > "$STATE"
 chmod 600 "$STATE"
 : > /tmp/dicttech-ap-bootstrap-isolated
 note "isolated setup: lan4, 192.168.250.1/24, SSH 12022; Wi-Fi disabled"
}

prepare() {
 [ ! -f /tmp/dicttech-ap-bootstrap-isolated ] || return 0
 if [ -e "$MARKER" ] || [ -L "$MARKER" ]; then
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
 if ! binding_matches "$ACCEPTED" && binding_matches "$BOOT_VALIDATED"; then
  if ! restore_boot_snapshot; then
   quarantine || { emergency_lock; return 1; }
   isolate invalid_boot_snapshot
   return 1
  fi
 fi
 if valid_marker; then
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
 [ ! -f /tmp/dicttech-ap-bootstrap-isolated ] || return 0
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
 prepare) prepare;;
 finalize) finalize;;
 *) note 'usage: bootstrap.sh prepare|finalize'; exit 2;;
esac
