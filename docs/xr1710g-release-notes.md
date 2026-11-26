这是 Gemtek XR1710G 原厂分区布局的双角色固件。保留配置升级沿用当前网络设置。

- **网关（gateway）**：首次启动时 WAN 获取 DHCP 地址，LAN 在 `192.168.1.1` 提供 DHCP 与 IPv4 NAT。
- **接入点（AP）**：首次启动时桥接四个电口，从上游 DHCP 获取管理地址。AP 自身不提供 DHCP 或 IPv6 RA。

本版内置内核和模块 BTF、CAKE、NFQUEUE 和 bridger。CAKE 和 NFQUEUE 随镜像提供，默认没有整形配置或排队规则。bridger 和 IRQ 亲和性调优默认关闭。

修复 netdev LED 绑定后模式设置的顺序，并限制风扇曲线只写入识别出的 NCT7802。内核模块 ABI 已更新，请使用本版配套 APK。

两个角色的首次启动均关闭无线和漫游。设置管理凭据后，再配置无线和远程访问。独立的 wrtix 与 wrtmesh 源码版本和归档校验值见各角色构建清单。

在配套的[二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader)和持久状态卷可用时，AP 支持逐级启动容错。LuCI「系统 → 重启」显示容错状态，并提供一次性「重启到 U-Boot」。详情见 [AP 安装、升级与恢复说明](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/blob/master/docs/xr1710g-ap-operations.md)。

下载与设备角色和布局匹配的 `-sysupgrade.itb`，刷写前核对 `sha256sums` 并备份设置。存量自行安装的 APK 应使用新镜像配套版本重新安装。普通固件升级保留现有分区和二级 U-Boot。
