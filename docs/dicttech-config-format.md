# DictTech AP configuration v1

The interchange format is UTF-8 JSON with `schema_version: 1` and
`profile: "dicttech-ap-v1"`. The local merge command and a future Nix+sops
producer must emit the same document. No Nix, Python, SOPS or decryption runtime
is required on the AP: resolution and secret handling run on the deployment
computer; the AP receives validated rendered configuration.

`package/dicttech/ap-profile/files/usr/share/dicttech-ap/defaults.json` is the
firmware's reviewed default profile. Its installed path is
`/usr/share/dicttech-ap/defaults.json`. A deployment records its exact SHA-256,
and a target with different defaults must be rejected rather than silently
applying a different policy. The versioned JSON Schema is adjacent to that file
as `deployment.schema.json`; the Python validator adds fleet consistency and
UTF-8 byte-length checks that JSON Schema cannot fully express.

## Inputs

Keep the following real files outside the firmware Git checkout. A combined
file containing secrets and the secrets component must have mode `0600` or
stricter; the renderer refuses public permissions. Examples under
`examples/dicttech-ap/` contain no real credentials and the secret template is
intentionally invalid until its root hash is replaced.

`ap-common.json` describes only differences from firmware defaults:

```json
{
  "schema_version": 1,
  "profile": "dicttech-ap-v1",
  "common": {}
}
```

`ap-devices.json` registers stable device identities:

```json
{
  "devices": {
    "ap_example": {
      "hostname": "ap-example",
      "maintenance_ipv4": "192.168.250.3",
      "mac_base": "02:aa:bb:10:00:00"
    }
  }
}
```

Device IDs start with a letter and contain at most 48 letters, digits or
underscores; `main`, `battalion`, `guest` and `iot` are reserved section names.
A v1 fleet contains 1–16 APs, limiting each profile to at most 15 peers and
keeping synchronization work bounded for the intended home deployment.
Hostnames, maintenance addresses, DHCP DUIDs and emitted MAC
identities must be unique across the inventory. Explicit allocation is needed;
a failed ping is not evidence that an address is free.

`ap-secrets.json` contains per-ESS passwords and each device's root password
hash. Its shape is:

```text
secrets
  wifi
    main / legacy / guest / iot: password strings
  devices
    <registered device ID>
      root_password_hash: supported SHA-512 or SHA-256 crypt hash
      authorized_keys: optional array of public-key lines
```

Wi-Fi passwords are 8–63 UTF-8 bytes. Control characters, duplicate JSON fields,
unknown configuration fields and unsupported versions are rejected. Secrets
must not be passed as shell arguments or printed by validation/plan commands.
Absent `authorized_keys` preserves the device's current keys; an explicit `[]`
requests an empty authorized-key file. Device SSH host keys are never copied or
rendered. Updating root's password hash must preserve all other shadow rows.

## Supported differences

`common` and optional per-device `overrides` accept this small curated surface:

- `country`: `US` or `CN`; a CN profile requires `ssids.main.six_ghz: false`.
- `ssh_port`, `timezone`, `zonename`, `ntp_servers`.
- `radios.radio0`: `channel` (1–11), `htmode` (`EHT20` or `EHT40`), `force_40`.
- `radios.radio1`: `channel` (36–64 in steps of four), `htmode` through `EHT160`.
- `radios.radio2`: a valid 6 GHz primary `channel`, `htmode` through `EHT320`.
- `ssids.main.name` and `ssids.main.six_ghz`; `ssids.legacy.name`;
  `ssids.guest.name`; `ssids.iot.name` and `ssids.iot.hidden`.
- `roaming.enabled`, `roaming.interval` (30–300), `roaming.ttl` (180–1800), with
  TTL at least three check intervals.

A device's `legacy_ssid` is a shorthand override for its legacy ESS name.
Resolution order is firmware defaults, common differences, device overrides,
then that shorthand. The four SSIDs remain enabled in v1 to retain the tested
C7 MLD interface numbering. Disabling an arbitrary MLD or changing encryption,
VLAN IDs, interface names, physical ports or raw UCI fields is outside this
profile. Such changes need a reviewed profile/version change, not an unchecked
configuration escape hatch. Radio width support does not guarantee that a
particular client or regulatory state will use that width; actual operation
belongs in the live acceptance report.

Main is WPA3/PMF-required with 2.4+5 GHz and optional 6 GHz. Legacy and Guest are
2.4+5 GHz WPA3/PMF-required MLO. IoT is a hidden-by-default 2.4 GHz single-link
WPA2/WPA3 mixed BSS with optional PMF. All have k/v enabled and FT disabled.
Guest clients may communicate; guest traffic stays on VLAN 192. Same-name ESSes
must use the same role, security and password. Different channels are valid for
wired-backhaul peers; different legacy names are excluded from each other's
candidate lists.

## Device MAC identity

`mac_base` is a stable nonzero unicast address. New devices derive a locally
administered wireless range from it. The offset allocation is part of v1:

| Identity | Offset from base, local/unicast bits applied |
| --- | --- |
| WAN / CPU Ethernet | `0x01` / `0x04` |
| Radio 0/1/2 base | `0x10` / `0x20` / `0x30` |
| Legacy MLD, links | `0x40`, `0x41` / `0x42` |
| Main MLD, links | `0x50`, `0x50` / `0x52` / `0x53` |
| Guest MLD, links | `0x60`, `0x60` / `0x62` |
| IoT BSSID | `0x70` |

The bridge and LAN ports use `mac_base`; default DUID is an Ethernet DUID-LL.
For existing devices, optional `identity` overrides preserve already deployed
addresses without changing factory data:

```text
identity
  bridge: MAC
  ethernet: {wan, lan2, lan3, lan4, cpu}
  radios: {radio0, radio1, radio2}
  mlds: {main, legacy, guest}
  bssids: {main: [3 MACs], legacy: [2], guest: [2], iot: [1]}
  duid: hexadecimal string
```

The override may be partial. Different BSSes must have distinct addresses; an
MLD may equal its own first-link BSSID. A single device's bridge/port identities
can repeat, matching the current AP layout. Any address shared across devices
is rejected. Register a new base range before deployment; do not copy factory
partitions, calibration or another device's host private keys.

The larger explicit `identity` objects for existing APs are migration pins,
not required boilerplate for every new AP. A newly registered device normally
needs only `hostname`, `maintenance_ipv4` and its assigned unique `mac_base`.
The firmware supplies the shared policy and the renderer derives the remaining
identity range. Per-device root hashes and the four ESS passwords remain
explicit private inputs because they cannot have public firmware defaults.

## Local commands and Nix migration

From the firmware checkout:

```sh
python3 scripts/dicttech/ap_config.py merge \
  --common ../ap-common.json --devices ../ap-devices.json \
  --secrets ../ap-secrets.json --output ../ap-deployment.json

python3 scripts/dicttech/ap_config.py validate --config ../ap-deployment.json

python3 scripts/dicttech/ap_config.py render --config ../ap-deployment.json \
  --device ap_example --output ../ap-example-rendered
```

Render is local only. Its output directory must be new/empty, is mode `0700`,
and all desired files are mode `0600`; it contains Wi-Fi secrets and must never
be committed or uploaded as a public build artifact. Success output names the
managed files without printing their contents. The deployment tool consumes the
same Python API: `load_document(path)`, `resolve(document, device_id,
defaults_path=None)`, and `render(resolved, defaults_path=None)` returning
relative paths mapped to bytes.

A Nix+sops implementation can directly generate the combined document with
these five top-level fields: `schema_version`, `profile`, `common`, `devices`,
`secrets`. It does not need to invoke the temporary three-file merge step, and
it should write the result into a private runtime directory with mode `0600`.
The deployment tool and on-device transaction remain unchanged. Treat generated
backups, rendered wireless configuration and password hashes as private too.

The Nix store is not a secret store. Keep decrypted Wi-Fi passwords, root
password hashes, combined JSON and rendered archives out of Nix evaluation
outputs and build derivations: reading decrypted values into `writeText`,
`toFile` or another store output would expose them in store paths and build
artifacts. Use activation/runtime SOPS substitution instead: a public template
may contain placeholders, and substitution writes the completed JSON only to
the protected runtime file. The Nix-generated file must have the same shape and
types as the temporary merged file; the deployment tool still performs all
validation. `examples/dicttech-ap/deployment.template.json` shows this complete
shape without embedding any usable credential.

The model renderer does not copy SSH host keys. The deployment tool's explicit
same-device identity-preservation export is separate: it may privately carry
that device's existing host keys into its own upgrade archive after identity
checks. Never reuse that archive for another AP.

The renderer creates `etc/dicttech-ap/device.json` with nonsecret resolved
identity/policy, defaults SHA and a nonsecret configuration fingerprint. It
omits credentials and secret-derived hashes. The private root hash lives at
`etc/dicttech-ap/root-password-hash`; the target transaction synchronizes only
root's shadow hash. The renderer never generates host keys or whole shadow files.

`etc/dicttech-ap/managed-files.json` is a separate **private** first-preseed
completeness manifest. It contains hashes of all rendered files except itself,
including secret-bearing files, so it must remain mode `0600` and must not be
published. Initial bootstrap verifies this manifest before accepting a preseed.
After acceptance, the boot policy verifies critical structural invariants;
ordinary GUI channel/password edits are reported as deployment drift rather
than being rejected solely because their old hashes changed. The nonsecret
`config_fingerprint` alone is never sufficient to decide deployment is a no-op.

The fleet inventory also generates `config peer` sections and per-profile
`list peers` references with `peer_mode 'fleet'` for the roaming package. Adding a node requires
redeploying the changed peer configuration to existing nodes; unknown APs are
not discovered or trusted automatically. A one-AP fleet is valid and publishes
its own metadata with an empty peer list.
