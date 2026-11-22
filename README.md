# ImmortalWrt for XR1710G

[![构建](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml/badge.svg?branch=master)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/xr1710g-release.yml)
[![最新发布](https://img.shields.io/github/v/release/DictXiong/ImmortalWrt-for-Gemtek-brightspeed?sort=date)](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest)
[English](README.en.md)

面向 Gemtek XR1710G 的 ImmortalWrt 固件。支持三频 Wi-Fi 7、两只 10G 和两只 1G 电口，集成 Airoha 管理页、LuCI、可选的有线回程漫游管理 [wrtmesh](https://github.com/DictXiong/wrtmesh) 和声明式配置工具 [wrtix](https://github.com/DictXiong/wrtix)。本仓只维护并发布 XR1710G 固件，XG2010G 和其他机型不在维护范围。

## 下载和初始网络

从[最新发布](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/releases/latest)选择角色和匹配设备的 Legacy 镜像。直接升级使用 `-sysupgrade.itb`，配套 APK、精确源码与构建清单见同一 Release。刷机前核对 `sha256sums` 并备份配置。

| 角色 | 首次启动或恢复出厂后的网络 |
| --- | --- |
| **网关（gateway）** | WAN 通过 DHCP 获取地址，LAN 管理地址为 `192.168.1.1`，提供 DHCP，IPv4 经 WAN NAT。 |
| **接入点（AP）** | 四个电口加入同一网桥，从上游获取管理地址。到上游 DHCP 租约表查找地址。设备不提供 DHCP 或 IPv6 RA。 |

两种角色均以 LAN 侧 SSH 22 和空密码的 `root` 开始，无线与漫游初始关闭。首次启动应在可信网络中进行，登录后设置管理凭据，并按实际地区选择无线监管域和加密参数。保留配置升级沿用设备已有网络。额外手动安装的 APK 需要使用新固件的配套版本重新安装，尤其是内核模块。

## 安装、升级和恢复

1. **首次安装**采用原厂分区布局及配套的[二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader#安装与升级)。先确认当前引导器接受的槽位格式。XR1710G 的整盘 UBI 布局尚不适用于本发行版。
2. **已有可用系统**按[保留配置的升级说明](docs/xr1710g-ap-operations.md#保留配置升级)验签和核对布局。原生升级不会替换二级 U-Boot 或转换分区。
3. **AP 启动容错**在配套二级 U-Boot 和持久状态卷可用时，从正常启动逐级降级到 OpenWrt failsafe 和 U-Boot 网页恢复。LuCI「系统 → 重启」显示容错状态，并在条件满足时提供「重启到 U-Boot」。测试或维护请使用可信直连或隔离链路。步骤和各阶段地址见[日常维护与恢复](docs/xr1710g-ap-operations.md#日常维护与恢复)。

## 构建与发布

`master` 是开发主线。[构建工作流](.github/workflows/xr1710g-release.yml)从同一源码和依赖锁分别生成网关与 AP 镜像。正式发布使用北京时间的八位日期 `YYYYMMDD` 作为标签，设备从概览和 `/etc/openwrt_release` 显示 `ImmortalWrt for XR1710G YYYYMMDD-提交短哈希`。见[本地构建与发布流程](docs/xr1710g-releases.md)和[固件组件与默认配置](docs/xr1710g-full.md)。

本项目基于 [ImmortalWrt](https://github.com/immortalwrt/immortalwrt) 与 [naoki66 的 XR1710G 工作](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed)。相关上游包括 [LuCI](https://github.com/immortalwrt/luci)、[mt76](https://github.com/openwrt/mt76) 和 [Airoha LuCI 应用](https://github.com/naoki66/luci-app-airoha)。许可证为 [GPL-2.0-only](https://spdx.org/licenses/GPL-2.0-only.html)。
