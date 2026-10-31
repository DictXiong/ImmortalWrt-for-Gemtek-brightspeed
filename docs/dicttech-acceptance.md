# Resource verification and acceptance limits

`ap-verify.py` reads the same complete private v3 configuration as `ap-deploy.py`.
It never applies resources, writes neighbors, starts services, reloads Wi-Fi or
reboots a router. It has no fixed SSID, network, VLAN, guard-template or channel
policy: expectations come from the producer's UCI and public metadata.

```sh
python3 scripts/dicttech/ap-verify.py \
  --config ../configuration.private.json --device DEVICE_ID \
  --host TARGET_IP --port SSH_PORT --output ../verification.private.json
```

Use the same optional `--control-socket` and `--jump-host` transport settings as
the deployer. Reports and bindings use mode `0600`. Output excludes passwords,
root hashes, complete UCI, raw daemon argv and private resource digests.
Hostapd responses are filtered to a safe-field allowlist on the target.

## Observed runtime checks

- Backend schema/profile/contract hash, registered device, observed MAC and
  allowed board; contract and boot stability across sequential collection.
- Desired resource bytes, root ownership and each explicitly declared mode;
  configured root password and explicit resolver target.
- Declared UCI interfaces and their expected protocol/device/address behavior.
- Declared service enable/running policy, including oneshots without a PID,
  and persistent rollback/prepare/finalize boot hooks.
- Exactly the declared radio/BSS inventory, active link BSSIDs/frequencies,
  WPA/PMF/MLO/hidden/EHT/k-v configuration and actual channel/width. A supplied
  2.4 GHz 20/40 coexistence policy may legitimately run at 20 MHz.
- Fresh explicitly selected roaming peers. Disabled BSS roaming has no worker;
  a single-device inventory does not require a fictitious peer.

These checks do not prove forwarding, VLAN isolation or hardware acceleration.
Matching rendered firewall/guard files proves desired bytes were installed;
actual packet behavior needs independent client evidence. Empty wireless and
service inventories are valid and do not acquire invented AP expectations.
C7 `ssid2` quoted strings are decoded literally or from strict hexadecimal;
optional hostapd printf encoding is refused rather than guessed.

## Independent client and hardware evidence

An otherwise clean runtime-only report has overall `NOT_TESTED`, while its
observed checks are `PASS`. `PASS` requires applicable independent tests:
management reachability, dual-stack networking, cold boot/link recovery and
performance; wireless inventories additionally need client authentication and
actual roaming where enabled. A declared guest-VLAN platform also requires
client mutual communication, internal isolation, PPE-bound isolation/VLAN
retention, and controlled source/VLAN-injection tests. Enabled 6 GHz requires an
actual compatible client. These gates do not run merely by reading AP status.

Use `--require-production` to reject untested acceptance gates. A normal zero
exit status means no observed check failed; it is not full production approval.
The verifier consumes external evidence but does not rerun it or attest that
reported tests were executed.

`--binding-output PATH` writes a private boot/device/full-resource binding. An
external runner copies these fields into its report and adds a `tests` map:

```json
{"tests":{"wifi_client_auth":{"status":"PASS"},"ipv4_ipv6_internet":{"status":"PASS"}}}
```

This fragment is not a complete report. Preserve all binding fields, record only
tests actually executed, keep underlying local evidence, and pass the complete
private report via `--external-report PATH`. A different boot, device or desired
configuration fails binding. Missing outcomes remain `NOT_TESTED`; old evidence
is never loaded automatically.

Offline fixtures run with `python3 scripts/dicttech/test-ap-verify.py`. They test
the reader's logic, not a wireless client, RF compatibility or hardware datapath.
