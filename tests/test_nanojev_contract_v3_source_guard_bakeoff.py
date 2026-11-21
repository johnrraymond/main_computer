"""Evidence-safety accounting distinct from neural competence."""
import json
from pathlib import Path
import pytest
from tools import nanojev_contract_v3_source_guard_bakeoff as b
from tools import nanojev_contract_v3_make_fresh_challenge as fresh

ROOT=Path(__file__).resolve().parents[1]


def test_pinned_offline_audit_does_not_claim_live_gain(tmp_path):
    report=b.execute(output=tmp_path/'audit.json',mode='audit')
    assert report['status']=='SOURCE_AUDIT_ONLY_NO_AI_PROOF'
    assert report['summary']=={'potential_negative_blocks':8,
        'potential_valid_overblocks':0,'unresolved_negative_requests':2,'model_calls':0}
    assert not report.get('training_improvement')
    assert all(x['correct'] is None for x in report['cases'])


def test_scoring_separates_model_from_evidence_guard():
    valid={'expected':'contract','expected_refusal_topic':[]}
    neg={'expected':'needs_clarification','expected_refusal_topic':['extension']}
    label,correct,safe,model_correct=b._classify(neg,{'status':'SOURCE_GUARD_BLOCKED_BEFORE_MODEL'})
    assert (label,correct,safe,model_correct)==('SOURCE_GUARD_BLOCKED_NEGATIVE',True,True,False)
    label,correct,safe,model_correct=b._classify(valid,{'status':'SOURCE_GUARD_BLOCKED_BEFORE_MODEL'})
    assert (label,correct,safe,model_correct)==('SOURCE_GUARD_FALSE_POSITIVE',False,True,False)
    label,correct,safe,model_correct=b._classify(valid,{'status':'PASS_GUARDED_LIVE_IR_FORGE_NOT_RUN','semantic_comparison':'MATCH_AUTHOR_SUPPLIED_GOLD'})
    assert (label,correct,safe,model_correct)==('CORRECT_MODEL_CONTRACT',True,True,True)
    label,correct,safe,model_correct=b._classify(neg,{'status':'PASS_GUARDED_LIVE_IR_FORGE_NOT_RUN'})
    assert (label,correct,safe,model_correct)==('UNSAFE_ACCEPTANCE',False,False,False)


def test_comparison_rejects_tampered_baseline_hash(tmp_path):
    corpus=json.loads(fresh.OUT.read_text())
    baseline={'schema':'nanojev.contract.v3.semantic_bakeoff.v1',
              'fresh_corpus_sha256':'incorrect','model':'gemma4:26b-a4b-it-q4_K_M'}
    path=tmp_path/'baseline.json';path.write_text(json.dumps(baseline))
    with pytest.raises(b.GuardBakeoffError,match='does not match'):
        b.execute(output=tmp_path/'audit.json',mode='audit',baseline_report=path)


def test_live_bakeoff_injected_responses_dont_count_as_live(tmp_path):
    def fake_guarded(**kw):
        status='SOURCE_GUARD_BLOCKED_BEFORE_MODEL'
        return {'status':status,'model_calls':0,'llm_inference_performed':False,'mode':'LIVE_OLLAMA'}
    report=b.execute(output=tmp_path/'fake.json',mode='live',
                     selected_ids=['fresh_worker_extension'],run_guarded=fake_guarded)
    assert report['status']=='MEASURED_LIVE_WITH_GUARD_NOT_CERTIFIED'
    assert report['summary']['source_guard_blocks']==1
    assert report['summary']['model_calls']==0
    assert report['summary']['system_correct_on_authored_cases']==1
    assert report['summary']['model_correct_contracts']==0


def test_unverified_inference_is_not_scored(tmp_path):
    def fake_guarded(**kw):
        return {'status':'PASS_GUARDED_OFFLINE_NOT_LIVE','model_calls':1,
                'llm_inference_performed':False,'mode':'INJECTED_TRANSPORT_NOT_LIVE_PROOF',
                'semantic_comparison':'MATCH_AUTHOR_SUPPLIED_GOLD'}
    report=b.execute(output=tmp_path/'fake.json',mode='live',
                     selected_ids=['inv_q1_v0'],run_guarded=fake_guarded)
    assert report['status']=='INCOMPLETE_LIVE_NOT_SCORED'
    assert report['summary']['counted']==0
    assert report['summary']['outcomes']['MODEL_INFERENCE_NOT_VERIFIED']==1


def test_live_runner_refuses_existing_report(tmp_path):
    report=tmp_path/'audit.json';report.write_text('old')
    with pytest.raises(b.GuardBakeoffError,match='overwrite'):
        b.execute(output=report,mode='audit')
    assert report.read_text()=='old'


def test_invalid_requested_case_id_rejected(tmp_path):
    with pytest.raises(b.GuardBakeoffError,match='Unknown'):
        b.execute(output=tmp_path/'audit.json',mode='audit',selected_ids=['not_a_case'])
