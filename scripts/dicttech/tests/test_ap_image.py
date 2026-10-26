#!/usr/bin/env python3
"""Synthetic, public fixtures for the final FIT/partition compatibility gate."""
import copy
import contextlib
import io
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

SOURCE = Path(__file__).resolve().parents[1] / 'check-ap-image.py'
spec = importlib.util.spec_from_file_location('check_ap_image', SOURCE)
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)


def cell(*values):
    return struct.pack('>' + 'I' * len(values), *values)


def text(value):
    return value.encode() + b'\0'


def tree(nodes):
    names, offsets = bytearray(), {}
    for props in nodes.values():
        for key in props:
            if key not in offsets:
                offsets[key] = len(names); names.extend(text(key))
    paths = set(nodes)
    for path in tuple(paths):
        while path != '/':
            path = path.rsplit('/', 1)[0] or '/'; paths.add(path)
    def encode(path):
        name = '' if path == '/' else path.rsplit('/', 1)[1]
        result = bytearray(cell(1) + text(name))
        result.extend(b'\0' * (-len(result) % 4))
        for key, value in nodes.get(path, {}).items():
            result.extend(cell(3, len(value), offsets[key]) + value)
            result.extend(b'\0' * (-len(result) % 4))
        prefix = path.rstrip('/') + '/'
        for child in sorted(p for p in paths if p.startswith(prefix) and p != path and '/' not in p[len(prefix):]):
            result.extend(encode(child))
        result.extend(cell(2)); return result
    body = encode('/') + cell(9)
    start = 56; strings = start + len(body); total = strings + len(names)
    return cell(0xd00dfeed, total, start, strings, 40, 17, 16, 0, len(names), len(body)) + b'\0' * 16 + body + names


def device(layout='legacy', old_alias=False):
    board = 'gemtek,xr1710g' + ('-ubi' if layout == 'ubi' else '')
    compatible = text(board) + (text('gemtek,xr1710g-ubi') if old_alias else b'') + text('airoha,an7581')
    nodes = {'/': {'compatible': compatible}, '/chosen': {'rootdisk': cell(55)},
             '/soc/nand/partitions': {'compatible': text('fixed-partitions'), '#address-cells': cell(1), '#size-cells': cell(1)}}
    for name, offset, size, ro in check.PARTITIONS[layout]:
        path = '/soc/nand/partitions/' + name + '@' + format(offset, 'x')
        nodes[path] = {'label': text(name), 'reg': cell(offset, size)}
        if ro:
            nodes[path]['read-only'] = b''
        if name == 'ubi':
            ubi = path
    nodes[ubi + '/volumes/fit'] = {'volname': text('fit'), 'phandle': cell(55)}
    factory = ubi + '/volumes/factory'; nodes[factory] = {'volname': text('factory')}
    offsets = [(0, 0x1e00), (0x5000, 6), (0x6000, 6)] if layout == 'legacy' else [(0x5000, 0x1e00), (0x6c, 17), (0x86, 17)]
    for index, (offset, size) in enumerate(offsets):
        nodes[factory + '/nvmem-layout/cell' + str(index)] = {'reg': cell(offset, size)}
    return nodes


def metadata(layout='legacy'):
    board = 'gemtek,xr1710g' + ('-ubi' if layout == 'ubi' else '')
    result = {'metadata_version': '1.1', 'compat_version': '1.0' if layout == 'legacy' else '2.0',
              'supported_devices': [board] if layout == 'legacy' else ['requires layout compatibility'],
              'version': {'target': 'airoha/an7581', 'board': 'gemtek_xr1710g' + ('-ubi' if layout == 'ubi' else '')}}
    if layout == 'ubi':
        result['new_supported_devices'] = [board]
    return result


def fit(nodes, meta, bad_hash=False):
    payloads = {'kernel-1': ('kernel', b'public synthetic kernel'), 'rootfs-1': ('filesystem', b'public synthetic rootfs'), 'fdt-1': ('flat_dt', tree(nodes))}
    outer = {'/configurations': {'default': text('config-1')}, '/configurations/config-1': {'fdt': text('fdt-1'), 'kernel': text('kernel-1'), 'loadables': text('rootfs-1')}}
    for name, (kind, data) in payloads.items():
        path = '/images/' + name
        outer[path] = {'type': text(kind), 'data': data}
        outer[path + '/hash-1'] = {'algo': text('sha256'), 'value': hashlib.sha256(data).digest() if not (bad_hash and name == 'kernel-1') else b'\0' * 32}
    raw = tree(outer); body = b'\0' * 8 + json.dumps(meta).encode()
    prefix = raw + body
    return prefix + struct.pack('>IIB3xI', 0x46577830, zlib.crc32(prefix) ^ 0xffffffff, 1, len(body) + 16)


def module(vermagic='6.18.55 SMP mod_unload aarch64', machine=183):
    names = b'\0.shstrtab\0.modinfo\0'
    modinfo = b'license=GPL\0vermagic=' + vermagic.encode() + b'\0'
    table_offset = (64 + len(names) + len(modinfo) + 7) & ~7
    ident = b'\x7fELF' + bytes([2, 1, 1]) + b'\0' * 9
    header = ident + struct.pack('<HHIQQQIHHHHHH', 1, machine, 1, 0, 0, table_offset, 0, 64, 0, 0, 64, 3, 1)
    data = header + names + modinfo
    data += b'\0' * (table_offset - len(data))
    data += b'\0' * 64
    data += struct.pack('<IIQQQQIIQQ', 1, 3, 0, 0, 64, len(names), 0, 0, 1, 0)
    data += struct.pack('<IIQQQQIIQQ', 11, 1, 0, 0, 64 + len(names), len(modinfo), 0, 0, 1, 0)
    return data


class ImageChecks(unittest.TestCase):
    def test_selected_verified_rootfs_export_is_unique_private_and_exclusive(self):
        image = fit(device(), metadata()); sink = {}
        report = check.check_image(image, 'legacy', rootfs_sink=sink)
        self.assertEqual(sink['payload'], b'public synthetic rootfs')
        self.assertEqual(report['selected_rootfs']['sha256'], hashlib.sha256(sink['payload']).hexdigest())
        with tempfile.TemporaryDirectory(prefix='dicttech-rootfs-export-') as temp:
            source = Path(temp) / 'image.itb'; destination = Path(temp) / 'root.squashfs'; source.write_bytes(image)
            from unittest.mock import patch
            argv = ['check-ap-image.py', '--layout', 'legacy', '--image', str(source), '--rootfs-image-output', str(destination)]
            with patch.object(check.sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                check.main()
            self.assertEqual(destination.read_bytes(), sink['payload'])
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with patch.object(check.sys, 'argv', argv):
                with self.assertRaises(FileExistsError):
                    check.main()
            destination.unlink(); source.write_bytes(fit(device(), metadata(), bad_hash=True))
            with patch.object(check.sys, 'argv', argv):
                with self.assertRaises(check.ImageError):
                    check.main()
            self.assertFalse(destination.exists())

    def test_module_vermagic_consistency_and_aarch64_architecture(self):
        with tempfile.TemporaryDirectory(prefix='dicttech-kmod-fixture-') as temp:
            root = Path(temp); modules = root / 'lib/modules/6.18.55'; modules.mkdir(parents=True)
            (modules / 'first.ko').write_bytes(module())
            (modules / 'second.ko').write_bytes(module())
            self.assertEqual(check.check_modules(root, '6.18.55')['modules_checked'], 2)
            (modules / 'second.ko').write_bytes(module('6.18.44 SMP mod_unload aarch64'))
            with self.assertRaisesRegex(check.ImageError, 'mixes'):
                check.check_modules(root)
            (modules / 'second.ko').write_bytes(module(machine=62))
            with self.assertRaisesRegex(check.ImageError, 'not AArch64'):
                check.check_modules(root)

    def test_legacy_and_ubi_are_separate_valid_profiles(self):
        for layout in ('legacy', 'ubi'):
            report = check.check_image(fit(device(layout), metadata(layout)), layout)
            self.assertTrue(report['passed']); self.assertEqual(report['factory_cell_format'], layout)

    def test_wrong_layout_metadata_is_rejected(self):
        with self.assertRaisesRegex(check.ImageError, 'compatibility'):
            check.check_image(fit(device('legacy'), metadata('ubi')), 'legacy')

    def test_merged_writable_vendor_is_rejected(self):
        nodes = device()
        for path in tuple(nodes):
            if path in ('/soc/nand/partitions/bootloader@0', '/soc/nand/partitions/uenv@200000', '/soc/nand/partitions/dsd@400000'):
                del nodes[path]
        nodes['/soc/nand/partitions/vendor@0'] = {'label': text('vendor'), 'reg': cell(0, 0x600000)}
        with self.assertRaisesRegex(check.ImageError, 'NAND partitions'):
            check.check_image(fit(nodes, metadata()), 'legacy')

    def test_partition_geometry_and_bootloader_write_protection_are_checked(self):
        for mutate in ('geometry', 'writeable'):
            nodes = device()
            if mutate == 'geometry':
                nodes['/soc/nand/partitions/ubi@700000']['reg'] = cell(0x20000, 0)
            else:
                del nodes['/soc/nand/partitions/bootloader@0']['read-only']
            with self.assertRaisesRegex(check.ImageError, 'NAND partitions'):
                check.check_image(fit(nodes, metadata()), 'legacy')

    def test_legacy_ubi_alias_requires_explicit_historical_override(self):
        image = fit(device(old_alias=True), metadata())
        with self.assertRaisesRegex(check.ImageError, 'aliases'):
            check.check_image(image, 'legacy')
        self.assertTrue(check.check_image(image, 'legacy', True)['passed'])

    def test_rootdisk_and_calibration_format_are_checked(self):
        nodes = device(); nodes['/chosen']['rootdisk'] = cell(99)
        with self.assertRaisesRegex(check.ImageError, 'rootdisk'):
            check.check_image(fit(nodes, metadata()), 'legacy')
        nodes = device(); path = next(p for p in nodes if p.endswith('/cell0'))
        nodes[path]['reg'] = cell(0x5000, 0x1e00)
        with self.assertRaisesRegex(check.ImageError, 'EEPROM'):
            check.check_image(fit(nodes, metadata()), 'legacy')

    def test_whole_image_crc_and_individual_fit_hashes_are_checked(self):
        image = bytearray(fit(device(), metadata())); image[55] ^= 1
        with self.assertRaisesRegex(check.ImageError, 'CRC'):
            check.check_image(image, 'legacy')
        with self.assertRaisesRegex(check.ImageError, 'FIT image hash mismatch'):
            check.check_image(fit(device(), metadata(), bad_hash=True), 'legacy')


if __name__ == '__main__':
    unittest.main(verbosity=2)
