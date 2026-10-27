#!/usr/bin/env python3
"""No-device tests for source/installed byte identity and virgin-image policy."""
import importlib.util
import json
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
        (self.root / 'etc/dicttech-ap-bootstrap-key').mkdir(mode=0o700)
        shadow = self.root / 'etc/shadow'; shadow.write_text('root:::0:99999:7:::\nservice:*:0:0:99999:7:::\n'); shadow.chmod(0o600)
        (self.root / 'www').mkdir(); (self.root / 'www/xr-roaming').symlink_to('/tmp/xr-roaming-public')
        (self.root / 'etc/resolv.conf').symlink_to('/tmp/resolv.conf.d/resolv.conf.auto')
        for relative in check.RPC_REQUIRED_FILES:
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('public synthetic RPC fixture\n'); target.chmod(0o644)
        for relative, source in check.cli_map(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o600 if relative.startswith('root/') else 0o755 if relative.startswith('usr/libexec/') else 0o644)
        for relative, content in check.history_sources(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content); target.chmod(0o755 if relative in ('usr/sbin/wifihistory', 'etc/init.d/wifihistory') else 0o644)
        keys = self.root / 'etc/dropbear/authorized_keys'; keys.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(check.REPO / 'package/dicttech/cli-profile/files/usr/share/dicttech-cli/authorized_keys', keys)
        keys.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_public_package_files_and_safe_defaults_pass(self):
        report = check.check_rootfs(self.root)
        self.assertTrue(report['passed'])
        self.assertGreaterEqual(len(report['files_checked']), 20)
        self.assertFalse(report['clean_image']['preset_root_password'])
        self.assertEqual(report['clean_image']['preset_authorized_keys'], 'reviewed public DictTech set')

    def test_initialization_credential_asset_and_empty_private_lifecycle_are_required(self):
        asset = self.root / 'usr/share/dicttech-ap/bootstrap-authorized-key.pub'
        original = asset.read_bytes()
        asset.chmod(0o600)
        with self.assertRaisesRegex(check.RootfsError, 'initialization public key'):
            check.check_rootfs(self.root)
        asset.chmod(0o644)
        state = self.root / 'etc/dicttech-ap-bootstrap-key/state'; state.write_text('fresh\n')
        with self.assertRaisesRegex(check.RootfsError, 'lifecycle state'):
            check.check_rootfs(self.root)
        state.unlink()
        self.assertEqual(asset.read_bytes(), original)

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
            patch = source / check.HISTORY_PATCH; patch.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(check.REPO / check.HISTORY_PATCH, patch)
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

    def test_preset_root_password_and_unknown_authorized_key_rejected(self):
        shadow = self.root / 'etc/shadow'; original = shadow.read_text()
        shadow.write_text('root:$6$fixture$not-a-real-hash:0:0:99999:7:::\n')
        with self.assertRaisesRegex(check.RootfsError, 'preset root password'):
            check.check_rootfs(self.root)
        shadow.write_text(original)
        keys = self.root / 'etc/dropbear/authorized_keys'; keys.parent.mkdir(parents=True, exist_ok=True); keys.write_text('synthetic authorized fixture\n')
        with self.assertRaisesRegex(check.RootfsError, 'authorized SSH keys'):
            check.check_rootfs(self.root)


    def test_public_keys_are_required_exact_regular_and_private_mode(self):
        keys = self.root / 'etc/dropbear/authorized_keys'; original = keys.read_bytes()
        for content in (b'', original + b'\n# unknown addition\n', b'synthetic unknown authorized key\n'):
            keys.write_bytes(content)
            with self.assertRaisesRegex(check.RootfsError, 'exactly the reviewed public authorized SSH keys'):
                check.check_rootfs(self.root)
        keys.unlink()
        with self.assertRaisesRegex(check.RootfsError, 'exactly the reviewed public authorized SSH keys'):
            check.check_rootfs(self.root)
        keys.symlink_to(self.root / 'usr/share/dicttech-cli/authorized_keys')
        with self.assertRaisesRegex(check.RootfsError, 'exactly the reviewed public authorized SSH keys'):
            check.check_rootfs(self.root)
        keys.unlink(); keys.write_bytes(original); keys.chmod(0o644)
        with self.assertRaisesRegex(check.RootfsError, 'mode 0600'):
            check.check_rootfs(self.root)
        keys.chmod(0o600)
        self.assertTrue(check.check_rootfs(self.root)['passed'])

    def test_cli_asset_helper_and_root_rc_byte_mode_and_symlink_drift_fail(self):
        for relative in ('usr/share/dicttech-cli/zsh/ys.zsh-theme',
                         'usr/libexec/dicttech-cli/activate-root-shell', 'root/.zshrc'):
            with self.subTest(path=relative):
                path = self.root / relative; original = path.read_bytes(); mode = path.stat().st_mode & 0o777
                path.write_bytes(original + b'\n# stale fixture\n')
                with self.assertRaisesRegex(check.RootfsError, 'differ from authoritative source'):
                    check.check_rootfs(self.root)
                path.write_bytes(original); path.chmod(mode | 0o002)
                with self.assertRaisesRegex(check.RootfsError, 'CLI asset has unsafe or unexpected mode'):
                    check.check_rootfs(self.root)
                path.chmod(mode); path.unlink(); path.symlink_to('/unowned-fixture')
                with self.assertRaisesRegex(check.RootfsError, 'missing or symlinked'):
                    check.check_rootfs(self.root)
                path.unlink(); path.write_bytes(original); path.chmod(mode)
        self.assertTrue(check.check_rootfs(self.root)['passed'])

    def test_cli_shared_parent_must_not_be_group_writable(self):
        path = self.root / 'usr/share/dicttech-cli'; path.chmod(0o775)
        with self.assertRaisesRegex(check.RootfsError, 'CLI asset parent is group/other writable'):
            check.check_rootfs(self.root)

    def test_history_collector_rpc_init_and_acl_exact_postimages_required(self):
        for relative in check.HISTORY_FILES:
            with self.subTest(path=relative):
                path = self.root / relative; original = path.read_bytes(); path.write_bytes(original + b'\n# stale fixture\n')
                with self.assertRaisesRegex(check.RootfsError, 'differ from authoritative source'):
                    check.check_rootfs(self.root)
                path.write_bytes(original)
        self.assertTrue(check.check_rootfs(self.root)['passed'])

    def test_history_ui_manifest_is_locked_to_reviewed_patch(self):
        manifest = json.loads((check.REPO / 'configs/dicttech-ui-manifest.json').read_text())
        entry = manifest['patches']['wifi-history']
        self.assertEqual(entry['patch'], check.HISTORY_PATCH)
        expected = check.patch_postimage(check.REPO / entry['patch'], entry['target'])
        self.assertEqual(check.digest(expected), entry['patched_sha256'])
        # Glass intentionally hides generic page descriptions. Sampling health
        # is operational status and must remain a visible live region instead.
        status_node = next(line for line in expected.decode().splitlines()
                           if "E('p'," in line and "'wifi_history_status'" in line)
        self.assertIn("'role': 'status'", status_node)
        self.assertNotIn('cbi-map-descr', status_node)

    def test_history_gate_rejects_unpatched_prepared_feed(self):
        with tempfile.TemporaryDirectory(prefix='dicttech-history-source-fixture-') as temp:
            source = Path(temp)
            patch = source / check.HISTORY_PATCH; patch.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(check.REPO / check.HISTORY_PATCH, patch)
            for relative, suffix in check.HISTORY_FILES.items():
                target = source / 'feeds/luci/applications/luci-app-wifihistory' / suffix
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(check.history_sources(check.REPO)[relative])
            self.assertEqual(check.history_sources(source), check.history_sources(check.REPO))
            path = source / 'feeds/luci/applications/luci-app-wifihistory/root/usr/sbin/wifihistory'
            path.write_text('# public stale pre-patch collector fixture\n')
            with self.assertRaisesRegex(check.RootfsError, 'prepared feed differs from reviewed history patch'):
                check.history_sources(source)

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
