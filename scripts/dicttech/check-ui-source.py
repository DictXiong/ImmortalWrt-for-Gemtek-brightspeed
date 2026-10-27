#!/usr/bin/env python3
"""Refuse source/UI drift; optionally verify locked LuCI jsmin output."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('rootfs_policy', ROOT / 'scripts/dicttech/check-ap-rootfs.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def git_data(feed, *args):
    return subprocess.check_output(['git', '-C', str(feed), *args])


def check_source(root, jsmin=None):
    manifest = policy.factory_policy(root)
    feed = root / 'feeds/app_airoha'
    revision = manifest['feed_app_airoha_revision']
    policy.require(git_data(feed, 'rev-parse', 'HEAD').decode().strip() == revision,
                   'Wrong factory feed revision')
    sources = policy.factory_sources(root)
    rows = []
    for name, item in manifest['patches'].items():
        if 'patch' in item:
            path = root / 'feeds' / Path(item['patch']).parts[2] / item['target']
        else:
            path = root / item['target']
        policy.require(policy.regular(path), 'UI source missing or symlinked: ' + name)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        policy.require(actual == item['patched_sha256'], 'Unreviewed UI source: ' + name)
        if name in policy.FACTORY_TARGETS:
            original = git_data(feed, 'show', revision + ':' + item['target'])
            policy.require(policy.digest(original) == item['source_sha256'],
                           'Factory patch preimage differs from locked feed: ' + name)
        rows.append({'name': name, 'sha256': actual})
    result = {'passed': True, 'files': rows, 'factory_feed_revision': revision}
    if jsmin is not None:
        policy.require(policy.regular(jsmin) and jsmin.stat().st_mode & 0o111, 'official LuCI jsmin missing or not executable')
        relative = 'www/luci-static/resources/view/airoha_factory/factory.js'
        q = subprocess.run([str(jsmin.resolve())], input=sources[relative], capture_output=True, timeout=30, check=True)
        actual = policy.digest(q.stdout)
        policy.require(actual == manifest['patches']['factory-info-view']['minified_sha256'],
                       'Official LuCI jsmin output differs from reviewed factory view')
        result['factory_minified_sha256'] = actual
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--jsmin', type=Path, help='official built staging_dir/hostpkg/bin/jsmin; required by the image build after compilation')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check_source(args.source_root.resolve(), args.jsmin)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('Reviewed source UI changes: {0}/{0}'.format(len(result['files'])))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
