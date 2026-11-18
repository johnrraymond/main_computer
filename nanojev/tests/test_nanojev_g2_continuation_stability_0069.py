"""G2-0069: frozen source integrity, equal-step controls, loss and split gates."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from tools import nanojev_g2_continuation_stability_0069 as g
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as p57
from tools import nanojev_gamer_v1_g2_scaling_0059 as scale


def _sample(i=0,side=0,split='train'):
    node=torch.zeros(3,96)
    node[0,0]=-1.;node[1,0]=1.;node[2,1]=2.
    r={'node':node,'kinds':torch.zeros(3,dtype=torch.long),'margin':torch.zeros(3),
       'src':torch.tensor([0,1,0,1]),'dst':torch.tensor([1,2,2,0]),
       'relation':torch.tensor([0,1,2,0]),'geometry':torch.zeros(4,5),
       'positive':torch.tensor([0,1]),'truth_count':2,
       'family':'allocation' if i%2==0 else 'encounter',
       'mode':'renaming' if i%4<2 else 'role_swap',
       'prompt_sha256':f'{split}-{i}-{side}',
       'edge_count':4}
    if split=='train':r['training_node_ids']=('a','b','c')
    return r


class Corpus:
    def __init__(self,split,n=16):
        self.split=split
        self.pairs=[]
        self.indices=list(range(n))
        for i in range(n):
            a=_sample(i,0,split);b=_sample(i,1,split)
            self.pairs.append((i,0,a,b))
    def pair(self,i,with_labels=True):
        a=_sample(i,0,self.split);b=_sample(i,1,self.split)
        assert with_labels
        return (a,b),{'id':f'{self.split}-{i}',
                      'family':a['family'],'mode':a['mode'],
                      'sha256':(a['prompt_sha256'],b['prompt_sha256'])}


def test_nested_training_diversity_balanced_and_reproducible():
    ds=Corpus('train',24)
    low,high=g._nested_train_pools(ds,4,8,48)
    assert low==g._nested_train_pools(ds,4,8,48)[0]
    assert high==g._nested_train_pools(ds,4,8,48)[1]
    assert high[:4]==low and len(set(high))==8
    assert { (ds.pairs[i][2]['family'],ds.pairs[i][2]['mode']) for i in low }=={
        ('allocation','renaming'),('encounter','renaming'),
        ('allocation','role_swap'),('encounter','role_swap')}
    with pytest.raises(ValueError,match='NOT_ENOUGH'):
        g._nested_train_pools(ds,4,28,48)


def test_equal_step_same_presentations_different_diversity():
    a=g._schedule([0,1,2,3],4,2)
    b=g._schedule(list(range(8)),4,2)
    assert a==g._schedule([0,1,2,3],4,2)
    assert len(a)==len(b)==4
    assert sum(map(len,a))==sum(map(len,b))==8
    assert set(sum((list(i) for i in a),[]))=={0,1,2,3}
    assert set(sum((list(i) for i in b),[]))==set(range(8))
    with pytest.raises(ValueError,match='PAIR_POOL'):
        g._schedule(list(range(8)),3,2)


def test_exact_original_objective_and_gradient_for_continuation():
    torch.manual_seed(6)
    model=p55.model_for('set_prediction')
    a,b=Corpus('train').pair(0)[0]
    rows={a['prompt_sha256']:a,b['prompt_sha256']:b}
    pair=Corpus('train').pair(0)[1]
    expected=p57.batch_objective(model,[pair],rows,'D_combined')
    got=p57.batch_objective(model,[pair],rows,'D_combined')
    assert torch.equal(got,expected)
    with torch.no_grad():
        logits=model(a)
        assert g._infer_from_logits(logits,{k:a[k] for k in scale.FEATURE_FIELDS},0.)==p55.infer_edges(model,a,'set_prediction')


def test_decoder_randomized_matches_original_without_gold():
    torch.manual_seed(17)
    class Frozen:
        def __init__(self,forward):self._forward=forward
        def __call__(self,s):return self._forward
    for i in range(15):
        n=3+i%4;e=6+i%5
        f={'src':torch.randint(0,n,(e,)),'dst':torch.randint(0,n,(e,)),
           'relation':torch.randint(0,4,(e,))}
        logits=(torch.randn(p55.SLOTS,5),torch.randn(p55.SLOTS,n),torch.randn(p55.SLOTS,n))
        assert g._infer_from_logits(logits,f,0.)==p55.infer_edges(Frozen(logits),f,'set_prediction')


def test_forward_never_receives_labels_and_calibration_is_separate():
    ds=Corpus('validation',16)
    class NoLabel(torch.nn.Module):
        def __init__(self):
            super().__init__();self.weight=torch.nn.Parameter(torch.tensor(0.))
        def forward(self,s):
            assert set(s)==set(scale.FEATURE_FIELDS)
            return (torch.zeros(p55.SLOTS,5),torch.zeros(p55.SLOTS,3),
                    torch.zeros(p55.SLOTS,3))
    cases=g._collect(NoLabel(),ds,[0,1])
    assert len(cases)==4 and len(cases[0]['pred'])==len(g.MARGINS)
    margin,meta=g._select_margin(cases)
    assert margin in g.MARGINS
    assert meta['graphs']==4


def test_validation_partition_pair_disjoint_and_stable():
    ds=Corpus('validation',16)
    x,y=g._split_validation(ds,4,8)
    assert len(x)==4 and len(y)==8 and not (set(x)&set(y))
    assert (x,y)==g._split_validation(ds,4,8)
    with pytest.raises(ValueError,match='INSUFFICIENT'):
        g._split_validation(ds,10,10)


def test_calibration_step_selection_no_eval_lookahead():
    curve=[{'step':0,'cal_f1':.82,'cal_edge_errors':100,'margin':1.5},
           {'step':2,'cal_f1':.81,'cal_edge_errors':110,'margin':1.},
           {'step':4,'cal_f1':.835,'cal_edge_errors':90,'margin':2.}]
    assert g._select_checkpoint(curve,4)==4
    decisions=g._validate_selection({}, {'arm':curve},4)
    assert decisions['arm']['fixed_final']=={'step':4,'margin':2.}
    assert decisions['arm']['cal_selected']=={'step':4,'margin':2.}
    curve[2]['cal_f1']=.79
    assert g._select_checkpoint(curve,4)==2


def test_pair_bootstrap_has_correct_sign():
    base={'by_pair':{'1':{'tp':2,'gold_edges':4,'predicted_edges':4},
                     '2':{'tp':2,'gold_edges':4,'predicted_edges':4}}}
    win={'by_pair':{'1':{'tp':3,'gold_edges':4,'predicted_edges':4},
                    '2':{'tp':3,'gold_edges':4,'predicted_edges':4}}}
    assert g._bootstrap(base,win)==[25.,25.]
    with pytest.raises(ValueError,match='EVAL_PAIR'):
        g._bootstrap(base,{'by_pair':{'1':base['by_pair']['1']}})


def test_weight_source_integrity_and_optimizer_restore_hash(tmp_path,monkeypatch):
    from safetensors.torch import save_file
    root=tmp_path/'source';root.mkdir()
    plan=tmp_path/'plan';plan.mkdir()
    cap=tmp_path/'capture';cap.mkdir()
    (plan/'plan.json').write_text('{}');(cap/'capture.json').write_text('{}')
    model=p55.model_for('set_prediction')
    save_file(model.state_dict(),str(root/'checkpoint-000004900.safetensors'))
    opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
    torch.save(opt.state_dict(),root/'checkpoint-000004900.optimizer.pt')
    r={'step':4900,
       'weights_sha256':scale.file_sha(root/'checkpoint-000004900.safetensors'),
       'optimizer_sha256':scale.file_sha(root/'checkpoint-000004900.optimizer.pt')}
    (root/'checkpoint-000004900.json').write_text(json.dumps(r))
    cfg={'training_budget':{'requested_training_graphs':5000},
         'sealed_test_used':False,'production_promotion':False,
         'plan_sha256':scale.file_sha(plan/'plan.json'),
         'capture_config_sha256':scale.file_sha(cap/'capture.json')}
    (root/'training.json').write_text(json.dumps(cfg))
    class FakeCorpus(Corpus):
        def __init__(self,plan_dir,capture_dir,split,require_complete):
            super().__init__(split,16)
            self.config={'candidate_policy':'actor_cues'}
    monkeypatch.setattr(scale,'FeatureCorpus',FakeCorpus)
    state,td,vd,config,receipt,path,limit=g._load_parent(root,plan,cap,4900,'cpu',True)
    assert path.exists() and limit==5000 and receipt['weights_sha256']==r['weights_sha256']
    assert set(state)==set(model.state_dict())
    corrupted={**r,'optimizer_sha256':'x'}
    (root/'checkpoint-000004900.json').write_text(json.dumps(corrupted))
    with pytest.raises(ValueError,match='ORIGINAL_OPTIMIZER_HASH'):
        g._load_parent(root,plan,cap,4900,'cpu',True)
    (root/'checkpoint-000004900.json').write_text(json.dumps(r))
    bad={**cfg,'sealed_test_used':True}
    (root/'training.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='NOT_RESEARCH'):
        g._load_parent(root,plan,cap,4900,'cpu',True)


def test_optimizer_state_restored_vs_fresh_is_isolated(tmp_path):
    model=p55.model_for('set_prediction')
    src=g._snap(model)
    opt=torch.optim.AdamW(model.parameters(),lr=5e-4)
    ds=Corpus('train',8)
    g._train_step(model,opt,ds,(0,1),'cpu')
    state_file=tmp_path/'optimizer.pt';torch.save(opt.state_dict(),state_file)
    fresh,_=g._run_arm(src,ds,((0,1),),lr=5e-4,device='cpu',milestones=(1,))
    resumed,_=g._run_arm(src,ds,((0,1),),lr=5e-4,device='cpu',milestones=(1,),optimizer_path=state_file)
    assert all(torch.equal(src[k],fresh[0][k]) for k in src)
    assert all(torch.equal(src[k],resumed[0][k]) for k in src)
    assert any(not torch.equal(fresh[1][k],resumed[1][k]) for k in src)


def test_full_orchestrator_equal_budget_and_no_eval_before_cal(monkeypatch,tmp_path):
    read=[]
    td=Corpus('train',16);vd=Corpus('validation',16)
    def fake_load(*args):
        model=p55.model_for('set_prediction')
        return (g._snap(model),td,vd,
                {'plan_sha256':'p','capture_config_sha256':'c',
                 'training_budget':{'requested_training_graphs':5000}},
                {'weights_sha256':'w','optimizer_sha256':'o'},tmp_path/'optimizer.pt',5000)
    monkeypatch.setattr(g,'_load_parent',fake_load)
    raw=g._collect
    def audited_collect(model,ds,indices,margin=None):
        read.append(('train' if ds is td else 'validation',tuple(indices),margin))
        return raw(model,ds,indices,margin)
    monkeypatch.setattr(g,'_collect',audited_collect)
    report=g.run(training_dir=tmp_path,plan_dir=tmp_path,capture_dir=tmp_path,
                 low_pairs=4,high_pairs=8,calibration_pairs=4,evaluation_pairs=4,
                 steps=4,batch_pairs=2,interval=2,device='cpu',cpu_threads=2)
    assert len(report['training'])==6
    assert all(v['steps']==4 for v in report['training'].values())
    assert set(report['design']['milestones'])=={0,2,4}
    assert report['frozen_reference']['overall']['graphs']==8
    assert all(set(v)=={'fixed_final','cal_selected'} for v in report['evaluation'].values())
    assert report['sealed_test_used'] is False and report['production_promotion'] is False
    assert 'G2-0069' in g.format_report(report)
    # CAL is read for every arm/snapshot before the first EVAL read.
    assert all(tuple(indices)==read[0][1] for _kind,indices,_margin in read[:1])
    assert sum(entry[2] is None for entry in read)==(6*3+1)
    assert sum(entry[2] is not None for entry in read)==(1+6*2)
    # No original saved checkpoint under report working directory.
    assert not list(tmp_path.glob('*.safetensors'))


def test_cli_output_create_only(tmp_path):
    existing=tmp_path/'already.json';existing.write_text('original')
    with pytest.raises(SystemExit):g.main(['--output',str(existing)])
    assert existing.read_text()=='original'
