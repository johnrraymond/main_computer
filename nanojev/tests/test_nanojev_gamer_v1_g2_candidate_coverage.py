"""G2-0035 bounded G1/G2 candidate-coverage intervention contracts."""
from copy import deepcopy
from pathlib import Path
import json
import inspect
import sys
import subprocess

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_candidate_coverage as m


def test_fixed_bounded_arms_and_no_train_gold_in_intervention():
    assert m.ARMS=={'baseline':(False,24),
        'endpoint_recovery':(True,24),
        'relationship_expansion':(False,48),
        'joint_coverage':(True,48)}
    assert m.MAX_EXTRA_SPANS==16
    for part in (m.recover_predicted_spans,m._scores,m.capture_variants,m.run):
        source=inspect.getsource(part)
        assert 'torch.optim.' not in source
        assert 'fit_models(' not in source
    assert 'verify_parent34(' in inspect.getsource(m.run)
    assert 'PARENT_0034_LIVE_REPLAY_DRIFT' in inspect.getsource(m.analyze_variants)


def test_g1_neural_recovery_is_monotonic_bounded_deterministic_and_gold_blind(monkeypatch):
    prompt='ABCDEFGHIJKLMNO'
    prepared={'offsets':[(i,i+1) for i in range(len(prompt))],
              'prompt_start':0,'prompt':prompt}
    original=[{'token_first':2,'token_last':3,'kind':'action'}]
    candidates={(2,3,'action'):8.,(4,5,'action'):7.,(6,7,'action'):6.,
                (7,8,'action'):5.,(9,10,'action'):4.}
    monkeypatch.setattr(m.g1diag,'frozen_stages',lambda *_:{'all_catalog':candidates})
    g1_output={}
    result=m.recover_predicted_spans(g1_output,prepared,original,max_extra=2)
    assert len(result)==3
    assert set(m.base.predicted_anchors(original,prepared))<=set(m.base.predicted_anchors(result,prepared))
    assert m.recover_predicted_spans(g1_output,prepared,original,max_extra=2)==result
    assert m.recover_predicted_spans(g1_output,prepared,original,max_extra=0)==original
    # A competing overlapping span may be included, preserving recoverability.
    assert (4,6,'action') in m.base.predicted_anchors(result,prepared)
    with pytest.raises(ValueError,match='BAD_ENDPOINT_BUDGET'):
        m.recover_predicted_spans(g1_output,prepared,original,max_extra=-1)


def test_source_inventory_topk_expansion_recovers_out_of_top24_without_gold():
    src=m.source13
    kind=m.x31.base.BIND_CLASSES[1]
    # 30 identical-kind nodes, candidate at rank 27 excluded by K=24.
    anchors=[(3*i,3*i+1,'actor') for i in range(30)]
    score=torch.zeros(30,30,len(src.BIND_CLASSES));score[...,0]=0.
    for j in range(1,30):
        score[0,j,1]=30-j
    allowed={(kind,'actor','actor')}
    case={'anchors':anchors,'vectors':torch.randn(30,8),
          'gold':{(anchors[0],anchors[27],kind)}}
    old=src.make_inventory(case,score,allowed,top_k=24,train_gold=False)
    wide=src.make_inventory(case,score,allowed,top_k=48,train_gold=False)
    gold=next(iter(case['gold']))
    assert gold not in set(old['edges'])
    assert gold in set(wide['edges'])
    assert set(old['edges'])<=set(wide['edges'])
    # Even changing offline labels must not change the proposed inventory.
    case['gold']={gold,(anchors[2],anchors[5],kind)}
    again=src.make_inventory(case,score,allowed,top_k=48,train_gold=False)
    assert wide['edges']==again['edges']


def test_parent34_refuses_corrupt_contract(tmp_path):
    (tmp_path/m.x34.REPORT).write_text(json.dumps({'schema':'bad'}))
    with pytest.raises(ValueError,match='PARENT_0034_CONTRACT:schema'):
        m.verify_parent34(tmp_path,ancestors={},train_sha='',old_dev={},fresh34={})


def test_coverage_splits_endpoint_and_inventory_lost_edges():
    a=(0,1,'actor');b=(2,3,'actor');c=(4,5,'actor');z=(6,7,'actor')
    gold={(a,b,'rel'),(a,c,'rel'),(a,z,'rel')}
    common={'gold':gold,'gold_total':3}
    base={**common,'edges':[(a,b,'rel')]}
    widened={**common,'edges':[(a,b,'rel'),(a,c,'rel')]}
    recovered={**common,'edges':[(a,b,'rel'),(a,z,'rel')]}
    cb=m.x34.candidate_ceiling(base,[a,b,c]);cb.pop('candidate_oracle')
    cw=m.x34.candidate_ceiling(widened,[a,b,c]);cw.pop('candidate_oracle')
    ce=m.x34.candidate_ceiling(recovered,[a,b,c,z]);ce.pop('candidate_oracle')
    assert cb['missing_g1_endpoint']==1 and cb['endpoints_present_but_not_in_inventory']==1
    assert cw['missing_g1_endpoint']==1 and cw['endpoints_present_but_not_in_inventory']==0
    assert ce['missing_g1_endpoint']==0 and ce['endpoints_present_but_not_in_inventory']==1
    row={'coverage':{k:{**cb,'g1_anchor_count':3,'added_anchors':0,
          'new_candidates':0,'displaced_candidates':0,
          'new_gold_candidates':0,'displaced_gold_candidates':0,
          'pre_shortlist_candidate_gold':cb['represented_gold'],
          'pre_shortlist_candidate_count':len(base['edges']),
          'lost_gold_during_shortlisting':0} for k in m.ARMS}}
    row['coverage']['relationship_expansion']={**cw,'added_anchors':0,'new_candidates':1,
        'displaced_candidates':0,'new_gold_candidates':1,'displaced_gold_candidates':0,
        'pre_shortlist_candidate_gold':2,'pre_shortlist_candidate_count':2,
        'lost_gold_during_shortlisting':0}
    row['coverage']['endpoint_recovery']={**ce,'added_anchors':1,'new_candidates':1,
        'displaced_candidates':0,'new_gold_candidates':1,'displaced_gold_candidates':0,
        'pre_shortlist_candidate_gold':2,'pre_shortlist_candidate_count':2,
        'lost_gold_during_shortlisting':0}
    agg=m.summarize_coverage([row])
    assert agg['baseline']['missing_gold']==2
    assert agg['endpoint_recovery']['missing_g1_endpoint']==0
    assert agg['relationship_expansion']['endpoints_present_but_not_in_inventory']==0


def test_end_to_end_mock_writes_all_four_arms_no_training(monkeypatch,tmp_path):
    row={'prompt':'unseen synthetic prompt','family':'encounter','split':'dev',
         'structural_topology':'native','graph':{'nodes':[],'edges':[]}}
    a=(0,1,'actor');b=(3,4,'actor');c=(6,7,'actor')
    gold={(a,b,'rel')}
    def ccase(anchors):
        return {'anchors':anchors,'windows':torch.zeros(len(anchors),5,8),
            'window_mask':torch.ones(len(anchors),5,dtype=torch.bool),
            'vectors':torch.zeros(len(anchors),8),'gold':gold}
    captured={'baseline':ccase([a,b]),'expanded':ccase([a,b,c])}
    state={'ancestors':{},'training':[], 'history29':[], 'fresh29':{},
           'head':object(),'head_before':object(), 'model':torch.nn.Identity(),
           'reference':torch.nn.Identity(),'reference_before':{},
           'tokenizer':object(),'models29':{}}
    parent_policy={'source':'occurrence_graph_s460331','alpha':1.,'threshold':.93,
                   'margin':2.,'reference_gate':None}
    pol30={'calibrated_reference':parent_policy,m.x31.CHAMPION:parent_policy}
    pol31={'calibrated_reference':parent_policy,
           **{f'occurrence_graph_s{s}':{**parent_policy,'source':f'occurrence_graph_s{s}'} for s in m.SEEDS}}
    pol32={f'group_recovery_s{s}':{'source':f'occurrence_graph_s{s}'} for s in m.SEEDS}
    pol33={f'structured_set_s{s}':{'source':f'occurrence_graph_s{s}',
        'enabled':True,'fallback':f'group_recovery_s{s}',
        'group_weight':0.,'global_weight':0.} for s in m.SEEDS}
    fakeitem={'edges':[(a,b,'rel'),(a,c,'rel')],
       'gold':gold,'gold_total':1,'candidate_gold':1,'candidate_count':2,
       'groups':np.array([0,0]),'raw':{f'occurrence_graph_s{s}':np.array([2.,-2.]) for s in m.SEEDS}}
    monkeypatch.setattr(m.p30,'_prepare',lambda **kwargs:state)
    monkeypatch.setattr(m.p30,'calibration_and_dev',lambda *args:([],{}))
    monkeypatch.setattr(m.x31,'verify_parent30',lambda *a,**k:(None,pol30,None,{}))
    monkeypatch.setattr(m.x31,'fresh_cohorts',lambda *a:([],{}))
    monkeypatch.setattr(m.x32,'verify_parent31',lambda *a,**k:(None,pol31,None,{},{}))
    monkeypatch.setattr(m.x32,'fresh_cohorts',lambda *a:(([],[],[]),{}))
    monkeypatch.setattr(m.x33,'verify_parent32',lambda *a,**k:(None,pol32,None,{},{}))
    monkeypatch.setattr(m.x33,'fresh_cohorts',lambda *a:(([],[],[]),{}))
    monkeypatch.setattr(m.x34,'verify_parent33',lambda *a,**k:(None,pol33,None,{},{}))
    monkeypatch.setattr(m.x34,'fresh_cohorts',lambda *a:{k:[row] for k in m.REPLAY})
    monkeypatch.setattr(m,'verify_parent34',lambda *a,**k:({k:[{'stub':True}] for k in m.REPLAY},{}))
    monkeypatch.setattr(m,'fresh_cohorts',lambda *a:{k:[row] for k in m.NEW})
    monkeypatch.setattr(m.x32,'load_parent_models',lambda *a,**k:{s:{} for s in m.SEEDS})
    monkeypatch.setattr(m.x33,'load_parent32_models',lambda *a,**k:{s:(torch.nn.Identity().eval().requires_grad_(False),torch.nn.Identity().eval().requires_grad_(False)) for s in m.SEEDS})
    monkeypatch.setattr(m.x34,'load_parent33_models',lambda *a,**k:{s:torch.nn.Identity().eval().requires_grad_(False) for s in m.SEEDS})
    monkeypatch.setattr(m,'capture_variants',lambda *a,**k:[deepcopy(captured)])
    monkeypatch.setattr(m.x31,'example_from_case',lambda *a,**k:{'stub':True})
    monkeypatch.setattr(m.x31,'score_case',lambda *a,**k:deepcopy(fakeitem))
    monkeypatch.setattr(m,'_scores',lambda item,arm,*args:
        ({f'{arm}__{name}_s{seed}':{(a,b,'rel')}
          for name in ('structured','group') for seed in m.SEEDS},
         {f'{arm}__{name}_s{seed}':np.array([2.,-2.])
          for name in ('structured','group') for seed in m.SEEDS}))
    monkeypatch.setattr(m.x33,'evaluate_case',lambda *a,**k:{'stub':True})
    # Separate replay drift guard is exercised in another test.
    original=m.analyze_variants
    monkeypatch.setattr(m,'analyze_variants',lambda *a,**k:
        original(*a,**{**k,'parent34':None}))
    monkeypatch.setattr(m.x31.rel,'validate_original_state',lambda *a:None)
    directory=tmp_path/'candidate35'
    report=m.run(precision30_run='x',representation31_run='x',
        completeness32_run='x',structured33_run='x',diagnosis34_run='x',
        output=str(directory),device='cpu')
    assert report['evaluation']['new_0035_dev']['cases']==2
    assert report['coverage_by_cohort']['new_0035_dev']['baseline']['cases']==2
    assert (directory/m.REPORT).is_file() and (directory/m.LEDGER).is_file()
    assert report['new_optimizer_steps']==0 and report['production_promotion'] is False
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(precision30_run='x',representation31_run='x',
           completeness32_run='x',structured33_run='x',diagnosis34_run='x',
           output=str(directory),device='cpu')


def test_cli_parses_and_matches_root_relative_output():
    root=Path(__file__).resolve().parents[1]
    text=(root/'tools'/'nanojev_gamer_v1_graph_curriculum.py').read_text()
    assert "sub.add_parser('frob-g2-candidate-coverage'" in text
    assert "from tools.nanojev_gamer_v1_g2_candidate_coverage import run" in text
    assert "'diagnosis34_run':'graph-g2-causal-graph-diagnosis-0034'" in text
    cp=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-candidate-coverage','--help'],cwd=str(root),capture_output=True,text=True)
    assert cp.returncode==0 and '--diagnosis34-run' in cp.stdout


def test_capture_variants_reuses_one_frozen_backbone_and_preserves_base(monkeypatch):
    import torch.nn as nn
    prepared={'prompt':'abc def ghi','offsets':[(i,i+1) for i in range(11)],
        'prompt_start':0,'eligible':torch.ones(11,dtype=torch.bool)}
    calls={'backbone':0,'g1':0,'g2':0}
    class Head(nn.Module):
        def forward(self,hidden,*,eligible,active_stage,training_spans=None):
            if active_stage=='G1':
                calls['g1']+=1
                return {'span_logits':torch.zeros(11,1,2),'span_valid':torch.ones(11,1,dtype=torch.bool)}
            calls['g2']+=1
            return {'node_vectors':torch.stack([
                hidden[s['token_first']:s['token_last']+1].mean(0)
                for s in training_spans])}
    head=Head();backbone=nn.Identity()
    def get_hidden(*a,**k):
        calls['backbone']+=1
        return torch.ones(11,8)
    monkeypatch.setattr(m.backbone,'prepare_prompt',lambda *a,**k:prepared)
    monkeypatch.setattr(m.backbone,'frozen_context',get_hidden)
    original=[{'kind':'actor','token_first':0,'token_last':2}]
    extra=[*original,{'kind':'actor','token_first':4,'token_last':6}]
    monkeypatch.setattr(m.base,'predicted_spans',lambda *a:original)
    monkeypatch.setattr(m,'recover_predicted_spans',lambda *a,**k:extra)
    monkeypatch.setattr(m.base,'gold_binding_edges',lambda *a:set())
    output=m.capture_variants(head,backbone,object(),[{'prompt':'abc def ghi','family':'encounter'}],
                             device='cpu',max_length=m.MAX_LENGTH,group='synthetic')
    assert calls=={'backbone':1,'g1':1,'g2':2}
    result=output[0]
    assert result['baseline']['anchors']==[(0,3,'actor')]
    assert result['expanded']['anchors']==[(0,3,'actor'),(4,7,'actor')]
    assert torch.equal(result['baseline']['vectors'][0],result['expanded']['vectors'][0])
    assert result['baseline']['windows'].shape==(1,5,8)
    assert result['expanded']['windows'].shape==(2,5,8)
    assert result['expanded']['windows'].requires_grad is False


def test_development_gates_require_gold_coverage_and_all_seed_downstream_preservation():
    from copy import deepcopy
    models={}
    for arm in m.ARMS:
        for seed in m.SEEDS:
            for mode in ('structured','group'):
                models[f'{arm}__{mode}_s{seed}']={
                    'f1':.7,'exact_cases':10,'ranking_failure_rate':.1}
    fresh={'models':models}
    ood={'models':deepcopy(models)}
    coverage={arm:{'represented_gold':100,'fully_representable':4} for arm in m.ARMS}
    coverage['joint_coverage']={'represented_gold':110,'fully_representable':8}
    passed=m.development_gates(fresh,ood,coverage)
    assert passed['arms']['joint_coverage']['all_seeds_nonregressing'] is True
    assert passed['arms']['endpoint_recovery']['all_seeds_nonregressing'] is False
    fresh['models'][f'joint_coverage__group_s{m.SEEDS[0]}']['f1']=.69
    assert m.development_gates(fresh,ood,coverage)['arms']['joint_coverage']['all_seeds_nonregressing'] is False


def test_live_parent34_replay_compares_common_controls_not_diagnostic_metrics(monkeypatch):
    a=(0,1,'actor');b=(2,3,'actor');edge=(a,b,'rel')
    row={'prompt':'synthetic','family':'encounter','structural_topology':'native'}
    one={'anchors':[a,b],'gold':{edge}}
    cases={'baseline':one,'expanded':one}
    item={'edges':[edge],'gold':{edge},'gold_total':1,'candidate_gold':1,
          'candidate_count':1,'groups':np.array([0]),
          'raw':{f'occurrence_graph_s{s}':np.array([2.]) for s in m.SEEDS}}
    expected={'group':'replay','index':1,'prompt_sha256':m.base._sha(row['prompt']),
              'family':row['family'],'structural_topology':'native',
              'candidate_gold':1,'candidate_count':1,'gold_total':1,
              'metrics':{'parent_control':{'correct':1,'missing':0}}}
    parent={**deepcopy(expected),'metrics':{**deepcopy(expected['metrics']),
         'oracle_group_counts':{'correct':1,'missing':0}}}
    monkeypatch.setattr(m.x31,'example_from_case',lambda *a,**k:{})
    monkeypatch.setattr(m.x31,'score_case',lambda *a,**k:deepcopy(item))
    monkeypatch.setattr(m,'_scores',lambda item,arm,*a:({
          f'{arm}__structured_s{s}':{edge} for s in m.SEEDS},
          {f'{arm}__structured_s{s}':np.array([2.]) for s in m.SEEDS}))
    monkeypatch.setattr(m.x33,'evaluate_case',lambda *a,**k:deepcopy(expected))
    result=m.analyze_variants(cases,row,{'reference':object()},None,{},None,None,None,None,
        split='replay',index=1,parent34=parent)
    assert result['coverage']['baseline']['candidate_complete'] is True
    parent['metrics']['parent_control']['correct']=0
    with pytest.raises(ValueError,match='PARENT_0034_LIVE_REPLAY_DRIFT'):
        m.analyze_variants(cases,row,{'reference':object()},None,{},None,None,None,None,
            split='replay',index=1,parent34=parent)


def test_shortlist_preserves_frozen_24_capacity_and_can_rescue_low_rank_candidate():
    a=(0,1,'actor');dst=[(i+2,i+3,'actor') for i in range(32)]
    edges=[(a,b,'rel') for b in dst]
    base_scores=np.arange(32,0,-1,dtype=np.float32)
    learned=base_scores.copy()
    learned[29]=100. # candidate at raw rank 30 is favored by learned G2
    raw={m.x31.REFERENCE:base_scores.copy(),
         **{f'occurrence_graph_s{s}':learned.copy() for s in m.SEEDS}}
    item={'edges':edges,'groups':np.zeros(32,dtype=np.int32),
          'raw':raw,'gold':{edges[29]},'candidate_gold':1,'candidate_count':32}
    chosen=m.shortlist_for_frozen_decoder(item)
    assert len(chosen['edges'])==24
    assert edges[29] in chosen['edges']
    assert edges[23] not in chosen['edges']
    assert all(len(values)==24 for values in chosen['raw'].values())
    other=deepcopy(item);other['gold']=set(edges[:2])
    assert m.shortlist_for_frozen_decoder(other)['edges']==chosen['edges']
    assert len(m.shortlist_for_frozen_decoder(item,capacity=48)['edges'])==32


def test_structured_model_runs_with_consensus_shortlist_and_full_frozen_policies():
    a=(0,1,'actor');b=(2,3,'actor');c=(4,5,'actor')
    edges=[(a,b,'rel'),(a,c,'rel')]
    reference=np.array([5.,-5.],dtype=np.float32)
    raw={m.x31.REFERENCE:reference.copy(),
         **{f'occurrence_graph_s{s}':reference.copy() for s in m.SEEDS}}
    item={'edges':edges,'groups':np.array([0,0],dtype=np.int32),
          'raw':raw,'gold':{edges[0]},'gold_total':1,
          'candidate_gold':1,'candidate_count':2}
    policies={f'occurrence_graph_s{s}':dict(source=f'occurrence_graph_s{s}',
        alpha=1.,threshold=.93,margin=2.,reference_gate=None) for s in m.SEEDS}
    recovery={f'group_recovery_s{s}':dict(source=f'occurrence_graph_s{s}',
        enabled=False,mode='disabled',edge_prob=1.,confidence=1.,
        group_gap=0.,max_additions=0,offset=0.) for s in m.SEEDS}
    struct={f'structured_set_s{s}':dict(source=f'occurrence_graph_s{s}',
        enabled=True,group_weight=0.,global_weight=0.,fallback=f'group_recovery_s{s}')
        for s in m.SEEDS}
    nets32={s:(m.x32.GroupCardinality().eval().requires_grad_(False),
               m.x32.GraphCardinality().eval().requires_grad_(False)) for s in m.SEEDS}
    nets33={s:m.x33.GraphSetEnergy().eval().requires_grad_(False) for s in m.SEEDS}
    selections,ranking=m._scores(item,'baseline',3,policies,recovery,nets32,nets33,struct)
    assert len(selections)==6
    assert all(v<=set(edges) for v in selections.values())
    assert all(len(v)==2 for v in ranking.values())
    copy=deepcopy(item);copy['gold']=set(edges)
    second,_=m._scores(copy,'baseline',3,policies,recovery,nets32,nets33,struct)
    assert second==selections


def test_wide_reference_respects_frozen_top24_contract_and_preserves_head_scores(monkeypatch):
    """Regression: the *real* 48-candidate path must not reach G2-0023 as 48."""
    src=m.source13
    anchors=[(3*i,3*i+1,'actor') for i in range(30)]
    prompt='x'*100
    relation=src.BIND_CLASSES[1]
    logits=torch.full((30,30,len(src.BIND_CLASSES)),-5.)
    logits[...,0]=0.
    for i in range(30):
        for j in range(30):
            if i!=j:logits[i,j,1]=10.-j*.1
    monkeypatch.setattr(m.x31.rel,'full_logits',lambda *_:logits)
    class CappedReference(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.inner=m.x31.m29.parent23.RelationalContextScorer(width=8)
            self.calls=[]
        def forward(self,x,b,groups,**kwargs):
            from collections import Counter
            width=max(Counter(groups).values(),default=0)
            self.calls.append(width)
            assert width<=24, 'widened group escaped the bounded reference adapter'
            return self.inner(x,b,groups,**kwargs)
    frozen=CappedReference().eval().requires_grad_(False)
    gold={(anchors[0],anchors[27],relation)}
    case={'anchors':anchors,'vectors':torch.randn(30,8),'gold':gold,
          'prompt_sha256':m.base._sha(prompt),'index':1,
          'windows':torch.ones(30,m.x31.WINDOW,8),
          'window_mask':torch.ones(30,m.x31.WINDOW,dtype=torch.bool)}
    row={'prompt':prompt,'family':'encounter'}
    state={'reference':frozen,'head':object(),'allowed':{(relation,'actor','actor')}}
    with torch.inference_mode():
        baseline=m.x31.example_from_case(case,row,state,top_k=24)
        with pytest.raises(AssertionError,match='widened group'):
            m.x31.example_from_case(case,row,state,top_k=48)
        frozen.calls.clear()
        widened_state=m._scoring_state_for_topk(state,48)
        widened=m.x31.example_from_case(case,row,widened_state,top_k=48)
        again=m.x31.example_from_case({**case,'gold':set()},row,widened_state,top_k=48)
    assert len(widened['edges'])>len(baseline['edges'])
    assert gold.isdisjoint(baseline['edges']) and gold<=set(widened['edges'])
    wide_ref=dict(zip(widened['edges'],widened['reference'].tolist()))
    assert all(abs(wide_ref[e]-score)<1e-5
               for e,score in zip(baseline['edges'],baseline['reference'].tolist()))
    assert torch.equal(widened['reference'],again['reference'])
    assert max(frozen.calls)==24
    assert m._scoring_state_for_topk(state,24) is state
    with pytest.raises(ValueError,match='UNSUPPORTED_INVENTORY_WIDTH'):
        m._scoring_state_for_topk(state,49)


def test_bounded_reference_is_ordered_finite_and_contextual_for_tail():
    class Context:
        def __call__(self,x,base,groups,**kwargs):
            from collections import Counter
            assert max(Counter(groups).values(),default=0)<=24
            result=[]
            for idx,g in enumerate(groups):
                result.append(float(x[idx,0])+float(base[idx]) +
                              sum(float(x[j,0]) for j,h in enumerate(groups) if h==g)/100.)
            return torch.tensor(result,dtype=x.dtype,device=x.device)
    ref=m._BoundedWideReference(Context())
    x=torch.arange(52,dtype=torch.float32).reshape(-1,1)
    b=torch.arange(52,dtype=torch.float32)*.1
    groups=[0]*48+[1]*4
    result=ref(x,b,groups)
    assert result.shape==(52,) and torch.isfinite(result).all()
    expected_head=Context()(x[:24],b[:24],[0]*24)
    assert torch.equal(result[:24],expected_head)
    assert torch.equal(result[48:],Context()(x[48:],b[48:],[1]*4))
    # The second 24 scores are not silently discarded or filled with zero.
    assert torch.all(result[24:48]!=0)
    changed=x.clone();changed[47,0]=10000
    assert torch.equal(ref(changed,b,groups)[:24],result[:24])
    with pytest.raises(ValueError,match='GROUP_EXCEEDS_WIDE_PROTOCOL'):
        ref(torch.arange(49,dtype=torch.float32).reshape(-1,1),
            torch.zeros(49),[0]*49)


def test_wide_inventory_reaches_frozen_occurrence_score_and_24_decoder_shortlist(monkeypatch):
    """Exercise real G2-0031 score_case, not a mock that hides the oversized group."""
    anchors=[(i*3,i*3+1,'actor') for i in range(30)]
    relation=m.source13.BIND_CLASSES[1]
    prompt='x'*100
    logits=torch.full((30,30,len(m.source13.BIND_CLASSES)),-4.)
    logits[...,0]=0.
    for i in range(30):
        for j in range(30):
            if i!=j:logits[i,j,1]=10-j*.1
    monkeypatch.setattr(m.x31.rel,'full_logits',lambda *_:logits)
    case={'anchors':anchors,'vectors':torch.randn(30,8),
          'gold':{(anchors[0],anchors[27],relation)},
          'prompt_sha256':m.base._sha(prompt),'index':1,
          'windows':torch.ones(30,m.x31.WINDOW,8),
          'window_mask':torch.ones(30,m.x31.WINDOW,dtype=torch.bool)}
    row={'prompt':prompt,'family':'encounter'}
    reference=m.x31.m29.parent23.RelationalContextScorer(8).eval().requires_grad_(False)
    state={'head':object(),'reference':reference,'allowed':{(relation,'actor','actor')},
           'models29':{}}
    nets={seed:{'occurrence_graph':m.x31.OccurrenceGraph(8).eval().requires_grad_(False)}
          for seed in m.SEEDS}
    with torch.inference_mode():
        ex=m.x31.example_from_case(case,row,m._scoring_state_for_topk(state,48),top_k=48)
        full=m.x31.score_case(ex,state,nets)
        shortlist=m.shortlist_for_frozen_decoder(full)
        no_gold=m.x31.example_from_case({**case,'gold':set()},row,
                                       m._scoring_state_for_topk(state,48),top_k=48)
        no_gold_full=m.x31.score_case(no_gold,state,nets)
        no_gold_shortlist=m.shortlist_for_frozen_decoder(no_gold_full)
    from collections import Counter
    assert max(Counter(full['groups']).values())==29
    assert max(Counter(shortlist['groups']).values())==24
    assert len(full['edges'])>len(shortlist['edges'])
    assert all(len(value)==len(full['edges']) and np.isfinite(value).all()
               for value in full['raw'].values())
    assert shortlist['edges']==no_gold_shortlist['edges']
