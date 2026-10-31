#!/usr/bin/env python3
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
"""Check the public resource schema against complete synthetic producer input."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator, ValidationError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'scripts/dicttech'))
import ap_config as model
from resource_fixture import document, single_band

SCHEMA = ROOT / 'package/dicttech/ap-profile/files/usr/share/dicttech-ap/deployment.schema.json'


class ResourceSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA.read_text())
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_complete_synthetic_producers_and_public_example(self):
        empty = document()
        node = empty['devices']['node_0']
        node.update(uci={}, files={}, services={}, ssids=[], peers={}, common={}, resolver=None)
        node['identity'].update(ethernet={}, radios={}, mlds={}, bssids={})
        node['platform'] = {'board_names': ['example,other']}
        examples = [document(), document(2), single_band(document()), empty,
                    json.loads((ROOT / 'examples/dicttech-ap/configuration.json').read_text())]
        for value in examples:
            with self.subTest(devices=list(value['devices'])):
                self.validator.validate(value)
                for device in value['devices']:
                    model.resolve(value, device)

    def test_binary_descriptors_are_part_of_the_public_schema(self):
        value = document()
        value['devices']['node_0']['files']['etc/example.bin'] = {
            'content_base64': 'AP8=', 'mode': '755'}
        self.validator.validate(value)
        self.assertEqual(model.render(model.resolve(value, 'node_0'))['etc/example.bin'], b'\0\xff')
        value['devices']['node_0']['files']['etc/example.bin']['mode'] = '777'
        with self.assertRaises(ValidationError):
            self.validator.validate(value)

    def test_he_only_producer_needs_no_mld_capability(self):
        value = single_band(document())
        node = value['devices']['node_0']
        node['platform']['max_mlds'] = 0
        node['identity']['radios'] = {'phy5g': node['identity']['radios']['phy5g']}
        node['ssids'][0]['eht'] = False
        wireless = node['uci']['wireless']
        wireless.pop('phy2g')
        wireless.pop('phy6g')
        wireless['phy5g']['options']['htmode'] = 'HE80'
        self.validator.validate(value)
        model.resolve(value, 'node_0')

    def test_structural_errors_fail_in_schema_and_model(self):
        cases = []
        value = document(); value['schema_version'] = 2; cases.append(value)
        value = document(); value['schema_version'] = True; cases.append(value)
        value = document(); value['devices']['node_0']['credentials'] = {}; cases.append(value)
        value = document(); del value['devices']['node_0']['identity']; cases.append(value)
        value = document(); value['devices']['node_0']['ssids'] = 'invalid'; cases.append(value)
        value = document(); value['devices']['node_0']['unexpected'] = True; cases.append(value)
        for value in cases:
            with self.subTest(value=list(value['devices']['node_0'])):
                with self.assertRaises(ValidationError):
                    self.validator.validate(value)
                with self.assertRaises(model.ConfigError):
                    model.resolve(value, 'node_0')

    def test_file_resources_cannot_replace_system_or_transaction_state(self):
        paths = ('etc/gshadow', 'etc/shadow', 'etc/passwd', 'etc/group',
                 'etc/dropbear/authorized_keys', 'etc/dropbear/dropbear_ed25519_host_key',
                 'etc/ssh/sshd_config', 'etc/rc.d/S99example',
                 'etc/openwrt-deploy', 'etc/openwrt-deploy/current.json',
                 'etc/dicttech-ap', 'etc/dicttech-ap/device.json',
                 'etc/dicttech-ap-bootstrap-key', 'etc/dicttech-ap-bootstrap-key/key',
                 'etc/init.d/openwrt-deploy-guard', 'etc/init.d/example',
                 'etc/../config/example', 'etc/example/..', 'etc//example',
                 '/etc/example', 'usr/bin/example')
        for path in paths:
            value = document()
            value['devices']['node_0']['files'][path] = 'untrusted replacement'
            with self.subTest(path=path):
                with self.assertRaises(ValidationError):
                    self.validator.validate(value)
                with self.assertRaises(model.ConfigError):
                    model.resolve(value, 'node_0')

        value = document()
        for path in ('etc/example.conf', 'etc/init.d/openwrt-managed-example',
                     'usr/libexec/openwrt-managed/example'):
            value['devices']['node_0']['files'][path] = 'managed plugin resource'
        self.validator.validate(value)
        model.resolve(value, 'node_0')

    def test_private_runtime_markers_are_not_producer_documents(self):
        marker = json.loads(model.render(model.resolve(document(), 'node_0'))['etc/dicttech-ap/device.json'])
        with self.assertRaises(ValidationError):
            self.validator.validate(marker)


if __name__ == '__main__':
    unittest.main(verbosity=2)
