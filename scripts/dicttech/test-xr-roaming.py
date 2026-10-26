#!/usr/bin/env python3
"""Run inert roaming regressions against the actual package source files.

Needs Python 3, jq and ordinary POSIX utilities. Optional C7 rootfs/qemu checks
exercise the target ash and slim jq. No AP, SSH, HTTP or UCI service is used:
all commands, clocks, HTTP responses and radio state are local fixtures.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parents[2]
    parser.add_argument('--package-root', type=Path,
                        default=repo / 'package/dicttech/xr-roaming')
    parser.add_argument('--c7-rootfs', type=Path,
                        help='optional C7 rootfs with bin/busybox and usr/bin/jq')
    parser.add_argument('--qemu', default=shutil.which('qemu-aarch64'))
    parser.add_argument('--output', type=Path, help='write combined JSON result')
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or os.geteuid() != 0:
        parser.error('run in a Linux environment as root; runtime owner checks '
                     'and /proc lifecycle fixtures require it')
    if not shutil.which('jq'):
        parser.error('jq is required')
    source = args.package_root.resolve() / 'files'
    names = {'helper.sh': 'helper.sh', 'common.jq': 'common.jq',
             'cli-report.jq': 'cli-report.jq', 'xr_roaming.init': 'init.sh'}
    for name in names:
        if not (source / name).is_file():
            parser.error(f'missing package input: {source / name}')
    env = dict(os.environ)
    if args.c7_rootfs:
        args.c7_rootfs = args.c7_rootfs.resolve()
        if not args.qemu:
            parser.error('--c7-rootfs requires qemu-aarch64 or --qemu')
        for binary in ['bin/busybox', 'usr/bin/jq']:
            if not (args.c7_rootfs / binary).is_file():
                parser.error(f'missing target binary: {binary}')
        env['XR_C7_ROOTFS'] = str(args.c7_rootfs)
        env['XR_QEMU'] = args.qemu
    else:
        env.pop('XR_C7_ROOTFS', None)
        env.pop('XR_QEMU', None)
    pins = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in names}
    fixtures = Path(__file__).resolve().parent / 'tests/xr-roaming'
    with tempfile.TemporaryDirectory(prefix='xr-roaming-suite-') as temp:
        stage = Path(temp)
        shutil.copytree(fixtures, stage / 'tests')
        (stage / 'helper-v4').mkdir()
        for source_name, staged_name in names.items():
            for dest in [stage / 'helper-v4', stage / 'tests/tree']:
                shutil.copy2(source / source_name, dest / staged_name)
        for suite in ['targeted.py', 'cache.py', 'fleet.py']:
            result = subprocess.run([sys.executable, str(stage / 'tests' / suite)],
                                    env=env, timeout=180)
            if result.returncode:
                return result.returncode
        base = json.loads((stage / 'tests/tree/selftest-profiles-result.json').read_text())
        targeted = json.loads((stage / 'targeted-tests-result.json').read_text())
        cache = json.loads((stage / 'cache-tests-result.json').read_text())
        fleet = json.loads((stage / 'fleet-tests-result.json').read_text())
        count = base['total'] + len(targeted['results']) - 1 + len(cache['results']) + len(fleet['results'])
        result = {
            'passed': base['passed'] and targeted['passed'] and cache['passed'] and fleet['passed'],
            'scope': 'Local inert fixtures; no router or network-service calls.',
            'sourceSHA': pins,
            'legacy_checks': base['total'],
            'lifecycle_checks': len(targeted['results']) - 1,
            'cache_groups': len(cache['results']),
            'fleet_groups': len(fleet['results']),
            'total': count,
            'target_C7_ash_and_jq': bool(args.c7_rootfs),
            'skipped': [] if args.c7_rootfs else [
                'C7 ash/slim-jq direct cycle', 'C7 ash/slim-jq cached daemon'],
            'limits': ['Service APIs are inert; actual procd and wireless behavior '
                       'require an independent live acceptance run.'],
        }
    if pins != {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                for name in names}:
        raise RuntimeError('package runtime changed during the test run')
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
