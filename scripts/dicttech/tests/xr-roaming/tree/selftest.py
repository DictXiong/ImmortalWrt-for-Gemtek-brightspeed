#!/usr/bin/env python3
"""Local-only tests. AP commands, HTTP and clocks are inert fixtures."""
from pathlib import Path
import hashlib,json,os,re,shlex,shutil,socket,subprocess,tempfile
S=Path(__file__).resolve().parent
JQ=shutil.which('jq')
BB=os.environ.get('XR_TEST_BUSYBOX') or shutil.which('busybox') or ''
SHELL=[BB,'ash'] if BB else ['/bin/sh']
results=[]
target_vectors=[]
def record(name,fn):
 fn();results.append({'name':name,'passed':True})
def jq_check(expr, obj=None, expected=True):
 target_vectors.append({'kind':'common','filter':'include "common"; '+expr,'input':obj,'ok':expected})
 p=subprocess.run([JQ,'-e','-L',str(S),'include "common"; '+expr],input=json.dumps(obj).encode(),capture_output=True)
 assert (p.returncode==0)==expected,(expr,p.returncode,p.stderr.decode())
def parse(link,status=None,neighbors=None,after=None,ok=True):
 args=[JQ,'-ecn','-L',str(S),'--arg','socket',f'ap-mld1_link{link}','--arg','ssid','TestMain','--arg','ssidhex','546573744d61696e']
 args+=['--arg','status',status or (S/f'proof/link{link}-status.txt').read_text()]
 args+=['--arg','neighbors',neighbors or (S/f'proof/link{link}-show_neighbor.txt').read_text()]
 if after is not None:args+=['--arg','status_after',after]
 p=subprocess.run(args+['-f',str(S/'cli-report.jq')],capture_output=True)
 target_vectors.append({'kind':'parser','socket':f'ap-mld1_link{link}','ssid':'TestMain','ssidhex':'546573744d61696e',
  'status':status or (S/f'proof/link{link}-status.txt').read_text(),
  'neighbors':neighbors or (S/f'proof/link{link}-show_neighbor.txt').read_text(),
  'status_after':after,'ok':ok,'expected_output':json.loads(p.stdout)if ok and p.returncode==0 else None})
 assert (p.returncode==0)==ok,(link,p.stderr.decode())
 return json.loads(p.stdout) if ok else None
own=[parse(i)for i in range(3)]
peer=[]
for i,r in enumerate(own):
 b=f'02:00:00:20:00:{[0x61,0x62,0x63][i]:02x}'
 peer.append({'bssid':b,'ssid':'TestMain','nr':b.replace(':','')+r['nr'][12:]})
pub={'version':1,'bridge_mac':'02:00:00:00:00:03','ssid':'TestMain',
 'revision':'11111111-1111-4111-8111-111111111111','reports':peer}
allow=[r['bssid']for r in peer]
expr='public_ok("TestMain";"02:00:00:00:00:03";'+json.dumps(allow)+')'
record('three_C7_format_control_socket_reports',lambda:None)
record('RFC1918_maintenance_and_DHCP_addresses',lambda:jq_check('["10.24.0.1","10.24.0.2","10.23.0.10"]|all(.[];private_ipv4)'))
for value in ['1.1.1.1','10.24.0.999','10.24.0.01','10.24.0.0','127.0.0.1','http://x;reboot']:
 record('reject_peer_address_'+value,lambda v=value:jq_check('private_ipv4',v,False))
record('valid_strict_three_link_public_metadata',lambda:jq_check(expr,pub))
for name,change in [
 ('foreign_SSID',lambda x:x.update(ssid='TestMain-Guest')),
 ('wrong_bridge_identity',lambda x:x.update(bridge_mac='02:00:00:00:00:01')),
 ('extra_executable_field',lambda x:x.update(command='reboot')),
 ('disallowed_BSSID',lambda x:x['reports'][0].update(bssid='02:00:00:20:00:80')),
 ('duplicate_report',lambda x:x['reports'].__setitem__(1,x['reports'][0])),
 ('four_links',lambda x:x['reports'].append(x['reports'][0])),
 ('odd_hex',lambda x:x['reports'][0].update(nr=x['reports'][0]['nr']+'0')),
 ('invalid_hex',lambda x:x['reports'][0].update(nr='g'*36)),
 ('NR_BSSID_mismatch',lambda x:x['reports'][0].update(nr='000000000000'+x['reports'][0]['nr'][12:])),
 ('truncated_TLV',lambda x:x['reports'][0].update(nr=x['reports'][0]['nr'][:-2])),
 ('zero_channel',lambda x:x['reports'][0].update(nr=x['reports'][0]['nr'][:22]+'00'+x['reports'][0]['nr'][24:])),
 ('oversized_NR',lambda x:x['reports'][0].update(nr=x['reports'][0]['nr']+'00'*256)),
]:
 def check(change=change):
  v=json.loads(json.dumps(pub));change(v);jq_check(expr,v,False)
 record('reject_'+name,check)
st=(S/'proof/link1-status.txt').read_text()
record('STATUS_other_radio_BSS_not_mistaken_for_own',lambda:parse(1))
record('reject_channel_changed_between_status_reads',lambda:parse(1,after=st.replace('channel=44\n','channel=36\n'),ok=False))
record('reject_NR_stale_channel',lambda:parse(1,status=st.replace('channel=44\n','channel=36\n'),ok=False))
record('reject_disabled_link',lambda:parse(1,status=st.replace('state=ENABLED','state=DISABLED'),ok=False))
record('reject_missing_own_NR',lambda:parse(1,neighbors='FAIL\n',ok=False))

class Fixture:
 def __init__(self):
  self.temp=tempfile.TemporaryDirectory(prefix='xr-roaming-fixture-');self.p=Path(self.temp.name)
  self.state=self.p/'state';self.public=self.p/'public';self.web=self.p/'web';self.ctrl=self.p/'ctrl'
  self.ctrl.mkdir();self.sockets=[]
  for i in range(3):
   z=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);z.bind(str(self.ctrl/f'ap-mld1_link{i}'));self.sockets.append(z)
  self.config=self.p/'config';self.http=self.p/'http';self.clock=self.p/'uptime';self.uuid=self.p/'uuid';self.actions=self.p/'actions'
  self.lan_status=self.p/'lan-status';self.curl_log=self.p/'curl-log'
  self.lan={'up':True,'proto':'dhcp','l3_device':'br-lan.1','ipv4-address':[{'address':'10.23.0.161','mask':24}]}
  self.save_lan()
  self.uuid.write_text('33333333-3333-4333-8333-333333333333\n');self.at(100);self.http.write_text(json.dumps(pub))
  self.values={'enabled':'1','ssid':'TestMain','peer':'10.24.0.1','peer_mac':pub['bridge_mac'],
   'peer_bssid':' '.join(allow),'interval':'30','ttl':'180'}
  self.save_config();self.bin=self.p/'bin';self.bin.mkdir()
  def script(name,text):
   path=self.bin/name;path.write_text('#!'+shutil.which('python3')+'\n'+text);path.chmod(0o700);return path
  cli=script('cli',f'import sys,pathlib\ns=sys.argv[sys.argv.index("-i")+1];link=int(s.rsplit("link",1)[1]);command=sys.argv[-1]\nprint((pathlib.Path({str(S)!r})/f"proof/link{{link}}-{{command}}.txt").read_text(),end="")\n')
  uci=script('uci',f'import sys,json\na=sys.argv\nv=json.load(open({str(self.config)!r}))\nif "show" in a: print("network.@device[0]=device")\nelif a[-1]=="network.@device[0].name":print("br-lan")\nelif a[-1]=="network.@device[0].macaddr":print("02:00:00:00:00:02")\nelif a[-1]=="xr_roaming.main":print("roaming")\nelse: print(v.get(a[-1].rsplit(".",1)[1],""))\n')
  curl=script('curl',f'import sys,pathlib,shutil\na=sys.argv\nassert a[1]=="--disable" and "--noproxy" in a and "--max-filesize" in a and "--proto" in a and "--interface" in a\nwith open({str(self.curl_log)!r},"a")as f:f.write(a[a.index("--interface")+1]+"\\n")\np=pathlib.Path({str(self.http)!r})\nif not p.exists():sys.exit(22)\nshutil.copyfile(p,a[a.index("--output")+1])\n')
  ubus=script('ubus',f'import sys,json,pathlib\na=sys.argv\nif "network.interface.lan" in a:print(pathlib.Path({str(self.lan_status)!r}).read_text())\nelif a[-1]=="get_status":print(json.dumps({{"ssid":"TestMain","status":"ENABLED","bssid":{own[0]["bssid"]!r}}}))\nelse:\n assert "rrm_nr_set" in a\n with open({str(self.actions)!r},"a")as f:f.write(a[-1]+"\\n")\n if pathlib.Path({str(self.p/'ubus-fail')!r}).exists():sys.exit(1)\n print("{{}}")\n')
  text=(S/'helper.sh').read_text()
  mapping={'ROOT':S,'STATE':self.state,'PUBLIC':self.public,'BASE_STATE':self.state,'BASE_PUBLIC':self.public,'WEB':self.web,'CTRL':self.ctrl,'CLI':cli,'UCI':uci,
   'UBUS':ubus,'JQ':JQ,'CURL':curl,'TIMEOUT':shutil.which('timeout'),'UPTIME':self.clock,'UUID':self.uuid}
  for k,v in mapping.items():
   text,n=re.subn('^'+k+'=.*$',k+'='+shlex.quote(str(v)),text,flags=re.M);assert n==1,k
  text=text.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin','export PATH='+shlex.quote(str(self.bin)+':'+os.environ['PATH']))
  self.helper=self.p/'helper';self.helper.write_text(text);self.helper.chmod(0o700)
  self.web.symlink_to(self.public)
 def save_config(self):self.config.write_text(json.dumps(self.values))
 def save_lan(self):self.lan_status.write_text(json.dumps(self.lan))
 def at(self,n):self.clock.write_text(f'{n}.00 0.00\n')
 def run(self,mode='once',ok=True):
  r=subprocess.run(SHELL+[str(self.helper),mode],capture_output=True,timeout=15)
  assert (r.returncode==0)==ok,(r.returncode,r.stdout.decode(),r.stderr.decode())
  return r
 def last(self):return json.loads(self.actions.read_text().splitlines()[-1])['list']
 def close(self):
  for x in self.sockets:x.close()
  self.temp.cleanup()
f=Fixture()
try:
 record('service_preflight_without_network_or_UCI_mutation',lambda:f.run('preflight'))
 def missing_link():
  f.web.unlink();f.run('preflight',ok=False);f.web.symlink_to(f.public)
 record('preflight_rejects_missing_WWW_link',missing_link)
 def wrong_link():
  f.web.unlink();f.web.symlink_to(f.p/'wrong');f.run('preflight',ok=False)
  f.web.unlink();f.web.symlink_to(f.public)
 record('preflight_rejects_wrong_WWW_link',wrong_link)
 def valid_cycle():
  f.run();assert len(f.last())==3
  ownpub=json.loads((f.public/'own.json').read_text());assert len(ownpub['reports'])==3
  assert set(ownpub)=={'version','bridge_mac','ssid','revision','reports'}
  assert all(set(r)=={'ssid','bssid','nr'}for r in ownpub['reports'])
 record('one_complete_MLD_set_and_public_metadata_only',valid_cycle)
 record('HTTP_bound_to_current_DHCP_LAN_IP_literal',lambda: (f.curl_log.read_text().splitlines()[-1]=='10.23.0.161')or (_ for _ in []).throw(AssertionError('wrong source')))
 def invalid_inspect():
  f.values['enabled']='0';f.save_config();f.run('inspect',ok=False)
  f.values['enabled']='1';f.save_config()
 record('inspect_does_not_reuse_stale_links_after_config_failure',invalid_inspect)
 def failed_fetch_retains():
  f.http.unlink();f.at(200);f.run();assert len(f.last())==3
 record('temporary_fetch_failure_retains_until_TTL',failed_fetch_retains)
 def expired():
  f.at(280);f.run();assert f.last()==[]
 record('fetch_failure_TTL180_clears_peer_reports',expired)
 def frozen():
  f.http.write_text(json.dumps(pub));f.at(300);f.run();assert f.last()==[]
 record('frozen_static_HTTP_revision_does_not_extend_TTL',frozen)
 def refreshed():
  v=json.loads(json.dumps(pub));v['revision']='44444444-4444-4444-8444-444444444444'
  f.http.write_text(json.dumps(v));f.at(301);f.run();assert len(f.last())==3
 record('fresh_valid_peer_revision_repopulates_full_table',refreshed)
 def malformed():
  f.http.write_text('{bad');f.at(481);f.run();assert f.last()==[]
 record('malformed_HTTP_does_not_refresh_TTL',malformed)
 def transport_oversize():
  f.http.write_text(' '*20000);f.at(482);f.run();assert f.last()==[]
 record('oversized_HTTP_body_rejected',transport_oversize)
 def reconfigured_identity():
  f.http.write_text(json.dumps(pub));f.at(500);f.run();assert len(f.last())==3
  f.values['peer_mac']='02:00:00:00:00:01';f.save_config();f.at(501);f.run();assert f.last()==[]
 record('peer_configuration_change_invalidates_old_cache',reconfigured_identity)
 def clear():
  f.run('clear');assert f.last()==[] and not (f.public/'own.json').exists()
 record('stop_clear_removes_publication_and_nonself_neighbors',clear)
 def source_refresh():
  f.values['peer_mac']=pub['bridge_mac'];f.save_config()
  v=json.loads(json.dumps(pub));v['revision']='55555555-5555-4555-8555-555555555555';f.http.write_text(json.dumps(v))
  f.lan['ipv4-address'][0]['address']='10.23.0.172';f.save_lan();f.at(600);f.run()
  assert f.curl_log.read_text().splitlines()[-1]=='10.23.0.172' and len(f.last())==3
 record('DHCP_change_updates_bound_source_each_cycle',source_refresh)
 for name,edit in [('missing_source',lambda x:x.update({'ipv4-address':[]})),
                   ('wrong_L3',lambda x:x.update(l3_device='br-lan.192')),
                   ('multiple_sources',lambda x:x['ipv4-address'].append({'address':'10.23.0.173','mask':24})),
                   ('nonprivate_source',lambda x:x['ipv4-address'][0].update(address='1.1.1.1')),
                   ('static_protocol',lambda x:x.update(proto='static'))]:
  def bad_source(edit=edit):
   previous=f.curl_log.read_text();saved=json.loads(json.dumps(f.lan));edit(f.lan);f.save_lan()
   f.at(780);f.run();assert f.curl_log.read_text()==previous and f.last()==[]
   f.lan=saved;f.save_lan()
  record('reject_HTTP_'+name+'_without_alias_fallback',bad_source)
 def ubus_failure():
  (f.p/'ubus-fail').write_text('1');f.run(ok=False)
 record('nonzero_MLD_set_return_is_failure',ubus_failure)
 record('nonzero_clear_MLD_set_return_is_failure',lambda:f.run('clear',ok=False))
 def missing_tool_gate():
  original=f.helper.read_text();f.helper.write_text(re.sub(r'(for utility in [^\n]*)(; do)',r'\1 xr_required_missing_fixture\2',original))
  f.run('preflight',ok=False);f.helper.write_text(original)
 record('missing_external_utility_rejected_before_state_access',missing_tool_gate)
 def wrong_owner():
  fake=f.bin/'ls';fake.write_text('#!/bin/sh\nprintf "%s\\n" "drwx------ 2 123 0 40 Oct 26 fixture"\n');fake.chmod(0o700)
  f.run('preflight',ok=False);fake.unlink()
 record('numeric_ls_owner_must_be_root',wrong_owner)
finally:f.close()

out={'local_only':True,'remote_mutations':False,'shell':SHELL,'passed':all(x['passed']for x in results),
 'count':len(results),'cases':results,'limits':['No real service deployment or peer HTTP yet.',
 'C7 MLD list implementation is sequential internally, not an atomic transaction.',
 'No real DFS event was generated; only stale-channel and status-change rejection were simulated.']}
(S/'selftest-result.json').write_text(json.dumps(out,indent=2)+'\n')
(S/'target-jq-vectors.json').write_text(json.dumps(target_vectors,indent=2)+'\n')
print(json.dumps({'passed':out['passed'],'count':len(results),'shell':SHELL}))
