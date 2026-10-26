#!/usr/bin/env python3
"""Local synthetic schema/render tests. No real credentials or device calls."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('ap_config.py')
spec = importlib.util.spec_from_file_location('ap_config', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HASH = '$6$fixturesalt$' + 'a' * 86


def document(count=2):
    nodes = {f'ap_{i}': {'hostname': f'ap-{i}', 'maintenance_ipv4': f'192.168.250.{i + 1}',
                         'mac_base': f'02:aa:bb:10:{i:02x}:00'} for i in range(count)}
    return {'schema_version': 1, 'profile': 'dicttech-ap-v1', 'common': {}, 'devices': nodes,
            'secrets': {'wifi': {role: role + '-fixture-password' for role in m.ROLES},
                        'devices': {node: {'root_password_hash': HASH} for node in nodes}}}


def parse_uci(data):
    result, current = [], None
    for line in data.decode().splitlines():
        value = shlex.split(line)
        if not value:
            continue
        if value[0] == 'config':
            current = {'type': value[1], 'name': value[2] if len(value) == 3 else None, 'options': {}, 'lists': {}}
            result.append(current)
        elif value[0] == 'option':
            if len(value) != 3 or value[1] in current['options']:
                raise AssertionError('malformed/duplicate UCI option')
            current['options'][value[1]] = value[2]
        elif value[0] == 'list':
            if len(value) != 3:
                raise AssertionError('malformed UCI list')
            current['lists'].setdefault(value[1], []).append(value[2])
        else:
            raise AssertionError('unexpected UCI directive')
    return result


class Configuration(unittest.TestCase):
    def state(self, value=None, node='ap_0'):
        return m.resolve(document() if value is None else value, node)

    def reject(self, fn):
        with self.assertRaises(m.ConfigError):
            fn()

    def test_defaults_are_delta_only_and_deterministic(self):
        a = self.state()
        self.assertEqual(a['common']['country'], 'US')
        self.assertTrue(a['common']['ssids']['main']['six_ghz'])
        self.assertEqual(m.render(a), m.render(self.state()))

    def test_four_port_vlan_dhcp_and_maintenance(self):
        state = self.state(); files = m.render(state)
        rows = {r['name']: r for r in parse_uci(files['etc/config/network'])}
        self.assertEqual(rows['bridge']['lists']['ports'], ['wan', 'lan2', 'lan3', 'lan4'])
        self.assertEqual(rows['native_vlan']['lists']['ports'], [p + ':u*' for p in ['wan', 'lan2', 'lan3', 'lan4']])
        self.assertEqual(rows['guest_vlan']['lists']['ports'], [p + ':t' for p in ['wan', 'lan2', 'lan3', 'lan4']])
        self.assertEqual(rows['lan']['options']['proto'], 'dhcp')
        self.assertEqual(rows['lan']['options']['device'], 'br-lan.1')
        self.assertEqual(rows['ap_maintenance']['options']['ipaddr'], '192.168.250.1/24')
        self.assertNotIn('gateway', rows['ap_maintenance']['options'])
        self.assertEqual(rows['guest']['options']['proto'], 'none')
        self.assertEqual(rows['guest_device']['options']['ipv6'], '0')
        self.assertNotIn('wan', rows)
        self.assertNotIn('lab_fallback', rows)

    def test_security_bands_order_and_client_interoperation(self):
        rows = parse_uci(m.render(self.state())['etc/config/wireless'])
        vifs = [r for r in rows if r['type'] == 'wifi-iface']
        self.assertEqual([r['name'] for r in vifs], ['default_radio1', 'wifinet1', 'dicttech_mlo', 'guest_mlo'])
        by_name = {r['name']: r for r in vifs}
        self.assertEqual(by_name['dicttech_mlo']['lists']['device'], ['radio0', 'radio1', 'radio2'])
        self.assertEqual(by_name['guest_mlo']['lists']['device'], ['radio0', 'radio1'])
        self.assertEqual(by_name['guest_mlo']['options']['isolate'], '0')
        self.assertEqual(by_name['wifinet1']['options']['device'], 'radio0')
        self.assertEqual(by_name['wifinet1']['options']['encryption'], 'sae-mixed')
        self.assertEqual(by_name['wifinet1']['options']['ieee80211w'], '1')
        for vif in vifs:
            self.assertEqual(vif['options']['ieee80211r'], '0')
            self.assertEqual(vif['options']['ieee80211k'], '1')
            self.assertEqual(vif['options']['bss_transition'], '1')
        radios = {r['name']: r for r in rows if r['type'] == 'wifi-device'}
        self.assertEqual(radios['radio0']['options']['noscan'], '0')
        self.assertNotIn('noscan', radios['radio1']['options'])

    def test_no_nat_no_dhcp_no_guest_ip_service(self):
        files = m.render(self.state())
        fw = parse_uci(files['etc/config/firewall'])
        self.assertFalse(any('masq' in row['options'] for row in fw))
        self.assertFalse(any(row['type'] == 'forwarding' for row in fw))
        guest = next(r for r in fw if r['name'] == 'guest')
        self.assertEqual(guest['options']['input'], 'DROP')
        self.assertEqual(guest['options']['forward'], 'DROP')
        dhcp = parse_uci(files['etc/config/dhcp'])
        self.assertEqual(dhcp[0]['options']['port'], '0')
        for row in dhcp:
            if row['type'] == 'dhcp':
                for field in ['dhcpv4', 'dhcpv6', 'ra', 'ndp']:
                    self.assertEqual(row['options'][field], 'disabled')
        self.assertIn(b'vlan id 192 vlan type != 0x888e', files['etc/xr-guest-vlan192.nft'])
        self.assertIn(b'bridge-nf-filter-vlan-tagged=0', files['etc/sysctl.d/13-nf-bridge.conf'])

    def test_one_node_and_many_peers(self):
        one = self.state(document(1))
        self.assertTrue(all(not peers for peers in one['peers'].values()))
        state = self.state(document(4))
        self.assertTrue(all(len(peers) == 3 for peers in state['peers'].values()))
        rows = parse_uci(m.render(state)['etc/config/xr_roaming'])
        self.assertEqual(len([r for r in rows if r['type'] == 'peer']), 3)
        main = next(r for r in rows if r['name'] == 'main')
        self.assertEqual(main['options']['peer_mode'], 'fleet')
        self.assertEqual(main['lists']['peers'], ['ap_1', 'ap_2', 'ap_3'])

    def test_different_legacy_names_excluded(self):
        value = document()
        value['devices']['ap_1']['legacy_ssid'] = 'DifferentLegacy'
        state = self.state(value)
        self.assertEqual(state['peers']['battalion'], [])
        self.assertEqual(len(state['peers']['main']), 1)

    def test_main_dual_band_peer_and_cn(self):
        value = document()
        value['devices']['ap_1']['overrides'] = {'ssids': {'main': {'six_ghz': False}}, 'country': 'CN'}
        state = self.state(value)
        self.assertEqual(len(state['peers']['main'][0]['bssids']), 2)
        other = self.state(value, 'ap_1')
        rows = {r['name']: r for r in parse_uci(m.render(other)['etc/config/wireless'])}
        self.assertEqual(rows['radio2']['options']['disabled'], '1')
        self.assertEqual(rows['dicttech_mlo']['lists']['device'], ['radio0', 'radio1'])
        value['devices']['ap_1']['overrides']['ssids']['main']['six_ghz'] = True
        self.reject(lambda: self.state(value))

    def test_mac_overrides_and_mld_first_link_alias(self):
        value = document()
        first = self.state(value)['device']['identity']['bssids']['main'][0]
        value['devices']['ap_0']['identity'] = {'mlds': {'main': first}}
        self.assertEqual(self.state(value)['device']['identity']['mlds']['main'], first)
        value['devices']['ap_0']['identity']['mlds']['guest'] = first
        self.reject(lambda: self.state(value))

    def test_fleet_collisions(self):
        for field in ['hostname', 'maintenance_ipv4', 'mac_base']:
            value = document(); value['devices']['ap_1'][field] = value['devices']['ap_0'][field]
            self.reject(lambda: self.state(value))
        value = document(); value['devices']['ap_1']['hostname'] = value['devices']['ap_0']['hostname'].upper()
        self.reject(lambda: self.state(value))
        value = document(); value['devices']['ap_1']['identity'] = {'duid': self.state(value)['device']['identity']['duid']}
        self.reject(lambda: self.state(value))

    def test_reserved_and_malformed_addresses_and_macs(self):
        for address in ['192.168.250.0', '192.168.250.100', '192.168.250.254', '192.168.250.1/24', '192.168.68.10']:
            value = document(); value['devices']['ap_0']['maintenance_ipv4'] = address
            self.reject(lambda: self.state(value))
        for address in ['01:aa:bb:00:00:00', '00:00:00:00:00:00', 'bad']:
            value = document(); value['devices']['ap_0']['mac_base'] = address
            self.reject(lambda: self.state(value))

    def test_secret_privacy_and_shadow_identity(self):
        state = self.state(); files = m.render(state)
        marker = files['etc/dicttech-ap/device.json']
        for password in state['credentials']['wifi'].values():
            self.assertNotIn(password.encode(), marker)
        self.assertNotIn(HASH.encode(), marker)
        self.assertNotIn(b'credentials', marker)
        self.assertEqual(files['etc/dicttech-ap/root-password-hash'], (HASH + '\n').encode())
        self.assertNotIn('etc/shadow', files)
        self.assertNotIn('etc/dropbear/authorized_keys', files)
        self.assertFalse(any('host_key' in name for name in files))
        manifest = json.loads(files['etc/dicttech-ap/managed-files.json'])
        actual = {row['path']: row['sha256'] for row in manifest['files']}
        self.assertEqual(actual, {name: hashlib.sha256(data).hexdigest() for name, data in files.items()
                                  if name != 'etc/dicttech-ap/managed-files.json'})
        self.assertNotIn('etc/dicttech-ap/managed-files.json', actual)

    def test_explicit_authorized_keys_and_empty_preserve_semantics(self):
        value = document(); secret = value['secrets']['devices']['ap_0']
        secret['authorized_keys'] = []
        self.assertEqual(m.render(self.state(value))['etc/dropbear/authorized_keys'], b'')
        kind = b'ssh-ed25519'
        wire = len(kind).to_bytes(4, 'big') + kind + (32).to_bytes(4, 'big') + b'x' * 32
        key = 'ssh-ed25519 ' + base64.b64encode(wire).decode() + ' fixture'
        secret['authorized_keys'] = [key]
        self.assertEqual(m.render(self.state(value))['etc/dropbear/authorized_keys'], (key + '\n').encode())
        secret['authorized_keys'] = ['ssh-ed25519 ' + base64.b64encode(b'x' * 60).decode()]
        self.reject(lambda: self.state(value))

    def test_utf8_limits_and_shell_metacharacter_roundtrip(self):
        value = document()
        value['common'] = {'ssids': {'main': {'name': "A' $(touch marker) \\"}}}
        state = self.state(value)
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
        self.assertEqual(rows['dicttech_mlo']['options']['ssid'], value['common']['ssids']['main']['name'])
        value['common']['ssids']['main']['name'] = '界' * 11
        self.reject(lambda: self.state(value))
        value['common']['ssids']['main']['name'] = '\ud800'
        self.reject(lambda: self.state(value))
        value['common']['ssids']['main']['name'] = 'newline\nSSID'
        self.reject(lambda: self.state(value))

    def test_unknown_fields_versions_and_no_raw_uci(self):
        for key in ['wireless', 'uci', 'shell', 'enabled']:
            value = document(); value['common'][key] = 'bad'
            self.reject(lambda: self.state(value))
        value = document(); value['schema_version'] = True
        self.reject(lambda: self.state(value))
        self.reject(lambda: self.state(document(17)))
        value = document(); value['devices']['main'] = value['devices'].pop('ap_0')
        value['secrets']['devices']['main'] = value['secrets']['devices'].pop('ap_0')
        self.reject(lambda: self.state(value))
        value = document(); value['profile'] = 'new-unknown'
        self.reject(lambda: self.state(value))
        value = document(); value['devices']['bad-id'] = value['devices'].pop('ap_0')
        value['secrets']['devices']['bad-id'] = value['secrets']['devices'].pop('ap_0')
        self.reject(lambda: self.state(value))

    def test_bad_secret_constraints_without_value_disclosure(self):
        for password in ['short', 'x' * 64, 'line\n' + 'x' * 12]:
            value = document(); value['secrets']['wifi']['main'] = password
            with self.assertRaises(m.ConfigError) as caught:
                self.state(value)
            self.assertNotIn(password, str(caught.exception))
        for password_hash in ['', 'root', '$6$bad$short', '!' + HASH]:
            value = document(); value['secrets']['devices']['ap_0']['root_password_hash'] = password_hash
            self.reject(lambda: self.state(value))

    def test_invalid_radio_and_ttl_constraints(self):
        for common in [{'radios': {'radio1': {'channel': 100}}}, {'radios': {'radio2': {'channel': 2}}},
                       {'radios': {'radio1': {'force_40': True}}}, {'radios': {'radio0': {'htmode': 'EHT20', 'force_40': True}}},
                       {'roaming': {'interval': 300, 'ttl': 180}}, {'ssids': {'legacy': {'enabled': False}}}]:
            value = document(); value['common'] = common
            self.reject(lambda: self.state(value))

    def test_local_merge_mode_duplicate_keys_and_private_render(self):
        value = document()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, data in [('common', {k: value[k] for k in ['schema_version', 'profile', 'common']}),
                               ('devices', {'devices': value['devices']}), ('secrets', {'secrets': value['secrets']})]:
                (root / name).write_text(json.dumps(data)); (root / name).chmod(0o600)
            merged = m.merge_files(root / 'common', root / 'devices', root / 'secrets')
            m.write_private(root / 'combined', m.canonical(merged))
            self.assertEqual(m.load_document(root / 'combined'), value)
            self.assertEqual((root / 'combined').stat().st_mode & 0o777, 0o600)
            (root / 'combined').chmod(0o644)
            self.reject(lambda: m.load_document(root / 'combined'))
            (root / 'combined').write_text('{"schema_version":1,"schema_version":1}'); (root / 'combined').chmod(0o600)
            self.reject(lambda: m.load_document(root / 'combined'))
            (root / 'combined').write_bytes(m.canonical(value))
            out = root / 'render'
            q = subprocess.run(['python3', str(SOURCE), 'render', '--config', str(root / 'combined'), '--device', 'ap_0', '--output', str(out)], capture_output=True, text=True)
            self.assertEqual(q.returncode, 0, q.stderr)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            for path in out.rglob('*'):
                if path.is_file():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('fixture-password', q.stdout)
            q2 = subprocess.run(['python3', str(SOURCE), 'render', '--config', str(root / 'combined'), '--device', 'ap_0', '--output', str(out)], capture_output=True)
            self.assertNotEqual(q2.returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
