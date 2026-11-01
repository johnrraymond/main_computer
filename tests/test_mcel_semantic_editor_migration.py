"""Real semantic Code Editor contract and mocked browser-transport Monaco workbench.

No source files are mutated by these tests. The API transport and Monaco model
are mocked; live Monaco loader and Windows backend remain separate gates.
"""
import hashlib
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from tools.mcel_phase2b_browser import launch_semantic_browser
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page

ROOT = Path(__file__).resolve().parents[1]


def test_authored_editor_provides_monaco_contract_and_governed_save():
    ir=compile_semantic_app('code-editor')
    assert ir['layouts']==[]
    nodes=ir['surfaces'][0]['nodes']
    doc=[n for n in nodes if n['nodeKind']=='document-editor']
    assert len(doc)==1
    assert doc[0]['path']['ref']=='state:selected-path'
    assert doc[0]['contentState']['ref']=='state:source'
    assert doc[0]['draft']['ref']=='state:draft'
    assert doc[0]['loadIntent']['ref']=='intent:open-source'
    assert doc[0]['saveIntent']['ref']=='intent:save-source'
    effects={e['id']:e for e in ir['effects']}
    assert effects['effect:save-source.request']['risk']=='source-write'
    assert effects['effect:open-source.request']['risk']=='external-read'
    assert effects['effect:apply-reviewed-change.request']['risk']=='source-write'
    assert effects['effect:prepare-reviewed-change.request']['risk']=='external-read'
    assert 'source-write' not in {e['risk'] for e in compile_semantic_app('task-manager')['effects']}
    assert 'source-write' not in {e['risk'] for e in compile_semantic_app('file-manager')['effects']}


def _start_page(browser):
    page=browser.new_page(viewport={'width':1100,'height':880})
    original='# Sample project'
    expected=hashlib.sha256(original.encode()).hexdigest()
    page.evaluate('const expected = '+json.dumps(expected)+';\n'+'''(() => {
      window.__networkCalls=[];
      window.fetch=async (url,options)=>{
        const body=JSON.parse(options.body||'{}');
        window.__networkCalls.push({url:String(url),body});
        let payload;
        if(String(url).endsWith('/editor/files')){
          if(body.path==='src')payload={entries:[{path:'src/app.js',name:'app.js',kind:'file'}],path:'src'};
          else payload={entries:[{path:'README.md',name:'README.md',kind:'file'},
            {path:'NOTES.md',name:'NOTES.md',kind:'file'},
            {path:'src',name:'src',kind:'dir'}],path:''};
        }else if(String(url).endsWith('/editor/read'))payload={ok:true,files:[{path:body.files[0],content:window.__truncated?'[truncated at 200000 characters]':(window.__externalSource || '# Sample project')}]};
        else if(String(url).endsWith('/editor/project/file/save')){
          window.__saveRequest=body;
          if(window.__badSaveReceipt) return {ok:true,status:200,json:async()=>({ok:true,schema:'mcel-code-editor-file-save-v1',state:'applied',status:'pass',savedPath:body.path,receipt:{ok:true}})};
          if(body.expected_before_sha256!==expected || !body.explicit_save ||
             !body.stale_source_checked || body.write_policy!=='author-owned-source')
            return {ok:false,status:409,json:async()=>({error:'stale source'})};
          payload={ok:true,schema:'mcel-code-editor-file-save-v1',status:'pass',state:'applied',
            handle:'a'.repeat(32),savedPath:body.path,changedFiles:[body.path],
            receipt:{ok:true,rollback_required:false,transaction_id:'b'.repeat(24),files:[{
              operation:'modify',path:body.path,before_sha256:expected,
              after_sha256:expected,written_sha256:expected}]}};
        }else if(String(url).endsWith('/aider/prepare')){
          window.__aiderPreview=body;
          payload={ok:true,command:['aider','--dry-run','--file',body.files[0]]};
        }else if(String(url).endsWith('/editor/project/transaction/prepare')){
          window.__preparedChange=body;
          payload={ok:true,schema:'mcel-code-editor-project-transaction-v1',state:'prepared',
            handle:'c'.repeat(32),transaction:{transaction_id:'d'.repeat(24)}};
        }else if(String(url).endsWith('/editor/project/transaction/apply')){
          window.__appliedChange=body;
          payload={ok:true,schema:'mcel-code-editor-project-transaction-v1',state:'applied',
            handle:'c'.repeat(32),changedFiles:['README.md'],receipt:{ok:true,rollback_required:false,
              transaction_id:'d'.repeat(24),files:[{path:'README.md',operation:'modify',
                before_sha256:expected,after_sha256:window.__reviewAfterHash,written_sha256:window.__reviewAfterHash}]}};
        }else return {ok:false,status:404,json:async()=>({error:'not admitted'})};
        return {ok:true,status:200,json:async()=>payload};
      };
    })()''')
    page.set_content(render_semantic_page('code-editor'))
    # The sandbox blocks loopback navigation, so the browser test supplies a
    # deterministic SHA-256 response. Real WebCrypto on localhost is a separate gate.
    page.evaluate('''(hash) => {
      const bytes=Uint8Array.from(hash.match(/.{2}/g), x=>parseInt(x,16));
      Object.defineProperty(window.crypto,'subtle',{configurable:true,
        value:{digest:async ()=>bytes.buffer}});
    }''', expected)
    page.evaluate('''() => {
      window.MainComputerMonacoAdapter={
        mount:async opts=>{
          opts.host.replaceChildren();
          const node=document.createElement('div');node.className='monaco-editor';node.textContent=opts.value;
          opts.host.append(node);
          opts.host.dataset.monacoOutcome='pass';
          window.__fakeMonaco={path:opts.path,value:opts.value,readOnly:opts.readOnly,onChange:opts.onChange,host:opts.host};
          return {ok:true,externalOutcome:'model-mounted'};
        },
        setModel:opts=>{
          window.__fakeMonaco.value=opts.value;
          window.__fakeMonaco.host.textContent=opts.value;
          return {ok:true};
        },
        dispose:()=>({ok:true})
      };
    }''')
    return page


def test_monaco_document_edit_and_server_guarded_save():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"]').wait_for()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        # File choice must perform the declared read and mount Monaco directly.
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        assert page.locator('[data-mcel-document-surface] .monaco-editor').count() == 1
        assert page.evaluate('window.__fakeMonaco.value')=='# Sample project'
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:source")')=='# Sample project'
        page.evaluate('''() => window.__fakeMonaco.onChange('# Edited project')''')
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")')=='# Edited project'
        assert page.locator('[data-mcel-document-save]').is_enabled()
        page.on('dialog',lambda dialog:dialog.accept())
        page.locator('[data-mcel-document-save]').click()
        page.wait_for_function('window.__saveRequest?.replacement_text === "# Edited project"')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(r => r.intentId === "intent:save-source" && r.disposition === "completed")')
        assert not page.locator('[data-mcel-document-save]').is_enabled()
        assert page.evaluate('window.__saveRequest.path')=='README.md'
        page.close()
      finally:b.close()


def test_directory_browser_and_dirty_edit_safety():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="src"] [data-select-key]').click()
        page.locator('[data-record-key="src/app.js"]').wait_for()
        assert page.evaluate('window.__networkCalls.filter(c=>c.url.endsWith("/editor/files")).length')==2
        page.locator('[data-record-key="src/app.js"] [data-select-key]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "src/app.js"')
        page.evaluate('''()=>window.__fakeMonaco.onChange('modified')''')
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")')=='modified'
        # Navigating to another selected record cannot silently abandon draft.
        result=page.evaluate('''async()=>window.__mcelWorkbench.app.execute('intent:select-file',{selection:'README.md'})''')
        # Refusal is permitted regardless of the exact exposed selection input name.
        assert result['disposition']=='refused-before-attempt'
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")')=='modified'
        page.close()
      finally:b.close()


def test_source_write_is_refused_without_open_and_forged_authority():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        allowed=page.evaluate('''async()=>{
          const port=McelSemanticReadPorts.create({appId:'semantic-code-editor'});
          const req={appId:'semantic-code-editor',intentId:'intent:save-source',
            effectId:'effect:save-source.request',capabilityId:'capability:source-writer',
            operation:'save',runtimeMethod:'saveSource',risk:'source-write',
            arguments:{path:'README.md',text:'malicious'}};
          return [await port.authorize(req), await port.authorize({...req,risk:'external-read'})];
        }''')
        assert allowed == [False,False]
        assert page.evaluate('window.__networkCalls.length') == 0
        page.close()
      finally:b.close()


def test_truncated_source_is_not_editable_or_writable():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        page.evaluate('window.__truncated=true')
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        # File choice must perform the declared read and mount Monaco directly.
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        assert page.evaluate('window.__fakeMonaco.readOnly') is True
        assert not page.locator('[data-mcel-document-save]').is_enabled()
        assert 'cannot be saved safely' in page.locator('[data-mcel-document-dirty]').inner_text()
        result=page.evaluate('''async()=>{
          const port=McelSemanticReadPorts.create({appId:'semantic-code-editor'});
          return port.authorize({appId:'semantic-code-editor',intentId:'intent:save-source',
            effectId:'effect:save-source.request',capabilityId:'capability:source-writer',
            operation:'save',runtimeMethod:'saveSource',risk:'source-write',
            arguments:{path:'README.md',text:'replacement'}});
        }''')
        assert result is False
        assert page.evaluate('window.__networkCalls.length') == 2
        page.close()
      finally:b.close()


def test_one_click_file_open_reports_real_document_and_monaco_parity():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        assert page.evaluate("async()=> (await window.MCELDiagnosticPlugins.enable('mcel-app-parity')).status") == 'enabled'
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.wait_for_function("""() => document.querySelector('[data-mcel-document-surface] .monaco-editor')
          && window.__mcelWorkbench.app.receipts().some(r =>
            r.intentId === 'intent:open-source' && r.disposition === 'completed')""")
        trace=page.evaluate('window.MCELAppParityTrace.exportTrace()')
        assert trace['finalState']['documentOpen'] is True
        assert trace['finalState']['monacoPresent'] is True
        assert trace['finalState']['monacoHostPresent'] is True
        assert [c['url'] for c in page.evaluate('window.__networkCalls')].count('/api/applications/editor/read')==1
        events=trace['events']
        assert any(e.get('intent')=='intent:open-source' and e.get('disposition')=='completed' for e in events)
        assert any(e.get('intent')=='editor.monaco.mount' and e.get('disposition')=='mounted' for e in events)
        assert any(e.get('endpoint')=='/api/applications/editor/read' for e in events)
        assert '# Sample project' not in json.dumps(trace)
        page.close()
      finally:b.close()


def test_semantic_renderer_reaches_real_monaco_adapter_mount():
    """Exercise the shipped adapter with a local Monaco API stand-in.

    This proves the renderer/adapter handshake but not that the real vendor
    distribution loads from a live Windows host.
    """
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=b.new_page(viewport={'width':1100,'height':800})
        page.evaluate('''() => {
          const models=new Map();
          window.monaco={
            Uri:{parse:s=>s},
            editor:{
              getModel:uri=>models.get(uri)||null,
              createModel:(value,language,uri)=>{
                const model={value,language,uri,getValue(){return this.value;},
                  setValue(next){this.value=next;},
                  onDidChangeContent:()=>({dispose(){}})};
                models.set(uri,model);return model;
              },
              create:(host,options)=>{
                const node=document.createElement('div');
                node.className='monaco-editor';
                node.textContent=options.model.getValue();
                host.append(node);
                return {layout(){},dispose(){node.remove();},setModel(m){node.textContent=m.getValue();},updateOptions(){}};
              }
            }
          };
          window.fetch=async(url,options)=>{
            const body=JSON.parse(options?.body||'{}');
            if(String(url).endsWith('/editor/files'))return {ok:true,status:200,
              json:async()=>({ok:true,entries:[{path:'demo.js',name:'demo.js',kind:'file'}]})};
            if(String(url).endsWith('/editor/read'))return {ok:true,status:200,
              json:async()=>({ok:true,files:[{path:body.files[0],content:'const demo = 42;'}]})};
            return {ok:false,status:404,json:async()=>({error:'unexpected'})};
          };
        }''')
        page.set_content(render_semantic_page('code-editor'))
        assert page.evaluate("async()=> (await MCELDiagnosticPlugins.enable('mcel-app-parity')).status")=='enabled'
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="demo.js"] [data-select-key]').click()
        page.wait_for_function('''() => document.querySelector('[data-mcel-document-surface] .monaco-editor')
          && document.querySelector('[data-mcel-document-surface]').dataset.monacoOutcome === 'pass' ''')
        trace=page.evaluate('window.MCELAppParityTrace.exportTrace()')
        assert trace['finalState']['monacoPresent'] is True
        assert page.evaluate('window.__CE_MONACO_MODEL__.getValue()') == 'const demo = 42;'
        assert any(e.get('intent')=='editor.monaco.load' and e.get('disposition')=='already-loaded'
                   for e in trace['events'])
        assert any(e.get('intent')=='editor.monaco.mount' and e.get('disposition')=='mounted'
                   for e in trace['events'])
        page.close()
      finally:b.close()


def test_superseded_async_monaco_mount_cannot_replace_newer_document():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        page.evaluate("""() => {
          window.__pendingMounts=[];
          window.MainComputerMonacoAdapter.mount=opts=>new Promise(resolve=>{
            window.__pendingMounts.push({path:opts.path,complete:()=>{
              opts.host.replaceChildren();
              const editor=document.createElement('div');editor.className='monaco-editor';
              editor.textContent=opts.path;opts.host.append(editor);
              resolve({ok:true,externalOutcome:'model-mounted'});
            }});
          });
          window.MainComputerMonacoAdapter.dispose=()=>({ok:true});
        }""")
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.wait_for_function('window.__pendingMounts.length === 1')
        page.locator('[data-record-key="NOTES.md"] [data-select-key]').click()
        page.wait_for_function("""() => window.__mcelWorkbench.app.readState('state:selected-path')==='NOTES.md'
          && window.__mcelWorkbench.app.receipts().filter(r=>r.intentId==='intent:open-source').length===2""")
        page.evaluate('window.__pendingMounts[0].complete()')
        page.wait_for_function('window.__pendingMounts.length === 2')
        assert page.evaluate('window.__pendingMounts[1].path')=='NOTES.md'
        page.evaluate('window.__pendingMounts[1].complete()')
        page.wait_for_function("""() => document.querySelector('[data-mcel-document-surface] .monaco-editor')?.textContent==='NOTES.md' """)
        assert 'NOTES.md' in page.locator('[data-mcel-document-dirty]').inner_text()
        page.close()
      finally:b.close()


def test_multiple_monaco_documents_retain_independent_dirty_drafts_and_guard_close():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        page.evaluate("() => window.__fakeMonaco.onChange('README unsaved')")
        page.locator('[data-record-key="NOTES.md"] [data-select-key]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "NOTES.md"')
        page.evaluate("() => window.__fakeMonaco.onChange('NOTES unsaved')")
        assert page.locator('[data-mcel-open-tab]').count()==2
        assert page.locator('[data-mcel-open-tab="README.md"] [role="tab"]').inner_text().endswith('*')
        page.locator('[data-mcel-tab-path="README.md"]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        assert page.evaluate('window.__fakeMonaco.value')=='README unsaved'
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")')=='README unsaved'
        page.locator('[data-mcel-tab-path="NOTES.md"]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "NOTES.md"')
        assert page.evaluate('window.__fakeMonaco.value')=='NOTES unsaved'
        page.once('dialog',lambda d:d.dismiss())
        page.locator('[data-mcel-close-tab="README.md"]').click()
        assert page.locator('[data-mcel-open-tab]').count()==2
        page.once('dialog',lambda d:d.accept())
        page.locator('[data-mcel-close-tab="README.md"]').click()
        assert page.locator('[data-mcel-open-tab]').count()==1
        assert page.locator('[data-mcel-open-tab="NOTES.md"]').count()==1
        page.close()
      finally:b.close()


def test_changed_source_blocks_stale_tab_save_until_explicit_discard_and_reload():
    with sync_playwright() as p:
      b,_=launch_semantic_browser(p)
      try:
        page=_start_page(b)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        page.evaluate("() => window.__fakeMonaco.onChange('local dirty')")
        page.locator('[data-record-key="NOTES.md"] [data-select-key]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "NOTES.md"')
        page.evaluate("window.__externalSource='external edit'")
        page.locator('[data-mcel-tab-path="README.md"]').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        assert page.evaluate('window.__fakeMonaco.value')=='local dirty'
        assert page.locator('[data-mcel-document-save]').is_disabled()
        assert page.get_by_text('Source changed outside this tab',exact=False).count()>=1
        page.locator('[data-mcel-document-discard]').click()
        page.wait_for_function('window.__fakeMonaco?.value === "external edit"')
        assert page.locator('[data-mcel-document-save]').is_disabled()
        page.close()
      finally:b.close()
