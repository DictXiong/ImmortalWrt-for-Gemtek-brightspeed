#!/usr/bin/env python3
"""Check locked feeds, AP package selection and public release metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    'ap-profile', 'xr-roaming', 'wpad-mesh-mbedtls', 'hostapd-utils', 'jq',
    'curl', 'coreutils-timeout', 'flock', 'firewall4', 'kmod-nft-bridge',
    'kmod-nft-offload', 'luci-app-mlo', 'luci-app-airoha-fancontrol',
    'luci-app-airoha-npu', 'luci-theme-glass', 'dropbear', 'uhttpd', 'cpufreq',
    'luci-i18n-base-zh-cn', 'cli-profile', 'zsh', 'vim-full', 'tmux', 'less',
    'git-http', 'rsync', 'luci-app-airoha-factory',
    'luci-app-package-manager', 'luci-app-wifihistory',
    'luci-i18n-airoha-factory-zh-cn', 'luci-i18n-package-manager-zh-cn',
    'luci-i18n-wifihistory-zh-cn',
}
FORBIDDEN = {
    'usteer', 'dawn', 'luci-app-usteer', 'luci-app-dawn', 'miniupnpd',
    'miniupnpd-nftables', 'luci-app-upnp', 'lucky', 'luci-app-lucky',
    'watchcat', 'luci-app-watchcat', 'luci-app-autoreboot', 'ttyd',
    'luci-app-ttyd', 'ddns-scripts', 'luci-app-ddns', 'dnsmasq',
    'dnsmasq-full', 'odhcpd', 'odhcpd-ipv6only', 'easytier',
    'luci-app-easytier', 'luci-app-ssr-plus', 'luci-app-openclash',
    'luci-app-passwall', 'luci-app-passwall2', 'default-settings',
    'default-settings-chn', 'autosamba', 'automount',
    'squeezelite-custom', 'squeezelite-dynamic', 'squeezelite-full', '3proxy',
    'ppp', 'ppp-mod-pppoe',
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


def config(path, layout):
    values = dict(re.findall(r'^(CONFIG_[A-Za-z0-9_-]+)=(.*)$', path.read_text(), re.M))
    missing = sorted(p for p in REQUIRED if values.get('CONFIG_PACKAGE_' + p) != 'y')
    extra = sorted(p for p in FORBIDDEN if values.get('CONFIG_PACKAGE_' + p) == 'y')
    if missing or extra:
        raise ValueError('AP package policy mismatch: ' + json.dumps({'missing': missing, 'forbidden': extra}))
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
    result = {'schema_version': 1, 'feeds': feeds()}
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
