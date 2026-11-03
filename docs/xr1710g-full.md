# XR1710G full edition

XR1710G 3.0.0 introduces the `full` edition. It combines hardware/driver fixes, LuCI improvements, commonly
useful command-line tools, `wrtix-agent` and `xr-roaming`. It is a general router
image. No owner public keys, shared password, custom maintenance subnet, AP
policy or MAC allocator is included. The Full-only `xr1710g-cli` package provides
an offline ys zsh prompt, One Dark Vim theme and tmux defaults. Interactive root
terminals enter zsh; the account shell remains `/bin/ash`, so remote commands,
failsafe and package removal retain the normal shell. These defaults work before
wrtix enrollment and carry no authentication material.

CLI assets live in `/usr/share/xr1710g-cli`, with the login hook at
`/etc/profile.d/80-xr1710g-cli.sh`. Create the adjacent
`80-xr1710g-cli.hush` file to disable automatic zsh entry. Optional user overrides
are `/root/.zshrc.local`, `/root/.vimrc.local` and `/root/.tmux.conf.local`.
History and editor caches use a private RAM directory rather than flash.

Initial network setup follows OpenWrt's board configuration: LAN management at
`192.168.1.1` with DHCP service, WAN DHCP/NAT, and no WAN administration exposure.
The initial root password is empty and SSH uses LAN port 22. Set authentication
from the trusted LAN before exposing management elsewhere. Wireless interfaces
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
  bash scripts/dicttech/build-full.sh --layout legacy --jobs 4
```

Use `ubi` only for hardware already using that bootloader/partition layout.
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
[package checks](dicttech-package-checks.md), and the [paired release workflow](xr1710g-releases.md).

A firmware upgrade and a wrtix configuration transaction are different operations.
Complete pending transactions before upgrading. Include a compatible agent in a
new image when preserving managed configuration. Configuration rollback cannot
restore firmware or arbitrary package installations.
