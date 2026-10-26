#!/usr/bin/env python3
"""Inert temporary Git fixtures for source permission preparation."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'prepare-source-permissions.py'
spec = importlib.util.spec_from_file_location('source_permissions', SOURCE)
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)


def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c',
        'user.email=fixture@example.invalid', '-C', str(repo), *args], stderr=subprocess.DEVNULL)


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


class SourcePermissions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dicttech-source-permissions-')
        self.base = Path(self.temp.name); self.root = self.base / 'source'
        self.root.mkdir(mode=0o700); git(self.root, 'init', '-q')
        self.feed = self.root / 'feeds/alternate-directory'
        self.feed.mkdir(parents=True); git(self.feed, 'init', '-q')
        self.make_file(self.feed, 'module/rpcd/luci', 'public fixture RPC source\n', 0o644)
        self.make_file(self.feed, 'module/start', '#!/bin/sh\nexit 0\n', 0o755)
        git(self.feed, 'add', 'module'); git(self.feed, 'commit', '-qm', 'Fixture feed')
        self.feed_revision = git(self.feed, 'rev-parse', 'HEAD').decode().strip()
        self.make_file(self.root, 'configs/dicttech-feeds.conf',
            '# Pinned fixture feed, no network operations\n'
            'src-git --directory=alternate-directory logical-feed https://example.invalid/feed.git^' + self.feed_revision + '\n')
        self.make_file(self.root, 'src/plain', 'ordinary public source\n', 0o644)
        self.make_file(self.root, 'src/run', '#!/bin/sh\nexit 0\n', 0o755)
        (self.root / 'src/link').symlink_to('../../outside')
        git(self.root, 'add', 'configs', 'src'); git(self.root, 'commit', '-qm', 'Fixture source')
        self.outside = self.base / 'outside'; self.outside.write_text('outside sentinel\n'); self.outside.chmod(0o666)
        self.cache = self.root / 'build_dir/private-cache'; self.cache.mkdir(parents=True)
        (self.cache / 'secret').write_text('untracked private sentinel\n'); (self.cache / 'secret').chmod(0o600)
        self.cache.chmod(0o700)
        self.untracked = self.root / 'src/local-private'; self.untracked.write_text('untracked\n'); self.untracked.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def make_file(self, root, relative, text, permissions=0o644):
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text); path.chmod(permissions); return path

    def taint(self):
        for root in (self.root, self.feed):
            for relative in ('src/plain', 'src/run') if root == self.root else ('module/rpcd/luci', 'module/start'):
                (root / relative).chmod(0o666)
        (self.root / 'src').chmod(0o777)
        (self.root / 'feeds').chmod(0o777)
        (self.feed / 'module/rpcd').chmod(0o2777)

    def test_restores_index_modes_and_source_parents_without_changing_contents_or_private_root(self):
        self.taint()
        paths = [self.root / 'src/plain', self.root / 'src/run', self.feed / 'module/rpcd/luci', self.feed / 'module/start']
        before = {p: p.read_bytes() for p in paths}
        report = check.prepare(self.root)
        self.assertTrue(report['passed']); self.assertEqual(report['changed_files'], 4)
        self.assertEqual(report['changed_directories'], 3)
        self.assertEqual([row['repository'] for row in report['repositories']], ['main', 'logical-feed'])
        self.assertEqual([mode(p) for p in paths], [0o644, 0o755, 0o644, 0o755])
        self.assertEqual({p: p.read_bytes() for p in paths}, before)
        self.assertEqual(mode(self.root), 0o700)
        self.assertEqual(mode(self.root / 'feeds'), 0o755)
        self.assertEqual(mode(self.feed / 'module/rpcd'), 0o755)
        self.assertEqual(mode(self.cache), 0o700); self.assertEqual(mode(self.cache / 'secret'), 0o600)
        self.assertEqual(mode(self.untracked), 0o600)
        self.assertEqual(mode(self.outside), 0o666)
        self.assertTrue((self.root / 'src/link').is_symlink())
        self.assertEqual(check.prepare(self.root)['changes_needed'], 0)

    def test_check_reports_drift_and_cli_exit_status_without_mutation(self):
        self.taint()
        result = subprocess.run([sys.executable, str(SOURCE), '--source-root', str(self.root), '--check'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['passed']); self.assertEqual(report['changed_files'], 0)
        self.assertGreater(report['changes_needed'], 0)
        self.assertEqual(mode(self.root / 'src/plain'), 0o666)
        check.prepare(self.root)
        result = subprocess.run([sys.executable, str(SOURCE), '--source-root', str(self.root), '--check'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('outside sentinel', result.stdout + result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)

    def test_wrong_feed_commit_rejects_before_any_main_source_change(self):
        self.taint()
        self.make_file(self.feed, 'later', 'later commit\n'); git(self.feed, 'add', 'later')
        git(self.feed, 'commit', '-qm', 'Later fixture commit')
        with self.assertRaisesRegex(check.PermissionsError, 'wrong locked feed commit'):
            check.prepare(self.root)
        self.assertEqual(mode(self.root / 'src/plain'), 0o666)
        self.assertEqual(mode(self.root / 'src'), 0o777)

    def test_tracked_file_replaced_with_external_symlink_is_refused(self):
        self.taint()
        path = self.feed / 'module/rpcd/luci'; path.unlink(); path.symlink_to(self.outside)
        with self.assertRaises(OSError):
            check.prepare(self.root)
        self.assertEqual(mode(self.outside), 0o666)
        self.assertEqual(mode(self.root / 'src/plain'), 0o666)

    def test_tracked_parent_symlink_even_inside_checkout_is_refused(self):
        self.taint()
        directory = self.feed / 'module/rpcd'; moved = self.feed / 'saved'
        directory.rename(moved); directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            check.prepare(self.root)
        self.assertEqual(mode(moved / 'luci'), 0o666)
        self.assertEqual(mode(self.root / 'src/plain'), 0o666)

    def test_symlinked_feed_or_source_root_is_refused(self):
        moved = self.base / 'moved-feed'; self.feed.rename(moved)
        self.feed.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            check.prepare(self.root)
        self.feed.unlink(); moved.rename(self.feed)
        alias = self.base / 'source-alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            check.prepare(alias)

    def test_hardlink_cannot_mutate_an_external_inode(self):
        self.taint()
        path = self.root / 'src/plain'; path.unlink(); os.link(self.outside, path)
        with self.assertRaisesRegex(check.PermissionsError, 'hard links'):
            check.prepare(self.root)
        self.assertEqual(mode(self.outside), 0o666)
        self.assertEqual(mode(self.feed / 'module/rpcd/luci'), 0o666)

    def test_feed_lock_paths_and_unpinned_revisions_are_rejected(self):
        lock = self.root / 'configs/dicttech-feeds.conf'
        for content in (
            'src-git --directory=../outside feed https://example.invalid/feed^' + self.feed_revision,
            'src-git --directory=/outside feed https://example.invalid/feed^' + self.feed_revision,
            'src-git feed https://example.invalid/feed^main',
            'src-link feed /outside',
        ):
            lock.write_text(content + '\n')
            with self.assertRaises(check.PermissionsError):
                check.prepare(self.root)

    def test_missing_index_file_or_changed_symlink_type_is_refused(self):
        plain = self.root / 'src/plain'; data = plain.read_bytes(); plain.unlink()
        with self.assertRaises(FileNotFoundError):
            check.prepare(self.root)
        plain.write_bytes(data)
        link = self.root / 'src/link'; link.unlink(); link.write_text('now an ordinary file')
        with self.assertRaisesRegex(check.PermissionsError, 'symlink has changed file type'):
            check.prepare(self.root)

    def test_default_feed_directory_is_the_logical_name(self):
        self.feed.rename(self.root / 'feeds/logical-feed')
        (self.root / 'configs/dicttech-feeds.conf').write_text(
            'src-git logical-feed https://example.invalid/feed^' + self.feed_revision + '\n')
        report = check.prepare(self.root, check=True)
        self.assertTrue(report['passed'])
        self.assertEqual(report['repositories'][1]['repository'], 'logical-feed')

    def test_checkout_root_only_loses_group_other_write_permission(self):
        self.root.chmod(0o777); self.feed.chmod(0o700)
        check.prepare(self.root)
        self.assertEqual(mode(self.root), 0o755)
        self.assertEqual(mode(self.feed), 0o700)

    def test_source_metadata_path_parser_rejects_dot_components(self):
        for path in ('/absolute', '../parent', 'src/../outside', 'src//file', './file', 'trailing/'):
            with self.assertRaises(check.PermissionsError):
                check.safe_relative(path)
        self.assertEqual(check.safe_relative('src/file with\nwhitespace'), 'src/file with\nwhitespace')


if __name__ == '__main__':
    unittest.main(verbosity=2)
