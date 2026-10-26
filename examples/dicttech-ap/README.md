# Configuration shape examples

These files describe the public format, not a deployable site configuration.
`common.json`, `devices.json` and `secrets.template.json` show the temporary
three-component inputs. `deployment.template.json` shows their combined
interchange document, which a future Nix+sops producer may generate directly.

Copy the relevant template outside this Git checkout, make secret-containing
files mode `0600`, then fill in the registered identity and all private values.
Every Wi-Fi password is intentionally empty, and the root hash is an invalid
placeholder, so an unchanged or partly filled template fails validation rather
than deploying a shared example password. Omit `identity` for newly assigned
MAC ranges; preserve explicit identity pins when migrating existing devices.

Read [the format](../../docs/dicttech-config-format.md) and
[deployment procedure](../../docs/dicttech-ap.md). A runtime SOPS substitution
must write completed secret JSON outside the Nix store. No example requires
secret evaluation/build-time access or a decryption package on the AP.
