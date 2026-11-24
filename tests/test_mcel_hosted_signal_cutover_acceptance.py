"""Fail-closed hosted signal harness and real HTTP HTML delivery (no production writes)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

import pytest
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_applications_shell_host import render_applications_shell_candidate
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools import mcel_hosted_signal_cutover_acceptance as acceptance

ROOT=Path(__file__).resolve().parents[1]


def test_browser_instrumentation_preserves_incumbent_reference_and_candidate():
    for app in acceptance.APPS:
        source=acceptance.expected_html(app,ROOT)
        candidate=acceptance.instrument_candidate(app,source)
        reference=acceptance.instrument_reference(app,source)
        assert 'signalSchedulerIntegration:true' in candidate
        assert 'signalSchedulerStartEnabled:true' in candidate
        if app=='code-editor':
            assert 'signalSchedulerIntegration:true' in source
            assert 'signalSchedulerStartEnabled:true' in source
            assert 'signalSchedulerIntegration:false' in reference
            assert 'signalSchedulerStartEnabled:false' in reference
            assert 'window.__MCEL_CODE_EDITOR_SIGNAL_ACCEPTANCE__' not in candidate
        else:
            assert candidate!=source
            assert reference==source
    assert 'window.__MCEL_CALCULATOR_SIGNAL_ACCEPTANCE__=true' in acceptance.instrument_candidate(
        'calculator',acceptance.expected_html('calculator',ROOT))
    with pytest.raises(ValueError,match='HOST_DEFAULT_NOT_INCUMBENT'):
        acceptance.instrument_candidate('file-manager','<head></head>')
    with pytest.raises(ValueError,match='EDITOR_PRODUCTION_SIGNAL_MOUNT_DRIFT'):
        acceptance.instrument_candidate('code-editor','<head>signalSchedulerIntegration:false signalSchedulerStartEnabled:false</head>')
    with pytest.raises(ValueError,match='EDITOR_RUNTIME_GATE_DRIFT'):
        acceptance.instrument_candidate('code-editor','<head>signalSchedulerIntegration:true signalSchedulerStartEnabled:true</head>')


def test_network_guard_denies_writes_remote_and_arbitrary_mathics():
    base='http://127.0.0.1:8765'
    for app in acceptance.APPS:
        assert acceptance._safe_request(app,base,base+'/api/applications/editor/project/file/save','POST','{}').startswith('deny-')
        assert acceptance._safe_request(app,base,'http://example.com/secret','GET',None).startswith('deny-')
        assert acceptance._safe_request(app,base,base+'/api/applications/calculator/mathics/prepare','POST','{}').startswith('deny-')
        assert acceptance._safe_request(app,base,base+'/api/chat','POST','{}').startswith('deny-')
        assert acceptance._safe_request(app,base,base+'/api/applications/aider/run','POST','{}').startswith('deny-')
    assert acceptance._safe_request('file-manager',base,base+'/api/applications/file-explorer/roots','POST','{}')=='allow-api-read'
    assert acceptance._safe_request('code-editor',base,base+'/api/applications/editor/files','POST',
             json.dumps({'repo_dir':'.','path':'','limit':500}))=='allow-api-read'


def test_input_rejects_non_loopback():
    for value in ('https://127.0.0.1:8765','http://localhost:8765','http://8.8.8.8:8765',
                  'http://127.0.0.1:8765/x','http://127.0.0.1:8765/?a=b'):
        with pytest.raises(ValueError):acceptance.validate_loopback_base(value)


def test_real_http_calculator_mount_rollback_without_simulated_html(tmp_path):
    # The actual production page bytes are served over real loopback HTTP.
    # No external IO or backend response is stubbed for Calculator arithmetic.
    original=render_expanded_page(ROOT,promoted=True).encode()
    shell=render_applications_shell_candidate(ROOT,canonical_promoted=True).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/applications/calculator':
                data=shell
            elif self.path==acceptance.native_child_route('calculator'):
                data=original
            elif self.path in acceptance.DEPENDENCIES:
                data=b'<!doctype html><title>Dependency available</title>'
            else:
                self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        result=acceptance.run(f'http://127.0.0.1:{server.server_port}',ROOT,('calculator',),
                             report=tmp_path/'report.json')
        if any('ERR_BLOCKED_BY_ADMINISTRATOR' in err for err in result['apps']['calculator']['failures']):
            pytest.skip('Sandbox browser blocks loopback navigation; run against live Windows host.')
        assert result['apps']['calculator']['status']=='PASS_HOSTED_READONLY_WITH_SIGNALS',result['apps']['calculator']['failures']
        assert result['apps']['calculator']['actualSkips']>0
        assert result['readOnlyGate']=='PASS_HOSTED_READONLY'
        assert result['productionCutoverAuthorized'] is False
        assert result['status']=='BLOCKED' # no certified report files in exported snapshot
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_prior_isolated_gate_requires_matching_app_and_runtime_hash(tmp_path):
    repo=tmp_path
    source=repo/acceptance.APPLICATION_SOURCES['calculator']
    runtime=repo/acceptance.RUNTIME_SOURCE
    source.parent.mkdir(parents=True,exist_ok=True)
    runtime.parent.mkdir(parents=True,exist_ok=True)
    source.write_text('app source',encoding='utf8')
    runtime.write_text('runtime source',encoding='utf8')
    report_path=repo/acceptance.ISOLATED_REPORTS['calculator']
    report_path.parent.mkdir(parents=True,exist_ok=True)
    assert not acceptance.prior_isolated_certifications(repo,('calculator',))['calculator']['verified']
    from hashlib import sha256
    fingerprint={'applicationSha256':sha256(source.read_bytes()).hexdigest(),
                 'runtimeSha256':sha256(runtime.read_bytes()).hexdigest()}
    report_path.write_text(json.dumps({'status':'PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS',
                                       'fingerprints':fingerprint}),encoding='utf8')
    assert acceptance.prior_isolated_certifications(repo,('calculator',))['calculator']['verified']
    source.write_text('modified application',encoding='utf8')
    assert acceptance.prior_isolated_certifications(repo,('calculator',))['calculator']['status']=='REPORT_OR_SOURCE_MISMATCH'


def test_editor_read_requires_explicit_history_consent():
    base='http://127.0.0.1:8765'
    path=base+'/api/applications/editor/read'
    body=json.dumps({'repo_dir':'.','files':['README.md']})
    assert acceptance._safe_request('code-editor',base,path,'POST',body).startswith('deny-editor-read')
    assert acceptance._safe_request('code-editor',base,path,'POST',body,allow_editor_read_history=True)=='allow-api-read'


def test_runtime_signal_mounts_remain_test_only_and_calculator_executes():
    # Browser-only module verification; not a live-host pass.
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    with sync_playwright() as playwright:
        browser,_=launch_semantic_browser(playwright)
        try:
            for app in acceptance.APPS:
                page=browser.new_page(viewport={'width':1272,'height':900})
                page.route('**/*',lambda route:route.abort())
                if app=='calculator':
                    page.evaluate("""() => {const state=new Map();Object.defineProperty(window,'localStorage',
                      {configurable:true,value:{getItem:key=>state.get(key)??null,
                        setItem:(key,val)=>state.set(key,String(val)),removeItem:key=>state.delete(key),
                        clear:()=>state.clear()}})}""")
                page.set_content(acceptance.instrument_candidate(app,acceptance.expected_html(app)))
                handle=acceptance._app_handle(app)
                page.wait_for_function('handle=>!!window[handle]?.app',arg=handle,timeout=12000)
                report=page.evaluate('handle=>window[handle].app.signalCandidateReport()',handle)
                assert report['mode']=='integrated-opt-in' and report['integrationEnabled'] is True
                if app=='calculator':
                    form=page.locator('form[data-intent="intent:evaluate"]')
                    form.locator('input[name="expression"]').fill('7*(3+2)')
                    form.locator('button[type="submit"]').click()
                    page.wait_for_function('()=>window.__mcelExpanded.app.readState("state:arithmetic-result")==="35"')
                    assert page.evaluate('window.__mcelExpanded.app.signalCandidateReport().statistics.skips')>0
                page.close()
        finally:browser.close()


def test_disposable_write_guard_binds_exact_file_body_and_origin():
    from hashlib import sha256
    from tools import mcel_hosted_cutover_disposable as d
    base='http://127.0.0.1:8765'
    target='runtime/reports/mcel-hosted-signal-abcdefgh/candidate.js'
    route=base+'/api/applications/editor/project/file/save'
    request={'repo_dir':'.','project_root':'.','path':target,
        'replacement_text':d.REPLACEMENT,
        'expected_before_sha256':sha256(d.ORIGINAL.encode()).hexdigest(),
        'explicit_save':True,'stale_source_checked':True,
        'write_policy':'author-owned-source','validation_profile':'none'}
    assert acceptance._safe_request('code-editor',base,route,'POST',json.dumps(request),
                   disposable_write_path=target)=='allow-disposable-source-write'
    for change in ('path','replacement_text','expected_before_sha256'):
        forged={**request,change:'not-authorized'}
        assert acceptance._safe_request('code-editor',base,route,'POST',json.dumps(forged),
                   disposable_write_path=target).startswith('deny-')
    assert acceptance._safe_request('code-editor',base,'http://127.0.0.2:8765/api/applications/editor/project/file/save',
              'POST',json.dumps(request),disposable_write_path=target).startswith('deny-')
    assert acceptance._safe_request('code-editor',base,route,'POST',json.dumps(request)).startswith('deny-')
    with pytest.raises(ValueError,match='DISPOSABLE_WRITES_REQUIRE'):
        acceptance.run(base,ROOT,('file-manager',),authorize_disposable_writes=True)


def test_temporary_disposable_fixture_never_overwrites_existing_file(tmp_path):
    from tools import mcel_hosted_cutover_disposable as d
    root=tmp_path
    directory=root/'runtime/reports/mcel-hosted-signal-abcdefgh'
    directory.mkdir(parents=True)
    fixture=d.create_fixture(root,directory)
    assert set(fixture['paths'])=={'incumbent','signals'}
    assert all(path.read_text(encoding='utf8')==d.ORIGINAL for path in fixture['local'].values())
    assert all(path.read_bytes()==d.ORIGINAL.encode('utf8') for path in fixture['local'].values())
    assert all(path.stat().st_size==len(d.ORIGINAL.encode('utf8')) for path in fixture['local'].values())
    with pytest.raises(ValueError,match='ALREADY_EXISTS'):
        d.create_fixture(root,directory)
    external=root/'unapproved'
    external.mkdir()
    with pytest.raises(ValueError,match='UNSAFE'):
        d.create_fixture(root,external)



def test_disposable_fixture_is_newline_independent_and_revalidates_at_checkpoints(tmp_path, monkeypatch, capsys):
    """Cross-platform exact-byte fixture is immune to CRLF conversion."""
    import hashlib
    from tools import mcel_hosted_cutover_disposable as d
    repo=tmp_path
    directory=repo/'runtime/reports/mcel-hosted-signal-windows-crlf'
    directory.mkdir(parents=True)
    raw=d.ORIGINAL.encode('utf8')
    assert b'\r' not in raw and b'\n' not in raw
    assert len(raw)==38
    assert d.REPLACEMENT.endswith(';') and '\n' not in d.REPLACEMENT
    original_write_text=Path.write_text
    def fail_js_text_write(path,data,*args,**kwargs):
        if path.suffix=='.js':
            raise AssertionError('fixture should use exclusive binary creation')
        return original_write_text(path,data,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',fail_js_text_write)
    fixture=d.create_fixture(repo,directory)
    assert all(p.read_bytes()==raw for p in fixture['local'].values())
    assert 'NO_NEWLINE_EXACT_BYTES_V2' in capsys.readouterr().out
    d._assert_original_fixture_bytes(fixture,'regression')
    assert all(d._disposable_file_evidence(p)['original'] is True
               for p in fixture['local'].values())
    target=fixture['paths']['incumbent']
    request={'repo_dir':'.','project_root':'.','path':target,
        'replacement_text':d.REPLACEMENT,
        'expected_before_sha256':hashlib.sha256(raw).hexdigest(),
        'explicit_save':True,'stale_source_checked':True,
        'write_policy':'author-owned-source','validation_profile':'none'}
    base='http://127.0.0.1:8765'
    route=base+'/api/applications/editor/project/file/save'
    assert d.write_decision(base,route,'POST',json.dumps(request),target)=='allow-disposable-source-write'
    request['expected_before_sha256']='0'*64
    assert d.write_decision(base,route,'POST',json.dumps(request),target)=='deny-disposable-write-payload'
    fixture['local']['incumbent'].write_bytes(raw+b'\r\n')
    with pytest.raises(RuntimeError,match='DISPOSABLE_FIXTURE_CHANGED:') as err:
        d._assert_original_fixture_bytes(fixture,'after-open-incumbent')
    assert 'after-open-incumbent' in str(err.value)
    assert 'observedSha256' in str(err.value)


def test_live_document_preflight_distinguishes_source_drift_auth_and_redirect():
    app='calculator'
    original=acceptance.expected_html(app,ROOT)
    clean=acceptance._host_document_diagnostic(app,original,status=200,body=original.encode('utf8'),
                                                 content_type='text/html; charset=utf-8')
    assert clean['status']=='PASS_EXACT' and clean['reason']=='CANONICAL_BYTES_MATCH'
    assert clean['servedAppId']=='semantic-calculator-expanded'
    assert clean['expectedSha256']==clean['observedSha256']
    modified=original.replace('<title>Main Computer Calculator</title>',
                             '<title>Main Computer Calculator old</title>')
    assert modified!=original
    drift=acceptance._host_document_diagnostic(app,original,status=200,body=modified.encode())
    assert drift['status']=='BLOCKED' and drift['reason']=='HOSTED_APP_SOURCE_DRIFT'
    assert drift['observedSha256']!=drift['expectedSha256']
    assert drift['servedAppId']=='semantic-calculator-expanded'
    login=acceptance._host_document_diagnostic(app,original,status=200,
                         body=b'<html><title>Sign in</title></html>')
    assert login['reason']=='NONCANONICAL_RESPONSE_OR_AUTH_PAGE'
    assert login['servedAppId'] is None
    redirect=acceptance._host_document_diagnostic(app,original,status=302,body=b'')
    assert redirect['reason']=='HOST_DOCUMENT_NON_200_OR_REDIRECT'
    assert redirect['status']=='BLOCKED'
    shell=render_applications_shell_candidate(ROOT,canonical_promoted=True).encode()
    wrong_layer=acceptance._host_document_diagnostic(app,original,status=200,body=shell,parent_shell=shell)
    assert wrong_layer['reason']=='PARENT_SHELL_RETURNED_FOR_NATIVE_CHILD'
    assert wrong_layer['observedBytes']==len(shell)


def test_native_child_route_reproduces_actual_router_contract():
    import ast
    from hashlib import sha256
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit
    # Compile the actual handler function without importing the entire viewport
    # (which bootstraps unrelated game-project runtime assets in this snapshot).
    route_module=ast.parse((ROOT/'main_computer/viewport_route_dispatch.py').read_text(encoding='utf-8'))
    handler=next(n for n in route_module.body if isinstance(n,ast.FunctionDef)
                 and n.name=='_is_mcel_native_shell_child')
    env={'parse_qs':parse_qs,'urlsplit':urlsplit}
    exec(compile(ast.Module(body=[handler],type_ignores=[]),'viewport_child_contract','exec'),env)
    _is_mcel_native_shell_child=env['_is_mcel_native_shell_child']
    shell=render_applications_shell_candidate(ROOT,canonical_promoted=True).encode()
    assert len(shell)>10000
    assert b'data-mcel-shell-canonical-promoted="true"' in shell
    assert len(sha256(shell).hexdigest())==64
    for app in acceptance.APPS:
        child=acceptance.native_child_route(app)
        assert child==acceptance.ROUTES[app]+'?mcel-shell-child=1'
        assert _is_mcel_native_shell_child(SimpleNamespace(path=child,headers={})) is True
        assert _is_mcel_native_shell_child(SimpleNamespace(path=acceptance.ROUTES[app],headers={})) is False
        assert _is_mcel_native_shell_child(SimpleNamespace(path=acceptance.ROUTES[app],
             headers={'Sec-Fetch-Dest':'iframe','Sec-Fetch-Site':'same-origin'})) is True


def test_document_firewall_only_instruments_exact_native_child_and_rejects_parent_fallback():
    from types import SimpleNamespace
    base='http://127.0.0.1:8765'
    app='calculator'
    child=acceptance.native_child_route(app)
    canonical=acceptance.expected_html(app,ROOT)
    shell=render_applications_shell_candidate(ROOT,canonical_promoted=True)

    class Context:
        def route(self,match,handler):
            assert match=='**/*'
            self.handler=handler
        def route_web_socket(self,match,handler):
            self.socket_handler=handler

    class Route:
        def __init__(self,body):
            self.body=body
            self.fulfilled=None
            self.aborted=False
        def fetch(self,**kwargs):
            return SimpleNamespace(status=200,text=lambda:self.body,
                body=lambda:self.body.encode(),headers={'content-type':'text/html; charset=utf-8'})
        def fulfill(self,**kwargs):self.fulfilled=kwargs
        def abort(self):self.aborted=True
        def continue_(self):raise AssertionError('Forbidden implicit network continuation')

    for role in ('incumbent','signals'):
        firewall={'blocked':[],'requests':[],'responses':[],'servedHashes':[]}
        ctx=Context()
        acceptance._guard_context(ctx,app,base,candidate=role=='signals',original_html=canonical,
                                  firewall=firewall,parent_shell=shell.encode())
        req=SimpleNamespace(url=base+child,method='GET',post_data=None,resource_type='document')
        exact=Route(canonical)
        ctx.handler(exact,req)
        assert not exact.aborted and exact.fulfilled is not None
        assert exact.fulfilled['body']==(acceptance.instrument_candidate(app,canonical)
                                        if role=='signals' else acceptance.instrument_reference(app,canonical))
        assert firewall['servedHashes']==[acceptance._sha(canonical)]

        wrong=Route(shell)
        ctx.handler(wrong,req)
        assert wrong.aborted and wrong.fulfilled is None
        assert firewall['documentDrift']['reason']=='PARENT_SHELL_RETURNED_FOR_NATIVE_CHILD'

        parent=Route(shell)
        ctx.handler(parent,SimpleNamespace(url=base+acceptance.ROUTES[app],method='GET',
                                          post_data=None,resource_type='document'))
        assert parent.aborted and parent.fulfilled is None


def test_preflight_only_accepts_real_promoted_shell_and_all_three_native_children(tmp_path,monkeypatch):
    shell=render_applications_shell_candidate(ROOT,canonical_promoted=True).encode()
    native={acceptance.native_child_route(app):acceptance.expected_html(app,ROOT).encode()
            for app in acceptance.APPS}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in acceptance.ROUTES.values():data=shell
            elif self.path in native:data=native[self.path]
            elif self.path in acceptance.DEPENDENCIES:data=shell
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    def forbidden(*_args,**_kwargs):
        raise AssertionError('Preflight-only must never mount or enable signals')
    monkeypatch.setattr(acceptance,'_single_app',forbidden)
    try:
        result=acceptance.run(f'http://127.0.0.1:{server.server_port}',ROOT,
                report=tmp_path/'healthy.json',preflight_only=True)
        assert all(row['status']=='PASS_EXACT' for row in result['parentShellPreflight'].values())
        assert all(row['status']=='PASS_EXACT' for row in result['hostDocumentPreflight'].values())
        assert all(row['status']=='NOT_RUN_PREFLIGHT_ONLY' for row in result['apps'].values())
        assert all(row['stages']==[] for row in result['apps'].values())
        assert result['productionCutoverAuthorized'] is False
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_preflight_rejects_parent_shell_drift_even_with_exact_native_child(tmp_path,monkeypatch):
    shell=render_applications_shell_candidate(ROOT,canonical_promoted=True).encode()
    bad_shell=shell.replace(b'MCEL Shell',b'MCEL OLD Shell',1)
    assert bad_shell!=shell
    child=acceptance.expected_html('calculator',ROOT).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path==acceptance.ROUTES['calculator']:data=bad_shell
            elif self.path==acceptance.native_child_route('calculator'):data=child
            elif self.path in acceptance.DEPENDENCIES:data=bad_shell
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(acceptance,'_single_app',lambda *a,**k:
                        (_ for _ in ()).throw(AssertionError('Cannot mount with wrong shell')))
    try:
        result=acceptance.run(f'http://127.0.0.1:{server.server_port}',ROOT,('calculator',),
                              report=tmp_path/'parent-drift.json',preflight_only=True)
        assert result['parentShellPreflight']['calculator']['status']=='BLOCKED'
        assert result['hostDocumentPreflight']['calculator']['status']=='PASS_EXACT'
        assert result['apps']['calculator']['status']=='BLOCKED'
        assert result['apps']['calculator']['failures']==[
            'PARENT_SHELL_SOURCE_DRIFT_OR_AUTH_PAGE']
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_preflight_only_rejects_stale_server_and_never_mounts_apps(tmp_path,monkeypatch):
    # One canonical app is deliberately tampered *on the server*, rather than
    # changing local source; the harness must diagnose, not patch it in-browser.
    original=acceptance.expected_html('calculator',ROOT).encode()
    stale=original.replace(b'Main Computer Calculator</title>',
                           b'Main Computer Calculator stale</title>')
    assert stale!=original
    shell=render_applications_shell_candidate(ROOT,canonical_promoted=True).encode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/applications/calculator':
                data=shell
            elif self.path==acceptance.native_child_route('calculator'):
                data=stale
            elif self.path in acceptance.DEPENDENCIES:
                data=b'<!doctype html><title>Dependency available</title>'
            else:
                self.send_error(404);return
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    def forbidden(*_args,**_kwargs):
        raise AssertionError('Mount must never be attempted for a mismatched server')
    monkeypatch.setattr(acceptance,'_single_app',forbidden)
    try:
        result=acceptance.run(f'http://127.0.0.1:{server.server_port}',ROOT,('calculator',),
                              report=tmp_path/'preflight.json',preflight_only=True)
        probe=result['hostDocumentPreflight']['calculator']
        assert probe['status']=='BLOCKED' and probe['reason']=='HOSTED_APP_SOURCE_DRIFT'
        assert probe['expectedSha256']!=probe['observedSha256']
        assert result['apps']['calculator']['status']=='BLOCKED'
        assert result['apps']['calculator']['stages']==[]
        assert result['productionCutoverAuthorized'] is False
        assert 'Sign in' not in (tmp_path/'preflight.json').read_text()
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_hosted_file_manager_mtime_resampling_is_strict_and_does_not_mask_other_differences():
    from tools.mcel_signal_differential_acceptance import _diff
    base={'rows':[], 'focus':'outside-root', 'selection':None,
          'state':{'state:entries':[{'id':'runtime','name':'runtime','mtime':100.0,'bytes':0}]},
          'receipts':[], 'transport':[], 'effectCount':0, 'overflow':False}
    from copy import deepcopy
    newer=deepcopy(base)
    newer['state']['state:entries'][0]['mtime']=105.0
    count,samples=_diff(base,newer)
    assert count==1 and samples[0]['path']=='state.state:entries[0].mtime'
    assert acceptance._is_file_manager_mtime_only_difference(count,samples)
    # A time change plus a write-visible size/content change must not be
    # called a harmless live re-observation.
    newer['state']['state:entries'][0]['bytes']=3
    count,samples=_diff(base,newer)
    assert count==2 and not acceptance._is_file_manager_mtime_only_difference(count,samples)
    assert not acceptance._is_file_manager_mtime_only_difference(2,samples[:1])
    assert not acceptance._is_file_manager_mtime_only_difference(1,[{'path':'focus'}])
    assert not acceptance._is_file_manager_mtime_only_difference(1,[{'path':'state.state:preview.content'}])


def test_live_file_manager_mtime_reobservation_requires_full_convergence(monkeypatch):
    from copy import deepcopy
    same={'rows':[], 'focus':'outside-root', 'selection':None,
          'state':{'state:entries':[{'id':'runtime','mtime':100.0}]},
          'receipts':[], 'transport':[], 'effectCount':0, 'overflow':False}
    newer=deepcopy(same)
    newer['state']['state:entries'][0]['mtime']=120.0
    fake={'incumbent':object(),'signals':object()}
    reads=[]
    monkeypatch.setattr(acceptance,'_settle',lambda page:None)
    monkeypatch.setattr(acceptance,'_refresh_file_manager_live_listing',lambda page:reads.append(page))
    snapshots={'incumbent':same,'signals':newer}
    monkeypatch.setattr(acceptance,'_snapshot',lambda page,app:snapshots[
        'incumbent' if page is fake['incumbent'] else 'signals'])
    # A changed filesystem metadata value may only pass after both apps have
    # made a fresh real read and the complete snapshots compare equal.
    def refresh(page):
        reads.append(page)
        if page is fake['signals']:
            snapshots['signals']=deepcopy(same)
    monkeypatch.setattr(acceptance,'_refresh_file_manager_live_listing',refresh)
    result=acceptance._stabilize_file_manager_live_listing(fake)
    assert result['status']=='PASS_LIVE_REOBSERVATION'
    assert result['liveResamples']==1 and len(reads)==2
    assert result['differencePathsBefore']==['state.state:entries[0].mtime']
    assert result['differencePathsAfter']==[]
    # If backend metadata keeps changing, never silently declare parity.
    snapshots['signals']=newer
    reads.clear()
    monkeypatch.setattr(acceptance,'_refresh_file_manager_live_listing',lambda page:reads.append(page))
    result=acceptance._stabilize_file_manager_live_listing(fake,max_resamples=2)
    assert result['status']=='BLOCKED_LIVE_METADATA_DID_NOT_CONVERGE'
    assert result['liveResamples']==2 and len(reads)==4
    # Non-mtime mismatch cannot trigger any extra backend requests.
    snapshots['signals']=deepcopy(same)
    snapshots['signals']['focus']='different'
    reads.clear()
    result=acceptance._stabilize_file_manager_live_listing(fake)
    assert result['status']=='BLOCKED_NON_METADATA_DIFFERENCE'
    assert result['liveResamples']==0 and not reads


def test_hosted_diff_diagnostics_are_content_redacted_and_path_specific():
    a={'rows':[{'path':'0','tag':'DIV','attrs':{'id':'root'},'text':None,'visible':True},
               {'path':'0/0','tag':'SPAN','attrs':{},'text':'private live status A','visible':True}]}
    b={'rows':[{'path':'0','tag':'DIV','attrs':{'id':'root'},'text':None,'visible':True},
               {'path':'0/0','tag':'SPAN','attrs':{},'text':'private live status B','visible':True}]}
    sample=[{'path':'rows[1].text','incumbent':'private live status A',
             'candidate':'private live status B'}]
    result=acceptance._snapshot_difference_diagnostics(a,b,sample)
    assert result[0]['path']=='rows[1].text'
    assert result[0]['incumbent']['nearestNamedAncestor']=='id=root'
    assert result[0]['incumbent']['sha256']!=result[0]['signals']['sha256']
    assert 'private live status' not in json.dumps(result)
    assert acceptance._snapshot_difference_diagnostics(a,b,[{'path':'rows.length'}])==[
        {'path':'rows.length'}]


def test_editor_monaco_ready_barrier_verifies_real_native_model_identity():
    class FakePage:
        def __init__(self,path='README.md',text='hello source'):
            self.path=path;self.text=text
        def wait_for_function(self,expr,*,arg,timeout):
            assert 'MainComputerMonacoAdapter' in expr
            assert arg=='README.md' and timeout==30000
            if self.path!=arg:raise TimeoutError('wrong document')
        def evaluate(self,expr):
            if 'content' in expr:return {}
            if 'text:window.__CE_MONACO_MODEL__' in expr:
                return {'mounted':True,'path':self.path,'readOnly':False,'text':self.text}
            return {}
    good=acceptance._await_editor_monaco_ready(FakePage(),'README.md')
    assert good['mounted'] is True and good['contentSha256']==acceptance._sha('hello source')
    with pytest.raises(RuntimeError,match='HOSTED_NATIVE_MONACO_NOT_READY'):
        acceptance._await_editor_monaco_ready(FakePage('wrong.js'),'README.md')


def test_disposable_lane_requires_workbench_reveal_before_live_click():
    import inspect
    from tools import mcel_hosted_cutover_disposable as d
    source=inspect.getsource(d.exercise_write)
    assert '_reveal_explorer_source(page,target,role)' in source
    assert 'reveal_editor_control(page,control)' in inspect.getsource(d._reveal_explorer_source)
    assert 'reveal_editor_control(page,save)' in source
    assert 'reveal_editor_control(incumbent,incumbent_save)' in source
    assert 'window.MainComputerMonacoAdapter?.inspect?.().mounted===true' in source
    assert 'document.querySelector(\'[data-mcel-document-dirty]\')?.dataset.mcelDocumentDirty' in source


def test_disposable_control_expands_real_browser_explorer_and_ancestor_branches():
    from playwright.sync_api import sync_playwright
    from tools import mcel_hosted_cutover_disposable as d
    from tools.mcel_phase2b_browser import launch_semantic_browser
    target='runtime/reports/mcel-hosted-signal-case/reference.js'
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':1272,'height':900})
            # Real browser actionability is checked without fake localhost IO.
            page.set_content('''<div data-mcel-document-workbench data-mcel-explorer-open="false">
              <button data-mcel-document-toggle="explorer">Explorer</button>
              <aside data-mcel-document-explorer hidden>
                <div data-mcel-folder-tree>
                  <button data-mcel-tree-toggle="runtime" aria-expanded="false">Runtime</button>
                  <div id="runtime-children" hidden>
                    <button data-mcel-tree-toggle="runtime/reports" aria-expanded="false">Reports</button>
                    <div id="reports-children" hidden>
                       <button data-mcel-tree-toggle="runtime/reports/mcel-hosted-signal-case" aria-expanded="false">Fixture</button>
                       <div id="fixture-children" hidden>
                         <span data-record-key="'''+target+'''">
                         <button data-select-key="'''+target+'''">reference.js</button></span>
                       </div>
                    </div>
                  </div>
                </div>
              </aside>
            </div><script>
              const root=document.querySelector('[data-mcel-document-workbench]');
              const explorer=document.querySelector('[data-mcel-document-explorer]');
              document.querySelector('[data-mcel-document-toggle]').onclick=()=>{
                root.setAttribute('data-mcel-explorer-open','true');explorer.hidden=false;
              };
              for(const [key,id] of [['runtime','runtime-children'],
                  ['runtime/reports','reports-children'],
                  ['runtime/reports/mcel-hosted-signal-case','fixture-children']]){
                const el=document.querySelector('[data-mcel-tree-toggle="'+key+'"]');
                el.onclick=()=>{el.setAttribute('aria-expanded','true');document.getElementById(id).hidden=false;};
              }
              window.__chosen=0;
              document.querySelector('[data-select-key]').onclick=()=>window.__chosen++;
            </script>''')
            control=d._reveal_explorer_source(page,target,'incumbent')
            assert control.is_visible()
            d._click_observed(page,control,'select-disposable-source','incumbent')
            assert page.evaluate('window.__chosen')==1
            assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-explorer-open')=='true'
        finally:
            browser.close()


def test_disposable_click_failure_reports_step_without_source_contents():
    from playwright.sync_api import sync_playwright
    from tools import mcel_hosted_cutover_disposable as d
    from tools.mcel_phase2b_browser import launch_semantic_browser
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            page.set_content('<button data-mcel-document-save disabled>Save source</button>')
            with pytest.raises(RuntimeError,match='DISPOSABLE_UI_CLICK_FAILED:save-incumbent-disposable-source:incumbent:') as exc:
                d._click_observed(page,page.locator('[data-mcel-document-save]'),
                                  'save-incumbent-disposable-source','incumbent')
            assert '"enabled": false' in str(exc.value)
        finally:
            browser.close()

def test_disposable_keyboard_activates_real_generated_tree_with_many_siblings():
    """Use the actual Code Editor renderer, not just a hand-authored DOM toy."""
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools.mcel_code_editor_functional_parity_frob import candidate_html
    from tools import mcel_hosted_cutover_disposable as d
    target='runtime/reports/mcel-hosted-signal-case/reference.js'
    html=candidate_html(ROOT)
    original="const dirs=prefix===''?['src','docs']:[];"
    replacement=("const dirs=prefix===''?['src','docs','runtime']:"
                 "prefix==='runtime'?['runtime/reports']:"
                 "prefix==='runtime/reports'?[...Array.from({length:350},"
                 "(_,i)=>'runtime/reports/log-'+String(i).padStart(3,'0')),'runtime/reports/mcel-hosted-signal-case']:[];")
    assert html.count(original)==1
    html=html.replace(original,replacement)
    with sync_playwright() as playwright:
        browser,_=launch_semantic_browser(playwright)
        try:
            page=browser.new_page(viewport={'width':1272,'height':900})
            page.set_content(html)
            page.evaluate('''path=>window.__editorFiles[path]='export const mcelHostedAcceptance = 1;\\n' ''',target)
            page.locator('button[data-intent="intent:list-files"]').first.click()
            page.locator('[data-record-key="runtime"] button[data-select-key="runtime"]').wait_for()
            for directory in ('runtime','runtime/reports','runtime/reports/mcel-hosted-signal-case'):
                result=page.evaluate('''async path=>window.__mcelWorkbench.app.execute(
                   'intent:browse-directory',{path})''',directory)
                assert result['disposition']=='completed'
            control=d._reveal_explorer_source(page,target,'incumbent')
            assert control.is_visible()
            before=page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
                .filter(r=>r.intentId==='intent:open-source').length''')
            d._activate_observed_file_by_keyboard(page,target,'incumbent',before)
            after=page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
                .filter(r=>r.intentId==='intent:open-source').length''')
            assert after==before+1
            assert page.evaluate('''()=>window.__editorCalls.filter(c=>c.path.endsWith('/editor/read')).length''')==1
        finally:
            browser.close()


def test_disposable_keyboard_rejects_unobserved_or_hidden_file_controls():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    with sync_playwright() as playwright:
        browser,_=launch_semantic_browser(playwright)
        try:
            page=browser.new_page()
            page.set_content('''<div data-mcel-document-workbench data-mcel-explorer-open="true">
              <div data-mcel-folder-tree><span data-record-key="test.js">
               <button type="button" data-select-key="test.js" hidden>test.js</button>
              </span></div></div>''')
            with pytest.raises(RuntimeError,match='DISPOSABLE_SOURCE_KEYBOARD_NOT_ACTIONABLE'):
                d._activate_observed_file_by_keyboard(page,'test.js','incumbent',0)
            with pytest.raises(RuntimeError,match='DISPOSABLE_SOURCE_KEYBOARD_NOT_ACTIONABLE'):
                d._activate_observed_file_by_keyboard(page,'missing.js','incumbent',0)
        finally:
            browser.close()


def test_disposable_click_diagnostic_falls_back_to_fresh_dom_query():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    class UnavailableHandle:
        def element_handle(self,timeout):
            raise TimeoutError('Element was replaced during actionability probing')
        def count(self):return 1
    with sync_playwright() as playwright:
        browser,_=launch_semantic_browser(playwright)
        try:
            page=browser.new_page()
            page.set_content('<button type="button" data-mcel-document-save disabled>Save</button>')
            details=d._control_geometry(page,UnavailableHandle(),'save-incumbent-disposable-source')
            assert details['diagnostic']=='FRESH_DOM_QUERY_AFTER_HANDLE_UNAVAILABLE'
            assert details['present'] is True
            assert details['enabled'] is False
        finally:
            browser.close()

def _disposable_keyboard_receipt_test_page(page, *, disposition='completed', count=1):
    '''A native file button with a real browser event handler and app receipt ledger.'''
    page.set_content('''
      <div data-mcel-document-workbench data-mcel-explorer-open="true">
        <div data-mcel-folder-tree>
          <div data-record-key="runtime/reports/test/reference.js">
            <button type="button" data-select-key="runtime/reports/test/reference.js">reference.js</button>
          </div>
        </div>
      </div>
      <script>
        window.__openReceipts=[];
        window.__mcelWorkbench={app:{receipts:()=>window.__openReceipts}};
        document.querySelector('[data-select-key]').addEventListener('click',()=>{
          for(let i=0;i<window.__receiptCount;i++)
            window.__openReceipts.push({intentId:'intent:open-source',
              disposition:window.__receiptDisposition,sequence:i+1});
        });
      </script>
    ''')
    page.evaluate('''([disposition,count])=>{
       window.__receiptDisposition=disposition;window.__receiptCount=count;
    }''',[disposition,count])


def test_disposable_receipt_barrier_is_independent_of_animation_frames():
    '''Regression: the background page has a receipt even if rAF never executes.'''
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _disposable_keyboard_receipt_test_page(page)
            page.evaluate('window.requestAnimationFrame=()=>0')
            d._activate_observed_file_by_keyboard(
                page,'runtime/reports/test/reference.js','incumbent',0)
            assert len(page.evaluate('window.__openReceipts'))==1
        finally:
            browser.close()


def test_disposable_receipt_barrier_confirms_final_state_after_missed_poll():
    '''The polling callback can fail after a valid, exactly-once receipt arrived.'''
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    class PageWithMissedPoll:
        def __init__(self,actual):self.actual=actual;self.keyboard=actual.keyboard
        def evaluate(self,*args,**kwargs):return self.actual.evaluate(*args,**kwargs)
        def wait_for_function(self,*args,**kwargs):
            assert kwargs['polling']==100
            raise PlaywrightTimeoutError('polling callback missed update')
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _disposable_keyboard_receipt_test_page(page)
            d._activate_observed_file_by_keyboard(
                PageWithMissedPoll(page),'runtime/reports/test/reference.js','incumbent',0)
            assert len(page.evaluate('window.__openReceipts'))==1
        finally:browser.close()


def test_disposable_receipt_barrier_rejects_failure_and_duplicates():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _disposable_keyboard_receipt_test_page(page,disposition='failed')
            with pytest.raises(RuntimeError,match='DISPOSABLE_SOURCE_OPEN_RECEIPT_NOT_COMPLETED'):
                d._activate_observed_file_by_keyboard(
                    page,'runtime/reports/test/reference.js','incumbent',0)
            _disposable_keyboard_receipt_test_page(page,count=2)
            with pytest.raises(RuntimeError,match='DISPOSABLE_SOURCE_OPEN_RECEIPT_NOT_EXACTLY_ONCE'):
                d._activate_observed_file_by_keyboard(
                    page,'runtime/reports/test/reference.js','incumbent',0)
        finally:browser.close()


def _disposable_save_test_page(page):
    """A real Chromium receipt ledger with no network side effects."""
    page.set_content('''
      <div data-mcel-document-dirty="true"></div>
      <button type="button" data-mcel-document-save>Save source</button>
      <script>
        window.__saveReceipts=[];
        window.__hostHeld=[];
        window.__mcelWorkbench={app:{receipts:()=>window.__saveReceipts}};
        document.querySelector('[data-mcel-document-save]').onclick=()=>{
          window.__saveReceipts.push({intentId:'intent:save-source',disposition:'completed',sequence:1});
        };
      </script>''')


def test_disposable_actual_save_barrier_uses_receipts_not_paint(tmp_path):
    """The old gate timed out using animation frames even when a save had settled."""
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    path=tmp_path/'reference.js'
    path.write_text(d.REPLACEMENT,encoding='utf8')
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _disposable_save_test_page(page)
            page.evaluate('window.requestAnimationFrame=()=>0')
            d._click_observed(page,page.locator('[data-mcel-document-save]'),
                              'save-incumbent-disposable-source','incumbent')
            witness=d._await_exact_save_receipt(page,'incumbent',0,path,timeout_seconds=1)
            assert witness['receiptCount']==1
            assert witness['fileSha256']==d._disposable_file_evidence(path)['sha256']
        finally:browser.close()


def test_disposable_save_rejects_failed_duplicate_and_missing_receipts(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    path=tmp_path/'candidate.js'
    path.write_text(d.REPLACEMENT,encoding='utf8')
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _disposable_save_test_page(page)
            with pytest.raises(RuntimeError,match='DISPOSABLE_SAVE_NO_RECEIPT'):
                d._await_exact_save_receipt(page,'incumbent',0,path,timeout_seconds=.12)
            page.evaluate("window.__saveReceipts.push({intentId:'intent:save-source',disposition:'failed',reason:'save-receipt-invalid'})")
            with pytest.raises(RuntimeError,match='DISPOSABLE_SAVE_RECEIPT_NOT_COMPLETED:incumbent') as error:
                d._await_exact_save_receipt(page,'incumbent',0,path,timeout_seconds=.2)
            assert 'save-receipt-invalid' in str(error.value)
            page.evaluate("window.__saveReceipts=[{intentId:'intent:save-source',disposition:'completed'},{intentId:'intent:save-source',disposition:'completed'}]")
            with pytest.raises(RuntimeError,match='DISPOSABLE_SAVE_NOT_EXACTLY_ONCE'):
                d._await_exact_save_receipt(page,'incumbent',0,path,timeout_seconds=.2)
            page.evaluate("window.__saveReceipts=[{intentId:'intent:save-source',disposition:'completed'}]")
            path.write_text(d.ORIGINAL,encoding='utf8')
            with pytest.raises(RuntimeError,match='DISPOSABLE_SAVE_RECEIPT_WITHOUT_EXACT_FILE'):
                d._await_exact_save_receipt(page,'incumbent',0,path,timeout_seconds=.2)
        finally:browser.close()


def test_disposable_held_write_requires_actual_bytes_without_early_receipt(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools import mcel_hosted_cutover_disposable as d
    path=tmp_path/'candidate.js'
    path.write_text(d.REPLACEMENT,encoding='utf8')
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _disposable_save_test_page(page)
            page.evaluate('window.__hostHeld=[{}]')
            d._await_held_write_response(page,0,path,timeout_seconds=.2)
            page.evaluate('window.__hostHeld=[{},{}]')
            with pytest.raises(RuntimeError,match='DISPOSABLE_SAVE_HELD_RESPONSE_NOT_EXACTLY_ONCE'):
                d._await_held_write_response(page,0,path,timeout_seconds=.2)
            page.evaluate('window.__hostHeld=[{}];window.__saveReceipts=[{intentId:"intent:save-source",disposition:"completed"}]')
            with pytest.raises(RuntimeError,match='DISPOSABLE_SAVE_SETTLED_BEFORE_HELD_RESPONSE_RELEASE'):
                d._await_held_write_response(page,0,path,timeout_seconds=.2)
            page.evaluate('window.__saveReceipts=[]')
            path.write_text(d.ORIGINAL,encoding='utf8')
            with pytest.raises(RuntimeError,match='HOST_WRITE_NOT_APPLIED_BEFORE_HELD_RECEIPT'):
                d._await_held_write_response(page,0,path,timeout_seconds=.2)
        finally:browser.close()


def test_live_save_lifecycle_reports_real_dispatch_without_contents():
    """Witness the real UI click/port/fetch chain without source or auth leakage."""
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from tools.mcel_hosted_signal_cutover_acceptance import HOLD_WRAPPER
    from tools import mcel_hosted_cutover_disposable as d
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            page.set_content('<button type="button" data-mcel-document-save>Save</button>'
                             '<p data-mcel-document-dirty="true">Unsaved changes</p>')
            page.evaluate("window.fetch=async()=>({ok:true,status:200})")
            page.evaluate('('+HOLD_WRAPPER+')()')
            page.locator('[data-mcel-document-save]').click()
            page.evaluate('''()=>document.dispatchEvent(new CustomEvent('mcel:parity-transport',
              {detail:{phase:'request',endpoint:'/api/applications/editor/project/file/save',requestId:4}}))''')
            page.evaluate("window.fetch('/api/applications/editor/project/file/save')")
            event=d._save_lifecycle_evidence(page,firewall={
                'requests':[{'path':'/api/applications/editor/project/file/save'}],
                'responses':[{'path':'/api/applications/editor/project/file/save','status':200}],
                'blocked':[]})
            assert d._save_unsettled_classification(event)=='HTTP_RESPONSE_SEEN_BUT_RECEIPT_UNSETTLED'
            assert [e['phase'] for e in event['browser']['events']]==[
                'native-save-click','port-request','fetch-start','fetch-response']
            assert event['network']['requestCount']==1
            assert event['network']['responseStatuses']==[200]
            assert 'source' not in str(event).lower()
        finally:browser.close()


def test_live_save_lifecycle_distinguishes_guard_and_missing_click():
    from tools import mcel_hosted_cutover_disposable as d
    assert d._save_unsettled_classification({'browser':{'events':[]}})=='SAVE_CLICK_NOT_OBSERVED'
    assert d._save_unsettled_classification({'browser':{'events':[{'phase':'native-save-click'}]}})==\
           'UI_CLICK_PENDING_BEFORE_PORT_REQUEST'
    assert d._save_unsettled_classification({'browser':{'events':[{'phase':'port-request'}]}})==\
           'PORT_REQUEST_NOT_DISPATCHED_TO_FETCH'
    assert d._save_unsettled_classification({'browser':{'events':[{'phase':'fetch-start'}]},
              'network':{'requestCount':1,'responseStatuses':[]}})==\
           'BACKEND_OR_HTTP_RESPONSE_PENDING'
    assert d._save_unsettled_classification({'browser':{'events':[{'phase':'fetch-start'}]},
              'network':{'blockedReasons':['DENIED_denied-disposable-write-payload']}})==\
           'GUARDED_REQUEST_REJECTED'


def test_hosted_pageerror_evidence_preserves_js_error_and_safe_location():
    class SyntheticPageError(Exception):
        @property
        def stack(self):
            return ('TypeError: Cannot read properties of null (reading \"map\")\n'
                    '    at updateDraft (http://127.0.0.1:8765/applications/scripts/mcel-semantic-operational-runtime.js:442:17)\n'
                    '    at run (file:///C:/Users/private_user/source/app.js:77:8)')
    witness=acceptance._browser_pageerror_evidence(
        SyntheticPageError('TypeError: Cannot read properties of null (reading "map")'),
        'signals','post-disposable-live-save')
    assert witness['role']=='signals'
    assert witness['stage']=='post-disposable-live-save'
    assert 'Cannot read properties of null' in witness['message']
    assert witness['frames'][0]=={'file':'mcel-semantic-operational-runtime.js',
        'line':442,'column':17,'function':'updateDraft'}
    assert witness['frames'][1]['file']=='app.js'
    assert 'private_user' not in json.dumps(witness)
    assert '127.0.0.1' not in json.dumps(witness)
    assert len(witness['fingerprint'])==16
    assert '[URL]' in acceptance._safe_browser_error_text(
        'Error: at http://127.0.0.1:8765/secret?access_token=ABC')
    assert '[LOCAL_PATH]' in acceptance._safe_browser_error_text(
        'Error: at C:\\Users\\private_user\\token.txt')
    assert 'ABC' not in acceptance._safe_browser_error_text(
        'Error: at http://127.0.0.1:8765/secret?access_token=ABC')


def test_hosted_pageerror_evidence_real_browser_event():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            events=[]
            page.on('pageerror',lambda exc:events.append(
                acceptance._browser_pageerror_evidence(exc,'incumbent','after-save')))
            page.set_content('<!doctype html><script>setTimeout(()=>{' 
                             'throw new TypeError("MCEL_BROWSER_PAGEERROR_PROBE");},0);</script>')
            page.wait_for_timeout(150)
            assert len(events)==1,events
            assert 'MCEL_BROWSER_PAGEERROR_PROBE' in events[0]['message']
            assert events[0]['role']=='incumbent'
            assert events[0]['stage']=='after-save'
            assert events[0]['errorClass']
        finally:
            browser.close()


def test_hosted_pageerror_remains_a_failure_and_records_details():
    # Ensure the new instrumentation cannot be mistaken for an exception waiver.
    from inspect import getsource
    code=getsource(acceptance._single_app)
    assert "data['failures'].append('BROWSER_PAGEERROR')" in code
    assert "data['browserPageErrors']" in code
    assert "data['status']='BLOCKED'" in code
    assert "observed_pageerror(exc,r)" in code


def test_hosted_monaco_style_difference_keeps_exact_gate_and_emits_privacy_safe_fingerprint():
    from tools import mcel_hosted_signal_cutover_acceptance as a
    assert a._exact_snapshot_resamples('code-editor','hosted-source-open')==4
    assert a._exact_snapshot_resamples('code-editor','live-read-response-held')==0
    assert a._exact_snapshot_resamples('file-manager','hosted-source-open')==0
    assert a._exact_snapshot_resamples('calculator','real-host-arithmetic')==3
    left={'rows':[{'path':'0/15/4','tag':'DIV','attrs':{
        'class':'view-line', 'style':'left: 10px; width: 12px'}}]}
    right={'rows':[{'path':'0/15/4','tag':'DIV','attrs':{
        'class':'view-line', 'style':'left: 11px; width: 12px'}}]}
    n,differences=a._diff(left,right)
    assert n==1
    assert differences[0]['path']=='rows[0].attrs.style'
    diagnostic=a._snapshot_difference_diagnostics(left,right,differences)
    assert len(diagnostic)==1
    assert diagnostic[0]['incumbent']['nodePath']=='0/15/4'
    assert diagnostic[0]['signals']['nodePath']=='0/15/4'
    assert diagnostic[0]['incumbent']['styleSha256']!=diagnostic[0]['signals']['styleSha256']
    assert diagnostic[0]['incumbent']['styleProperties']==['left','width']
    assert '10px' not in str(diagnostic) and '11px' not in str(diagnostic)
