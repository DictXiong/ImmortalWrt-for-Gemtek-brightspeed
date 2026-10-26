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
  for p in ['etc/config','etc/dicttech-ap','etc/dropbear','etc/sysctl.d','root','tmp','tmp/.uci','bin','usr/lib/dicttech-ap','usr/libexec/dicttech-ap']:(f/p).mkdir(parents=True)
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
  text=f'printf "%s\\n" "$*" >>"{log}"\ncase "$*" in\n'
  for k,v in values.items():text+=' "-q get '+k+'") printf "%s\\n" "'+v+'";;\n'
  text+=' *"show wireless"*) printf "%s\\n" "wireless.radio0=wifi-device" "wireless.radio1=wifi-device" "wireless.radio2=wifi-device" "wireless.main=wifi-iface";;\nesac\n'
  tool('uci',text)
  tool('nft',f'printf "nft %s\\n" "$*" >>"{log}"\ncase "$*" in "list table"*) exit 1;; esac\ncat >/dev/null\n')
  if name=='backup-failure':tool('tar','exit 1\n')
  p=f/'usr/libexec/dicttech-ap/transaction';p.write_text('#!/bin/sh\nprintf "sync-root-password\\n" >>"'+str(log)+'"\n');p.chmod(0o755)
  p=f/'usr/lib/dicttech-ap/services.sh';p.write_text('#!/bin/sh\nprintf "services %s\\n" "$*" >>"'+str(log)+'"\n');p.chmod(0o755)
  script=(SRC/'bootstrap.sh').read_text();script=re.sub(r'/(?:usr/share|usr/libexec|usr/lib|etc|root|tmp)/',lambda m:str(f/m.group(0).strip('/'))+'/',script)
  script=script.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin','PATH='+shlex.quote(str(f/'bin')+':'+os.environ['PATH'])).replace('-C / ','-C '+str(f)+' ').replace('"/$name"','"'+str(f)+'/$name"').replace('destination=/$name','destination='+str(f)+'/$name').replace('base=${2:-}', 'base=${2:-'+str(f)+'}')
  p=f/'bootstrap.sh';p.write_text(script)
  def run(command):return subprocess.run([os.environ['DICTTECH_AP_TEST_SHELL'],str(p),command],text=True,capture_output=True)
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
for name in ['fresh','valid','normalization','steady-gui','new-manifest','invalid','missing-config','truncated-config','tampered-config','backup-failure','corrupt-snapshot','empty-authorized-keys','delta-clear-failure','restore-copy-failure']:case(name)
print(json.dumps(report,indent=2))
