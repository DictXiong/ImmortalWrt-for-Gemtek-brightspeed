# Complete resource configuration example

`configuration.json` is a complete synthetic v3 producer document. It intentionally
uses arbitrary SSID/interface names, a non-default network/VLAN description and
an explicit service/file resource to demonstrate that the backend has no presets.
Its public fixture passwords and root-hash-shaped value are not production
credentials. Keep real complete documents private (mode0600).

Production Nix configuration should generate the resource document; users do not
need to maintain this lower-level structure by hand. See the
[resource format](../../docs/dicttech-config-format.md) and
[deployment guide](../../docs/dicttech-ap.md). There is no split/common/defaults
merge input or legacy migration interface.
