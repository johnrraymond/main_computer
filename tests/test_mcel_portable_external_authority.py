"""Patch 18 authorization, provenance, and non-forgeability regression tests.

Mocks here test error handling and wiring; they MUST NOT certify live behavior.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.mcel_portable_calculator_external import (
    EXTERNAL_INTENTS, PortableCalculatorExternalAuthority, _sha,
    validate_mathics_expression,
)
from main_computer.mcel_portable_workspace import PortableCapabilityRefusal, PortableWorkspaceBroker
from tools.mcel_portable_intent_probe import finalize_evidence

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('url', [
    'http://example.org:11434', 'https://127.0.0.1:11434',
    'http://127.0.0.2:11434', 'http://127.0.0.1:11434/api/chat',
    'http://user:pw@127.0.0.1:11434', 'http://127.0.0.1:11434/?x=1',
])
def test_loopback_endpoint_does_not_allow_external_host_or_redirect_path(url):
    with pytest.raises(PortableCapabilityRefusal, match='loopback'):
        PortableCalculatorExternalAuthority(repo_root=ROOT, model='test:1',ollama_url=url)


@pytest.mark.parametrize('expression', [
    'Run["whoami"]', 'Import["/etc/passwd"]', 'Get["secret"]',
    'x;Quit[]', 'Set[x, 2]', 'x=5', '__import__("os")',
    'Expand[(x+1)^2]\nRun["bad"]',
])
def test_mathics_expression_allowlist_refuses_untrusted_side_effects(expression):
    with pytest.raises(PortableCapabilityRefusal):
        validate_mathics_expression(expression)


def test_safe_mathics_expression_remains_supported():
    assert validate_mathics_expression(' Expand[(x + 1)^2] ') == 'Expand[(x + 1)^2]'


def test_explicit_authority_is_empty_until_granted(tmp_path):
    authority=PortableCalculatorExternalAuthority(repo_root=ROOT, model='test:1')
    assert len(authority.handlers()) == 5
    broker=PortableWorkspaceBroker(tmp_path)
    for name in EXTERNAL_INTENTS:
        denied=broker.request(name, {'prompt':'2+3', 'context':{}})
        assert denied['ok'] is False
    assert authority.records == []


def test_real_backend_contract_is_built_from_executed_results_not_requested_claims(monkeypatch,tmp_path):
    authority=PortableCalculatorExternalAuthority(repo_root=ROOT, model='test:1')
    # This is an injected unit-test backend, explicitly NOT live proof.
    returned={'askModelForExpression':'2+3', 'askModelForGraphExpression':'2*x',
              'askModelForMathicsExpression':'Expand[(x + 1)^2]',
              'askResultQuestion':'5'}
    monkeypatch.setattr(authority,'_ollama',lambda intent,*_:returned[intent])
    monkeypatch.setattr(authority,'_mathics',lambda expr:'5' if expr=='2 + 3' else '1 + 2 x + x ^ 2')
    broker=PortableWorkspaceBroker(tmp_path,external=authority.handlers())
    for intent in ('askModelForExpression','askModelForGraphExpression','askModelForMathicsExpression',
                   'evaluateMathics','askResultQuestion'):
        payload={'context':{}, 'prompt':'example', 'expression':'2 + 3','question':'2+3'}
        response=broker.request(intent,payload)
        assert response['ok'] is True
        assert response['evidence']['intent'] == intent
        assert response['evidence']['verifiedExecution'] is True
        assert response['evidence']['outputSha256'] == _sha(response['text'])
        assert response['evidence']['backend'] == response['provider']
    assert len(authority.records)==5
    assert not broker.request('askModelForExpression',{'context':{},'prompt':''})['ok']
    assert len(authority.records)==5
    assert not broker.request('evaluateMathics',{'context':{},'expression':'Run["x"]'})['ok']
    assert len(authority.records)==5


def test_backend_error_never_generates_receipt(monkeypatch):
    authority=PortableCalculatorExternalAuthority(repo_root=ROOT,model='test:1')
    def error(*_):raise PortableCapabilityRefusal('model-server-down')
    monkeypatch.setattr(authority,'_ollama',error)
    with pytest.raises(PortableCapabilityRefusal,match='model-server-down'):
        authority.execute('askModelForExpression',{'prompt':'2+3','context':{}})
    assert authority.records==[]


def test_arbitrary_external_json_cannot_promote_full_certificate():
    # finalize_evidence cannot use provider receipts by themselves to invent
    # missing browser, workspace, and real backend evidence.
    evidence={'applications':[{'appId':'calculator','status':'pass',
        'externalProviderVerified':True,'externalProviderReceipts':[{'intent':name} for name in EXTERNAL_INTENTS],
        'externalSemanticChecks':list(EXTERNAL_INTENTS)}],
        'liveProviderProofRequested':True}
    finalize_evidence(evidence)
    assert evidence['status']=='refused'
    assert evidence['fullSemanticRuntimeProven'] is False
    assert evidence['passed'] is False


def test_plain_json_live_requested_without_runtime_authority_is_never_promoted():
    from tests.test_mcel_portable_intent_execution import _successful_probe_evidence
    evidence=_successful_probe_evidence()
    evidence['liveProviderProofRequested']=True
    calc=evidence['applications'][0]
    calc['executed']+=list(EXTERNAL_INTENTS)
    calc['safetyRefusals']=list(EXTERNAL_INTENTS)
    calc['externalInputRefusals']=list(EXTERNAL_INTENTS)
    calc['externalProviderVerified']=True
    calc['externalProviderReceipts']=[{'intent':i} for i in EXTERNAL_INTENTS]
    calc['externalSemanticChecks']=list(EXTERNAL_INTENTS)
    evidence['missingExternalProviderProof']=[]
    finalize_evidence(evidence)
    assert evidence['passed'] is False and evidence['fullSemanticRuntimeProven'] is False


def test_loopback_ollama_http_response_identity_and_redirect_refusal(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class FakeOllama(BaseHTTPRequestHandler):
        reply_model='test:1'
        redirect=False
        requests=[]
        def log_message(self,*args):pass
        def do_POST(self):
            if self.redirect:
                self.send_response(302)
                self.send_header('Location','https://example.org/escape')
                self.end_headers()
                return
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append((self.path,body))
            data=json.dumps({'model':self.reply_model,'done':True,'message':{'content':'2+3'}}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server=ThreadingHTTPServer(('127.0.0.1',0),FakeOllama)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        authority=PortableCalculatorExternalAuthority(repo_root=ROOT, model='test:1',
                       ollama_url=f'http://127.0.0.1:{server.server_address[1]}')
        assert authority._ollama('askModelForExpression','system','prompt')=='2+3'
        assert FakeOllama.requests[-1][0]=='/api/chat'
        assert FakeOllama.requests[-1][1]['model']=='test:1'
        FakeOllama.reply_model='other:1'
        with pytest.raises(PortableCapabilityRefusal,match='identity'):
            authority._ollama('askModelForExpression','system','prompt')
        FakeOllama.redirect=True
        with pytest.raises(PortableCapabilityRefusal):
            authority._ollama('askModelForExpression','system','prompt')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_live_verdict_requires_complete_external_inventory_and_separate_local_proof():
    # Synthetic verdict exercising logic only, NOT a certification run.
    from tests.test_mcel_portable_intent_execution import _successful_probe_evidence
    evidence=_successful_probe_evidence()
    evidence['liveProviderProofRequested']=True
    calc=evidence['applications'][0]
    calc['localValidated']=True
    calc['executed']+=sorted(EXTERNAL_INTENTS)
    calc['safetyRefusals']=sorted(EXTERNAL_INTENTS)
    calc['externalInputRefusals']=sorted(EXTERNAL_INTENTS)
    calc['externalProviderVerified']=True
    calc['externalProviderReceipts']=[{'intent':i} for i in sorted(EXTERNAL_INTENTS)]
    calc['externalSemanticChecks']=sorted(EXTERNAL_INTENTS)
    evidence['missingExternalProviderProof']=[]
    finalize_evidence(evidence, certified_runtime=True)
    assert evidence['status']=='pass'
    assert evidence['passed'] and evidence['fullSemanticRuntimeProven']
    evidence2=_successful_probe_evidence()
    evidence2['liveProviderProofRequested']=True
    calc=evidence2['applications'][0]
    calc['localValidated']=True
    calc['status']='refused'  # live provider outage after six verified local intents
    evidence2['missingExternalProviderProof']=list(EXTERNAL_INTENTS)
    finalize_evidence(evidence2,certified_runtime=True)
    assert evidence2['status']=='partial-pass'
    assert evidence2['portableLocalAndWorkspacePassed'] is True
    assert evidence2['passed'] is False
    assert evidence2['fullSemanticRuntimeProven'] is False


def test_strict_single_line_accepts_entire_fence_but_not_prose():
    from main_computer.mcel_portable_calculator_external import _single_line
    assert _single_line('```math\n2+3\n```','askModelForExpression') == '2+3'
    assert _single_line(' 2 + 3 ','askModelForExpression') == '2 + 3'
    with pytest.raises(PortableCapabilityRefusal, match='unbounded'):
        _single_line('The answer is:\n2+3', 'askModelForExpression')
    with pytest.raises(PortableCapabilityRefusal):
        _single_line('```math\n2+3\nRun["x"]\n```', 'askModelForExpression')


def test_model_format_retry_uses_real_transport_and_produces_a_single_receipt(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class FormatDrift(BaseHTTPRequestHandler):
        requests = []
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append(body)
            content = ('Here is your expression:\n2+3' if len(self.requests)==1
                       else '```math\n2+3\n```')
            raw = json.dumps({'model':'test:1', 'done':True,
                              'message':{'content':content}}).encode()
            self.send_response(200)
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server=ThreadingHTTPServer(('127.0.0.1',0),FormatDrift)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        authority=PortableCalculatorExternalAuthority(repo_root=ROOT,model='test:1',
                        ollama_url=f'http://127.0.0.1:{server.server_address[1]}')
        receipt=authority.execute('askModelForExpression',{'prompt':'add two and three','context':{}})
        assert receipt['text']=='2+3'
        assert receipt['evidence']['outputSha256']==_sha('2+3')
        assert len(authority.records)==1
        assert [a['accepted'] for a in authority.model_attempts]==[False,True]
        assert len(FormatDrift.requests)==2
        assert all(request['think'] is False for request in FormatDrift.requests)
        assert 'Retry the original request' in FormatDrift.requests[1]['messages'][1]['content']
        assert 'Here is your expression' not in str(authority.model_attempts)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_model_three_invalid_responses_refuse_without_receipt():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class AlwaysNarrates(BaseHTTPRequestHandler):
        calls=0
        def log_message(self,*args):pass
        def do_POST(self):
            type(self).calls+=1
            self.rfile.read(int(self.headers['Content-Length']))
            raw=json.dumps({'model':'test:1','done':True,
                            'message':{'content':'I think:\n2+3'}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),AlwaysNarrates)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        authority=PortableCalculatorExternalAuthority(repo_root=ROOT,model='test:1',
                        ollama_url=f'http://127.0.0.1:{server.server_address[1]}')
        with pytest.raises(PortableCapabilityRefusal,match='after-3-attempts'):
            authority.execute('askModelForExpression',{'prompt':'add two and three','context':{}})
        assert AlwaysNarrates.calls==3
        assert len(authority.records)==0
        assert len(authority.model_attempts)==3
        assert all(not a['accepted'] for a in authority.model_attempts)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_no_retry_for_unsafe_mathics_identifier():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    class Unsafe(BaseHTTPRequestHandler):
        calls=0
        def log_message(self,*args):pass
        def do_POST(self):
            type(self).calls+=1
            self.rfile.read(int(self.headers['Content-Length']))
            raw=json.dumps({'model':'test:1','done':True,
                            'message':{'content':'Run[1]'}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Unsafe)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        authority=PortableCalculatorExternalAuthority(repo_root=ROOT,model='test:1',
                        ollama_url=f'http://127.0.0.1:{server.server_address[1]}')
        with pytest.raises(PortableCapabilityRefusal,match='mathics-expression-unsafe'):
            authority.execute('askModelForMathicsExpression',{'prompt':'expand','context':{}})
        assert Unsafe.calls==1
        assert not authority.records
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.mark.parametrize('expression', [
    '2*x', 'x', 'x^2+3*x-1', 'sin(x)', 'sqrt(x+1)',
    '2*(x+1)', 'min(x,2)', 'max(x,0)', '2.5e-3*x',
])
def test_graph_preflight_accepts_executable_calculator_forms(expression):
    from main_computer.mcel_portable_calculator_external import validate_graph_expression, _single_line
    assert validate_graph_expression(expression) == expression
    assert _single_line(expression, 'askModelForGraphExpression') == expression


@pytest.mark.parametrize('expression', [
    '2x', '2(x+1)', '(x+1)(x-1)', 'x(2)', 'sin(2x)', '2sin(x)',
    'x**2', 'x//2', 'unknown(x)', 'Run(x)', 'min()', 'round(x,2)',
    'x;1', 'x[0]', 'x=1', 'x\n2', '__import__(x)', 'x + .', '1e309*x',
])
def test_graph_preflight_refuses_invalid_or_implicit_multiplication(expression):
    from main_computer.mcel_portable_calculator_external import _single_line
    with pytest.raises(PortableCapabilityRefusal):
        _single_line(expression, 'askModelForGraphExpression')


def test_graph_preflight_matches_pure_js_graph_parser_on_regression_cases():
    # A syntax preflight is not evidence of runtime semantics. Compare it to
    # the exact pure JS domain parser that the browser later samples.
    import json, shutil, subprocess
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    expressions=['2*x','x','x^2+3*x-1','sin(x)','sqrt(x+1)',
                 '2*(x+1)','min(x,2)','2.5e-3*x','2x','2(x+1)',
                 '(x+1)(x-1)','sin(2x)','x**2','x//2','unknown(x)']
    core=ROOT/'main_computer/web/applications/scripts/calculator-core.js'
    script=('const c=require(process.argv[1]);'
            'const xs=JSON.parse(process.argv[2]);'
            'console.log(JSON.stringify(xs.map(x=>{try{c.parseCalculatorGraphExpression(x);'
            'return true}catch(e){return false}})))')
    proc=subprocess.run(['node','-e',script,str(core),json.dumps(expressions)],
                        text=True,capture_output=True,timeout=10,check=True)
    js=json.loads(proc.stdout)
    from main_computer.mcel_portable_calculator_external import validate_graph_expression
    py=[]
    for expr in expressions:
        try:
            validate_graph_expression(expr)
            py.append(True)
        except PortableCapabilityRefusal:
            py.append(False)
    assert py==js


def test_graph_implicit_multiplication_retries_real_backend_without_forged_receipt():
    import json, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class ImplicitThenExplicit(BaseHTTPRequestHandler):
        requests=[]
        def log_message(self,*args):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.requests.append(request)
            content='2x' if len(self.requests)==1 else '2*x'
            response=json.dumps({'model':'test:1','done':True,
                                 'message':{'content':content}}).encode()
            self.send_response(200)
            self.send_header('Content-Length',str(len(response)))
            self.end_headers()
            self.wfile.write(response)
    server=ThreadingHTTPServer(('127.0.0.1',0),ImplicitThenExplicit)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        authority=PortableCalculatorExternalAuthority(repo_root=ROOT,model='test:1',
                         ollama_url=f'http://127.0.0.1:{server.server_address[1]}')
        receipt=authority.execute('askModelForGraphExpression',
                                  {'prompt':'twice x','context':{}})
        assert receipt['text']=='2*x'
        assert receipt['evidence']['outputSha256']==_sha('2*x')
        assert len(authority.records)==1
        assert [a['accepted'] for a in authority.model_attempts]==[False,True]
        assert authority.model_attempts[0]['rejection']=='model-graph-expression-not-parseable'
        assert '2*x' in ImplicitThenExplicit.requests[1]['messages'][1]['content']
        assert '2x' not in str(authority.model_attempts)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_graph_stubborn_implicit_multiplication_stays_fail_closed(monkeypatch):
    import json, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class AlwaysImplicit(BaseHTTPRequestHandler):
        calls=0
        def log_message(self,*args):pass
        def do_POST(self):
            type(self).calls+=1
            self.rfile.read(int(self.headers['Content-Length']))
            response=json.dumps({'model':'test:1','done':True,
                                 'message':{'content':'2x'}}).encode()
            self.send_response(200)
            self.send_header('Content-Length',str(len(response)))
            self.end_headers();self.wfile.write(response)
    server=ThreadingHTTPServer(('127.0.0.1',0),AlwaysImplicit)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        authority=PortableCalculatorExternalAuthority(repo_root=ROOT,model='test:1',
                        ollama_url=f'http://127.0.0.1:{server.server_address[1]}')
        with pytest.raises(PortableCapabilityRefusal,match='after-3-attempts'):
            authority.execute('askModelForGraphExpression',{'prompt':'twice x','context':{}})
        assert AlwaysImplicit.calls==3
        assert not authority.records
        assert all(not a['accepted'] for a in authority.model_attempts)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
