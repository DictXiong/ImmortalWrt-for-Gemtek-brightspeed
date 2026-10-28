# XR1710G：两条维护分支，一个双版本 Release

日常维护两条活跃分支，已经提交 PR 的分支保持冻结：

| 分支 | 用途 |
| --- | --- |
| `fix/xr1710g-ap-datapath` | 原始三个 fix 的 PR 分支，保持提交和引用不动 |
| `dicttech/xr1710g-fixes` | 跟踪 naoki，维护可用于通用固件的 bug 修复 |
| `dicttech/xr1710g-ap` | 个人定制版，通过 merge 跟进 fixes 分支 |

fixes 分支从原 PR 提交出发合并既有 naoki 基线，保留原始三个 fix 的 SHA。以后通用修复直接追加在 fixes 分支，再由定制分支 merge；不需要把新 fix 插入早期历史，也不需要为此 rebase 已发布或已部署的定制提交。仅涉及个人部署策略、配置契约或自用脚本的修复留在定制分支。

## 固定两个源码提交

定制分支中的 `configs/xr1710g-release.json` 使用 schema 2，记录：

- `version`：与 AP profile 一致的公共版本。
- `upstream_base`：这次 fixes 快照包含的固定 naoki 基线。
- `fixes_commit`：这次通用固件使用的准确 fixes 提交 SHA。

运行 workflow 时，定制版/发布控制器提交先固定为该次运行的 `github.sha`；fixes SHA 从这份提交中的配置读取。控制器检查固定 fixes 提交已经合入定制提交，避免两种版本携带不同批次的通用修复。

构建阶段不再读取移动中的分支头。两个隔离的 detached checkout 分别取准确的定制提交和 fixes 提交，之后独立构建与审计；不会临时拼接补丁列表，也不会在维护分支中来回 checkout。源码提交、Git tree、控制器提交、分区布局和 feed 版本一起进入每份产物的 `source.json`。脏控制器、改动后的源码及额外的 rootfs overlay 会被拒绝。

| 固件 | 固定源码 | 默认行为 |
| --- | --- | --- |
| `full` | 定制分支的发布提交 | DictTech AP 策略、部署契约、所有者公钥、CLI、漫游脚本、定制 UI 和已合入的通用修复 |
| `fixes` | 配置中的 `fixes_commit` | 该 fixes 快照中的上游路由器/软件包默认行为及通用修复；不含个人策略、公钥和定制脚本 |

fixes 构建沿用该快照的 `1710.config` 软件包策略，仅选定一个分区 profile 并写入可区分版本的构建信息。其依赖由定制提交中的 `configs/xr1710g-fixes-feeds.conf` 固定；共用的七个 feed 使用已验证的相同版本，其余上游 feed 单独固定。只应用 fixes 源码本身已有的 feed 补丁，不复制个人 feed 补丁。

## 分区布局与版本切换

一次构建选择 `legacy`（默认）或 `ubi`，产出该布局的两个 sysupgrade ITB。选择 profile 不会转换引导程序或分区表，也不会刷写路由器、Recovery、U-Boot 或校准数据。

对于 `legacy`，通用版保留上游 DTS 的前 6 MiB `vendor` 分区命名；定制版将同一跨度拆分为 `bootloader`、`uenv` 和 `dsd`。两个版本的镜像检查分别验证各自准确的分区表、rootdisk 和 factory cell。

跨定制版/通用版切换前应备份并准备适合目标版本的配置。源码和镜像隔离不会清除路由器已有的 overlay；直接保留旧配置可能留下旧版公钥、访客规则及 AP 配置，即使新镜像不含对应个人软件包。

## 本地构建

先提交经过检查的修改，真实部署输入仍放在本仓库外。从干净的定制分支运行：

```sh
python3 scripts/dicttech/release.py plan
python3 scripts/dicttech/release.py prepare --flavor full --layout legacy \
  --directory /tmp/xr-release-full
python3 scripts/dicttech/release.py prepare --flavor fixes --layout legacy \
  --directory /tmp/xr-release-fixes
python3 scripts/dicttech/release.py build --directory /tmp/xr-release-full --jobs 4
python3 scripts/dicttech/release.py build --directory /tmp/xr-release-fixes --jobs 4
```

每次使用新的构建目录。`build --prepare-only` 只进行 feed 安装和 defconfig，不完整编译固件。两个版本可在独立构建器上并行运行；不会共享可变的构建输出或 APK 签名私钥。

两个版本都会将 Kconfig 循环依赖作为配置错误，在编译前停止。并行编译失败时自动以 `-j1 V=s` 串行重试，保留具体编译错误；只有后续完整编译和产物审计通过才会生成公开候选。
CI 按 runner 实际 CPU 核数设置并行编译，并在日志中记录 CPU、内存和磁盘容量。

每个 `public/` 都包含明确标注版本的可直接刷写 ITB、配套固件/APK 归档、由准确 `source_commit` 导出的独立 `*-source.tar.gz` 源码归档、JSON 来源记录和校验值。归档只收录与该镜像清单匹配、经其公钥验证的 APK，不上传构建器私钥或真实部署配置。两个版本具有各自的内核 ABI 和签名密钥，不能混用 kmod/APK。固定输入不承诺二进制逐字节可重现，因为每次构建仍会生成独立签名密钥。

将两个 `public/` 目录作为输入交给 `assemble`，可得到一个配对目录：

```sh
python3 scripts/dicttech/release.py assemble --inputs /path/to/edition-artifacts \
  --output /tmp/xr-release-pair
python3 scripts/dicttech/release.py notes --pair /tmp/xr-release-pair/release-pair.json \
  --output /tmp/xr-release-notes.md
```

只有两份产物都通过、属于配置中的同一对固定提交且布局一致时才会成功。缺失版本、混入其他提交、修改后的 ITB/归档或额外的私密归档成员会被拒绝。`notes` 使用中文模板与已验证配对记录生成说明，列出真实源码提交，不把构建检查写成不存在的实机验收。

## 一个 GitHub Release

在定制分支运行 **Build XR1710G release pair**（`xr1710g-release.yml`）：

定制分支推送修改发布工作流或 `configs/xr1710g-release.json` 的提交时，也会自动启动 `legacy` 布局的双版本候选构建。这使尚未进入默认分支的工作流也能首次运行；自动触发始终只生成候选 artifacts，不创建 tag 或 Release。其他源码提交可使用手动运行，显式选择布局；尚未在默认分支注册的 workflow 可能不支持 GitHub 的手动调度，可通过一次发布配置更新触发候选构建。

1. 开始时固定并校验 full/fixes 两个 SHA，后续所有 job 使用这对快照。
2. 两个隔离 matrix job 分别构建、审计 `full` 和 `fixes`；任一失败就不会汇总 Release。
3. `release_tag` 留空时，只生成候选 artifacts，不需要额外发布凭据。
4. 填写新标签时，先检查额外发布凭据是否配置；两边构建及配对检查成功后，生成中文 notes 并创建一个草稿 Release。
5. 发布前补充并检查本次实机结果与已知问题。工作流不会覆盖已有 tag 或 Release。

Release 的主 tag 指向固定的定制提交；附件中的通用固件对应 `fixes_commit`，由清单明确标注。GitHub 自动附带的源码 ZIP/tarball 对应主 tag，不代表另一份源码。发布附件因此同时提供两个版本各自的精确源码归档，并校验归档哈希；也可以为准确 fixes SHA 另建 `版本号-fixes` tag，便于浏览，但无需再创建第二个 Release。

原 **Build Firmware** workflow 在个人源码树上仍会拒绝执行，防止把上游配置与个人源码混成第三种固件。**Build DictTech AP** push workflow 保留为定制版候选检查，不发布 Release。

### 自动创建草稿的凭据

创建草稿使用仓库 Secret `RELEASE_TOKEN`，不回退到默认 `GITHUB_TOKEN`。该 token 必须能访问本仓库，并有 **Contents: write** 和 **Workflows: write**；经典 PAT 对应仓库访问及 `workflow` scope。无需创建草稿时，保持 `release_tag` 为空即可。

原因是定制提交相对默认分支修改了 `.github/workflows/`：GitHub 对该类目标提交要求额外的 workflow 写权限，默认 `GITHUB_TOKEN` 无法获得这项权限。详见 [GitHub 创建 Release API 的权限说明](https://docs.github.com/en/rest/releases/releases#create-a-release)。缺少 Secret 会在耗时编译前失败；其具体权限是否充分仍由 GitHub 在创建 Release 时校验，仓库可读不等于具备发布权限。此实现不自动修改 Secret，也没有把未实际调用过的发布路径声称为已在线验证。

## 后续修复与上游同步

通用修复在 `dicttech/xr1710g-fixes` 上开发、验证和提交。naoki 更新也先 merge 到这个分支，再把 fixes 分支 merge 到定制分支；新 fix 只需追加，不重排旧历史或改写原 PR 分支。

完成合并后，在定制分支更新 `upstream_base` 和 `fixes_commit`，审查需要更新的 feed pins，提交固定配置。运行 `plan` 验证祖先关系和两份准确源码，再执行两个版本的构建及适用的实机验收。已被上游采纳的修复按正常 merge/冲突处理维护，发布时不再依赖固定的“前三/第四个 fix”提交位置。

离线回归：

```sh
python3 scripts/dicttech/tests/test_release.py
```

CI 的源码、构建及产物检查不等于实机认证。WAN 长时稳定性、冷/暖启动恢复、实际 MLO/6GHz 客户端、物理漫游和硬件卸载下完整的访客源地址伪造/VLAN 注入矩阵，只有相应实测证据才能标为通过；未测项目继续保留为未测。
