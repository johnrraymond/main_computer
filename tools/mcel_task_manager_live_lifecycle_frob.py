#!/usr/bin/env python3
"""Patch 10: independent read-only lifecycle acceptance.

Default: real Task Manager host markup with synthetic API-like snapshots, and a
cross-domain collection. --host-url adds actual network/browser acceptance;
absence of a running host is BLOCKED, never a substitute PASS.
"""
from __future__ import annotations
import argparse, hashlib, json, shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'
REPORT=ROOT/'runtime/reports/mcel-task-manager-live-lifecycle'
WIDTHS=(320,390,560,1100)



# The host can throw non-Error JavaScript values (including DOM Event objects).
# Playwright renders these as "Uncaught [object Event]", losing their origin.
# Preserve the CDP exception location and page-side event provenance without
# weakening the hosted `browser-page-errors` acceptance gate.
def compact_cdp_exception(message):
    details=(message or {}).get('exceptionDetails') or {}
    remote=details.get('exception') or {}
    stack=details.get('stackTrace') or {}
    frames=(stack.get('callFrames') or [])[:6]
    return {
        'text':str(details.get('text') or '')[:600],
        'url':str(details.get('url') or '')[:800],
        'line':details.get('lineNumber'),
        'column':details.get('columnNumber'),
        'type':str(remote.get('type') or '')[:60],
        'className':str(remote.get('className') or '')[:100],
        'description':str(remote.get('description') or '')[:1200],
        'frames':[
            {'url':str(f.get('url') or '')[:800],
             'function':str(f.get('functionName') or '')[:150],
             'line':f.get('lineNumber'), 'column':f.get('columnNumber')}
            for f in frames
        ],
    }


_HOST_EVENT_PROBE = r"""(() => {
  if (window.__mcelLifecycleErrorProbe) return;
  const events = [];
  const push = item => {
    if (events.length >= 60) events.shift();
    events.push({time: Math.round(performance.now()), ...item});
  };
  const valueType = value => {
    try { return Object.prototype.toString.call(value); }
    catch (_) { return 'uninspectable'; }
  };
  window.addEventListener('error', event => {
    if (event instanceof ErrorEvent) {
      const error = event.error;
      push({kind:'uncaught-error', message:String(event.message || '').slice(0,1000),
        filename:String(event.filename || '').slice(0,800), line:event.lineno, column:event.colno,
        errorType:valueType(error), exceptionEventType:String(error?.type || '').slice(0,120),
        stack:String(error?.stack || '').slice(0,1800)});
    } else {
      const target=event.target;
      push({kind:'resource-error', tag:String(target?.tagName || '').slice(0,50),
        source:String(target?.currentSrc || target?.src || target?.href || '').slice(0,800)});
    }
  }, true);
  window.addEventListener('unhandledrejection', event => {
    const reason=event.reason;
    push({kind:'unhandled-rejection',reasonType:valueType(reason),
      reasonEventType:String(reason?.type || '').slice(0,120),
      message:String(reason?.message || reason || '').slice(0,1000),
      stack:String(reason?.stack || '').slice(0,1800)});
  }, true);
  Object.defineProperty(window,'__mcelLifecycleErrorProbe', {
    value:Object.freeze({events:()=>events.slice()}),configurable:false});
})()"""


def hosted_error_evidence(page):
    """Read diagnostics without mutating the application or clearing its logs."""
    try:
        return page.evaluate("() => ({captured:window.__mcelLifecycleErrorProbe?.events() || [], early:window.__MCELDiagnosticEarly?.events() || []})")
    except Exception as exc:
        return {'captureError':str(exc)[:300]}


def hosted_error_origin_hints(cdp_exceptions, browser_events):
    hints=[]
    for item in cdp_exceptions:
        loc=item.get('frames') or []
        url=(loc[0].get('url') if loc else None) or item.get('url') or ''
        if url:
            hints.append('CDP exception origin: '+str(url)[:300]+
                         (':' + str(loc[0].get('line')) if loc else ''))
    for item in (browser_events or {}).get('captured',[]):
        if item.get('kind')=='unhandled-rejection' and item.get('reasonType')=='[object Event]':
            hints.append('A rejected DOM Event was not handled; inspect the attached rejection context and script origin.')
        if item.get('kind')=='resource-error' and item.get('source'):
            hints.append('Failed resource: '+str(item['source'])[:300])
    return list(dict.fromkeys(hints))[:12]

def hosted_gate_checks(http_status, initial, refresh, page_errors):
    """Explain every hosted acceptance assertion without relaxing any gate.

    A failing live check must stay a FAIL, not be buried inside a single boolean.
    This is pure so missing/stale-build and refresh-race cases can be regression-tested.
    """
    initial=initial or {}
    refresh=refresh or {}
    receipt=refresh.get('report') or {}
    same=bool(refresh.get('stillSameGeneration'))
    return {
        'http-200': http_status == 200,
        'cold-source-identity-parity': initial.get('identical') is True and
            initial.get('sourceCount') == initial.get('renderedCount'),
        'cold-publish-receipt': initial.get('delivered') is True,
        'running-bridge-build': initial.get('build') == 'mcel-task-notebook-lifecycle-p10.v1',
        'second-refresh-no-error': not refresh.get('refreshError'),
        'second-refresh-published': isinstance(refresh.get('before'),int) and
            isinstance(refresh.get('after'),int) and refresh['after'] > refresh['before'],
        'second-source-identity-parity': refresh.get('parity') is True,
        'second-publish-receipt': receipt.get('ok') is True,
        'same-generation-dom-continuity': not same or refresh.get('continuous') is True,
        'same-generation-focus-continuity': not same or refresh.get('focused') is True,
        'same-generation-inspection-continuity': not same or refresh.get('inspectorPreserved') is True,
        'browser-page-errors': not page_errors,
    }


def hosted_failure_hints(failed):
    """Likely next investigation, never asserted to be the cause without evidence."""
    hints=[]
    if 'running-bridge-build' in failed:
        hints.append('Running server/browser may still serve an earlier patch; restart app and hard-reload.')
    if 'cold-publish-receipt' in failed or 'second-publish-receipt' in failed:
        hints.append('Inspect bridge lastReceipt, including refusal code and source status.')
    if 'second-refresh-published' in failed:
        hints.append('Refresh did not publish after the explicit request; inspect HTTP/API errors and coalesced refresh queue.')
    if 'cold-source-identity-parity' in failed or 'second-source-identity-parity' in failed:
        hints.append('Compare source PIDs with the semantic mcel-row data-row-id set.')
    if any(name.startswith('same-generation-') for name in failed):
        hints.append('Keyed continuity was broken for an unchanged record; inspect generation, DOM, focus and inspector.')
    if 'browser-page-errors' in failed:
        hints.append('Review captured pageErrors for an exception affecting the hosted page.')
    return hints


def run(output=REPORT,host_url=None):
    from playwright.sync_api import sync_playwright
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    sources={n:hashlib.sha256((SCRIPTS/n).read_bytes()).hexdigest() for n in (
        'mcel-semantic-value-contract.js','mcel-semantic-collection-workspace.js',
        'mcel-task-manager-notebook-bridge.js','mcel-task-manager-process-contract.js',
        'mcel-tab-workspace.js')}
    report={'schema':'mcel.task-manager-live-lifecycle.v1','createdUtc':datetime.now(timezone.utc).isoformat(),
        'offline':'FAIL','hosted':'BLOCKED','hostReason':'No --host-url provided',
        'sourceSha256':sources,'cases':[],'operatorActionsExecuted':False}
    with sync_playwright() as p:
      exe=shutil.which('chromium') or shutil.which('chromium-browser')
      browser=p.chromium.launch(headless=True,executable_path=exe,args=['--no-sandbox','--disable-dev-shm-usage'] if exe else [])
      try:
        for width in WIDTHS:
          page=browser.new_page(viewport={'width':width,'height':800})
          errors=[];requests=[]
          page.on('pageerror',lambda err:errors.append(str(err)))
          page.on('request',lambda req:requests.append(req.url))
          page.set_content(html_fixture())
          observations=page.evaluate('''() => {
            const root=document.querySelector('#task-manager-app');
            const raw=(pid,gen,mem,quality,cmd)=>({pid,name:'worker-'+pid,
              status:'',status_availability:'unavailable',cmdline:cmd||'C:\\\\Work\\\\svc --full',
              memory_rss:mem,memory_availability:quality,is_main_computer:false,
              is_current_process:pid===101,created_at:gen});
            const initial=[raw(100,'g100',null,'unavailable'),raw(101,'g101',0,'present'),raw(102,'g102',512,'present')];
            const external={processes:initial,all_processes:initial,connections:[],hardware:{}};
            const mount=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=lab');
            const first=McelTaskManagerNotebookLab.publish(external,'available',1,
              {streams:{processes:'available',all_processes:'available',connections:'empty',hardware:'empty'}});
            const panel=root.querySelector('[data-mcel-panel="server-processes"]');
            const host=panel.querySelector('.mcel-semantic-collection');
            const collection=host.querySelector('mcel-collection');
            const row=id=>collection.querySelector('mcel-row[data-row-id="'+id+'"]');
            const zero=row('101').querySelector('[data-semantic-field="memory"] .field-value').textContent;
            const unknown=row('100').querySelector('[data-semantic-field="memory"] .field-value').textContent;
            row('101').click();row('101').focus();
            const before=row('101');const originalCmd=initial[1].cmdline;
            const changed=[raw(103,'g103',0,'present'),raw(101,'g101',256,'present',originalCmd+' --updated'),raw(100,'g100',null,'unavailable')];
            const second=McelTaskManagerNotebookLab.publish({processes:changed,all_processes:changed,
              connections:[],hardware:{}},'available',2);
            const persisted=before===row('101');
            const focused=document.activeElement===before;
            const inspected=host.querySelector('.mcel-semantic-collection__inspector').textContent;
            const full=inspected.includes('--updated') && inspected.includes('256');
            const absent=!row('102') && !!row('103');
            const stale=McelTaskManagerNotebookLab.publish(external,'available',1);
            const duplicate=McelTaskManagerNotebookLab.publish({processes:[changed[1],changed[1]],
              all_processes:changed,connections:[],hardware:{}},'available',3);
            const afterDuplicate=McelTaskManagerNotebookLab.status().version;
            const partial=McelTaskManagerNotebookLab.publish({processes:changed,all_processes:[],
              connections:[],hardware:{}},'available',3,
              {streams:{processes:'available',all_processes:'failed',connections:'empty',hardware:'empty'}});
            const allPanel=root.querySelector('[data-mcel-panel="all-processes"]');
            const retained=allPanel.querySelectorAll('mcel-row').length===3;
            const partialText=allPanel.querySelector('.mcel-tab-workspace__source-status')?.textContent||'';
            const reused=[raw(103,'g103',0,'present'),raw(101,'DIFFERENT_GENERATION',4096,'present'),raw(100,'g100',null,'unavailable')];
            const reuse=McelTaskManagerNotebookLab.publish({processes:reused,all_processes:reused,
              connections:[],hardware:{}},'available',4);
            const protectedReuse=before!==row('101') &&
              !host.querySelector('.mcel-semantic-collection__inspector').textContent.includes('--updated');
            const bridge=McelTaskManagerNotebookLab.status();
            const finalIds=[...collection.querySelectorAll('mcel-row')].map(r=>r.dataset.rowId).sort();
            const exact=JSON.stringify(finalIds)===JSON.stringify(['100','101','103']);
            const sourceUnchanged=initial[1].memory_rss===0 && initial[0].memory_rss===null;
            const legacy=!!root.querySelector('.task-notebook');
            const rollback=McelTaskManagerNotebookLab.unmount(root);
            // Independent domain, identical keyed lifecycle engine; renaming
            // its source fields does not require changing the shared consumer.
            const spec={schema:'mcel.semantic-collection-spec.v2',identity:'ident',
              sourceFields:['idValue','titleText','generation','bytes','quality'],
              reconciliation:{generationFrom:'generation'},fields:[
                {key:'ident',from:'idValue',kind:'identifier',tier:'primary',label:'Identity'},
                {key:'title',from:'titleText',kind:'text',tier:'primary',label:'Title'},
                {key:'weight',from:'bytes',kind:'quantity',tier:'secondary',label:'Weight',
                  validity:{availabilityFrom:'quality'},presentation:{units:'bytes'}}]};
            const fixture=[{idValue:'parcel-A',titleText:'Parcel',generation:'g1',bytes:0,quality:'present'}];
            const div=document.createElement('div');div.style.width='100%';root.append(div);
            const m=McelSemanticCollectionWorkspace.mount(div,fixture,spec,{observeResize:false});
            const original=div.querySelector('mcel-row');original.focus();
            const update=m.update([{...fixture[0],bytes:1024}]);
            const generic=update.ok && original===div.querySelector('mcel-row') &&
              div.querySelector('[data-semantic-field="weight"] .field-value').textContent==='1.0 KB';
            m.destroy();div.remove();
            return {mount,first,zero,unknown,second,persisted,focused,full,absent,
              stale,duplicate,afterDuplicate,partial,retained,partialText,
              reuse,protectedReuse,bridge,exact,finalIds,sourceUnchanged,legacy,rollback,generic,
              controls:root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').length};
          }''')
          passed=(observations['mount']['ok'] and observations['first']['ok'] and
                  observations['zero']=='0 B' and observations['unknown']=='Unavailable' and
                  observations['persisted'] and observations['focused'] and observations['full'] and
                  observations['absent'] and observations['stale']['code']=='stale-snapshot' and
                  observations['duplicate']['code']=='renderer-update-refused' and
                  observations['afterDuplicate']==2 and observations['partial']['status']=='partial' and
                  observations['retained'] and 'unavailable' in observations['partialText'].lower() and
                  observations['reuse']['ok'] and observations['protectedReuse'] and
                  observations['exact'] and observations['sourceUnchanged'] and
                  observations['legacy'] and observations['rollback']['ok'] and
                  observations['generic'] and observations['controls']==5 and not errors and not requests)
          report['cases'].append({'width':width,'status':'PASS' if passed else 'FAIL',
             'evidence':observations,'pageErrors':errors,'networkRequests':requests})
          page.close()
        report['offline']='PASS' if all(case['status']=='PASS' for case in report['cases']) else 'FAIL'
        if host_url:
          try:
            live=browser.new_page(viewport={'width':1100,'height':800})
            failures=[]
            cdp_exceptions=[]
            console_errors=[]
            failed_requests=[]
            live.add_init_script(script=_HOST_EVENT_PROBE)
            live.on('pageerror',lambda err:failures.append(str(err)))
            live.on('console',lambda msg:console_errors.append({
                'text':msg.text[:1000], 'location':msg.location}) if msg.type=='error' and len(console_errors)<30 else None)
            live.on('requestfailed',lambda req:failed_requests.append({
                'url':req.url.split('?',1)[0][:800],
                'failure':req.failure}) if len(failed_requests)<30 else None)
            # Runtime.exceptionThrown retains the script URL and stack even
            # when the thrown value is a DOM Event rather than an Error.
            cdp=live.context.new_cdp_session(live)
            cdp.on('Runtime.exceptionThrown',lambda msg:cdp_exceptions.append(
                compact_cdp_exception(msg)) if len(cdp_exceptions)<30 else None)
            cdp.send('Runtime.enable')
            response=live.goto(host_url,wait_until='domcontentloaded',timeout=12000)
            live.wait_for_function('''() => window.McelTaskManagerNotebookLab?.status()?.mounted &&
              typeof taskManagerSnapshotCache!=='undefined' &&
              Array.isArray(taskManagerSnapshotCache?.processes)''',timeout=12000)
            live.wait_for_function('''() => document.querySelector(
              '[data-mcel-panel="server-processes"] mcel-row')!==null''',timeout=12000)
            result=live.evaluate('''() => {
              const source=taskManagerSnapshotCache.processes;
              const rendered=[...document.querySelectorAll('[data-mcel-panel="server-processes"] mcel-row')]
                .map(n=>n.dataset.rowId).sort();
              const expected=source.map(r=>String(r.pid)).sort();
              const state=McelTaskManagerNotebookLab.status();
              return {sourceCount:source.length,renderedCount:rendered.length,
                identical:JSON.stringify(expected)===JSON.stringify(rendered),
                build:state.build,delivered:state.lastReceipt?.ok===true,
                version:state.version};}''')
            # A real second API refresh is required; a cold-load snapshot
            # alone cannot certify state-preserving hosted delivery.
            followup=live.evaluate('''async () => {
              const root=document.querySelector('#task-manager-app');
              const panel=root.querySelector('[data-mcel-panel="server-processes"]');
              const collection=panel.querySelector('mcel-collection');
              const first=collection.querySelector('mcel-row');
              const id=first?.dataset.rowId;
              const original=first;
              const snapshotBefore=taskManagerSnapshotCache?.processes?.find(x=>String(x.pid)===id);
              const generation=snapshotBefore?.created_at;
              if(first){first.click();first.focus();}
              const before=McelTaskManagerNotebookLab.status().version;
              // Task Manager coalesces overlapping requests. Awaiting a coalesced
              // promise does not guarantee the queued refresh has published yet.
              // Wait for the observable bridge revision, not merely the promise.
              let refreshError=null;
              const waitStarted=performance.now();
              try {await refreshTaskManager();}
              catch(error){refreshError=String(error);}
              const immediateAfter=McelTaskManagerNotebookLab.status().version;
              let after=immediateAfter;
              const deadline=performance.now()+12000;
              while(!refreshError && after<=before && performance.now()<deadline){
                await new Promise(resolve=>setTimeout(resolve,80));
                after=McelTaskManagerNotebookLab.status().version;
              }
              const waitedMs=Math.round(performance.now()-waitStarted);
              const next=collection.querySelector('mcel-row[data-row-id="'+id+'"]');
              const source=taskManagerSnapshotCache?.processes||[];
              const ids=source.map(x=>String(x.pid)).sort();
              const rendered=[...collection.querySelectorAll('mcel-row')].map(x=>x.dataset.rowId).sort();
              const continuous=next===original;
              const stillSameGeneration=!!(generation && source.find(x=>String(x.pid)===id)?.created_at===generation);
              const inspector=panel.querySelector('.mcel-semantic-collection__inspector')?.textContent||'';
              return {before,immediateAfter,after,waitedMs,refreshError,
                parity:JSON.stringify(ids)===JSON.stringify(rendered),
                sourceIds:ids,renderedIds:rendered,selectedId:id,generation,
                continuous,stillSameGeneration,focused:document.activeElement===original,
                inspectorPreserved:inspector.includes('Exact record '+id),
                report:McelTaskManagerNotebookLab.status().lastReceipt};
            }''')
            status_code=response.status if response else None
            browser_events=hosted_error_evidence(live)
            checks=hosted_gate_checks(status_code,result,followup,failures)
            failed=[name for name,ok in checks.items() if not ok]
            report['hosted']='FAIL' if failed else 'PASS'
            if failed:
              report['hostReason']='Hosted checks failed: '+', '.join(failed)
            report['hostEvidence']={'url':host_url,'httpStatus':status_code,
                'observations':result,'secondRefresh':followup,'pageErrors':failures,
                'checks':checks,'failedChecks':failed,
                'cdpExceptions':cdp_exceptions,'browserEventEvidence':browser_events,
                'consoleErrors':console_errors,'failedRequests':failed_requests,
                'diagnosticHints':hosted_failure_hints(failed)+
                  hosted_error_origin_hints(cdp_exceptions,browser_events)}
            if failed:
              screenshot=output/'hosted-failure.png'
              try:
                live.screenshot(path=str(screenshot),animations='disabled')
                report['hostEvidence']['screenshot']=str(screenshot)
              except Exception as screenshot_error:
                report['hostEvidence']['screenshotError']=str(screenshot_error)
            live.close()
          except Exception as exc:
            report['hosted']='BLOCKED'
            report['hostReason']=type(exc).__name__+': '+str(exc)[:500]
      finally:browser.close()
    (output/'report.json').write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf8')
    return report

if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--host-url');cli.add_argument('--output',type=Path,default=REPORT)
    args=cli.parse_args();result=run(args.output,args.host_url)
    print('MCEL_TASK_MANAGER_LIVE_LIFECYCLE_OFFLINE_'+result['offline'])
    print('MCEL_TASK_MANAGER_LIVE_LIFECYCLE_HOSTED_'+result['hosted'])
    if result['hosted'] in ('FAIL','BLOCKED'):
      print('host_reason:',result.get('hostReason','not provided'))
      for name in result.get('hostEvidence',{}).get('failedChecks',[]):
        print('host_failed_check:',name)
      for hint in result.get('hostEvidence',{}).get('diagnosticHints',[]):
        print('host_hint:',hint)
      for exception in result.get('hostEvidence',{}).get('cdpExceptions',[])[:5]:
        print('host_exception:',json.dumps(exception,ensure_ascii=False)[:1500])
      for event in result.get('hostEvidence',{}).get('browserEventEvidence',{}).get('captured',[])[:8]:
        print('host_browser_event:',json.dumps(event,ensure_ascii=False)[:1000])
    for case in result['cases']:
      print('width',case['width'],case['status'], 'errors:',case['pageErrors'])
    print('report:',args.output/'report.json')
    raise SystemExit(0 if result['offline']=='PASS' and (not args.host_url or result['hosted']=='PASS') else 1)
