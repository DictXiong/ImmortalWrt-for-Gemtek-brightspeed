# DictTech XR1710G configuration and deployment

This branch supplies a no-secret firmware image and a guarded declarative
resource runtime. Nix owns the current AP scenario: SSID defaults, complete MAC
allocation, management/maintenance addresses, VLANs, forwarding policy, hardware
settings and wired-backhaul roaming. The shared backend has no AP presets and
can validate/render other complete OpenWrt resource configurations.

The source protocol is now **v3**, `profile: "openwrt-config-v3"`. Use matching
host tools and firmware; the published v2 runtime cannot accept it. There is no
automatic v1/v2 input migration or defaults merge. Read the
[resource format](dicttech-config-format.md), [maintenance boundary](dicttech-maintenance.md)
and [acceptance limits](dicttech-acceptance.md) before deployment.

The existing Glass footer identifies the image as **DictTech AP · 定制版** using
the theme's ordinary typography. Installed owner tools and the minimal factory
enrollment contract are firmware features; configured network policy comes
from the producer.

## Configuration input

The preferred producer is the Nix OpenWrt module and `openwrt-rebuild`. Keep
secrets in encrypted configuration and decrypt only during deployment. Its
resolved complete resource document is the same protocol consumed by
`ap_config.py`; the backend itself never allocates policy or decrypts secrets.
For standalone validation, provide a complete private JSON document:

```sh
python3 scripts/dicttech/ap_config.py validate --config ../configuration.private.json
```

Keep private input, rendered files, receipts and backups outside the public
source tree and public build artifacts. The firmware's
`/usr/share/dicttech-ap/contract.json` identifies the schema/capabilities only.
Both transaction endpoints pin that exact contract hash.

## Build

For a release containing both the personal image and a generic fixes-only
image from this same branch, use the [paired release workflow](xr1710g-releases.md).
The single-edition command below remains useful for personal candidate builds.

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
compare managed executables/contracts against source and reject embedded device
credentials or unexpected packages.
The build sets umask `022` and restores tracked public source permissions from
Git, including pinned feeds. Previously polluted package/install caches need a
clean rebuild; see [source permission preparation](dicttech-source-permissions.md).
The extracted image also checks LuCI RPC code permissions. APK signing private
keys are generated with umask `077` and are never part of the image.

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

An unprovisioned image has two management entries. **wan/lan2/lan3** form a
temporary native-VLAN-1 bridge and obtain a DHCP address. A private, persistent
random locally administered MAC and `xr1710g-setup-<MAC>` hostname distinguish
fresh APs in the upstream lease list, even if factory MACs were duplicated.
Both the DHCP entry and the offline entry allow SSH on port `12022` as
**root/password** until initialization is confirmed. The reviewed owner public
keys remain available, but no installation-specific private key is required.
The shared image contains a public default-password hash; the virgin shadow
remains blank/locked until the first fresh boot installs that hash. The DHCP
entry has no HTTP listener.

An active initialization transaction retains the default password for address
changes and its one explicitly planned reboot. Confirmation installs the
configured private root-password hash and retires the default credential without
restarting Dropbear, so the authenticated master can receive the result. If no
valid final hash exists, retirement locks the default password. Explicit
`authorized_keys` lists, including `[]`, take effect exactly and are never
supplemented; an empty key list does not disable the initialization password.

The private lifecycle stamp under `/etc/dicttech-ap-bootstrap-key/` is separate
from the managed profile, retained by package upgrades and sysupgrade, and
backed up/restored with initialization transactions. Configured exports carry a
retired stamp and filter out the legacy initialization public key. The helper and stamp use the `bootstrap-key` lifecycle namespace; the old public
key is a revoked credential tombstone, not a granted initialization key. Ordinary reboot, a missing/quarantined
profile, or an existing user password never re-enables the default password.
A full factory reset erases this stamp and intentionally begins a new
initialization. Failed/unconfirmed initialization restores the original
authorization, shadow and lifecycle state through rollback.

**lan4** remains isolated at `192.168.250.1/24`, with HTTP and SSH `12022`.
Connect directly and assign, for example, `192.168.250.100/24` without a gateway
or DNS. This fallback works without a DHCP uplink and uses the same initial
root/password credential. Provisioning installs the private password hash.
Wi-Fi, DHCP server, IPv6 and NAT are disabled during
setup. ARP settings keep the static setup address off the DHCP bridge, avoiding
default-IP collisions during batch enrollment. A quarantined invalid managed
configuration fails closed to **lan4 only**, rather than joining an uplink.

Discovering a DHCP lease is not proof of device identity: verify its SSH host
key on a trusted/physical connection before enrolling it. Do not clone host
private keys from another router.
For password-based setup, establish an interactive master first, then pass its
socket to the batch commands below (and identify the host key before accepting
it). Do not put a password in a shell argument:

```sh
ssh -M -N -f -S /tmp/dicttech-ap-setup -o ControlPersist=1h \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=20 \
  -p 12022 root@192.168.250.1
```

Add `--control-socket /tmp/dicttech-ap-setup` to commands targeting that session.
After provisioning changes the address, establish a new authenticated session
at the assigned address rather than reusing the old socket as identity proof.

On a matching running image, read the target and its boot identity:

```sh
python3 scripts/dicttech/ap-deploy.py check --host DHCP_OR_MAINTENANCE_IP
```

The Nix deployment wrapper supports initial password enrollment through
`openwrt-rebuild ... switch --device DEVICE_ID --target-host root@DHCP_IP --init`.
For standalone deployment, establish a trusted authenticated SSH master first
and pass `--control-socket` to these commands:

```sh
python3 scripts/dicttech/ap-deploy.py plan \
  --config ../configuration.private.json --device DEVICE_ID --host TARGET_IP \
  --initialize --expect-boot-id BOOT_ID
python3 scripts/dicttech/ap-deploy.py apply \
  --config ../configuration.private.json --device DEVICE_ID --host TARGET_IP \
  --initialize --expect-boot-id BOOT_ID --receipt ../receipt.private.json
```

`DEVICE_ID`, `TARGET_IP` and `BOOT_ID` are placeholders. The exact setup proof,
SSH host identity and boot pin authorize initialization; a reused master is not
proof of the target. For an already configured device, omit the initialization
flags. The default rollback lease is 600 seconds. Apply does not confirm itself;
reconnect at the declared access address and inspect the result before confirming:

```sh
python3 scripts/dicttech/ap-verify.py \
  --config ../configuration.private.json --device DEVICE_ID --host TARGET_IP \
  --output ../verification.private.json
python3 scripts/dicttech/ap-deploy.py confirm \
  --host TARGET_IP --receipt ../receipt.private.json
```

The guard survives loss of SSH/controller connectivity. Pending UCI changes,
baseline drift, another device, incomplete reload, unsafe permissions or altered
candidate files block staging/confirmation. Live SSH-port changes require offline
provisioning. The Nix wrapper separately supports its one guarded MAC reboot.

## Existing devices and image upgrades

Back up firmware and configuration first. Schema-v3 configuration is a breaking
source-contract change: retain neither an unconverted v2 marker nor v2 generated
archives when upgrading to this runtime. Generate an explicit v3 configuration
and export it with matching tools:

```sh
python3 scripts/dicttech/ap-deploy.py export \
  --config ../configuration.private.json --device DEVICE_ID \
  --preserve-host-identity --host CURRENT_AP_ADDRESS \
  --output ../configuration.private.tar.gz
```

The optional preservation flag verifies the selected device and boot, then
captures only that device's SSH/HTTPS identity and permanent keys not explicitly
replaced by the producer. Do not use it to clone another router's host identity.
The archive is private, contains rendered data, and marks initialization retired;
it is a normal `sysupgrade -f` input, not a firmware image or a rollback slot.

Validate image layout and archive with the ordinary upgrade test before flashing:

```sh
sysupgrade --no-provisioning -T -f /tmp/configuration.private.tar.gz /tmp/image.itb
```

Do not force a rejected compatibility check. Follow the [finished-image gate](dicttech-image-checks.md).
The XR1710G has one firmware slot: a configuration guard cannot repair a kernel
that never boots. Preserve a physical Recovery path.

Prepare runs at S04 and finalize at S13, after stock UCI normalization and before
management/network startup. Dynamic private ledgers replace fixed role and file
inventories. Accepted GUI password/channel edits survive normal reboot; explicit
redeployment detects drift. Missing or unsafe owned resources are quarantined
and fail closed. The generic sysupgrade hook backs up the producer's validated
resource list, including plugin binaries and explicit modes, without assuming
an AP topology or replacing the user's backup list.

For new/renamed devices or changed SSID identities, regenerate and deploy each
affected peer's final address/roaming list. Gateway reservations are a separate
NixOS deployment prerequisite; the router tool does not switch its gateway.

## Owner tools and status pages

The image includes Zsh, full Vim, tmux, less, HTTPS Git and rsync. Its offline
ys/OneDark configuration follows the reviewed dotfiles snapshot; it runs no
plugin installer, updater or network request at login. Root's default login
shell becomes Zsh after a successful executable check; system `/bin/sh` stays
BusyBox ash. Local overrides live in `.zshrc.local`, `.vimrc.local` and
`.tmux.conf.local`. Shell/editor transient state uses private RAM storage.

System / Device serial number and System / Software remain available. The
serial tool reads the split legacy `dsd` partition from offset zero and shows
its original serial/MAC fields separately from the active managed bridge MAC.
This split layout is read-only in both the page and RPC backend; factory data,
EEPROM and U-Boot environment are not rewritten. If original data is missing,
independent firmware/device information still remains visible. Existing vendor
and UBI layouts retain their supported behavior. Package exploration remains
possible, with matching release kmods required.

Status / WiFi client history is enabled on provisioned APs. It samples every
30 seconds, deduplicates shared MLO interfaces and retains at most 512 devices
for seven days in RAM. Reboot clears it. The page reports collector health;
an empty history before the first association is normal. Pure APs may lack
upstream DHCP hostname/IP hints. A client present entirely between samples
can be missed. This is a bounded diagnostic view, not an audit log.

## Tests and release handling

The public tests exercise the model, transactions and synthetic radio state.
CI also executes the bootstrap against the built ARM ash/UCI/slim-jq binaries.
The host's full jq does not have the same feature set: C7 intentionally omits
Oniguruma, so regular-expression filters must not enter target bootstrap code.
Run `test-ap-bootstrap-c7.py --c7-rootfs EXTRACTED_ROM --qemu qemu-aarch64`
as root for the private, inert ownership/runtime fixture before hardware trials.
See the component READMEs for commands and optional target-ash checks. The
read-only verifier distinguishes on-AP configuration evidence from real
client/Internet/PPE/isolation/roaming/cold-boot evidence. Missing evidence is
`NOT_TESTED`; `--require-production` refuses an incomplete production gate.

Keep personal policy changes as a coherent feature commit where practical,
with general driver fixes separate. Already published history need not be
rewritten to merge upstream: merge the chosen upstream revision, refresh the
locked feeds/source patches deliberately, rebuild, then repeat acceptance.
Do not automatically deploy an untested upstream merge to production.
