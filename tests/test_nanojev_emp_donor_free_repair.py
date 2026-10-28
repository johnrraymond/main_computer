"""Donor-free repair: isolation, fold discipline, sparsity, model freeze, gate tests."""
import json
from types import SimpleNamespace

import pytest
import torch

from tools import nanojev_emp_donor_free_repair as lab
from tools.nanojev_addition_curriculum import AdditionNet


def fixtures():
    defects=lab.defects_from_csv(None)
    local=lab.neighborhood(defects,lab.DEFAULT_ANCHORS,2,464,32)
    return defects,local


def test_four_defects_and_real_30_card_neighborhood():
    defects,local=fixtures()
    assert len(defects)==4
    assert len(local)==30
    assert all(r['a']+r['b']==r['target'] for r in local)
    assert {(r['a'],r['b']) for r in defects} <= {(r['a'],r['b']) for r in local}
    assert all(r['a']*r['b'] <= 0 for r in local)


def test_saved_folds_exclude_known_observations_and_each_other():
    defects,local=fixtures()
    cfg={'parameters':{'seed':42,'start_radius':32,'radius_step':16,
         'initial_delta':8,'delta_step':1,'max_delta':32}}
    sets=lab.build_sets(cfg,defects,local,(128,256,384,464),64,27)
    seen={k:{(r['a'],r['b']) for r in rs} for k,rs in sets.items()}
    excluded={(r['a'],r['b']) for r in local}
    assert all(seen[k].isdisjoint(excluded) for k in seen)
    assert seen['train'].isdisjoint(seen['dev'])
    assert seen['train'].isdisjoint(seen['test'])
    assert seen['dev'].isdisjoint(seen['test'])
    for name,rs in sets.items():
        allowed={'train':{2,3,4},'dev':{0},'test':{1}}[name]
        assert all(lab.iso.stable_bucket((r['a'],r['b'],r['target']),42) in allowed for r in rs)
    with pytest.raises(RuntimeError,match='SAVED_SAMPLER'):
        lab.build_sets({},defects,local,(128,),64,27)
    with pytest.raises(ValueError,match='NOT_MASTERED'):
        lab.build_sets(cfg,defects,local,(999,),64,27)


def test_oracle_coordinates_never_import_donor_values(tmp_path):
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    old=model.state_dict()
    mask=lab.input_masks(old,'baseline')
    assert sum(int(x.numel()) for x in mask.values())==16*(10+7+2+2+1)
    items=[]
    for k in lab.INPUT_KEYS:
        value=old[k]; row=None if value.ndim==1 else 0
        items.append({'tensor':k,'shape':list(value.shape),
                      'active_rows':[{'row':row,'dimensions':[0]}],
                      'donor_parameter_values':[9999.0]})
    path=tmp_path/'active_delta.json'
    path.write_text(json.dumps({'tensors':items}),encoding='utf-8')
    mask=lab.input_masks(old,'oracle',str(path))
    assert sum(int(x.sum()) for x in mask.values())==5
    assert all(int(x.max())==1 for x in mask.values())
    assert all(float(x.min())==0 for x in mask.values())
    with pytest.raises(ValueError,match='NEEDS_EXPLICIT'):
        lab.input_masks(old,'oracle')


def test_gradients_and_prox_do_not_modify_frozen_weights():
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    old={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
    masks={k:torch.ones_like(old[k]) for k in lab.INPUT_KEYS}
    params,hooks=lab.configure_trainable(model,old,masks)
    assert set(k for k,p in model.named_parameters() if p.requires_grad)==set(lab.INPUT_KEYS)
    prior=lab.capture_teacher(model,[{'a':-6,'b':4,'target':-2}], 'cpu',1)
    try:
        optim=torch.optim.Adam(params.values(),lr=.01)
        args=SimpleNamespace(preserve_batch=1,device='cpu',defect_weight=8.,
                             preserve_ce=.1,distill_weight=.5,l2=.01,
                             lr=.01,sparsity=0.,group_sparsity=0.,
                             max_delta_norm=1.,clip=1.)
        lab.optimizer_step(model,optim,params,old,masks,
            [{'a':-8,'b':5,'target':-3}],[{'a':-6,'b':4,'target':-2}],
            prior,__import__('random').Random(4),args,'baseline','cpu')
        state={k:v.detach().cpu() for k,v in model.state_dict().items()}
        lab.validate_frozen(old,state)
        assert lab.diff_count(old,state)>0
        # One-coordinate masked update is the only permitted parameter modification.
        model.load_state_dict(old)
        for k in masks:masks[k].zero_()
        masks[lab.INPUT_KEYS[0]][4,7]=1
        for h in hooks:h.remove()
        params,hooks=lab.configure_trainable(model,old,masks)
        optim=torch.optim.Adam(params.values(),lr=.01)
        lab.optimizer_step(model,optim,params,old,masks,
            [{'a':-8,'b':5,'target':-3}],[{'a':-6,'b':4,'target':-2}],
            prior,__import__('random').Random(4),args,'oracle','cpu')
        state={k:v.detach().cpu() for k,v in model.state_dict().items()}
        lab.validate_frozen(old,state)
        assert lab.diff_count(old,state)<=1
    finally:
        for h in hooks:h.remove()


def test_candidate_selection_and_gate_are_strict():
    def candidate(mode,updated,dev=0,train=0):
        return {'mode':mode,'qualified':True,'updated_elements':updated,'delta_l2':.2,
                'step':100,'dev_guard':{'new_regressions':dev},
                'train_guard':{'new_regressions':train}}
    a=candidate('baseline',1408)
    b=candidate('sparse',288)
    assert lab.select_best([a,b]) is b
    b['qualified']=False
    assert lab.select_best([a,b]) is a
    a['qualified']=False
    assert lab.select_best([a,b]) is None
    cases=[{'a':-2,'b':1,'target':-1},{'a':-3,'b':2,'target':-1}]
    assert lab.compare([-1,-2],[-1,-1],cases)['new_repairs']==1
    assert lab.compare([-1,-1],[-2,-1],cases)['new_regressions']==1


def test_no_donor_access_in_baseline_and_sparse(monkeypatch):
    # Even if an oracle-coordinate file is absent, ordinary modes are available.
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    old=model.state_dict()
    for mode in ('baseline','sparse'):
        masks=lab.input_masks(old,mode,'/does/not/exist/donor.json')
        assert all(torch.count_nonzero(t)==t.numel() for t in masks.values())
    with pytest.raises(ValueError,match='INVALID_MODES'):
        lab.parse_modes('baseline,baseline')


def test_sparse_pruning_retains_necessary_coordinate(monkeypatch):
    model=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    before={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
    state={k:v.clone() for k,v in before.items()}
    critical='input.digit.weight'
    state[critical][4,7]+=1.
    for k in lab.INPUT_KEYS:
        state[k].view(-1)[:3]+=.0001
    def fake_evaluate(model,*_args):
        fixed=bool(model.state_dict()[critical][4,7] > before[critical][4,7]+.5)
        return {'qualified':fixed,'dev_guard':{'new_regressions':0}}
    monkeypatch.setattr(lab,'stage_evaluate',fake_evaluate)
    args=SimpleNamespace()
    pruned,trials=lab.prune_sparse_candidate(model,before,state,[],[],[],[],{},args)
    lab.validate_frozen(before,pruned)
    assert pruned[critical][4,7] == state[critical][4,7]
    assert lab.diff_count(before,pruned) < lab.diff_count(before,state)
    assert any(x['accepted'] for x in trials)


def test_no_candidate_does_not_touch_test_guard(monkeypatch):
    calls=[]
    def impossible(preds):
        calls.append('selection')
        return None
    monkeypatch.setattr(lab,'select_best',impossible)
    assert lab.select_best([]) is None
    assert calls == ['selection']
