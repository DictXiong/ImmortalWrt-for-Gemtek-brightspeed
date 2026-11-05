# XR1710G Full 双角色发布

当前仅维护 Full 固件，源码位于 `dicttech/xr1710g-ap`。上游更新及通用修复直接合入这条维护线；已提交 PR 的分支、历史 Fixes 分支和已有发布保持原样。Fixes 不再新增构建或发布，重现历史产物时使用对应旧标签的工具。

## 固定输入与角色

每个 Release 从同一个固定 Full 提交构建两种默认角色：

| 角色 | 初始网络 |
| --- | --- |
| `gateway` | WAN 为 DHCP 客户端，LAN `192.168.1.1` 提供 DHCP／NAT |
| `ap` | `wan lan2 lan3 lan4` 全桥接，自身为 DHCP 客户端，不提供 DHCP／RA 服务 |

角色与分区布局是两个独立选择。`legacy`／`ubi` 必须匹配设备现有布局，构建和升级不会转换 U-Boot、Recovery、分区表或校准数据。默认角色仅适用于清空配置或恢复出厂；保留配置升级不重写既有网络。

`configs/xr1710g-release.json` 使用 schema 3，保存 `version` 与固定的 `upstream_base`。当前干净提交同时是发布控制器和两种角色的唯一源码；控制器校验上游基线已合入，拒绝脏源码及 `files/` overlay。公开版本使用 `x.y.z` 或 `x.y.z-alpha.N`／`beta.N`／`rc.N`，标签为 `v` 加版本号；APK 的预发布版本使用其本地排序形式。

两种角色保持相同驱动、软件包清单、feed 与 wrtix 版本。`configs/dicttech-feeds.conf` 固定 feed 提交，`configs/wrtix-source.json` 固定 wrtix 提交和 Git archive 哈希。构建须设置 `WRTIX_SOURCE` 指向匹配的干净 checkout。GitHub Actions 默认从 `DictXiong/wrtix` 获取；`wrtix_repository` 输入优先于 `WRTIX_REPOSITORY` 仓库变量，覆盖仓库仍须满足同一提交和哈希。

## 本地构建与配对

先提交已审查的源码，再从维护线运行：

```sh
python3 scripts/dicttech/release.py plan
python3 scripts/dicttech/release.py prepare --role gateway --layout legacy \
  --directory /tmp/xr-release-gateway
python3 scripts/dicttech/release.py prepare --role ap --layout legacy \
  --directory /tmp/xr-release-ap
python3 scripts/dicttech/release.py build --directory /tmp/xr-release-gateway --jobs 4
python3 scripts/dicttech/release.py build --directory /tmp/xr-release-ap --jobs 4
```

每个构建使用新目录和独立签名密钥。`build --prepare-only` 只安装 feed 并生成配置，不编译镜像。Kconfig 循环依赖会使准备失败；并行编译失败时串行重试并保留错误。完成后检查 FIT／分区布局、rootfs、模块 ABI、APK 签名及软件包清单。

将两个构建的 `public/` 复制到一个只包含这两份公开产物的输入目录后配对：

```sh
python3 scripts/dicttech/release.py assemble --inputs /path/to/role-artifacts \
  --output /tmp/xr-release-pair
python3 scripts/dicttech/release.py notes --pair /tmp/xr-release-pair/release-pair.json \
  --output /tmp/xr-release-notes.md
```

配对要求两种角色齐全，准确源码、布局、feed 和 wrtix 输入一致，软件包及版本清单相同，构建配置仅允许角色开关不同。还会验证实际角色审计结果、镜像／归档哈希和归档成员；不能用改文件名或复用另一角色的镜像凑成一对。

附件前缀为 `xr1710g-full-{gateway|ap}-{layout}-{version}-{commit}`。每种角色提供可直接刷写的 ITB、配套固件／APK 归档、精确源码及来源 JSON。`source.json` 和 `release-pair.json` 使用 schema 4，记录相同的 `source_commit`／`source_tree`、显式 `role` 和固定构建输入。两个源码归档对应同一提交；GitHub 自动源码包也对应主标签。独立签名密钥不会公开，不同构建的 APK／kmod 不应混用；固定输入不承诺二进制逐字节可重现。

## CI 与发布

**Build XR1710G release pair**（`xr1710g-release.yml`）固定一次源码提交，然后在两个隔离 job 中分别构建 `gateway`／`ap`。只有两份产物均通过且配对检查成功，才汇总 Release 附件。完整版单独检查工作流也对两种角色分别运行。

维护线推送发布配置或发布工作流修改时，自动生成 `legacy` 候选；手动运行可以选择布局。`release_tag` 留空时只生成候选 artifacts，填写新版本标签时创建草稿。发布前补充实际测试结果与已知限制，工作流不会覆盖已有标签或 Release。

自动创建草稿需要仓库 Secret `RELEASE_TOKEN`，具有本仓库的 Contents／Workflows 写权限；不使用默认 `GITHUB_TOKEN` 代替。无需创建草稿时保持标签为空。此设置不影响无发布权限的候选构建。

源码、镜像、模拟和实际设备验收必须区分。新 AP 默认角色的真实首次启动、DHCP 供址、管理接入与 wrtix 初始化，只有完成对应实机测试后才能声称通过；已有网关角色或保留配置升级的结果不能替代它。无线、漫游及卸载下访客隔离等结论也只覆盖实际测试场景。

离线回归与 ARM rootfs 验证入口：

```sh
sudo bash scripts/dicttech/check-regressions.sh host
sudo bash scripts/dicttech/check-regressions.sh rootfs /path/to/root-airoha /tmp/xr-target-reports
```
