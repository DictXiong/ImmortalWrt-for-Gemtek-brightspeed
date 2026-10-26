#!/usr/bin/env python3
"""Inert multi-AP peer isolation/expiry checks using the package runtime."""
from pathlib import Path
import json,os,shutil,subprocess
T=Path(__file__).resolve().parent;R=T.parent
ns={'__file__':str(T/'cache.py'),'__name__':'fleet_fixture_definitions'}
text=(T/'cache.py').read_text().split("record('steady_cache_and_generation_config_radio_audit_invalidation'")[0]
exec(compile(text,str(T/'cache.py'),'exec'),ns)
Cache=ns['Cache'];SHELL=ns['SHELL'];C7=ns['C7'];ROOT=ns['ROOT'];g=ns['ns']['g']
results=[]
class Fleet(Cache):
 def __init__(self,ids=('peer_a','peer_b'),shell=None):
  self.peers={}
  super().__init__('iot',shell)
  self.profiles['iot']['peer_mode']='fleet';self.profiles['iot']['peers']=' '.join(ids)
  old=json.loads((self.http/'iot.json').read_text())
  self.payloads={}
  for index,peer_id in enumerate(ids):
   payload=json.loads(json.dumps(old));last=0x31+index
   payload['bridge_mac']=f'02:00:00:aa:00:{last:02x}'
   for report in payload['reports']:
    report['bssid']=f'02:00:00:bb:00:{last:02x}';report['nr']=report['bssid'].replace(':','')+report['nr'][12:]
   address='10.24.0.'+str(10+index)
   self.peers[peer_id]={'address':address,'bridge_mac':payload['bridge_mac'],'iot_bssid':' '.join(x['bssid']for x in payload['reports'])}
   self.payloads[peer_id]=payload;self.remote(peer_id,100)
  self.save_multi()
  py=shutil.which('python3')
  (self.bin/'uci').write_text('#!'+py+'\n'+f'''import sys,json
a=sys.argv;k=a[-1];v=json.load(open({str(self.config)!r}))
with open({str(self.uci_log)!r},"a")as f:f.write(k+"\\n")
if "show"in a:print("network.@device[0]=device")
elif k=="network.@device[0].name":print("br-lan")
elif k=="network.@device[0].macaddr":print("02:00:00:00:00:02")
elif k.startswith("xr_roaming."):
 p=k.split(".");name=p[1]
 if len(p)==2:print("peer"if name in v["peers"]else"roaming")
 else:print((v["peers"].get(name)or v["profiles"].get(name,{{}})).get(p[2],""))
elif k.startswith("wireless."):
 p=k.split(".");print("wifi-iface"if len(p)==2 else v["wireless"].get(p[1],""))
else:sys.exit(1)
''')
  (self.bin/'curl').write_text('#!'+py+'\n'+f'''import sys,json,pathlib,shutil,urllib.parse
a=sys.argv;u=urllib.parse.urlsplit(a[-1]);mapping={{{','.join(repr(v['address'])+':'+repr(k)for k,v in self.peers.items())}}}
assert u.scheme=="http"and u.hostname in mapping and u.path=="/xr-roaming/iot/own.json"
with open({str(self.curl_log)!r},"a")as f:f.write(mapping[u.hostname]+"\\n")
p=pathlib.Path({str(self.http)!r})/(mapping[u.hostname]+".json")
if not p.exists():sys.exit(22)
shutil.copyfile(p,a[a.index("--output")+1])
''')
 def save_multi(self):
  self.config.write_text(json.dumps({'profiles':self.profiles,'wireless':self.wireless,'types':getattr(self,'types',{}),'peers':self.peers}))
 def remote(self,peer_id,tick):
  self.payloads[peer_id]['revision']='11111111-1111-4111-8111-'+f'{tick:012x}'
  (self.http/(peer_id+'.json')).write_text(json.dumps(self.payloads[peer_id]))
 def step(self,tick,fresh=()):
  for peer_id in fresh:self.remote(peer_id,tick)
  return super().step(tick,peer=False)
def check(name,fn):
 details=fn();results.append({'name':name,'passed':True,'details':details});print(name+' PASS',flush=True)
def independent_expiry():
 m=Fleet(shell=C7 if ROOT else SHELL)
 try:
  m.start();assert len(m.table('iot'))==2
  writes=m.writes();m.step(130);assert m.writes()==writes
  for tick in [160,190,220,250]:m.step(tick,fresh=('peer_b',))
  x=m.step(280,fresh=('peer_b',));assert len(m.table('iot'))==1
  assert m.table('iot')[0][0]==m.payloads['peer_b']['reports'][0]['bssid']
  info={x['id']:x for x in x['peers']};assert not info['peer_a']['fresh']and info['peer_b']['fresh']
  m.step(340,fresh=('peer_a','peer_b'));assert len(m.table('iot'))==2
  (m.http/'peer_a.json').write_text('{bad')
  for tick in [400,460,520]:m.step(tick,fresh=('peer_b',))
  assert len(m.table('iot'))==1,'malformed peer should expire alone'
  return {'two_peers_union':True,'independent_frozen_and_malformed_expiry':True,'healthy_peer_retained':True,'fresh_peer_recovers':True,'target_C7':bool(ROOT)}
 finally:m.close()
def empty_fleet():
 m=Fleet(ids=())
 try:
  x=m.start();assert m.table('iot')==[]and x['state']=='ok'and m.pub('iot').exists()
  m.step(160);assert m.pub('iot').exists()and not m.curl_log.exists()
  return {'single_AP_valid':True,'own_metadata_published':True,'no_HTTP_fetch':True}
 finally:m.close()
def duplicate_identity():
 m=Fleet()
 try:
  m.peers['peer_b']['iot_bssid']=m.peers['peer_a']['iot_bssid'];m.save_multi()
  q=subprocess.run(SHELL+[str(m.helper),'preflight','iot'],capture_output=True,timeout=10)
  assert q.returncode!=0
  assert not m.curl_log.exists()and not m.actions.exists()
  return {'cross_peer_BSSID_collision_rejected_before_network_or_apply':True}
 finally:m.close()
def cache_repair_and_new_own_identity():
 m=Fleet()
 try:
  m.start();initial=m.writes()
  (m.state_path('iot')/'peer.request.json').unlink()
  m.step(130);assert len(m.table('iot'))==2 and m.writes()==initial
  # Simulate a driver-side own-BSSID change without editing UCI. Hostapd's
  # keyed neighbor database has one row per BSSID, so remove the old peer row
  # now occupied by the new own record, while retaining the healthy peer.
  old=g['nodes']['test']['bssids']['iot'][0]
  own=m.payloads['peer_a']['reports'][0]['bssid']
  radio=m.radio['phy0.0-ap1']
  radio['status']=radio['status'].replace(old,own)
  nr=own.replace(':','')+m.payloads['peer_a']['reports'][0]['nr'][12:]
  ssid=m.wireless[g['profiles']['iot']['section']]
  radio['show_neighbor']=own+' ssid='+ssid.encode().hex()+' nr='+nr+'\n'
  m.objects['hostapd.phy0.0-ap1']['bssid']=own;m.save_radio()
  rows=[row for row in m.table('iot') if row[0]!=own]
  m.backend.write_text(json.dumps({'hostapd.phy0.0-ap1':rows}))
  m.step(140)
  assert len(m.table('iot'))==1 and m.table('iot')[0][0]!=own
  assert json.loads(m.pub('iot').read_text())['reports'][0]['bssid']==own
  return {'missing_aggregate_rebuilt_without_table_rewrite':True,'driver_own_BSSID_change_revalidates_all_peer_caches':True,'own_alias_never_reapplied':True}
 finally:m.close()
check('fleet_independent_expiry_and_union',independent_expiry)
check('fleet_zero_peer_single_AP',empty_fleet)
check('fleet_rejects_duplicate_peer_identity',duplicate_identity)
check('fleet_cache_repair_and_own_identity_change',cache_repair_and_new_own_identity)
(R/'fleet-tests-result.json').write_text(json.dumps({'passed':True,'results':results},indent=2)+'\n')
