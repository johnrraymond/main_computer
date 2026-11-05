"""Evidence from the real SCM kernel, not legacy Studio DOM specimens."""
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from tools.mcel_phase2b_browser import launch_semantic_browser
from tests.test_mcel_semantic_editor_scm_runtime_lifecycle import _port_page

JS_SETUP = '''async()=>{
  const port=window.__port;
  const operations={
    'studio-proof-list':['index','listStudioProofEvidence'],
    'studio-proof-inspect':['inspect','inspectStudioProofEvidence'],
    'studio-proof-export':['packet','exportStudioProofPacket'],
    'studio-regression-run':['regress','runStudioKernelRegressions'],
    'studio-repair-packet':['repair-packet','buildStudioAiRepairPacket']
  };
  window.__proofReq=(name,args)=>{
    const [operation,runtimeMethod]=operations[name];
    return {appId:'semantic-code-editor',intentId:'intent:'+name,
      effectId:'effect:'+name+'.request',capabilityId:'capability:scm-proof',
      operation,runtimeMethod,risk:'external-read',arguments:args};
  };
  await port.invoke(window.__req('list-files',{}));
  await port.invoke(window.__req('open-source',{path:'example.txt'}));
  const a={path:'example.txt',text:'hello world'};
  await port.invoke(window.__req('studio-runtime-mount',a));
  await port.invoke(window.__req('studio-runtime-validate',a));
  return true;
}'''


def test_proof_contract_is_paired_readonly_and_layout_free():
    ir=compile_semantic_app('code-editor')
    assert len(ir['provenance']['frontend']['sourceFiles'])==2
    assert ir['layouts']==[]
    caps={x['id']:x for x in ir['capabilities']}
    assert caps['capability:scm-proof']['risk']=='external-read'
    intents={x['id']:x for x in ir['intents']}
    for name in ('studio-proof-list','studio-proof-inspect','studio-proof-export','studio-regression-run','studio-repair-packet'):
        assert intents['intent:'+name]['risk']=='external-read'


def test_actual_kernel_evidence_filter_select_and_bounded_export():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            page.evaluate(JS_SETUP)
            result=page.evaluate('''async()=>{
              const port=window.__port,req=window.__proofReq,a={path:'example.txt',text:'hello world'};
              const index=await port.invoke(req('studio-proof-list',a));
              const id=index[0].id;
              const inspection=JSON.parse(await port.invoke(req('studio-proof-inspect',{...a,filter:'all',key:id})));
              const packet=JSON.parse(await port.invoke(req('studio-proof-export',{...a,filter:'all',key:id})));
              const violationPacket=JSON.parse(await port.invoke(req('studio-proof-export',{...a,filter:'violations',key:''})));
              const unknown=await port.authorize(req('studio-proof-inspect',{...a,filter:'fabricated',key:''}));
              let refused=false;
              try{await port.invoke(req('studio-proof-inspect',{...a,filter:'all',key:'route:9999'}));}
              catch(e){refused=String(e).includes('selection-not-observed');}
              return {index,id,inspection,packet,violationPacket,unknown,refused,
                calls:window.__requestLog};
            }''')
            assert result['index']
            assert result['inspection']['selectedEvidence']['phase']
            assert result['packet']['origin']=='actual-scm-kernel'
            assert result['packet']['selectedKey']==result['id']
            assert result['packet']['counts']['all']>=1
            assert result['packet']['lastKernelRegressions'] is None
            assert result['violationPacket']['counts']['visible']==result['violationPacket']['counts']['violations']
            assert result['unknown'] is False
            assert result['refused'] is True
            assert result['calls']==['/api/applications/editor/files','/api/applications/editor/read']
        finally:
            browser.close()


def test_proof_is_revoked_by_document_close():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            page.evaluate(JS_SETUP)
            result=page.evaluate('''async()=>{
              const a={path:'example.txt',text:'hello world'};
              const req=window.__proofReq('studio-proof-export',{...a,filter:'all',key:''});
              const before=await window.__port.authorize(req);
              window.__port.closeDocument('example.txt');
              const after=await window.__port.authorize(req);
              return {before,after};
            }''')
            assert result=={'before':True,'after':False}
        finally:
            browser.close()


def test_generated_semantic_editor_exposes_proof_in_its_own_interface():
    from tests.test_mcel_semantic_editor_migration import _start_page
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_start_page(browser)
            page.get_by_role('button',name='List Files').click()
            page.locator('[data-record-key="README.md"] [data-select-key]').click()
            page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
            result=page.evaluate('''async()=>{
              const app=window.__mcelWorkbench.app;
              const mount=await app.execute('intent:studio-runtime-mount',{});
              const validate=await app.execute('intent:studio-runtime-validate',{});
              const index=await app.execute('intent:studio-proof-list',{});
              const regression=await app.execute('intent:studio-regression-run',{});
              const repair=await app.execute('intent:studio-repair-packet',{filter:'all'});
              const proposal=JSON.parse(app.readState('state:studio-repair-packet'));
              return {dispositions:[mount.disposition,validate.disposition,index.disposition,
                regression.disposition,repair.disposition],
                index:app.readState('state:studio-proof-index'),
                regression:JSON.parse(app.readState('state:studio-regression-result')),
                proposal,calls:window.__networkCalls.map(c=>c.url)};
            }''')
            assert result['dispositions']==['completed']*5
            assert result['index']
            assert result['regression']['passed']==5
            assert result['proposal']['execution']=='NOT_EXECUTED'
            assert result['calls']==['/api/applications/editor/files','/api/applications/editor/read']
            assert page.locator('[data-mcel-id="studio-proof-packet"]').count() or 'studio-proof-packet' in page.content()
        finally:
            browser.close()


def test_proof_includes_real_scm_route_evidence_when_route_is_entered():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            page.evaluate(JS_SETUP)
            result=page.evaluate('''async()=>{
              const port=window.__port,a={path:'example.txt',text:'hello world'};
              const route={...window.__req('studio-runtime-mount',a),
                intentId:'intent:studio-runtime-route-enter',
                effectId:'effect:studio-runtime-route-enter.request',
                operation:'route-enter',runtimeMethod:'enterStudioRoute'};
              await port.invoke(route);
              const index=await port.invoke(window.__proofReq('studio-proof-list',a));
              const entry=index.find(x=>x.scope==='route');
              const packet=JSON.parse(await port.invoke(window.__proofReq('studio-proof-export',
                {...a,filter:'route',key:entry?.id||''})));
              return {entry,packet};
            }''')
            assert result['entry'] is not None
            assert result['entry']['id'].startswith('route:')
            assert result['packet']['contracts']['route']
            assert result['packet']['selectedEvidence']['kind']=='mcel-scm-route-evidence'
            assert result['packet']['counts']['visible']>=1
        finally:
            browser.close()
