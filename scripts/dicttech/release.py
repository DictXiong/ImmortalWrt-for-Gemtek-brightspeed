#!/usr/bin/env python3
"""Build two independently audited editions from one immutable release commit.

The fixes edition is an upstream checkout plus explicitly reviewed commits,
not a package-selection switch on the personal source tree. No branch is moved.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'configs/xr1710g-release.json'
FEEDS = 'configs/xr1710g-fixes-feeds.conf'
FLAVORS = ('full', 'fixes')
FORBIDDEN_PACKAGES = {'ap-profile', 'cli-profile', 'xr-roaming'}


def need(value, message):
    if not value:
        raise ValueError(message)


def run(args, cwd, *, data=None, capture=True, env=None):
    result = subprocess.run(args, cwd=cwd, input=data, env=env,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode and capture:
        sys.stderr.write((result.stderr or b'').decode(errors='replace')[-12000:])
    result.check_returncode()
    return result.stdout


def git(repo, *args):
    return run(['git', *args], repo).decode().strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as output:
        json.dump(value, output, indent=2); output.write('\n')


def plan(repo):
    revision = git(repo, 'rev-parse', 'HEAD')
    need(not git(repo, 'status', '--porcelain'), 'Commit the source before preparing a release')
    lock = json.loads(git(repo, 'show', revision + ':' + LOCK))
    need(set(lock) == {'schema_version', 'version', 'upstream_base', 'fixes'} and
         lock['schema_version'] == 1, 'Unsupported release lock')
    need(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', lock['version']), 'Invalid public version')
    profile=git(repo,'show',revision+':package/dicttech/ap-profile/Makefile')
    need(re.search(r'^PKG_VERSION:='+re.escape(lock['version'])+r'$',profile,re.M), 'Release/profile version mismatch')
    commits = [lock['upstream_base'], *lock['fixes']]
    need(isinstance(lock['fixes'], list) and len(lock['fixes']) == len(set(lock['fixes'])) and
         all(re.fullmatch('[0-9a-f]{40}', c) for c in commits), 'Use distinct full source commit IDs')
    for commit in commits:
        git(repo, 'merge-base', '--is-ancestor', commit, revision)
    paths = set()
    for commit in lock['fixes']:
        need(len(git(repo, 'rev-list', '--parents', '-n1', commit).split()) == 2,
             'Fix entries must be individual non-merge commits')
        paths.update(git(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines())
    feeds = git(repo, 'show', revision + ':' + FEEDS) + '\n'
    feed_rows = []
    for line in feeds.splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        need(fields[0] == 'src-git' and len(fields) in (3, 4), 'Invalid fixed feed entry')
        name, source = fields[-2:]
        url, pin = source.rsplit('^', 1)
        need(url.startswith('https://') and re.fullmatch('[0-9a-f]{40}', pin), 'Unpinned public feed')
        directory = fields[1].removeprefix('--directory=') if len(fields) == 4 else name
        need(re.fullmatch('[A-Za-z0-9_-]+', directory), 'Invalid feed directory')
        feed_rows.append({'name': name, 'directory': directory, 'url': url, 'commit': pin})
    need(len({x['name'] for x in feed_rows}) == len(feed_rows), 'Duplicate feed')
    stock_names = {line.split()[-2] for line in git(repo, 'show', lock['upstream_base'] + ':feeds.conf.default').splitlines()
                   if line.startswith('src-git ')}
    need({x['name'] for x in feed_rows} == stock_names, 'Fixes feed lock must cover exactly the upstream feeds')
    return {'schema_version': 1, 'release_commit': revision,
            'source_date_epoch': int(git(repo, 'show', '-s', '--format=%ct', revision)),
            **lock,
            'fix_paths': sorted(paths), 'fixed_feeds': feed_rows, 'feed_text': feeds}


def source_check(directory, receipt):
    source = directory / 'source'
    need(receipt['flavor'] in FLAVORS and receipt['layout'] in ('legacy', 'ubi'), 'Invalid build receipt')
    if receipt['flavor'] == 'full':
        need(receipt['source_tree'] == git(source, 'rev-parse', receipt['release_commit']+'^{tree}'),
             'Full source must be the exact release tree')
    else:
        changed = set(git(source, 'diff', '--name-only', receipt['upstream_base'], receipt['source_tree']).splitlines())
        need(changed <= set(receipt['fix_paths']), 'Unreviewed changes in fixes source')
        if receipt['fix_paths']:
            run(['git', 'diff', '--exit-code', receipt['release_commit'], receipt['source_tree'], '--', *receipt['fix_paths']], source)
    need(git(source, 'write-tree') == receipt['source_tree'], 'Prepared source index changed')
    need(not git(source, 'diff', '--name-only'), 'Prepared source files changed')
    need(not git(source, 'ls-files', '--others', '--exclude-standard'), 'Unexpected untracked source input')
    need(not (source / 'files').exists() and not (source / 'files').is_symlink(), 'Rootfs overlays are not release inputs')


def prepare(repo, directory, flavor, layout):
    need(flavor in FLAVORS and layout in ('legacy', 'ubi'), 'Invalid edition/layout')
    need(not directory.is_symlink(), 'Build directory must not be a symlink')
    repo, directory = repo.resolve(), directory.resolve()
    need(not directory.is_relative_to(repo) and not repo.is_relative_to(directory), 'Build outside the source checkout')
    need(not directory.exists() and not directory.is_symlink(), 'Use a new build directory')
    p = plan(repo)
    directory.mkdir(parents=True)
    source = directory / 'source'
    run(['git', 'clone', '--shared', '--no-checkout', str(repo), str(source)], repo)
    run(['git', 'checkout', '--detach', p['release_commit'] if flavor == 'full' else p['upstream_base']], source)
    applied = []
    if flavor == 'fixes':
        for commit in p['fixes']:
            patch = run(['git', 'show', '--format=', '--binary', commit], repo)
            check = subprocess.run(['git', 'apply', '--reverse', '--check', '-'], cwd=source,
                                   input=patch, capture_output=True)
            if check.returncode == 0:
                applied.append({'commit': commit, 'state': 'already_in_upstream'})
            else:
                run(['git', 'apply', '--index', '-'], source, data=patch)
                applied.append({'commit': commit, 'state': 'applied'})
        # A fix must have the same final contents in both editions. A later
        # conflicting modification requires reviewing/updating the release lock.
        if p['fix_paths']:
            run(['git', 'diff', '--exit-code', p['release_commit'], '--', *p['fix_paths']], source)
        need(not (source / 'package/dicttech').exists(), 'Personal packages leaked into fixes source')
    receipt = {k: v for k, v in p.items() if k != 'feed_text'}
    # Keep the combined public build version distinct from the schema's version.
    receipt['build_version'] = p['version'] + '-' + p['release_commit'][:12]
    receipt.update({'flavor': flavor, 'layout': layout, 'source_tree': git(source, 'write-tree'),
                    'applied_fixes': applied, 'fixes_feed_lock_sha256': hashlib.sha256(p['feed_text'].encode()).hexdigest()})
    save(directory / 'source.json', receipt)
    if flavor == 'fixes':
        (directory / 'feeds.lock').write_text(p['feed_text'])
    source_check(directory, receipt)
    return receipt


def stock_config(text, layout, version, revision):
    # Stock package/default policy, narrowed to one selected partition profile.
    keys = ('CONFIG_TARGET_MULTI_PROFILE', 'CONFIG_TARGET_PER_DEVICE_ROOTFS',
            'CONFIG_TARGET_DEVICE_', 'CONFIG_TARGET_airoha_an7581_DEVICE_',
            'CONFIG_VERSION_DIST', 'CONFIG_VERSION_NUMBER', 'CONFIG_VERSION_CODE',
            'CONFIG_VERSION_FILENAMES', 'CONFIG_VERSION_CODE_FILENAMES')
    lines = [line for line in text.splitlines() if not line.removeprefix('# ').startswith(keys)]
    profile = 'gemtek_xr1710g' + ('-ubi' if layout == 'ubi' else '')
    lines += ['# CONFIG_TARGET_MULTI_PROFILE is not set', '# CONFIG_TARGET_PER_DEVICE_ROOTFS is not set',
              'CONFIG_TARGET_airoha_an7581_DEVICE_' + profile + '=y',
              'CONFIG_VERSION_DIST="Gemtek Fixes"', 'CONFIG_VERSION_NUMBER="' + version + '"',
              'CONFIG_VERSION_CODE="' + revision + '"', 'CONFIG_VERSION_FILENAMES=y',
              '# CONFIG_VERSION_CODE_FILENAMES is not set']
    return '\n'.join(lines) + '\n'


def module(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/dicttech' / filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def fixes_image_checker():
    checker = module('check-ap-image.py', 'release_fit')
    # Strict stock layout, not a relaxation of the personal six-partition audit.
    checker.PARTITIONS = dict(checker.PARTITIONS)
    checker.PARTITIONS['legacy'] = [('vendor', 0, 0x600000, False),
        ('chainloader', 0x600000, 0x100000, True), ('ubi', 0x700000, 0x1b700000, False),
        ('reserved_bmt', 0x1be00000, 0x4200000, True)]
    return checker


def community_audit(source, image, manifest, layout, output):
    checker = fixes_image_checker()
    sink = {}
    fit = checker.check_image(image.read_bytes(), layout, rootfs_sink=sink)
    squash = output / 'rootfs.squashfs'; squash.write_bytes(sink['payload'])
    rootfs = output / 'rootfs'
    run([str(source/'staging_dir/host/bin/unsquashfs4'), '-no-progress', '-no-xattrs', '-excludes',
         '-d', str(rootfs), str(squash), 'dev'], source)
    packages = module('check-ap-packages.py', 'release_packages')
    installed = packages.read_manifest(manifest)
    audit_fixes_rootfs(rootfs, installed)
    fit['modules'] = checker.check_modules(rootfs, installed['kernel'].split('~',1)[0])
    report = packages.check_packages(manifest,
        [source/'bin/targets/airoha/an7581/packages', source/'bin/packages/aarch64_cortex-a53'],
        rootfs, source/'staging_dir/host/bin/apk', output/'packages')
    save(output/'image.audit.json', fit); save(output/'packages.audit.json', report)


def audit_fixes_rootfs(rootfs, installed):
    need(not FORBIDDEN_PACKAGES.intersection(installed), 'Personal package in fixes manifest')
    for relative in ['usr/share/dicttech-ap', 'usr/share/dicttech-cli', 'usr/lib/xr-roaming',
                     'etc/dicttech-ap', 'etc/dicttech-ap-bootstrap-key']:
        need(not (rootfs/relative).exists() and not (rootfs/relative).is_symlink(),
             'Personal runtime policy in fixes rootfs')
    for relative in ['etc/dropbear/authorized_keys', 'root/.ssh/authorized_keys']:
        path = rootfs/relative
        need(not path.is_symlink() and (not path.exists() or not path.read_bytes().strip()),
             'Pre-authorized SSH identity in fixes rootfs')
    need(not list((rootfs/'etc/dropbear').glob('dropbear_*_host_key')) and
         not list((rootfs/'etc/ssh').glob('ssh_host_*_key')), 'SSH private host key in public fixes image')


def build(directory, jobs, prepare_only=False):
    d = json.loads((directory/'source.json').read_text());source = directory/'source'
    need(isinstance(jobs, int) and 1 <= jobs <= 256, 'Invalid parallelism')
    source_check(directory, d)
    locked = plan(ROOT)
    need(all(d[key] == locked[key] for key in ['release_commit','source_date_epoch','version','upstream_base','fixes','fix_paths','fixed_feeds']),
         'Build receipt is not bound to this release controller')
    env = {**os.environ, 'SOURCE_DATE_EPOCH': str(d['source_date_epoch'])}
    def shell(script):
        run(['bash', '-Eeuo', 'pipefail', '-c', 'umask 022\n'+script], source, capture=False, env=env)
    if d['flavor'] == 'full':
        command = ['bash', 'scripts/dicttech/build-ap.sh', '--layout', d['layout'], '--jobs', str(jobs)]
        if prepare_only: command.append('--prepare-only')
        run(command, source, capture=False, env=env)
    else:
        shutil.copyfile(directory/'feeds.lock', source/'feeds.conf')
        need(digest(source/'feeds.conf') == d['fixes_feed_lock_sha256'], 'Feed lock changed after preparation')
        shell('./scripts/feeds update -a\n./scripts/feeds install -a\nFEED_PATCHES_STRICT=1 bash scripts/apply-feed-patches.sh\nbash scripts/fix-stale-golang-host.sh')
        for feed in d['fixed_feeds']:
            need(git(source/'feeds'/feed['directory'], 'rev-parse', 'HEAD') == feed['commit'], 'Wrong fixed feed')
        (source/'.config').write_text(stock_config((source/'1710.config').read_text(),d['layout'],d['build_version'],d['release_commit'][:12]))
        shell('make defconfig\nbash scripts/check-gemtek-profile-isolation.sh --config .config')
        if not prepare_only:
            shell('make download -j'+str(jobs)+'\nmake world -j'+str(jobs))
    source_check(directory, d)
    if prepare_only: return
    target = source/'bin/targets/airoha/an7581'
    profile = 'gemtek_xr1710g' + ('-ubi' if d['layout']=='ubi' else '')
    images = list(target.glob('*-'+profile+'-squashfs-sysupgrade.itb'))
    need(len(images)==1, 'Expected exactly one current sysupgrade image')
    image = images[0]; manifest = image.with_name(image.name.removesuffix('-squashfs-sysupgrade.itb')+'.manifest')
    need(manifest.is_file(), 'Matching manifest missing')
    output = directory/'audit'; output.mkdir()
    if d['flavor']=='fixes':
        community_audit(source, image, manifest, d['layout'], output)
        package_dir = output/'packages'
    else:
        package_dir = target/'dicttech-packages'/image.name.removesuffix('-squashfs-sysupgrade.itb')
        need(package_dir.is_dir(), 'Verified full-edition APK set missing')
    public = directory/'public';public.mkdir()
    # Flat variant archives prevent collisions between config.buildinfo, APKs,
    # kernel ABI and checksums. Never upload bin/** or builder signing keys.
    prefix = 'xr1710g-'+d['flavor']+'-'+d['layout']+'-'+d['build_version']
    bundle=public/(prefix+'.tar.gz')
    with tarfile.open(bundle,'x:gz')as archive:
        for path in [image,manifest,*[target/n for n in ['config.buildinfo','feeds.buildinfo','version.buildinfo']]]:
            need(path.is_file() and not path.is_symlink(), 'Release metadata missing')
            archive.add(path,arcname=path.name,recursive=False)
        archive.add(directory/'source.json',arcname='source.json',recursive=False)
        for path in sorted(package_dir.iterdir()):
            need(path.is_file() and not path.is_symlink() and path.suffix=='.apk', 'Unexpected APK export member')
            archive.add(path,arcname='packages/'+path.name,recursive=False)
        for path in sorted((output if d['flavor']=='fixes' else target).glob('*.audit.json')):
            archive.add(path,arcname='audit/'+path.name,recursive=False)
    release={**d,'passed':True,'image':image.name,'image_sha256':digest(image),
             'bundle':bundle.name,'bundle_sha256':digest(bundle)}
    download=public/(prefix+'-sysupgrade.itb')
    shutil.copyfile(image,download)
    save(public/(prefix+'.json'),release)
    (public/(prefix+'.sha256')).write_text(''.join(digest(p)+'  '+p.name+'\n'for p in [bundle,download]))


def assemble(inputs, output, revision):
    """Publish only a complete pair, with names and bytes checked before copying."""
    need(not output.exists(), 'Use a new pair output directory')
    reports = []
    for path in inputs.rglob('xr1710g-*.json'):
        d = json.loads(path.read_text())
        need(d.get('passed') is True and d.get('release_commit') == revision and d.get('flavor') in FLAVORS,
             'Artifact has a different source or did not pass')
        need(d['layout'] in ('legacy','ubi') and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-'+revision[:12],d['build_version']),
             'Invalid artifact layout/version')
        expected = 'xr1710g-'+d['flavor']+'-'+d['layout']+'-'+d['build_version']
        need(path.name == expected+'.json' and d['bundle'] == expected+'.tar.gz', 'Unexpected artifact name')
        bundle=path.parent/d['bundle']
        need(bundle.is_file() and not bundle.is_symlink() and digest(bundle)==d['bundle_sha256'], 'Bundle changed')
        download=path.parent/(expected+'-sysupgrade.itb')
        need(download.is_file() and not download.is_symlink() and digest(download)==d['image_sha256'], 'Download image changed')
        # Inspect the archive without extracting any path or following links.
        with tarfile.open(bundle) as archive:
            members = archive.getmembers()
            need(all(m.isfile() and not m.name.startswith('/') and '..' not in Path(m.name).parts for m in members), 'Unsafe public archive')
            need(len({m.name for m in members}) == len(members), 'Duplicate archive names')
            image_name=d['image'];need(Path(image_name).name==image_name and image_name.endswith('-squashfs-sysupgrade.itb'),'Invalid image name')
            allowed={image_name,image_name.removesuffix('-squashfs-sysupgrade.itb')+'.manifest',
                     'source.json','config.buildinfo','feeds.buildinfo','version.buildinfo'}
            need(all(m.name in allowed or re.fullmatch(r'packages/[A-Za-z0-9+_.~:-]+\.apk',m.name) or
                     re.fullmatch(r'audit/[A-Za-z0-9_.-]+\.audit\.json',m.name) for m in members),
                 'Non-public file in release archive')
            need(allowed <= {m.name for m in members},'Required public metadata absent')
            inside=json.load(archive.extractfile('source.json'))
            need(set(inside)==set(d)-{'passed','image','image_sha256','bundle','bundle_sha256'}, 'Incomplete source receipt')
            need(all(inside[k]==d[k] for k in inside), 'Bundle source receipt differs')
            image=archive.extractfile(d['image'])
            need(image is not None and hashlib.sha256(image.read()).hexdigest()==d['image_sha256'], 'Image differs from receipt')
        reports.append((path,bundle,d))
    need(len(reports)==2 and {d['flavor'] for _,_,d in reports}==set(FLAVORS), 'Both editions must succeed')
    need(len({(d['layout'],d['build_version'],d['upstream_base'],tuple(d['fixes'])) for _,_,d in reports})==1,
         'Editions have different release inputs')
    output.mkdir(parents=True)
    for path,bundle,d in reports:
        shutil.copyfile(path,output/path.name);shutil.copyfile(bundle,output/bundle.name)
        download=path.parent/(path.stem+'-sysupgrade.itb')
        shutil.copyfile(download,output/download.name)
    save(output/'release-pair.json',{'schema_version':1,'release_commit':revision,
        'layout':reports[0][2]['layout'],'variants':[d for _,_,d in sorted(reports,key=lambda x:x[2]['flavor'])]})
    (output/'sha256sums').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(output.iterdir()) if p.is_file()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('plan')
    p=sub.add_parser('prepare');p.add_argument('--flavor',choices=FLAVORS,required=True);p.add_argument('--layout',choices=['legacy','ubi'],default='legacy');p.add_argument('--directory',type=Path,required=True)
    p=sub.add_parser('build');p.add_argument('--directory',type=Path,required=True);p.add_argument('--jobs',type=int,default=2);p.add_argument('--prepare-only',action='store_true')
    p=sub.add_parser('assemble');p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='plan':
        p=plan(ROOT);p.pop('feed_text');print(json.dumps(p,indent=2))
    elif args.action=='prepare':print(json.dumps(prepare(ROOT,args.directory,args.flavor,args.layout),indent=2))
    elif args.action=='assemble':assemble(args.inputs,args.output,git(ROOT,'rev-parse','HEAD'))
    else:build(args.directory.resolve(),args.jobs,args.prepare_only)


if __name__=='__main__':
    # Public sources stay public even if invoked from a private deployment shell.
    os.umask(0o022)
    try:main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit('Release build refused: '+str(exc)) from None
