#!/usr/bin/env python3
"""Synthetic Git snapshots and adversarial Full role release archives; no builds."""
import copy
import hashlib
import importlib.util
import io
import json
import shutil
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[1]/'release.py')
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.repo = self.root/'repo'; self.repo.mkdir()
        r.git(self.repo,'init','-q','-b','fixture-main')
        for key,value in [('user.name','Fixture'),('user.email','fixture@example.invalid'),('commit.gpgsign','false'),('core.hooksPath','/dev/null')]:
            r.git(self.repo,'config',key,value)
        self.put('base','base\n'); self.base = self.commit('upstream')
        self.put('.gitignore','/.config\n/files\n')
        self.put('package/dicttech/xr1710g-full/Makefile','PKG_VERSION:=3.1.0_alpha1\n')
        self.put('configs/xr1710g-release.json',json.dumps({'schema_version':3,'version':'3.1.0-alpha.1','upstream_base':self.base}))
        self.put('configs/dicttech-feeds.conf','src-git --directory=theme glass https://example.invalid/theme.git^'+'d'*40+'\n')
        self.pin={'schema_version':1,'revision':'a'*40,'archive_sha256':hashlib.sha256(b'wrtix fixture').hexdigest()}
        self.put('configs/wrtix-source.json',json.dumps(self.pin)); self.head=self.commit('Full release')
        self.tree=r.git(self.repo,'rev-parse','HEAD^{tree}')
        self.inputs=self.root/'inputs';self.inputs.mkdir()

    def put(self,name,data):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)

    def commit(self,title):
        r.git(self.repo,'add','.');r.git(self.repo,'commit','-qm',title);return r.git(self.repo,'rev-parse','HEAD')

    def output(self): return self.root/'pair'

    def artifact(self,role, *, manifest='kernel - 6.18~abi\nwrtix-agent - 1.0.0-r1\n',
                 config='CONFIG_PACKAGE_wrtix-agent=y\n', audit_role=None, update=None, extra=None):
        folder=self.inputs/role;folder.mkdir(exist_ok=True)
        d={k:v for k,v in r.plan(self.repo).items() if k!='feed_text'}
        d.update(schema_version=r.ARTIFACT_SCHEMA, flavor='full',role=role,layout='legacy',
                 source_commit=self.head,source_tree=self.tree,
                 build_version='3.1.0-alpha.1-'+self.head[:12],feed_lock_sha256='e'*64)
        if update:d.update(update)
        prefix='xr1710g-full-'+role+'-legacy-'+d['build_version']; image=prefix+'-squashfs-sysupgrade.itb'
        members={'source.json':json.dumps(d).encode(),image:b'image fixture '+role.encode(),
                 image.removesuffix('-squashfs-sysupgrade.itb')+'.manifest':manifest.encode(),
                 'config.buildinfo':(config+('CONFIG_XR1710G_DEFAULT_ROLE_AP=y\n' if role=='ap' else '# CONFIG_XR1710G_DEFAULT_ROLE_AP is not set\n')).encode(),
                 'feeds.buildinfo':b'fixed feeds\n','version.buildinfo':b'fixed version\n',
                 'dicttech-wrtix-source.json':json.dumps(self.pin).encode(),
                 'dicttech-wrtix-source.tar':b'wrtix fixture',
                 'audit/image.rootfs.audit.json':json.dumps({'passed':True,'clean_image':{'network_role':audit_role or role}}).encode()}
        if extra:members.update(extra)
        bundle=folder/(prefix+'.tar.gz')
        with tarfile.open(bundle,'w:gz')as a:
            for name,data in members.items():
                m=tarfile.TarInfo(name);m.size=len(data);a.addfile(m,io.BytesIO(data))
        download=folder/(prefix+'-sysupgrade.itb');download.write_bytes(members[image])
        source=folder/(prefix+'-source.tar.gz');source.write_bytes(b'git source fixture')
        d.update(passed=True,image=image,image_sha256=r.digest(download),bundle=bundle.name,
                 bundle_sha256=r.digest(bundle),source_archive=source.name,source_archive_sha256=r.digest(source))
        path=folder/(prefix+'.json');path.write_text(json.dumps(d));return path

    def pair(self,**kwargs):
        self.artifact('gateway');self.artifact('ap',**kwargs)

    def assemble(self):
        r.assemble(self.inputs,self.output(),self.head,self.base,self.tree)

    def test_both_roles_are_exact_same_source_without_moving_branches(self):
        before=r.git(self.repo,'show-ref')
        for role in r.ROLES:
            directory=self.root/role
            receipt=r.prepare(self.repo,directory,role,'legacy')
            self.assertEqual(receipt['source_commit'],self.head);self.assertEqual(receipt['source_tree'],self.tree)
            self.assertEqual(receipt['role'],role);self.assertEqual(receipt['flavor'],'full')
            self.assertEqual(receipt['schema_version'],4);r.source_check(directory,receipt)
        self.assertEqual(r.git(self.repo,'show-ref'),before)

    def test_retired_fixes_role_is_rejected(self):
        with self.assertRaises(ValueError):r.prepare(self.repo,self.root/'fixes','fixes','legacy')

    def test_dirty_and_rootfs_overlay_sources_are_rejected(self):
        self.put('untracked','private')
        with self.assertRaisesRegex(ValueError,'Commit'):r.plan(self.repo)
        (self.repo/'untracked').unlink();(self.repo/'files').mkdir()
        with self.assertRaisesRegex(ValueError,'overlays'):r.prepare(self.repo,self.root/'ap','ap','legacy')

    def test_checkout_and_receipt_tampering_are_rejected(self):
        out=self.root/'ap';d=r.prepare(self.repo,out,'ap','legacy')
        changed=copy.deepcopy(d);changed['source_commit']=self.base
        with self.assertRaisesRegex(ValueError,'source'):r.source_check(out,changed)
        (out/'source/base').write_text('modified')
        with self.assertRaisesRegex(ValueError,'files changed'):r.source_check(out,d)

    def test_old_lock_schema_rejected(self):
        self.put('configs/xr1710g-release.json',json.dumps({'schema_version':2,'version':'3.1.0-alpha.1','upstream_base':self.base,'fixes_commit':self.base}));self.commit('old schema')
        with self.assertRaisesRegex(ValueError,'lock'):r.plan(self.repo)

    def test_unpinned_feed_rejected(self):
        self.put('configs/dicttech-feeds.conf','src-git glass https://example.invalid/theme.git^main\n');self.commit('unpinned')
        with self.assertRaisesRegex(ValueError,'Unpinned'):r.plan(self.repo)

    def test_duplicate_feed_rejected(self):
        p=self.repo/'configs/dicttech-feeds.conf';p.write_text(p.read_text()*2);self.commit('duplicate')
        with self.assertRaisesRegex(ValueError,'Duplicate'):r.plan(self.repo)

    def test_marker_version_must_match_lock(self):
        self.put('package/dicttech/xr1710g-full/Makefile','PKG_VERSION:=3.0.0\n');self.commit('wrong marker')
        with self.assertRaisesRegex(ValueError,'version mismatch'):r.plan(self.repo)

    def test_version_mapping(self):
        for tag,expected in [('3.0.0','3.0.0'),('3.1.0-alpha.1','3.1.0_alpha1'),('3.1.0-beta.2','3.1.0_beta2'),('3.1.0-rc.3','3.1.0_rc3')]:self.assertEqual(r.package_version(tag),expected)
        for tag in ['3.0','3.0.0-alpha.0','3.0.0;echo secret','v3.0.0']:
            with self.assertRaises(ValueError):r.package_version(tag)

    def test_pair_accepts_roles_and_writes_checked_public_assets(self):
        self.pair();self.assemble();x=json.loads((self.output()/'release-pair.json').read_text())
        self.assertEqual({d['role'] for d in x['variants']},set(r.ROLES));self.assertEqual(len(list(self.output().glob('*'))),10)
        subprocess.run(['sha256sum','--check','sha256sums'],cwd=self.output(),stdout=subprocess.DEVNULL,check=True)

    def test_missing_or_duplicate_role_rejected(self):
        self.artifact('gateway')
        with self.assertRaisesRegex(ValueError,'Both'):self.assemble()

    def test_duplicate_role_rejected(self):
        self.pair();shutil.copytree(self.inputs/'gateway',self.inputs/'duplicate')
        with self.assertRaisesRegex(ValueError,'Both'):self.assemble()

    def test_old_artifact_schema_and_retired_flavor_rejected(self):
        self.pair(update={'schema_version':3,'flavor':'fixes'})
        with self.assertRaisesRegex(ValueError,'different source'):self.assemble()

    def test_different_actual_feeds_rejected(self):
        self.pair(extra={'feeds.buildinfo':b'different actual revision'})
        with self.assertRaisesRegex(ValueError,'actual feed'):self.assemble()

    def test_different_packages_rejected(self):
        self.pair(manifest='wrtix-agent - 9.9.9-r1\n')
        with self.assertRaisesRegex(ValueError,'different packages'):self.assemble()

    def test_different_kernel_config_rejected(self):
        self.pair(config='CONFIG_PACKAGE_wrtix-agent=y\nCONFIG_KERNEL_DEBUG_INFO=y\n')
        with self.assertRaisesRegex(ValueError,'configuration'):self.assemble()

    def test_wrong_actual_rootfs_role_rejected(self):
        self.pair(audit_role='gateway')
        with self.assertRaisesRegex(ValueError,'role audit'):self.assemble()

    def test_wrong_source_rejected(self):
        self.pair(update={'source_commit':self.base})
        with self.assertRaisesRegex(ValueError,'different source'):self.assemble()

    def test_wrtix_pin_changes_rejected(self):
        self.pair(update={'wrtix_source':{**self.pin,'revision':'b'*40}})
        with self.assertRaisesRegex(ValueError,'wrtix source'):self.assemble()

    def test_modified_bundle_rejected(self):
        self.pair();report=json.loads(next((self.inputs/'ap').glob('*.json')).read_text());bundle=self.inputs/'ap'/report['bundle'];bundle.write_bytes(b'tamper')
        with self.assertRaisesRegex(ValueError,'Bundle changed'):self.assemble()

    def test_modified_download_rejected(self):
        self.pair();next((self.inputs/'ap').glob('*.itb')).write_bytes(b'tamper')
        with self.assertRaisesRegex(ValueError,'Download image'):self.assemble()

    def test_private_or_unsafe_archive_member_rejected(self):
        self.pair(extra={'private/password.txt':b'synthetic'})
        with self.assertRaisesRegex(ValueError,'Non-public'):self.assemble()

    def test_traversal_member_rejected(self):
        self.pair(extra={'../outside':b'synthetic'})
        with self.assertRaisesRegex(ValueError,'Unsafe'):self.assemble()

    def test_notes_require_complete_new_schema_pair(self):
        self.pair();self.assemble();template=self.root/'notes';template.write_text('Public notes\n');out=self.root/'notes.md'
        r.generate_notes(self.output()/'release-pair.json',out,template)
        text=out.read_text();self.assertIn('gateway／ap',text);self.assertNotIn('fixes',text)
        x=json.loads((self.output()/'release-pair.json').read_text());x['variants'].pop();(self.output()/'release-pair.json').write_text(json.dumps(x))
        with self.assertRaisesRegex(ValueError,'both Full roles'):r.generate_notes(self.output()/'release-pair.json',self.root/'bad.md',template)


if __name__=='__main__':unittest.main()
