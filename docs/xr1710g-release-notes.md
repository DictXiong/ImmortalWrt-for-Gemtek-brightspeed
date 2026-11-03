XR1710G 3.0.0-alpha.3 同步 naoki 上游，新增 MT7996 Beacon Protection 能力声明，并完善旧布局与新 UBI 布局的升级说明。

- **`full` 完整版**：通用硬件/驱动修复、改进的 WebUI、zsh/Vim/tmux 离线终端环境、可选的 `wrtix-agent` 配置管理与 `xr-roaming`。可直接作为普通 OpenWrt 使用，无需 Nix。
- **`fixes` 通用修复版**：固定 naoki 上游快照与通用硬件/驱动修复，保留该基线的软件包和默认行为。

`full` 默认 LAN 为 `192.168.1.1` 并提供 DHCP，WAN 使用 DHCP/NAT；无线和漫游关闭。不预置个人公钥、共享密码、维护地址或 AP 策略。初始用户名 `root`、密码为空，SSH 为 LAN 端口 22；请先从可信 LAN 设置密码，再配置网络和无线。wrtix 用户使用与内置 agent 匹配的控制器，由自己的配置声明热点、VLAN、地址与秘密。

下载匹配现有分区布局的 `.itb` 并核对 `sha256sums`。旧固件名称中的 `-ubi` 不能单独判断布局；Full 旧布局的 `bootloader/uenv/dsd` 与 fixes 的 `vendor` 覆盖相同的前 6 MiB 区域。升级不转换分区，不更新 U-Boot、Recovery 或原厂校准数据。附件提供匹配的 APK、精确源码和构建记录，不应混用不同构建的软件包。

Beacon Protection 需要客户端支持；驱动能力声明不等于已经验证实际 Beacon 完整性保护。测试结论以本次候选的构建和实机验收记录为准。部分客户端的受控漫游仍可能出现约 2–3 秒中断；短测不能代表全部客户端兼容性或长期稳定性。
