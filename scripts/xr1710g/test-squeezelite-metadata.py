#!/usr/bin/env python3
"""Regress the optional-codec cycle with real feed Make/Kconfig declarations.

Read exact Git blobs without updating the feed. GNU Make expands the real
package inheritance, metadata.pl emits the actual dependency closure, and the
real Kconfig parser checks both the unpatched and patched graphs. Only external
leaf packages and machine/build feature symbols are inert fixtures.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
REVISION = '72c7391fb2d04080afd0bfe919605749f81eb747'
PATCH = ROOT / 'patches/feeds/packages/sound/squeezelite/100-keep-codec-menu-independent.patch'
PACKAGES = {
    'sound/squeezelite/Makefile': [
        'squeezelite-full', 'squeezelite-dynamic', 'squeezelite-custom'],
    'multimedia/ffmpeg/Makefile': ['libffmpeg', 'libffmpeg-mini'],
    'libs/libdrm/Makefile': ['libdrm'],
    'libs/libx264/Makefile': ['libx264'],
    'libs/alsa-lib/Makefile': ['alsa-lib'],
    'libs/libudev-zero/Makefile': ['libudev-zero'],
}


def command(argv, directory, **kwargs):
    return subprocess.run(argv, cwd=directory, text=True, capture_output=True,
                          timeout=45, **kwargs)


def git_file(feed, revision, path):
    return command(['git', '-C', str(feed), 'show', revision + ':' + path],
                   ROOT, env=dict(os.environ, GIT_NO_LAZY_FETCH='1'), check=True).stdout


def expand(files, directory):
    definitions = '\n'.join(
        definition for source in files.values()
        for definition in re.findall(r'^define Package/[^\n]+\n.*?^endef\n',
                                     source, re.M | re.S))
    lines = ['.PHONY: all\nall:\n', definitions]
    for names in PACKAGES.values():
        for name in names:
            lines.extend(['$(eval DEPENDS:=)', '$(eval PROVIDES:=)',
                          '$(eval CONFLICTS:=)', '$(eval MENU:=)',
                          '$(eval $(call Package/' + name + '))',
                          '$(info ROW\t' + name + '\t$(DEPENDS)\t$(PROVIDES)'
                          '\t$(CONFLICTS)\t$(MENU))'])
    makefile = directory / 'declarations.mk'
    makefile.write_text('\n'.join(lines) + '\n')
    result = command(['make', '--no-print-directory', '-f', str(makefile), 'all'],
                     directory, check=True)
    records = {}
    for line in result.stdout.splitlines():
        if line.startswith('ROW\t'):
            _, name, depends, provides, conflicts, menu = line.split('\t')
            records[name] = dict(depends=depends, provides=provides,
                                 conflicts=conflicts, menu=menu)
    assert len(records) == sum(map(len, PACKAGES.values()))
    return records


def configuration(source, name):
    match = re.search(r'^define Package/' + re.escape(name) +
                      r'/config\n(.*?)^endef\n', source, re.M | re.S)
    return match.group(1) if match else ''


def generate(files, ffmpeg_config, directory):
    records = expand(files, directory)
    rows = []
    aliases = {alias for record in records.values()
               for alias in record['provides'].split()}
    for path, names in PACKAGES.items():
        rows.append('Source-Makefile: package/feeds/packages/' +
                    Path(path).parent.name + '/Makefile\n')
        for name in names:
            record = records[name]
            config = configuration(files[path], name)
            # Resolve the real libffmpeg Config.in include to this isolated copy.
            config = config.replace('$(SOURCE)/Config.in', str(directory / 'ffmpeg.in'))
            rows.append('Package: ' + name + '\nDepends: ' + record['depends'] +
                        '\nProvides: ' + record['provides'] + '\nConflicts: ' +
                        record['conflicts'] + '\nMenu: ' + (record['menu'] or '0') +
                        '\nType: ipkg\nSection: libs\nCategory: Libraries\n'
                        'Title: Dependency regression\nDescription: fixture\n@@\n' +
                        ('Config:\n' + config + '@@\n' if config else ''))
    leaves = {'libc'}
    for record in records.values():
        for dependency in record['depends'].split():
            if not dependency.startswith('@'):
                leaves.add(dependency.rsplit(':', 1)[-1].lstrip('+'))
    leaves -= records.keys() | aliases
    for name in sorted(leaves):
        rows.append('Source-Makefile: package/fixture/' + name + '/Makefile\n'
                    'Package: ' + name + '\nDepends:\nType: ipkg\nSection: libs\n'
                    'Category: Libraries\nTitle: External leaf\nDescription: fixture\n@@\n')
    (directory / 'ffmpeg.in').write_text(ffmpeg_config)
    metadata = directory / 'packageinfo'
    metadata.write_text('\n'.join(rows))
    result = command(['perl', str(ROOT / 'scripts/package-metadata.pl'),
                      'config', str(metadata)], directory, check=True)
    assert 'does not exist' not in result.stderr, result.stderr
    return result.stdout


def configure(generated, conf, directory, seed):
    directory.mkdir()
    image = directory / 'package/fixture/image-config.in'
    image.parent.mkdir(parents=True)
    image.write_text('')
    features = ['ALL', 'ALL_NONSHARED', 'BUILD_PATENTED', 'SOFT_FLOAT',
                'x86_64', 'i386', 'TARGET_x86_geode', 'TARGET_x86_legacy']
    prefix = 'mainmenu "Squeezelite dependency regression"\n'
    prefix += 'config MODULES\n\tmodules\n\tbool\n\tdefault y\n'
    prefix += ''.join('config ' + name + '\n\tbool\n' for name in features)
    (directory / 'Kconfig').write_text(prefix + generated)
    (directory / 'seed').write_text(seed)
    result = command([str(conf), '--fatalrecursive', '--defconfig=' +
                      str(directory / 'seed'), str(directory / 'Kconfig')],
                     directory, env=dict(os.environ,
                                         KCONFIG_CONFIG=str(directory / '.config')))
    text = (directory / '.config').read_text() if (directory / '.config').exists() else ''
    return result, text



def selected(text, name):
    match = re.search(r'^CONFIG_' + re.escape(name) + r'=(y|m|n)$', text, re.M)
    return match.group(1) if match else 'n'


def flags(source, directory, text):
    # Evaluate the real custom-variant Make conditions, without include files,
    # BuildPackage expansion, configuration, toolchain work or any compilation.
    source = re.sub(r'^include .*\n', '', source, flags=re.M)
    makefile = directory / 'flags.mk'
    makefile.write_text('.PHONY: all\nall:\n\t@:\n' + source +
                        '\n$(info OPTS\t$(opts))\n$(info CFLAGS\t$(TARGET_CFLAGS))\n')
    settings = re.findall(r'^(CONFIG_[A-Za-z0-9_]+)=(y|m|n)$', text, re.M)
    result = command(['make', '--no-print-directory', '-f', str(makefile), 'all',
                      'BUILD_VARIANT=custom', *[k + '=' + v for k, v in settings]],
                     directory, check=True)
    return {line.split('\t', 1)[0]: line.split('\t', 1)[1]
            for line in result.stdout.splitlines() if line.startswith(('OPTS\t', 'CFLAGS\t'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed-path', type=Path, default=ROOT / 'feeds/packages')
    parser.add_argument('--feed-revision', default=REVISION)
    parser.add_argument('--kconfig-conf', type=Path, default=ROOT / 'scripts/config/conf')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.feed_revision):
        parser.error('feed revision must be an exact commit')
    feed, conf = args.feed_path.resolve(), args.kconfig_conf.resolve()
    if not conf.is_file():
        parser.error('pass an existing --kconfig-conf; this test never compiles it')
    if args.output and args.output.exists():
        parser.error('use a new output file')
    with tempfile.TemporaryDirectory(prefix='squeezelite-metadata-') as temporary:
        root = Path(temporary)
        original = {path: git_file(feed, args.feed_revision, path) for path in PACKAGES}
        ffmpeg = git_file(feed, args.feed_revision, 'multimedia/ffmpeg/Config.in')
        target = root / 'source/sound/squeezelite/Makefile'
        target.parent.mkdir(parents=True)
        target.write_text(original['sound/squeezelite/Makefile'])
        for options in [['--check'], [], ['--reverse', '--check']]:
            command(['git', 'apply', *options, str(PATCH)], root / 'source', check=True)
        patched = dict(original, **{'sound/squeezelite/Makefile': target.read_text()})
        old_source, new_source = original['sound/squeezelite/Makefile'], target.read_text()
        config_pattern = r'^define Package/squeezelite-custom/config\n.*?^endef\n'
        assert re.sub(config_pattern, '', old_source, flags=re.M | re.S) == re.sub(
            config_pattern, '', new_source, flags=re.M | re.S)
        def options_only(source):
            return '\n'.join(line for line in configuration(source, 'squeezelite-custom').splitlines()
                if line.strip() and not line.strip().startswith(('#', 'if PACKAGE_', 'menu ', 'endmenu', 'endif')))
        assert options_only(old_source) == options_only(new_source)
        assert '+SQUEEZELITE_WMA_ALAC:libffmpeg-audio-dec' in new_source
        before, after = root / 'before', root / 'after'
        before.mkdir(); after.mkdir()
        old_graph = generate(original, ffmpeg, before)
        new_graph = generate(patched, ffmpeg, after)
        base_seed = ('CONFIG_MODULES=y\nCONFIG_PACKAGE_audio-support=y\n'
                     'CONFIG_PACKAGE_video-support=y\nCONFIG_BUILD_PATENTED=y\n')
        result, _ = configure(old_graph, conf, root / 'negative', base_seed)
        error = result.stdout + result.stderr
        assert result.returncode and 'recursive dependency' in error, error
        assert 'PACKAGE_squeezelite-custom' in error and 'SQUEEZELITE_WMA_ALAC' in error, error
        cases = []
        for parent in ('n', 'y', 'm'):
            for codec in ('n', 'y'):
                name = 'parent-' + parent + '-codec-' + codec
                seed = base_seed + 'CONFIG_PACKAGE_squeezelite-custom=' + parent + '\n'
                seed += 'CONFIG_SQUEEZELITE_WMA_ALAC=' + codec + '\n'
                result, text = configure(new_graph, conf, root / name, seed)
                assert result.returncode == 0, result.stdout + result.stderr
                assert 'recursive dependency' not in result.stdout + result.stderr
                actual_parent = selected(text, 'PACKAGE_squeezelite-custom')
                actual_codec = selected(text, 'SQUEEZELITE_WMA_ALAC')
                decoder = selected(text, 'PACKAGE_libffmpeg')
                expected_decoder = parent if codec == 'y' else 'n'
                assert actual_parent == parent, (name, text)
                assert actual_codec == codec, (name, text)
                assert decoder == expected_decoder, (name, text)
                row = {'case': name, 'passed': True, 'parent': actual_parent,
                       'codec': actual_codec, 'decoder': decoder}
                if parent != 'n':
                    old_flags = flags(old_source, root / name, text)
                    new_flags = flags(new_source, root / name, text)
                    assert old_flags == new_flags, name
                    assert ('-DFFMPEG' in new_flags['OPTS'].split()) == (codec == 'y'), row
                    row['flags_unchanged'] = True
                    row['opts'] = new_flags['OPTS']
                cases.append(row)
        constraint_cases = [
            ('video-required',
             'CONFIG_PACKAGE_video-support=n\nCONFIG_FFMPEG_LIBDRM=y\nCONFIG_FFMPEG_V4L2_REQUEST=y\n',
             'n', 'n'),
            ('audio-without-video',
             'CONFIG_PACKAGE_video-support=n\nCONFIG_FFMPEG_LIBDRM=n\nCONFIG_FFMPEG_V4L2_REQUEST=n\n',
             'y', 'y'),
            ('patented-disabled', 'CONFIG_BUILD_PATENTED=n\nCONFIG_FFMPEG_LIBX264=y\n', 'y', 'y'),
        ]
        for name, overrides, expected_parent, expected_decoder in constraint_cases:
            seed = base_seed + 'CONFIG_PACKAGE_squeezelite-custom=y\nCONFIG_SQUEEZELITE_WMA_ALAC=y\n'
            for line in overrides.splitlines():
                key = line.split('=', 1)[0]
                seed = re.sub(r'^' + re.escape(key) + r'=.*\n', '', seed, flags=re.M)
            seed += overrides
            result, text = configure(new_graph, conf, root / name, seed)
            assert result.returncode == 0, result.stdout + result.stderr
            assert 'recursive dependency' not in result.stdout + result.stderr
            assert selected(text, 'PACKAGE_squeezelite-custom') == expected_parent, (name, text)
            assert selected(text, 'PACKAGE_libffmpeg') == expected_decoder, (name, text)
            if name == 'patented-disabled':
                assert selected(text, 'FFMPEG_LIBX264') == 'n', text
                assert selected(text, 'PACKAGE_libx264') == 'n', text
            if expected_parent == 'y':
                assert flags(old_source, root / name, text) == flags(new_source, root / name, text)
            cases.append({'case': name, 'passed': True, 'parent': expected_parent,
                          'decoder': expected_decoder, 'constraint_enforced': True})
        assert 'select PACKAGE_libffmpeg if SQUEEZELITE_WMA_ALAC' in new_graph
        result = {'passed': True, 'feed_revision': args.feed_revision,
                  'preimage_sha256': hashlib.sha256(old_source.encode()).hexdigest(),
                  'postimage_sha256': hashlib.sha256(new_source.encode()).hexdigest(),
                  'patch_sha256': hashlib.sha256(PATCH.read_bytes()).hexdigest(),
                  'metadata_generator_sha256': hashlib.sha256((ROOT / 'scripts/package-metadata.pl').read_bytes()).hexdigest(),
                  'kconfig_conf_sha256': hashlib.sha256(conf.read_bytes()).hexdigest(),
                  'real_make_metadata_and_kconfig': True, 'original_cycle_reproduced': True,
                  'original_cycle_diagnostic': error,
                  'decoder_dependency': 'conditional on WMA/ALAC and selected custom variant',
                  'menu_visibility': 'independent menu remains visible without selecting the package',
                  'other_recipe_content_unchanged': True, 'codec_options_unchanged': True,
                  'cases': cases, 'feed_changed': False, 'firmware_built': False,
                  'codec_runtime_tested': False,
                  'limits': 'Real feed declarations and metadata parser; external leaves and machine/build features are inert. No codec binary compilation or runtime playback.'}
    if args.output:
        with args.output.open('x') as output:
            output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
