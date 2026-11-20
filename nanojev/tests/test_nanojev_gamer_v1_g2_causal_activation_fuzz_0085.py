"""G2-0085 frozen-checkpoint causal-activation lab: deterministic CPU unit tests."""
from __future__ import annotations

from copy import deepcopy

import pytest
import torch

from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as mod
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead


def little_head(*,coupled=True):
    torch.manual_seed(10)
    head=GamerGraphHead(hidden_size=8,width=16,layers=1,latent_slots=2,
                        memory_slots=2,max_span_tokens=4,oracle_coupling=coupled)
    return head.eval().requires_grad_(False)


def small_input():
    hidden=torch.linspace(-2,2,48).reshape(6,8)
    eligible=torch.ones(6,dtype=torch.bool)
    spans=[{'kind':'actor','token_first':0,'token_last':0},
           {'kind':'action','token_first':2,'token_last':2},
           {'kind':'quantity','token_first':4,'token_last':4}]
    return hidden,eligible,spans


def test_counterfactual_modes_annotated_and_changes():
    rows=mod.make_pairs(pairs=8)
    assert [r['mode'] for r in rows]==list(mod.MODES)*2
    assert {r['family'] for r in rows}=={'allocation','encounter'}
    for pair in rows:
        assert pair['base']['prompt']!=pair['fuzz']['prompt']
        assert pair['base']['object_spans'] and pair['fuzz']['object_spans']
        assert pair['base']['annotation_provenance']=='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
        if pair['mode']=='added_distractor':
            assert pair['fuzz']['graph']==pair['base']['graph']
            assert pair['fuzz']['prompt'].startswith(pair['base']['prompt'])
        if pair['mode']=='action_removal':
            assert len(pair['base']['graph']['edges'])>len(pair['fuzz']['graph']['edges'])
    assert [(x['base']['prompt'],x['fuzz']['prompt']) for x in rows] == [
        (x['base']['prompt'],x['fuzz']['prompt']) for x in mod.make_pairs(pairs=8)]
    with pytest.raises(ValueError,match='G2_0085_DESIGN_PAIRS_MULTIPLE_OF_FOUR'):
        mod.make_pairs(pairs=5)


def test_aucs_distinguish_rank_ties_and_nonseparable():
    assert mod.rank_auc([4,3],[2,1])==1.
    assert mod.rank_auc([1,1],[1,1])==0.5
    assert mod.rank_auc([0],[1])==0.
    assert mod.rank_auc([],[1]) is None
    assert mod.rank_auc([1],[]) is None


def test_hook_trace_is_frozen_and_complete():
    head=little_head();hidden,eligible,spans=small_input()
    before={k:v.clone() for k,v in head.state_dict().items()}
    output,caps=mod.trace_head(head,hidden,eligible,spans)
    assert caps['project'].shape==(6,16)
    assert caps['encoder'].shape==(1,6,16)
    assert caps['g1_g2_coupler'].shape==(6,16)
    assert caps['pair_projection'].shape==(5,5,16)
    assert caps['binding_input'].shape==(5,5,16)
    assert caps['binding'].shape==(5,5,5)
    assert output['surface_count']==3
    assert all(torch.equal(v,head.state_dict()[k]) for k,v in before.items())
    # A second call must not encounter leaked hooks.
    _,again=mod.trace_head(head,hidden,eligible,spans)
    assert torch.equal(caps['binding'],again['binding'])


def test_interventions_modify_activations_not_checkpoint():
    head=little_head();hidden,eligible,spans=small_input()
    output,caps=mod.trace_head(head,hidden,eligible,spans)
    frozen={k:v.clone() for k,v in head.state_dict().items()}
    for stage in ('encoder','g1_g2_coupler','pair_projection'):
        changed=mod._patch_head(head,hidden,eligible,spans,stage=stage,
            src_index=0 if stage!='pair_projection' else None,
            pair=(0,1) if stage=='pair_projection' else None)
        assert changed.shape==output['binding_logits'].shape
        assert torch.isfinite(changed).all()
        assert not torch.equal(changed,output['binding_logits'])
    swapped=mod._patch_head(head,hidden,eligible,spans,stage='pair_projection',
        pair=(0,1),replacement=caps['pair_projection'][2,1])
    assert torch.allclose(swapped[0,1],caps['binding'][2,1],atol=1e-6)
    assert torch.equal(swapped[0,2],caps['binding'][0,2])
    assert all(torch.equal(v,head.state_dict()[k]) for k,v in frozen.items())
    with pytest.raises(ValueError,match='G2_0085_BAD_REPLACEMENT_SHAPE'):
        mod._patch_head(head,hidden,eligible,spans,stage='pair_projection',
                        pair=(0,1),replacement=torch.zeros(4))
    # Failed hook must be removed too.
    _,again=mod.trace_head(head,hidden,eligible,spans)
    assert torch.equal(again['binding'],caps['binding'])


def test_uncoupled_head_skips_missing_stage():
    head=little_head(coupled=False);hidden,eligible,spans=small_input()
    _,caps=mod.trace_head(head,hidden,eligible,spans)
    assert 'g1_g2_coupler' not in caps
    with pytest.raises(ValueError,match='G2_0085_MISSING_PATCH_STAGE'):
        mod._patch_head(head,hidden,eligible,spans,
                        stage='g1_g2_coupler',src_index=0)


def test_gold_never_enters_head_trace_or_inference(monkeypatch):
    head=little_head();hidden,eligible,spans=small_input()
    prompt='ABCDEF'
    prep={'prompt':prompt,'prompt_start':0,'offsets':[(i,i+1) for i in range(6)],
          'eligible':eligible}
    graph={'nodes':[{'id':'n0','span':[0,1],'kind':'actor'},
                    {'id':'n1','span':[2,3],'kind':'action'},
                    {'id':'n2','span':[4,5],'kind':'quantity'}],
           'edges':[{'src':'n0','dst':'n1','type':'owns'}]}
    _,caps=mod.trace_head(head,hidden,eligible,spans)
    original=caps['binding'].clone()
    rows=mod._pair_stats(original,spans,prep,graph)
    assert len(rows)==6
    assert sum(r['gold_label']>0 for r in rows)==1
    altered=deepcopy(graph);altered['edges']=[]
    rows2=mod._pair_stats(original,spans,prep,altered)
    assert all(r['gold_label']==0 for r in rows2)
    assert [(r['pred_label'],r['edge_margin']) for r in rows]==[
        (r['pred_label'],r['edge_margin']) for r in rows2]
    stats=mod.summarize_pairs(rows)
    assert stats['gold_positive']==1 and stats['gold_negative']==5
    effects=mod.interventions(head,hidden,eligible,spans,rows,caps,max_targets=1)
    assert effects
    assert {x['intervention'] for x in effects} >= {
        'encoder_src_zero','pair_projection_zero','coupler_src_zero'}
    assert all('correct_after' in x for x in effects)


def test_pairwise_synthetic_end_to_end(monkeypatch):
    head=little_head();hidden,eligible,spans=small_input()
    gold={'nodes':[{'id':'n0','span':[0,1],'kind':'actor'},
                   {'id':'n1','span':[2,3],'kind':'action'},
                   {'id':'n2','span':[4,5],'kind':'quantity'}],
          'edges':[{'src':'n0','dst':'n1','type':'owns'}]}
    pair={'id':'test-pair','mode':'renaming','family':'allocation',
          'base':{'prompt':'ABCDEF','graph':deepcopy(gold)},
          'fuzz':{'prompt':'ABCDXY','graph':deepcopy(gold)}}
    def capture(model,tok,prompt,device,max_length):
        return {'prompt':prompt,'prompt_start':0,'offsets':[(i,i+1) for i in range(6)],
                'eligible':eligible},hidden
    class DummyComposite:
        def predict(self,hidden,prep,**_):return {'token_spans':spans}
    monkeypatch.setattr(mod.prior,'_capture_inference',capture)
    data,comparison=mod.analyze_pair(pair,None,None,head,DummyComposite(),
                                   device='cpu',max_length=128,max_targets=1)
    assert len(data)==2 and comparison['expected_gold_invariant']
    assert data[0]['g1_exact'] and data[1]['g1_exact']
    assert data[0]['pair_metrics']['candidate_pairs']==6
    assert data[0]['interventions']
    summary=mod.summarize(data,[comparison])
    assert summary['totals']['cases']==2
    assert summary['gold_invariant_pairs']==1


def test_missing_checkpoint_refuses_without_output(tmp_path):
    with pytest.raises(ValueError,match='G2_0085_TRAINED_RUN_NOT_FOUND'):
        mod.run(root=tmp_path/'none',output=tmp_path/'output',device='cpu')
    assert not (tmp_path/'output').exists()
