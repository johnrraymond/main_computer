"""Production shared-MCEL viewport law, positive and negative browser witnesses."""
from __future__ import annotations

import json
import shutil

from playwright.sync_api import sync_playwright

from tools import mcel_viewport_scroll_frob as frob


def test_normal_content_composition_and_containment_in_rendered_semantic_hosts(tmp_path):
    assert frob.run(output=tmp_path, viewports=((390,650),(1040,768)))==0
    report=json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    assert report['status']=='PASS'
    assert len(report['cases'])==18
    assert all(all(c['evidence']['checks'].values()) for c in report['cases']
               if c['app'] not in ('chat-console',))
    assert all(c['composition']['status']=='PASS' for c in report['cases'])


def test_frob_refuses_document_scroll_and_inaccessible_children():
    bad='''<!doctype html><html><style>
      html,body{margin:0;overflow:auto}
      #mcel-workbench{width:100%;height:300px;overflow:auto}
      main{display:grid;grid-template-columns:1fr 2fr;height:300px}
      nav,section{overflow:hidden;height:300px}
      #tail{height:1500px}
      </style><body><div id="mcel-workbench" data-mcel-semantic-operational="negative-test">
      <main data-mcel-region="root" data-mcel-purpose="application">
      <nav data-mcel-region="nav" data-mcel-purpose="navigation"></nav>
      <section data-mcel-region="work" data-mcel-purpose="workspace"></section>
      </main></div><div id="tail"></div></body></html>'''
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium'))
        try:
            page=browser.new_page(viewport={'width':1040,'height':768})
            page.set_content(bad)
            result=page.evaluate(frob.PROBE)
            assert not result['ok'],result
            assert result['checks']['document_bounded'] is False
            assert result['checks']['navigation_scrollable'] is False
            assert result['checks']['workspace_scrollable'] is False
            page.close()
        finally:
            browser.close()


def test_frob_main_entrypoint_has_mandatory_viewport_scroll_mode():
    from tools import mcel_layout_frob
    source=(mcel_layout_frob.ROOT/'tools/mcel_layout_frob.py').read_text(encoding='utf-8')
    assert "viewport-composition" in source
    assert "viewport-scroll" in source
    assert "if args.mode=='smoke' and not args.no_browser:" in source
    assert "run_viewport_scroll(" in source


def test_generic_mcel_application_shell_scrolls_its_regions_not_its_root():
    style=(frob.ROOT/'main_computer/web/applications/styles/mcel-semantic-application-shell.css').read_text(encoding='utf-8')
    html=('<!doctype html><html><head><style>html,body{height:100%;margin:0;overflow:hidden}'
          +style+'</style></head><body><main class="mcel-application-shell">'
          '<header class="mcel-application-shell__heading"><h1>MCEL workbench</h1></header>'
          '<div class="mcel-application-shell__layout">'
          '<nav class="mcel-application-shell__sidebar"></nav>'
          '<section class="mcel-application-shell__main"></section>'
          '</div></main></body></html>')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium'))
        try:
            page=browser.new_page(viewport={'width':1040,'height':768})
            page.set_content(html)
            result=page.evaluate('''() => {
              const root=document.querySelector('.mcel-application-shell');
              const nav=root.querySelector('.mcel-application-shell__sidebar');
              const main=root.querySelector('.mcel-application-shell__main');
              for(const host of [nav,main]){
                const tall=document.createElement('div');tall.style.height='2500px';host.append(tall);
              }
              nav.scrollTop=220;main.scrollTop=310;window.scrollTo(0,500);root.scrollTop=500;
              return {pageScroll:scrollY,rootScroll:root.scrollTop,
                rootOverflow:getComputedStyle(root).overflowY,
                navOverflow:getComputedStyle(nav).overflowY,
                mainOverflow:getComputedStyle(main).overflowY,
                navScroll:nav.scrollTop,mainScroll:main.scrollTop,
                height:root.getBoundingClientRect().height,viewport:innerHeight};
            }''')
            assert result['pageScroll']==result['rootScroll']==0,result
            assert result['rootOverflow']=='hidden',result
            assert result['navOverflow']==result['mainOverflow']=='auto',result
            assert result['navScroll']>0 and result['mainScroll']>0,result
            assert result['height']<=result['viewport']+1,result
            page.close()
        finally:
            browser.close()
