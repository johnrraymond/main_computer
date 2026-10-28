"""Headless CPU proofs for the Patch3 T00/trace diagnostic. No Qwen downloads."""
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_hybridlink_latent_diagnostics as d
from tools import nanojev_hybridlink_latent_loop_cutover as old
from test_nanojev_hybridlink_latent_loop_cutover import StubBB, StubTokenizer, StubNumeric, AlwaysDetector, _allow_latent_embeddings
from test_nanojev_hybridlink_frozen_detector_cutover import make_args
from tools import nanojev_qwen_numeric_dual_cutover as ct
from tools import nanojev_hybridlink_frozen_detector_cutover as fc


class ContractNumeric(nn.Module):
    def __init__(self):
        super().__init__()
        self.dummy=nn.Parameter(torch.tensor(1.0))
    def forward(self,h,scale):
        x=h[:,0].double()*self.dummy
        # Synthetic independent readout; log radius is exactly 0 when (a,b)=(.6,.8).
        a=x*0+.6;b=x*0+.8
        c=torch.log(torch.hypot(a,b))
        return {'direct_number':x,'complex_number':scale*torch.sinh(c)+1,
                'a':a,'b':b,'direct_rho':x,'complex_rho':c,
                'delta_rho':c-x,'phase':torch.atan2(b,a)}


def test_numerical_readout_preserves_full_complex_pair_and_target_errors():
    n=ContractNumeric()
    read=d._readouts(n,torch.zeros(1,8),64.0,1)
    assert abs(read['complex_radius']-1)<1e-12
    assert abs(read['unit_circle_error'])<1e-12
    assert abs(read['complex_code_abs_error'])<1e-12
    assert abs(read['phase']-math.atan2(.8,.6))<1e-12
    assert read['direct_code_abs_error']==0
    other=d._readouts(n,torch.ones(1,8),64.,6)
    assert other['direct_code_abs_error']>0
    assert other['complex_code_abs_error']>0
    assert other['unit_circle_error'] is None


def test_generation_never_accesses_gold_and_geometry_cannot_control_feedback():
    torch.manual_seed(9)
    bb=StubBB();link=old.make_link(8,8);det=AlwaysDetector(-10);num=ContractNumeric();tok=StubTokenizer()
    p=d.generate_one(bb,tok,link,det,num,'PROBLEM: 5+1','cpu',128,64.)
    z=d.generate_one(bb,tok,link,det,num,'PROBLEM: 5+1','cpu',128,64.,control='zero_latent')
    assert p['generated'].shape==(1,8)
    assert not torch.equal(p['generated'],z['generated'])
    assert p['probability']<.5
    # A readout intervention changes the reported value, not the vector.
    class BadNumeric(nn.Module):
        def forward(self,h,scale):
            out=num(h,scale)
            return {**out,'direct_number':out['direct_number']+10000}
    altered=d.generate_one(bb,tok,link,det,BadNumeric(),'PROBLEM: 5+1','cpu',128,64.)
    assert torch.equal(p['generated'],altered['generated'])
    assert altered['readout']['direct_number']!=p['readout']['direct_number']
    assert bb.lm_head.weight.grad is None


def test_t00_separates_reference_generator_detector_and_zero_baseline():
    torch.manual_seed(12)
    bb=StubBB();link=old.make_link(8,8);tok=StubTokenizer();det=AlwaysDetector(-10);num=ContractNumeric()
    p=d.generate_one(bb,tok,link,det,num,'PROBLEM: 1+5','cpu',128,64.)
    z=d.generate_one(bb,tok,link,det,num,'PROBLEM: 1+5','cpu',128,64.,control='zero_latent')
    gold=torch.randn_like(p['generated'])
    r=d.grade_one(p,gold,6,64.,.9,zero=z,gold_prob=.999,problem_prob=.001,naked_prob=.005,
                  numeric=num,source='PROBLEM: 1+5',expected_depth=2)
    assert r['gold_positive_generated_negative']
    assert r['generated_would_exit'] is False
    assert abs(r['marker_contrast']-.998)<1e-6
    summ=d.summarize_t00([r],.9)
    assert 'GENERATOR_TO_DETECTOR_DISTRIBUTION_GAP' in summ['triage']
    assert 'POSSIBLE_PREFIX_SHORTCUT' in summ['triage']
    assert 'COMPLEX_HEAD_IS_OBSERVER_ONLY' in summ['triage']
    assert summ['mean_intact_minus_zero_cosine'] is not None


def test_stalled_not_answer_and_unterminated_trace_has_per_step_readouts():
    torch.manual_seed(42)
    bb=StubBB();tok=StubTokenizer();link=old.make_link(8,8);det=AlwaysDetector(-10);num=ContractNumeric()
    ch=['PROBLEM: (2+(-1))+5','PROBLEM: 1+5','ANSWER: 6']
    raw=d.build_trace(bb,tok,link,det,num,ch,'cpu',128,64.,.9,1e-8,2,3)
    assert raw['status'] in ('BUDGET_EXHAUSTED','STALLED')
    assert raw['steps']>=1
    assert raw['generated'][0]['readout']['a']==pytest.approx(.6)
    assert not raw['generated'][0]['action']=='ANSWER'
    reference={'PROBLEM: 1+5':torch.zeros(1,8),'ANSWER: 6':torch.ones(1,8)}
    # gold strings are only handed to the scoring function after build_trace.
    graded=d.grade_trace(raw,ch,reference,num,64.,.9)
    assert graded['status']!='ANSWER'
    assert graded['history'][0]['gold_target']=='PROBLEM: 1+5'
    assert graded['history'][0]['readout']['target_value']==1
    assert graded['history'][0]['readout']['unit_circle_error']==pytest.approx(0)
    assert graded['history'][0]['target_vector_cosine']==pytest.approx(0.)
    assert d.summarize_traces([graded])['answer_exits']==0


def test_zero_signal_cannot_be_selected_as_valid_best():
    summary={'generated_answer_exits':0,'gold_reference_answer_exits':3,
             'gold_positive_generated_negative':3,'mean_intact_minus_zero_cosine':-.1,
             'mean_gain_over_source_cosine':-.2,'mean_marker_contrast':.9,
             'geometry_feedback_causal':False}
    flags=d.triage(summary)
    for flag in ('NO_GENERATED_ANSWER_EXITS','GENERATOR_TO_DETECTOR_DISTRIBUTION_GAP',
                 'NO_POSITIVE_GAIN_VS_ZERO_LATENT','NO_FORWARD_PROGRESS_TO_REFERENCE',
                 'POSSIBLE_PREFIX_SHORTCUT','COMPLEX_HEAD_IS_OBSERVER_ONLY'):
        assert flag in flags


def test_root_depth_selection_and_output_protection(tmp_path):
    chains=[['PROBLEM: A'+str(n)]+['PROBLEM: B'+str(n)]*n+['ANSWER: 1'] for n in [1,2,3,4,6,8,12]]
    s=d.select(chains,6)
    assert {len(x)-1 for x in s}=={3,4,5,7,9,13}
    with pytest.raises(ValueError,match='CASE'):d.select(chains,0)
    p=tmp_path/'protected'/'best.pt';p.parent.mkdir();p.write_bytes(b'test')
    with pytest.raises(ValueError,match='OVERLAPS_INPUT'):
        d.verify_paths(p.parent/'diagnostic',[p.parent])
    d.verify_paths(tmp_path/'safe',[p.parent])


def test_digest_does_not_change_checkpoint_on_smoke(monkeypatch,tmp_path):
    # End-to-end exercising real loader signatures, without fetching real Qwen.
    _allow_latent_embeddings(monkeypatch)
    args=make_args(monkeypatch,tmp_path)
    args.steps=2;args.eval_every=1
    fc.train(args)
    args.detector_checkpoint=str(Path(args.output)/'best.pt')
    args.output=str(tmp_path/'latent');args.checkpoint=None
    args.steps=2;args.eval_every=1;args.eval_cases=3
    args.max_depth=2;args.max_unroll=1;args.lr=.001;args.detector_lr=.001
    args.completion_weight=.2;args.seed=23;args.reset_detector=False
    args.drift_threshold=1e-8;args.stall_patience=2
    old.train(args)
    args.checkpoint=str(Path(args.output)/'best.pt')
    args.output=str(tmp_path/'diagnostics')
    args.stage='t00';args.split='dev';args.t00_cases=3;args.trace_cases=2
    args.answer_threshold=None;args.drift_threshold=1e-8;args.stall_patience=2
    expected=(ct.file_hash(args.stage1_checkpoint),ct.file_hash(args.detector_checkpoint),ct.file_hash(args.checkpoint))
    report=d.run(args)
    assert report['no_training'] and report['numerical_geometry_in_feedback'] is False
    assert report['t00']['cases']>0
    assert report['t00']['mean_intact_minus_zero_cosine'] is not None
    assert report['t00_cases'][0]['gold_answer_probability'] is not None
    assert report['t00_cases'][0]['counterfactual_problem_probability'] is not None
    assert report['trace'] is None
    assert Path(args.output,'diagnostic.json').exists()
    assert expected==(ct.file_hash(args.stage1_checkpoint),ct.file_hash(args.detector_checkpoint),ct.file_hash(args.checkpoint))
    args.output=str(tmp_path/'diagnostics_all');args.stage='all'
    report2=d.run(args)
    assert report2['trace']['cases']>=1
    assert len(report2['trace_cases'][0]['history'])>=1


def test_preview_works_without_loading_model(monkeypatch,tmp_path):
    from test_nanojev_qwen_numeric_dual_cutover import fake_stage1,make_parent,args_for
    fake=fake_stage1(monkeypatch)
    text,parent,_=make_parent(tmp_path,fake)
    a=args_for(tmp_path,text,parent)
    a.stage='preview';a.split='dev'
    r=d.run(a)
    assert r['stage']=='preview' and not r['trained_weights_loaded']
