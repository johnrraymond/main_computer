"""Native NanoJev application-path tests; mock checkpoint, real HTTP handler/provider."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from threading import Thread

import pytest

from tools import nanojev_live_application_probe as live
from tools import nanojev_system_acceptance as acceptance

SHA = 'c' * 64


@pytest.fixture()
def native_server():
    # Exercise the *repository's* CLEF server HTTP handler and real provider,
    # while substituting the loaded weights only. No model downloads or GPU.
    source = Path(__file__).resolve().parents[1] / 'docker/nanojev/clef_service.py'
    spec = importlib.util.spec_from_file_location('tested_clef_service', source)
    service = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = service
    spec.loader.exec_module(service)

    class FakeCheckpoint:
        operations = []
        def health(self):
            return {'ready': True, 'model_loaded_once': True, 'model_family': 'nanojev-clef',
                    'release_manifest_sha256': SHA, 'release_name': 'mock-only'}
        def model_catalog(self):
            return {'schema': service.MODEL_CATALOG_SCHEMA, 'ready': True,
                    'models': [{'id': service.MODEL_ID, 'ready': True,
                                'operations': list(service.OPERATIONS),
                                'checkpoint': {'release_manifest_sha256': SHA,
                                               'release_name': 'mock-only'}}]}
        def infer(self, req):
            self.operations.append(req['operation'])
            assert req['checkpoint_sha256'] == SHA
            assert req['operation'] == service.OPERATIONS[0]
            assert req['schema'] == service.INFERENCE_REQUEST_SCHEMA
            result_model = {'family': service.MODEL_ID, 'release_name': 'mock-only',
                            'release_manifest_sha256': SHA}
            result = {'model': result_model,
                      'states': [{'id': 'native-acceptance-01', 'answers': {
                          'receipt': {'type': 'boolean', 'value': True,
                                      'probabilities': {'false': .125, 'true': .875}}}}]}
            return {'schema': service.INFERENCE_RESULT_SCHEMA, 'model': service.MODEL_ID,
                    'operation': req['operation'], 'checkpoint': result_model, 'result': result}

    rt = FakeCheckpoint()
    server = service.ClefServer(('127.0.0.1', 0), rt)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), rt
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


@pytest.mark.parametrize('url', [
    'http://example.com:9765', 'https://127.0.0.1:9765',
    'http://127.0.0.1:9765/api/infer', 'http://127.0.0.1:9765?x=1',
    'http://user:pwd@127.0.0.1:9765', 'http://127.0.0.1:9765/#frag',
    'file:///tmp/foo', 'http://127.0.0.1',
    'http://8.8.8.8:9765', 'http://127.0.0.1:99999',
])
def test_refuse_nonlocal_or_nonorigin_endpoints(url):
    with pytest.raises(ValueError, match='NANOJEV_LIVE_'):
        live.validate_local_url(url)


def test_actual_native_handler_and_main_computer_provider(native_server):
    origin, fake = native_server
    result = live.run_probe(base_url=origin, timeout_s=5)
    assert result['status'] == 'PASS_NATIVE_CLEF_APPLICATION_BOUND'
    assert result['checkpoint_sha256'] == SHA
    assert result['requests_executed'] == 2
    assert fake.operations == ['nanojev.clef.evaluate.v1'] * 2
    assert result['native_answer_is_not_independently_gold_scored'] is True
    assert result['contract_extraction_production_bound'] is False
    assert result['unsupported_contract_operation_refused'] is True
    assert result['service_restart_tested'] is False
    assert len(result['source_sha256']['main_computer/providers/nanojev.py']) == 64


def test_missing_native_service_is_not_a_pass():
    from main_computer.providers.nanojev import NanoJevProviderError
    with pytest.raises(NanoJevProviderError):
        # Connection refused; service isn't made up or silently emulated.
        live.run_probe(base_url='http://127.0.0.1:1', timeout_s=.2)


def test_same_process_repetition_detects_divergent_native_answers(native_server, monkeypatch):
    origin, rt = native_server
    base = rt.infer
    def changed_second(request):
        got = base(request)
        if len(rt.operations) == 2:
            got['result']['states'][0]['answers']['receipt']['probabilities'] = {
                'false': .45, 'true': .55}
        return got
    monkeypatch.setattr(rt, 'infer', changed_second)
    with pytest.raises(RuntimeError, match='REPEATABILITY_FAILED'):
        live.run_probe(base_url=origin, timeout_s=5)


def test_cli_exposes_bounded_native_probe_without_changing_default():
    old = acceptance.parser().parse_args([])
    assert old.live_nanojev_url is None
    assert old.production_command_json is None
    args = acceptance.parser().parse_args([
        '--live-nanojev-url', 'http://127.0.0.1:9765', '--live-nanojev-timeout','30'])
    assert args.live_nanojev_url == live.DEFAULT_URL
    assert args.live_nanojev_timeout == 30


def test_report_flag_does_not_claim_clef_is_contract_extractor():
    """Guard the production-vs-CLEF distinction in the actual acceptance code."""
    code = Path(acceptance.__file__).read_text(encoding='utf8')
    assert "'CONTRACT_EXTRACTION_ENTRYPOINT_NOT_CONNECTED_CLEF_ONLY'" in code
    assert "'NATIVE_CLEF_BOUND_CONTRACT_EXTRACTOR_UNBOUND'" in code
    assert "'LIVE_NANOJEV_APPLICATION_PROBE_FAILED'" in code
    assert "'REAL_APPLICATION_ENTRYPOINT_NOT_CONNECTED'" in code


def _synthetic_acceptance(tmp_path, *, live_url):
    """Use real orchestration with mock encoder while native CLEF HTTP is exercised."""
    from argparse import Namespace
    from tools import nanojev_system_contract_service as service
    from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
    from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder
    from tests.test_nanojev_system_acceptance import synthetic_evidence_reports
    prior, external = synthetic_evidence_reports(tmp_path)
    base = binding.load_fixture(binding.FIXTURE)
    runtime = service.frozen_runtime('cpu', prior, external,
                                     encoder=synthetic_encoder(base))
    def simulate(argv, requests, *, timeout, cwd):
        responses = service.process_batch(requests, runtime)
        return {'argv': argv, 'exit_code': 0, 'responses': responses,
                'response_count': len(responses), 'stderr_tail': '', 'elapsed_s': 0.1}
    args = Namespace(device='cpu', prior_report=prior, external_report=external,
                     timeout=120, production_command_json=None,
                     expert_corpus=None, live_nanojev_url=live_url,
                     live_nanojev_timeout=5, output=tmp_path/'acceptance.json')
    result = acceptance.run(args, invoke=simulate)
    return result


def test_acceptance_report_proves_native_binding_but_blocks_contract_extraction(native_server, tmp_path):
    url, runtime = native_server
    result = _synthetic_acceptance(tmp_path, live_url=url)
    assert result['status'] == acceptance.STATUS_BLOCK
    assert result['checks']['live_nanojev_application']['status'] == 'PASS_NATIVE_CLEF_APPLICATION_BOUND'
    assert result['scope']['production_integration'] == 'NATIVE_CLEF_BOUND_CONTRACT_EXTRACTOR_UNBOUND'
    assert 'CONTRACT_EXTRACTION_ENTRYPOINT_NOT_CONNECTED_CLEF_ONLY' in result['blockers']
    assert 'REAL_APPLICATION_ENTRYPOINT_NOT_CONNECTED' not in result['blockers']
    assert 'ZERO_NOVEL_LANGUAGE_COVERAGE' in result['blockers']
    assert 'INDEPENDENT_EXPERT_GOLD_NOT_PROVIDED' in result['blockers']
    assert result['checks']['candidate_graphs']['passed_protocol'] is True
    assert result['capability']['historical_supported_accepted'] == 216
    assert result['capability']['novel_supported_accepted'] == 0
    assert runtime.operations == ['nanojev.clef.evaluate.v1'] * 2
    assert json.loads((tmp_path/'acceptance.json').read_text())['status'] == acceptance.STATUS_BLOCK


def test_unreachable_live_service_adds_blocker_without_faking_binding(tmp_path):
    result = _synthetic_acceptance(tmp_path, live_url='http://127.0.0.1:1')
    assert result['status'] == acceptance.STATUS_BLOCK
    assert result['checks']['live_nanojev_application']['status'] == 'FAILED'
    assert 'LIVE_NANOJEV_APPLICATION_PROBE_FAILED' in result['blockers']
    assert 'REAL_APPLICATION_ENTRYPOINT_NOT_CONNECTED' in result['blockers']
    assert result['scope']['production_integration'] == 'NOT_BOUND'


def test_native_cli_fails_closed_if_no_service(tmp_path, capsys):
    path = tmp_path / 'probe.json'
    rc = live.main(['--base-url', 'http://127.0.0.1:1', '--timeout', '.2', '--output', str(path)])
    assert rc == 2
    result = json.loads(path.read_text())
    assert result['status'] == 'FAILED'
    assert result['contract_extraction_production_bound'] is False
    with pytest.raises(FileExistsError, match='REPORT_EXISTS_REFUSED'):
        live.main(['--base-url', 'http://127.0.0.1:1', '--output', str(path)])


def test_native_cli_uses_actual_main_computer_route(native_server, tmp_path):
    origin, runtime = native_server
    report = tmp_path/'probe.json'
    rc = live.main(['--base-url', origin, '--timeout', '5', '--output', str(report)])
    assert rc == 0
    obj = json.loads(report.read_text())
    assert obj['status'] == 'PASS_NATIVE_CLEF_APPLICATION_BOUND'
    assert 'main_computer.router.MainComputer.build/solve' in obj['application_entrypoint']
    assert obj['contract_extraction_production_bound'] is False
    assert runtime.operations == ['nanojev.clef.evaluate.v1'] * 2
