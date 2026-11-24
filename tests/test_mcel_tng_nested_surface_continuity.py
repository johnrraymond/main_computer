"""Unframed nested CRT content shares its parent material across MCEL apps.

The real Code Editor navigation exercise is deliberately an integration test:
no app identity is consulted by the shared Chrome law itself.
"""
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page

ROOT = Path(__file__).resolve().parents[1]
CHROME = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js'
TRANSPARENT = 'rgba(0, 0, 0, 0)'


def _browser(playwright):
    return playwright.chromium.launch(headless=True,
        executable_path=shutil.which('chromium'),
        args=['--no-sandbox', '--disable-dev-shm-usage'])


def test_shared_chrome_does_not_include_application_specific_background_exceptions():
    law = CHROME.read_text(encoding='utf-8')
    fn = law[law.index('function inheritsEnclosingCRT('):law.index('function mountMaterials(')]
    assert 'code-editor' not in fn and 'slh.activity' not in fn
    assert 'data-mcel-region' in fn and 'data-node-kind="collection"' in fn


def test_real_code_editor_unframed_navigation_and_collection_share_parent_paint():
    with sync_playwright() as playwright:
        browser = _browser(playwright)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.set_content(render_semantic_page('code-editor', ROOT, promoted=True))
            result = page.evaluate(r'''async () => {
              const root = document.getElementById('mcel-workbench');
              const nav = root.querySelector('[data-mcel-region][data-mcel-purpose="navigation"]');
              const col = nav.querySelector('[data-node-kind="collection"]');
              const host = nav.parentElement;
              const btn = nav.querySelector('[data-mcel-parent-directory]');
              const initialText = btn?.textContent;
              const childNodes = btn ? [...btn.childNodes] : [];
              const pause=()=>new Promise(ok=>setTimeout(ok,90));
              const paint = el => {
                const css=getComputedStyle(el);
                return {background:css.backgroundColor,image:css.backgroundImage,
                  shadow:css.boxShadow, border:css.borderTopWidth,
                  material:el.getAttribute('data-mcel-chrome-material')};
              };
              const initial={ambient:paint(root),nav:paint(nav),collection:paint(col),container:paint(host),
                label:col.textContent,rc:[nav.getBoundingClientRect().width,nav.getBoundingClientRect().height]};
              nav.style.border='1px solid gold';await pause();
              const framed=paint(nav);
              nav.style.border='';await pause();
              const unframed=paint(nav);
              col.style.borderRadius='8px';await pause();
              const shaped=paint(col);
              col.style.borderRadius='';await pause();
              col.setAttribute('aria-selected','true');await pause();
              const selected=paint(col);
              col.removeAttribute('aria-selected');await pause();
              const restored=paint(col);
              const stable=btn && btn.textContent===initialText &&
                childNodes.length===btn.childNodes.length &&
                childNodes.every((n,i)=>n===btn.childNodes[i]);
              root.setAttribute('data-mcel-semantic-chrome','chrome-strict-hierarchy');
              const off={nav:paint(nav),collection:paint(col)};
              root.setAttribute('data-mcel-semantic-chrome','chrome-tng-side-rails');
              const on={nav:paint(nav),collection:paint(col)};
              return {initial,framed,unframed,shaped,selected,restored,stable,off,on};
            }''')
            assert result['initial']['nav']['background']==TRANSPARENT,result
            assert result['initial']['collection']['background']==TRANSPARENT,result
            assert result['initial']['nav']['image']=='none' and result['initial']['collection']['image']=='none',result
            assert result['initial']['nav']['shadow']=='none',result
            assert result['initial']['label'].strip().endswith('No records loaded'),result
            assert result['initial']['container']['background']==TRANSPARENT,result
            assert result['initial']['ambient']['background']=='rgb(3, 19, 15)',result
            assert result['initial']['ambient']['image']!='none',result
            assert result['framed']['background']!=TRANSPARENT,result
            assert result['framed']['material']=='housing',result
            assert result['unframed']['background']==TRANSPARENT,result
            assert result['shaped']['background']!=TRANSPARENT,result
            assert result['selected']['background']!=TRANSPARENT,result
            assert result['restored']['background']==TRANSPARENT,result
            assert result['stable'],result
            assert result['off']['nav']['background']!=TRANSPARENT,result
            assert result['off']['collection']['background']!=TRANSPARENT,result
            assert result['on']['nav']['background']==TRANSPARENT,result
            assert result['on']['collection']['background']==TRANSPARENT,result
        finally:
            browser.close()
