#!/usr/bin/env python3
"""Check locked feeds, Full package selection and public release metadata."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    'wrtix-agent', 'xr1710g-full', 'xr1710g-cli', 'wrtmesh', 'wpad-mesh-mbedtls', 'hostapd-utils', 'jq',
    'curl', 'coreutils-timeout', 'flock', 'umdns', 'firewall4', 'kmod-nft-bridge',
    'kmod-nft-offload', 'luci-app-mlo', 'luci-app-airoha-fancontrol',
    'luci-app-airoha-npu', 'luci-theme-glass', 'dropbear', 'uhttpd', 'cpufreq',
    'luci-i18n-base-zh-cn', 'zsh', 'vim-full', 'tmux', 'less',
    'git-http', 'rsync', 'luci-app-airoha-factory',
    'luci-app-package-manager', 'luci-app-wifihistory',
    'luci-i18n-airoha-factory-zh-cn', 'luci-i18n-package-manager-zh-cn',
    'luci-i18n-wifihistory-zh-cn', 'dnsmasq', 'odhcpd-ipv6only',
    'ppp', 'ppp-mod-pppoe', 'luci-proto-ppp', 'kmod-crypto-hw-eip93',
    'kmod-crypto-cbc', 'kmod-crypto-ctr', 'kmod-crypto-ecb',
}
FORBIDDEN = {
    'xr-roaming', 'ap-profile', 'dicttech-setup', 'cli-profile', 'usteer', 'dawn', 'luci-app-usteer', 'luci-app-dawn', 'miniupnpd',
    'miniupnpd-nftables', 'luci-app-upnp', 'lucky', 'luci-app-lucky',
    'watchcat', 'luci-app-watchcat', 'luci-app-autoreboot', 'ttyd',
    'luci-app-ttyd', 'ddns-scripts', 'luci-app-ddns',
    'dnsmasq-full', 'odhcpd', 'easytier',
    'luci-app-easytier', 'luci-app-ssr-plus', 'luci-app-openclash',
    'luci-app-passwall', 'luci-app-passwall2', 'default-settings',
    'default-settings-chn', 'autosamba', 'automount',
    'squeezelite-custom', 'squeezelite-dynamic', 'squeezelite-full', '3proxy',
}


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()


def feeds():
    rows = []
    for line in (ROOT / 'configs/dicttech-feeds.conf').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if fields[1].startswith('--directory='):
            directory = fields[1].split('=', 1)[1]
            _, _, name, source = fields
        else:
            _, name, source = fields
            directory = name
        url, revision = source.rsplit('^', 1)
        if not re.fullmatch('[0-9a-f]{40}', revision):
            raise ValueError('Unpinned feed: ' + name)
        path = ROOT / 'feeds' / directory
        if git('rev-parse', 'HEAD', cwd=path) != revision:
            raise ValueError('Wrong feed revision: ' + name)
        rows.append({'name': name, 'url': url, 'revision': revision,
                     'directory': directory})
    return rows


LOCAL_FEEDS = ('wrtix', 'wrtmesh')


def local_feed_source(name, source_root=ROOT):
    spec = importlib.util.spec_from_file_location('pinned_feed_source', source_root / 'scripts/dicttech/wrtix-source.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    pin = json.loads((source_root / f'configs/{name}-source.json').read_text())
    if set(pin) != {'schema_version', 'revision', 'archive_sha256'} or pin['schema_version'] != 1 or not re.fullmatch('[0-9a-f]{40}', pin['revision']) or not re.fullmatch('[0-9a-f]{64}', pin['archive_sha256']):
        raise ValueError(f'Invalid {name} source pin')
    metadata = json.loads((source_root / f'bin/dicttech-{name}-source.json').read_text())
    if hashlib.sha256((source_root / f'bin/dicttech-{name}-source.tar').read_bytes()).hexdigest() != pin['archive_sha256']:
        raise ValueError(f'{name} source archive differs from its pin')
    directory = source_root / f'tmp/{name}-sources' / pin['revision']
    if metadata != {**pin, 'tree_sha256': module.inventory(directory)} or not directory.is_dir() or directory.is_symlink():
        raise ValueError(f'Materialized {name} feed differs from its verified source')
    feed = source_root / f'feeds/{name}'
    if not feed.is_symlink() or feed.resolve() != directory.resolve():
        raise ValueError(f'{name} feed does not point to the verified snapshot')
    return directory, metadata


def config(path, layout):
    values = dict(re.findall(r'^(CONFIG_[A-Za-z0-9_-]+)=(.*)$', path.read_text(), re.M))
    missing = sorted(p for p in REQUIRED if values.get('CONFIG_PACKAGE_' + p) != 'y')
    extra = sorted(p for p in FORBIDDEN if values.get('CONFIG_PACKAGE_' + p) == 'y')
    if missing or extra:
        raise ValueError('Full package policy mismatch: ' + json.dumps({'missing': missing, 'forbidden': extra}))
    expected = 'CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g' + ('-ubi' if layout == 'ubi' else '')
    if values.get(expected) != 'y':
        raise ValueError('Wrong XR1710G partition profile')
    return {'layout': layout, 'required_packages': sorted(REQUIRED),
            'config_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--feeds-only', action='store_true')
    parser.add_argument('--config', type=Path, default=ROOT / '.config')
    parser.add_argument('--layout', choices=['legacy', 'ubi'], default='legacy')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = {'schema_version': 1, 'feeds': feeds(), **{name: local_feed_source(name)[1] for name in LOCAL_FEEDS}}
    if not args.feeds_only:
        result.update(config(args.config, args.layout))
        result['source_revision'] = git('rev-parse', 'HEAD')
        result['source_date_epoch'] = int(git('show', '-s', '--format=%ct', 'HEAD'))
        result['source_dirty'] = bool(git('status', '--porcelain'))
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
