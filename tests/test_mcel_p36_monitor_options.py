"""Operator-configurable P3 monitor options remain pure semantic inputs."""
from pathlib import Path
import pytest
from main_computer.mcel_semantic_generated_host import compile_p3_generated_candidate,render_p3_generated_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_compiled_p3_options_have_no_authored_layout():
    ir,composition=compile_p3_generated_candidate()
    surface=ir['surfaces'][0]
    forms=[n for n in surface['nodes'] if n.get('nodeKind')=='form' and n['id'].split('.')[-1] in ('monitor-query','all-query','connection-query','hardware-query')]
    assert len(forms)==4
    assert all(set(n['fields'])=={'query','limit','includeConnections'} for n in forms)
    assert not ir.get('layouts') and not surface.get('htmlBindings')


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b,_=launch_semantic_browser(p)
        try:yield b
        finally:b.close()


def test_p3_monitor_options_reach_port_without_changing_p2(browser):
    page=browser.new_page(viewport={'width':1100,'height':800})
    try:
        page.evaluate('''() => {
          window.__calls=[];
          window.fetch=async(url,opts={})=>{
            const b=opts.body?JSON.parse(opts.body):{};
            window.__calls.push({url,body:b});
            const response=url.endsWith('/overview')?{ok:true,processes:[],connections:[],
              hardware:{summary:'No telemetry',cpu:{},gpu:{}}}:{ok:true,csrf:'test'};
            return {ok:true,status:200,json:async()=>response};
          };
        }''')
        page.set_content(render_p3_generated_page())
        assert 'mounted' in page.locator('#mcel-workbench-status').inner_text()
        form=page.locator('form[data-intent="intent:refresh"]')
        form.locator('[name="limit"]').fill('7')
        form.locator('[name="includeConnections"]').fill('false')
        form.locator('button[type="submit"]').click()
        page.wait_for_function('window.__calls.some(x=>x.url.endsWith("/overview"))')
        body=page.evaluate('window.__calls.find(x=>x.url.endsWith("/overview")).body')
        assert body['limit']==7 and body['include_connections'] is False
        before=page.evaluate('window.__calls.length')
        form.locator('[name="limit"]').fill('0')
        form.locator('button[type="submit"]').click()
        page.wait_for_timeout(120)
        assert page.evaluate('window.__calls.length')==before # no fetch on invalid input
    finally:page.close()
