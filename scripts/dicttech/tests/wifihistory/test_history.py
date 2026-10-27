#!/usr/bin/env python3
"""Run the patched collector/RPC in real ucode; replace only the ubus transport.

Use --ucode 'ucode -L /path/to/ucode/modules' or the target ucode under qemu.
The complete postimage is extracted from the strict full-context feed patch.
"""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PATCH = REPO / 'patches/feeds/luci/applications/luci-app-wifihistory/120-dicttech-bounded-mld-history.patch'
PACKAGE = 'applications/luci-app-wifihistory/'
RUNNER = []


def postimage(relative):
    lines = PATCH.read_text().splitlines(keepends=True)
    marker = '+++ b/' + PACKAGE + relative + '\n'
    start = lines.index(marker) + 1
    match = re.fullmatch(r'@@ -1,\d+ \+1,(\d+) @@\n', lines[start])
    if not match:
        raise AssertionError('Test requires one full-file context hunk: ' + relative)
    result = []
    for line in lines[start + 1:]:
        if line.startswith('diff --git '):
            break
        if line.startswith((' ', '+')):
            result.append(line[1:])
        elif line.startswith('@@'):
            raise AssertionError('Postimage omits source context: ' + relative)
    if len(result) != int(match.group(1)):
        raise AssertionError('Incomplete postimage: ' + relative)
    return ''.join(result)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wifihistory-test-')
        self.root = Path(self.tmp.name)
        self.history_dir = self.root / 'history'
        self.history_dir.mkdir()
        self.lock = self.root / 'history.lock'
        self.fixture = json.loads((HERE / 'fixtures/c7-mld.json').read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def run_ucode(self, source):
        path = self.root / 'test.uc'
        path.write_text(source)
        result = subprocess.run(RUNNER + [str(path)], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def paths(self, source):
        return source.replace("'/var/lib/wifihistory'", json.dumps(str(self.history_dir))).replace(
            "'/var/lock/wifihistory.lock'", json.dumps(str(self.lock)))

    def write_history(self, value):
        (self.history_dir / 'history.json').write_text(json.dumps(value))

    def read(self, name='history.json'):
        return json.loads((self.history_dir / name).read_text())

    def poll(self, fixture=None):
        fixture = fixture or self.fixture
        data = self.root / 'fixture.json'
        data.write_text(json.dumps(fixture))
        source = postimage('root/usr/sbin/wifihistory')
        transport = '''
const fixture = json(readfile(%s));
let seen_calls = [];
function connect() {
    if (fixture.connect_failed) return null;
    return {
        call: function(object, method, args) {
            let key = object + '.' + method + (args?.device ? '@' + args.device : '');
            push(seen_calls, key);
            return fixture.calls[key];
        },
        disconnect: function() {}
    };
}
''' % json.dumps(str(data))
        source = source.replace("import { connect } from 'ubus';", transport)
        source = source.replace('let now = time();', 'let now = fixture.now;')
        source += "\nwrite_json(HISTORY_DIR + '/calls.json', seen_calls);\n"
        self.run_ucode(self.paths(source))
        return self.read()

    def rpc(self, method, now=None):
        source = self.paths(postimage('root/usr/share/rpcd/ucode/wifihistory.uc'))
        source = source.replace('time()', str(now or self.fixture['now']))
        source = source.replace("return { 'luci.wifihistory': methods };", "printf('%J', methods[" + json.dumps(method) + "].call());")
        return json.loads(self.run_ucode(source))

    @staticmethod
    def old_row(mac='02:AA:BB:CC:DD:EE', now=2000000000, **extra):
        row = dict(mac=mac, ifname='ap-mld0', connected=True,
                   network='DictTech', first_seen=now - 100, last_seen=now - 30)
        row.update(extra)
        return row

    def test_c7_mld_aggregate_and_link_hints(self):
        history = self.poll()
        self.assertEqual(list(history), ['02:AA:BB:CC:DD:EE'])
        row = history['02:AA:BB:CC:DD:EE']
        self.assertEqual((row['signal'], row['network'], row['hostname']), (-41, 'DictTech', 'test-client'))
        self.assertEqual((row['ipv4'], row['ipv6']), ('192.0.2.23', '2001:db8::23'))
        calls = self.read('calls.json')
        self.assertEqual(calls.count('hostapd.ap-mld0.get_clients'), 1)
        self.assertFalse(any(call.startswith('iwinfo.') for call in calls))
        self.assertEqual(self.read('status.json')['stations'], 1)
        self.assertEqual(stat.S_IMODE(self.history_dir.stat().st_mode), 0o700)
        for filename in ('history.json', 'status.json'):
            self.assertEqual(stat.S_IMODE((self.history_dir / filename).stat().st_mode), 0o600)

    def test_empty_sample_disconnects_and_rejoin_preserves_first_seen(self):
        self.write_history({'02:aa:bb:cc:dd:ee': self.old_row()})
        fixture = copy.deepcopy(self.fixture)
        fixture['calls']['hostapd.ap-mld0.get_clients']['clients'] = {}
        absent = self.poll(fixture)['02:AA:BB:CC:DD:EE']
        self.assertFalse(absent['connected'])
        self.assertEqual(absent['last_seen'], self.fixture['now'] - 30)
        joined = self.poll()['02:AA:BB:CC:DD:EE']
        self.assertTrue(joined['connected'])
        self.assertEqual(joined['first_seen'], self.fixture['now'] - 100)
        self.assertEqual(joined['last_seen'], self.fixture['now'])

    def test_failed_interface_preserves_last_connection_state(self):
        self.write_history({'02:AA:BB:CC:DD:EE': self.old_row()})
        fixture = copy.deepcopy(self.fixture)
        fixture['calls']['hostapd.ap-mld0.get_clients'] = None
        result = self.poll(fixture)
        self.assertTrue(result['02:AA:BB:CC:DD:EE']['connected'])
        self.assertEqual(self.read('status.json')['state'], 'partial')

    def test_ubus_and_netifd_failure_preserve_history(self):
        initial = {'02:AA:BB:CC:DD:EE': self.old_row()}
        for fail in ('connect_failed', 'netifd'):
            with self.subTest(fail=fail):
                self.write_history(initial)
                fixture = copy.deepcopy(self.fixture)
                if fail == 'connect_failed':
                    fixture['connect_failed'] = True
                else:
                    fixture['calls']['network.wireless.status'] = None
                self.assertEqual(self.poll(fixture), initial)
                self.assertEqual(self.read('status.json')['state'], 'failed')

    def test_iwinfo_legacy_fallback_and_invalid_station(self):
        fixture = copy.deepcopy(self.fixture)
        fixture['calls']['hostapd.ap-mld0.get_clients'] = None
        fixture['calls']['hostapd.ap-mld0.get_status'] = None
        fixture['calls']['iwinfo.info@ap-mld0'] = {'ssid': 'Legacy'}
        fixture['calls']['iwinfo.assoclist@ap-mld0'] = {'results': [
            {'mac': '02:11:22:33:44:55', 'signal': -52, 'noise': -92},
            {'mac': '01:11:22:33:44:55'}, {'mac': 'invalid'}]}
        for radio in fixture['calls']['network.wireless.status'].values():
            radio['interfaces'][0]['config']['ssid'] = ''
        row = self.poll(fixture)['02:11:22:33:44:55']
        self.assertEqual((row['signal'], row['noise'], row['network']), (-52, -92, 'Legacy'))
        self.assertEqual(self.read('status.json')['state'], 'ok')

    def test_retention_capacity_and_allowlisted_fields(self):
        now = self.fixture['now']
        rows = {}
        for index in range(600):
            mac = '02:00:00:00:%02X:%02X' % (index // 256, index % 256)
            rows[mac] = self.old_row(mac, connected=False, extra_secret='never-store-this')
        rows['02:FF:FF:FF:FF:FF'] = self.old_row('02:FF:FF:FF:FF:FF', last_seen=now - 604801)
        self.write_history(rows)
        result = self.poll()
        self.assertEqual(len(result), 512)
        self.assertIn('02:AA:BB:CC:DD:EE', result)
        self.assertNotIn('02:FF:FF:FF:FF:FF', result)
        self.assertNotIn('extra_secret', json.dumps(result))

    def test_malformed_history_is_rebuilt(self):
        (self.history_dir / 'history.json').write_text('{invalid')
        self.assertEqual(len(self.poll()), 1)

    def test_clear_shares_lock_and_does_not_race_collector(self):
        self.write_history({'02:AA:BB:CC:DD:EE': self.old_row()})
        with self.lock.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.rpc('clearHistory')['result'])
            self.assertTrue(self.read())
        self.assertTrue(self.rpc('clearHistory')['result'])
        self.assertEqual(self.read(), {})
        self.assertEqual(stat.S_IMODE((self.history_dir / 'history.json').stat().st_mode), 0o600)

    def test_health_distinguishes_idle_running_partial_and_failed(self):
        self.assertFalse(self.rpc('getStatus')['status']['active'])
        for state in ('ok', 'partial', 'failed'):
            (self.history_dir / 'status.json').write_text(json.dumps(dict(last_poll=self.fixture['now'], state=state)))
            status = self.rpc('getStatus')['status']
            self.assertTrue(status['active'])
            self.assertEqual(status['state'], state)
        self.assertFalse(self.rpc('getStatus', now=self.fixture['now'] + 91)['status']['active'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ucode', default=os.environ.get('DICTTECH_TEST_UCODE', 'ucode'))
    args, remaining = parser.parse_known_args()
    RUNNER = shlex.split(args.ucode)
    unittest.main(argv=[__file__] + remaining)
