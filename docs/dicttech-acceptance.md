# Acceptance boundaries

Use the pinned wrtix checkout's `nix flake check` for generic modules, ownership,
compiler/controller, receipt and target-shell regressions. Its separate official
OpenWrt VM harness validates package lifecycle, enrollment, interrupted
controllers, endpoint handoff, reboot, authentication rollback and receipt
recovery. Public tests use synthetic configurations and contain no private AP
inventory or credentials.

wrtix 0.2.0 has passed that suite on official OpenWrt 25.12.0 x86/64,
including actual DHCP endpoint changes, receipt recovery, planned reboots,
failed-check and timeout rollback, and package removal/reinstallation. Initial
installation and minimal enrollment preserved existing authentication and network
configuration; repeated deployment was a noop. This validates the generic
management package, not XR1710G 3.0.0 hardware behavior.

The 3.0.0 full candidate has separately passed physical-device checks for fresh
LAN DHCP, LAN-only management, WAN DHCP/NAT and Internet access, optional wrtix
enrollment, its planned reboot, configuration verification and repeat-deployment
noop. Wireless checks cover WPA2/WPA3 association, local dual-stack TCP, 6GHz SLO,
5+6GHz MLO, LuCI rendering, and guest VLAN isolation with source/VLAN injection
controls while matching PPE bindings were active.

The strict wireless matrix is not an all-pass result. Controlled roaming restored
association and traffic but exceeded the two-second interruption criterion;
version-anchored older-firmware records already show interruptions around 2.2
seconds. The latest run also recorded IPv6 gaps around 3.2 seconds on two
profiles when returning to the test target; equivalence of those gaps to older
firmware has not been established. The original immediate supplicant rejection
did not recur in an independent retry with a matching target BSS cache.
Intermittent USB-client IoT SAE HTTPS timeouts were also reproduced on 2.2.0,
including successful wired controls before and after the failed wireless request.
The older records establish that interruption and intermittent HTTPS symptoms
predate 3.0.0; they do not establish the cause of each new failure, identical
severity, or equal failure rates across versions. Earlier failed attempts remain
part of the evidence. These results do not certify natural RSSI roaming, FT/BTM,
3.0.0 cold boot, long-term stability, simultaneous MLO-band aggregation, or every
packet's hardware-offload path.

The consumer AP extension owns allocation/security/capacity checks and the live
runtime checker. Required checks must fail deployment or confirmation when they
fail or cannot execute. A current runtime check is not proof of client traffic,
radio compatibility, roaming or hardware acceleration.

Firmware checks remain local to this repository:

```sh
sudo bash scripts/dicttech/check-regressions.sh host
sudo bash scripts/dicttech/check-regressions.sh rootfs /build/root-airoha /private/reports
```

The second command uses built ARM tools for history and serial-tool fixtures.
It leaves the input rootfs unchanged and uses inert network/service commands. See the [image gate](dicttech-image-checks.md)
for actual FIT, rootfs, source-pin and package checks.

Before production image acceptance, separately verify cold/warm boot, wired
management, LAN/WAN defaults, authentication, optional enrollment and user-defined
maintenance interfaces, client authentication, IPv4/IPv6 Internet access, VLAN isolation, hardware-bound
forwarding and wired-backhaul roaming as applicable. Bind evidence to the exact
image, configuration and device boot. Missing or skipped tests remain unverified;
configuration-state checks do not substitute for traffic measurements. Configuration
rollback does not provide firmware rollback on a single-slot router.

These instructions define acceptance scope, not a claim that each new candidate
has passed it. Keep detailed deployment data and raw evidence out of public
release notes; report tested features and outstanding limitations only.
