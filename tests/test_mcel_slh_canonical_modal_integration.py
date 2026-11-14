"""SLH/LMS uses the canonical MCEL Lab modal; no application-owned geometry."""
from __future__ import annotations

from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'main_computer/web/applications'


def _page(browser, viewport=(1250,810)):
    page=browser.new_page(viewport={'width':viewport[0],'height':viewport[1]})
    page.set_content('''<!doctype html><html><body class="mcel-workbench-page" style="margin:0;background:#0b1016">
       <button id="navigator" style="position:fixed;left:8px;top:12px;width:140px;z-index:1">Switch target</button>
       <main id="slh" style="position:absolute;left:210px;top:34px;width:790px;height:650px">
         <section data-mcel-zone="workspace" style="width:100%;height:100%;background:#1a2833">
           <button id="workspace-button">Workspace action</button>
         </section>
       </main></body></html>''')
    page.add_style_tag(content=(WEB/'styles/mcel-semantic-modal-subprogram.css').read_text(encoding='utf8'))
    page.add_style_tag(content=(WEB/'styles/mcel-semantic-workbench.css').read_text(encoding='utf8'))
    page.add_script_tag(path=str(WEB/'scripts/mcel-semantic-modal-subprogram.js'))
    page.add_script_tag(path=str(WEB/'scripts/mcel-semantic-slh-management-modal.js'))
    page.evaluate('''() => {
      window.obs={state:'IDLE',busy:false,operation:null};
      window.cancelRequests=0;
      window.backend=async(action,args)=>{
        if(action==='operation-state')return window.obs;
        if(action==='cancel-operation'){window.cancelRequests++;return {status:'REFUSED'};}
        return {phase:'RECORDED',operation_id:args.operationId,result:{status:'COMPLETE'}};
      };
      window.manager=McelSlhManagementModal.create({host:document.querySelector('#slh'),request:window.backend});
    }''')
    return page


def _geometry(page):
    return page.evaluate('''() => {
      const m=window.manager.modal;
      const rect=(el)=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
      const output=m.region('output');
      return {panel:rect(m.panel),header:rect(m.region('header')),
        details:rect(m.region('details')),output:rect(output),actions:rect(m.region('actions')),
        scrollHeight:output.scrollHeight,clientHeight:output.clientHeight,
        panelScrollHeight:m.panel.scrollHeight,panelClientHeight:m.panel.clientHeight,
        viewport:rect(m.element),oversized:m.geometry().exceedsViewport,
        commit:m.panel.dataset.mcelGeometryCommit,blocking:m.element.dataset.mcelBlocking};
    }''')


def test_slh_modal_contract_is_compiled_and_loaded_before_management_controller():
    from main_computer.mcel_slh_application import render_slh_page
    html=render_slh_page('overview')
    assert html.index('global.McelSemanticModalSubprogram = api') < html.index('global.McelSlhManagementModal=Object.freeze')
    assert '.mcel-semantic-modal-region[data-mcel-overflow="scroll"]' in html
    assert 'mcel-modal-subprogram-lab' not in html
    contract=(WEB/'scripts/mcel-semantic-slh-management-modal.js').read_text()
    assert 'Engine.define({' in contract
    assert 'id:"slh-lms-operation-monitor"' in contract
    assert '{id:"output",budget:"flexible",minimumHeight:132,overflow:"scroll"}' in contract
    assert 'new McelSemanticModalSubprogram' not in contract


def test_click_opens_immediately_and_streaming_does_not_change_geometry():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_page(browser)
            page.evaluate('''() => window.manager.begin('intent:seh-deployment-deploy','Deploy SEH')''')
            assert page.locator('[data-mcel-lms-operation-modal]').is_visible()
            first=_geometry(page)
            assert first['commit']=='open'
            assert not first['oversized']
            assert first['panel']['height']<=first['viewport']['height']
            assert first['panelScrollHeight']==first['panelClientHeight']
            assert first['header']['height']==116
            assert first['details']['height']==142
            assert first['actions']['height']==70
            page.evaluate('''async () => {
              const id='mcel-seh-modal-stream-123456789';
              window.manager.track(id);
              window.obs={state:'BUSY',busy:true,operation:{operation_id:id,action:'deploy-seh',
                 phase:'RUNNING',stage:'HARNESS_EXECUTING',cancelable:false,
                 output:Array.from({length:120},(_,i)=>({seq:i+1,stream:i%2?'stderr':'stdout',
                   text:`simulated diagnostic line ${i} - ${'x'.repeat(90)}`}))}};
              await window.manager.start();
            }''')
            after=_geometry(page)
            for name in ('panel','header','details','output','actions'):
                assert after[name]==first[name], (name,first[name],after[name])
            assert after['commit']=='open'
            assert after['scrollHeight']>after['clientHeight']
            assert after['panelScrollHeight']==after['panelClientHeight']
            assert 'simulated diagnostic line 119' in page.locator('[data-mcel-lms-operation-results]').inner_text()
            # Reading earlier output must not be interrupted by subsequent polling.
            page.evaluate('''() => window.manager.modal.region('output').scrollTop=0''')
            page.evaluate('''async()=>{
              window.obs.operation.output.push({seq:121,stream:'stdout',text:'new final line'});
              await window.manager.refresh();
            }''')
            assert page.evaluate('window.manager.modal.region("output").scrollTop')==0
            assert _geometry(page)['panel']==first['panel']
        finally:
            browser.close()


def test_workspace_viewport_not_full_browser_and_oversize_scrolling():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_page(browser)
            page.evaluate('''() => window.manager.begin('intent:seh-deployment-deploy','Deploy SEH')''')
            initial=_geometry(page)
            assert initial['viewport']['x']>=209
            assert initial['viewport']['width']==790
            assert initial['panel']['width']<=680
            # Viewport change is a legitimate renegotiation; a content update is not.
            page.set_viewport_size({'width':460,'height':330})
            page.wait_for_function('''() => window.manager.modal.geometry().exceedsViewport''')
            small=_geometry(page)
            assert small['panel']['width']>=440
            assert small['panel']['height']>=460
            assert small['oversized']
            assert small['commit']=='viewport-change'
            assert small['viewport']['width'] < small['panel']['width']
            assert page.locator('[data-mcel-lms-operation-modal]').evaluate(
                '(node)=>getComputedStyle(node).overflowY')=='auto'
        finally:
            browser.close()


def test_dismiss_and_reconnect_preserve_lms_state_and_nonblocking_notices():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_page(browser)
            page.evaluate('''async()=>{
              window.obs={state:'BUSY',busy:true,operation:{operation_id:'mcel-seh-test-123456789',
                action:'activate-seh',stage:'ACTIVATION_EXECUTING',phase:'RUNNING',cancelable:false}};
              await window.manager.start();
            }''')
            assert page.locator('[data-mcel-lms-operation-modal]').is_visible()
            assert page.evaluate('document.querySelector("#workspace-button").inert')
            page.get_by_role('button',name='Dismiss').click()
            assert page.locator('[data-mcel-lms-operation-modal]').is_hidden()
            assert page.evaluate('document.querySelector("#workspace-button").inert')==False
            assert page.evaluate('window.cancelRequests')==0
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            assert page.locator('[data-mcel-lms-operation-modal]').is_visible()
            page.keyboard.press('Escape')
            assert page.locator('[data-mcel-lms-operation-modal]').is_hidden()
            page.evaluate('''async()=>{
              window.obs={state:'IDLE',busy:false,operation:null,last_operation:{
                operation_id:'mcel-seh-test-123456789',action:'activate-seh',
                phase:'RECORDED',stage:'HARNESS_RETURNED',cancelable:false,
                result:{status:'INCOMPLETE',remote_mutation:'UNKNOWN'}}};
              await window.manager.refresh();
            }''')
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            assert page.locator('[data-mcel-lms-operation-modal]').is_visible()
            assert page.locator('[data-mcel-lms-operation-modal]').get_attribute('data-mcel-blocking')=='false'
            assert page.evaluate('document.querySelector("#workspace-button").inert')==False
            # An unrelated navigation button remains operable during the nonblocking notice.
            page.locator('#navigator').click()
            assert page.evaluate('window.cancelRequests')==0
        finally:
            browser.close()
