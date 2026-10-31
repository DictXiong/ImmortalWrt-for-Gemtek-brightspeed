#!/usr/bin/env python3
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
"""Offline complete-resource backend tests; all credentials are synthetic."""
from __future__ import annotations
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'tests'))
import ap_config as m
from resource_fixture import PASSWORD, PASSWORD_HASH, document, single_band


class ResourceTests(unittest.TestCase):
    def state(self, value=None, node='node_0'):
        return m.resolve(document() if value is None else value, node)

    def reject(self, value):
        with self.assertRaises(m.ConfigError):
            self.state(value)

    def test_arbitrary_producer_policy_is_preserved(self):
        value = document()
        state = self.state(value)
        output = m.render(state)
        self.assertEqual(state['device']['maintenance_ipv4'], '198.51.100.10')
        self.assertEqual(state['platform']['native_vlan'], 137)
        self.assertEqual(state['platform']['guest_vlan'], 345)
        self.assertEqual(set(output), {'etc/config/network', 'etc/config/wireless', 'etc/example.conf',
                         'etc/dicttech-ap/device.json', 'etc/dicttech-ap/managed-files.json', 'etc/dicttech-ap/root-password-hash'})
        self.assertIn(b"option country 'DE'", output['etc/config/wireless'])
        self.assertIn(b"option channel '149'", output['etc/config/wireless'])
        self.assertEqual(output['etc/example.conf'], b'explicit producer content\n')
        self.assertNotIn('etc/config/firewall', output)
        self.assertNotIn('etc/config/fan', output)
        self.assertNotIn('etc/config/cpufreq', output)
        self.assertNotIn('etc/config/airoha_npu', output)

    def test_no_hotspots_services_or_network_are_required(self):
        value = document()
        node = value['devices']['node_0']
        node.update(uci={}, files={}, ssids=[], peers={}, services={}, common={}, resolver=None)
        node['identity'].update(ethernet={}, radios={}, mlds={}, bssids={})
        node['platform'] = {'board_names': ['example,other']}
        output = m.render(self.state(value))
        self.assertEqual(set(output), {'etc/dicttech-ap/device.json', 'etc/dicttech-ap/managed-files.json', 'etc/dicttech-ap/root-password-hash'})

    def test_old_presets_and_partial_identity_are_rejected(self):
        value = document()
        value['common'] = {}
        self.reject(value)
        for field in ('uci', 'identity', 'ssids', 'services', 'platform'):
            value = document(); del value['devices']['node_0'][field]
            self.reject(value)
        value = document(); value['devices']['node_0']['identity']['complete'] = True
        self.reject(value)
        value = document(); value['devices']['node_0']['identity']['bssids'].clear()
        self.reject(value)
        value = document(); value['schema_version'] = 2; value['profile'] = 'dicttech-ap-v2'
        self.reject(value)

    def test_old_role_names_are_not_reserved(self):
        value = document()
        value['devices']['main'] = value['devices'].pop('node_0')
        state = m.resolve(value, 'main')
        self.assertEqual(state['device_id'], 'main')
        self.assertEqual(state['ssids'][0]['key'], 'office')

    def test_no_address_allocation_occurs(self):
        value = document()
        identity = copy.deepcopy(value['devices']['node_0']['identity'])
        self.assertEqual(self.state(value)['device']['identity'], identity)
        del value['devices']['node_0']['identity']['radios']
        self.reject(value)

    def test_mld_alias_only_its_own_reserved_bssid(self):
        value = document()
        node=value['devices']['node_0']; profile=node['ssids'][0]
        address=node['identity']['bssids']['office'][0]
        node['identity']['mlds']['office']=address
        profile['mld_mac']=address
        node['uci']['wireless'][profile['section']]['options']['macaddr']=address
        self.state(value)
        node['identity']['bssids']['other']=['02:cc:00:00:00:01','02:cc:00:00:00:02','02:cc:00:00:00:03']
        node['identity']['mlds']['other']=address
        self.reject(value)

    def test_identity_and_fleet_collisions(self):
        for group in ('ethernet', 'radios', 'mlds', 'bssids'):
            value = document(2)
            value['devices']['node_1']['identity'][group] = copy.deepcopy(value['devices']['node_0']['identity'][group])
            self.reject(value)
        value = document(2)
        value['devices']['node_1']['identity']['duid'] = value['devices']['node_0']['identity']['duid']
        self.reject(value)
        for field in ('hostname', 'management_ipv4'):
            value = document(2); value['devices']['node_1'][field] = value['devices']['node_0'][field]
            self.reject(value)

    def test_declared_hardware_prefix_constraint(self):
        value = document(); node=value['devices']['node_0']
        node['identity']['ethernet']['cpu'] = '02:00:01:00:00:01'
        node['uci']['network']['mac_cpu']={'type':'device','options':{'name':'cpu','macaddr':node['identity']['ethernet']['cpu']}}
        node['platform']['mac_prefix_groups'] = [['cpu','desk']]
        self.reject(value)
        node['platform']['mac_prefix_groups'] = []
        self.state(value)
        node['identity']['ethernet']['cpu'] = node['identity']['ethernet']['desk'][:8]+':ff:ff:ff'
        node['uci']['network']['mac_cpu']['options']['macaddr']=node['identity']['ethernet']['cpu']
        node['platform']['mac_prefix_groups'] = [['cpu','desk']]
        self.state(value)

    def test_bssid_reserved_shape_addresses_and_collision(self):
        for mac in ('01:00:00:00:00:00','00:00:00:00:00:00','bad'):
            value=document(); value['devices']['node_0']['identity']['bridge']=mac
            self.reject(value)
        value=document(); value['devices']['node_0']['identity']['bssids']['office'].pop()
        self.reject(value)
        value=document(); identity=value['devices']['node_0']['identity']
        identity['radios']['phy2g']=identity['bssids']['office'][0]
        self.reject(value)

    def test_single_band_wpa2_on_any_ssid(self):
        value=single_band(document())
        state=self.state(value)
        self.assertFalse(state['ssids'][0]['mlo'])
        self.assertEqual(state['ssids'][0]['encryption'],'psk2')
        self.assertEqual(state['ssids'][0]['bssids'],[state['device']['identity']['bssids']['office'][1]])

    def test_multi_band_and_6ghz_require_valid_security(self):
        for modification in ('encryption','pmf','eht','mlo','bands'):
            value=document(); profile=value['devices']['node_0']['ssids'][0]
            profile[modification]={'encryption':'psk2','pmf':1,'eht':False,'mlo':False,'bands':['5g']}[modification]
            self.reject(value)
        value=single_band(document(),'6g','psk2')
        self.reject(value)
        self.state(single_band(document(),'6g','sae'))

    def test_metadata_does_not_override_wireless_resources(self):
        for field in ('ifname','ssid','encryption','hidden','disabled','ieee80211w','macaddr','device','network','radio_macaddr'):
            value=document(); options=value['devices']['node_0']['uci']['wireless']['bss_office']['options']
            options[field]='mismatch'
            self.reject(value)
        for field in ('radios','link_ids','bssids'):
            value=document(); value['devices']['node_0']['ssids'][0][field]=[]
            self.reject(value)

    def test_untracked_ap_bss_is_rejected(self):
        value=document(); node=value['devices']['node_0']
        node['uci']['wireless']['untracked']=copy.deepcopy(node['uci']['wireless']['bss_office'])
        self.reject(value)

    def test_capacity_comes_from_explicit_platform(self):
        value=document(); node=value['devices']['node_0']
        node['platform']['max_mlds']=0
        self.reject(value)
        del node['platform']['max_mlds']
        self.state(value)

    def test_peers_use_actual_other_device_table(self):
        value=document(2); self.state(value)
        peer=value['devices']['node_0']['peers']['office'][0]
        peer['bssids']=['02:cc:01:00:00:01']
        self.reject(value)
        value=document(2); value['devices']['node_1']['uci']['wireless']['bss_office']['options']['key']='different-password'
        self.reject(value)

    def test_peer_requires_full_table_but_not_matching_local_ids(self):
        value=document(2)
        node=value['devices']['node_1']; profile=node['ssids'][0]
        profile['key']='different_local_id'
        for group in ('mlds','bssids'):
            node['identity'][group]['different_local_id']=node['identity'][group].pop('office')
        node['peers']['different_local_id']=node['peers'].pop('office')
        self.state(value)
        value['devices']['node_0']['peers']['office'][0]['id']='unknown_peer'
        self.reject(value)

    def test_same_ess_can_use_multiple_single_link_bsses(self):
        value=single_band(document(),'5g','psk2')
        node=value['devices']['node_0']
        profile=copy.deepcopy(node['ssids'][0]); profile.update(key='office24',section='bss_office24',iface='wifi-office24',bands=['2g'],radios=['phy2g'],link_ids=[0])
        node['identity']['mlds']['office24']='02:cc:12:00:00:00'
        node['identity']['bssids']['office24']=['02:cc:12:00:00:01','02:cc:12:00:00:02','02:cc:12:00:00:03']
        profile['bssids']=[node['identity']['bssids']['office24'][0]]
        node['ssids'].append(profile);node['peers']['office24']=[]
        row=copy.deepcopy(node['uci']['wireless']['bss_office'])
        row['options'].update(ifname=profile['iface'],device='phy2g',macaddr=profile['bssids'][0])
        node['uci']['wireless'][profile['section']]=row
        state=self.state(value)
        self.assertEqual([item['name'] for item in state['ssids']],['Example-Office','Example-Office'])
        self.assertTrue(all(not item['mlo'] for item in state['ssids']))
        row['options']['key']='inconsistent-password'
        self.reject(value)

    def test_no_automatic_peer_discovery_or_default_policy(self):
        value=document(2)
        for node in value['devices'].values(): node['peers']['office']=[]
        self.assertEqual(self.state(value)['peers'],{'office':[]})

    def test_programmed_physical_macs_must_match_the_table(self):
        for package, section, field in (('network','mac_uplink','macaddr'),('network','bridge','macaddr'),('wireless','phy2g','macaddr_base')):
            value=document();value['devices']['node_0']['uci'][package][section]['options'][field]='02:cc:00:00:00:01'
            self.reject(value)

    def test_service_policy_is_exact_and_triggered_resources_exist(self):
        state=self.state()
        self.assertEqual(set(state['services']),{'example-agent'})
        for modification in ({'enable':False,'running':True},{'reloadOn':{'uci':['missing'],'files':[]}}, {'unknown':True}):
            value=document(); value['devices']['node_0']['services']['example-agent'].update(modification)
            self.reject(value)
        value=document(); value['devices']['node_0']['services']['openwrt-deploy-guard']={}
        self.reject(value)

    def test_configuration_engines_cannot_manage_their_own_safety_services(self):
        contract=json.loads(m.CONTRACT.read_text())
        self.assertEqual(set(contract['reserved_services']),{'dicttech-ap-transaction','dicttech-ap-prepare','dicttech-ap-finalize','openwrt-deploy-guard'})
        for service in contract['reserved_services']:
            value=document()
            value['devices']['node_0']['services'][service]={'enable':False,'running':False,'reloadOn':{'uci':[],'files':[]}}
            self.reject(value)

    def test_section_and_path_protocol_bounds_match_filesystem_components(self):
        value=document();node=value['devices']['node_0'];old=node['ssids'][0]['section']
        section='s'*255
        node['ssids'][0]['section']=section
        node['uci']['wireless'][section]=node['uci']['wireless'].pop(old)
        self.state(value)
        node['ssids'][0]['section']=section+'s'
        node['uci']['wireless'][section+'s']=node['uci']['wireless'].pop(section)
        self.reject(value)
        maximum='etc/'+'/'.join(['a'*255]*15+['b'*252])
        self.assertEqual(len(maximum),4096)
        self.assertEqual(m.resource_path(maximum,'path'),maximum)
        for path in (maximum+'x','etc/'+'a'*256):
            with self.assertRaises(m.ConfigError):m.resource_path(path,'path')

    def test_forbidden_paths_and_non_text_files(self):
        for name in ('/etc/absolute','etc/../shadow','etc//config','etc/shadow','etc/config/network','etc/dicttech-ap/device.json',
                     'etc/dropbear/authorized_keys','etc/ssh/private','etc/rc.d/S90bad','tmp/file','etc/init.d/arbitrary'):
            value=document(); value['devices']['node_0']['files']={name:'payload'}
            self.reject(value)
        value=document(); value['devices']['node_0']['files']['etc/example.conf']='bad\0payload'
        self.reject(value)

    def test_binary_files_and_explicit_modes_are_preserved(self):
        value=document(); content=b"binary\0content\xff"
        value['devices']['node_0']['files']['etc/example.bin']={'content_base64':base64.b64encode(content).decode(),'mode':'644'}
        value['devices']['node_0']['files']['usr/libexec/openwrt-managed/action']={'content_base64':base64.b64encode(b'#!/bin/sh\nexit 0\n').decode(),'mode':'755'}
        output=m.render(self.state(value))
        self.assertEqual(output['etc/example.bin'],content)
        ledger=json.loads(output['etc/dicttech-ap/managed-files.json'])
        modes={row['path']:row['mode'] for row in ledger['files']}
        self.assertEqual(modes['etc/example.bin'],'644')
        self.assertEqual(modes['usr/libexec/openwrt-managed/action'],'755')
        value['devices']['node_0']['files']['etc/example.bin']['mode']='777'
        self.reject(value)

    def test_cipher_suffix_is_syntax_not_a_role_policy(self):
        for value in (document(),single_band(document())):
            profile=value['devices']['node_0']['ssids'][0]
            profile['encryption']+='+ccmp'
            value['devices']['node_0']['uci']['wireless'][profile['section']]['options']['encryption']=profile['encryption']
            self.state(value)

    def test_malformed_compound_values_raise_config_error(self):
        for field in ('identity','platform','common','ssids','peers','services','uci','files','credentials'):
            for invalid in (None,7,False,'wrong'):
                value=document();value['devices']['node_0'][field]=invalid
                self.reject(value)
        for field in ('bands','radios','link_ids','bssids'):
            value=document();value['devices']['node_0']['ssids'][0][field]=[{}]
            self.reject(value)
        value=document();value['devices']['node_0']['platform']['mac_prefix_groups']=[[{},{}]]
        self.reject(value)

    def test_diagnostics_cannot_accept_nested_credentials(self):
        value=document();value['devices']['node_0']['common']['arbitrary']={'password':PASSWORD}
        self.reject(value)
        value=document();value['devices']['node_0']['common']['radios']={'phy2g':{'password':PASSWORD}}
        self.reject(value)

    def test_uci_escaping_order_scalars_and_anonymous_sections(self):
        value=document(); node=value['devices']['node_0']
        node['uci']['example']={
            'later':{'type':'record','order':2,'options':{'text':"quote' $(not executed)", 'list':['a','b']}},
            'earlier':{'type':'record','order':1,'anonymous':True,'options':{'enabled':True,'count':3}},
        }
        rendered=m.render(self.state(value))['etc/config/example']
        self.assertTrue(rendered.startswith(b'config record\n'))
        self.assertIn(b"option enabled '1'",rendered)
        self.assertIn(b"option count '3'",rendered)
        self.assertIn(b"quote'\\'' $(not executed)",rendered)
        for invalid in (None,3.14,{'injected':'object'}):
            node['uci']['example']['earlier']['options']['count']=invalid
            self.reject(value)

    def test_resolver_policy_is_explicit_optional_and_has_no_default_target(self):
        value=document()
        state=self.state(value)
        self.assertEqual(state['resolver'],{'target':'/tmp/example-resolver.conf'})
        output=m.render(state)
        self.assertNotIn('etc/resolv.conf',output)
        self.assertEqual(json.loads(output['etc/dicttech-ap/device.json'])['resolver'],state['resolver'])
        value['devices']['node_0']['resolver']=None
        self.assertIsNone(self.state(value)['resolver'])
        for invalid in ({},{'target':'relative'},{'target':'/etc/../secret'},{'target':'/tmp//name'},
                        {'target':'/etc/resolv.conf'},{'target':'/tmp/evil\nline'},{'target':'/'},'auto'):
            value['devices']['node_0']['resolver']=invalid
            self.reject(value)
        del value['devices']['node_0']['resolver']
        self.reject(value)

    def test_secret_values_are_not_in_public_marker_or_diagnostics(self):
        state=self.state(); output=m.render(state)
        marker=output['etc/dicttech-ap/device.json']
        self.assertNotIn(PASSWORD.encode(),marker)
        self.assertNotIn(PASSWORD_HASH.encode(),marker)
        self.assertNotIn('uci',json.loads(marker))
        self.assertNotIn('files',json.loads(marker))
        self.assertEqual(output['etc/dicttech-ap/root-password-hash'],(PASSWORD_HASH+'\n').encode())
        value=document(); value['devices']['node_0']['uci']['wireless']['bss_office']['options']['key']='short'
        with self.assertRaises(m.ConfigError) as caught:self.state(value)
        self.assertNotIn('short',str(caught.exception))

    def test_private_managed_ledger_covers_exact_resource_set(self):
        files=m.render(self.state())
        ledger=json.loads(files['etc/dicttech-ap/managed-files.json'])
        expected={name:(hashlib.sha256(data).hexdigest(),'600') for name,data in files.items() if name!='etc/dicttech-ap/managed-files.json'}
        self.assertEqual({row['path']:(row['sha256'],row['mode']) for row in ledger['files']},expected)
        self.assertEqual(ledger['schema_version'],3)

    def test_credentials_are_validated_and_optional_keys_preserve_semantics(self):
        value=document(); node=value['devices']['node_0']
        node['credentials']['authorized_keys']=[]
        self.assertEqual(m.render(self.state(value))['etc/dropbear/authorized_keys'],b'')
        node['credentials']['authorized_keys']=[m.BOOTSTRAP_KEY.read_text().strip()]
        self.reject(value)
        for password_hash in ('root','!'+PASSWORD_HASH,'$6$bad$short'):
            node['credentials']={'root_password_hash':password_hash}
            self.reject(value)

    def test_json_private_permissions_duplicate_fields_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'input.json'
            path.write_bytes(m.canonical(document())); path.chmod(0o644)
            with self.assertRaises(m.ConfigError):m.load_document(path)
            path.chmod(0o600)
            self.assertEqual(m.load_document(path),document())
            path.write_text('{"schema_version":3,"schema_version":3}')
            with self.assertRaises(m.ConfigError):m.load_document(path)
            path.write_text('x'*(m.MAX_JSON_BYTES+1))
            with self.assertRaises(m.ConfigError):m.load_document(path)

    def test_cli_render_requires_complete_input_and_empty_private_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'input.json'; target=root/'output'
            m.write_private(path,m.canonical(document()))
            args=[sys.executable,str(HERE/'ap_config.py'),'render','--config',str(path),'--device','node_0','--output',str(target)]
            result=subprocess.run(args,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn(PASSWORD.encode(),result.stdout)
            self.assertEqual(target.stat().st_mode & 0o777,0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777==0o600 for p in target.rglob('*') if p.is_file()))
            self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)
            args=[sys.executable,str(HERE/'ap_config.py'),'merge','--help']
            self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)


if __name__=='__main__':unittest.main(verbosity=2)
