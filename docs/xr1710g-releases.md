# XR1710G 与 W1701K Full 固件发布

当前仅维护 Full 固件，后续开发主线为 `master`。上游更新及通用修复合入 `master`；`dicttech/xr1710g-ap` 维护分支、已提交 PR 的分支、历史 Fixes 分支和已有发布保持原样。Fixes 不再新增构建或发布，重现历史产物时使用对应旧标签的工具。

## 固定输入与角色

每个 Release 从同一个固定 Full 提交为 XR1710G 和 W1701K 各构建两种默认角色。

| 角色 | 初始网络 |
| --- | --- |
| `gateway` | WAN 为 DHCP 客户端，LAN `192.168.1.1` 提供 DHCP／NAT |
| `ap` | 所有电口合入 LAN 网桥，自身为 DHCP 客户端，不提供 DHCP／RA 服务 |

正式版采用 XR1710G `legacy` 和 W1701K `ubi`。手动构建可以为 XR1710G 选择 `ubi`。分区布局必须匹配设备现状。升级保留 U-Boot、分区布局、校准数据和既有网络设置。默认角色适用于清空配置后的首次启动。

`configs/xr1710g-release.json` 使用 schema 3，保存 `version` 与固定的 `upstream_base`。当前干净提交同时是发布控制器和两种角色的唯一源码；控制器校验上游基线已合入，拒绝脏源码及 `files/` overlay。正式版本与标签均使用北京时间的八位发布日期 `YYYYMMDD`。固件显示名还带源码短哈希。历史发布保留原有标签和附件名。

两种角色保持相同驱动、软件包清单和 feed 版本。`configs/dicttech-feeds.conf` 固定 feed 提交，`configs/wrtix-source.json` 和 `configs/wrtmesh-source.json` 固定两个独立仓库的提交和 Git archive 哈希。本地构建可通过 `WRTIX_SOURCE` 和 `WRTMESH_SOURCE` 指向匹配的干净 checkout。GitHub Actions 默认从 `DictXiong/wrtix` 获取；`wrtix_repository` 输入优先于 `WRTIX_REPOSITORY` 仓库变量，覆盖仓库仍须满足同一提交和哈希。wrtmesh 从 `DictXiong/wrtmesh` 获取。

## 本地构建与发布校验

先提交已审查的源码，再从维护线运行：

```sh
python3 scripts/dicttech/release.py plan
for device in xr1710g w1701k; do
  for role in gateway ap; do
    directory="/tmp/gemtek-release-$device-$role"
    python3 scripts/dicttech/release.py prepare --device "$device" --role "$role" \
      --directory "$directory"
    python3 scripts/dicttech/release.py build --directory "$directory" --jobs 4
  done
done
```

每个角色使用独立构建目录。签名私钥由各自构建环境持有。`build --prepare-only` 只安装 feed 并生成配置，不编译镜像。Kconfig 循环依赖会使准备失败；并行编译失败时串行重试并保留错误。完成后检查 FIT／分区布局、rootfs、模块 ABI、APK 签名及软件包清单。

将四个构建的 `public/` 放在同一个输入目录下汇总。

```sh
python3 scripts/dicttech/release.py assemble --inputs /path/to/role-artifacts \
  --output /tmp/xr-release-pair
python3 scripts/dicttech/release.py notes --pair /tmp/xr-release-pair/release-pair.json \
  --output /tmp/xr-release-notes.md
```

汇总要求两个机型的两种角色全部齐全，四份产物使用相同源码、锁定 feed 和 wrtix／wrtmesh 输入。同一机型的两种角色使用相同布局、软件包清单和版本，构建配置仅允许角色开关不同。汇总检查各镜像的板型、分区布局、实际角色、校验值和归档成员。若给 XR1710G 构建了 UBI 版，汇总时指定 `--xr1710g-layout ubi`。

附件前缀为 `immortalwrt-{device}-{role}-{layout}-{YYYYMMDD}-{commit}`。各组合提供 ITB、配套固件／APK 归档、精确源码及来源 JSON。`source.json` 和 `release-pair.json` 使用 schema 6，记录 `device`、`role`、分区布局和固定构建输入。镜像、软件包和内核模块按机型及构建配套使用。GitHub 自动源码包对应主标签。

## CI 与发布

**Build ImmortalWrt for XR1710G and W1701K**（`xr1710g-release.yml`）固定一次源码提交，在四个隔离 job 中按机型和角色构建。所有产物通过检查后才汇总 Release 附件。源码、ARM 程序和浏览器检查均由此工作流完成。

推送 `master` 会构建两机型的四份候选。手动运行可选择 XR1710G 布局，新推送不会取消显式调度的发布。正式发布使用 `master` 上经验证的同一个提交创建日期标签。`release_tag` 留空只生成候选，填写日期可为当前远端 `master` 生成包含四份固件的草稿。重新构建的附件应按自身哈希另做验收。

自动创建草稿需要仓库 Secret `RELEASE_TOKEN`，具有本仓库的 Contents／Workflows 写权限；不使用默认 `GITHUB_TOKEN` 代替。无需创建草稿时保持标签为空。此设置不影响无发布权限的候选构建。

源码、镜像、模拟和实际设备验收必须区分。新 AP 默认角色的真实首次启动、DHCP 供址、管理接入与 wrtix 初始化，只有完成对应实机测试后才能声称通过；已有网关角色或保留配置升级的结果不能替代它。无线、漫游及卸载下访客隔离等结论也只覆盖实际测试场景。

离线回归与 ARM rootfs 验证入口：

```sh
sudo bash scripts/dicttech/check-regressions.sh host
sudo bash scripts/dicttech/check-regressions.sh rootfs /path/to/root-airoha /tmp/xr-target-reports
```

## 固件构建时间

概览的“固件构建时间”来自镜像内普通静态资源 `firmware-build-time.json`，记录本次 rootfs 镜像打包起点的 UTC Unix 秒，按浏览器本地时区显示。它不是源码提交日期、每个软件包的编译时间或全部制品完成时刻。每次实际重新生成 rootfs 时更新；旧镜像资源缺失或无效时显示未知，不猜值回填。标准 `OPENWRT_BUILD_DATE`、`release.builddate` 与 `SOURCE_DATE_EPOCH` 保持原语义及格式。
