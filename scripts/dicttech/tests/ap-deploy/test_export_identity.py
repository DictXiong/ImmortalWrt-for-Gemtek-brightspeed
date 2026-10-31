#!/usr/bin/env python3
"""Same-device upgrade export tests. SSH and identities are inert fixtures."""
from pathlib import Path
import contextlib
import gzip
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[4]
CONTROLLER = REPO / 'scripts/dicttech/ap-deploy.py'
spec = importlib.util.spec_from_file_location('ap_deploy_export', CONTROLLER)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class IdentityExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dicttech-export-')
        self.base = Path(self.tmp.name)
        self.dest = self.base / 'configuration.tar.gz'
        self.resolved = {'platform': {'board_names': ['gemtek,xr1710g']},
                         'device': {'identity': {'bridge': '02:00:00:00:01:00'}}}
        self.files = {'etc/config/network': b'fixture network\n'}
        self.identity = {'etc/dropbear/dropbear_ed25519_host_key': b'private fixture key\n',
                         'etc/uhttpd.key': b'private fixture HTTPS key\n',
                         'etc/uhttpd.crt': b'fixture certificate\n',
                         'etc/dropbear/authorized_keys': b'fixture public key\n'}
        self.probe = b'gemtek,xr1710g\n11111111-1111-4111-8111-111111111111\n02:00:00:00:01:00\n\n'
        self.argv = [str(CONTROLLER), 'export', '--config', 'unused.json', '--device', 'fixture_ap',
                     '--output', str(self.dest), '--host', 'ap.example.invalid']

    def tearDown(self):
        self.tmp.cleanup()

    def run_export(self, preserve=False, files=None, responses=None):
        argv = self.argv + (['--preserve-host-identity'] if preserve else [])
        output = io.StringIO()
        with patch.object(deploy, 'model', return_value=(self.resolved, files or self.files)), \
                patch.object(deploy, 'legacy_read', side_effect=responses) as read, \
                patch.object(deploy.sys, 'argv', argv), contextlib.redirect_stdout(output):
            deploy.main()
        with tarfile.open(self.dest, 'r:gz') as tar:
            result = {m.name: tar.extractfile(m).read() for m in tar}
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o600)
        return result, read, json.loads(output.getvalue())

    def test_default_export_never_captures_or_clones_host_identity(self):
        archive, read, report = self.run_export()
        read.assert_not_called()
        self.assertFalse(any(name.startswith('etc/uci-defaults/') for name in archive))
        self.assertFalse(any(name in archive for name in deploy.HOST_IDENTITY_FILES))
        self.assertEqual(report['preserved_identity_paths'], [])
        self.assertEqual(archive['etc/dicttech-ap-bootstrap-key/state'], b'retired\n')

    def test_initialization_key_identity_is_never_preserved_as_permanent_auth(self):
        key = (REPO / 'package/dicttech/ap-profile/files/usr/share/dicttech-ap/bootstrap-authorized-key.pub').read_bytes()
        owner = (REPO / 'package/dicttech/cli-profile/files/usr/share/dicttech-cli/authorized_keys').read_bytes()
        identity = b' '.join(key.split()[:2])
        unrelated = next(row for row in owner.splitlines() if row.startswith(b'ssh-ed25519 '))
        kept = unrelated + b' comment mentions ' + identity + b' "arbitrary comment\n'
        captured = {**self.identity, 'etc/dropbear/authorized_keys': kept + key + b'restrict ' + identity + b' renamed\n'}
        archive, _, _ = self.run_export(True, responses=[self.probe, deploy.tar_bytes(captured)])
        self.assertEqual(archive['etc/dropbear/authorized_keys'], kept)
        self.assertEqual(archive['etc/dicttech-ap-bootstrap-key/state'], b'retired\n')

    def test_initialization_only_key_capture_becomes_empty_without_backfill(self):
        key = (REPO / 'package/dicttech/ap-profile/files/usr/share/dicttech-ap/bootstrap-authorized-key.pub').read_bytes()
        captured = {**self.identity, 'etc/dropbear/authorized_keys': key}
        archive, _, _ = self.run_export(True, responses=[self.probe, deploy.tar_bytes(captured)])
        self.assertEqual(archive['etc/dropbear/authorized_keys'], b'')

    def test_existing_device_identity_preserved_in_private_archive(self):
        archive, read, report = self.run_export(True, responses=[self.probe, deploy.tar_bytes(self.identity)])
        for name, value in self.identity.items():
            self.assertEqual(archive[name], value)
        self.assertEqual(read.call_count, 2)
        self.assertFalse(any(name.startswith('etc/uci-defaults/') for name in archive))
        capture = read.call_args.args[1]
        self.assertIn('set -o pipefail\n', capture)
        self.assertEqual(capture.count('cat /proc/sys/kernel/random/boot_id'), 2)
        self.assertEqual(capture.count('$(observed_bridge_mac)'), 2)
        self.assertEqual(set(report['preserved_identity_paths']), set(self.identity))
        self.assertNotIn('private fixture', json.dumps(report))

    def test_wrong_device_rejected_before_capture_or_archive_creation(self):
        wrong = self.probe.replace(b'02:00:00:00:01:00', b'02:00:00:00:02:00')
        with patch.object(deploy, 'model', return_value=(self.resolved, self.files)), \
                patch.object(deploy, 'legacy_read', return_value=wrong) as read, \
                patch.object(deploy.sys, 'argv', self.argv + ['--preserve-host-identity']):
            with self.assertRaisesRegex(RuntimeError, 'source differs'):
                deploy.main()
        self.assertEqual(read.call_count, 1)
        self.assertFalse(self.dest.exists())

    def test_explicit_empty_authorized_keys_wins(self):
        files = {**self.files, 'etc/dropbear/authorized_keys': b''}
        captured = {key: value for key, value in self.identity.items() if key != 'etc/dropbear/authorized_keys'}
        archive, read, _ = self.run_export(True, files, [self.probe, deploy.tar_bytes(captured)])
        self.assertEqual(archive['etc/dropbear/authorized_keys'], b'')
        self.assertNotIn('etc/dropbear/authorized_keys', read.call_args.args[1])

    def test_links_extra_members_and_oversized_files_rejected(self):
        args = type('Args', (), {'device': 'fixture_ap'})()
        for name, kind, size in [('etc/shadow', tarfile.REGTYPE, 1),
                                 ('etc/uhttpd.key', tarfile.SYMTYPE, 0),
                                 ('etc/uhttpd.key', tarfile.LNKTYPE, 0),
                                 ('etc/uhttpd.key', tarfile.REGTYPE, 131073)]:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w') as tar:
                item = tarfile.TarInfo(name); item.type = kind; item.size = size
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    item.linkname = '/unowned-fixture'
                tar.addfile(item, io.BytesIO(b'x' * size))
            with patch.object(deploy, 'legacy_read', side_effect=[self.probe, buffer.getvalue()]):
                with self.assertRaisesRegex(RuntimeError, 'invalid same-device'):
                    deploy.preserve_host_identity(args, self.resolved, self.files)

    def test_legacy_transport_sends_only_fixed_script_on_stdin(self):
        args = type('Args', (), {'host': 'ap.example.invalid', 'port': 12022, 'control_socket': '/private/master', 'jump_host': None})()
        result = subprocess.CompletedProcess([], 0, self.probe, b'')
        with patch.object(deploy.subprocess, 'run', return_value=result) as run:
            self.assertEqual(deploy.legacy_read(args, deploy.LEGACY_PROBE), self.probe)
        self.assertEqual(run.call_args.args[0][-1], 'sh -s')
        self.assertEqual(run.call_args.kwargs['input'], deploy.LEGACY_PROBE.encode())
        self.assertNotIn('private fixture', ' '.join(run.call_args.args[0]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
