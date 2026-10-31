#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# XR1710G factory enrollment adapter, selected by the image package.
# Configured resource acceptance does not depend on this factory topology.
SETUP=/usr/lib/dicttech-ap/factory/setup

factory_supported() {
 case "$(cat /tmp/sysinfo/board_name)" in
  gemtek,xr1710g|gemtek,xr1710g-ubi) return 0;;
  *) return 1;;
 esac
}

factory_config_paths() {
 printf '%s\n' 'etc/config/network etc/config/wireless etc/config/firewall etc/config/dhcp etc/config/dropbear etc/config/uhttpd'
}

factory_valid_identity() {
 private_file "$IDENTITY" || return 1
 jq -e '
  type=="object" and (keys==["hostname","mac","schema_version"]) and .schema_version==1 and
  (.mac|type=="string" and length==17 and
    (split(":")|length==6 and .[0]=="02" and
      all(.[];length==2 and (explode|all(.[];(.>=48 and .<=57) or (.>=97 and .<=102)))))) and
  .hostname==("xr1710g-setup-"+(.mac|split(":")|join("")))
 ' "$IDENTITY" >/dev/null 2>&1
}

factory_identity() {
 local hex mac tmp=$IDENTITY.$$.tmp
 if [ -e "$IDENTITY" ] || [ -L "$IDENTITY" ]; then factory_valid_identity; return; fi
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
 factory_valid_identity
}

factory_template() {
 local name=$1 mode=$2 mac hostname
 if [ "$mode" = dhcp ]; then
  case "$name" in
   network)
    factory_valid_identity || return 1
    mac=$(jq -r .mac "$IDENTITY"); hostname=$(jq -r .hostname "$IDENTITY")
    sed -e "s/@BOOTSTRAP_MAC@/$mac/g" -e "s/@BOOTSTRAP_HOSTNAME@/$hostname/g" "$SETUP/network-dhcp"
    return;;
   dropbear|firewall) cat "$SETUP/$name-dhcp"; return;;
  esac
 fi
 cat "$SETUP/$name"
}

factory_state() {
 jq -cn --arg reason "$1" --arg mode "$2" \
  '{schema_version:1,state:"unprovisioned",reason:$reason,mode:$mode,maintenance_address:"192.168.250.1",maintenance_port:"lan4"}'
}

factory_files_match() {
 local name mode reason expected sections section
 private_file "$STATE" || return 1
 mode=$(jq -er '.mode|select(.=="dhcp" or .=="isolated")' "$STATE") || return 1
 reason=$(jq -er '.reason|select(.=="no_marker" or .=="invalid_marker" or .=="boot_snapshot_failed" or .=="invalid_boot_snapshot" or .=="identity_invalid")' "$STATE") || return 1
 [ "$mode" != dhcp ] || [ "$reason" = no_marker ] || return 1
 expected=$(factory_state "$reason" "$mode") || return 1
 printf '%s\n' "$expected" | cmp -s - "$STATE" || return 1
 for name in network firewall dhcp dropbear uhttpd; do
  private_file "/etc/config/$name" || return 1
  expected=$(factory_template "$name" "$mode") || return 1
  printf '%s\n' "$expected" | cmp -s - "/etc/config/$name" || return 1
 done
 [ -f /etc/config/wireless ] && [ ! -L /etc/config/wireless ] || return 1
 sections=$(uci -q show wireless) || return 1
 for section in $(printf '%s\n' "$sections" | sed -n 's/^wireless\.\([^.=]*\)=wifi-\(device\|iface\)$/\1/p'); do
  [ "$(uci -q get "wireless.$section.disabled")" = 1 ] || return 1
 done
}

factory_check_setup() {
 # Read-only proof: neither the boot stamp nor STATE alone grants initialization.
 local mode mac= dhcp_ready=false status actual expected
 factory_files_match || return 1
 [ ! -e "$MARKER" ] && [ ! -L "$MARKER" ] || return 1
 [ -f /tmp/dicttech-ap-bootstrap-isolated ] && [ ! -L /tmp/dicttech-ap-bootstrap-isolated ] || return 1
 [ ! -e /sys/class/net/br-lan ] && [ ! -L /sys/class/net/lan4/master ] || return 1
 ubus -t 2 call network.interface.ap_maintenance status | jq -e '
  .up==true and .proto=="static" and .l3_device=="lan4" and
  (."ipv4-address"|type=="array" and length==1 and .[0].address=="192.168.250.1" and .[0].mask==24)
 ' >/dev/null || return 1
 mode=$(jq -r .mode "$STATE")
 if [ "$mode" = dhcp ]; then
  factory_valid_identity || return 1
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

factory_isolate() {
 local reason=$1 mode=isolated name tmp
 [ ! -L /etc/dicttech-ap ] || return 1
 umask 077
 mkdir -p /etc/dicttech-ap
 if [ "$reason" = no_marker ]; then
  if factory_identity; then mode=dhcp; else reason=identity_invalid; fi
 fi
 clear_managed_deltas || { emergency_lock; return 1; }
 for name in network firewall dhcp dropbear uhttpd; do
  tmp=/etc/config/$name.dicttech-setup.$$
  [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
  factory_template "$name" "$mode" > "$tmp" || { emergency_lock; return 1; }
  chmod 600 "$tmp" || { emergency_lock; return 1; }
  mv "$tmp" "/etc/config/$name" || { emergency_lock; return 1; }
 done
 disable_wireless || { emergency_lock; return 1; }
 sysctl -p "$SETUP/arp.conf" >/dev/null || { emergency_lock; return 1; }
 /usr/lib/dicttech-ap/services.sh setup || { emergency_lock; return 1; }
 tmp=$STATE.$$.tmp
 [ ! -L "$STATE" ] && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || { emergency_lock; return 1; }
 factory_state "$reason" "$mode" > "$tmp" || { emergency_lock; return 1; }
 chmod 600 "$tmp" || { emergency_lock; return 1; }
 mv "$tmp" "$STATE" || { emergency_lock; return 1; }
 if [ "$reason" = no_marker ]; then key_lifecycle fresh; else key_lifecycle retire; fi
 : > /tmp/dicttech-ap-bootstrap-isolated
 note "$mode setup: LAN4 192.168.250.1/24, SSH 12022; Wi-Fi disabled"
}

