"""Semantic modal-subprogram lab: isolated lifecycle + geometry regression."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'main_computer/web/applications'
HTML = APP / 'apps/mcel-lab.html'
RUNTIME = APP / 'scripts/mcel-semantic-modal-subprogram.js'
FIXTURE = APP / 'scripts/mcel-modal-subprogram-lab.js'
CSS = APP / 'styles/mcel-semantic-modal-subprogram.css'
LAB_CSS = APP / 'styles/mcel-lab.css'
SHELL = ROOT / 'main_computer/web/applications.html'
REGISTRY = APP / 'scripts/mcel-element-registry.js'
ELEMENTS = APP / 'scripts/mcel-elements-core.js'


def test_modal_is_registered_as_general_mcel_semantic_subprogram():
    shell = SHELL.read_text(encoding='utf8')
    assert 'mcel-semantic-modal-subprogram.js' in shell
    assert shell.index('mcel-semantic-modal-subprogram.js') < shell.index('mcel-modal-subprogram-lab.js')
    assert shell.index('mcel-modal-subprogram-lab.js') < shell.index('mcel-lab.js')
    assert 'mcel-semantic-modal-subprogram.css' in shell
    assert 'element.core.modal-subprogram' in ELEMENTS.read_text(encoding='utf8')
    html = HTML.read_text(encoding='utf8')
    assert 'id="mcel-modal-subprogram-lab"' in html
    assert 'data-mcel-modal-reconnect' in html
    assert 'data-mcel-modal-fail' in html


def test_modal_contract_treats_hard_minimum_separately_from_soft_percent_request():
    node = shutil.which('node')
    if not node: pytest.skip('Node not installed')
    source = f'''
const modal=require({json.dumps(str(RUNTIME))});
const lab=require('fs').readFileSync({json.dumps(str(FIXTURE))},'utf8');
const spec={{id:'proof',geometry:{{minimumWidth:440,minimumHeight:450,preferredWidth:690,preferredHeight:560,maximumRequestedWidthPercent:86,maximumRequestedHeightPercent:82}},regions:[
{{id:'header',budget:'fixed',height:104,overflow:'wrap'}},
{{id:'details',budget:'fixed',height:118,overflow:'clip'}},
{{id:'output',budget:'flexible',minimumHeight:142,overflow:'scroll'}},
{{id:'actions',budget:'fixed',height:74,overflow:'clip'}}]}};
const definition=modal.define(spec);
const big=modal.allocate(definition,{{width:1440,height:900}});
const small=modal.allocate(definition,{{width:380,height:320}});
let rejected=false;
try{{modal.define({{...spec,regions:spec.regions.map(r=>r.id==='header'?{{...r,height:400}}:r)}});}}catch(e){{rejected=e.message.includes('UNSATISFIABLE');}}
process.stdout.write(JSON.stringify({{big,small,rejected}}));
'''
    proc = subprocess.run([node, '-e', source], capture_output=True, text=True, check=True)
    proof = json.loads(proc.stdout)
    assert proof['big']['width'] == 690
    assert proof['big']['height'] == 560
    assert proof['big']['regions'] == {'header': 104, 'details':118, 'output':264, 'actions':74}
    assert proof['small']['width'] == 440  # minimum overrides 86% request
    assert proof['small']['height'] == 450
    assert proof['small']['exceedsViewport'] is True
    assert proof['rejected'] is True


def test_general_modal_contract_is_not_fixed_to_lms_four_region_shape():
    node=shutil.which('node')
    if not node: pytest.skip('Node not installed')
    script=f"""
const api=require({json.dumps(str(RUNTIME))});
const c=api.define({{id:'unrelated-application',geometry:{{minimumWidth:300,minimumHeight:320,preferredWidth:600,preferredHeight:600,maximumRequestedWidthPercent:90,maximumRequestedHeightPercent:90}},regions:[
  {{id:'toolbar',budget:'fixed',height:60,overflow:'wrap'}},
  {{id:'left',budget:'flexible',minimumHeight:110,weight:1,overflow:'scroll'}},
  {{id:'right',budget:'flexible',minimumHeight:110,weight:2,overflow:'scroll'}}]}});
const g=api.allocate(c,{{width:1000,height:800}});
process.stdout.write(JSON.stringify(g));
"""
    out=subprocess.run([node,'-e',script],check=True,capture_output=True,text=True)
    data=json.loads(out.stdout)
    assert data['height']==600
    assert data['regions']['toolbar']==60
    assert data['regions']['left']+data['regions']['right']==540
    assert data['regions']['right']>data['regions']['left']


def _browser():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    return sync_playwright


def test_lab_modal_lifecycle_streaming_geometry_and_reconnect():
    with _browser()() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None,
            args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1366, 'height': 850})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.set_content('<!doctype html><html><body style="margin:0;background:#080d0c">'+HTML.read_text(encoding='utf8')+'</body></html>')
        page.add_style_tag(content=CSS.read_text(encoding='utf8')+'\n'+LAB_CSS.read_text(encoding='utf8'))
        page.evaluate("document.getElementById('mcel-lab-app').style.display='block'")
        page.add_script_tag(path=str(RUNTIME))
        page.add_script_tag(path=str(FIXTURE))
        page.evaluate('McelModalSubprogramLab.init(document)')
        page.locator('[data-mcel-modal-start]').click()
        assert page.locator('[data-mcel-semantic-modal="mcel-lab-operation-fixture"]').is_visible()
        assert 'SUBMITTING' in page.locator('.mcel-modal-lab-details').inner_text()
        assert page.evaluate('McelModalSubprogramLab.controller.modal.lifecycle()') == 'ACTIVE' 
        before = page.evaluate('''() => {
          const c=McelModalSubprogramLab.controller;
          const p=c.modal.panel, o=c.modal.region('output'), h=c.modal.region('header'), f=c.modal.region('actions');
          const r=(el)=>{const x=el.getBoundingClientRect();return [x.width,x.height,x.top]};
          return {card:r(p), output:r(o), header:r(h), footer:r(f), allocation:c.modal.geometry()};
        }''')
        page.evaluate('document.querySelector("[data-mcel-modal-stress]").click()')
        page.wait_for_timeout(700)
        after = page.evaluate('''() => {
          const c=McelModalSubprogramLab.controller;
          const p=c.modal.panel, o=c.modal.region('output'), h=c.modal.region('header'), f=c.modal.region('actions');
          const r=(el)=>{const x=el.getBoundingClientRect();return [x.width,x.height,x.top]};
          return {card:r(p), output:r(o), header:r(h), footer:r(f), allocation:c.modal.geometry(),
            scrollable:o.scrollHeight>o.clientHeight, scrollOwner:getComputedStyle(o).overflowY,
            outerScroll:getComputedStyle(p).overflowY};
        }''')
        for key in ('card','header','output','footer'):
            assert before[key] == after[key], (key,before[key],after[key])
        assert after['allocation'] == before['allocation']
        assert after['scrollable'] and after['scrollOwner'] == 'auto'
        assert after['outerScroll'] == 'hidden'
        assert len(page.locator('.mcel-semantic-modal-region--output pre').inner_text().splitlines()) >= 180
        page.get_by_role('button', name='Dismiss', exact=True).click()
        assert not page.locator('[data-mcel-semantic-modal="mcel-lab-operation-fixture"]').is_visible()
        assert page.evaluate('McelModalSubprogramLab.controller.modal.lifecycle()') == 'DISMISSED'
        prior_id=page.evaluate('McelModalSubprogramLab.controller.service.snapshot().id')
        page.wait_for_timeout(2700)
        assert page.evaluate('McelModalSubprogramLab.controller.service.snapshot().phase') == 'COMPLETED'
        page.locator('[data-mcel-modal-reconnect]').click()
        assert page.locator('[data-mcel-semantic-modal="mcel-lab-operation-fixture"]').is_visible()
        assert page.evaluate('McelModalSubprogramLab.controller.service.snapshot().id') == prior_id
        assert 'COMPLETED' in page.locator('.mcel-modal-lab-details').inner_text()
        assert page.evaluate('McelModalSubprogramLab.controller.modal.lifecycle()') == 'ACTIVE'
        page.keyboard.press('Escape')
        assert not page.locator('[data-mcel-semantic-modal="mcel-lab-operation-fixture"]').is_visible()
        assert not errors, errors
        browser.close()


def test_lab_modal_refusal_and_oversize_viewport():
    with _browser()() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,
            args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':380,'height':320})
        page.set_content('<!doctype html><html><body>'+HTML.read_text(encoding='utf8')+'</body></html>')
        page.add_style_tag(content=CSS.read_text(encoding='utf8')+'\n'+LAB_CSS.read_text(encoding='utf8'))
        page.evaluate("document.getElementById('mcel-lab-app').style.display='block'")
        page.add_script_tag(path=str(RUNTIME)); page.add_script_tag(path=str(FIXTURE))
        page.evaluate('McelModalSubprogramLab.init(document)')
        page.evaluate('document.querySelector("[data-mcel-modal-fail]").click()')
        proof=page.evaluate('''() => {
          const c=McelModalSubprogramLab.controller;
          return {g:c.modal.geometry(),open:c.modal.isOpen(),scroll:getComputedStyle(c.modal.element).overflowY};
        }''')
        assert proof['open']
        assert proof['g']['width']==440 and proof['g']['height']==450
        assert proof['g']['exceedsViewport']
        assert proof['scroll']=='auto'
        page.wait_for_timeout(3500)
        assert page.evaluate('McelModalSubprogramLab.controller.service.snapshot().phase')=='FAILED'
        assert 'REFUSED' in page.locator('.mcel-semantic-modal-region--output').inner_text()
        page.evaluate('McelModalSubprogramLab.controller.modal.dismiss()')
        browser.close()


def test_lab_modal_cancel_is_separate_from_dismiss_and_viewport_resize_is_explicit():
    with _browser()() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,
            args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1200,'height':800})
        page.set_content('<!doctype html><html><body>'+HTML.read_text(encoding='utf8')+'</body></html>')
        page.add_style_tag(content=CSS.read_text(encoding='utf8')+'\n'+LAB_CSS.read_text(encoding='utf8'))
        page.evaluate("document.getElementById('mcel-lab-app').style.display='block'")
        page.add_script_tag(path=str(RUNTIME));page.add_script_tag(path=str(FIXTURE))
        page.evaluate('McelModalSubprogramLab.init(document)')
        page.evaluate('document.querySelector("[data-mcel-modal-start]").click()')
        original=page.evaluate('McelModalSubprogramLab.controller.modal.geometry()')
        assert original['width']==690 and original['height']==560
        page.set_viewport_size({'width':700,'height':600})
        page.wait_for_timeout(100)
        revised=page.evaluate('McelModalSubprogramLab.controller.modal.geometry()')
        assert revised['width']==602 and revised['height']==492
        for key in ('header','details','actions'):
            assert revised['regions'][key]==original['regions'][key]
        assert revised['regions']['output']<original['regions']['output']
        page.get_by_role('button',name='Cancel operation').click()
        page.wait_for_timeout(700)
        assert page.evaluate('McelModalSubprogramLab.controller.service.snapshot().phase')=='CANCELLED'
        page.get_by_role('button',name='Dismiss',exact=True).click()
        page.evaluate('document.querySelector("[data-mcel-modal-reconnect]").click()')
        assert page.evaluate('McelModalSubprogramLab.controller.service.snapshot().phase')=='CANCELLED'
        assert page.evaluate('McelModalSubprogramLab.controller.modal.isOpen()')
        browser.close()
