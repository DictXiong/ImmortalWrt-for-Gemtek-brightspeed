#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only

. /lib/functions.sh
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

if [ "$1" = verify-ready ]; then
	volume="$(recovery_state_device)" || exit 1
	exec /sbin/xr-boot-recovery verify-ready "$volume"
fi

mode="$1"
rescue_interface="$2"
rescue_address="$3"

ssh_listening() {
	netstat -lntp 2>/dev/null | awk -v port="$1" '
		$6 == "LISTEN" && $7 ~ /^[0-9]+\/dropbear$/ &&
		$4 !~ /^(127\.|\[?::1\]?:)/ && (port == "" || $4 ~ (":" port "$")) {
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
