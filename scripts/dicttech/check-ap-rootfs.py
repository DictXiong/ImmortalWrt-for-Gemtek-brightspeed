#!/usr/bin/env python3
"""Verify a virgin AP rootfs against the authoritative public package sources."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import sys

REPO = Path(__file__).resolve().parents[2]


class RootfsError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RootfsError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    return path.is_file() and not path.is_symlink()


def safe_ancestors(rootfs, relative):
    current = rootfs
    for part in Path(relative).parts[:-1]:
        current /= part
        require(not current.is_symlink(), 'rootfs path has a symlinked parent: ' + relative)


def compare(source, target, relative):
    require(regular(source), 'source is not a regular package file: ' + str(source))
    require(regular(target), 'installed file missing or symlinked: ' + relative)
    expected = source.read_bytes()
    require(target.read_bytes() == expected, 'installed bytes differ from authoritative source: ' + relative)
    return {'path': relative, 'sha256': digest(expected)}


def roaming_map(source_root):
    package = source_root / 'package/dicttech/xr-roaming'
    makefile = (package / 'Makefile').read_text()
    match = re.search(r'^define Package/xr-roaming/install\n(.*?)^endef$', makefile, re.M | re.S)
    require(match is not None, 'xr-roaming Makefile install block missing')
    mapping = {}
    for line in match[1].splitlines():
        words = line.split()
        if not words or words[0] not in {'$(INSTALL_BIN)', '$(INSTALL_DATA)', '$(INSTALL_CONF)'}:
            continue
        require(words[-1].startswith('$(1)/'), 'unsupported xr-roaming install destination')
        destination = words[-1][5:]
        for token in words[1:-1]:
            require(token.startswith('./files/'), 'unsupported xr-roaming install source')
            name = token[len('./files/'):]
            relative = destination + Path(name).name if destination.endswith('/') else destination
            require(relative not in mapping and '..' not in Path(relative).parts, 'duplicate or unsafe xr-roaming destination')
            mapping[relative] = package / 'files' / name
    require({p.name for p in mapping.values()} == {'helper.sh', 'common.jq', 'cli-report.jq', 'xr_roaming.init', 'xr_roaming.config'},
            'xr-roaming package mapping changed; review the rootfs checker')
    return mapping


GENERATOR_FILES = {
    'lib/wifi/mac80211.uc': 'package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc',
    'etc/board.d/03_wifi_defaults': 'package/network/config/airoha-an7581-mt7996-board/files/etc/board.d/03_wifi_defaults',
    'etc/uci-defaults/03_wireless': 'package/network/config/airoha-an7581-mt7996-board/files/etc/uci-defaults/03_wireless',
    'etc/uci-defaults/50-root-passwd': 'package/base-files/files/etc/uci-defaults/50-root-passwd',
    'etc/uci-defaults/50-dropbear': 'package/network/services/dropbear/files/dropbear.defaults',
}


def check_generators(source_root, rootfs):
    rows = []
    guard = '[ ! -f /usr/share/dicttech-ap/defaults.json ] || exit 0'
    for relative, source in GENERATOR_FILES.items():
        rows.append(compare(source_root / source, rootfs / relative, relative))
        content = (rootfs / relative).read_text()
        if relative == 'lib/wifi/mac80211.uc':
            require(re.search(r'set \$\{si\}\.disabled=\x27\$\{readfile\("/usr/share/dicttech-ap/defaults\.json"\)\s*\?\s*"1"\s*:\s*"0"\}\x27', content) is not None,
                    'wireless generator no longer disables AP-profile interfaces by default')
        else:
            require(guard in '\n'.join(content.splitlines()[:8]), 'upstream defaults are not suppressed early: ' + relative)
    return rows


def check_unprovisioned(rootfs):
    directory = rootfs / 'etc/dicttech-ap'
    require(directory.is_dir() and not directory.is_symlink(), 'private deployment directory is absent or symlinked')
    require(directory.stat().st_mode & 0o777 == 0o700, 'private deployment directory must have mode 0700')
    entries = [p for p in directory.rglob('*') if p.is_file() or p.is_symlink()]
    require(not entries, 'virgin rootfs contains device deployment state or secrets under etc/dicttech-ap')
    paths = []
    for pattern in ('etc/dropbear/dropbear_*_host_key', 'etc/ssh/ssh_host_*_key', 'etc/uhttpd.key'):
        paths.extend(rootfs.glob(pattern))
    require(not paths, 'virgin rootfs contains a device SSH/HTTPS private key')
    authorized = rootfs / 'etc/dropbear/authorized_keys'
    require(not authorized.is_symlink() and (not authorized.exists() or authorized.read_bytes() == b''),
            'virgin rootfs contains preset authorized SSH keys')
    shadow = rootfs / 'etc/shadow'
    require(regular(shadow), 'virgin rootfs shadow is missing or symlinked')
    require(shadow.stat().st_mode & 0o777 == 0o600, 'virgin rootfs shadow must have mode 0600')
    rows = [line.split(':') for line in shadow.read_text().splitlines() if line.startswith('root:')]
    require(len(rows) == 1 and len(rows[0]) == 9 and rows[0][1] in {'', '!', '*'}, 'virgin rootfs contains a preset root password')
    wireless = rootfs / 'etc/config/wireless'
    if wireless.exists() or wireless.is_symlink():
        require(regular(wireless), 'preset wireless config is not a regular file')
        current, sections = None, []
        for line in wireless.read_text().splitlines():
            words = shlex.split(line, comments=True)
            if not words:
                continue
            if words[0] == 'config':
                require(len(words) in (2, 3), 'invalid preset wireless section')
                current = {'type': words[1], 'options': {}}; sections.append(current)
            elif words[0] == 'option':
                require(current is not None and len(words) == 3, 'invalid preset wireless option')
                current['options'][words[1]] = words[2]
        for section in sections:
            if section['type'] in ('wifi-iface', 'wifi-device'):
                require(section['options'].get('disabled') == '1', 'virgin rootfs has enabled preset Wi-Fi')
                require(not any(section['options'].get(key) for key in ('key', 'sae_password', 'wpa_psk')), 'virgin rootfs contains preset wireless credentials')
    public_link = rootfs / 'www/xr-roaming'
    require(public_link.is_symlink() and os.readlink(public_link) == '/tmp/xr-roaming-public', 'roaming publication link must point to RAM')
    resolver = rootfs / 'etc/resolv.conf'
    require(resolver.is_symlink() and os.readlink(resolver) == '/tmp/resolv.conf.d/resolv.conf.auto', 'virgin AP resolver must use netifd DNS without dnsmasq')
    return {'deployment_state': 'absent', 'private_host_identity': 'absent', 'preset_root_password': False,
            'preset_authorized_keys': False, 'wireless_generation': 'AP marker selects disabled interfaces; upstream defaults suppressed',
            'roaming_publication': 'RAM symlink', 'resolver': 'netifd automatic DNS symlink'}


def manifest_policy(source_root, path):
    spec = importlib.util.spec_from_file_location('dicttech_check_build_policy', source_root / 'scripts/dicttech/check-build.py')
    require(spec is not None and spec.loader is not None, 'build policy module missing')
    policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
    packages = set()
    for line in path.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.-]* - .+', line) is not None, 'invalid installed-package manifest row')
        name = line.split(' - ', 1)[0]
        require(name not in packages, 'duplicate installed-package manifest row')
        packages.add(name)
    missing = sorted(policy.REQUIRED - packages); forbidden = sorted(policy.FORBIDDEN & packages)
    require(not missing and not forbidden, 'actual package policy mismatch: ' + json.dumps({'missing': missing, 'forbidden': forbidden}))
    return {'manifest_sha256': digest(path.read_bytes()), 'package_count': len(packages), 'required': sorted(policy.REQUIRED), 'forbidden_present': []}


RPC_CODE_ROOTS = ('usr/share/rpcd/ucode', 'usr/lib/rpcd', 'usr/libexec/rpcd',
                  'usr/share/ucode/luci', 'usr/lib/ucode/luci')
RPC_REQUIRED_FILES = ('usr/share/rpcd/ucode/luci', 'usr/lib/rpcd/ucode.so',
                      'usr/lib/rpcd/luci.so', 'usr/libexec/rpcd/luci.airoha_npu',
                      'usr/libexec/rpcd/luci.fan', 'usr/lib/ucode/luci/core.so',
                      'usr/share/ucode/luci/sys.uc', 'usr/share/ucode/luci/version.uc',
                      'usr/share/ucode/luci/zoneinfo.uc')


def check_luci_rpc(rootfs):
    # rpcd deliberately refuses world-writable ucode plugins. The source cache's
    # host permissions are not authoritative: inspect the installed code and
    # its parents after package assembly/extraction, including imported modules.
    checked = set()
    for relative in RPC_CODE_ROOTS:
        directory = rootfs / relative
        safe_ancestors(rootfs, relative)
        require(directory.is_dir() and not directory.is_symlink(), 'RPC code directory is missing or symlinked: ' + relative)
        for path in [directory, *directory.parents]:
            if path == rootfs:
                break
            checked.add(path)
        for path in directory.rglob('*'):
            require(not path.is_symlink() and (path.is_dir() or path.is_file()),
                    'RPC code path is not a regular file/directory: ' + str(path.relative_to(rootfs)))
            checked.add(path)
    for relative in RPC_REQUIRED_FILES:
        path = rootfs / relative
        require(regular(path) and path.stat().st_size > 0, 'required LuCI RPC file is missing or empty: ' + relative)
    for path in sorted(checked):
        require(not path.stat().st_mode & 0o022, 'RPC code or parent is group/other writable: ' + str(path.relative_to(rootfs)))
    return {'required_files': list(RPC_REQUIRED_FILES), 'code_and_parent_paths_checked': len(checked),
            'group_or_other_writable': False}


def check_rootfs(rootfs, source_root=REPO, manifest=None):
    rootfs, source_root = rootfs.resolve(), source_root.resolve()
    require(rootfs.is_dir(), 'rootfs directory missing')
    for relative in ('etc/dicttech-ap/state', 'etc/dropbear/key', 'etc/ssh/key', 'www/xr-roaming', 'lib/wifi/mac80211.uc'):
        safe_ancestors(rootfs, relative)
    source = source_root / 'package/dicttech/ap-profile/files'
    require(source.is_dir(), 'authoritative ap-profile source directory missing')
    mapping = {str(p.relative_to(source)): p for p in source.rglob('*') if p.is_file() or p.is_symlink()}
    require(bool(mapping), 'ap-profile source is empty')
    mapping.update(roaming_map(source_root))
    for relative in {**mapping, **GENERATOR_FILES}:
        safe_ancestors(rootfs, relative)
    rows = [compare(path, rootfs / relative, relative) for relative, path in sorted(mapping.items())]
    rows += check_generators(source_root, rootfs)
    result = {'schema_version': 1, 'passed': True, 'files_checked': rows,
              'clean_image': check_unprovisioned(rootfs), 'luci_rpc': check_luci_rpc(rootfs),
              'scope': 'virgin assembled or extracted rootfs; not a running/provisioned AP'}
    if manifest:
        result['packages'] = manifest_policy(source_root, manifest)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', required=True, type=Path)
    parser.add_argument('--source-root', default=REPO, type=Path, help='authoritative reviewed source tree, not an unverified build mirror')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        require(not args.output.resolve().is_relative_to(args.rootfs.resolve()), 'report output must be outside the verified rootfs')
        require(args.manifest is None or args.output.resolve() != args.manifest.resolve(), 'report output must not overwrite the package manifest')
    result = check_rootfs(args.rootfs, args.source_root, args.manifest)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    try:
        main()
    except (RootfsError, OSError, UnicodeError, ValueError, ImportError) as exc:
        print('AP rootfs check failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
