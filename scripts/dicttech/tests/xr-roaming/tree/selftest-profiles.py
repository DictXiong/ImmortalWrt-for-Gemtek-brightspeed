#!/usr/bin/env python3
"""Extend the preserved50 local tests with inert profile/plain-socket isolation tests."""
from pathlib import Path
import runpy,json,hashlib,subprocess,shutil,socket,os,re,shlex
S=Path(__file__).resolve().parent
g=runpy.run_path(str(S/'selftest.py'))
Fixture=g['Fixture'];SHELL=g['SHELL'];JQ=g['JQ'];base_results=g['results']
meta=json.loads((S/'profiles.json').read_text());profiles=meta['profiles'];nodes=meta['nodes']
own_main=g['own']
new_results=[]
def record(name,fn):
 fn();new_results.append({'name':name,'passed':True})
def jq(expr,obj=None,expected=True):
 r=subprocess.run([JQ,'-e','-L',str(S),'include "common"; '+expr],input=json.dumps(obj).encode(),capture_output=True)
 assert (r.returncode==0)==expected,(expr,r.stderr.decode())
record('UTF8_SSID_hex_matches_encoded_bytes',lambda:jq('ssid_hex=="e6b58be8af95f09f9880"','测试😀'))
record('SSID_limit_is32UTF8_bytes',lambda:jq('ssid_ok','测'*11,False))
record('SSID_control_char_rejected',lambda:jq('ssid_ok','a\nb',False))
record('SSID_printable_escaping_matches_hostapd',lambda:jq('ssid_status=="a\\\\\\"b\\\\\\\\c"','a"b\\c'))
class Multi(Fixture):
 def __init__(self):
  super().__init__()
  self.datafile=self.p/'radio-data';self.backend=self.p/'backend';self.cli_log=self.p/'cli-log';self.uci_log=self.p/'uci-log'
  self.profiles={};self.wireless={};self.radio={};self.objects={}
  self.http.unlink();self.http.mkdir()
  nr_iot=json.loads((S/'proof/iot-own-real-getNR.json').read_text())['value'][2]
  for profile,m in profiles.items():
   iface=m['iface'];section=m['section']
   for i,bssid in enumerate(nodes['test']['bssids'][profile]):
    name=iface+'_link'+str(i)if m['mld']else iface
    if profile=='main':st=(S/f'proof/link{i}-status.txt').read_text()
    else:st=(S/f'proof/all-{name}-status.actual.txt').read_text()
    kv=dict(x.split('=',1)for x in st.splitlines()if '='in x)
    idx=[k[4:-1]for k,v in kv.items()if k.startswith('bss[')and v==iface]
    assert len(idx)==1;idx=idx[0];ssid=kv['ssid['+idx+']']
    assert kv['bssid['+idx+']']==bssid
    self.wireless[section]=ssid
    nr=nr_iot if profile=='iot'else bssid.replace(':','')+own_main[i]['nr'][12:]
    neighbors=bssid+' ssid='+ssid.encode().hex()+' nr='+nr+' stat\n'
    self.radio[name]={'status':st,'show_neighbor':neighbors}
    if i==0:self.objects['hostapd.'+iface]={'ssid':ssid,'bssid':bssid,'status':'ENABLED'}
    if profile!='main'and not (self.ctrl/name).exists():
     z=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);z.bind(str(self.ctrl/name));self.sockets.append(z)
   self.profiles[profile]={'enabled':'1','section':section,'iface':iface,'peer':'10.24.0.1',
    'peer_mac':nodes['production']['bridge_mac'],'peer_bssid':' '.join(nodes['production']['bssids'][profile]),'interval':'30','ttl':'180'}
   self.write_peer(profile)
  self.save_multi();self.save_radio();self.backend.write_text('{}')
  py=shutil.which('python3')
  def script(name,body):
   x=self.bin/name;x.write_text('#!'+py+'\n'+body);x.chmod(0o700);return x
  cli=script('cli',f'''import sys,json
a=sys.argv;s=a[a.index("-i")+1];command=a[-1]
with open({str(self.cli_log)!r},"a")as f:f.write(s+"\\n")
d=json.load(open({str(self.datafile)!r}))["radio"];print(d[s][command],end="")
''')
  script('uci',f'''import sys,json
a=sys.argv;k=a[-1];v=json.load(open({str(self.config)!r}))
with open({str(self.uci_log)!r},"a")as f:f.write(k+"\\n")
if "show"in a:print("network.@device[0]=device")
elif k=="network.@device[0].name":print("br-lan")
elif k=="network.@device[0].macaddr":print("02:00:00:00:00:02")
elif k.startswith("xr_roaming."):
 parts=k.split(".");profile=parts[1];print("roaming"if len(parts)==2 else v["profiles"].get(profile,{{}}).get(parts[2],""))
elif k.startswith("wireless."):
 parts=k.split(".");section=parts[1];print(v["types"].get(section,"wifi-iface")if len(parts)==2 else v["wireless"].get(section,""))
else:sys.exit(1)
''')
  script('curl',f'''import sys,pathlib,json,urllib.parse,shutil
a=sys.argv;assert a[1]=="--disable"and"--interface"in a
u=urllib.parse.urlsplit(a[-1]);assert u.hostname=="10.24.0.1"and u.scheme=="http"
profile="main"if u.path=="/xr-roaming/own.json"else u.path.split("/")[2]
assert profile in {list(profiles)!r}and u.path==("/xr-roaming/own.json"if profile=="main"else"/xr-roaming/"+profile+"/own.json")
with open({str(self.curl_log)!r},"a")as f:f.write(json.dumps({{"profile":profile,"url":a[-1],"source":a[a.index("--interface")+1]}})+"\\n")
p=pathlib.Path({str(self.http)!r})/(profile+".json")
if not p.exists():sys.exit(22)
shutil.copyfile(p,a[a.index("--output")+1])
''')
  script('ubus',f'''import sys,json,pathlib
a=sys.argv
if "network.interface.lan"in a:print(pathlib.Path({str(self.lan_status)!r}).read_text());sys.exit()
obj=a[a.index("call")+1]
d=json.load(open({str(self.datafile)!r}))
if a[-1]=="get_status":print(json.dumps(d["objects"][obj]));sys.exit()
assert "rrm_nr_set"in a
payload=json.loads(a[-1]);backend=pathlib.Path({str(self.backend)!r});v=json.loads(backend.read_text());v[obj]=payload["list"];backend.write_text(json.dumps(v))
with open({str(self.actions)!r},"a")as f:f.write(json.dumps({{"object":obj,"payload":payload}})+"\\n")
print("{{}}")
''')
 def save_multi(self):self.config.write_text(json.dumps({'profiles':self.profiles,'wireless':self.wireless,'types':getattr(self,'types',{})}))
 def save_radio(self):self.datafile.write_text(json.dumps({'radio':self.radio,'objects':self.objects}))
 def write_peer(self,profile,revision='11111111-1111-4111-8111-111111111111'):
  section=profiles[profile]['section'];ssid=self.wireless[section];reports=[]
  for i,bssid in enumerate(nodes['production']['bssids'][profile]):
   nr=bssid.replace(':','')+own_main[i]['nr'][12:]
   reports.append({'bssid':bssid,'ssid':ssid,'nr':nr})
  (self.http/(profile+'.json')).write_text(json.dumps({'version':1,'bridge_mac':nodes['production']['bridge_mac'],'ssid':ssid,'revision':revision,'reports':reports}))
 def run(self,mode='once',profile='main',ok=True):
  r=subprocess.run(SHELL+[str(self.helper),mode,profile],capture_output=True,timeout=15)
  assert (r.returncode==0)==ok,(mode,profile,r.returncode,r.stderr.decode())
  return r
 def table(self,profile):return json.loads(self.backend.read_text()).get('hostapd.'+profiles[profile]['iface'],[])
 def state_path(self,profile):return self.p/'state'if profile=='main'else self.p/'state'/profile
 def pub(self,profile):return self.public/'own.json'if profile=='main'else self.public/profile/'own.json'
 def rename(self,profile,ssid):
  section=profiles[profile]['section'];old=self.wireless[section];self.wireless[section]=ssid
  def encoded(s):return ''.join(('\\'+chr(b))if b in(34,92)else chr(b)if 32<=b<=126 else'\\x'+f'{b:02x}'for b in s.encode())
  for name,values in self.radio.items():
   if name==profiles[profile]['iface']or name.startswith(profiles[profile]['iface']+'_link'):
    values['status']=values['status'].replace('='+encoded(old)+'\n','='+encoded(ssid)+'\n')
    values['show_neighbor']=values['show_neighbor'].replace('ssid='+old.encode().hex()+' ','ssid='+ssid.encode().hex()+' ')
  self.objects['hostapd.'+profiles[profile]['iface']]['ssid']=ssid
  self.save_multi();self.save_radio();self.write_peer(profile,revision='22222222-2222-4222-8222-222222222222')
m=Multi()
profile_vectors=[]
for profile,definition in profiles.items():
 for name,data in m.radio.items():
  if name==definition['iface']or name.startswith(definition['iface']+'_link'):
   profile_vectors.append({'profile':profile,'socket':name,'iface':definition['iface'],'mld':definition['mld'],
    'ssid':m.wireless[definition['section']],'status':data['status'],'neighbors':data['show_neighbor'],'ok':True,
    'boundary':'Synthetic C7-format NR/STATUS parser fixture; never sent to a live AP.'})
v=next(x for x in profile_vectors if x['profile']=='iot')
profile_vectors.append({**v,'neighbors':'FAIL\n','ok':False,'boundary':'Actual SHOW before k was enabled must fail closed.'})
try:
 def all4():
  for p in profiles:m.run(profile=p)
  assert all(len(m.table(p))==profiles[p]['max_links']for p in profiles)
  assert m.pub('main').exists()and all(m.pub(p).exists()for p in profiles)
 record('four_profiles_populate_only_their_own_backend_objects',all4)
 def source_only_ssid():
  reads=m.uci_log.read_text().splitlines()
  assert all(not x.endswith(('.key','.sae_password','.wpa_psk'))for x in reads)
  assert all('wireless.'+v['section']+'.ssid'in reads for v in profiles.values())
 record('SSID_derived_from_typed_UCI_sections_without_reading_keys',source_only_ssid)
 def own_scope():
  for p in profiles:
   m.cli_log.write_text('');m.run(profile=p)
   got=set(m.cli_log.read_text().splitlines());iface=profiles[p]['iface']
   expected={iface+'_link'+str(i)for i in range(profiles[p]['max_links'])}if profiles[p]['mld']else{iface}
   assert got==expected,(p,got,expected)
 record('each_collector_queries_only_its_configured_iface_sockets',own_scope)
 def plain():
  own=json.loads((m.state_path('iot')/'links.json').read_text())
  assert len(own)==1 and own[0]['link_id']is None and own[0]['mld']is False and own[0]['bssid']=='02:00:00:00:00:0d'
 record('IoT_plain_STATUS_index1_and_actual_getown_NR_not_first_BSS',plain)
 def schemas():
  assert all(set(json.loads(m.pub(p).read_text()))=={'version','bridge_mac','ssid','revision','reports'}for p in profiles)
  assert json.loads(m.pub('main').read_text())['ssid']=='TestMain'
 record('main_old_URI_and_public_schema_preserved_all_profiles',schemas)
 def clear_guest():
  before={p:m.table(p)for p in profiles};m.run('clear','guest')
  assert m.table('guest')==[]and not m.pub('guest').exists()
  assert all(m.table(p)==before[p]and m.pub(p).exists()for p in profiles if p!='guest')
 record('clear_guest_does_not_remove_main_battalion_IoT',clear_guest)
 def expiry_iot():
  m.run(profile='guest');m.at(200);m.write_peer('main','33333333-3333-4333-8333-333333333333');m.run(profile='main')
  before={p:m.table(p)for p in profiles};(m.http/'iot.json').unlink();m.at(280);m.run(profile='iot')
  assert m.table('iot')==[]and all(m.table(p)==before[p]for p in profiles if p!='iot')
  assert json.loads((m.state_path('main')/'peer.json').read_text())['ssid']=='TestMain'
 record('IoT_TTL_expiry_is_independent_from_other_profile_freshness',expiry_iot)
 def crossed():
  for target,source in [('guest','main'),('battalion','guest'),('iot','battalion')]:
   m.run('clear',target);(m.http/(target+'.json')).write_bytes((m.http/(source+'.json')).read_bytes());m.run(profile=target)
   assert m.table(target)==[]
 record('cross_SSID_or_BSSID_peer_payload_is_rejected',crossed)
 def renamed():
  m.at(300);m.rename('iot','测试IoT"\\');m.run(profile='iot')
  assert json.loads(m.pub('iot').read_text())['ssid']=='测试IoT"\\'and len(m.table('iot'))==1
  profile_vectors.append({**v,'ssid':m.wireless['wifinet1'],'status':m.radio['phy0.0-ap1']['status'],
   'neighbors':m.radio['phy0.0-ap1']['show_neighbor'],'ok':True,'boundary':'Synthetic UI rename fixture only.'})
 record('UI_SSID_rename_including_UTF8_updates_hex_STATUS_and_metadata',renamed)
 def flags():
  before={p:m.table(p)for p in profiles};m.profiles['guest']['enabled']='0';m.save_multi();m.run(profile='guest',ok=False)
  assert m.table('guest')==[]and all(m.table(p)==before[p]for p in profiles if p!='guest')
 record('disable_one_profile_clears_only_its_own_peers',flags)
 def wrong_iface():
  m.profiles['iot']['iface']='ap-mld1';m.save_multi();m.run(profile='iot',ok=False);m.profiles['iot']['iface']='phy0.0-ap1';m.save_multi()
 record('wrong_profile_iface_mapping_is_rejected',wrong_iface)
 def wrong_section_type():
  m.types={'wifinet1':'wifi-device'};m.save_multi();m.run('inspect','iot',ok=False);m.types={};m.save_multi()
 record('non_wifi_iface_section_cannot_be_used_as_SSID_source',wrong_section_type)
 def traversal():
  for profile in ['../main','guest/../../','main;reboot','MAIN','guest\n']:
   m.run('inspect',profile,ok=False)
 record('profile_token_rejects_path_shell_and_case_aliases',traversal)
 def procd_profiles():
  output=m.p/'procd-instances';helper=m.p/'inert-preflight'
  helper.write_text('#!/bin/sh\n[ "$1" = profiles ] || exit 1\nprintf "%s\\n" main battalion guest iot\n');helper.chmod(0o700)
  init=(S/'init.sh').read_text().replace('/usr/lib/xr-roaming/helper.sh',str(helper))
  initpath=m.p/'init-fixture';initpath.write_text(init)
  script='''uci() { printf '1\\n'; }
procd_open_instance() { printf 'OPEN %s\\n' "$1" >> "$PROCD_FIXTURE"; }
procd_set_param() { if [ "$1" = command ]; then shift; printf 'COMMAND %s\\n' "$*" >> "$PROCD_FIXTURE"; fi; }
procd_close_instance() { :; }
. "$INIT_FIXTURE"
start_service
'''
  r=subprocess.run(SHELL+['-c',script],env={**os.environ,'PROCD_FIXTURE':str(output),'INIT_FIXTURE':str(initpath)},capture_output=True,timeout=5)
  assert r.returncode==0,r.stderr.decode()
  lines=output.read_text().splitlines()
  assert [x for x in lines if x.startswith('OPEN ')]==['OPEN '+p for p in profiles]
  assert [x for x in lines if x.startswith('COMMAND ')]==['COMMAND '+str(helper)+' daemon '+p for p in profiles]
 record('procd_uses_four_explicit_independent_profile_instances',procd_profiles)
finally:m.close()
out={'result':'PASS_LOCAL_ONLY_MULTI_PROFILE','old_cases':len(base_results),'new_cases':len(new_results),'total':len(base_results)+len(new_results),'passed':True,
 'newResults':new_results,'no_device_actions':True,'limits':['Some MLO battalion/guest NR values are synthetic parser fixtures and never deployed.','All radio data are synthetic fixtures, not live observations.','Actual procd and radio behavior require an independent live acceptance run.']}
(S/'selftest-profiles-result.json').write_text(json.dumps(out,indent=2)+'\n')
(S/'target-v2-parser-vectors.json').write_text(json.dumps(profile_vectors,indent=2)+'\n')
print(json.dumps({k:out[k]for k in ['result','old_cases','new_cases','total','passed']}))
