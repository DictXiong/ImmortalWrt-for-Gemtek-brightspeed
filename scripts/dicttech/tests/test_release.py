#!/usr/bin/env python3
"""Offline tests of real Git projections, source isolation and release pairing."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('release',Path(__file__).resolve().parents[1]/'release.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);cls.repo=cls.root/'repo';cls.repo.mkdir()
        for args in [('init','-q'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),
                     ('config','commit.gpgsign','false'),('config','core.hooksPath','/dev/null')]:r.git(cls.repo,*args)
        def put(name,text):
            p=cls.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        def commit(title):
            r.git(cls.repo,'add','.');r.git(cls.repo,'commit','-qm',title);return r.git(cls.repo,'rev-parse','HEAD')
        put('feeds.conf.default','src-git luci https://example.invalid/luci.git\n')
        put('.gitignore','/files\n/.config\n/feeds.conf\n')
        put('1710.config','CONFIG_PACKAGE_luci=y\nCONFIG_TARGET_MULTI_PROFILE=y\nCONFIG_TARGET_DEVICE_airoha_an7581_DEVICE_gemtek_xr1710g=y\n')
        cls.base=commit('Upstream')
        cls.fixes=[]
        for i in range(3):put('fix-'+str(i)+'.patch','reviewed '+str(i)+'\n');cls.fixes.append(commit('Fix '+str(i)))
        put('package/dicttech/owner','PUBLIC OWNER FIXTURE - NOT FOR COMMUNITY\n')
        put('package/dicttech/ap-profile/Makefile','PKG_VERSION:=2.1.0\n')
        put('configs/xr1710g-release.json',json.dumps({'schema_version':1,'version':'2.1.0','upstream_base':cls.base,'fixes':cls.fixes}))
        put('configs/xr1710g-fixes-feeds.conf','src-git luci https://example.invalid/luci.git^'+'d'*40+'\n')
        cls.head=commit('Personal policy and release metadata')

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def destination(self):return Path(tempfile.mkdtemp(dir=self.root))/ 'new'

    def test_real_git_projection_and_branches(self):
        before=r.git(self.repo,'show-ref')
        for flavor in r.FLAVORS:
            out=self.destination();d=r.prepare(self.repo,out,flavor,'legacy')
            self.assertEqual((out/'source/package/dicttech/owner').exists(),flavor=='full')
            self.assertEqual(d['release_commit'],self.head)
            for i in range(3):self.assertEqual((out/'source'/('fix-'+str(i)+'.patch')).read_text(),'reviewed '+str(i)+'\n')
            r.source_check(out,d)
        self.assertEqual(r.git(self.repo,'show-ref'),before)

    def test_modified_source_and_forged_tree_rejected(self):
        out=self.destination();d=r.prepare(self.repo,out,'fixes','legacy');source=out/'source'
        (source/'1710.config').write_text('tampered\n')
        with self.assertRaises(ValueError):r.source_check(out,d)
        r.git(source,'add','1710.config');d['source_tree']=r.git(source,'write-tree')
        with self.assertRaises(ValueError):r.source_check(out,d)

    def test_already_upstream_fix_and_dirty_controller(self):
        repo=self.destination();r.run(['git','clone','--shared',str(self.repo),str(repo)],self.root)
        for args in [('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),('config','commit.gpgsign','false')]:r.git(repo,*args)
        path=repo/'configs/xr1710g-release.json';d=json.loads(path.read_text());d['upstream_base']=self.fixes[0];path.write_text(json.dumps(d))
        with self.assertRaises(ValueError):r.plan(repo)
        r.git(repo,'add','.');r.git(repo,'commit','-qm','Record upstream adoption')
        result=r.prepare(repo,self.destination(),'fixes','legacy')
        self.assertEqual(result['applied_fixes'][0]['state'],'already_in_upstream')

    def test_overlay_and_destination_rejected(self):
        with self.assertRaises(ValueError):r.prepare(self.repo,self.repo/'danger','fixes','legacy')
        target=self.destination();target.symlink_to(target.parent/'absent')
        with self.assertRaises(ValueError):r.prepare(self.repo,target,'fixes','legacy')
        out=self.destination();d=r.prepare(self.repo,out,'fixes','legacy');(out/'source/files').mkdir()
        with self.assertRaises(ValueError):r.source_check(out,d)

    def test_one_partition_profile_stock_packages_kept(self):
        for layout in ['legacy','ubi']:
            text=r.stock_config((self.repo/'1710.config').read_text(),layout,'2.1.0-test','abc')
            self.assertIn('CONFIG_PACKAGE_luci=y',text)
            self.assertNotIn('CONFIG_TARGET_DEVICE_airoha',text)
            self.assertEqual(text.count('CONFIG_TARGET_airoha_an7581_DEVICE_'),1)
            self.assertNotIn('CONFIG_TARGET_MULTI_PROFILE=y',text)
            self.assertNotIn('CONFIG_PACKAGE_ap-profile',text)

    def test_rootfs_personal_credentials_and_policy_rejected(self):
        root=self.destination();root.mkdir();r.audit_fixes_rootfs(root,{'kernel':'test'})
        p=root/'etc/dropbear/authorized_keys';p.parent.mkdir(parents=True);p.write_text('ssh-ed25519 FIXTURE\n')
        with self.assertRaises(ValueError):r.audit_fixes_rootfs(root,{'kernel':'test'})
        p.unlink();p.symlink_to('/nonexistent')
        with self.assertRaises(ValueError):r.audit_fixes_rootfs(root,{'kernel':'test'})
        p.unlink()
        with self.assertRaises(ValueError):r.audit_fixes_rootfs(root,{'kernel':'test','ap-profile':'test'})

    def test_stock_and_personal_partition_audits_are_separate(self):
        spec=importlib.util.spec_from_file_location('fit_fixture',Path(__file__).with_name('test_ap_image.py'))
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        personal=fixture.check;stock=r.fixes_image_checker()
        fixture.check=stock
        for layout in ['legacy','ubi']:
            image=fixture.fit(fixture.device(layout),fixture.metadata(layout))
            self.assertTrue(stock.check_image(image,layout)['passed'])
            if layout=='legacy':
                with self.assertRaises(personal.ImageError):personal.check_image(image,layout)
        fixture.check=personal
        image=fixture.fit(fixture.device(),fixture.metadata())
        with self.assertRaises(stock.ImageError):stock.check_image(image,'legacy')

    def fake_bundle(self,folder,flavor,*,evil=False,revision=None):
        folder.mkdir(parents=True);revision=revision or self.head;version='2.1.0-'+revision[:12]
        prefix='xr1710g-'+flavor+'-legacy-'+version
        d={'passed':True,'flavor':flavor,'layout':'legacy','build_version':version,'release_commit':revision,
           'upstream_base':self.base,'fixes':self.fixes,'image':'fixture-squashfs-sysupgrade.itb'}
        image=b'INERT FIT FIXTURE';d['image_sha256']=__import__('hashlib').sha256(image).hexdigest()
        d['bundle']=prefix+'.tar.gz';bundle=folder/d['bundle']
        with tarfile.open(bundle,'w:gz')as t:
            rows={'source.json':json.dumps({k:v for k,v in d.items()if k not in ['passed','image','image_sha256','bundle']}).encode(),d['image']:image,'fixture.manifest':b'kernel - test\n',**{n:b'fixture'for n in ['config.buildinfo','feeds.buildinfo','version.buildinfo']}}
            if evil:rows['key-build.pem']=b'INERT PRIVATE KEY FIXTURE'
            for n,b in rows.items():
                m=tarfile.TarInfo(n);m.size=len(b);t.addfile(m,io.BytesIO(b))
        d['bundle_sha256']=r.digest(bundle);(folder/(prefix+'.json')).write_text(json.dumps(d))
        (folder/(prefix+'-sysupgrade.itb')).write_bytes(image)

    def test_complete_pair_and_hashes(self):
        inputs=self.destination();self.fake_bundle(inputs/'one','full');self.fake_bundle(inputs/'two','fixes')
        out=self.destination();r.assemble(inputs,out,self.head)
        self.assertEqual(len(list(out.glob('*.tar.gz'))),2)
        self.assertEqual(len(list(out.glob('*.itb'))),2)
        self.assertEqual(len(json.loads((out/'release-pair.json').read_text())['variants']),2)

    def test_missing_mixed_and_private_artifacts_rejected(self):
        for mode in ['missing','mixed','private','modified']:
            inputs=self.destination();self.fake_bundle(inputs/'one','full')
            if mode!='missing':self.fake_bundle(inputs/'two','fixes',evil=mode=='private',revision=self.base if mode=='mixed'else None)
            if mode=='modified':
                p=next((inputs/'two').glob('*.tar.gz'));p.write_bytes(p.read_bytes()+b'changed')
            with self.subTest(mode=mode),self.assertRaises(ValueError):r.assemble(inputs,self.destination(),self.head)


if __name__=='__main__':
    os.umask(0o022)
    unittest.main()
