#!/usr/bin/env python3
"""Refuse drift in the reviewed source UI fixes before compiling packages."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[2])
root = parser.parse_args().source_root.resolve()
manifest = json.loads((root / 'configs/dicttech-ui-manifest.json').read_text())
for name, item in manifest['patches'].items():
    if 'patch' in item:
        feed = Path(item['patch']).parts[2]
        path = root / 'feeds' / feed / item['target']
    else:
        path = root / item['target']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item['patched_sha256']:
        raise SystemExit('Unreviewed UI source: ' + name)
count = len(manifest['patches'])
print(f'Reviewed source UI changes: {count}/{count}')
