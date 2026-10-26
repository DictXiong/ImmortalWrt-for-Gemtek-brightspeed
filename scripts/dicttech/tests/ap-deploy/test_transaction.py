#!/usr/bin/env python3
"""Inert transaction tests: every path, command, clock and service is private."""
from __future__ import annotations
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
        self.service_started = self.base / 'service-started'
        self.services = self.base / 'services'
        self.services.write_text('#!' + shutil.which('python3') + '\nimport sys,pathlib,json,time,os\n'
            + 'state=pathlib.Path(' + repr(str(self.service_state)) + ')\n'
            + 'with open(' + repr(str(self.service_log)) + ',"a") as log:log.write(" ".join(sys.argv[1:]) + "\\n")\n'
            + 'if sys.argv[1]=="snapshot":pathlib.Path(sys.argv[2]).write_bytes(state.read_bytes())\n'
            + 'elif sys.argv[1]=="apply":\n'
            + ' state.write_text(json.dumps({"enabled":["candidate"],"running":["candidate"]}))\n'
            + ' if pathlib.Path(' + repr(str(self.service_hang)) + ').exists():\n'
            + '  pathlib.Path(' + repr(str(self.service_started)) + ').write_text(str(os.getpid()))\n'
            + '  time.sleep(60)\n'
            + 'elif sys.argv[1]=="restore":\n'
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
        self.put('usr/share/dicttech-ap/defaults.json', b'{"profile":"dicttech-ap-v1","schema_version":1,"platform":{"board_names":["gemtek,xr1710g"]}}\n')
        self.put('etc/config/network', b'original network\n', 0o644)
        self.put('etc/shadow', b'root:oldhash:20000:0:99999:7:::\nservice:locked:20000:0:99999:7:::\n')
        self.put('etc/dropbear/dropbear_ed25519_host_key', b'fixture-host-key\n')
        self.before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.files = {
            'etc/config/network': b'candidate network\n',
            'etc/config/wireless': b'candidate wireless with confidential fixture key\n',
            'etc/dicttech-ap/root-password-hash': b'$6$fixture$newhash\n',
            'etc/dicttech-ap/device.json': b'{"profile":"dicttech-ap-v1","schema_version":1,"device_id":"fixture_ap"}\n',
        }

    def put(self, path, value, mode=0o600):
        p = self.root / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(value); p.chmod(mode)

    def call(self, command, token=None, data=None, good=True):
        result = subprocess.run(self.shell + [str(self.script), command] + ([token] if token else []), input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode == 0, good, (result.returncode, result.stderr.decode()))
        return json.loads(result.stdout) if good and result.stdout else result

    def stage(self, files=None, mutate=None):
        contract = self.call('check')
        manifest, payload = deploy.bundle(files or self.files, contract, 'fixture_ap', 120)
        if mutate:
            mutate(manifest)
            payload = deploy.tar_bytes(files or self.files, manifest)
        result = self.call('stage', data=payload)
        self.assertEqual(result['token'], manifest['token'])
        self.token = manifest['token']
        return manifest

    def tearDown(self):
        ready = self.state / 'watch.ready'
        if ready.exists():
            try:
                os.kill(int(ready.read_text().split()[0]), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.tmp.cleanup()

    def assertOriginal(self, services=True):
        for name, value in self.before.items():
            self.assertEqual((self.root / name).read_bytes(), value, name)
        self.assertFalse((self.root / 'etc/config/wireless').exists())
        self.assertFalse((self.root / 'etc/dicttech-ap/root-password-hash').exists())
        self.assertFalse((self.root / 'etc/resolv.conf').is_symlink())
        self.assertFalse((self.root / 'etc/resolv.conf').exists())
        self.assertEqual((self.root / 'etc/config/network').stat().st_mode & 0o777, 0o644)
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
        unsafe = {'etc/dropbear/dropbear_ed25519_host_key': b'never install\n'}
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
        manifest['boot_id'] = contract['boot_id']; manifest['defaults_sha256'] = '0' * 64
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

    def test_isolated_initialization_requires_exact_setup_and_board(self):
        self.put('tmp/dicttech-ap-bootstrap-isolated', b'')
        self.put('usr/lib/dicttech-ap/setup/network', b'original network\n')
        (self.root / 'sys/class/net/br-lan/address').unlink()
        self.before.pop('sys/class/net/br-lan/address')
        self.ubus.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"up":true,"proto":"static","l3_device":"lan4","ipv4-address":[{"address":"192.168.250.1","mask":24}]}\'\n')
        contract = self.call('check'); self.assertTrue(contract['identity']['bootstrap_isolated'])
        approved = {'device_id': 'fixture_ap', 'bridge_mac': '02:00:00:00:99:00', 'allowed_boards': ['gemtek,xr1710g'], 'initialize': True}
        manifest, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120, approved)
        self.call('stage', data=payload)
        self.call('rollback', manifest['token'])
        self.assertOriginal()

    def test_controller_refuses_wrong_master_device_before_any_stage(self):
        from unittest.mock import patch
        import contextlib
        contract = self.call('check')
        resolved = {'defaults_sha256': contract['defaults_sha256'], 'device': {'identity': {'bridge': '02:00:00:00:01:00'}},
                    'platform': {'board_names': ['gemtek,xr1710g']}, 'common': {'ssh_port': 12022}}
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
        self.call('rollback', self.token)
        contract = self.call('check'); _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        self.call('stage', data=payload, good=False)
        wait(lambda: not (self.state / 'services-restore-pending').exists())
        self.assertOriginal()
        stat = Path(f'/proc/{child}/stat')
        self.assertTrue(not stat.exists() or stat.read_text().split(') ', 1)[1].split()[0] == 'Z')
        time.sleep(.1)
        self.assertEqual(json.loads(self.service_state.read_text()), json.loads(self.original_services))

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
        contract = self.call('check'); _, payload = deploy.bundle(self.files, contract, 'fixture_ap', 120)
        self.call('stage', data=payload, good=False)
        self.assertFalse((self.base / 'unowned').exists())

    def test_export_is_private_tar_and_never_contains_ssh_host_keys(self):
        payload = deploy.tar_bytes(self.files, provision=True)
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            self.assertIn('etc/uci-defaults/99-dicttech-ap-provision', tar.getnames())
            self.assertTrue(all(m.mode == (0o700 if m.name.startswith('etc/uci-defaults/') else 0o600) for m in tar))
            self.assertFalse(any('host_key' in m.name for m in tar))


if __name__ == '__main__':
    unittest.main(verbosity=2)
