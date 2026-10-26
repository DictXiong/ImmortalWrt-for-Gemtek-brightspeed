#!/usr/bin/env python3
"""Refuse drift in the reviewed source UI fixes before compiling packages."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
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
print('Reviewed source UI fixes: 4/4')
