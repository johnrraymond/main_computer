"""Read-only parity recovery: real endpoint contracts with deterministic browser transport."""
from __future__ import annotations
import json
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def page_for(browser, app, width=1100):
    page=browser.new_page(viewport={"width":width,"height":900})
    page.evaluate('''() => {
      window.__calls=[];
      window.fetch=async(url,options)=>{
        const body=JSON.parse(options.body||'{}');const path=String(url);
        window.__calls.push({path,body});let data;
        if(path.endsWith('/file-explorer/roots'))data={ok:true,roots:[{id:'workspace',label:'Workspace'}]};
        else if(path.endsWith('/file-explorer/list')){
          let entries=[];
          if(body.relative_path==='')entries=[{relative_path:'src',name:'src',kind:'directory'},
             {relative_path:'README.md',name:'README.md',kind:'file'}];
          else if(body.relative_path==='src')entries=[{relative_path:'src/a.js',name:'a.js',kind:'file'},
             {relative_path:'src/nested',name:'nested',kind:'directory'}];
          else if(body.relative_path==='src/nested')entries=[{relative_path:'src/nested/hi.js',name:'hi.js',kind:'file'}];
          data={ok:true,root_id:body.root_id,relative_path:body.relative_path,entries};
        }else if(path.endsWith('/file-explorer/search')){
          data={ok:true,root_id:body.root_id,query:body.query,results:[
            {relative_path:'src/a.js',name:'a.js',kind:'file'}]};
        }else if(path.endsWith('/file-explorer/read'))data={ok:true,readable:true,content:'const a=1;'};
        else if(path.endsWith('/task/overview'))data={ok:true,
           processes:[{pid:17,name:body.include_all?'extra-process':'server',status:'running',created_at:'t0',memory_human:'5 MB'}],
           connections:[{pid:17,process_name:'server',status:'LISTEN',local:'127.0.0.1:80',remote:''}],
           hardware:{summary:'CPU 12% | GPU 5%'}};
        else if(path.endsWith('/task/schedules'))data={ok:true,schedules:[{id:'sched-a',action:'server_restart',run_at:'2026-11-01T10:00:00Z',note:'maintenance'}]};
        else if(path.endsWith('/task/ai'))data={ok:true,content:'CPU is stable.'};
        else return {ok:false,status:404,json:async()=>({error:'unrecognized endpoint'})};
        return {ok:true,status:200,json:async()=>data};
      };
    }''')
    page.set_content(render_semantic_page(app))
    assert page.locator('#mcel-workbench-status').inner_text()=='Semantic candidate ready'
    return page


def test_file_manager_nested_parent_search_and_preview():
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=page_for(browser,'file-manager')
        page.get_by_role('button',name='Load Roots').click()
        page.locator('[data-record-key="workspace"] button').click()
        page.get_by_role('button',name='Browse',exact=True).click()
        page.locator('[data-record-key="src"] button').click()
        page.locator('[data-record-key="src/a.js"]').wait_for()
        assert page.locator('[data-mcel-current-directory]').inner_text()=='/src'
        page.get_by_role('button',name='Parent directory').click()
        page.locator('[data-record-key="README.md"]').wait_for()
        assert page.locator('[data-mcel-current-directory]').inner_text()=='/'
        page.locator('form[data-intent="intent:search"] input[name="query"]').fill('a.js')
        page.locator('form[data-intent="intent:search"] button[type="submit"]').click()
        page.locator('[data-record-key="src/a.js"] button').click()
        page.get_by_role('button',name='Preview Entry').click()
        page.locator('[data-state="state:preview"]').get_by_text('const a=1;').wait_for()
        assert page.evaluate('window.__calls.some(c=>c.path.endsWith("/file-explorer/search"))')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.close()
      finally:browser.close()


def test_file_manager_refuses_unobserved_directory_and_traversal():
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=page_for(browser,'file-manager')
        page.get_by_role('button',name='Load Roots').click()
        page.locator('[data-record-key="workspace"] button').click()
        page.get_by_role('button',name='Browse',exact=True).click()
        result=page.evaluate('''async()=>window.__mcelWorkbench.app.execute('intent:enter-directory',{relativePath:'../../etc'})''')
        assert result['disposition']=='refused-before-attempt'
        assert not page.evaluate('window.__calls.some(c=>c.body.relative_path==="../../etc")')
        page.close()
      finally:browser.close()


def test_task_manager_real_four_dataset_views():
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=page_for(browser,'task-manager')
        page.locator('form[data-intent="intent:refresh"] button[type="submit"]').click()
        page.locator('[data-record-key="17:t0"]').wait_for()
        modes={'all-processes':('intent:refresh-all','extra-process'),
               'connections':('intent:refresh-connections','127.0.0.1:80'),
               'cpu-gpu':('intent:refresh-hardware','CPU 12%')}
        for mode,(action,expected) in modes.items():
          page.locator('[data-mcel-mode-tabs] button[data-value="'+mode+'"]').click()
          page.locator('form[data-intent="'+action+'"] button[type="submit"]').click()
          page.get_by_text(expected,exact=False).first.wait_for()
          assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        calls=page.evaluate('window.__calls.filter(c=>c.path.endsWith("/task/overview")).map(c=>c.body)')
        assert len(calls)==4
        assert calls[1]['include_all'] is True
        assert calls[2]['include_connections'] is True
        page.close()
      finally:browser.close()


def test_editor_close_rejects_reuse_of_save_authority():
    from tests.test_mcel_semantic_editor_migration import _start_page
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=_start_page(browser)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.get_by_role('button',name='Open Source').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        page.locator('[data-mcel-document-close]').click()
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:source")')==''
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-path")')==''
        assert page.locator('[data-mcel-document-save]').is_disabled()
        assert page.evaluate('''async()=>{
          const port=McelSemanticReadPorts.create({appId:'semantic-code-editor'});
          return await port.authorize({appId:'semantic-code-editor',intentId:'intent:save-source',
            effectId:'effect:save-source.request',capabilityId:'capability:source-writer',
            operation:'save',runtimeMethod:'saveSource',risk:'source-write',
            arguments:{path:'README.md',text:'not authorized'}});
        }''') is False
        page.close()
      finally:browser.close()


def test_editor_refuses_forged_applied_receipt_and_preserves_dirty_draft():
    from tests.test_mcel_semantic_editor_migration import _start_page
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=_start_page(browser)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.get_by_role('button',name='Open Source').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        page.evaluate('window.__badSaveReceipt=true')
        page.evaluate("() => window.__fakeMonaco.onChange('# modified')")
        page.on('dialog',lambda d:d.accept())
        page.locator('[data-mcel-document-save]').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(r=>r.intentId==="intent:save-source"&&r.disposition==="failed")')
        # A forged receipt may conceal a real server mutation. Keep the draft,
        # but revoke the old write baseline until an explicit source reread.
        assert page.locator('[data-mcel-document-save]').is_disabled()
        assert page.locator('[data-mcel-document-dirty="true"]').count()==1
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")') == '# modified'
        assert 'Source write authority was revoked' in page.locator('[data-mcel-document-dirty]').inner_text()
        previous=page.evaluate('window.__mcelWorkbench.app.receipts().filter(r=>r.intentId==="intent:open-source").length')
        page.get_by_role('button',name='Open Source').click()
        page.wait_for_function("""count => window.__mcelWorkbench.app.receipts().filter(r => r.intentId === 'intent:open-source').length > count""", arg=previous)
        page.wait_for_function("""() => !document.querySelector('[data-mcel-document-save]').disabled""")
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")') == '# modified'
        page.close()
      finally:browser.close()


def test_editor_dirty_close_requires_explicit_discard_approval():
    from tests.test_mcel_semantic_editor_migration import _start_page
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=_start_page(browser)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.get_by_role('button',name='Open Source').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        page.evaluate("() => window.__fakeMonaco.onChange('# work')")
        page.once('dialog',lambda d:d.dismiss())
        page.locator('[data-mcel-document-close]').click()
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")')=='# work'
        page.once('dialog',lambda d:d.accept())
        page.locator('[data-mcel-document-close]').click()
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:draft")')==''
        assert page.locator('[data-mcel-document-save]').is_disabled()
        page.close()
      finally:browser.close()


def test_editor_reviewed_transaction_apply_and_stale_draft_refusal():
    import hashlib
    from tests.test_mcel_semantic_editor_migration import _start_page
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=_start_page(browser)
        oldhash=hashlib.sha256(b'# Sample project').hexdigest()
        newhash=hashlib.sha256(b'# Reviewed update').hexdigest()
        page.evaluate('''({oldhash,newhash}) => {
          window.__reviewAfterHash=newhash;
          Object.defineProperty(window.crypto,'subtle',{configurable:true,
            value:{digest:async (kind,bytes)=>{
              const text=new TextDecoder().decode(bytes);
              const hex=text==='# Sample project'?oldhash:newhash;
              return Uint8Array.from(hex.match(/.{2}/g),v=>parseInt(v,16)).buffer;
            }}});
        }''',{ 'oldhash':oldhash,'newhash':newhash })
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.get_by_role('button',name='Open Source').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        # No server-issued review handle exists yet.
        denied=page.evaluate('''async()=>window.__mcelWorkbench.app.execute('intent:apply-reviewed-change')''')
        assert denied['disposition']=='refused-before-attempt'
        page.evaluate("() => window.__fakeMonaco.onChange('# Reviewed update')")
        page.get_by_role('button',name='Prepare Reviewed Change').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(r=>r.intentId==="intent:prepare-reviewed-change"&&r.disposition==="completed")')
        assert page.evaluate('window.__preparedChange.changes[0].expected_before_sha256')==oldhash
        assert page.evaluate('window.__preparedChange.changes[0].replacement_text')=='# Reviewed update'
        # Changing the draft AFTER review must invalidate permission to apply.
        page.evaluate("() => window.__fakeMonaco.onChange('# Changed since review')")
        page.on('dialog',lambda d:d.accept())
        before=page.evaluate('window.__networkCalls.length')
        page.get_by_role('button',name='Apply Reviewed Change').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(r=>r.intentId==="intent:apply-reviewed-change"&&r.disposition==="refused-before-attempt")')
        assert not page.evaluate('window.__appliedChange !== undefined')
        page.evaluate("() => window.__fakeMonaco.onChange('# Reviewed update')")
        page.get_by_role('button',name='Apply Reviewed Change').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(r=>r.intentId==="intent:apply-reviewed-change"&&r.disposition==="completed")')
        assert page.evaluate('window.__appliedChange.reviewed') is True
        assert page.evaluate('window.__appliedChange.approved') is True
        assert not page.locator('[data-mcel-document-save]').is_enabled()
        page.close()
      finally:browser.close()


def test_task_schedules_and_ai_only_on_explicit_user_action():
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=page_for(browser,'task-manager')
        assert page.evaluate('window.__calls.length')==0
        page.get_by_role('button',name='List Schedules').click()
        page.locator('[data-record-key="sched-a"]').wait_for()
        assert page.evaluate('window.__calls.filter(c=>c.path.endsWith("/task/ai")).length')==0
        page.locator('form[data-intent="intent:task-ai"] input[name="instruction"]').fill('What needs attention?')
        page.locator('form[data-intent="intent:task-ai"] button[type="submit"]').click()
        page.locator('[data-state="state:ai-result"]').get_by_text('CPU is stable.').wait_for()
        assert page.evaluate('window.__calls.filter(c=>c.path.endsWith("/task/ai")).length')==1
        page.close()
      finally:browser.close()


def test_editor_aider_preview_is_explicit_and_dry_run():
    from tests.test_mcel_semantic_editor_migration import _start_page
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=_start_page(browser)
        page.get_by_role('button',name='List Files').click()
        page.locator('[data-record-key="README.md"] [data-select-key]').click()
        page.get_by_role('button',name='Open Source').click()
        page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
        assert page.evaluate('window.__aiderPreview === undefined')
        page.locator('form[data-intent="intent:preview-aider-plan"] input[name="instruction"]').fill('Review this edit')
        page.locator('form[data-intent="intent:preview-aider-plan"] button[type="submit"]').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(r=>r.intentId==="intent:preview-aider-plan"&&r.disposition==="completed")')
        assert page.evaluate('window.__aiderPreview.dry_run') is True
        assert page.evaluate('window.__aiderPreview.files')==['README.md']
        assert not page.evaluate('window.__networkCalls.some(c=>c.url.endsWith("/aider/run"))')
        page.close()
      finally:browser.close()


def test_file_manager_tree_retains_parent_and_loaded_nested_branches():
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=page_for(browser,'file-manager')
        page.get_by_role('button',name='Load Roots').click()
        page.locator('[data-record-key="workspace"] button').click()
        page.get_by_role('button',name='Browse',exact=True).click()
        page.locator('[data-record-key="src"] button').click()
        page.locator('[data-record-key="src/nested"] button').click()
        page.locator('[data-record-key="src/nested/hi.js"]').wait_for()
        assert page.locator('[data-mcel-folder-tree]').count()==1
        assert page.locator('[data-mcel-tree-branch=""]').count()==1
        assert page.locator('[data-mcel-tree-branch="src"]').count()==1
        assert page.locator('[data-mcel-tree-branch="src/nested"]').count()==1
        assert page.locator('[data-record-key="README.md"]').count()==1
        page.close()
      finally:browser.close()


def test_task_manager_automatic_refresh_toggle_and_unmount_cleanup():
    with sync_playwright() as p:
      browser,_=launch_semantic_browser(p)
      try:
        page=page_for(browser,'task-manager')
        assert page.locator('[data-mcel-auto-refresh]').count()==1
        page.wait_for_function('window.__calls.filter(c=>c.path.endsWith("/task/overview")).length>=1',timeout=9000)
        first=page.evaluate('window.__calls.filter(c=>c.path.endsWith("/task/overview")).length')
        page.locator('[data-mcel-refresh-toggle]').click()
        page.wait_for_timeout(5300)
        assert page.evaluate('window.__calls.filter(c=>c.path.endsWith("/task/overview")).length')==first
        page.locator('[data-mcel-refresh-toggle]').click()
        page.wait_for_function('window.__calls.filter(c=>c.path.endsWith("/task/overview")).length>'+str(first),timeout=9000)
        page.evaluate('window.__mcelWorkbench.app.unmount()')
        assert page.locator('[data-mcel-auto-refresh]').count()==0
        page.close()
      finally:browser.close()
