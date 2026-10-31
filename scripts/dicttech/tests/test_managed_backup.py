#!/usr/bin/env python3
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
"""Inert sysupgrade-hook tests; no real backups, services or firmware changes."""
from pathlib import Path
import copy
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / 'package/dicttech/ap-profile/files/lib/upgrade/dicttech-managed.sh'


class ManagedBackup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='managed-backup-'); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'root'; self.root.mkdir()
        self.ledger=self.root/'etc/dicttech-ap/managed-files.json'
        self.target=Path(self.temp.name)/'conffiles'; self.target.write_text('/etc/existing\n')
        self.payloads={'etc/dicttech-ap/device.json':b'{"schema_version":3}\n',
                       'etc/dicttech-ap/root-password-hash':b'$6$fixture$hash\n',
                       'etc/custom/plugin.bin':b'\x00binary\xff\n',
                       'usr/libexec/openwrt-managed/plugin':b'#!/bin/sh\nexit 0\n'}
        self.modes={path:'600' for path in self.payloads}; self.modes['usr/libexec/openwrt-managed/plugin']='755'
        for path,value in self.payloads.items():
            target=self.root/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(value); target.chmod(int(self.modes[path],8))
        self.manifest={'schema_version':3,'profile':'openwrt-config-v3','files':[{'path':path,'mode':self.modes[path],'sha256':hashlib.sha256(value).hexdigest()} for path,value in self.payloads.items()]}
        self.write_manifest()
        source=HOOK.read_text()
        source=re.sub(r'/(?:etc|usr/libexec)/',lambda m:str(self.root/m[0].strip('/'))+'/',source)
        source=source.replace('next current=', 'next current='+shlex.quote(str(self.root)))
        source=source.replace('"/$name"', '"'+str(self.root)+'/$name"')
        self.script=Path(self.temp.name)/'hook.sh'; self.script.write_text(source)
    def write_manifest(self):
        self.ledger.parent.mkdir(parents=True,exist_ok=True)
        self.ledger.write_text(json.dumps(self.manifest));self.ledger.chmod(0o600)
    def run_hook(self,good=True):
        script='set -eu\nappend() { eval "$1=\\\"\\${$1-} $2\\\""; }\nsysupgrade_init_conffiles=original_hook\n. '+shlex.quote(str(self.script))+'\ncase "$sysupgrade_init_conffiles" in *dicttech_add_managed_conffiles*) ;; *) exit 90;; esac\ndicttech_add_managed_conffiles '+shlex.quote(str(self.target))+'\n'
        result=subprocess.run(['sh','-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode==0,good,result.stderr)
        return result
    def test_explicit_binary_and_executable_plugin_survive_backup_list_and_tar(self):
        self.run_hook()
        paths=self.target.read_text().splitlines()
        for path in self.payloads:self.assertIn('/'+path,paths)
        self.run_hook();self.assertEqual(self.target.read_text().splitlines(),paths)
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w') as tar:
            for line in paths:
                path=line.removeprefix('/')
                if path in self.payloads:tar.add(self.root/path,arcname=path)
        with tarfile.open(fileobj=io.BytesIO(archive.getvalue())) as tar:
            for path,value in self.payloads.items():
                self.assertEqual(tar.extractfile(path).read(),value)
                self.assertEqual(tar.getmember(path).mode,int(self.modes[path],8))
        self.assertNotIn('/etc/xr-guest-vlan192.nft',paths)
    def test_missing_resource_or_wrong_mode_refuses_before_backup_list_mutation(self):
        for mode in ('missing','mode'):
            with self.subTest(mode=mode):
                target=self.root/'etc/custom/plugin.bin'
                if mode=='missing':target.unlink()
                else:target.write_bytes(self.payloads['etc/custom/plugin.bin']);target.chmod(0o644)
                self.run_hook(False);self.assertEqual(self.target.read_text(),'/etc/existing\n')
    def test_parent_symlink_and_file_symlink_are_never_traversed(self):
        target=self.root/'etc/custom/plugin.bin';target.unlink()
        outside=Path(self.temp.name)/'outside';outside.mkdir();(outside/'plugin.bin').write_bytes(b'outside')
        (self.root/'etc/custom').rmdir();(self.root/'etc/custom').symlink_to(outside,target_is_directory=True)
        self.run_hook(False);self.assertEqual((outside/'plugin.bin').read_bytes(),b'outside')
        (self.root/'etc/custom').unlink();(self.root/'etc/custom').mkdir();target.symlink_to(outside/'plugin.bin')
        self.run_hook(False);self.assertEqual(self.target.read_text(),'/etc/existing\n')
    def test_traversal_reserved_files_and_invalid_schema_rejected(self):
        original=copy.deepcopy(self.manifest)
        for value in ('../outside','etc/../shadow','etc/shadow','etc/gshadow','etc/openwrt-deploy','etc/openwrt-deploy/managed.json',
                      'etc/dicttech-ap','etc/dicttech-ap-bootstrap-key','etc/dicttech-ap-bootstrap-key/state',
                      'etc/dropbear/dropbear_ed25519_host_key'):
            self.manifest=copy.deepcopy(original);self.manifest['files'][-1]['path']=value;self.write_manifest()
            self.run_hook(False);self.assertEqual(self.target.read_text(),'/etc/existing\n')
        self.manifest=copy.deepcopy(original);self.manifest['schema_version']=2;self.write_manifest();self.run_hook(False)
    def test_ledger_symlink_and_world_readable_ledger_refused(self):
        self.ledger.chmod(0o644);self.run_hook(False)
        self.ledger.unlink();other=Path(self.temp.name)/'ledger';other.write_text(json.dumps(self.manifest));self.ledger.symlink_to(other)
        self.run_hook(False);self.assertEqual(self.target.read_text(),'/etc/existing\n')
    def test_intentional_luci_content_drift_is_preserved(self):
        (self.root/'etc/custom/plugin.bin').write_bytes(b'changed through administration')
        self.run_hook();self.assertIn('/etc/custom/plugin.bin',self.target.read_text())
    def test_uninitialized_machine_is_noop_but_incomplete_managed_marker_fails(self):
        self.ledger.unlink();(self.root/'etc/dicttech-ap/device.json').unlink()
        self.run_hook();self.assertEqual(self.target.read_text(),'/etc/existing\n')
        (self.root/'etc/dicttech-ap/device.json').write_text('{}')
        self.run_hook(False)


if __name__=='__main__':unittest.main(verbosity=2)
