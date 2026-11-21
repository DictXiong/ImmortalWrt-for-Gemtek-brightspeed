# SPDX-License-Identifier: GPL-2.0-only

xr1710g_boot_state_valid_volume() {
	local dir="/sys/class/ubi/$1"

	[ "$(cat "$dir/type" 2>/dev/null)" = dynamic ] &&
		[ "$(cat "$dir/reserved_ebs" 2>/dev/null)" = 1 ] &&
		[ "$(cat "$dir/usable_eb_size" 2>/dev/null)" = 126976 ] &&
		[ "$(cat "$dir/upd_marker" 2>/dev/null)" = 0 ] &&
		[ "$(cat "$dir/corrupted" 2>/dev/null)" = 0 ]
}

xr1710g_boot_state_layout() (
	local ubi state fit overlay name bytes free fit_lebs overlay_lebs extra

	# Native legacy sysupgrade must derive the same single system UBI/FIT.
	unset CI_METHOD CI_KERNPART CI_KERNVOLID
	export_fitblk_bootdev
	[ "$CI_METHOD" = ubi ] && [ "$CI_UBIPART" = ubi ] && [ "$CI_KERNPART" = fit ] &&
		[ "${CI_KERN_UBIPART:-ubi}" = ubi ] &&
		[ "${CI_ROOT_UBIPART:-ubi}" = ubi ] &&
		[ "${CI_DATA_UBIPART:-ubi}" = ubi ] || return 1

	ubi="$(nand_find_ubi ubi)"
	[ -n "$ubi" ] && [ -c "/dev/$ubi" ] &&
		[ "$(cat "/sys/class/ubi/$ubi/ro_mode")" = 0 ] &&
		[ "$(cat "/sys/class/ubi/$ubi/eraseblock_size")" = 126976 ] ||
		return 1
	[ -z "$(nand_find_volume "$ubi" "$CI_ROOTPART")" ] || return 1
	fit="$(nand_find_volume "$ubi" fit)"
	overlay="$(nand_find_volume "$ubi" rootfs_data)"
	[ -n "$fit" ] || return 1
	fit_lebs="$(cat "/sys/class/ubi/$fit/reserved_ebs")"
	overlay_lebs=0
	[ -z "$overlay" ] || overlay_lebs="$(cat "/sys/class/ubi/$overlay/reserved_ebs")"
	free="$(cat "/sys/class/ubi/$ubi/avail_eraseblocks")"
	bytes="$(wc -c < "$1")"
	for name in "$fit_lebs" "$overlay_lebs" "$free" "$bytes"; do
		case "$name" in ''|*[!0-9]*) return 1 ;; esac
	done

	state="$(nand_find_volume "$ubi" xr_boot_state)"
	extra=1
	if [ -n "$state" ]; then
		if ! xr1710g_boot_state_valid_volume "$state"; then
			echo "XR1710G: xr_boot_state has an invalid volume layout; refusing upgrade." >&2
			return 1
		fi
		extra=0
	fi
	# Allow for provisioning created by the default native upgrade. Before
	# stage2 its RPC options are unavailable, so reserve the default cost.
	if [ "${UPGRADE_OPT_ADD_PROVISIONING:-1}" -gt 0 ] &&
	   [ -z "$(nand_find_volume "$ubi" provisioning)" ]; then
		extra=$((extra + 2))
	fi
	# UBIFS has an absolute minimum of 20 LEBs for this kernel.
	if [ $((free + fit_lebs + overlay_lebs - (bytes + 126975) / 126976 - extra)) -lt 20 ]; then
		echo "XR1710G: insufficient UBI capacity for FIT, recovery state and overlay." >&2
		return 1
	fi
	if [ -n "$state" ]; then
		echo "XR1710G: existing recovery state volume will be preserved." >&2
	else
		echo "XR1710G: one LEB will be reserved for boot recovery before rootfs_data." >&2
	fi
)

xr1710g_boot_state_check_upgrade() {
	local status=0

	XR1710G_RECOVERY_FIT=
	[ "$(get_magic_long "$1" cat)" = d00dfeed ] || return 0
	fit_check_sign -R -f "$1" >/dev/null || status=$?
	case "$status" in
		0) ;;
		2) return 0 ;;
		*)
			echo "XR1710G: cannot verify the recovery FIT." >&2
			return 1
			;;
	esac
	xr1710g_boot_state_layout "$1" || {
		echo "XR1710G: boot state preflight failed before removing any UBI volume." >&2
		return 1
	}
	XR1710G_RECOVERY_FIT=1
}

platform_reserve_nand_state() {
	local ubi="$1" volume

	[ "${XR1710G_RECOVERY_FIT:-}" = 1 ] || return 0
	volume="$(nand_find_volume "$ubi" xr_boot_state)"
	if [ -z "$volume" ]; then
		ubimkvol "/dev/$ubi" -N xr_boot_state -s 126976 -t dynamic || :
		volume="$(nand_find_volume "$ubi" xr_boot_state)"
	fi
	if [ -n "$volume" ] && xr1710g_boot_state_valid_volume "$volume"; then
		v "XR1710G: one LEB recovery state volume ready"
	else
		# The old FIT and overlay have already been removed. Finish writing
		# the new FIT, rather than leave an otherwise bootable unit blank.
		v "XR1710G: boot recovery unavailable: state reservation could not be verified"
	fi
	return 0
}
