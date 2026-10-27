# Factory information compatibility checks

These tests use synthetic data only. Never place raw factory reads, serials,
credentials, live MAC addresses or EEPROM contents in this directory.

```sh
python3 scripts/dicttech/tests/factory/test_factory.py --rootfs /path/to/extracted-c7-rootfs
PLAYWRIGHT_MODULE=/path/to/playwright-core BROWSER_EXECUTABLE=/path/to/chromium \
  node scripts/dicttech/tests/factory/frontend.cjs
```

The shell suite executes the exact full-context patch postimage with the real
C7 ARM BusyBox ash, applets and jshn through qemu-aarch64. All filesystem paths
are redirected to an isolated temporary tree; only its synthetic device files
are substituted for character nodes. Ubus is an inert public-metadata fixture.
Factory/boot writes, environment tools and reboot are logged stubs. Coverage:

- split raw DSD on the exact supported board: offset-zero, 4096-byte read;
- direct setFactory rejection before argument parsing, including malformed JSON;
- missing/short/malformed data retains metadata without inventing a serial;
- unrecognized board never selects the raw layout;
- existing aggregate vendor offset and priority, plus UBI DSD detection remain;
- unrelated factory password fields are omitted from the new read-only reply;
- different factory LAN/WAN and managed bridge MAC remain separate values.

The browser fixture blocks every network request and invokes the real render
function with synthetic replies. It checks that raw mode contains static values,
no inputs/save action or bootloader-upgrade hint, and no guessed U-Boot identity;
a factory failure still displays independent firmware/device metadata.

Raw split DSD is deliberately read-only, even when the kernel marks its MTD
writable. Its status path never writes fw_env configuration or invokes boot
environment tools. Existing supported vendor/UBI write implementations remain
unchanged and are never executed by these tests.
