"""A semantic collection view fills its workspace without a second scroll rail.

Use the genuine Task Manager renderer and an inert service snapshot. The shared
CSS is responsible for the geometry; no application-specific layout overrides
or browser twiddle are used.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


MOCK_PROCESSES = """() => {
  window.fetch = async (_url, options={}) => {
    const args=JSON.parse(options.body||'{}');
    return {ok:true,status:200,json:async()=>({
      connections:Array.from({length:60},(_,i)=>({pid:100+i,
        process_name:'network-'+i,local:'127.0.0.1:1234',remote:'127.0.0.1:8080',
        status:'ESTABLISHED'})),
      processes:Array.from({length:60},(_,i)=>({
        pid:100+i,name:'Worker '+i,status:'running',memory_human:'5 MB',
        command_preview:'python --worker='+i,created_at:'2026-11-01T01:00:00Z'}))})};
  };
}"""


@pytest.mark.parametrize('width,height', [(680,588),(1040,768),(1272,588)])
@pytest.mark.parametrize('view,form,region', [
    ('server-processes','refresh','processes'),
    ('all-processes','refresh-all','all'),
    ('connections','refresh-connections','connections'),
])
def test_collection_owns_scrolling_and_header_remains_stationary(width,height,view,form,region):
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':width,'height':height})
            page.evaluate(MOCK_PROCESSES)
            page.set_content(render_semantic_page('task-manager',promoted=True))
            page.locator(f'[data-mcel-purpose="navigation"] [data-value="{view}"]').click()
            page.locator(f'form[data-intent="intent:{form}"] button[type="submit"]').click()
            page.locator(f'[data-mcel-region$=".{region}"] [data-record-key]').first.wait_for()
            result=page.evaluate('''(region) => {
              const outer=document.querySelector('[data-mcel-viewport-composed] > [data-mcel-zone="workspace"]');
              const panel=document.querySelector('[data-mcel-region$=".'+region+'"]');
              const inner=panel.querySelector('[data-collection]');
              const heading=panel.querySelector('h2');
              const query=panel.querySelector('form');
              const topHeading=heading.getBoundingClientRect().top;
              const topQuery=query.getBoundingClientRect().top;
              const initialOuterScroll=outer.scrollTop;
              inner.scrollTop=175;
              return {
                outerHeight:outer.clientHeight,outerScrollHeight:outer.scrollHeight,
                innerHeight:inner.clientHeight,innerScrollHeight:inner.scrollHeight,
                innerScrollTop:inner.scrollTop,outerScrollTop:outer.scrollTop,
                initialOuterScroll,headingStill:heading.getBoundingClientRect().top===topHeading,
                queryStill:query.getBoundingClientRect().top===topQuery,
                bottomGap:Math.round(outer.getBoundingClientRect().bottom-inner.getBoundingClientRect().bottom),
                pageOverflow:document.documentElement.scrollWidth>innerWidth+2
              };
            }''',region)
            assert result['outerScrollHeight'] <= result['outerHeight']+2,result
            assert result['innerHeight']>=90,result
            assert result['innerScrollHeight']>result['innerHeight'],result
            assert result['innerScrollTop']>0,result
            assert result['outerScrollTop']==result['initialOuterScroll']==0,result
            assert result['headingStill'] and result['queryStill'],result
            assert abs(result['bottomGap'])<=2,result
            assert not result['pageOverflow'],result
            page.close()
        finally:browser.close()


def test_narrow_workspace_retains_access_to_query_and_navigation():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':390,'height':588})
            page.evaluate(MOCK_PROCESSES)
            page.set_content(render_semantic_page('task-manager',promoted=True))
            page.locator('form[data-intent="intent:refresh"] input[name="query"]').fill('worker')
            page.locator('form[data-intent="intent:refresh"] button[type="submit"]').click()
            page.locator('[data-mcel-region$=".processes"] [data-record-key]').first.wait_for()
            assert page.locator('[data-mcel-purpose="navigation"] [data-value="all-processes"]').count()==1
            assert page.locator('form[data-intent="intent:refresh"] input[name="query"]').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            page.close()
        finally:browser.close()


def test_view_switch_and_resizing_preserve_collection_scroll_ownership():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':1272,'height':588})
            page.evaluate(MOCK_PROCESSES)
            page.set_content(render_semantic_page('task-manager',promoted=True))
            for width,height,view,form,region in [
                (1272,588,'server-processes','refresh','processes'),
                (680,588,'all-processes','refresh-all','all'),
                (1040,768,'connections','refresh-connections','connections'),
                (390,588,'server-processes','refresh','processes'),
                (1272,588,'server-processes','refresh','processes'),
            ]:
                page.set_viewport_size({'width':width,'height':height})
                page.locator(f'[data-mcel-purpose="navigation"] [data-value="{view}"]').click()
                page.locator(f'form[data-intent="intent:{form}"] button[type="submit"]').click()
                page.locator(f'[data-mcel-region$=".{region}"] [data-record-key]').first.wait_for()
                sizes=page.evaluate('''(region) => {
                  const outer=document.querySelector('[data-mcel-viewport-composed] > [data-mcel-zone="workspace"]');
                  const inner=document.querySelector('[data-mcel-region$=".'+region+'"] [data-collection]');
                  return {outer:outer.clientHeight,outerContent:outer.scrollHeight,
                    inner:inner.clientHeight,innerContent:inner.scrollHeight,
                    gap:Math.round(outer.getBoundingClientRect().bottom-inner.getBoundingClientRect().bottom)};
                }''',region)
                if width>=680:
                    assert sizes['outerContent']<=sizes['outer']+2,sizes
                    assert sizes['innerContent']>sizes['inner']>=90,sizes
                    assert abs(sizes['gap'])<=2,sizes
                else:
                    assert page.locator(f'form[data-intent="intent:{form}"] button[type="submit"]').is_visible()
            page.close()
        finally:browser.close()
