"""Graph and Activity share Calculator's authored inspection region.

This is a Calculator-local semantic/layout repair. It must not require a
replacement MCEL renderer, negotiation adapter, or new graph state authority.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_graph_and_activity_are_authored_as_inspection_peers():
    ir = compile_expanded()
    regions = {r['id'].rsplit('.', 1)[-1]: r
               for r in ir['surfaces'][0]['interfaceIntent']['regions']}
    parent = regions['output-details']
    graph = regions['graph-output']
    activity = regions['status']
    assert parent['purpose'] == 'inspection'
    assert graph['parentId'] == activity['parentId'] == parent['id']
    assert graph['purpose'] == 'results'
    assert activity['purpose'] == 'status'
    assert graph['visibleWhen']['equals'] == 'graphing'
    assert not activity.get('visibleWhen')
    # Unchanged functional link: the graph reads the state produced by draw-graph.
    plot = next(n for n in ir['surfaces'][0]['nodes'] if n['nodeKind'] == 'plot')
    assert plot['state']['ref'] == 'state:graph-points'


@pytest.mark.parametrize('width,height', [(390,780), (800,780), (1100,780), (1260,780)])
def test_graph_resides_with_activity_and_updates_after_mode_switch(width, height):
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        page = browser.new_page(viewport={'width': width, 'height': height})
        try:
            page.evaluate('''() => {
              const data=new Map();Object.defineProperty(window,'localStorage',{
                configurable:true,value:{getItem:k=>data.get(k)??null,
                setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k),
                clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
                get length(){return data.size;}}});
            }''')
            page.set_content(render_expanded_page())
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            graph = page.locator('[data-mcel-region$=".graph-output"]')
            activity = page.locator('[data-mcel-region$=".status"]')
            inspector = page.locator('[data-mcel-natural-scroller="inspection"]')
            assert graph.is_hidden()
            assert activity.is_visible()
            assert inspector.locator('[data-mcel-activity]').count() == 1

            page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
            assert graph.is_visible()
            assert page.evaluate('window.__mcelExpanded.app.readState("state:mode")') == 'graphing'
            geometry = page.evaluate('''() => {
              const $=q=>document.querySelector(q), rect=e=>e.getBoundingClientRect();
              const graph=$('[data-mcel-region$=".graph-output"]');
              const activity=$('[data-mcel-region$=".status"]');
              const main=$('[data-mcel-natural-scroller="workspace"]');
              const details=$('[data-mcel-natural-scroller="inspection"]');
              const form=$('[data-mcel-region$=".graph-input"]');
              const gr=rect(graph), ar=rect(activity), dr=rect(details), fr=rect(form);
              const fields=[...form.querySelectorAll('form > label')].map(rect);
              return {
                graphAndActivityArePeers: graph.parentElement === activity.parentElement,
                parentIsInspection: graph.parentElement.dataset.mcelPurpose === 'inspection',
                graphInDetails: details.contains(graph) && details.contains(activity),
                controlsInWorkspace: main.contains(form),
                graphBeforeActivity: gr.top < ar.top,
                graphWidth: gr.width, controlsWidth: fr.width,
                graphX:gr.x,controlsX:fr.x,graphTop:gr.top,
                detailsX:dr.x, detailsY:dr.y, formY:fr.y,
                overflow:document.documentElement.scrollWidth-innerWidth,
                fieldsOverlap:fields.some((a,i)=>fields.slice(i+1).some(b=>
                  Math.min(a.right,b.right)-Math.max(a.left,b.left)>2 &&
                  Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2))
              };
            }''')
            assert geometry['graphAndActivityArePeers'] and geometry['parentIsInspection']
            assert geometry['graphInDetails'] and geometry['controlsInWorkspace']
            assert geometry['graphBeforeActivity']
            assert geometry['overflow'] <= 1
            assert not geometry['fieldsOverlap']
            if width >= 1000:
                assert geometry['detailsX'] > geometry['controlsX']
                assert geometry['graphWidth'] > geometry['controlsWidth']
            elif width == 800:
                assert geometry['detailsX'] == pytest.approx(geometry['controlsX'], abs=2)
                assert geometry['detailsY'] > geometry['formY']
            else:
                assert geometry['detailsY'] > geometry['formY']

            form = page.locator('form[data-intent="intent:draw-graph"]')
            form.locator('input[name="expression"]').fill('sin(x)')
            form.locator('button[type="submit"]').click()
            graph.locator('[data-mcel-plot] svg path').wait_for()
            samples = page.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')
            assert len(samples) > 100
            assert page.locator('[data-mcel-activity] li').count() == 1
            # Scrolling details must not take control of the working region.
            scroll = page.evaluate('''() => {
              const details=document.querySelector('[data-mcel-natural-scroller="inspection"]');
              const workspace=document.querySelector('[data-mcel-natural-scroller="workspace"]');
              const unchanged=workspace.scrollTop;
              details.scrollTop=details.scrollHeight;
              const history=document.querySelector('[data-mcel-region$=".status"]').getBoundingClientRect();
              const rail=details.getBoundingClientRect();
              return {independent:workspace.scrollTop===unchanged,
                historyReachable:history.bottom<=rail.bottom+2};
            }''')
            assert scroll['independent'] and scroll['historyReachable']
            # Changing mode must not remount the shared inspection region or
            # duplicate its Activity log. Graph updates after returning.
            page.locator('[data-mcel-mode-tabs] button[data-value="basic"]').click()
            assert graph.is_hidden()
            assert activity.is_visible()
            assert page.locator('[data-mcel-expression-pad] button').count() == 21
            page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
            form.locator('input[name="expression"]').fill('cos(x)')
            form.locator('button[type="submit"]').click()
            page.wait_for_function('''() => {
              const series=window.__mcelExpanded.app.readState('state:graph-points');
              return Array.isArray(series) && series.length>100 && Math.abs(series[0].y+0.839)<0.08;
            }''')
            assert page.locator('[data-mcel-activity] li').count() == 2
        finally:
            page.close()
            browser.close()


def test_graph_keeps_data_and_activity_through_live_resizing():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        page = browser.new_page(viewport={'width': 1260, 'height': 780})
        try:
            page.evaluate('''() => {
              const data=new Map();Object.defineProperty(window,'localStorage',{
                configurable:true,value:{getItem:k=>data.get(k)??null,
                setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k),
                clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
                get length(){return data.size;}}});
            }''')
            page.set_content(render_expanded_page(promoted=True))
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
            form = page.locator('form[data-intent="intent:draw-graph"]')
            form.locator('input[name="expression"]').fill('x*x')
            form.locator('button[type="submit"]').click()
            page.locator('[data-mcel-plot] svg path').wait_for()
            before = page.evaluate('window.__mcelExpanded.app.readState("state:graph-points")')
            for width in (800, 390, 1100, 1260):
                page.set_viewport_size({'width': width, 'height': 780})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('[data-mcel-region$=".graph-output"]:visible [data-mcel-plot] svg path').count() > 0
                assert page.locator('[data-mcel-natural-scroller="inspection"] [data-mcel-activity]').count() == 1
                assert page.evaluate('window.__mcelExpanded.app.readState("state:graph-points")') == before
            assert page.locator('[data-mcel-activity] li').count() == 1
        finally:
            page.close()
            browser.close()
