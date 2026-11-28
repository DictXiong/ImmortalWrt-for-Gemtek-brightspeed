# ImmortalWrt for XR1710G and W1701K

[![Build](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml/badge.svg?branch=master)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml)
[![Latest release](https://img.shields.io/github/v/release/DictXiong/ImmortalWrt-for-Gemtek-brightspeed?sort=date)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest)
[![Upstream](https://img.shields.io/badge/upstream-naoki66%20%40%209a70b257-blue)](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed/commit/9a70b25731dc64822dcb24cf9a7dbe7a84ed1ba1)
[![Kernel](https://img.shields.io/badge/kernel-Linux%206.18-red)](target/linux/generic/kernel-6.18)
[![SoC](https://img.shields.io/badge/SoC-Airoha%20AN7581GT-orange)](target/linux/airoha/dts/an7581-xr1710g.dts)
[![License](https://img.shields.io/badge/license-GPL--2.0--only-green)](COPYING)
[简体中文](README.md)

ImmortalWrt firmware for the Gemtek XR1710G and Quantum Fiber W1701K. Both support tri-band Wi-Fi 7, Airoha management pages and LuCI. The images include [wrtmesh](https://github.com/DictXiong/wrtmesh) for optional wired-backhaul roaming management and [wrtix](https://github.com/DictXiong/wrtix) for optional declarative configuration.

The W1701K target (`gemtek_w1701k-ubi`) uses the vendor first stage and UBI 2.0. In `make menuconfig`, select **Gemtek W1701K (UBI)** under Airoha AN7581. For a config seed, set `CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_w1701k-ubi=y` and run `make defconfig`.

> [!NOTE]
> **What this fork adds on top of [upstream](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed)**
>
> - W1701K support for UBI 2.0 images, both EN8811H ports and tri-band Wi-Fi.
> - [Gateway and AP defaults](docs/xr1710g-full.md), with LuCI improvements, command-line tools, wrtix and wrtmesh.
> - [Staged boot recovery](docs/xr1710g-ap-operations.en.md). Repeated boot failures trigger OpenWrt failsafe. If failsafe also fails, the device enters second-stage U-Boot web recovery. Migration can preserve settings, and LuCI shows recovery status and offers a one-shot **Reboot to U-Boot** button. These recovery features require a [second-stage U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/README.en.md) that supports the protocol.
> - [Airoha bridge offload fixes](docs/airoha-bridge-fallback-lifecycle.md) for forwarding state, FDB lifecycle and aging.

## Downloads and initial network settings

Choose the device, partition layout and role from the [latest release](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest). Releases provide XR1710G Legacy and W1701K UBI 2.0. Use the `-sysupgrade.itb` for an upgrade. The release also includes matching APKs, exact source archives and build manifests. Verify `sha256sums` and back up your settings before flashing.

| Role | Network after a fresh install or factory reset |
| --- | --- |
| **Gateway** | The WAN port (the left port on the W1701K) acquires an address with DHCP. The LAN serves DHCP at `192.168.1.1` and IPv4 is NATed through the WAN. |
| **Access point (AP)** | All Ethernet ports share a bridge. Look up its management address in the upstream DHCP lease table. The AP runs no DHCP or IPv6 RA server. |

Both roles start with LAN-side SSH on port 22, an empty `root` password and Wi-Fi and roaming disabled. Use a trusted network on first boot, set management credentials and select the correct wireless regulatory domain and security settings. A settings-preserving upgrade keeps the existing network settings. Manually installed APKs, especially kernel modules, may need to be reinstalled using builds for the new firmware.

## Installation, upgrades and recovery

We recommend the matching [second-stage U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/README.en.md). Existing compatible bootloaders can still be used, and both projects can be upgraded independently.

1. **First installation** starts with checking the device, partition layout and the slot format accepted by the current bootloader. Follow the serial guide for [XR1710G](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/flashing-xr1710g.md) or [W1701K](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/flashing-xr1710g.md#w1701k).
2. **Existing OpenWrt installations** can follow the [settings-preserving upgrade guide](docs/xr1710g-ap-operations.en.md#keep-settings-during-an-upgrade) to verify the image signature and layout. A native firmware upgrade leaves the second-stage U-Boot and partition layout in place.
3. **Boot recovery** falls back from a normal boot to OpenWrt failsafe and then to U-Boot web recovery when the matching bootloader and persistent state volume are available. LuCI's **System → Reboot** page shows recovery status and offers **Reboot to U-Boot** when available. See [daily operation and recovery](docs/xr1710g-ap-operations.en.md#daily-operation-and-recovery).

## Builds and releases

Development takes place on `master`. The [build workflow](.github/workflows/xr1710g-release.yml) builds the gateway and AP roles for both devices from the same pinned source and dependencies. Official tags use the eight-digit release date `YYYYMMDD`. LuCI Overview and `/etc/openwrt_release` show the device, version and short commit. See the [local build and release guide](docs/xr1710g-releases.md) and [firmware components and defaults](docs/xr1710g-full.md).

This firmware is based on [ImmortalWrt](https://github.com/immortalwrt/immortalwrt) and [naoki66's XR1710G work](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed). It draws on [LuCI](https://github.com/immortalwrt/luci), [mt76](https://github.com/openwrt/mt76), and the [Airoha LuCI applications](https://github.com/naoki66/luci-app-airoha). Licensed under [GPL-2.0-only](https://spdx.org/licenses/GPL-2.0-only.html).
