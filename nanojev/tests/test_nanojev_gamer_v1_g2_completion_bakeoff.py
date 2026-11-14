"""G2-0040: admission-aware routing, fixed bake-off, immutable parent contracts."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_completion_bakeoff as g


def toy():
    edges=[('a','b','owns'),('a','c','owns'),('a','d','owns'),
           ('a','e','owns'),('b','a','owns'),('b','d','owns')]
    raw={g.x31.REFERENCE:np.array([3.3,2.6,1.4,.5,3.1,2.4],np.float32)}
    for seed in g.x39.SEEDS:
        raw[f'occurrence_graph_s{seed}']=np.array([3.9,3.3,2.1,-.5,3.5,2.1],np.float32)
    raw[g.x31.CHAMPION]=raw[g.x31.REFERENCE].copy()
    item={'raw':raw,'groups':np.array([0,0,0,0,1,1],np.int32),
          'edges':edges,'gold':set(), 'gold_total':0,'candidate_gold':0,'candidate_count':6}
    policy={'source':g.SOURCE,'alpha':1.,'threshold':.93,
            'margin':2.,'reference_gate':None}
    return item,policy


def sample(admitted=True):
    item,policy=toy()
    group={'candidate__group_s460333':set(item['edges'][:1]),
           'candidate__structured_s460333':set(item['edges'][:2])}
    rankings={k:np.arange(len(item['edges']),dtype=np.float64)
              for k in group}
    features=g.x33.set_features(item,g.SOURCE,policy,4)
    net=g.x33.GraphSetEnergy().eval()
    pred=g.x33.predictions(features,net)
    # Sufficient shape to test cardinality routing; not used to read gold.
    count=([],[],[],set(),np.ones(len(item['edges'])),np.ones((2,9)),4.)
    return {'row':{'prompt':'x','family':'encounter'},'routed':item,
            'routed_decode':(group,rankings,item), 'baseline':(group,rankings,item),
            'shortlisted':item,'parent_policy':policy,'features':features,
            'structured_pred':pred,'adapted_pred':pred,'count_prediction':count,
            'admitted':admitted,'new_scorer_used':admitted,
            'original':{'anchors':['a'] if admitted else ['a','b','c','d','e']},
            'recovered':{'anchors':['a','b','c','d','e']}}


def test_routes_only_when_new_anchors_admitted():
    old={'raw':{g.SOURCE:np.array([1.,2.],np.float32)}}
    new={'raw':{g.SOURCE:np.array([10.,20.],np.float32)}}
    policy={'enabled':True,'blend':.75}
    kept,changed=g.admission_route(old,new,['a','b'],['b','a'],policy)
    assert kept is old and changed is False
    kept,changed=g.admission_route(old,new,['a','b'],['a','b','c'],policy)
    assert kept is not old and changed is True
    assert np.allclose(kept['raw'][g.SOURCE],[7.75,15.5])
    assert np.allclose(old['raw'][g.SOURCE],[1.,2.])
    kept,changed=g.admission_route(old,new,['a','b'],['a','b','c'],
                                   {'enabled':False,'blend':0.})
    assert kept is old and changed is False
    with pytest.raises(ValueError,match='REMOVED_ANCHOR'):
        g.admission_route(old,new,['a','b'],['a','c'],policy)


def test_fixed_grid_and_frozen_decoders():
    candidates=g.policy_candidates()
    assert len(candidates)==9
    assert {arm for arm,_ in candidates}==set(g.ARMS)
    assert len({(arm,json.dumps(cfg,sort_keys=True)) for arm,cfg in candidates})==9
    assert all(config['enabled'] for _,config in candidates)
    assert all(config['source']==g.SOURCE for arm,config in candidates
               if arm!='cardinality')


def test_all_arms_protect_no_admission_graph(monkeypatch):
    record=sample(False)
    def fail(*args,**kwargs):raise AssertionError('decoder must not run')
    monkeypatch.setattr(g.x32,'_select_with_prediction',fail)
    monkeypatch.setattr(g.x33,'decode',fail)
    for arm,cfg in g.policy_candidates():
        assert g.select_arm(record,arm,cfg)==g.baseline_selection(record,arm)


def test_real_constrained_selection_is_label_blind():
    record=sample(True)
    before={arm: g.select_arm(record,arm,cfg) for arm,cfg in g.policy_candidates()
            if arm!='cardinality'}
    record['shortlisted']['gold']=set(record['shortlisted']['edges'])
    after={arm: g.select_arm(record,arm,cfg) for arm,cfg in g.policy_candidates()
           if arm!='cardinality'}
    # Duplicate keys collapse configs in dict but each of those must be invariant.
    assert before==after


def test_train_fit_and_disjoint_audit_gate(monkeypatch):
    calls=[]
    def summary(cohort,arm,cfg):
        calls.append((cohort,arm,cfg))
        eligible=arm=='constrained' and cfg['group_weight']==.7
        safe=eligible and cohort=='fit'
        return dict(new_exact=2 if safe else 0,lost_exact=0,
                    f1=.82 if safe else .79,parent_f1=.80,
                    fp=5 if safe else 9,parent_fp=8,
                    exact=4,parent_exact=2,cases=6,rank_errors=3 if safe else 9,
                    parent_rank_errors=8)
    monkeypatch.setattr(g,'summarize_policy',summary)
    p,grid=g.fit_bakeoff('fit','audit')
    assert not p['enabled']
    assert len(grid)==9
    assert sum(x['eligible_fit'] for x in grid)==1
    assert sum(x['eligible_audit'] for x in grid)==0
    assert len([c for c in calls if c[0]=='audit'])==1


def test_winner_must_survive_audit(monkeypatch):
    def summary(cohort,arm,cfg):
        accepted=arm=='adapted' and cfg['group_weight']==1.4
        return dict(new_exact=2 if accepted else 0,lost_exact=0,
                    f1=.82 if accepted else .79,parent_f1=.80,
                    fp=5 if accepted else 9,parent_fp=8,
                    exact=4,parent_exact=2,cases=6,rank_errors=3 if accepted else 9,
                    parent_rank_errors=8)
    monkeypatch.setattr(g,'summarize_policy',summary)
    p,grid=g.fit_bakeoff('fit','audit')
    assert p['enabled'] and p['arm']=='adapted'
    assert p['config']['group_weight']==1.4


def test_training_updates_copy_not_parent():
    torch.set_num_threads(1)
    parent=g.x33.GraphSetEnergy().eval().requires_grad_(False)
    snapshot={k:v.clone() for k,v in parent.state_dict().items()}
    sample_rows=[]
    for i in range(16):
        rec=sample(i%2==0)
        rec['row']={'prompt':f'train{i}', 'family':'encounter'}
        sample_rows.append(rec)
    gold=set(toy()[0]['edges'][:3])
    from unittest.mock import patch
    with patch.object(g.base,'gold_binding_edges',return_value=gold):
        model,audit=g.fine_tune_decoder(sample_rows,parent,steps=2)
    assert audit['original_cases']==8 and audit['expanded_cases']==8
    assert all(torch.equal(parent.state_dict()[k],v) for k,v in snapshot.items())
    assert any(not torch.equal(model.state_dict()[k],v) for k,v in snapshot.items())
    assert not any(p.requires_grad for p in model.parameters())


def test_parent39_contract_fails_closed(tmp_path):
    (tmp_path/g.x39.REPORT).write_text(json.dumps({'schema':'wrong'}))
    with pytest.raises(ValueError,match='PARENT_0039_CONTRACT:schema'):
        g.verify_parent39(tmp_path,{}, {},'hash')


def test_output_immutable_and_hashes(tmp_path):
    net=g.x33.GraphSetEnergy()
    output=tmp_path/'new'
    report={'schema':g.SCHEMA,'production_promotion':False}
    p={'enabled':False,'arm':None,'config':None}
    g.persist(output,report,net,p,[])
    assert (output/g.REPORT).is_file()
    assert report['weights_sha256']==g.backbone.sha256(output/g.WEIGHTS)
    assert report['policies_sha256']==g.backbone.sha256(output/g.POLICIES)
    with pytest.raises(FileExistsError):g.persist(output,{},net,p,[])


def test_command_exposes_parent39_and_no_other_required_variables():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-completion-bakeoff','--help'],capture_output=True,text=True,check=True)
    assert '--relationship39-run' in result.stdout
    assert '--device' in result.stdout


def test_prior_duplicate_history_is_allowed_but_new_cohorts_are_disjoint():
    prior={'prompt':'inherited-duplicate','split':'train','family':'encounter'}
    pool,dev=g.fresh_cohorts40([prior,prior],[],{},[],{},[],{})
    assert [len(p) for p in pool]==[1700,700,400]
    assert [len(dev[key]) for key in g.FRESH]==[320,128]
    sha=[g.base._sha(row['prompt']) for batch in pool for row in batch]
    sha.extend(g.base._sha(row['prompt']) for split in dev.values() for row in split)
    assert len(sha)==len(set(sha))
    assert all(row['split']=='train' for batch in pool for row in batch)
    assert all(row['split']=='dev' for part in dev.values() for row in part)


def test_development_labels_are_read_only_after_all_graphs_selected(monkeypatch):
    rec=sample(True);rec['row']={'prompt':'dev case','family':'encounter'}
    calls=[]
    def proposal(record,arm,cfg):
        calls.append('inference')
        return g.baseline_selection(record,arm)
    monkeypatch.setattr(g,'select_arm',proposal)
    gold=set(rec['shortlisted']['edges'][:2])
    def guarded_gold(row):
        assert len(calls)==10  # 9 contenders and one frozen winner
        calls.append('labels')
        return gold
    monkeypatch.setattr(g.base,'gold_binding_edges',guarded_gold)
    policy={'enabled':True,'arm':'constrained',
            'config':g.configuration('constrained',g.SET_GRID[0])}
    result=g.evaluation_record(rec,policy,'new_independent_dev_0040',1)
    assert result['metrics']['winner']['exact_cases']==1
    assert result['admitted'] is True
    assert calls[-1]=='labels'


def test_common_baseline_is_structured_for_every_arm():
    rec=sample(False)
    expected=rec['routed_decode'][0]['candidate__structured_s460333']
    assert all(g.baseline_selection(rec,arm)==expected for arm in g.ARMS)


def test_audit_cannot_pick_runner_up_when_fit_winner_fails(monkeypatch):
    observed=[]
    def summary(cohort,arm,cfg):
        observed.append((cohort,arm))
        winner=arm=='adapted' and cfg['group_weight']==1.4
        runner_up=arm=='constrained' and cfg['group_weight']==.7
        good=winner or runner_up
        return {'new_exact':3 if winner else (1 if runner_up else 0),
                'lost_exact':1 if cohort=='audit' and winner else 0,
                'f1':.83 if good else .70,'parent_f1':.80,
                'fp':4 if good else 12,'parent_fp':10,
                'rank_errors':3 if good else 12,'parent_rank_errors':10}
    monkeypatch.setattr(g,'summarize_policy',summary)
    chosen,ledger=g.fit_bakeoff('fit','audit')
    assert chosen['enabled'] is False
    assert chosen['disabled_reason']=='train_fit_winner_failed_independent_audit'
    assert observed.count(('audit','adapted'))==1
    assert not any(cohort=='audit' and arm!='adapted' for cohort,arm in observed)


def test_train_miner_enriches_recovery_and_preserves_controls():
    rows=[{'prompt':f'train-row-{i}','split':'train',
           'family':'encounter'} for i in range(100)]
    def screen(row,state,nets,policies,*,device):
        index=int(row['prompt'].split('-')[-1])
        admitted=index%3==0
        return {'admitted':admitted,'gold_anchor_rescue':admitted and index%2==0,
                'new_gold_anchors':int(admitted),'false_new_anchors':0}
    chosen,diag=g.mine_enriched_train(rows,{},None,None,tag='train_0040',
        size=24,expanded_target=12,min_rescues=5,device='cpu',screen=screen)
    assert len(chosen)==24
    assert diag['selected_expanded']==12
    assert diag['selected_unchanged']==12
    assert diag['selected_anchor_rescued']>=5
    assert diag['selected_sha256']==g.sha_rows(chosen)
    assert all(r['split']=='train' for r in chosen)
    assert len({r['prompt'] for r in chosen})==24


def test_miner_refuses_dev_samples_and_short_opportunity_pool():
    rows=[{'prompt':f'train-row-{i}','split':'train','family':'encounter'}
          for i in range(20)]
    def no_admission(row,*unused,**kwargs):
        return {'admitted':False,'gold_anchor_rescue':False,
                'new_gold_anchors':0,'false_new_anchors':0}
    with pytest.raises(ValueError,match='INSUFFICIENT_RECOVERY_OPPORTUNITIES'):
        g.mine_enriched_train(rows,{},None,None,tag='fit',size=8,
            expanded_target=3,min_rescues=1,device='cpu',screen=no_admission)
    rows[0]['split']='dev'
    with pytest.raises(ValueError,match='MINE_DEV_FORBIDDEN'):
        g.mine_enriched_train(rows,{},None,None,tag='fit',size=8,
            expanded_target=3,min_rescues=1,device='cpu',screen=no_admission)


def test_feasibility_distinguishes_anchor_coverage_from_g2_shortlist(monkeypatch):
    rec=sample(True)
    gold=set(rec['shortlisted']['edges'])
    monkeypatch.setattr(g.base,'gold_binding_edges',lambda row:gold)
    info=g.reachable_opportunity(rec)
    assert info['anchor_representable']
    assert info['shortlist_representable']
    assert info['newly_anchor_representable']
    assert info['repairable']
    rec['shortlisted']={**rec['shortlisted'], 'edges':rec['shortlisted']['edges'][:-1]}
    info=g.reachable_opportunity(rec)
    assert info['anchor_representable']
    assert not info['shortlist_representable']
    assert not info['repairable']
    assert info['unrepresented_gold_edges']==1
    summary=g.opportunity_summary([rec])
    assert summary['expanded_unreachable']['cases']==1
    assert summary['expanded_unreachable']['missing_candidate_gold_edges']==1


def test_label_blind_prescreen_read_gold_after_admission(monkeypatch):
    events=[]
    ev={'baseline_anchors':('a',)}
    def freeze(*args,**kwargs):
        events.append('freeze')
        return ev,('prepared','hidden','eligible')
    def choose(evidence,acceptors,policies,seed):
        events.append('choose')
        return ['chosen']
    def anchor(spans,prepared):
        events.append('anchors')
        return ('a','b')
    def gold(row):
        assert events==['freeze','choose','anchors']
        events.append('labels')
        return {('a','b','owns')}
    monkeypatch.setattr(g.x38,'freeze_context_case',freeze)
    monkeypatch.setattr(g.x39,'chosen_spans',choose)
    monkeypatch.setattr(g.base,'predicted_anchors',anchor)
    monkeypatch.setattr(g.base,'gold_binding_edges',gold)
    result=g._screen_train_row({'prompt':'train','split':'train'},
        {'head':None,'model':None,'tokenizer':None},None,None,device='cpu')
    assert result['admitted'] and result['gold_anchor_rescue']
    assert events[-1]=='labels'
