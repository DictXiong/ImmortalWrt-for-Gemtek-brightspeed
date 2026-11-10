#!/usr/bin/env python3
"""Exercise the real init serializer with target jshn and inert network data."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--qemu', default=shutil.which('qemu-aarch64'))
    args = parser.parse_args()
    root = args.rootfs.resolve()
    repo = Path(__file__).resolve().parents[4]
    assert args.qemu and (root / 'usr/bin/jshn').is_file()
    init = repo / 'package/network/services/umdns/files/umdns.init'
    with tempfile.TemporaryDirectory(prefix='umdns-config-') as temp:
        stage = Path(temp)
        data = stage / 'interfaces.json'
        output = stage / 'request.json'
        tools = {
            'uci': '#!/bin/sh\nprintf "%s\\n" "lan lan6 second"\n',
            'ifstatus': '#!' + os.sys.executable + '\nimport json,sys\nprint(json.dumps(json.load(open(' + repr(str(data)) + ')).get(sys.argv[1], {})))\n',
            'ubus': '#!' + os.sys.executable + '\nimport json,sys\nassert sys.argv[1:4]==["call","umdns","set_config"]\njson.dump(json.loads(sys.argv[4]),open(' + repr(str(output)) + ',"w"))\n',
            'jshn': '#!/bin/sh\nexec ' + shlex.join([args.qemu, '-L', str(root), str(root / 'usr/bin/jshn')]) + ' "$@"\n',
        }
        for name, source in tools.items():
            path = stage / name
            path.write_text(source)
            path.chmod(0o700)
        script = ('. ' + shlex.quote(str(root / 'usr/share/libubox/jshn.sh')) + '\n' +
                  '. ' + shlex.quote(str(init)) + '\n' + '''
json_set_namespace original_
json_init
json_add_string marker kept
do_reload_service
test "$JSON_PREFIX" = original_
json_get_var marker marker
test "$marker" = kept
''')
        cases = [
            ('192.0.2.20', None, {}),
            ('192.0.2.21', None, {}),
            (None, None, {}),
            (None, '192.0.2.22', {}),
            ('192.0.2.20', None, {'l3_device': 'br-other', 'ipv4-address': [{'address': '198.51.100.20'}]}),
        ]
        for primary, secondary, other in cases:
            def interface(address):
                return {'l3_device': 'br-lan.1', 'ipv4-address': [{'address': address}] if address else []}
            data.write_text(json.dumps({'lan': interface(primary), 'lan6': interface(secondary), 'second': other}))
            result = subprocess.run([args.qemu, '-L', str(root), str(root / 'bin/busybox'), 'ash', '-s'],
                                    input=script, text=True, capture_output=True,
                                    env={**os.environ, 'PATH': str(stage) + ':' + os.environ['PATH']}, timeout=20)
            assert result.returncode == 0, result.stderr
            expected = {'interfaces': ['br-lan.1'], 'ipv4_sources': {'br-lan.1': primary or secondary or ''}}
            if other:
                expected['interfaces'].append('br-other')
                expected['ipv4_sources']['br-other'] = '198.51.100.20'
            assert json.loads(output.read_text()) == expected
    print('umdns netifd source serialization passed 5 target-jshn fixtures.')


if __name__ == '__main__':
    main()
