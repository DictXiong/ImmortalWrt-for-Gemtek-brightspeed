#!/usr/bin/env python3
"""Inert two-rate/cache regression. No AP or network access."""
from pathlib import Path
import fcntl,hashlib,json,os,re,resource,shlex,shutil,socket,subprocess,time
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
T=Path(__file__).resolve().parent;R=T.parent;S=R/'helper-v4'
os.environ['XR_ONLY_DELTA']='1'
ns={'__file__':str(T/'targeted.py'),'__name__':'fixture_definitions'}
exec(compile((T/'targeted.py').read_text().split("record('bulk_config_fields_preserve_SSID_bytes_and_no_eval'")[0],str(T/'targeted.py'),'exec'),ns)
Multi=ns['Multi'];until=ns['until'];C7=ns['C7'];SHELL=ns['g']['SHELL'];QEMU=ns['QEMU'];ROOT=ns['ROOT']
pins={n:hashlib.sha256((S/n).read_bytes()).hexdigest()for n in ['helper.sh','init.sh','common.jq','cli-report.jq']}
results=[];OUT=R/'cache-tests-result.json'
def save():OUT.write_text(json.dumps({'sourceSHA':pins,'scope':'Local inert cache fixtures only; no AP calls.','results':results,'passed':all(x['passed']for x in results)},indent=2)+'\n')
def record(name,fn):
 try:v=fn()
 except Exception as e:results.append({'name':name,'passed':False,'error':repr(e)});save();raise
 results.append({'name':name,'passed':True,'details':v});save();print(name+' PASS',flush=True)
class Cache(Multi):
 def __init__(self,profile='iot',shell=None):
  super().__init__();self.profile=profile;self.shell=shell or SHELL;self.daemon=None;self.log=None
  self.commands=self.p/'commands';cli=self.bin/'cli'
  cli.write_text('#!'+shutil.which('python3')+'\n'+f'''import sys,json
a=sys.argv;s=a[a.index("-i")+1];command=a[-1]
with open({str(self.cli_log)!r},"a")as f:f.write(s+"\\n")
with open({str(self.commands)!r},"a")as f:f.write(json.dumps({{"socket":s,"command":command}})+"\\n")
d=json.load(open({str(self.datafile)!r}))["radio"];text=d[s][command]
if command=="show_neighbor":
 obj="hostapd."+(s.split("_link")[0]if "_link"in s else s)
 for row in json.load(open({str(self.backend)!r})).get(obj,[]):
  text+=row[0]+" ssid="+row[1].encode().hex()+" nr="+row[2]+"\\n"
print(text,end="")
''');cli.chmod(0o700)
  self.gate=self.p/'release';self.counter=self.p/'sleep-count';self.gate.write_text('0')
  sleeper=self.bin/'gated-sleep'
  sleeper.write_text('#!'+shutil.which('python3')+'\n'+f'''from pathlib import Path
import time
c=Path({str(self.counter)!r});n=int(c.read_text())+1 if c.exists()else 1;c.write_text(str(n))
while int(Path({str(self.gate)!r}).read_text())<n:time.sleep(.01)
''');sleeper.chmod(0o700)
  text=self.helper.read_text();assert 'sleep "$delay" 9>&- &'in text
  self.helper.write_text(text.replace('sleep "$delay" 9>&- &',shlex.quote(str(sleeper))+' "$delay" 9>&- &'))
 def count(self,command):return sum(json.loads(x)['command']==command for x in self.commands.read_text().splitlines())if self.commands.exists()else 0
 def writes(self):return len(self.actions.read_text().splitlines())if self.actions.exists()else 0
 def view(self):
  q=subprocess.run(self.shell+[str(self.helper),'status',self.profile],capture_output=True,timeout=10)
  assert q.returncode==0,q.stderr.decode();return json.loads(q.stdout)
 def start(self,seed=False):
  if seed:
   x=json.loads((self.http/(self.profile+'.json')).read_text())
   self.backend.write_text(json.dumps({'hostapd.'+ns['g']['profiles'][self.profile]['iface']:[[r['bssid'],r['ssid'],r['nr']]for r in x['reports']]}))
  self.log=(self.p/'daemon.log').open('wb')
  self.daemon=subprocess.Popen(self.shell+[str(self.helper),'daemon',self.profile],stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,cwd=self.p)
  until(lambda:self.counter.exists()and self.counter.read_text()=='1',18)
  assert self.daemon.poll()is None
  assert self.pub(self.profile).exists(),'first heartbeat absent under DEVNULL stdin'
  return self.view()
 def step(self,tick,peer=True):
  index=int(self.counter.read_text());self.at(tick)
  self.uuid.write_text('33333333-3333-4333-8333-'+f'{tick:012x}'+'\n')
  if peer:self.write_peer(self.profile,'11111111-1111-4111-8111-'+f'{tick:012x}')
  self.gate.write_text(str(index))
  until(lambda:self.counter.read_text()==str(index+1),18)
  assert self.daemon.poll()is None
  return self.view()
 def close(self):
  if self.daemon and self.daemon.poll()is None:
   self.daemon.terminate()
   try:self.daemon.wait(timeout=12)
   except subprocess.TimeoutExpired:self.daemon.kill();self.daemon.wait(timeout=3)
  if self.log:self.log.close()
  super().close()
def steady_and_invalidation():
 m=Cache()
 try:
  x=m.start(seed=True);assert x['state']=='ok'and m.writes()==1
  assert json.loads(m.pub('iot').read_text())['version']==1
  show=m.count('show_neighbor');reads=m.uci_log.read_text();old=json.loads(m.pub('iot').read_text())['revision']
  for values in m.radio.values():
   values['status']=re.sub(r'(num_sta\[\d+\]=)\d+',r'\g<1>99',values['status'])
   values['status']=re.sub(r'(chan_util_avg=)\d+',r'\g<1>201',values['status'])
  m.save_radio();a=m.step(130)
  assert m.count('show_neighbor')==show and m.writes()==1 and m.uci_log.read_text()==reads
  assert json.loads(m.pub('iot').read_text())['revision']!=old
  assert len(m.curl_log.read_text().splitlines())==1
  m.step(160);assert len(m.curl_log.read_text().splitlines())==2 and m.writes()==1
  m.pidfile.write_text('5000 5001\n');m.step(190)
  assert m.count('show_neighbor')==show+1 and m.writes()==2,'generation must seed shared MLD cache even matching observed'
  path=m.ctrl/'phy0.0-ap1';path.unlink();sock=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);sock.bind(str(path));m.sockets.append(sock)
  m.step(220);assert m.count('show_neighbor')==show+2 and m.writes()==3
  (m.deltadir/'xr_roaming').write_text('pending metadata delta\n');m.step(250)
  assert m.count('show_neighbor')==show+3
  (m.deltadir/'xr_roaming').unlink();(m.state/'reload.token').write_text('reload-one\n');m.step(280)
  c=m.count('show_neighbor');(m.state/'reload.token').write_text('reload-two\n');m.step(310);assert m.count('show_neighbor')==c+1
  value='家用%"\\';m.rename('iot',value);m.step(340)
  assert json.loads(m.pub('iot').read_text())['ssid']==value
  saved=m.config.read_bytes();m.profiles['iot']['enabled']='0';m.save_multi();b=m.step(370)
  assert b['state']=='error'and not m.pub('iot').exists(),'invalid config must withdraw cached heartbeat'
  c=m.count('show_neighbor');m.config.write_bytes(saved);m.profiles['iot']['enabled']='1';m.step(400)
  assert m.count('show_neighbor')==c+1 and m.pub('iot').exists(),'exact old config restore must revalidate'
  c=m.count('show_neighbor');st=m.radio['phy0.0-ap1']['status'];m.radio['phy0.0-ap1']['status']=st.replace('state=ENABLED','state=DISABLED');m.objects['hostapd.phy0.0-ap1']['status']='DISABLED';m.save_radio();m.step(430)
  assert not m.pub('iot').exists(),'all-invalid radios must not publish cached reports'
  m.radio['phy0.0-ap1']['status']=st;m.objects['hostapd.phy0.0-ap1']['status']='ENABLED';m.save_radio();m.step(460)
  assert m.count('show_neighbor')==c+1 and m.pub('iot').exists(),'radio recovery must collect strict reports again'
  c=m.count('show_neighbor');writes=m.writes();m.backend.write_text('{}')
  for tick in range(490,760,30):m.step(tick)
  assert m.count('show_neighbor')==c and m.writes()==writes
  z=m.step(760);assert m.count('show_neighbor')==c+1 and m.writes()==writes+1 and len(m.table('iot'))==1
  return {'first_DEVNULL_heartbeat':True,'client_count_does_not_invalidate':True,'fetch_at60_not30':True,'stable_heartbeat_without_NR_writes':True,'PID_socket_pending_reload_config_invalidate':True,'invalid_config_and_radio_withdraw_and_revalidate':True,'UTF8_quoted_cached_SSID':True,'full300audit_repairs_actual_cleared_NR':True,'final_cache':z['cache']}
 finally:m.close()
record('steady_cache_and_generation_config_radio_audit_invalidation',steady_and_invalidation)
def frozen_peer():
 m=Cache()
 try:
  m.start();first=m.writes()
  for tick in [130,160,190,220,250]:m.step(tick,peer=False)
  assert len(m.table('iot'))==1 and m.writes()==first
  x=m.step(280,peer=False);assert not x['peer']['fresh']and m.table('iot')==[]and m.writes()==first+1
  m.step(310,peer=False);assert m.writes()==first+1,'expired empty writes should not repeat'
  y=m.step(340,peer=True);assert y['peer']['fresh']and len(m.table('iot'))==1
  return {'HTTP_same_UUID_does_not_extend180TTL':True,'expiry_writes_empty_once':True,'fresh_revision_recovers':True}
 finally:m.close()
record('frozen_peer_expiry_and_recovery',frozen_peer)
def partial_mlo():
 m=Cache('main')
 try:
  m.start();path=m.ctrl/'ap-mld1_link2';path.unlink();m.step(130)
  assert len(json.loads(m.pub('main').read_text())['reports'])==2,'only validated live subset may publish'
  for name in m.radio:
   if name.startswith('ap-mld1_link'):m.radio[name]['status']=m.radio[name]['status'].replace('state=ENABLED','state=DISABLED')
  m.objects['hostapd.ap-mld1']['status']='DISABLED';m.save_radio();m.step(160)
  assert not m.pub('main').exists()
  for name in m.radio:
   if name.startswith('ap-mld1_link'):m.radio[name]['status']=m.radio[name]['status'].replace('state=DISABLED','state=ENABLED')
  m.objects['hostapd.ap-mld1']['status']='ENABLED';m.save_radio()
  sock=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);sock.bind(str(path));m.sockets.append(sock);m.step(190)
  assert len(json.loads(m.pub('main').read_text())['reports'])==3
  return {'partial_MLD_publishes_only_strict_valid_subset':True,'all_failed_MLD_withdrawn':True,'restored3links_recollected':True}
 finally:m.close()
record('partial_MLD_and_all_failed_radio_cache_safety',partial_mlo)
def c7_cached_path():
 m=Cache(shell=C7)
 try:
  wrapper=m.bin/'c7-cache-jq';wrapper.write_text('#!/bin/sh\nexec '+shlex.join([QEMU,'-L',str(ROOT),str(ROOT/'usr/bin/jq')])+' "$@"\n');wrapper.chmod(0o700)
  m.helper.write_text(re.sub(r'^JQ=.*$','JQ='+shlex.quote(str(wrapper)),m.helper.read_text(),flags=re.M))
  x=m.start(seed=True);assert x['state']=='ok'and m.writes()==1
  show=m.count('show_neighbor');m.step(130)
  assert m.count('show_neighbor')==show and m.writes()==1
  y=m.step(160);assert y['peer']['fresh']and m.writes()==1
  m.pidfile.write_text('6000 6001\n');z=m.step(190)
  assert m.count('show_neighbor')==show+1 and m.writes()==2
  return {'actual_C7_ash_and_slim_jq':True,'DEVNULL_first_and_cached_heartbeat':True,'30check60fetch_no_redundant_write':True,'new_generation_seeds_list':True}
 finally:m.close()
if ROOT: record('actual_C7_cached_daemon_path',c7_cached_path)
else: print('SKIP actual C7 cached path: supply --c7-rootfs',flush=True)
assert pins=={n:hashlib.sha256((S/n).read_bytes()).hexdigest()for n in pins},'runtime source changed during suite'
save();print(json.dumps({'passed':True,'new_cache_cases':len(results),'result':str(OUT)}))
