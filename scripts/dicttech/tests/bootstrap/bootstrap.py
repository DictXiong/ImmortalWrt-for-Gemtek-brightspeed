"""Exercise first-boot decisions with inert private config/init/nft/uci fixtures."""
import hashlib,json,os,pathlib,re,shlex,shutil,subprocess,tempfile
R=pathlib.Path(os.environ['DICTTECH_AP_TEST_SOURCE_ROOT']).resolve()
SRC=R/'package/dicttech/ap-profile/files/usr/lib/dicttech-ap'
SHARE=R/'package/dicttech/ap-profile/files/usr/share/dicttech-ap'
CONFIGS=['network','wireless','firewall','dhcp','system','dropbear','uhttpd','xr_roaming','fan','cpufreq','airoha_npu']
report=[]

def case(name):
 with tempfile.TemporaryDirectory(prefix='dt-ap-bootstrap-', dir='/tmp') as d:
  f=pathlib.Path(d)
  for p in ['etc/config','etc/dicttech-ap','etc/dropbear','etc/sysctl.d','etc/init.d','etc/rc.d','etc/uci-defaults','lib/functions','proc/sys/kernel/random','root','tmp','tmp/.uci','bin','usr/lib/dicttech-ap','usr/libexec/dicttech-ap']:(f/p).mkdir(parents=True)
  (f/'proc/sys/kernel/random/boot_id').write_text('00000000-0000-4000-8000-000000000001\n')
  shutil.copytree(SHARE,f/'usr/share/dicttech-ap');shutil.copytree(SRC/'setup',f/'usr/lib/dicttech-ap/setup')
  for n in CONFIGS:
   (f/'etc/config'/n).write_text('ORIGINAL-'+n+'\n')
   (f/'tmp/.uci'/n).write_text('MANAGED-DELTA-'+n+'\n')
  (f/'tmp/.uci/unrelated').write_text('UNRELATED-PENDING-DELTA\n')
  shutil.copy(SHARE/'templates/guest-guard.nft',f/'etc/xr-guest-vlan192.nft');shutil.copy(SHARE/'templates/13-nf-bridge.conf',f/'etc/sysctl.d/13-nf-bridge.conf')
  (f/'etc/dicttech-ap/root-password-hash').write_text('$6$fixture$fixture\n')
  (f/'etc/shadow').write_text('root:!:1:0:99999:7:::\n');(f/'etc/dropbear/authorized_keys').write_text('fixture-key\n')
  marker={'schema_version':1,'profile':'dicttech-ap-v1','device_id':'fixture','defaults_sha256':hashlib.sha256((SHARE/'defaults.json').read_bytes()).hexdigest(),'config_fingerprint':'a'*64}
  if name!='fresh':(f/'etc/dicttech-ap/device.json').write_text(json.dumps(marker))
  def manifest():
   rels=['etc/config/'+n for n in CONFIGS]+['etc/sysctl.d/13-nf-bridge.conf','etc/xr-guest-vlan192.nft','etc/dicttech-ap/device.json','etc/dicttech-ap/root-password-hash']
   if name=='empty-authorized-keys':
    (f/'etc/dropbear/authorized_keys').write_text('');rels.append('etc/dropbear/authorized_keys')
   value={'schema_version':1,'profile':'dicttech-ap-v1','files':[{'path':p,'sha256':hashlib.sha256((f/p).read_bytes()).hexdigest()}for p in rels]}
   (f/'etc/dicttech-ap/managed-files.json').write_text(json.dumps(value))
  if name!='fresh':manifest()
  def tool(n,text):
   p=f/'bin'/n;p.write_text('#!/bin/sh\n'+text);p.chmod(0o755)
  log=f/'commands'
  values={'network.lan.proto':'dhcp','network.lan.device':'br-lan.1','network.bridge.vlan_filtering':'1','network.guest_vlan.vlan':'192','network.guest.proto':'none','network.ap_maintenance.proto':'static','firewall.guest.input':'DROP',**{'wireless.'+n:'wifi-iface'for n in ['dicttech_mlo','default_radio1','guest_mlo','wifinet1']}}
  text=f'printf "%s\\n" "$*" >>"{log}"\n# Isolated setup has no managed network.lan; mirror that invariant.\nif [ "$*" = "-q get network.lan.proto" ] && [ -f "{f}/tmp/dicttech-ap-bootstrap-isolated" ]; then exit 1; fi\ncase "$*" in\n'
  for k,v in values.items():text+=' "-q get '+k+'") printf "%s\\n" "'+v+'";;\n'
  text+=' *"show wireless"*) printf "%s\\n" "wireless.radio0=wifi-device" "wireless.radio1=wifi-device" "wireless.radio2=wifi-device" "wireless.main=wifi-iface";;\nesac\n'
  tool('uci',text)
  tool('nft',f'printf "nft %s\\n" "$*" >>"{log}"\ncase "$*" in "list table"*) exit 1;; esac\ncat >/dev/null\n')
  if name=='backup-failure':tool('tar','exit 1\n')
  p=f/'usr/libexec/dicttech-ap/transaction';p.write_text('#!/bin/sh\nprintf "sync-root-password\\n" >>"'+str(log)+'"\n');p.chmod(0o755)
  p=f/'usr/lib/dicttech-ap/services.sh';p.write_text('#!/bin/sh\nprintf "services %s\\n" "$*" >>"'+str(log)+'"\n');p.chmod(0o755)
  script=(SRC/'bootstrap.sh').read_text();script=re.sub(r'/(?:usr/share|usr/libexec|usr/lib|etc|root|tmp)/',lambda m:str(f/m.group(0).strip('/'))+'/',script)
  script=script.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin','PATH='+shlex.quote(str(f/'bin')+':'+os.environ['PATH'])).replace('-C / ','-C '+str(f)+' ').replace('"/$name"','"'+str(f)+'/$name"').replace('destination=/$name','destination='+str(f)+'/$name').replace('base=${2:-}', 'base=${2:-'+str(f)+'}').replace('/proc/sys/kernel/random/boot_id',str(f/'proc/sys/kernel/random/boot_id'))
  p=f/'bootstrap.sh';p.write_text(script);p.chmod(0o755)
  def run(command):return subprocess.run([os.environ['DICTTECH_AP_TEST_SHELL'],str(p),command],text=True,capture_output=True)
  if name.startswith('boot-twice-'):
   persistent_boot_case(name,f,p,log)
   report.append({'case':name,'passed':True});return
  if name in ['invalid','backup-failure']:(f/'etc/dicttech-ap/device.json').write_text(json.dumps(dict(marker,defaults_sha256='0'*64)))
  if name=='missing-config':(f/'etc/config/wireless').unlink()
  if name=='truncated-config':(f/'etc/config/wireless').write_text('')
  if name=='tampered-config':(f/'etc/config/wireless').write_text('TAMPERED\n')
  out=run('prepare')
  if name in ['valid','normalization','steady-gui','new-manifest','corrupt-snapshot','empty-authorized-keys','delta-clear-failure','restore-copy-failure']:
   assert out.returncode==0,out.stderr
   assert (f/'etc/config/network').read_text()=='ORIGINAL-network\n'
   if name=='normalization':(f/'etc/config/network').write_text('NORMALIZED-network\n')
   if name=='delta-clear-failure':
    tool('rm', 'case "$*" in *"/.uci/network") exit 1;; esac\nexec '+shlex.quote(shutil.which('rm'))+' "$@"\n')
   if name=='restore-copy-failure':
    tool('cp', 'case "$*" in *".dicttech-firstboot."*) exit 1;; esac\nexec '+shlex.quote(shutil.which('cp'))+' "$@"\n')
   if name in ['delta-clear-failure','restore-copy-failure']:
    out=run('finalize');assert out.returncode!=0
    assert not (f/'etc/dicttech-ap/accepted.json').exists()
    assert (f/'tmp/.uci/unrelated').read_text()=='UNRELATED-PENDING-DELTA\n'
    if name=='delta-clear-failure':assert 'nft -f -' in log.read_text()
    else:assert "option device 'lan4'" in (f/'etc/config/network').read_text()
    report.append({'case':name,'passed':True});return
   if name=='corrupt-snapshot':
    (f/'tmp/dicttech-ap-firstboot-snapshot/files/etc/config/network').write_text('CORRUPTED-RAM\n')
    out=run('finalize');assert out.returncode!=0
    assert "option device 'lan4'" in (f/'etc/config/network').read_text()
    assert not (f/'etc/dicttech-ap/accepted.json').exists()
    report.append({'case':name,'passed':True});return
   out=run('finalize');assert out.returncode==0,out.stderr
   assert (f/'etc/dicttech-ap/accepted.json').is_file()
   if name=='normalization':
    assert (f/'etc/config/network').read_text()=='ORIGINAL-network\n'
    assert all(not (f/'tmp/.uci'/n).exists() for n in CONFIGS)
    assert (f/'tmp/.uci/unrelated').read_text()=='UNRELATED-PENDING-DELTA\n'
   assert log.read_text().count('sync-root-password')==1
   if name=='empty-authorized-keys':assert (f/'etc/dropbear/authorized_keys').read_bytes()==b''
   if name=='steady-gui':
    (f/'etc/config/wireless').write_text('GUI-CHANGED\n');(f/'etc/shadow').write_text('root:GUI-CHANGED:1:0:99999:7:::\n')
    assert run('prepare').returncode==0
    assert run('finalize').returncode==0
    assert (f/'etc/config/wireless').read_text()=='GUI-CHANGED\n'
    assert log.read_text().count('sync-root-password')==1
   if name=='new-manifest':
    (f/'etc/dicttech-ap/root-password-hash').write_text('$6$new-fixture$new-fixture\n');manifest()
    out=run('finalize');assert out.returncode==0,out.stderr
    assert log.read_text().count('sync-root-password')==2
  elif name=='backup-failure':
   assert out.returncode!=0
   assert (f/'etc/config/network').read_text()=='ORIGINAL-network\n'
   assert 'nft -f -' in log.read_text()
  else:
   assert out.returncode==0,out.stderr
   assert "option device 'lan4'" in (f/'etc/config/network').read_text()
   assert "list listen_http '192.168.250.1:80'" in (f/'etc/config/uhttpd').read_text()
   assert 'wireless.radio0.disabled=1' in log.read_text()
   if name!='fresh':
    archives=list((f/'root/dicttech-ap-quarantine').glob('*.tar.gz'));assert len(archives)==1
    x=subprocess.run(['tar','-xOzf',str(archives[0]),'etc/config/network'],text=True,capture_output=True);assert x.stdout=='ORIGINAL-network\n'
  report.append({'case':name,'passed':True})
def persistent_boot_case(name,f,bootstrap,log):
 """Use actual rc.common enabling/dispatch, without executing host init services."""
 init=R/'package/dicttech/ap-profile/files/etc/init.d'
 rccommon=R/'package/base-files/files/etc/rc.common'
 shell=os.environ['DICTTECH_AP_TEST_SHELL']
 env=dict(os.environ,IPKG_INSTROOT=str(f))
 # rc.common's enable/boot paths need only this shell list helper; all actual
 # service/procd functions are intentionally absent in the isolated fixture.
 (f/'lib/functions.sh').write_text('list_contains() { local value; eval "value=\\${$1}"; case " $value " in *" $2 "*) return 0;; *) return 1;; esac; }\n')
 (f/'lib/functions/service.sh').write_text('# inert local rc.common fixture\n')
 def start_number(path):
  match=re.search(r'^START=(\d+)$',path.read_text(),re.M)
  assert match,'missing explicit START: '+str(path)
  return int(match.group(1))
 starts={
  'transaction':start_number(init/'dicttech-ap-transaction'),
  'prepare':start_number(init/'dicttech-ap-prepare'),
  'boot':start_number(R/'package/base-files/files/etc/init.d/boot'),
  'finalize':start_number(init/'dicttech-ap-finalize'),
  'cpufreq':start_number(R/'package/emortal/cpufreq/files/cpufreq.init'),
  'network':start_number(R/'package/network/config/netifd/files/etc/init.d/network'),
  'dropbear':start_number(R/'package/network/services/dropbear/files/dropbear.init'),
  'uhttpd':start_number(R/'package/network/services/uhttpd/files/uhttpd.init'),
 }
 assert starts['transaction']<starts['prepare']<starts['boot']<starts['finalize']
 assert starts['finalize']<min(starts[n]for n in ['cpufreq','network','dropbear','uhttpd'])
 def dispatch(path,action):
  result=subprocess.run([shell,str(rccommon),str(path),action],env=env,text=True,capture_output=True)
  assert result.returncode==0,(action,result.stderr)
 for phase in ['prepare','finalize']:
  source=init/('dicttech-ap-'+phase)
  text=source.read_text()
  assert text.startswith('#!/bin/sh /etc/rc.common\n')
  assert 'USE_PROCD' not in text and 'procd_' not in text
  text=text.replace('/usr/lib/dicttech-ap/bootstrap.sh',str(bootstrap))
  target=f/'etc/init.d'/source.name;target.write_text(text);target.chmod(0o755)
  dispatch(target,'enable')
  assert (f/'etc/rc.d'/('S%02d%s'%(starts[phase],source.name))).is_symlink()
 # default_postinst enables and starts installed init scripts. Their ordinary
 # start actions must be harmless in either package file order, including after
 # boot where a stray prepare would leave a boot-defer marker behind.
 def runtime_start_has_no_effect():
  before={str(path.relative_to(f)):path.read_bytes() for path in f.rglob('*') if path.is_file() and not path.is_symlink()}
  for phase in ['finalize','prepare','prepare','finalize']:
   dispatch(f/'etc/init.d'/('dicttech-ap-'+phase),'start')
  after={str(path.relative_to(f)):path.read_bytes() for path in f.rglob('*') if path.is_file() and not path.is_symlink()}
  assert after==before,'ordinary init start changed configuration or boot state'
 runtime_start_has_no_effect()
 # The package has no transient bootstrap entry: only these persistent hooks
 # may accept a preseed, after S10's entire uci-defaults/global commit phase.
 assert not (R/'package/dicttech/ap-profile/files/etc/uci-defaults/99-dicttech-ap-bootstrap').exists()
 defaults=f/'etc/uci-defaults/11-fixture-normalize'
 defaults.write_text('# one-use stock normalization fixture\n')
 legacy=f/'etc/uci-defaults/99-dicttech-ap-provision'
 legacy.write_text('#!/bin/sh\n'+shlex.quote(str(bootstrap))+' finalize\n')
 accepted=f/'etc/dicttech-ap/accepted.json'
 def boot_cycle(first):
  for stage in sorted(['transaction','prepare','boot','finalize','cpufreq','network','dropbear','uhttpd'],key=lambda n:starts[n]):
   if stage in ['prepare','finalize']:
    dispatch(f/'etc/init.d'/('dicttech-ap-'+stage),'boot')
   elif stage=='boot':
    if defaults.exists():
     assert not accepted.exists(),'preseed accepted before all stock defaults'
     (f/'etc/config/network').write_text('STOCK-NORMALIZED-network\n')
     (f/'tmp/.uci/network').write_text('STOCK-PENDING-network\n')
     defaults.unlink() # boot removes successful one-use uci-defaults scripts
    if legacy.exists():
     result=subprocess.run([shell,str(legacy)],text=True,capture_output=True)
     assert result.returncode==0,result.stderr
     assert not accepted.exists(),'old export shim bypassed boot deferral'
     legacy.unlink()
    if first:assert not accepted.exists(),'preseed accepted inside S10'
   elif stage=='cpufreq':
    # This is the first consumer after S13; malformed second-boot state must
    # already be isolated, before any management/network service starts.
    isolated=(f/'tmp/dicttech-ap-bootstrap-isolated').exists()
    assert isolated==(not first and name!='boot-twice-gui'),(name,first,isolated)
  if first:
   assert accepted.exists()
   assert (f/'etc/config/network').read_text()=='ORIGINAL-network\n'
   assert not (f/'tmp/.uci/network').exists()
 boot_cycle(True)
 runtime_start_has_no_effect()
 assert log.read_text().count('sync-root-password')==1
 assert not any((f/'etc/uci-defaults').iterdir())
 # A real reboot loses /tmp and has a distinct boot ID; accepted profile and
 # ROM rc links survive, unlike the deleted uci-defaults scripts.
 shutil.rmtree(f/'tmp');(f/'tmp/.uci').mkdir(parents=True)
 (f/'proc/sys/kernel/random/boot_id').write_text('00000000-0000-4000-8000-000000000002\n')
 shadow='root:GUI-CHANGED:1:0:99999:7:::\n'
 (f/'etc/shadow').write_text(shadow)
 if name=='boot-twice-gui':(f/'etc/config/wireless').write_text('GUI-CHANGED\n')
 elif name=='boot-twice-missing-config':(f/'etc/config/wireless').unlink()
 elif name=='boot-twice-corrupt-guard':(f/'etc/xr-guest-vlan192.nft').write_text('INVALID-GUARD\n')
 else:raise AssertionError('unknown persistent boot fixture')
 boot_cycle(False)
 runtime_start_has_no_effect()
 assert log.read_text().count('sync-root-password')==1,'ordinary boot must not reset GUI password'
 assert (f/'etc/shadow').read_text()==shadow
 if name=='boot-twice-gui':
  assert (f/'etc/config/wireless').read_text()=='GUI-CHANGED\n'
  assert not (f/'tmp/dicttech-ap-firstboot-snapshot').exists()
 else:
  assert "option device 'lan4'" in (f/'etc/config/network').read_text()
  assert 'wireless.radio0.disabled=1' in log.read_text()
  assert len(list((f/'root/dicttech-ap-quarantine').glob('*.tar.gz')))==1

for name in ['fresh','valid','normalization','steady-gui','new-manifest','invalid','missing-config','truncated-config','tampered-config','backup-failure','corrupt-snapshot','empty-authorized-keys','delta-clear-failure','restore-copy-failure','boot-twice-gui','boot-twice-missing-config','boot-twice-corrupt-guard']:case(name)
print(json.dumps(report,indent=2))
