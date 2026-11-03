#!/usr/bin/env python3
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
# SPDX-License-Identifier: GPL-2.0-only
"""Full shell login boundaries and package installation, without host changes."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'package/dicttech/xr1710g-cli'


class FullCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='full-cli-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.zsh = self.root / 'zsh'
        self.marker = self.root / 'entered'
        self.zsh.write_text('#!/bin/sh\n[ "$1" = -f ] && exit 0\nprintf "%s" "$*" > ' + str(self.marker) + '\n')
        self.zsh.chmod(0o755)
        self.hook = self.root / 'login.sh'
        self.hook.write_text((PACKAGE / 'files/etc/profile.d/80-xr1710g-cli.sh').read_text().replace('/usr/bin/zsh', str(self.zsh)))

    def login(self, *, interactive=True, tty=True, uid=0, failsafe=False, zsh=False, name='sh'):
        master, slave = os.openpty()
        try:
            command = 'id() { echo ' + str(uid) + '; }; . ' + str(self.hook)
            env = dict(os.environ)
            for key in ('FAILSAFE', 'ZSH_VERSION'): env.pop(key, None)
            if failsafe: env['FAILSAFE'] = '1'
            if zsh: env['ZSH_VERSION'] = '5'
            result = subprocess.run(['/bin/sh', '-ic' if interactive else '-c', command, name],
                stdin=slave if tty else subprocess.DEVNULL, stdout=slave if tty else subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            os.close(master); os.close(slave)
        return self.marker.exists()

    def test_root_terminal_enters_zsh_without_changing_account_shell(self):
        self.assertTrue(self.login())
        self.assertEqual(self.marker.read_text(), '-l')
        text = (PACKAGE / 'Makefile').read_text()
        self.assertNotIn('postinst', text)
        self.assertNotIn('/etc/passwd', text)

    def test_commands_failsafe_and_other_users_keep_original_shell(self):
        for args in ({'interactive': False}, {'tty': False}, {'uid': 1000},
                     {'failsafe': True}, {'zsh': True}, {'name': 'bash'}):
            with self.subTest(args=args): self.assertFalse(self.login(**args))
        self.zsh.write_text('#!/bin/sh\nexit 1\n')
        self.assertFalse(self.login())

    def test_install_is_full_only_and_has_no_authentication_assets(self):
        text = (PACKAGE / 'Makefile').read_text()
        recipe = re.search(r'define Package/xr1710g-cli/install\n(.*?)\nendef', text, re.S).group(1)
        stage = self.root / 'stage'
        make = self.root / 'install.mk'
        make.write_text('INSTALL_DIR=mkdir -p\nCP=cp -fpR\nINSTALL_DATA=install -m0644\nall:\n' + recipe.replace('$(1)', str(stage)) + '\n')
        subprocess.run(['make', '-f', str(make)], cwd=PACKAGE, check=True, capture_output=True)
        files = {str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()}
        self.assertEqual(len(files), 11)
        self.assertIn('usr/share/xr1710g-cli/.zshrc', files)
        self.assertIn('etc/profile.d/80-xr1710g-cli.sh', files)
        for path in files:
            self.assertNotIn('authorized_keys', path)
            value = (stage / path).read_text()
            self.assertNotRegex(value, r'(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+) [A-Za-z0-9+/]{20}')
            self.assertNotIn('PRIVATE KEY-----', value)
            self.assertEqual((stage / path).stat().st_mode & 0o7777, 0o644)
        sources = json.loads((stage / 'usr/share/xr1710g-cli/sources.json').read_text())
        for item in sources['sources']:
            self.assertEqual(hashlib.sha256((stage / 'usr/share/xr1710g-cli' / item['installed_relative_path']).read_bytes()).hexdigest(), item['sha256'])
        self.assertIn('CONFIG_PACKAGE_xr1710g-cli=y', (ROOT / 'configs/xr1710g-full.config').read_text())
        for config in (ROOT / 'configs').glob('*fixes*.config'):
            self.assertNotIn('CONFIG_PACKAGE_xr1710g-cli=y', config.read_text())


if __name__ == '__main__': unittest.main()
