#!/usr/bin/env python3
"""Inert factory source/feed/minified identity gates; no feed/network mutation."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('check_ui_source', REPO / 'scripts/dicttech/check-ui-source.py')
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)


def preimage(target):
    lines = (REPO / check.policy.FACTORY_PATCH).read_text().splitlines(keepends=True)
    start = lines.index('+++ b/' + target + '\n') + 1
    expected = int(re.fullmatch(r'@@ -1,(\d+) \+1,\d+ @@\n', lines[start])[1])
    data = []
    for line in lines[start + 1:]:
        if line.startswith('diff --git '):
            break
        if line.startswith((' ', '-')):
            data.append(line[1:])
    assert len(data) == expected
    return ''.join(data).encode()


class UISourceChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dicttech-ui-source-')
        self.root = Path(self.temp.name)
        (self.root / 'configs').mkdir()
        self.manifest = json.loads((REPO / 'configs/dicttech-ui-manifest.json').read_text())
        self.manifest['patches'] = {k: v for k, v in self.manifest['patches'].items() if k.startswith('factory-')}
        self.save_manifest()
        shutil.copyfile(REPO / 'configs/dicttech-feeds.conf', self.root / 'configs/dicttech-feeds.conf')
        source_patch = self.root / check.policy.FACTORY_PATCH; source_patch.parent.mkdir(parents=True)
        shutil.copyfile(REPO / check.policy.FACTORY_PATCH, source_patch)
        for target_name in check.policy.FACTORY_TARGETS.values():
            target = self.root / 'feeds/app_airoha' / target_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(check.policy.patch_postimage(REPO / check.policy.FACTORY_PATCH, target_name))
        self.git = patch.object(check, 'git_data', side_effect=self.git_data)
        self.git.start()

    def tearDown(self):
        self.git.stop(); self.temp.cleanup()

    def save_manifest(self):
        (self.root / 'configs/dicttech-ui-manifest.json').write_text(json.dumps(self.manifest))

    def git_data(self, feed, *args):
        self.assertEqual(feed, self.root / 'feeds/app_airoha')
        if args == ('rev-parse', 'HEAD'):
            return (self.manifest['feed_app_airoha_revision'] + '\n').encode()
        self.assertEqual(args[0], 'show')
        revision, target = args[1].split(':', 1)
        self.assertEqual(revision, self.manifest['feed_app_airoha_revision'])
        return preimage(target)

    def test_exact_frozen_factory_source_and_preimages_pass(self):
        result = check.check_source(self.root)
        self.assertTrue(result['passed']); self.assertEqual(len(result['files']), 3)

    def test_warm_feed_translation_drift_cannot_hide_behind_matching_code(self):
        target = self.root / 'feeds/app_airoha' / self.manifest['patches']['factory-translation']['target']
        target.write_bytes(target.read_bytes() + b'\n# unreviewed translation drift\n')
        with self.assertRaisesRegex(check.policy.RootfsError, 'prepared feed differs.*po/zh_Hans'):
            check.check_source(self.root)

    def test_wrong_actual_feed_revision_rejected(self):
        with patch.object(check, 'git_data', return_value=b'0' * 40 + b'\n'):
            with self.assertRaisesRegex(check.policy.RootfsError, 'Wrong factory feed revision'):
                check.check_source(self.root)

    def test_manifest_feed_pin_must_match_public_build_lock(self):
        self.manifest['feed_app_airoha_revision'] = '0' * 40; self.save_manifest()
        with self.assertRaisesRegex(check.policy.RootfsError, 'differs from locked app_airoha'):
            check.check_source(self.root)

    def test_source_and_preimage_drift_rejected(self):
        name = 'factory-status'
        self.manifest['patches'][name]['source_sha256'] = '0' * 64; self.save_manifest()
        with self.assertRaisesRegex(check.policy.RootfsError, 'preimage differs'):
            check.check_source(self.root)
        self.manifest['patches'][name]['source_sha256'] = check.policy.digest(preimage(self.manifest['patches'][name]['target']))
        self.save_manifest()
        target = self.root / 'feeds/app_airoha' / self.manifest['patches'][name]['target']
        target.write_bytes(target.read_bytes() + b'\n# unreviewed\n')
        with self.assertRaisesRegex(check.policy.RootfsError, 'prepared feed differs'):
            check.check_source(self.root)

    def test_incomplete_patch_postimage_rejected(self):
        target = self.root / check.policy.FACTORY_PATCH
        target.write_text(re.sub(r'(@@ -1,\d+ \+1,)(\d+)( @@)',
                                lambda row: row[1] + str(int(row[2]) - 1) + row[3], target.read_text(), count=1))
        with self.assertRaisesRegex(check.policy.RootfsError, 'postimage length mismatch'):
            check.check_source(self.root)

    def test_minifier_output_must_match_complete_pinned_hash(self):
        tool = self.root / 'synthetic-jsmin'; tool.write_text('#!/bin/sh\nexit 0\n'); tool.chmod(0o700)
        output = b'synthetic official tool output\n'
        self.manifest['patches']['factory-info-view']['minified_sha256'] = check.policy.digest(output); self.save_manifest()
        with patch.object(check.subprocess, 'run', return_value=subprocess.CompletedProcess([str(tool)], 0, output, b'')):
            self.assertEqual(check.check_source(self.root, tool)['factory_minified_sha256'], check.policy.digest(output))
        with patch.object(check.subprocess, 'run', return_value=subprocess.CompletedProcess([str(tool)], 0, output + b'drift', b'')):
            with self.assertRaisesRegex(check.policy.RootfsError, 'jsmin output differs'):
                check.check_source(self.root, tool)


if __name__ == '__main__':
    unittest.main(verbosity=2)
