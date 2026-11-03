#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# This file was generated entirely by AI.
# It has not been reviewed line by line by a human.
"""Pin and materialize the local wrtix feed without mutable source inputs."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.DEVNULL)


def snapshot(source):
    if git(source, 'status', '--porcelain', '--untracked-files=normal').strip():
        raise ValueError('wrtix source must be a clean, committed checkout')
    revision = git(source, 'rev-parse', 'HEAD').decode().strip()
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('wrtix requires a full Git commit')
    data = git(source, 'archive', '--format=tar', revision)
    pin = {'schema_version': 1, 'revision': revision,
           'archive_sha256': hashlib.sha256(data).hexdigest()}
    return pin, data


def inventory(directory):
    items = []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('wrtix source archive cannot contain symlinks')
        if path.is_file():
            items.append([str(path.relative_to(directory)), hashlib.sha256(path.read_bytes()).hexdigest(),
                          bool(path.stat().st_mode & 0o111)])
    return hashlib.sha256(json.dumps(items, separators=(',', ':')).encode()).hexdigest()


def materialize(pin, data, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wrtix-feed-', dir=destination.parent) as tmp:
        tree = Path(tmp) / 'source'
        tree.mkdir()
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isdir() or member.isfile()):
                    raise ValueError('unsupported wrtix source archive member')
            archive.extractall(tree, filter='data')
        for relative in ('feed/wrtix-agent/Makefile', 'agent/agent.sh', 'agent/guard.init', 'agent/contract.json'):
            if not (tree / relative).is_file():
                raise ValueError('wrtix source is missing a required package input')
        checksum = inventory(tree)
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_dir() or inventory(destination) != checksum:
                raise ValueError('existing wrtix source snapshot differs from the pin')
        else:
            tree.rename(destination)
        return {**pin, 'tree_sha256': checksum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['lock', 'prepare'])
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--feeds-file', type=Path)
    parser.add_argument('--metadata', type=Path)
    args = parser.parse_args()
    pin, data = snapshot(args.source.resolve())
    if args.command == 'lock':
        args.lock.write_text(json.dumps(pin, indent=2) + '\n')
        return
    if not args.output or not args.feeds_file or not args.metadata:
        parser.error('prepare requires --output, --feeds-file and --metadata')
    if pin != json.loads(args.lock.read_text()):
        raise ValueError('wrtix source differs from configs/wrtix-source.json; update the pin deliberately')
    destination = args.output.resolve() / pin['revision']
    if any(c.isspace() for c in str(destination)):
        raise ValueError('OpenWrt feed paths must not contain whitespace')
    metadata = materialize(pin, data, destination)
    lines = args.feeds_file.read_text().splitlines()
    if any(re.match(r'^src-\S+\s+wrtix\s', line) for line in lines):
        raise ValueError('duplicate wrtix feed declaration')
    args.feeds_file.write_text('\n'.join(lines) + '\nsrc-link wrtix ' + str(destination) + '\n')
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(json.dumps(metadata, indent=2) + '\n')
    args.metadata.with_suffix('.tar').write_bytes(data)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit('wrtix source: ' + str(error))
