首次在同一个 Release 提供 Gemtek XR1710G 和 Quantum Fiber W1701K 的 Full 固件。W1701K 加入双 EN8811H 网口与三频无线适配，使用 UBI 2.0。保留配置升级沿用当前网络设置。

- **网关（gateway）**：首次启动时 WAN 获取 DHCP 地址，LAN 在 `192.168.1.1` 提供 DHCP 与 IPv4 NAT。
- **接入点（AP）**：首次启动时桥接所有电口，从上游 DHCP 获取管理地址。AP 自身不提供 DHCP 或 IPv6 RA。

本版内置内核和模块 BTF、CAKE、NFQUEUE 和 bridger。CAKE 和 NFQUEUE 随镜像提供，默认没有整形配置或排队规则。bridger 和 IRQ 亲和性调优默认关闭。

两个角色的首次启动均关闭无线和漫游。设置管理凭据后，再配置无线和远程访问。独立的 wrtix 与 wrtmesh 源码版本和归档校验值见各角色构建清单。

20261128 重新发布的四个镜像内置 wrtmesh 6.5.2，支持为 Mesh 对端元数据请求强制使用 HTTPS，也支持跟随同一对端的 HTTP 到 HTTPS 跳转。此版本使用新提交后缀，下载时请使用本页更新后的校验文件。

推荐使用同机型的[二级 U-Boot](https://github.com/DictXiong/Gemtek-brightspeed-chainloader/releases)，已有兼容引导器可继续使用。XR1710G Legacy 与 W1701K UBI 2.0 的 Full AP 在兼容引导器和持久状态卷可用时支持分级启动容错与一次性「重启到 U-Boot」。详情见 [Full AP 安装、升级与恢复](https://github.com/DictXiong/ImmortalWrt-for-Gemtek-brightspeed/blob/master/docs/xr1710g-ap-operations.md)。

下载与设备角色和布局匹配的 `-sysupgrade.itb`，刷写前核对 `sha256sums` 并备份设置。手动安装的 APK 可能需要使用新固件的配套版本重新安装，尤其是内核模块。普通固件升级保留现有分区和二级 U-Boot。
