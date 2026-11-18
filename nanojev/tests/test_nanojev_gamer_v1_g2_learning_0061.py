"""G2-0061 acceptance: research-only capture, censored-pair training, restart."""
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_scaling_0059 as g
from tools import nanojev_gamer_v1_g2_learning_0061 as x
from tools import nanojev_gamer_v1_g2_actor_bakeoff_0060 as a60
from test_nanojev_gamer_v1_g2_scaling_0059 import _fake_capture


def _setup(monkeypatch,tmp_path):
    root=_fake_capture(monkeypatch,tmp_path)
    def fake_evidence(raw,parent):
        return {**parent, 'typed_edges':set(parent['edges']),
                'raw_lattice':{},'new_anchors':[], 'new_endpoint_provenance':{},
                'prepared':{'prompt':'', 'prompt_start':0,'offsets':[]}}
    monkeypatch.setattr(g,'_construct_evidence',fake_evidence)
    monkeypatch.setattr(a60,'_actor_reservation',lambda raw,parent,model,*,arm,evidence:
                        evidence)
    monkeypatch.setattr(g,'_diagnose_missing_edges',lambda row,raw,expanded,*,baseline=None:
                        {'missing_edges':0,'by_cause':{},'by_origin':{},
                         'by_relation':{},'cases':[]})
    plan=tmp_path/'plan';capture=tmp_path/'research-capture'
    g.create_plan(plan,train_graphs=32,validation_graphs=16,
                  test_graphs=8,challenge_graphs=8)
    return root,plan,capture


def _simulate_representability_gap(capture,split,*,first_pair=0):
    """Only test metadata: pretend gold endpoint is unavailable in one side.

    The on-disk feature/label hashes stay valid; eligible train pairs are read
    from the actual committed manifest rather than inferred from label count.
    """
    meta=g._shard_paths(capture,split,0)[1]
    data=json.loads(meta.read_text(encoding='utf-8'))
    entry=data['entries'][first_pair*2]
    entry['candidate_reachable']=False
    entry['truth_count']+=1
    entry['missing_candidate_gold_edges']=1
    if split=='train':entry['training_node_ids']=None
    meta.write_text(json.dumps(data),encoding='utf-8')


def test_research_capture_is_independent_resumable_and_never_opens_sealed_data(monkeypatch,tmp_path):
    root,plan,capture=_setup(monkeypatch,tmp_path)
    first=x.capture_learning(plan,capture,root=root,device='cpu',train_graphs=32,
                            validation_graphs=16,max_shards=1)
    assert first['status']=='CAPTURE_IN_PROGRESS'
    assert first['train_pairs_captured']==16 and first['validation_pairs_captured']==0
    assert not (capture/'test-0000000.json').exists()
    assert not (capture/'challenge-0000000.json').exists()
    with pytest.raises(ValueError,match='INCOMPLETE_VALIDATION_PREFIX'):
        x.research_budget(plan,capture,train_graphs=32,validation_graphs=16,
                          epochs=1,batch_pairs=2)
    final=x.capture_learning(plan,capture,root=root,device='cpu',train_graphs=32,
                             validation_graphs=16)
    assert final['status']=='CAPTURE_READY_FOR_RESEARCH_TRAINING'
    assert final['capture']['completed_before']==1
    assert g.FeatureCorpus(plan,capture,'train',require_complete=False).config['candidate_policy']=='actor_cues'
    assert 'actor_policy_0060' in json.loads((capture/'capture.json').read_text())['capture_source_hashes']
    assert not list(capture.glob('test-*.json')) and not list(capture.glob('challenge-*.json'))
    again=x.capture_learning(plan,capture,root=root,device='cpu',train_graphs=32,validation_graphs=16)
    assert again['capture']['captured_now']==0


def test_censored_gold_is_excluded_not_treated_as_negative_and_training_resumes(monkeypatch,tmp_path):
    root,plan,capture=_setup(monkeypatch,tmp_path)
    x.capture_learning(plan,capture,root=root,device='cpu',train_graphs=32,validation_graphs=16)
    _simulate_representability_gap(capture,'train')
    _simulate_representability_gap(capture,'validation')
    budget=x.research_budget(plan,capture,train_graphs=32,validation_graphs=16,
                             epochs=1,batch_pairs=2)
    assert budget['eligible_train_pairs']==15
    assert budget['excluded_train_pairs']==1
    assert budget['steps_per_epoch']==8
    assert budget['validation_pair_reachability']==.875
    assert budget['unreachable_gold_edges_in_training_loss']==0
    with pytest.raises(ValueError,match='FROZEN_CANDIDATE_COVERAGE_BELOW_98_PERCENT'):
        g.training_plan(plan,capture,epochs=1,batch_pairs=2)
    original=g.p57.batch_objective
    learned=[]
    def guarded(model,pairs,rows,arm):
        for row in rows.values():
            assert len(row['positive'])==row['truth_count']
            assert row['training_node_ids'] is not None
        learned.append([p['id'] for p in pairs])
        return original(model,pairs,rows,arm)
    monkeypatch.setattr(g.p57,'batch_objective',guarded)
    out=tmp_path/'trained'
    paused=x.train_learning(plan,capture,out,root=root,device='cpu',train_graphs=32,
                           validation_graphs=16,epochs=1,batch_pairs=2,max_new_steps=1)
    assert paused['status']=='PAUSED_RESTARTABLE' and paused['global_step']==1
    resumed=x.train_learning(plan,capture,out,root=root,device='cpu',train_graphs=32,
                             validation_graphs=16,epochs=1,batch_pairs=2)
    assert resumed['status']=='EXPERIMENTAL_LEARNING_COMPLETE_NOT_CERTIFIED'
    assert resumed['optimizer_steps']==8 and resumed['training_budget']['excluded_train_pairs']==1
    assert resumed['validation']['graphs']==16
    assert resumed['validation']['candidate_reachable']==15
    assert resumed['validation']['reachable_only']['graphs']==14
    assert resumed['selected_checkpoint_reachable_training']['graphs']==30
    assert 'training_minus_reachable_validation_exact_graph_rate' in resumed
    assert 'baseline_step_zero_validation' in resumed
    assert not resumed['production_promotion'] and not resumed['sealed_test_used']
    assert learned and all(len(pair)<=2 for pair in learned)
    assert (out/'selection.json').exists()
    assert not (out/'evaluation.json').exists()


def test_restart_is_source_pinned_and_does_not_change_formal_gate(monkeypatch,tmp_path):
    root,plan,capture=_setup(monkeypatch,tmp_path)
    x.capture_learning(plan,capture,root=root,device='cpu',train_graphs=32,validation_graphs=16)
    out=tmp_path/'research-training'
    x.train_learning(plan,capture,out,root=root,device='cpu',train_graphs=32,
                     validation_graphs=16,epochs=1,batch_pairs=2,max_new_steps=1)
    cfg=out/'training.json';data=json.loads(cfg.read_text())
    data['learning_rate']=.000013
    cfg.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='RESTART_CONFIG_OR_SOURCE_DRIFT'):
        x.train_learning(plan,capture,out,root=root,device='cpu',train_graphs=32,
                         validation_graphs=16,epochs=1,batch_pairs=2)


def test_cli_has_explicit_opt_in_and_separate_research_phases():
    text=Path(g.__file__).read_text(encoding='utf-8')
    assert "sub.add_parser('learn-capture'" in text
    assert "sub.add_parser('learn-train'" in text
    assert "if not a.research_coverage_exception:" in text
    assert "refuse('EXPERIMENTAL_TRAINING_REQUIRES_EXPLICIT_COVERAGE_EXCEPTION')" in text
    assert "if candidate_policy not in ('legacy','spatial','evidence','actor_cues')" in text
    assert g.ROLLOUT_MIN_REACHABILITY==.98
    assert x.POLICY=='actor_cues'


def test_prefix_integrity_requires_exact_contiguity():
    class Sample:
        pairs=[(0,0,{'side':0},{'side':1}),
               (0,2,{'side':0},{'side':1})]
    with pytest.raises(ValueError,match='NONCONTIGUOUS_TRAIN_PREFIX'):
        x._check_prefix(Sample(),2,'train')
