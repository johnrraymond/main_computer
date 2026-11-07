"""Guard regression and hermetic browser proof; NEVER counts as live Windows evidence."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from tools.mcel_four_app_live_readonly_frob import allowed_post, request_decision, run
from tools.mcel_v1_four_app_hosted_acceptance import candidate_html

BASE = 'http://127.0.0.1:8765'


def test_readonly_contract_allowlist_and_consequential_refusals():
    assert allowed_post('task-manager', '/api/applications/task/overview',
                        {'query':'', 'limit':100, 'include_all':False, 'include_connections':False})
    assert allowed_post('file-manager', '/api/applications/file-explorer/roots', {})
    assert allowed_post('code-editor', '/api/applications/editor/files',
                        {'repo_dir':'.','path':'','limit':500})
    assert not allowed_post('code-editor', '/api/applications/editor/project/file/save', {})
    assert not allowed_post('task-manager', '/api/applications/task/action', {'action':'server_shutdown','confirm':True})
    assert not allowed_post('task-manager', '/api/applications/task/ai', {'instruction':'test'})
    assert not allowed_post('task-manager', '/api/applications/task/schedule/create', {})
    assert not allowed_post('code-editor', '/api/applications/aider/run', {})
    assert not allowed_post('file-manager', '/api/applications/file-explorer/read',
                            {'root_id':'workspace','relative_path':'../private'})
    assert not allowed_post('code-editor', '/api/applications/editor/read',
                            {'repo_dir':'.','files':['../private.py']})
    assert not allowed_post('task-manager', '/api/applications/task/overview',
                            {'query':'', 'limit':True, 'include_all':False, 'include_connections':False})


def test_all_network_decisions_fail_closed():
    assert request_decision('file-manager', BASE, BASE+'/api/applications/file-explorer/roots',
                            'POST', '{}') == 'allow-api-read'
    assert request_decision('task-manager', BASE, BASE+'/api/applications/task/action',
                            'POST', '{"action":"server_shutdown","confirm":true}') == 'deny-api-post'
    assert request_decision('code-editor', BASE, BASE+'/api/applications/editor/project/file/save',
                            'POST', '{}') == 'deny-api-post'
    assert request_decision('file-manager', BASE, BASE+'/api/applications/file-explorer/roots',
                            'POST', '{bad}') == 'deny-body'
    assert request_decision('file-manager', BASE, BASE+'/api/applications/file-explorer/roots',
                            'POST', '['+'0,'*5000+'0]') == 'deny-body'
    assert request_decision('file-manager', BASE, BASE+'/api/applications/file-explorer/roots',
                            'GET', None) == 'deny-api-get'
    assert request_decision('file-manager', BASE, 'http://127.0.0.1:9876/applications/file-explorer',
                            'GET') == 'deny-off-origin'
    assert request_decision('file-manager', BASE, 'https://evil.example/applications/file-explorer',
                            'GET') == 'deny-off-origin'
    assert request_decision('task-manager', BASE, BASE+'/anything', 'DELETE') == 'deny-method'
    assert request_decision('task-manager', BASE, BASE+'/hidden-operator-endpoint', 'GET') == 'deny-nonasset-get'
    assert request_decision('calculator', BASE, BASE+'/api/applications/mathics/eval',
                            'POST', '{}') == 'deny-api-post'
    assert request_decision('code-editor', BASE,
                            'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js',
                            'GET') == 'allow-pinned-monaco'


class FixtureHandler(BaseHTTPRequestHandler):
    pages = {}
    def log_message(self, *args):
        pass
    def do_GET(self):
        app = next((name for name, path in self.pages.items() if path[0] == self.path), None)
        if app:
            content = self.pages[app][1].encode('utf-8')
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(content))); self.end_headers(); self.wfile.write(content)
        else:
            self.send_error(404)
    def do_POST(self):
        size = int(self.headers.get('Content-Length','0'))
        body = json.loads(self.rfile.read(size) or b'{}')
        if self.path == '/api/applications/file-explorer/roots':
            data = {'ok':True, 'roots':[{'id':'workspace','label':'Workspace'}]}
        elif self.path == '/api/applications/file-explorer/list':
            data = {'ok':True,'root_id':body['root_id'],'relative_path':body['relative_path'],
                    'entries':[{'relative_path':'README.md','name':'README.md','kind':'file',
                                'bytes':12,'category':'document','suggested_app':'document'}]}
        elif self.path == '/api/applications/task/overview':
            data = {'ok':True,'processes':[{'pid':43121,'name':'disposable','created_at':'2026-11-06T11:00:00Z'}],
                    'connections':[{'pid':43121,'process_name':'disposable','local':'127.0.0.1:1',
                                    'remote':'127.0.0.1:2','status':'LISTEN'}],
                    'hardware':{'summary':'CPU 12%, GPU 3%', 'cpu':{'available':True,'overall_percent':12},
                                'gpu':{'available':True,'overall_percent':3}},'server':{'status':'running'}}
        elif self.path == '/api/applications/editor/files':
            data = {'ok':True,'path':'', 'entries':[{'path':'src','name':'src','kind':'dir'}]}
        else:
            data = {'ok':False,'error':'fixture-endpoint-not-allowed'}
        raw = json.dumps(data).encode('utf-8')
        self.send_response(200 if data['ok'] else 404)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers(); self.wfile.write(raw)


def test_browser_runner_uses_actual_http_without_unexpected_mutations(tmp_path):
    # Runs against an in-process fixture to prove *harness behavior*, not host readiness.
    from tools.mcel_semantic_reference_parity_frob import APPLICATIONS
    pages = {}
    for app in ('calculator','file-manager','task-manager','code-editor'):
        pages[app] = (APPLICATIONS[app]['semantic_route'], candidate_html(app))
    FixtureHandler.pages = pages
    server = ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = run(f'http://127.0.0.1:{server.server_port}',
                     tmp_path/'report.json', ('calculator','file-manager','task-manager','code-editor'))
    finally:
        server.shutdown(); server.server_close();worker.join(timeout=4)
    assert (tmp_path/'report.json').is_file()
    # This execution environment can deny Chromium loopback network access at
    # the browser's administrator policy layer. That is an INCOMPLETE diagnostic,
    # never a PASS or a concealed simulated-host success.
    if all('ERR_BLOCKED_BY_ADMINISTRATOR' in x.get('error','') for x in result['apps'].values()):
        assert result['overall']=='INCOMPLETE'
        return
    assert not any(x['blocked'] for x in result['apps'].values())
    assert not any(x['page_errors'] for x in result['apps'].values())
    for app, observation in result['apps'].items():
        assert observation['mount']['source_fingerprint_matches'], (app,observation)
        assert observation['mount']['ready'], (app,observation)
        assert observation['route_http'] == 200
    assert all(not any(x['path'].endswith(('/action','/save','/run','/create','/delete'))
                       for x in observation['requests']) for observation in result['apps'].values())


def test_ui_readonly_scenarios_with_offline_browser_fixtures():
    """Proves UI selectors and readonly action sequence; external IO is fake."""
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools.mcel_four_app_live_readonly_frob import _exercise, _summary
    from tools.mcel_v1_four_app_hosted_acceptance import candidate_ir, json_fingerprint
    fake_fetch = r'''() => {
      window.__safeCalls=[];
      window.fetch=async (url,opts={}) => {
        const path=String(url),body=JSON.parse(opts.body||'{}');
        window.__safeCalls.push({path,body});
        let data={ok:true};
        if(path.endsWith('/roots'))data.roots=[{id:'workspace',label:'Workspace'}];
        if(path.endsWith('/list'))data={ok:true,root_id:body.root_id,relative_path:body.relative_path,
          entries:[{relative_path:'README.md',name:'README.md',kind:'file',suggested_app:'document'}]};
        if(path.endsWith('/overview')){await new Promise(resolve=>setTimeout(resolve,750));}
        if(path.endsWith('/overview'))data={ok:true,
          processes:[{pid:124,name:'disposable',created_at:'2026-11-06T12:00:00Z'}],
          connections:[{pid:124,process_name:'disposable',local:'127.0.0.1:1',remote:'127.0.0.1:2',status:'LISTEN'}],
          hardware:{summary:'CPU 12%, GPU 3%',cpu:{available:true,overall_percent:12},
                    gpu:{available:true,overall_percent:3}}};
        if(path.endsWith('/files'))data={ok:true,path:body.path,entries:[{path:'src',name:'src',kind:'dir'}]};
        return {ok:true,status:200,json:async()=>data};
      };
    }'''
    with sync_playwright() as playwright:
        browser, _ = launch_semantic_browser(playwright)
        try:
            for app in ('file-manager','task-manager','code-editor'):
                page = browser.new_page(viewport={'width':1272,'height':900})
                try:
                    page.goto('about:blank')
                    page.evaluate(fake_fetch)
                    page.set_content(candidate_html(app),wait_until='domcontentloaded')
                    page.wait_for_timeout(350)
                    state = _summary(page,app,json_fingerprint(candidate_ir(app)))
                    assert all(state[k] for k in ('ready','source_fingerprint_matches','mounted','no_horizontal_overflow')), (app,state)
                    checks = _exercise(page,app)
                    assert all(v in ('OBSERVED','VISIBLE','PASS') for k,v in checks.items()
                               if k in ('startup_root_and_listing','cpu_gpu_view','connections_view','file_inventory')), (app,checks)
                    calls = page.evaluate('window.__safeCalls')
                    assert calls,app
                    assert all(request_decision(app,BASE,BASE + x['path'],'POST',json.dumps(x['body']))=='allow-api-read'
                               for x in calls), (app,calls)
                    if app == 'task-manager':
                        # Delayed real-service responses must not make the live
                        # runner conclude that completed reads never occurred.
                        assert len([x for x in calls if x['path'].endswith('/overview')]) >= 3
                        assert checks['monitor_receipts']['hardware']['disposition']=='completed'
                        assert checks['monitor_receipts']['connections']['disposition']=='completed'
                finally:
                    page.close()
        finally:
            browser.close()


def test_calculator_local_core_readonly_with_opaque_origin_storage_stub():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools.mcel_four_app_live_readonly_frob import _exercise, _summary
    from tools.mcel_v1_four_app_hosted_acceptance import candidate_ir, json_fingerprint
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = browser.new_page()
            page.goto('about:blank')
            page.evaluate('''() => {
              const items=new Map();
              Object.defineProperty(window,'localStorage',{configurable:true,value:{
                getItem:k=>items.get(k)||null, setItem:(k,v)=>items.set(k,String(v)),
                removeItem:k=>items.delete(k), clear:()=>items.clear(),
                key:i=>[...items.keys()][i]||null, get length(){return items.size}
              }});
            }''')
            page.set_content(candidate_html('calculator'))
            state = _summary(page,'calculator',json_fingerprint(candidate_ir('calculator')))
            assert all(state[k] for k in ('ready','source_fingerprint_matches','mounted','no_horizontal_overflow'))
            assert _exercise(page,'calculator')['local_arithmetic']=='PASS'
        finally:
            browser.close()


def test_failed_monitor_receipt_is_not_converted_into_success():
    from tools.mcel_four_app_live_readonly_frob import _settled_receipt
    class FailedPage:
        def wait_for_function(self, *args, **kwargs):
            return None
        def evaluate(self, *args, **kwargs):
            return [{'intentId':'intent:refresh-hardware','disposition':'failed',
                     'reason':'hardware-missing:private-host-detail-must-not-be-copied'}]
    result = _settled_receipt(FailedPage(), {'intent':'intent:refresh-hardware','before':0})
    assert result == {'disposition':'failed','reason_class':'hardware-missing'}
