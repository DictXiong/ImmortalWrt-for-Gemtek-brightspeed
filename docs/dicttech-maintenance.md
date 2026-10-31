# Configured and factory maintenance access

A configured maintenance address is producer-owned policy. The backend accepts
explicit device/access metadata and the corresponding network UCI; it does not
choose a subnet, address, prefix length, interface, VLAN or gateway.

The current Nix AP profile uses DHCP for online management and a persistent
maintenance address derived from each device's `subId`. Uniqueness is required
within the same LAN, not globally. This additional native-network address needs
no upstream DHCP server, but still requires a functioning configured interface.
The Nix configuration reference is authoritative for its allocation rule and
for the guest/local-input policy. Do not infer free addresses from failed ping.

For offline use, connect to a bridged trusted port and configure the computer
with a different address in the declared maintenance subnet and the correct
mask. No default gateway or DNS is necessary for direct management. SSH uses
the declared port. This is ordinary Linux management, not bootloader Recovery;
it cannot recover an unbootable installed kernel.

The **factory enrollment contract** is separate. A freshly flashed XR1710G uses
isolated LAN4 `192.168.250.1/24` and a random-identity DHCP discovery bridge on
the other ports. Only this unconfigured/recovery state has that fixed address.
Factory-only ARP settings prevent weak-host replies exposing the common LAN4
address on the discovery bridge during batch enrollment. Configured settings
come from Nix and are not imposed by the firmware's backend.

The XR factory implementation lives in
`package/dicttech/ap-profile/files/usr/lib/dicttech-ap/platforms/xr1710g/`.
The image package selects this adapter and its templates for installation at
`/usr/lib/dicttech-ap/factory/`. Before writing or proving factory setup, the
adapter requires `gemtek,xr1710g` or `gemtek,xr1710g-ubi`, matching the
legacy and UBI XR device trees. Its port layout, discovery
bridge, setup hostname, factory service policy and template proof are platform
facts. The ten factory assets retain the existing XR enrollment behavior,
including DHCP discovery, separate LAN4 maintenance and root/password on SSH
port 12022.

The shared bootstrap retains resource verification, credential lifecycle,
snapshots, rollback and configured-boot acceptance. A valid complete producer
configuration does not load or require the XR factory adapter. A future hardware
port supplies another factory adapter/assets and selects suitable package
dependencies and target restrictions; it does not replace the shared transaction
or acceptance engine. The current image still targets Airoha/XR1710G. This
separation does not claim that another router has been ported or tested.

Roaming peer addresses are also explicit producer input. An offline maintenance
entry does not automatically make peer discovery or Internet access available;
those depend on the generated network and roaming configuration.
