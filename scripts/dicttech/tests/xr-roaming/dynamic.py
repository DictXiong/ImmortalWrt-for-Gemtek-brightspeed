#!/usr/bin/env python3
"""Dynamic ESS/MLD/SLO, namespace, lifecycle and peer-cache fixtures. No AP I/O."""
from pathlib import Path
import json, os, re, shlex, shutil, socket, subprocess

T=Path(__file__).resolve().parent; R=T.parent
ns={'__file__':str(T/'cache.py'),'__name__':'dynamic_fixture_definitions'}
exec(compile((T/'cache.py').read_text().split("record('steady_cache_and_generation_config_radio_audit_invalidation'")[0],str(T/'cache.py'),'exec'),ns)
Cache=ns['Cache']; SHELL=ns['SHELL']; C7=ns['C7']; ROOT=ns['ROOT']; QEMU=ns['QEMU']; g=ns['ns']['g']; S=ns['S']
results=[]

class Dynamic(Cache):
 def __init__(self,extra=True,profile='main',shell=None):
  self.meta=None;super().__init__('main',shell);self.profile=profile
  self.meta={'main':{'iface':'ap-mld0','bands':[0,1,2]},'guest':{'iface':'ap-mld1','bands':[0,1]},'iot':{'iface':'ap-iot','bands':[0]}}
  if extra:self.meta.update({'office':{'iface':'ap-mld2','bands':[1,2]},'sensor':{'iface':'ap-xf00abc12','bands':[1]}})
  self.profiles={};self.wireless={};self.radio={};self.objects={};self.types={};self.peers={}
  for old in self.ctrl.iterdir():old.unlink()
  for index,(name,definition) in enumerate(self.meta.items()):
   iface=definition['iface'];mld=len(definition['bands'])>1;section='ssid_'+name;ssid='ESS-'+name
   definition.update(section=section,ssid=ssid,mld=mld,own=[],remote=[])
   for band in definition['bands']:
    bssid='02:aa:01:00:'+f'{index:02x}:{band+1:02x}';remote='02:aa:02:00:'+f'{index:02x}:{band+1:02x}'
    definition['own'].append(bssid);definition['remote'].append(remote)
    original=(T/'tree/proof'/f'link{band}-status.txt').read_text()
    rows=[line for line in original.splitlines() if not re.match(r'^(bss|bssid|ssid|mld_addr|mld_link_id|mld_id|num_sta)\[',line)]
    rows+=['bss[0]='+iface,'bssid[0]='+bssid,'ssid[0]='+ssid]
    if mld:rows+=['mld_link_id[0]='+str(band)]
    control=iface+'_link'+str(band) if mld else iface
    nr=bssid.replace(':','')+g['own'][band]['nr'][12:]
    self.radio[control]={'status':'\n'.join(rows)+'\n','show_neighbor':bssid+' ssid='+ssid.encode().hex()+' nr='+nr+'\n'}
    sock=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);sock.bind(str(self.ctrl/control));self.sockets.append(sock)
   self.wireless[section]=ssid
   self.objects['hostapd.'+iface]={'ssid':ssid,'bssid':definition['own'][0],'status':'ENABLED'}
   self.profiles[name]={'enabled':'1','section':section,'iface':iface,'mlo':'1'if mld else'0','max_links':str(len(definition['bands'])),
    'peer':'10.24.0.1','peer_mac':'02:00:00:00:00:03','peer_bssid':' '.join(definition['remote']),'interval':'30','ttl':'180'}
   self.write_peer(name)
  self.save_multi();self.save_radio();self.backend.write_text('{}')
  py=shutil.which('python3')
  (self.bin/'uci').write_text('#!'+py+'\n'+f'''import json,sys
a=sys.argv;k=a[-1];v=json.load(open({str(self.config)!r}))
with open({str(self.uci_log)!r},"a")as f:f.write(k+"\\n")
if "show" in a:
 if k=="xr_roaming":
  for name in v["profiles"]:print("xr_roaming."+name+"=roaming")
  for name in v["peers"]:print("xr_roaming."+name+"=peer")
 else:print("network.@device[0]=device")
elif k=="network.@device[0].name":print("br-lan")
elif k=="network.@device[0].macaddr":print("02:00:00:00:00:02")
elif k.startswith("xr_roaming."):
 p=k.split(".");name=p[1]
 if len(p)==2:print("roaming"if name in v["profiles"]else"peer"if name in v["peers"]else"")
 else:print((v["profiles"].get(name)or v["peers"].get(name,{{}})).get(p[2],""))
elif k.startswith("wireless."):
 p=k.split(".");print(v["types"].get(p[1],"wifi-iface")if len(p)==2 else v["wireless"].get(p[1],""))
else:sys.exit(1)
''')
  (self.bin/'curl').write_text('#!'+py+'\n'+f'''import json,sys,pathlib,urllib.parse,shutil
a=sys.argv;u=urllib.parse.urlsplit(a[-1]);assert u.scheme=="http"and u.hostname in ["10.24.0.1","10.24.0.10","10.24.0.11"]
profile="main"if u.path=="/xr-roaming/own.json"else u.path.split("/")[2]
assert u.path==("/xr-roaming/own.json"if profile=="main"else"/xr-roaming/"+profile+"/own.json")
suffix=""if u.hostname=="10.24.0.1"else"."+("peer_a"if u.hostname.endswith(".10")else"peer_b")
p=pathlib.Path({str(self.http)!r})/(profile+suffix+".json")
with open({str(self.curl_log)!r},"a")as f:f.write(profile+suffix+"\\n")
if not p.exists():sys.exit(22)
shutil.copyfile(p,a[a.index("--output")+1])
''')
  if ROOT:
   wrapper=self.bin/'slim-jq';wrapper.write_text('#!/bin/sh\nexec '+shlex.join([QEMU,'-L',str(ROOT),str(ROOT/'usr/bin/jq')])+' "$@"\n');wrapper.chmod(0o700)
   self.helper.write_text(re.sub(r'^JQ=.*$','JQ='+shlex.quote(str(wrapper)),self.helper.read_text(),flags=re.M))
 def save_multi(self):
  if self.meta is None:return super().save_multi()
  self.config.write_text(json.dumps({'profiles':self.profiles,'wireless':self.wireless,'types':self.types,'peers':self.peers}))
 def write_peer(self,profile,revision='11111111-1111-4111-8111-111111111111'):
  if self.meta is None:return super().write_peer(profile,revision)
  meta=self.meta[profile];reports=[]
  for band,bssid in zip(meta['bands'],meta['remote']):reports.append({'bssid':bssid,'ssid':meta['ssid'],'nr':bssid.replace(':','')+g['own'][band]['nr'][12:]})
  (self.http/(profile+'.json')).write_text(json.dumps({'version':1,'bridge_mac':'02:00:00:00:00:03','ssid':meta['ssid'],'revision':revision,'reports':reports}))
 def state_path(self,profile):
  return self.p/'state'if profile=='main'else self.p/'state'/(profile if profile in ['guest','iot','battalion']else'profile-'+profile)
 def table(self,profile):return json.loads(self.backend.read_text()).get('hostapd.'+self.meta[profile]['iface'],[])
 def call(self,mode='once',profile=None,ok=True):
  args=[str(self.helper),mode]+([]if mode=='profiles'else[profile or self.profile])
  q=subprocess.run(self.shell+args,capture_output=True,timeout=18,cwd=self.p)
  assert (q.returncode==0)==ok,(mode,profile,q.returncode,q.stderr.decode());return q

def check(name,fn):
 details=fn();results.append({'name':name,'passed':True,'details':details});print(name+' PASS',flush=True)

def default_only():
 m=Dynamic(extra=False,shell=C7 if ROOT else SHELL)
 try:
  assert m.call('profiles').stdout.decode().splitlines()==['main','guest','iot']
  for profile,meta in m.meta.items():
   m.call(profile=profile);assert len(m.table(profile))==len(meta['bands'])
  return {'zero_extras':True,'main_mld0_guest_mld1_iot_explicit_SLO':True,'target_C7':bool(ROOT)}
 finally:m.close()

def multiple_links_and_slo():
 m=Dynamic(shell=C7 if ROOT else SHELL)
 try:
  for profile in m.meta:m.call(profile=profile)
  assert len(m.table('office'))==2 and len(m.table('sensor'))==1
  links=json.loads((m.state_path('office')/'links.json').read_text());assert [x['link_id']for x in links]==[1,2]
  assert all(x['base']=='ap-mld2'for x in links)
  assert json.loads(m.pub('office').read_text())['version']==1
  # A peer can have a third radio absent on this AP; metadata carries no iface.
  payload=json.loads((m.http/'office.json').read_text());extra=json.loads((m.http/'main.json').read_text())['reports'][0]
  extra['ssid']=m.meta['office']['ssid'];payload['reports'].append(extra)
  m.profiles['office']['peer_bssid']+=' '+extra['bssid'];m.save_multi();(m.http/'office.json').write_text(json.dumps(payload))
  m.call(profile='office');assert len(m.table('office'))==3
  return {'arbitrary_profiles':True,'5_6_GHz_real_link_ids':[1,2],'single_5_GHz':True,'peer_link_count_independent_of_local':True,'public_v1_no_iface_dependency':True}
 finally:m.close()

def metadata_rejection():
 m=Dynamic()
 try:
  saved=dict(m.profiles['office'])
  for change in [{'mlo':''},{'max_links':''},{'mlo':'true'},{'max_links':'4'},{'max_links':'02'},{'mlo':'0','max_links':'2'},{'iface':'ap-mld16'},{'iface':'../../other'},{'iface':'ap-mld123456789012'}]:
   m.profiles['office']={**saved,**change};m.save_multi();m.call('preflight','office',False)
  m.profiles['office']=saved;m.save_multi();m.call('preflight','office')
  main=dict(m.profiles['main'])
  for field in ['section','iface']:
   m.profiles['main']={**main,field:'','ssid':m.meta['main']['ssid']};m.save_multi();m.call('preflight','main',False)
  m.profiles['main']=main;m.save_multi()
  for profile in ['../office','office;echo','1office','a'*49,'x\ny']:m.call('preflight',profile,False)
  assert not m.actions.exists()and not m.curl_log.exists()
  return {'malformed_metadata_and_tokens_fail_before_network_or_apply':True}
 finally:m.close()

def list_bounds_and_namespace():
 m=Dynamic()
 try:
  # Caller-controlled IDs cannot collide with main's lock/object/peer cache.
  for name in ['lock','object','peers','count','result']:
   m.profiles[name]={**m.profiles['sensor'],'section':'ssid_'+name};m.wireless['ssid_'+name]=m.meta['sensor']['ssid']
   m.save_multi();m.call('preflight',name)
   assert m.state_path(name).is_dir()
  m.profiles={('a'+str(i)):{'enabled':'0'} for i in range(17)};m.save_multi();assert m.call('profiles').stdout==b''
  m.profiles['eighteenth']={'enabled':'1'};m.save_multi();assert m.call('profiles',ok=False).stdout==b''
  m.profiles={'a'*48:{'enabled':'1'},'disabled':{'enabled':'0'}};m.save_multi();assert m.call('profiles').stdout.decode().splitlines()==['a'*48]
  m.profiles['disabled']['enabled']='yes';m.save_multi();assert m.call('profiles',ok=False).stdout==b''
  m.profiles['disabled']['enabled']='0'
  m.profiles['bad;command']={'enabled':'1'};m.save_multi();assert m.call('profiles',ok=False).stdout==b''
  return {'limit_17':True,'whole_list_validated_before_emitting':True,'max_48_byte_TOKEN':True,'internal_state_namespace_isolated':True}
 finally:m.close()

def dynamic_procd():
 m=Dynamic()
 try:
  m.profiles['sensor']['enabled']='0';m.save_multi()
  init=m.p/'dynamic-init';init.write_text((S/'init.sh').read_text().replace('/usr/lib/xr-roaming/helper.sh',str(m.helper)))
  output=m.p/'instances'
  script='''uci() { printf '1\\n'; }
procd_open_instance() { printf 'OPEN %s\\n' "$1" >> "$OUT"; }
procd_set_param() { if [ "$1" = command ]; then shift; printf 'COMMAND %s\\n' "$*" >> "$OUT"; fi; }
procd_close_instance() { :; }
. "$INIT"
start_service
'''
  q=subprocess.run(SHELL+['-c',script],env={**os.environ,'INIT':str(init),'OUT':str(output)},capture_output=True,timeout=8)
  assert q.returncode==0,q.stderr.decode();rows=output.read_text().splitlines()
  assert [x for x in rows if x.startswith('OPEN ')]==['OPEN '+p for p in m.profiles if p!='sensor']
  assert not m.actions.exists()and not m.curl_log.exists()
  return {'enabled_dynamic_instances':4,'disabled_SLO_omitted':True,'no_radio_preflight_or_writes':True}
 finally:m.close()

def dynamic_cache_and_expiry():
 m=Dynamic(profile='office',shell=C7 if ROOT else SHELL)
 try:
  m.start();shows=m.count('show_neighbor');writes=m.writes()
  m.step(130,peer=False);assert m.count('show_neighbor')==shows and m.writes()==writes
  # Frozen old-format public metadata ages out despite HTTP success.
  for tick in [160,220,280]:x=m.step(tick,peer=False)
  assert m.table('office')==[]and x['state']=='degraded'
  x=m.step(340);assert x['state']=='ok'and len(m.table('office'))==2
  return {'unchanged_topology_uses_cache':True,'frozen_v1_peer_expires':True,'new_revision_recovers':True,'target_C7':bool(ROOT)}
 finally:m.close()

def dynamic_fleet_expiry():
 m=Dynamic(profile='sensor',shell=C7 if ROOT else SHELL)
 try:
  m.profiles['sensor'].update(peer_mode='fleet',peers='peer_a peer_b')
  original=json.loads((m.http/'sensor.json').read_text());payloads={}
  for i,name in enumerate(['peer_a','peer_b']):
   payload=json.loads(json.dumps(original));payload['bridge_mac']='02:00:00:aa:00:'+str(31+i)
   report=payload['reports'][0];report['bssid']='02:00:00:bb:00:'+str(31+i);report['nr']=report['bssid'].replace(':','')+report['nr'][12:]
   m.peers[name]={'address':'10.24.0.'+str(10+i),'bridge_mac':payload['bridge_mac'],'sensor_bssid':report['bssid']};payloads[name]=payload
  m.save_multi()
  def fresh(name,tick):
   payloads[name]['revision']='11111111-1111-4111-8111-'+f'{tick:012x}'
   (m.http/('sensor.'+name+'.json')).write_text(json.dumps(payloads[name]))
  for name in payloads:fresh(name,100)
  m.start();assert len(m.table('sensor'))==2
  for tick in [160,220,280]:fresh('peer_b',tick);x=m.step(tick,peer=False)
  assert len(m.table('sensor'))==1 and m.table('sensor')[0][0]==payloads['peer_b']['reports'][0]['bssid']
  rows={p['id']:p for p in x['peers']};assert not rows['peer_a']['fresh']and rows['peer_b']['fresh']
  return {'dynamic_SLO_fleet_union':True,'independent_peer_TTL':True,'healthy_peer_retained':True,'target_C7':bool(ROOT)}
 finally:m.close()

for name,fn in [('dynamic_three_presets_without_extras',default_only),('dynamic_MLO_5_6_and_single_band',multiple_links_and_slo),('dynamic_metadata_rejects_invalid_inputs',metadata_rejection),('dynamic_profile_bounds_and_private_namespace',list_bounds_and_namespace),('dynamic_procd_instances_from_typed_config',dynamic_procd),('dynamic_cached_MLO_and_v1_peer_expiry',dynamic_cache_and_expiry),('dynamic_SLO_fleet_independent_expiry',dynamic_fleet_expiry)]:check(name,fn)
(R/'dynamic-tests-result.json').write_text(json.dumps({'passed':True,'results':results},indent=2)+'\n')
