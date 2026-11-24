"""Cross-law TNG checks: ownership, stateful paint, Form changes, and geometry.

All browser tests use the real shared chrome against canonical SLH/Calculator
surfaces.  No application IDs appear in the chrome source; an SLH fixture
is used only as one realistic consumer of the shared contract.
"""
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_slh_startup_ewg import MOCK
from main_computer.mcel_calculator_expanded_host import render_expanded_page

ROOT = Path(__file__).resolve().parents[1]
CHROME = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js'
RUNTIME = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'


def _slh(browser):
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.evaluate(MOCK)
    page.set_content(render_slh_page(repo=ROOT))
    page.wait_for_function('() => !!window.__mcelSlh?.app')
    return page


def test_chrome_has_no_application_specific_surface_or_navigation_exception():
    source = CHROME.read_text(encoding='utf-8')
    assert 'surface-node:slh.activity' not in source
    assert 'plainExplorerNavigation' not in source
    assert 'data-mcel-explorer-navigation] [role="treeitem"]' not in source[source.index('function resolveMaterialTargets'):source.index('function inheritsEnclosingCRT')]
    assert 'data-mcel-control-surface' not in source
    assert 'data-mcel-control-surface' not in RUNTIME.read_text(encoding='utf-8')
    assert 'flatNavigationCache' in source


def test_unframed_content_inherits_but_selected_authored_and_form_shaped_surfaces_keep_paint():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
        try:
            page = _slh(browser)
            state = page.evaluate('''async () => {
              const root=document.querySelector('#mcel-workbench');
              const original=root.querySelector('[data-mcel-node][data-node-kind="operation-evidence"]');
              const frame=original.closest('[data-mcel-region]');
              const css=el=>getComputedStyle(el).backgroundColor;
              const kids=(el)=>el.replaceChildren(document.createElement('p'),document.createElement('details'));
              const names=['plain','selected','class-selected','form','painted','direct'];
              const el={};
              for(const name of names){
                const node=original.cloneNode(false);kids(node);
                node.setAttribute('data-mcel-node','surface-node:test-'+name);
                if(name==='selected')node.setAttribute('aria-selected','true');
                if(name==='class-selected')node.classList.add('is-active');
                if(name==='form')node.setAttribute('data-mcel-form-presentation-corner','round');
                if(name==='painted')node.style.backgroundColor='rgb(20, 30, 40)';
                if(name==='direct')node.append(' Record');
                frame.append(node);el[name]=node;
              }
              await new Promise(done=>setTimeout(done,150));
              const initial=Object.fromEntries(names.map(n=>[n,css(el[n])]));
              // Form now takes an independently painted boundary.
              el.plain.setAttribute('data-mcel-form-presentation-corner','round');
              await new Promise(done=>setTimeout(done,150));
              const formed=css(el.plain);
              el.plain.removeAttribute('data-mcel-form-presentation-corner');
              await new Promise(done=>setTimeout(done,150));
              const returned=css(el.plain);
              // Selection toggled after mounting must update immediately.
              el.plain.setAttribute('aria-selected','true');
              await new Promise(done=>setTimeout(done,150));
              const selected=css(el.plain);
              el.plain.removeAttribute('aria-selected');
              await new Promise(done=>setTimeout(done,150));
              const unselected=css(el.plain);
              const beforeOff=css(original);
              root.setAttribute('data-mcel-semantic-chrome','chrome-strict-hierarchy');
              const off=css(original);
              root.setAttribute('data-mcel-semantic-chrome','chrome-tng-side-rails');
              const on=css(original);
              return {initial,formed,returned,selected,unselected,beforeOff,off,on};
            }''')
            assert state['initial']['plain'] == 'rgba(0, 0, 0, 0)',state
            for name in ('selected','class-selected','form','painted','direct'):
                assert state['initial'][name] != 'rgba(0, 0, 0, 0)',(name,state)
            assert state['formed'] != 'rgba(0, 0, 0, 0)',state
            assert state['returned'] == 'rgba(0, 0, 0, 0)',state
            assert state['selected'] != 'rgba(0, 0, 0, 0)',state
            assert state['unselected'] == 'rgba(0, 0, 0, 0)',state
            assert state['beforeOff'] == state['on'] == 'rgba(0, 0, 0, 0)',state
            assert state['off'] != 'rgba(0, 0, 0, 0)',state
        finally:
            browser.close()


def test_incremental_gold_heading_updates_owned_text_without_wrappers_or_duplicates():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
        try:
            page = _slh(browser)
            observed=page.evaluate('''async()=>{
              const root=document.querySelector('#mcel-workbench');
              const frame=root.querySelector('[data-mcel-region]');
              const heading=document.createElement('h3');
              heading.setAttribute('data-mcel-color-role','text.primary');
              const owned=document.createTextNode('Status');
              heading.appendChild(owned);frame.appendChild(heading);
              const record=[];
              for(const value of ['Running','Stopped','Running','Disconnected']){
                // Incremental runtime edits the original direct Text node.
                owned.nodeValue=value;
                await new Promise(resolve=>setTimeout(resolve,60));
                const css=getComputedStyle(heading);
                record.push({text:heading.textContent,first:heading.firstChild===owned,
                  nodes:heading.childNodes.length,spans:heading.querySelectorAll('[data-mcel-tng-gold-text]').length,
                  gradient:css.backgroundImage.includes('gradient'),shadow:css.textShadow});
              }
              return record;
            }''')
            for value,state in zip(['Running','Stopped','Running','Disconnected'],observed):
                assert state['text']==value and state['first'] and state['nodes']==1 and state['spans']==0,state
                assert state['gradient'] and state['shadow']=='none',state
        finally:
            browser.close()


def test_chrome_does_not_create_positioning_context_for_static_housing():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width':1400,'height':900})
            page.set_content(render_expanded_page(ROOT,promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js','mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts'/name))
            result=page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const opts={root,ir,bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const region=root.querySelector('[data-mcel-region]:not([data-mcel-purpose="workspace"])');
              const pos=()=>getComputedStyle(region).position;
              const original=pos();
              const abs=document.createElement('span');
              abs.style.cssText='position:absolute;left:0;top:0;width:3px;height:3px';
              region.appendChild(abs);
              const before=abs.getBoundingClientRect().toJSON();
              const applied=McelSemanticChromePresentationLaw.apply(opts);
              const after=abs.getBoundingClientRect().toJSON();
              const painted=getComputedStyle(region).backgroundImage;
              const overlays=region.querySelectorAll(':scope > [data-mcel-tng-wear-surface]').length;
              const changed=pos();
              const removed=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
              return {original,changed,delta:[after.x-before.x,after.y-before.y],
                painted:painted.includes('data:image/webp'),overlays,
                cleared:pos(),applied:applied.status,removed:removed.status};
            }''')
            assert result['applied']=='applied' and result['removed']=='unchanged',result
            assert result['original']==result['changed']==result['cleared'],result
            assert result['delta']==[0,0],result
            assert result['painted'],result
            if result['original']=='static':
                assert result['overlays']==0,result
        finally:
            browser.close()


def test_repeated_mount_and_unmount_do_not_leak_constructed_sheets_or_text_wrappers():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        try:
            page=browser.new_page();page.set_content(render_expanded_page(ROOT,promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js','mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts'/name))
            state=page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const opts={root,ir,bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const before=document.adoptedStyleSheets.length;
              const report=[];
              for(let i=0;i<4;i++){
                const on=McelSemanticChromePresentationLaw.apply(opts);
                const mounted=document.adoptedStyleSheets.length;
                const off=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
                report.push({on:on.status,off:off.status,mounted,after:document.adoptedStyleSheets.length,
                  wrappers:root.querySelectorAll('[data-mcel-tng-gold-text]').length});
              }return{before,report};
            }''')
            for state_item in state['report']:
                assert state_item == {'on':'applied','off':'unchanged','mounted':state['before']+1,
                    'after':state['before'],'wrappers':0},state
        finally:
            browser.close()
