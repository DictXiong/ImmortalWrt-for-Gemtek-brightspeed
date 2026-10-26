#!/usr/bin/env python3
"""Read-only FIT, XR1710G partition, metadata and optional module checks."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib


class ImageError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ImageError(message)


def u32(value):
    require(len(value) == 4, 'expected one 32-bit DT cell')
    return struct.unpack('>I', value)[0]


def string(value):
    require(value.endswith(b'\0') and b'\0' not in value[:-1], 'invalid DT string')
    return value[:-1].decode('ascii')


def strings(value):
    require(value.endswith(b'\0'), 'unterminated DT string list')
    return [part.decode('ascii') for part in value[:-1].split(b'\0')]


def fdt(blob):
    require(len(blob) >= 40, 'truncated FDT header')
    magic, total, start, names, reserve, version, compat, cpu, names_size, tree_size = struct.unpack_from('>10I', blob)
    require(magic == 0xd00dfeed and 40 <= total <= len(blob), 'invalid FDT size or magic')
    require(version >= 17 and compat <= 17, 'unsupported FDT version')
    require(start + tree_size <= total and names + names_size <= total, 'FDT tables exceed declared size')
    end = start + tree_size
    pos, stack, nodes = start, [], {}
    while pos + 4 <= end:
        token = struct.unpack_from('>I', blob, pos)[0]; pos += 4
        if token == 1:
            stop = blob.find(b'\0', pos, end)
            require(stop >= 0, 'unterminated FDT node')
            name = blob[pos:stop].decode('ascii'); pos = (stop + 4) & ~3
            require('/' not in name and len(stack) < 128, 'invalid FDT node name or depth')
            stack.append(name)
            path = '/' + '/'.join(stack[1:])
            require(path not in nodes, 'duplicate FDT node')
            nodes[path] = {}
        elif token == 2:
            require(bool(stack), 'unbalanced FDT node')
            stack.pop()
        elif token == 3:
            require(bool(stack) and pos + 8 <= end, 'invalid FDT property header')
            length, offset = struct.unpack_from('>II', blob, pos); pos += 8
            require(offset < names_size and pos + length <= end, 'invalid FDT property bounds')
            stop = blob.find(b'\0', names + offset, names + names_size)
            require(stop >= 0, 'unterminated FDT property name')
            name = blob[names + offset:stop].decode('ascii')
            props = nodes['/' + '/'.join(stack[1:])]
            require(name not in props, 'duplicate FDT property')
            props[name] = blob[pos:pos + length]; pos = (pos + length + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            require(not stack and '/' in nodes, 'unbalanced FDT structure')
            return total, nodes
        else:
            raise ImageError('unknown FDT structure token')
    raise ImageError('FDT end token missing')


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate firmware metadata key')
        result[key] = value
    return result


def firmware_metadata(blob):
    end, metadata, signatures = len(blob), None, 0
    for _ in range(4):
        require(end >= 16, 'fwtool metadata trailer missing')
        magic, crc, kind, size = struct.unpack_from('>IIB3xI', blob, end - 16)
        require(magic == 0x46577830 and 16 <= size <= min(end, 32768), 'invalid fwtool trailer')
        require((zlib.crc32(blob[:end - 16]) ^ 0xffffffff) == crc, 'fwtool firmware CRC mismatch')
        start = end - size
        if kind == 1:
            require(metadata is None and size >= 24, 'duplicate or truncated fwtool metadata')
            require(blob[start:start + 8] == b'\0' * 8, 'unsupported fwtool information header')
            metadata = json.loads(blob[start + 8:end - 16], object_pairs_hook=unique_json)
            end = start
            break
        require(kind == 0, 'unknown fwtool trailer type')
        signatures += 1; end = start
    require(isinstance(metadata, dict), 'fwtool information missing')
    return metadata, end, signatures


def image_payload(blob, total, props, payload_end):
    if 'data' in props:
        return props['data']
    require('data-size' in props, 'FIT image data size missing')
    size = u32(props['data-size'])
    if 'data-position' in props:
        start = u32(props['data-position'])
    else:
        require('data-offset' in props, 'FIT external data position missing')
        start = ((total + 3) & ~3) + u32(props['data-offset'])
    require(start >= total and size > 0 and start + size <= payload_end, 'FIT external image exceeds firmware payload')
    return blob[start:start + size]


def child_paths(nodes, parent):
    prefix = parent.rstrip('/') + '/'
    return [path for path in nodes if path.startswith(prefix) and '/' not in path[len(prefix):]]


PARTITIONS = {
    'legacy': [('bootloader', 0, 0x200000, True), ('uenv', 0x200000, 0x200000, False),
               ('dsd', 0x400000, 0x200000, False), ('chainloader', 0x600000, 0x100000, True),
               ('ubi', 0x700000, 0x1b700000, False), ('reserved_bmt', 0x1be00000, 0x4200000, True)],
    'ubi': [('bl2', 0, 0x20000, True), ('ubi', 0x20000, 0, False)],
}


def check_tree(blob, layout, allow_legacy_alias=False):
    _, nodes = fdt(blob)
    expected_board = 'gemtek,xr1710g' + ('-ubi' if layout == 'ubi' else '')
    opposite = 'gemtek,xr1710g' if layout == 'ubi' else 'gemtek,xr1710g-ubi'
    compatible = strings(nodes['/'].get('compatible', b''))
    require(compatible and compatible[0] == expected_board, 'DT primary board does not match the selected layout')
    require(opposite not in compatible or (layout == 'legacy' and allow_legacy_alias), 'DT mixes legacy and UBI board aliases')
    groups = [path for path, props in nodes.items()
              if props.get('compatible') == b'fixed-partitions\0']
    require(len(groups) == 1, 'expected exactly one fixed NAND partition table')
    table = groups[0]
    actual, partition_paths = [], {}
    for path in child_paths(nodes, table):
        props = nodes[path]
        require('label' in props and len(props.get('reg', b'')) == 8, 'invalid NAND partition entry')
        label = string(props['label']); offset, size = struct.unpack('>II', props['reg'])
        actual.append((label, offset, size, 'read-only' in props)); partition_paths[label] = path
    actual.sort(key=lambda item: item[1])
    require(actual == PARTITIONS[layout], 'NAND partitions/offsets/sizes/read-only flags differ from the selected layout')
    root = nodes.get('/chosen', {}).get('rootdisk', b'')
    require(len(root) == 4, 'chosen rootdisk phandle missing')
    roots = [path for path, props in nodes.items() if props.get('phandle') == root]
    require(len(roots) == 1 and roots[0].startswith(partition_paths['ubi'] + '/volumes/') and
            nodes[roots[0]].get('volname') == b'fit\0', 'rootdisk does not select this UBI partition fit volume')
    factories = [path for path in child_paths(nodes, partition_paths['ubi'] + '/volumes')
                 if nodes[path].get('volname') == b'factory\0']
    require(len(factories) == 1, 'factory calibration volume missing or duplicated')
    cells = []
    for path in child_paths(nodes, factories[0] + '/nvmem-layout'):
        reg = nodes[path].get('reg', b'')
        if len(reg) == 8:
            cells.append(tuple(struct.unpack('>II', reg)))
    expected_cells = {(0, 0x1e00), (0x5000, 6), (0x6000, 6)} if layout == 'legacy' else {(0x5000, 0x1e00), (0x6c, 0x11), (0x86, 0x11)}
    require(set(cells) == expected_cells, 'factory EEPROM/MAC cell offsets do not match the selected layout format')
    return {'compatible': compatible, 'partitions': [{'name': name, 'offset': offset, 'size': size, 'read_only': ro}
            for name, offset, size, ro in actual], 'rootdisk_volume': 'fit', 'factory_cell_format': layout,
            'dtb_sha256': hashlib.sha256(blob).hexdigest()}


def check_image(blob, layout, allow_legacy_alias=False, rootfs_sink=None):
    metadata, payload_end, signatures = firmware_metadata(blob)
    expected_board = 'gemtek,xr1710g' + ('-ubi' if layout == 'ubi' else '')
    expected_compat = '1.0' if layout == 'legacy' else '2.0'
    require(metadata.get('compat_version', '1.0') == expected_compat, 'metadata compatibility version does not match the layout')
    supported = metadata.get('supported_devices' if expected_compat == '1.0' else 'new_supported_devices')
    require(supported == [expected_board], 'metadata supported device list does not match the layout')
    version = metadata.get('version', {})
    require(isinstance(version, dict) and version.get('target') == 'airoha/an7581' and
            version.get('board') == 'gemtek_xr1710g' + ('-ubi' if layout == 'ubi' else ''), 'metadata target/profile does not match XR1710G layout')
    total, nodes = fdt(blob)
    default = string(nodes.get('/configurations', {}).get('default', b''))
    configuration = nodes.get('/configurations/' + default)
    require(configuration is not None and 'fdt' in configuration, 'default FIT configuration does not select a DTB')
    require('kernel' in configuration and 'loadables' in configuration, 'default FIT configuration misses kernel or rootfs references')
    selected_fdt = string(configuration['fdt'])
    payloads, hashes = {}, {}
    for path in child_paths(nodes, '/images'):
        name = path.rsplit('/', 1)[1]; data = image_payload(blob, total, nodes[path], payload_end)
        algorithms = []
        for child in child_paths(nodes, path):
            props = nodes[child]
            if 'algo' not in props:
                continue
            algo = string(props['algo']); expected = props.get('value', b'')
            if algo == 'crc32':
                actual = struct.pack('>I', zlib.crc32(data))
            else:
                require(algo in {'sha1', 'sha256', 'sha384', 'sha512'}, 'unsupported FIT image hash algorithm')
                actual = hashlib.new(algo, data).digest()
            require(actual == expected, 'FIT image hash mismatch: ' + name)
            algorithms.append(algo)
        require(algorithms, 'FIT image hash missing: ' + name)
        payloads[name] = data; hashes[name] = algorithms
    kernel = string(configuration['kernel'])
    loadables = strings(configuration['loadables'])
    require(kernel in payloads and nodes['/images/' + kernel].get('type') == b'kernel\0', 'default FIT kernel reference is invalid')
    require(all(name in payloads for name in loadables) and any(nodes['/images/' + name].get('type') == b'filesystem\0' for name in loadables), 'default FIT rootfs reference is invalid')
    require(selected_fdt in payloads and nodes['/images/' + selected_fdt].get('type') == b'flat_dt\0', 'selected FIT DTB is absent or has the wrong type')
    # Check every DTB configuration, not just the default, to prevent a second
    # boot configuration selecting the other partition layout.
    dtbs = {string(nodes[path]['fdt']) for path in child_paths(nodes, '/configurations') if 'fdt' in nodes[path]}
    require(dtbs == {selected_fdt}, 'FIT configurations reference different DTBs')
    selected_roots = set()
    for path in child_paths(nodes, '/configurations'):
        refs = strings(nodes[path].get('loadables', b''))
        roots = [name for name in refs if name in payloads and nodes['/images/' + name].get('type') == b'filesystem\0']
        require(len(roots) == 1, 'FIT configuration does not select one unique rootfs image')
        selected_roots.update(roots)
    require(len(selected_roots) == 1, 'FIT configurations select different rootfs images')
    selected_root = next(iter(selected_roots))
    require(nodes['/images/' + selected_root].get('compression', b'none\0') == b'none\0', 'FIT rootfs payload is compressed')
    tree = check_tree(payloads[selected_fdt], layout, allow_legacy_alias)
    if rootfs_sink is not None:
        rootfs_sink['payload'] = payloads[selected_root]
    return {'schema_version': 1, 'passed': True, 'layout': layout,
            'image_sha256': hashlib.sha256(blob).hexdigest(), 'metadata': metadata,
            'fwtool_crc_verified': True, 'fwtool_signature_chunks': signatures,
            'signature_authenticity': 'not_checked', 'fit_hashes': hashes,
            'selected_rootfs': {'image': selected_root, 'size': len(payloads[selected_root]), 'sha256': hashlib.sha256(payloads[selected_root]).hexdigest()}, **tree}


def elf_vermagic(data):
    require(len(data) >= 64 and data[:4] == b'\x7fELF' and data[4] == 2 and data[5] in (1, 2), 'unsupported kernel module ELF format')
    endian = '<' if data[5] == 1 else '>'
    header = struct.unpack_from(endian + 'HHIQQQIHHHHHH', data, 16)
    require(header[1] == 183, 'kernel module is not AArch64')
    offset, entry_size, count, names_index = header[5], header[10], header[11], header[12]
    require(entry_size >= 64 and count > 0 and names_index < count and offset + count * entry_size <= len(data), 'invalid module section table')
    sections = [struct.unpack_from(endian + 'IIQQQQIIQQ', data, offset + i * entry_size) for i in range(count)]
    names = sections[names_index]; table = data[names[4]:names[4] + names[5]]
    for section in sections:
        end = table.find(b'\0', section[0]); require(end >= 0, 'invalid module section name')
        if table[section[0]:end] != b'.modinfo':
            continue
        require(section[4] + section[5] <= len(data), 'module modinfo exceeds ELF file')
        values = [x[len(b'vermagic='):].decode('ascii') for x in data[section[4]:section[4] + section[5]].split(b'\0') if x.startswith(b'vermagic=')]
        require(len(values) == 1, 'kernel module vermagic missing or duplicated')
        return values[0]
    raise ImageError('kernel module modinfo section missing')


def check_modules(rootfs, kernel_release=None):
    paths = sorted(path for path in (rootfs / 'lib/modules').rglob('*') if path.is_file() and '.ko' in path.name)
    require(paths, 'no kernel modules found in the supplied rootfs')
    versions, releases = set(), set()
    for path in paths:
        data = path.read_bytes()
        if path.name.endswith('.gz'):
            data = gzip.decompress(data)
        elif path.name.endswith('.xz'):
            data = lzma.decompress(data)
        else:
            require(path.name.endswith('.ko'), 'unsupported module compression: ' + path.name)
        versions.add(elf_vermagic(data)); releases.add(path.relative_to(rootfs / 'lib/modules').parts[0])
    require(len(versions) == 1 and len(releases) == 1, 'assembled rootfs mixes kernel module releases or vermagic')
    release = next(iter(releases)); vermagic = next(iter(versions))
    require(vermagic.split()[0] == release and (kernel_release is None or release == kernel_release), 'module vermagic disagrees with the kernel release')
    return {'modules_checked': len(paths), 'kernel_release': release, 'vermagic': vermagic,
            'scope': 'supplied assembled rootfs ELF modules; kernel ABI hash and FIT-rootfs association are not checked'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--layout', '--profile', dest='layout', choices=['legacy', 'ubi'], required=True)
    parser.add_argument('--allow-legacy-ubi-alias', action='store_true', help='historical legacy C7 audit only; new releases must omit the UBI alias')
    parser.add_argument('--rootfs', type=Path, help='optional assembled rootfs for ELF module consistency checks')
    parser.add_argument('--kernel-release')
    parser.add_argument('--fwtool', type=Path, help='optional executable for independent read-only metadata extraction')
    parser.add_argument('--fit-check-sign', type=Path, help='optional executable for independent FIT checks')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--rootfs-image-output', type=Path, help='write the verified unique selected rootfs payload to a new private file')
    args = parser.parse_args()
    if args.output:
        require(args.output.resolve() != args.image.resolve() and not (args.output.exists() and args.output.samefile(args.image)), 'report output must not overwrite the input image')
        require(args.rootfs_image_output is None or args.output.resolve() != args.rootfs_image_output.resolve(), 'report and rootfs payload outputs must differ')
    sink = {}
    result = check_image(args.image.read_bytes(), args.layout, args.allow_legacy_ubi_alias, sink)
    if args.rootfs:
        result['kernel_modules'] = check_modules(args.rootfs, args.kernel_release)
    if args.fwtool:
        probe = subprocess.check_output([str(args.fwtool.resolve()), '-q', '-i', '-', str(args.image.resolve())], timeout=30)
        require(json.loads(probe) == result['metadata'], 'fwtool extraction differs from the built-in parser')
        result['external_fwtool'] = 'passed'
    if args.fit_check_sign:
        subprocess.run([str(args.fit_check_sign.resolve()), '-f', str(args.image.resolve())], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        result['external_fit_check_sign'] = 'passed'
    if args.rootfs_image_output:
        descriptor = os.open(args.rootfs_image_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(sink['payload'])
        result['rootfs_image_output'] = str(args.rootfs_image_output)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    try:
        main()
    except (ImageError, OSError, UnicodeError, struct.error, json.JSONDecodeError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print('AP image check failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
