#!/usr/bin/env python3
"""Exercise the real feed patches through Make, metadata.pl and Kconfig.

No package build or feed checkout mutation. The minimal metadata derives its
package declarations from the pinned feed Makefiles, not copied dependencies.
"""
from pathlib import Path
import argparse
import json
import os
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
PATCHES = {
    'net/git/Makefile': 'net/git/100-condition-gitweb-perl-dependencies.patch',
    'net/rsync/Makefile': 'net/rsync/100-condition-rrsync-python-dependency.patch',
    'utils/vim/Makefile': 'utils/vim/100-avoid-fuller-real-package-provider-cycle.patch',
}
PACKAGES = {
    'net/git/Makefile': ['git', 'git-http', 'git-gitweb'],
    'net/rsync/Makefile': ['rsync', 'rsyncd', 'rrsync'],
    'utils/vim/Makefile': ['vim', 'vim-full', 'vim-fuller', 'vim-runtime'],
    'cli-profile': ['cli-profile'],
}


def command(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                          timeout=45, **kwargs)


def fields(makefiles, directory):
    """GNU Make expands the actual Package/Default inheritance and += rules."""
    definitions = []
    for text in makefiles.values():
        definitions.extend(re.findall(r'^define Package/[^\n]+\n.*?^endef\n', text, re.M | re.S))
    rows = ['.PHONY: all\nall:\n', '\n'.join(definitions)]
    for names in PACKAGES.values():
        for name in names:
            rows.extend(['$(eval DEPENDS:=)', '$(eval PROVIDES:=)', '$(eval CONFLICTS:=)',
                         '$(eval $(call Package/' + name + '))',
                         '$(info RECORD\t' + name + '\t$(DEPENDS)\t$(PROVIDES)\t$(CONFLICTS))'])
    path = directory / 'read-packages.mk'; path.write_text('\n'.join(rows) + '\n')
    result = command(['make', '--no-print-directory', '-f', str(path), 'all'], directory, check=True)
    parsed = {}
    for line in result.stdout.splitlines():
        if line.startswith('RECORD\t'):
            _, name, depends, provides, conflicts = line.split('\t')
            parsed[name] = {'depends': depends, 'provides': provides, 'conflicts': conflicts}
    assert len(parsed) == sum(len(names) for names in PACKAGES.values())
    return parsed


def metadata(records):
    """Only unrelated leaf libraries are synthetic; tested declarations are real."""
    result = []
    for path, names in PACKAGES.items():
        source = 'dicttech/cli-profile' if path == 'cli-profile' else 'feeds/packages/' + Path(path).parent.name
        result.append('Source-Makefile: package/' + source + '/Makefile\n')
        for name in names:
            record = records[name]
            result.append('Package: ' + name + '\nDepends: ' + record['depends'] +
                          '\nProvides: ' + record['provides'] + '\nConflicts: ' + record['conflicts'] +
                          '\nSection: utils\nCategory: Utilities\nTitle: CLI dependency fixture\nDescription: fixture\n@@\n')
    leaves = {'libc'}
    for record in records.values():
        for dependency in record['depends'].split():
            if dependency.startswith('@'): continue
            leaves.add(dependency.rsplit(':', 1)[-1].lstrip('+'))
    leaves -= records.keys()
    for name in sorted(leaves):
        source = 'python3' if name == 'python3-light' else ('perl' if name.startswith('perlbase-') else name)
        result.append('Source-Makefile: package/feeds/packages/' + source + '/Makefile\nPackage: ' + name +
                      '\nDepends: \nSection: libs\nCategory: Libraries\nTitle: Leaf fixture\nDescription: fixture\n@@\n')
    return '\n'.join(result)


def generate(records, directory):
    info = directory / 'packageinfo'; info.write_text(metadata(records))
    outputs = {}
    for mode in ['mk', 'config']:
        result = command(['perl', str(ROOT / 'scripts/package-metadata.pl'), mode, str(info)], directory, check=True)
        assert 'does not exist' not in result.stderr, result.stderr
        outputs[mode] = result.stdout
    return outputs


def dependencies(generated, selected, directory):
    path = directory / 'dependencies.mk'; path.write_text(generated)
    data = 'curdir:=package\n' + ''.join('CONFIG_PACKAGE_' + name + ':=y\n' for name in selected)
    data += 'include ' + str(path) + '\n.PHONY: all\nall:\n'
    for name in ['git', 'rsync']:
        data += '$(info EDGE ' + name + '=$(package/feeds/packages/' + name + '/compile))\n'
    script = directory / 'read-dependencies.mk'; script.write_text(data)
    output = command(['make', '--no-print-directory', '-f', str(script), 'all'], directory, check=True).stdout
    return {name: value.split() for name, value in re.findall(r'^EDGE (\S+)=(.*)$', output, re.M)}


def kconfig(generated, conf, directory):
    # Define only external feature symbols used by the locked declarations.
    features = ['ALL', 'BUILD_NLS', 'RSYNC_xattr', 'RSYNC_acl', 'RSYNC_idn',
                'RSYNC_zstd', 'RSYNC_xxhash', 'RSYNC_lz4', 'PACKAGE_openssh-server',
                'PACKAGE_openssh-server-pam', 'PACKAGE_dropbear', 'DROPBEAR_SVR_PUBKEY_OPTIONS']
    prefix = 'mainmenu "CLI regression"\nconfig MODULES\n\tmodules\n\tbool\n\tdefault y\n'
    prefix += ''.join('config ' + name + '\n\tbool\n' for name in features)
    images = directory / 'package/fixture/image-config.in'
    images.parent.mkdir(parents=True); images.write_text('')
    path = directory / 'Kconfig'; path.write_text(prefix + generated)
    seed = directory / 'seed'; seed.write_text('CONFIG_PACKAGE_cli-profile=y\nCONFIG_PACKAGE_vim-full=y\n')
    env = dict(os.environ, KCONFIG_CONFIG=str(directory / '.config'))
    return command([str(conf), '--fatalrecursive', '--defconfig=' + str(seed), str(path)], directory, env=env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed-path', type=Path, default=ROOT / 'feeds/packages')
    parser.add_argument('--kconfig-conf', type=Path, default=ROOT / 'scripts/config/conf')
    args = parser.parse_args()
    feed, conf = args.feed_path.resolve(), args.kconfig_conf.resolve()
    if not conf.is_file(): raise SystemExit('Build scripts/config/conf first or pass --kconfig-conf')
    lock = re.search(r'^src-git packages \S+\^([0-9a-f]{40})$',
                     (ROOT / 'configs/dicttech-feeds.conf').read_text(), re.M)
    assert lock, 'packages feed must have an exact revision'
    revision = lock[1]
    with tempfile.TemporaryDirectory(prefix='dicttech-cli-metadata-') as temporary:
        directory = Path(temporary); pristine = directory / 'pristine'; pristine.mkdir()
        original = {}
        for path, patch in PATCHES.items():
            original[path] = command(['git', '-C', str(feed), 'show', revision + ':' + path], ROOT, check=True).stdout
            target = pristine / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(original[path])
        # Validate and apply the checked-in patch files to exact git-show bytes.
        for patch in PATCHES.values():
            path = ROOT / 'patches/feeds/packages' / patch
            command(['git', 'apply', '--check', str(path)], pristine, check=True)
            command(['git', 'apply', str(path)], pristine, check=True)
            command(['git', 'apply', '--reverse', '--check', str(path)], pristine, check=True)
        patched = {path: (pristine / path).read_text() for path in PATCHES}
        cli = (ROOT / 'package/dicttech/cli-profile/Makefile').read_text()
        original['cli-profile'] = patched['cli-profile'] = cli
        before_dir, after_dir = directory / 'before', directory / 'after'
        before_dir.mkdir(); after_dir.mkdir()
        before = generate(fields(original, before_dir), before_dir)
        after = generate(fields(patched, after_dir), after_dir)
        baseline = dependencies(before['mk'], [], before_dir)
        unselected = dependencies(after['mk'], [], after_dir)
        selected = dependencies(after['mk'], ['rrsync', 'git-gitweb'], after_dir)
        python = 'package/feeds/packages/python3/compile'
        perl = 'package/feeds/packages/perl/compile'
        assert python in baseline['rsync'] and perl in baseline['git'], 'negative control did not reproduce original build graph'
        assert python not in unselected['rsync'] and perl not in unselected['git']
        assert 'package/feeds/packages/perl-cgi/compile' not in unselected['git']
        assert python in selected['rsync'] and perl in selected['git']
        assert 'package/feeds/packages/perl-cgi/compile' in selected['git'], 'selected gitweb lost interpreter dependency'
        old_conf = kconfig(before['config'], conf, before_dir)
        new_conf = kconfig(after['config'], conf, after_dir)
        assert old_conf.returncode != 0 and 'recursive dependency' in old_conf.stdout + old_conf.stderr, old_conf.stdout + old_conf.stderr
        assert new_conf.returncode == 0 and 'recursive dependency' not in new_conf.stdout + new_conf.stderr, new_conf.stdout + new_conf.stderr
        final = (after_dir / '.config').read_text()
        assert 'CONFIG_PACKAGE_cli-profile=y' in final and 'CONFIG_PACKAGE_vim-full=y' in final
        assert 'CONFIG_PACKAGE_vim-fuller=y' not in final
    print(json.dumps({'result': 'PASS', 'feed_revision': revision,
                      'patches_apply_cleanly': True, 'unselected_sibling_dependencies_pruned': True,
                      'selected_sibling_dependencies_retained': True, 'vim_cycle_negative_and_positive_controls': True}))


if __name__ == '__main__': main()
