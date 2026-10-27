#!/usr/bin/env python3
"""Real C7 ash/jshn against synthetic MTD/UBI files; no devices/network used."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[4]
PATCH=REPO/'patches/feeds/app_airoha/luci-app-airoha-factory/120-dicttech-readonly-split-dsd.patch'
spec=importlib.util.spec_from_file_location('rootfs_gate',REPO/'scripts/dicttech/check-ap-rootfs.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
ROM=None
QEMU=None


def source():
    return gate.patch_postimage(PATCH,'luci-app-airoha-factory/root/usr/libexec/rpcd/luci.airoha_factory').decode()


class Factory(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='factory-c7-')
        self.root=Path(self.temp.name);self.bin=self.root/'tools';self.bin.mkdir()
        self.rom=ROM;self.bb=self.rom/'bin/busybox'
        for rel in ('dev','proc','sys/class/mtd','sys/class/ubi','tmp/sysinfo','etc','lib/upgrade','usr/share/libubox','sys/class/net/br-lan'):
            (self.root/rel).mkdir(parents=True,exist_ok=True)
        self.put('tmp/sysinfo/board_name',b'gemtek,xr1710g\n')
        self.put('sys/class/net/br-lan/address',b'02:00:00:00:99:00\n')
        self.put('usr/share/libubox/jshn.sh',(self.rom/'usr/share/libubox/jshn.sh').read_bytes())
        for name in 'cat awk dd grep sed tr wc mktemp rm head hexdump cut'.split():
            self.wrap(name,self.bb,[name])
        self.wrap('jshn',self.rom/'usr/bin/jshn',[])
        self.tool('ubus','print('+repr(json.dumps({'hostname':'test-ap','board_name':'gemtek,xr1710g','model':'Synthetic XR1710G','kernel':'6.18.55','release':{'description':'DictTech AP synthetic','version':'2.1.0','revision':'fixture'}}))+')\n')
        for name in ('fw_printenv','fw_setenv','ubiupdatevol','mtd','reboot'):
            self.tool(name,"import pathlib,sys\nwith pathlib.Path("+repr(str(self.root/'sideeffects'))+").open('a') as f:f.write("+repr(name)+"+'\\n')\nsys.exit(79)\n")
        self.put('lib/upgrade/nand.sh',b'nand_find_ubi() { echo ubi0; }\nnand_find_volume() { echo ubi0_2; }\n')
        self.env={**os.environ,'PATH':str(self.bin),'TMPDIR':str(self.root/'tmp')}
        body=source()
        # Only the fixture's known synthetic device nodes are regular files.
        # All absolute paths (including /sbin/reboot) are confined to this tree.
        import re
        body=re.sub(r'(?<![A-Za-z0-9_}])/(?:usr/share|lib|tmp|etc|dev|proc|sys|sbin)/',lambda m:str(self.root/m[0].strip('/'))+'/',body)
        body=body.replace('[ -c ', '[ -f ')
        self.script=self.root/'backend';self.script.write_text(body)

    def tearDown(self):self.temp.cleanup()

    def put(self,rel,data):
        p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p

    def wrap(self,name,program,args):
        self.tool(name,'import os,sys\nargv='+repr([QEMU,'-L',str(self.rom),str(program),*args])+'+sys.argv[1:]\nos.execv(argv[0],argv)\n')

    def tool(self,name,body):
        p=self.bin/name;p.write_text('#!'+sys.executable+'\n'+body);p.chmod(0o700)

    @staticmethod
    def text(valid=True):
        data=bytearray(b'\xff'*131072)
        header=b'serial_number=SYNTHETIC-123\nadmin_password=never-return-this\n'
        data[:len(header)]=header
        data[len(header):100]=b'#'*(99-len(header))+b'\n'
        data[100:126]=b'wan_mac=02:11:22:33:44:50\n'
        data[126:152]=b'lan_mac=02:11:22:33:44:a9\n'
        if not valid:data[:4096]=b'\xff'*4096
        return bytes(data)

    def mtd(self,label,number=2,payload=None):
        self.put('proc/mtd',('dev: size erasesize name\nmtd%d: 00200000 00020000 "%s"\n'%(number,label)).encode())
        self.put('sys/class/mtd/mtd%d/name'%number,(label+'\n').encode())
        self.put('sys/class/mtd/mtd%d/flags'%number,b'0x400\n')
        return self.put('dev/mtd%d'%number,self.text()if payload is None else payload)

    def call(self,method='getStatus',data=b'{}'):
        r=subprocess.run([QEMU,'-L',str(self.rom),str(self.bb),'ash',str(self.script),'call',method],input=data,env=self.env,capture_output=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stderr.decode())
        try:return json.loads(r.stdout)
        except ValueError:self.fail('invalid JSON: '+r.stdout.decode(errors='replace')+' stderr='+r.stderr.decode(errors='replace'))

    def no_sideeffects(self):
        self.assertFalse((self.root/'sideeffects').exists())
        self.assertFalse((self.root/'etc/fw_env.factory.config').exists())

    def test_split_dsd_bounded_read_and_factory_current_separation(self):
        payload=bytearray(self.text())
        outside=b'serial_number=OUTSIDE-READ-BOUND\nwan_mac=02:ff:ff:ff:ff:ff\n'
        payload[8192:8192+len(outside)]=outside
        dev=self.mtd('dsd',payload=bytes(payload));before=hashlib.sha256(dev.read_bytes()).hexdigest()
        value=self.call()
        self.assertEqual(value['layout'],'raw-dsd');self.assertTrue(value['readable']);self.assertFalse(value['writable'])
        self.assertEqual(value['fields'],{'wan_mac':'02:11:22:33:44:50','lan_mac':'02:11:22:33:44:a9','serial_number':'SYNTHETIC-123'})
        self.assertEqual(value['metadata']['bridge_mac'],'02:00:00:00:99:00')
        self.assertEqual(value['metadata']['description'],'DictTech AP synthetic')
        self.assertNotIn('never-return-this',json.dumps(value));self.assertFalse(value['env']['available'])
        self.assertEqual(hashlib.sha256(dev.read_bytes()).hexdigest(),before);self.no_sideeffects()

    def test_raw_write_rejected_before_even_malformed_arguments(self):
        dev=self.mtd('dsd');before=dev.read_bytes()
        for data in (b'{not-json',b'{"fields":{"serial_number":"changed"}}'):
            value=self.call('setFactory',data);self.assertFalse(value['success']);self.assertIn('split raw DSD',value['error'])
        self.assertEqual(dev.read_bytes(),before);self.no_sideeffects()

    def test_missing_partition_still_returns_metadata(self):
        value=self.call();self.assertIn('error',value);self.assertFalse(value['writable'])
        self.assertEqual(value['fields'],{});self.assertEqual(value['metadata']['model'],'Synthetic XR1710G');self.no_sideeffects()

    def test_short_or_malformed_raw_data_does_not_invent_serial(self):
        for payload in (b'too short',self.text(False), b'serial_number\n'+b'\xff'*(131072-14)):
            with self.subTest(size=len(payload)):
                self.mtd('dsd',payload=payload);value=self.call();self.assertIn('error',value);self.assertEqual(value['fields'],{})
                self.assertEqual(value['metadata']['hostname'],'test-ap');self.no_sideeffects()

    def test_wrong_board_does_not_select_raw_partition(self):
        self.put('tmp/sysinfo/board_name',b'other,board\n');self.mtd('dsd');value=self.call()
        self.assertNotEqual(value['layout'],'raw-dsd');self.assertIn('error',value);self.no_sideeffects()

    def test_vendor_layout_keeps_aggregate_offset_and_priority(self):
        data=bytearray(b'\xff'*(4194304+131072));data[4194304:]=self.text()
        self.mtd('vendor',0,bytes(data));self.put('sys/class/mtd/mtd2/name',b'dsd\n');self.put('dev/mtd2',self.text(False))
        value=self.call();self.assertEqual(value['layout'],'legacy');self.assertTrue(value['writable'])
        self.assertEqual(value['fields']['serial_number'],'SYNTHETIC-123')
        self.assertEqual(value['fields']['lan_mac'],'02:11:22:33:44:a9')

    def test_ubi_layout_uses_its_original_text_offset(self):
        self.put('tmp/sysinfo/board_name',b'gemtek,xr1710g-ubi\n')
        self.put('dev/ubi0_2',self.text());self.put('sys/class/ubi/ubi0_2/name',b'factory\n')
        self.put('etc/fw_env.config',b'# existing synthetic env\n')
        value=self.call();self.assertEqual(value['layout'],'dsd');self.assertTrue(value['writable'])
        self.assertEqual(value['fields']['serial_number'],'SYNTHETIC-123')
        self.assertEqual(value['fields']['lan_mac'],'02:11:22:33:44:a9')
        self.assertEqual(value['ubi']['wan'],'02:11:22:33:44:50')

    def test_supported_layout_setter_dispatch_preserved_without_writes(self):
        self.mtd('vendor',0,b'\xff'*4198400)
        value=self.call('setFactory',b'{}');self.assertEqual(value['error'],'missing wan_mac')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rootfs',required=True,type=Path);p.add_argument('--qemu',default=shutil.which('qemu-aarch64'))
    args,remaining=p.parse_known_args();ROM=args.rootfs.resolve();QEMU=args.qemu
    if not QEMU:p.error('qemu-aarch64 is required')
    unittest.main(argv=[__file__]+remaining)
