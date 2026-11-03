#!/usr/bin/env python3
"""Verify a virgin firmware rootfs against the authoritative public package sources."""
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


def compare_bytes(expected, target, relative):
    require(regular(target), 'installed file missing or symlinked: ' + relative)
    require(target.read_bytes() == expected, 'installed bytes differ from authoritative source: ' + relative)
    return {'path': relative, 'sha256': digest(expected)}


def compare(source, target, relative):
    require(regular(source), 'source is not a regular package file: ' + str(source))
    return compare_bytes(source.read_bytes(), target, relative)


HISTORY_PATCH = 'patches/feeds/luci/applications/luci-app-wifihistory/120-dicttech-bounded-mld-history.patch'
HISTORY_FILES = {
    'usr/sbin/wifihistory': 'root/usr/sbin/wifihistory',
    'etc/init.d/wifihistory': 'root/etc/init.d/wifihistory',
    'usr/share/rpcd/ucode/wifihistory.uc': 'root/usr/share/rpcd/ucode/wifihistory.uc',
    'usr/share/rpcd/acl.d/luci-app-wifihistory.json': 'root/usr/share/rpcd/acl.d/luci-app-wifihistory.json',
}
FACTORY_PATCH = 'patches/feeds/app_airoha/luci-app-airoha-factory/120-dicttech-readonly-split-dsd.patch'
FACTORY_FILES = {
    'usr/libexec/rpcd/luci.airoha_factory': ('factory-status', 'luci-app-airoha-factory/root/usr/libexec/rpcd/luci.airoha_factory'),
    'www/luci-static/resources/view/airoha_factory/factory.js': ('factory-info-view', 'luci-app-airoha-factory/htdocs/luci-static/resources/view/airoha_factory/factory.js'),
}
FACTORY_TARGETS = {name: target for name, target in FACTORY_FILES.values()}
FACTORY_TARGETS['factory-translation'] = 'luci-app-airoha-factory/po/zh_Hans/luci-app-airoha-factory.po'


def patch_postimage(patch, target):
    # These reviewed feed patches intentionally retain complete file context.
    # Auditing against their postimages also works before feeds are fetched.
    require(regular(patch), 'reviewed full-context feed patch missing or symlinked: ' + str(patch))
    lines = patch.read_text().splitlines(keepends=True)
    marker = '+++ b/' + target + '\n'
    require(lines.count(marker) == 1, 'reviewed patch target is missing or ambiguous: ' + target)
    start = lines.index(marker) + 1
    require(start < len(lines), 'reviewed feed patch has no postimage hunk: ' + target)
    match = re.fullmatch(r'@@ -1,\d+ \+1,(\d+) @@\n', lines[start])
    require(match is not None, 'reviewed feed patch lacks full file context: ' + target)
    result = []
    for line in lines[start + 1:]:
        if line.startswith('diff --git '):
            break
        require(not line.startswith('@@'), 'reviewed feed patch has incomplete postimage context: ' + target)
        if line.startswith((' ', '+')):
            result.append(line[1:])
    require(len(result) == int(match.group(1)), 'reviewed feed patch postimage length mismatch: ' + target)
    return ''.join(result).encode()


def history_sources(source_root):
    expected = {}
    patch = source_root / HISTORY_PATCH
    package = Path('applications/luci-app-wifihistory')
    feed = source_root / 'feeds/luci'
    for relative, suffix in HISTORY_FILES.items():
        target = str(package / suffix)
        expected[relative] = patch_postimage(patch, target)
        if feed.exists() or feed.is_symlink():
            current = feed / target
            require(regular(current) and current.read_bytes() == expected[relative],
                    'prepared feed differs from reviewed history patch: ' + target)
    return expected


def factory_policy(source_root):
    manifest = json.loads((source_root / 'configs/dicttech-ui-manifest.json').read_text())
    revision = manifest.get('feed_app_airoha_revision', '')
    require(re.fullmatch(r'[0-9a-f]{40}', revision) is not None, 'factory feed revision missing or malformed')
    config = (source_root / 'configs/dicttech-feeds.conf').read_text()
    pins = re.findall(r'^src-git app_airoha \S+\^([0-9a-f]{40})$', config, re.M)
    require(pins == [revision], 'factory UI manifest differs from locked app_airoha feed')
    rows = manifest.get('patches', {})
    for name, target in FACTORY_TARGETS.items():
        row = rows.get(name, {})
        require(row.get('patch') == FACTORY_PATCH and row.get('target') == target,
                'factory UI manifest target differs from reviewed patch: ' + name)
        for key in ('source_sha256', 'patched_sha256'):
            require(re.fullmatch(r'[0-9a-f]{64}', row.get(key, '')) is not None, 'factory UI manifest hash missing: ' + name)
    require(re.fullmatch(r'[0-9a-f]{64}', rows['factory-info-view'].get('minified_sha256', '')) is not None,
            'factory official minified hash missing')
    return manifest


def factory_sources(source_root):
    manifest = factory_policy(source_root)
    expected = {}
    postimages = {}
    for name, target in FACTORY_TARGETS.items():
        postimages[name] = patch_postimage(source_root / FACTORY_PATCH, target)
        require(digest(postimages[name]) == manifest['patches'][name]['patched_sha256'],
                'factory reviewed postimage differs from UI manifest: ' + name)
        feed = source_root / 'feeds/app_airoha'
        if feed.exists() or feed.is_symlink():
            current = feed / target
            require(regular(current) and current.read_bytes() == postimages[name],
                    'prepared feed differs from reviewed factory patch: ' + target)
    for relative, (name, _) in FACTORY_FILES.items():
        expected[relative] = postimages[name]
    return expected


def check_factory(rootfs, source_root, expected):
    manifest = factory_policy(source_root)
    rows = []
    for relative, data in expected.items():
        target = rootfs / relative
        safe_ancestors(rootfs, relative)
        if relative.startswith('usr/libexec/'):
            rows.append(compare_bytes(data, target, relative))
            mode = 0o755
        else:
            require(regular(target), 'factory view missing or symlinked: ' + relative)
            actual = digest(target.read_bytes())
            official = manifest['patches']['factory-info-view']['minified_sha256']
            # Both are complete byte identities. The normal full build also
            # recomputes the compact form with the locked LuCI host jsmin.
            require(actual in {digest(data), official}, 'factory view differs from reviewed source or official minified bytes')
            rows.append({'path': relative, 'sha256': actual,
                         'format': 'official-jsmin' if actual == official else 'reviewed-source'})
            mode = 0o644
        require(target.stat().st_mode & 0o7777 == mode, 'factory asset has unexpected mode: ' + relative)
        for parent in target.parents:
            if parent == rootfs:
                break
            require(not parent.stat().st_mode & 0o022, 'factory asset parent is group/other writable: ' + relative)
    return rows


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
    for relative, source in GENERATOR_FILES.items():
        rows.append(compare(source_root / source, rootfs / relative, relative))
        content = (rootfs / relative).read_text()
        require('/usr/share/dicttech-ap/' not in content and '/usr/share/dicttech-setup/' not in content,
                'personal factory suppression remains in generator: ' + relative)
    for relative in ('lib/wifi/mac80211.uc', 'etc/board.d/03_wifi_defaults', 'etc/uci-defaults/03_wireless'):
        require('/etc/xr1710g-full' in (rootfs / relative).read_text(), 'full-edition wireless guard missing: ' + relative)
    return rows


def cli_map(source_root):
    directory = source_root / 'package/dicttech/xr1710g-cli/files'
    mapping = {str(p.relative_to(directory)): p for p in directory.rglob('*') if p.is_file()}
    require(len(mapping) == 11 and 'etc/profile.d/80-xr1710g-cli.sh' in mapping,
            'unexpected Full CLI package layout')
    for relative, path in mapping.items():
        require(regular(path) and path.stat().st_mode & 0o7777 == 0o644,
                'invalid CLI source mode: ' + relative)
        require('authorized_keys' not in relative and 'ssh-ed25519 ' not in path.read_text()
                and 'ssh-rsa ' not in path.read_text() and 'PRIVATE KEY-----' not in path.read_text(),
                'authentication material in Full CLI assets')
    return mapping



def preset_sections(path):
    """Read only UCI data; reject malformed or ambiguous preset declarations."""
    if not path.exists() and not path.is_symlink():
        return []
    require(regular(path), 'preset UCI config is not regular: ' + path.name)
    current, sections = None, []
    for line in path.read_text().splitlines():
        words = shlex.split(line, comments=True)
        if not words:
            continue
        if words[0] == 'config':
            require(len(words) in (2, 3), 'invalid preset UCI section')
            current = {'type': words[1], 'options': {}}; sections.append(current)
        else:
            require(words[0] in ('option', 'list') and current is not None and len(words) == 3,
                    'invalid preset UCI option')
            key, value = words[1:]
            options = current['options']
            if words[0] == 'list':
                require(key not in options or isinstance(options[key], list), 'ambiguous preset UCI option')
                options.setdefault(key, []).append(value)
            else:
                require(key not in options, 'duplicate preset UCI option')
                options[key] = value
    return sections

def check_unprovisioned(rootfs, source_root):
    marker = rootfs / 'etc/xr1710g-full'
    require(regular(marker) and marker.stat().st_mode & 0o7777 == 0o644 and marker.read_bytes()==b'XR1710G Full\n', 'invalid full-edition marker')
    directory = rootfs / 'etc/wrtix'
    require(directory.is_dir() and not directory.is_symlink() and directory.stat().st_mode & 0o777 == 0o700,
            'private deployment directory must be regular 0700')
    require(not list(directory.iterdir()), 'virgin rootfs contains deployment state or secrets')
    forbidden = ('etc/dicttech-ap', 'etc/dicttech-setup', 'etc/dicttech-ap-bootstrap-key',
                 'usr/share/dicttech-ap', 'usr/share/dicttech-setup', 'usr/share/dicttech-cli',
                 'usr/libexec/dicttech-setup', 'usr/libexec/dicttech-cli',
                 'root/.zshrc', 'root/.vimrc', 'root/.tmux.conf')
    for relative in forbidden:
        path = rootfs / relative
        require(not path.exists() and not path.is_symlink(), 'personal asset remains in full image: ' + relative)
    for pattern in ('etc/dropbear/dropbear_*_host_key', 'etc/ssh/ssh_host_*_key', 'etc/uhttpd.key'):
        require(not list(rootfs.glob(pattern)), 'virgin rootfs contains a private device key')
    for relative in ('etc/dropbear/authorized_keys', 'root/.ssh/authorized_keys', 'root/.ssh/authorized_keys2'):
        path = rootfs / relative
        require(not path.is_symlink() and (not path.exists() or regular(path) and not path.read_bytes().strip()),
                'virgin full image contains preset authorized SSH keys')
    shadow = rootfs / 'etc/shadow'
    require(regular(shadow) and shadow.stat().st_mode & 0o777 == 0o600, 'virgin rootfs shadow must be regular 0600')
    rows = [line.split(':') for line in shadow.read_text().splitlines() if line.startswith('root:')]
    require(len(rows)==1 and len(rows[0])==9 and rows[0][1] in {'', '!', '*'}, 'virgin rootfs contains a preset root password')
    passwd = rootfs / 'etc/passwd'
    require(regular(passwd), 'virgin rootfs passwd missing or symlinked')
    rows = [line.split(':') for line in passwd.read_text().splitlines() if line.startswith('root:')]
    require(len(rows)==1 and len(rows[0])==7 and rows[0][-1]=='/bin/ash', 'full image overrides the default root shell')
    # Board detection creates network configuration on first boot. A virgin
    # image must not carry site-specific interfaces under any chosen name.
    require(not preset_sections(rootfs / 'etc/config/network'),
            'virgin rootfs contains preset network sections')
    for section in preset_sections(rootfs / 'etc/config/wireless'):
        require(section['type'] in ('wifi-iface', 'wifi-device'), 'invalid preset wireless section type')
        options = section['options']
        require(options.get('disabled') == '1', 'virgin rootfs has enabled preset Wi-Fi')
        require(not any(options.get(key) for key in ('key', 'key1', 'key2', 'key3', 'key4',
                    'sae_password', 'wpa_psk', 'r0kh', 'r1kh')),
                'virgin rootfs contains preset Wi-Fi credentials')
        require(not any(options.get(key) for key in ('ssid', 'bssid', 'macaddr', 'macaddr_base', 'mld_addr', 'nasid')),
                'virgin rootfs contains preset wireless identity')
    for section in preset_sections(rootfs / 'etc/config/firewall'):
        require(section['type'] in ('defaults', 'zone', 'forwarding', 'rule'),
                'virgin rootfs contains nondefault firewall section')
        for key, value in section['options'].items():
            values = value if isinstance(value, list) else [value]
            if key == 'network':
                require(all(v in ('lan', 'wan', 'wan6') for v in values),
                        'virgin rootfs contains site-specific firewall network')
            elif key in ('src', 'dest'):
                require(all(v in ('lan', 'wan', '*') for v in values),
                        'virgin rootfs contains site-specific firewall zone')
            elif key in ('src_ip', 'dest_ip', 'src_mac', 'dest_mac'):
                # The standard Allow-MLD rule needs this protocol-defined prefix.
                require(key == 'src_ip' and values == ['fe80::/10'],
                        'virgin rootfs contains site-specific firewall address')
        if section['type'] == 'zone':
            require(section['options'].get('name') in ('lan', 'wan'),
                    'virgin rootfs contains site-specific firewall zone')
    roaming = rootfs / 'etc/config/xr_roaming'
    require(regular(roaming), 'roaming configuration missing')
    require(all(not line.strip() or line.lstrip().startswith("#") for line in roaming.read_text().splitlines()),
            'roaming must ship without predefined profiles')
    public_link = rootfs / 'www/xr-roaming'
    require(public_link.is_symlink() and os.readlink(public_link)=='/tmp/xr-roaming-public', 'roaming publication link must point to RAM')
    return {'deployment_state':'absent', 'private_host_identity':'absent', 'preset_root_password':False,
            'preset_authorized_keys':'absent', 'root_shell':'/bin/ash', 'roaming_default':'unconfigured'}


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


def pinned_wrtix(source_root):
    spec = importlib.util.spec_from_file_location('dicttech_build_policy', source_root / 'scripts/dicttech/check-build.py')
    policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
    return policy.wrtix_source(source_root)[0]


def profile_map(source_root):
    marker_package = source_root / 'package/dicttech/xr1710g-full/Makefile'
    require(regular(marker_package), 'public full-edition marker package missing')
    require("printf 'XR1710G Full\\n' > $(1)/etc/xr1710g-full" in marker_package.read_text(), 'full marker install recipe changed')
    mapping = {}
    wrtix = pinned_wrtix(source_root)
    mapping.update({
        'usr/libexec/wrtix/agent': wrtix / 'agent/agent.sh',
        'etc/init.d/wrtix-agent': wrtix / 'agent/guard.init',
        'usr/share/wrtix/contract.json': wrtix / 'agent/contract.json',
    })
    assets = wrtix / 'feed/wrtix-agent/files'
    mapping.update({str(path.relative_to(assets)): path for path in assets.rglob('*') if path.is_file() or path.is_symlink()})
    return mapping


def check_runtime_manifest(rootfs):
    files = ('usr/libexec/wrtix/agent', 'etc/init.d/wrtix-agent', 'usr/share/wrtix/contract.json')
    for relative in ('usr/libexec/dicttech-ap', 'usr/lib/dicttech-ap', 'etc/init.d/dicttech-ap-guard'):
        require(not (rootfs / relative).exists() and not (rootfs / relative).is_symlink(), 'retired runtime remains in rootfs: ' + relative)
    for relative in (*files, 'usr/libexec/wrtix/package-lifecycle', 'lib/apk/commit_hooks.d/50-wrtix', 'lib/upgrade/wrtix.sh'):
        path = rootfs / relative
        expected = 0o644 if relative in ('usr/share/wrtix/contract.json', 'lib/upgrade/wrtix.sh') else 0o755
        require(path.stat().st_mode & 0o7777 == expected, 'wrtix runtime has unexpected mode: ' + relative)
        for parent in path.parents:
            if parent == rootfs:
                break
            require(not parent.stat().st_mode & 0o022, 'wrtix runtime parent is group/other writable: ' + relative)
    expected = ''.join(digest((rootfs / name).read_bytes()) + '  ' + name + '\n' for name in files).encode()
    return compare_bytes(expected, rootfs / 'usr/share/wrtix/runtime.sha256', 'usr/share/wrtix/runtime.sha256')


def check_rootfs(rootfs, source_root=REPO, manifest=None):
    rootfs, source_root = rootfs.resolve(), source_root.resolve()
    require(rootfs.is_dir(), 'rootfs directory missing')
    for relative in ('etc/wrtix/managed.json', 'etc/dropbear/key', 'etc/ssh/key', 'www/xr-roaming', 'lib/wifi/mac80211.uc'):
        safe_ancestors(rootfs, relative)
    mapping = profile_map(source_root)
    mapping.update(roaming_map(source_root))
    cli = cli_map(source_root)
    mapping.update(cli)
    history = history_sources(source_root)
    factory = factory_sources(source_root)
    for relative in {**mapping, **GENERATOR_FILES, **history, **factory}:
        safe_ancestors(rootfs, relative)
    rows = [compare(path, rootfs / relative, relative) for relative, path in sorted(mapping.items())]
    for relative in cli:
        require((rootfs / relative).stat().st_mode & 0o7777 == 0o644,
                'invalid installed CLI file mode: ' + relative)
    rows.append(check_runtime_manifest(rootfs))
    rows += check_generators(source_root, rootfs)
    rows += [compare_bytes(expected, rootfs / relative, relative) for relative, expected in sorted(history.items())]
    rows += check_factory(rootfs, source_root, factory)
    result = {'schema_version': 1, 'passed': True, 'files_checked': rows,
              'clean_image': check_unprovisioned(rootfs, source_root), 'luci_rpc': check_luci_rpc(rootfs),
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
        print('Rootfs check failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
