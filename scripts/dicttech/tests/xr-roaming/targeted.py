#!/usr/bin/env python3
"""Local inert regression checks for v4 cache and preserved v3 lifecycle. No AP commands or network."""
from pathlib import Path
import fcntl, hashlib, json, os, re, resource, runpy, shlex, shutil, signal, subprocess, time

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
T=Path(__file__).resolve().parent
R=T.parent
S=R/'helper-v4'
ROOT=Path(os.environ['XR_C7_ROOTFS']) if os.environ.get('XR_C7_ROOTFS') else None
QEMU=os.environ.get('XR_QEMU') or shutil.which('qemu-aarch64')
C7=[QEMU,'-L',str(ROOT),str(ROOT/'bin/busybox'),'ash'] if ROOT else ['/bin/sh']
pins={n:hashlib.sha256((S/n).read_bytes()).hexdigest()for n in ['helper.sh','init.sh','common.jq','cli-report.jq']}
results=[]
DELTA_ONLY=os.environ.get('XR_ONLY_DELTA')=='1'
OUT=R/('targeted-debug-result.json'if DELTA_ONLY else'targeted-tests-result.json')
def save():
 OUT.write_text(json.dumps({'scope':'Local inert fixtures only; no AP or PI calls.','sourceSHA':pins,'results':results,'passed':all(x['passed']for x in results)},indent=2)+'\n')
def record(name,fn):
 try: details=fn()
 except Exception as e:
  results.append({'name':name,'passed':False,'error':repr(e)});save();raise
 results.append({'name':name,'passed':True,'details':details});save();print(name+' PASS',flush=True)
def until(fn,seconds=10):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   x=fn()
   if x:return x
  except (FileNotFoundError,json.JSONDecodeError):pass
  time.sleep(.025)
 raise AssertionError('bounded fixture wait expired')
def run(m,mode='once',profile='iot',shell=None,ok=True):
 q=subprocess.run((shell or g['SHELL'])+[str(m.helper),mode,profile],capture_output=True,timeout=18,cwd=m.p)
 assert (q.returncode==0)==ok,(mode,q.returncode,q.stderr.decode())
 return q
def status(m,profile='iot',shell=None):return json.loads(run(m,'status',profile,shell).stdout)
def kidinfo(pid):
 p=Path('/proc')/str(pid)
 return {'pid':pid,'start':(p/'stat').read_text().split(') ',1)[1].split()[19],'cmdline':(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')}
def still_same(kid):
 try:return kidinfo(kid['pid'])['start']==kid['start']
 except (FileNotFoundError,ProcessLookupError):return False

# Execute the copied legacy suite once, then reuse its inert Multi fixture.
# A debug run may load only fixture definitions to locate the next failure;
# the final release run always executes all legacy checks again.
if DELTA_ONLY:
 tree=T/'tree';g={'__file__':str(tree/'selftest.py'),'__name__':'fixture_definitions'}
 exec(compile((tree/'selftest.py').read_text().split('f=Fixture()')[0],str(tree/'selftest.py'),'exec'),g)
 meta=json.loads((tree/'profiles.json').read_text())
 g.update(meta=meta,profiles=meta['profiles'],nodes=meta['nodes'],own_main=g['own'])
 text=(tree/'selftest-profiles.py').read_text()
 exec(compile('class Multi(Fixture):'+text.split('class Multi(Fixture):',1)[1].split('\nm=Multi()',1)[0],str(tree/'selftest-profiles.py'),'exec'),g)
else:g=runpy.run_path(str(T/'tree/selftest-profiles.py'))
Multi=g['Multi']
OriginalMulti=Multi
class Multi(OriginalMulti):
 def __init__(self):
  super().__init__()
  self.cfgdir=self.p/'etc-config';self.cfgdir.mkdir()
  for name in ['xr_roaming','wireless','network']:(self.cfgdir/name).symlink_to(self.config)
  self.deltadir=self.p/'delta';self.deltadir.mkdir()
  self.pidfile=self.p/'hostapd-pids';self.pidfile.write_text('4000 4001\n')
  pidof=self.bin/'pidof';pidof.write_text('#!/bin/sh\n[ "$1" = hostapd ] || exit 1\ncat '+shlex.quote(str(self.pidfile))+'\n');pidof.chmod(0o700)
  text=self.helper.read_text()
  for name,value in [('CONFIGDIR',self.cfgdir),('DELTADIR',self.deltadir)]:
   text,n=re.subn('^'+name+'=.*$',name+'='+shlex.quote(str(value)),text,flags=re.M);assert n==1
  self.helper.write_text(text)

if not DELTA_ONLY:
 results.append({'name':'preserved_legacy_68_checks','passed':True,'details':{'count':len(g['base_results'])+len(g['new_results'])}});save()

def special_ssids():
 m=Multi()
 try:
  for p in g['profiles']:
   value=' 空 格"\\😀 '+p if p!='iot' else ' $(touch owned) " \\ '
   m.rename(p,value);run(m,profile=p)
   assert json.loads((m.state_path(p)/'config.json').read_text())['ssid']==value
   assert json.loads(m.pub(p).read_text())['ssid']==value
   assert len(m.table(p))==g['profiles'][p]['max_links']
  assert not (m.p/'owned').exists()
  return {'profiles':4,'UTF8_spaces_quotes_backslashes_preserved':True,'shell_text_not_evaluated':True}
 finally:m.close()
record('bulk_config_fields_preserve_SSID_bytes_and_no_eval',special_ssids)

def c7_plain_cycle():
 m=Multi()
 try:
  wrapper=m.bin/'c7-jq';wrapper.write_text('#!/bin/sh\nexec '+shlex.join([QEMU,'-L',str(ROOT),str(ROOT/'usr/bin/jq')])+' "$@"\n');wrapper.chmod(0o700)
  m.helper.write_text(re.sub(r'^JQ=.*$','JQ='+shlex.quote(str(wrapper)),m.helper.read_text(),flags=re.M))
  run(m,profile='iot',shell=C7)
  x=status(m,shell=C7)
  assert x['state']=='ok' and x['peer']['fresh'] and x['counts']=={'own':1,'cached_peer':1,'requested_peer':1}
  return {'shell':'C7 BusyBox ash under qemu','jq':'C7 jq under qemu','counts':x['counts']}
 finally:m.close()
if ROOT: record('actual_C7_ash_and_slim_jq_accept_delta',c7_plain_cycle)
else: print('SKIP actual C7 ash/slim jq: supply --c7-rootfs',flush=True)

def lifecycle_status():
 m=Multi();daemon=None;log=None
 try:
  log=(m.p/'daemon.log').open('wb')
  daemon=subprocess.Popen(C7+[str(m.helper),'daemon','iot'],stdout=log,stderr=log,cwd=m.p)
  until(lambda:(m.state_path('iot')/'status.json').exists())
  # Wait until the main loop is sleeping after its complete first cycle.
  def sleeping_kids():
   ids=[int(x)for x in Path(f'/proc/{daemon.pid}/task/{daemon.pid}/children').read_text().split()]
   if len(ids)!=1:return None
   kid=kidinfo(ids[0]);wchan=Path(f'/proc/{ids[0]}/wchan').read_text().strip()
   return [kid]if 'sleep'in kid['cmdline']or'nanosleep'in wchan else None
  kids=until(sleeping_kids)
  logs=[m.uci_log,m.cli_log,m.curl_log,m.actions]
  before={str(p):p.read_bytes()if p.exists()else b'' for p in logs}
  lock=m.state_path('iot')/'lock'
  with lock.open('a')as f:
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:pass
   else:raise AssertionError('daemon must hold its exclusive lock')
  x=status(m,shell=C7);assert x['state']=='ok' and x['snapshot_fresh'] and x['peer']['fresh']
  m.at(290);old=status(m,shell=C7)
  assert old['state']=='stale'and not old['snapshot_fresh']and not old['peer']['fresh']and old['peer']['age_seconds']==190
  assert before=={str(p):p.read_bytes()if p.exists()else b''for p in logs},'status invoked a control/config/network fixture'
  # tools() must also remain bypassed when reading an existing snapshot.
  saved=m.helper.read_text();m.helper.write_text(re.sub(r'^CLI=.*$','CLI=/nonexistent-fixture-CLI',saved,flags=re.M))
  assert status(m,shell=C7)['state']=='stale';m.helper.write_text(saved)
  daemon.send_signal(signal.SIGTERM);assert daemon.wait(timeout=12)==0
  assert all(not still_same(k)for k in kids),'owned background sleep survived TERM'
  with lock.open('a')as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  stopped=status(m,shell=C7);assert stopped['state']=='stopped' and not stopped['peer']['fresh']
  run(m,'preflight','iot',C7)
  return {'readonly_status_works_under_daemon_lock':True,'stale_snapshot_detected':True,'status_bypasses_tools':True,'captured_sleep_children':kids,'TERM_releases_lock_and_reaps_sleep':True,'immediate_preflight_passed':True}
 finally:
  if daemon and daemon.poll()is None:
   daemon.terminate()
   try:daemon.wait(timeout=12)
   except subprocess.TimeoutExpired:daemon.kill();daemon.wait(timeout=3)
  if log:log.close()
  m.close()
record('daemon_TERM_immediate_restart_and_lockfree_status',lifecycle_status)

def failure_states():
 m=Multi();daemon=None;log=None
 try:
  gate=m.p/'release';counter=m.p/'sleep-counter';gate.write_text('0')
  fake=m.bin/'gated-sleep'
  fake.write_text('#!'+shutil.which('python3')+'\n'+f'''from pathlib import Path
import time
c=Path({str(counter)!r});n=int(c.read_text())+1 if c.exists()else 1;c.write_text(str(n))
while int(Path({str(gate)!r}).read_text())<n:time.sleep(.02)
''');fake.chmod(0o700)
  m.helper.write_text(m.helper.read_text().replace('sleep "$delay" 9>&- &',''+shlex.quote(str(fake))+' "$delay" 9>&- &'))
  logpath=m.p/'daemon.log';log=logpath.open('wb')
  daemon=subprocess.Popen(g['SHELL']+[str(m.helper),'daemon','iot'],stdout=log,stderr=log,cwd=m.p)
  until(lambda:counter.exists()and counter.read_text()=='1')
  x=status(m);assert x['state']=='ok'and x['counts']['requested_peer']==1
  def step(index,tick):
   m.at(tick);gate.write_text(str(index));until(lambda:counter.read_text()==str(index+1));return status(m)
  (m.http/'iot.json').unlink()
  a=step(1,160);assert a['state']=='degraded'and a['reason']=='peer_download_failed'and a['peer']['fresh']and a['counts']['requested_peer']==1
  b=step(2,190);assert b['state']=='degraded'and b['peer']['fresh']
  c=step(3,280);assert not c['peer']['fresh']and c['counts']['requested_peer']==0 and m.table('iot')==[]
  m.write_peer('iot','55555555-5555-4555-8555-555555555555')
  d=step(4,310);assert d['state']=='ok'and d['peer']['fresh']and d['counts']['requested_peer']==1
  log.flush();lines=logpath.read_text().splitlines()
  assert sum('state=degraded reason=peer_download_failed'in x for x in lines)==1,lines
  assert sum('state=ok reason=ok'in x for x in lines)==2,lines
  assert d['last_error']['reason']=='peer_download_failed'
  daemon.terminate();assert daemon.wait(timeout=12)==0
  return {'cached_peer_retained_before_TTL':True,'TTL180_cleared_peer_table':True,'fresh_revision_recovered':True,'repeated_failure_logs':1,'initial_and_recovery_ok_logs':2,'historical_last_error_retained':True}
 finally:
  if daemon and daemon.poll()is None:
   daemon.terminate()
   try:daemon.wait(timeout=12)
   except subprocess.TimeoutExpired:daemon.kill();daemon.wait(timeout=3)
  if log:log.close()
  m.close()
record('status_reports_transient_error_expiry_recovery_and_transition_logs',failure_states)

def procd_reload():
 m=Multi()
 try:
  helper=m.p/'inert-profiles';helper.write_text('#!/bin/sh\n[ "$1" = profiles ] || exit 1\nprintf "%s\\n" guest\n');helper.chmod(0o700)
  init=m.p/'init';init.write_text((S/'init.sh').read_text().replace('/tmp/xr-roaming-private',str(m.state)).replace('/usr/lib/xr-roaming/helper.sh',str(helper)))
  output=m.p/'reload-sequence'
  script='''uci() { case "$*" in *guest.enabled) printf '1\\n';; *) printf '0\\n';; esac; }
procd_open_instance() { printf 'OPEN %s\\n' "$1" >> "$OUT"; }
procd_set_param() { :; }
procd_close_instance() { :; }
start() { printf 'RECONCILE\\n' >> "$OUT"; start_service; }
. "$INIT"
reload_service
'''
  q=subprocess.run(g['SHELL']+['-c',script],capture_output=True,env={**os.environ,'INIT':str(init),'OUT':str(output)},timeout=5)
  assert q.returncode==0,q.stderr.decode();assert output.read_text().splitlines()==['RECONCILE','OPEN guest']
  return {'native_reload_declares_newly_enabled_guest':True,'no_running_lock_preflight':True,'limit':'Inert service API declaration check; actual procd instance reconciliation is a root live gate.'}
 finally:m.close()
record('reload_declares_enabled_profiles_without_lock_preflight',procd_reload)

assert pins=={n:hashlib.sha256((S/n).read_bytes()).hexdigest()for n in pins},'runtime changed during final test run'
save();print(json.dumps({'passed':True,'delta_cases':len(results)-(0 if DELTA_ONLY else 1),'legacy_cases':0 if DELTA_ONLY else 68,'result':str(OUT)}))
