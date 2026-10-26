#!/usr/bin/env python3
"""Local synthetic APK/host-tool fixtures; no real keys, APs or network."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'check-ap-packages.py'
spec = importlib.util.spec_from_file_location('check_ap_packages', SOURCE)
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)


class Packages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dicttech-apks-fixture-')
        self.base = Path(self.temp.name); self.root = self.base / 'rootfs'
        self.keys = self.root / 'etc/apk/keys'; self.keys.mkdir(parents=True)
        (self.keys / 'trusted-public.pem').write_text('public synthetic key only\n')
        self.packages = self.base / 'packages'; self.packages.mkdir()
        self.kernel = '6.18.55~' + '1' * 32 + '-r1'
        self.rows = {'kernel': self.kernel, 'kmod-fixture': '6.18.55-r1', 'ap-profile': '1.0.0-r2'}
        self.manifest = self.base / 'image.manifest'
        self.manifest.write_text(''.join(name + ' - ' + version + '\n' for name, version in self.rows.items()))
        self.log = self.base / 'tool-log'
        self.apk = self.base / 'apk'
        self.apk.write_text('#!' + shutil.which('python3') + '\nimport sys,json,pathlib\n'
            'a=sys.argv[1:]\n'
            'assert a[:3]==["--no-network","--no-cache","--no-logfile"]\n'
            'assert a[3]=="--keys-dir" and a[4]==' + repr(str(self.keys)) + '\n'
            'command=a[5];package=pathlib.Path(a[-1])\n'
            'with open(' + repr(str(self.log)) + ',"a") as out:out.write(command+" "+package.name+"\\n")\n'
            'value=json.loads(package.read_text())\n'
            'if command=="verify":sys.exit(0 if value["trusted"] and value["payload_complete"] else 1)\n'
            'assert command=="adbdump" and a[6:8]==["--format","json"]\n'
            'print(json.dumps({"info":value["info"]}))\n')
        self.apk.chmod(0o700)
        for name, version in self.rows.items():
            self.write(name, version, ['kernel=' + self.kernel] if name.startswith('kmod-') else [])
        self.destination = self.base / 'release'

    def write(self, name, version, depends, **overrides):
        value = {'trusted': True, 'payload_complete': True,
                 'info': {'name': name, 'version': version, 'arch': 'aarch64_cortex-a53', 'depends': depends}}
        value.update(overrides)
        path = self.packages / (name + '-' + version + '.apk'); path.write_text(json.dumps(value)); return path

    def run_gate(self, roots=None, output=True):
        return check.check_packages(self.manifest, roots or [self.packages], self.root, self.apk,
                                    self.destination if output else None)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_manifest_set_verified_and_copied_without_cached_old_kmods(self):
        (self.packages / 'kmod-unselected-6.18.44-r1.apk').write_text('unselected old ABI; never read')
        report = self.run_gate()
        self.assertTrue(report['passed']); self.assertEqual(report['package_count'], 3)
        self.assertEqual({path.name for path in self.destination.iterdir()}, {name + '-' + version + '.apk' for name, version in self.rows.items()})
        self.assertNotIn('unselected', self.log.read_text())
        self.assertTrue(all(row['signature'] == 'verified' for row in report['packages']))

    def test_untrusted_signature_or_incomplete_payload_prevents_any_release_copy(self):
        for field in ('trusted', 'payload_complete'):
            path = self.write('ap-profile', self.rows['ap-profile'], [], **{field: False})
            with self.assertRaisesRegex(check.PackageError, 'APK verify failed'):
                self.run_gate()
            self.assertFalse(self.destination.exists())
            self.write('ap-profile', self.rows['ap-profile'], [])

    def test_exact_kernel_abi_required_even_if_kernel_release_is_equal(self):
        self.write('kmod-fixture', self.rows['kmod-fixture'], ['kernel=6.18.55~' + '2' * 32 + '-r1'])
        with self.assertRaisesRegex(check.PackageError, 'kernel version/ABI dependency differs'):
            self.run_gate()
        self.assertFalse(self.destination.exists())

    def test_ambiguous_same_name_version_bytes_rejected_before_tool_execution(self):
        other = self.base / 'other'; other.mkdir()
        source = self.packages / ('ap-profile-' + self.rows['ap-profile'] + '.apk')
        (other / source.name).write_bytes(source.read_bytes() + b' ')
        with self.assertRaisesRegex(check.PackageError, 'conflicting bytes'):
            self.run_gate([self.packages, other])
        self.assertFalse(self.log.exists()); self.assertFalse(self.destination.exists())

    def test_identical_copies_are_accepted_but_missing_exact_version_is_not(self):
        other = self.base / 'other'; shutil.copytree(self.packages, other)
        report = self.run_gate([self.packages, other], output=False)
        self.assertTrue(all(len(row['identical_sources']) == 2 for row in report['packages']))
        for path in self.packages.glob('ap-profile-*.apk'):
            path.unlink()
        self.write('ap-profile', '1.0.0-r1', [])
        with self.assertRaisesRegex(check.PackageError, 'APKs missing'):
            self.run_gate()

    def test_metadata_name_version_and_architecture_are_checked(self):
        path = self.packages / ('ap-profile-' + self.rows['ap-profile'] + '.apk')
        original = json.loads(path.read_text())
        for key, value in [('name', 'wrong-package'), ('version', 'other-version'), ('arch', 'x86_64')]:
            altered = json.loads(json.dumps(original)); altered['info'][key] = value; path.write_text(json.dumps(altered))
            with self.assertRaises(check.PackageError):
                self.run_gate()
        self.assertFalse(self.destination.exists())

    def test_existing_output_directory_and_symlinked_package_are_refused(self):
        self.destination.mkdir()
        with self.assertRaisesRegex(check.PackageError, 'must be new'):
            self.run_gate()
        self.destination.rmdir()
        original = next(self.packages.glob('ap-profile-*.apk')); backup = self.base / 'outside.apk'
        original.rename(backup); original.symlink_to(backup)
        with self.assertRaisesRegex(check.PackageError, 'not a regular file'):
            self.run_gate()

    def test_bare_kernel_release_without_manifest_abi_is_rejected(self):
        self.manifest.write_text('kernel - 6.18.55-r1\n')
        with self.assertRaisesRegex(check.PackageError, 'lacks an explicit ABI hash'):
            self.run_gate()
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
