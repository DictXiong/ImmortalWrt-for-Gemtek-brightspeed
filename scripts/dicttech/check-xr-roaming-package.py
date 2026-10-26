#!/usr/bin/env python3
"""Check package lifecycle in inert directories; never operate an AP."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path,
                        help="also inspect the unpacked/installed APK payload")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    package = repo / "package/dicttech/xr-roaming"
    makefile = (package / "Makefile").read_text()
    preinst = makefile.split("define Package/xr-roaming/preinst\n", 1)[1].split("\nendef", 1)[0]
    preinst = preinst.replace("$$", "$")
    results = []

    def check(name, fn):
        fn()
        results.append({"name": name, "passed": True})

    def hooks():
        with tempfile.TemporaryDirectory(prefix="xr-roaming-package-") as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir()
            service = root / "service"
            calls = root / "calls"
            service.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" >> " + shlex.quote(str(calls)) + "\n")
            service.chmod(0o700)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            sleep = bin_dir / "sleep"
            # A failed lock still exercises real flock. Do not spend 20 seconds
            # waiting for a deliberately permanent fixture lock.
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o700)
            script = preinst.replace("/etc/init.d/xr_roaming", str(service))
            script = script.replace("/tmp/xr-roaming-private", str(state))
            env = dict(os.environ, PATH=str(bin_dir) + ":" + os.environ["PATH"])
            for name in ["IPKG_INSTROOT", "IPKG_NO_SCRIPT"]:
                env.pop(name, None)

            def run(extra=None):
                return subprocess.run(["sh", "-s"], input=script, text=True,
                                      capture_output=True, env=dict(env, **(extra or {})), timeout=8)

            assert run({"IPKG_INSTROOT": str(root / "image")}).returncode == 0
            assert not calls.exists(), "offline image construction called a service"
            assert run({"IPKG_NO_SCRIPT": "1"}).returncode == 0
            assert not calls.exists(), "no-script installation called a service"
            assert run().returncode == 0
            assert calls.read_text().splitlines() == ["stop"]
            calls.unlink()
            lock = state / "lock"
            with lock.open("w") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                failed = run()
                assert failed.returncode != 0, "upgrade proceeded with a running old worker"
                assert "not released" in failed.stderr
                assert calls.read_text().splitlines() == ["stop", "start"]
            calls.unlink()
            assert run().returncode == 0
            assert calls.read_text().splitlines() == ["stop"]
            calls.unlink()
            lock.unlink()
            sentinel = root / "unrelated"
            sentinel.write_text("keep me\n")
            lock.symlink_to(sentinel)
            failed = run()
            assert failed.returncode != 0
            assert "symlinked" in failed.stderr
            assert sentinel.read_text() == "keep me\n"
            assert calls.read_text().splitlines() == ["stop", "start"]

    check("offline-and-upgrade-hooks-locks-and-failure-recovery", hooks)

    def defaults():
        config = (package / "files/xr_roaming.config").read_text()
        profiles = []
        for section in config.split("config roaming ")[1:]:
            profiles.append(section.splitlines()[0].strip("'"))
            assert "option enabled '0'" in section
            assert "option peer " not in section and "list peer_bssid " not in section
        assert profiles == ["main", "battalion", "guest", "iot"]
        conffiles = makefile.split("define Package/xr-roaming/conffiles\n", 1)[1].split("\nendef", 1)[0]
        assert conffiles.splitlines() == ["/etc/config/xr_roaming"]
        assert "uci-defaults" not in makefile
        helper = (package / "files/helper.sh").read_text()
        assert "CLI=/usr/sbin/hostapd_cli\n" in helper and "CLI_SHA=" not in helper
        assert "$(LN) /tmp/xr-roaming-public $(1)/www/xr-roaming" in makefile

    check("disabled-profile-and-package-persistence-contract", defaults)

    if args.installed_root:
        def payload():
            root = args.installed_root.resolve()
            for name in ["helper.sh", "common.jq", "cli-report.jq"]:
                assert (root / "usr/lib/xr-roaming" / name).read_bytes() == (package / "files" / name).read_bytes()
            assert (root / "etc/init.d/xr_roaming").read_bytes() == (package / "files/xr_roaming.init").read_bytes()
            link = root / "www/xr-roaming"
            assert link.is_symlink() and os.readlink(link) == "/tmp/xr-roaming-public"
            assert not (root / "usr/lib/xr-roaming/hostapd_cli").exists()
            assert not (root / "lib/upgrade/keep.d/xr_roaming").exists()
        check("built-apk-runtime-and-symlink-match-current-source", payload)

    print(json.dumps({"passed": True, "scope": "local inert fixtures", "results": results}, indent=2))


if __name__ == "__main__":
    main()
