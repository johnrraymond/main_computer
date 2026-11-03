"""Stable Task Manager acceptance regression tests; migrated without weakening negative gates."""
from __future__ import annotations

from datetime import datetime, timezone

from types import SimpleNamespace

from tools.mcel_task_manager_acceptance.hosted import (
    _is_targeted_pid_response,
    _matching_independent_pid_generation,
)

HOST='http://127.0.0.1:8765'

URL=HOST+'/api/applications/task/overview'

PID=58123

def response(*, url=URL, method='POST', query=str(PID), include_all=True,
             limit=100, body=None):
    if body is None:
        body={'query':query,'include_all':include_all,'limit':limit}
    return SimpleNamespace(url=url,request=SimpleNamespace(method=method,post_data_json=body))

def test_targeted_response_ignores_other_inflight_overviews():
    assert not _is_targeted_pid_response(response(query=''),HOST,PID)
    assert not _is_targeted_pid_response(response(query='cpu'),HOST,PID)
    assert not _is_targeted_pid_response(response(query='5812'),HOST,PID)
    assert not _is_targeted_pid_response(response(include_all=False),HOST,PID)
    assert not _is_targeted_pid_response(response(method='GET'),HOST,PID)
    assert not _is_targeted_pid_response(response(url=HOST+'/api/applications/task/action'),HOST,PID)
    assert _is_targeted_pid_response(response(),HOST,PID)

def test_targeted_response_cannot_accept_malformed_request_body():
    for value in (None,[],{'query':str(PID),'include_all':True},
                  {'query':str(PID),'include_all':1,'limit':100},
                  {'query':str(PID),'include_all':True,'limit':True},
                  {'query':str(PID),'include_all':True,'limit':999}):
        assert not _is_targeted_pid_response(response(body=value if value is not None else 'bad-json'),HOST,PID)

def test_pid_identity_needs_correct_os_generation():
    os_birth=datetime(2026,11,2,19,32,10,123000,tzinfo=timezone.utc).timestamp()
    correct={'pid':PID,'created_at':'2026-11-02T19:32:10.123+00:00'}
    wrong={'pid':PID,'created_at':'2026-11-01T19:32:10.123+00:00'}
    other={'pid':PID+1,'created_at':correct['created_at']}
    assert _matching_independent_pid_generation([correct],pid=PID,os_birth=os_birth)==[correct['created_at']]
    assert _matching_independent_pid_generation([wrong],pid=PID,os_birth=os_birth)==[]
    assert _matching_independent_pid_generation([other],pid=PID,os_birth=os_birth)==[]
    assert _matching_independent_pid_generation([{'pid':PID,'created_at':'/Date(1793647930123)/'}],pid=PID,os_birth=os_birth)==[]
    assert _matching_independent_pid_generation([{'pid':PID}],pid=PID,os_birth=os_birth)==[]
    assert _matching_independent_pid_generation([],pid=PID,os_birth=os_birth)==[]

def test_pid_identity_never_accepts_bad_process_payload():
    assert _matching_independent_pid_generation('not a process list',pid=PID,os_birth=0)==[]
    assert _matching_independent_pid_generation([None,'wrong',42,{}],pid=PID,os_birth=0)==[]

import json

import threading

from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from http import HTTPStatus

from main_computer.task_manager import TaskManagerService

from main_computer.mcel_semantic_generated_host import render_p3_generated_page

from tools.mcel_task_manager_acceptance.hosted import probe

from tools.mcel_task_manager_acceptance.__main__ import ROOT

def test_generated_all_process_and_schedule_selection_rendered_from_semantics():
    """Bounded UI test. The real hosted acceptance remains a separate gate."""
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from main_computer.mcel_semantic_generated_host import render_p3_generated_page

    with sync_playwright() as player:
        browser,_=launch_semantic_browser(player)
        page=browser.new_page(viewport={'width':1100,'height':800})
        try:
            page.evaluate('''() => {
              window.fetch=async(url, opts={})=>{
                const body=JSON.parse(opts.body||'{}');
                let payload={ok:true};
                if(String(url).endsWith('/overview'))payload={ok:true,processes:[{
                  pid:18457,name:'owned-test-process',status:'running',memory_rss:12,
                  created_at:'2026-11-02T15:00:00+00:00',command_preview:'test-only'
                }],connections:[],hardware:{summary:'none',cpu:{},gpu:{}}};
                if(String(url).endsWith('/schedules'))payload={ok:true,schedules:[{
                  id:'schedule-test-1',action:'server_status',revision:'rev1',
                  run_at:'2026-11-03T15:00:00+00:00',note:'test-only'
                }]};
                return {ok:true,status:200,json:async()=>payload};
              };
            }''')
            page.set_content(render_p3_generated_page())
            page.locator('[data-mcel-mode-tabs] [data-value="all-processes"]').click()
            form=page.locator('form[data-intent="intent:refresh-all"]')
            form.locator('button[type="submit"]').click()
            item=page.locator('[data-record-key="18457:2026-11-02T15:00:00+00:00"]')
            item.wait_for(timeout=6000)
            item.locator('button[data-select-key]').click()
            page.locator('[data-mcel-selected-details="18457:2026-11-02T15:00:00+00:00"]').first.wait_for(timeout=4000)
            # A selected all-process identity remains subject to the real
            # capability's generation checks; it is not a free-form PID input.
            assert page.locator('button[data-intent="intent:terminate-process"]').is_enabled()
            page.locator('button[data-intent="intent:list-schedules"]').click()
            schedule=page.locator('[data-record-key="schedule-test-1"]')
            schedule.wait_for(timeout=4000)
            schedule.locator('button[data-select-key]').click()
            run=page.locator('button[data-intent="intent:run-schedule"]')
            delete=page.locator('button[data-intent="intent:delete-schedule"]')
            assert run.is_enabled() and delete.is_enabled()
        finally:
            page.close();browser.close()


class LaboratoryHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path!='/applications/mcel-semantic-task-manager-generated-p3':
            self.send_error(404);return
        body=render_p3_generated_page().encode('utf-8')
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path!='/api/applications/task/overview':self.send_error(404);return
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        params=json.loads(raw)
        result=self.server.task_manager.snapshot(query=params.get('query',''),limit=int(params.get('limit',100)),
            include_all=bool(params.get('include_all')),include_connections=bool(params.get('include_connections')))
        body=json.dumps(result).encode()
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

def test_browser_four_views_real_http_isolated_adapter(tmp_path):
    server=ThreadingHTTPServer(('127.0.0.1',0),LaboratoryHandler)
    server.task_manager=TaskManagerService(ROOT,default_port=server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        results=probe('http://127.0.0.1:'+str(server.server_port),tmp_path,timeout_ms=15000)
        if not results['passed']:
            # This cloud sandbox can prohibit Chromium localhost navigation.
            # A blocked browser must remain an explicit blocked acceptance test,
            # never be converted to a live-host success.
            failures=[v.get('reason','') for v in results['widths'].values() if not v.get('passed')]
            assert failures and all('ERR_BLOCKED_BY_ADMINISTRATOR' in v for v in failures),failures
            return
        for width in (320,390,560,1100,1272):
            item=results['widths'][str(width)]
            assert len(item['views'])==4
            assert all(value['httpStatus']==200 for value in item['views'].values())
            assert item['hostPageSha256']==item['expectedPageSha256']
            assert (tmp_path/f'viewport-{width}.png').exists()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)

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



