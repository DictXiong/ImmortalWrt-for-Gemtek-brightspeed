# Inert roaming regression fixtures

Run `python3 scripts/dicttech/test-xr-roaming.py` from a checkout. The runner
copies these inputs and the actual package runtime to a temporary directory;
tests never modify the source tree. Python 3, jq and ordinary POSIX utilities
are needed. The fixture creates local UNIX sockets, so a sandbox must permit
those sockets. It does not access routers or real network services.

Run in a Linux environment as root: the unchanged runtime intentionally rejects
state directories whose numeric owner is not root, and the lifecycle fixtures
use Linux `/proc`. The runner checks this prerequisite explicitly; it does not
elevate privileges or invoke sudo itself.

Use `--c7-rootfs PATH --qemu PATH` to additionally exercise a supplied target
BusyBox ash and slim jq under qemu. Without those inputs the two target-only
checks are explicitly skipped; native lifecycle and cache checks still run.
The target rootfs is an external test prerequisite and is not part of Git.

All addresses, MAC identities and SSIDs here are synthetic examples. The
`proof` inputs preserve C7's hostapd wire format, including per-link BSS data;
their file names are historical fixture labels, not claims of live testing.
Generated JSON reports are written only to the temporary staging tree, unless
`--output` explicitly requests the combined result at another path.

Coverage includes strict peer validation, cross-SSID separation, malformed and
stale metadata, missing/changed radio links, 180-second revision TTL, cached
30/60/300-second scheduling, suppressed redundant writes, table-clear repair,
TERM/lock release, read-only status and procd declaration reconciliation. The
old benchmark comparing v2 implementation details is intentionally omitted.
