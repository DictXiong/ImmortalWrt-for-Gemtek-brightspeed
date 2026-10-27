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


def fixture(common=None):
    document = {'schema_version': 2, 'profile': 'dicttech-ap-v2', 'common': copy.deepcopy(common or {}),
                'devices': {'ap_example': {'hostname': 'ap-example', 'maintenance_ipv4': '192.168.250.3', 'mac_base': '02:aa:bb:10:00:00'}},
                'secrets': {'wifi': {role: role + '-fixture-password' for role in ('main', 'guest', 'iot')},
                            'devices': {'ap_example': {'root_password_hash': '$6$fixture$' + 'a' * 86}}}}
    state = resolve(document, 'ap_example'); files = render(state)
    contract = {'schema_version': state['schema_version'], 'profile': state['profile'], 'defaults_sha256': state['defaults_sha256'], 'boot_id': 'fixture-boot',
                'files': {path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
                'modes': {path: ['-rw-------', '0'] for path in files}, 'root_password_synced': True, 'resolver_correct': True,
                'identity': {'device_id': state['device_id'], 'bridge_mac': state['device']['identity']['bridge'], 'board_name': 'gemtek,xr1710g'}}
    data = {'contract': contract, 'snapshot_stable': True, 'guest_addresses': [{'ifname': 'br-lan.192', 'addr_info': []}],
            'sysctls': '1\n1\n0\n0\n0\n0\n', 'vlans': [], 'security': '',
            'services': {name: {'instances': {'main': {'running': True}}} for name in ['network', 'sysntpd', 'dropbear', 'uhttpd', 'xr_roaming']},
            'service_links': '\n'.join('S20' + name for name in state['services']['enabled']),
            'boot_hooks': 'S03dicttech-ap-transaction\nS04dicttech-ap-prepare\nS13dicttech-ap-finalize\n',
            'luci_features': {'hostapd': {'cli': True, '11be': True, 'sae': True}},
            'fw4': {'nftables': [{'table': {'family': 'inet', 'name': 'fw4'}}]}}
    for name, proto, device in [('lan', 'dhcp', 'br-lan.1'), ('lan6', 'dhcpv6', 'br-lan.1'), ('maintenance', 'static', 'br-lan.1'), ('guest', 'none', 'br-lan.192')]:
        data[name] = {'up': True, 'proto': proto, 'l3_device': device, 'ipv4-address': [], 'ipv6-address': []}
    data['lan']['ipv4-address'] = [{'address': '10.23.0.3', 'mask': 24}]
    data['lan6']['ipv6-address'] = [{'address': 'fd00::3', 'mask': 64}]
    data['maintenance']['ipv4-address'] = [{'address': '192.168.250.3', 'mask': 24}]
    for port in state['platform']['ports']:
        data['vlans'].append({'ifname': port, 'vlans': [{'vlan': 1, 'flags': ['PVID', 'Egress Untagged']}, {'vlan': 192}]})
    data['vlans'].append({'ifname': 'br-lan', 'vlans': [{'vlan': 1}, {'vlan': 192}]})
    data['radio_inventory'] = {}
    profiles = v.ssid_profiles(state)
    for band, (index, _) in v.BANDS.items():
        interfaces = [{'section': profile['section'], 'ifname': profile['iface']} for profile in profiles if band in profile['bands']]
        data['radio_inventory']['radio' + str(index)] = {'up': bool(interfaces), 'disabled': not interfaces, 'interfaces': interfaces}
    data['hostapd_objects'] = '\n'.join('hostapd.' + profile['iface'] for profile in profiles) + '\n'
    data['services']['xr_roaming']['instances'] = {}
    for profile in profiles:
        key = profile['key']
        links = [{'bssid': bssid, 'freq': v.frequency(state, band)} for bssid, band in zip(profile['bssids'], profile['bands'])]
        data['hostapd_' + key] = {'status': 'ENABLED', 'ssid': profile['name'], **({'links': links} if profile['mlo'] else links[0])}
        if profile['roaming']:
            data['roaming_' + key] = {'state': 'ok', 'snapshot_fresh': True, 'peers': []}
            data['services']['xr_roaming']['instances'][key] = {'running': True}
        data['vlans'].append({'ifname': profile['iface'], 'vlans': [{'vlan': 192 if profile['network'] == 'guest' else 1,
                                                                  'flags': ['PVID', 'Egress Untagged']}]})
        for bssid, link_id in zip(profile['bssids'], profile['link_ids']):
            modes = {'sae': 'SAE SAE-EXT-KEY', 'sae-mixed': 'WPA-PSK SAE', 'psk2': 'WPA-PSK WPA-PSK-SHA256'}[profile['encryption']]
            data['security'] += ('interface=' + profile['iface'] + '\nssid=' + profile['name'] + '\nbssid=' + bssid +
                                 '\nbridge=' + state['platform']['bridge'] +
                                 '\nwpa=2\nwpa_key_mgmt=' + modes + '\nieee80211w=' + str(profile['pmf']) +
                                 '\nignore_broadcast_ssid=' + ('1' if profile['hidden'] else '0') + '\nap_isolate=0\n')
            if profile['mlo']:
                data['security'] += 'mld_ap=1\nmld_link_id=' + str(link_id) + '\n'
            if profile['roaming']:
                data['security'] += 'rrm_neighbor_report=1\nrrm_beacon_report=1\nbss_transition=1\n'
    for index, (band, _) in v.radio_probes(state).items():
        radio = state['common']['radios']['radio' + str(index)]
        code = {20: 0, 40: 0, 80: 1, 160: 2, 320: 4}[int(radio['htmode'][3:])]
        secondary = '1' if radio['htmode'] == 'EHT40' else '0'
        data['radio_' + str(index)] = f'state=ENABLED\nchannel={radio["channel"]}\nfreq={v.frequency(state, band)}\neht_oper_chwidth={code}\nsecondary_channel={secondary}\n'
    bridge_expr = [{'match': {'op': '==', 'left': {'payload': {'protocol': 'vlan', 'field': 'id'}}, 'right': 192}},
                   {'match': {'op': '!=', 'left': {'payload': {'protocol': 'vlan', 'field': 'type'}}, 'right': '8021x'}}, {'drop': None}]
    inet_expr = [{'match': {'op': '==', 'left': {'meta': {'key': 'iifname'}}, 'right': 'br-lan.192'}}, {'drop': None}]
    for name, expr in [('bridge_guard', bridge_expr), ('inet_guard', inet_expr)]:
        family, table = ('bridge', 'xr_guest_guard') if name == 'bridge_guard' else ('inet', 'xr_guest_local')
        data[name] = {'nftables': [{'table': {'family': family, 'name': table}},
                                 {'chain': {'family': family, 'table': table, 'name': 'input', 'type': 'filter', 'hook': 'input', 'prio': -10, 'policy': 'accept'}},
                                 {'rule': {'family': family, 'table': table, 'chain': 'input', 'expr': expr}}]}
    return state, files, data


def custom_common():
    return {'ssids': {'main': {'six_ghz': False}}, 'extra_ssids': [
        {'id': 'battalion', 'name': '327Battalion'},
        {'id': 'upper', 'name': 'UpperBand', 'bands': ['5g', '6g']},
        {'id': 'printer', 'name': 'Printer', 'bands': ['5g'], 'mlo': False,
         'network': 'guest', 'encryption': 'psk2', 'hidden': True, 'roaming': False},
    ]}


def change_security(data, ssid, field, value):
    blocks = v.security_blocks(data['security'])
    for block in blocks:
        if block.get('ssid') == ssid:
            block[field] = value
    data['security'] = '\n'.join(field + '=' + value for block in blocks for field, value in block.items()) + '\n'


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

    def test_no_optional_ssid_is_required_or_reported(self):
        report, _ = self.run_report()
        self.assertEqual([profile['key'] for profile in v.ssid_profiles(self.state)], ['main', 'guest', 'iot'])
        self.assertEqual({test['id'] for test in report['tests'] if test['id'].startswith('wireless_') and test['id'] != 'wireless_inventory'},
                         {'wireless_main', 'wireless_guest', 'wireless_iot'})
        self.assertNotIn('legacy', v.json.dumps(report))

    def test_multiple_optional_mlo_and_single_band_profiles_are_all_verified(self):
        self.state, self.files, self.data = fixture(custom_common())
        report, _ = self.run_report()
        self.assertEqual(report['status'], 'NOT_TESTED')
        for profile in v.ssid_profiles(self.state):
            for category in ('wireless_', 'security_', 'roaming_'):
                self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == category + profile['key']), 'PASS')
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'width_radio2'), 'PASS')
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'six_ghz_client_auth'), 'NOT_TESTED')

    def test_collection_uses_dynamic_interfaces_and_any_profile_can_enable_6ghz(self):
        self.state, self.files, self.data = fixture(custom_common())
        captured = []
        def query(args, words, parse=True):
            captured.append(words)
            return {} if parse else ''
        with patch.object(v.deploy, 'remote', return_value=self.data['contract']), patch.object(v, 'read_command', side_effect=query):
            v.collect(object(), self.state)
        profiles = v.ssid_profiles(self.state)
        self.assertEqual({words[4] for words in captured if words[:4] == ['ubus', '-t', '5', 'call'] and words[-1] == 'get_status'},
                         {'hostapd.' + profile['iface'] for profile in profiles})
        self.assertEqual({words[-1] for words in captured if words[:2] == ['/usr/lib/xr-roaming/helper.sh', 'status']},
                         {profile['key'] for profile in profiles})
        upper = next(profile for profile in profiles if profile['key'] == 'upper')
        self.assertIn(['hostapd_cli', '-p', '/var/run/hostapd', '-i', upper['iface'] + '_link2', 'status'], captured)
        self.assertNotIn(['hostapd_cli', '-p', '/var/run/hostapd', '-i', upper['iface'] + '_link0', 'status'], captured)
        # With this profile alone, link numbering must remain physical1/2.
        self.assertEqual(v.radio_probes({'ssids': [upper]}),
                         {1: ('5g', upper['iface'] + '_link1'), 2: ('6g', upper['iface'] + '_link2')})

    def test_single_band_control_socket_and_no_main_6ghz_assumption(self):
        common = {'ssids': {'main': {'six_ghz': False}}, 'extra_ssids': [
            {'id': 'only6', 'name': 'Only6', 'bands': ['6g'], 'mlo': False}]}
        self.state, self.files, self.data = fixture(common)
        only = next(profile for profile in v.ssid_profiles(self.state) if profile['key'] == 'only6')
        self.assertEqual(v.radio_probes(self.state)[2], ('6g', only['iface']))
        report, _ = self.run_report()
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'wireless_only6'), 'PASS')
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'six_ghz_client_auth'), 'NOT_TESTED')
        self.data['radio_2'] = self.data['radio_2'].replace('freq=6135', 'freq=5220')
        self.assertEqual(next(test['status'] for test in self.run_report()[0]['tests'] if test['id'] == 'width_radio2'), 'FAIL')

    def test_missing_custom_profile_status_security_or_metadata_fails(self):
        for missing in ('wireless', 'security', 'roaming'):
            self.state, self.files, self.data = fixture(custom_common())
            if missing == 'wireless':
                del self.data['hostapd_upper']
            elif missing == 'security':
                self.data['security'] = self.data['security'].replace('ssid=UpperBand', 'ssid=Unregistered')
            else:
                del self.data['roaming_upper']
            report, _ = self.run_report()
            self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == missing + '_upper'), 'FAIL')

    def test_fake_key_management_pmf_hidden_network_or_link_identity_fails(self):
        cases = [('Printer', 'wpa_key_mgmt', 'SAE'), ('Printer', 'wpa_key_mgmt', 'WPA-PSK WPA-EAP'),
                 ('Printer', 'wpa', '1'), ('Printer', 'ieee80211w', '0'),
                 ('Printer', 'ignore_broadcast_ssid', '0'), ('Printer', 'bridge', 'br-foreign'),
                 ('Printer', 'ap_isolate', '1'), ('Printer', 'rrm_neighbor_report', '1'),
                 ('UpperBand', 'mld_ap', '0'), ('UpperBand', 'mld_link_id', '0'),
                 ('UpperBand', 'interface', 'ap-mld99'), ('UpperBand', 'bssid', '02:00:00:00:00:ff'),
                 ('DictTech', 'wpa_key_mgmt', 'WPA-PSK SAE'), ('DictTech-IoT', 'wpa_key_mgmt', 'SAE')]
        for name, field, value in cases:
            with self.subTest(ssid=name, field=field):
                self.state, self.files, self.data = fixture(custom_common())
                change_security(self.data, name, field, value)
                self.assertEqual(self.run_report()[0]['status'], 'FAIL')

    def test_dynamic_guest_ssid_must_have_only_vlan192_and_native_ssid_only_vlan1(self):
        for key, values in [('printer', [{'vlan': 1, 'flags': ['PVID', 'Egress Untagged']}]),
                            ('main', [{'vlan': 192, 'flags': ['PVID', 'Egress Untagged']}]),
                            ('printer', [{'vlan': 192, 'flags': ['PVID']}]),
                            ('printer', [{'vlan': 192, 'flags': ['PVID', 'Egress Untagged']}, {'vlan': 1}])]:
            self.state, self.files, self.data = fixture(custom_common())
            profile = next(profile for profile in v.ssid_profiles(self.state) if profile['key'] == key)
            next(row for row in self.data['vlans'] if row['ifname'] == profile['iface'])['vlans'] = values
            report, _ = self.run_report()
            self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'vlan_ssid_' + key), 'FAIL')

    def test_removed_ssid_or_live_unused_radio_cannot_hide_outside_expected_queries(self):
        for kind in ('old_netifd_iface', 'old_hostapd_bss', 'missing_iface', 'radio2_still_up', 'missing_disabled'):
            self.state, self.files, self.data = fixture({'ssids': {'main': {'six_ghz': False}}})
            if kind == 'old_netifd_iface':
                self.data['radio_inventory']['radio0']['interfaces'].append({'section': 'default_radio1', 'ifname': 'ap-mld9'})
            elif kind == 'old_hostapd_bss':
                self.data['hostapd_objects'] += 'hostapd.ap-mld9\n'
            elif kind == 'missing_iface':
                self.data['radio_inventory']['radio1']['interfaces'].pop()
            elif kind == 'radio2_still_up':
                self.data['radio_inventory']['radio2']['up'] = True
            else:
                del self.data['radio_inventory']['radio2']['disabled']
            report, _ = self.run_report()
            self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'wireless_inventory'), 'FAIL')

    def test_no_6ghz_profile_means_no_6ghz_external_gate(self):
        self.state, self.files, self.data = fixture({'ssids': {'main': {'six_ghz': False}}})
        self.assertNotIn(2, v.radio_probes(self.state))
        self.assertNotIn('radio_2', self.data)
        report, binding = self.run_report()
        self.assertNotIn('six_ghz_client_auth', {test['id'] for test in report['tests']})
        evidence = {**binding, 'tests': {name: {'status': 'PASS', 'evidence': 'private-test-run-001'} for name in v.EXTERNAL}}
        self.assertEqual(self.run_report(evidence)[0]['status'], 'PASS')

    def test_wrong_physical_frequency_or_duplicated_mlo_link_fails(self):
        for kind in ('wrong_frequency', 'duplicate', 'missing', 'malformed'):
            self.state, self.files, self.data = fixture(custom_common())
            links = self.data['hostapd_upper']['links']
            if kind == 'wrong_frequency': links[0]['freq'] = 2437
            elif kind == 'duplicate': links[1] = copy.deepcopy(links[0])
            elif kind == 'missing': links.pop()
            else: self.data['hostapd_upper']['links'] = [None]
            report, _ = self.run_report()
            self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'wireless_upper'), 'FAIL')

    def test_disabled_roaming_has_no_workers_and_all_disabled_is_valid(self):
        self.state, self.files, self.data = fixture({'roaming': {'enabled': False}})
        report, _ = self.run_report()
        self.assertTrue(all(test['status'] == 'PASS' for test in report['tests'] if test['id'].startswith('roaming_') or test['id'] == 'required_services'))
        self.state, self.files, self.data = fixture(custom_common())
        self.data['services']['xr_roaming']['instances']['printer'] = {'running': True}
        report, _ = self.run_report()
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'roaming_printer'), 'FAIL')
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'required_services'), 'FAIL')

    def test_unconfigured_or_missing_roaming_worker_fails(self):
        for name, action in [('old_legacy', 'add'), ('main', 'remove')]:
            self.state, self.files, self.data = fixture()
            instances = self.data['services']['xr_roaming']['instances']
            if action == 'add': instances[name] = {'running': True}
            else: del instances[name]
            report, _ = self.run_report()
            self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'required_services'), 'FAIL')

    def test_v1_contract_and_external_evidence_for_different_ssid_set_are_refused(self):
        self.data['contract']['schema_version'] = 1
        report, _ = self.run_report()
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'target_identity'), 'FAIL')
        self.state, self.files, self.data = fixture()
        _, binding = self.run_report()
        evidence = {**binding, 'tests': {name: {'status': 'PASS', 'evidence': 'private-test-run-001'} for name in v.EXTERNAL + ('six_ghz_client_auth',)}}
        self.state, self.files, self.data = fixture(custom_common())
        report, _ = self.run_report(evidence)
        self.assertEqual(next(test['status'] for test in report['tests'] if test['id'] == 'external_report_binding'), 'FAIL')

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

    def test_luci_rpc_absence_malformed_reply_or_missing_wifi_features_fails(self):
        for value in [None, [], {}, {'hostapd': None}, {'hostapd': {'cli': True, '11be': True, 'sae': False}}]:
            self.data['luci_features'] = value
            report, _ = self.run_report()
            self.assertEqual(next(t['status'] for t in report['tests'] if t['id'] == 'luci_rpc_features'), 'FAIL')
        del self.data['luci_features']
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
