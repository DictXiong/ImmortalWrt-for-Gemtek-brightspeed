#!/usr/bin/env python3
"""Inert resource-verifier fixtures; no network, credentials or live hardware."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / 'tests'))
import ap_config as model
from resource_fixture import document, single_band, PASSWORD
spec = importlib.util.spec_from_file_location('resource_verify', HERE / 'ap-verify.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)


def fixture(value=None):
    state = model.resolve(value or document(), 'node_0'); files = model.render(state)
    modes = v.deploy.file_modes(files)
    data = {
        'stable_snapshot': True, 'contract': {
            'schema_version': 3, 'profile': state['profile'], 'contract_sha256': state['contract_sha256'],
            'boot_id': 'fixture-boot', 'identity': {'device_id': 'node_0', 'bridge_mac': state['device']['identity']['bridge'], 'board_name': 'example,board'},
            'files': {path: hashlib.sha256(value).hexdigest() for path, value in files.items()},
            'modes': {path: [v.deploy.mode_security(modes[path]), '0'] for path in files}, 'root_password_synced': True,
            'resolver_state': {'type':'symlink','target':state['resolver']['target']} if state['resolver'] is not None else {'type':'absent'},
        },
        'services': {name: {'instances': {'main': {'running': policy.get('running') is True}}} for name, policy in state['services'].items()},
        'service_links': '\n'.join('S20' + name for name, policy in state['services'].items() if policy['enable']),
        'boot_hooks': 'S03dicttech-ap-transaction\nS04dicttech-ap-prepare\nS13dicttech-ap-finalize',
        'radio_inventory': {}, 'hostapd_objects': '\n'.join('hostapd.' + profile['iface'] for profile in state['ssids']),
        'security': '',
    }
    for name, section in state['uci'].get('network', {}).items():
        if section['type'] == 'interface':
            data['interface_' + name] = {'up': True, 'proto': section['options']['proto'], 'l3_device': section['options']['device'],
                                         'ipv4-address': [{'address': '203.0.113.10', 'mask': 24}]}
    for radio, section in state['uci'].get('wireless', {}).items():
        if section['type'] != 'wifi-device': continue
        profiles = [profile for profile in state['ssids'] if radio in profile['radios']]
        data['radio_inventory'][radio] = {'up': bool(profiles), 'disabled': not profiles,
                                         'interfaces': [{'section': profile['section'], 'ifname': profile['iface']} for profile in profiles]}
    for profile in state['ssids']:
        key = profile['key']
        links = [{'bssid': bssid, 'freq': v.frequency(state, band, radio)} for bssid, band, radio in zip(profile['bssids'], profile['bands'], profile['radios'])]
        data['hostapd_' + key] = {'status': 'ENABLED', 'ssid': profile['name'], **({'links': links} if profile['mlo'] else links[0])}
        data['roaming_' + key] = {'state': 'ok', 'snapshot_fresh': True, 'peers': [dict(peer, fresh=True) for peer in state['peers'][key]]}
        data['services'].setdefault('xr_roaming', {'instances': {}})['instances'][key] = {'running': profile['roaming']}
        for bssid, link in zip(profile['bssids'], profile['link_ids']):
            pairs = {'interface': profile['iface'], 'ssid': profile['name'], 'bssid': bssid, 'wpa': '2',
                     'wpa_key_mgmt': {'sae': 'SAE', 'sae-mixed': 'SAE WPA-PSK', 'psk2': 'WPA-PSK', 'owe':'OWE'} .get(profile['encryption'],'UNSUPPORTED-TEST'),
                     'ieee80211w': str(profile['pmf']), 'ignore_broadcast_ssid': str(int(profile['hidden'])),
                     'disable_11be': str(int(not profile['eht'])), 'mld_ap': str(int(profile['mlo'])), 'mld_link_id': str(link),
                     **{name: str(int(profile['roaming'])) for name in ['rrm_neighbor_report', 'rrm_beacon_report', 'bss_transition']}}
            data['security'] += '\n'.join(name + '=' + value for name, value in pairs.items()) + '\n'
    for radio, (band, _) in v.radio_probes(state).items():
        options = state['uci']['wireless'][radio]['options']; width = int(options['htmode'][3:])
        data['radio_' + radio] = '\n'.join('='.join(item) for item in {
            'state': 'ENABLED', 'freq': str(v.frequency(state, band, radio)), 'channel': str(options['channel']),
            'eht_oper_chwidth': {20: '0', 40: '0', 80: '1', 160: '2', 320: '4'}[width],
            'secondary_channel': '0' if width == 20 else '1',
        }.items())
    return state, files, data


class Verify(unittest.TestCase):
    def setUp(self): self.state, self.files, self.data = fixture()
    def report(self): return v.evaluate(self.state, self.files, self.data)[0]
    def assertFailed(self, name):
        report = self.report()
        self.assertEqual(next(item['status'] for item in report['tests'] if item['id'] == name), 'FAIL')
    def test_generic_names_vlan_and_non_us_channel_are_not_replaced_by_presets(self):
        report = self.report()
        self.assertEqual(report['configuration_status'], 'PASS')
        self.assertEqual(report['status'], 'NOT_TESTED')
        self.assertNotIn('192', json.dumps(report))
        self.assertNotIn(PASSWORD, json.dumps(report))
    def test_empty_wireless_and_minimal_resource_inventory_are_valid(self):
        value = document(); node = value['devices']['node_0']
        node.update(uci={}, files={}, ssids=[], peers={}, services={}, common={})
        node['identity'].update(ethernet={}, radios={}, mlds={}, bssids={}); node['platform'] = {'board_names': ['example,board']}
        self.state, self.files, self.data = fixture(value)
        self.assertEqual(self.report()['configuration_status'], 'PASS')
        self.assertFalse(any(item['id'].startswith('wireless') for item in self.report()['tests']))
    def test_explicit_resource_modes_are_verified(self):
        value=document(); value['devices']['node_0']['files']['etc/example.conf']={'content_base64':'YmluYXJ5AA==','mode':'644'}
        self.state,self.files,self.data=fixture(value)
        self.assertEqual(self.report()['configuration_status'],'PASS')
        self.data['contract']['modes']['etc/example.conf']=['-rw-------','0']; self.assertFailed('managed_resources')
    def test_file_hash_or_owner_drift_fails(self):
        self.data['contract']['files']['etc/example.conf']='0'*64; self.assertFailed('managed_resources')
        self.state,self.files,self.data=fixture(); self.data['contract']['modes']['etc/example.conf']=['-rw-------','99']; self.assertFailed('managed_resources')
    def test_wrong_contract_board_identity_and_unstable_collection_fail(self):
        for field,value in [('contract_sha256','0'*64),('schema_version',2)]:
            self.state,self.files,self.data=fixture(); self.data['contract'][field]=value; self.assertFailed('target_identity')
        self.state,self.files,self.data=fixture(); self.data['stable_snapshot']=False; self.assertFailed('stable_snapshot')
    def test_single_band_wpa2_uses_generic_profile_and_socket(self):
        self.state,self.files,self.data=fixture(single_band(document()))
        self.assertEqual(v.radio_probes(self.state),{'phy5g':('5g','wifi-office')})
        self.assertEqual(self.report()['configuration_status'],'PASS')
    def test_security_mlo_bssid_hidden_and_frequency_drift_fail(self):
        for old,new,name in [('ieee80211w=2','ieee80211w=1','security_office'),('mld_ap=1','mld_ap=0','security_office'),('ignore_broadcast_ssid=0','ignore_broadcast_ssid=1','security_office')]:
            self.state,self.files,self.data=fixture(); self.data['security']=self.data['security'].replace(old,new); self.assertFailed(name)
        self.state,self.files,self.data=fixture(); self.data['hostapd_office']['links'][0]['freq']+=5; self.assertFailed('wireless_office')
    def test_service_policy_and_required_boot_hooks_are_explicit(self):
        self.data['services']['example-agent']['instances']['main']['running']=False; self.assertFailed('service_example-agent')
        self.state,self.files,self.data=fixture(); self.data['boot_hooks']=''; self.assertFailed('boot_policy_hooks')
    def test_disabled_roaming_must_have_no_worker(self):
        self.state['ssids'][0]['roaming']=False; self.assertFailed('roaming_office')
    def test_radio_and_unconfigured_hostapd_inventory_drift_fail(self):
        self.data['hostapd_objects']+='\nhostapd.hidden-extra'; self.assertFailed('wireless_inventory')
        self.state,self.files,self.data=fixture(); self.data['radio_phy5g']=self.data['radio_phy5g'].replace('eht_oper_chwidth=2','eht_oper_chwidth=1'); self.assertFailed('width_phy5g')
    def test_stale_peer_fails(self):
        self.data['roaming_office']['snapshot_fresh']=False; self.assertFailed('roaming_office')
    def test_external_client_evidence_is_bound_to_complete_desired_state(self):
        report,binding=v.evaluate(self.state,self.files,self.data)
        external=dict(binding,tests={item['id']:{'status':'PASS'} for item in report['tests'] if item['status']=='NOT_TESTED'})
        self.assertEqual(v.evaluate(self.state,self.files,self.data,external)[0]['status'],'PASS')
        external['boot_id']='another-boot'
        self.assertEqual(v.evaluate(self.state,self.files,self.data,external)[0]['status'],'FAIL')
    def test_owe_is_supported_and_unknown_akm_is_reported_untested(self):
        value=single_band(document(),encryption='owe');node=value['devices']['node_0']
        node['ssids'][0]['pmf']=2;node['uci']['wireless']['bss_office']['options']['ieee80211w']='2'
        self.state,self.files,self.data=fixture(value)
        self.assertEqual(self.report()['configuration_status'],'PASS')
        self.state,self.files,self.data=fixture(single_band(document(),encryption='custom-akm'))
        self.assertEqual(self.report()['configuration_status'],'NOT_TESTED')
        self.assertEqual(next(item['status'] for item in self.report()['tests'] if item['id']=='akm_office'),'NOT_TESTED')

    def test_explicit_he_mode_and_auto_channel_do_not_require_eht_fixed_channels(self):
        self.state['uci']['wireless']['phy5g']['options'].update(htmode='HE160',channel='auto')
        self.assertEqual(self.report()['configuration_status'],'PASS')

    def test_collection_uses_dynamic_uci_interfaces_and_redacts_daemon_credentials(self):
        seen=[]
        def read(args,words,parse=True): seen.append(words); return {} if parse else ''
        with patch.object(v,'read_command',side_effect=read),patch.object(v.deploy,'remote',return_value=self.data['contract']):
            value=v.collect(object(),self.state)
        joined='\n'.join(' '.join(words) for words in seen)
        self.assertIn('network.interface.trusted',joined)
        self.assertNotIn('network.interface.guest',joined)
        self.assertNotIn('br-lan.192',joined)
        self.assertNotIn('wpa_passphrase',joined)
        self.assertTrue(value['stable_snapshot'])
    def test_ssid2_literal_decoding_does_not_guess_printf_escapes(self):
        self.assertEqual(v.parse_ssid2('"a\\\\b"'),'a\\\\b')
        self.assertEqual(v.parse_ssid2('e6b58be8af95'),'测试')
        with self.assertRaises(ValueError):v.parse_ssid2('P"a\\\\nb"')


if __name__=='__main__':unittest.main(verbosity=2)
