"""G2-0042: separate ranking from calibration, safety gates, immutable parents."""
import copy
import json
import subprocess
import sys

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_rank_calibration_bakeoff as b


def toy(*,poor_order=True):
    edges=[('a','b','rel'),('a','c','rel'),('d','e','rel'),('d','f','rel')]
    gold={edges[0],edges[2]}
    logits=np.array([-3.,2.,-3.,2.] if poor_order else [-4.,-5.,-4.,-5.],dtype=np.float64)
    counts=np.full((2,b.x33.COUNT_CLASSES),-50.,dtype=np.float64)
    counts[:,1]=0.
    if not poor_order:counts[:,0]=0.
    parent=(logits,counts,2.)
    features={'edges':np.zeros((4,b.x33.EDGE_DIM),dtype=np.float32),
              'groups':np.zeros((2,b.x33.GROUP_DIM),dtype=np.float32),
              'index':np.array([0,0,1,1],dtype=np.int64)}
    cfg={'enabled':True,'source':b.g40.SOURCE,'group_weight':.7,
         'global_weight':0.,'fallback':None}
    selected=b.x33.decode({'edges':edges},features,parent,cfg)
    record={'row':{'prompt':'toy graph','split':'train','gold':gold},
        'admitted':True,'original':{'anchors':{'a','d'}},
        'recovered':{'anchors':{'a','b','c','d','e','f'}},
        'features':features,'structured_pred':parent,
        'shortlisted':{'edges':edges},
        'routed_decode':({'candidate__structured_s460333':selected},{})}
    return record,gold,cfg


def test_rank_only_permutes_original_score_magnitudes(monkeypatch):
    r,gold,policy=toy()
    sorted_before=sorted(r['structured_pred'][0])
    after=b.oracle_order_only(r,gold)
    assert sorted(after)==sorted_before
    assert after[0]>after[1] and after[2]>after[3]
    assert b.decode_logits(r,r['structured_pred'][0],policy)!=gold
    assert b.decode_logits(r,after,policy)==gold
    monkeypatch.setattr(b.base,'gold_binding_edges',lambda row:row['gold'])
    diag=b.causal_separation(r,policy)
    assert diag['repairable']
    assert diag['order_only_exact']
    assert diag['ranking_inversions']==2


def test_calibration_preserves_order_and_can_restore_missing_edges(monkeypatch):
    r,gold,policy=toy(poor_order=False)
    assert b.decode_logits(r,r['structured_pred'][0],policy)!=gold
    assert b.decode_logits(r,r['structured_pred'][0]+6,policy)==gold
    monkeypatch.setattr(b.base,'gold_binding_edges',lambda row:row['gold'])
    finding=b.causal_separation(r,policy)
    assert finding['calibration_only_exact'] is True
    assert finding['order_only_exact'] is False
    assert finding['ranking_inversions']==0


def test_counterfactual_labels_forbidden_on_dev():
    r,gold,policy=toy()
    r['row']['split']='dev'
    with pytest.raises(ValueError,match='DEV_ORACLE_FORBIDDEN'):
        b.causal_separation(r,policy)


def test_unchanged_graph_cannot_be_modified(monkeypatch):
    r,gold,policy=toy()
    r['admitted']=False
    assert b.choose(r,{'kind':'calibration','scale':2.,'bias':10.},{},policy)==b.g40.baseline_selection(r,'constrained')


def test_fit_winner_and_independent_audit_veto(monkeypatch):
    monkeypatch.setattr(b,'candidate_configs',lambda:[{'kind':'calibration','scale':1.,'bias':1.}])
    def metrics(rows,*args,**kwargs):
        if rows==['fit']:
            return {'new_exact':2,'lost_exact':0,'f1':.75,'parent_f1':.74,
                'fp':10,'parent_fp':11,'rank_inversions':4,'parent_rank_inversions':4},[]
        return {'new_exact':0,'lost_exact':1,'f1':.76,'parent_f1':.74,
                'fp':9,'parent_fp':11,'rank_inversions':4,'parent_rank_inversions':4},[]
    monkeypatch.setattr(b,'measure',metrics)
    policy,rows=b.select(['fit'],['audit'],{}, {})
    assert not policy['enabled']
    assert rows[0]['eligible_fit']
    assert not rows[0]['eligible_audit']
    assert policy['disabled_reason']=='winner_failed_independent_natural_train_audit'


def test_train_head_only_parent_unchanged(monkeypatch):
    r,truth,_=toy()
    records=[]
    for i in range(16):
        rec=copy.deepcopy(r)
        rec['admitted']=i<8
        rec['row']['prompt']=f'train {i}'
        records.append(rec)
    monkeypatch.setattr(b.base,'gold_binding_edges',lambda row:row['gold'])
    parent=b.x33.GraphSetEnergy().eval().requires_grad_(False)
    original={k:v.clone() for k,v in parent.state_dict().items()}
    tuned,stats=b.train_edge_head(records,parent,'hard_negative',steps=3)
    assert stats['step']==3
    assert all(torch.equal(parent.state_dict()[k],v) for k,v in original.items())
    assert all(torch.equal(tuned.state_dict()[k],v) for k,v in original.items()
               if not k.startswith('edge_readout.'))
    preds=b.x33.predictions(r['features'],parent)
    tuned_preds=b.x33.predictions(r['features'],tuned)
    assert np.array_equal(preds[1],tuned_preds[1])
    assert preds[2]==tuned_preds[2]


def test_measure_no_gold_leak_and_frozen_routing(monkeypatch):
    r,gold,policy=toy()
    monkeypatch.setattr(b.base,'gold_binding_edges',lambda row:row['gold'])
    cfg={'kind':'calibration','scale':1.0,'bias':0.}
    stat,rows=b.measure([r],cfg,{},policy)
    assert stat['exact']==stat['parent_exact']
    assert stat['lost_exact']==0 and stat['new_exact']==0
    assert len(rows)==1
    r['row']['split']='dev'
    with pytest.raises(ValueError,match='DEV_TRAIN_METRICS_FORBIDDEN'):
        b.measure([r],cfg,{},policy)


def test_parent41_hash_validation_against_real_upload():
    import os
    root='/mnt/data'
    if not os.path.exists(root+'/'+b.g41.REPORT):pytest.skip('uploaded report unavailable')
    report=json.load(open(root+'/'+b.g41.REPORT))
    verified,cohorts,hashes=b.verify_parent41(root,report['parent_hashes'])
    assert verified['cohorts']=={'train':180,'train_fit':80,'natural_train_audit':80}
    assert len(cohorts['train_fit'])==80
    assert hashes['g2_0041_ledger_sha256']==verified['ledger_sha256']


def test_new_cli_registered():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-rank-calibration-bakeoff','--help'],
        capture_output=True,text=True,check=True)
    assert '--oracle41-run' in result.stdout
    assert '--bakeoff40-run' in result.stdout


def test_development_capture_only_after_train_gate():
    import inspect
    source=inspect.getsource(b.stage2)
    assert source.index('winner,calibration=select(')<source.index('dev=dev_capture()')
    assert source.index("if winner['enabled']:")<source.index('dev=dev_capture()')


def test_learned_order_reassignment_does_not_change_cardinality_energy():
    r,gold,policy=toy()
    original=r['structured_pred'][0]
    learned=np.array([10.,-10.,10.,-10.])
    changed=b.learned_order_preserving_magnitudes(r,learned)
    assert set(changed)==set(original)
    assert changed[0]==original[1] and changed[1]==original[0]
    assert changed[2]==original[3] and changed[3]==original[2]
    assert b.decode_logits(r,changed,policy)==gold
    # Reassigning scores cannot alter the optimal counts per group; only which
    # relationships receive those counts.
    old_graph=b.decode_logits(r,original,policy)
    new_graph=b.decode_logits(r,changed,policy)
    for group in [('a',),('d',)]:
        assert sum(e[0]==group[0] for e in old_graph)==sum(e[0]==group[0] for e in new_graph)


def test_policy_grid_has_independent_and_joint_arms():
    candidates=b.candidate_configs()
    assert len(candidates)==6+2*(1+len(b.JOINT_BIASES))
    assert {c['kind'] for c in candidates}=={'calibration','pairwise','hard_negative'}
    assert all(c['scale']>0 for c in candidates)


def test_stage2_no_winner_skips_dev_and_writes_immutable_result(monkeypatch,tmp_path):
    r,_,policy=toy()
    original=b.x33.GraphSetEnergy().eval().requires_grad_(False)
    monkeypatch.setattr(b,'train_edge_head',lambda records,parent,kind:(copy.deepcopy(parent),{'step':1,'loss':.1}))
    monkeypatch.setattr(b,'select',lambda *args:({'enabled':False,'config':None,'disabled_reason':'none'},[]))
    parent41={'train_dataset_sha256':'abc','training_sha256':{'train_0040':'train',
              'train_fit_0040':'fit','train_audit_0040':'audit'},'opportunity_frontier':{}}
    output=tmp_path/'output'
    def reject_dev():
        raise AssertionError('DEV access before train approval')
    report=b.stage2([r]*16,[r]*4,[r]*4,policy,original,{'fit':{'cases':4}},[],
                    reject_dev,lambda:None,output,{}, {},parent41)
    assert report['status']=='TRAIN_SCORE_BAKEOFF_NO_SAFE_WINNER'
    assert report['development_evaluation_skipped']
    assert (output/b.WEIGHTS).exists()
    assert (output/b.POLICIES).exists()
    assert (output/b.LEDGER).exists()
    with pytest.raises(FileExistsError):
        b.persist(output,report,[],{},report['train_policy'])
