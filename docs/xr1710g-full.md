# XR1710G full edition

The `full` edition combines hardware/driver fixes, LuCI improvements, commonly
useful command-line tools, `wrtix-agent` and `wrtmesh`. Its `gateway` and `ap`
build roles use the same source, drivers, packages and wrtix version; only the
initial network configuration differs. No owner public keys, shared password,
custom maintenance subnet, personal AP policy or MAC allocator is included. The Full-only `xr1710g-cli` package provides
an offline ys zsh prompt, One Dark Vim theme and tmux defaults. Interactive root
terminals enter zsh; the account shell remains `/bin/ash`, so remote commands,
failsafe and package removal retain the normal shell. These defaults work before
wrtix enrollment and carry no authentication material.

Both roles include `umdns` for mDNS discovery, with logical-network IPv4 source
selection, and `arp-scan` with its MAC vendor database for on-demand LAN
diagnostics. `arp-scan` does not run a background service. These packages are
required by both build-configuration and installed-package manifest checks.

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
Wireless interfaces are disabled initially. wrtmesh ships no predefined peers
or ESS profiles. Choose the actual regulatory country and Wi-Fi security
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
WRTMESH_SOURCE=/absolute/path/to/clean/wrtmesh \
  bash scripts/dicttech/build-full.sh --role gateway --layout legacy --jobs 4
```

Select `--role ap` for the bridged DHCP-client default. Role and partition layout
are independent choices. Use `ubi` only for hardware already using that
bootloader/partition layout.
The command leaves the partition layout and bootloader in place. The build pins
all feeds and rejects a root `files/` overlay. Commit the source before building
a release. Use `--allow-dirty` for explicitly uncommitted local trials.
`--prepare-only` prepares feed/config inputs without compiling the image.

`configs/wrtix-source.json` and `configs/wrtmesh-source.json` record each standalone feed’s Git commit and archive hash.
GitHub Actions fetches `DictXiong/wrtix` and `DictXiong/wrtmesh` by default. The `wrtix_repository`
workflow input takes precedence over the `WRTIX_REPOSITORY` repository variable;
either override still has to supply the pinned commit and matching archive.
The build verifies and materializes both checkouts and includes both source
archives in the release bundle. The wrtmesh APK manages `/etc/config/wrtmesh` and
`/etc/config/wrtmesh_peers`, preserved during firmware sysupgrade. Package
upgrades migrate the historical roaming configuration and peer overrides.
Migration preserves mode-0600 source backups at
`/etc/wrtmesh/migration/roaming.uci` and `/etc/wrtmesh/migration/peers.uci`.
The init service is `/etc/init.d/wrtmesh` and runtime tools are under
`/usr/lib/wrtmesh`. Configuration producers must use these names and the new
UCI package names when preparing a subsequent deployment.
The package also owns the historical `/xr-roaming/` metadata and
`/cgi-bin/xr-mesh` aliases for communication with older peers.
The Mesh peer dialog selects actual local SSIDs. The package prepares local
controllers and learns BSSIDs from fresh peer snapshots after validating the
LAN IP/MAC binding. “Roaming enabled” means current candidate reports were
applied to hostapd; it is derived from worker evidence. Internal controller
names are not user input and do not need to match between nodes.
The APK also installs `view/status/include/65_wrtmesh.js`, which LuCI loads on
Overview to show Mesh topology and remote wireless clients. For builds that
reuse a LuCI feed previously modified by the old Mesh patch, start from the
clean pinned LuCI checkout so the standard Wi-Fi Overview module is restored.

The finished FIT, extracted rootfs, package signatures and exact kmod set are
checked before publishing. Private keys, personal authentication material and
preset deployment state are prohibited. See [image checks](dicttech-image-checks.md),
[package checks](dicttech-package-checks.md), and the [paired-role release workflow](xr1710g-releases.md).
Fixes builds are retired; historical release assets and tags remain available.

A firmware upgrade and a wrtix configuration transaction are different operations.
Complete pending transactions before upgrading. Include a compatible agent in a
new image when preserving managed configuration. Configuration rollback cannot
restore firmware or arbitrary package installations.
