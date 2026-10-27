#!/usr/bin/env python3
"""Run bootstrap with real C7 ARM ash/UCI/slim jq, using inert runtime fixtures.

The extracted ROM is read-only input. A private copy receives the selected source
bootstrap scripts; absolute filesystem references are redirected into that copy.
Actual init scripts may only enable/disable rc links. No network service starts,
host network interfaces, mounts, firmware, or remote devices are touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time


class Runtime:
    def __init__(self, source: Path, rom: Path, qemu: str, directory: Path):
        self.source, self.rom, self.qemu, self.base = source, rom, qemu, directory
        self.root, self.tools = directory / 'root', directory / 'tools'
        self.tools.mkdir()
        shutil.copytree(rom, self.root, symlinks=True)
        profile = source / 'package/dicttech/ap-profile/files'
        for rel in ['usr/lib/dicttech-ap', 'usr/libexec/dicttech-ap']:
            shutil.copytree(profile / rel, self.root / rel, dirs_exist_ok=True, symlinks=True)
        # early_mounts() creates /tmp/run and /tmp/lock before procd/rcS. Leaving
        # them out falsely makes actual procd.sh fail at its load-time lock.
        for rel in ['tmp/lock', 'tmp/run', 'proc/sys/kernel/random', 'sys', 'root', 'tmp/sysinfo']:
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        # Shell paths inside this synthetic passwd must match the redirected
        # activation helper's comparisons; no account on the host is modified.
        passwd = self.root / 'etc/passwd'
        rows = passwd.read_text().splitlines()
        passwd.write_text('\n'.join(':'.join(row.split(':')[:6] +
            [str(self.root / row.split(':')[6].lstrip('/'))]) if row.startswith('root:') else row
            for row in rows) + '\n')
        self.put('proc/sys/kernel/random/boot_id', b'11111111-1111-4111-8111-111111111111\n')
        self.put('tmp/sysinfo/board_name', b'gemtek,xr1710g\n')
        self.put('proc/uptime', b'100.00 0.00\n')
        self.runtime = self.base / 'runtime.json'
        self.runtime.write_text('{}')
        self.bb = rom / 'bin/busybox'
        self.ash = self.base / 'ash-dispatch'
        self.rc = self.base / 'rc-dispatch'
        prefix = [qemu, '-L', str(rom), str(self.bb), 'ash']
        self.executable(self.ash, '#!/bin/sh\nexec ' + shlex.join(prefix) + ' "$@"\n')
        self.executable(self.rc, '#!/bin/sh\ncase "$2" in enable|disable|enabled) ;; *) exit 90;; esac\n'
                        'exec ' + shlex.join(prefix + [str(self.root / 'etc/rc.common')]) + ' "$@"\n')
        roots = 'usr/libexec|usr/lib|usr/share|usr/sbin|usr/bin|sbin|bin|etc|root|tmp|lib|proc|sys'
        for rel in ['etc/init.d', 'usr/lib/dicttech-ap', 'usr/libexec/dicttech-ap',
                    'usr/libexec/dicttech-cli', 'lib', 'usr/share/libubox']:
            directory = self.root / rel
            if not directory.exists():
                continue
            for path in directory.rglob('*'):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    text = path.read_text()
                except UnicodeDecodeError:
                    continue
                if path.suffix != '.sh' and not text.startswith('#!/bin/sh') and path.name != 'rc.common':
                    continue
                # A variable-prefixed $IPKG_INSTROOT/lib path is already private.
                text = re.sub(r'(?<![A-Za-z0-9_@}])/(?:' + roots + r')/',
                              lambda m: str(self.root / m[0].strip('/')) + '/', text)
                text = text.replace('-C / ', '-C ' + shlex.quote(str(self.root)) + ' ')
                text = text.replace('"/$name"', '"' + str(self.root) + '/$name"')
                text = text.replace('destination=/$name', 'destination=' + str(self.root) + '/$name')
                text = text.replace('base=${2:-}', 'base=${2:-' + str(self.root) + '}')
                text = re.sub(r'^(?:export )?PATH=.*$', 'export PATH=' + shlex.quote(str(self.tools)), text, flags=re.M)
                if path.parent == self.root / 'etc/init.d':
                    text = re.sub(r'^#!.*', '#!' + str(self.rc), text, count=1)
                elif text.startswith('#!'):
                    text = re.sub(r'^#!.*', '#!' + str(self.ash), text, count=1)
                path.write_text(text)
        for name in ('awk basename cat chmod cmp cp cut echo env find grep head hexdump id ln ls mkdir '
                     'mktemp mv printf readlink rm sed sha256sum sort sync tail tar touch tr uname uniq wc').split():
            self.command(name, self.bb, [name])
        for name, rel, args in [
            ('uci', 'sbin/uci', ['-c', str(self.root / 'etc/config'), '-t', str(self.root / 'tmp/.uci')]),
            ('jq', 'usr/bin/jq', []), ('zsh', 'usr/bin/zsh', []),
            ('flock', 'usr/bin/util-linux-flock', []), ('timeout', 'usr/bin/timeout', []),
            ('jshn', 'usr/bin/jshn', []),
        ]:
            actual = self.resolve_program(rel)
            self.command(name, actual, args, rel)
        self.stub('ubus', '''import json,pathlib,sys
args=sys.argv[1:];index=args.index('call');name=args[index+1]
value=json.loads(pathlib.Path(RUNTIME).read_text()).get(name)
if value is None:sys.exit(1)
print(json.dumps(value))
''', 'bin/ubus')
        self.stub('ip', '''import json,pathlib,sys
assert sys.argv[1:5]==['-4','address','show','dev']
device=sys.argv[5];assert device in ['br-setup','br-setup.1']
data=json.loads(pathlib.Path(RUNTIME).read_text())
rows=data.get('network.interface.bootstrap',{}).get('ipv4-address',[]) if device=='br-setup.1' else []
print('1: '+device+': <UP>')
for row in rows:print('    inet '+row['address']+'/'+str(row['mask'])+' scope global '+device)
''', 'sbin/ip')
        for name, rel in [('nft', 'usr/sbin/nft'), ('wifi', 'sbin/wifi'), ('netifd', 'sbin/netifd')]:
            self.stub(name, 'raise RuntimeError("runtime service forbidden in fixture")\n', rel)
        self.stub('logger', 'pass\n')
        self.env = {**os.environ, 'PATH': str(self.tools), 'IPKG_INSTROOT': str(self.root),
                    'TMPDIR': str(self.root / 'tmp')}
        self.steps = []

    def resolve_program(self, rel):
        path = self.rom / rel
        for _ in range(16):
            if not path.is_symlink():
                if not path.is_file():
                    raise ValueError('required C7 program missing: ' + rel)
                return path
            target = Path(os.readlink(path))
            path = self.rom / str(target).lstrip('/') if target.is_absolute() else path.parent / target
        raise ValueError('C7 program symlink loop')

    def executable(self, path, text):
        if path.is_symlink():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text); path.chmod(0o700)

    def put(self, rel, value):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value); path.chmod(0o600)

    def command(self, name, actual, args, rel=None):
        argv = [self.qemu, '-L', str(self.rom), str(actual), *args]
        body = '#!' + sys.executable + '\nimport os,sys\nos.execv(' + repr(self.qemu) + ',' + repr(argv) + '+sys.argv[1:])\n'
        self.executable(self.tools / name, body)
        if rel:
            self.executable(self.root / rel, body)

    def stub(self, name, body, rel=None):
        text = '#!' + sys.executable + '\nRUNTIME=' + repr(str(self.runtime)) + '\n' + body
        self.executable(self.tools / name, text)
        if rel:
            self.executable(self.root / rel, text)

    def run(self, label, argv, good=True):
        before = time.monotonic()
        process = subprocess.Popen(argv, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=40)
        except subprocess.TimeoutExpired:
            # This process owns its fresh session and is not reaped until below.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise RuntimeError('C7 fixture command timeout: ' + label) from None
        self.steps.append({'step': label, 'returncode': process.returncode,
                           'seconds': round(time.monotonic() - before, 3)})
        if (process.returncode == 0) != good:
            # These are synthetic fresh-profile files; never print a whole ROM
            # config, password, authorized key or a target transaction response.
            (self.base / 'failed-command.stderr').write_text(stderr)
            raise RuntimeError('C7 fixture command failed: ' + label)
        return stdout

    def shell(self, label, rel, command, good=True):
        return self.run(label, [self.qemu, '-L', str(self.rom), str(self.bb), 'ash',
                               str(self.root / rel), command], good)

    def uci(self, label, *args):
        return self.run(label, [str(self.tools / 'uci'), *args])

    def activate(self, ready):
        identity = json.loads((self.root / 'etc/dicttech-ap/bootstrap-identity.json').read_text())
        self.put('sys/class/net/br-setup/address', (identity['mac'] + '\n').encode())
        self.put('sys/class/net/br-setup.1/address', (identity['mac'] + '\n').encode())
        status = {'up': ready, 'proto': 'dhcp'}
        if ready:
            status.update(l3_device='br-setup.1', **{'ipv4-address': [{'address': '192.0.2.20', 'mask': 24}]})
        else:
            status.update(device='br-setup.1', pending=True)
        self.runtime.write_text(json.dumps({
            'network.interface.ap_maintenance': {'up': True, 'proto': 'static', 'l3_device': 'lan4',
                'ipv4-address': [{'address': '192.168.250.1', 'mask': 24}]},
            'network.interface.bootstrap': status,
            'network.device': {'up': True, 'macaddr': identity['mac'], 'bridge-members': ['wan', 'lan2', 'lan3']},
        }))
        return identity

    def exercise(self):
        bootstrap = 'usr/lib/dicttech-ap/bootstrap.sh'
        transaction = 'usr/libexec/dicttech-ap/transaction'
        self.run('C7 slim jq rejects regex', [str(self.tools / 'jq'), '-cn', '"x"|test("x")'], good=False)
        if (self.root / 'etc/dicttech-ap/device.json').exists():
            raise ValueError('C7 ROM must not contain a configured device marker')
        for rel in ['etc/config/network', 'etc/config/wireless', 'etc/config/system']:
            (self.root / rel).unlink(missing_ok=True)
        self.shell('S04 fresh prepare', bootstrap, 'prepare-boot')
        shell = next(row.split(':')[6] for row in (self.root / 'etc/passwd').read_text().splitlines()
                     if row.startswith('root:'))
        if shell != str(self.root / 'usr/bin/zsh'):
            raise RuntimeError('C7 bounded CLI activation did not select the root shell')
        state = json.loads((self.root / 'etc/dicttech-ap/bootstrap-state.json').read_text())
        if state['mode'] != 'dhcp' or state['reason'] != 'no_marker':
            raise RuntimeError('C7 fresh bootstrap did not choose DHCP')
        identity = json.loads((self.root / 'etc/dicttech-ap/bootstrap-identity.json').read_text())
        if (self.root / 'etc/dicttech-ap/bootstrap-identity.json').stat().st_mode & 0o777 != 0o600:
            raise RuntimeError('C7 bootstrap identity permissions differ')
        original_network = (self.root / 'etc/config/network').read_bytes()
        # Reproduce S10's saved deltas, global commit and newly discovered radios.
        for statement in ['network.globals=globals', 'network.globals.ula_prefix=fd00::/48',
                          'wireless.radio0=wifi-device', 'wireless.radio0.disabled=0',
                          'wireless.fixture_ap=wifi-iface', 'wireless.fixture_ap.disabled=0']:
            self.uci('S10 synthetic normalization', 'set', statement)
        self.uci('S10 actual global commit', 'commit')
        helper = self.root / 'usr/libexec/dicttech-cli/activate-root-shell'
        helper.rename(helper.with_name('fixture-cli-helper-disabled'))
        self.shell('S13 finalization without optional CLI helper', bootstrap, 'finalize-boot')
        if (self.root / 'etc/config/network').read_bytes() != original_network:
            raise RuntimeError('C7 finalization did not restore the setup template')
        for section in ['radio0', 'fixture_ap']:
            if self.uci('S13 actual disabled radio', '-q', 'get', 'wireless.' + section + '.disabled').strip() != '1':
                raise RuntimeError('C7 finalization left Wi-Fi enabled')
        if not (self.root / 'etc/rc.d/S20network').is_symlink():
            raise RuntimeError('C7 actual init enable did not keep networking enabled')
        for ready in [True, False]:
            self.activate(ready)
            proof = json.loads(self.shell('C7 read-only setup proof', bootstrap, 'check-setup'))
            if proof['mode'] != 'dhcp' or proof['dhcp_ready'] != ready or proof['bridge_mac'] != identity['mac']:
                raise RuntimeError('C7 setup proof differs')
            contract = json.loads(self.shell('C7 transaction target identity', transaction, 'check'))
            target = contract['identity']
            if (not target['bootstrap_dhcp'] or target['bootstrap_dhcp_ready'] != ready or
                    target['bridge_mac'] != identity['mac'] or target['ssh_port'] != '12022'):
                raise RuntimeError('C7 transaction target identity lost bootstrap proof')
        for rel in ['etc/config/dropbear', 'etc/config/network']:
            path = self.root / rel; original = path.read_bytes()
            path.write_bytes(original + b'# fixture tamper\n')
            self.shell('C7 rejects altered setup template', bootstrap, 'check-setup', good=False)
            path.write_bytes(original)
        changed = {**identity, 'mac': '02:00:00:00:00:GG', 'hostname': 'xr1710g-setup-0200000000GG'}
        self.put('etc/dicttech-ap/bootstrap-identity.json', json.dumps(changed).encode())
        self.shell('C7 rejects nonhex identity', bootstrap, 'check-setup', good=False)
        self.put('etc/dicttech-ap/bootstrap-identity.json', json.dumps(identity, separators=(',', ':')).encode() + b'\n')
        self.shell('C7 proof restored', bootstrap, 'check-setup')
        return {'passed': True, 'steps': self.steps, 'dhcp_and_offline_pending_proven': True,
                'actual_init_enable_exercised': True, 'slim_jq_without_regex': True,
                'bounded_cli_success_and_missing_helper_proven': True,
                'ash_sha256': hashlib.sha256(self.bb.read_bytes()).hexdigest(),
                'jq_sha256': hashlib.sha256((self.rom / 'usr/bin/jq').read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--c7-rootfs', type=Path, required=True, help='extracted read-only C7 ARM ROM')
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--qemu', default=shutil.which('qemu-aarch64'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--keep-fixture', action='store_true', help='keep the private tree for failure diagnosis')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('the private target ownership/transaction fixture requires root')
    if not args.qemu:
        parser.error('qemu-aarch64 is required')
    rom, source = args.c7_rootfs.resolve(), args.source_root.resolve()
    if rom == Path('/') or not (rom / 'bin/busybox').is_file():
        parser.error('C7 rootfs must be an extracted ROM directory')
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix='dicttech-c7-bootstrap-'))
    report = {'schema_version': 1, 'offline_only': True, 'router_access': False,
              'host_services_modified': False, 'passed': False}
    try:
        report.update(Runtime(source, rom, args.qemu, directory).exercise())
    except (RuntimeError, ValueError, OSError, KeyError) as error:
        report['failure'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    finally:
        if args.keep_fixture:
            report['private_fixture'] = str(directory)
        else:
            shutil.rmtree(directory)
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        if args.output.is_symlink():
            parser.error('refusing a symbolic-link output')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text); args.output.chmod(0o600)
    print(text, end='')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
