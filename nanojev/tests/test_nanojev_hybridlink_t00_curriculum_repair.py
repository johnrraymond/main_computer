"""CPU validation for T00 gated curriculum, immutable parent and reachability."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import torch
from tools import nanojev_hybridlink_t00_curriculum_repair as fix
from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_hybridlink_frozen_detector_cutover as frozen
from tools import nanojev_qwen_numeric_dual_cutover as parent
from test_nanojev_hybridlink_frozen_detector_cutover import make_args
from test_nanojev_hybridlink_latent_loop_cutover import _allow_latent_embeddings


def test_t00_all_depths_anchored_to_real_final_answer():
    chains=[['PROBLEM: 1+2','ANSWER: 3'],
            ['PROBLEM: (2+(-1))+5','PROBLEM: 1+5','ANSWER: 6'],
            ['PROBLEM: (((1+2)+3)+4)+5','PROBLEM: ((3+3)+4)+5',
             'PROBLEM: (6+4)+5','PROBLEM: 10+5','ANSWER: 15']]
    assert fix.t00_pairs(chains)==[(ch[-2],ch[-1]) for ch in chains]
    for ch in chains:
        assert fix.suffix_window(ch,0,2)==ch[-2:]
        if len(ch)>2:
            assert fix.suffix_window(ch,1,2)==ch[-3:]
    assert len(fix.windows_for_rung(chains[-1],3,2))==3
    windows=fix.windows_for_rung(chains[-1],3,2)
    assert windows[0][-1]=='ANSWER: 15'
    assert windows[-1][0]==chains[-1][0]
    assert windows[-1][-1]!='ANSWER: 15'
    with pytest.raises(ValueError,match='SHALLOW'):
        fix.suffix_window(chains[0],1,2)


def _args(monkeypatch,tmp_path):
    _allow_latent_embeddings(monkeypatch)
    a=make_args(monkeypatch,tmp_path)
    a.steps=2;a.eval_every=1
    frozen.train(a)
    a.detector_checkpoint=str(Path(a.output)/'best.pt')
    a.output=str(tmp_path/'t00-repair')
    a.steps=2;a.eval_every=1;a.eval_cases=3;a.min_gate_cases=2
    a.min_steps_per_rung=100
    a.max_unroll=2;a.max_depth=2;a.max_rung=0
    a.lr=.001;a.completion_weight=.2
    a.drift_threshold=1e-8;a.stall_patience=2
    a.min_exit_rate=.5;a.min_gold_exit_rate=.0+0.01
    a.min_source_gain=0.;a.min_zero_gain=0.;a.min_gold_cosine=0.;a.max_early_stops=0
    a.warm_start_loop=None
    a.reachability_cases=2;a.reachability_steps=2;a.reachability_lr=.01
    a.seed=23;a.control='intact'
    return a


def test_gate_refuses_zero_success_and_counts_steps():
    a=SimpleNamespace(min_steps_per_rung=2,min_gate_cases=3,min_exit_rate=.5,
                      min_gold_exit_rate=.8,min_gold_cosine=.7,min_source_gain=.02,min_zero_gain=.01,max_early_stops=0)
    t00={'cases':4,'correct_stop_depth':0,'early_false_stops':0,'generated_exit_rate':0.,
         'gold_exit_rate':1.,'mean_generated_to_gold_cosine':.8,'mean_gain_over_source_cosine':.12,
         'mean_intact_minus_zero_cosine':.13}
    ok,reasons=fix.gate(t00,t00,a,0,2)
    assert not ok and 'INSUFFICIENT_CORRECT_EXITS' in reasons and 'T00_RETENTION_FAILED' in reasons
    t00.update(correct_stop_depth=3,generated_exit_rate=.75)
    assert fix.gate(t00,t00,a,0,2)[0]
    assert not fix.gate(t00,t00,a,0,1)[0]
    t00['early_false_stops']=1
    assert not fix.gate(t00,t00,a,0,2)[0]


def test_cpu_training_provisional_no_fake_best_parent_unchanged(monkeypatch,tmp_path):
    a=_args(monkeypatch,tmp_path)
    parent_before=parent.file_hash(a.stage1_checkpoint)
    det_before=parent.file_hash(a.detector_checkpoint)
    result=fix.train(a)
    assert result['status']=='T00_NOT_PASSED'
    assert result['checkpoint'] is None
    assert not (Path(a.output)/'best.pt').exists()
    ck=torch.load(Path(a.output)/'candidate.pt',map_location='cpu',weights_only=True)
    assert ck['status']=='PROVISIONAL_NOT_PROMOTED'
    assert ck['trained_generator_only'] and 'link' in ck and 'detector' in ck
    assert 'numeric' not in ck and 'lora' not in ck
    assert parent.file_hash(a.stage1_checkpoint)==parent_before
    assert parent.file_hash(a.detector_checkpoint)==det_before
    hist=json.loads((Path(a.output)/'history.json').read_text())
    assert hist[0]['rung']=='T00' and hist[0]['window_contains_answer']
    assert all(x['detector_updated'] is False and x['qwen_updated'] is False for x in hist)
    assert 'MIN_STAGE_STEPS_NOT_MET' in hist[-1]['gate_reasons']
    assert result['geometry_observer_only']


def test_reachability_per_case_optimizes_only_free_embedding(monkeypatch,tmp_path):
    a=_args(monkeypatch,tmp_path)
    result=fix.train(a)
    a.warm_start_loop=result['candidate']
    a.output=str(tmp_path/'reachability')
    p=parent.file_hash(a.stage1_checkpoint)
    d=parent.file_hash(a.detector_checkpoint)
    out=fix.reachability(a)
    assert out['only_latent_embedding_optimized']
    assert out['all_model_weights_unchanged']
    assert out['cases']>=1
    assert all(item['optimized_cosine']>=item['starting_cosine']-1e-6 for item in out['rows'])
    assert parent.file_hash(a.stage1_checkpoint)==p and parent.file_hash(a.detector_checkpoint)==d
    assert (Path(a.output)/'reachability.json').exists()


def test_training_end_gate_cannot_promote_zero_exit(monkeypatch,tmp_path):
    a=_args(monkeypatch,tmp_path)
    a.steps=3;a.min_steps_per_rung=1;a.min_exit_rate=1.
    # Detector frozen and near-random generated states; ensure a zero-exit
    # candidate can never become the accepted stage merely because no other
    # candidate has a better score.
    monkeypatch.setattr(fix,'t00_eval',lambda *x:({'cases':3,'correct_stop_depth':0,
        'answer_exits':0,'early_false_stops':0,'generated_exit_rate':0.,'gold_exit_rate':1.,'mean_generated_to_gold_cosine':.8,
        'mean_gain_over_source_cosine':.3,'mean_intact_minus_zero_cosine':.2},[]))
    result=fix.train(a)
    assert result['checkpoint'] is None
    assert result['status']=='T00_NOT_PASSED'
    assert (Path(a.output)/'candidate.pt').exists()
    assert not (Path(a.output)/'best.pt').exists()


def test_gate_promotes_compatible_checkpoint_without_faking_full_certification(monkeypatch,tmp_path):
    a=_args(monkeypatch,tmp_path)
    a.min_steps_per_rung=1
    a.steps=2
    a.max_rung=0
    monkeypatch.setattr(fix,'t00_eval',lambda *x:({'cases':3,'correct_stop_depth':3,
        'answer_exits':3,'early_false_stops':0,'generated_exit_rate':1.,'gold_exit_rate':1.,
        'mean_generated_to_gold_cosine':.93,
        'mean_gain_over_source_cosine':.31,'mean_intact_minus_zero_cosine':.19},[]))
    result=fix.train(a)
    assert result['accepted_rung']=='T00'
    assert result['status']=='T00_GATE_PASSED_NOT_FULL_LATENT_CERTIFIED'
    assert (Path(a.output)/'best.pt').exists()
    assert (Path(a.output)/'accepted_T00.pt').exists()
    ck=torch.load(Path(a.output)/'best.pt',weights_only=True)
    assert ck['accepted_rung']==0
    assert ck['trained_generator_only']
    a.checkpoint=result['checkpoint']
    cfg,_=loop._load_runtime_checkpoint(a)
    assert cfg['repair_schema']==fix.SCHEMA
    a.stage='inspect';a.state='PROBLEM: 6+6'
    # Inference compatibility with original Patch3 probe/inspect.
    out=loop.evaluate(a)
    assert out['headless'] and not out['inference_oracle_used']


def test_warm_start_checks_checkpoint_lineage(monkeypatch,tmp_path):
    a=_args(monkeypatch,tmp_path)
    result=fix.train(a)
    ckpath=Path(result['candidate'])
    prepared={'parent_hash':parent.file_hash(a.stage1_checkpoint),'frozen_hash':'different-from-checkpoint'}
    a._det_sha=parent.file_hash(a.detector_checkpoint)
    with pytest.raises(RuntimeError,match='PARENT_DRIFT'):
        fix._verified_link_checkpoint(ckpath,a,prepared)
