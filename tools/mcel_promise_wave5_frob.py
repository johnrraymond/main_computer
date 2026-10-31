#!/usr/bin/env python3
"""Independent MCEL File Explorer candidate: source, semantic operations, design,
rollback, safety, and browser coverage. Live-host proof is a separate gate.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl,urlencode,urlunsplit
from tools.mcel_file_explorer_candidate_bundle import build, SOURCE, TARGET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runtime/reports/mcel-promise-wave5'
WIDTHS=(320,390,560,1100,1272)
PROFILES=('operational','analytical')
SCRIPTS=[
 'mcel-semantic-adapter-toolkit.js',
 'file-explorer-semantic-adapter.js',
 'mcel-intent-effect-runtime.js',
 'mcel-semantic-application-shell.js',
 'mcel-file-explorer-candidate-ir.js',
 'mcel-file-explorer-candidate-bridge.js'
]
HTML='''<!doctype html><html><head><meta charset="utf-8" /></head><body style="margin:0">
<div id="file-explorer-app" style="width:100%;height:94vh"><div class="file-explorer-shell" data-old="true">Original File Explorer</div></div>
</body></html>'''

# Real FileExplorerSemanticAdapter, fake transport confined to browser fixture.
TRANSPORT='''() => {
  const roots=[{id:'workspace',label:'Workspace'},{id:'archive',label:'Archive'}];
  const folders={
    'workspace:':[{name:'docs',kind:'directory',relative_path:'docs',bytes:0},
                  {name:'hello.txt',kind:'file',relative_path:'hello.txt',bytes:17}],
    'workspace:docs':[{name:'notes.txt',kind:'file',relative_path:'docs/notes.txt',bytes:12}],
    'archive:':[{name:'old.txt',kind:'file',relative_path:'old.txt',bytes:9}]
  };
  window.__calls=[];
  FileExplorerSemanticAdapter.setTransport(async (path,payload)=>{
    window.__calls.push({path,payload});
    if(path.endsWith('/roots'))return {ok:true,roots};
    if(path.endsWith('/list'))return {ok:true,root_id:payload.root_id,relative_path:payload.relative_path||'',
      count:(folders[`${payload.root_id}:${payload.relative_path||''}`]||[]).length,
      entries:folders[`${payload.root_id}:${payload.relative_path||''}`]||[]};
    if(path.endsWith('/search'))return {ok:true,root_id:payload.root_id,relative_path:payload.relative_path||'',
      query:payload.query,results:[{name:'hello.txt',kind:'file',relative_path:'hello.txt',bytes:17}],count:1};
    if(path.endsWith('/read'))return {ok:true,readable:true,encoding:'utf-8',content:'Hello from real adapter transport',
      entry:{name:payload.relative_path.split('/').pop(),kind:'file',relative_path:payload.relative_path}};
    throw Error('unknown route');
  });
  return true;
}'''

async_js='''async (profile) => {
  const root=document.getElementById('file-explorer-app');
  const first=McelFileExplorerCandidate.mount(root,{presentation:profile});
  const deadline=performance.now()+2000;
  while(McelFileExplorerCandidate.status().domain?.selectedRootId!=='workspace' && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,10));
  const shell=root.querySelector('.mcel-application-shell');
  const initial=McelFileExplorerCandidate.status();
  const firstRows=[...root.querySelectorAll('[data-mcel-region="region:entries"] [data-mcel-row-select]')];
  const original=root.querySelector('.file-explorer-shell');
  const initialRows=firstRows.map(x=>x.textContent);
  const firstRoot=root.querySelector('[data-mcel-region="region:roots"] [data-mcel-row-select]');
  const focused=firstRows.find(x=>x.textContent==='docs');
  focused?.focus();
  const before=initial.version;
  root.querySelector('[data-mcel-shell-action="refresh-directory"]').click();
  while(McelFileExplorerCandidate.status().version<=before && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,10));
  const keyedStable=focused===root.querySelector('[data-mcel-region="region:entries"] [data-mcel-row-key="docs"] [data-mcel-row-select]')
    && document.activeElement===focused;
  const folder=[...root.querySelectorAll('[data-mcel-region="region:entries"] [data-mcel-row-select]')].find(x=>x.textContent==='docs');
  folder?.click();
  while(McelFileExplorerCandidate.status().domain?.relativePath!=='docs' && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,10));
  const folderState=McelFileExplorerCandidate.status();
  root.querySelector('[data-mcel-shell-action="navigate-up"]').click();
  while(McelFileExplorerCandidate.status().domain?.relativePath==='docs' && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,10));
  const search=root.querySelector('[data-mcel-field="query"]');
  search.value='hello';search.dispatchEvent(new Event('input',{bubbles:true}));
  root.querySelector('[data-mcel-shell-action="search-directory"]').click();
  while(McelFileExplorerCandidate.status().domain?.search?.query!=='hello' && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,10));
  const searched=McelFileExplorerCandidate.status();
  const entry=root.querySelector('[data-mcel-region="region:entries"] [data-mcel-row-select]');
  entry?.click();
  while(!McelFileExplorerCandidate.status().domain?.preview && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,10));
  const preview=McelFileExplorerCandidate.status();
  const viewBefore=shell.querySelector('[data-mcel-region="region:entries"]');
  const switcher=shell.querySelector('[data-mcel-presentation-picker]');
  switcher.value=profile==='analytical'?'operational':'analytical';
  switcher.dispatchEvent(new Event('change',{bubbles:true}));
  const stayed=viewBefore===shell.querySelector('[data-mcel-region="region:entries"]');
  const forbidden=FileExplorerSemanticAdapter.preflightIntent('deleteFile',FileExplorerSemanticAdapter.getState(),{parameters:{}});
  const metrics={initial:initialRows,root:initial.domain?.selectedRootId,
    folder:folderState.domain?.relativePath,afterUp:searched.domain?.relativePath,
    search:searched.domain?.search?.query,preview:preview.domain?.preview?.content,
    sourceMatches:McelFileExplorerCandidateSource.ir.application.appId==='file-explorer-candidate',
    hidden:original.hidden,sameNodeAfterSwitch:stayed,keyedStable,
    nonAdmitted:root.querySelector('[data-mcel-shell-action="delete"]')?.disabled,
    forbiddenDenied:!forbidden.allowed,
    readOnlyReceipts:FileExplorerSemanticAdapter.listReceipts().every(r=>r.readOnly===true && r.mutationAttempted!==true),
    requestedOperations:window.__calls.map(x=>x.path),
    documentWidth:document.documentElement.scrollWidth,viewport:innerWidth,
    shellWidth:shell.getBoundingClientRect().width,firstMount:first.ok,
    errorMessage:shell.querySelector('.mcel-application-shell__operation')?.textContent};
  const rollback=McelFileExplorerCandidate.destroy();
  metrics.rollback=rollback.ok && !original.hidden && root.querySelector('.mcel-application-shell')===null;
  return metrics;
}'''

def hosted_url(base, profile):
    p=urlsplit(base)
    query=dict(parse_qsl(p.query,keep_blank_values=True))
    query.update(mcel_file_explorer='candidate',mcel_design=profile)
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),p.fragment))

def run(output:Path=OUT,host_url:str|None=None):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True,exist_ok=True)
    expected,meta=build()
    source={'status':'PASS' if TARGET.is_file() and TARGET.read_text(encoding='utf-8')==expected else 'FAIL',
            'sourceSha256':meta['sourceSha256'],
            'browserArtifactSha256':hashlib.sha256(TARGET.read_bytes()).hexdigest() if TARGET.is_file() else None}
    report={'schema':'mcel.promise-wave5-second-app.v1','generatedUtc':datetime.now(timezone.utc).isoformat(),
            'source':source,'browserCases':[],'hosted':'UNVERIFIED','authoringEconomy':'UNVERIFIED',
            'humanDesignQuality':'NOT_ACCEPTED','scope':'independent-read-only-file-explorer-candidate'}
    stylesheet=ROOT/'main_computer/web/applications/styles/mcel-semantic-application-shell.css'
    with sync_playwright() as p:
        executable=shutil.which('chromium') or shutil.which('chromium-browser')
        launch={'headless':True}
        if executable: launch.update(executable_path=executable,args=['--no-sandbox','--disable-dev-shm-usage'])
        browser=p.chromium.launch(**launch)
        try:
            for profile in PROFILES:
                for width in WIDTHS:
                    page=browser.new_page(viewport={'width':width,'height':800})
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.set_content(HTML);page.add_style_tag(content=stylesheet.read_text(encoding='utf-8'))
                    for name in SCRIPTS:
                        page.add_script_tag(content=(ROOT/'main_computer/web/applications/scripts'/name).read_text(encoding='utf-8'))
                    page.evaluate(TRANSPORT)
                    try:data=page.evaluate(async_js,profile)
                    except Exception as e:data={'error':str(e)}
                    checks={
                        'source-bound-and-mounted':data.get('sourceMatches') is True and data.get('firstMount') is True,
                        'real-adapter-initial-roots':data.get('root')=='workspace' and 'docs' in data.get('initial',[]),
                        'directory-navigation':data.get('folder')=='docs' and data.get('afterUp')=='',
                        'search-and-preview':data.get('search')=='hello' and 'Hello from real adapter' in str(data.get('preview')),
                        'design-no-remount':data.get('sameNodeAfterSwitch') is True,
                        'keyed-focus-preservation':data.get('keyedStable') is True,
                        'forbidden-write-refused':data.get('forbiddenDenied') is True and data.get('nonAdmitted') is True,
                        'read-only-receipts':data.get('readOnlyReceipts') is True,
                        'legacy-hidden-and-restored':data.get('hidden') is True and data.get('rollback') is True,
                        'responsive':data.get('documentWidth',99999)<=width+2 and data.get('shellWidth',99999)<=width+2,
                        'no-js-errors':not errors
                    }
                    report['browserCases'].append({'profile':profile,'width':width,'checks':checks,
                        'status':'PASS' if all(checks.values()) else 'FAIL',
                        'evidence':data,'browserErrors':errors})
                    if width in (390,1100):
                        # A separate render before rollback for visual evidence.
                        page.evaluate(TRANSPORT)
                        page.evaluate("() => McelFileExplorerCandidate.mount(document.getElementById('file-explorer-app'),{presentation:'"+profile+"'})")
                        page.wait_for_timeout(100)
                        page.screenshot(path=str(output/f'file-explorer-{profile}-{width}.png'),full_page=True)
                    page.close()
            if host_url:
                report['hostCases']=[]
                for profile in PROFILES:
                    page=browser.new_page(viewport={'width':1100,'height':820})
                    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    case={'profile':profile,'status':'BLOCKED','checks':{},'browserErrors':errors}
                    try:
                        response=page.goto(hosted_url(host_url,profile),wait_until='domcontentloaded',timeout=20000)
                        page.wait_for_function('''() => document.querySelector('#file-explorer-app .mcel-file-explorer-candidate-host')!==null''',timeout=13000)
                        page.wait_for_function('''() => window.McelFileExplorerCandidate?.status()?.domain?.phase==='ready' ||
                             window.McelFileExplorerCandidate?.status()?.domain?.phase==='error' ''',timeout=13000)
                        # A real declared Refresh must complete AFTER the initial load.
                        # Merely checking all([]) receipts made the old gate vacuous.
                        before=page.evaluate('''() => McelFileExplorerCandidate.status().version''')
                        page.locator('[data-mcel-shell-action="refresh-directory"]').click()
                        page.wait_for_function('''before => {
                          const s=McelFileExplorerCandidate.status();
                          return s.version>before && s.operation?.phase==='completed';
                        }''',arg=before,timeout=15000)
                        data=page.evaluate('''before => {
                          const root=document.getElementById('file-explorer-app');
                          const state=McelFileExplorerCandidate.status();
                          const shell=root.querySelector('.mcel-application-shell');
                          const roots=state.domain.roots||[],entries=state.domain.entries||[];
                          const keys=region=>[...root.querySelectorAll(`[data-mcel-region="${region}"] tbody tr`)]
                            .map(tr=>tr.dataset.mcelRowKey);
                          const receipts=FileExplorerSemanticAdapter.listReceipts();
                          const latestRead=receipts.filter(r=>r.intentId==='listDirectory').slice(-1)[0];
                          const operation=state.operation;
                          return {profile:shell?.dataset.mcelPresentation,
                            legacyHidden:root.querySelector('.file-explorer-shell')?.hidden,
                            source:McelFileExplorerCandidateSource.metadata.sourceSha256,
                            sourceCount:roots.length,renderedRoots:keys('region:roots').length,
                            rootKeys:roots.map(r=>String(r.id)),renderedRootKeys:keys('region:roots'),
                            entryCount:entries.length,renderedEntries:keys('region:entries').length,
                            entryKeys:entries.map(e=>String(e.relative_path)),renderedEntryKeys:keys('region:entries'),
                            phase:state.domain.phase,readOnly:state.domain.readOnly,
                            beforeVersion:before,afterVersion:state.version,operationPhase:operation?.phase,
                            operationReceipt:operation?.receipt,latestReadReceipt:latestRead,
                            receiptCount:receipts.length,
                            documentWidth:document.documentElement.scrollWidth,viewport:innerWidth};
                        }''',before)
                        checks={'http200':response is not None and response.status==200,
                          'correct-profile':data['profile']==profile,
                          'legacy-hidden':data['legacyHidden'] is True,
                          'source-hash':data['source']==source['sourceSha256'],
                          'real-roots-parity':data['sourceCount']==data['renderedRoots'] and data['sourceCount']>0,
                          'real-entries-parity':data['entryCount']==data['renderedEntries'],
                          'exact-root-identity-parity':data['rootKeys']==data['renderedRootKeys'],
                          'exact-entry-identity-parity':data['entryKeys']==data['renderedEntryKeys'],
                          'refresh-version-advanced':data['afterVersion']>data['beforeVersion'],
                          'refresh-operation-completed':data['operationPhase']=='completed' and
                            data['operationReceipt'] is not None and data['operationReceipt'].get('ok') is True and
                            data['operationReceipt'].get('afterVersion',0)>data['beforeVersion'],
                          'actual-read-receipt':data['latestReadReceipt'] is not None and
                            data['latestReadReceipt'].get('readOnly') is True and
                            data['latestReadReceipt'].get('mutationAttempted') is not True and
                            data['latestReadReceipt'].get('status')=='pass',
                          'adapter-ready-read-only':data['phase']=='ready' and data['readOnly'] is True,
                          'no-horizontal-overflow':data['documentWidth']<=data['viewport']+2,
                          'no-browser-errors':not errors}
                        case.update(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,evidence=data)
                    except Exception as e:
                        case.update(status='BLOCKED',reason=str(e)[:600])
                    report['hostCases'].append(case)
                    page.close()
                report['hosted']='PASS' if all(c['status']=='PASS' for c in report['hostCases']) else 'BLOCKED' if any(c['status']=='BLOCKED' for c in report['hostCases']) else 'FAIL'
        finally:browser.close()
    report['offline']='PASS' if source['status']=='PASS' and all(c['status']=='PASS' for c in report['browserCases']) else 'FAIL'
    report['acceptance']='INCOMPLETE' # human aesthetic and comparative labor still outstanding
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('MCEL_PROMISE_WAVE5_'+report['offline'],'acceptance',report['acceptance'],'hosted',report['hosted'])
    for c in report['browserCases']:
        print(c['profile'],c['width'],c['status'],[k for k,v in c['checks'].items() if not v])
    print('report',output/'report.json')
    return 0 if report['offline']=='PASS' and (not host_url or report['hosted']=='PASS') else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUT);parser.add_argument('--host-url')
    args=parser.parse_args();raise SystemExit(run(args.output,args.host_url))
