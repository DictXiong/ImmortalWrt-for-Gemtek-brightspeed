#!/usr/bin/env python3
"""Read-only AP acceptance. Distinguish configuration checks from client tests."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ap_config import DEFAULTS, IFACES, ROAM_ROLES, ROLES, canonical, load_document, render, resolve, write_private
spec = importlib.util.spec_from_file_location('dicttech_ap_deploy', HERE / 'ap-deploy.py')
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
EXTERNAL = ('wifi_client_auth', 'ipv4_ipv6_internet', 'guest_client_mutual',
            'guest_internal_isolation', 'ppe_bound_isolation_and_vlan',
            'spoof_and_vlan_injection', 'actual_client_roaming',
            'cold_boot_and_link_recovery', 'cpu_and_throughput')


def read_command(args, words, parse=True):
    q = subprocess.run(deploy.ssh_base(args) + [shlex.join(words)], capture_output=True, timeout=20)
    if q.returncode:
        raise RuntimeError('read-only command unavailable')
    return json.loads(q.stdout) if parse else q.stdout.decode('utf-8')


def collect(args, state):
    data, errors = {}, {}
    queries = {
        'contract': ['check'],
        **{name: ['ubus', '-t', '5', 'call', 'network.interface.' + interface, 'status']
           for name, interface in [('lan', 'lan'), ('lan6', 'lan6'), ('maintenance', 'ap_maintenance'), ('guest', 'guest')]},
        'guest_addresses': ['ip', '-j', 'address', 'show', 'dev', 'br-lan.192'],
        'vlans': ['bridge', '-j', 'vlan', 'show'],
        'bridge_guard': ['nft', '-j', 'list', 'table', 'bridge', 'xr_guest_guard'],
        'inet_guard': ['nft', '-j', 'list', 'table', 'inet', 'xr_guest_local'],
        'fw4': ['nft', '-j', 'list', 'table', 'inet', 'fw4'],
        'sysctls': ['sysctl', '-n', 'net.bridge.bridge-nf-call-iptables', 'net.bridge.bridge-nf-call-ip6tables',
                    'net.bridge.bridge-nf-filter-vlan-tagged', 'net.bridge.bridge-nf-pass-vlan-input-dev',
                    'net.bridge.bridge-nf-call-arptables', 'net.bridge.bridge-nf-filter-pppoe-tagged'],
        'service_links': ['ls', '-1', '/etc/rc.d'],
        # Filter on the AP: daemon argv can contain secrets and is never returned.
        'services': ['sh', '-c', "ubus -t 5 call service list | jq 'map_values({instances:(.instances // {} | map_values({running:.running}))})'"],
        # Deliberate field allowlist excludes passphrases, PSKs, SAE passwords.
        'security': ['sh', '-c', "for f in /var/run/hostapd-phy*.conf; do [ -f \"$f\" ] || continue; awk '/^(interface|bss|ssid|ssid2|bssid|wpa_key_mgmt|ieee80211w|rrm_neighbor_report|rrm_beacon_report|bss_transition|ap_isolate|ignore_broadcast_ssid)=/ { print }' \"$f\"; done"],
    }
    for role in ROLES:
        queries['hostapd_' + role] = ['ubus', '-t', '5', 'call', 'hostapd.' + IFACES[role], 'get_status']
        queries['roaming_' + role] = ['/usr/lib/xr-roaming/helper.sh', 'status', ROAM_ROLES[role]]
    links = 3 if state['common']['ssids']['main']['six_ghz'] else 2
    for index in range(links):
        queries['radio_' + str(index)] = ['hostapd_cli', '-p', '/var/run/hostapd', '-i', 'ap-mld1_link' + str(index), 'status']
    text = {'sysctls', 'service_links', 'security', *('radio_' + str(i) for i in range(links))}
    for name, words in queries.items():
        try:
            data[name] = deploy.remote(args, 'check') if name == 'contract' else read_command(args, words, name not in text)
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
            errors[name] = 'unavailable or malformed read-only response'
    # Sequential reads are not an atomic AP snapshot. Reject observed changes
    # to the boot or full private contract while collecting live evidence.
    try:
        final_contract = deploy.remote(args, 'check')
        initial = data.get('contract', {})
        data['snapshot_stable'] = bool(initial.get('boot_id')) and (
            hashlib.sha256(canonical(initial)).digest() ==
            hashlib.sha256(canonical(final_contract)).digest())
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
        data['snapshot_stable'] = False
    if not data['snapshot_stable']:
        errors['snapshot'] = 'boot or full contract changed/unavailable during collection'
    data['errors'] = errors
    return data


def security_blocks(value):
    blocks, current = [], {}
    for line in value.splitlines():
        key, sep, item = line.partition('=')
        if not sep:
            continue
        if key in ('interface', 'bss') and current:
            blocks.append(current); current = {}
        current[key] = item
    if current:
        blocks.append(current)
    for block in blocks:
        if 'ssid2' in block:
            try:
                block['ssid'] = bytes.fromhex(block['ssid2']).decode()
            except (ValueError, UnicodeError):
                block['ssid'] = None
    return blocks


def matches(expr, protocol, field, right, op='=='):
    match = expr.get('match', {}) if isinstance(expr, dict) else {}
    return match.get('op') == op and match.get('left') == {'payload': {'protocol': protocol, 'field': field}} and match.get('right') == right


def guard_valid(value, family):
    rows = value.get('nftables', [])
    table = 'xr_guest_guard' if family == 'bridge' else 'xr_guest_local'
    if not any(row.get('table', {}).get('family') == family and row.get('table', {}).get('name') == table for row in rows):
        return False
    chains = [row['chain'] for row in rows if 'chain' in row]
    hooks = {c.get('name') for c in chains if isinstance(c.get('name'), str) and c['name'] and c.get('family') == family and c.get('table') == table and c.get('type') == 'filter' and c.get('hook') == 'input' and c.get('prio') == -10 and c.get('policy') == 'accept'}
    if not hooks:
        return False
    for row in rows:
        rule = row.get('rule', {})
        if rule.get('family') != family or rule.get('table') != table or rule.get('chain') not in hooks:
            continue
        expr = rule.get('expr', [])
        if not any(isinstance(item, dict) and 'drop' in item for item in expr):
            continue
        if family == 'bridge':
            vid = any(matches(item, 'vlan', 'id', 192) for item in expr)
            eapol = any(matches(item, 'vlan', 'type', kind, '!=') for item in expr for kind in (34958, '0x888e', '8021x', 'eapol'))
            if vid and eapol:
                return True
        elif any(item.get('match', {}).get('left') == {'meta': {'key': 'iifname'}} and item.get('match', {}).get('right') == 'br-lan.192' and item.get('match', {}).get('op') == '==' for item in expr if isinstance(item, dict)):
            return True
    return False


def evaluate(state, files, data, external=None):
    tests = []
    def test(name, okay, detail):
        tests.append({'id': name, 'status': 'PASS' if okay else 'FAIL', 'detail': detail})
    contract = data.get('contract', {})
    test('snapshot_stability', data.get('snapshot_stable') is True, 'Boot ID and full contract unchanged across read-only collection')
    expected = {path: hashlib.sha256(value).hexdigest() for path, value in files.items()}
    mismatch = [path for path, value in expected.items() if contract.get('files', {}).get(path) != value or contract.get('modes', {}).get(path) != ['-rw-------', '0']]
    test('desired_configuration', not mismatch and bool(contract) and contract.get('root_password_synced') is True and contract.get('resolver_correct') is True,
         'Desired file hashes/private modes, root hash and resolver match' if not mismatch else 'Changed/missing paths: ' + ', '.join(mismatch))
    identity = contract.get('identity', {})
    test('target_identity', contract.get('profile') == state['profile'] and contract.get('defaults_sha256') == state['defaults_sha256'] and identity.get('device_id') == state['device_id'] and identity.get('bridge_mac') == state['device']['identity']['bridge'] and identity.get('board_name') in state['platform']['board_names'], 'Profile, defaults, registered device, bridge identity and board')
    for name, proto, device in [('lan', 'dhcp', 'br-lan.1'), ('lan6', 'dhcpv6', 'br-lan.1'), ('maintenance', 'static', 'br-lan.1'), ('guest', 'none', 'br-lan.192')]:
        item = data.get(name, {})
        okay = item.get('up') is True and item.get('proto') == proto and item.get('l3_device') == device
        if name == 'lan':
            okay = okay and bool(item.get('ipv4-address'))
        elif name == 'lan6':
            okay = okay and bool(item.get('ipv6-address'))
        elif name == 'maintenance':
            okay = okay and any(a.get('address') == state['device']['maintenance_ipv4'] and a.get('mask') == 24 for a in item.get('ipv4-address', []))
        else:
            okay = okay and not item.get('ipv4-address') and not item.get('ipv6-address') and bool(data.get('guest_addresses')) and not any(a.get('addr_info') for a in data.get('guest_addresses', []))
        test('interface_' + name, okay, 'Expected protocol/device/address policy')
    members = {row.get('ifname'): row.get('vlans', []) for row in data.get('vlans', [])}
    for port in state['platform']['ports']:
        values = members.get(port, [])
        native = next((row for row in values if row.get('vlan') == 1), {})
        guest = next((row for row in values if row.get('vlan') == 192), {})
        flags = native.get('flags', [])
        test('vlan_' + port, {row.get('vlan') for row in values} == {1, 192} and bool(native) and bool(guest) and 'PVID' in flags and 'Egress Untagged' in flags and not guest.get('flags'), 'Only native VLAN1 untagged/PVID and guest VLAN192 tagged')
    local_vlans = members.get('br-lan', [])
    test('vlan_bridge_self', {row.get('vlan') for row in local_vlans} == {1, 192} and all(not row.get('flags') for row in local_vlans), 'Bridge-local VLAN1/192 stay tagged for their subinterfaces')
    blocks = security_blocks(data.get('security', ''))
    for role in ROLES:
        status = data.get('hostapd_' + role, {})
        bssids = state['device']['identity']['bssids'][role]
        count = 1 if role == 'iot' else 3 if role == 'main' and state['common']['ssids']['main']['six_ghz'] else 2
        expected_links = {bssids[i]: (2407 if i == 0 else 5000 if i == 1 else 5950) + 5 * state['common']['radios']['radio' + str(i)]['channel'] for i in range(count)}
        observed = status.get('links') if role != 'iot' else [status]
        actual = {item.get('bssid', '').lower(): item.get('freq') for item in observed or []}
        test('wireless_' + role, status.get('status') == 'ENABLED' and status.get('ssid') == state['common']['ssids'][role]['name'] and actual == expected_links, 'Live SSID, BSSIDs and per-link frequencies')
        configs = [b for b in blocks if b.get('ssid') == state['common']['ssids'][role]['name']]
        def secure(block):
            modes = block.get('wpa_key_mgmt', '').split()
            okay = 'SAE' in modes and block.get('ieee80211w') == str(state['security'][role]['pmf'])
            okay = okay and (('WPA-PSK' in modes) if role == 'iot' else not any('PSK' in mode for mode in modes))
            okay = okay and all(block.get(key) == '1' for key in ('rrm_neighbor_report', 'rrm_beacon_report', 'bss_transition'))
            return okay and (role != 'guest' or block.get('ap_isolate', '0') == '0')
        test('security_' + role, len(configs) >= count and all(secure(block) for block in configs), 'Generated hostapd key-management/PMF/k-v policy (secret fields never collected)')
        roaming = data.get('roaming_' + role, {})
        peers = state['peers'][ROAM_ROLES[role]]
        observed_peers = {p.get('id'): p for p in roaming.get('peers', [])}
        okay = roaming.get('state') == 'ok' and roaming.get('snapshot_fresh') is True and set(observed_peers) == {p['id'] for p in peers} and all(p.get('fresh') is True for p in observed_peers.values())
        if not state['common']['roaming']['enabled']:
            okay = roaming.get('state') in ('stopped', None)
        test('roaming_' + role, okay, 'Fresh per-profile/per-peer metadata; no steering claimed')
    for index in range(3 if state['common']['ssids']['main']['six_ghz'] else 2):
        status = dict(line.split('=', 1) for line in data.get('radio_' + str(index), '').splitlines() if '=' in line)
        width_code = status.get('eht_oper_chwidth')
        width = (40 if status.get('secondary_channel') not in (None, '0') else 20) if width_code == '0' else {'1': 80, '2': 160, '4': 320}.get(width_code)
        wanted = int(state['common']['radios']['radio' + str(index)]['htmode'][3:])
        allowed = {20, 40} if index == 0 and wanted == 40 and not state['common']['radios']['radio0']['force_40'] else {wanted}
        test('width_radio' + str(index), status.get('state') == 'ENABLED' and width in allowed, 'Actual width obeys policy; 2.4GHz coexistence may use20MHz')
    test('guest_local_guards', guard_valid(data.get('bridge_guard', {}), 'bridge') and guard_valid(data.get('inet_guard', {}), 'inet'), 'Bridge-local VLAN192 drop with EAPOL exception and inet-local drop')
    test('bridge_sysctls', data.get('sysctls', '').split() == ['1', '1', '0', '0', '0', '0'], 'Pinned bridge-nf policy')
    running = {name: any(row.get('running') is True for row in value.get('instances', {}).values()) for name, value in data.get('services', {}).items()}
    links = data.get('service_links', '').splitlines()
    enabled = lambda service: any(name.startswith('S') and name[1:3].isdigit() and name[3:] == service for name in links)
    core = state['services']['enabled']
    fw4 = any(row.get('table', {}).get('family') == 'inet' and row.get('table', {}).get('name') == 'fw4' for row in data.get('fw4', {}).get('nftables', []))
    test('required_services', all(enabled(name) and (fw4 if name == 'firewall' else running.get(name)) for name in core), 'Required daemons running; oneshot firewall enabled with live fw4 table')
    disabled = state['services']['disabled']
    test('unwanted_services', all(not running.get(name, False) and not enabled(name) for name in disabled), 'Unwanted services neither enabled nor running')
    binding = {'schema_version': 1, 'profile': state['profile'], 'device_id': state['device_id'], 'boot_id': contract.get('boot_id'), 'desired_sha256': hashlib.sha256(canonical(expected)).hexdigest()}
    valid_external = isinstance(external, dict) and all(external.get(k) == v for k, v in binding.items())
    if external is not None:
        test('external_report_binding', valid_external, 'External evidence belongs to this boot/device/desired configuration')
    gates = EXTERNAL + (('six_ghz_client_auth',) if state['common']['ssids']['main']['six_ghz'] else ())
    for name in gates:
        external_tests = external.get('tests', {}) if valid_external else {}
        item = external_tests.get(name, {}) if isinstance(external_tests, dict) else {}
        item = item if isinstance(item, dict) else {}
        status = item.get('status', 'NOT_TESTED')
        if status not in ('PASS', 'FAIL', 'NOT_TESTED') or status == 'PASS' and not isinstance(item.get('evidence'), str) or status == 'PASS' and not item.get('evidence'):
            status = 'FAIL'
        tests.append({'id': name, 'status': status, 'detail': 'External client/hardware evidence required; configuration checks do not prove this behavior'})
    fail = any(t['status'] == 'FAIL' for t in tests)
    missing = any(t['status'] == 'NOT_TESTED' for t in tests)
    return {'schema_version': 1, 'profile': state['profile'], 'device_id': state['device_id'], 'boot_id': contract.get('boot_id'),
            'status': 'FAIL' if fail else 'NOT_TESTED' if missing else 'PASS', 'tests': tests}, binding


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'device', 'host'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--defaults', default=str(DEFAULTS))
    p.add_argument('--port', type=int, default=12022)
    p.add_argument('--control-socket'); p.add_argument('--jump-host')
    p.add_argument('--external-report', type=Path); p.add_argument('--binding-output', type=Path)
    p.add_argument('--output', type=Path); p.add_argument('--require-production', action='store_true')
    args = p.parse_args()
    if not 1 <= args.port <= 65535:
        p.error('invalid SSH port')
    try:
        state = resolve(load_document(args.config), args.device, args.defaults)
        files = render(state, args.defaults)
        external = None
        if args.external_report:
            if args.external_report.stat().st_mode & 0o077:
                raise ValueError('external report must be private')
            external = json.loads(args.external_report.read_text())
        report, binding = evaluate(state, files, collect(args, state), external)
        if args.binding_output:
            write_private(args.binding_output, canonical(binding))
        if args.output:
            write_private(args.output, canonical(report))
        print(json.dumps(report, indent=2))
        return 1 if report['status'] == 'FAIL' or args.require_production and report['status'] != 'PASS' else 0
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError, subprocess.TimeoutExpired):
        print(json.dumps({'status': 'FAIL', 'reason': 'configuration or read-only verification failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
