"""Canonical E/W/G integration and Twiddle acceptance, including a non-Calculator host."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_ewg_composition_twiddle import run


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        binary=shutil.which('chromium') or shutil.which('chromium-browser')
        b=playwright.chromium.launch(headless=True,executable_path=binary or None)
        try:yield b
        finally:b.close()


def open_page(browser,width=1272):
    page=browser.new_page(viewport={'width':width,'height':900})
    page.evaluate('''()=>{const m=new Map();const store={getItem:k=>m.get(k)||null,
      setItem:(k,v)=>m.set(k,String(v)),removeItem:k=>m.delete(k),
      clear:()=>m.clear(),key:i=>[...m.keys()][i]||null,get length(){return m.size}};
      Object.defineProperty(window,'localStorage',{configurable:true,value:store});
      Object.defineProperty(window,'sessionStorage',{configurable:true,value:store});
      window.__copied=[];Object.defineProperty(navigator,'clipboard',{
        configurable:true,value:{writeText:async text=>{window.__copied.push(text);}}});}''')
    page.route('**/*',lambda route:route.abort())
    page.set_content(render_expanded_page(promoted=True))
    return page


def test_title_counter_direct_clipboard_and_causal_evidence(browser):
    page=open_page(browser)
    try:
        assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
        title=page.locator('[data-mcel-application-title]')
        counter=page.locator('[data-mcel-diagnostic-title-control]')
        assert title.inner_text()=='Semantic Calculator'
        assert counter.count()==1
        assert counter.evaluate('el=>el.previousElementSibling===document.querySelector("[data-mcel-application-title]")')
        page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
        counter.click()
        page.wait_for_function('window.__copied.length===1')
        copied=json.loads(page.evaluate('window.__copied[0]'))
        evidence=copied['diagnosticEvidence']
        assert copied['appId']=='semantic-calculator-expanded'
        assert copied['schema']=='mcel-diagnostics-counter-copy-v4'
        assert {x['code'] for x in evidence['findings']} >= {'layout-authority-override','unallocated-vertical-space'}
        overlap=next(x for x in evidence['findings'] if x['code']=='layout-authority-override')
        assert any(x['origin']=='inline' and x['important'] and x['value']=='grid' for x in overlap['evidence']['competingRules'])
        assert any(x['origin']=='stylesheet' and x['value']=='flex' for x in overlap['evidence']['competingRules'])
        assert evidence['coverage']['nestedCanonical']['status']=='declared'
        assert any(x['layoutNode']=='notebook-cells' and x['reason']=='verifyControls:false'
                   for x in evidence['coverage']['nestedCanonical']['excluded'])
        assert evidence['plugins']['status']=='observed'
        assert evidence['plugins']['notExecuted']
        assert evidence['coverage']['selfDiagnosis']['status']=='not-covered'
        assert evidence['privacy']['conversationMessages'] is False
        assert page.locator('dialog:visible,[role="dialog"]:visible').count()==0
        assert page.locator('[data-mcel-region$=".help-workspace"]').is_visible()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.locator('[data-chat-conversation-switch]').click()
        counter.click()
        page.wait_for_function('window.__copied.length===2')
        copied=json.loads(page.evaluate('window.__copied[1]'))
        codes=[item['code'] for item in copied['diagnosticEvidence']['findings']]
        assert codes.count('atomic-text-split')>=2
        assert any(x['token']=='Thinking' for x in copied['diagnosticEvidence']['findings']
                   if x['code']=='atomic-text-split' for x in x['evidence']['brokenWords'])
        assert {x['projection'] for x in copied['diagnosticEvidence']['history']} >= {'conversation','notebook'}
        assert copied['diagnosticEvidence']['nested']['status']=='pass'
    finally:page.close()


def test_shared_collector_handles_unrelated_semantic_app_without_new_rules(browser):
    page=open_page(browser)
    try:
        data=page.evaluate('''()=>{
          const root=document.createElement('main');root.id='mcel-generic-test-root';
          root.innerHTML='<div data-mcel-navigation-chrome><h1 data-mcel-application-title>Generic Editor</h1></div>'+
            '<section data-mcel-region="interface-region:generic-editor.work">'+
            '<label data-mcel-chat-role="status">Thinking about this <input type="checkbox"></label></section>';
          document.body.appendChild(root);
          const label=root.querySelector('label');label.style.cssText='display:block;width:32px;overflow-wrap:anywhere;';
          const app='generic-editor';
          const ir={application:{appId:app}};
          MCELDiagnosticsCounterWidget.mountSemantic(app,root,ir,null);
          return {evidence:MCELDiagnosticEvidence.snapshot(app),widget:!!root.querySelector('[data-mcel-diagnostic-title-control]')};
        }''')
        assert data['widget']
        assert any(f['code']=='atomic-text-split' for f in data['evidence']['findings'])
        assert data['evidence']['coverage']['nestedCanonical']['status']=='no-layout-tree'
        assert data['evidence']['plugins']['status']=='observed'
    finally:page.close()


def test_twiddle_uses_same_clipboard_evidence_and_preserves_original_failure(tmp_path:Path):
    subprocess.run([sys.executable,'-m','tools.mcel_ewg_composition_twiddle',
        '--widths','390','1272','--output',str(tmp_path),'--no-screenshots'],
        check=True,timeout=90,capture_output=True,text=True)
    report=json.loads((tmp_path/'report.json').read_text(encoding='utf8'))
    assert len(report['cases'])==4
    for width in (390,1272):
        conversation=next(c for c in report['cases'] if c['width']==width and c['projection']=='conversation')
        notebook=next(c for c in report['cases'] if c['width']==width and c['projection']=='notebook')
        assert conversation['report']['schema']=='mcel-diagnostics-counter-copy-v4'
        assert notebook['report']['schema']=='mcel-diagnostics-counter-copy-v4'
        assert conversation['report']['diagnosticEvidence']['schema']=='mcel-diagnostic-evidence-v1'
        assert notebook['report']['diagnosticEvidence']['schema']=='mcel-diagnostic-evidence-v1'
        assert 'layout-authority-override' in {f['code'] for f in conversation['report']['diagnosticEvidence']['findings']}
        assert 'unallocated-vertical-space' in {f['code'] for f in conversation['report']['diagnosticEvidence']['findings']}
        assert [f['code'] for f in notebook['report']['diagnosticEvidence']['findings']].count('atomic-text-split')>=2
    assert (tmp_path/'report.json').is_file()
