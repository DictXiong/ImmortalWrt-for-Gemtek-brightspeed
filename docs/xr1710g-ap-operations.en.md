# XR1710G Full AP installation, upgrades and recovery

[简体中文](xr1710g-ap-operations.md) · [Full firmware and defaults](xr1710g-full.md)

This guide covers Full Legacy AP on the XR1710G (W1700K / W1701K). The image uses the `gemtek_xr1710g` profile and is built with `--role ap --layout legacy`. Automatic recovery requires the matching [second-stage U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader), the original Legacy AP FIT and usable `xr_boot_state` storage. Gateway images, other models and the XR1710G full-UBI layout have separate installation paths.

## First installation

1. Isolate the device, back up critical factory partitions and existing settings, and verify the image against the matching checksums.
2. Follow the [second-stage U-Boot installation guide](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/README.en.md#installation-and-upgrades) to choose a complete slot image accepted by the installed bootloader. The first-stage bootloader stays in place. Check the exact version of the current bootloader for its upload and environment reset behavior.
3. Check the second-stage build identity on the [recovery page](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/recovery-web.md). Use the **firmware** upload target with the original `*-gemtek_xr1710g-squashfs-sysupgrade.itb`. A web rebuild replaces the system UBI contents, including settings and overlay data, and reserves state space for a supported FIT.
4. After OpenWrt starts normally, find the AP in the upstream DHCP leases. Check the version, network and SSH, then set management credentials. The AP starts with Wi-Fi disabled and supplies no DHCP service.

## Keep settings during an upgrade

While the AP runs normally, use LuCI's backup and flash page or OpenWrt `sysupgrade` to keep settings. Export a backup and record additional packages. Check the selected image using `sysupgrade -T /tmp/<image-file>`. Reinstall any extra packages using versions that match the new firmware.

On the first migration from an older upgrader, the **running system** performs the write. It may allocate all spare space to `rootfs_data`. After the new AP starts normally, use `ubinfo -a` to look for a one-LEB dynamic volume named `xr_boot_state`, then verify that the current boot is confirmed.

```sh
/lib/xr-boot-recovery verify-ready >/dev/null && echo READY
```

If the volume is missing and the new AP firmware already runs normally, perform one more settings-preserving upgrade with the same verified FIT. The new upgrader reserves space before allocating the overlay. It preserves an existing valid state volume. If the volume exists but READY cannot be verified, check the paired second stage, the original FIT and the volume state. A normal boot can succeed before automatic recovery is enabled.

## Daily operation and recovery

| Environment | Access and next step |
| --- | --- |
| Normal OpenWrt | Use its current management address, SSH or LuCI. On a confirmed compatible boot, root can run `reboot-to-uboot` for a one-time restart into web recovery. Use Reboot on the page when maintenance is complete. |
| OpenWrt failsafe | Use a static client address such as `192.168.1.2/24`, connect directly to a usable port and use SSH at `192.168.1.1:22`. Failsafe uses a temporary SSH host key. To repair persistent settings, verify that the overlay is usable, run `mount_root`, then make the changes and reboot. |
| Second-stage U-Boot web recovery | Use DHCP or set the client to `192.168.0.10/24` and open `http://192.168.0.1/`. Select a backup or upload target on the page, or reboot without an upload. |

The [automatic recovery guide and compatibility table](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/xr1710g-boot-recovery.en.md) cover the escalation sequence, one-shot entry and failure boundaries. OpenWrt failsafe attempts WAN, 10G LAN and both 1G LAN sockets, isolating each usable port before enabling it. If one port fails, use another that passed initialization. Manual failsafe can return to the original verified standalone port after safely cleaning up a failed rescue bridge. Automatic failsafe confirms readiness only with a working isolated bridge setup and a local SSH listener.

> [!WARNING]
> Failsafe accepts root SSH without a password. The U-Boot page can flash images and provides DHCP by default. Use a trusted isolated segment or a direct cable, including when deciding which network an automatically recovering device will still be connected to.
