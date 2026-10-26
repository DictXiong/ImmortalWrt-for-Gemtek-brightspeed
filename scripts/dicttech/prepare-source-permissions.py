#!/usr/bin/env python3
"""Restore public source permissions from the main and pinned-feed Git indexes."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'configs/dicttech-feeds.conf'
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class PermissionsError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PermissionsError(message)


def safe_relative(value):
    # Git's NUL-separated index permits whitespace and non-UTF8 names. Neither
    # alters the path boundary, but absolute paths and dot components do.
    require(value and not value.startswith('/') and '\x00' not in value and
            all(part not in {'', '.', '..'} for part in value.split('/')),
            'unsafe path in source metadata')
    return value


def open_root(path):
    """Open every component without following even an intermediate symlink."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    fd = os.open('/', DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(component, DIRECTORY, dir_fd=fd)
            os.close(fd); fd = next_fd
        return absolute, fd
    except BaseException:
        os.close(fd)
        raise


def open_parent(root_fd, relative):
    fd = os.dup(root_fd)
    try:
        for component in relative.split('/')[:-1]:
            next_fd = os.open(component, DIRECTORY, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_entry(root_fd, relative, directory=False):
    if not relative:
        return os.dup(root_fd)
    parent = open_parent(root_fd, relative)
    try:
        flags = DIRECTORY if directory else os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        return os.open(relative.split('/')[-1], flags, dir_fd=parent)
    finally:
        os.close(parent)


def git(root, *arguments):
    try:
        return subprocess.check_output(
            ['git', '-c', 'core.fsmonitor=false', '-C', str(root), *arguments],
            stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        raise PermissionsError('cannot read a source Git repository') from exc


def index(root):
    top = os.fsdecode(git(root, 'rev-parse', '--show-toplevel')).rstrip('\n')
    require(Path(top) == root, 'source directory is not an independent Git checkout')
    files, symlinks = {}, set()
    for row in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not row:
            continue
        header, separator, raw_path = row.partition(b'\t')
        fields = header.split()
        require(separator and len(fields) == 3 and fields[2] == b'0',
                'source Git index has an invalid or unmerged entry')
        path = safe_relative(os.fsdecode(raw_path))
        require(path not in files and path not in symlinks, 'duplicate source Git index path')
        if fields[0] in {b'100644', b'100755'}:
            files[path] = 0o644 if fields[0] == b'100644' else 0o755
        elif fields[0] == b'120000':
            symlinks.add(path)
        else:
            raise PermissionsError('unsupported source Git index entry (including submodules)')
    require(files or symlinks, 'source Git index is empty')
    return files, symlinks


def locked_feeds(root_fd):
    """Read only the frozen feed configuration; no update/fetch operations."""
    fd = open_entry(root_fd, LOCK)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'feed lock is not a regular file')
        with os.fdopen(os.dup(fd), 'r', encoding='utf-8') as stream:
            lines = stream.read().splitlines()
    finally:
        os.close(fd)
    result, names, directories = [], set(), set()
    for line in lines:
        fields = shlex.split(line, comments=True)
        if not fields:
            continue
        require(fields[0] in {'src-git', 'src-git-full'}, 'unsupported locked feed type')
        directory = None
        if len(fields) == 4 and fields[1].startswith('--directory='):
            directory = fields.pop(1).split('=', 1)[1]
        require(len(fields) == 3, 'invalid locked feed declaration')
        _, name, source = fields
        directory = directory or name
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) and
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', directory),
                'unsafe locked feed name/directory')
        require(name not in names and directory not in directories, 'duplicate locked feed name/directory')
        url, separator, revision = source.rpartition('^')
        require(url and separator and re.fullmatch(r'[0-9a-f]{40}', revision), 'feed is not commit-pinned')
        names.add(name); directories.add(directory)
        result.append((name, directory, revision))
    require(result, 'feed lock has no pinned feeds')
    return result


@dataclass
class Entry:
    root_fd: int
    path: str
    directory: bool
    before: int
    expected: int
    inode: tuple


def plan_repository(root, fd, label, extra_directories=()):
    files, symlinks = index(root)
    directories = {''} | set(extra_directories)
    for relative in set(files) | symlinks:
        parts = relative.split('/')
        directories.update('/'.join(parts[:length]) for length in range(1, len(parts)))
    entries = []
    for relative in sorted(directories, key=lambda name: (name.count('/'), name)):
        current_fd = open_entry(fd, relative, directory=True)
        try:
            info = os.fstat(current_fd)
            require(stat.S_ISDIR(info.st_mode), 'source parent is not a directory')
            mode = stat.S_IMODE(info.st_mode)
            # Preserve an intentionally private checkout root (e.g. 0700).
            expected = mode & ~0o022 if not relative else 0o755
            entries.append(Entry(fd, relative, True, mode, expected, (info.st_dev, info.st_ino)))
        finally:
            os.close(current_fd)
    for relative, expected in sorted(files.items()):
        current_fd = open_entry(fd, relative)
        try:
            info = os.fstat(current_fd)
            require(stat.S_ISREG(info.st_mode), 'tracked source entry is not a regular file')
            require(info.st_nlink == 1, 'tracked source file has hard links; refusing an external inode change')
            entries.append(Entry(fd, relative, False, stat.S_IMODE(info.st_mode), expected,
                                 (info.st_dev, info.st_ino)))
        finally:
            os.close(current_fd)
    for relative in sorted(symlinks):
        parent = open_parent(fd, relative)
        try:
            info = os.stat(relative.split('/')[-1], dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISLNK(info.st_mode), 'tracked source symlink has changed file type')
        finally:
            os.close(parent)
    return entries, {'repository': label, 'regular_files': len(files), 'symlinks_skipped': len(symlinks),
                     'directories': len(directories), 'changes_needed': sum(e.before != e.expected for e in entries)}


def prepare(source_root=ROOT, check=False):
    """Validate the entire selection before changing any source permissions."""
    with ExitStack() as stack:
        root, root_fd = open_root(source_root); stack.callback(os.close, root_fd)
        main_files, _ = index(root)
        require(LOCK in main_files, 'frozen feed configuration is not a tracked regular file')
        feeds = locked_feeds(root_fd)
        repositories = [(root, root_fd, 'main', ('feeds',))]
        for name, directory, revision in feeds:
            feed, feed_fd = open_root(root / 'feeds' / directory); stack.callback(os.close, feed_fd)
            require(git(feed, 'rev-parse', 'HEAD').strip().decode('ascii') == revision,
                    'wrong locked feed commit: ' + name)
            repositories.append((feed, feed_fd, name, ()))
        entries, reports = [], []
        for repo, fd, label, extra in repositories:
            selected, report = plan_repository(repo, fd, label, extra)
            entries.extend(selected); reports.append(report)
        changed_files = changed_directories = 0
        if not check:
            for entry in entries:
                if entry.before == entry.expected:
                    continue
                fd = open_entry(entry.root_fd, entry.path, directory=entry.directory)
                try:
                    info = os.fstat(fd)
                    require((info.st_dev, info.st_ino) == entry.inode and
                            stat.S_IMODE(info.st_mode) == entry.before and
                            (stat.S_ISDIR(info.st_mode) if entry.directory else stat.S_ISREG(info.st_mode)),
                            'source entry changed during permission preparation')
                    require(entry.directory or info.st_nlink == 1, 'source file gained a hard link during preparation')
                    os.fchmod(fd, entry.expected)
                    require(stat.S_IMODE(os.fstat(fd).st_mode) == entry.expected, 'source chmod did not take effect')
                finally:
                    os.close(fd)
                if entry.directory:
                    changed_directories += 1
                else:
                    changed_files += 1
        needed = sum(row['changes_needed'] for row in reports)
        return {'schema_version': 1, 'check_only': check, 'passed': not check or needed == 0,
                'repositories': reports, 'changes_needed': needed,
                'changed_files': changed_files, 'changed_directories': changed_directories}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--check', action='store_true', help='report permission drift without changing it; exit 1 on drift')
    args = parser.parse_args()
    report = prepare(args.source_root, args.check)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (PermissionsError, OSError, UnicodeError, ValueError) as exc:
        # Paths can name private checkout locations. Report the operation/type,
        # not filesystem contents or a potentially sensitive absolute pathname.
        message = str(exc) if isinstance(exc, PermissionsError) else type(exc).__name__
        print('source permission preparation failed: ' + message, file=sys.stderr)
        raise SystemExit(1)
