#!/usr/bin/env python3
"""Select and verify exactly one image manifest's signed APK release set.

No network, package installation, resigning, private-key access or AP operations.
"""
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


class PackageError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PackageError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(path):
    packages = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9+_.-]*) - ([A-Za-z0-9][A-Za-z0-9+_.~:-]*)', line)
        require(match is not None, 'invalid image package manifest row')
        name, version = match.groups()
        require(name not in packages, 'duplicate image manifest package: ' + name)
        packages[name] = version
    require(bool(packages), 'image package manifest is empty')
    require('kernel' in packages, 'image package manifest has no kernel package')
    require(re.fullmatch(r'.+~(?:[0-9a-f]{32}|[0-9a-f]{64})-r[0-9]+', packages['kernel']) is not None,
            'manifest kernel version lacks an explicit ABI hash')
    return packages


def select_packages(packages, roots):
    expected = {name + '-' + version + '.apk': name for name, version in packages.items()}
    candidates = {filename: [] for filename in expected}
    for root in roots:
        require(root.is_dir(), 'explicit package root is missing: ' + str(root))
        for path in root.rglob('*.apk'):
            if path.name not in expected:
                continue
            require(path.is_file() and not path.is_symlink(), 'selected APK is not a regular file: ' + str(path))
            absolute = path.resolve()
            if absolute not in candidates[path.name]:
                candidates[path.name].append(absolute)
    missing = sorted(filename for filename, values in candidates.items() if not values)
    require(not missing, 'manifest APKs missing: ' + ', '.join(missing))
    selected = []
    for filename, name in sorted(expected.items()):
        paths = sorted(candidates[filename])
        hashes = {sha(path) for path in paths}
        require(len(hashes) == 1, 'conflicting bytes for manifest APK: ' + filename)
        selected.append({'name': name, 'version': packages[name], 'filename': filename,
                         'sha256': next(iter(hashes)), 'source': str(paths[0]),
                         'identical_sources': [str(path) for path in paths]})
    return selected


def apk_run(apk, keys, command, package):
    args = [str(apk), '--no-network', '--no-cache', '--no-logfile', '--keys-dir', str(keys)]
    args += [command] + (['--format', 'json'] if command == 'adbdump' else []) + [str(package)]
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    require(result.returncode == 0, 'APK ' + command + ' failed: ' + package.name)
    return result.stdout


def verify_packages(packages, selected, apk, keys):
    require(apk.is_file() and os.access(apk, os.X_OK), 'host apk executable missing')
    require(keys.is_dir(), 'actual image trusted-key directory missing')
    key_paths = sorted(keys.iterdir())
    require(bool(key_paths), 'actual image trusted-key directory is empty')
    for key in key_paths:
        require('private' not in key.name.lower(), 'private-key filename found in image trusted-key directory')
        require(key.is_file() and not key.is_symlink(), 'image trusted key is not a regular public-key file')
    key_hashes = {key.name: sha(key) for key in key_paths}
    # Verify the complete selected set before trusting its ADB metadata.
    for row in selected:
        apk_run(apk, keys, 'verify', Path(row['source']))
    info = {}
    for row in selected:
        try:
            document = json.loads(apk_run(apk, keys, 'adbdump', Path(row['source'])))
        except (UnicodeError, json.JSONDecodeError):
            raise PackageError('invalid APK ADB JSON: ' + row['filename']) from None
        require(isinstance(document, dict) and isinstance(document.get('info'), dict), 'APK ADB package info missing: ' + row['filename'])
        metadata = document['info']
        require(metadata.get('name') == row['name'] and metadata.get('version') == row['version'],
                'APK metadata disagrees with its exact manifest name/version: ' + row['filename'])
        require(isinstance(metadata.get('arch'), str) and metadata['arch'], 'APK architecture missing: ' + row['filename'])
        depends = metadata.get('depends', [])
        require(isinstance(depends, list) and all(isinstance(value, str) for value in depends), 'unsupported APK dependency schema: ' + row['filename'])
        kernel = [value for value in depends if value == 'kernel' or re.match(r'^kernel[=<>~]', value)]
        if row['name'].startswith('kmod-') or kernel:
            require(kernel == ['kernel=' + packages['kernel']], 'APK kernel version/ABI dependency differs from manifest: ' + row['filename'])
        row.update(architecture=metadata['arch'], signature='verified', kernel_dependencies=kernel)
        info[row['name']] = metadata
    architecture = info['kernel']['arch']
    require(architecture not in ('all', 'noarch'), 'kernel package does not identify a target architecture')
    for row in selected:
        require(row['architecture'] in (architecture, 'all', 'noarch'), 'APK architecture differs from the manifest kernel: ' + row['filename'])
        # Detect a cache rebuild/resign while verification was running.
        require(all(sha(Path(path)) == row['sha256'] for path in row['identical_sources']),
                'selected APK changed during verification: ' + row['filename'])
    require(sorted(path.name for path in keys.iterdir()) == sorted(key_hashes) and
            all(sha(keys / name) == expected for name, expected in key_hashes.items()),
            'image trusted-key set changed during verification')
    return [{'filename': name, 'sha256': expected} for name, expected in sorted(key_hashes.items())]


def copy_verified(selected, destination):
    require(not destination.exists() and not destination.is_symlink(), 'release package output directory must be new')
    destination.mkdir(mode=0o755)
    try:
        for row in selected:
            target = destination / row['filename']
            with target.open('xb') as output, Path(row['source']).open('rb') as source:
                shutil.copyfileobj(source, output)
            target.chmod(0o644)
            require(sha(target) == row['sha256'], 'selected APK changed while copying: ' + row['filename'])
    except BaseException:
        shutil.rmtree(destination)
        raise


def check_packages(manifest, roots, rootfs, apk, output_directory=None):
    if output_directory:
        require(not output_directory.exists() and not output_directory.is_symlink(), 'release package output directory must be new')
    manifest_hash = sha(manifest)
    packages = read_manifest(manifest)
    require(sha(manifest) == manifest_hash, 'image manifest changed while selecting packages')
    selected = select_packages(packages, roots)
    keys = rootfs / 'etc/apk/keys'
    public_keys = verify_packages(packages, selected, apk, keys)
    require(sha(manifest) == manifest_hash, 'image manifest changed during verification')
    if output_directory:
        copy_verified(selected, output_directory)
        if sha(manifest) != manifest_hash:
            shutil.rmtree(output_directory)
            raise PackageError('image manifest changed while copying packages')
    return {'schema_version': 1, 'passed': True, 'manifest_sha256': manifest_hash,
            'kernel_version_and_abi': packages['kernel'], 'package_count': len(selected),
            'public_trusted_keys': public_keys, 'packages': selected,
            'release_package_directory': str(output_directory) if output_directory else None,
            'network': False, 'resigning': False,
            'scope': 'exact manifest name/version APK bytes, signatures trusted by supplied image keys, and exact kernel ABI dependencies'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--package-root', required=True, type=Path, action='append', help='explicit APK search root; repeat for target/feed directories')
    parser.add_argument('--rootfs', required=True, type=Path, help='actual assembled/extracted image rootfs containing etc/apk/keys')
    parser.add_argument('--apk', required=True, type=Path, help='host apk-tools 3 executable or a compatible wrapper')
    parser.add_argument('--output-dir', type=Path, help='new directory receiving only the verified manifest APK set')
    parser.add_argument('--output', type=Path, help='public verification receipt JSON')
    args = parser.parse_args()
    if args.output:
        require(args.output.suffix == '.json', 'package verification report must use a .json filename')
        require(args.output.resolve() != args.manifest.resolve(), 'report must not overwrite the image package manifest')
    result = check_packages(args.manifest, [path.resolve() for path in args.package_root],
                            args.rootfs.resolve(), args.apk.resolve(), args.output_dir)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    try:
        main()
    except (PackageError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('AP package check failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
