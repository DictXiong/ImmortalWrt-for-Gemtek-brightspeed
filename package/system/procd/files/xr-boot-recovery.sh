#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only

. /lib/functions.sh
. /lib/functions/xr-failsafe-network.sh
. /lib/upgrade/nand.sh

recovery_state_device() {
	local ubidev volume

	# Linux names this legacy partition "ubi", U-Boot names it "system".
	ubidev="$(nand_find_ubi ubi)" && [ -n "$ubidev" ] || return 1
	volume="$(nand_find_volume "$ubidev" xr_boot_state)" &&
		[ -n "$volume" ] && [ "$(cat "/sys/class/ubi/$volume/type")" = dynamic ] ||
		return 1
	printf '/dev/%s\n' "$volume"
}

recovery_state_writable() {
	local volume="${1#/dev/}" ubidev sysfs

	[ -n "$volume" ] || return 1
	ubidev="${volume%_*}"
	sysfs="/sys/class/ubi/$volume"
	[ "$(cat "/sys/class/ubi/$ubidev/ro_mode")" = 0 ] &&
	[ "$(cat "$sysfs/reserved_ebs")" = 1 ] &&
	[ "$(cat "$sysfs/upd_marker")" = 0 ] &&
	[ "$(cat "$sysfs/corrupted")" = 0 ]
}

case "$1" in
	verify-ready)
		volume="$(recovery_state_device)" || exit 1
		exec /sbin/xr-boot-recovery verify-ready "$volume"
		;;
	reboot-to-uboot)
		volume="$(recovery_state_device)" && recovery_state_writable "$volume" || {
			echo "XR1710G: writable boot state volume unavailable" >&2
			exit 1
		}
		exec /sbin/xr-boot-recovery reboot-to-uboot "$volume"
		;;
	status)
		# The NAND helpers may create missing device nodes. A read-only
		# status request only inspects nodes that already exist.
		volume="$(
			ubi_mknod() { :; }
			recovery_state_device
		)" 2>/dev/null || volume=
		[ -c "$volume" ] || volume=
		if recovery_state_writable "$volume" 2>/dev/null; then
			exec /sbin/xr-boot-recovery status "$volume" writable
		fi
		exec /sbin/xr-boot-recovery status "$volume"
		;;
esac

mode="$1"
rescue_interface="$2"
rescue_address="$3"

ssh_listening() {
	netstat -lntp 2>/dev/null | awk -v port="$1" -v address="${2:-}" '
		$6 == "LISTEN" && $7 ~ /^[0-9]+\/dropbear$/ &&
		$4 !~ /^(127\.|\[?::1\]?:)/ && (port == "" || $4 ~ (":" port "$")) &&
		(address == "" || $4 == ("0.0.0.0:" port) || $4 == (address ":" port)) {
			found = 1
		}
		END { exit !found }
	'
}

ap_enabled() {
	local mode disabled device

	config_get mode "$1" mode
	config_get device "$1" device
	config_get_bool disabled "$1" disabled 0
	[ "$mode" = ap ] && [ "$disabled" = 0 ] && [ -n "$device" ] || return 0
	config_get_bool disabled "$device" disabled 0
	[ "$disabled" = 0 ] && check_hostapd=1
}

normal_ready() {
	local status device hostapd check_hostapd=0

	awk '$2 == "/overlay" && $3 == "ubifs" && $4 ~ /(^|,)rw(,|$)/ { found = 1 }
	     END { exit !found }' /proc/mounts || return 1

	status="$(ubus -t 2 call network.interface.lan status)" || return 1
	device="$(jsonfilter -s "$status" -e '@.device')" || return 1
	[ -n "$device" ] && ip -o link show dev "$device" | grep -qE '<([^>]*,)?UP([,>])' || return 1
	ip -o addr show dev "$device" scope global | awk '
		($3 == "inet" || $3 == "inet6") && $0 !~ / (tentative|dadfailed)( |$)/ {
			found = 1
		}
		END { exit !found }
	' || return 1
	ssh_listening "" || return 1
	ubus -t 2 call network.wireless status > /dev/null || return 1

	config_load wireless || return 1
	config_foreach ap_enabled wifi-iface
	[ "$check_hostapd" = 1 ] || return 0
	hostapd="$(ubus -t 2 list 'hostapd.*' | sed -n '/^hostapd\./{ p; q; }')"
	[ -n "$hostapd" ] && ubus -t 2 call "$hostapd" get_status > /dev/null
}

failsafe_ready() {
	if xr_failsafe_is_ap; then
		[ "$rescue_address" = 192.168.1.1 ] &&
			ssh_listening 22 "$rescue_address" || return 1
		xr_failsafe_bridge_ready "$rescue_interface" ||
			{ /sbin/xr-boot-recovery waiting normal &&
			  xr_failsafe_single_ready "$rescue_interface"; }
		return
	fi
	[ -n "$rescue_interface" ] && [ -n "$rescue_address" ] || return 1
	ip -4 -o addr show dev "$rescue_interface" | awk -v address="$rescue_address" '
		index($4, address "/") == 1 { found = 1 }
		END { exit !found }
	' && ssh_listening 22
}

while /sbin/xr-boot-recovery waiting "$mode"; do
	if "${mode}_ready"; then
		if volume="$(recovery_state_device)" &&
		   /sbin/xr-boot-recovery ready "$mode" "$volume"; then
			exit 0
		fi
	fi
	sleep 2
done
