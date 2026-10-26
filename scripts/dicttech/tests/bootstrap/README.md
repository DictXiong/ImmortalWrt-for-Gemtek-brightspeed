Run from the source checkout:

```sh
python3 scripts/dicttech/test-ap-bootstrap.py --output bootstrap-tests.json
```

Linux, Python 3, jq and ordinary shell utilities are required. No root privileges,
router access, SSH authentication, browser, prepared feeds or built firmware are
needed. `--source-root` can select a separate build source checkout.

The fixtures read the actual bootstrap/services source, replace filesystem paths
with private temporary trees, and provide inert UCI, nft, ubus and init commands.
They never operate on host network interfaces or services. All credential-shaped
values are synthetic test strings confined to the temporary tree.

Coverage includes complete and incomplete preseed snapshots, canonical restore
after stock UCI normalization, preserving unrelated pending deltas, explicit empty
authorized_keys, steady-state GUI changes, new manifest acceptance, corrupt RAM
snapshots, quarantine and restore I/O failures, service enable/running snapshots,
exact rc-link restoration, ordered hardware one-shot replay, unknown service
rejection, malformed snapshot JSON and unavailable/malformed ubus replies.

The persistent-boot cases enable and dispatch the actual S04/S13 hook sources
through the checkout's `rc.common` in the private fixture. They verify ordering
after S03 transaction recovery, around S10's one-use defaults, and before CPU,
network and management services. Ordinary `start` actions in either package
installation order must leave all files unchanged before and after boot; only
the explicit `boot` action executes the two boot stages. Two consecutive boots use different synthetic
boot IDs and discard `/tmp`; after one-use defaults are gone, GUI Wi-Fi/password
edits still survive while missing critical configuration or a corrupt guest
guard is quarantined and isolated before network services.

These tests verify control flow and local file transformations. Actual netifd,
firewall, wireless, cold-start and offload behavior still require hardware
acceptance separately.
