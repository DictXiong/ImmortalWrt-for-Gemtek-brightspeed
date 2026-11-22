# ImmortalWrt for XR1710G

[![Build](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml/badge.svg?branch=master)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml)
[![Latest release](https://img.shields.io/github/v/release/DictXiong/ImmortalWrt-for-Gemtek-brightspeed?sort=date)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest)
[简体中文](README.md)

ImmortalWrt firmware for the Gemtek XR1710G. It supports tri-band Wi-Fi 7, two 10 GbE and two 1 GbE ports, Airoha management pages and LuCI. It includes [wrtmesh](https://github.com/DictXiong/wrtmesh) for optional wired-backhaul roaming management and [wrtix](https://github.com/DictXiong/wrtix) for optional declarative configuration. This repository maintains and releases firmware for the XR1710G. The XG2010G and other models are outside its maintenance scope.

## Downloads and initial network settings

Choose a role and the matching legacy image from the [latest release](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest). Use the `-sysupgrade.itb` for an upgrade. The release also includes matching APKs, exact source archives and build manifests. Verify `sha256sums` and back up your settings before flashing.

| Role | Network after a fresh install or factory reset |
| --- | --- |
| **Gateway** | The WAN acquires an address with DHCP. The LAN serves DHCP at `192.168.1.1` and IPv4 is NATed through the WAN. |
| **Access point (AP)** | All four Ethernet ports share a bridge. Look up its management address in the upstream DHCP lease table. The AP runs no DHCP or IPv6 RA server. |

Both roles start with LAN-side SSH on port 22, an empty `root` password and Wi-Fi and roaming disabled. Use a trusted network on first boot, set management credentials and select the correct wireless regulatory domain and security settings. A settings-preserving upgrade keeps the existing network role. Reinstall manually added APKs using builds for the new firmware, especially kernel modules.

## Installation, upgrades and recovery

1. **First installation** uses the stock partition layout and the matching [second-stage U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/README.en.md#installation-and-upgrades). Confirm which slot format the current bootloader accepts. The full-NAND UBI layout is outside this release's supported configurations.
2. **Existing OpenWrt installations** can follow the [settings-preserving upgrade guide](docs/xr1710g-ap-operations.en.md) to check the image and layout. A native firmware upgrade leaves the second-stage U-Boot and partition layout in place.
3. **AP boot recovery** falls back from a normal boot to OpenWrt failsafe and then to U-Boot web recovery when the matching bootloader and persistent state volume are available. LuCI's **System → Reboot** page shows recovery status and offers **Reboot to U-Boot** when available. Use a trusted direct link or isolated network for recovery. See [daily operation and recovery](docs/xr1710g-ap-operations.en.md#daily-operation-and-recovery).

## Builds and releases

Development takes place on `master`. The [build workflow](.github/workflows/xr1710g-release.yml) builds the gateway and AP roles from the same pinned source and dependencies. Official tags use the eight-digit Shanghai release date `YYYYMMDD`. LuCI Overview and `/etc/openwrt_release` show `ImmortalWrt for XR1710G YYYYMMDD-short-commit`. See the [local build and release guide](docs/xr1710g-releases.md) and [firmware components and defaults](docs/xr1710g-full.md).

This firmware is based on [ImmortalWrt](https://github.com/immortalwrt/immortalwrt) and [naoki66's XR1710G work](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed). It draws on [LuCI](https://github.com/immortalwrt/luci), [mt76](https://github.com/openwrt/mt76), and the [Airoha LuCI applications](https://github.com/naoki66/luci-app-airoha). Licensed under [GPL-2.0-only](https://spdx.org/licenses/GPL-2.0-only.html).
