"""Private, inert DHCP/offline bootstrap proof fixtures; never touch host networking."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


ROOT = Path(os.environ.get('DICTTECH_AP_TEST_SOURCE_ROOT', Path(__file__).resolve().parents[4])).resolve()
SOURCE = ROOT / 'package/dicttech/ap-profile/files/usr/lib/dicttech-ap'
SHARE = ROOT / 'package/dicttech/ap-profile/files/usr/share/dicttech-ap'


class SetupFixture:
    def __init__(self, base, root=None):
        self.base = Path(base)
        self.root = Path(root) if root else self.base / 'root'
        self.root.mkdir(parents=True, exist_ok=True)
        for rel in ['etc/config', 'etc/dicttech-ap', 'etc/dropbear', 'tmp', 'tmp/.uci',
                    'sys/class/net/lan4', 'proc/sys/kernel/random', 'usr/libexec/dicttech-ap']:
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        self.bin = self.base / 'setup-bin'; self.bin.mkdir()
        for src, dest in [(SOURCE / 'setup', self.root / 'usr/lib/dicttech-ap/setup'),
                          (SHARE, self.root / 'usr/share/dicttech-ap')]:
            shutil.copytree(src, dest, dirs_exist_ok=True)
        self.write('proc/sys/kernel/random/boot_id', b'00000000-0000-4000-8000-000000000001\n')
        self.write('etc/config/wireless', b"config wifi-device 'radio0'\n option disabled '0'\nconfig wifi-iface 'default_radio0'\n option disabled '0'\n")
        self.write('etc/config/network', b'# original fixture network\n')
        if not (self.root / 'etc/shadow').exists():
            self.write('etc/shadow', b'root:!:20000:0:99999:7:::\n')
        self.runtime = self.base / 'setup-runtime.json'
        self.runtime.write_text('{}')
        self.tool('uci', '''import pathlib,shlex,sys
root=pathlib.Path(ROOT)
args=[arg for arg in sys.argv[1:] if arg!='-q']
command=args[0]
if command in ['changes','commit']:sys.exit(0)
name=(args[1] if len(args)>1 else '').split('.')[0]
path=root/'etc/config'/name
rows={};section=None
for line in path.read_text().splitlines() if path.exists() else []:
 words=shlex.split(line,comments=True)
 if not words:continue
 if words[0]=='config':
  section=words[2] if len(words)>2 else '@'+words[1]+'[0]';rows[section]={'_type':words[1]}
 elif words[0]=='option':rows[section][words[1]]=words[2]
if command=='set':
 field,value=args[1].split('=',1);package,section,option=field.split('.')
 assert package=='wireless' and option=='disabled' and value=='1'
 rows[section][option]=value
 data=''
 for key,row in rows.items():
  data+='config '+row['_type']+' '+shlex.quote(key)+'\\n'
  for option,value in row.items():
   if option!='_type':data+=' option '+shlex.quote(option)+' '+shlex.quote(value)+'\\n'
 path.write_text(data)
elif command=='show':
 for key,row in rows.items():
  print(name+'.'+key+'='+row['_type'])
  for option,value in row.items():
   if option!='_type':print(name+'.'+key+'.'+option+'='+shlex.quote(value))
elif command=='get':
 parts=args[1].split('.')
 try:print(rows[parts[1]]['_type' if len(parts)==2 else parts[2]])
 except KeyError:sys.exit(1)
else:sys.exit(1)
''')
        self.tool('ubus', '''import json,pathlib,sys
args=sys.argv[1:];index=args.index('call');name=args[index+1]
if name=='network.device':assert json.loads(args[index+3])=={'name':'br-setup'}
value=json.loads(pathlib.Path(RUNTIME).read_text()).get(name)
if value is None:sys.exit(1)
print(json.dumps(value))
''', runtime=str(self.runtime))
        self.tool('ip', '''import json,pathlib,sys
assert sys.argv[1:5]==['-4','address','show','dev']
device=sys.argv[5];assert device in ['br-setup','br-setup.1']
value=json.loads(pathlib.Path(RUNTIME).read_text())
addresses=value.get('kernel_ipv4',{}).get(device)
if addresses is None:
 addresses=value.get('network.interface.bootstrap',{}).get('ipv4-address',[]) if device=='br-setup.1' else []
print('1: '+device+': <UP>')
for item in addresses:print('    inet '+item['address']+'/'+str(item['mask'])+' scope global '+device)
''', runtime=str(self.runtime))
        self.tool('ls', '''import pathlib,stat,sys
assert sys.argv[1]=='-nd' and len(sys.argv)==3
path=pathlib.Path(sys.argv[2]);value=path.lstat()
# The fixture tree represents root-owned target files even under a nonroot CI uid.
print(stat.filemode(value.st_mode)+' 1 0 0 '+str(value.st_size)+' Jan 1 00:00 '+str(path))
''')
        self.shell_tool('nft', 'case "$*" in "list table"*) exit 1;; esac\ncat >/dev/null\n')
        self.write('usr/lib/dicttech-ap/services.sh', b'#!/bin/sh\nexit 0\n', 0o700)
        self.write('usr/libexec/dicttech-ap/transaction', b'#!/bin/sh\nexit 0\n', 0o700)
        script = (SOURCE / 'bootstrap.sh').read_text()
        script = re.sub(r'(?<!/proc)/(?:usr/share|usr/libexec|usr/lib|etc|root|tmp|sys)/',
                        lambda m: str(self.root / m[0].strip('/')) + '/', script)
        script = script.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                                'PATH=' + shlex.quote(str(self.bin) + ':' + os.environ['PATH']))
        script = script.replace('-C / ', '-C ' + shlex.quote(str(self.root)) + ' ')
        script = script.replace('"/$name"', '"' + str(self.root) + '/$name"')
        script = script.replace('destination=/$name', 'destination=' + str(self.root) + '/$name')
        script = script.replace('base=${2:-}', 'base=${2:-' + str(self.root) + '}')
        script = script.replace('/proc/sys/kernel/random/boot_id', str(self.root / 'proc/sys/kernel/random/boot_id'))
        self.script = self.base / 'bootstrap-fixture.sh'
        self.script.write_text(script); self.script.chmod(0o700)

    def write(self, rel, value, mode=0o600):
        path = self.root / rel; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value); path.chmod(mode)

    def tool(self, name, body, **extra):
        text = '#!' + shutil.which('python3') + '\nROOT=' + repr(str(self.root)) + '\n'
        text += ''.join(key.upper() + '=' + repr(value) + '\n' for key, value in extra.items())
        path = self.bin / name; path.write_text(text + body); path.chmod(0o700)

    def shell_tool(self, name, text):
        path = self.bin / name; path.write_text('#!/bin/sh\n' + text); path.chmod(0o700)

    def call(self, command, good=True):
        result = subprocess.run([os.environ.get('DICTTECH_AP_TEST_SHELL', '/bin/sh'), str(self.script), command],
                                text=True, capture_output=True, timeout=15)
        assert (result.returncode == 0) == good, (command, result.returncode, result.stdout, result.stderr)
        return result

    @property
    def identity(self):
        return json.loads((self.root / 'etc/dicttech-ap/bootstrap-identity.json').read_text())

    def activate(self):
        mode = json.loads((self.root / 'etc/dicttech-ap/bootstrap-state.json').read_text())['mode']
        runtime = {'network.interface.ap_maintenance': {
            'up': True, 'proto': 'static', 'l3_device': 'lan4',
            'ipv4-address': [{'address': '192.168.250.1', 'mask': 24}]}}
        if mode == 'dhcp':
            runtime.update({'network.interface.bootstrap': {'up': True, 'proto': 'dhcp',
                            'l3_device': 'br-setup.1', 'ipv6-address': [],
                            'ipv4-address': [{'address': '192.0.2.20', 'mask': 24}]},
                            'network.device': {'up': True, 'macaddr': self.identity['mac'],
                            'bridge-members': ['wan', 'lan2', 'lan3']}})
            self.write('sys/class/net/br-setup/address', (self.identity['mac'] + '\n').encode())
            self.write('sys/class/net/br-setup.1/address', (self.identity['mac'] + '\n').encode())
        self.runtime.write_text(json.dumps(runtime))


def main():
    report = []
    def check(name, fn):
        with tempfile.TemporaryDirectory(prefix='dicttech-setup-') as base:
            fixture = SetupFixture(base); fn(fixture)
        report.append({'case': name, 'passed': True})

    def fresh(f):
        f.call('prepare'); f.activate()
        value = json.loads(f.call('check-setup').stdout)
        assert value == {'mode': 'dhcp', 'bridge_mac': f.identity['mac'], 'dhcp_ready': True, 'ssh_port': '12022'}
        assert re.fullmatch(r'02(?::[0-9a-f]{2}){5}', f.identity['mac'])
        assert f.identity['hostname'] == 'xr1710g-setup-' + f.identity['mac'].replace(':', '')
        assert (f.root / 'etc/dicttech-ap/bootstrap-identity.json').stat().st_mode & 0o777 == 0o600
        network = (f.root / 'etc/config/network').read_text()
        assert "option proto 'dhcp'" in network and "option vlan '1'" in network
        assert "list ports 'wan'" in network and "list ports 'lan4'" not in network
        assert "option device 'lan4'" in network and "option device 'br-setup.1'" in network
        dropbear = (f.root / 'etc/config/dropbear').read_text().split("config dropbear 'bootstrap'")
        assert "option PasswordAuth 'on'" in dropbear[0]
        assert "option PasswordAuth 'off'" in dropbear[1] and "option RootPasswordAuth 'off'" in dropbear[1]
        assert "listen_http '192.168.250.1:80'" in (f.root / 'etc/config/uhttpd').read_text()
        firewall = (f.root / 'etc/config/firewall').read_text()
        assert "option forward 'DROP'" in firewall and 'masq' not in firewall
        assert "option dest_port '80 12022'" not in firewall.split("config zone 'bootstrap'")[1]
        assert (f.root / 'etc/config/dhcp').read_text().lstrip().startswith('#')
        before = {str(p): p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
        f.call('check-setup')
        assert before == {str(p): p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
    check('fresh_dhcp_plus_separate_offline_maintenance_and_readonly_proof', fresh)

    def stable(f):
        f.call('prepare'); original = f.identity
        network = (f.root / 'etc/config/network').read_bytes()
        (f.root / 'tmp/dicttech-ap-bootstrap-isolated').unlink()
        f.call('prepare')
        assert f.identity == original and (f.root / 'etc/config/network').read_bytes() == network
        with tempfile.TemporaryDirectory(prefix='dicttech-other-setup-') as other:
            second = SetupFixture(other); second.call('prepare')
            assert original['mac'] != second.identity['mac']
    check('random_per_device_identity_is_unique_and_persistent_across_boots', stable)

    def normalization(f):
        f.call('prepare-boot'); original = f.identity
        with (f.root / 'etc/config/network').open('a') as stream: stream.write('# S10 normalization\n')
        f.write('tmp/.uci/network', b"network.globals.ula_prefix='fixture'\n")
        f.call('finalize')  # Older one-shot shims must stay deferred during S10.
        assert '# S10 normalization' in (f.root / 'etc/config/network').read_text()
        f.call('finalize-boot'); f.activate(); f.call('check-setup')
        assert f.identity == original and not (f.root / 'tmp/.uci/network').exists()
    check('fresh_setup_is_restored_after_stock_firstboot_normalization', normalization)

    def invalid(f):
        f.write('etc/dicttech-ap/device.json', b'{"invalid":"marker"}\n')
        f.call('prepare'); f.activate()
        state = json.loads((f.root / 'etc/dicttech-ap/bootstrap-state.json').read_text())
        assert state['reason'] == 'invalid_marker' and state['mode'] == 'isolated'
        assert not (f.root / 'etc/dicttech-ap/bootstrap-identity.json').exists()
        assert (f.root / 'etc/config/network').read_bytes() == (SOURCE / 'setup/network').read_bytes()
        f.call('check-setup', good=False)  # A quarantined marker is not a new-device enrollment.
    check('invalid_managed_marker_never_exposes_dhcp_or_initialization', invalid)

    def isolated(f):
        f.shell_tool('hexdump', 'exit 1\n'); f.call('prepare'); f.activate()
        assert not (f.root / 'etc/dicttech-ap/bootstrap-identity.json').exists()
        assert json.loads(f.call('check-setup').stdout)['mode'] == 'isolated'
        assert 'br-setup' not in (f.root / 'etc/config/network').read_text()
    check('failed_entropy_stays_isolated_without_fixed_mac_fallback', isolated)

    def shell_failure(f):
        f.write('usr/libexec/dicttech-cli/activate-root-shell', b'#!/bin/sh\nexit 1\n', 0o700)
        result = f.call('prepare'); f.activate(); f.call('check-setup')
        assert 'CLI root shell activation failed' in result.stderr
        result = f.call('finalize'); f.call('check-setup')
        assert 'CLI root shell activation failed' in result.stderr
    check('optional_cli_activation_failure_preserves_setup_recovery', shell_failure)

    def shell_timeout(f):
        import time
        f.write('usr/libexec/dicttech-cli/activate-root-shell', b'#!/bin/sh\nsleep 60\n', 0o700)
        before = time.monotonic()
        result = f.call('prepare'); elapsed = time.monotonic() - before
        f.activate(); f.call('check-setup')
        assert elapsed < 12 and 'CLI root shell activation failed' in result.stderr
    check('optional_cli_activation_timeout_preserves_dhcp_setup', shell_timeout)

    def direct_dropbear_binding(f):
        source = (ROOT / 'package/network/services/dropbear/files/dropbear.init').read_text()
        def function(name):
            match = re.search(r'^' + name + r'\(\)\n\{.*?^\}', source, re.M | re.S)
            assert match, 'missing actual dropbear init function: ' + name
            return match.group()
        defaults = {'enable': '1', 'Port': '12022', 'PasswordAuth': '1', 'RootPasswordAuth': '1',
                    'RootLogin': '1', 'GatewayPorts': '0', 'LocalPortForward': '1', 'RemotePortForward': '1',
                    'ForceCommand': '', 'rsakeyfile': '', 'BannerFile': '', 'IdleTimeout': '0',
                    'MaxSessionDuration': '0', 'SSHKeepAlive': '300', 'MaxAuthTries': '3',
                    'RecvWindowSize': '0', 'mdns': '0', 'vrf': '', 'Interface': ''}
        for profile, section in [('dropbear', 'setup'), ('dropbear-dhcp', 'setup'), ('dropbear-dhcp', 'bootstrap')]:
            rows = {}; current = None
            for line in (SOURCE / 'setup' / profile).read_text().splitlines():
                words = shlex.split(line, comments=True)
                if not words: continue
                if words[0] == 'config': current = words[2]; rows[current] = {}
                elif words[0] == 'option': rows[current][words[1]] = words[2]
            values = {**defaults, **rows[section]}
            assert 'Interface' not in rows[section]
            assert values['DirectInterface'] == ('bootstrap' if section == 'bootstrap' else 'ap_maintenance')
            for ready, boot in [(False, ''), (False, '1'), (True, ''), (True, '1')]:
                command_file = f.base / 'dropbear-command'
                command_file.unlink(missing_ok=True)
                command = f.base / 'dropbear-init-fixture.sh'
                header = 'set -eu\nNAME=dropbear\nPROG=/usr/sbin/dropbear\n'
                header += ''.join(key + '=' + shlex.quote('1' if value == 'on' else '0' if value == 'off' else value) + '\n'
                                  for key, value in {**values, 'BOOT': boot}.items())
                header += 'network_is_up() { [ "$1" = ap_maintenance ] || [ ' + str(int(ready)) + ' = 1 ]; }\n'
                header += 'network_get_device() { if [ "$2" = ap_maintenance ]; then eval "$1=lan4"; else eval "$1=br-setup.1"; fi; }\n'
                header += 'logger() { :; }\nconfig_list_foreach() { :; }\n'
                header += 'procd_open_instance() { :; }\nprocd_close_instance() { :; }\nprocd_set_param() {\n'
                header += ' if [ "$1" = command ]; then shift; printf "%s\\n" "$@" > ' + shlex.quote(str(command_file)) + '; fi\n}\n'
                header += 'procd_append_param() {\n'
                header += ' if [ "$1" = command ]; then shift; printf "%s\\n" "$@" >> ' + shlex.quote(str(command_file)) + '; fi\n}\n'
                command.write_text(header + function('normalize_list') + '\n' + function('dropbear_instance') + '\n' +
                                   'dropbear_instance ' + shlex.quote(section) + ' 0\n')
                result = subprocess.run([os.environ.get('DICTTECH_AP_TEST_SHELL', '/bin/sh'), str(command)],
                                        text=True, capture_output=True, timeout=10)
                if section == 'bootstrap' and not ready:
                    assert not command_file.exists(), 'pending DHCP emitted a wildcard SSH listener'
                    assert result.returncode == (0 if boot else 1), result.stderr
                else:
                    assert result.returncode == 0, result.stderr
                    argv = command_file.read_text().splitlines()
                    device = 'br-setup.1' if section == 'bootstrap' else 'lan4'
                    assert argv[argv.index('-l') + 1] == device and argv[argv.index('-p') + 1] == '12022'
                    assert ('-s' in argv) == (section == 'bootstrap')
                    assert ('-g' in argv) == (section == 'bootstrap')
    check('actual_dropbear_init_device_binds_each_entry_and_never_wildcards_pending_dhcp', direct_dropbear_binding)

    for name in ['network', 'firewall', 'dhcp', 'dropbear', 'uhttpd']:
        def tamper_template(f, name=name):
            f.call('prepare'); f.activate(); f.call('check-setup')
            with (f.root / 'etc/config' / name).open('a') as stream: stream.write('# altered\n')
            f.call('check-setup', good=False)
        check('reject_tampered_' + name + '_template', tamper_template)

    def pending(f):
        f.call('prepare'); f.activate()
        runtime = json.loads(f.runtime.read_text())
        runtime['network.interface.bootstrap'] = {'up': False, 'pending': True,
            'proto': 'dhcp', 'device': 'br-setup.1'}
        f.runtime.write_text(json.dumps(runtime))
        proof = json.loads(f.call('check-setup').stdout)
        assert proof['mode'] == 'dhcp' and proof['dhcp_ready'] is False
    check('offline_lan4_enrollment_accepts_exact_dhcp_pending_without_lease', pending)

    for name in ['identity', 'identity_mode', 'state', 'wireless', 'runtime_mac', 'runtime_dhcp', 'runtime_ports', 'runtime_lan4', 'unexpected_static_address']:
        def tamper(f, name=name):
            f.call('prepare'); f.activate(); f.call('check-setup')
            if name == 'identity':
                value = f.identity; value['mac'] = '02:00:00:00:00:00'
                f.write('etc/dicttech-ap/bootstrap-identity.json', json.dumps(value).encode())
            elif name == 'identity_mode': (f.root / 'etc/dicttech-ap/bootstrap-identity.json').chmod(0o644)
            elif name == 'state':
                path = f.root / 'etc/dicttech-ap/bootstrap-state.json'
                value = json.loads(path.read_text()); value['mode'] = 'isolated'; path.write_text(json.dumps(value))
            elif name == 'wireless':
                path = f.root / 'etc/config/wireless'; path.write_text(path.read_text().replace('disabled 1', 'disabled 0'))
            elif name == 'runtime_mac': f.write('sys/class/net/br-setup/address', b'02:00:00:00:00:00\n')
            else:
                value = json.loads(f.runtime.read_text())
                if name == 'runtime_dhcp': value['network.interface.bootstrap']['up'] = False
                elif name == 'runtime_ports': value['network.device']['bridge-members'].append('lan4')
                elif name == 'unexpected_static_address':
                    value['network.interface.bootstrap'] = {'up': False, 'pending': True, 'proto': 'dhcp', 'device': 'br-setup.1'}
                    value['kernel_ipv4'] = {'br-setup.1': [{'address': '192.0.2.25', 'mask': 24}]}
                else: value['network.interface.ap_maintenance']['l3_device'] = 'br-setup.1'
                f.runtime.write_text(json.dumps(value))
            f.call('check-setup', good=False)
        check('reject_tampered_' + name, tamper)

    def corrupted_identity(f):
        f.write('etc/dicttech-ap/bootstrap-identity.json', b'{"schema_version":1,"mac":"00:11:22:33:44:55"}\n')
        original = (f.root / 'etc/dicttech-ap/bootstrap-identity.json').read_bytes()
        f.call('prepare'); f.activate()
        assert json.loads(f.call('check-setup').stdout)['mode'] == 'isolated'
        assert (f.root / 'etc/dicttech-ap/bootstrap-identity.json').read_bytes() == original
    check('invalid_persistent_identity_is_not_replaced_or_used_on_uplink', corrupted_identity)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
