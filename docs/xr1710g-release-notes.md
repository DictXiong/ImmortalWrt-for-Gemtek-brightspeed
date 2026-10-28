This release contains two XR1710G editions built from one recorded release
commit. Select the edition and partition layout matching your intended use.

- **full**: DictTech's personal AP firmware, including owner public-key access,
  fleet provisioning, guest policy, wired roaming and personal UI/CLI defaults.
  This edition is intended for the owner's deployment.
- **fixes**: the recorded naoki baseline plus the reviewed WAN RX calibration,
  PPE bridge TTL and mac80211 ONLY_MONITOR fixes. It retains upstream router
  defaults and excludes DictTech public keys, provisioning and personal policy.

Both ITBs are directly downloadable. Each matching archive includes its own
image, manifest, source/config/feed metadata, audit reports and signed APK set.
Check `sha256sums` and `release-pair.json`; do not mix editions' kmods or APKs.
Firmware publisher signing and runtime hardware acceptance are separate from
the bundled integrity/package-signature checks.

These images do not migrate the bootloader/Recovery or partition layout.
Back up before upgrading. Changing between personal and generic editions with
retained configuration can retain the previous edition's keys and policy.

Before publication, add the actual hardware acceptance results and known
issues. Build success alone does not establish warm/cold boot reliability,
MLO/6GHz client behavior or complete guest isolation under hardware offload.
