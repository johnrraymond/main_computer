"""Baked scoring regressions use injected run outcomes, never claim live model proof."""
from tools import nanojev_contract_v3_semantic_bakeoff as bo
from tools import nanojev_contract_v3_make_fresh_challenge as fresh
import pytest


def test_full_pairwise_score_separates_model_correctness_from_safety(tmp_path):
    seen=[]
    def baseline(**kwargs):
        assert kwargs['retry_false_clarification'] is False
        assert kwargs['forge'] is False
        assert kwargs['request'].read_text().strip()
        seen.append(('baseline',kwargs['request'].parent.parent.name))
        return {'status':'PASS_LIVE_HEAD_V3_IR_FORGE_NOT_RUN','mode':'LIVE_OLLAMA',
                'llm_inference_performed':True,'semantic_comparison':
                   'MATCH_AUTHOR_SUPPLIED_GOLD' if kwargs['gold'] else 'NO_GOLD_SUPPLIED',
                'model_attempts':1}
    def deliberative(**kwargs):
        assert kwargs['mode']=='live'
        assert kwargs['postflight_review'] is True
        assert kwargs['forge'] is False
        seen.append(('deliberative',kwargs['request'].parent.parent.name))
        return ({'status':'PASS_DELIBERATIVE_V3_IR_FORGE_NOT_RUN','mode':'LIVE',
                'llm_inference_performed':True,'semantic_comparison':'MATCH_AUTHOR_SUPPLIED_GOLD',
                'model_calls':4} if kwargs['gold'] else
                {'status':'REFUSED_BY_SEMANTIC_REVIEW_NOT_CERTIFIED','mode':'LIVE',
                 'llm_inference_performed':True,'semantic_comparison':'NOT_CHECKED',
                 'clarification_needed':['worker approval authority unsupported'],
                 'model_calls':2})
    report=bo.execute(output=tmp_path/'bakeoff.json',
                      selected_ids=['inv_q1_v0','fresh_worker_approves'],
                      run_baseline=baseline,run_deliberative=deliberative)
    assert len(seen)==4
    assert report['by_strategy']['baseline']['correct']==1
    assert report['by_strategy']['baseline']['unsafe_acceptances']==1
    assert report['by_strategy']['deliberative']['correct']==2
    assert report['by_strategy']['deliberative']['unsafe_acceptances']==0
    assert report['by_strategy']['deliberative']['gated_negative_blocks']==0
    assert report['comparison']=={'correct_delta':1,'unsafe_acceptance_delta':-1,
             'NOTE':'system-level multipass versus single-pass; NOT evidence of weight learning'}
    assert report['native_nanojev_neural_model_used'] is False


def test_unverified_injection_not_scored(tmp_path):
    def fake(**kwargs):
        return {'status':'PASS_DELIBERATIVE_V3_IR_FORGE_NOT_RUN',
                'mode':'OFFLINE_REPLAY','llm_inference_performed':False,
                'semantic_comparison':'MATCH_AUTHOR_SUPPLIED_GOLD', 'model_calls':4}
    r=bo.execute(output=tmp_path/'bakeoff.json',strategy='deliberative',
                 selected_ids=['inv_q1_v0'],run_deliberative=fake)
    assert r['status']=='INCOMPLETE_LIVE_BAKEOFF_NOT_SCORED'
    assert r['by_strategy']['deliberative']['correct']==0
    assert r['by_strategy']['deliberative']['measured']==0
    assert r['by_strategy']['deliberative']['outcomes']['LIVE_INFERENCE_NOT_VERIFIED']==1


def test_does_not_overwrite_report(tmp_path):
    path=tmp_path/'out.json';path.write_text('existing')
    with pytest.raises(bo.BakeoffError,match='overwrite'):
        bo.execute(output=path,strategy='baseline')
    assert path.read_text()=='existing'
