# WiFi station history regression tests

Run the exact postimage from `120-dicttech-bounded-mld-history.patch` with an
installed ucode interpreter, including its `fs` module:

```sh
python3 scripts/dicttech/tests/wifihistory/test_history.py --ucode ucode
```

`--ucode` (or `DICTTECH_TEST_UCODE`) accepts a command with arguments, so the
matching firmware interpreter may also run through `qemu-aarch64 -L ROOTFS
ROOTFS/usr/bin/ucode -L ROOTFS/usr/lib/ucode`. The test changes only filesystem
paths, the clock, and the ubus transport; the collector and RPC methods execute
in the real interpreter, with real file permissions, atomic renames, and flock.
It fails if ucode is unavailable instead of silently skipping coverage.

The synthetic `fixtures/c7-mld.json` uses the pinned C7 netifd and hostapd API
shapes: one shared `ap-mld0` appears on multiple radios; `get_clients` is an
aggregate MAC-keyed object with link addresses, and signed signal values may be
encoded as u32. The fixture contains documentation addresses, no household
credentials or captured client identities. Tests cover MLD deduplication,
normalized MAC/link hints, iwinfo fallback, absence versus backend failures,
seven-day/512-station bounds, field allowlisting, permissions, concurrent clear,
and health RPC states. They do not claim to verify a physical association.

## Live image acceptance

After deploying the image, root should verify the enabled/running `wifihistory`
service and these read-only calls:

```sh
ubus call luci.wifihistory getStatus
ubus call luci.wifihistory getHistory
```

With radios deliberately disabled at bootstrap, a stopped/no-sample state is
normal. With radios enabled, an empty station history is normal until a client
has associated for at least one 30-second sample. Join a real test client on a
shared MLD, wait for one sample, and confirm one MAC row, SSID and connected
state. Leave, wait for another sample, and confirm the row is retained as
disconnected. A failed/partial sampler must preserve existing rows rather than
inventing disconnects. A pure AP may lack DHCP hostname/IP hints, particularly
on guest VLANs; those empty columns do not indicate failed station collection.

History and sampler status live only in `/var/lib/wifihistory` (`/var` is RAM on
OpenWrt), clear on reboot, and are bounded to 512 MACs and seven days. A station
which connects and disconnects entirely between two samples may not be recorded.
MAC randomization may make one physical device appear as several history rows.
