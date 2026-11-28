# SPDX-License-Identifier: GPL-2.0-only

xr_failsafe_ports() {
	case "$(board_name)" in
		gemtek,xr1710g) printf '%s\n' 'wan lan2 lan3 lan4' ;;
		gemtek,w1701k-ubi) printf '%s\n' 'lan1 lan2' ;;
		*) return 1 ;;
	esac
}

xr_failsafe_is_ap() {
	[ "$(cat /etc/xr1710g-network-role 2>/dev/null)" = ap ] &&
		xr_failsafe_ports >/dev/null
}

xr_failsafe_is_port() {
	local ports

	[ -n "$1" ] || return 1
	ports="$(xr_failsafe_ports)" || return 1
	case " $ports " in
		*" $1 "*) return 0 ;;
		*) return 1 ;;
	esac
}

xr_failsafe_forwarding_off() {
	[ "$(cat /proc/sys/net/ipv4/ip_forward)" = 0 ] &&
		{ [ ! -e /proc/sys/net/ipv6/conf/all/forwarding ] ||
		  [ "$(cat /proc/sys/net/ipv6/conf/all/forwarding)" = 0 ]; }
}

xr_failsafe_link_up() {
	ip -o link show dev "$1" | grep -qE '<([^>]*,)?UP([,>])'
}

xr_failsafe_address_owner() {
	local addresses

	addresses="$(ip -4 -o address show to 192.168.1.1/32)" || return 1
	printf '%s\n' "$addresses" | awk 'NF { print $2, $4 }'
}

xr_failsafe_independent_port() {
	xr_failsafe_is_port "$1" &&
		[ -e "/sys/class/net/$1" ] && [ ! -e "/sys/class/net/$1/master" ]
}

xr_failsafe_single_ready() {
	local owner

	xr_failsafe_independent_port "$1" && xr_failsafe_link_up "$1" &&
		xr_failsafe_forwarding_off &&
		[ ! -e /sys/class/net/br-xr-rescue ] &&
		owner="$(xr_failsafe_address_owner)" && [ "$owner" = "$1 192.168.1.1/24" ]
}

xr_failsafe_bridge_ready() {
	local bridge_dev="$1" member port ready=0

	[ "$bridge_dev" = br-xr-rescue ] &&
		xr_failsafe_forwarding_off &&
		xr_failsafe_link_up "$bridge_dev" || return 1

	ip -4 -o address show dev "$bridge_dev" | awk '
		$4 == "192.168.1.1/24" { found = 1 }
		END { exit !found }
	' || return 1

	for member in "/sys/class/net/$bridge_dev/brif/"*; do
		[ -e "$member" ] || continue
		port="${member##*/}"
		xr_failsafe_is_port "$port" || return 1
		[ "$(cat "/sys/class/net/$port/brport/isolated")" = 1 ] || return 1
		# A usable port may have no cable connected when the device boots.
		xr_failsafe_link_up "$port" && ready=1
	done

	[ "$ready" = 1 ]
}
