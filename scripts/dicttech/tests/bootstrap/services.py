"""Run deployment policy against private inert fake init/ubus state only."""
import json,os,pathlib,subprocess,tempfile,shutil,re,shlex
R=pathlib.Path(os.environ['DICTTECH_AP_TEST_SOURCE_ROOT']).resolve()
SRC=R/'package/dicttech/ap-profile/files/usr/lib/dicttech-ap/services.sh'
report=[]
with tempfile.TemporaryDirectory(prefix='dt-ap-services-', dir='/tmp') as d:
 f=pathlib.Path(d)
 for p in ['etc/init.d','etc/rc.d','etc/dicttech-ap','usr/share/dicttech-ap','bin','running']:(f/p).mkdir(parents=True)
 shutil.copy(R/'package/dicttech/ap-profile/files/usr/share/dicttech-ap/defaults.json',f/'usr/share/dicttech-ap/defaults.json')
 names=['network','sysntpd','firewall','dropbear','uhttpd','xr_roaming','wifihistory','sysctl','cpufreq','airoha-npu','fan','dnsmasq','usteer','npu-jitter','radius','3proxy']
 for name in names:
  p=f/'etc/init.d'/name
  p.write_text('''#!/bin/sh
name=${0##*/}
printf '%s %s\\n' "$name" "$1" >>"LOG"
case "$1" in
 enabled) for p in RC/S[0-9][0-9]"$name"; do [ ! -L "$p" ] || exit 0; done; exit 1;;
 disable) for p in RC/S[0-9][0-9]"$name" RC/K[0-9][0-9]"$name"; do [ ! -L "$p" ] || rm "$p"; done;;
 enable) "$0" disable; ln -s "../init.d/$name" "RC/S40$name";;
 start) case "$name" in sysctl|cpufreq|airoha-npu|fan) :;; *) touch "RUN/$name";; esac;;
 stop) rm -f "RUN/$name";;
 *) exit 2;;
esac
'''.replace('RC',str(f/'etc/rc.d')).replace('RUN',str(f/'running')).replace('LOG',str(f/'actions')))
  p.chmod(0o755)
 for n in ['network','dropbear','dnsmasq','usteer','radius','3proxy']:
  (f/'etc/rc.d'/('S23'+n)).symlink_to('../init.d/'+n)
  (f/'etc/rc.d'/('K77'+n)).symlink_to('../init.d/'+n)
  (f/'running'/n).touch()
 p=f/'bin/ubus';p.write_text('#!'+shutil.which('python3')+'\nimport sys,json,pathlib\nn=json.loads(sys.argv[4])["name"];print(json.dumps({n:{"instances":{"main":{"running":(pathlib.Path('+repr(str(f/'running'))+')/n).exists()}}}}))\n');p.chmod(0o755)
 text=SRC.read_text();text=re.sub(r'/(?:usr/share|etc)/',lambda m:str(f/m.group(0).strip('/'))+'/',text);text=text.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin','PATH='+shlex.quote(str(f/'bin')+':'+os.environ['PATH']))
 p=f/'services.sh';p.write_text(text)
 def run(*args):return subprocess.run([os.environ['DICTTECH_AP_TEST_SHELL'],str(p),*map(str,args)],capture_output=True,text=True)
 before_links={x.name:os.readlink(x) for x in (f/'etc/rc.d').iterdir()}
 before_running={x.name for x in (f/'running').iterdir()}
 snap=f/'snapshot.json';o=run('snapshot',snap);assert o.returncode==0,o.stderr
 j=json.loads(snap.read_text());assert len(j['services'])==len(re.search("ALLOWED='([^']+)'",SRC.read_text()).group(1).split())
 assert (snap.stat().st_mode&0o777)==0o600
 ntp=next(x for x in j['services'] if x['service']=='sysntpd');assert ntp['present'] and not ntp['enabled'] and not ntp['running'] and ntp['links']==[]
 report.append({'case':'snapshot_exact_rc_and_running','passed':True})
 o=run('apply');assert o.returncode==0,o.stderr
 for n in ['dnsmasq','usteer','npu-jitter','radius','3proxy']:
  assert not (f/'running'/n).exists()
  assert not list((f/'etc/rc.d').glob('S??'+n))
 for n in ['network','sysntpd','firewall','dropbear','uhttpd','xr_roaming','wifihistory']:assert (f/'running'/n).exists(),n
 for n in ['sysctl','cpufreq','airoha-npu','fan']:assert n+' start' in (f/'actions').read_text(),n
 report.append({'case':'apply_required_optional_and_disabled','passed':True})
 o=run('restore',snap,'links');assert o.returncode==0,o.stderr
 assert {x.name:os.readlink(x) for x in (f/'etc/rc.d').iterdir()}==before_links
 report.append({'case':'restore_exact_rc_links','passed':True})
 o=run('restore',snap,'running');assert o.returncode==0,o.stderr
 assert {x.name for x in (f/'running').iterdir()}==before_running
 assert not (f/'running/sysntpd').exists() and not list((f/'etc/rc.d').glob('S??sysntpd'))
 report.append({'case':'restore_running_deviations_including_sysntpd','passed':True})
 bad=dict(j);bad['services']=[dict(x) for x in j['services']];bad['services'][0]['service']='not-allowed'
 badpath=f/'bad.json';badpath.write_text(json.dumps(bad));count=(f/'actions').read_text();o=run('restore',badpath,'links');assert o.returncode!=0;assert (f/'actions').read_text()==count
 report.append({'case':'reject_unknown_service_before_mutation','passed':True})
 policy=json.loads((f/'usr/share/dicttech-ap/defaults.json').read_text());policy['services']['enabled'][0]='network firewall';(f/'etc/dicttech-ap/device.json').write_text(json.dumps(policy));count=(f/'actions').read_text();o=run('apply');assert o.returncode!=0;assert (f/'actions').read_text()==count
 report.append({'case':'reject_whitespace_service_injection','passed':True})
 count=(f/'actions').read_text();active={x.name for x in (f/'running').iterdir()};o=run('setup');assert o.returncode==0,o.stderr
 assert not list((f/'etc/rc.d').glob('S??xr_roaming'))
 assert not list((f/'etc/rc.d').glob('S??wifihistory'))
 assert not list((f/'etc/rc.d').glob('S??npu-jitter'))
 assert list((f/'etc/rc.d').glob('S??network'))
 assert {x.name for x in (f/'running').iterdir()}==active
 report.append({'case':'setup_ignores_invalid_marker_links_only_no_roaming_jitter','passed':True})
 oneshot=json.loads(snap.read_text());one_names=['sysctl','cpufreq','airoha-npu','fan']
 for x in oneshot['services']:
  if x['service'] in one_names:x['enabled']=True;x['running']=False
 one_file=f/'oneshot.json';one_file.write_text(json.dumps(oneshot));count=(f/'actions').read_text();o=run('restore',one_file,'running');assert o.returncode==0,o.stderr
 starts=[line.split()[0] for line in (f/'actions').read_text()[len(count):].splitlines() if line.endswith(' start')]
 assert starts==one_names,starts
 report.append({'case':'restore_enabled_oneshot_hardware_policy_in_fixed_order','passed':True})
 # A malformed JSON producer must fail before deleting any rc links.
 for content in ['{bad', json.dumps({'schema_version':1,'services':[]})]:
  broken=f/'broken.json';broken.write_text(content);links={x.name:os.readlink(x)for x in(f/'etc/rc.d').iterdir()}
  o=run('restore',broken,'links');assert o.returncode!=0
  assert {x.name:os.readlink(x)for x in(f/'etc/rc.d').iterdir()}==links
 report.append({'case':'malformed_or_incomplete_snapshot_refused_before_rc_mutation','passed':True})
 # Transport and JSON errors are not equivalent to a stopped service.
 ubus=f/'bin/ubus';original_ubus=ubus.read_text()
 for body in ['#!/bin/sh\nexit 1\n', '#!/bin/sh\nprintf \"{bad\"\n']:
  ubus.write_text(body);ubus.chmod(0o755);failed=f/'failed-snapshot.json';links={x.name:os.readlink(x)for x in(f/'etc/rc.d').iterdir()}
  o=run('snapshot',failed);assert o.returncode!=0
  assert not failed.exists()
  assert {x.name:os.readlink(x)for x in(f/'etc/rc.d').iterdir()}==links
 ubus.write_text(original_ubus);ubus.chmod(0o755)
 report.append({'case':'ubus_failure_and_malformed_json_not_silently_recorded_as_stopped','passed':True})

print(json.dumps(report,indent=2))
