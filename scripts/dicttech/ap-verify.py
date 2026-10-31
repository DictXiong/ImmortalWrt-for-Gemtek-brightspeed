#!/usr/bin/env python3
"""Read-only resource verification; runtime checks never imply client acceptance."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ap_config import canonical, load_document, render, resolve, ssid_profiles, write_private
spec = importlib.util.spec_from_file_location('dicttech_ap_deploy', HERE / 'ap-deploy.py')
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
EXTERNAL = ('management_reachability', 'ipv4_ipv6_internet', 'cold_boot_and_link_recovery', 'cpu_and_throughput')
BANDS = {'2g': 2407, '5g': 5000, '6g': 5950}


def radio_probes(state):
    probes = {}
    for profile in ssid_profiles(state):
        for radio, band, link in zip(profile['radios'], profile['bands'], profile['link_ids']):
            iface = profile['iface'] + '_link' + str(link) if profile['mlo'] else profile['iface']
            probes.setdefault(radio, (band, iface))
    return dict(sorted(probes.items()))


def frequency(state, band, radio=None):
    if radio is None:
        radio = next(name for name, spec in state['uci'].get('wireless', {}).items()
                     if spec['type'] == 'wifi-device' and spec['options'].get('band') == band)
    configured = str(state['uci']['wireless'][radio]['options'].get('channel', 'auto'))
    if not configured.isdecimal():
        return None
    channel = int(configured)
    if band == '2g' and channel == 14:
        return 2484
    return BANDS[band] + 5 * channel


def read_command(args, words, parse=True):
    result = subprocess.run(deploy.ssh_base(args) + [shlex.join(words)], capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('read-only command unavailable')
    return json.loads(result.stdout) if parse else result.stdout.decode('utf-8')


def collect(args, state):
    data, errors = {}, {}
    queries = {
        'contract': ['check'],
        'service_links': ['ls', '-1', '/etc/rc.d'],
        'boot_hooks': ['sh', '-c', 'for n in S03dicttech-ap-transaction S04dicttech-ap-prepare S13dicttech-ap-finalize; do p=/etc/rc.d/$n; [ -L "$p" ] && [ -x "$p" ] || continue; t=$(readlink "$p"); s=${n#S??}; case "$t" in "../init.d/$s"|"/etc/init.d/$s") printf "%s\\n" "$n";; esac; done'],
        'services': ['sh', '-c', "ubus -t 5 call service list | jq 'map_values({instances:(.instances // {} | map_values({running:.running}))})'"],
    }
    for name, section in state['uci'].get('network', {}).items():
        if section['type'] == 'interface':
            queries['interface_' + name] = ['ubus', '-t', '5', 'call', 'network.interface.' + name, 'status']
    if state['ssids']:
        queries.update({
            'radio_inventory': ['sh', '-c', "ubus -t 5 call network.wireless status | jq 'with_entries(.value |= {up,disabled,interfaces:[(.interfaces // [])[] | {section,ifname}]})'"],
            'hostapd_objects': ['ubus', 'list', 'hostapd.*'],
            'security': ['sh', '-c', "for f in /var/run/hostapd-*.conf; do [ -f \"$f\" ] || continue; awk '/^(interface|bss|ssid|ssid2|bssid|bridge|wpa|wpa_key_mgmt|ieee80211w|rrm_neighbor_report|rrm_beacon_report|bss_transition|ap_isolate|ignore_broadcast_ssid|mld_ap|mld_link_id|disable_11be|beacon_prot)=/ { print }' \"$f\"; done"],
        })
    for profile in state['ssids']:
        key = profile['key']
        queries['hostapd_' + key] = ['ubus', '-t', '5', 'call', 'hostapd.' + profile['iface'], 'get_status']
        if profile['roaming']:
            queries['roaming_' + key] = ['/usr/lib/xr-roaming/helper.sh', 'status', key]
    for radio, (_, iface) in radio_probes(state).items():
        queries['radio_' + radio] = ['hostapd_cli', '-p', '/var/run/hostapd', '-i', iface, 'status']
    text = {'service_links', 'boot_hooks', 'security', 'hostapd_objects', *('radio_' + radio for radio in radio_probes(state))}
    for name, words in queries.items():
        try:
            data[name] = deploy.remote(args, 'check') if name == 'contract' else read_command(args, words, name not in text)
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
            errors[name] = 'unavailable or malformed read-only response'
    try:
        final = deploy.remote(args, 'check')
        data['stable_snapshot'] = final == data.get('contract')
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired):
        data['stable_snapshot'] = False
    data['collection_errors'] = errors
    return data


def parse_ssid2(value):
    # Match hostapd src/utils/common.c:wpa_config_parse_string() at source
    # 831364bf, plus hostapd's1..32byteSSIDlimit. Ordinary quoted strings
    # are literal bytes, NOT JSON/printf strings: backslashes stay unchanged.
    # P"..." uses hostapd printf_decode(), which we deliberately do not guess.
    if value.startswith('P"'):
        raise ValueError('unsupported_ssid_printf_encoding')
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise ValueError('invalid_ssid_string')
        raw = value[1:-1].encode('utf-8')
    else:
        if len(value) % 2 or not re.fullmatch('[0-9a-fA-F]+', value):
            raise ValueError('invalid_ssid_hex')
        raw = bytes.fromhex(value)
    if not 1 <= len(raw) <= 32:
        raise ValueError('invalid_ssid_length')
    return raw.decode('utf-8')


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
                block['ssid'] = parse_ssid2(block['ssid2'])
            except (ValueError, UnicodeError) as error:
                block['ssid'] = None
                block['ssid_parse_error'] = 'unsupported_ssid_printf_encoding' if str(error) == 'unsupported_ssid_printf_encoding' else 'invalid_ssid_encoding'
    return blocks



def evaluate(state, files, data, external=None):
    tests = []
    def test(name, okay, detail):
        tests.append({'id': name, 'status': 'NOT_TESTED' if okay is None else 'PASS' if okay else 'FAIL', 'detail': detail})
    contract = data.get('contract', {})
    identity = contract.get('identity', {})
    test('stable_snapshot', data.get('stable_snapshot') is True, 'Boot and complete resource contract stayed unchanged during collection')
    test('target_identity', contract.get('schema_version') == state['schema_version'] and
         contract.get('profile') == state['profile'] and contract.get('contract_sha256') == state['contract_sha256'] and
         identity.get('device_id') == state['device_id'] and identity.get('bridge_mac') == state['device']['identity']['bridge'] and
         identity.get('board_name') in state['platform']['board_names'], 'Backend contract, device identity and allowed board match')
    modes = deploy.file_modes(files)
    test('managed_resources', all(contract.get('files', {}).get(path) == hashlib.sha256(value).hexdigest() and
         contract.get('modes', {}).get(path) == [deploy.mode_security(modes[path]), '0'] for path, value in files.items()),
         'Every explicitly managed resource has its desired bytes, owner and mode')
    test('root_password', contract.get('root_password_synced') is True, 'Configured root password is active')
    if state['resolver'] is not None:
        test('resolver_policy', contract.get('resolver_state') == {'type':'symlink','target':state['resolver']['target']}, 'Resolver follows the explicit producer-supplied target')
    # Network expectations come from explicit UCI, never a built-in topology.
    for name, section in state['uci'].get('network', {}).items():
        if section['type'] != 'interface':
            continue
        options = section['options']; item = data.get('interface_' + name, {})
        needed = str(options.get('auto', '1')) != '0'
        okay = item.get('up') is needed and item.get('proto') == str(options['proto'])
        if options.get('device') is not None:
            okay = okay and item.get('l3_device', item.get('device')) == str(options['device'])
        if needed and options['proto'] == 'dhcp':
            okay = okay and bool(item.get('ipv4-address'))
        if needed and options['proto'] == 'dhcpv6' and options.get('reqaddress', 'try') != 'none':
            okay = okay and bool(item.get('ipv6-address'))
        if needed and options['proto'] == 'static':
            values = options.get('ipaddr', [])
            values = values if isinstance(values, list) else [values]
            addresses = {row.get('address') for row in item.get('ipv4-address', [])}
            okay = okay and all(str(ipaddress.ip_interface(value).ip) in addresses for value in values)
        test('interface_' + name, okay, 'Live interface follows the explicitly configured protocol/device/address')
    running = {name: any(row.get('running') is True for row in value.get('instances', {}).values())
               for name, value in data.get('services', {}).items()}
    links = data.get('service_links', '').splitlines()
    for name, policy in state['services'].items():
        enabled = any(link.startswith('S') and link[1:3].isdigit() and link[3:] == name for link in links)
        okay = enabled is policy['enable']
        if not policy['enable'] or policy.get('running') is False:
            okay = okay and not running.get(name, False)
        elif policy.get('running') is True and not policy.get('oneshot', False):
            okay = okay and running.get(name, False)
        test('service_' + name, okay, 'Live enable/running state follows the explicit service policy')
    test('boot_policy_hooks', set(data.get('boot_hooks', '').splitlines()) ==
         {'S03dicttech-ap-transaction', 'S04dicttech-ap-prepare', 'S13dicttech-ap-finalize'},
         'Persistent rollback, resource prepare and finalize hooks are enabled')
    profiles = state['ssids']
    if profiles:
        inventory = data.get('radio_inventory', {})
        wanted_radios = {name for name, spec in state['uci'].get('wireless', {}).items() if spec['type'] == 'wifi-device'}
        okay = isinstance(inventory, dict) and set(inventory) == wanted_radios
        for radio in wanted_radios:
            expected = {(profile['section'], profile['iface']) for profile in profiles if radio in profile['radios']}
            item = inventory.get(radio, {})
            rows = item.get('interfaces', [])
            actual = {(row.get('section'), row.get('ifname')) for row in rows}
            okay = okay and actual == expected and len(actual) == len(rows)
            if expected:
                okay = okay and item.get('up') is True and item.get('disabled') is False
        objects = data.get('hostapd_objects', '').splitlines()
        okay = okay and len(set(objects)) == len(objects) and set(objects) == {'hostapd.' + profile['iface'] for profile in profiles}
        test('wireless_inventory', okay, 'Exactly the producer-selected BSS and radio assignments are present')
    blocks = security_blocks(data.get('security', ''))
    workers = {name for name, spec in data.get('services', {}).get('xr_roaming', {}).get('instances', {}).items() if spec.get('running') is True}
    for profile in profiles:
        key = profile['key']; status = data.get('hostapd_' + key, {})
        expected_links = {bssid: frequency(state, band, radio) for bssid, band, radio in zip(profile['bssids'], profile['bands'], profile['radios'])}
        observed = status.get('links') if profile['mlo'] else [status]
        valid = isinstance(observed, list) and len(observed) == len(expected_links) and all(isinstance(row, dict) and isinstance(row.get('bssid'), str) for row in observed)
        actual = {row['bssid'].lower(): row.get('freq') for row in observed} if valid else {}
        test('wireless_' + key, status.get('status') == 'ENABLED' and status.get('ssid') == profile['name'] and set(actual) == set(expected_links) and all(type(actual[address]) is int and (wanted is None or actual[address] == wanted) for address,wanted in expected_links.items()),
             'Live BSSIDs, SSID and link frequencies match producer metadata')
        configs = [block for block in blocks if block.get('ssid') == profile['name']]
        options = state['uci']['wireless'][profile['section']]['options']
        encryption = profile['encryption'].split('+', 1)[0]
        supported = encryption in {'none','psk','psk2','psk-mixed','sae','sae-mixed','owe','wpa','wpa2','wpa-mixed','wpa3','wpa3-192'}
        link_ids = dict(zip(profile['bssids'], profile['link_ids']))
        def secure(block):
            modes = set(block.get('wpa_key_mgmt', '').split()); psk = bool(modes & {'WPA-PSK', 'WPA-PSK-SHA256'})
            required = {
                'none': set(), 'psk': {'WPA-PSK'}, 'psk2': {'WPA-PSK','WPA-PSK-SHA256'},
                'psk-mixed': {'WPA-PSK'}, 'sae': {'SAE','SAE-EXT-KEY'}, 'sae-mixed': {'SAE','SAE-EXT-KEY','WPA-PSK','WPA-PSK-SHA256'},
                'owe': {'OWE'}, 'wpa': {'WPA-EAP'}, 'wpa2': {'WPA-EAP','WPA-EAP-SHA256'},
                'wpa-mixed': {'WPA-EAP'}, 'wpa3': {'WPA-EAP-SHA256'}, 'wpa3-192': {'WPA-EAP-SUITE-B-192'},
            }.get(encryption)
            akm = True if required is None else not modes if encryption == 'none' else bool(modes) and modes <= required
            if encryption == 'sae-mixed': akm = akm and psk and bool(modes & {'SAE','SAE-EXT-KEY'})
            bssid = block.get('bssid', '').lower()
            mlo = block.get('mld_ap') == '1' and block.get('mld_link_id') == str(link_ids.get(bssid)) if profile['mlo'] else block.get('mld_ap', '0') == '0'
            wanted_wpa = {'none':'0','psk':'1','wpa':'1','psk-mixed':'3','wpa-mixed':'3'}.get(encryption,'2')
            result = bool(akm and (not supported or block.get('wpa','0') == wanted_wpa) and block.get('ieee80211w', '0') == str(profile['pmf']) and
                          block.get('interface', block.get('bss')) == profile['iface'] and bssid in expected_links and mlo and
                          block.get('disable_11be', '0') == ('0' if profile['eht'] else '1') and
                          block.get('ignore_broadcast_ssid', '0') == ('1' if profile['hidden'] else '0'))
            return result and all(block.get(field, '0') == str(options.get(field,options.get('ieee80211k','0') if field.startswith('rrm_') else '0')) for field in ('rrm_neighbor_report','rrm_beacon_report','bss_transition'))
        test('security_' + key, {block.get('bssid', '').lower() for block in configs} == set(expected_links) and all(secure(block) for block in configs),
             'Live WPA/PMF/MLO/hidden/EHT/roaming configuration follows explicit BSS metadata')
        if not supported:
            test('akm_' + key, None, 'This AKM needs independent runtime verification; resource rendering remains supported')
        if profile['roaming']:
            observed = data.get('roaming_' + key, {})
            peers = {item.get('id'): item for item in observed.get('peers', [])}
            wanted = {item['id'] for item in state['peers'][key]}
            test('roaming_' + key, observed.get('state') == 'ok' and observed.get('snapshot_fresh') is True and set(peers) == wanted and all(item.get('fresh') is True for item in peers.values()),
                 'Only explicit peer metadata is present and fresh')
        else:
            test('roaming_' + key, key not in workers, 'Disabled BSS roaming has no worker')
    for radio, (band, _) in radio_probes(state).items():
        status = dict(line.split('=', 1) for line in data.get('radio_' + radio, '').splitlines() if '=' in line)
        options = state['uci']['wireless'][radio]['options']
        width_code = status.get('eht_oper_chwidth',status.get('he_oper_chwidth',status.get('vht_oper_chwidth')))
        secondary = status.get('secondary_channel')
        width = (40 if secondary in ('-1','1') else 20 if secondary=='0' else None) if width_code in ('0',None) else {'1':80,'2':160,'4':320}.get(width_code)
        mode = re.fullmatch(r'(?:EHT|HE|VHT|HT)(20|40|80|160|320)(?:[+-])?',str(options.get('htmode','')))
        wanted = int(mode[1]) if mode else None
        allowed = {20,40} if band=='2g' and wanted==40 and str(options.get('noscan','0'))=='0' else {wanted}
        configured_frequency = frequency(state,band,radio)
        okay = status.get('state')=='ENABLED'
        if configured_frequency is not None:
            okay = okay and status.get('freq')==str(configured_frequency) and status.get('channel')==str(options['channel'])
        if wanted is not None:
            okay = okay and width in allowed
        test('width_' + radio, okay, 'Live channel and width follow explicitly supplied radio constraints')
    binding = {'schema_version': 1, 'profile': state['profile'], 'device_id': state['device_id'], 'boot_id': contract.get('boot_id'),
               'desired_sha256': hashlib.sha256(canonical({path: hashlib.sha256(value).hexdigest() for path, value in sorted(files.items())})).hexdigest()}
    valid_external = isinstance(external, dict) and all(external.get(key) == value for key, value in binding.items())
    if external is not None:
        test('external_report_binding', valid_external, 'External evidence belongs to this boot/device/full desired configuration')
    configuration = 'FAIL' if any(item['status']=='FAIL' for item in tests) else 'NOT_TESTED' if any(item['status']=='NOT_TESTED' for item in tests) else 'PASS'
    guest_gates = ('guest_client_mutual','guest_internal_isolation','ppe_bound_isolation_and_vlan','spoof_and_vlan_injection') if 'guest_vlan' in state['platform'] else ()
    gates = EXTERNAL + guest_gates + (('wifi_client_auth',) if profiles else ()) + (('actual_client_roaming',) if any(profile['roaming'] for profile in profiles) else ()) + (('six_ghz_client_auth',) if any('6g' in profile['bands'] for profile in profiles) else ())
    for name in gates:
        item = external.get('tests', {}).get(name, {}) if valid_external else {}
        status = item.get('status') if item.get('status') in ('PASS', 'FAIL') else 'NOT_TESTED'
        tests.append({'id': name, 'status': status, 'detail': 'Independent client or hardware evidence is required'})
    status = 'FAIL' if any(item['status'] == 'FAIL' for item in tests) else 'NOT_TESTED' if any(item['status'] == 'NOT_TESTED' for item in tests) else 'PASS'
    return {'schema_version': 1, 'profile': state['profile'], 'device_id': state['device_id'], 'boot_id': contract.get('boot_id'),
            'status': status, 'configuration_status': configuration, 'tests': tests}, binding


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'device', 'host'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--port', type=int, default=12022)
    p.add_argument('--control-socket'); p.add_argument('--jump-host')
    p.add_argument('--external-report', type=Path); p.add_argument('--binding-output', type=Path)
    p.add_argument('--output', type=Path); p.add_argument('--require-production', action='store_true')
    args = p.parse_args()
    if not 1 <= args.port <= 65535:
        p.error('invalid SSH port')
    try:
        state = resolve(load_document(args.config), args.device)
        files = render(state)
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
