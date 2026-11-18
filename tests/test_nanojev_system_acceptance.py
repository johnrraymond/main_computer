"""Offline protocol/integration orchestration tests; NOT CUDA acceptance."""
from __future__ import annotations

from argparse import Namespace
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import nanojev_system_contract_service as service
from tools import nanojev_system_acceptance as acceptance
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding


def test_fixture_rebuild_is_exhaustive_without_relabeling():
    rows = acceptance.cases_from_existing_frozen_fixtures()
    assert len(rows) == 1042
    assert sum(x['cohort'] == 'historical' and x['policy'] == 'supported' for x in rows) == 216
    assert sum(x['cohort'] == 'fresh' and x['policy'] == 'supported' for x in rows) == 288
    assert sum(x['cohort'] == 'fresh' and x['family'] not in ('known_control', 'external_legal', 'nonoperative') for x in rows) == 240
    assert sum(x['cohort'] == 'fresh' and x['policy'] == 'abstain' for x in rows) == 154
    assert len(set(x['id'] for x in rows)) == 1042


@pytest.mark.parametrize('payload,code', [
    ({}, 'INVALID_REQUEST_ID'),
    ({'request_id':'a','operation':'bogus','text':'hello'}, 'INVALID_REQUEST_SHAPE'),
    ({'request_id':'a','operation':'extract_contract','text':[]}, 'INVALID_TEXT'),
    ({'request_id':'a','operation':'extract_contract','text':''}, 'INVALID_TEXT'),
    ({'request_id':'a','operation':'extract_contract','text':'a\x00b'}, 'INVALID_TEXT'),
    ({'request_id':'a','operation':'extract_contract','text':'a'*12001}, 'INVALID_TEXT'),
    ({'request_id':'a','operation':'extract_contract','text':'a','gold':['invalid']}, 'INVALID_REQUEST_SHAPE'),
])
def test_request_validation_fails_closed(payload, code):
    got = service.process(payload, {})
    assert got['decision'] == 'error'
    assert code in got['reasons']
    assert got['relations'] == []


def test_multiline_refused_without_encoder_call():
    got = service.process({'request_id':'a','operation':'extract_contract','text':'first\nsecond'}, {})
    assert got['decision'] == 'abstain'
    assert got['reasons'] == ['UNSUPPORTED_DOCUMENT_SEGMENTATION']


def test_grounding_with_no_gold(monkeypatch):
    model = object()
    predicted = [('Tarin', 'Bela', 'ledger', 'the audit is complete', 'payment is withheld', -1)]
    captured = {}
    def predict(rows, supplied_model, encoder, base, rf):
        captured['rows'] = rows
        assert supplied_model is model
        return [predicted]
    def ground(text, pred, base, refs, *, repair_mode):
        assert repair_mode is True
        assert pred == predicted
        return True, pred, (), {}
    monkeypatch.setattr(service.neg, 'predict_baseline', predict)
    monkeypatch.setattr(service.neg, 'grounded_document', ground)
    monkeypatch.setattr(service.repair, 'contradiction', lambda pred, base: False)
    rt = {'model':model,'encoder':object(),'base':{},'rf':{'reference_markers':[]}}
    text = 'The actor is Tarin; recipient is Bela; legal duty.'
    got = service.process({'request_id':'xyz','operation':'extract_contract','text':text}, rt)
    assert got['decision'] == 'accepted'
    assert got['relations'][0] == dict(zip(binding.FIELDS, predicted[0]))
    assert got['source_sha256'] == sha256(text.encode('utf8')).hexdigest()
    assert got['contradiction'] is False
    assert captured['rows'] == [{'text':text}]


def test_unsupported_parser_never_returns_graph(monkeypatch):
    monkeypatch.setattr(service.neg, 'predict_baseline', lambda *args: (_ for _ in ()).throw(ValueError('invalid')))
    rt = {'model':object(), 'encoder':object(), 'base':{}, 'rf':{}}
    got = service.process({'request_id':'a', 'operation':'extract_contract', 'text':'unknown'}, rt)
    assert got['decision'] == 'abstain'
    assert not got['relations']


def test_line_protocol_no_extra_stdout():
    buffer = StringIO()
    service.serve({}, StringIO('{bad\n'+json.dumps({'request_id':'q','operation':'extract_contract','text':''})+'\n'), buffer)
    rows = [json.loads(x) for x in buffer.getvalue().splitlines()]
    assert len(rows) == 2
    assert rows[0]['reasons'] == ['INVALID_REQUEST_ID']
    assert rows[1]['request_id'] == 'q'
    assert rows[1]['decision'] == 'error'


def test_score_blocks_bad_acceptance():
    cases = [{'id':'a', 'text':'A', 'cohort':'fresh','family':'new_grammar',
              'kind':'t','policy':'supported','gold':[('Tarin','Bela','ledger','condition','consequence',1)]},
             {'id':'b','text':'B','cohort':'fresh','family':'nonoperative',
              'kind':'u','policy':'abstain','gold':None}]
    responses = [{'schema':service.SCHEMA,'request_id':'a','source_sha256':sha256(b'A').hexdigest(),
                  'decision':'accepted','relations':[dict(zip(binding.FIELDS, ['Mira','Bela','ledger','condition','consequence',1]))],
                  'contradiction':False},
                 {'schema':service.SCHEMA,'request_id':'b','source_sha256':sha256(b'B').hexdigest(),
                  'decision':'accepted','relations':[dict(zip(binding.FIELDS,['Tarin','Bela','ledger','condition','consequence',1]))],
                  'contradiction':False}]
    result = acceptance.score(cases, {'responses':responses,'exit_code':0})
    assert result['errors_total'] == 2
    assert result['by_family']['fresh/new_grammar']['false_confident_wrong'] == 1
    assert result['by_family']['fresh/nonoperative']['unsafe_accepted'] == 1


def test_score_correct_abstention_does_not_count_as_comprehension():
    cases = [{'id':'a', 'text':' A ', 'cohort':'fresh','family':'new_vocabulary',
              'kind':'t','policy':'supported','gold':[('a','b','c','d','e',1)]}]
    responses = [{'schema':service.SCHEMA,'request_id':'a', 'source_sha256':sha256(b'A').hexdigest(),
                  'decision':'abstain','relations':[],'contradiction':None,'reasons':['UNSUPPORTED']}]
    result = acceptance.score(cases, {'responses':responses,'exit_code':0})
    assert result['errors_total'] == 0
    assert result['by_family']['fresh/new_vocabulary'].get('correct_accepted', 0) == 0
    assert result['by_family']['fresh/new_vocabulary']['abstain'] == 1


def test_fault_suite_and_recovery():
    requested = acceptance.fault_requests()
    assert len(requested) == len(set(x['request_id'] for x in requested))
    answers = [{'request_id':r['request_id'],'decision':('abstain' if r['request_id']=='fault:multiline' else 'error'),
                'relations':[]} for r in requested]
    assert acceptance.verify_faults(requested, {'exit_code':0,'responses':answers}) == []
    answers[0]['decision'] = 'accepted'
    assert acceptance.verify_faults(requested, {'exit_code':0,'responses':answers})


def test_no_overwrite_even_for_blocked_report(tmp_path):
    report = tmp_path / 'report.json'
    report.write_text('{}', encoding='utf8')
    with pytest.raises(FileExistsError, match='SYSTEM_REPORT_EXISTS_REFUSED'):
        acceptance.run(Namespace(output=report, production_command_json=None))


def test_production_command_cannot_be_candidate():
    assert 'nanojev_system_contract_service' in ' '.join(acceptance.argv_for_candidate('cuda', Path('x'), Path('y')))


def test_parser_defaults_require_real_full_acceptance():
    args = acceptance.parser().parse_args([])
    assert args.production_command_json is None
    assert args.device == 'cuda'
    assert args.timeout >= 300



def synthetic_evidence_reports(tmp_path):
    from tools import nanojev_sealed_overlap_external_generalization_bakeoff as ext
    from tools import nanojev_sealed_overlap_negative_consistency_bakeoff as neg
    from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
    from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
    from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
    from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
    from tools import nanojev_sealed_overlap_scope_obligation_bakeoff as scope
    paths={'base_fixture_sha256':binding.FIXTURE,'stress_fixture_sha256':stress.FIXTURE,
           'interface_fixture_sha256':interface.FIXTURE,'repair_fixture_sha256':repair.FIXTURE,
           'blind_fixture_sha256':blind.FIXTURE,'scope_fixture_sha256':scope.FIXTURE,
           'new_fixture_sha256':neg.FIXTURE}
    aggregate={'documents':408,'supported_accepted':216,'unsafe_accepted':0,
               'false_confident_wrong':0}
    prior={'schema':neg.SCHEMA,'status':neg.STATUS,
           'acceptance':{'no_model_weight_updates':True,'no_final_based_model_selection':True},
           'design':{'view':'final:mean','frozen_training_examples':648},
           'new_holdout':{'arms':{'evidence_repair':{'aggregate':aggregate}}},
           'historical_diagnostic':{'arms':{'evidence_repair':{'aggregate':{'supported_accepted':216,'false_confident_wrong':0}}}},
           'sources':{k:sha256(v.read_bytes()).hexdigest() for k,v in paths.items()}}
    first=tmp_path/'synthetic_prereq.json'
    first.write_text(json.dumps(prior),encoding='utf8')
    external={'schema':ext.SCHEMA,'status':ext.STATUS,
              'source_pins':{'module_sha256':ext.fingerprint_preflight(),
                             'fixture_sha256':ext.FROZEN_HOLDOUT_SHA256,
                             'prior_report_sha256':sha256(first.read_bytes()).hexdigest()},
              'historical_canary':{'matches_prior_216_of_216':True},
              'outcome':{'fresh_valid_generalization_certified':False}}
    second=tmp_path/'synthetic_external.json'
    second.write_text(json.dumps(external),encoding='utf8')
    return first,second


def test_batch_matches_one_at_a_time_for_actual_frozen_pipeline(tmp_path):
    from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder
    # Actual frozen code and mathematical heads; only the encoder is mocked.
    base = binding.load_fixture(binding.FIXTURE)
    prior, ext_report = synthetic_evidence_reports(tmp_path)
    rt = service.frozen_runtime('cpu', prior, ext_report,
                                encoder=synthetic_encoder(base))
    rows = acceptance.cases_from_existing_frozen_fixtures()
    selected = [rows[0], rows[12], rows[245], rows[600], rows[612], rows[660], rows[-1]]
    reqs = [acceptance.make_request(r) for r in selected] + acceptance.fault_requests()
    single = [service.process(x, rt) for x in reqs]
    batched = service.process_batch(reqs, rt)
    assert batched == single
    stream = StringIO()
    service.serve_batch(rt, StringIO(''.join(json.dumps(x)+'\n' for x in reqs)), stream, chunk_size=4)
    assert [json.loads(x) for x in stream.getvalue().splitlines()] == single


def test_expert_corpus_rejects_reused_and_unreviewed_material(tmp_path):
    rows = acceptance.cases_from_existing_frozen_fixtures()
    example = {'schema':'nanojev.system-expert-corpus.v1',
               'attestation':{'reviewer':'Independent Reviewer','origin':'Private authored draft',
                              'independently_authored':True},
               'cases':[{'id':str(i),'text':f'Independent clause {i}.',
                         'expected_relations':None if i % 2 else [dict(zip(binding.FIELDS,
                                      ['A','B','C','D','E',1]))]} for i in range(24)]}
    path = tmp_path/'gold.json'
    path.write_text(json.dumps(example),encoding='utf8')
    result=acceptance.load_expert_cases(path,rows)
    assert len(result)==24 and sum(x['policy']=='supported' for x in result)==12
    example['cases'][0]['text']=rows[0]['text']
    path.write_text(json.dumps(example),encoding='utf8')
    with pytest.raises(ValueError,match='DUPLICATE_OR_KNOWN_TEXT'):
        acceptance.load_expert_cases(path,rows)
    example['cases'][0]['text']='Independent clause 0.'
    example['attestation']['independently_authored']=False
    path.write_text(json.dumps(example),encoding='utf8')
    with pytest.raises(ValueError,match='EXPERT_CORPUS_PROVENANCE'):
        acceptance.load_expert_cases(path,rows)


def test_full_orchestrator_is_blocked_when_production_and_expert_gold_unbound(tmp_path):
    from argparse import Namespace
    from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder
    prior, external = synthetic_evidence_reports(tmp_path)
    base = binding.load_fixture(binding.FIXTURE)
    rt = service.frozen_runtime('cpu',prior,external,encoder=synthetic_encoder(base))
    calls = []
    def simulate_process(argv, requests, *, timeout, cwd):
        calls.append(len(requests))
        replies = service.process_batch(requests, rt)
        return {'argv':argv, 'elapsed_s':0.1, 'exit_code':0,
                'responses':replies, 'response_count':len(replies), 'stderr_tail':''}
    args=Namespace(device='cpu',prior_report=prior,external_report=external,
                   timeout=30,production_command_json=None,expert_corpus=None,
                   output=tmp_path/'report.json')
    report=acceptance.run(args,invoke=simulate_process)
    assert report['status']==acceptance.STATUS_BLOCK
    assert 'ZERO_NOVEL_LANGUAGE_COVERAGE' in report['blockers']
    assert 'REAL_APPLICATION_ENTRYPOINT_NOT_CONNECTED' in report['blockers']
    assert 'INDEPENDENT_EXPERT_GOLD_NOT_PROVIDED' in report['blockers']
    assert report['checks']['candidate_graphs']['passed_protocol'] is True
    assert report['checks']['restart_repeatability']['pass'] is True
    assert report['checks']['fault_isolation']['pass'] is True
    assert report['checks']['source_immutability']['pass'] is True
    assert report['capability']['historical_supported_accepted']==216
    assert report['capability']['novel_supported_accepted']==0
    assert calls==[1042,8,8]
    assert json.loads(args.output.read_text())['status'] == acceptance.STATUS_BLOCK


def test_unicode_provenance_is_identical_across_actual_subprocess(monkeypatch, tmp_path):
    """Force a hostile legacy locale and confirm JSONL remains byte-accurate."""
    monkeypatch.setenv('PYTHONIOENCODING', 'cp1252:strict')
    monkeypatch.setenv('PYTHONUTF8', '0')
    text = 'This paragraph does not give the Government any right to audit the Contractor’s records.'
    code = (
        'import sys,json,hashlib; '
        'r=json.loads(sys.stdin.readline()); '
        'print(json.dumps({"schema":"nanojev.system-contract-jsonl.v1",'
        '"request_id":r["request_id"],"decision":"abstain",'
        '"source_sha256":hashlib.sha256(r["text"].strip().encode("utf8")).hexdigest(),'
        '"relations":[],"contradiction":None,"reasons":["UNSUPPORTED"]}))'
    )
    request = {'request_id': 'external:far52_212_4_audit',
               'operation': 'extract_contract', 'text': text}
    result = acceptance.invoke_jsonl([sys.executable, '-c', code], [request],
                                      timeout=10, cwd=tmp_path)
    assert result['exit_code'] == 0
    assert result['response_count'] == 1
    assert result['responses'][0]['source_sha256'] == sha256(text.encode('utf8')).hexdigest()
    case = {'id': request['request_id'], 'text':text, 'cohort':'fresh',
            'family':'external_legal', 'kind':'external', 'policy':'abstain', 'gold':None}
    evaluation = acceptance.score([case], result)
    assert evaluation['passed_protocol'] is True
    assert evaluation['errors_total'] == 0


def test_acceptance_fixture_contains_only_one_nonascii_case():
    """The reported failure was isolated to the only UTF-8-sensitive case."""
    cases = acceptance.cases_from_existing_frozen_fixtures()
    nonascii = [x for x in cases if not x['text'].isascii()]
    assert [x['id'] for x in nonascii] == ['external:far52_212_4_audit']


def test_invoke_jsonl_surfaces_bad_child_stdout_as_failure(tmp_path):
    script = 'import sys; sys.stdout.buffer.write(bytes([0xff])+b"\\n")'
    result = acceptance.invoke_jsonl([sys.executable, '-c', script], [], timeout=10, cwd=tmp_path)
    assert result.get('startup_failure') is True
    assert result['exit_code'] is None
    assert 'UnicodeDecodeError' in result['stderr_tail']
