#!/usr/bin/env python3
"""Build matching Full gateway/AP images from one immutable source snapshot."""
from __future__ import annotations
import argparse
import hashlib
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
FEEDS = 'configs/dicttech-feeds.conf'
LOCK_SCHEMA = 3
ARTIFACT_SCHEMA = 4
PUBLIC_VERSION = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-(?:alpha|beta|rc)\.[1-9][0-9]*)?'
ROLES = ('gateway', 'ap')


def need(value, message):
    if not value:
        raise ValueError(message)


def package_version(version):
    """Map the public prerelease name to APK's ordered version syntax."""
    need(isinstance(version, str) and re.fullmatch(PUBLIC_VERSION, version),
         'Invalid public version')
    return re.sub(r'-(alpha|beta|rc)\.([1-9][0-9]*)$', r'_\1\2', version)


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
    need(set(lock) == {'schema_version', 'version', 'upstream_base'} and
         lock['schema_version'] == LOCK_SCHEMA, 'Unsupported release lock')
    marker_version = package_version(lock['version'])
    profile=git(repo,'show',revision+':package/dicttech/xr1710g-full/Makefile')
    need(re.search(r'^PKG_VERSION:='+re.escape(marker_version)+r'$',profile,re.M), 'Release/profile version mismatch')
    commit = lock['upstream_base']
    need(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit), 'Use full commit IDs')
    need(git(repo, 'cat-file', '-t', commit) == 'commit', 'Source ID is not a commit')
    git(repo, 'merge-base', '--is-ancestor', commit, revision)
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
    wrtix = json.loads(git(repo, 'show', revision + ':configs/wrtix-source.json'))
    need(set(wrtix) == {'schema_version', 'revision', 'archive_sha256'} and wrtix['schema_version'] == 1 and
         re.fullmatch('[0-9a-f]{40}', wrtix['revision']) and re.fullmatch('[0-9a-f]{64}', wrtix['archive_sha256']), 'Invalid wrtix source pin')
    return {'release_commit': revision, 'wrtix_source': wrtix,
            'source_date_epoch': int(git(repo, 'show', '-s', '--format=%ct', revision)),
            **lock,
            'fixed_feeds': feed_rows, 'feed_text': feeds}


def source_check(directory, receipt):
    source = directory / 'source'
    need(receipt.get('schema_version') == ARTIFACT_SCHEMA, 'Unsupported build receipt schema')
    need(receipt['flavor'] == 'full' and receipt['role'] in ROLES and receipt['layout'] in ('legacy', 'ubi'), 'Invalid build receipt')
    expected = receipt['release_commit']
    need(receipt['source_commit'] == expected, 'Edition source does not match its fixed commit')
    need(git(source, 'rev-parse', 'HEAD') == expected, 'Source HEAD moved')
    need(receipt['source_tree'] == git(source, 'rev-parse', expected+'^{tree}'),
         'Source tree differs from the fixed commit')
    need(git(source, 'write-tree') == receipt['source_tree'], 'Prepared source index changed')
    need(not git(source, 'diff', '--name-only'), 'Prepared source files changed')
    need(not git(source, 'ls-files', '--others', '--exclude-standard'), 'Unexpected untracked source input')
    need(not (source / 'files').exists() and not (source / 'files').is_symlink(), 'Rootfs overlays are not release inputs')


def prepare(repo, directory, role, layout):
    need(role in ROLES and layout in ('legacy', 'ubi'), 'Invalid role/layout')
    need(not directory.is_symlink(), 'Build directory must not be a symlink')
    repo, directory = repo.resolve(), directory.resolve()
    need(not (repo/'files').exists() and not (repo/'files').is_symlink(), 'Rootfs overlays are not release inputs')
    need(not directory.is_relative_to(repo) and not repo.is_relative_to(directory), 'Build outside the source checkout')
    need(not directory.exists() and not directory.is_symlink(), 'Use a new build directory')
    p = plan(repo)
    directory.mkdir(parents=True)
    source = directory / 'source'
    run(['git', 'clone', '--shared', '--no-checkout', str(repo), str(source)], repo)
    source_commit = p['release_commit']
    run(['git', 'checkout', '--detach', source_commit], source)
    receipt = {k: v for k, v in p.items() if k != 'feed_text'}
    receipt['schema_version'] = ARTIFACT_SCHEMA
    # Keep the combined public build version distinct from the schema's version.
    receipt['build_version'] = p['version'] + '-' + p['release_commit'][:12]
    receipt.update({'flavor': 'full', 'role': role, 'layout': layout, 'source_commit': source_commit,
                    'source_tree': git(source, 'write-tree'),
                    'feed_lock_sha256': hashlib.sha256(p['feed_text'].encode()).hexdigest()})
    save(directory / 'source.json', receipt)
    source_check(directory, receipt)
    return receipt


def build(directory, jobs, prepare_only=False):
    d = json.loads((directory/'source.json').read_text());source = directory/'source'
    need(isinstance(jobs, int) and 1 <= jobs <= 256, 'Invalid parallelism')
    source_check(directory, d)
    locked = plan(ROOT)
    need(all(d[key] == locked[key] for key in ['release_commit','source_date_epoch','version','upstream_base','fixed_feeds','wrtix_source']),
         'Build receipt is not bound to this release controller')
    need(d['feed_lock_sha256'] == hashlib.sha256(locked['feed_text'].encode()).hexdigest(), 'Feed lock changed after preparation')
    env = {**os.environ, 'SOURCE_DATE_EPOCH': str(d['source_date_epoch'])}
    command = ['bash', 'scripts/dicttech/build-full.sh', '--layout', d['layout'],
               '--role', d['role'], '--jobs', str(jobs)]
    if prepare_only: command.append('--prepare-only')
    run(command, source, capture=False, env=env)
    source_check(directory, d)
    if prepare_only: return
    target = source/'bin/targets/airoha/an7581'
    profile = 'gemtek_xr1710g' + ('-ubi' if d['layout']=='ubi' else '')
    images = list(target.glob('*-'+profile+'-squashfs-sysupgrade.itb'))
    need(len(images)==1, 'Expected exactly one current sysupgrade image')
    image = images[0]; manifest = image.with_name(image.name.removesuffix('-squashfs-sysupgrade.itb')+'.manifest')
    need(manifest.is_file(), 'Matching manifest missing')
    package_dir = target/'dicttech-packages'/image.name.removesuffix('-squashfs-sysupgrade.itb')
    need(package_dir.is_dir(), 'Verified full-edition APK set missing')
    rootfs_audit = image.with_suffix('.rootfs.audit.json')
    audit = json.loads(rootfs_audit.read_text())
    need(audit.get('clean_image', {}).get('network_role') == d['role'],
         'Audited rootfs role differs from build receipt')
    public = directory/'public';public.mkdir()
    # Flat variant archives prevent collisions between config.buildinfo, APKs,
    # kernel ABI and checksums. Never upload bin/** or builder signing keys.
    prefix = 'xr1710g-full-'+d['role']+'-'+d['layout']+'-'+d['build_version']
    bundle=public/(prefix+'.tar.gz')
    with tarfile.open(bundle,'x:gz')as archive:
        for path in [image,manifest,*[target/n for n in ['config.buildinfo','feeds.buildinfo','version.buildinfo']]]:
            need(path.is_file() and not path.is_symlink(), 'Release metadata missing')
            archive.add(path,arcname=path.name,recursive=False)
        archive.add(directory/'source.json',arcname='source.json',recursive=False)
        metadata = target/'dicttech-wrtix-source.json'
        pin = json.loads(metadata.read_text())
        need(all(pin.get(key) == value for key, value in d['wrtix_source'].items()), 'Built wrtix package source differs from release pin')
        archive.add(metadata, arcname=metadata.name, recursive=False)
        wrtix_archive = target/'dicttech-wrtix-source.tar'
        need(wrtix_archive.is_file() and not wrtix_archive.is_symlink() and digest(wrtix_archive) == pin['archive_sha256'], 'Built wrtix source archive differs from pin')
        archive.add(wrtix_archive, arcname=wrtix_archive.name, recursive=False)
        for path in sorted(package_dir.iterdir()):
            need(path.is_file() and not path.is_symlink() and path.suffix=='.apk', 'Unexpected APK export member')
            archive.add(path,arcname='packages/'+path.name,recursive=False)
        for path in sorted(target.glob('*.audit.json')):
            archive.add(path,arcname='audit/'+path.name,recursive=False)
    release={**d,'passed':True,'image':image.name,'image_sha256':digest(image),
             'bundle':bundle.name,'bundle_sha256':digest(bundle)}
    source_archive=public/(prefix+'-source.tar.gz')
    with source_archive.open('xb') as archive:
        subprocess.run(['git','archive','--format=tar.gz',d['source_commit']],cwd=source,
                       stdout=archive,check=True)
    release.update(source_archive=source_archive.name,source_archive_sha256=digest(source_archive))
    download=public/(prefix+'-sysupgrade.itb')
    shutil.copyfile(image,download)
    save(public/(prefix+'.json'),release)
    (public/(prefix+'.sha256')).write_text(''.join(digest(p)+'  '+p.name+'\n'for p in [bundle,download,source_archive]))


def normalized_config(data):
    """Role is the only permitted Kconfig input difference."""
    return sorted(line for line in data.decode().splitlines()
                  if line and not line.startswith(('CONFIG_XR1710G_DEFAULT_ROLE_AP=',
                                                  '# CONFIG_XR1710G_DEFAULT_ROLE_AP ')))


def normalized_feeds(data, receipt):
    """Validate real feed identities; only the verified local wrtix path varies."""
    expected = {f['name']: ('src-git', f['url']+'^'+f['commit'])
                for f in receipt['fixed_feeds']}
    need(len(expected) == len(receipt['fixed_feeds']) and 'wrtix' not in expected,
         'Invalid actual feed pin inventory')
    seen = {}
    pin = receipt['wrtix_source']
    for line in data.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        need(len(fields) == 3, 'Invalid actual feed record')
        kind, name, source = fields
        need(name not in seen, 'Duplicate actual feed record')
        if name == 'wrtix':
            path = Path(source)
            need(kind == 'src-link' and path.is_absolute() and '..' not in path.parts and
                 path.parts[-3:] == ('tmp', 'wrtix-sources', pin['revision']),
                 'Invalid actual feed wrtix snapshot path')
            # The archive's exact bytes and revision are independently verified
            # before this function; a runner-local prefix is not source identity.
            seen[name] = (kind, pin['revision'], pin['archive_sha256'])
        else:
            need(name in expected and (kind, source) == expected[name],
                 'Invalid actual feed source: differs from locked revision')
            seen[name] = (kind, source)
    need(set(seen) == set(expected) | {'wrtix'}, 'Missing actual feed record')
    return seen


def assemble(inputs, output, revision, expected_upstream=None, expected_tree=None):
    """Accept only two audited roles with the same code, packages and policy."""
    need(not output.exists(), 'Use a new pair output directory')
    reports, manifests, configs, feed_receipts = [], [], [], []
    for path in inputs.rglob('xr1710g-*.json'):
        d = json.loads(path.read_text())
        need(d.get('schema_version') == ARTIFACT_SCHEMA and d.get('passed') is True and
             d.get('release_commit') == revision and d.get('source_commit') == revision and
             d.get('flavor') == 'full' and d.get('role') in ROLES,
             'Artifact has a different source, role or did not pass')
        need(re.fullmatch('[0-9a-f]{40}', d.get('source_tree', '')), 'Invalid source tree')
        if expected_upstream is not None:
            need(d['upstream_base'] == expected_upstream, 'Artifact upstream base changed')
        if expected_tree is not None:
            need(d['source_tree'] == expected_tree, 'Artifact source tree changed')
        package_version(d['version'])
        need(d['layout'] in ('legacy','ubi') and d['build_version'] == d['version']+'-'+revision[:12],
             'Invalid artifact layout/version')
        expected = 'xr1710g-full-'+d['role']+'-'+d['layout']+'-'+d['build_version']
        need(path.name == expected+'.json' and d['bundle'] == expected+'.tar.gz', 'Unexpected artifact name')
        bundle = path.parent/d['bundle']
        download = path.parent/(expected+'-sysupgrade.itb')
        source_archive = path.parent/d['source_archive']
        need(bundle.is_file() and not bundle.is_symlink() and digest(bundle) == d['bundle_sha256'], 'Bundle changed')
        need(download.is_file() and not download.is_symlink() and digest(download) == d['image_sha256'], 'Download image changed')
        need(d['source_archive'] == expected+'-source.tar.gz' and source_archive.is_file() and
             not source_archive.is_symlink() and digest(source_archive) == d['source_archive_sha256'], 'Source archive changed')
        with tarfile.open(bundle) as archive:
            members = archive.getmembers()
            need(all(m.isfile() and not m.name.startswith('/') and '..' not in Path(m.name).parts for m in members), 'Unsafe public archive')
            need(len({m.name for m in members}) == len(members), 'Duplicate archive names')
            image_name = d['image']
            need(Path(image_name).name == image_name and image_name.endswith('-squashfs-sysupgrade.itb'), 'Invalid image name')
            manifest = image_name.removesuffix('-squashfs-sysupgrade.itb')+'.manifest'
            allowed = {image_name, manifest, 'source.json', 'config.buildinfo', 'feeds.buildinfo',
                       'version.buildinfo', 'dicttech-wrtix-source.json', 'dicttech-wrtix-source.tar'}
            need(allowed <= {m.name for m in members}, 'Required public metadata absent')
            need(all(m.name in allowed or re.fullmatch(r'packages/[A-Za-z0-9+_.~:-]+\.apk',m.name) or
                     re.fullmatch(r'audit/[A-Za-z0-9_.-]+\.audit\.json',m.name) for m in members), 'Non-public file in release archive')
            pin = json.load(archive.extractfile('dicttech-wrtix-source.json'))
            need(all(pin.get(k) == v for k, v in d['wrtix_source'].items()), 'Artifact wrtix source differs from release pin')
            need(hashlib.sha256(archive.extractfile('dicttech-wrtix-source.tar').read()).hexdigest() == pin['archive_sha256'], 'Artifact wrtix archive differs from pin')
            inside = json.load(archive.extractfile('source.json'))
            need(set(inside) == set(d)-{'passed','image','image_sha256','bundle','bundle_sha256',
                                      'source_archive','source_archive_sha256'} and
                 all(inside[k] == d[k] for k in inside), 'Bundle source receipt differs')
            need(hashlib.sha256(archive.extractfile(image_name).read()).hexdigest() == d['image_sha256'], 'Image differs from receipt')
            audits = [json.load(archive.extractfile(m)) for m in members if m.name.endswith('.rootfs.audit.json')]
            need(len(audits) == 1 and audits[0].get('passed') is True and
                 audits[0].get('clean_image', {}).get('network_role') == d['role'], 'Rootfs role audit absent or mismatched')
            manifests.append(sorted(archive.extractfile(manifest).read().decode().splitlines()))
            configs.append(normalized_config(archive.extractfile('config.buildinfo').read()))
            feed_receipts.append(normalized_feeds(archive.extractfile('feeds.buildinfo').read(), d))
        reports.append((path,bundle,d))
    need(len(reports) == 2 and {d['role'] for _,_,d in reports} == set(ROLES), 'Both Full roles must succeed')
    first = reports[0][2]
    for _,_,d in reports[1:]:
        need(all(d[k] == first[k] for k in ['layout','build_version','upstream_base','source_tree',
                                          'fixed_feeds','feed_lock_sha256','wrtix_source','source_archive_sha256']), 'Roles have different release inputs')
    need(manifests[0] == manifests[1], 'Roles have different packages or package versions')
    need(configs[0] == configs[1], 'Roles have different driver or package configuration')
    need(feed_receipts[0] == feed_receipts[1], 'Roles have different actual feed revisions')
    output.mkdir(parents=True)
    for path,bundle,d in reports:
        for item in [path,bundle,path.parent/(path.stem+'-sysupgrade.itb'),path.parent/d['source_archive']]:
            shutil.copyfile(item, output/item.name)
    save(output/'release-pair.json', {'schema_version':ARTIFACT_SCHEMA,'release_commit':revision,
         'layout':first['layout'],'variants':[d for _,_,d in sorted(reports,key=lambda x:x[2]['role'])]})
    (output/'sha256sums').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(output.iterdir()) if p.is_file()))


def generate_notes(pair_path, output_path, template_path=None):
    pair = json.loads(pair_path.read_text())
    need(pair.get('schema_version') == ARTIFACT_SCHEMA, 'Unsupported release pair')
    revision = pair.get('release_commit')
    need(isinstance(revision,str) and re.fullmatch('[0-9a-f]{40}',revision), 'Invalid release source ID')
    variants = pair.get('variants', [])
    need(len(variants) == 2 and {d.get('role') for d in variants} == set(ROLES), 'Notes require both Full roles')
    need(pair.get('layout') in ('legacy','ubi'), 'Invalid release layout')
    for d in variants:
        need(d.get('schema_version') == ARTIFACT_SCHEMA and d.get('flavor') == 'full' and
             d.get('release_commit') == revision and d.get('source_commit') == revision and
             d.get('layout') == pair['layout'], 'Notes contain mixed source snapshots')
    text = (template_path or ROOT/'docs/xr1710g-release-notes.md').read_text().rstrip()
    text += '\n\n源码：`'+revision+'`；分区布局：`'+pair['layout']+'`。\n'
    text += '附件分别提供 Full gateway／ap 镜像、匹配 APK、源码与 `sha256sums`。\n'
    with output_path.open('x') as output: output.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('plan')
    p=sub.add_parser('prepare');p.add_argument('--role',choices=ROLES,required=True);p.add_argument('--layout',choices=['legacy','ubi'],default='legacy');p.add_argument('--directory',type=Path,required=True)
    p=sub.add_parser('build');p.add_argument('--directory',type=Path,required=True);p.add_argument('--jobs',type=int,default=2);p.add_argument('--prepare-only',action='store_true')
    p=sub.add_parser('assemble');p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('notes');p.add_argument('--pair',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action == 'plan':
        p=plan(ROOT);p.pop('feed_text');print(json.dumps(p,indent=2))
    elif args.action == 'prepare': print(json.dumps(prepare(ROOT,args.directory,args.role,args.layout),indent=2))
    elif args.action == 'assemble':
        p=plan(ROOT)
        assemble(args.inputs,args.output,p['release_commit'],p['upstream_base'],git(ROOT,'rev-parse',p['release_commit']+'^{tree}'))
    elif args.action == 'notes': generate_notes(args.pair,args.output)
    else: build(args.directory.resolve(),args.jobs,args.prepare_only)


if __name__ == '__main__':
    os.umask(0o022)
    try: main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit('Release build refused: '+str(exc)) from None
