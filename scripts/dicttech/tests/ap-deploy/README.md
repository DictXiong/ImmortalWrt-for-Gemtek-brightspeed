# Configuration transaction checks

Run from the firmware repository:

```sh
python3 scripts/dicttech/tests/ap-deploy/test_transaction.py
```

The test needs Python 3, jq, the ordinary POSIX/coreutils commands, and root
inside the disposable development environment. It does not connect to an AP.
Every configuration path, init script, service state, clock and target command is
redirected into a private temporary directory. Synthetic addresses and hashes
are fixtures. No real Wi-Fi credentials or device inventory belong here.

An optional command prefix exercises the shell code with a target BusyBox ash:

```sh
DICTTECH_TEST_ASH='qemu-aarch64 -L /path/to/rootfs /path/to/rootfs/bin/busybox ash' \
  python3 scripts/dicttech/tests/ap-deploy/test_transaction.py
```

This tests target shell semantics with inert native command fixtures. It does
not prove real procd, netifd, hostapd, NPU, service, or radio behavior.

Coverage includes full apply/explicit confirmation; autonomous expiry; early
boot rollback without starting network services before ubus; bounded deferred
service restoration; pending UCI and check/stage/apply drift; malformed and
unexpected tar members; file hashes, private modes, tokens and device identity;
root-only shadow replacement preserving other users and SSH host keys; DNS
resolver normalization with exact original regular-file/symlink restoration;
unapplied trial cancellation preserving concurrent edits; and stopping a hung
owned reloader before rollback can complete.

Fresh initialization retains root/password through apply and installs the
configured root hash only at confirmation. The tests verify this with omitted
and explicitly empty authorized keys, refuse confirmation when retirement fails
or leaves the default hash, and restore shadow plus lifecycle state on rollback.

# Operator interface

`ap-deploy.py` consumes the canonical structured JSON emitted by `ap_config.py`.
The document and any exported archive contain secrets and must remain private.
An existing authenticated SSH master can be used with `--control-socket`; the
controller does not put passwords in SSH arguments or initialize SSH trust.

```sh
python3 scripts/dicttech/ap-deploy.py plan \
  --config /private/ap.json --device ap_node --host ap.example.invalid

python3 scripts/dicttech/ap-deploy.py apply \
  --config /private/ap.json --device ap_node --host ap.example.invalid \
  --control-socket /private/ap-master --receipt /private/ap-trial.json

python3 scripts/dicttech/ap-deploy.py status --host ap.example.invalid
python3 scripts/dicttech/ap-deploy.py confirm --receipt /private/ap-trial.json
python3 scripts/dicttech/ap-deploy.py rollback --receipt /private/ap-trial.json
```

The default lease is 600 seconds. `apply` does not confirm automatically. Check
actual management, forwarding, wireless and isolation behavior before explicit
confirmation. A trial survives the SSH session; expiry restores the original
managed configuration, shadow, resolver and service enable/running state. An
unexpected reboot restores configuration/enable links before normal network
startup and defers running-state reconciliation until ubus is ready. Failed
reconciliation is reported as `rollback_incomplete`.

A staged trial that never applied is cancelled without restoring old files over
external edits. The controller compares all desired files, and the authenticated
check baseline is verified again at stage and apply. Confirmation checks all
rendered files, permissions, actual identity and the resolver. Existing device
IDs and bridge MACs must match. A quarantined or new isolated device needs
`--initialize --expect-boot-id` after checking its physical identity; this cannot
bypass a different registered device ID. Version 1 rejects live SSH port changes.

For a new image, export a private deterministic gzip configuration archive:

```sh
python3 scripts/dicttech/ap-deploy.py export \
  --config /private/ap.json --device ap_node --output /private/ap-config.tar.gz
```

Exporting does not flash firmware or arm a firmware rollback. The archive
contains configuration and identity data only, with no `uci-defaults`
provisioning shim. The firmware owns persistent S04 prepare and S13 finalize
services, so a preseed is finalized at the same boot stage as every later boot.
Fresh-device export excludes SSH host private keys. Firmware flashing,
image compatibility and recovery remain the separate firmware deployment flow.

For an existing-device upgrade, preserve that same device's SSH and HTTPS
identity explicitly; `sysupgrade -f` otherwise restores only the supplied archive:

```sh
python3 scripts/dicttech/ap-deploy.py export \
  --config /private/ap.json --device ap_node --output /private/ap-upgrade.tar.gz \
  --preserve-host-identity --host ap.example.invalid --control-socket /private/ap-master
```

This mode probes board, boot ID, actual bridge MAC and any registered device ID
before a second read-only capture. It accepts only the fixed regular-file
Dropbear/HTTPS identity allowlist; explicitly supplied `authorized_keys`, even
an empty file, takes precedence. The archive contains private host keys and
remains local with mode 0600. Never use this mode to clone identities onto a
different new device. Run its inert checks with:

```sh
python3 scripts/dicttech/tests/ap-deploy/test_export_identity.py
```
