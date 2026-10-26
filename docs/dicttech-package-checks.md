# Verify the APKs published with an AP image

A correct flashed image and a usable APK release set are separate checks.
Cached APKs can have the same name/version and kernel release while carrying
another kernel ABI dependency or a signature made by a different build key.
Select the exact final image manifest and the trusted keys from that image's
assembled or, preferably, extracted rootfs:

```sh
python3 scripts/dicttech/check-ap-packages.py \
  --manifest /build/targets/airoha/an7581/current-image.manifest \
  --package-root /build/targets/airoha/an7581/packages \
  --package-root /build/packages/aarch64_cortex-a53 \
  --rootfs /private/extracted-image-rootfs \
  --apk /build/staging_dir/host/bin/apk \
  --output-dir /release/current/packages \
  --output /release/current/packages-check.json
```

`--package-root` is repeatable. Only exact `name-version.apk` filenames listed
in the supplied manifest are selected; old/unselected cached kmods are ignored.
Missing files or different bytes for the same selected filename are errors.
Multiple identical copies are accepted and recorded. Symlinked selected APKs
are refused.

Every selected APK is checked with the supplied host apk-tools 3 executable:

- `apk --no-network --no-cache --no-logfile --keys-dir IMAGE/etc/apk/keys verify`
  checks integrity and the signature against the image's actual public keys.
  No untrusted-signature override is used.
- `apk adbdump --format json` checks the actual package name, version and
  architecture. Every kmod must have exactly `kernel=VERSION`, including the
  ABI hash and package revision, matching the image manifest's kernel entry.
  Equality of `6.x.y` or ELF vermagic alone is insufficient.
- Manifest, selected APK bytes and public-key fingerprints must remain stable
  throughout validation.

The host tool may be a compatible executable wrapper; it must expose these
read-only apk interfaces and local paths. It never receives a private key.
There are no `adbsign`, install, index-update or network operations in this gate.
For apk-tools 3, `verify` walks package data blocks and file hashes as well as
metadata, so a metadata-only truncated package must fail verification.

The optional output directory must not exist, and its parent must exist. Only
a completely verified manifest set is copied there; copy errors remove the new
partial directory. Publish that collection after the gate succeeds, rather
than globbing all APKs from the build cache. The JSON receipt records the
manifest hash, full kernel ABI dependency, public-key fingerprints and selected
APK hashes. Reports use a `.json` filename and contain no private signing data.
This script does not rotate keys, re-sign packages or repair a failed cache.

Run synthetic local fixtures without network access or real signing keys:

```sh
python3 scripts/dicttech/tests/test_ap_packages.py
```
