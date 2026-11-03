#!/usr/bin/env python3
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
# SPDX-License-Identifier: GPL-2.0-only
"""Public defaults preserve choices; fixtures never touch host network state."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
BOARD = ROOT / 'package/network/config/airoha-an7581-mt7996-board/files/etc'
FIREWALL = ROOT / 'target/linux/airoha/an7581/base-files/etc/uci-defaults/18-xr1710g-firewall-defaults'
MIGRATE = ROOT / 'target/linux/airoha/an7581/base-files/etc/uci-defaults/22_airoha-network-migrate-v4'


class FullDefaults(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='full-defaults-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        (self.root / 'system.sh').write_text('board_name() { echo gemtek,xr1710g; }\n')
        self.state = self.root / 'state.json'
        self.state.write_text('{}')
        tool = self.root / 'bin/uci'
        tool.write_text('#!' + os.sys.executable + '\n' + '''import json,pathlib,sys
p=pathlib.Path(''' + repr(str(self.state)) + '''); state=json.loads(p.read_text())
args=[a for a in sys.argv[1:] if a!='-q']
if args[0]=='get':
 if args[1] not in state: sys.exit(1)
 print(state[args[1]])
elif args[0]=='set':
 key,value=args[1].split('=',1); state[key]=value; p.write_text(json.dumps(state))
elif args[0]!='commit': sys.exit(5)
''')
        tool.chmod(0o755)

    def run_script(self, source, replacements=()):
        text = source.read_text().replace('/lib/functions/system.sh', str(self.root / 'system.sh'))
        for old, new in replacements: text = text.replace(old, str(new))
        script = self.root / 'script.sh'; script.write_text(text)
        result = subprocess.run(['/bin/sh', str(script)], env={**os.environ, 'PATH':str(self.root / 'bin') + ':' + os.environ['PATH']}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_firewall_offload_choices_survive(self):
        before = {'firewall.@defaults[0].flow_offloading':'0', 'firewall.@defaults[0].flow_offloading_hw':'0'}
        self.state.write_text(json.dumps(before)); self.run_script(FIREWALL)
        self.assertEqual(json.loads(self.state.read_text()), before)

    def test_only_missing_firewall_defaults_are_filled(self):
        self.state.write_text(json.dumps({'firewall.@defaults[0].flow_offloading':'0'}))
        self.run_script(FIREWALL)
        self.assertEqual(json.loads(self.state.read_text()), {'firewall.@defaults[0].flow_offloading':'0', 'firewall.@defaults[0].flow_offloading_hw':'1'})
        self.assertNotIn('/etc/init.d/', FIREWALL.read_text())

    def test_full_marker_skips_upstream_wifi_rewrites(self):
        marker = self.root / 'full'; marker.write_text('XR1710G Full\n')
        # The absent upstream helper libraries make any continuation fail.
        for source in (BOARD / 'board.d/03_wifi_defaults', BOARD / 'uci-defaults/03_wireless'):
            self.run_script(source, [('/etc/xr1710g-full', marker)])
        self.assertEqual(self.state.read_text(), '{}')

    def test_registered_network_is_not_reinterpreted(self):
        marker = self.root / 'identity.json'; marker.write_text('{}')
        self.run_script(MIGRATE, [('/etc/wrtix/identity.json', marker)])
        self.assertEqual(self.state.read_text(), '{}')

    def test_full_selection_has_routing_stack_and_no_personal_packages(self):
        spec=importlib.util.spec_from_file_location('full_policy', ROOT / 'scripts/dicttech/check-build.py')
        policy=importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
        result=policy.config(ROOT / 'configs/xr1710g-full.config', 'legacy')
        required=set(result['required_packages'])
        self.assertTrue({'dnsmasq','odhcpd-ipv6only','ppp','ppp-mod-pppoe','luci-proto-ppp'} <= required)
        self.assertTrue({'cli-profile','dicttech-setup','ap-profile'} <= policy.FORBIDDEN)


if __name__ == '__main__': unittest.main()
