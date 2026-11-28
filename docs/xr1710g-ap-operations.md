# XR1710G 和 W1701K Full AP 安装、升级与恢复

[English](xr1710g-ap-operations.en.md) · [Full 固件与默认设置](xr1710g-full.md)

XR1710G 选用 Legacy Full AP（`gemtek_xr1710g`），W1701K 选用 UBI 2.0 Full AP（`gemtek_w1701k-ubi`）。自动恢复需要同机型支持协议的[二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader)、原始 Full AP FIT 和有效的 `xr_boot_state`。保留的网络配置可以是 AP 或网关配置。网关构建和 XR1710G 全盘 UBI 布局使用各自的安装路径。

## 首次安装

1. 将设备接入隔离网络，备份原厂关键分区和现有配置，并校验准备使用的镜像与校验文件。
2. 按[二级 U-Boot 安装指南](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/README.md#安装与升级)选择当前引导器接受的完整槽位。安装槽位保留原厂一级。旧引导器的上传规则、环境清理行为取决于具体版本。
3. 从[网页恢复](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/recovery-web.md)核对二级 U-Boot 版本。在 **firmware** 上传项使用对应机型和布局的原始 Full AP `*-sysupgrade.itb`。网页重建系统 UBI 会清除当前配置和 overlay 数据，并为支持协议的 FIT 预留状态卷。
4. 回到正常 OpenWrt 后，从上游 DHCP 租约查找 AP 管理地址，核对固件版本、网络和 SSH，设置管理凭据。默认 AP 本身不提供 DHCP，初始无线关闭。

## 保留配置升级

正常运行的 AP 用 LuCI「备份/升级」或 OpenWrt `sysupgrade` 保留配置。先导出配置备份，记录额外安装的软件包，并以 `sysupgrade -T /tmp/<固件文件>` 验证镜像。手动安装的 APK 可能需要使用新固件的配套版本重新安装，尤其是内核模块。

从未支持状态卷的旧升级器迁入时，第一次写入由**旧系统**执行，旧流程可能把所有剩余空间留给 `rootfs_data`。正常启动后，用 `ubinfo -a` 查看是否有占一个 LEB 的动态卷 `xr_boot_state`，再核对本轮已确认状态。

```sh
/lib/xr-boot-recovery verify-ready >/dev/null && echo READY
```

如果状态卷缺失、且新 AP 固件已正常运行，才用同一份校验通过的 FIT 做一次保留配置升级。此时由新升级器在分配 overlay 前预留状态卷。已有正确状态卷会保留。如果状态卷存在但 READY 未确认，先核对配套二级 U-Boot、原始 FIT 和卷状态。普通系统能启动，也可能尚未启用自动恢复。

## 日常维护与恢复

| 环境 | 接入与处理 |
| --- | --- |
| 正常 OpenWrt | 使用现有管理地址和 SSH 或 LuCI。已确认的配套系统中，root 可运行 `reboot-to-uboot`，下一次启动进入网页恢复。完成维护后从网页重启回正常引导。 |
| OpenWrt failsafe | 电脑设为同网段静态地址，例如 `192.168.1.2/24`，直连可用网口，通过 SSH 登录 `192.168.1.1:22`。使用临时 SSH 主机密钥。需要修复持久配置时，在确认 overlay 可用后运行 `mount_root`，再修改并重启。 |
| 二级 U-Boot 网页恢复 | 电脑用 DHCP 或设为 `192.168.0.10/24`，访问 `http://192.168.0.1/`。从网页按目标备份、刷写，或直接重启。 |

[自动恢复说明与兼容表](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/blob/main/docs/xr1710g-boot-recovery.md)说明切换顺序、主动入口和故障边界。OpenWrt failsafe 会逐口隔离并启用可用端口，XR1710G 尝试四口，W1701K 尝试两口。手动 failsafe 在安全清理失败的临时桥后，可回到原先已验证的独立口。自动 failsafe 需要合格的隔离桥和本地 SSH 监听才确认。

> [!WARNING]
> failsafe 的 root SSH 无密码，U-Boot 网页允许刷写且默认开启 DHCP。恢复前使用可信隔离网络或直连；配置自动恢复时也要考虑设备故障时仍接在哪个网络上。
