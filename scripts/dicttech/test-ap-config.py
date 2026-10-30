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
    return {'schema_version': 2, 'profile': 'dicttech-ap-v2', 'common': {}, 'devices': nodes,
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

    def complete_identity(self):
        address = lambda tail: 'f2:a5:44:14:' + tail
        return {'complete': True, 'bridge': address('00:00'),
                'ethernet': {key: address(f'00:{slot:02x}') for key, slot in
                             [('wan', 16), ('lan2', 17), ('lan3', 18), ('lan4', 19), ('cpu', 31)]},
                'radios': {f'radio{i}': address(f'00:{128 + i * 16:02x}') for i in range(3)},
                'mlds': {role: address(f'02:{i * 4:02x}') for i, role in enumerate(m.ROLES)},
                'bssids': {role: [address(f'02:{i * 4 + band:02x}') for band in (1, 2, 3)]
                           for i, role in enumerate(m.ROLES)},
                'duid': '00030001f2a544140000'}

    def test_complete_producer_table_is_rendered_without_legacy_allocation(self):
        value = document(1)
        identity = self.complete_identity()
        value['devices']['ap_0']['identity'] = identity
        original = copy.deepcopy(value)
        state = self.state(value)
        self.assertEqual(state['device']['identity'], identity)
        self.assertEqual(value, original)
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
        self.assertEqual(rows['ssid_main']['options']['macaddr'], identity['mlds']['main'])
        self.assertEqual(rows['ssid_iot']['options']['macaddr'], identity['bssids']['iot'][0])
        self.assertEqual(state['device']['mac_base'], original['devices']['ap_0']['mac_base'])

    def test_complete_table_rejects_missing_slots_collisions_and_hardware_mismatch(self):
        for change in ('missing', 'collision', 'cpu-prefix', 'radio-collision'):
            with self.subTest(change=change):
                value = document(1)
                identity = self.complete_identity()
                value['devices']['ap_0']['identity'] = identity
                if change == 'missing':
                    del identity['mlds']['iot']
                elif change == 'collision':
                    identity['bssids']['guest'][2] = identity['bssids']['main'][0]
                elif change == 'cpu-prefix':
                    identity['ethernet']['cpu'] = '00:58:28:44:14:1f'
                else:
                    identity['radios']['radio0'] = identity['bridge']
                self.reject(lambda: self.state(value))

    def test_complete_table_preserves_unused_custom_reservations(self):
        value = document(1)
        identity = self.complete_identity()
        identity['mlds']['spare'] = 'f2:a5:44:14:02:0c'
        identity['bssids']['spare'] = ['f2:a5:44:14:02:0d', 'f2:a5:44:14:02:0e', 'f2:a5:44:14:02:0f']
        value['devices']['ap_0']['identity'] = identity
        self.assertEqual(self.state(value)['device']['identity'], identity)
        value['common']['extra_ssids'] = [{'id': 'spare', 'name': 'Spare', 'bands': ['5g']}]
        profile = next(p for p in self.state(value)['ssids'] if p['key'] == 'spare')
        self.assertEqual(profile['bssids'], [identity['bssids']['spare'][1]])

    def test_defaults_are_delta_only_and_deterministic(self):
        a = self.state()
        self.assertEqual(a['common']['country'], 'US')
        self.assertTrue(a['common']['ssids']['main']['six_ghz'])
        self.assertEqual(m.render(a), m.render(self.state()))
        self.assertEqual([p['key'] for p in m.ssid_profiles(a)], ['main', 'guest', 'iot'])
        self.assertEqual(a['common']['extra_ssids'], [])
        self.assertNotIn('security', a)
        self.assertEqual(a['common']['ssids']['main']['encryption'], 'sae')

    def test_preset_security_lives_with_ssid_and_derives_pmf(self):
        for encryption, pmf in [('sae', 2), ('sae-mixed', 1), ('psk2', 1)]:
            value = document()
            value['common']['ssids'] = {'iot': {'encryption': encryption}}
            state = self.state(value)
            profile = next(p for p in state['ssids'] if p['key'] == 'iot')
            self.assertEqual((profile['encryption'], profile['pmf']), (encryption, pmf))
            rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
            self.assertEqual(rows['ssid_iot']['options']['encryption'], encryption)
            self.assertEqual(rows['ssid_iot']['options']['ieee80211w'], str(pmf))
        for role in ['main', 'guest']:
            for encryption in ['psk2', 'sae-mixed', 'none']:
                value = document()
                value['common']['ssids'] = {role: {'encryption': encryption}}
                self.reject(lambda: self.state(value))

    def test_per_ssid_eht_preserves_other_bss_and_identity(self):
        value = document()
        value['common']['extra_ssids'] = [{'id': 'printer', 'name': 'Printer', 'bands': ['5g'], 'encryption': 'psk2'}]
        baseline = self.state(value)
        value['common']['ssids'] = {'iot': {'eht': False}}
        value['common']['extra_ssids'][0]['eht'] = False
        state = self.state(value)
        profiles = {p['key']: p for p in state['ssids']}
        self.assertFalse(profiles['iot']['eht'])
        self.assertFalse(profiles['printer']['eht'])
        self.assertTrue(profiles['main']['eht'])
        self.assertTrue(profiles['guest']['eht'])
        self.assertEqual(state['device']['identity'], baseline['device']['identity'])
        before = {r['name']: r for r in parse_uci(m.render(baseline)['etc/config/wireless'])}
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
        for section in ('ssid_iot', 'ssid_extra_printer'):
            self.assertEqual(rows[section]['lists'].pop('hostapd_bss_options'), ['disable_11be=1', 'beacon_prot=1'])
        self.assertEqual(rows, before)
        value['common']['ssids']['iot']['eht'] = True
        value['common']['extra_ssids'][0]['eht'] = True
        restored = m.render(self.state(value))['etc/config/wireless']
        self.assertEqual(restored, m.render(baseline)['etc/config/wireless'])

    def test_eht_rejects_mlo_and_non_boolean_values(self):
        value = document()
        value['common']['extra_ssids'] = [{'id': 'lab', 'name': 'Lab', 'eht': False}]
        self.reject(lambda: self.state(value))
        for role in ('main', 'guest'):
            value = document(); value['common']['ssids'] = {role: {'eht': False}}
            self.reject(lambda: self.state(value))
        for invalid in (None, 0, 1, 'false', []):
            value = document(); value['common']['ssids'] = {'iot': {'eht': invalid}}
            self.reject(lambda: self.state(value))
            value = document(); value['common']['extra_ssids'] = [{'id': 'single', 'name': 'Single', 'bands': ['5g'], 'eht': invalid}]
            self.reject(lambda: self.state(value))

    def test_default_ntp_keeps_owner_servers_and_fallbacks(self):
        state = self.state()
        servers = state['common']['ntp_servers']
        self.assertEqual(servers[:3], ['time0.dxng.net', 'ntp.ibd.ink', 'ntp.dxng.cn'])
        self.assertEqual(len(servers), len(set(servers)))
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/system'])}
        self.assertEqual(rows['ntp']['lists']['server'], servers)

    def test_custom_minimal_defaults_and_explicit_mld_order(self):
        value = document()
        value['common']['extra_ssids'] = [
            {'id': 'battalion', 'name': '327Battalion'},
            {'id': 'printer', 'name': 'Printer', 'bands': ['5g'], 'encryption': 'psk2'},
            {'id': 'lab', 'name': 'Lab', 'bands': ['6g', '5g']},
        ]
        state = self.state(value)
        profiles = m.ssid_profiles(state)
        self.assertEqual([p['key'] for p in profiles], ['main', 'guest', 'battalion', 'printer', 'lab', 'iot'])
        by_key = {p['key']: p for p in profiles}
        self.assertEqual([p['iface'] for p in profiles if p['mlo']], ['ap-mld0', 'ap-mld1', 'ap-mld2', 'ap-mld3'])
        self.assertEqual(by_key['lab']['bands'], ['5g', '6g'])
        self.assertEqual(by_key['lab']['radios'], ['radio1', 'radio2'])
        self.assertEqual(by_key['lab']['link_ids'], [1, 2])
        self.assertEqual(by_key['battalion']['password_ref'], 'main')
        self.assertEqual(by_key['battalion']['encryption'], 'sae')
        self.assertEqual(by_key['battalion']['pmf'], 2)
        self.assertFalse(by_key['battalion']['hidden'])
        self.assertFalse(by_key['printer']['mlo'])
        self.assertTrue(by_key['printer']['iface'].startswith('ap-x'))
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
        self.assertEqual(rows['ssid_extra_lab']['lists']['device'], ['radio1', 'radio2'])
        self.assertEqual(rows['ssid_extra_lab']['lists']['radio_macaddr'], by_key['lab']['bssids'])
        self.assertEqual(rows['ssid_extra_printer']['options']['device'], 'radio1')
        self.assertNotIn('mlo', rows['ssid_extra_printer']['options'])
        self.assertEqual(rows['ssid_extra_battalion']['options']['key'], value['secrets']['wifi']['main'])
        for profile in profiles:
            self.assertEqual(rows[profile['section']]['options']['ifname'], profile['iface'])
        roaming = {r['name']: r for r in parse_uci(m.render(state)['etc/config/xr_roaming'])}
        self.assertEqual(roaming['lab']['options']['mlo'], '1')
        self.assertEqual(roaming['lab']['options']['max_links'], '2')
        self.assertEqual(roaming['printer']['options']['mlo'], '0')

    def test_custom_identity_and_slo_names_survive_reorder_and_band_reduction(self):
        value = document()
        value['common']['extra_ssids'] = [
            {'id': 'first', 'name': 'First'},
            {'id': 'single', 'name': 'Single', 'bands': ['5g']},
            {'id': 'second', 'name': 'Second', 'bands': ['2g', '5g', '6g']},
        ]
        before = self.state(value)
        value['common']['extra_ssids'].reverse()
        after = self.state(value)
        for group in ['mlds', 'bssids']:
            self.assertEqual(before['device']['identity'][group], after['device']['identity'][group])
        old = {p['key']: p for p in before['ssids']}
        new = {p['key']: p for p in after['ssids']}
        self.assertEqual(old['single']['iface'], new['single']['iface'])
        self.assertNotEqual(old['first']['iface'], new['first']['iface'])
        value['common']['extra_ssids'][0]['bands'] = ['5g', '6g']
        reduced = next(p for p in self.state(value)['ssids'] if p['key'] == 'second')
        self.assertEqual(reduced['bssids'], old['second']['bssids'][1:])

    def test_custom_secret_network_hidden_and_disabled_roaming(self):
        value = document()
        value['common']['extra_ssids'] = [{'id': 'private', 'name': 'Private', 'bands': ['5g'],
            'encryption': 'sae-mixed', 'network': 'guest', 'hidden': True,
            'password_ref': 'private_key', 'roaming': False}]
        value['secrets']['wifi']['private_key'] = 'private-fixture-password'
        state = self.state(value)
        profile = next(p for p in state['ssids'] if p['key'] == 'private')
        self.assertEqual(profile['pmf'], 1)
        self.assertFalse(profile['roaming'])
        self.assertEqual(state['peers']['private'], [])
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
        row = rows['ssid_extra_private']
        self.assertEqual(row['lists']['network'], ['guest'])
        self.assertEqual(row['options']['hidden'], '1')
        self.assertEqual(row['options']['isolate'], '0')
        self.assertEqual(row['options']['key'], value['secrets']['wifi']['private_key'])
        for field in ['ieee80211k', 'rrm_neighbor_report', 'rrm_beacon_report', 'bss_transition']:
            self.assertEqual(row['options'][field], '0')
        marker = m.render(state)['etc/dicttech-ap/device.json']
        self.assertNotIn(value['secrets']['wifi']['private_key'].encode(), marker)
        self.assertIn(b'private_key', marker)  # A reference name is public policy.
        value['common']['roaming'] = {'enabled': False}
        self.assertTrue(all(not p['roaming'] for p in self.state(value)['ssids']))

    def test_custom_six_ghz_enables_radio_without_main_link(self):
        value = document()
        value['common'] = {'ssids': {'main': {'six_ghz': False}},
                           'extra_ssids': [{'id': 'six', 'name': 'Six', 'bands': ['6g']}]}
        state = self.state(value)
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
        self.assertEqual(rows['radio2']['options']['disabled'], '0')
        self.assertEqual(rows['ssid_extra_six']['options']['device'], 'radio2')
        self.assertEqual(next(p for p in state['ssids'] if p['key'] == 'six')['link_ids'], [2])
        value['common']['country'] = 'CN'
        self.reject(lambda: self.state(value))

    def test_custom_hardware_limits_and_full_mld_budget(self):
        value = document(1)
        value['common']['extra_ssids'] = [{'id': f'custom{i}', 'name': f'Custom{i}', 'bands': ['5g', '6g']} for i in range(14)]
        self.assertEqual(sum(p['mlo'] for p in self.state(value)['ssids']), 16)
        self.assertEqual(len(self.state(value)['ssids']), 17)
        value['common']['extra_ssids'].append({'id': 'too_many', 'name': 'TooMany'})
        self.reject(lambda: self.state(value))
        value['common']['extra_ssids'] = [{'id': f'custom{i}', 'name': f'Custom{i}'} for i in range(14)]
        self.reject(lambda: self.state(value))  # 17 APs on 2GHz, including IoT.
        value['common']['extra_ssids'].pop()
        self.assertEqual(len(self.state(value)['ssids']), 16)

    def test_custom_validation_and_shared_ess_constraints(self):
        cases = [
            {'id': 'main', 'name': 'Custom'}, {'id': 'x' * 33, 'name': 'Custom'},
            {'id': 'bad-id', 'name': 'Custom'}, {'id': 'x', 'name': 'DictTech'},
            {'id': 'x', 'name': 'X', 'bands': []}, {'id': 'x', 'name': 'X', 'bands': ['5g', '5g']},
            {'id': 'x', 'name': 'X', 'bands': ['7g']}, {'id': 'x', 'name': 'X', 'bands': ['5g'], 'mlo': True},
            {'id': 'x', 'name': 'X', 'mlo': False}, {'id': 'x', 'name': 'X', 'encryption': 'psk2'},
            {'id': 'x', 'name': 'X', 'bands': ['6g'], 'encryption': 'sae-mixed'},
            {'id': 'x', 'name': 'X', 'encryption': 'none'}, {'id': 'x', 'name': 'X', 'network': 'wan'},
            {'id': 'x', 'name': 'X', 'hidden': 1}, {'id': 'x', 'name': 'X', 'password_ref': 'missing'},
        ]
        for entry in cases:
            value = document(); value['common']['extra_ssids'] = [entry]
            self.reject(lambda: self.state(value))
        value = document(); value['common']['extra_ssids'] = [{'id': 'x', 'name': 'X'}, {'id': 'x', 'name': 'Y'}]
        self.reject(lambda: self.state(value))
        for delta in [{'network': 'guest'}, {'password_ref': 'guest'}, {'id': 'different_id'}]:
            value = document(); value['common']['extra_ssids'] = [{'id': 'x', 'name': 'X'}]
            value['devices']['ap_1']['overrides'] = {'extra_ssids': [{**value['common']['extra_ssids'][0], **delta}]}
            self.reject(lambda: self.state(value))
        value = document(); value['common']['extra_ssids'] = [{'id': 'ap_1', 'name': 'X'}]
        self.reject(lambda: self.state(value))

    def test_custom_mac_pins_and_collisions(self):
        value = document()
        value['common']['extra_ssids'] = [{'id': 'battalion', 'name': '327Battalion'}]
        identity = self.state(value)['device']['identity']
        value['devices']['ap_0']['identity'] = {'mlds': {'battalion': identity['bssids']['battalion'][0]},
                                              'bssids': {'battalion': identity['bssids']['battalion']}}
        self.assertEqual(self.state(value)['device']['identity']['mlds']['battalion'], identity['bssids']['battalion'][0])
        value['devices']['ap_0']['identity']['bssids']['battalion'][0] = identity['bssids']['main'][0]
        self.reject(lambda: self.state(value))
        value = document(); value['common']['extra_ssids'] = [{'id': 'battalion', 'name': '327Battalion'}]
        value['devices']['ap_1']['identity'] = {'bssids': {'battalion': identity['bssids']['battalion']}}
        self.reject(lambda: self.state(value))

    def test_custom_inventory_pins_survive_removal_band_changes_and_readdition(self):
        value = document(1)
        item = {'id': 'battalion', 'name': '327Battalion'}
        value['common']['extra_ssids'] = [item]
        pins = {'2g': '02:cc:aa:10:00:41', '5g': '02:cc:aa:10:00:42'}
        value['devices']['ap_0']['identity'] = {'mlds': {'battalion': '02:cc:aa:10:00:40'},
                                              'bssids': {'battalion': pins}}
        original = self.state(value)
        original_profile = next(p for p in original['ssids'] if p['key'] == 'battalion')
        self.assertEqual(original_profile['bssids'], [pins['2g'], pins['5g']])
        inventory = original['device']['identity']
        self.assertEqual(len(inventory['bssids']['battalion']), 3)
        value['common']['extra_ssids'] = [{**item, 'bands': ['5g']}]
        single = self.state(value)
        profile = next(p for p in single['ssids'] if p['key'] == 'battalion')
        self.assertFalse(profile['mlo'])
        self.assertEqual(profile['bssids'], [pins['5g']])
        self.assertEqual(single['device']['identity'], inventory)
        value['common']['extra_ssids'] = [{**item, 'bands': ['2g', '5g', '6g']}]
        profile = next(p for p in self.state(value)['ssids'] if p['key'] == 'battalion')
        self.assertEqual(profile['bssids'], inventory['bssids']['battalion'])
        value['common']['extra_ssids'] = []
        removed = self.state(value)
        self.assertEqual([p['key'] for p in removed['ssids']], ['main', 'guest', 'iot'])
        self.assertEqual(removed['device']['identity'], inventory)
        wireless = parse_uci(m.render(removed)['etc/config/wireless'])
        self.assertFalse(any(row['name'] == 'ssid_extra_battalion' for row in wireless))
        self.assertNotIn('battalion', removed['peers'])
        value['common']['extra_ssids'] = [item]
        restored = self.state(value)
        self.assertEqual(restored['device']['identity'], inventory)
        self.assertEqual(next(p for p in restored['ssids'] if p['key'] == 'battalion')['bssids'], original_profile['bssids'])

    def test_inactive_custom_slots_validate_shape_limits_and_fleet_collisions(self):
        value = document()
        other_mac = self.state(value, 'ap_1')['device']['identity']['bssids']['main'][0]
        value['devices']['ap_0']['identity'] = {'bssids': {'unused': {'6g': other_mac}}}
        self.reject(lambda: self.state(value))
        bad = [
            {'bssids': {'unused': {}}}, {'bssids': {'unused': {'7g': other_mac}}},
            {'bssids': {'unused': [other_mac, other_mac]}}, {'bssids': {'main': {'2g': other_mac}}},
            {'mlds': {'iot': other_mac}}, {'bssids': {'bad-id': {'5g': other_mac}}},
            {'radios': {'unknown': other_mac}},
        ]
        for identity in bad:
            value = document(1); value['devices']['ap_0']['identity'] = identity
            self.reject(lambda: self.state(value))
        value = document(1)
        value['devices']['ap_0']['identity'] = {'mlds': {f'reserved{i}': f'02:cc:aa:00:01:{i:02x}' for i in range(64)}}
        state = self.state(value)
        self.assertEqual(len(state['device']['identity']['bssids']), 67)
        self.assertEqual(len(state['ssids']), 3)
        value['devices']['ap_0']['identity']['mlds']['one_more'] = '02:cc:aa:00:02:00'
        self.reject(lambda: self.state(value))

    def test_custom_mld_alias_can_keep_its_own_reserved_band_address(self):
        value = document(1)
        item = {'id': 'custom', 'name': 'Custom', 'bands': ['5g', '6g']}
        value['common']['extra_ssids'] = [item]
        pin = '02:cc:aa:10:00:42'
        value['devices']['ap_0']['identity'] = {'mlds': {'custom': pin}, 'bssids': {'custom': {'5g': pin}}}
        original = self.state(value)['device']['identity']
        value['common']['extra_ssids'][0]['bands'] = ['2g', '5g', '6g']
        self.assertEqual(self.state(value)['device']['identity'], original)
        value['common']['extra_ssids'] = []
        self.assertEqual(self.state(value)['device']['identity'], original)
        value['devices']['ap_0']['identity']['mlds']['custom'] = original['bssids']['main'][0]
        self.reject(lambda: self.state(value))

    def test_v1_and_legacy_input_require_explicit_migration(self):
        value = document(); value['schema_version'] = 1; value['profile'] = 'dicttech-ap-v1'
        self.reject(lambda: self.state(value))
        value = document(); value['devices']['ap_0']['legacy_ssid'] = 'Old'
        self.reject(lambda: self.state(value))

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
        self.assertEqual([r['name'] for r in vifs], ['ssid_main', 'ssid_guest', 'ssid_iot'])
        by_name = {r['name']: r for r in vifs}
        self.assertEqual(by_name['ssid_main']['lists']['device'], ['radio0', 'radio1', 'radio2'])
        self.assertEqual(by_name['ssid_guest']['lists']['device'], ['radio0', 'radio1'])
        self.assertEqual(by_name['ssid_guest']['options']['isolate'], '0')
        self.assertEqual(by_name['ssid_iot']['options']['device'], 'radio0')
        self.assertEqual(by_name['ssid_iot']['options']['encryption'], 'sae-mixed')
        self.assertEqual(by_name['ssid_iot']['options']['ieee80211w'], '1')
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

    def test_generated_external_guest_policies_survive_sysupgrade_backup(self):
        files = m.render(self.state())
        policies = {name for name in files
                    if name.startswith('etc/sysctl.d/') or name.endswith('.nft')}
        self.assertTrue(policies)
        keep = SOURCE.parents[2] / 'package/dicttech/ap-profile/files/lib/upgrade/keep.d'
        preserved = {line.strip().lstrip('/') for path in keep.iterdir()
                     for line in path.read_text().splitlines()
                     if line.strip() and not line.lstrip().startswith('#')}
        self.assertTrue(policies <= preserved, sorted(policies - preserved))

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

    def test_different_custom_names_and_absent_profiles_excluded(self):
        value = document()
        value['common']['extra_ssids'] = [{'id': 'battalion', 'name': '327Battalion'}]
        value['devices']['ap_1']['overrides'] = {'extra_ssids': [{'id': 'battalion', 'name': 'DifferentName'}]}
        state = self.state(value)
        self.assertEqual(state['peers']['battalion'], [])
        self.assertEqual(len(state['peers']['main']), 1)
        value['devices']['ap_1']['overrides']['extra_ssids'] = []
        self.assertEqual(self.state(value)['peers']['battalion'], [])

    def test_main_dual_band_peer_and_cn(self):
        value = document()
        value['devices']['ap_1']['overrides'] = {'ssids': {'main': {'six_ghz': False}}, 'country': 'CN'}
        state = self.state(value)
        self.assertEqual(len(state['peers']['main'][0]['bssids']), 2)
        other = self.state(value, 'ap_1')
        rows = {r['name']: r for r in parse_uci(m.render(other)['etc/config/wireless'])}
        self.assertEqual(rows['radio2']['options']['disabled'], '1')
        self.assertEqual(rows['ssid_main']['lists']['device'], ['radio0', 'radio1'])
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

    def test_initialization_key_cannot_be_promoted_to_explicit_permanent_access(self):
        value = document()
        key = m.BOOTSTRAP_KEY.read_text().strip()
        value['secrets']['devices']['ap_0']['authorized_keys'] = [key]
        self.reject(lambda: self.state(value))

    def test_utf8_limits_and_shell_metacharacter_roundtrip(self):
        value = document()
        value['common'] = {'ssids': {'main': {'name': "A' $(touch marker) \\"}}}
        state = self.state(value)
        rows = {r['name']: r for r in parse_uci(m.render(state)['etc/config/wireless'])}
        self.assertEqual(rows['ssid_main']['options']['ssid'], value['common']['ssids']['main']['name'])
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
