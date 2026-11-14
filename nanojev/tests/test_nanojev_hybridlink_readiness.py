"""CPU-only, offline readiness diagnostic regression tests."""
import csv
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from tools import nanojev_hybridlink_readiness as ready
from tools import nanojev_qwen_problem_answer_stage1 as stage1
from tools import nanojev_hybridlink_latent_diagnostics as diag


def chain(numbers):
    return [r['source'] for r in stage1._rows_for(numbers,'case',0)]+[
        stage1._rows_for(numbers,'case',0)[-1]['target']]


def test_oracle_canonical_and_exact_step():
    ch=chain([900,-899,3,-2])
    rec=ready.verify_oracle(ch,stage1)
    assert rec['answer']==2 and rec['max_literal']==900 and rec['steps']==3


def test_oracle_detects_well_formed_but_incorrect_intermediate():
    ch=chain([900,-899,3])
    ch[1]='PROBLEM: 2+3'
    with pytest.raises(ValueError,match='CHAIN_NOT_EXACT_REDUCTION'):
        ready.verify_oracle(ch,stage1)


def test_no_unsafe_expression():
    with pytest.raises((ValueError, SyntaxError)):
        ready.verify_oracle(['PROBLEM: __import__("os").system("echo nope")','ANSWER: 1'],stage1)


def test_real_recorded_suffix_selection_and_fullchain_labels():
    ch=chain([5,-5,3,4,-7])
    chosen,counts,allcases=ready.catalog([ch],stage1,4,0,5,20)
    assert len(chosen[0])==1 and len(chosen[3])==1
    assert chosen[3][0]['full_chain']
    assert not chosen[0][0]['full_chain']
    assert all(x['answer']==0 for x in allcases)
    assert all(x['rung']+1==x['steps'] for x in allcases)


def test_signed_residual_and_magnitude_coverage_zero_holes():
    deck=[chain([8,-8]),chain([2000,-1999]),chain([2000,-2001])]
    selected,avail,items=ready.catalog(deck,stage1,0,1,42,12)
    matrix,summary=ready.coverage_matrix(avail,items,0,1,1)
    assert summary['0']['target_cells']==3*6
    assert sum(r['selected'] for r in matrix)==3
    assert any(r['coverage']=='missing_from_deck' for r in matrix)
    assert any(r['residual']==-1 and r['selected']==1 for r in matrix)


def test_cannot_conclude_qwen_needs_finetuning_from_readout_failure():
    x=ready.grade_components(False,True,True,True,.6,qwen_transition=True)
    assert 'QWEN_NUMERIC_SIDECAR_DISAGREEMENT' in x['issues']
    assert not x['qwen_finetuning_justified']
    assert 'isolate_qwen_representation_from_numeric_head_before_finetuning' in x['next_actions']


def test_cannot_certify_neural_arithmetic_from_stop_and_cosine():
    x=ready.grade_components(True,True,True,True,.6,qwen_transition=True)
    assert 'AUTONOMOUS_QWEN_REDUCTION_NOT_TESTED' in x['issues']
    assert x['status']=='sampled_pathway_pass_not_arithmetic_certification'


def test_distinguishes_detector_vs_generator():
    x=ready.grade_components(True,False,False,False,.6,qwen_transition=True)
    assert 'DETECTOR_MISCLASSIFIES_GOLD_STATES' in x['issues']
    assert 'GENERATED_LATENT_FIDELITY_FAILURE' in x['issues']
    assert 'GENERATED_STOPPING_FAILURE' in x['issues']


def test_zero_recommendation_on_invalid_oracle():
    assert ready.grade_components(True,True,True,True,.6,oracle_valid=False)['status']=='blocked'


def test_empty_rung_no_fabricated_pass():
    chosen,counts,items=ready.catalog([chain([6,6])],stage1,8,12,42,4)
    matrix,summary=ready.coverage_matrix(counts,items,8,12,1)
    assert summary['8']['selected_cases']==0
    assert summary['8']['adequately_sampled_cells']==0
    assert ready._summarize([],0.6)['joint_pathway_pass'] is None


def test_readiness_stage_wiring():
    import inspect
    src=inspect.getsource(diag.main)
    assert "'readiness-preview','readiness'" in src
    assert "--max-rung" in src


def test_report_no_unverified_qwen_certification(tmp_path):
    rep={'stage':'readiness-preview','split':'dev','target':{'max_rung':0,'signed_residual_radius':1},
         'coverage':{'0':{'selected_cases':0,'available_cells':0,'target_cells':18}}}
    ready._write_report(tmp_path,rep)
    doc=(tmp_path/'readiness.md').read_text()
    assert 'do NOT certify autonomous Qwen arithmetic' in doc
    assert json.loads((tmp_path/'readiness.json').read_text())['stage']=='readiness-preview'


def test_invalid_configuration():
    args=SimpleNamespace(max_rung=8,radius=99999,max_cases_per_rung=24,min_per_cell=1,max_tokens=192,
                         min_gold_cosine=.6,answer_threshold=.9,qwen_text_probe_per_rung=0,qwen_text_max_new_tokens=48)
    with pytest.raises(ValueError,match='READINESS_INVALID_FRONTIER_TARGET'):
        ready._validate(args)


def test_raw_qwen_prompt_does_not_embed_gold_target():
    prompt=ready._text_reduction_prompt('PROBLEM: (900+(-899))+3')
    assert 'PROBLEM: (900+(-899))+3' in prompt
    assert 'PROBLEM: 1+3' not in prompt
    assert 'Input: PROBLEM: 2+3' in prompt


def test_preview_end_to_end_without_loading_qwen_or_detector(tmp_path,monkeypatch):
    from tools import nanojev_qwen_numeric_dual_cutover as parent
    from tools import nanojev_hybridlink_latent_diagnostics as runner
    from tools import nanojev_qwen_problem_answer_stage1 as stage1
    d=stage1._rows_for([800,-799,0],'dev',0)
    monkeypatch.setattr(parent,'load_parent',lambda a: ({'seed':1},{}))
    monkeypatch.setattr(parent,'_rows',lambda s,cfg,split: d)
    args=SimpleNamespace(stage='readiness-preview',split='dev',stage1_checkpoint=str(tmp_path/'stage1'/'best.pt'),
        text_checkpoint=str(tmp_path/'text'/'best.pt'),detector_checkpoint=None,checkpoint=None,
        cache_dir=None,output=str(tmp_path/'report'),radius=1,max_rung=8,max_cases_per_rung=12,
        min_per_cell=1,seed=1,max_tokens=192,min_gold_cosine=.6,answer_threshold=.9,
        qwen_text_probe_per_rung=0,qwen_text_max_new_tokens=48)
    result=ready.run(args)
    assert result['status']=='coverage_preview_only'
    assert result['coverage']['1']['selected_cases']==1
    assert result['coverage']['8']['selected_cases']==0
    assert (tmp_path/'report'/'coverage.csv').exists()
    assert not result['qwen_autonomous_reduction_proven']


def test_cuda_readiness_checkpoint_verification_wires_actual_detector_and_guards_drift(tmp_path,monkeypatch):
    """Catches the CUDA-only KeyError and verifies frozen-detector lineage stays enforced."""
    import torch
    from tools import nanojev_qwen_numeric_dual_cutover as parent
    from tools import nanojev_hybridlink_t00_curriculum_repair as t00
    from tools import nanojev_hybridlink_frontier_repair as frontier
    from tools import nanojev_hybridlink_latent_loop_cutover as loop
    from tools import nanojev_hybridlink_frozen_detector_cutover as frozen

    deck=stage1._rows_for([800,-799,0],'dev',0)
    monkeypatch.setattr(parent,'load_parent',lambda a: ({'seed':1},{}))
    monkeypatch.setattr(parent,'_rows',lambda s,cfg,split: deck)

    class TinyBackbone(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.config=SimpleNamespace(hidden_size=4)
            self.embedding=torch.nn.Embedding(8,4)
        def get_input_embeddings(self):
            return self.embedding

    bb=TinyBackbone()
    numeric=torch.nn.Linear(4,1)
    detector=torch.nn.Linear(4,1)
    prepared={'bb':bb,'tok':object(),'cache_dir':tmp_path/'cache','identity':{},'det':None}
    # Mirror production _prepare contract: the returned prepared dict has NO det.
    prepared.pop('det')
    monkeypatch.setattr(t00,'_prepare',lambda a:(prepared,numeric,detector,{'scale':1.0}))
    good={k:v.detach().cpu().clone() for k,v in detector.state_dict().items()}
    checkpoint={'training_schema':frontier.t01.SCHEMA,'accepted_rung':1,
                'status':'T01_ACCEPTED_NOT_FULL_LATENT_CERTIFIED',
                'detector':good,
                'link':loop.make_link(4,4).state_dict()}
    monkeypatch.setattr(t00,'_verified_link_checkpoint',lambda *args:checkpoint)
    monkeypatch.setattr(frozen,'_load_or_encode',lambda *args:([],'unused'))

    args=SimpleNamespace(stage='readiness',split='dev',
        stage1_checkpoint=str(tmp_path/'stage1'/'best.pt'),
        text_checkpoint=str(tmp_path/'text'/'best.pt'),
        detector_checkpoint=str(tmp_path/'det'/'best.pt'),
        checkpoint=str(tmp_path/'link'/'best.pt'),cache_dir=None,
        output=str(tmp_path/'report'),radius=1,max_rung=0,max_cases_per_rung=2,
        min_per_cell=1,seed=1,max_tokens=192,min_gold_cosine=.6,
        answer_threshold=.9,qwen_text_probe_per_rung=0,
        qwen_text_max_new_tokens=48,device='cpu')

    # A matching frozen detector must reach reference encoding, NOT KeyError 'det'.
    def stop_after_verification(*a,**kw):
        raise RuntimeError('READINESS_PAST_CHECKPOINT_VERIFICATION')
    monkeypatch.setattr(frozen,'_load_or_encode',stop_after_verification)
    with pytest.raises(RuntimeError,match='READINESS_PAST_CHECKPOINT_VERIFICATION'):
        ready.run(args)
    assert 'det' not in prepared  # no mutation of t00's cached context

    # Alter an otherwise well-formed checkpoint's frozen detector weights:
    # provenance drift MUST still block before any expensive decoding.
    checkpoint['detector']={k:v+1 for k,v in good.items()}
    args.output=str(tmp_path/'report_drift')
    with pytest.raises(RuntimeError,match='FRONTIER_DETECTOR_CHECKPOINT_DRIFT'):
        ready.run(args)


def test_strict_operand_ceiling_applies_to_original_complete_chain():
    # A small final suffix may originate from a very-large-operand problem.
    # All original steps, not just the selected suffix, must remain in-bounds.
    too_large=chain([2000,-1999,5,-5])
    allowed=chain([1000,-999,5,-5])
    selected,avail,allcases=ready.catalog([too_large,allowed],stage1,3,1,42,25,1024)
    assert all(x['max_literal']<=1024 for x in allcases)
    assert not any(x['chain']==too_large for x in allcases)
    assert any(x['chain']==allowed for x in allcases)
    assert selected[2]
    matrix,summary=ready.coverage_matrix(avail,allcases,3,1,1,1024)
    assert summary['0']['target_cells']==9 # 3 residuals x 3 capped bands
    assert set(row['operand_band'] for row in matrix)=={'0-8','9-64','65-1024'}
    assert all(row['selected']<=row['available_recorded'] for row in matrix)


def test_per_step_failure_attribution_without_qwen_causal_guess(monkeypatch):
    case={'rung':1,'chain':chain([5,-5,3]),'answer':3,'max_literal':5,'full_chain':True}
    import torch
    generated=[{'step':1,'p_answer':.99,'action':'ANSWER','readout':{'direct_number':4.0,'complex_number':3.0},
               '_vector':torch.zeros(4)}]
    raw={'generated':generated,'status':'ANSWER','steps':1}
    grade={'correct':False,'steps':1,'status':'ANSWER','early_false_stop':True,
           'late_exit':False,'history':[{'target_vector_cosine':.2}]}
    readouts=[{'target':0,'direct':1.,'complex':0.,'direct_exact':False,'complex_exact':True},
              {'target':3,'direct':3.,'complex':3.,'direct_exact':True,'complex_exact':True}]
    gold=[{'correct':False,'p_answer':.95,'expected_answer':False},
          {'correct':True,'p_answer':.95,'expected_answer':True}]
    events=ready.classify_path(case,9,readouts,gold,[False,True],raw,grade,.6,.9,1.,None)
    codes={e['error_type'] for e in events}
    assert {'STAGE1_RETRIEVAL_MISMATCH','GOLD_STATE_NUMERIC_READOUT_ERROR',
            'GOLD_DETECTOR_FALSE_POSITIVE','GENERATED_DETECTOR_FALSE_POSITIVE',
            'GENERATED_EARLY_STOP','MISSING_LATENT_TRANSITION',
            'LATENT_FIDELITY_BELOW_FLOOR'} <= codes
    assert all(e['rung']==1 and e['case_index']==9 for e in events)
    assert not any('QWEN_BACKBONE_NEEDS_TRAINING' in e['error_type'] for e in events)


def test_matrix_marks_unknown_qwen_and_detects_exact_component():
    obs=[{'rung':0,'qwen_text_reduction':{'correct':None},
          'qwen_transition':False,'qwen_direct':False,'qwen_complex':True,
          'detector_gold':True,'correct_stop':False,'latent_quality':False,
          'final_number_exact':False,
          'failures':[{'component':'numeric_direct','case_index':1}]}]
    cov={'0':{'adequately_sampled_cells':1,'target_cells':51},
         '1':{'adequately_sampled_cells':0,'target_cells':51}}
    matrix=ready.component_matrix(obs,cov,1,1024)
    def row(r,c):return next(x for x in matrix if x['rung']==r and x['component']==c)
    assert row(0,'qwen_backbone')['status']=='UNMEASURED_NOT_QWEN_CERTIFIED'
    assert row(0,'numeric_direct')['status']=='OBSERVED_FAILURE'
    assert row(0,'numeric_complex')['status']=='SAMPLED_PASS'
    assert row(0,'detector_generated')['status']=='OBSERVED_FAILURE'
    assert row(1,'stage1_probe')['status']=='UNMEASURED'
    decision=ready.next_training_stage(matrix)
    assert any(x['component']=='numeric_direct' for x in decision['0'])
    assert any(x['component']=='dataset' for x in decision['1'])


def test_cli_operand_limit_configured():
    import inspect
    assert "--operand-limit" in inspect.getsource(diag.main)
