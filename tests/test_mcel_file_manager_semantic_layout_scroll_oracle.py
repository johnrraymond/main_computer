"""Independent browser oracle: three authored semantic zones, not three nested lists.

No live filesystem access. The existing mock transport is used only to populate
source-bound views; overflow pressure is injected into rendered DOM, not state.
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

from tests.test_mcel_semantic_parity_recovery import page_for
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools import mcel_paired_app_oracles as oracle


def test_source_bound_tree_and_inspector_reject_nested_scroll_at_all_rungs():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            for width in (390, 560, 900, 1100, 1280):
                page = page_for(browser, 'file-manager', width=width)
                try:
                    page.locator('[data-mcel-explorer-navigation] [data-mcel-folder-tree]').wait_for()
                    observed = page.evaluate(oracle.DOM_OBSERVATION)
                    result = oracle.check_layout('file-manager', observed, width)
                    assert result['status'] == 'PASS', (width, result)
                    assert page.locator('[data-mcel-purpose="navigation"] [data-mcel-explorer-navigation]').count() == 1
                    assert page.locator('[data-mcel-zone="workspace"] [data-mcel-explorer-view]').count() == 1
                    assert page.locator('[data-mcel-zone="tools"] [data-mcel-purpose="inspection"]').count() == 1
                    if width >= 980:
                        assert page.locator('[data-mcel-zone="tools"]').get_attribute('data-mcel-placement') == 'dock-end'

                    # Inspecting a real selected record still goes through the
                    # source-bound selection/preview capabilities.
                    page.locator('[data-mcel-directory-open="README.md"]').click()
                    page.get_by_role('button', name='Preview Entry').click()
                    page.locator('[data-state="state:preview"]').get_by_text('const a=1;').wait_for()
                    assert page.locator('[data-mcel-zone="tools"] [data-state="state:preview"]').count() == 1

                    evidence = page.evaluate('''() => {
                      const query=s=>document.querySelector(s),
                        nav=query('[data-mcel-zone="navigator"]'),
                        main=query('[data-mcel-zone="workspace"]'),
                        tools=query('[data-mcel-zone="tools"]'),
                        tree=query('[data-mcel-folder-tree]'),
                        contents=query('[data-mcel-directory-contents]'),
                        collection=query('[data-mcel-explorer-list]'),
                        preview=tools.querySelector('output[data-state="state:preview"]');
                      if(!preview)throw Error('missing-source-bound-preview');
                      for(let i=0;i<90;i++){
                        const folder=document.createElement('div');
                        folder.textContent='A retained navigation entry '+i;
                        tree.append(folder);
                        const row=document.createElement('div');
                        row.textContent='A directory entry '+i;
                        row.style.minHeight='35px';
                        contents.append(row);
                      }
                      preview.textContent=Array(250).fill('A long read-only preview').join('\\n');
                      nav.scrollTop=173;main.scrollTop=239;tools.scrollTop=121;
                      const scroll=el=>getComputedStyle(el).overflowY;
                      return {
                        navigator:nav.scrollTop,workspace:main.scrollTop,inspector:tools.scrollTop,
                        navigatorOverflow:scroll(nav),workspaceOverflow:scroll(main),inspectorOverflow:scroll(tools),
                        treeOverflow:scroll(tree),listOverflow:scroll(contents),
                        collectionOverflow:scroll(collection),previewOverflow:scroll(preview),
                        frameOverflow:scroll(query('[data-mcel-viewport-composed]')),
                        pageOverflow:scroll(document.documentElement),
                        pageScrollHeight:document.documentElement.scrollHeight,
                        viewportHeight:innerHeight,
                        windowScrollY:scrollY,applicationScrollTop:query('#mcel-workbench').scrollTop,
                        mainScrollHeight:main.scrollHeight,mainClientHeight:main.clientHeight,
                        toolsScrollHeight:tools.scrollHeight,toolsClientHeight:tools.clientHeight,
                        navScrollHeight:nav.scrollHeight,navClientHeight:nav.clientHeight,
                      };
                    }''')
                    assert evidence['navigator'] > 0 and evidence['workspace'] > 0 and evidence['inspector'] > 0, (width, evidence)
                    assert all(evidence[key] == 'auto' for key in ('navigatorOverflow', 'workspaceOverflow', 'inspectorOverflow')), evidence
                    assert all(evidence[key] == 'visible' for key in ('treeOverflow', 'listOverflow', 'collectionOverflow', 'previewOverflow')), evidence
                    assert evidence['frameOverflow'] == evidence['pageOverflow'] == 'hidden', evidence
                    assert evidence['pageScrollHeight'] <= evidence['viewportHeight']+1, evidence
                    assert evidence['windowScrollY'] == evidence['applicationScrollTop'] == 0, evidence
                    assert evidence['mainScrollHeight'] > evidence['mainClientHeight'], evidence
                    assert evidence['navScrollHeight'] > evidence['navClientHeight'], evidence
                    assert evidence['toolsScrollHeight'] > evidence['toolsClientHeight'], evidence
                finally:
                    page.close()
        finally:
            browser.close()
