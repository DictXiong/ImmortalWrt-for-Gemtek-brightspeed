#!/usr/bin/env python3
"""No-device tests for source/installed byte identity and virgin-image policy."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'check-rootfs.py'
spec = importlib.util.spec_from_file_location('check_ap_rootfs', SOURCE)
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)


class RootfsChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dicttech-rootfs-fixture-')
        self.root = Path(self.temp.name) / 'rootfs'; self.root.mkdir()
        wrtix = Path(self.temp.name) / 'wrtix'
        for relative in ('agent/agent.sh', 'agent/guard.init', 'agent/contract.json',
                         'feed/wrtix-agent/files/usr/libexec/wrtix/package-lifecycle',
                         'feed/wrtix-agent/files/lib/apk/commit_hooks.d/50-wrtix',
                         'feed/wrtix-agent/files/lib/upgrade/wrtix.sh'):
            path = wrtix / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic immutable package asset\n')
        self.wrtix_patch = patch.object(check, 'pinned_wrtix', return_value=wrtix)
        self.wrtix_patch.start(); self.addCleanup(self.wrtix_patch.stop)
        for relative, source in check.profile_map(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if relative.startswith(('etc/init.d/', 'usr/libexec/', 'lib/apk/commit_hooks.d/')):
                target.chmod(0o755)
        for relative, source in check.roaming_map(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for relative, source in check.cli_map(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for relative, source in check.GENERATOR_FILES.items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(check.REPO / source, target)
        (self.root / 'etc/wrtix').mkdir(mode=0o700)
        (self.root / 'etc/xr1710g-full').write_text('XR1710G Full\n')
        files = ('usr/libexec/wrtix/agent', 'etc/init.d/wrtix-agent', 'usr/share/wrtix/contract.json')
        (self.root / 'usr/share/wrtix/runtime.sha256').write_text(''.join(check.digest((self.root / name).read_bytes()) + '  ' + name + '\n' for name in files))
        shadow = self.root / 'etc/shadow'; shadow.write_text('root:::0:99999:7:::\nservice:*:0:0:99999:7:::\n'); shadow.chmod(0o600)
        (self.root / 'www').mkdir(); (self.root / 'www/xr-roaming').symlink_to('/tmp/xr-roaming-public')
        (self.root / 'etc/resolv.conf').symlink_to('/tmp/resolv.conf')
        (self.root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\n')
        for relative in check.RPC_REQUIRED_FILES:
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('public synthetic RPC fixture\n'); target.chmod(0o644)
        for relative, content in check.history_sources(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content); target.chmod(0o755 if relative in ('usr/sbin/wifihistory', 'etc/init.d/wifihistory') else 0o644)
        for relative, content in check.factory_sources(check.REPO).items():
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content); target.chmod(0o755 if relative.startswith('usr/libexec/') else 0o644)

    def tearDown(self):
        self.temp.cleanup()

    def test_cli_installed_identity_and_permissions(self):
        target = self.root / 'etc/profile.d/80-xr1710g-cli.sh'
        target.chmod(0o666)
        with self.assertRaisesRegex(check.RootfsError, 'CLI file mode'):
            check.check_rootfs(self.root)
        target.chmod(0o644)
        target.write_text('exec /bin/false\n')
        with self.assertRaisesRegex(check.RootfsError, 'installed bytes differ'):
            check.check_rootfs(self.root)

    def test_matching_public_package_files_and_safe_defaults_pass(self):
        report = check.check_rootfs(self.root)
        self.assertTrue(report['passed'])
        self.assertGreaterEqual(len(report['files_checked']), 20)
        self.assertFalse(report['clean_image']['preset_root_password'])
        self.assertEqual(report['clean_image']['preset_authorized_keys'], 'absent')

    def test_runtime_modes_manifest_and_retired_runtime_rejected(self):
        path = self.root / 'etc/init.d/wrtix-agent'
        path.chmod(0o777)
        with self.assertRaisesRegex(check.RootfsError, 'unexpected mode'):
            check.check_rootfs(self.root)
        path.chmod(0o755)
        manifest = self.root / 'usr/share/wrtix/runtime.sha256'
        content = manifest.read_bytes(); manifest.write_text('forged\n')
        with self.assertRaisesRegex(check.RootfsError, 'authoritative source'):
            check.check_rootfs(self.root)
        manifest.write_bytes(content)
        old = self.root / 'usr/libexec/dicttech-ap'; old.mkdir()
        with self.assertRaisesRegex(check.RootfsError, 'retired runtime'):
            check.check_rootfs(self.root)

    def test_personal_assets_and_preset_credentials_rejected(self):
        for relative in ('etc/dicttech-setup/identity.json', 'usr/share/dicttech-cli/authorized_keys',
                         'root/.zshrc', 'etc/wrtix/device.json', 'etc/dropbear/authorized_keys',
                         'etc/dropbear/dropbear_ed25519_host_key'):
            path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic prohibited state\n')
            with self.assertRaises(check.RootfsError): check.check_rootfs(self.root)
            path.unlink()
            if relative.startswith('etc/dicttech-setup/'):
                path.parent.rmdir()
            if relative.startswith('usr/share/dicttech-cli/'):
                path.parent.rmdir()
        shadow = self.root / 'etc/shadow'
        shadow.write_text('root:$6$fixture$hash:0:0:99999:7:::\n')
        with self.assertRaisesRegex(check.RootfsError, 'preset root password'):
            check.check_rootfs(self.root)

    def test_nondefault_shell_and_personal_maintenance_rejected(self):
        passwd = self.root / 'etc/passwd'; original = passwd.read_text()
        passwd.write_text(original.replace('/bin/ash', '/usr/bin/zsh'))
        with self.assertRaisesRegex(check.RootfsError, 'default root shell'): check.check_rootfs(self.root)
        passwd.write_text(original)
        path = self.root / 'etc/config/network'
        path.write_text("config interface 'site_management'\n option ipaddr '192.0.2.1'\n")
        with self.assertRaisesRegex(check.RootfsError, 'preset network'): check.check_rootfs(self.root)

    def test_wireless_identity_and_credentials_rejected_for_any_name(self):
        path = self.root / 'etc/config/wireless'
        path.write_text("config wifi-device 'radio0'\n option disabled '1'\n")
        self.assertTrue(check.check_rootfs(self.root)['passed'])
        for declaration, message in [
                ("option ssid 'Example-Site'", 'wireless identity'),
                ("option macaddr '02:00:00:00:00:01'", 'wireless identity'),
                ("option key 'synthetic-fixture'", 'Wi-Fi credentials'),
                ("list sae_password 'synthetic-fixture'", 'Wi-Fi credentials'),
                ("list r0kh 'synthetic-fixture'", 'Wi-Fi credentials'),
                ("option disabled '0'", 'duplicate preset UCI option')]:
            with self.subTest(declaration=declaration):
                path.write_text("config wifi-iface 'example'\n option disabled '1'\n " + declaration + "\n")
                with self.assertRaisesRegex(check.RootfsError, message): check.check_rootfs(self.root)
        path.write_text("config wifi-device 'radio0'\n option disabled '0'\n")
        with self.assertRaisesRegex(check.RootfsError, 'enabled preset Wi-Fi'): check.check_rootfs(self.root)

    def test_firewall_has_only_public_default_network_bindings(self):
        path = self.root / 'etc/config/firewall'
        path.write_text("config zone\n option name 'lan'\n list network 'lan'\n"
                        "config rule\n option src 'wan'\n option src_ip 'fe80::/10'\n")
        self.assertTrue(check.check_rootfs(self.root)['passed'])
        for data in ["config zone\n option name 'example_site'\n",
                     "config zone\n option name 'lan'\n list network 'site_management'\n",
                     "config rule\n option src 'example_site'\n",
                     "config rule\n option src_ip '192.0.2.10'\n",
                     "config rule\n option src_mac '02:00:00:00:00:01'\n",
                     "config include\n option path '/etc/example_site.nft'\n"]:
            with self.subTest(data=data):
                path.write_text(data)
                with self.assertRaisesRegex(check.RootfsError, 'firewall'): check.check_rootfs(self.root)

    def test_stale_runtime_and_changed_default_config_fail(self):
        for relative in ('usr/libexec/wrtix/agent', 'usr/lib/xr-roaming/helper.sh', 'etc/config/xr_roaming'):
            path = self.root / relative; before = path.read_bytes(); path.write_bytes(before + b'\n# stale fixture\n')
            with self.assertRaisesRegex(check.RootfsError, 'differ from authoritative source'):
                check.check_rootfs(self.root)
            path.write_bytes(before)

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

    def test_factory_backend_and_view_require_complete_reviewed_byte_identity(self):
        for relative in check.FACTORY_FILES:
            with self.subTest(path=relative):
                path = self.root / relative; original = path.read_bytes()
                path.write_bytes(original + b'\n/* unreviewed fixture drift */\n')
                with self.assertRaises(check.RootfsError):
                    check.check_rootfs(self.root)
                path.write_bytes(original)
        self.assertTrue(check.check_rootfs(self.root)['passed'])

    def test_factory_assets_missing_symlinked_or_wrong_mode_fail(self):
        for relative in check.FACTORY_FILES:
            with self.subTest(path=relative):
                path = self.root / relative; original = path.read_bytes(); mode = path.stat().st_mode & 0o777
                path.unlink()
                with self.assertRaises(check.RootfsError):
                    check.check_rootfs(self.root)
                path.symlink_to('/not-an-owned-factory-fixture')
                with self.assertRaises(check.RootfsError):
                    check.check_rootfs(self.root)
                path.unlink(); path.write_bytes(original); path.chmod(mode | 0o002)
                with self.assertRaisesRegex(check.RootfsError, 'factory asset has unexpected mode'):
                    check.check_rootfs(self.root)
                path.chmod(mode)

    def test_factory_official_minified_hash_is_an_independent_complete_identity(self):
        # Exercise the compact-byte branch without depending on a host compiler
        # before CI builds the real official jsmin. Actual builds recheck jsmin.
        from unittest.mock import patch
        relative = 'www/luci-static/resources/view/airoha_factory/factory.js'
        path = self.root / relative; original = path.read_bytes()
        fixture = b'synthetic official-minifier output\n'; path.write_bytes(fixture)
        manifest = check.factory_policy(check.REPO)
        manifest['patches']['factory-info-view']['minified_sha256'] = check.digest(fixture)
        with patch.object(check, 'factory_policy', return_value=manifest):
            rows = check.check_factory(self.root, check.REPO, check.factory_sources(check.REPO))
            self.assertEqual(rows[-1]['format'], 'official-jsmin')
            path.write_bytes(fixture + b'changed')
            with self.assertRaisesRegex(check.RootfsError, 'official minified bytes'):
                check.check_factory(self.root, check.REPO, check.factory_sources(check.REPO))
        path.write_bytes(original)

    def test_factory_view_parent_must_not_be_group_writable(self):
        parent = self.root / 'www/luci-static/resources/view/airoha_factory'
        parent.chmod(0o775)
        with self.assertRaisesRegex(check.RootfsError, 'factory asset parent is group/other writable'):
            check.check_rootfs(self.root)

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
