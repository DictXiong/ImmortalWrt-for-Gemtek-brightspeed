<img src="https://avatars.githubusercontent.com/u/53193414?s=200&v=4" alt="logo" width="200" height="200" align="right">

# ImmortalWrt for Brightspeed / Gemtek：XR1710G 与 XG2010G

[English →](README.en.md) · **简体中文**

[![Build Status](https://img.shields.io/github/actions/workflow/status/naoki66/ImmortalWrt-for-Gemtek-brightspeed/build-firmware.yml?branch=master&label=Build)](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed/actions/workflows/build-firmware.yml)
[![Upstream](https://img.shields.io/badge/upstream-immortalwrt-blue)](https://github.com/immortalwrt/immortalwrt)
[![Kernel](https://img.shields.io/badge/kernel-6.18-red)](https://www.kernel.org/)
[![SoC](https://img.shields.io/badge/SoC-Airoha%20AN7581-orange)]()
[![License](https://img.shields.io/badge/license-GPL--2.0-green)](https://spdx.org/licenses/GPL-2.0-only.html)

> 基于 [ImmortalWrt](https://github.com/immortalwrt/immortalwrt) 的 Airoha AN7581 固件项目，为两款硬件相互隔离的 Brightspeed/Gemtek 设备维护独立的构建配置与镜像：XR1710G（10G Wi-Fi 7 路由器）和 XG2010G（XG(S)-PON/XE-PON 网关）。

**文档基线**：
>naoki `master` @ `9a70b25731dc`（2026-11-15 核对）。
>重要功能与稳定性变更见 [更新日志](CHANGELOG.md)。
>当前内核基线为 `6.18.60`（[`target/linux/generic/kernel-6.18`](target/linux/generic/kernel-6.18)），
>本分支固定集成 naoki `9a70b25731dc`；继承的自动同步写入工作流已停用。

[完整版固件](docs/xr1710g-full.md)提供通用路由功能、改进的 WebUI、命令行工具、
可选的 wrtix 配置管理与有线回程漫游软件包。初始配置沿用 OpenWrt 的 LAN/WAN 行为，
无线默认关闭，不包含个人公钥、密码、维护地址或私人 AP 策略。
[双角色发布流程](docs/xr1710g-releases.md)从同一份 Full 源码构建 `gateway` 网关和
`ap` 接入点两种镜像，共用驱动、软件包与 wrtix，仅默认网络配置不同。
Fixes 通用修复版已停止维护和新增构建；历史标签及发布附件保留。

`master` 是后续开发主线；维护分支及已有 tags 保留。当前开发版本为 `3.1.1-alpha.1`，不代表正式发布或新基线已通过设备验收。

## 速览

| 设备 | 定位 | 种子配置 | 当前启用的 profile | 引导方式 / 状态   |
|------|------|----------|--------------------|------------------ |
| XR1710G（原厂 U-Boot 分区 + chainloader） | 10G Wi-Fi 7 路由器 | [`1710.config`](1710.config) | `gemtek_xr1710g` | [naoki66/Gemtek-brightspeed-chainloader](https://github.com/naoki66/Gemtek-brightspeed-chainloader) · [AP 容错配套分支](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/tree/main)   |
| XR1710G（OpenWrt U-Boot UBI 布局） | 整盘 UBI 引导方案 | [`1710.config`](1710.config) | profile 已定义，种子配置未启用 | **暂未完成：缺少 trueKey**   |
| XG2010G（1 GiB / 2 GiB 自适应） | XG(S)-PON 网关 | [`2010.config`](2010.config) | `gemtek_xg2010g-ubi` | [naoki66/uboot-an758x](https://github.com/naoki66/uboot-an758x)   |
| Nokia XG-040G-MD / XG-040G-TF（UBI，512 MiB / 1 GiB 自适应） | 附加设备配置 | [`040.config`](040.config) / [`040-tf.config`](040-tf.config) | 各自独立配置，CI 不提供 | [naoki66/uboot-an758x](https://github.com/naoki66/uboot-an758x)   |

> [!WARNING]
> XR1710G 与 XG2010G 虽然同属 Airoha AN7581 平台，但分区布局、设备树和软件包集合完全不同，**不能互刷固件**；同一设备的原厂分区与 UBI 布局镜像也因 `SUPPORTED_DEVICES` 不同而不能互刷。
>
> XR1710G 的 OpenWrt U-Boot UBI 布局**暂未完成（缺少 trueKey）**；当前 XR1710G 使用「原厂 U-Boot 分区 + chainloader」方案，详见[引导方案与完成状态](#引导方案与完成状态)。

## 目录

[快速开始](#快速开始) · [支持设备](#支持设备) · [XR1710G AP 启动容错](#xr1710g-ap-启动容错) · [首次启动默认设置](#首次启动默认设置) · [构建与发布](#构建与发布) · [刷写与升级注意事项](#刷写与升级注意事项) · [致谢](#致谢) · [许可证](#许可证)

## 快速开始

**1. 下载并校验镜像。** 打开 [Releases 页面](https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed/releases)，选择与设备、分区布局完全匹配的 `*-squashfs-sysupgrade.itb`，并下载同批次按配置区分的 `sha256sums-<配置文件名>`。在镜像所在目录执行：

```bash
sha256sum -c sha256sums-1710.config   # XR1710G
sha256sum -c sha256sums-2010.config   # XG2010G
```

Release 同时提供软件包 manifest，可用来确认镜像预装内容；不同构建配置共用同一个 Release 时，请使用与固件对应的校验文件。

**2. 刷写。** 从已有系统升级时，在 LuCI 中打开「系统 → 备份/升级 → 刷写固件」并选择镜像。首次从原厂固件刷入、或改用 UBI 布局时，请先阅读[刷写与升级注意事项](#刷写与升级注意事项)，XG2010G 必须先通过串口确认启动状态并备份原厂分区；XR1710G 需先按[引导方案](#引导方案与完成状态)写入 chainloader，UBI 布局镜像当前不可用。

**3. 首次登录后立即修改凭据。** 默认 `root` 无密码，Wi-Fi 使用出厂 SSID 和统一密码。先设置管理密码，再修改无线密码与区域码，然后到 NPU / 风扇页面确认硬件加速和温控状态。

## 支持设备

### 硬件与配置对比

| 项目 | XR1710G | XG2010G | Nokia XG-040G-MD | Nokia XG-040G-TF |
|------|---------|---------|------------------|------------------|
| **定位** | 10G Wi-Fi 7 路由器 | XG(S)-PON 网关 | XG(S)-PON 网关 | XG(S)-PON 网关 |
| **SoC** | Airoha AN7581GT（1.3 GHz 4 核 + 8 核 NPU） | Airoha AN7581 / EN7581 | Airoha AN7581 / EN7581 | Airoha AN7581 / EN7581 |
| **内存** | 2 GiB DDR4 | 1 GiB DDR4；自适应魔改 2 GiB | 512 MiB / 自适应魔改 1 GiB | 512 MiB / 自适应魔改 1 GiB |
| **闪存** | 512 MiB SPI-NAND | 512 MiB SPI-NAND（W25N04KV） | 256 MiB SPI-NAND（`all_flash` 0x10000000） | 同 MD |
| **以太网** | 2×10G RTL8261BE + 2×1G AN7581 | 2×10G RTL8261N + 1×2.5G EN8811H + 1G 交换端口 | 1×2.5G EN8811H + 3×1G 交换端口（lan2–lan4） | 同 MD |
| **光接入** | 无（纯网口设备） | EN7572 xPON 前端 | EN7572 xPON 前端 | EN7572 xPON 前端 |
| **无线** | MT7996AV BE19000，三频 1376 / 5760 / 11520 Mbps | 无 | 无 | 无 |
| **USB** | 无 | 无 | 2 个 USB 端口 | 无 |
| **风扇 / 供电** | NCT7802 PWM 风扇；12 V 5 A | — | — | — |
| **设备树** | [`an7581-xr1710g.dts`](target/linux/airoha/dts/an7581-xr1710g.dts)（另有未完成的 UBI 布局 DTS） | [`an7581-gemtek-xg2010g-ubi.dts`](target/linux/airoha/dts/an7581-gemtek-xg2010g-ubi.dts) | [`an7581-nokia_xg-040g-md-ubi.dts`](target/linux/airoha/dts/an7581-nokia_xg-040g-md-ubi.dts) | [`an7581-nokia_xg-040g-tf-ubi.dts`](target/linux/airoha/dts/an7581-nokia_xg-040g-tf-ubi.dts) |
| **种子配置** | [`1710.config`](1710.config) | [`2010.config`](2010.config) | [`040.config`](040.config) | [`040-tf.config`](040-tf.config) |
| **引导 / 状态** | 原厂 U-Boot + chainloader；UBI 布局暂未完成（缺少 trueKey） | naoki66/uboot-an758x | naoki66/uboot-an758x | 同 MD |

> 表中参数取自各设备 DTS、[`an7581.mk`](target/linux/airoha/image/an7581.mk) 与根目录 `*.config`；「—」表示仓库未给出该项。XR1710G 三频芯片为 MT7976GN / MT7977BN / MT7977AN，峰值为 802.11be 理论 PHY 速率，合计约 18.66 Gbps（BE19000 级别）；精确 MCS 记法为 1375 / 5765 / 11530 Mbps，差异仅来自取整。来源：[D-Link DPN-BE19000GR](https://www.dlink.com.sg/products/be19000-tri-band-10g-wi-fi-7-router)、[WikiDevi Brightspeed XR1710G](https://wikidevi.wi-cat.ru/Brightspeed_XR1710G)、[MediaTek Filogic 880](https://www.mediatek.com/zh-tw/products/broadband-wifi/mediatek-filogic-880)。

`1710.config` 当前 `CONFIG_TARGET_MULTI_PROFILE is not set`，一次构建只产出 `gemtek_xr1710g` 一份固件；`2010.config` 只启用 `gemtek_xg2010g-ubi`。CI 的 `config_seed` 输入仅接受 `1710.config` 与 `2010.config`（默认 `1710.config`）。

### 引导方案与完成状态

> 维护者备注（2026-11-10）：引导实现在独立仓库维护，下表说明本固件仓库与它们的对应关系。

| 设备 / 布局 | 引导实现 | 状态 |
|-------------|----------|------|
| XR1710G（原厂 U-Boot 分区 + chainloader） | [naoki66/Gemtek-brightspeed-chainloader](https://github.com/naoki66/Gemtek-brightspeed-chainloader) | 当前方案；保留原厂前 6 MiB，在 NAND `0x600000` 写入 1 MiB chainloader 槽位 |
| XR1710G（OpenWrt U-Boot UBI 布局） | 需从 `0x0` 重建引导区 | **暂未完成：缺少 trueKey**，请勿刷写该布局镜像 |
| XG2010G（1 GiB / 2 GiB 自适应） | [naoki66/uboot-an758x](https://github.com/naoki66/uboot-an758x)（`xg2010g` / `xg2010g-2g`） | 使用中 |
| Nokia XG-040G-MD / XG-040G-TF（UBI，512 MiB / 1 GiB 自适应） | [naoki66/uboot-an758x](https://github.com/naoki66/uboot-an758x)（`xg-040g-md` / `xg-040g-tf` 及 `-1g` target） | 使用中 |

> [!WARNING]
> XR1710G 的 OpenWrt U-Boot UBI 布局暂未完成（缺少 trueKey）。当前请只使用「原厂 U-Boot 分区 + chainloader」方案，不要刷写 `gemtek_xr1710g-ubi` 镜像。XG2010G 与 Nokia 设备由 `naoki66/uboot-an758x` 引导，其引导区布局与 chainloader 方案不同，不可混用。

## XR1710G AP 启动容错

Full Legacy AP 搭配 [DictXiong 的二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader) 后，支持 **普通启动 → OpenWrt failsafe → U-Boot 网页恢复**。原厂一级和 Legacy NAND 布局继续沿用。容错只在配套启动器识别到带恢复协议标记的 Legacy AP FIT 时启用。Gateway、其他设备与未完成的 XR1710G 全盘 UBI 布局不在适用范围内。

| 阶段 | 如何进入 | 如何恢复 |
| --- | --- | --- |
| 普通启动 | 启动器与 OpenWrt 记录本次启动，OpenWrt 在就绪后确认。连续两次普通启动未确认时，看门狗复位并进入 failsafe。 | 正常运行时沿用原管理地址和配置。 |
| OpenWrt failsafe | 自动进入，或使用原有的手动 failsafe 方式。 | 直连网线后通过 `192.168.1.1:22` SSH 维护。自动 failsafe 也未确认时，看门狗复位到 U-Boot 网页。 |
| U-Boot 网页恢复 | 自动降级、按引导仓库说明使用硬件按键，或在已确认的正常系统中以 root 执行一次 `reboot-to-uboot`。 | 打开 `http://192.168.0.1/`。维护完成后可从网页重启。软件请求由配套启动器消费一次。 |

救援访问覆盖 10G WAN、10G LAN 和两个 1G LAN。OpenWrt failsafe 逐口启用并隔离可用端口，U-Boot 自动模式从可用端口提供网页。桥端口隔离只限制设备自身的端口转发。failsafe 提供无需密码的 root SSH，U-Boot 网页可刷写且默认提供 DHCP。请在可信隔离网络或直连环境使用恢复功能，尤其注意自动进入恢复时仍连接的网络。

### 首次安装和保留配置

两条安装路径都先做外部备份。若当前使用其他 U-Boot，先核对它接受的二级升级包、实际写入范围，以及是否会清理引导环境或配置。按当前引导器支持的方式将[配套二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/tree/main)写入 Legacy 的 1 MiB 二级槽，保留原厂一级。在隔离网络上通过恢复页核对 build ID，确认后再刷 FIT。

- **全新安装**使用配套二级 U-Boot 的网页恢复。选择 [Full Legacy AP 构建](docs/xr1710g-full.md#building)（`--role ap --layout legacy`）的原始 sysupgrade FIT，并按引导仓库说明操作。网页刷固件会重建系统 UBI，清除现有配置。另请确认已备份设备的原厂数据。
- **保留配置升级**确认旧系统的原生镜像检查（`sysupgrade -T`）通过，再正常保配置升级。旧升级器可能把全部剩余空间留给 `rootfs_data`，首次启动后先用 `ubinfo -a` 检查 `xr_boot_state` 是动态卷且只占一个 LEB，再执行 `/lib/xr-boot-recovery verify-ready >/dev/null && echo READY`。状态卷有效且显示 READY 时即可结束。仅当新固件已正常启动、状态卷缺失时，才用同一份校验过的 FIT 再做一次正常保配置升级。新升级器会先校验布局和容量，再预留一个 LEB；如状态卷存在但未确认，先检查引导握手或卷状态。
- **兼容性**正常升级仍以设备和 Legacy 布局匹配为前提。其他 U-Boot 或未标记固件只在原有兼容的组合中按原方式启动。自动容错和一次性入口需要双方支持，请使用未转换设备标识的原始 Legacy AP FIT。

先前的隔离实机验证覆盖普通启动未确认后自动进入 failsafe、WAN 与 10G LAN 上的 SSH 和网页、一次性软件入口与正常回程。四个 failsafe 端口的逐口验证，以及 failsafe 未确认后自动进入网页，使用的是之前的候选版本和专用试验镜像。无状态卷的容量与分配、状态写入失败和单 PHY/QDMA 故障隔离已有离线回放。本文描述的最终配套尚未进行整套实机验证，首次保配置迁移也未做现场复测。

## 首次启动默认设置

以下默认地址与无线设置描述 `1710.config` 上游基线。当前发布的 **Full gateway** 使用 `192.168.1.1` 提供 LAN DHCP，WAN 为 DHCP/NAT；**Full ap** 将四口桥接，自身从上游获取 DHCP 地址，不提供 DHCP 服务。两种 Full 均为 root 空密码、LAN 侧 SSH 22、无线初始关闭。默认角色只在清空配置或恢复出厂后生效；保留配置升级不会切换现有角色。完整说明见 [Full 使用说明](docs/xr1710g-full.md)。

以下表格描述上游种子配置；Full 的默认行为以上述说明为准。

| 项目 | XR1710G | XG2010G / Nokia XG-040G-MD / Nokia XG-040G-TF |
|------|---------|-------------|
| 管理地址 | `http://192.168.50.1` 或 `http://immortalwrt.lan` | `http://192.168.1.1` 或 `http://immortalwrt.lan` |
| 用户名 / 密码 | `root` / 无密码 | `root` / 无密码 |
| 无线 SSID | `ImmortalWrt-2G`、`ImmortalWrt-5G`、`ImmortalWrt-6G` | 不适用 |
| 无线密码 | 统一 `12345678` | 不适用 |
| 加密方式 | 2.4 GHz WPA2 (`psk2`)、5 GHz WPA2/WPA3 (`sae-mixed`)、6 GHz WPA3 (`sae`) | 不适用 |
| 无线区域码 | `US`（见 [03_wifi_defaults](package/network/config/airoha-an7581-mt7996-board/files/etc/board.d/03_wifi_defaults)） | 不适用 |

默认 IPv6 使用 SLAAC/EUI-64，关闭 DHCPv6/NDP 与 RA DNS/附加标志，减少国内网络环境下的兼容性问题。

> [!WARNING]
> 出厂凭据是公开信息。首次登录后请立即修改 root 密码与无线密码；在暴露到公网前，也请检查防火墙与 SSH 设置。

## 构建与发布

### 种子配置

| 文件 | 目标 profile | 用途 |
|------|--------------|------|
| [`1710.config`](1710.config) | `DEVICE_gemtek_xr1710g` | XR1710G 原厂分区，CI 默认 |
| [`2010.config`](2010.config) | `DEVICE_gemtek_xg2010g-ubi` | XG2010G UBI 布局，1 GiB / 2 GiB 自适应 |
| [`040.config`](040.config) | `DEVICE_nokia_xg-040g-md-ubi` | Nokia XG-040G-MD，附加配置（512 MiB / 1 GiB 自适应） |
| [`040-tf.config`](040-tf.config) | `DEVICE_nokia_xg-040g-tf-ubi` | Nokia XG-040G-TF，附加配置（512 MiB / 1 GiB 自适应） |

种子文件不会被 `make defconfig` 或 `make menuconfig` 改写，它们只用于 `cp <config> .config`。CI 的 `config_seed` 输入只提供前两项。

### 本地构建

```bash
git clone https://github.com/naoki66/ImmortalWrt-for-Gemtek-brightspeed.git
cd ImmortalWrt-for-Gemtek-brightspeed
./scripts/feeds update -a
./scripts/feeds install -a
bash scripts/fix-stale-golang-host.sh
cp 1710.config .config          # 或 2010.config / 040.config / 040-tf.config
make defconfig
bash scripts/set-build-version.sh .config
make -j$(nproc) world 2>&1 | tee build.log
bash scripts/summarize-build-errors.sh build.log
```

> [!NOTE]
> `make defconfig` 与 `make menuconfig` 只会改写 `.config`。如果执行过这两条命令，编译前请重新执行 `bash scripts/set-build-version.sh .config`，恢复 `CONFIG_VERSION_*`、`CONFIG_TARGET_HOSTNAME`、`CONFIG_EXTRA_IMAGE_NAME` 等构建信息，再运行 `make world`。

构建环境：GNU/Linux（推荐 Debian 11+），AMD64 架构，至少 4 GB RAM 与 25 GB 可用磁盘（并行构建建议更高配置）。依赖清单见 [OpenWrt 构建系统文档](https://openwrt.org/docs/guide-developer/build-system/install-buildsystem)。

构建流程使用 `cp <config> .config && make defconfig && bash scripts/set-build-version.sh .config`，即在 `defconfig` **之后**写入版本字段，避免版本/文件名配置被默认值覆盖。[`scripts/set-build-version.sh`](scripts/set-build-version.sh) 同时写入 LuCI 可见的构建日期与 commit hash：文件名只保留 `日期-本机commit`，完整的 `日期-本机commit-上游commit` 写入 `CONFIG_VERSION_CODE`，可在 LuCI 状态页与 `/etc/openwrt_release` 查看。需要把 revision 也拼进文件名时执行 `VERSION_CODE_FILENAMES=y bash scripts/set-build-version.sh .config`。

### 下载文件

| 布局 | sysupgrade 文件名 |
|------|-------------------|
| XR1710G 原版分区 | `immortalwrt-naoki66-YYYYMMDD-<repo-hash>-airoha-an7581-gemtek_xr1710g-squashfs-sysupgrade.itb` |
| XR1710G OpenWrt U-Boot UBI 布局（**暂未完成，勿刷写**） | `immortalwrt-naoki66-YYYYMMDD-<repo-hash>-airoha-an7581-gemtek_xr1710g-ubi-squashfs-sysupgrade.itb` |
| XG2010G UBI 布局 | `immortalwrt-naoki66-YYYYMMDD-<repo-hash>-airoha-an7581-gemtek_xg2010g-ubi-squashfs-sysupgrade.itb` |

XR1710G UBI 布局**暂未完成（缺少 trueKey）**，即使启用 `gemtek_xr1710g-ubi` profile 产出镜像也不要刷写；当前只使用「原厂 U-Boot 分区 + chainloader」方案。每个 Release 还提供软件包 manifest 与按配置区分的 `sha256sums-<配置文件名>`。

## 刷写与升级注意事项

Full 旧布局仍使用各 2 MiB 的 `bootloader`、`uenv`、`dsd`，`chainloader`/`ubi` 偏移不变。Factory 页面只读展示 DSD，不提供写入入口。


> [!WARNING]
> **XR1710G 引导**：原厂 U-Boot 分区固件必须配合 [naoki66/Gemtek-brightspeed-chainloader](https://github.com/naoki66/Gemtek-brightspeed-chainloader) 引导（保留原厂前 6 MiB，chainloader 写入 NAND `0x600000`）。OpenWrt U-Boot UBI 布局暂未完成（缺少 trueKey），请勿刷写 `gemtek_xr1710g-ubi` 镜像，它还在测试中。

> [!WARNING]
> **XG2010G 首次刷写**：必须通过串口确认当前启动状态，并完成原厂 NAND/关键分区的只读备份和校验。只允许针对匹配的 `ubi` 分区升级；必须保留 `bootloader`、`uenv`、`dsd` 分区。XG2010G 与 Nokia 设备应使用 [naoki66/uboot-an758x](https://github.com/naoki66/uboot-an758x) 引导，不要和XR1710G混用两套引导布局。

> [!WARNING]
> **跨版本升级**：LuCI 的「保留配置」不会保留额外安装的软件包。升级前备份配置并记录已安装的软件包（`apk list --installed` 或 LuCI 已安装软件包列表），升级后需要重新安装未预装组件（例如 `luci-app-adguardhome`）。请使用与新固件匹配的软件包，不要恢复旧固件的 `kmod-*` 内核模块。

## 致谢

### 上游固件

- [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) - ImmortalWrt 主项目
- [immortalwrt/luci](https://github.com/immortalwrt/luci) - LuCI Web 界面
- [immortalwrt/packages](https://github.com/immortalwrt/packages) - 社区软件包仓库
- [openwrt/routing](https://github.com/openwrt/routing) - OpenWrt 路由相关包
- [openwrt/mt76](https://github.com/openwrt/mt76) - MediaTek WiFi 驱动

### 参考项目

- [YYH2913/openwrt](https://github.com/YYH2913/openwrt) - XR1710G 6.18 内核集成参考
- [hurrian/openwrt-w1700k](https://github.com/hurrian/openwrt-w1700k) - XR1710G PCIe 3.0 x2 补丁参考（912 Gen3 速度协商）
- [lvcdy/openwrt_xr1710g](https://github.com/lvcdy/openwrt_xr1710g) - XR1710G 早期移植参考（分区表、PHY 配置）
- [Gilly1970/Gemtek-W1700K](https://github.com/Gilly1970/Gemtek-W1700K) - Airoha 风扇控制与 FlowSense（commit `db3f1c8`）
- [pbs05/uboot-an758x](https://github.com/pbs05/uboot-an758x) - OpenWrt U-Boot 与整盘 UBI 引导方案的上游参考，后续分叉到 [naoki66/uboot-an758x](https://github.com/naoki66/uboot-an758x)
- [naoki66/uboot-an758x](https://github.com/naoki66/uboot-an758x) - XG2010G 与 Nokia XG-040G-MD / XG-040G-TF 使用的 U-Boot / UBI 引导实现

### XG2010G / Nokia XG-040G-MD / XG-040G-TF PON 驱动与用户态

- [pbs05/openwrt-pon-drivers](https://github.com/pbs05/openwrt-pon-drivers) - 早期 6.18 PON 驱动，后续分叉到 [naoki66/openwrt-pon-drivers](https://github.com/naoki66/openwrt-pon-drivers)
- [pbs05/openwrt-pon-userspace](https://github.com/pbs05/openwrt-pon-userspace) - 早期 6.18 PON 驱动，后续分叉到 [naoki66/OpenWrt_ONU_CONFIG](https://github.com/naoki66/OpenWrt_ONU_CONFIG)

### LuCI 应用来源

**[naoki66/luci-app-airoha](https://github.com/naoki66/luci-app-airoha)** 是本固件 Airoha 设备专属 LuCI 应用的主要来源，提供 `luci-app-airoha-npu`、`luci-app-airoha-fancontrol`、`luci-app-netmode` 等界面；具体预装范围以根目录 `*.config` 为准。

其他来源：

- [sirpdboy/luci-app-lucky](https://github.com/sirpdboy/luci-app-lucky) - Lucky 多功能工具

## 许可证

[GPL-2.0-only](https://spdx.org/licenses/GPL-2.0-only.html)（继承 ImmortalWrt）。

## 赞赏

如果这个固件对你有帮助，可以请作者喝杯咖啡。

<img src="c6ea388c976395326514814f80d512d5.png" alt="微信赞赏码" width="300">
