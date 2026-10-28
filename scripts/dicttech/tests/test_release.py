#!/usr/bin/env python3
"""Offline tests of two pinned Git snapshots, isolation and one release pair."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'release', Path(__file__).resolve().parents[1] / 'release.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
REJECTED = (ValueError, subprocess.CalledProcessError)


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.repo = cls.root / 'repo'
        cls.repo.mkdir()
        r.git(cls.repo, 'init', '-q', '-b', 'fixture-main')
        cls.configure(cls.repo)
        cls.put(cls.repo, 'feeds.conf.default',
                'src-git luci https://example.invalid/luci.git\n')
        cls.put(cls.repo, '.gitignore', '/files\n/.config\n/feeds.conf\n')
        cls.put(cls.repo, '1710.config',
                'CONFIG_PACKAGE_luci=y\nCONFIG_TARGET_MULTI_PROFILE=y\n'
                'CONFIG_TARGET_DEVICE_airoha_an7581_DEVICE_gemtek_xr1710g=y\n')
        cls.base = cls.commit(cls.repo, 'Upstream')
        cls.fixes = []
        for i in range(3):
            cls.put(cls.repo, 'fix-' + str(i) + '.patch',
                    'reviewed ' + str(i) + '\n')
            cls.fixes.append(cls.commit(cls.repo, 'Fix ' + str(i)))
        cls.fixes_head = cls.fixes[-1]
        r.git(cls.repo, 'branch', 'fixture-fixes', cls.fixes_head)
        r.git(cls.repo, 'branch', 'fixture-pr-frozen', cls.fixes_head)
        cls.put(cls.repo, 'package/dicttech/owner',
                'PUBLIC OWNER FIXTURE - NOT FOR COMMUNITY\n')
        cls.put(cls.repo, 'package/dicttech/ap-profile/Makefile',
                'PKG_VERSION:=2.1.0\n')
        cls.put(cls.repo, 'configs/xr1710g-release.json', json.dumps({
            'schema_version': 2, 'version': '2.1.0',
            'upstream_base': cls.base, 'fixes_commit': cls.fixes_head}))
        cls.put(cls.repo, 'configs/xr1710g-fixes-feeds.conf',
                'src-git luci https://example.invalid/luci.git^' + 'd' * 40 + '\n')
        cls.head = cls.commit(cls.repo, 'Personal policy and release metadata')

    @staticmethod
    def configure(repo):
        for args in [('user.name', 'Fixture'),
                     ('user.email', 'fixture@example.invalid'),
                     ('commit.gpgsign', 'false'),
                     ('core.hooksPath', '/dev/null')]:
            r.git(repo, 'config', *args)

    @staticmethod
    def put(repo, name, text):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    @staticmethod
    def commit(repo, title):
        r.git(repo, 'add', '.')
        r.git(repo, 'commit', '-qm', title)
        return r.git(repo, 'rev-parse', 'HEAD')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def destination(self):
        return Path(tempfile.mkdtemp(dir=self.root)) / 'new'

    def clone(self):
        repo = self.destination()
        r.run(['git', 'clone', '--shared', str(self.repo), str(repo)], self.root)
        self.configure(repo)
        return repo

    def set_lock(self, repo, **updates):
        path = repo / 'configs/xr1710g-release.json'
        document = json.loads(path.read_text())
        document.update(updates)
        path.write_text(json.dumps(document))

    def append_fourth_fix(self, repo):
        r.git(repo, 'checkout', '--detach', self.fixes_head)
        self.put(repo, 'fix-3.patch', 'reviewed 3\n')
        return self.commit(repo, 'Fourth generic fix')

    def test_exact_two_commits_without_moving_branches(self):
        before = r.git(self.repo, 'show-ref')
        for flavor in r.FLAVORS:
            with self.subTest(flavor=flavor):
                out = self.destination()
                receipt = r.prepare(self.repo, out, flavor, 'legacy')
                source = out / 'source'
                expected = self.head if flavor == 'full' else self.fixes_head
                self.assertEqual(receipt['release_commit'], self.head)
                self.assertEqual(receipt['fixes_commit'], self.fixes_head)
                self.assertEqual(receipt['source_commit'], expected)
                self.assertEqual(r.git(source, 'rev-parse', 'HEAD'), expected)
                self.assertEqual(receipt['source_tree'],
                                 r.git(source, 'rev-parse', expected + '^{tree}'))
                self.assertEqual((source / 'package/dicttech/owner').exists(),
                                 flavor == 'full')
                self.assertFalse(r.git(source, 'status', '--porcelain'))
                for i in range(3):
                    self.assertEqual((source / ('fix-' + str(i) + '.patch')).read_text(),
                                     'reviewed ' + str(i) + '\n')
                r.source_check(out, receipt)
        self.assertEqual(r.git(self.repo, 'show-ref'), before)

    def test_fourth_fix_is_merged_without_rebasing_personal_history(self):
        repo = self.clone()
        fourth = self.append_fourth_fix(repo)
        self.assertEqual(r.git(repo, 'rev-parse', fourth + '^'), self.fixes_head)
        r.git(repo, 'checkout', 'fixture-main')
        r.git(repo, 'merge', '--no-ff', '-m', 'Merge fourth generic fix', fourth)
        self.set_lock(repo, fixes_commit=fourth)
        full = self.commit(repo, 'Pin merged fourth fix')
        r.git(repo, 'merge-base', '--is-ancestor', self.head, full)
        r.git(repo, 'merge-base', '--is-ancestor', fourth, full)
        self.assertEqual(r.git(repo, 'rev-parse', 'origin/fixture-pr-frozen'),
                         self.fixes_head)
        for flavor in r.FLAVORS:
            out = self.destination()
            receipt = r.prepare(repo, out, flavor, 'legacy')
            self.assertEqual(receipt['fixes_commit'], fourth)
            self.assertEqual((out / 'source/fix-3.patch').read_text(), 'reviewed 3\n')
            self.assertEqual(receipt['source_commit'], full if flavor == 'full' else fourth)

    def test_branch_head_movement_does_not_change_locked_snapshot(self):
        repo = self.clone()
        fourth = self.append_fourth_fix(repo)
        r.git(repo, 'branch', '-f', 'fixture-fixes', fourth)
        r.git(repo, 'checkout', 'fixture-main')
        self.assertEqual(r.plan(repo)['fixes_commit'], self.fixes_head)
        out = self.destination()
        receipt = r.prepare(repo, out, 'fixes', 'legacy')
        self.assertEqual(receipt['source_commit'], self.fixes_head)
        self.assertFalse((out / 'source/fix-3.patch').exists())
        self.assertEqual(r.git(repo, 'rev-parse', 'fixture-fixes'), fourth)

    def test_unmerged_fixes_commit_is_rejected(self):
        repo = self.clone()
        fourth = self.append_fourth_fix(repo)
        r.git(repo, 'checkout', 'fixture-main')
        self.set_lock(repo, fixes_commit=fourth)
        self.commit(repo, 'Incorrectly pin an unmerged fix')
        with self.assertRaises(REJECTED):
            r.plan(repo)

    def test_personal_snapshot_cannot_be_used_as_fixes_source(self):
        repo = self.clone()
        self.set_lock(repo, fixes_commit=self.head)
        self.commit(repo, 'Incorrectly select a personal snapshot as fixes')
        with self.assertRaises(REJECTED):
            r.prepare(repo, self.destination(), 'fixes', 'legacy')

    def test_wrong_upstream_base_and_moving_fix_ref_are_rejected(self):
        for updates in [{'upstream_base': self.head},
                        {'fixes_commit': 'fixture-fixes'}]:
            repo = self.clone()
            self.set_lock(repo, **updates)
            self.commit(repo, 'Invalid release lock')
            with self.subTest(updates=updates), self.assertRaises(REJECTED):
                r.plan(repo)

    def test_dirty_controller_and_old_patch_list_schema_are_rejected(self):
        repo = self.clone()
        self.set_lock(repo, version='2.1.1')
        with self.assertRaises(REJECTED):
            r.plan(repo)
        self.put(repo, 'configs/xr1710g-release.json', json.dumps({
            'schema_version': 1, 'version': '2.1.0',
            'upstream_base': self.base, 'fixes': self.fixes}))
        self.commit(repo, 'Obsolete patch-list release lock')
        with self.assertRaises(REJECTED):
            r.plan(repo)

    def test_modified_source_and_forged_tree_are_rejected(self):
        out = self.destination()
        receipt = r.prepare(self.repo, out, 'fixes', 'legacy')
        source = out / 'source'
        (source / '1710.config').write_text('tampered\n')
        with self.assertRaises(REJECTED):
            r.source_check(out, receipt)
        r.git(source, 'add', '1710.config')
        receipt['source_tree'] = r.git(source, 'write-tree')
        with self.assertRaises(REJECTED):
            r.source_check(out, receipt)

    def test_forged_source_commit_and_actual_wrong_checkout_are_rejected(self):
        out = self.destination()
        receipt = r.prepare(self.repo, out, 'fixes', 'legacy')
        wrong = copy.deepcopy(receipt)
        wrong['source_commit'] = self.head
        with self.assertRaises(REJECTED):
            r.source_check(out, wrong)
        r.git(out / 'source', 'checkout', '--detach', self.head)
        with self.assertRaises(REJECTED):
            r.source_check(out, receipt)

    def test_overlay_and_destination_are_rejected(self):
        with self.assertRaises(REJECTED):
            r.prepare(self.repo, self.repo / 'danger', 'fixes', 'legacy')
        target = self.destination()
        target.symlink_to(target.parent / 'absent')
        with self.assertRaises(REJECTED):
            r.prepare(self.repo, target, 'fixes', 'legacy')
        out = self.destination()
        receipt = r.prepare(self.repo, out, 'fixes', 'legacy')
        (out / 'source/files').mkdir()
        with self.assertRaises(REJECTED):
            r.source_check(out, receipt)

    def test_one_partition_profile_keeps_stock_packages(self):
        for layout in ['legacy', 'ubi']:
            text = r.stock_config((self.repo / '1710.config').read_text(),
                                  layout, '2.1.0-test', 'abc')
            self.assertIn('CONFIG_PACKAGE_luci=y', text)
            self.assertNotIn('CONFIG_TARGET_DEVICE_airoha', text)
            self.assertEqual(text.count('CONFIG_TARGET_airoha_an7581_DEVICE_'), 1)
            self.assertNotIn('CONFIG_TARGET_MULTI_PROFILE=y', text)
            self.assertNotIn('CONFIG_PACKAGE_ap-profile', text)

    def test_rootfs_personal_credentials_and_policy_are_rejected(self):
        root = self.destination()
        root.mkdir()
        r.audit_fixes_rootfs(root, {'kernel': 'test'})
        path = root / 'etc/dropbear/authorized_keys'
        path.parent.mkdir(parents=True)
        path.write_text('ssh-ed25519 FIXTURE\n')
        with self.assertRaises(REJECTED):
            r.audit_fixes_rootfs(root, {'kernel': 'test'})
        path.unlink()
        path.symlink_to('/nonexistent')
        with self.assertRaises(REJECTED):
            r.audit_fixes_rootfs(root, {'kernel': 'test'})
        path.unlink()
        with self.assertRaises(REJECTED):
            r.audit_fixes_rootfs(root, {'kernel': 'test', 'ap-profile': 'test'})
        host_key = root / 'etc/dropbear/dropbear_ed25519_host_key'
        host_key.write_bytes(b'INERT PRIVATE HOST KEY FIXTURE')
        with self.assertRaises(REJECTED):
            r.audit_fixes_rootfs(root, {'kernel': 'test'})

    def test_stock_and_personal_partition_audits_are_separate(self):
        spec = importlib.util.spec_from_file_location(
            'fit_fixture', Path(__file__).with_name('test_ap_image.py'))
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        personal = fixture.check
        stock = r.fixes_image_checker()
        fixture.check = stock
        for layout in ['legacy', 'ubi']:
            image = fixture.fit(fixture.device(layout), fixture.metadata(layout))
            self.assertTrue(stock.check_image(image, layout)['passed'])
            if layout == 'legacy':
                with self.assertRaises(personal.ImageError):
                    personal.check_image(image, layout)
        fixture.check = personal
        image = fixture.fit(fixture.device(), fixture.metadata())
        with self.assertRaises(stock.ImageError):
            stock.check_image(image, 'legacy')

    def fake_bundle(self, folder, flavor, *, evil=False, revision=None,
                    fixes_commit=None, source_commit=None, source_tree=None,
                    upstream_base=None):
        folder.mkdir(parents=True)
        revision = revision or self.head
        fixes_commit = fixes_commit or self.fixes_head
        source_commit = source_commit or (revision if flavor == 'full' else fixes_commit)
        version = '2.1.0-' + revision[:12]
        prefix = 'xr1710g-' + flavor + '-legacy-' + version
        document = {
            'schema_version': 2, 'flavor': flavor, 'layout': 'legacy',
            'version': '2.1.0', 'build_version': version,
            'release_commit': revision, 'fixes_commit': fixes_commit,
            'source_commit': source_commit,
            'source_tree': source_tree or r.git(self.repo, 'rev-parse', source_commit + '^{tree}'),
            'upstream_base': upstream_base or self.base,
        }
        image = b'INERT FIT FIXTURE'
        document.update(passed=True, image='fixture-squashfs-sysupgrade.itb',
                        image_sha256=hashlib.sha256(image).hexdigest(),
                        bundle=prefix + '.tar.gz',
                        source_archive=prefix + '-source.tar.gz')
        source_archive = folder / document['source_archive']
        with tarfile.open(source_archive, 'w:gz') as archive:
            data = b'INERT EXACT-SOURCE ARCHIVE FIXTURE\n'
            member = tarfile.TarInfo('README.txt')
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        document['source_archive_sha256'] = r.digest(source_archive)
        bundle = folder / document['bundle']
        with tarfile.open(bundle, 'w:gz') as archive:
            receipt = {k: v for k, v in document.items()
                       if k not in ['passed', 'image', 'image_sha256', 'bundle',
                                    'source_archive', 'source_archive_sha256']}
            rows = {
                'source.json': json.dumps(receipt).encode(),
                document['image']: image,
                'fixture.manifest': b'kernel - test\n',
                **{name: b'fixture' for name in
                   ['config.buildinfo', 'feeds.buildinfo', 'version.buildinfo']},
            }
            if evil:
                rows['key-build.pem'] = b'INERT PRIVATE KEY FIXTURE'
            for name, data in rows.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        document['bundle_sha256'] = r.digest(bundle)
        (folder / (prefix + '.json')).write_text(json.dumps(document))
        (folder / (prefix + '-sysupgrade.itb')).write_bytes(image)

    def test_complete_pair_and_hashes(self):
        inputs = self.destination()
        self.fake_bundle(inputs / 'one', 'full')
        self.fake_bundle(inputs / 'two', 'fixes')
        out = self.destination()
        r.assemble(inputs, out, self.head, self.fixes_head)
        self.assertEqual(len(list(out.glob('*.tar.gz'))), 4)
        self.assertEqual(len(list(out.glob('*-source.tar.gz'))), 2)
        self.assertEqual(len(list(out.glob('*.itb'))), 2)
        pair = json.loads((out / 'release-pair.json').read_text())
        self.assertEqual(pair['fixes_commit'], self.fixes_head)
        self.assertEqual({d['source_commit'] for d in pair['variants']},
                         {self.head, self.fixes_head})

    def test_missing_mixed_and_private_artifacts_are_rejected(self):
        for mode in ['missing', 'mixed', 'private', 'modified', 'wrong-fixes',
                     'wrong-source', 'duplicate-edition', 'source-archive-modified']:
            inputs = self.destination()
            self.fake_bundle(inputs / 'one', 'full')
            if mode != 'missing':
                self.fake_bundle(
                    inputs / 'two', 'full' if mode == 'duplicate-edition' else 'fixes',
                    evil=mode == 'private',
                    revision=self.base if mode == 'mixed' else None,
                    fixes_commit=self.base if mode == 'wrong-fixes' else None,
                    source_commit=self.head if mode == 'wrong-source' else None)
            if mode == 'modified':
                path = next(path for path in (inputs / 'two').glob('*.tar.gz')
                            if not path.name.endswith('-source.tar.gz'))
                path.write_bytes(path.read_bytes() + b'changed')
            if mode == 'source-archive-modified':
                path = next((inputs / 'two').glob('*-source.tar.gz'))
                path.write_bytes(path.read_bytes() + b'changed')
            with self.subTest(mode=mode), self.assertRaises(REJECTED):
                r.assemble(inputs, self.destination(), self.head, self.fixes_head)

    def test_pair_controller_pins_upstream_and_both_source_trees(self):
        trees = {
            'full': r.git(self.repo, 'rev-parse', self.head + '^{tree}'),
            'fixes': r.git(self.repo, 'rev-parse', self.fixes_head + '^{tree}'),
        }
        for mode in ['valid', 'wrong-upstream', 'forged-tree']:
            inputs = self.destination()
            upstream = self.head if mode == 'wrong-upstream' else self.base
            self.fake_bundle(inputs / 'one', 'full', upstream_base=upstream)
            self.fake_bundle(inputs / 'two', 'fixes', upstream_base=upstream,
                             source_tree='f' * 40 if mode == 'forged-tree' else None)
            with self.subTest(mode=mode):
                if mode == 'valid':
                    r.assemble(inputs, self.destination(), self.head,
                               self.fixes_head, self.base, trees)
                else:
                    with self.assertRaises(REJECTED):
                        r.assemble(inputs, self.destination(), self.head,
                                   self.fixes_head, self.base, trees)

    def test_chinese_notes_pin_both_source_commits(self):
        inputs = self.destination()
        self.fake_bundle(inputs / 'one', 'full')
        self.fake_bundle(inputs / 'two', 'fixes')
        pair = self.destination()
        r.assemble(inputs, pair, self.head, self.fixes_head)
        output = self.destination()
        template = self.destination()
        template.write_text('这是测试用中文发布说明。\n')
        r.generate_notes(pair / 'release-pair.json', output, template)
        notes = output.read_text()
        self.assertIn('中文发布说明', notes)
        self.assertIn(self.head, notes)
        self.assertIn(self.fixes_head, notes)
        self.assertIn('full', notes)
        self.assertIn('fixes', notes)
        with self.assertRaises(FileExistsError):
            r.generate_notes(pair / 'release-pair.json', output, template)
        document = json.loads((pair / 'release-pair.json').read_text())
        document['variants'][0]['source_commit'] = self.base
        (pair / 'release-pair.json').write_text(json.dumps(document))
        with self.assertRaises(REJECTED):
            r.generate_notes(pair / 'release-pair.json', self.destination(), template)


if __name__ == '__main__':
    os.umask(0o022)
    unittest.main()
