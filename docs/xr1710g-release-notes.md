这是 Gemtek XR1710G 原厂分区布局的双角色固件。保留配置升级沿用当前网络设置。

- **网关（gateway）**：首次启动时 WAN 获取 DHCP 地址，LAN 在 `192.168.1.1` 提供 DHCP 与 IPv4 NAT。
- **接入点（AP）**：首次启动时桥接四个电口，从上游 DHCP 获取管理地址。AP 自身不提供 DHCP 或 IPv6 RA。

两个角色的首次启动均关闭无线和漫游。设置管理凭据后，再配置无线和远程访问。独立的 wrtix 与 wrtmesh 源码版本和归档校验值见各角色构建清单。

在配套的[二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader)和持久状态卷可用时，AP 支持逐级启动容错。LuCI「系统 → 重启」显示容错状态，并提供一次性「重启到 U-Boot」。详情见 [AP 安装、升级与恢复说明](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/blob/master/docs/xr1710g-ap-operations.md)。

下载与设备角色和布局匹配的 `-sysupgrade.itb`，刷写前核对 `sha256sums` 并备份设置。存量自行安装的 APK 应使用新镜像配套版本重新安装。普通固件升级保留现有分区和二级 U-Boot。
