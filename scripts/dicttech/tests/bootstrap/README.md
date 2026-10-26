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

These tests verify control flow and local file transformations. Actual netifd,
firewall, wireless, cold-start and offload behavior still require hardware
acceptance separately.
