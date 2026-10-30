# DictTech AP configuration v2

The interchange format is UTF-8 JSON with `schema_version: 2` and
`profile: "dicttech-ap-v2"`. The local merge command and a future Nix+sops
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

Use the schema shipped with the matching deployment tools. An older AP can
retain an earlier v2 schema that rejects newly supported optional fields such
as `eht`; this does not prevent deployment by the updated host renderer. The
optional fields do not change the profile ID, schema version or defaults hash.

## Inputs

Keep the following real files outside the firmware Git checkout. A combined
file containing secrets and the secrets component must have mode `0600` or
stricter; the renderer refuses public permissions. Examples under
`examples/dicttech-ap/` contain no real credentials and the secret template is
intentionally invalid until its root hash is replaced.

`ap-common.json` describes only differences from firmware defaults:

```json
{
  "schema_version": 2,
  "profile": "dicttech-ap-v2",
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
underscores. They must not collide with any resolved SSID/profile ID in the
fleet; `main`, `guest` and `iot` are always reserved. A fleet contains 1–16 APs,
limiting each profile to at most 15 peers and
keeping synchronization work bounded for the intended home deployment.
Hostnames, maintenance addresses, DHCP DUIDs and emitted MAC
identities must be unique across the inventory. Explicit allocation is needed;
a failed ping is not evidence that an address is free.

`ap-secrets.json` contains per-ESS passwords and each device's root password
hash. Its shape is:

```text
secrets
  wifi
    main / guest / iot: required password strings
    <optional password_ref>: additional password strings
  devices
    <registered device ID>
      root_password_hash: supported SHA-512 or SHA-256 crypt hash
      authorized_keys: optional array of public-key lines
```

Wi-Fi passwords are 8–63 UTF-8 bytes. Control characters, duplicate JSON fields,
unknown configuration fields and unsupported versions are rejected. Secrets
must not be passed as shell arguments or printed by validation/plan commands.
Absent `authorized_keys` preserves current permanent keys; during first
initialization it leaves the extra bootstrap credential in place until confirm
retires it. Same-device exports remove that temporary identity. An explicit `[]`
requests an empty authorized-key file immediately, and a nonempty explicit list
replaces it exactly. The shipped initialization key cannot be listed as a
permanent credential. Device SSH host keys are not part of
this document or model rendering. Updating root's password hash must preserve
all other shadow rows.

Fresh images contain the owner's reviewed public-key set pinned in
`cli-profile`; there are no private keys. Preservation includes that default
set on a new AP. An explicit key list replaces it, and `[]` revokes it without
the next boot silently restoring it. Image rebuilds do not fetch keys during
login or automatically merge new keys into an already provisioned AP.

## Supported differences

`common` and optional per-device `overrides` accept this curated surface:

- `country`: `US` or `CN`; a CN profile must have no 6 GHz SSID.
- `ssh_port`, `timezone`, `zonename`, `ntp_servers`.
- `radios.radio0`: `channel` (1–11), `htmode` (`EHT20` or `EHT40`), `force_40`.
- `radios.radio1`: `channel` (36–64 in steps of four), `htmode` through `EHT160`.
- `radios.radio2`: a valid 6 GHz primary `channel`, `htmode` through `EHT320`.
- `ssids.main.name` and `ssids.main.six_ghz`; `ssids.guest.name`;
  `ssids.iot.name` and `ssids.iot.hidden`. Each SSID also owns its `encryption`:
  Main/Guest require `sae`; IoT accepts `sae`, `sae-mixed` or `psk2`.
- `extra_ssids`: an ordered array of zero or more ordinary custom SSIDs.
- `ssids.iot.eht` and each custom SSID's `eht`: boolean, default `true`.
  `false` disables only that BSS's EHT while keeping HE and the existing
  PMF/beacon protection. MLO requires EHT; the renderer rejects that conflict.
- `roaming.enabled`, `roaming.interval` (30–300), `roaming.ttl` (180–1800), with
  TTL at least three check intervals.

Resolution order is firmware defaults, common differences, then device
`overrides`. Objects merge recursively; arrays **replace the entire array**.
Thus a device override `"extra_ssids": []` removes all its custom SSIDs, while
an override containing several entries must repeat the full desired ordered
list. The three presets remain present; there is no special legacy/compatibility
role or mandatory fourth SSID. VLAN IDs, physical ports and raw UCI fields are
not a configuration escape hatch and still require a reviewed profile change.

### Presets and custom defaults

Main (`DictTech`) is 2.4+5 GHz MLO with optional 6 GHz, WPA3 SAE and required PMF.
Guest (`DictTech-Guest`) is 2.4+5 GHz MLO with the same security and VLAN 192.
IoT (`DictTech-IoT`) is hidden by default, 2.4 GHz single-link, WPA2/WPA3 mixed
with optional PMF. Guest clients may communicate. FT remains disabled.

Encryption belongs beside each SSID rather than in a separate `security`
table. PMF is derived (required for SAE, optional for mixed/WPA2), preventing
contradictory settings. Passwords remain in private `secrets.wifi`, referenced
by profile, so the public SSID configuration can stay secret-free. The default
NTP list starts with `time0.dxng.net`, `ntp.ibd.ink`, `ntp.dxng.cn` and retains
the existing public fallback servers; `ntp_servers` can replace the whole list.

A custom SSID needs only a stable ID and a name:

```json
{
  "common": {
    "extra_ssids": [
      {"id": "studio", "name": "Studio"}
    ]
  }
}
```

This is a `common` fragment, not the complete deployment document. Each custom
ID starts with a letter and has at most 32 letters, digits or underscores;
`main`, `guest` and `iot` are reserved. IDs identify profiles for MAC derivation
and roaming and should stay stable when a display name changes.

The public defaults file's top-level `extra_defaults` supplies omitted values:

| Field | Default and supported values |
| --- | --- |
| `bands` | `["2g", "5g"]`; any distinct subset of `2g`, `5g`, `6g` |
| `mlo` | Inferred: multiple bands enable MLO; one band selects single-link |
| `encryption` | `sae`; also `sae-mixed` or `psk2` for eligible single-link SSIDs |
| `network` | `lan`; `guest` selects the existing VLAN 192 network |
| `hidden` | `false` |
| `password_ref` | `main`; another key in private `secrets.wifi` may be selected |
| `roaming` | `true`, additionally gated by global `roaming.enabled` |

Explicit `mlo: true` requires multiple bands; `mlo: false` requires exactly one.
MLO and every 6 GHz SSID require `sae`/PMF-required; WPA2 or mixed encryption is
rejected for them. Other allowed encryption uses optional PMF. A custom SSID
with no password reference inherits the private Main password; adding a separate
password only requires adding that private secret and selecting its key.
`roaming: false` disables both that profile's synchronization worker and its
802.11k/v capabilities. All SSIDs on a physical radio share its channel/width.
Radio width support does not guarantee a client's actual negotiated width.

For example, a single-link WPA2 SSID can use:

```json
{"id": "printer", "name": "Printer", "bands": ["5g"],
 "encryption": "psk2", "hidden": true, "password_ref": "iot"}
```

There are at most 14 custom entries, at most 16 AP interfaces on each radio and
at most 16 MLDs. These are source/model capacity checks, **not evidence that all
maximum-size combinations have been tested on hardware**. The model checks the
actual bands: fourteen extra 5+6 GHz MLDs can fit, while fourteen extra 2.4+5 GHz
MLDs exceed the 2.4 GHz AP limit because IoT also uses that radio.

### Interface order and roaming identity

Main is always `ap-mld0`, Guest `ap-mld1`, and custom MLO entries receive
`ap-mld2`, `ap-mld3`, … in their list order. Single-link custom entries do not
consume an MLD number. They use stable short names derived from their IDs;
IoT uses `ap-iot`. Reordering custom MLOs changes their interface numbers but
not their derived MAC identities. Changing numbering recreates wireless
interfaces and can briefly disconnect clients, so test the migration first.

A shared SSID across APs must have the same profile ID, network, encryption,
PMF and password. Its bands may differ between wired-backhaul APs. Matching
profiles advertise only explicitly registered peers; a missing, renamed or
roaming-disabled profile is excluded. There is no automatic trust/discovery.

## Device MAC identity

`mac_base` is a stable nonzero unicast address. New devices derive a locally
administered wireless identities from it. The preset offset allocation is:

| Identity | Offset from base, local/unicast bits applied |
| --- | --- |
| WAN / CPU Ethernet | `0x01` / `0x04` |
| Radio 0/1/2 base | `0x10` / `0x20` / `0x30` |
| Main MLD, links | `0x50`, `0x50` / `0x52` / `0x53` |
| Guest MLD, links | `0x60`, `0x60` / `0x62` |
| IoT BSSID | `0x70` |

Custom identities are derived from the base, stable profile ID and band,
independently of list position; changing a custom band set does not change the
remaining bands' derived addresses. Main reserves all three BSSIDs even when
its 6 GHz link is disabled. Each custom ID also reserves three canonical
2g/5g/6g BSSID slots and an MLD address, even while absent or single-link.
Only its active bands' BSSIDs and, for MLO, its MLD are rendered into Wi-Fi.

The bridge and LAN ports use `mac_base`; default DUID is an Ethernet DUID-LL.
For existing devices, optional `identity` overrides preserve already deployed
addresses without changing factory data:

```text
identity
  bridge: MAC
  ethernet: {wan, lan2, lan3, lan4, cpu}
  radios: {radio0, radio1, radio2}
  mlds: {main, guest, <custom ID>: MAC}
  bssids: {main: [3 MACs], guest: [2], iot: [1], <custom ID>: band-map or [3 MACs]}
  duid: hexadecimal string
```

For custom pins, use a partial named band-map such as
`{"2g": "02:aa:bb:10:00:41", "5g": "02:aa:bb:10:00:42"}`; the omitted 6 GHz
slot is derived independently. A full three-element array in 2g/5g/6g order is
also accepted. Preset BSSID arrays keep their fixed lengths of 3/2/1; custom
arrays always have three slots, regardless of active `bands`.

Custom identity entries may remain in the inventory after their SSID is
removed from `extra_ssids`. Up to 64 custom identity slots may be reserved;
inactive slots create no SSID, roaming worker or hardware interface and do not
consume the active SSID/MLD limits. Reusing the same ID restores its original
identity. A migrated hotspot can therefore be deleted, changed to single-band
or extended to 6 GHz by editing only its ordinary SSID entry, without rewriting
its identity pins. Unknown bands, malformed IDs and invalid MACs still fail.

The override may be partial. Different BSSes must have distinct addresses. A
preset MLD may equal its own first-link BSSID; a custom MLD may reuse one of its
own reserved band addresses so band changes need not rewrite that pin. Sharing
with another ESS or MLD is rejected. A single device's bridge/port identities
can repeat, matching the current AP layout. All reserved addresses participate
in cross-device collision checking, including inactive slots. Register a new
base range before deployment; do not copy factory partitions, calibration or
another device's host private keys. These model checks do not replace actual
wireless/hardware acceptance.

The larger explicit `identity` objects for existing APs are migration pins,
not required boilerplate for every new AP. A newly registered device normally
needs only `hostname`, `maintenance_ipv4` and its assigned unique `mac_base`.
The firmware supplies the shared policy and the renderer derives the remaining
identities. Per-device root hashes and the three preset passwords remain
explicit private inputs because they cannot have public firmware defaults.
Custom SSIDs can reference those secrets or additional Wi-Fi password entries.

## Explicit v1 migration

Version 1's fixed `legacy` role is not accepted by the v2 model. Back up the
old combined JSON, then convert it to a different private output file:

```sh
python3 scripts/dicttech/ap-migrate-v1.py \
  --config ../ap-deployment-v1.private.json \
  --output ../ap-deployment-v2.private.json
```

The local-only tool preserves effective preset settings and credentials and
turns the old legacy ESS into an ordinary custom entry with ID `battalion`.
It preserves the effective name, including per-device override/shorthand
precedence, moves the private password reference and pins its existing MLD/BSS
MACs (including old derived defaults) using a 2g/5g band-map. A later 6 GHz link
gets a separately derived identity. It changes no input file or router and
validates/render-checks every device before writing mode `0600` output. The
migration API is `migrate(document) -> document`.

The resulting `battalion` item has no reserved meaning: remove it, rename it or
add other entries as desired. A newly configured v2 AP has no custom SSIDs
unless listed. Update any separately maintained common/devices/secrets
components from the converted result before the next merge.

V1 and v2 profile/default fingerprints are incompatible. Use a v2 image and an
archive exported by its matching v2 tool; carrying an unchanged v1 preseed or
whole old `/etc` into that image is not a supported upgrade. Validate on a test
AP with backups and rollback/recovery ready before changing production. See
[image upgrades](dicttech-ap.md#existing-devices-and-image-upgrades).

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
