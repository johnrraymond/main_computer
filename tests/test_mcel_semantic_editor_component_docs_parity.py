"""Bounded generated documentation workflow; no host or production mutations."""
from __future__ import annotations

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from playwright.sync_api import sync_playwright
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_documentation_is_paired_and_exactly_scoped():
    ir = compile_semantic_app('code-editor')
    assert len(ir['provenance']['frontend']['sourceFiles']) == 2
    assert ir['layouts'] == []
    intents = {item['id']: item for item in ir['intents']}
    effects = {item['id']: item for item in ir['effects']}
    assert intents['intent:read-component-doc']['risk'] == 'external-read'
    assert effects['effect:read-component-doc.request']['risk'] == 'external-read'
    assert effects['effect:list-component-docs.request']['risk'] == 'external-read'
    assert intents['intent:choose-component-doc']['operationKind'] == 'interaction'
    assert {node['id'] for node in ir['surfaces'][0]['nodes'] if 'component-doc' in node['id']}


def _start_docs_page(browser):
    page=browser.new_page(viewport={"width":1100,"height":880})
    page.evaluate('''() => {
      window.__docsRequests=[];
      window.fetch=async(uri, options={})=>{
        if(!String(uri).includes('/component-docs/'))return {ok:false,status:404,json:async()=>({error:'outside-test-allowlist'})};
        const body=JSON.parse(options.body||'{}');
        window.__docsRequests.push({uri:String(uri),body});
        if(String(uri).endsWith('/manifest')){
          if(window.__docsManifestWait){await window.__docsManifestWait;}
          return {ok:true,status:200,json:async()=>({ok:true,entries:[
            {id:'code-editor.aider.run',title:'Aider run'},
            {id:'code-editor.viewport.root',title:'Documentation viewport'}]})};
        }
        if(String(uri).endsWith('/read')){
          if(window.__docsDelayRead) await new Promise(r=>window.__docsReleaseRead=r);
          return {ok:true,status:200,
          json:async()=>window.__docsInvalid
          ? {ok:true,exists:true,id:body.id,content_type:'text/plain',content:'BAD'}
          : {ok:true,exists:true,id:body.id,content_type:'text/html',
             content:'<article><script>window.__docsInjected=true</script><h1>Documentation</h1></article>'}};
        }
        throw Error('unexpected docs URL');
      };
    }''')
    page.set_content(render_semantic_page('code-editor'))
    assert page.locator('#mcel-workbench-status').inner_text()=='Semantic candidate ready'
    return page


def test_doc_manifest_authorizes_only_observed_targets_and_does_not_inject_html():
    with sync_playwright() as p:
        b,_=launch_semantic_browser(p)
        try:
            page=_start_docs_page(b)
            # Missing manifest grant is rejected without calling the backend.
            denied=page.evaluate('''async()=>{
              try{return (await window.__mcelWorkbench.app.execute('intent:read-component-doc',{id:'not-listed'})).disposition;}
              catch(e){return String(e);}
            }''')
            assert denied != 'completed'
            assert page.evaluate('window.__docsRequests.length') == 0
            page.evaluate('''async()=>await window.__mcelWorkbench.app.execute('intent:list-component-docs',{})''')
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:component-doc-targets").length') == 2
            # Direct forged request remains refused after the manifest read.
            bad=page.evaluate('''async()=>{
              try{return (await window.__mcelWorkbench.app.execute('intent:read-component-doc',{id:'../../../etc/passwd'})).disposition;}
              catch(e){return String(e);}
            }''')
            assert bad != 'completed'
            page.evaluate('''async()=>await window.__mcelWorkbench.app.execute('intent:choose-component-doc',{key:'code-editor.aider.run'})''')
            page.evaluate('''async()=>await window.__mcelWorkbench.app.execute('intent:read-component-doc',{})''')
            assert 'Documentation' in page.evaluate('window.__mcelWorkbench.app.readState("state:component-doc-content")')
            assert page.evaluate('window.__docsInjected === true') is False
            assert page.evaluate('window.__docsRequests.map(x=>x.uri.split("/").pop())') == ['manifest','read']
            page.close()
        finally:
            b.close()


def test_invalid_doc_receipt_does_not_publish_content():
    with sync_playwright() as p:
        b,_=launch_semantic_browser(p)
        try:
            page=_start_docs_page(b)
            page.evaluate('''async()=>await window.__mcelWorkbench.app.execute('intent:list-component-docs',{})''')
            page.evaluate('''async()=>await window.__mcelWorkbench.app.execute('intent:choose-component-doc',{key:'code-editor.viewport.root'})''')
            page.evaluate('window.__docsInvalid=true')
            disposition=page.evaluate('''async()=>{
              try{return (await window.__mcelWorkbench.app.execute('intent:read-component-doc',{})).disposition;}
              catch(e){return String(e);}
            }''')
            assert disposition != 'completed'
            assert not page.evaluate('window.__mcelWorkbench.app.readState("state:component-doc-content")')
            page.close()
        finally:
            b.close()


def test_late_doc_read_cannot_replace_newer_selected_component():
    with sync_playwright() as p:
        b,_=launch_semantic_browser(p)
        try:
            page=_start_docs_page(b)
            page.evaluate("async()=>await window.__mcelWorkbench.app.execute('intent:list-component-docs',{})")
            page.evaluate("async()=>await window.__mcelWorkbench.app.execute('intent:choose-component-doc',{key:'code-editor.aider.run'})")
            page.evaluate('window.__docsDelayRead=true')
            page.evaluate("() => {window.__lateRead=window.__mcelWorkbench.app.execute('intent:read-component-doc',{});}")
            page.wait_for_function('typeof window.__docsReleaseRead === "function"')
            page.evaluate("async()=>await window.__mcelWorkbench.app.execute('intent:choose-component-doc',{key:'code-editor.viewport.root'})")
            outcome=page.evaluate('''async()=>{
              window.__docsReleaseRead();return (await window.__lateRead).disposition;
            }''')
            assert outcome == 'superseded'
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-component-doc")') == 'code-editor.viewport.root'
            assert not page.evaluate('window.__mcelWorkbench.app.readState("state:component-doc-content")')
            page.close()
        finally:
            b.close()
