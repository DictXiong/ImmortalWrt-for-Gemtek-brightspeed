#!/usr/bin/env python3
"""Synthetic acceptance fixtures; no AP access or real credentials."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ap_verify', HERE / 'ap-verify.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
from ap_config import resolve, render


def fixture():
    document = {'schema_version': 1, 'profile': 'dicttech-ap-v1', 'common': {},
                'devices': {'ap_example': {'hostname': 'ap-example', 'maintenance_ipv4': '192.168.250.3', 'mac_base': '02:aa:bb:10:00:00'}},
                'secrets': {'wifi': {role: role + '-fixture-password' for role in v.ROLES},
                            'devices': {'ap_example': {'root_password_hash': '$6$fixture$' + 'a' * 86}}}}
    state = resolve(document, 'ap_example'); files = render(state)
    contract = {'profile': state['profile'], 'defaults_sha256': state['defaults_sha256'], 'boot_id': 'fixture-boot',
                'files': {path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
                'modes': {path: ['-rw-------', '0'] for path in files}, 'root_password_synced': True, 'resolver_correct': True,
                'identity': {'device_id': state['device_id'], 'bridge_mac': state['device']['identity']['bridge'], 'board_name': 'gemtek,xr1710g'}}
    data = {'contract': contract, 'snapshot_stable': True, 'guest_addresses': [{'ifname': 'br-lan.192', 'addr_info': []}],
            'sysctls': '1\n1\n0\n0\n0\n0\n', 'vlans': [], 'security': '',
            'services': {name: {'instances': {'main': {'running': True}}} for name in ['network', 'sysntpd', 'dropbear', 'uhttpd', 'xr_roaming']},
            'service_links': '\n'.join('S20' + name for name in state['services']['enabled']),
            'boot_hooks': 'S03dicttech-ap-transaction\nS04dicttech-ap-prepare\nS13dicttech-ap-finalize\n',
            'fw4': {'nftables': [{'table': {'family': 'inet', 'name': 'fw4'}}]}}
    for name, proto, device in [('lan', 'dhcp', 'br-lan.1'), ('lan6', 'dhcpv6', 'br-lan.1'), ('maintenance', 'static', 'br-lan.1'), ('guest', 'none', 'br-lan.192')]:
        data[name] = {'up': True, 'proto': proto, 'l3_device': device, 'ipv4-address': [], 'ipv6-address': []}
    data['lan']['ipv4-address'] = [{'address': '10.23.0.3', 'mask': 24}]
    data['lan6']['ipv6-address'] = [{'address': 'fd00::3', 'mask': 64}]
    data['maintenance']['ipv4-address'] = [{'address': '192.168.250.3', 'mask': 24}]
    for port in state['platform']['ports']:
        data['vlans'].append({'ifname': port, 'vlans': [{'vlan': 1, 'flags': ['PVID', 'Egress Untagged']}, {'vlan': 192}]})
    data['vlans'].append({'ifname': 'br-lan', 'vlans': [{'vlan': 1}, {'vlan': 192}]})
    for role in v.ROLES:
        count = 1 if role == 'iot' else 3 if role == 'main' else 2
        links = [{'bssid': state['device']['identity']['bssids'][role][i], 'freq': [2437, 5220, 6135][i]} for i in range(count)]
        data['hostapd_' + role] = {'status': 'ENABLED', 'ssid': state['common']['ssids'][role]['name'], **(links[0] if role == 'iot' else {'links': links})}
        data['roaming_' + role] = {'state': 'ok', 'snapshot_fresh': True, 'peers': []}
        for i in range(count):
            data['security'] += ('interface=fixture\nssid=' + state['common']['ssids'][role]['name'] + '\nwpa_key_mgmt=' + ('WPA-PSK SAE' if role == 'iot' else 'SAE SAE-EXT-KEY') + '\nieee80211w=' + str(state['security'][role]['pmf']) + '\nrrm_neighbor_report=1\nrrm_beacon_report=1\nbss_transition=1\n')
    for index, code in enumerate([0, 2, 4]):
        data['radio_' + str(index)] = f'state=ENABLED\neht_oper_chwidth={code}\nsecondary_channel=0\n'
    bridge_expr = [{'match': {'op': '==', 'left': {'payload': {'protocol': 'vlan', 'field': 'id'}}, 'right': 192}},
                   {'match': {'op': '!=', 'left': {'payload': {'protocol': 'vlan', 'field': 'type'}}, 'right': '8021x'}}, {'drop': None}]
    inet_expr = [{'match': {'op': '==', 'left': {'meta': {'key': 'iifname'}}, 'right': 'br-lan.192'}}, {'drop': None}]
    for name, expr in [('bridge_guard', bridge_expr), ('inet_guard', inet_expr)]:
        family, table = ('bridge', 'xr_guest_guard') if name == 'bridge_guard' else ('inet', 'xr_guest_local')
        data[name] = {'nftables': [{'table': {'family': family, 'name': table}},
                                 {'chain': {'family': family, 'table': table, 'name': 'input', 'type': 'filter', 'hook': 'input', 'prio': -10, 'policy': 'accept'}},
                                 {'rule': {'family': family, 'table': table, 'chain': 'input', 'expr': expr}}]}
    return state, files, data


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.state, self.files, self.data = fixture()

    def run_report(self, external=None):
        return v.evaluate(self.state, self.files, self.data, external)

    def test_configuration_success_never_claims_client_or_hardware_pass(self):
        report, _ = self.run_report()
        self.assertEqual(report['status'], 'NOT_TESTED')
        self.assertTrue(all(t['status'] == 'PASS' for t in report['tests'] if t['id'] not in v.EXTERNAL + ('six_ghz_client_auth',)))
        self.assertTrue(all(t['status'] == 'NOT_TESTED' for t in report['tests'] if t['id'] in v.EXTERNAL))

    def test_valid_external_evidence_is_boot_device_and_full_config_bound(self):
        _, binding = self.run_report()
        evidence = {**binding, 'tests': {name: {'status': 'PASS', 'evidence': 'private-test-run-001'} for name in v.EXTERNAL + ('six_ghz_client_auth',)}}
        report, _ = self.run_report(evidence)
        self.assertEqual(report['status'], 'PASS')
        evidence['boot_id'] = 'old-boot'
        self.assertEqual(self.run_report(evidence)[0]['status'], 'FAIL')

    def test_missing_guard_or_eapol_exception_fails(self):
        self.data['bridge_guard']['nftables'][2]['rule']['expr'].pop(1)
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_guard_rule_must_belong_to_matching_live_input_hook(self):
        for key, value in [('chain', 'unused'), ('family', 'inet'), ('table', 'unrelated')]:
            self.state, self.files, self.data = fixture()
            self.data['bridge_guard']['nftables'][2]['rule'][key] = value
            report, _ = self.run_report()
            self.assertEqual(next(t['status'] for t in report['tests'] if t['id'] == 'guest_local_guards'), 'FAIL')
        self.state, self.files, self.data = fixture()
        self.data['inet_guard']['nftables'][1]['chain']['hook'] = 'forward'
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_collection_rechecks_boot_and_full_contract_without_exposing_hash(self):
        for field in ['boot_id', 'files']:
            final = copy.deepcopy(self.data['contract'])
            if field == 'boot_id':
                final[field] = 'different-boot'
            else:
                final[field]['etc/config/wireless'] = '0' * 64
            with patch.object(v.deploy, 'remote', side_effect=[self.data['contract'], final]) as call, patch.object(v, 'read_command', return_value={}):
                observed = v.collect(object(), self.state)
            self.assertEqual(call.call_count, 2)
            self.assertFalse(observed['snapshot_stable'])
            self.data['snapshot_stable'] = observed['snapshot_stable']
            report, _ = self.run_report()
            self.assertEqual(report['status'], 'FAIL')
            self.assertNotIn('0' * 64, v.json.dumps(report))
        with patch.object(v.deploy, 'remote', side_effect=[self.data['contract'], copy.deepcopy(self.data['contract'])]), patch.object(v, 'read_command', return_value={}):
            self.assertTrue(v.collect(object(), self.state)['snapshot_stable'])

    def test_unexpected_vlan_or_guest_local_address_fails(self):
        self.data['vlans'][0]['vlans'].append({'vlan': 20})
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')
        self.state, self.files, self.data = fixture()
        self.data['guest_addresses'][0]['addr_info'] = [{'family': 'inet6', 'local': 'fe80::3'}]
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_config_permission_drift_and_stale_roamer_fail(self):
        self.data['contract']['modes']['etc/config/wireless'] = ['-rw-r--r--', '0']
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

        self.state, self.files, self.data = fixture()
        self.data['roaming_main']['snapshot_fresh'] = False
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_ntp_client_must_be_enabled_and_running(self):
        self.data['services']['sysntpd']['instances']['main']['running'] = False
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_real_width_and_runtime_security_checked(self):
        self.data['radio_1'] = 'state=ENABLED\neht_oper_chwidth=1\nsecondary_channel=1\n'
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_c7_quoted_ssid2_matches_plaintext_utf8_and_literal_backslashes(self):
        values = ['DictTech', 'DictTech-IoT', '327Battalion', '含空格 WLAN', 'A"B', r'A\B', r'not\nnewline', '"quoted"']
        for value in values:
            self.assertEqual(v.parse_ssid2('"' + value + '"'), value)
            self.assertEqual(v.parse_ssid2(value.encode().hex()), value)
        # Actual C7 generated shape uses ssid2="DictTech", not hex-only.
        self.data['security'] = '\n'.join('ssid2="' + line[5:] + '"' if line.startswith('ssid=') else line for line in self.data['security'].splitlines())
        report, _ = self.run_report()
        self.assertTrue(all(t['status'] == 'PASS' for t in report['tests'] if t['id'].startswith('security_')))

    def test_ssid2_printf_is_explicitly_unsupported_and_hex_is_strict(self):
        for value in ['P"name\\x20suffix"', '"unterminated', '"name"extra', '41 42', 'a', 'zz', '""', '"' + '界' * 11 + '"']:
            with self.assertRaises(ValueError):
                v.parse_ssid2(value)
        blocks = v.security_blocks('interface=fixture\nssid2=P"name\\x20suffix"\n')
        self.assertIsNone(blocks[0]['ssid'])
        self.assertEqual(blocks[0]['ssid_parse_error'], 'unsupported_ssid_printf_encoding')

    def test_management_reachability_needs_independent_evidence(self):
        report, _ = self.run_report()
        self.assertEqual(next(t['status'] for t in report['tests'] if t['id'] == 'management_reachability'), 'NOT_TESTED')

    def test_persistent_boot_policy_links_are_required_without_daemon_pid(self):
        self.assertNotIn('dicttech-ap-prepare', self.data['services'])
        report, _ = self.run_report()
        self.assertEqual(next(t['status'] for t in report['tests'] if t['id'] == 'boot_policy_hooks'), 'PASS')
        self.data['boot_hooks'] = self.data['boot_hooks'].replace('S04dicttech-ap-prepare\n', '')
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')
        self.state, self.files, self.data = fixture()
        self.data['security'] = self.data['security'].replace('ieee80211w=2', 'ieee80211w=1')
        self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_report_never_prints_secret_values_or_secret_hashes(self):
        report, binding = self.run_report()
        encoded = v.json.dumps(report)
        self.assertNotIn('fixture-password', encoded)
        self.assertNotIn('$6$fixture$', encoded)
        self.assertNotIn(binding['desired_sha256'], encoded)


if __name__ == '__main__':
    unittest.main(verbosity=2)
