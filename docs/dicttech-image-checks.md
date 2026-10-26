# XR1710G image and layout gate

Check the finished image, including its actual embedded DTB, before publishing
or flashing it. The source DTS alone is insufficient: an old DTB can remain in
an incremental build. This checker uses Python's standard library and does not
connect to a router or modify an image.

```sh
python3 scripts/dicttech/check-ap-image.py \
  --layout legacy --image /build/xr1710g-sysupgrade.itb \
  --rootfs /build/root-airoha --kernel-release 6.18.55 \
  --output /build/image-check.json
```

`--profile` is an alias for `--layout`. Use `ubi` only for the distinct OpenWrt
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
- Optional assembled-rootfs module architecture and ELF vermagic consistency.
  This is **not** a kernel ABI hash check or proof that the supplied directory
  was extracted from this exact FIT rootfs. Keep the build manifest, kernel
  configuration, feed pins and matching kmod packages as release artifacts.

The historical C7 legacy DTB carried `gemtek,xr1710g-ubi` as an extra compatible.
For auditing that old image only, add `--allow-legacy-ubi-alias`. New legacy
releases should retain only their proper legacy device identity.

Optional independent tools may be supplied by explicit path:

```sh
python3 scripts/dicttech/check-ap-image.py --layout legacy --image image.itb \
  --fwtool /tools/fwtool --fit-check-sign /tools/fit_check_sign
```

The tool cross-checks metadata extracted with `fwtool -q -i -` and invokes
`fit_check_sign -f`. A wrapper executable can provide a cross-architecture tool.
The built-in CRC/payload hashes establish integrity, not publisher identity;
cryptographic release/signature trust remains a separate release policy.

Run the public, synthetic regression fixtures:

```sh
python3 scripts/dicttech/tests/test_ap_image.py
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
python3 scripts/dicttech/check-ap-rootfs.py \
  --rootfs /build/root-airoha --source-root /reviewed/firmware-repository \
  --manifest /build/xr1710g.manifest --output /build/rootfs-check.json
```

The checker compares every `ap-profile/files/` file at its installed path. It
parses the `xr-roaming` Makefile install mapping, including its disabled default
UCI configuration. This is a **virgin image** check; a running AP legitimately
has its own device configuration and credentials. It also verifies installed
wireless/default-password generators against source, checks the AP conditional
that generates disabled Wi-Fi interfaces, and confirms upstream credential and
wireless defaults are suppressed. It does not execute a hardware-dependent UCI
wireless generator on the build host.

Device deployment state, preset usable root passwords, nonempty authorized
keys and private SSH/HTTPS host identities are rejected in the shared image.
The manifest policy reuses `REQUIRED` and `FORBIDDEN` from `check-build.py`, so
package selection and artifact checks share one policy. Manifest checking is
optional; supply the manifest produced alongside the exact finished image.

To check the rootfs from the **final ITB**, rather than a staging directory:

```sh
python3 scripts/dicttech/check-ap-image.py \
  --layout legacy --image /build/xr1710g-sysupgrade.itb \
  --rootfs-image-output /private/ap-root.squashfs --output /build/image-check.json
unsquashfs -d /private/ap-root /private/ap-root.squashfs
python3 scripts/dicttech/check-ap-rootfs.py \
  --rootfs /private/ap-root --source-root /reviewed/firmware-repository \
  --manifest /build/xr1710g.manifest --output /build/rootfs-check.json
```

The image checker writes only the unique rootfs selected by all FIT boot
configurations, after metadata, payload hashes and layout validation succeed.
It refuses an existing payload destination and creates the extracted file with
mode 0600. Use a new extraction directory and the reviewed repository as the
comparison source; an unverified build mirror is not an independent reference.
Reports must be written outside the rootfs being verified.

```sh
python3 scripts/dicttech/tests/test_ap_rootfs.py
python3 scripts/dicttech/tests/test_ap_image.py
```
