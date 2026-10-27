#!/usr/bin/env python3
"""Render and transact a DictTech AP profile; secrets never enter argv or logs."""
from __future__ import annotations
import argparse
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TARGET = '/usr/libexec/dicttech-ap/transaction'
from ap_config import PROFILE, SCHEMA_VERSION
DEFAULTS = HERE.parents[1] / 'package/dicttech/ap-profile/files/usr/share/dicttech-ap/defaults.json'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def out(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def model(args):
    from ap_config import load_document, resolve, render
    resolved = resolve(load_document(Path(args.config)), args.device, Path(args.defaults))
    files = render(resolved, Path(args.defaults))
    if not files or any(not isinstance(k, str) or not isinstance(v, bytes) for k, v in files.items()):
        raise ValueError('renderer returned an invalid file map')
    return resolved, files


def ssh_base(args, host=None):
    if not (host or args.host):
        raise ValueError('--host is required for SSH commands')
    target = host or args.host
    # A host is data, never an ssh option. Permit hostnames, literal IPv4/IPv6.
    if not re.fullmatch(r'[A-Za-z0-9_.:\[\]-]+', target) or target.startswith('-'):
        raise ValueError('invalid SSH host')
    result = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ServerAliveInterval=20',
              '-o', 'ServerAliveCountMax=3', '-p', str(args.port)]
    if args.control_socket:
        result += ['-S', args.control_socket]
    if args.jump_host:
        if not re.fullmatch(r'[A-Za-z0-9_@.:[\],-]+', args.jump_host) or args.jump_host.startswith('-'):
            raise ValueError('invalid jump host')
        result += ['-J', args.jump_host]
    return result + ['root@' + target]


def remote(args, command, token=None, data=None, host=None, timeout=45):
    if command not in {'check', 'stage', 'apply', 'status', 'confirm', 'rollback'}:
        raise ValueError('invalid transaction command')
    words = [TARGET, command]
    if token is not None:
        if not re.fullmatch('[0-9a-f]{32}', token):
            raise ValueError('invalid transaction token')
        words.append(token)
    proc = subprocess.run(ssh_base(args, host) + [shlex.join(words)], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode:
        # Remote errors contain only fixed transaction reasons; never echo generic SSH output.
        reason = proc.stderr.decode(errors='replace').strip()
        safe = re.findall(r'dicttech-ap: ([a-z_]+)', reason)
        raise RuntimeError('remote transaction failed' + (': ' + safe[-1] if safe else ''))
    try:
        return json.loads(proc.stdout)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError('invalid remote JSON response') from None


HOST_IDENTITY_FILES = frozenset({
    'etc/dropbear/dropbear_ed25519_host_key', 'etc/dropbear/dropbear_rsa_host_key',
    'etc/dropbear/dropbear_ecdsa_host_key', 'etc/uhttpd.key', 'etc/uhttpd.crt',
})
LEGACY_PROBE = r"""set -eu
[ "$(id -u)" = 0 ]
board=$(cat /tmp/sysinfo/board_name)
boot=$(cat /proc/sys/kernel/random/boot_id)
mac=$(cat /sys/class/net/br-lan/address)
device_id=
if [ -e /etc/dicttech-ap/device.json ] || [ -L /etc/dicttech-ap/device.json ]; then
 [ -f /etc/dicttech-ap/device.json ] && [ ! -L /etc/dicttech-ap/device.json ]
 device_id=$(jq -er '.device_id|select(type=="string" and length>=1 and length<=48)' /etc/dicttech-ap/device.json)
fi
printf '%s\n' "$board" "$boot" "$mac" "$device_id"
"""


def legacy_read(args, script, timeout=30):
    proc = subprocess.run(ssh_base(args) + ['sh -s'], input=script.encode(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode:
        raise RuntimeError('same-device host identity capture failed')
    return proc.stdout


def preserve_host_identity(args, resolved, files):
    """Read identity only from an existing, explicitly selected same-device AP."""
    lines = legacy_read(args, LEGACY_PROBE).decode('ascii').splitlines()
    if len(lines) != 4:
        raise RuntimeError('invalid legacy identity probe')
    board, boot, mac, device_id = lines
    if (board not in resolved['platform']['board_names'] or
            mac != resolved['device']['identity']['bridge'] or
            (device_id and device_id != args.device) or
            not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot) or
            not re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', mac) or
            (device_id and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,47}', device_id))):
        raise RuntimeError('host identity source differs from the selected device')
    allowed = set(HOST_IDENTITY_FILES)
    if 'etc/dropbear/authorized_keys' not in files:
        allowed.add('etc/dropbear/authorized_keys')
    # Every inserted value has passed the strict token checks above; file names
    # come exclusively from this fixed allowlist, never from JSON or shell text.
    guard = ('[ "$(cat /proc/sys/kernel/random/boot_id)" = ' + shlex.quote(boot) + ' ]\n'
             '[ "$(cat /sys/class/net/br-lan/address)" = ' + shlex.quote(mac) + ' ]\n')
    script = ('set -eu\nset -o pipefail\n' + guard + 'set --\n'
              'for path in ' + ' '.join(sorted(allowed)) + '; do\n'
              ' if [ -e "/$path" ] || [ -L "/$path" ]; then\n'
              '  [ -f "/$path" ] && [ ! -L "/$path" ]\n'
              '  [ "$(wc -c < "/$path")" -le 131072 ]\n'
              '  set -- "$@" "$path"\n'
              ' fi\ndone\n[ "$#" -gt 0 ]\n'
              'tar -C / -cf - "$@" | head -c 2097153\n' + guard)
    captured = legacy_read(args, script)
    if len(captured) > 2097152:
        raise RuntimeError('host identity archive exceeds the size limit')
    preserved = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(captured), mode='r:') as tar:
            for member in tar:
                if (member.name not in allowed or member.name in preserved or
                        not member.isfile() or member.size > 131072 or
                        (member.size == 0 and member.name != 'etc/dropbear/authorized_keys')):
                    raise ValueError('invalid captured member')
                stream = tar.extractfile(member)
                value = stream.read(131073) if stream else None
                if value is None or len(value) != member.size:
                    raise ValueError('invalid captured size')
                preserved[member.name] = value
    except (tarfile.TarError, ValueError, OSError):
        raise RuntimeError('invalid same-device host identity archive') from None
    if not any(name.startswith('etc/dropbear/dropbear_') for name in preserved):
        raise RuntimeError('source has no supported Dropbear host identity')
    return {**files, **preserved}, sorted(preserved)


def tar_bytes(files: dict[str, bytes], manifest=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        contents = {'manifest.json': json.dumps(manifest, separators=(',', ':')).encode() + b'\n'} if manifest is not None else {}
        contents.update({'payload/' + str(i): value for i, value in enumerate(files.values())} if manifest is not None else files)
        for path, data in contents.items():
            if path.startswith('/') or '..' in Path(path).parts:
                raise ValueError('unsafe archive path')
            info = tarfile.TarInfo(path)
            info.uid = info.gid = 0
            info.mode = 0o600
            info.mtime = 0
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def bundle(files, contract, device_id, ttl, approved_target=None):
    if contract.get('profile') != PROFILE or contract.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('incompatible target firmware profile')
    manifest = {
        'schema_version': SCHEMA_VERSION, 'profile': PROFILE, 'device_id': device_id,
        'token': secrets.token_hex(16), 'boot_id': contract['boot_id'],
        'defaults_sha256': contract['defaults_sha256'], 'ttl_seconds': ttl,
        'approved_target': approved_target or {'device_id': device_id, 'bridge_mac': contract['identity'].get('bridge_mac', ''), 'allowed_boards': [contract['identity'].get('board_name', '')], 'initialize': False},
        'baseline': {key: contract[key] for key in ('files', 'modes', 'shadow_sha256', 'shadow_security', 'resolver_state', 'identity')},
        'changed_paths': [path for path, data in files.items() if contract['files'].get(path) != digest(data) or contract['modes'].get(path) != ['-rw-------', '0'] or (path == 'etc/dicttech-ap/root-password-hash' and not contract.get('root_password_synced'))] + ([] if contract.get('resolver_correct') else ['etc/resolv.conf']),
        'files': [{'path': path, 'sha256': digest(data), 'size': len(data),
                   'mode': '600', 'payload': 'payload/' + str(i)}
                  for i, (path, data) in enumerate(files.items())],
    }
    return manifest, tar_bytes(files, manifest)


def public_status(status):
    # Tokens are local transaction capabilities; keep them in the private receipt only.
    return {key: value for key, value in status.items() if key != 'token'}


def bootstrap_access(identity):
    """Describe setup entry points without treating a DHCP lease as identity."""
    if not (identity.get('bootstrap_isolated') or identity.get('bootstrap_dhcp')):
        return None
    return {'mode': 'dhcp' if identity.get('bootstrap_dhcp') else 'isolated',
            'offline': {'port': 'lan4', 'address': '192.168.250.1', 'ssh_port': 12022, 'http_port': 80},
            'dhcp': {'ready': identity.get('bootstrap_dhcp_ready') is True,
                     'ssh_port': 12022, 'public_key_only': True}}


def receipt_path(args):
    if not args.receipt:
        raise ValueError('--receipt is required for mutating commands')
    return Path(args.receipt)


def save_receipt(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('receipt must not be a symbolic link')
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, separators=(',', ':'))
            f.write('\n')
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_receipt(args):
    path = receipt_path(args)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('receipt must be a private regular file (0600)')
    value = json.loads(path.read_text())
    if value.get('profile') != PROFILE:
        raise ValueError('invalid receipt profile')
    return path, value


def reconnect(args, addresses):
    error = None
    for _ in range(args.reconnect_seconds // 3 + 1):
        for host in dict.fromkeys(x for x in addresses if x):
            try:
                return host, remote(args, 'status', host=host, timeout=8)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                error = exc
        time.sleep(3)
    raise RuntimeError('could not reconnect; automatic rollback remains armed') from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'export', 'apply', 'check', 'status', 'confirm', 'rollback'])
    parser.add_argument('--config')
    parser.add_argument('--device')
    parser.add_argument('--defaults', default=str(DEFAULTS))
    parser.add_argument('--host')
    parser.add_argument('--port', type=int, default=12022)
    parser.add_argument('--control-socket')
    parser.add_argument('--jump-host')
    parser.add_argument('--receipt')
    parser.add_argument('--initialize', action='store_true', help='allow first provisioning after identifying an unprovisioned setup device')
    parser.add_argument('--expect-boot-id', help='required boot identity pin for first provisioning')
    parser.add_argument('--output')
    parser.add_argument('--preserve-host-identity', action='store_true', help='read same-device SSH/HTTPS identity for an existing-device upgrade export')
    parser.add_argument('--ttl', type=int, default=600)
    parser.add_argument('--reconnect-seconds', type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 120 <= args.ttl <= 3600 or not 0 <= args.reconnect_seconds <= 600:
        parser.error('port, TTL or reconnect limit is out of range')
    if args.preserve_host_identity and (args.command != 'export' or not args.host):
        parser.error('--preserve-host-identity requires export and --host')
    if args.command in {'plan', 'export', 'apply'}:
        if not args.config or not args.device:
            parser.error('--config and --device are required')
        resolved, files = model(args)
        if args.command == 'export':
            if not args.output:
                parser.error('--output is required')
            dest = Path(args.output)
            if dest.exists() or dest.is_symlink():
                raise ValueError('output already exists')
            preserved_paths = []
            if args.preserve_host_identity:
                files, preserved_paths = preserve_host_identity(args, resolved, files)
            payload = gzip.compress(tar_bytes(files), mtime=0)
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(payload)
            out({'state': 'exported', 'device_id': args.device, 'output': str(dest),
                 'secret_material': True, 'paths': list(files), 'preserved_identity_paths': preserved_paths, 'format': 'private-sysupgrade-tar.gz'})
            return
        contract = remote(args, 'check') if args.host else None
        if contract is not None:
            identity = contract.get('identity', {})
            if identity.get('device_id') and identity['device_id'] != args.device:
                raise RuntimeError('target device identity differs from the selected device')
            if identity.get('board_name') not in resolved['platform']['board_names']:
                raise RuntimeError('target board is not allowed by the selected profile')
            desired_mac = resolved['device']['identity']['bridge']
            if identity.get('bridge_mac') != desired_mac:
                if not (args.initialize and not identity.get('device_id')
                        and (identity.get('bootstrap_isolated') is True or identity.get('bootstrap_dhcp') is True)
                        and args.expect_boot_id == contract.get('boot_id')):
                    raise RuntimeError('target bridge MAC differs; first provisioning requires an identified bootstrap device and pinned boot ID')
            if args.command == 'apply' and identity.get('ssh_port') != str(resolved['common']['ssh_port']):
                raise RuntimeError('live SSH port changes are unsupported; use offline provisioning')
        if contract is not None and contract.get('defaults_sha256') != resolved['defaults_sha256']:
            raise RuntimeError('target firmware defaults differ from the selected local defaults')
        changed = {path: data for path, data in files.items()
                   if contract is None or contract.get('files', {}).get(path) != digest(data)
                   or contract.get('modes', {}).get(path) != ['-rw-------', '0']
                   or (path == 'etc/dicttech-ap/root-password-hash' and not contract.get('root_password_synced'))}
        if args.command == 'plan':
            result = {'device_id': args.device, 'profile': PROFILE,
                 'comparison': 'remote' if contract else 'not-connected',
                 'changed_paths': list(changed) + ([] if contract is None or contract.get('resolver_correct') else ['etc/resolv.conf'])}
            access = bootstrap_access(contract.get('identity', {})) if contract else None
            if access is not None:
                result['bootstrap_access'] = access
            out(result)
            return
        if contract is None:
            parser.error('--host is required for apply')
        if not changed and contract.get('resolver_correct'):
            out({'state': 'noop', 'device_id': args.device, 'changed_paths': []})
            return
        path = receipt_path(args)
        if path.exists() or path.is_symlink():
            raise RuntimeError('receipt already exists; choose a new private receipt path')
        manifest, payload = bundle(files, contract, args.device, args.ttl, {'device_id': args.device, 'bridge_mac': resolved['device']['identity']['bridge'], 'allowed_boards': resolved['platform']['board_names'], 'initialize': args.initialize, 'expect_boot_id': args.expect_boot_id})
        receipt = {'profile': PROFILE, 'device_id': args.device, 'token': manifest['token'],
                   'boot_id': contract['boot_id'], 'host': args.host,
                   'maintenance_host': resolved['device']['maintenance_ipv4'].split('/')[0],
                   'changed_paths': manifest['changed_paths'], 'state': 'prepared'}
        access = bootstrap_access(contract.get('identity', {}))
        if access is not None:
            receipt['bootstrap_access'] = access
        save_receipt(path, receipt)
        staged = remote(args, 'stage', data=payload)
        if staged.get('token') != manifest['token']:
            raise RuntimeError('staged transaction identity mismatch')
        receipt['state'] = 'staged'; save_receipt(path, receipt)
        try:
            applied = remote(args, 'apply', manifest['token'])
        except (RuntimeError, subprocess.TimeoutExpired):
            host, applied = reconnect(args, [args.host, receipt['maintenance_host']])
            receipt['host'] = host
        if applied.get('token') != manifest['token'] or applied.get('state') not in {'applied', 'confirmed'}:
            raise RuntimeError('apply readback is not the expected transaction; rollback remains armed')
        receipt['state'] = applied['state']; save_receipt(path, receipt)
        out(public_status(applied))
        return
    if args.command in {'check', 'status'}:
        result = remote(args, args.command)
        # check exposes only the contract, not per-file digests of secret configuration.
        if args.command == 'check':
            for key in ('files', 'modes', 'shadow_sha256', 'shadow_security'):
                result.pop(key, None)
            access = bootstrap_access(result.get('identity', {}))
            if access is not None:
                result['bootstrap_access'] = access
        out(public_status(result))
        return
    path, receipt = load_receipt(args)
    host = args.host or receipt['host']
    result = remote(args, args.command, receipt['token'], host=host)
    if result.get('token') != receipt['token']:
        raise RuntimeError('transaction identity mismatch')
    receipt['state'] = result['state']; save_receipt(path, receipt)
    out(public_status(result))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        # Do not print exception data from parser/renderer/subprocess: it may carry secrets.
        allowed = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('ap-deploy: ' + allowed, file=sys.stderr)
        sys.exit(1)
