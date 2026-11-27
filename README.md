# ImmortalWrt for XR1710G and W1701K

[![构建](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml/badge.svg?branch=master)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml)
[![最新发布](https://img.shields.io/github/v/release/DictXiong/ImmortalWrt-for-Gemtek-brightspeed?sort=date)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest)
[![Upstream](https://img.shields.io/badge/upstream-naoki66%20%40%209a70b257-blue)](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed/commit/9a70b25731dc64822dcb24cf9a7dbe7a84ed1ba1)
[![Kernel](https://img.shields.io/badge/kernel-Linux%206.18-red)](target/linux/generic/kernel-6.18)
[![SoC](https://img.shields.io/badge/SoC-Airoha%20AN7581GT-orange)](target/linux/airoha/dts/an7581-xr1710g.dts)
[![License](https://img.shields.io/badge/license-GPL--2.0--only-green)](COPYING)
[English](README.en.md)

面向 Gemtek XR1710G 和 Quantum Fiber W1701K 的 ImmortalWrt 固件。两款均支持三频 Wi-Fi 7，集成 Airoha 管理页、LuCI、可选的有线回程漫游管理 [wrtmesh](https://github.com/DictXiong/wrtmesh) 和声明式配置工具 [wrtix](https://github.com/DictXiong/wrtix)。

W1701K 的 `gemtek_w1701k-ubi` 目标使用原厂一级引导和 UBI 2.0。自行编译时，在 `make menuconfig` 的 Airoha AN7581 下选择 **Gemtek W1701K (UBI)**，或设置 `CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_w1701k-ubi=y` 后运行 `make defconfig`。

> [!NOTE]
> **本仓在上游基础上加入的功能**
>
> - 新增 W1701K 支持，包括 UBI 2.0 镜像、双 EN8811H 网口和三频无线适配。两个机型均发布 Full 网关和 AP 固件。
> - [网关与 AP 两种默认角色](docs/xr1710g-full.md)，集成 LuCI 改进、命令行工具、wrtix 和 wrtmesh。
> - [AP 分级启动容错](docs/xr1710g-ap-operations.md)、保配置迁移，以及 LuCI 的容错状态和一次性「重启到 U-Boot」。
> - [Airoha 桥接卸载修复](docs/airoha-bridge-fallback-lifecycle.md)，覆盖转发状态、FDB 生命周期及老化。

## 下载和初始网络

从[最新发布](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest)按机型、分区布局和角色选择镜像。正式版提供 XR1710G Legacy 和 W1701K UBI 2.0。直接升级使用 `-sysupgrade.itb`，配套 APK、精确源码与构建清单见同一 Release。刷机前核对 `sha256sums` 并备份配置。

| 角色 | 首次启动或恢复出厂后的网络 |
| --- | --- |
| **网关（gateway）** | WAN 通过 DHCP 获取地址，LAN 管理地址为 `192.168.1.1`，提供 DHCP，IPv4 经 WAN NAT。 |
| **接入点（AP）** | 所有电口加入同一网桥，从上游获取管理地址。到上游 DHCP 租约表查找地址。设备不提供 DHCP 或 IPv6 RA。 |

两种角色均以 LAN 侧 SSH 22 和空密码的 `root` 开始，无线与漫游初始关闭。首次启动应在可信网络中进行，登录后设置管理凭据，并按实际地区选择无线监管域和加密参数。保留配置升级沿用设备已有网络。额外手动安装的 APK 需要使用新固件的配套版本重新安装，尤其是内核模块。

## 安装、升级和恢复

推荐搭配同机型的[二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader)。已有兼容引导器可以继续使用，两个项目均可独立升级。

1. **首次安装**先核对机型、分区布局和当前引导器接受的槽位格式，再按 [XR1710G](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/flashing-xr1710g.md) 或 [W1701K](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/flashing-xr1710g.md#w1701k) 的串口说明操作。
2. **已有可用系统**按[保留配置的升级说明](docs/xr1710g-ap-operations.md#保留配置升级)验签和核对布局。原生升级不会替换二级 U-Boot 或转换分区。
3. **XR1710G Legacy AP 启动容错**在配套二级 U-Boot 和持久状态卷可用时，从正常启动逐级降级到 OpenWrt failsafe 和 U-Boot 网页恢复。LuCI「系统 → 重启」显示容错状态，并在条件满足时提供「重启到 U-Boot」。步骤和各阶段地址见[日常维护与恢复](docs/xr1710g-ap-operations.md#日常维护与恢复)。

## 构建与发布

`master` 是开发主线。[构建工作流](.github/workflows/xr1710g-release.yml)从同一源码和依赖锁构建两款机型的网关与 AP 镜像。正式版以北京时间的八位日期 `YYYYMMDD` 为标签，LuCI 概览和 `/etc/openwrt_release` 显示机型、版本和提交短哈希。见[本地构建与发布流程](docs/xr1710g-releases.md)和[固件组件与默认配置](docs/xr1710g-full.md)。

本项目基于 [ImmortalWrt](https://github.com/immortalwrt/immortalwrt) 与 [naoki66 的 XR1710G 工作](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed)。相关上游包括 [LuCI](https://github.com/immortalwrt/luci)、[mt76](https://github.com/openwrt/mt76) 和 [Airoha LuCI 应用](https://github.com/naoki66/luci-app-airoha)。许可证为 [GPL-2.0-only](https://spdx.org/licenses/GPL-2.0-only.html)。
