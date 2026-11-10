# XR1710G image and layout gate

Check the finished image, including its actual embedded DTB, before publishing
or flashing it. The source DTS alone is insufficient: an old DTB can remain in
an incremental build. This checker uses Python's standard library and does not
connect to a router or modify an image.

```sh
python3 scripts/dicttech/check-image.py \
  --layout legacy --image /build/xr1710g-sysupgrade.itb \
  --rootfs /build/root-airoha --kernel-release 6.18.55 \
  --output /build/image-check.json
```

Use `--layout ubi` only for the distinct OpenWrt
U-Boot layout. A passing UBI image check does not convert a legacy device's boot
chain or factory data. The deployer must separately match the running device's
U-Boot, raw NAND layout and UBI volumes to the selected image, then run the
ordinary `sysupgrade -T` check without forcing compatibility.

| Profile | Raw NAND partitions |
| --- | --- |
| legacy | bootloader 0–2 MiB (RO); uenv 2–4 MiB; dsd 4–6 MiB; chainloader 6–7 MiB (RO); ubi 7–446 MiB; reserved_bmt 446–512 MiB (RO) |
| ubi | bl2 0–128 KiB (RO); ubi from 128 KiB to the physical NAND end |

The gate verifies:

- OpenWrt fwtool metadata CRC, compatibility version, board and supported-device
  list. Legacy is `gemtek,xr1710g`/1.0; UBI is `gemtek,xr1710g-ubi`/2.0.
- Every FIT image payload hash, including external payload positions, and the
  default kernel, rootfs and DTB references.
- The actual DTB's primary compatible, exact partition offsets/sizes/read-only
  flags and `chosen/rootdisk` reference to the selected UBI's `fit` volume.
- The factory EEPROM/MAC offsets: legacy uses its repacked calibration volume;
  the OpenWrt U-Boot layout uses the complete DSD format. They are not
  interchangeable.
- The compiled I2C0 node uses the supported `mediatek,mt7621-i2c` binding and
  enables the NCT7802 fan/temperature monitor at address `0x2e`. The legacy
  board must inherit this binding from the SoC DTS; the unsupported vendor
  override leaves the bus unbound. This image check establishes the device-tree
  contract; runtime fan feedback and driver probing still require a hardware
  check.
- Optional assembled-rootfs module architecture and ELF vermagic consistency.
  This is **not** a kernel ABI hash check or proof that the supplied directory
  was extracted from this exact FIT rootfs. Keep the build manifest, kernel
  configuration, feed pins and matching kmod packages as release artifacts.

Legacy and UBI board aliases must never be mixed in one device tree.

Optional independent tools may be supplied by explicit path:

```sh
python3 scripts/dicttech/check-image.py --layout legacy --image image.itb \
  --fwtool /tools/fwtool --fit-check-sign /tools/fit_check_sign
```

The tool cross-checks metadata extracted with `fwtool -q -i -` and invokes
`fit_check_sign -f`. A wrapper executable can provide a cross-architecture tool.
The built-in CRC/payload hashes establish integrity, not publisher identity;
cryptographic release/signature trust remains a separate release policy.

Run the public, synthetic regression fixtures:

```sh
python3 scripts/dicttech/tests/test_image.py
```

For a layout change, amend the source DTS and rebuild the image. Copying an old
DTB or upgrade script to make a hash match bypasses source review and can discard
unrelated upstream fixes. On this legacy board, splitting the former writable
0–6 MiB `vendor` entry back into bootloader/uenv/dsd preserves both the physical
contract and the bootloader's read-only protection. FIT upgrades normally target
`ubi/fit`, so merely merging these DT partitions does not itself move NAND data;
it still changes MTD names, numbers and write boundaries.

# Validate bytes in the finished rootfs

A successful source build does not prove a copied build tree contained every
reviewed runtime file. For example, an rsync exclusion named `files` can omit
package `files/` directories while leaving a superficially usable build tree.
Compare the actual rootfs bytes against the authoritative reviewed repository:

```sh
python3 scripts/dicttech/check-rootfs.py \
  --rootfs /build/root-airoha --source-root /reviewed/firmware-repository \
  --manifest /build/xr1710g.manifest --output /build/rootfs-check.json
```

The source tree must have completed `build-full.sh --prepare-only` with its pinned
`WRTIX_SOURCE` and `WRTMESH_SOURCE`. This materializes the two verified feeds used by the checker.

The checker compares the public `xr1710g-full` marker and pinned `wrtix-agent`
runtime, contract, lifecycle hooks and runtime hash inventory at installed paths.
It rejects retired runtime files and deployment state. It compares the pinned
`wrtmesh` install mapping with the image, including its disabled defaults,
standalone LuCI Overview include, stylesheet and compiled translation. It also
checks the standard Wi-Fi Overview module for the legacy Mesh integration and
verifies history/factory UI postimages and RPC permissions. The audit covers a
**virgin image**. Running devices legitimately carry user configuration and
credentials.

The full-edition wireless guards suppress upstream fixed-country/password presets
and disable newly generated interfaces without rewriting preserved configuration.
The checker verifies their installed bytes and guard markers; actual hardware
wireless generation is a separate target check.

Personal CLI/setup assets, nonempty authorized-key sets, preset root password
hashes, private SSH/HTTPS device keys, custom root shell and personal network
presets are rejected. The image does not prescribe an AP maintenance subnet or a
netifd-only resolver symlink. Routing packages are required alongside management
and wireless tools. User SSH keys, password, topology and shell preferences belong
in configuration applied after installation.
The manifest policy reuses `REQUIRED` and `FORBIDDEN` from `check-build.py`, so
package selection and artifact checks share one policy. Manifest checking is
optional; supply the manifest produced alongside the exact finished image.

To check the rootfs from the **final ITB**, rather than a staging directory:

```sh
python3 scripts/dicttech/check-image.py \
  --layout legacy --image /build/xr1710g-sysupgrade.itb \
  --rootfs-image-output /private/image-root.squashfs --output /build/image-check.json
unsquashfs -d /private/image-root /private/image-root.squashfs
python3 scripts/dicttech/check-rootfs.py \
  --rootfs /private/image-root --source-root /reviewed/firmware-repository \
  --manifest /build/xr1710g.manifest --output /build/rootfs-check.json
```

The image checker writes only the unique rootfs selected by all FIT boot
configurations, after metadata, payload hashes and layout validation succeed.
It refuses an existing payload destination and creates the extracted file with
mode 0600. Use a new extraction directory and the reviewed repository as the
comparison source; an unverified build mirror is not an independent reference.
Reports must be written outside the rootfs being verified.

```sh
python3 scripts/dicttech/tests/test_rootfs.py
python3 scripts/dicttech/tests/test_image.py
```
