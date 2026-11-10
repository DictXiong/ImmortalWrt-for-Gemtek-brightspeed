#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('wrtix_source', Path(__file__).resolve().parents[1] / 'wrtix-source.py')
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class FeedPin(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        for name in ['feed/wrtix-agent/Makefile', 'agent/agent.sh', 'agent/guard.init', 'agent/contract.json']:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL)

    def test_snapshot_reproducible_and_modified_cache_rejected(self):
        pin, data = source.snapshot(self.repo)
        self.assertEqual((pin, data), source.snapshot(self.repo))
        dest = self.root / 'materialized'
        metadata = source.materialize(pin, data, dest)
        self.assertEqual(metadata, source.materialize(pin, data, dest))
        (dest / 'agent/agent.sh').write_text('tampered\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            source.materialize(pin, data, dest)

    def test_dirty_checkout_rejected(self):
        (self.repo / 'agent/agent.sh').write_text('uncommitted\n')
        with self.assertRaisesRegex(ValueError, 'clean'):
            source.snapshot(self.repo)

    def test_untracked_source_rejected(self):
        (self.repo / 'extra').write_text('untracked\n')
        with self.assertRaisesRegex(ValueError, 'clean'):
            source.snapshot(self.repo)

    def test_wrtmesh_source_requires_its_own_overview_module(self):
        overview = self.repo / 'feed/wrtmesh/files/65_wrtmesh.js'
        for path in (self.repo / 'VERSION', self.repo / 'feed/wrtmesh/Makefile',
                     self.repo / 'feed/wrtmesh/files/helper.sh', overview):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'wrtmesh fixture')
        dest = self.root / 'materialized'
        pin, data = source.snapshot(self.repo, 'wrtmesh')
        source.materialize(pin, data, dest, 'wrtmesh')
        self.assertEqual((dest / overview.relative_to(self.repo)).read_text(), 'fixture\n')
        self.git('rm', str(overview.relative_to(self.repo)))
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'remove required module')
        pin, data = source.snapshot(self.repo, 'wrtmesh')
        with self.assertRaisesRegex(ValueError, 'missing a required package input'):
            source.materialize(pin, data, dest, 'wrtmesh')


if __name__ == '__main__':
    unittest.main()
