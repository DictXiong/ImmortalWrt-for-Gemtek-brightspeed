#!/usr/bin/env python3
"""Run private inert first-boot and service-policy fixtures, without AP access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parents[2]
    parser.add_argument('--source-root', type=Path, default=repo,
                        help='source checkout containing package/dicttech/ap-profile')
    parser.add_argument('--output', type=Path, help='write aggregate JSON report')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('the inert /proc and shell fixtures require Linux')
    commands = ['sh', 'jq', 'tar', 'cp', 'mv', 'chmod', 'wc', 'tr', 'readlink', 'hexdump', 'timeout',
                'ln', 'rm', 'mkdir', 'sed', 'sha256sum', 'awk', 'cat', 'touch']
    missing = [name for name in commands if not shutil.which(name)]
    if missing:
        parser.error('missing local test tools: ' + ', '.join(missing))
    source = args.source_root.resolve()
    package = source / 'package/dicttech/ap-profile/files'
    inputs = [package / 'usr/lib/dicttech-ap/bootstrap.sh',
              package / 'usr/lib/dicttech-ap/services.sh',
              package / 'usr/share/dicttech-ap/defaults.json',
              package / 'etc/init.d/dicttech-ap-prepare',
              package / 'etc/init.d/dicttech-ap-finalize']
    inputs.extend(sorted((package / 'usr/lib/dicttech-ap/setup').iterdir()))
    inputs.append(source / 'package/network/services/dropbear/files/dropbear.init')
    for item in inputs:
        if not item.is_file():
            parser.error('missing package source: ' + str(item))
    pins = {str(item.relative_to(source)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in inputs}
    fixtures = Path(__file__).resolve().parent / 'tests/bootstrap'
    environment = dict(os.environ, DICTTECH_AP_TEST_SOURCE_ROOT=str(source),
                       DICTTECH_AP_TEST_SHELL=shutil.which('sh'))
    report = {'schema_version': 1, 'offline_only': True, 'router_access': False,
              'host_services_modified': False, 'sourceSHA': pins,
              'passed': True, 'suites': {}}
    for name in ['bootstrap', 'setup', 'services']:
        try:
            result = subprocess.run([sys.executable, str(fixtures / (name + '.py'))],
                                    env=environment, capture_output=True, text=True,
                                    timeout=120)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip())
            cases = json.loads(result.stdout)
            if not isinstance(cases, list) or not cases or not all(row['passed'] for row in cases):
                raise ValueError('fixture returned an incomplete result')
            report['suites'][name] = {'passed': True, 'cases': cases}
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            report['passed'] = False
            report['suites'][name] = {'passed': False, 'failure': str(error)}
    if pins != {str(item.relative_to(source)): hashlib.sha256(item.read_bytes()).hexdigest()
                for item in inputs}:
        report['passed'] = False
        report['failure'] = 'package source changed during the fixture run'
    report['total_cases'] = sum(len(value.get('cases', []))
                                for value in report['suites'].values())
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        if args.output.is_symlink():
            parser.error('refusing a symbolic-link report output')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end='')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
