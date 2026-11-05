# XR1710G full edition

The `full` edition combines hardware/driver fixes, LuCI improvements, commonly
useful command-line tools, `wrtix-agent` and `xr-roaming`. Its `gateway` and `ap`
build roles use the same source, drivers, packages and wrtix version; only the
initial network configuration differs. No owner public keys, shared password,
custom maintenance subnet, personal AP policy or MAC allocator is included. The Full-only `xr1710g-cli` package provides
an offline ys zsh prompt, One Dark Vim theme and tmux defaults. Interactive root
terminals enter zsh; the account shell remains `/bin/ash`, so remote commands,
failsafe and package removal retain the normal shell. These defaults work before
wrtix enrollment and carry no authentication material.

CLI assets live in `/usr/share/xr1710g-cli`, with the login hook at
`/etc/profile.d/80-xr1710g-cli.sh`. Create the adjacent
`80-xr1710g-cli.hush` file to disable automatic zsh entry. Optional user overrides
are `/root/.zshrc.local`, `/root/.vimrc.local` and `/root/.tmux.conf.local`.
History and editor caches use a private RAM directory rather than flash.

Choose the default network role when building or downloading:

| Role | Initial network and management |
| --- | --- |
| `gateway` | WAN DHCP client and NAT; LAN at `192.168.1.1` with DHCP service; administration is blocked on WAN. |
| `ap` | All four ports (`wan`, `lan2`, `lan3`, `lan4`) form one LAN bridge. The AP obtains DHCP/DHCPv6 addresses from its upstream and does not serve DHCP or IPv6 RA. Find its address in the upstream lease table. |

AP mode has no preset maintenance IPv4 address or local DHCP server. An offline
direct connection therefore needs a separately prepared management path. The
physical WAN port is a LAN bridge member in AP mode, including for management.
Use an unconfigured image on a trusted network: the initial root password is
empty and SSH uses LAN port 22 in both roles. Set authentication before exposing
management elsewhere. Role defaults apply only to a fresh/reset configuration;
upgrading while retaining configuration does not convert its network role.
Wireless interfaces
are disabled initially; `xr-roaming` has no predefined profiles and starts no
worker until configured. Choose the actual regulatory country and Wi-Fi security
before enabling radios. The full-edition marker suppresses upstream preset
wireless credentials and country overrides; it does not rewrite retained settings.

LuCI/UCI provides normal router configuration without installing Nix or using
wrtix. Optional declarative management uses the separately pinned wrtix tools. User profiles
supply topology, SSIDs, VLANs, addresses, secrets and roaming policy. The agent
itself has no such defaults. See [configuration ownership](dicttech-config-format.md),
[access and maintenance](dicttech-maintenance.md), and [acceptance](dicttech-acceptance.md).

## Building

```sh
WRTIX_SOURCE=/absolute/path/to/clean/wrtix \
  bash scripts/dicttech/build-full.sh --role gateway --layout legacy --jobs 4
```

Select `--role ap` for the bridged DHCP-client default. Role and partition layout
are independent choices. Use `ubi` only for hardware already using that
bootloader/partition layout.
The command does not convert partitions or flash bootloaders. Feeds and wrtix
are pinned, and the build rejects a root `files/` overlay. Source must be
committed; `--allow-dirty` is reserved for explicitly uncommitted local trials.
`--prepare-only` prepares feed/config inputs without compiling the image.

`configs/wrtix-source.json` records the exact wrtix commit and Git archive hash.
GitHub Actions fetches `DictXiong/wrtix` by default. The `wrtix_repository`
workflow input takes precedence over the `WRTIX_REPOSITORY` repository variable;
either override still has to supply the pinned commit and matching archive.
The build requires `WRTIX_SOURCE` to match, materializes a verified snapshot,
and includes its source archive in the release bundle. Runtime protocol versions
are independent of the firmware's version. A matching SDK/firmware build supplies
its architecture-independent agent package and target dependencies.

The finished FIT, extracted rootfs, package signatures and exact kmod set are
checked before publishing. Private keys, personal authentication material and
preset deployment state are prohibited. See [image checks](dicttech-image-checks.md),
[package checks](dicttech-package-checks.md), and the [paired-role release workflow](xr1710g-releases.md).
Fixes builds are retired; historical release assets and tags remain available.

A firmware upgrade and a wrtix configuration transaction are different operations.
Complete pending transactions before upgrading. Include a compatible agent in a
new image when preserving managed configuration. Configuration rollback cannot
restore firmware or arbitrary package installations.
