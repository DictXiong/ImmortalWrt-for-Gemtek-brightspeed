# AP acceptance

`ap-verify.py` is the read-only baseline acceptance entry. It uses the same
private structured configuration and defaults as `ap-deploy.py`, and the same
SSH transport options. It never applies configuration, invokes neighbor writes,
starts services, reloads Wi-Fi or reboots a router.

```sh
python3 scripts/dicttech/ap-verify.py \
  --config ../ap-deployment.json --device ap_example \
  --host 192.168.250.3 --port 12022 \
  --output ../ap-example-verification.json
```

Optional `--control-socket` and `--jump-host` have the same meaning as in the
deployment tool. The target identity is checked against the selected device;
a reused SSH master does not by itself prove that the intended AP was reached.
Reports are written privately (`0600`). Success output contains fixed check
results, not root hashes, Wi-Fi keys, daemon argv or complete UCI configuration.
Generated hostapd configuration is filtered on the AP to an explicit safe-field
allowlist before transport.

The baseline checks cover:

- Desired configuration file hashes and private modes, defaults/profile, device
  identity, root-password synchronization and resolver policy.
- The boot ID and full private target contract are reread after collection;
  observed changes fail acceptance instead of combining different boot/config
  snapshots. Their private digests are not printed in the report.
- Management DHCP, maintenance `/24`, IPv6 management and a guest bridge with no
  AP-local IP addresses.
- All four physical ports: only VLAN 1 untagged/PVID and VLAN 192 tagged; bridge
  self-membership remains tagged for the `.1` and `.192` subinterfaces.
- Four live SSIDs and expected per-link BSSIDs/frequencies; generated hostapd
  key management, PMF and k/v flags. The main links' actual width is checked;
  2.4 GHz 20/40 coexistence may legitimately report 20 MHz.
- Fresh roaming snapshots and every configured peer. A single-AP inventory has
  an empty expected peer list and does not require a fictitious remote peer.
- Guest bridge/inet local guards, the EAPOL exception and pinned bridge sysctls.
  A matching drop expression in an unused or unrelated chain does not count:
  the rule must belong to the expected family/table's actual input hook.
- Required daemons and disabled unwanted services. Firewall is a oneshot
  service, so its live fw4 table is checked instead of requiring a daemon PID.

A changed radio frequency/width is reported as a failed policy check, not as a
proven firmware bug. DFS, regulatory behavior or unfinished startup can require
investigation or a later read-only rerun.

## External client and hardware gates

A running AP and matching configuration do not prove actual client behavior.
The following tests are explicitly `NOT_TESTED` unless external evidence is
provided:

- Four-SSID client authentication, including forced WPA2 IoT compatibility.
- IPv4/IPv6 Internet access and DNS/HTTPS from a real wireless client.
- Guest-client mutual communication and internal-network isolation.
- Isolation and VID 192 retention after the specific test flow is PPE-bound.
- Source-address spoofing and VLAN-tag injection under the controlled test plan.
- Actual cross-AP roaming, cold boot/link recovery, CPU and throughput.
- A real 6 GHz client when the main ESS's 6 GHz link is enabled.

`PASS` is reserved for all checks, including required external gates. A clean
configuration-only run reports overall `NOT_TESTED` with its individual local
checks marked `PASS`. Exit status is zero when no observed check failed; add
`--require-production` to require every gate to be `PASS` and return nonzero on
untested behavior. This prevents a configuration-only report from being used
as an unrestricted production-acceptance certificate.

The repository currently provides the AP-side reader and evidence aggregator,
not a universal wireless-client or PPE traffic-test executor. Those external
tests must actually run using the available test client/hardware and preserve
their results. A zero exit status from the baseline command means that no
observed baseline check failed; it does not mean all production gates ran.

## Binding external evidence

Use `--binding-output ../ap-example-binding.json` during verification to write
a private binding containing schema/profile, device, observed boot ID and the
hash of the full desired file map. The binding includes secret-derived hashes
and must not be published. An external test runner can copy those fields into
its private report and add:

```json
{
  "tests": {
    "wifi_client_auth": {"status": "PASS", "evidence": "private-run-001/auth"},
    "ipv4_ipv6_internet": {"status": "PASS", "evidence": "private-run-001/dualstack"}
  }
}
```

This fragment is not a complete valid report: retain all binding fields and
supply outcomes for the tests actually executed. `evidence` is an opaque local
reference, not a password or raw packet payload. Feed the complete `0600`
report to `--external-report PATH`. Missing tests remain `NOT_TESTED`; an old
boot, another device or different desired configuration fails binding. The
verifier consumes reported evidence but does not independently rerun those
client tests, so preserve their underlying captures/results for review.
The binding and a nonempty evidence reference prevent accidental reuse of a
different boot/configuration report; they are not attestation of the report's
truth. Filling a report with `PASS` without executing the named tests is not
acceptance. No report is loaded automatically from a previous run.

External IDs are `wifi_client_auth`, `ipv4_ipv6_internet`,
`guest_client_mutual`, `guest_internal_isolation`,
`ppe_bound_isolation_and_vlan`, `spoof_and_vlan_injection`,
`actual_client_roaming`, `cold_boot_and_link_recovery`, `cpu_and_throughput`,
and, where enabled, `six_ghz_client_auth`.

Local unit fixtures for the verifier are run with:

```sh
python3 scripts/dicttech/test-ap-verify.py
```
