# AP maintenance addresses

The management DHCP lease and the maintenance address are independent. The
DHCP address is used for normal management; the static maintenance address is
an additional address on the trusted native network and remains usable without
a DHCP server, provided that the bridge/interface has come up.

## Allocation

Use `192.168.250.0/24` for this installation. Keep each allocation in the local
device inventory, outside the firmware source repository:

| Addresses | Use |
| --- | --- |
| `192.168.250.1` | Existing primary AP |
| `192.168.250.2` | Existing secondary AP |
| `192.168.250.3`–`192.168.250.99` | Additional APs, allocated explicitly |
| `192.168.250.100`–`192.168.250.199` | Temporary maintenance computers |
| `192.168.250.200`–`192.168.250.253` | Reserved |
| `192.168.250.254` | Reserved for the existing pi1 maintenance alias |

The inventory is authoritative. Addresses must be unique even when two APs
are temporarily disconnected: a ping or ARP probe cannot prove that an
unresponsive address is unallocated. Retain an AP's allocation across firmware
upgrades, DHCP lease changes and reboots. Do not derive it from the current
DHCP lease or select a new random address at boot. Reject duplicate inventory
entries and a maintenance subnet overlapping another site network or a VPN.

A generic firmware must not activate the same maintenance address on every new
AP before it joins the home bridge. Register and provision each device before
joining that bridge, or perform first-time provisioning on an isolated cable.
The firmware/device-profile work must implement this bootstrap separation.

## Interface policy

`ap_maintenance` uses `proto static` on `br-lan.1`, with the registered IPv4
address and a `/24` netmask. It has no default gateway, DNS setting or DHCP
server. The normal `lan` interface retains its DHCP client on `br-lan.1`.

Keep the `/24` mask: changing to `/32` would require additional host routes and
would change the existing offline maintenance procedure. Do not add NAT or
routing between management and maintenance networks.

This is an alternate management address on VLAN 1, not a separate security
zone or a dedicated physical port. Trusted native traffic can reach it through
the four bridged Ethernet ports. Guest VLAN 192 remains subject to the guest
isolation and local-input policy; the additional address must not bypass that
policy.

## Offline use

Connect a computer to one of the bridged Ethernet ports. For example, configure
the computer as `192.168.250.100/24`, with no gateway or DNS, then open the AP's
registered maintenance address. SSH uses the AP's configured port, currently
12022. Do not assign the AP's own address to the computer.

This address needs a functioning network interface, but no uplink or DHCP
server. It is distinct from bootloader Recovery and does not provide Recovery
when the installed kernel cannot boot.

The current roaming helper intentionally binds peer HTTP requests to the real
DHCP management address. Offline management through `ap_maintenance` does not
mean that roaming metadata exchange runs without a valid management DHCP
lease. The helper also does not allocate or configure maintenance addresses.
