"""G2-0067 frozen graph assembly causal and leakage tests."""
from types import SimpleNamespace
import json
import math

import pytest
from tools import nanojev_g2_joint_graph_bakeoff_0067 as b


def toy(graph=0, gold=(0,2)):
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    r=torch.full((p55.SLOTS,5),-10.0);r[:,0]=10.
    s=torch.zeros((p55.SLOTS,4));d=torch.zeros((p55.SLOTS,4))
    r[:3,0]=0.;r[:3,1]=10.
    # Independent slots 0+1 both propose (0,2,rel0). Slot 2 proposes (1,2,rel0).
    s[0,0]=9.;d[0,2]=9.
    s[1,0]=9.;s[1,1]=8.8;d[1,2]=9.
    s[2,1]=9.;d[2,2]=9.
    features={'node':torch.randn(4,96),'kinds':torch.tensor([0,0,1,1]),
              'margin':torch.zeros(4),
              'src':torch.tensor([0,1,1,2,3]),'dst':torch.tensor([2,2,3,3,0]),
              'relation':torch.tensor([0,0,0,0,0]),
              'geometry':torch.zeros(5,5)}
    return {'logits':(r,s,d),'features':features,'gold':frozenset(gold),
            'truth_count':len(gold),'family':'allocation','mode':'renaming',
            'pair_index':graph,'id':f'graph-{graph}'}


def test_baseline_exact_real_decoder_parity_and_allowlist():
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    x=toy();model=p55.model_for('set_prediction').eval().requires_grad_(False)
    with torch.inference_mode():
        actual=p55.infer_edges(model,x['features'],'set_prediction')
        logits=model(x['features'])
    assert b._decode(logits,x['features'],{'margin':0})==actual
    assert b._decode(logits,x['features'],{'margin':0},prepared=b._prepared(logits,x['features']))==actual
    class Corpus:
        def pair(self,idx,with_labels=True):
            assert with_labels
            z={**x['features'],'positive':torch.tensor(sorted(x['gold'])),
                'truth_count':x['truth_count'],'family':x['family'],
                'mode':x['mode'],'prompt_sha256':'a'*64,
                'training_node_ids':(0,)}
            return (z,z),{}
    cases=b._capture(model,Corpus(),[0],p55)
    assert len(cases)==2 and cases[0]['gold']==x['gold']
    assert all('training_node_ids' not in z['features'] for z in cases)
    assert all('positive' not in z['features'] for z in cases)


def test_global_assignment_can_rescue_duplicate_while_retaining_null():
    proposals={0:[(0,-.1),(1,-.3)],1:[(0,-.1),(1,-.2)],2:[(2,-.1)]}
    res=b._assignment(proposals,1.0)
    assert {e for e,q,score in res}=={0,1,2}
    assert len({q for e,q,score in res})==3
    res=b._assignment({0:[(0,-.1)],1:[(0,-.1)]},.3)
    assert len(res)==1
    assert not b._assignment({0:[(0,-.1)]},0)


def test_endpoint_joint_balance_reorders_competing_pairs_without_gold():
    import torch
    x=toy();f=x['features']
    r,s,d=(v.clone() for v in x['logits'])
    r[1,0]=0;r[1,1]=15.
    s[1]=torch.tensor([8.,7.9,0.,0.]);d[1]=torch.tensor([0.,0.,3.,0.])
    f2={**f,'src':torch.tensor([0,1]),'dst':torch.tensor([2,2]),
        'relation':torch.tensor([0,0])}
    a=b._prepared((r,s,d),f2)
    assert a['imbalance'].shape==(64,2)
    without=b._decode((r,s,d),f2,{'margin':0,'joint':0})
    with_joint=b._decode((r,s,d),f2,{'margin':0,'joint':.8})
    assert all(i in {0,1} for i in without|with_joint)


def test_admission_penalty_rejects_uncertain_endpoint_and_null_slots():
    import torch
    x=toy()
    with_zero=b._decode(x['logits'],x['features'],{'margin':0})
    stronger=b._decode(x['logits'],x['features'],{'margin':0,'admission':100.})
    assert stronger.issubset(with_zero)
    assert b._decode(x['logits'],x['features'],{'margin':0,'admission':0})==with_zero
    with pytest.raises(ValueError,match='INVALID'):
        b._decode(x['logits'],x['features'],{'margin':0,'joint':-1})


def test_degree_constraints_fit_training_gold_only_and_are_not_one_to_one():
    x=toy(gold=(0,1,2));train=[x for _ in range(12)]
    profile=b._degree_profile(train)
    caps=b._caps(profile,.90,min_support=1)
    assert any(value>=2 for value in caps.values())
    features=x['features']
    selected=[(0,0,-.1),(1,1,-.2),(2,2,-.3)]
    out=b._graph_consistency(selected,features,{('in',0,1):1})
    assert len(out)==2
    # Nonexistent support never implies a one-to-one semantic constraint.
    assert b._graph_consistency(selected,features,{})==selected
    with pytest.raises(ValueError,match='UNREACHABLE_TRAIN'):
        b._degree_profile([{**x,'truth_count':4}])


def test_partition_and_calibration_are_repeatable_and_disjoint():
    rows=[]
    for f in ('allocation','encounter'):
        for m in ('renaming','role_swap'):
            for n in range(25):
                meta={'family':f,'mode':m}
                rows.append((0,0,meta,dict(meta)))
    ds=SimpleNamespace(pairs=rows)
    a,beta=b._partition(ds,24,48)
    assert (a,beta)==b._partition(ds,24,48)
    assert len(a)==24 and len(beta)==48 and not (set(a)&set(beta))
    with pytest.raises(ValueError,match='requested'):
        b._partition(ds,80,30)
    cal=[toy(i) for i in range(4)]
    for row in cal:row['prepared']=b._prepared(row['logits'],row['features'])
    first=b._calibrate(cal,{})
    assert 'combined_assembly' in first
    assert first==b._calibrate(cal,{})


def test_bootstrap_and_summary_are_pair_level():
    a=b._new_stats();c=b._new_stats()
    b._add(a,{0},{0,1},2);b._add(c,{0,1},{0,1},2)
    assert b._summary(a)['f1']==pytest.approx(2/3)
    assert b._summary(c)['exact']==1
    aa={'by_pair':{'0':b._summary(a),'1':b._summary(a)}}
    bb={'by_pair':{'0':b._summary(c),'1':b._summary(c)}}
    assert b._bootstrap(aa,bb)==[pytest.approx(100/3,abs=.001),pytest.approx(100/3,abs=.001)]


def test_end_to_end_split_guard_and_report(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    class Corpus:
        def __init__(self,n):
            self.pairs=[]
            for i in range(n):
                self.pairs.append((0,0,{'family':'allocation','mode':'renaming'},
                                    {'family':'allocation','mode':'renaming'}))
            self.indices=list(range(n))
    td=Corpus(12);vd=Corpus(16)
    def fake_load(*args):
        return (object(),td,vd,p55,{'plan_sha256':'x','capture_config_sha256':'y',
             'budget':{'requested_training_graphs':24}}, {'weights_sha256':'frozen'})
    accessed=[]
    def fake_capture(model,ds,indices,p55,parity=True):
        accessed.append(('train' if ds is td else 'validation',list(indices)))
        return [toy(i) for i in indices for _ in (0,1)]
    monkeypatch.setattr(b,'_load_model_and_data',fake_load)
    monkeypatch.setattr(b,'_capture',fake_capture)
    report=b.run(training_dir=tmp_path,plan_dir=tmp_path,capture_dir=tmp_path,
                 training_pairs=4,calibration_pairs=4,evaluation_pairs=4)
    assert [kind for kind,ids in accessed]==['train','validation','validation']
    assert not(set(accessed[1][1])&set(accessed[2][1]))
    assert len(report['evaluation'])==7
    assert report['production_promotion'] is False
    assert report['gold_at_inference'] is False
    assert report['sealed_test_used'] is False
    assert 'JOINT EDGE ASSIGNMENT' in b.format_report(report)


def test_cli_is_create_only_not_overwrite(monkeypatch,tmp_path):
    expected={'schema':b.SCHEMA,'checkpoint_step':4900,'checkpoint_sha256':'hash',
              'split':{'train_pairs':4,'cal_pairs':4,'eval_pairs':4},
              'evaluation':{'calibrated_baseline':{'overall':{'f1':.5,'precision':.5,
                  'recall':.5,'missing':2,'extra':2,'exact':0,'graphs':4}}},
              'hypothesis_results':{},'calibration':{}}
    monkeypatch.setattr(b,'run',lambda **kwargs:expected)
    out=tmp_path/'report.json'
    args=['--output',str(out)]
    b.main(args)
    assert json.loads(out.read_text())['schema']==b.SCHEMA
    with pytest.raises(SystemExit):b.main(args)
    assert json.loads(out.read_text())['schema']==b.SCHEMA


def test_original_inference_parity_randomized_logits():
    """No semantic label access; compare exact edge sets across 30 seeded logits."""
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    gen=torch.Generator().manual_seed(670067)
    class Model:
        def __init__(self,parts):self.parts=parts
        def __call__(self,features):return self.parts
    for i in range(30):
        n=3+(i%4);e=8+(i%8)
        f={'src':torch.randint(0,n,(e,),generator=gen),
           'dst':torch.randint(0,n,(e,),generator=gen),
           'relation':torch.randint(0,4,(e,),generator=gen)}
        parts=(torch.randn(p55.SLOTS,5,generator=gen)*1.4,
               torch.randn(p55.SLOTS,n,generator=gen),
               torch.randn(p55.SLOTS,n,generator=gen))
        actual=p55.infer_edges(Model(parts),f,'set_prediction')
        got=b._decode(parts,f,{'margin':0.0})
        assert got==actual, (i,actual,got)


def test_no_scipy_fallback_matches_global_assignment(monkeypatch):
    """User may not have SciPy; pure-numpy Hungarian fallback must work."""
    import builtins
    import sys
    proposals={0:[(0,-.1),(1,-.2)],1:[(0,-.1),(1,-.11)]}
    original=b._assignment(proposals,1.0)
    orig_import=builtins.__import__
    def no_scipy(name,*args,**kwargs):
        if name.startswith('scipy'):
            raise ImportError('no scipy')
        return orig_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',no_scipy)
    assert {e for e,q,score in b._assignment(proposals,1.0)} == \
           {e for e,q,score in original}


def test_checkpoint_load_gates_fail_on_hash_drift(monkeypatch,tmp_path):
    """Validate real checkpoint lineage checks without reading project test data."""
    import torch
    from safetensors.torch import save_file
    from tools import nanojev_gamer_v1_g2_scaling_0059 as g
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    configdir=tmp_path/'training';configdir.mkdir()
    plan=tmp_path/'plan';plan.mkdir()
    cap=tmp_path/'capture';cap.mkdir()
    (plan/'plan.json').write_text('{}')
    (cap/'capture.json').write_text('{}')
    step=4900
    weights=configdir/'checkpoint-000004900.safetensors'
    save_file(p55.model_for('set_prediction').state_dict(),str(weights))
    meta={'step':step,'weights_sha256':g.file_sha(weights),
          'validation':{'graphs':2,'exact':0,'true_positive':1,'gold_edges':2,
                        'predicted_edges':2,'missing':1,'extra':1}}
    (configdir/'checkpoint-000000000.json').write_text(json.dumps({**meta,'step':0}))
    (configdir/'checkpoint-000004900.json').write_text(json.dumps(meta))
    cfg={'sealed_test_used':False,'production_promotion':False,
         'plan_sha256':g.file_sha(plan/'plan.json'),
         'capture_config_sha256':g.file_sha(cap/'capture.json')}
    (configdir/'training.json').write_text(json.dumps(cfg))
    cfg['plan_sha256']='bad'
    (configdir/'training.json').write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='PLAN_HASH'):
        b._load_model_and_data(configdir,plan,cap,step,2)
    cfg['plan_sha256']=g.file_sha(plan/'plan.json');cfg['sealed_test_used']=True
    (configdir/'training.json').write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='EXPERIMENTAL_ONLY'):
        b._load_model_and_data(configdir,plan,cap,step,2)
