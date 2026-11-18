"""G2-0065: causal lane boundaries and synthetic learned-head checks."""

from types import SimpleNamespace
import math
import pytest
from tools import nanojev_g2_learning_bakeoff_0065 as b


def _toy():
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    rel=torch.full((p55.SLOTS,3),-10.)
    rel[:,0]=12.
    rel[0,0]=0.;rel[0,1]=15.
    src=torch.zeros(p55.SLOTS,3)
    dst=torch.zeros(p55.SLOTS,3)
    # Original prefers edge 0, but training teaches endpoint 1.
    src[0,0]=3.;src[0,1]=2.
    dst[0,2]=8.
    node=torch.zeros(3,96)
    node[0,0]=-3.;node[1,0]=3.
    data={'node':node,'src':torch.tensor([0,1,2]),
          'dst':torch.tensor([2,2,1]),'relation':torch.zeros(3,dtype=torch.long),
          'kinds':torch.zeros(3,dtype=torch.long),'margin':torch.zeros(3),
          'geometry':torch.zeros(3,5)}
    c={'logits':(rel,src,dst),'features':data,'gold':{1},'truth_count':1,
       'family':'allocation','mode':'role_swap','pair_index':0}
    return c,p55


def test_original_frozen_decoder_parity():
    import torch
    c,p55=_toy()
    class Frozen(torch.nn.Module):
        def forward(self,s):
            assert 'positive' not in s and 'truth_count' not in s
            return c['logits']
    expected=p55.infer_edges(Frozen(),c['features'],'set_prediction')
    assert expected=={0}
    assert b._decode(c)==expected
    assert b._decode(c,mode='joint',weights=None)==expected
    assert b._decode(c,mode='joint',weights=torch.zeros(3*96+2+5+2),alpha=0)==expected


def test_endpoint_source_training_improves_toy_target_without_gold_at_inference():
    c,_=_toy()
    w,receipt=b._fit_ranker([c]*8,'source',epochs=35)
    assert receipt['comparisons']>0 and math.isfinite(receipt['final_loss'])
    assert b._decode(c,mode='source',weights=w,alpha=2.0)=={1}
    unlabelled={k:v for k,v in c.items() if k not in ('gold','truth_count')}
    assert b._decode(unlabelled,mode='source',weights=w,alpha=2.0)=={1}


def test_endpoint_feature_scope_isolated():
    import torch
    c,_=_toy();f=c['features']
    source=b._features_for_edges(f,'source')
    dest=b._features_for_edges(f,'destination')
    joint=b._features_for_edges(f,'joint')
    mutated={**f,'node':f['node'].clone()}
    mutated['node'][0,0]=42.
    s1=b._features_for_edges(mutated,'source')
    d1=b._features_for_edges(mutated,'destination')
    assert not torch.equal(source,s1)
    assert torch.equal(dest[:2],d1[:2])  # unchanged shared destination 2
    assert joint.shape[1]>source.shape[1] and joint.shape[1]>dest.shape[1]
    with pytest.raises(ValueError,match='mode'):
        b._features_for_edges(f,'relation')


def test_admission_is_label_blind_and_can_reject_spurious_edges():
    import torch
    c,_=_toy()
    raw=b._propose(c['logits'],c['features'],margin=0.)
    assert raw and raw[0][0]==0
    w=torch.zeros(7)
    w[0]=-10.
    pred=b._decode({k:v for k,v in c.items() if k!='gold'},
                   admission=w,threshold=.5)
    assert pred==set()
    w[0]=10.
    assert b._decode(c,admission=w,threshold=.5)=={0}


def test_endpoint_training_rejects_unreachable_gold():
    c,_=_toy()
    c={**c,'truth_count':2}
    with pytest.raises(ValueError,match='Unreachable TRAIN'):
        b._rank_training_examples([c],'source')


def test_selection_does_not_read_evaluation_data_or_tensor_equality():
    c,_=_toy()
    import torch
    rank=torch.zeros(2)
    # Force a tie and ensure tensors do not trigger an ambiguous boolean.
    names=[('first',{'mode':'source','weights':rank,'alpha':0}),
           ('second',{'mode':'destination','weights':rank,'alpha':0})]
    win=b._select_cal([c],names)
    assert win[0]=='first'
    assert win[1] is names[0][1]


def test_train_selection_stratified_reachable_and_reproducible():
    cells=[];ids=[]
    for family in ('allocation','encounter'):
        for mode in ('renaming','role_swap'):
            for i in range(20):
                cells.append((0,0,{'family':family,'mode':mode},{}))
                ids.append(len(cells)-1)
    ds=SimpleNamespace(pairs=cells,indices=ids)
    assert b._balanced_train(ds,24)==b._balanced_train(ds,24)
    assert len(set(b._balanced_train(ds,24)))==24
    with pytest.raises(ValueError,match='Insufficient'):
        b._balanced_train(ds,100)


def test_pair_block_bootstrap_and_summary():
    c,_=_toy()
    c2={**c,'pair_index':1,'gold':{0}}
    cases=[c,c2]
    out=b._score(cases,{})
    assert out['overall']['graphs']==2
    assert out['overall']['exact']==1
    ci=b._paired_bootstrap(cases,{}, {'margin':999}, repeats=40)
    assert ci['delta_f1_pp_ci_95'][0]<=ci['delta_f1_pp_ci_95'][1]


def test_refuses_overwrite_existing_report(tmp_path):
    import inspect
    source=inspect.getsource(b.main)
    assert "a.output.exists()" in source
    assert "with a.output.open('x'" in source


def test_no_sealed_or_prod_io_in_run():
    import inspect
    s=inspect.getsource(b.run)
    assert "FeatureCorpus" not in s or "'test'" not in s
    assert "production_promotion':False" in s
    assert "sealed_test_used':False" in s


def test_bakeoff_end_to_end_policy_fitting_is_split_disjoint(monkeypatch,tmp_path):
    """Run the full orchestrator against labeled toy logits, without weights or CUDA."""
    from tools import nanojev_g2_endpoint_bakeoff_0064 as previous
    class Tiny:
        def __init__(self,split,n):
            self.split=split
            self.pairs=[]
            for i in range(n):
                f=('allocation','encounter')[i%2]
                m=('renaming','role_swap')[i//2%2]
                a={'family':f,'mode':m}
                self.pairs.append((i,0,a,dict(a)))
            self.indices=list(range(n))
    train=Tiny('train',8);val=Tiny('validation',12)
    def fake_load(*a):
        return (object(),object(),train,val,{'weights_sha256':'frozen-hash'},
                {'capture_config_sha256':'capture-hash','plan_sha256':'plan-hash'})
    def fake_predictions(model,ds,indices,p55,baseline_parity=True):
        result=[]
        for i in indices:
            for side in (0,1):
                c,_=_toy()
                c={**c,'gold':{(i+side)%2},'pair_index':i,
                   'family':ds.pairs[i][2]['family'],
                   'mode':ds.pairs[i][2]['mode'],
                   'id':f'{ds.split}-{i}-{side}'}
                result.append(c)
        return result
    monkeypatch.setattr(b,'_load_corpus_and_model',fake_load)
    monkeypatch.setattr(previous,'_capture_predictions',fake_predictions)
    report=b.run(training_dir=tmp_path,plan_dir=tmp_path,capture_dir=tmp_path,
                 train_pairs=8,calibration_pairs=4,evaluation_pairs=4)
    assert report['splits']['training_pairs']==8
    assert report['splits']['calibration_pairs']==4
    assert report['splits']['evaluation_pairs']==4
    assert len(report['evaluation'])==7
    assert report['evaluation']['baseline']['overall']['graphs']==8
    assert report['evaluation']['endpoint_plus_admission']['overall']['graphs']==8
    assert report['production_promotion'] is False
    assert report['sealed_test_used'] is False
    assert 'Arm' in b.format_report(report)
