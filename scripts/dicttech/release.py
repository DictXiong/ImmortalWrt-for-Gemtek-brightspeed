#!/usr/bin/env python3
"""Build matching Full gateway/AP images from one immutable source snapshot."""
from __future__ import annotations
import argparse
from datetime import datetime
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
ARTIFACT_SCHEMA = 6
LOCAL_FEEDS = ('wrtix', 'wrtmesh')
LOCAL_SOURCE_KEYS = tuple(f'{name}_source' for name in LOCAL_FEEDS)
PUBLIC_VERSION = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-(?:alpha|beta|rc)\.[1-9][0-9]*)?'
ROLES = ('gateway', 'ap')
DEVICES = {'xr1710g': ('legacy', 'ubi'), 'w1701k': ('ubi',)}
DEVICE_PHY_PACKAGES = {
    'xr1710g': {'kmod-phy-realtek', 'rtl826x-firmware'},
    'w1701k': {'airoha-en8811h-firmware', 'kmod-phy-airoha-en8811h'},
}


def need(value, message):
    if not value:
        raise ValueError(message)


def package_version(version):
    """Validate date releases and map historical prerelease names for APK."""
    if isinstance(version, str) and re.fullmatch(r'[0-9]{8}', version):
        datetime.strptime(version, '%Y%m%d')
        return version
    need(isinstance(version, str) and re.fullmatch(PUBLIC_VERSION, version),
         'Invalid public version')
    return re.sub(r'-(alpha|beta|rc)\.([1-9][0-9]*)$', r'_\1\2', version)


def release_tag(version):
    package_version(version)
    return version if version.isdigit() else 'v' + version


def asset_prefix(receipt):
    device = receipt['device']
    project = f'immortalwrt-{device}' if receipt['version'].isdigit() else f'{device}-full'
    return f"{project}-{receipt['role']}-{receipt['layout']}-{receipt['build_version']}"


def board_id(device, layout):
    return 'gemtek,' + device + ('-ubi' if layout == 'ubi' else '')


def valid_layout(device, layout):
    return layout in DEVICES.get(device, ())


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
    sources = {}
    for name in LOCAL_FEEDS:
        pin = json.loads(git(repo, 'show', revision + f':configs/{name}-source.json'))
        need(set(pin) == {'schema_version', 'revision', 'archive_sha256'} and pin['schema_version'] == 1 and
             re.fullmatch('[0-9a-f]{40}', pin['revision']) and
             re.fullmatch('[0-9a-f]{64}', pin['archive_sha256']), f'Invalid {name} source pin')
        sources[f'{name}_source'] = pin
    return {'release_commit': revision, **sources,
            'source_date_epoch': int(git(repo, 'show', '-s', '--format=%ct', revision)),
            **lock,
            'fixed_feeds': feed_rows, 'feed_text': feeds}


def source_check(directory, receipt):
    source = directory / 'source'
    need(receipt.get('schema_version') == ARTIFACT_SCHEMA, 'Unsupported build receipt schema')
    need(receipt['flavor'] == 'full' and receipt['role'] in ROLES and
         valid_layout(receipt['device'], receipt['layout']), 'Invalid build receipt')
    expected = receipt['release_commit']
    need(receipt['source_commit'] == expected, 'Edition source does not match its fixed commit')
    need(git(source, 'rev-parse', 'HEAD') == expected, 'Source HEAD moved')
    need(receipt['source_tree'] == git(source, 'rev-parse', expected+'^{tree}'),
         'Source tree differs from the fixed commit')
    need(git(source, 'write-tree') == receipt['source_tree'], 'Prepared source index changed')
    need(not git(source, 'diff', '--name-only'), 'Prepared source files changed')
    need(not git(source, 'ls-files', '--others', '--exclude-standard'), 'Unexpected untracked source input')
    need(not (source / 'files').exists() and not (source / 'files').is_symlink(), 'Rootfs overlays are not release inputs')


def prepare(repo, directory, role, layout=None, *, device='xr1710g'):
    need(device in DEVICES, 'Invalid device')
    layout = layout or DEVICES[device][0]
    need(role in ROLES and valid_layout(device, layout), 'Invalid role/layout')
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
    receipt.update({'flavor': 'full', 'role': role, 'device': device, 'layout': layout, 'source_commit': source_commit,
                    'source_tree': git(source, 'write-tree'),
                    'feed_lock_sha256': hashlib.sha256(p['feed_text'].encode()).hexdigest()})
    save(directory / 'source.json', receipt)
    source_check(directory, receipt)
    return receipt


def build(directory, jobs, prepare_only=False, reuse_feeds=False):
    d = json.loads((directory/'source.json').read_text());source = directory/'source'
    need(isinstance(jobs, int) and 1 <= jobs <= 256, 'Invalid parallelism')
    source_check(directory, d)
    locked = plan(ROOT)
    need(all(d[key] == locked[key] for key in ['release_commit','source_date_epoch','version','upstream_base','fixed_feeds', *LOCAL_SOURCE_KEYS]),
         'Build receipt is not bound to this release controller')
    need(d['feed_lock_sha256'] == hashlib.sha256(locked['feed_text'].encode()).hexdigest(), 'Feed lock changed after preparation')
    env = {**os.environ, 'SOURCE_DATE_EPOCH': str(d['source_date_epoch'])}
    command = ['bash', 'scripts/dicttech/build-full.sh', '--device', d['device'], '--layout', d['layout'],
               '--role', d['role'], '--jobs', str(jobs)]
    if prepare_only: command.append('--prepare-only')
    if reuse_feeds: command.append('--reuse-feeds')
    run(command, source, capture=False, env=env)
    source_check(directory, d)
    if prepare_only: return
    target = source/'bin/targets/airoha/an7581'
    profile = board_id(d['device'], d['layout']).replace(',', '_', 1)
    images = list(target.glob('*-'+d['build_version']+'-airoha-an7581-'+profile+'-squashfs-sysupgrade.itb'))
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
    prefix = asset_prefix(d)
    bundle=public/(prefix+'.tar.gz')
    with tarfile.open(bundle,'x:gz')as archive:
        for path in [image,manifest,*[target/n for n in ['config.buildinfo','feeds.buildinfo','version.buildinfo']]]:
            need(path.is_file() and not path.is_symlink(), 'Release metadata missing')
            archive.add(path,arcname=path.name,recursive=False)
        archive.add(directory/'source.json',arcname='source.json',recursive=False)
        for name in LOCAL_FEEDS:
            metadata = target / f'dicttech-{name}-source.json'
            pin = json.loads(metadata.read_text())
            need(all(pin.get(key) == value for key, value in d[f'{name}_source'].items()),
                 f'Built {name} package source differs from release pin')
            archive.add(metadata, arcname=metadata.name, recursive=False)
            source_tar = target / f'dicttech-{name}-source.tar'
            need(source_tar.is_file() and not source_tar.is_symlink() and
                 digest(source_tar) == pin['archive_sha256'],
                 f'Built {name} source archive differs from pin')
            archive.add(source_tar, arcname=source_tar.name, recursive=False)
        for path in sorted(package_dir.iterdir()):
            need(path.is_file() and not path.is_symlink() and path.suffix=='.apk', 'Unexpected APK export member')
            archive.add(path,arcname='packages/'+path.name,recursive=False)
        for path in sorted(target.glob(image.stem + '*.audit.json')):
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
    """Validate real feed identities; only verified local feed paths vary."""
    expected = {f['name']: ('src-git', f['url']+'^'+f['commit'])
                for f in receipt['fixed_feeds']}
    need(len(expected) == len(receipt['fixed_feeds']) and not set(LOCAL_FEEDS).intersection(expected),
         'Invalid actual feed pin inventory')
    seen = {}
    for line in data.decode().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        need(len(fields) == 3, 'Invalid actual feed record')
        kind, name, source = fields
        need(name not in seen, 'Duplicate actual feed record')
        if name in LOCAL_FEEDS:
            pin = receipt[f'{name}_source']
            path = Path(source)
            need(kind == 'src-link' and path.is_absolute() and '..' not in path.parts and
                 path.parts[-3:] == ('tmp', f'{name}-sources', pin['revision']),
                 f'Invalid actual feed {name} snapshot path')
            # The archive's exact bytes and revision are independently verified
            # before this function; a runner-local prefix is not source identity.
            seen[name] = (kind, pin['revision'], pin['archive_sha256'])
        else:
            need(name in expected and (kind, source) == expected[name],
                 'Invalid actual feed source: differs from locked revision')
            seen[name] = (kind, source)
    need(set(seen) == set(expected) | set(LOCAL_FEEDS), 'Missing actual feed record')
    return seen


def comparable_manifest(device, manifest):
    packages = set()
    for line in manifest:
        if not line or line.startswith('#'):
            continue
        name, separator, version = line.partition(' - ')
        need(separator and name and version, 'Malformed Full package manifest')
        if name not in DEVICE_PHY_PACKAGES[device]:
            # The module ABI hash is device-specific even for the same kernel.
            packages.add((name, version.split('~', 1)[0] if name == 'kernel' else version))
    return packages


def assemble(inputs, output, revision, expected_upstream=None, expected_tree=None, *, layouts):
    """Validate both roles per device and the source and feeds across the release."""
    need(not output.exists(), 'Use a new pair output directory')
    need(layouts and all(valid_layout(*item) for item in layouts.items()), 'Invalid release layouts')
    reports, manifests, configs, feed_receipts = {}, {}, {}, {}
    reports_paths = [path for device in DEVICES for pattern in
                     (f'{device}-full-*.json', f'immortalwrt-{device}-*.json')
                     for path in inputs.rglob(pattern)]
    for path in reports_paths:
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
        device = d['device']
        key = (device, d['role'])
        need(device in layouts and layouts[device] == d['layout'] and
             d['build_version'] == d['version']+'-'+revision[:12], 'Invalid artifact device/layout/version')
        need(key not in reports, 'Both Full roles must succeed exactly once per device')
        expected = asset_prefix(d)
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
            profile = board_id(device, d['layout']).replace(',', '_', 1)
            need(Path(image_name).name == image_name and
                 image_name.endswith(f"-{d['build_version']}-airoha-an7581-{profile}-squashfs-sysupgrade.itb"),
                 'Invalid image name for device/layout')
            manifest = image_name.removesuffix('-squashfs-sysupgrade.itb')+'.manifest'
            allowed = {image_name, manifest, 'source.json', 'config.buildinfo', 'feeds.buildinfo',
                       'version.buildinfo'} | {
                           f'dicttech-{name}-source.{ext}' for name in LOCAL_FEEDS for ext in ('json', 'tar')
                       }
            need(allowed <= {m.name for m in members}, 'Required public metadata absent')
            need(all(m.name in allowed or re.fullmatch(r'packages/[A-Za-z0-9+_.~:-]+\.apk',m.name) or
                     re.fullmatch(r'audit/[A-Za-z0-9_.-]+\.audit\.json',m.name) for m in members), 'Non-public file in release archive')
            for name in LOCAL_FEEDS:
                pin = json.load(archive.extractfile(f'dicttech-{name}-source.json'))
                need(all(pin.get(key) == value for key, value in d[f'{name}_source'].items()),
                     f'Artifact {name} source differs from release pin')
                source_tar = archive.extractfile(f'dicttech-{name}-source.tar')
                need(hashlib.sha256(source_tar.read()).hexdigest() == pin['archive_sha256'],
                     f'Artifact {name} archive differs from pin')
            inside = json.load(archive.extractfile('source.json'))
            need(set(inside) == set(d)-{'passed','image','image_sha256','bundle','bundle_sha256',
                                      'source_archive','source_archive_sha256'} and
                 all(inside[k] == d[k] for k in inside), 'Bundle source receipt differs')
            need(hashlib.sha256(archive.extractfile(image_name).read()).hexdigest() == d['image_sha256'], 'Image differs from receipt')
            audits = [json.load(archive.extractfile(m)) for m in members if m.name.endswith('.rootfs.audit.json')]
            need(len(audits) == 1 and audits[0].get('passed') is True and
                 audits[0].get('clean_image', {}).get('network_role') == d['role'], 'Rootfs role audit absent or mismatched')
            image_audit = 'audit/' + image_name.removesuffix('.itb') + '.audit.json'
            need(image_audit in archive.getnames(), 'Image device audit absent')
            audited_image = json.load(archive.extractfile(image_audit))
            need(audited_image.get('passed') is True and audited_image.get('layout') == d['layout'] and
                 audited_image.get('compatible', [])[:1] == [board_id(device, d['layout'])] and
                 audited_image.get('image_sha256') == d['image_sha256'], 'Image device audit mismatched')
            need(audited_image.get('role') == d['role'] and
                 audited_image.get('boot_recovery_rootfs_checked') is True and
                 type(audited_image.get('boot_recovery')) is bool,
                 'FIT and rootfs boot recovery audit missing')
            manifests[key] = sorted(archive.extractfile(manifest).read().decode().splitlines())
            configs[key] = normalized_config(archive.extractfile('config.buildinfo').read())
            feed_receipts[key] = normalized_feeds(archive.extractfile('feeds.buildinfo').read(), d)
        reports[key] = (path, bundle, d)
    need(set(reports) == {(device, role) for device in layouts for role in ROLES},
         'Both Full roles must succeed for every release device')
    first = next(iter(reports.values()))[2]
    for _,_,d in reports.values():
        need(all(d[k] == first[k] for k in ['build_version','upstream_base','source_tree',
                                          'fixed_feeds','feed_lock_sha256','source_archive_sha256', *LOCAL_SOURCE_KEYS]), 'Variants have different release inputs')
    for device in layouts:
        gateway, ap = ((device, role) for role in ROLES)
        need(manifests[gateway] == manifests[ap], f'{device} roles have different packages or package versions')
        need(configs[gateway] == configs[ap], f'{device} roles have different driver or package configuration')
    for role in ROLES:
        common_packages = [comparable_manifest(device, manifests[device, role]) for device in layouts]
        need(all(packages == common_packages[0] for packages in common_packages[1:]),
             f'{role} images differ across devices outside the hardware package exceptions')
    need(all(feeds == next(iter(feed_receipts.values())) for feeds in feed_receipts.values()),
         'Variants have different actual feed revisions')
    output.mkdir(parents=True)
    for path,bundle,d in reports.values():
        for item in [path,bundle,path.parent/(path.stem+'-sysupgrade.itb'),path.parent/d['source_archive']]:
            shutil.copyfile(item, output/item.name)
    save(output/'release-pair.json', {'schema_version':ARTIFACT_SCHEMA,'release_commit':revision,
         'layouts': layouts, 'variants': [reports[key][2] for key in sorted(reports)]})
    (output/'sha256sums').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(output.iterdir()) if p.is_file()))


def generate_notes(pair_path, output_path, template_path=None):
    pair = json.loads(pair_path.read_text())
    need(pair.get('schema_version') == ARTIFACT_SCHEMA, 'Unsupported release pair')
    revision = pair.get('release_commit')
    need(isinstance(revision,str) and re.fullmatch('[0-9a-f]{40}',revision), 'Invalid release source ID')
    variants = pair.get('variants', [])
    layouts = pair.get('layouts', {})
    need(layouts and all(valid_layout(*item) for item in layouts.items()), 'Invalid release layouts')
    expected = {(device, role) for device in layouts for role in ROLES}
    need(len(variants) == len(expected) and {(d.get('device'), d.get('role')) for d in variants} == expected,
         'Notes require both Full roles for every release device')
    for d in variants:
        need(d.get('schema_version') == ARTIFACT_SCHEMA and d.get('flavor') == 'full' and
             d.get('release_commit') == revision and d.get('source_commit') == revision and
             d.get('layout') == layouts[d['device']], 'Notes contain mixed source snapshots')
    text = (template_path or ROOT/'docs/xr1710g-release-notes.md').read_text().rstrip()
    text += '\n\n| 机型 | 分区布局 | 固件角色 |\n| --- | --- | --- |\n'
    for device, layout in sorted(layouts.items()):
        text += f'| {device.upper()} | `{layout}` | gateway／ap |\n'
    text += f'\n源码提交为 `{revision}`。附件包含各版本的镜像、配套 APK、源码和 `sha256sums`。\n'
    with output_path.open('x') as output: output.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('plan')
    sub.add_parser('tag')
    p=sub.add_parser('prepare');p.add_argument('--role',choices=ROLES,required=True);p.add_argument('--device',choices=DEVICES,default='xr1710g');p.add_argument('--layout',choices=['legacy','ubi']);p.add_argument('--directory',type=Path,required=True)
    p=sub.add_parser('build');p.add_argument('--directory',type=Path,required=True);p.add_argument('--jobs',type=int,default=2);p.add_argument('--prepare-only',action='store_true');p.add_argument('--reuse-feeds',action='store_true')
    p=sub.add_parser('assemble');p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--xr1710g-layout',choices=DEVICES['xr1710g'],default='legacy')
    p=sub.add_parser('notes');p.add_argument('--pair',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action == 'plan':
        p=plan(ROOT);p.pop('feed_text');print(json.dumps(p,indent=2))
    elif args.action == 'tag': print(release_tag(plan(ROOT)['version']))
    elif args.action == 'prepare': print(json.dumps(prepare(ROOT,args.directory,args.role,args.layout,device=args.device),indent=2))
    elif args.action == 'assemble':
        p=plan(ROOT)
        assemble(args.inputs,args.output,p['release_commit'],p['upstream_base'],git(ROOT,'rev-parse',p['release_commit']+'^{tree}'),
                 layouts={'xr1710g': args.xr1710g_layout, 'w1701k': 'ubi'})
    elif args.action == 'notes': generate_notes(args.pair,args.output)
    else: build(args.directory.resolve(),args.jobs,args.prepare_only,args.reuse_feeds)


if __name__ == '__main__':
    os.umask(0o022)
    try: main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit('Release build refused: '+str(exc)) from None
