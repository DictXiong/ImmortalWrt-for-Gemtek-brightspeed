# Declarative configuration resource format v3

The backend accepts complete UTF-8 JSON documents with `schema_version: 3` and
`profile: "openwrt-config-v3"`. Nix owns defaults, address allocation, AP profile
composition and peer selection. The backend validates and renders the supplied
resources; it does not infer missing topology, SSID roles, passwords, services,
MAC offsets or operational defaults.

The checked-in schema is
`package/dicttech/ap-profile/files/usr/share/dicttech-ap/deployment.schema.json`.
Its adjacent `contract.json` identifies the supported resource protocol and
capabilities. The exact contract SHA-256 is pinned during deployment. It is not
a configuration/defaults asset. Previous v1/v2 inputs, split merge inputs,
`--defaults`, migrations and partial-identity allocation are not supported.
Generate a new v3 document and use a matching firmware runtime and host tool.

## Complete producer input

A document contains only `schema_version`, `profile` and `devices`. Each stable
device ID maps to a complete record:

| Field | Meaning |
| --- | --- |
| `hostname`, `management_ipv4`, `maintenance_ipv4`, `ssh_port` | Explicit device and access metadata |
| `identity` | Complete bridge, Ethernet, radio-base, MLD, three-band reserved BSSID and DUID tables |
| `platform` | Producer-supplied board names, optional capacity and MAC-prefix constraints; AP descriptions may also include ports, bridge, radio path and VLANs |
| `common` | Public diagnostic metadata supplied by the producer; no defaults or SSID credentials |
| `ssids` | Ordered complete BSS metadata; arbitrary stable keys are accepted, including no SSIDs |
| `peers` | Explicit per-SSID peer identities and active BSSIDs; no peer allocation or discovery policy |
| `services` | Explicit named enable/running/oneshot/reload policy |
| `uci` | Structured complete UCI packages and sections |
| `files` | Producer-owned additional files, separate from UCI and lifecycle resources |
| `resolver` | `null` preserves the existing resolver; `{ "target": "/absolute/path" }` explicitly manages its symlink |
| `credentials` | Root password hash and optional complete public-key list |

All fields above are present even when their value is an empty object/array or
`resolver` is null. The [complete synthetic example](../examples/dicttech-ap/configuration.json)
is a protocol fixture, not a site configuration or production credential set.

An SSID record explicitly names its key, UCI section, interface, visible name,
bands, MLO flag, network, encryption, PMF, EHT, hidden and roaming flags, radios,
link IDs and active BSSIDs. An active MLO also has `mld_mac`. There are no special
`main`, `guest` or `iot` keys in the backend. Reserving their MAC slots or
choosing default policy belongs to Nix. Single-band SSIDs do not require an
active MLD interface; an identity table may still reserve their MLD MAC.

MLO requires multiple bands and EHT; MLO and 6 GHz require a compatible WPA3 or OWE
policy (including supported enterprise modes) with required PMF. The
current Nix AP profile selects SAE for those combinations. These are validation constraints. A role name never determines which
security options or bands are available. Supplied UCI and metadata must agree;
metadata cannot claim one security/VLAN/BSSID policy while rendering another.
Hardware capacity and address collisions are validated against the supplied
platform and identity. A radio `macaddr_base` is an allocation parameter, not an
extra active interface or a guaranteed PHY address.

## UCI, files and services

`uci` is a map of package names to named section records. Each section has
`type`, `options` and optional serialization syntax fields `anonymous` and
`order`. Options are scalar strings/integers/booleans or ordered scalar lists.
The renderer orders sections by `(order, name)` and quotes values; it never
executes supplied strings. Empty UCI is valid.

`files` maps safe relative paths to UTF-8 content, or to
`{ "content_base64": "…", "mode": "755" }`. Supported explicit modes are
`600`, `644`, `700` and `755`; text shorthand uses mode `600`. Binary plugin
assets and executable managed helpers are supported. UCI paths are reserved
for structured UCI; account databases, host private keys, deployment machinery
and lifecycle metadata cannot be overwritten through this map. Managed init
scripts/helpers use the `openwrt-managed-` / `openwrt-managed/` namespaces.

Each `services` entry explicitly declares `enable`, `running` and reload
triggers. `running = null` does not require a persistent process. `oneshot`
identifies services without a daemon PID; reload actions are limited to
`reload`/`restart`. Service names are data, never shell commands. There is no
firmware list of required or forbidden configured services.

Credentials are local private input. Wireless passwords occur in the complete
UCI supplied by the producer. `authorized_keys` omitted preserves permanent
authorization; an explicit list, including `[]`, replaces it. The revoked old
initialization key is never promoted into permanent exported authorization.
No private SSH host key is part of a device-independent configuration.

## Rendering and lifecycle

From this firmware checkout, with a private complete document:

```sh
chmod 600 ../configuration.private.json
python3 scripts/dicttech/ap_config.py validate --config ../configuration.private.json
python3 scripts/dicttech/ap_config.py render \
  --config ../configuration.private.json --device DEVICE_ID --output ../rendered-private
```

The Python API is `resolve(document, device_id)` followed by `render(state)`.
It returns an exact relative-path-to-bytes map. Validation errors identify the
resource/field without exposing credential values. No decryption, downloads,
remote calls or arbitrary host-file reads occur in the backend.

Besides explicit UCI/files, the renderer emits:

- `etc/dicttech-ap/device.json`: public diagnostic metadata and configuration
  fingerprint; credentials, complete UCI and file contents are excluded.
- `etc/dicttech-ap/root-password-hash`, and optionally authorization: private
  credential material applied through the transaction lifecycle.
- `etc/dicttech-ap/managed-files.json`: private resource paths, SHA-256 and modes.
  This excludes its own digest and must not be published.

The first configured boot verifies the complete producer ledger before
acceptance. Boot snapshots survive upstream UCI normalization without accepting
partial resources. Subsequent accepted GUI edits remain possible; a later
explicit deployment reports drift. Missing files, unsafe permissions or invalid
metadata fail closed to factory recovery, with the original files quarantined.
A fallback setup cannot reuse an old acceptance stamp to accept rewritten files.

The sysupgrade hook adds validated producer-owned paths to OpenWrt's existing
backup list. It preserves legitimate configuration drift, binary contents and
explicit modes, rejects missing/unsafe/symlinked paths, and does not overwrite
`/etc/sysupgrade.conf` or assume any particular plugin, VLAN or filename.
Firmware/bootstrap code comes from the new image; arbitrary old executable
runtime trees are not restored.
