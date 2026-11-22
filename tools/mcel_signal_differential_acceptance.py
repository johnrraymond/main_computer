"""Patch 2: actual selective-signal vs incumbent rendering, isolated Chromium fixtures.

Two separate DOM roots and in-memory backend transports. The candidate really
suppresses eligible consumers; the incumbent never does. No production action
or scheduler cutover is authorized by this module.
"""
from __future__ import annotations
import argparse
from base64 import b64encode
from datetime import datetime,timezone
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import render_slh_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tests.test_mcel_slh_lifecycle_disable_enablement_oracle import MOCK as SLH_MOCK
from tools.mcel_task_manager_functional_parity_frob import FIXTURE_JS as TASK_MOCK
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]
GATE='<script>window.__MCEL_SIGNAL_DIFFERENTIAL_ISOLATED__=true;</script>'
DEFAULT=Path('runtime/reports/mcel-signal-differential/report.json')
EXPERIMENTAL_KINDS=('condition','display','selected-record','typed-record')
SNAPSHOT=r'''() => {
 const root=document.getElementById('mcel-workbench');const rows=[];
 let focus='outside-root',overflow=false;
 const visit=(el,path)=>{
   if(rows.length>=15000){overflow=true;return;}
   if(el===document.activeElement)focus=path;
   const attrs=Object.fromEntries([...el.attributes].filter(a=>['id','class','style','role','title','hidden','disabled','readonly','required'].includes(a.name)
     ||a.name.startsWith('aria-')||a.name.startsWith('data-mcel-')).map(a=>[a.name,a.value]));
   const css=getComputedStyle(el),r=el.getBoundingClientRect();
   const visible=!!(r.width&&r.height)&&css.visibility!=='hidden'&&css.display!=='none'&&!el.hidden;
   const input=el.matches('input,select,textarea');
   rows.push({path,tag:el.tagName,attrs,text:el.children.length?null:(el.textContent||''),
     value:input?el.value:null,checked:el.matches('input')?el.checked:null,
     selectedIndex:el.matches('select')?el.selectedIndex:null,
     scrollTop:el.scrollTop||0,scrollLeft:el.scrollLeft||0,visible,
     geometry:visible?[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]:null,
     appearance:visible?[css.display,css.visibility,css.color,css.backgroundColor,css.opacity,css.fontSize,
       css.fontWeight,css.borderTopColor,css.borderTopWidth]:null});
   [...el.children].forEach((c,i)=>visit(c,path+'/'+i));
 };
 if(root)visit(root,'0');
 const sel=getSelection();const selection=sel?.rangeCount&&root.contains(sel.anchorNode)
  ?{text:sel.toString(),offset:sel.anchorOffset}:null;
 const app=window.__mcelSlh?.app||window.__mcelWorkbench?.app;
 const state=app?.getState?.()||{};
 const transport=window.__taskRequests?.map(x=>({path:x.path,method:x.method,action:x.body?.action,
   query:x.body?.query,limit:x.body?.limit}))||window.slhCalls?.filter(x=>x.url==='/api/applications/slh/setup/action'
   &&x.body?.action!=='authenticate').map(x=>({path:x.url,action:x.body?.action}))||[];
 return {rows,focus,selection,state:Object.fromEntries(Object.keys(state).sort().map(k=>[k,state[k]])),
   receipts:(app?.receipts?.()||[]).map(x=>({intent:x.intentId,disposition:x.disposition})),transport,
   readOnlyRequests:window.slhCalls?.filter(x=>x.url!=='/api/applications/slh/setup/action'||x.body?.action==='authenticate').length||0,
   effectCount:window.__taskEffects?.length||0,overflow,signal:app?.signalCandidateReport?.()||{}};
}'''
PIXELS=r'''async ({a,b})=>{
 const load=s=>new Promise((done,reject)=>{const im=new Image();im.onload=()=>done(im);
   im.onerror=()=>reject(Error('PNG_DECODE_FAILED'));im.src='data:image/png;base64,'+s;});
 const [left,right]=await Promise.all([load(a),load(b)]);
 if(left.width!==right.width||left.height!==right.height)
   return {count:left.width*left.height,maximum:255,bounds:null};
 const canvas=document.createElement('canvas');canvas.width=left.width;canvas.height=left.height;
 const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(left,0,0);
 const pa=ctx.getImageData(0,0,left.width,left.height).data;
 ctx.clearRect(0,0,left.width,left.height);ctx.drawImage(right,0,0);
 const pb=ctx.getImageData(0,0,left.width,left.height).data;
 let count=0,maximum=0,x0=left.width,y0=left.height,x1=0,y1=0;
 for(let i=0;i<pa.length;i+=4){const d=Math.max(Math.abs(pa[i]-pb[i]),Math.abs(pa[i+1]-pb[i+1]),Math.abs(pa[i+2]-pb[i+2]));
   if(!d)continue;count++;maximum=Math.max(maximum,d);
   const n=i/4,x=n%left.width,y=Math.floor(n/left.width);
   x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x+1);y1=Math.max(y1,y+1);
 }
 return {count,maximum,bounds:count?[x0,y0,x1,y1]:null};
}'''

def _html(app,candidate,repo=ROOT,*,candidate_kinds=None,candidate_mode='isolated'):
 if app=='slh':
  html=render_slh_page(repo=repo)
  marker='navigationChrome:document.getElementById("mcel-slh-navigation-context")'
 elif app=='task-manager':
  html=render_semantic_page('task-manager',repo,promoted=True)
  marker='workspacePlan:workspacePlan||undefined,'
 else:raise ValueError('UNSUPPORTED_APPLICATION')
 if candidate_mode not in ('isolated','integrated'):
  raise ValueError('INVALID_CANDIDATE_MODE')
 if candidate_mode=='isolated':
  # Patch 5 defaults on at the real host. Legacy isolated differential tests
  # must still use an incumbent reference, never a default-certified page.
  needle='signalSchedulerStartEnabled:'
  assert html.count(needle)==1,'MOUNT_CONTRACT_CHANGED'
  import re
  html,n=re.subn(r'signalSchedulerStartEnabled:(?:true|false)',needle+'false',html)
  assert n==1,'INCUMBENT_REFERENCE_CONTRACT_CHANGED'
 if candidate_mode=='integrated':
  if candidate_kinds is not None:
   if (not isinstance(candidate_kinds,(tuple,list)) or not candidate_kinds or
       len(set(candidate_kinds))!=len(candidate_kinds) or
       any(kind not in EXPERIMENTAL_KINDS for kind in candidate_kinds)):
    raise ValueError('INVALID_EXPERIMENTAL_CONSUMER_CLASSES')
  # The unchanged server-generated page is the actual application mount.
  # Normalize the literal opt-in so a test runner's inherited environment
  # cannot accidentally enable the incumbent or disable the candidate.
  needle='signalSchedulerStartEnabled:'
  assert html.count(needle)==1,'MAIN_PATH_MOUNT_CONTRACT_CHANGED'
  import re
  html,n=re.subn(r'signalSchedulerStartEnabled:(?:true|false)',
     needle+('true' if candidate else 'false'),html)
  assert n==1,'MAIN_PATH_OPT_IN_CONTRACT_CHANGED'
  if candidate and candidate_kinds is not None:
   # A class-specific experiment uses the normal runtime's explicit selective
   # policy option. It must not activate the isolated scheduler gate.
   assert html.count(needle)==1
   html=html.replace(needle,'signalSchedulerCertifiedKinds:'+json.dumps(list(candidate_kinds))+','+needle)
 if candidate and candidate_mode=='isolated':

  if candidate_kinds is not None:
   if (not isinstance(candidate_kinds,(tuple,list)) or not candidate_kinds or
       len(set(candidate_kinds))!=len(candidate_kinds) or
       any(kind not in EXPERIMENTAL_KINDS for kind in candidate_kinds)):
    raise ValueError('INVALID_EXPERIMENTAL_CONSUMER_CLASSES')
  assert html.count(marker)==1,'MOUNT_CONTRACT_CHANGED'
  class_option=('signalCandidateTestKinds:'+json.dumps(list(candidate_kinds))+','
                if candidate_kinds is not None else '')
  html=html.replace(marker,'signalCandidateIsolatedTest:true,'+class_option+marker)
  assert html.count('</head>')==1
  html=html.replace('</head>',GATE+'</head>')
 return html

def _diff(a,b,limit=24):
 out=[];total=0
 def compare(path,x,y):
  nonlocal total
  if x==y:return
  if isinstance(x,dict) and isinstance(y,dict):
   for key in sorted(x.keys()|y.keys()):compare(path+'.'+key,x.get(key,'<missing>'),y.get(key,'<missing>'))
  elif isinstance(x,list) and isinstance(y,list):
   if len(x)!=len(y):
    total+=1
    if len(out)<limit:out.append({'path':path+'.length','incumbent':len(x),'candidate':len(y)})
   for i in range(min(len(x),len(y))):compare(path+f'[{i}]',x[i],y[i])
  else:
   total+=1
   if len(out)<limit:out.append({'path':path,'incumbent':x,'candidate':y})
 for key in ('rows','focus','selection','state','receipts','transport','effectCount','overflow'):
  compare(key,a.get(key),b.get(key))
 return total,out

def run_differential(app,repo=ROOT,*,candidate_kinds=None,stress=False,require_skips=True,
                     candidate_mode='isolated'):
 errors={'incumbent':[],'candidate':[]};stages=[]
 with sync_playwright() as pw:
  browser,source=launch_semantic_browser(pw)
  try:
   pages={}
   def mount_page(role):
    page=browser.new_page(viewport={'width':1240,'height':900},device_scale_factor=1)
    page.set_default_timeout(12000)
    page.on('pageerror',lambda e,r=role:errors[r].append(str(e)))
    page.on('dialog',lambda d:d.accept())
    page.evaluate(SLH_MOCK if app=='slh' else TASK_MOCK)
    if app=='slh' and stress:
     # The 15-second SLH health poll otherwise fires at different wall-clock
     # points in sequentially mounted browsers. Hold only that timer, then
     # replay its REAL callback identically in both roots below.
     page.evaluate('''() => {
       const native=window.setInterval.bind(window);
       const clear=window.clearInterval.bind(window);
       const held=[];
       window.__mcelSignalHeldHealthPolls=held;
       window.setInterval=(fn,ms,...args)=>{
         if(ms===15000){const id=-9000-held.length;held.push({id,fn});return id;}
         return native(fn,ms,...args);
       };
       window.clearInterval=(id)=>{
         const index=held.findIndex(entry=>entry.id===id);
         if(index>=0){held.splice(index,1);return;}
         return clear(id);
       };
     }''')
    # The semantic read-port captures its fetcher at mount. Install the
    # fault-injection wrapper *before* mounting, then trigger it by flag.
    page.evaluate('''() => {
      const actual=window.fetch;
      window.fetch=(url,options)=>{
        if(window.__mcelSignalFailNext===true &&
           String(url).includes(window.__mcelSignalFailPath||'__no_match__')){
          window.__mcelSignalFailNext=false;
          return Promise.reject(Error('ISOLATED_SIGNAL_CERTIFICATION_INJECTED_FETCH_FAILURE'));
        }
        return actual(url,options);
      };
    }''')
    page.set_content(_html(app,role=='candidate',repo,candidate_kinds=candidate_kinds,
                            candidate_mode=candidate_mode))
    key='__mcelSlh' if app=='slh' else '__mcelWorkbench'
    page.wait_for_function(f'()=>!!window.{key}?.app')
    return page
   for role in ('incumbent','candidate'):pages[role]=mount_page(role)
   if app=='slh':
    for page in pages.values():page.wait_for_function("()=>window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='not_configured'")
   else:
    for page in pages.values():page.wait_for_function("()=>window.__mcelWorkbench.app.receipts().some(r=>r.intentId==='intent:refresh'&&r.disposition==='completed')")
   cumulative={'skips':0,'executions':0,'byClass':{},'consumerIds':set()}
   def record(phase):
    for page in pages.values():
     page.mouse.move(0,0)
     page.evaluate("async()=>{await document.fonts.ready;await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)))}")
     page.wait_for_timeout(90)
    reference=pages['incumbent'].evaluate(SNAPSHOT);candidate=pages['candidate'].evaluate(SNAPSHOT)
    count,diffs=_diff(reference,candidate)
    a=pages['incumbent'].screenshot(animations='disabled',caret='hide')
    b=pages['candidate'].screenshot(animations='disabled',caret='hide')
    pixels=pages['incumbent'].evaluate(PIXELS,{'a':b64encode(a).decode('ascii'),'b':b64encode(b).decode('ascii')})
    # Chromium can vary a handful of antialiasing pixels without any DOM,
    # style or geometry difference. Record this rather than claiming exact pixels.
    micro=0<pixels['count']<=512 and pixels['maximum']<=24
    stage={'phase':phase,'differenceCount':count,'differences':diffs,
      'pixelDifferences':pixels['count'],'pixelMaximumChannelDelta':pixels['maximum'],
      'pixelBounds':pixels['bounds'],'microRasterVariance':micro,
      'status':'PASS' if count==0 and (pixels['count']==0 or micro) else 'DIVERGED',
      'actualSkips':candidate['signal'].get('statistics',{}).get('skips',0),
      'candidateExecutions':candidate['signal'].get('statistics',{}).get('executions',0),
      'candidateRecentRevisions':candidate['signal'].get('history',[])[-3:],
      'incumbentReceipts':len(reference['receipts']),'candidateReceipts':len(candidate['receipts']),
      'incumbentReadOnlyRequests':reference['readOnlyRequests'],
      'candidateReadOnlyRequests':candidate['readOnlyRequests']}
    currentCount=candidate['signal'].get('statistics',{}).get('skips',0)
    precedingCount=stages[-1].get('mountCumulativeSkips',0) if stages else 0
    if stages and stages[-1].get('mountGeneration')!=cumulative.get('generation',0):precedingCount=0
    stage['mountCumulativeSkips']=currentCount
    stage['mountGeneration']=cumulative.get('generation',0)
    stage['actualSkipDelta']=max(0,currentCount-precedingCount)
    cumulative['skips']+=stage['actualSkipDelta']
    for consumer in candidate['signal'].get('consumers',[]):
     cumulative['consumerIds'].add(consumer['id'])
    for entry in candidate['signal'].get('history',[]):
     if entry.get('actualSkipConsumers'):
      for detail in entry['actualSkipConsumers']:
       className=detail['id'].split('/',1)[0]
       # Count unique revisions on this mount once, not repeated stage tails.
       marker=(cumulative.get('generation',0),entry.get('revision'),detail['id'])
       seen=cumulative.setdefault('skipSeen',set())
       if marker not in seen:
        seen.add(marker)
        cumulative['byClass'][className]=cumulative['byClass'].get(className,0)+1
    stages.append(stage)
   record('mounted-and-initial-read')
   if app=='slh':
    actions=[('seed-running','intent:refresh-lifecycle','running'),
      ('stop','intent:toggle-organization','stopped'),('start','intent:toggle-organization','running'),
      ('disable','intent:toggle-organization-enablement','disabled'),
      ('enable','intent:toggle-organization-enablement','running')]
    for phase,intent,expected in actions:
     for role,page in pages.items():
      if phase=='seed-running':page.evaluate("()=>{window.lifecycle='running'}")
      result=page.evaluate('(i)=>window.__mcelSlh.app.execute(i)',intent)
      if result['disposition']!='completed':errors[role].append(phase+':'+str(result['disposition']))
      page.wait_for_function("v=>window.__mcelSlh.app.readState('state:organization-lifecycle')?.state===v",arg=expected)
     record(phase)
   else:
    actions=[('manual-refresh','intent:refresh','server-processes'),
     ('connections','intent:refresh-connections','connections'),
     ('hardware','intent:refresh-hardware','cpu-gpu'),('schedules','intent:list-schedules',None)]
    for phase,intent,view in actions:
     for role,page in pages.items():
      before=page.evaluate('window.__mcelWorkbench.app.receipts().length')
      if view:
       if view!='server-processes':page.locator(f'[data-mcel-mode-tabs] [data-value="{view}"]').click()
       form=page.locator(f'form[data-intent="{intent}"]')
       if phase=='manual-refresh':
        form.locator('[name=query]').fill('disposable');form.locator('[name=limit]').fill('17')
       form.locator('button[type=submit]').click()
       page.wait_for_function("({n,i})=>window.__mcelWorkbench.app.receipts().slice(n).some(r=>r.intentId===i&&r.disposition==='completed')",arg={'n':before,'i':intent})
      else:
       result=page.evaluate('(i)=>window.__mcelWorkbench.app.execute(i)',intent)
       if result['disposition']!='completed':errors[role].append(phase+':'+str(result['disposition']))
     record(phase)
    for role,page in pages.items():page.evaluate('async()=>await window.__taskIntervals.find(t=>t.ms===5000).fn()')
    record('automatic-poll')
   if stress:
    # Disturb authoritative observations independently of the scheduler. The
    # same API actions and response order are replayed in two separate roots.
    if app=='slh':
     for role,page in pages.items():
      page.evaluate("()=>{window.lifecycle='failed'}")
      r=page.evaluate("()=>window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
      if r['disposition']!='completed':errors[role].append('fault-observation:'+str(r['disposition']))
     record('fault-observation')
     for role,page in pages.items():
      page.evaluate("()=>{window.lifecycle='running'}")
      r=page.evaluate("()=>window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
      if r['disposition']!='completed':errors[role].append('observation-recovery:'+str(r['disposition']))
     record('observation-recovery')
     # Replay the real health-poll callback at an identical checkpoint for both
     # roots. The observable requests and receipts are still strictly compared.
     for role,page in pages.items():
      count=page.evaluate('()=>window.__mcelSlh.app.receipts().length')
      available=page.evaluate('()=>window.__mcelSignalHeldHealthPolls?.length===1')
      if not available:
       errors[role].append('HEALTH_POLL_NOT_HELD')
       continue
      page.evaluate('()=>window.__mcelSignalHeldHealthPolls[0].fn()')
      page.wait_for_function('n=>window.__mcelSlh.app.receipts().length>=n+2',arg=count)
     record('controlled-health-poll')
     failing_intent='intent:refresh-lifecycle'
     fail_url='/api/applications/slh/observe?view=overview'
    else:
     for role,page in pages.items():
      page.evaluate("()=>{window.__taskCase.gpuMissing=true}")
      page.evaluate("async()=>await window.__taskIntervals.find(t=>t.ms===5000).fn()")
     record('fault-observation')
     for role,page in pages.items():
      page.evaluate("()=>{window.__taskCase.gpuMissing=false}")
      page.evaluate("async()=>await window.__taskIntervals.find(t=>t.ms===5000).fn()")
     record('observation-recovery')
     failing_intent='intent:list-schedules'
     fail_url='/schedules'
    failureDispositions={}
    for role,page in pages.items():
     page.evaluate("(path)=>{window.__mcelSignalFailPath=path;window.__mcelSignalFailNext=true}",fail_url)
     # A deliberately failed read must be handled identically. The next read
     # must recover without leaving stale presentation or missing receipts.
     failed=page.evaluate('(i)=>window.__mcelSlh?.app.execute(i)||window.__mcelWorkbench.app.execute(i)',failing_intent)
     triggered=page.evaluate('()=>window.__mcelSignalFailNext===false')
     failureDispositions[role]={'injectedFetchRejected':triggered,'receiptDisposition':failed['disposition']}
     if not triggered:
      errors[role].append('TRANSPORT_FAILURE_NOT_EXERCISED:'+str(failed['disposition']))
    record('transport-failure')
    stages[-1]['injectedFailureOutcomes']=failureDispositions
    for role,page in pages.items():
     r=page.evaluate('(i)=>window.__mcelSlh?.app.execute(i)||window.__mcelWorkbench.app.execute(i)',failing_intent)
     if r['disposition']!='completed':errors[role].append('transport-recovery:'+str(r['disposition']))
    record('transport-recovery')
    # Deliberately unmount both roots, verify subscriptions are discarded,
    # then mount fresh instances. Never re-use old subscription identities.
    for role,page in pages.items():
     key='__mcelSlh' if app=='slh' else '__mcelWorkbench'
     result=page.evaluate('''(key)=>{
       const a=window[key].app;
       const before=a.signalCandidateReport();
       a.unmount();
       const after=a.signalCandidateReport();
       return {before:before.consumerCount,after:after.consumerCount,
         empty:document.getElementById('mcel-workbench').childElementCount===0};
     }''',key)
     if not result['empty'] or (role=='candidate' and result['after']!=0):
      errors[role].append('UNMOUNT_SUBSCRIPTION_LEAK:'+str(result))
     page.close()
    cumulative['generation']=cumulative.get('generation',0)+1
    pages={role:mount_page(role) for role in ('incumbent','candidate')}
    if app=='slh':
     for page in pages.values():page.wait_for_function(
      "()=>window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='not_configured'")
     for role,page in pages.items():
      page.evaluate("()=>{window.lifecycle='running'}")
      r=page.evaluate("()=>window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
      if r['disposition']!='completed':errors[role].append('remount-refresh:'+str(r['disposition']))
    else:
     for page in pages.values():page.wait_for_function(
      "()=>window.__mcelWorkbench.app.receipts().some(r=>r.intentId==='intent:refresh'&&r.disposition==='completed')")
     for role,page in pages.items():
      r=page.evaluate("()=>window.__mcelWorkbench.app.execute('intent:list-schedules')")
      if r['disposition']!='completed':errors[role].append('remount-refresh:'+str(r['disposition']))
    record('remount-and-refresh')
   actual=pages['candidate'].evaluate(SNAPSHOT)['signal']
   incumbent=pages['incumbent'].evaluate(SNAPSHOT)['signal']
   failures=[]
   if any(s['differenceCount'] for s in stages):failures.append('SEMANTIC_DOM_OR_OPERATION_DIVERGENCE')
   if any(s['pixelDifferences'] and not s['microRasterVariance'] for s in stages):failures.append('MATERIAL_PIXEL_DIVERGENCE')
   if any(errors.values()):failures.append('BROWSER_OR_OPERATION_ERROR')
   if incumbent.get('mode','disabled')!='disabled':failures.append('INCUMBENT_GATE_VIOLATION')
   expected_mode='integrated-opt-in' if candidate_mode=='integrated' else 'isolated-differential'
   if actual.get('mode')!=expected_mode:failures.append('CANDIDATE_NOT_ENABLED')
   if candidate_mode=='integrated' and actual.get('integrationEnabled') is not True:
    failures.append('INTEGRATED_MAIN_PATH_NOT_ENABLED')
   if require_skips and cumulative['skips']<=0:failures.append('NO_ACTUAL_SIGNAL_SKIPS')
   if candidate_kinds is not None:
    unauthorized=[kind for kind in cumulative['byClass'] if kind not in candidate_kinds]
    if unauthorized:failures.append('UNAUTHORIZED_CLASS_SKIPPED:'+','.join(sorted(unauthorized)))
    if cumulative['skips']!=sum(cumulative['byClass'].values()):
     failures.append('SKIP_CLASS_ACCOUNTING_INCOMPLETE')
   return {'schema':'mcel.signal-differential.v1','app':app,'status':'PASS_DIFFERENTIAL' if not failures else 'FAIL_DIFFERENTIAL',
    'failures':failures,'errors':errors,'stages':stages,'browser':source,
    'candidate':{'actualSkips':cumulative['skips'],
      'actualSkipsByClass':dict(sorted(cumulative['byClass'].items())),
      'observedConsumersByClass':{kind:sum(1 for ident in cumulative['consumerIds'] if ident.startswith(kind+'/')) for kind in EXPERIMENTAL_KINDS},
      'executions':actual.get('statistics',{}).get('executions',0),'mode':actual.get('mode'),
      'experimentalClasses':list(candidate_kinds if candidate_kinds is not None else EXPERIMENTAL_KINDS)},
    'boundaries':{'hostMutations':'NEVER_DISPATCHED','backendTransport':'TWO_ISOLATED_IN_MEMORY_FIXTURES',
      'productionSchedulerChanged':False,'mainPathOptInExercised':candidate_mode=='integrated',
      'selectiveSchedulingCertified':False,
      'strictPixelParityCertified':False,'microRasterTolerance':{'pixels':512,'maxChannelDelta':24},
      'realWindowsHostAcceptance':'NOT_TESTED'}}
  finally:browser.close()

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--app',choices=('slh','task-manager','both'),default='both')
 parser.add_argument('--candidate-mode',choices=('isolated','integrated'),default='isolated')
 parser.add_argument('--stress',action='store_true',help='include failure, recovery and unmount/remount')
 parser.add_argument('--output',type=Path,default=DEFAULT)
 args=parser.parse_args(argv)
 results=[]
 for name in (('slh','task-manager') if args.app=='both' else (args.app,)):
  try:result=run_differential(name,candidate_mode=args.candidate_mode,stress=args.stress)
  except Exception as exc:result={'app':name,'status':'FAIL_DIFFERENTIAL','failures':[type(exc).__name__+': '+str(exc)[:1000]]}
  results.append(result)
  print('MCEL_SIGNAL_DIFFERENTIAL',name,result['status'],'skips='+str(result.get('candidate',{}).get('actualSkips',0)),
    'differences='+str(sum(s['differenceCount'] for s in result.get('stages',[]))),flush=True)
 report={'schema':'mcel.signal-differential.v1','generatedAt':datetime.now(timezone.utc).isoformat(),
  'status':'PASS' if all(x['status']=='PASS_DIFFERENTIAL' for x in results) else 'FAIL',
  'results':results,'productionSchedulerChanged':False,'selectiveSchedulingCertified':False}
 args.output.parent.mkdir(parents=True,exist_ok=True)
 args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf8')
 print('MCEL_SIGNAL_DIFFERENTIAL_OVERALL',report['status'],'report:',args.output)
 return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
