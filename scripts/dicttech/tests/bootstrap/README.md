Run from the source checkout:

```sh
python3 scripts/dicttech/test-ap-bootstrap.py --output bootstrap-tests.json
```

Linux, Python 3, jq, OpenSSL and ordinary shell utilities are required. No root privileges,
router access, SSH authentication, browser, prepared feeds or built firmware are
needed. `--source-root` can select a separate build source checkout.

The fixtures read the actual bootstrap/services source, replace filesystem paths
with private temporary trees, and provide inert UCI, nft, ubus and init commands.
They never operate on host network interfaces or services. All credential-shaped
values are synthetic test strings confined to the temporary tree.

Coverage includes complete and incomplete preseed snapshots, canonical restore
after stock UCI normalization, preserving unrelated pending deltas, explicit empty
authorized_keys, steady-state GUI changes, new manifest acceptance, corrupt RAM
snapshots, quarantine and restore I/O failures, service enable/running snapshots,
exact rc-link restoration, ordered hardware one-shot replay, unknown service
rejection, malformed snapshot JSON and unavailable/malformed ubus replies.

Fresh, unprovisioned firmware keeps LAN4 as a separate offline entry at
`192.168.250.1/24`. WAN/LAN2/LAN3 form the VLAN-aware `br-setup` DHCP uplink,
using a once-generated, private persistent locally administered MAC and hostname.
Both entries accept root/password SSH on port 12022 until initialization is
confirmed, alongside the unchanged owner public-key set. The offline LAN4 entry
also retains HTTP. No Wi-Fi, DHCP/RA server, NAT or upstream
LuCI listener starts. An invalid managed marker remains quarantined in the
LAN4-only profile and cannot use first-device initialization.

Both SSH instances use `DirectInterface`, which the actual Dropbear init converts
to device-bound `-l lan4` / `-l br-setup.1` sockets. Merely binding the maintenance
IP would let Linux weak-host routing reach the maintenance listener through another
interface; merely using `Interface` can also emit a wildcard listener while DHCP
is pending. The inert fixture executes the checkout's actual init function to
check both device bindings and that a pending DHCP interface creates no listener.

Initialization-authentication fixtures verify the public password against its
SHA-512 crypt value, first-boot installation, preservation of explicit empty
authorized keys, existing custom passwords and unrelated shadow rows. They cover
native and Nix pending journals, one planned reboot, expired/closed/wrong-boot
leases, legacy-key filtering, retirement and symlink refusal. Configured,
retired or quarantined devices never regain the default password; confirmation
activates the final root hash, or locks the default if none is valid. The helper
and lifecycle directory retain their legacy `bootstrap-key` names solely for
upgrade compatibility.

`bootstrap.sh check-setup` is a read-only proof shared by the transaction helper.
It compares all setup templates and persisted identity/state, checks disabled
Wi-Fi and maintenance/upstream runtime state, and rejects identity or template
tampering. A DHCP lease is optional for LAN4 initialization: exact DHCP pending
state, both setup device MACs and absence of unexpected static addresses must
still validate. Its `dhcp_ready` field distinguishes an available DHCP entry
from offline-only access. Random identities are unique and survive reboots;
entropy failure never falls back to a fixed uplink MAC. Optional CLI activation
failure is diagnosed without disabling wired recovery.

The fixtures cover those dual-entry/pending-DHCP decisions, each template and
identity/runtime tamper, and restoring canonical setup after stock first-boot
normalization. Transaction enrollment, pinned boot approval and exact rollback
are also exercised with the real bootstrap proof by:

```sh
python3 scripts/dicttech/tests/ap-deploy/test_transaction.py
```

The persistent-boot cases enable and dispatch the actual S04/S13 hook sources
through the checkout's `rc.common` in the private fixture. They verify ordering
after S03 transaction recovery, around S10's one-use defaults, and before CPU,
network and management services. Ordinary `start` actions in either package
installation order must leave all files unchanged before and after boot; only
the explicit `boot` action executes the two boot stages. Two consecutive boots use different synthetic
boot IDs and discard `/tmp`; after one-use defaults are gone, GUI Wi-Fi/password
edits still survive while missing critical configuration or a corrupt guest
guard is quarantined and isolated before network services.

These tests verify control flow and local file transformations. Actual netifd,
firewall, wireless, cold-start and offload behavior still require hardware
acceptance separately.

After extracting a C7 image, also run its actual ARM BusyBox ash, UCI, jq and
rc.common/procd enable paths through qemu-user:

```sh
python3 scripts/dicttech/test-ap-bootstrap-c7.py \
  --c7-rootfs /absolute/path/to/extracted-rom \
  --output c7-bootstrap-tests.json
```

This second fixture needs root for target ownership checks and `qemu-aarch64`.
It copies the ROM into a private temporary tree, overlays the selected source
bootstrap helpers, and redirects filesystem paths and command execution there.
Actual init scripts are restricted to enable/disable/links; ubus and IP state are
inert responses, with network services and nft blocked. It verifies fresh DHCP,
canonical S13 repair after real UCI commits, offline pending-DHCP enrollment,
transaction identity proof, template/MAC tamper rejection, and optional CLI
activation, initialization password retention across a planned reboot, expired
lease refusal, confirmation and legacy-key retirement. C7 intentionally ships jq without Oniguruma, so this fixture first
confirms that regex calls fail; bootstrap generation and validation must use
literal split/join and ASCII checks instead of jq test/gsub. This catches target
feature differences that the host jq fixtures cannot expose.
