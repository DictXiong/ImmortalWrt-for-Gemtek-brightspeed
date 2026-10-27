"""Real helper/bootstrap lifecycle boundaries on an inert private filesystem."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from setup import SetupFixture


def main():
    results = []
    def check(name, function):
        with tempfile.TemporaryDirectory(prefix='dicttech-initialization-key-') as base:
            fixture = SetupFixture(base)
            function(fixture)
        results.append({'case': name, 'passed': True})

    def paths(f):
        return (f.root / 'etc/dropbear/authorized_keys', f.root / 'etc/dicttech-ap-bootstrap-key/state',
                (f.root / 'usr/share/dicttech-cli/authorized_keys').read_bytes(),
                (f.root / 'usr/share/dicttech-ap/bootstrap-authorized-key.pub').read_bytes())

    def helper(f, action, good=True):
        q = subprocess.run([os.environ.get('DICTTECH_AP_TEST_SHELL', '/bin/sh'),
                            str(f.root / 'usr/libexec/dicttech-ap/bootstrap-key'), action],
                           env={**os.environ, 'PATH': str(f.bin) + ':' + os.environ['PATH']},
                           capture_output=True, text=True, timeout=15)
        assert (q.returncode == 0) == good, (action, q.returncode, q.stderr)

    def fresh(f):
        f.call('prepare')
        auth, state, owner, key = paths(f)
        assert auth.read_bytes() == owner + key and state.read_bytes() == b'fresh\n'
        assert auth.stat().st_mode & 0o777 == 0o600 and state.stat().st_mode & 0o777 == 0o600
        assert state.parent.stat().st_mode & 0o777 == 0o700
        before = auth.stat().st_mtime_ns, state.stat().st_mtime_ns
        helper(f, 'fresh'); f.call('finalize')
        assert (auth.stat().st_mtime_ns, state.stat().st_mtime_ns) == before

    def revoked(f, empty):
        f.call('prepare'); auth, state, owner, key = paths(f)
        auth.write_bytes(b'' if empty else owner)
        f.write('proc/sys/kernel/random/boot_id', b'00000000-0000-4000-8000-000000000002\n')
        (f.root / 'tmp/dicttech-ap-bootstrap-isolated').unlink()
        f.call('prepare')
        assert auth.read_bytes() == (b'' if empty else owner) and state.read_bytes() == b'fresh\n'

    def explicit_before_first(f):
        auth, state, owner, key = paths(f)
        auth.write_bytes(b'')
        f.call('prepare')
        assert auth.read_bytes() == b'' and state.read_bytes() == b'retired\n'

    def pending(f):
        f.call('prepare'); auth, state, owner, key = paths(f)
        f.write('root/.dicttech-ap-transaction/manifest.json', json.dumps({'approved_target': {'initialize': True},
                'boot_id': (f.root / 'proc/sys/kernel/random/boot_id').read_text().strip()}).encode())
        f.write('root/.dicttech-ap-transaction/applied', b'fixture\n')
        helper(f, 'managed')
        assert auth.read_bytes() == owner + key and state.read_bytes() == b'fresh\n'
        auth.write_bytes(b'')
        helper(f, 'managed')
        assert auth.read_bytes() == b'', 'explicit [] must never be supplemented'
        auth.write_bytes(owner + key)
        helper(f, 'retire')
        assert auth.read_bytes() == owner and state.read_bytes() == b'retired\n'
        shutil.rmtree(f.root / 'etc/dicttech-ap')
        (f.root / 'etc/dicttech-ap').mkdir(mode=0o700)
        (f.root / 'tmp/dicttech-ap-bootstrap-isolated').unlink()
        f.call('prepare')
        assert auth.read_bytes() == owner and state.read_bytes() == b'retired\n'

    def configured_or_quarantined(f, marker):
        auth, state, owner, key = paths(f)
        if marker:
            f.write('etc/dicttech-ap/device.json', b'{}\n')
        else:
            (f.root / 'root/dicttech-ap-quarantine').mkdir(parents=True)
        f.call('prepare')
        assert auth.read_bytes() == owner and state.read_bytes() == b'retired\n'

    def malformed_state(f, symlink):
        f.call('prepare'); auth, state, owner, key = paths(f)
        if symlink:
            target = f.root / 'tmp/untouched-state'; target.write_bytes(b'private fixture\n')
            state.unlink(); state.symlink_to(target)
            helper(f, 'fresh', good=False)
            assert target.read_bytes() == b'private fixture\n'
        else:
            state.write_bytes(b'unknown\n'); helper(f, 'fresh')
            assert state.read_bytes() == b'retired\n'
        assert auth.read_bytes() == owner

    def filtering(f):
        f.call('prepare'); auth, state, owner, key = paths(f)
        # Another key's comment can mention the temporary public identity.
        unrelated = next(line for line in owner.splitlines() if line.startswith(b'ssh-ed25519 '))
        identity = b' '.join(key.split()[:2])
        preserved = unrelated + b' comment says ' + identity + b' "unfinished comment\n'
        comment = b'# initialization public identity: ' + identity + b'\n'
        options = b'command="echo words",no-pty ' + identity + b' changed comment\n'
        actual = identity + b' "unterminated arbitrary key comment\n'
        auth.write_bytes(preserved + comment + options + actual)
        helper(f, 'retire')
        assert auth.read_bytes() == preserved + comment

    def corrupt_pending_marker(f):
        f.call('prepare'); auth, state, owner, key = paths(f)
        f.write('etc/dicttech-ap/device.json', b'{}\n')
        f.call('finalize')
        assert auth.read_bytes() == owner and state.read_bytes() == b'retired\n'

    def failed_grant_not_retried(f):
        # Stamp-first crash/failure intentionally fails closed. Recovery uses
        # existing owner keys or LAN4, never an automatic repeated grant.
        auth, state, owner, key = paths(f)
        auth.unlink()
        helper(f, 'fresh', good=False)
        auth.write_bytes(owner); auth.chmod(0o600)
        helper(f, 'fresh')
        assert auth.read_bytes() == owner and state.read_bytes() == b'fresh\n'

    check('fresh_grant_once_readonly_repeated_prepare', fresh)
    check('manual_removal_survives_same_fresh_reboot', lambda f: revoked(f, False))
    check('explicit_empty_survives_same_fresh_reboot', lambda f: revoked(f, True))
    check('explicit_empty_before_first_boot_never_supplemented', explicit_before_first)
    check('pending_hold_explicit_empty_confirm_retire_and_lost_marker', pending)
    check('configured_invalid_marker_retires_before_quarantine', lambda f: configured_or_quarantined(f, True))
    check('quarantine_without_marker_never_reopens', lambda f: configured_or_quarantined(f, False))
    check('malformed_state_removes_grant', lambda f: malformed_state(f, False))
    check('symlink_state_removes_grant_without_following', lambda f: malformed_state(f, True))
    check('filter_actual_identity_preserves_other_comments', filtering)
    check('corrupt_marker_during_setup_retires_before_fallback', corrupt_pending_marker)
    check('interrupted_initial_grant_fails_closed_without_retry', failed_grant_not_retried)
    print(json.dumps(results))


if __name__ == '__main__': main()
