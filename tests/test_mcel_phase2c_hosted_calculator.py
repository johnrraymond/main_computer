"""Hosted viewport integration and authority controls for the semantic Calculator candidate.

The HTTP route and real Calculator core are production code; the opted-in
candidate authored definition remains a Phase 2 reference source, not yet the
full production Calculator with all supported modes.
"""
from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_phase2c_candidate_host import CandidateHostError, compile_candidate, render_candidate_page, ROUTE
from tools.mcel_phase2b_browser import BrowserUnavailable, launch_semantic_browser


@pytest.fixture(scope="module")
def candidate_http():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != ROUTE:
                self.send_error(404)
                return
            self._send_text(render_candidate_page(), "text/html; charset=utf-8")
        def _send_text(self, value, content_type):
            raw = value.encode("utf8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.signal = lambda *_args, **_kwargs: None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{ROUTE}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b, _backend = launch_semantic_browser(p)
        except BrowserUnavailable as exc:
            pytest.fail(str(exc))
        try:
            yield b
        finally:
            b.close()


def test_candidate_http_delivery(candidate_http):
    from urllib.request import urlopen
    with urlopen(candidate_http, timeout=10) as response:
        assert response.status == 200
        page = response.read().decode('utf-8')
        assert 'mcel-phase2c-ir' in page
        assert 'McelSemanticOperationalRuntime.mount' in page


def test_candidate_paired_compiler_and_whitelisted_authority():
    ir = compile_candidate()
    assert ir['application']['appId'] == 'phase2-calculator'
    assert not ir.get('layouts')
    assert len(ir['intents']) == 2
    assert [e['risk'] for e in ir['effects']] == ['external-read']
    page = render_candidate_page()
    assert 'data-mcel-id="workspace"' not in page  # authored HTML isn't projected as fixed layout
    assert 'McelSemanticOperationalRuntime.mount' in page
    assert 'McelCalculatorCoreCapabilityPort.create' in page


def test_hosted_real_core_calculation_and_receipt(candidate_http, browser):
    page = browser.new_page()
    try:
        page.set_content(render_candidate_page())
        assert page.locator('[data-mcel-region]').count() == 5
        assert page.locator('#mcel-phase2c-diagnostic').inner_text() == 'Semantic Calculator candidate mounted'
        page.locator('input[name="expression"]').fill('2 + 3 * 4')
        page.locator('form[data-intent="intent:compute"] button').click()
        page.locator('[data-state="state:result"]').get_by_text('14').wait_for()
        page.locator('[data-disposition="completed"]').wait_for()
        assert page.evaluate('window.__mcelPhase2CCandidate.app.readState("state:result")') == 14
        receipt = page.evaluate('window.__mcelPhase2CCandidate.app.receipts().at(-1)')
        assert receipt['intentId'] == 'intent:compute'
        assert receipt['disposition'] == 'completed'
        # There is no arbitrary execution port or unsafe runtime authority.
        assert page.evaluate('window.__mcelPhase2CCandidate.app.getState()["state:result"]') == 14
    finally:
        page.close()


def test_bad_expression_is_not_published_as_success(candidate_http, browser):
    page = browser.new_page()
    try:
        page.set_content(render_candidate_page())
        page.locator('input[name="expression"]').fill('process.exit(1)')
        page.locator('form[data-intent="intent:compute"] button').click()
        page.locator('[data-disposition="failed"]').wait_for()
        assert page.locator('[data-state="state:result"]').inner_text() == ''
        assert page.evaluate('window.__mcelPhase2CCandidate.app.receipts().at(-1).disposition') == 'failed'
    finally:
        page.close()


def test_explicit_port_rejects_forged_authority(candidate_http, browser):
    page = browser.new_page()
    try:
        page.set_content(render_candidate_page())
        results = page.evaluate('''async () => {
          const port=McelCalculatorCoreCapabilityPort.create({core:MainComputerCalculatorCore});
          const base={appId:'phase2-calculator',intentId:'intent:compute',
            effectId:'effect:compute.request',capabilityId:'capability:arithmetic',
            operation:'compute',runtimeMethod:'evaluateExpression',risk:'external-read',
            arguments:{expression:'2+2'}};
          return {
            valid:await port.authorize(base),
            forgedApp:await port.authorize({...base,appId:'task-manager'}),
            forgedEffect:await port.authorize({...base,effectId:'effect:terminate'}),
            forgedRisk:await port.authorize({...base,risk:'source-write'}),
            forgedArgs:await port.authorize({...base,arguments:{expression:'2+2',other:0}}),
          };
        }''')
        assert results == dict(valid=True, forgedApp=False, forgedEffect=False, forgedRisk=False, forgedArgs=False)
    finally:
        page.close()


def test_source_compilation_rejects_authority_drift(monkeypatch):
    import main_computer.mcel_phase2c_candidate_host as host
    original = host.compile_dsl_application
    def forged(src):
        value = original(src)
        ir = copy.deepcopy(value.normalized_ir)
        ir['effects'][0]['risk'] = 'source-write'
        class Result:
            valid = True
            normalized_ir = ir
        return Result()
    monkeypatch.setattr(host, 'compile_dsl_application', forged)
    with pytest.raises(CandidateHostError, match='authority changed'):
        host.render_candidate_page()


def test_missing_companion_source_does_not_fallback_to_html(tmp_path):
    import main_computer.mcel_phase2c_candidate_host as host
    with pytest.raises(CandidateHostError):
        host.render_candidate_page(tmp_path)


def test_viewport_route_registration_remains_opt_in_and_local_only():
    root=Path(__file__).resolve().parents[1]
    code=(root/'main_computer/viewport_route_dispatch.py').read_text(encoding='utf8')
    assert 'route_path == "/applications/mcel-semantic-calculator-candidate"' in code
    begin=code.index('route_path == "/applications/mcel-semantic-calculator-candidate"')
    excerpt=code[begin:begin+460]
    assert '_hard_halt_client_is_local(self)' in excerpt
    assert 'render_candidate_page' in excerpt
    assert 'HTTPStatus.FORBIDDEN' in excerpt
