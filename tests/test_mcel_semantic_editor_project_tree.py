"""Semantic Code Editor hierarchy preservation, navigation and authority tests.

These are bounded browser and port tests with deterministic backend responses.
They do not certify a live Windows editor or a production replacement.
"""
from __future__ import annotations

from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_phase2b_browser import launch_semantic_browser
from main_computer.mcel_semantic_other_apps_host import render_semantic_page


PORT = Path(__file__).resolve().parents[1] / 'main_computer/web/applications/scripts/mcel-semantic-read-ports.js'


def test_editor_retains_sibling_branches_and_reopens_cached_file_parent():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 880})
            page.evaluate('''() => {
              window.__editorCalls=[];
              window.fetch=async(url,opts)=>{
                const path=String(url),body=JSON.parse(opts.body||'{}');
                window.__editorCalls.push({path,body});
                let result;
                if(path.endsWith('/editor/files')){
                  const entries={
                    '':[{path:'src',name:'src',kind:'dir'},
                        {path:'docs',name:'docs',kind:'dir'},
                        {path:'README.md',name:'README.md',kind:'file'}],
                    'src':[{path:'src/nested',name:'nested',kind:'dir'},
                           {path:'src/a.js',name:'a.js',kind:'file'}],
                    'src/nested':[{path:'src/nested/hi.js',name:'hi.js',kind:'file'}],
                    'docs':[{path:'docs/guide.md',name:'guide.md',kind:'file'}]
                  };
                  if(!(body.path in entries))throw Error('unexpected directory '+body.path);
                  result={ok:true,path:body.path,entries:entries[body.path]};
                }else if(path.endsWith('/editor/read'))result={ok:true,files:[{
                  path:body.files[0],content:'Source for '+body.files[0]}]};
                else throw Error('unexpected endpoint '+path);
                return {ok:true,status:200,json:async()=>result};
              };
            }''')
            page.set_content(render_semantic_page('code-editor'))
            page.evaluate('''() => {
              window.MainComputerMonacoAdapter={
                mount:async opts=>{
                  opts.host.replaceChildren();
                  const node=document.createElement('div');node.className='monaco-editor';
                  node.textContent=opts.value;opts.host.append(node);
                  window.__fakeMonaco={path:opts.path,value:opts.value};
                  return {ok:true,externalOutcome:'model-mounted'};
                },
                setModel:()=>({ok:true}),dispose:()=>({ok:true})
              };
            }''')
            page.get_by_role('button', name='List Files').click()
            page.locator('[data-mcel-tree-branch=""] [data-record-key="src"] button').click()
            page.locator('[data-mcel-tree-branch="src"] [data-record-key="src/nested"]').wait_for()
            page.locator('[data-mcel-tree-branch=""] [data-record-key="docs"] button').click()
            page.locator('[data-mcel-tree-branch="docs"] [data-record-key="docs/guide.md"]').wait_for()
            assert page.locator('[data-mcel-tree-branch="src"] [data-record-key="src/nested"]').count()==1
            # A cached branch must refresh its exact parent before selecting a file.
            page.locator('[data-mcel-tree-branch="src"] [data-record-key="src/a.js"] button').click()
            page.wait_for_function('window.__fakeMonaco?.path === "src/a.js"')
            assert page.evaluate('window.__fakeMonaco.value') == 'Source for src/a.js'
            assert page.locator('[data-mcel-current-directory]').inner_text() == '/src'
            calls=page.evaluate('''() => window.__editorCalls.filter(c=>c.path.endsWith('/editor/files') && c.body.path==='src').length''')
            assert calls==2  # initial visit plus fresh-parent revalidation
            page.get_by_role('button', name='Parent directory').click()
            page.wait_for_function('document.querySelector("[data-mcel-current-directory]")?.textContent === "/"')
            assert page.locator('[data-mcel-tree-branch="docs"] [data-record-key="docs/guide.md"]').count()==1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()


def test_editor_directory_authority_retains_siblings_prunes_removed_and_ignores_late_read():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=browser.new_page()
            page.add_script_tag(content=PORT.read_text(encoding='utf-8'))
            result=page.evaluate('''async () => {
              const reply=x=>({ok:true,status:200,json:async()=>x});
              let releaseSrc;
              let rootHasDocs=true;
              const fetcher=async(url,opts)=>{
                const body=JSON.parse(opts.body||'{}');
                if(!url.endsWith('/editor/files'))throw Error('unexpected');
                if(body.path==='')return reply({ok:true,path:'',entries:rootHasDocs?
                  [{path:'src',name:'src',kind:'dir'},{path:'docs',name:'docs',kind:'dir'}]:
                  [{path:'src',name:'src',kind:'dir'}]});
                if(body.path==='src')return new Promise(resolve=>{
                  releaseSrc=()=>resolve(reply({ok:true,path:'src',entries:[
                    {path:'src/late',name:'late',kind:'dir'}]}));
                });
                if(body.path==='docs')return reply({ok:true,path:'docs',entries:[
                  {path:'docs/guide.md',name:'guide.md',kind:'file'}]});
                throw Error('unexpected path:'+body.path);
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
              const spec={
                'list-files':['source-reader','list','listSourceFiles'],
                'browse-directory':['source-reader','directory','listDirectory']
              };
              const req=(name,args)=>{
                const [cap,op,method]=spec[name];
                return {appId:'semantic-code-editor',intentId:'intent:'+name,
                  effectId:'effect:'+name+'.request',capabilityId:'capability:'+cap,
                  operation:op,runtimeMethod:method,risk:'external-read',arguments:args};
              };
              await port.invoke(req('list-files',{}));
              const delayed=port.invoke(req('browse-directory',{path:'src'}));
              await Promise.resolve();
              await port.invoke(req('browse-directory',{path:'docs'}));
              releaseSrc();await delayed;
              const late=await port.authorize(req('browse-directory',{path:'src/late'}));
              const src=await port.authorize(req('browse-directory',{path:'src'}));
              const docs=await port.authorize(req('browse-directory',{path:'docs'}));
              const escape=await port.authorize(req('browse-directory',{path:'../outside'}));
              rootHasDocs=false;
              await port.invoke(req('list-files',{}));
              const removed=await port.authorize(req('browse-directory',{path:'docs'}));
              return {late,src,docs,escape,removed};
            }''')
            assert result == {'late':False,'src':True,'docs':True,'escape':False,'removed':False}
        finally:
            browser.close()


def test_closed_document_cannot_be_reauthorized_by_late_source_read():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=browser.new_page()
            page.add_script_tag(content=PORT.read_text(encoding='utf-8'))
            result=page.evaluate('''async () => {
              let releaseRead, hasSource=true;
              const reply=x=>({ok:true,status:200,json:async()=>x});
              const fetcher=async(url,opts)=>{
                const body=JSON.parse(opts.body||'{}');
                if(url.endsWith('/editor/files'))return reply({ok:true,path:'',entries:hasSource?
                  [{path:'README.md',name:'README.md',kind:'file'}]:[]});
                if(url.endsWith('/editor/read'))return new Promise(resolve=>{
                  releaseRead=()=>resolve(reply({ok:true,files:[{path:'README.md',content:'source'}]}));
                });
                throw Error('unexpected source endpoint');
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
              const req=(intent,op,method,args)=>({appId:'semantic-code-editor',intentId:'intent:'+intent,
                effectId:'effect:'+intent+'.request',capabilityId:'capability:source-reader',
                operation:op,runtimeMethod:method,risk:'external-read',arguments:args});
              const list=req('list-files','list','listSourceFiles',{});
              const read=req('open-source','read','readSource',{path:'README.md'});
              await port.invoke(list);
              const pending=port.invoke(read);
              await Promise.resolve();
              port.closeDocument('README.md');
              releaseRead();await pending;
              hasSource=false;
              await port.invoke(list);
              const resurrected=await port.authorize(read);
              const save=await port.authorize({appId:'semantic-code-editor',intentId:'intent:save-source',
                effectId:'effect:save-source.request',capabilityId:'capability:source-writer',
                operation:'save',runtimeMethod:'saveSource',risk:'source-write',
                arguments:{path:'README.md',text:'new content'}});
              return {resurrected,save};
            }''')
            assert result == {'resurrected': False, 'save': False}
        finally:
            browser.close()


def test_closing_inactive_tab_revokes_its_port_document_authority():
    from main_computer.mcel_semantic_other_apps_host import render_semantic_page
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=browser.new_page()
            page.evaluate('''() => {
              window.fetch=async(url,opts)=>{
                const body=JSON.parse(opts.body||'{}');let value;
                if(String(url).endsWith('/editor/files'))value={ok:true,path:'',entries:[
                  {path:'README.md',name:'README.md',kind:'file'},
                  {path:'NOTES.md',name:'NOTES.md',kind:'file'}]};
                else if(String(url).endsWith('/editor/read'))value={ok:true,files:[
                  {path:body.files[0],content:'source '+body.files[0]}]};
                else throw Error('unrecognized endpoint');
                return {ok:true,status:200,json:async()=>value};
              };
            }''')
            html=render_semantic_page('code-editor')
            # Observe the actual runtime's call into the existing port, without
            # changing the runtime or port source used by the browser.
            assert 'const app=McelSemanticOperationalRuntime.mount(' in html
            html=html.replace('const app=McelSemanticOperationalRuntime.mount(',
               '''window.__closedPaths=[];
               const tracingPort={...port,closeDocument:path=>{
                 window.__closedPaths.push(path);return port.closeDocument(path);
               }};
               const app=McelSemanticOperationalRuntime.mount(''').replace(
               'capabilities:port,presentation:"expert"','capabilities:tracingPort,presentation:"expert"')
            page.set_content(html)
            page.evaluate('''() => {
              window.MainComputerMonacoAdapter={
                mount:async opts=>{opts.host.replaceChildren();
                  const node=document.createElement('div');node.className='monaco-editor';
                  node.textContent=opts.value;opts.host.append(node);
                  window.__monacoPath=opts.path;return {ok:true,externalOutcome:'model-mounted'};},
                setModel:()=>({ok:true}),dispose:()=>({ok:true})
              };
            }''')
            page.get_by_role('button',name='List Files').click()
            page.locator('[data-record-key="README.md"] button').click()
            page.wait_for_function('window.__monacoPath === "README.md"')
            page.locator('[data-record-key="NOTES.md"] button').click()
            page.wait_for_function('window.__monacoPath === "NOTES.md"')
            page.locator('[data-mcel-close-tab="README.md"]').click()
            assert page.evaluate('window.__closedPaths') == ['README.md']
            assert page.locator('[data-mcel-open-tab="README.md"]').count() == 0
            assert page.locator('[data-mcel-open-tab="NOTES.md"]').count() == 1
        finally:
            browser.close()
