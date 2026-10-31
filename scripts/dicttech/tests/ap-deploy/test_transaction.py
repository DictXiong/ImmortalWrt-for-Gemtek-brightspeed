#!/usr/bin/env python3
"""Inert transaction tests: every path, command, clock and service is private."""
from __future__ import annotations
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import tarfile
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[4]
TOOL = REPO / 'package/dicttech/ap-profile/files/usr/libexec/dicttech-ap/transaction'
CONTROLLER = REPO / 'scripts/dicttech/ap-deploy.py'
spec = importlib.util.spec_from_file_location('ap_deploy', CONTROLLER)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def wait(fn, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            if fn():
                return
        except (FileNotFoundError, ValueError):
            pass
        time.sleep(.04)
    raise AssertionError('private fixture did not reach expected state')


class Transaction(unittest.TestCase):
    def setUp(self):
        self.shell = shlex.split(os.environ.get('DICTTECH_TEST_ASH', '/bin/sh'))
        self.tmp = tempfile.TemporaryDirectory(prefix='dicttech-transaction-')
        self.base = Path(self.tmp.name)
        # Detached watch/reload workers and their timeout/service descendants
        # inherit this unique marker. Their ready files may disappear or be
        # replaced before teardown, so a ready PID is not a process inventory.
        self.env = {**os.environ, 'DICTTECH_TRANSACTION_FIXTURE': str(self.base)}
        self.process_marker = ('DICTTECH_TRANSACTION_FIXTURE=' + str(self.base)).encode()
        self.root = self.base / 'root'; self.root.mkdir()
        self.state = self.root / 'root/.dicttech-ap-transaction'
        self.clock = self.base / 'uptime'; self.clock.write_text('100.00 0\n')
        self.boot = self.base / 'boot'; self.boot.write_text('11111111-1111-4111-8111-111111111111\n')
        self.pending = self.base / 'pending'
        self.script = self.base / 'transaction'
        self.service_log = self.base / 'service-log'
        self.service_state = self.base / 'service-state'
        self.service_state.write_text('{"enabled":["fixture_service"],"running":["fixture_service"]}')
        self.original_services = self.service_state.read_bytes()
        self.service_hang = self.base / 'service-hang'
        self.service_fail = self.base / 'service-fail'
        self.service_restore_barrier = self.base / 'service-restore-barrier'
        self.service_started = self.base / 'service-started'
        self.services = self.base / 'services'
        self.services.write_text('#!' + shutil.which('python3') + '\nimport sys,pathlib,json,time,os\n'
            + 'state=pathlib.Path(' + repr(str(self.service_state)) + ')\n'
            + 'with open(' + repr(str(self.service_log)) + ',"a") as log:log.write(" ".join(sys.argv[1:]) + "\\n")\n'
            + 'if sys.argv[1]=="snapshot":pathlib.Path(sys.argv[2]).write_bytes(state.read_bytes())\n'
            + 'elif sys.argv[1]=="apply":\n'
            + ' state.write_text(json.dumps({"enabled":["candidate"],"running":["candidate"]}))\n'
            + ' if pathlib.Path(' + repr(str(self.service_fail)) + ').exists():sys.exit(7)\n'
            + ' if pathlib.Path(' + repr(str(self.service_hang)) + ').exists():\n'
            + '  pathlib.Path(' + repr(str(self.service_started)) + ').write_text(str(os.getpid()))\n'
            + '  time.sleep(60)\n'
            + 'elif sys.argv[1]=="restore":\n'
            + ' if sys.argv[3]=="running":\n'
            + '  while pathlib.Path(' + repr(str(self.service_restore_barrier)) + ').exists():time.sleep(.02)\n'
            + ' old=json.loads(pathlib.Path(sys.argv[2]).read_text());current=json.loads(state.read_text())\n'
            + ' key="enabled" if sys.argv[3]=="links" else "running"\n'
            + ' current[key]=old[key];state.write_text(json.dumps(current))\n')
        self.services.chmod(0o700)
        self.ubus = self.base / 'ubus'
        self.ubus_ready = self.base / 'ubus-ready'
        self.ubus.write_text('#!/bin/sh\n[ -f ' + str(self.ubus_ready) + ' ]\n')
        self.ubus.chmod(0o700)
        self.uci = self.base / 'uci'
        self.uci.write_text('#!/bin/sh\n[ ! -f ' + str(self.pending) + ' ] || cat ' + str(self.pending) + '\n')
        self.uci.chmod(0o700)
        self.init = self.base / 'init'
        self.init.write_text('#!' + shutil.which('python3') + '\nimport subprocess,sys\n'
                             'if sys.argv[1]=="start":\n'
                             ' subprocess.Popen(' + repr(self.shell + [str(self.script), 'watch']) + ',stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\n')
        self.init.chmod(0o700)
        text = TOOL.read_text().replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'export PATH=' + os.environ['PATH'])
        replacements = {
            'SYSROOT': str(self.root), 'STATE': str(self.state),
            'ARCHIVES': str(self.root / 'root/dicttech-ap-backups'),
            'SELF': str(self.script), 'INIT': str(self.init),
            'SERVICES': str(self.services), 'UBUS': str(self.ubus), 'BOOTSTRAP': str(self.base / 'no-bootstrap'),
            'KEYLIFE': str(self.root / 'usr/libexec/dicttech-ap/bootstrap-key'),
            'JQ': shutil.which('jq'), 'UCI': str(self.uci),
            'TIMEOUT': shutil.which('timeout'), 'BOOT': str(self.boot), 'UPTIME': str(self.clock),
        }
        import re
        for key, value in replacements.items():
            text, count = re.subn(r'^' + key + '=.*$', key + '=' + shlex.quote(value), text, flags=re.M)
            self.assertEqual(count, 1, key)
        self.script.write_text(text); self.script.chmod(0o700)
        self.put('tmp/sysinfo/board_name', b'gemtek,xr1710g\n')
        self.put('sys/class/net/br-lan/address', b'02:00:00:00:01:00\n')
        self.put('usr/share/dicttech-ap/contract.json', b'{"profile":"openwrt-config-v3","schema_version":3,"platform":{"board_names":["gemtek,xr1710g"]}}\n')
        self.put('etc/config/network', b'original network\n', 0o644)
        self.put('etc/shadow', b'root:oldhash:20000:0:99999:7:::\nservice:locked:20000:0:99999:7:::\n')
        self.put('etc/dropbear/dropbear_ed25519_host_key', b'fixture-host-key\n')
        self.marker = {'profile':'openwrt-config-v3','schema_version':3,'device_id':'fixture_ap',
                       'resolver':{'target':'/tmp/resolv.conf.d/resolv.conf.auto'}, 'platform':{'bridge':'br-lan'}, 'device':{'identity':{'bridge':'02:00:00:00:01:00'}}, 'services':{}}
        self.put('etc/dicttech-ap/device.json', json.dumps(self.marker).encode()+b'\n')
        self.put('etc/dicttech-ap/managed-files.json', json.dumps({'schema_version':3,'profile':'openwrt-config-v3',
                 'files':[{'path':name,'mode':'644' if name=='etc/config/network' else '600','sha256':hashlib.sha256((self.root/name).read_bytes()).hexdigest()}
                          for name in ['etc/config/network','etc/dicttech-ap/device.json']]}).encode()+b'\n')
        self.before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.files = {
            'etc/config/network': b'candidate network\n',
            'etc/config/wireless': b'candidate wireless with confidential fixture key\n',
            'etc/dicttech-ap/root-password-hash': b'$6$fixture$newhash\n',
            'etc/dicttech-ap/device.json': json.dumps(self.marker).encode()+b'\n',
        }

    def put(self, path, value, mode=0o600):
        p = self.root / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(value); p.chmod(mode)

    def call(self, command, token=None, data=None, good=True):
        if command == 'check' and token is None and hasattr(self, 'files'):
            token='request'; data=json.dumps({'paths':list(self.files)}).encode()
        result = subprocess.run(self.shell + [str(self.script), command] + ([token] if token else []), input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                                env=self.env, start_new_session=True)
        self.assertEqual(result.returncode == 0, good, (result.returncode, result.stderr.decode()))
        return json.loads(result.stdout) if good and result.stdout else result

    def stage(self, files=None, mutate=None):
        contract = self.call('check', 'request', data=json.dumps({'paths':list(files or self.files)}).encode())
        manifest, payload = deploy.bundle(files or self.files, contract, 'fixture_ap', 120)
        if mutate:
            mutate(manifest)
            payload = deploy.tar_bytes(files or self.files, manifest)
        result = self.call('stage', data=payload)
        self.assertEqual(result['token'], manifest['token'])
        self.token = manifest['token']
        return manifest

    @staticmethod
    def process_state(pid):
        try:
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
            return fields[0], int(fields[19])  # state and Linux starttime ticks
        except (OSError, ValueError, IndexError):
            return None

    def owns_process(self, pid, ticks):
        before = self.process_state(pid)
        if before is None or before[0] == 'Z' or before[1] != ticks:
            return False
        try:
            owned = self.process_marker in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return False
        after = self.process_state(pid)
        return owned and after is not None and after[0] != 'Z' and after[1] == ticks

    def fixture_processes(self):
        result = set()
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal():
                continue
            pid = int(path.name)
            info = self.process_state(pid)
            if info is not None and self.owns_process(pid, info[1]):
                result.add((pid, info[1]))
        return result

    def signal_fixture_process(self, pid, ticks, sig):
        descriptor = None
        try:
            # A pidfd pins the selected process across the final ownership
            # check and signal. Older local Python/kernel combinations retain
            # the PID/starttime+marker check as a fallback.
            if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
                try:
                    descriptor = os.pidfd_open(pid)
                except OSError as error:
                    if error.errno != errno.ENOSYS:
                        raise
            if not self.owns_process(pid, ticks):
                return
            if descriptor is not None:
                signal.pidfd_send_signal(descriptor, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def stop_fixture_processes(self):
        # Stop all writers, including delayed reload launches and grandchildren.
        # Rescan because a worker may fork while handling TERM. Zombies cannot
        # write files; PID 1 owns reaping the fixture's detached descendants.
        for sig, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
            signaled = set()
            deadline = time.monotonic() + seconds
            empty = 0
            while True:
                remaining = self.fixture_processes()
                if not remaining:
                    # A /proc directory snapshot can miss a just-forked child
                    # whose parent exited during iteration. Require a second
                    # empty scan before removing this fixture's directory.
                    empty += 1
                    if empty == 2:
                        return
                else:
                    empty = 0
                for process in remaining - signaled:
                    self.signal_fixture_process(*process, sig)
                signaled.update(remaining)
                if time.monotonic() >= deadline:
                    break
                time.sleep(.04)
        self.assertFalse(self.fixture_processes(), 'owned fixture workers did not exit')

    def tearDown(self):
        self.stop_fixture_processes()
        self.tmp.cleanup()

    def assertOriginal(self, services=True):
        for name, value in self.before.items():
            self.assertEqual((self.root / name).read_bytes(), value, name)
        for name in ['etc/config/wireless', 'etc/dicttech-ap/root-password-hash']:
            if name not in self.before:
                self.assertFalse((self.root / name).exists())
        self.assertFalse((self.root / 'etc/resolv.conf').is_symlink())
        self.assertFalse((self.root / 'etc/resolv.conf').exists())
        self.assertEqual((self.root / 'etc/config/network').stat().st_mode & 0o777, self.original_network_mode if hasattr(self, 'original_network_mode') else 0o644)
        if services:
            self.assertEqual(json.loads(self.service_state.read_text()), json.loads(self.original_services))

    def test_controller_transport_keeps_payload_off_argv_and_rejects_tokens(self):
        import argparse
        from unittest.mock import patch
        args = argparse.Namespace(host='ap.example.invalid', port=12022, control_socket='/tmp/example-master', jump_host='root@pi.example.invalid:12022')
        secret = b'fixture secret over stdin only'
        response = subprocess.CompletedProcess([], 0, b'{"state":"staged"}', b'')
        with patch.object(deploy.subprocess, 'run', return_value=response) as call:
            self.assertEqual(deploy.remote(args, 'stage', data=secret)['state'], 'staged')
            argv = call.call_args.args[0]
            self.assertNotIn(secret.decode(), ' '.join(argv))
            self.assertEqual(call.call_args.kwargs['input'], secret)
            self.assertEqual(argv[-1], '/usr/libexec/dicttech-ap/transaction stage')
        with self.assertRaises(ValueError):
            deploy.remote(args, 'confirm', 'token; touch invalid')

    def test_system_and_ntp_reload_new_configuration_and_rollback_in_order(self):
        original = b"config system 'system'\n option hostname 'original-node'\n option timezone 'UTC0'\nconfig timeserver 'ntp'\n list server 'old.example.invalid'\n"
        candidate = b"config system 'system'\n option hostname 'candidate-node'\n option timezone 'CST-8'\nconfig timeserver 'ntp'\n list server 'new.example.invalid'\n"
        self.put('etc/config/system', original)
        self.before['etc/config/system'] = original
        self.files['etc/config/system'] = candidate
        self.marker['services'] = {name:{'enable':True,'running':None,'reloadOn':{'uci':['system'],'files':[]},'action':'reload'} for name in ['system','network','sysntpd']}
        encoded=json.dumps(self.marker).encode()+b'\n'
        self.put('etc/dicttech-ap/device.json',encoded)
        self.before['etc/dicttech-ap/device.json']=encoded
        self.files['etc/dicttech-ap/device.json']=encoded
        events = self.base / 'reload-events'
        for service in ['system', 'network', 'sysntpd']:
            runtime = self.base / ('effective-' + service)
            body = '#!' + shutil.which('python3') + '\nimport sys,json,pathlib\n'
            body += 'assert sys.argv[1]=="reload"\n'
            body += 'config=pathlib.Path(' + repr(str(self.root / 'etc/config/system')) + ').read_text()\n'
            body += 'with open(' + repr(str(events)) + ',"a") as log:log.write(json.dumps({"service":' + repr(service) + ',"config":config})+"\\n")\n'
            body += 'pathlib.Path(' + repr(str(runtime)) + ').write_text(config)\n'
            path = self.root / 'etc/init.d' / service
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body); path.chmod(0o700)
        self.stage(); self.call('apply', self.token)
        wait(lambda: (self.state / 'reload-complete').exists())
        applied = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual([row['service'] for row in applied], ['network', 'sysntpd', 'system'])
        self.assertTrue(all(row['config'] == candidate.decode() for row in applied))
        self.assertEqual((self.base / 'effective-system').read_bytes(), candidate)
        self.assertEqual((self.base / 'effective-sysntpd').read_bytes(), candidate)
        self.call('rollback', self.token)
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        restored = [json.loads(line) for line in events.read_text().splitlines()][len(applied):]
        self.assertEqual([row['service'] for row in restored], ['network', 'sysntpd', 'system'])
        self.assertTrue(all(row['config'] == original.decode() for row in restored))
        self.assertEqual((self.base / 'effective-system').read_bytes(), original)
        self.assertEqual((self.base / 'effective-sysntpd').read_bytes(), original)
        self.assertOriginal()

    def test_apply_confirm_preserves_other_shadow_and_host_keys(self):
        self.stage(); self.call('apply', self.token)
        wait(lambda: (self.state / 'reload-complete').exists())
        self.assertEqual((self.root / 'etc/shadow').read_text(),
                         'root:$6$fixture$newhash:20000:0:99999:7:::\nservice:locked:20000:0:99999:7:::\n')
        self.assertEqual((self.root / 'etc/dropbear/dropbear_ed25519_host_key').read_bytes(), b'fixture-host-key\n')
        self.assertEqual(self.call('confirm', self.token)['state'], 'confirmed')
        self.clock.write_text('999.00 0\n'); self.call('early')
        self.assertEqual((self.root / 'etc/config/network').read_bytes(), self.files['etc/config/network'])

    def test_unconfirmed_expiry_autonomously_restores_exact_files_and_modes(self):
        self.stage(); self.call('apply', self.token)
        self.clock.write_text('221.00 0\n')
        wait(lambda: (self.state / 'rolled-back').exists() and not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()

    def test_unexpected_boot_early_restores_before_network_start(self):
        self.stage(); self.call('apply', self.token)
        self.boot.write_text('22222222-2222-4222-8222-222222222222\n')
        self.call('early'); self.assertOriginal(services=False)
        self.assertEqual(self.call('status')['state'], 'rollback_pending')
        self.assertNotIn(' running', self.service_log.read_text())
        self.ubus_ready.touch(); self.call('finish-rollback')
        self.assertEqual(self.call('status')['state'], 'rolled-back')

    def test_finish_worker_reports_bounded_incomplete_until_ubus_ready(self):
        self.stage(); self.call('apply', self.token)
        self.boot.write_text('22222222-2222-4222-8222-222222222222\n')
        self.call('early')
        self.script.write_text(self.script.read_text().replace('sleep 2', 'sleep .01'))
        self.call('finish-rollback', good=False)
        self.assertEqual(self.call('status')['state'], 'rollback_incomplete')
        self.assertNotIn(' running', self.service_log.read_text())
        self.ubus_ready.touch(); self.call('finish-rollback')
        self.assertEqual(self.call('status')['state'], 'rolled-back')
        self.assertOriginal()

    def test_explicit_rollback_and_confirmation_readback(self):
        self.stage(); self.call('apply', self.token)
        self.put('etc/config/network', b'changed outside transaction\n')
        self.call('confirm', self.token, good=False)
        self.call('rollback', self.token)
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()

    def test_stage_rejects_pending_uci_without_mutation(self):
        self.pending.write_text("network.lan.proto='static'\n")
        contract = self.call('check'); _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        self.call('stage', data=payload, good=False); self.assertOriginal()

    def test_payload_digest_and_allowlist_rejected_before_mutation(self):
        contract = self.call('check'); manifest, _ = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        manifest['files'][0]['sha256'] = '0' * 64
        self.call('stage', data=deploy.tar_bytes(self.files, manifest), good=False); self.assertOriginal()
        for path in ('etc/dropbear/dropbear_ed25519_host_key', 'etc/gshadow',
                     'etc/openwrt-deploy', 'etc/openwrt-deploy/managed.json',
                     'etc/dicttech-ap', 'etc/dicttech-ap-bootstrap-key',
                     'etc/dicttech-ap-bootstrap-key/unowned'):
            with self.subTest(path=path):
                unsafe = {**self.files, path: b'never install\n'}
                _, payload = deploy.bundle(unsafe, contract, 'fixture_ap', 120)
                self.call('stage', data=payload, good=False); self.assertOriginal()

    def test_invalid_root_hash_rejected_before_any_write(self):
        contract = self.call('check')
        files = dict(self.files); files['etc/dicttech-ap/root-password-hash'] = b'$6$hash:injected\n'
        _, payload = deploy.bundle(files, contract, 'fixture_ap', 120)
        self.call('stage', data=payload, good=False); self.assertOriginal()

    def test_duplicate_and_extra_tar_members_rejected(self):
        contract = self.call('check'); manifest, _ = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        payload = deploy.tar_bytes(self.files, manifest)
        for name in ['manifest.json', 'extra-file']:
            f = io.BytesIO(payload)
            with tarfile.open(fileobj=f, mode='a') as tar:
                info = tarfile.TarInfo(name); info.size = 1; tar.addfile(info, io.BytesIO(b'x'))
            self.call('stage', data=f.getvalue(), good=False); self.assertOriginal()

    def test_stale_boot_and_defaults_rejected(self):
        contract = self.call('check'); manifest, _ = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        manifest['boot_id'] = '22222222-2222-4222-8222-222222222222'
        self.call('stage', data=deploy.tar_bytes(self.files, manifest), good=False)
        manifest['boot_id'] = contract['boot_id']; manifest['contract_sha256'] = '0' * 64
        self.call('stage', data=deploy.tar_bytes(self.files, manifest), good=False); self.assertOriginal()

    def test_target_rejects_wrong_registered_identity_and_unapproved_initialization(self):
        contract = self.call('check')
        approved = {'device_id': 'fixture_ap', 'bridge_mac': '02:00:00:00:99:00', 'allowed_boards': ['gemtek,xr1710g'], 'initialize': False}
        _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120, approved)
        self.call('stage', data=payload, good=False)
        approved['initialize'] = True
        _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120, approved)
        self.call('stage', data=payload, good=False)
        self.assertOriginal()

    def fresh_bootstrap(self, mode='dhcp'):
        # Ordinary transaction fixtures start with an owner's existing password;
        # a fresh-device fixture must start with a locked/empty factory account.
        self.put('etc/shadow', b'root:!:20000:0:99999:7:::\nservice:locked:20000:0:99999:7:::\n')
        fixture_source = CONTROLLER.parent / 'tests/bootstrap/setup.py'
        spec = importlib.util.spec_from_file_location('bootstrap_setup_fixture', fixture_source)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for rel in ['etc/dicttech-ap/device.json','etc/dicttech-ap/managed-files.json']:(self.root/rel).unlink(missing_ok=True)
        setup = module.SetupFixture(self.base, self.root)
        setup.write('proc/sys/kernel/random/boot_id', self.boot.read_bytes())
        if mode == 'isolated': setup.shell_tool('hexdump', 'exit 1\n')
        shutil.rmtree(self.root / 'sys/class/net/br-lan')
        setup.call('prepare'); setup.activate(); setup.call('check-setup')
        self.script.write_text(self.script.read_text().replace(
            'BOOTSTRAP=' + shlex.quote(str(self.base / 'no-bootstrap')),
            'BOOTSTRAP=' + shlex.quote(str(setup.script))))
        self.before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.original_network_mode = 0o600
        return setup

    def initialize_with_real_key_helper(self, explicit_auth=None):
        setup = self.fresh_bootstrap()
        # Keep the real read-only setup proof for check/stage/apply identity.
        # Only mock the full-config finalize consumer of this minimal payload.
        setup.script.write_text(setup.script.read_text().replace('set -eu\n',
            'set -eu\n[ "${1:-}" != finalize ] || exit 0\n', 1))
        contract = self.call('check')
        approved = {'device_id': 'fixture_ap', 'bridge_mac': '02:00:00:00:99:00',
                    'allowed_boards': ['gemtek,xr1710g'], 'initialize': True,
                    'expect_boot_id': contract['boot_id']}
        files = dict(self.files)
        if explicit_auth is not None:
            files['etc/dropbear/authorized_keys'] = explicit_auth
        manifest, payload = deploy.bundle(files, contract, 'fixture_ap', 120, approved)
        self.token = manifest['token']; self.call('stage', data=payload)
        self.call('apply', self.token)
        self.put('sys/class/net/br-lan/address', approved['bridge_mac'].encode() + b'\n')
        wait(lambda: (self.state / 'reload-complete').exists())
        return setup

    def call_key_helper(self, action):
        subprocess.run(self.shell + [str(self.root / 'usr/libexec/dicttech-ap/bootstrap-key'), action],
                       check=True, env=self.env, capture_output=True, timeout=15)

    def test_initialize_omitted_key_pending_confirm_and_retired_reboot(self):
        self.initialize_with_real_key_helper()
        auth = self.root / 'etc/dropbear/authorized_keys'
        state = self.root / 'etc/dicttech-ap-bootstrap-key/state'
        owner = (self.root / 'usr/share/dicttech-cli/authorized_keys').read_bytes()
        default_hash = (self.root / 'usr/share/dicttech-ap/bootstrap-password-hash').read_text().strip()
        self.call_key_helper('managed')
        self.assertEqual(auth.read_bytes(), owner)
        self.assertEqual((self.root / 'etc/shadow').read_text().split(':')[1], default_hash)
        self.assertEqual(state.read_bytes(), b'fresh\n')
        self.assertEqual(self.call('confirm', self.token)['state'], 'confirmed')
        self.assertEqual((self.root / 'etc/shadow').read_text().split(':')[1], '$6$fixture$newhash')
        self.assertEqual(auth.read_bytes(), owner)
        self.assertEqual(state.read_bytes(), b'retired\n')
        (self.root / 'etc/dicttech-ap/device.json').unlink()
        self.call_key_helper('fresh')
        self.assertEqual(auth.read_bytes(), owner)

    def test_initialize_explicit_empty_is_immediate_and_confirm_never_backfills(self):
        self.initialize_with_real_key_helper(b'')
        auth = self.root / 'etc/dropbear/authorized_keys'
        self.call_key_helper('managed')
        self.assertEqual(auth.read_bytes(), b'')
        self.assertEqual((self.root / 'etc/shadow').read_text().split(':')[1],
                         (self.root / 'usr/share/dicttech-ap/bootstrap-password-hash').read_text().strip())
        self.assertEqual(self.call('confirm', self.token)['state'], 'confirmed')
        self.assertEqual(auth.read_bytes(), b'')
        self.assertEqual((self.root / 'etc/shadow').read_text().split(':')[1], '$6$fixture$newhash')

    def test_failed_retirement_confirmation_rolls_back_key_and_lifecycle_together(self):
        self.initialize_with_real_key_helper()
        auth = self.root / 'etc/dropbear/authorized_keys'
        state = self.root / 'etc/dicttech-ap-bootstrap-key/state'
        original_auth, original_state = auth.read_bytes(), state.read_bytes()
        shadow = self.root / 'etc/shadow'
        original_shadow = shadow.read_bytes()
        helper = self.root / 'usr/libexec/dicttech-ap/bootstrap-key'
        real = helper.with_name('bootstrap-key.real'); helper.rename(real)
        helper.write_text('#!/bin/sh\n' + shlex.quote(str(real)) + ' "$@"\n[ "$1" != retire ]\n'); helper.chmod(0o700)
        self.call('confirm', self.token, good=False)
        self.assertFalse((self.state / 'confirmed').exists())
        self.assertNotEqual(shadow.read_bytes(), original_shadow)
        helper.unlink(); real.rename(helper)
        self.call('rollback', self.token)
        self.assertEqual(auth.read_bytes(), original_auth)
        self.assertEqual(state.read_bytes(), original_state)
        self.assertEqual(shadow.read_bytes(), original_shadow)

    def test_unconfirmed_initialize_reboot_restores_original_key_grant(self):
        self.initialize_with_real_key_helper()
        original_auth = self.before['etc/dropbear/authorized_keys']
        self.boot.write_text('22222222-2222-4222-8222-222222222222\n')
        self.call('early')
        self.assertEqual((self.root / 'etc/dropbear/authorized_keys').read_bytes(), original_auth)
        self.assertEqual((self.root / 'etc/dicttech-ap-bootstrap-key/state').read_bytes(), b'fresh\n')
        self.assertFalse((self.root / 'etc/dicttech-ap/device.json').exists())

    def test_retirement_success_without_final_password_never_confirms(self):
        self.initialize_with_real_key_helper()
        shadow = self.root / 'etc/shadow'
        default_shadow = shadow.read_bytes()
        helper = self.root / 'usr/libexec/dicttech-ap/bootstrap-key'
        real = helper.with_name('bootstrap-key.real'); helper.rename(real)
        helper.write_text('#!/bin/sh\n[ "$1" != retire ] || exit 0\nexec ' +
                          shlex.quote(str(real)) + ' "$@"\n'); helper.chmod(0o700)
        self.call('confirm', self.token, good=False)
        self.assertFalse((self.state / 'confirmed').exists())
        self.assertEqual(shadow.read_bytes(), default_shadow)
        helper.unlink(); real.rename(helper)
        self.call('rollback', self.token)
        self.assertEqual(shadow.read_bytes(), self.before['etc/shadow'])

    def test_isolated_initialization_requires_exact_setup_and_pinned_boot(self):
        self.fresh_bootstrap('isolated')
        contract = self.call('check')
        self.assertTrue(contract['identity']['bootstrap_isolated'])
        self.assertFalse(contract['identity']['bootstrap_dhcp'])
        self.assertEqual(contract['identity']['ssh_port'], '12022')
        approved = {'device_id': 'fixture_ap', 'bridge_mac': '02:00:00:00:99:00',
                    'allowed_boards': ['gemtek,xr1710g'], 'initialize': True,
                    'expect_boot_id': contract['boot_id']}
        manifest, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120, approved)
        self.call('stage', data=payload)
        self.call('rollback', manifest['token'])
        self.assertOriginal()

    def test_dhcp_initialization_rejects_missing_flag_wrong_boot_and_tampered_proof(self):
        setup = self.fresh_bootstrap()
        contract = self.call('check')
        self.assertTrue(contract['identity']['bootstrap_dhcp'])
        self.assertFalse(contract['identity']['bootstrap_isolated'])
        self.assertEqual(contract['identity']['bridge_mac'], setup.identity['mac'])
        self.assertEqual(contract['identity']['ssh_port'], '12022')
        approved = {'device_id': 'fixture_ap', 'bridge_mac': '02:00:00:00:99:00',
                    'allowed_boards': ['gemtek,xr1710g'], 'initialize': False,
                    'expect_boot_id': contract['boot_id']}
        for flag, pin in [(False, contract['boot_id']), (True, None),
                          (True, '22222222-2222-4222-8222-222222222222')]:
            approved.update(initialize=flag, expect_boot_id=pin)
            _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120, approved)
            self.call('stage', data=payload, good=False)
        approved.update(initialize=True, expect_boot_id=contract['boot_id'])
        with (self.root / 'etc/config/dropbear').open('a') as stream: stream.write('# tampered setup\n')
        altered_contract = self.call('check')
        self.assertFalse(altered_contract['identity']['bootstrap_dhcp'])
        _, payload = deploy.bundle(self.files, altered_contract, 'fixture_ap', 120, approved)
        self.call('stage', data=payload, good=False)

    def test_dhcp_initialization_rollback_restores_generated_setup_and_identity(self):
        setup = self.fresh_bootstrap()
        identity = setup.identity
        contract = self.call('check')
        approved = {'device_id': 'fixture_ap', 'bridge_mac': '02:00:00:00:99:00',
                    'allowed_boards': ['gemtek,xr1710g'], 'initialize': True,
                    'expect_boot_id': contract['boot_id']}
        manifest, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120, approved)
        self.call('stage', data=payload); self.call('apply', manifest['token'])
        wait(lambda: (self.state / 'reload-complete').exists())
        self.call('rollback', manifest['token'])
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()
        self.assertEqual(setup.identity, identity)
        self.assertEqual(json.loads(setup.call('check-setup').stdout)['mode'], 'dhcp')

    def test_offline_lan4_initialization_works_with_pending_dhcp_and_reports_access(self):
        setup = self.fresh_bootstrap()
        runtime = json.loads(setup.runtime.read_text())
        runtime['network.interface.bootstrap'] = {'up': False, 'pending': True,
            'proto': 'dhcp', 'device': 'br-setup.1'}
        setup.runtime.write_text(json.dumps(runtime))
        contract = self.call('check')
        self.assertTrue(contract['identity']['bootstrap_dhcp'])
        self.assertFalse(contract['identity']['bootstrap_dhcp_ready'])
        access = deploy.bootstrap_access(contract['identity'])
        self.assertEqual(access['offline']['address'], '192.168.250.1')
        self.assertFalse(access['dhcp']['ready'])
        self.assertFalse(access['dhcp']['public_key_only'])
        approved = {'device_id': 'fixture_ap', 'bridge_mac': '02:00:00:00:99:00',
                    'allowed_boards': ['gemtek,xr1710g'], 'initialize': True,
                    'expect_boot_id': contract['boot_id']}
        manifest, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120, approved)
        self.call('stage', data=payload); self.call('rollback', manifest['token'])
        self.assertOriginal()

    def test_setup_ssh_port_uses_named_first_instance_without_main(self):
        self.uci.write_text('#!/bin/sh\ncase "$*" in\n'
                           ' "-q show dropbear") printf "%s\\n" "dropbear.setup=dropbear" "dropbear.bootstrap=dropbear";;\n'
                           ' "-q get dropbear.setup.Port") printf "12022\\n";;\n'
                           'esac\n')
        self.assertEqual(self.call('check')['identity']['ssh_port'], '12022')

    def test_controller_initialization_requires_unregistered_device_proof_and_boot_pin(self):
        import contextlib
        from unittest.mock import patch
        contract = self.call('check')
        resolved = {'contract_sha256': contract['contract_sha256'],
                    'device': {'identity': {'bridge': '02:00:00:00:99:00'}, 'ssh_port':12022},
                    'platform': {'board_names': ['gemtek,xr1710g']}, 'common': {}}
        argv = [str(CONTROLLER), 'plan', '--config', 'unused.json', '--device', 'fixture_ap', '--host', 'ap.example.invalid']
        for mode in ['isolated', 'dhcp']:
            contract['identity'].update(device_id='', bootstrap_isolated=mode == 'isolated', bootstrap_dhcp=mode == 'dhcp')
            for extra, good in [([], False), (['--initialize'], False),
                                (['--initialize', '--expect-boot-id', 'wrong'], False),
                                (['--initialize', '--expect-boot-id', contract['boot_id']], True)]:
                with patch.object(deploy, 'model', return_value=(resolved, self.files)), \
                     patch.object(deploy, 'remote', return_value=contract), \
                     patch.object(deploy.sys, 'argv', argv + extra), contextlib.redirect_stdout(io.StringIO()):
                    if good: deploy.main()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'target bridge MAC differs'): deploy.main()
            contract['identity']['device_id'] = 'fixture_ap'
            with patch.object(deploy, 'model', return_value=(resolved, self.files)), \
                 patch.object(deploy, 'remote', return_value=contract), \
                 patch.object(deploy.sys, 'argv', argv + ['--initialize', '--expect-boot-id', contract['boot_id']]):
                with self.assertRaisesRegex(RuntimeError, 'target bridge MAC differs'): deploy.main()

    def test_controller_uses_explicit_device_ssh_port_without_common_defaults(self):
        import contextlib
        from unittest.mock import patch
        contract=self.call('check');contract['identity']['ssh_port']='2222'
        resolved={'contract_sha256':contract['contract_sha256'], 'common':{},
                  'device':{'identity':{'bridge':contract['identity']['bridge_mac']},'ssh_port':2222,'maintenance_ipv4':'198.51.100.10'},
                  'platform':{'board_names':['gemtek,xr1710g']}}
        token='a'*32
        def remote(args,command,*unused,**kwargs):
            if command=='check':return contract
            return {'token':token,'state':{'stage':'staged','apply':'applied'}[command]}
        argv=[str(CONTROLLER),'apply','--config','unused.json','--device','fixture_ap','--host','ap.example.invalid','--port','2222','--receipt',str(self.base/'receipt')]
        with patch.object(deploy,'model',return_value=(resolved,self.files)),patch.object(deploy,'remote',side_effect=remote),patch.object(deploy.secrets,'token_hex',return_value=token),patch.object(deploy.sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):
            deploy.main()
        self.assertEqual(json.loads((self.base/'receipt').read_text())['state'],'applied')

    def test_controller_refuses_wrong_master_device_before_any_stage(self):
        from unittest.mock import patch
        import contextlib
        contract = self.call('check')
        resolved = {'contract_sha256': contract['contract_sha256'], 'device': {'identity': {'bridge': '02:00:00:00:01:00'}, 'ssh_port':12022},
                    'platform': {'board_names': ['gemtek,xr1710g']}, 'common': {}}
        contract['identity']['device_id'] = 'other_fixture_ap'
        argv = [str(CONTROLLER), 'apply', '--config', 'unused.json', '--device', 'fixture_ap', '--host', 'ap.example.invalid', '--receipt', str(self.base / 'receipt')]
        with patch.object(deploy, 'model', return_value=(resolved, self.files)), patch.object(deploy, 'remote', return_value=contract) as remote, patch.object(deploy.sys, 'argv', argv):
            with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                deploy.main()
        self.assertEqual(remote.call_count, 1)
        self.assertFalse((self.base / 'receipt').exists())

    def test_independent_guard_finishes_rollback_if_controller_cannot_launch_reload(self):
        self.stage(); self.call('apply', self.token)
        wait(lambda: (self.state / 'reload-complete').exists())
        source = self.script.read_text()
        start = source.index('  if test -f "$STATE/applying" || test -f "$STATE/applied"; then\n', source.index(' rollback)'))
        end = source.index('  ;;\n early)', start)
        self.script.write_text(source[:start] + '  : # fixture simulates lost controller before detached reload launch\n' + source[end:])
        self.call('rollback', self.token)
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()

    def test_rollback_stops_owned_hung_reloader_and_refuses_new_stage_until_complete(self):
        self.service_hang.touch()
        self.stage(); self.call('apply', self.token)
        wait(lambda: self.service_started.exists())
        child = int(self.service_started.read_text())
        self.service_restore_barrier.touch()
        self.call('rollback', self.token)
        contract = self.call('check'); _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        self.call('stage', data=payload, good=False)
        self.service_restore_barrier.unlink()
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()
        stat = Path(f'/proc/{child}/stat')
        self.assertTrue(not stat.exists() or stat.read_text().split(') ', 1)[1].split()[0] == 'Z')
        time.sleep(.1)
        self.assertEqual(json.loads(self.service_state.read_text()), json.loads(self.original_services))

    def test_rollback_publishes_runtime_pending_before_terminal_state(self):
        barrier = self.base / 'rollback-publication-barrier'
        published = self.base / 'rollback-published'
        barrier.touch()
        source = self.script.read_text()
        marker = ' record rolled-back "$reason"\n'
        self.assertEqual(source.count(marker), 1)
        self.script.write_text(source.replace(marker, marker +
            ' : > ' + shlex.quote(str(published)) + '\n' +
            ' while test -e ' + shlex.quote(str(barrier)) + '; do sleep .02; done\n'))
        self.stage(); self.call('apply', self.token)
        wait(lambda: (self.state / 'reload-complete').exists())
        process = subprocess.Popen(self.shell + [str(self.script), 'rollback', self.token],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.env, start_new_session=True)
        try:
            wait(published.exists)
            self.assertTrue((self.state / 'services-restore-pending').exists())
            # The old apply completion stamp must not make rollback look done.
            self.assertTrue((self.state / 'reload-complete').exists())
            self.assertEqual(self.call('status')['state'], 'rollback_pending')
            self.assertNotEqual(json.loads(self.service_state.read_text()), json.loads(self.original_services))
        finally:
            barrier.unlink(missing_ok=True)
            stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertIn(json.loads(stdout)['state'], ('rollback_pending', 'rolled-back'))
        wait(lambda: (self.state / 'rolled-back').exists() and
             not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()

    def kill_recorded_reloader(self):
        pid, ticks, token = (self.state / 'reload.ready').read_text().split()
        pid, ticks = int(pid), int(ticks)
        self.assertEqual(token, self.token)
        self.assertTrue(self.owns_process(pid, ticks), 'fixture reloader is not the recorded live process')
        self.signal_fixture_process(pid, ticks, signal.SIGKILL)
        wait(lambda: not self.owns_process(pid, ticks))
        return pid

    def test_procd_watcher_recovers_silently_killed_ssh_reloader_and_can_confirm(self):
        self.service_hang.touch()
        self.stage(); self.call('apply', self.token)
        wait(lambda: self.service_started.exists() and (self.state / 'reload.child').exists())
        child_pid, child_ticks, child_token = (self.state / 'reload.child').read_text().split()
        self.assertEqual(child_token, self.token)
        self.assertTrue(self.owns_process(int(child_pid), int(child_ticks)))
        self.kill_recorded_reloader()
        self.service_hang.unlink()
        wait(lambda: (self.state / 'reload-complete').exists(), timeout=12)
        self.assertEqual((self.state / 'reload-recoveries').read_text().strip(), '1')
        self.assertFalse((self.state / 'reload-error').exists())
        self.assertFalse(self.owns_process(int(child_pid), int(child_ticks)), 'old service timeout survived recovery')
        self.assertEqual(self.service_log.read_text().splitlines().count('apply'), 2)
        self.assertEqual(self.call('confirm', self.token)['state'], 'confirmed')

    def test_recovered_hung_worker_never_blocks_watcher_lease_expiry(self):
        self.service_hang.touch()
        self.stage(); self.call('apply', self.token)
        wait(lambda: self.service_started.exists())
        original_service = self.service_started.read_text()
        self.kill_recorded_reloader()
        wait(lambda: self.service_started.read_text() != original_service and
             (self.state / 'reload-recoveries').exists(), timeout=12)
        self.assertFalse((self.state / 'reload-complete').exists())
        before = time.monotonic()
        self.clock.write_text('221.00 0\n')
        wait(lambda: (self.state / 'rolled-back').exists() and
             (self.state / 'reload-complete').exists() and
             not (self.state / 'services-restore-pending').exists(), timeout=8)
        self.assertLess(time.monotonic() - before, 8)
        self.assertOriginal()

    def test_silent_worker_recovery_budget_is_bounded_and_never_confirms_failure(self):
        self.service_hang.touch()
        self.stage(); self.call('apply', self.token)
        previous_service = None
        for number in range(4):
            wait(lambda: self.service_started.exists() and self.service_started.read_text() != previous_service,
                 timeout=12)
            previous_service = self.service_started.read_text()
            if number:
                self.assertEqual((self.state / 'reload-recoveries').read_text().strip(), str(number))
            self.kill_recorded_reloader()
        wait(lambda: (self.state / 'reload-error').exists())
        self.assertEqual((self.state / 'reload-error').read_text().strip(), 'reload_worker_recovery_exhausted')
        self.assertEqual((self.state / 'reload-recoveries').read_text().strip(), '3')
        self.assertEqual(self.service_log.read_text().splitlines().count('apply'), 4)
        self.call('confirm', self.token, good=False)
        self.clock.write_text('221.00 0\n')
        wait(lambda: (self.state / 'rolled-back').exists() and
             not (self.state / 'services-restore-pending').exists(), timeout=8)
        self.assertOriginal()

    def test_explicit_service_error_is_not_silently_retried(self):
        self.service_fail.touch()
        self.stage(); self.call('apply', self.token)
        wait(lambda: (self.state / 'reload-error').exists())
        time.sleep(2.5)  # Cross the independent watcher's next polling interval.
        self.assertEqual((self.state / 'reload-error').read_text().strip(), 'service_policy_failed')
        self.assertFalse((self.state / 'reload-recoveries').exists())
        self.assertEqual(self.service_log.read_text().splitlines().count('apply'), 1)
        self.clock.write_text('221.00 0\n')
        wait(lambda: (self.state / 'rolled-back').exists() and
             not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()

    def test_watcher_never_preempts_apply_response_before_worker_registration(self):
        text = self.script.read_text()
        start = text.index(' apply)\n')
        end = text.index(' confirm)\n', start)
        apply = text[start:end]
        self.assertIn('  status\n  (trap', apply)
        apply = apply.replace('  status\n  (trap', '  sleep 4\n  status\n  (trap')
        self.script.write_text(text[:start] + apply + text[end:])
        self.stage()
        process = subprocess.Popen(self.shell + [str(self.script), 'apply', self.token],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.env, start_new_session=True)
        wait(lambda: (self.state / 'applied').exists())
        time.sleep(2.5)  # Cross the watcher tick while the SSH response is pending.
        self.assertIsNone(process.poll())
        self.assertFalse((self.state / 'reload.ready').exists())
        self.assertFalse((self.state / 'reload-recoveries').exists())
        self.assertNotIn('apply', self.service_log.read_text().splitlines())
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(json.loads(stdout)['state'], 'applied')
        wait(lambda: (self.state / 'reload-complete').exists())
        self.assertEqual(self.service_log.read_text().splitlines().count('apply'), 1)
        self.assertFalse((self.state / 'reload-error').exists())
        self.assertEqual(self.call('confirm', self.token)['state'], 'confirmed')

    def test_check_to_stage_drift_rejected(self):
        contract = self.call('check'); _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        self.put('etc/config/network', b'external concurrent edit\n')
        self.call('stage', data=payload, good=False)
        self.assertEqual((self.root / 'etc/config/network').read_bytes(), b'external concurrent edit\n')
        self.assertFalse(self.state.exists())

    def test_unapplied_expiry_preserves_concurrent_external_edits(self):
        self.stage(); self.put('etc/config/network', b'external concurrent edit\n')
        self.call('apply', self.token, good=False)
        self.clock.write_text('221.00 0\n')
        wait(lambda: (self.state / 'rolled-back').exists())
        self.assertEqual((self.root / 'etc/config/network').read_bytes(), b'external concurrent edit\n')
        self.assertNotIn('restore ', self.service_log.read_text())

    def test_confirm_checks_full_desired_files_and_private_modes(self):
        self.stage(); self.call('apply', self.token)
        wait(lambda: (self.state / 'reload-complete').exists())
        self.put('etc/config/network', self.files['etc/config/network'], 0o644)
        self.call('confirm', self.token, good=False)
        self.put('etc/config/network', self.files['etc/config/network'], 0o600)
        self.put('etc/config/wireless', b'external altered file\n')
        self.call('confirm', self.token, good=False)

    def test_resolver_symlink_not_followed_and_rollback_restores_original(self):
        resolver = self.root / 'etc/resolv.conf'
        resolver.symlink_to('/tmp/resolv.conf')
        self.stage(); self.call('apply', self.token)
        self.assertEqual(os.readlink(resolver), '/tmp/resolv.conf.d/resolv.conf.auto')
        self.call('rollback', self.token)
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        self.assertEqual(os.readlink(resolver), '/tmp/resolv.conf')

    def test_resolver_regular_file_exactly_restored(self):
        self.put('etc/resolv.conf', b'nameserver 192.0.2.1\n', 0o644)
        self.stage(); self.call('apply', self.token)
        self.assertTrue((self.root / 'etc/resolv.conf').is_symlink())
        self.call('rollback', self.token)
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        self.assertEqual((self.root / 'etc/resolv.conf').read_bytes(), b'nameserver 192.0.2.1\n')
        self.assertEqual((self.root / 'etc/resolv.conf').stat().st_mode & 0o777, 0o644)

    def test_changed_baseline_prevents_apply(self):
        self.stage(); self.put('etc/config/network', b'external update\n')
        self.call('apply', self.token, good=False)
        self.assertFalse((self.root / 'etc/config/wireless').exists())

    def test_token_and_symlink_rejected(self):
        self.stage(); self.call('apply', '0' * 32, good=False)
        self.call('rollback', self.token)
        target = self.root / 'etc/config/network'; target.unlink(); target.symlink_to(self.base / 'unowned')
        self.call('check', good=False)
        self.assertFalse((self.base / 'unowned').exists())

    def test_backup_permission_tampering_is_rejected_before_any_restore(self):
        self.stage();self.call('apply',self.token);wait(lambda:(self.state/'reload-complete').exists())
        before={str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file() and str(path.relative_to(self.root)).startswith('etc/')}
        (self.state/'backup/shadow').chmod(0o777)
        self.call('rollback',self.token,good=False)
        self.assertFalse((self.state/'rolled-back').exists())
        for name,value in before.items():self.assertEqual((self.root/name).read_bytes(),value)
        self.assertEqual((self.root/'etc/shadow').stat().st_mode & 0o777,0o600)

    def test_resolver_backup_permission_tampering_is_rejected_before_any_restore(self):
        self.put('etc/resolv.conf',b'nameserver 192.0.2.53\n',0o644)
        self.stage();self.call('apply',self.token);wait(lambda:(self.state/'reload-complete').exists())
        current=(self.root/'etc/config/network').read_bytes()
        (self.state/'resolver.data').chmod(0o777)
        self.call('rollback',self.token,good=False)
        self.assertEqual((self.root/'etc/config/network').read_bytes(),current)
        self.assertTrue((self.root/'etc/resolv.conf').is_symlink())
        self.assertFalse((self.state/'rolled-back').exists())

    def test_executable_backup_mode_is_bound_to_the_original_metadata(self):
        self.put('usr/libexec/openwrt-managed/plugin',b'old executable\n',0o755)
        self.files['usr/libexec/openwrt-managed/plugin']=b'new executable\n'
        self.stage();self.call('apply',self.token);wait(lambda:(self.state/'reload-complete').exists())
        row=next(line.split() for line in (self.state/'before').read_text().splitlines() if line.startswith('usr/libexec/openwrt-managed/plugin '))
        (self.state/'backup'/row[3]).chmod(0o644)
        current=(self.root/'etc/config/network').read_bytes()
        self.call('rollback',self.token,good=False)
        self.assertEqual((self.root/'etc/config/network').read_bytes(),current)
        self.assertFalse((self.state/'rolled-back').exists())

    def test_explicit_binary_resource_modes_apply_and_restore(self):
        self.files['usr/libexec/openwrt-managed/plugin'] = b'\0binary\xff\n'
        ledger={'schema_version':3,'profile':'openwrt-config-v3','files':[{'path':path,'mode':'755' if path.startswith('usr/') else '600','sha256':hashlib.sha256(value).hexdigest()} for path,value in self.files.items()]}
        self.files['etc/dicttech-ap/managed-files.json']=json.dumps(ledger).encode()+b'\n'
        manifest=self.stage()
        self.assertEqual(next(item['mode'] for item in manifest['files'] if item['path'].startswith('usr/')),'755')
        self.call('apply',self.token);wait(lambda:(self.state/'reload-complete').exists())
        target=self.root/'usr/libexec/openwrt-managed/plugin'
        self.assertEqual(target.read_bytes(),b'\0binary\xff\n');self.assertEqual(target.stat().st_mode & 0o777,0o755)
        self.call('rollback',self.token);wait(lambda:not(self.state/'services-restore-pending').exists())
        self.assertFalse(target.exists());self.assertOriginal()

    def test_null_resolver_preserves_an_existing_relative_symlink(self):
        target=self.root/'etc/resolv.conf';target.symlink_to('../run/example-resolver.conf')
        marker=dict(self.marker,resolver=None)
        self.files['etc/dicttech-ap/device.json']=json.dumps(marker).encode()+b'\n'
        self.stage();self.call('apply',self.token);wait(lambda:(self.state/'reload-complete').exists())
        self.assertEqual(os.readlink(target),'../run/example-resolver.conf')
        self.assertEqual(self.call('confirm',self.token)['state'],'confirmed')
        self.assertEqual(os.readlink(target),'../run/example-resolver.conf')

    def test_explicit_nondefault_resolver_target_is_applied(self):
        marker=dict(self.marker,resolver={'target':'/run/custom/resolv.conf'})
        self.files['etc/dicttech-ap/device.json']=json.dumps(marker).encode()+b'\n'
        self.stage();self.call('apply',self.token);wait(lambda:(self.state/'reload-complete').exists())
        self.assertEqual(os.readlink(self.root/'etc/resolv.conf'),'/run/custom/resolv.conf')
        self.assertEqual(self.call('confirm',self.token)['state'],'confirmed')

    def test_export_is_private_tar_and_never_contains_ssh_host_keys(self):
        payload = deploy.tar_bytes(self.files)
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            self.assertEqual(set(tar.getnames()), set(self.files))
            self.assertFalse(any(name.startswith('etc/uci-defaults/') for name in tar.getnames()))
            self.assertTrue(all(m.mode == 0o600 for m in tar))
            self.assertFalse(any('host_key' in m.name for m in tar))


if __name__ == '__main__':
    unittest.main(verbosity=2)
