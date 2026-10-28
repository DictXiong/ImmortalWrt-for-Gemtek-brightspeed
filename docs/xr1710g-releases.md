# One branch, two XR1710G editions

Maintain `dicttech/xr1710g-ap` as the only evolving release branch. Leave the
three-commit PR branch `fix/xr1710g-ap-datapath` unchanged. Existing personal
commits are retained: squashing already pushed and deployed history would lose
useful regression references without reducing the eventual upstream merge diff.
The new paired-release infrastructure is one separate change. Future work can
remain in small, reviewable commits; release preparation never rewrites history.

## Source boundaries

| Edition | Firmware source | Default behavior |
| --- | --- | --- |
| `full` | Exact release commit on the personal branch | DictTech AP profile, deployment contract, CLI/owner public keys, roaming helper, personal UI and all three fixes |
| `fixes` | Recorded naoki baseline plus only the reviewed fix commits | Upstream router/package/default policy; no DictTech provisioning, owner keys, AP policy, scripts or personal UI patches |

`configs/xr1710g-release.json` pins the upstream baseline, public version and
ordered fix commit IDs. The initial fixes are pre-E2 WAN RX calibration, PPE
bridge TTL preservation and mac80211 ONLY_MONITOR decapsulation handling.
Upstream changes already merged into the recorded baseline are included in
both editions. An already incorporated identical fix is recorded as such;
patch conflicts stop the build and require review.

The controller makes two fresh detached local clones of the **same release
commit**. The fixes clone checks out the pinned upstream commit and applies the
three commits to its index without creating a new commit or maintaining another
branch. Its tree is checked against upstream plus the allowed fix paths, and
the final fix contents must equal those in the full edition. Full source must
equal the exact release tree. Rootfs overlays, dirty controllers, modified
sources and unexpected untracked inputs are refused.

This is deliberately not a Kconfig switch: personal changes also touch shared
boot code, hostapd, LuCI patches and DTS. Disabling just `ap-profile` would leave
some of those changes in a supposedly generic image.

The fixes build starts with upstream `1710.config`, retaining its package
policy and selecting just the requested partition profile. Feed repositories
are pinned by `configs/xr1710g-fixes-feeds.conf`; the seven shared feeds use the
same revisions as the validated personal build. Extra stock feeds are pinned
separately. Only feed patches present in the upstream source are applied.
Personal package/UI/boot changes are not copied into that tree.

## Partition layouts and upgrades

Every invocation chooses `legacy` (default) or `ubi`. These are distinct device
profiles, not a bootloader/partition migration. A release contains exactly two
sysupgrade ITBs for the selected layout, rather than mixing both layouts.

For `legacy`, the stock upstream DTS calls the first 6 MiB `vendor`; the full
edition splits that span into `bootloader`, `uenv` and `dsd`. Each edition's
finished-image checker validates its own exact map, rootdisk and factory cells.
Neither build flashes a router, Recovery, bootloader or factory data.

The generic edition is **not** a replacement for a provisioned full edition
with a retained personal overlay. Back up before crossing editions and use
fresh configuration appropriate to the destination. Otherwise retained owner
keys, guest rules and AP configuration can outlive the package that owned them.
This source/image separation does not erase configuration already on a device.

## Local entry points

Commit reviewed changes first; real deployment inputs stay outside this tree.
From a clean checkout:

```sh
python3 scripts/dicttech/release.py plan
python3 scripts/dicttech/release.py prepare --flavor full --layout legacy \
  --directory /tmp/xr-release-full
python3 scripts/dicttech/release.py prepare --flavor fixes --layout legacy \
  --directory /tmp/xr-release-fixes
python3 scripts/dicttech/release.py build --directory /tmp/xr-release-full --jobs 4
python3 scripts/dicttech/release.py build --directory /tmp/xr-release-fixes --jobs 4
```

Use fresh directory names for each build. `build --prepare-only` exercises feed
installation and defconfig without compiling firmware. No checkout operation
runs in the maintained source tree; the PR branch is not moved. Both builds
can run simultaneously on independent builders. Download/toolchain caches are
not shared with mutable outputs or signing keys.

Each `public/` contains a clearly named ITB, an archive with firmware, manifest,
config/feeds/version/source metadata, audit reports and **only** its matching
signed APK set, plus a JSON receipt and checksums. Each edition has its own
kernel ABI and signing keys; never mix their kmods/APKs. Random builder signing
keys mean locked inputs alone do not promise bit-for-bit identical binaries.
Private APK signing keys and real deployment data are never release members.
The firmware is not claimed to have a publisher signature merely because its
included APK signatures and FIT hashes passed.

`assemble` accepts two successful edition artifacts belonging to the same
release commit/layout/fix set, checks bundle and ITB hashes and the archive
member allowlist, and emits one pair manifest and checksum list. A missing
edition, mixed commit, modified image or extra private archive file is refused.

## GitHub Actions and releases

Run **Build XR1710G release pair** (`xr1710g-release.yml`) on this branch:

1. Select the layout. Two isolated matrix jobs build and audit `full`/`fixes`.
2. Leave `release_tag` empty for candidate artifacts only.
3. Set a new tag to create **one draft release**, only after both builds and
   pair validation succeed. It includes both directly downloadable ITBs and
   their separate firmware/APK bundles, source receipts and checksums.
4. Review hardware acceptance and release notes before publishing the draft.

The workflow never overwrites an existing tag/release. The original upstream
**Build Firmware** workflow refuses this personal source tree, preventing a
stock-config/personal-source hybrid from being published accidentally. The
existing **Build DictTech AP** push workflow remains a full-edition candidate
check and does not publish releases.

CI validates construction and artifact boundaries; it does not prove physical
WAN stability, cold/warm boot recovery, client MLO/6GHz, roaming or the live
guest spoofing/tag-injection matrix. Keep those acceptance results and known
issues explicit in each release rather than treating a successful compilation
as a hardware certification.

## Updating upstream

Merge naoki into the personal branch normally, update `upstream_base` to that
merged commit, review/update feed pins, and run source projection tests and both
builds. Update the explicit fix list when fixes are adopted or revised; do not
automatically use a moving remote branch as the release base. A conflict or fix
content mismatch blocks the release instead of silently changing its scope.

Offline boundary regression tests:

```sh
python3 scripts/dicttech/tests/test_release.py
```
