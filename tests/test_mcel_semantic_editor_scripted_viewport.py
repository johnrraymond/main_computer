"""Scripted documentation pixels stay isolated from the semantic host."""
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_scripted_viewport_remains_an_explicit_semantic_capability():
    ir=compile_semantic_app('code-editor')
    caps={c['id']:c for c in ir['capabilities']}
    intents={i['id']:i for i in ir['intents']}
    assert caps['capability:sandboxed-viewport']['risk']=='external-read'
    assert intents['intent:run-component-doc-script']['risk']=='external-read'
    assert ir['layouts']==[] and len(ir['provenance']['frontend']['sourceFiles'])==2


def test_generated_viewport_executes_sandboxed_pixel_script_and_renders_pixels():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=browser.new_page(viewport={'width':1100,'height':850})
            page.set_content(render_semantic_page('code-editor'))
            assert page.locator('#mcel-workbench-status').inner_text()=='Semantic candidate ready'
            result=page.evaluate('''async()=>{
              window.__hostTouched=false;
              const app=window.__mcelWorkbench.app;
              const script=`vram.reset({width:4,height:2,fill:[12,13,14,255]});
                vram.setPixel(2,1,[255,0,0,255]);
                console.log('pixel',vram.getPixel(2,1));
                try { parent.__hostTouched=true; } catch(e) { console.log('opaque-origin'); }`;
              const receipt=await app.execute('intent:run-component-doc-script',{script});
              return {disposition:receipt.disposition,
                data:JSON.parse(app.readState('state:component-doc-script-result')),
                touched:window.__hostTouched,
                image:document.querySelector('[data-mcel-sandboxed-image]')?.getAttribute('src')||'',
                log:document.querySelector('[data-mcel-sandboxed-log]')?.textContent||''};
            }''')
            assert result['disposition']=='completed'
            assert result['data']['kind']=='mcel.sandboxed-viewport-result.v1'
            assert result['data']['status']=='completed'
            assert result['data']['width']==4 and result['data']['height']==2
            assert result['image'].startswith('data:image/png;base64,')
            assert 'pixel' in result['log']
            assert result['touched'] is False
            assert page.locator('iframe[sandbox="allow-scripts"]').count()==0
        finally:
            browser.close()


def test_sandboxed_script_failure_is_reported_as_data_without_host_execution():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=browser.new_page()
            page.set_content(render_semantic_page('code-editor'))
            result=page.evaluate('''async()=>{
              const app=window.__mcelWorkbench.app;
              const a=await app.execute('intent:run-component-doc-script',{script:"throw Error('sandbox-test')"});
              const b=JSON.parse(app.readState('state:component-doc-script-result'));
              const refused=await app.execute('intent:run-component-doc-script',{script:''});
              return {a:a.disposition, b,refused:refused.disposition};
            }''')
            assert result['a']=='completed'
            assert result['b']['status']=='script-error'
            assert 'sandbox-test' in '\n'.join(result['b']['logs'])
            assert result['refused'] != 'completed'
        finally:
            browser.close()


def test_sandboxed_script_network_is_blocked_by_csp_and_not_dispatched():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=browser.new_page()
            external=[]
            page.on('request',lambda request:external.append(request.url))
            page.set_content(render_semantic_page('code-editor'))
            result=page.evaluate('''async()=>{
              const app=window.__mcelWorkbench.app;
              const receipt=await app.execute('intent:run-component-doc-script',
                {script:"try { await fetch('http://127.0.0.1:8765/api/applications/editor/files'); console.log('unexpected-network'); } catch (e) { console.log('network-blocked'); }"});
              return {disposition:receipt.disposition,
                response:JSON.parse(app.readState('state:component-doc-script-result'))};
            }''')
            assert result['disposition']=='completed'
            assert result['response']['status']=='completed'
            assert 'network-blocked' in '\n'.join(result['response']['logs'])
            assert 'unexpected-network' not in '\n'.join(result['response']['logs'])
            assert external==[]
        finally:
            browser.close()


def test_script_source_cannot_break_out_of_embedded_sandbox_html():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=browser.new_page()
            page.set_content(render_semantic_page('code-editor'))
            result=page.evaluate('''async()=>{
              window.__hostTouched=false;
              const script="console.log('</script><script>parent.__hostTouched=true</script>')";
              const out=await window.__mcelWorkbench.app.execute('intent:run-component-doc-script',{script});
              return {status:out.disposition,hostTouched:window.__hostTouched,
                logs:JSON.parse(window.__mcelWorkbench.app.readState('state:component-doc-script-result')).logs};
            }''')
            assert result['status']=='completed'
            assert not result['hostTouched']
            assert 'parent.__hostTouched=true' in '\n'.join(result['logs'])
        finally:
            browser.close()
