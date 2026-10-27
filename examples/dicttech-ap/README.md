# Configuration shape examples

These files describe schema 2 / `dicttech-ap-v2`, not a deployable site configuration.
`common.json`, `devices.json` and `secrets.template.json` show the temporary
three-component inputs. `deployment.template.json` shows their combined
interchange document, which a future Nix+sops producer may generate directly.

`common.json` uses only the Main, Guest and IoT presets. `common.extras.json`
is an alternative common input showing an ordinary minimal custom SSID, a
single-link WPA2 SSID and a 5+6 GHz guest MLO. The extra list can be empty,
shorter or longer; its names and IDs have no compatibility/legacy semantics.
The minimal `{id, name}` entry inherits `extra_defaults`, including the Main
password reference. The examples with explicit references reuse the private
IoT/Guest secrets, so no additional example password is required.

Main and Guest always use `ap-mld0` and `ap-mld1`. Additional MLO entries are
numbered from 2 in list order; single-link entries do not take an MLD number.
A device's `overrides.extra_ssids` replaces the full list rather than appending
or merging entries. Use an empty override to omit all extras on that device.

Copy the relevant template outside this Git checkout, make secret-containing
files mode `0600`, then fill in the registered identity and all private values.
Every Wi-Fi password is intentionally empty, and the root hash is an invalid
placeholder, so an unchanged or partly filled template fails validation rather
than deploying a shared example password. Omit `identity` for newly assigned
MAC ranges; preserve explicit identity pins when migrating existing devices.

The v1 migration tool preserves the old fourth ESS as an ordinary custom
`battalion` entry and retains its password and MAC identities. That entry may
later be removed or replaced. A v2 image needs a matching v2 export; test the
transition before changing production rather than carrying a v1 marker forward.

Read [the format](../../docs/dicttech-config-format.md) and
[deployment procedure](../../docs/dicttech-ap.md). A runtime SOPS substitution
must write completed secret JSON outside the Nix store. No example requires
secret evaluation/build-time access or a decryption package on the AP.
