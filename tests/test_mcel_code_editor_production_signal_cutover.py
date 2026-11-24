"""Production Code Editor signals, fail-closed rollback, and live witness contract."""
from __future__ import annotations

import pytest
from pathlib import Path

@pytest.fixture
def repo_root():
    return Path(__file__).resolve().parents[1]

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_signal_scheduler_rollout import start_certified
from tools import mcel_code_editor_signal_cutover_smoke as witness
from tools.mcel_code_editor_functional_parity_frob import FIXTURE_JS,MONACO_JS
from tools.mcel_phase2b_browser import launch_semantic_browser
from playwright.sync_api import sync_playwright


def _production_fixture(repo):
    html=render_semantic_page('code-editor',repo,promoted=True)
    token='const port=McelSemanticReadPorts.create({appId:ir.application.appId});'
    assert html.count(token)==1
    html=html.replace('<head>','<head>'+FIXTURE_JS,1)
    return html.replace(token,MONACO_JS+'\nconst port=(window.__editorPort=McelSemanticReadPorts.create({appId:ir.application.appId}));',1)


def test_default_is_on_and_rollback_switch_controls_only_code_editor(repo_root,monkeypatch):
    for x in ('MCEL_SIGNAL_SCHEDULER_DISABLE','MCEL_SIGNAL_SCHEDULER_CODE_EDITOR'):
        monkeypatch.delenv(x,raising=False)
    assert start_certified('code-editor',{})
    html=render_semantic_page('code-editor',repo_root,promoted=True)
    witness._expected_configuration(html,mode='signals')
    assert not start_certified('file-manager',{})
    assert 'signalSchedulerStartEnabled:false' in render_semantic_page('code-editor',repo_root,promoted=False)
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_CODE_EDITOR','incumbent')
    witness._expected_configuration(render_semantic_page('code-editor',repo_root,promoted=True),mode='incumbent')
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_CODE_EDITOR','certified')
    witness._expected_configuration(render_semantic_page('code-editor',repo_root,promoted=True),mode='signals')
    monkeypatch.setenv('MCEL_SIGNAL_SCHEDULER_DISABLE','1')
    witness._expected_configuration(render_semantic_page('code-editor',repo_root,promoted=True),mode='incumbent')


def test_normal_production_browser_runtime_rolls_back_without_losing_incumbent(repo_root,monkeypatch):
    for x in ('MCEL_SIGNAL_SCHEDULER_DISABLE','MCEL_SIGNAL_SCHEDULER_CODE_EDITOR'):
        monkeypatch.delenv(x,raising=False)
    html=_production_fixture(repo_root)
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':1272,'height':900})
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)[:250]))
            page.set_content(html)
            page.wait_for_function('()=>!!window.__mcelWorkbench?.app',timeout=15000)
            first=page.evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            assert first['mode']=='integrated-opt-in' and first['integrationEnabled'],first
            rolled=page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("incumbent")')
            assert rolled['status']=='ROLLED_BACK' and rolled['report']['mode']=='disabled',rolled
            after=page.evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
            assert not after['integrationEnabled']
            enabled=page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
            assert enabled['status']=='ENABLED' and enabled['report']['mode']=='integrated-opt-in'
            assert not errors, errors
        finally:browser.close()


def test_read_only_live_smoke_proves_default_and_runtime_rollback(repo_root,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import urlsplit
    for x in ('MCEL_SIGNAL_SCHEDULER_DISABLE','MCEL_SIGNAL_SCHEDULER_CODE_EDITOR'):
        monkeypatch.delenv(x,raising=False)
    expected=render_semantic_page('code-editor',repo_root,promoted=True).encode('utf8')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!=witness.NATIVE_PATH:
                self.send_error(404);return
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(expected)))
            self.end_headers()
            self.wfile.write(expected)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        out=witness.verify('http://127.0.0.1:'+str(server.server_port),repo_root)
        assert out['status']=='PASS' and out['initialMode']=='integrated-opt-in'
        assert out['finalMode']=='integrated-opt-in'
        assert out['runtimeRollback']=='PASS' and out['runtimeReenable']=='PASS'
        assert out['pageErrors']==0
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()
