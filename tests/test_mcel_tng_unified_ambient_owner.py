"""TNG ambient CRT belongs to one root, structural carriers inherit, panels do not.

No application identity is consulted by the Chrome Law or the workbench palette.
The examples deliberately cover independent layout technologies and frames.
"""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_worker_application import render_worker_page
from main_computer.mcel_slh_application import render_slh_page

ROOT = Path(__file__).resolve().parents[1]
TRANSPARENT = 'rgba(0, 0, 0, 0)'
CHROME = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js'
WORKBENCH = ROOT / 'main_computer/web/applications/styles/mcel-semantic-workbench.css'


def _browser(playwright):
    return playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium'),
        args=['--no-sandbox','--disable-dev-shm-usage'])


def _html(name):
    return (render_worker_page(ROOT, canonical=True) if name == 'worker'
            else render_slh_page(repo=ROOT) if name == 'slh'
            else render_semantic_page(name, ROOT, promoted=True))


def test_one_ambient_owner_and_preserved_independent_panels_across_apps():
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            for name in ('worker', 'slh', 'code-editor', 'file-manager', 'task-manager'):
                page = browser.new_page(viewport={'width':1272,'height':575})
                page.set_content(_html(name))
                actual=page.evaluate('''() => {
                  const root=document.querySelector('#mcel-workbench[data-mcel-semantic-chrome="chrome-tng-side-rails"]');
                  if (!root) return {error:'no TNG root'};
                  const paint=el=>{const css=el&&getComputedStyle(el);
                    return css && {color:css.backgroundColor,image:css.backgroundImage,bounds:[el.offsetWidth,el.offsetHeight]};};
                  const main=root.querySelector(':scope > main[data-mcel-region]');
                  const panel=[...root.querySelectorAll('[data-mcel-chrome-material="housing"]')]
                    .find(el=>el!==main && !el.matches('[data-mcel-document-workbench]'));
                  const editor=root.querySelector('[data-mcel-document-surface]');
                  return {root:paint(root),main:paint(main),panel:paint(panel),
                    dock:paint(root.querySelector('[data-mcel-document-workbench]')),
                    explorer:paint(root.querySelector('[data-mcel-document-explorer]')),
                    companion:paint(root.querySelector('[data-mcel-document-companion]')),
                    bottom:paint(root.querySelector('[data-mcel-document-bottom]')),
                    document:paint(editor),
                    tokens:{panel:getComputedStyle(root).getPropertyValue('--panel').trim(),
                      theme:getComputedStyle(root).getPropertyValue('--mcel-workbench-panel-surface').trim()}};
                }''')
                assert 'error' not in actual, (name, actual)
                assert actual['root']['color']=='rgb(3, 19, 15)', (name, actual)
                assert actual['root']['image'].count('data:image/webp')>=1,(name,actual)
                assert actual['main']['color']==TRANSPARENT and actual['main']['image']=='none', (name,actual)
                if actual['panel']:
                    assert actual['panel']['color']!=TRANSPARENT, (name,actual['panel'])
                    assert actual['panel']['image']!='none', (name,actual['panel'])
                assert actual['tokens']['theme'], (name,actual)
                if name=='code-editor':
                    assert all(actual[k]['color']==TRANSPARENT and actual[k]['image']=='none'
                               for k in ('dock','explorer','companion','bottom')),(name,actual)
                    assert actual['document']['color']!='rgba(0, 0, 0, 0)',actual
                page.close()
        finally:
            browser.close()


def test_explicit_material_boundary_and_theme_switch_restore_independent_paint():
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page=browser.new_page(viewport={'width':1272,'height':575})
            page.set_content(_html('code-editor'))
            result=page.evaluate('''async () => {
              const root=document.getElementById('mcel-workbench');
              const explorer=root.querySelector('[data-mcel-document-explorer]');
              const dock=root.querySelector('[data-mcel-document-workbench]');
              const nav=root.querySelector('[data-mcel-region][data-mcel-purpose="navigation"]');
              const editor=root.querySelector('[data-mcel-document-surface]');
              const button=root.querySelector('button');
              const beforeNodes=[...button.childNodes];
              const read=()=>({explorer:getComputedStyle(explorer).backgroundColor,
                dock:getComputedStyle(dock).backgroundColor,
                root:getComputedStyle(root).backgroundColor,
                nav:getComputedStyle(nav).backgroundColor,
                editor:getComputedStyle(editor).backgroundColor,
                panelVar:getComputedStyle(root).getPropertyValue('--panel').trim(),
                rects:[explorer,dock,editor].map(el=>[el.getBoundingClientRect().x,
                  el.getBoundingClientRect().y,el.getBoundingClientRect().width,
                  el.getBoundingClientRect().height])});
              const initial=read();
              explorer.setAttribute('data-mcel-material-boundary','independent');
              await new Promise(r=>setTimeout(r,80));
              const independent=read();
              explorer.removeAttribute('data-mcel-material-boundary');
              await new Promise(r=>setTimeout(r,80));
              const restored=read();
              root.setAttribute('data-mcel-semantic-chrome','chrome-strict-hierarchy');
              const off=read();
              root.setAttribute('data-mcel-semantic-chrome','chrome-tng-side-rails');
              const on=read();
              return {initial,independent,restored,off,on,
                buttonStable:beforeNodes.length===button.childNodes.length&&
                  beforeNodes.every((el,i)=>button.childNodes[i]===el)};
            }''')
            assert result['initial']['explorer']==TRANSPARENT,result
            assert result['independent']['explorer']!=TRANSPARENT,result
            assert result['restored']['explorer']==TRANSPARENT,result
            assert result['off']['explorer']!=TRANSPARENT,result
            assert result['on']['explorer']==TRANSPARENT,result
            assert result['initial']['nav']==TRANSPARENT,result
            assert result['initial']['editor']==result['restored']['editor']==result['on']['editor'],result
            assert result['initial']['rects']==result['independent']['rects']==result['restored']['rects']==result['off']['rects']==result['on']['rects'],result
            assert result['buttonStable'],result
        finally:
            browser.close()


def test_no_app_specific_ambient_or_competing_workbench_palette():
    chrome=CHROME.read_text()
    css=WORKBENCH.read_text()
    law=chrome[chrome.index('function isAmbientStructuralCarrier('):
                chrome.index('function resolveMaterialTargets(')]
    assert 'slh.activity' not in law and 'code-editor' not in law and 'worker' not in law
    assert 'data-mcel-material-boundary' in law
    assert 'var(--mcel-workbench-panel-surface)' in chrome
    assert '--mcel-workbench-panel-surface:' not in css
    assert 'var(--mcel-tng-rail-primary,#ffcb22) 10%' not in css
    assert 'data-mcel-semantic-operational="worker"' not in chrome
