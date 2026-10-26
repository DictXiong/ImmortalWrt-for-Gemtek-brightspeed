#!/usr/bin/env python3
"""No-device tests for source/installed byte identity and virgin-image policy."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'check-ap-rootfs.py'
spec = importlib.util.spec_from_file_location('check_ap_rootfs', SOURCE)
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)


class RootfsChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dicttech-rootfs-fixture-')
        self.root = Path(self.temp.name) / 'rootfs'; self.root.mkdir()
        package = check.REPO / 'package/dicttech/ap-profile/files'
        shutil.copytree(package, self.root, dirs_exist_ok=True)
        for relative, source in check.roaming_map(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for relative, source in check.GENERATOR_FILES.items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(check.REPO / source, target)
        (self.root / 'etc/dicttech-ap').mkdir(mode=0o700)
        shadow = self.root / 'etc/shadow'; shadow.write_text('root:::0:99999:7:::\nservice:*:0:0:99999:7:::\n'); shadow.chmod(0o600)
        (self.root / 'www').mkdir(); (self.root / 'www/xr-roaming').symlink_to('/tmp/xr-roaming-public')
        (self.root / 'etc/resolv.conf').symlink_to('/tmp/resolv.conf.d/resolv.conf.auto')
        for relative in check.RPC_REQUIRED_FILES:
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('public synthetic RPC fixture\n'); target.chmod(0o644)

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_public_package_files_and_safe_defaults_pass(self):
        report = check.check_rootfs(self.root)
        self.assertTrue(report['passed'])
        self.assertGreaterEqual(len(report['files_checked']), 20)
        self.assertFalse(report['clean_image']['preset_root_password'])

    def test_stale_runtime_and_changed_default_config_fail(self):
        for relative in ('usr/libexec/dicttech-ap/transaction', 'usr/lib/xr-roaming/helper.sh', 'etc/config/xr_roaming'):
            path = self.root / relative; before = path.read_bytes(); path.write_bytes(before + b'\n# stale fixture\n')
            with self.assertRaisesRegex(check.RootfsError, 'differ from authoritative source'):
                check.check_rootfs(self.root)
            path.write_bytes(before)

    def test_source_conditional_must_remain_in_authoritative_generator(self):
        with tempfile.TemporaryDirectory(prefix='dicttech-source-fixture-') as temp:
            source = Path(temp)
            shutil.copytree(check.REPO / 'package/dicttech', source / 'package/dicttech')
            for relative, original in check.GENERATOR_FILES.items():
                dest = source / original; dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(check.REPO / original, dest)
            relative = 'lib/wifi/mac80211.uc'; source_path = source / check.GENERATOR_FILES[relative]
            broken = source_path.read_text().replace('readfile("/usr/share/dicttech-ap/defaults.json") ? "1" : "0"', '"0"')
            source_path.write_text(broken); (self.root / relative).write_text(broken)
            with self.assertRaisesRegex(check.RootfsError, 'no longer disables'):
                check.check_rootfs(self.root, source)

    def test_deployment_material_or_private_host_key_rejected(self):
        for relative in ('etc/dicttech-ap/device.json', 'etc/dicttech-ap/root-password-hash',
                         'etc/dicttech-ap/managed-files.json', 'etc/dropbear/dropbear_ed25519_host_key', 'etc/uhttpd.key'):
            path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('public synthetic invalid fixture')
            with self.assertRaises(check.RootfsError):
                check.check_rootfs(self.root)
            path.unlink()

    def test_preset_root_password_and_authorized_key_rejected(self):
        shadow = self.root / 'etc/shadow'; original = shadow.read_text()
        shadow.write_text('root:$6$fixture$not-a-real-hash:0:0:99999:7:::\n')
        with self.assertRaisesRegex(check.RootfsError, 'preset root password'):
            check.check_rootfs(self.root)
        shadow.write_text(original)
        keys = self.root / 'etc/dropbear/authorized_keys'; keys.parent.mkdir(parents=True, exist_ok=True); keys.write_text('synthetic authorized fixture\n')
        with self.assertRaisesRegex(check.RootfsError, 'authorized SSH keys'):
            check.check_rootfs(self.root)

    def test_package_manifest_uses_authoritative_shared_policy(self):
        spec = importlib.util.spec_from_file_location('test_shared_policy', check.REPO / 'scripts/dicttech/check-build.py')
        policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
        manifest = Path(self.temp.name) / 'packages.manifest'
        manifest.write_text(''.join(name + ' - fixture-version\n' for name in sorted(policy.REQUIRED)))
        self.assertTrue(check.check_rootfs(self.root, manifest=manifest)['passed'])
        manifest.write_text(manifest.read_text() + sorted(policy.FORBIDDEN)[0] + ' - fixture-version\n')
        with self.assertRaisesRegex(check.RootfsError, 'actual package policy mismatch'):
            check.check_rootfs(self.root, manifest=manifest)

    def test_symlinked_runtime_parent_is_rejected_without_following_it(self):
        lib = self.root / 'usr/libexec'; shutil.rmtree(lib); lib.symlink_to('/unowned-fixture')
        with self.assertRaisesRegex(check.RootfsError, 'symlinked parent'):
            check.check_rootfs(self.root)

    def test_world_writable_rpc_script_and_group_writable_import_or_parent_fail(self):
        for relative, mode in [('usr/share/rpcd/ucode/luci', 0o666),
                               ('usr/share/ucode/luci/sys.uc', 0o664),
                               ('usr/libexec/rpcd/luci.fan', 0o777),
                               ('usr/share/rpcd', 0o775)]:
            path = self.root / relative; before = path.stat().st_mode & 0o777
            path.chmod(mode)
            with self.assertRaisesRegex(check.RootfsError, 'group/other writable'):
                check.check_rootfs(self.root)
            path.chmod(before)

    def test_missing_empty_or_symlinked_rpc_plugin_fails(self):
        path = self.root / 'usr/share/rpcd/ucode/luci'; original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(check.RootfsError, 'required LuCI RPC file'):
            check.check_rootfs(self.root)
        path.write_bytes(b'')
        with self.assertRaisesRegex(check.RootfsError, 'required LuCI RPC file'):
            check.check_rootfs(self.root)
        path.unlink(); path.symlink_to('/unowned-fixture')
        with self.assertRaisesRegex(check.RootfsError, 'not a regular file/directory'):
            check.check_rootfs(self.root)
        path.unlink(); path.write_bytes(original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
