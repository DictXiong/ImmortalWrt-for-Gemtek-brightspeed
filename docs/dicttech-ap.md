# DictTech XR1710G AP deployment

This personal branch supplies a no-secret image, a versioned configuration
contract and deployment/verification tools. The three general datapath fixes
remain separate from the personal AP policy. The device profile owns four
SSIDs, a wired backhaul, native VLAN 1, guest VLAN 192, DHCP management and a
unique static maintenance address. Read [the configuration format](dicttech-config-format.md),
[maintenance allocation](dicttech-maintenance.md) and
[acceptance limits](dicttech-acceptance.md) before deployment.

## Configuration inputs

Keep real input files outside this Git checkout. The current installation uses
the following files in the parent working directory, all mode `0600`:

* `ap-common.json`: schema/profile version and common overrides. An empty
  `common` object uses the complete firmware defaults.
* `ap-devices.json`: stable device IDs, hostnames, maintenance addresses and MAC
  identities; optional per-device overrides and legacy SSID names.
* `ap-secrets.json`: the four Wi-Fi passwords and each device's root password
  hash; optionally its intended authorized public keys.

Combine them from this checkout:

```sh
python3 scripts/dicttech/ap_config.py merge \
  --common ../ap-common.json --devices ../ap-devices.json \
  --secrets ../ap-secrets.json --output ../ap-deployment.json
python3 scripts/dicttech/ap_config.py validate --config ../ap-deployment.json
```

The resulting JSON is also private. Nix and sops may later generate that same
document directly; the deployment tool does not depend on how JSON was
produced. Neither real input files nor rendered archives belong in a public
firmware, Git commit, build log or CI artifact. The public
`/usr/share/dicttech-ap/defaults.json` is the authoritative default policy; the
local overrides are intentionally small. A local/target defaults hash mismatch
is refused rather than silently deploying against another version's policy.

## Build

Use a normal OpenWrt build environment and select the layout matching the
actual bootloader and partition table:

```sh
bash scripts/dicttech/build-ap.sh --layout legacy --jobs 4
```

`--layout ubi` is for a device already using that distinct layout. This command
does not convert partitions or update U-Boot/Recovery. It locks feeds, applies
reviewed source UI patches, checks the minimal AP package policy and builds a
matched image/package set. The **Build DictTech AP** Actions workflow runs on personal-branch pushes
and supports manual dispatch; it uses the same entry point and runs the public
configuration/deployment/roaming checks first. Public builds refuse a root `files/` overlay.
The source must be committed; `--allow-dirty` is reserved for local trials and
marks their version as dirty. Finished-image checks extract the actual rootfs,
compare managed executables/defaults against source and reject embedded device
credentials or unexpected packages.

The maintained **legacy** DTS preserves this existing partition map, with
offsets expressed in MiB:

| Partition | Range |
| --- | --- |
| bootloader | 0–2, read-only |
| uenv | 2–4 |
| dsd | 4–6 |
| chainloader | 6–7 |
| ubi | 7–446 |

Image acceptance must verify the generated legacy DTB against that map. The
`ubi` build profile has a different layout and is not a substitute image for
an existing legacy device. Neither the deployment tool nor a normal image
upgrade should rewrite the bootloader/Recovery or migrate the partition map.

Keep the image, manifest, build/feeds/config metadata, SHA256 sums and matching
APKs together. Updating this firmware normally means rebuilding and upgrading
the complete image; unrelated snapshot kmods are not a matched replacement.
The build does not publish a public binary/package repository automatically.

## A newly flashed device

Without a valid configuration the image provides isolated setup on **lan4**,
`192.168.250.1/24`, SSH port `12022`. Wi-Fi is disabled. Connect a computer
directly to that port and give it, for example, `192.168.250.100/24` with no
gateway/DNS. Complete SSH host-key identification on this isolated connection
and establish any required authentication before using batch deployment.
The blank initial root credential is confined to this physical setup network;
provisioning installs the private password hash before normal operation.

First read the target identity and boot ID:

```sh
python3 scripts/dicttech/ap-deploy.py check --host 192.168.250.1
```

Use the device ID registered in the JSON and the boot ID just observed:

```sh
python3 scripts/dicttech/ap-deploy.py plan \
  --config ../ap-deployment.json --device ap_new --host 192.168.250.1 \
  --initialize --expect-boot-id BOOT_ID
python3 scripts/dicttech/ap-deploy.py apply \
  --config ../ap-deployment.json --device ap_new --host 192.168.250.1 \
  --initialize --expect-boot-id BOOT_ID \
  --receipt ../ap_new-receipt.private.json
```

`ap_new` and `BOOT_ID` are placeholders, not preconfigured identities. Initial
provisioning requires the exact isolated setup state and an explicit boot pin.
A configured device with another ID is always refused. A control socket is
not proof of target identity: both controller and target check the identity.

The default rollback lease is 600 seconds. Apply does not confirm itself. If
the maintenance address changes, reconnect at the device's registered address;
normal management subsequently follows DHCP. Use the same private receipt to
query status, confirm or roll back:

```sh
python3 scripts/dicttech/ap-deploy.py status --host MAINTENANCE_IP
python3 scripts/dicttech/ap-verify.py --help
python3 scripts/dicttech/ap-deploy.py confirm \
  --host MAINTENANCE_IP --receipt ../ap_new-receipt.private.json
```

Confirm only after actual readback and the applicable acceptance checks. A
lost controller/SSH connection does not cancel rollback. Pending unrelated UCI
edits, changed baseline files, a wrong device, incomplete reload or altered
candidate files block the transaction/confirmation. Live SSH-port changes are
refused in v1; choose a non-default port through offline provisioning instead.

## Existing devices and image upgrades

An existing matching profile can use `plan` and `apply` with its current DHCP
or maintenance address, without `--initialize`. The tool sends secrets over
SSH stdin and prints changed paths, not credential values. An identical
configuration is a no-op. Preserve the receipt and private backups until the
result is confirmed.

To preseed a matching image upgrade, export only that device's configuration:

```sh
python3 scripts/dicttech/ap-deploy.py export \
  --config ../ap-deployment.json --device ap_test \
  --preserve-host-identity --host CURRENT_AP_ADDRESS \
  --output ../ap_test-config.private.tar.gz
```

The preservation flag performs a read-only same-device identity check before
capturing existing SSH host keys, HTTPS identity and any authorized keys not
explicitly set in the desired configuration. Use it for existing-device
upgrades, so SSH identity and access survive the custom configuration archive.
Omit it for a fresh device: another AP's host identity must never be cloned.

The gzip tar is a private `sysupgrade -f` input, not a public image. Check the
image/layout with the platform's upgrade test, back up the current system, then
supply this archive to the normal sysupgrade process. The export is suitable
for bootstrapping the new profile; it does not itself provide a firmware-slot
rollback or flash the image. The XR1710G's single firmware slot cannot recover
an unbootable kernel through a userspace configuration guard.

On the already identified target, validate the exact image and private archive
before starting its separately guarded upgrade:

```sh
sysupgrade --no-provisioning -T -f /tmp/ap-config.private.tar.gz /tmp/image.itb
```

Do not force a rejected compatibility check. Follow the
[finished-image gate](dicttech-image-checks.md) and preserve a known recovery
path before running the corresponding upgrade without `-T`.

The initial preseed is checked against a private managed-files manifest before
network services are accepted. The root hash is applied to root's field only;
other accounts and device SSH host keys are retained. Existing device host
keys are not copied into a new device's exported configuration.

The accepted marker allows later GUI password/channel edits to survive normal
boots. `plan` reports that drift before a subsequent explicit deployment.
Missing critical policy material fails closed into setup; invalid material is
preserved in a private quarantine. If firmware default policy changes, export
a new configuration with the matching tool/defaults rather than carrying an
unrecognized old policy forward.

The old manually deployed roaming helper kept its runtime files in sysupgrade
lists. Retire those old code/CLI/UI overrides during the guarded transition:
configuration is preserved, while executable code must come from the new
image. Do not restore a whole old `/etc` or runtime script directory over the
new firmware. The new profile does not contain the old test-only
`192.168.50.1` fallback.

When adding or renaming an AP, regenerate the document and deploy to every
affected AP so their explicit peer lists remain consistent. Differently named
legacy SSIDs do not receive each other's roaming candidates. The AP tool does
not modify pi1 reservations or deploy NixOS; those are separate upstream
network prerequisites.

## Tests and release handling

The public tests exercise the model, transactions and synthetic radio state.
See the component READMEs for commands and optional target-ash checks. The
read-only verifier distinguishes on-AP configuration evidence from real
client/Internet/PPE/isolation/roaming/cold-boot evidence. Missing evidence is
`NOT_TESTED`; `--require-production` refuses an incomplete production gate.

Keep personal policy changes as a coherent feature commit where practical,
with general driver fixes separate. Already published history need not be
rewritten to merge upstream: merge the chosen upstream revision, refresh the
locked feeds/source patches deliberately, rebuild, then repeat acceptance.
Do not automatically deploy an untested upstream merge to production.
