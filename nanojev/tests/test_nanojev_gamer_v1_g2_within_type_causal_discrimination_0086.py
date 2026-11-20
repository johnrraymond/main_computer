"""G2-0086: same-source same-kind matching and stage-specific transplants."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest
import torch

from tools import nanojev_gamer_v1_g2_within_type_causal_discrimination_0086 as mod
from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as fuzz
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead


def small_head(coupled=True):
    torch.manual_seed(860086)
    return GamerGraphHead(hidden_size=8,width=16,layers=1,latent_slots=2,
       memory_slots=2,max_span_tokens=4,oracle_coupling=coupled).eval().requires_grad_(False)


def fixtures():
    hidden=torch.randn((10,8),generator=torch.Generator().manual_seed(860086))
    eligible=torch.ones(10,dtype=torch.bool)
    spans=[{'kind':'quantity','token_first':0,'token_last':0},
           {'kind':'outcome','token_first':2,'token_last':2},
           {'kind':'outcome','token_first':4,'token_last':4},
           {'kind':'outcome','token_first':6,'token_last':6},
           {'kind':'action','token_first':8,'token_last':8}]
    return hidden,eligible,spans


def fake_rows():
    def r(j,gold,logit,kind='outcome',src=0):
        return {'i':src,'j':j,'src':[0,1,'quantity'],
                'dst':[j*2,j*2+1,kind],
                'gold_label':gold,'pred_label':gold if gold else 0,
                'raw_logits':[0.,logit,-1.,-2.,-3.],
                'outcome':'tp' if gold else 'tn'}
    return [r(1,1,2),r(2,0,4),r(3,0,3),r(4,1,1,'action'),r(5,0,8,'action')]


def test_matched_groups_same_source_kind_label_and_strict_rank():
    groups=mod.matching_groups(fake_rows())
    assert len(groups)==2
    quant_outcome=next(x for x in groups if x['dst_kind']=='outcome')
    assert quant_outcome['binding_type']=='owns'
    assert quant_outcome['positives']==1 and quant_outcome['negatives']==2
    assert quant_outcome['weakest_true_index']==1 and quant_outcome['hardest_false_index']==2
    assert quant_outcome['worst_case_rank_margin']==-2
    assert not quant_outcome['all_true_above_false']
    assert set(mod.public_group(quant_outcome)).isdisjoint({'_true','_false'})
    assert mod.summarize_group_ranks(groups)['groups']==2


def test_group_rank_tie_is_not_a_success():
    rows=fake_rows();rows[1]['raw_logits'][1]=2;rows[2]['raw_logits'][1]=2
    group=next(x for x in mod.matching_groups(rows) if x['dst_kind']=='outcome')
    assert group['ambiguous_exact_tie']
    assert not group['all_true_above_false']


def test_multiple_gold_destinations_are_not_misclassified_as_negatives():
    rows=fake_rows();rows[2]['gold_label']=1
    group=next(x for x in mod.matching_groups(rows) if x['dst_kind']=='outcome')
    assert group['positives']==2 and group['negatives']==1
    assert group['best_true_index']==3
    assert group['weakest_true_index']==1


def test_reject_bad_pair_rows():
    rows=fake_rows();rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError,match='G2_0086_DUPLICATE_PAIR_INDICES'):
        mod.matching_groups(rows)
    rows=fake_rows();rows[0]['raw_logits']=[float('nan')]*5
    with pytest.raises(ValueError,match='G2_0086_INVALID_PAIR_LOGITS'):
        mod.matching_groups(rows)


def test_selection_priority_and_positive_control():
    groups=mod.matching_groups(fake_rows())
    chosen=mod.choose_targets(groups,1)
    assert len(chosen)==1 and chosen[0]['dst_kind']=='outcome'
    with pytest.raises(ValueError,match='G2_0086_INVALID_INTERVENTION_LIMIT'):
        mod.choose_targets(groups,-1)


def test_pair_input_capture_matches_native_modules_and_no_leaked_hook():
    head=small_head();h,e,spans=fixtures()
    output,caps=mod.traced_pair_input(head,h,e,spans)
    assert caps['pair_input'].shape==(7,7,head.width*4)
    assert caps['pair_projection'].shape==(7,7,head.width)
    assert torch.allclose(output['binding_logits'],caps['binding'])
    assert len(head.pair_projection._forward_pre_hooks)==0
    after,again=mod.traced_pair_input(head,h,e,spans)
    assert torch.allclose(again['pair_input'],caps['pair_input'])


def test_geometry_has_explicit_source_invariance_and_destination_contrast():
    head=small_head();h,e,spans=fixtures()
    _,caps=mod.traced_pair_input(head,h,e,spans)
    g={'src_index':0,'weakest_true_index':1,'hardest_false_index':2,
       'binding_label':1}
    contrast=mod.trace_contrast(caps,spans,g)
    assert contrast['pair_input']['channels']['source']['distance']==0
    assert contrast['pair_input']['channels']['destination']['distance']>0
    assert contrast['pair_input']['channels']['interaction']['distance']>0
    assert contrast['pair_projection']['whole_pair']['distance']>0
    assert contrast['readout']['typed_true_margin'] == pytest.approx(
       float(caps['binding'][0,1,1]-caps['binding'][0,1,0]))
    assert contrast['encoder']['source_same_for_both_pairs']


def test_pair_input_sham_and_target_only_transplant():
    head=small_head();h,e,spans=fixtures()
    _,caps=mod.traced_pair_input(head,h,e,spans)
    base=caps['binding']
    sham=mod._run_intervention(head,h,e,spans,stage='pair_input',
        from_index=2,to_index=2,pair_source=0,channels=(1,2,3))
    assert torch.allclose(sham,base,atol=1e-5)
    patched=mod._run_intervention(head,h,e,spans,stage='pair_input',
        from_index=1,to_index=2,pair_source=0,channels=(1,2,3))
    assert torch.allclose(patched[0,2],base[0,1],atol=1e-5)
    mask=torch.ones(base.shape[:2],dtype=torch.bool)
    mask[0,2]=False
    assert torch.allclose(patched[mask],base[mask],atol=1e-5)
    single=mod._run_intervention(head,h,e,spans,stage='pair_input',
        from_index=1,to_index=2,pair_source=0,channels=(1,))
    assert not torch.allclose(single[0,2],base[0,2])
    assert torch.allclose(single[mask],base[mask],atol=1e-5)
    assert len(head.pair_projection._forward_pre_hooks)==0


def test_early_stage_transplants_are_rerun_and_checkpoint_unchanged():
    head=small_head();h,e,spans=fixtures()
    original={k:v.clone() for k,v in head.state_dict().items()}
    _,caps=mod.traced_pair_input(head,h,e,spans)
    for stage in ('encoder','g1_g2_coupler'):
        changed=mod._run_intervention(head,h,e,spans,stage=stage,
          from_index=1,to_index=2,pair_source=0)
        assert changed.shape==caps['binding'].shape
        assert not torch.allclose(changed[0,2],caps['binding'][0,2])
    assert all(torch.equal(t,head.state_dict()[k]) for k,t in original.items())
    assert not head.encoder._forward_hooks and not head.pair_projection._forward_pre_hooks


def test_uncoupled_g2_omits_coupler_without_changing_other_stages():
    head=small_head(coupled=False);h,e,spans=fixtures()
    _,caps=mod.traced_pair_input(head,h,e,spans)
    assert 'g1_g2_coupler' not in caps
    g={'src_index':0,'weakest_true_index':1,'hardest_false_index':2,
       'binding_label':1,'binding_type':'owns','_other_false':None}
    result=mod.interventions(head,h,e,spans,caps,g)
    assert result
    assert all(r['stage']!='g1_g2_coupler' for r in result)
    with pytest.raises(ValueError,match='G2_0086_MISSING_COUPLER'):
        mod._run_intervention(head,h,e,spans,stage='g1_g2_coupler',
            from_index=1,to_index=2,pair_source=0)


def test_reciprocal_transplants_and_controls_dont_change_checkpoint():
    head=small_head();h,e,spans=fixtures()
    _,caps=mod.traced_pair_input(head,h,e,spans)
    original={k:v.clone() for k,v in head.state_dict().items()}
    g={'src_index':0,'weakest_true_index':1,'hardest_false_index':2,
       'binding_label':1,'binding_type':'owns','_other_false':{'j':3}}
    changes=mod.interventions(head,h,e,spans,caps,g)
    assert {x['direction'] for x in changes} >= {'true_to_false','false_to_true',
         'sham_false','negative_control'}
    sham=next(r for r in changes if r['direction']=='sham_false')
    assert sham['delta_typed_margin']==pytest.approx(0,abs=1e-6)
    transfer=next(r for r in changes if r['direction']=='true_to_false' and
                  r['operation']=='all_destination_channels')
    assert transfer['donor_margin_transfer_fraction']==pytest.approx(1.,abs=1e-5)
    assert all(r['other_pair_outputs_unchanged'] for r in changes if r['stage']=='pair_input')
    assert all(torch.equal(t,head.state_dict()[k]) for k,t in original.items())
    assert len(head.pair_projection._forward_pre_hooks)==0
    assert len(head.encoder._forward_hooks)==0


def test_bad_stage_and_channel_mask_fail_closed():
    head=small_head();h,e,spans=fixtures()
    with pytest.raises(ValueError,match='G2_0086_BAD_INTERVENTION_STAGE'):
        mod._run_intervention(head,h,e,spans,stage='unknown',
            from_index=1,to_index=2,pair_source=0)
    with pytest.raises(ValueError,match='G2_0086_BAD_CHANNEL_MASK'):
        mod._run_intervention(head,h,e,spans,stage='pair_input',
            from_index=1,to_index=2,pair_source=0,channels=(4,))
    with pytest.raises(ValueError,match='G2_0086_BAD_INTERVENTION_INDEX'):
        mod._run_intervention(head,h,e,spans,stage='encoder',
            from_index=999,to_index=2,pair_source=0)



def test_missing_inputs_refuse_without_output(tmp_path):
    with pytest.raises(ValueError,match='G2_0086_G2_0083_NOT_FOUND'):
        mod.run(root=tmp_path/'missing',output=tmp_path/'new',device='cpu')
    assert not (tmp_path/'new').exists()


def test_replay_does_not_tolerate_changed_raw_logits():
    row=fake_rows()[0];case={'pair_metrics':{'candidate_pairs':1}}
    mod._assert_replay([row],{'scores':[deepcopy(row)]},case)
    changed=deepcopy(row);changed['raw_logits'][1]+=0.01
    with pytest.raises(ValueError,match='G2_0086_REPLAY_LOGIT_DRIFT'):
        mod._assert_replay([row],{'scores':[changed]},case)


def test_end_to_end_matched_native_head_does_not_read_gold_during_inference():
    head=small_head();hidden,eligible,spans=fixtures()
    prepared={'prompt':'abcdefghij','prompt_start':0,
              'offsets':[(i,i+1) for i in range(10)],'eligible':eligible}
    gold={'nodes':[
        {'id':'n0','span':[0,1],'kind':'quantity'},
        {'id':'n1','span':[2,3],'kind':'outcome'},
        {'id':'n2','span':[4,5],'kind':'outcome'},
        {'id':'n3','span':[6,7],'kind':'outcome'},
        {'id':'n4','span':[8,9],'kind':'action'}],
        'edges':[{'src':'n0','dst':'n1','type':'owns'}]}
    _,caps=mod.traced_pair_input(head,hidden,eligible,spans)
    baseline=caps['binding'].clone()
    rows=fuzz._pair_stats(baseline,spans,prepared,gold)
    groups=mod.matching_groups(rows)
    assert len(groups)==1 and groups[0]['dst_kind']=='outcome'
    result=mod.trace_contrast(caps,spans,groups[0])
    assert result['pair_input']['channels']['source']['distance']==0
    effects=mod.interventions(head,hidden,eligible,spans,caps,groups[0])
    assert effects and all(torch.isfinite(torch.tensor(r['intervened_typed_margin'])) for r in effects)
    # Changing the gold annotation cannot change the forward representation.
    changed=deepcopy(gold);changed['edges']=[]
    assert all(r['gold_label']==0 for r in fuzz._pair_stats(baseline,spans,prepared,changed))
    again,_=mod.traced_pair_input(head,hidden,eligible,spans)
    assert torch.equal(again['binding_logits'],baseline)


def test_evidence_join_validated_before_gpu_trace_and_tamper_fails(tmp_path):
    e=tmp_path/'old';e.mkdir()
    prompt1='abcdef';prompt2='ABCDEF'
    score=fake_rows()
    for r in score:r['edge_margin']=r['raw_logits'][1]-r['raw_logits'][0]
    m=fuzz.summarize_pairs(score)
    rows=[];scores=[]
    for side,prompt in (('base',prompt1),('fuzz',prompt2)):
        digest=hashlib.sha256(prompt.encode()).hexdigest()
        rows.append({'pair_id':'g2-0085-0001','side':side,
                     'prompt':prompt,'prompt_sha256':digest,'pair_metrics':m})
        scores.append({'pair_id':'g2-0085-0001','side':side,
                       'prompt_sha256':digest,'scores':score})
    report={'schema':fuzz.SCHEMA,
            'status':'FROZEN_G2_ACTIVATION_FUZZ_DIAGNOSIS_ONLY',
            'checkpoint_sha256':'abc','source_code_sha256':'src',
            'pairs':1,'no_train':True,'no_checkpoint_mutation':True}
    mod.prior.write_json(e/'report.json',report)
    mod.prior.write_jsonl(e/'cases.jsonl',rows)
    mod.prior.write_jsonl(e/'pair-scores.jsonl',scores)
    proof,cases,pairs,hashes=mod.verify_evidence(e,'abc','src')
    assert len(cases)==2 and len(pairs)==2
    assert all(len(x)==64 for x in hashes.values())
    rows[0]['prompt_sha256']='0'*64
    (e/'cases.jsonl').unlink()
    mod.prior.write_jsonl(e/'cases.jsonl',rows)
    with pytest.raises(ValueError,match='G2_0086_G2_0085_CASE_JOIN'):
        mod.verify_evidence(e,'abc','src')
    with pytest.raises(ValueError,match='G2_0086_G2_0085_EVIDENCE_PROVENANCE'):
        mod.verify_evidence(e,'other-head','src')


def test_exact_cached_pair_readout_equals_real_native_full_forward_for_every_channel():
    head=small_head();h,e,spans=fixtures()
    _,caps=mod.traced_pair_input(head,h,e,spans)
    for channels in mod.PAIR_CHANNELS.values():
        native=mod._run_intervention(head,h,e,spans,stage='pair_input',
            from_index=1,to_index=2,pair_source=0,channels=channels)
        cached=mod._local_pair_input_swap(head,caps,from_index=1,to_index=2,
            pair_source=0,channels=channels)
        assert torch.allclose(native,cached,atol=1e-6,rtol=1e-6)


def test_counterfactual_ranks_require_author_alignment_and_exact_g1():
    template={'pair_id':'a','source_author_id':'source','dst_kind':'outcome',
        'binding_type':'owns','true_destination_author_id':'truth','positives':1,
        'false_destination_author_id':'wrong1','worst_case_rank_margin':-.3,
        'all_true_above_false':False}
    a={'side':'base',**template}
    b={'side':'fuzz',**template,'false_destination_author_id':'wrong2',
       'worst_case_rank_margin':.5,'all_true_above_false':True}
    cases=[{'pair_id':'a','side':side,'mode':'renaming','g1_exact':True}
           for side in ('base','fuzz')]
    stat=mod.counterfactual_rank_stability([a,b],cases)
    assert stat['all']['compared']==1
    assert stat['all']['rank_status_flipped']==1
    assert stat['all']['hardest_false_identity_changed']==1
    cases[1]['g1_exact']=False
    assert mod.counterfactual_rank_stability([a,b],cases)['all']['compared']==0
    cases[1]['g1_exact']=True
    b['true_destination_author_id']='different'
    assert mod.counterfactual_rank_stability([a,b],cases)['all']['compared']==0


def test_aggregate_stage_geometry_and_interventions_are_numeric():
    head=small_head();h,e,spans=fixtures()
    _,caps=mod.traced_pair_input(head,h,e,spans)
    g={'src_index':0,'weakest_true_index':1,'hardest_false_index':2,
        'binding_label':1,'binding_type':'owns','_other_false':{'j':3},
        'all_true_above_false':False}
    trial={'group':mod.public_group(g),
         'stage_contrast':mod.trace_contrast(caps,spans,g),
         'interventions':mod.interventions(head,h,e,spans,caps,g)}
    result=mod._aggregate([trial])
    assert result['intervened_groups']==1
    assert result['inverted_ranks']==1
    assert result['stage_destination_or_pair_geometry']['encoder']['n']==1
    assert result['by_intervention']['true_to_false:all_destination_channels'][
        'mean_margin_transfer_fraction']==pytest.approx(1.,abs=1e-5)


def test_aggregate_consumes_exact_match_records_schema_from_run():
    # Regression: run() writes one group per match to matches.jsonl, not a
    # per-case `groups` array. The previous mismatch crashed after all CUDA
    # traces had finished, before any result files were written.
    head=small_head();h,e,spans=fixtures()
    _,caps=mod.traced_pair_input(head,h,e,spans)
    g={'src_index':0,'weakest_true_index':1,'hardest_false_index':2,
       'binding_label':1,'binding_type':'owns','_other_false':{'j':3},
       'all_true_above_false':False}
    trace=mod.trace_contrast(caps,spans,g)
    swaps=mod.interventions(head,h,e,spans,caps,g)
    actual={'pair_id':'g2-0085-0001','side':'base',
            'prompt_sha256':'a'*64,'g1_exact':True,
            'group':mod.public_group(g),
            'stage_contrast':trace,'interventions':swaps}
    report=mod._aggregate([actual,actual])
    assert report['intervened_groups']==2
    assert report['inverted_ranks']==2
    assert report['stage_destination_or_pair_geometry']['pair_input']['n']==2
    assert report['by_intervention']['true_to_false:all_destination_channels']['n']==2
    assert report['by_intervention']['sham_false:self_pair_input_control']['n']==2
    assert report['by_intervention']['sham_false:self_pair_input_control'][
        'mean_delta_typed_margin']==pytest.approx(0,abs=1e-6)


def test_aggregate_rejects_obsolete_or_corrupt_match_schema():
    bad={'groups':[{'all_true_above_false':False}],
         'stage_contrast':{},'interventions':[]}
    with pytest.raises(ValueError,match='G2_0086_INVALID_MATCH_RECORD'):
        mod._aggregate([bad])
    bad={'group':{'all_true_above_false':0},
         'stage_contrast':{},'interventions':[]}
    with pytest.raises(ValueError,match='G2_0086_INVALID_MATCH_RECORD'):
        mod._aggregate([bad])
