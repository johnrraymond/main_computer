"""Parameter group discovery with no hardcoded repair location or donor data."""
from types import SimpleNamespace
import math
import torch
import pytest
from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_autodiscover_repair as autod


def model_and_old():
    m=AdditionNet('digit_tokens',width=16,heads=4,layers=1,rounds=1)
    return m,{k:v.detach().clone() for k,v in m.state_dict().items()}


def rows():
    return ([{'a':-8,'b':5,'target':-3},{'a':-7,'b':5,'target':-2}],
            [{'a':-6,'b':4,'target':-2},{'a':-5,'b':3,'target':-2}])


def test_full_model_group_discovery_from_actual_gradients():
    m,_=model_and_old()
    defect,controls=rows()
    groups,scores,ranked=autod.gradient_discovery(m,defect,controls,'cpu',2)
    assert 'input.digit' in groups
    assert 'input.norm' in groups
    assert 'backbone.layers.0' in groups
    assert 'sign_head' in groups
    assert 'digit_head' in groups
    assert 'query' in groups and 'pos' in groups
    assert all(not p.requires_grad for p in m.parameters())
    assert len(ranked)>5
    assert set(ranked) <= set(groups)
    assert all(x['defect_grad_l2']>0 for x in scores if x['group'] in ranked)
    # No oracle repair coordinates, post-training weights, or masks are consumed.
    assert all(isinstance(x['score'],float) for x in scores)


def test_group_score_conflict_sign_and_interleaving():
    m,_=model_and_old()
    groups={'input.digit':['input.digit.weight'],
            'input.norm':['input.norm.weight']}
    zero=torch.zeros_like(m.input.digit.weight)
    one=torch.ones_like(m.input.norm.weight)
    values=autod.score_from_grads(m,
                 {'input.digit.weight':torch.ones_like(zero),
                  'input.norm.weight':one},
                 {'input.digit.weight':-torch.ones_like(zero),
                  'input.norm.weight':one},groups)
    d={r['group']:r for r in values}
    assert d['input.digit']['gradient_cosine']==pytest.approx(-1.)
    assert d['input.norm']['gradient_cosine']==pytest.approx(1.)
    ranked=autod.interleave_priorities(values)
    assert set(ranked)==set(groups)
    assert autod.expansion_schedule('1,2,5',len(ranked),14)==[1,2]
    with pytest.raises(ValueError):
        autod.expansion_schedule('0,2',3,3)


def test_train_any_param_group_and_check_strict_freeze():
    m,orig=model_and_old()
    groups=autod.parameter_groups(m)
    chosen=['sign_head']
    selected=autod.select_editable(m,orig,groups,chosen)
    assert set(selected)==set(groups['sign_head'])
    assert set(n for n,p in m.named_parameters() if p.requires_grad)==set(selected)
    defects,controls=rows()
    teacher=autod.prior.capture_teacher(m,controls,'cpu',2)
    opt=torch.optim.Adam(list(selected.values()),lr=.01)
    args=SimpleNamespace(preserve_batch=2,defect_weight=8.,
          preserve_ce=.35,distill_weight=1.5,l2=.01,clip=1.,max_delta_norm=1.)
    metrics=autod.step(m,opt,selected,orig,defects,controls,teacher,
                      __import__('random').Random(2),args,'cpu')
    assert math.isfinite(metrics['loss'])
    state=m.state_dict()
    autod.frozen_match(orig,state,set(selected))
    assert autod.delta_counts(orig,state,set(selected))>0
    with pytest.raises(RuntimeError,match='FROZEN_CHANGED'):
        autod.frozen_match(orig,state,set())


def test_ablate_groups_only_if_control_and_dev_pass(monkeypatch):
    m,old=model_and_old()
    groups=autod.parameter_groups(m)
    state={k:v.clone() for k,v in old.items()}
    edit1=groups['input.digit'][0]
    edit2=groups['sign_head'][0]
    state[edit1].view(-1)[0] += 2
    state[edit2].view(-1)[0] += 3
    def fake_qualify(model,*unused):
        now=model.state_dict()
        correct=now[edit1].view(-1)[0]>old[edit1].view(-1)[0]+1
        return {'qualified':bool(correct),'defects_fixed':4 if correct else 0,
                'local_damage':0,'train_regressions':0,'dev_regressions':0}
    monkeypatch.setattr(autod,'qualify',fake_qualify)
    active,new,audit=autod.ablate_groups(m,old,state,groups,
                        ['input.digit','sign_head'],[],[],[],[],{},SimpleNamespace())
    assert active==['input.digit']
    assert len(audit)==2 and not audit[0]['accepted'] and audit[1]['accepted']
    assert torch.equal(new[edit2],old[edit2])
    assert not torch.equal(new[edit1],old[edit1])


def test_budgets_are_independent_and_test_never_consulted(monkeypatch,tmp_path):
    m,old=model_and_old()
    groups=autod.parameter_groups(m)
    defect,control=rows()
    selection=['input.digit']
    teacher=autod.prior.capture_teacher(m,control,'cpu',2)
    old_preds=autod.prior.predictions(m,defect,'cpu',2)
    base={'defects':old_preds,'local':old_preds,'train':old_preds,'dev':old_preds}
    monkeypatch.setattr(autod,'qualify',lambda *a,**k:{'qualified':False,
                     'defects_fixed':0,'local_damage':0,'train_regressions':0,
                     'dev_regressions':0,'dev_repairs':0})
    args=SimpleNamespace(search_steps=2,check_every=1,max_candidate_elements=999999,
         lr=.001,seed=42,preserve_batch=2,defect_weight=8.,preserve_ce=.35,
         distill_weight=1.5,l2=.01,clip=1.,max_delta_norm=1.,device='cpu',eval_batch=2)
    record,state=autod.optimize_budget(m,old,groups,selection,defect,defect,defect,
               defect,base,control,teacher,args,tmp_path,0)
    assert record['status']=='not_qualified' and state is None
    autod.frozen_match(old,m.state_dict(),set(groups[selection[0]]))
    assert (tmp_path/'budget_00_checks.json').is_file()
    # The next budget explicitly resets all previous fitted parameters.
    autod.select_editable(m,old,groups,['sign_head'])
    assert all(torch.equal(m.state_dict()[k],v) for k,v in old.items())


def test_budget_refusal_and_selection_invariant(tmp_path):
    m,old=model_and_old()
    groups=autod.parameter_groups(m)
    args=SimpleNamespace(max_candidate_elements=1)
    info,state=autod.optimize_budget(m,old,groups,['input.digit'],[],[],[],[],
                          {},[],{},args,tmp_path,0)
    assert state is None
    assert info['status']=='skipped_element_budget'
    assert autod.improve({'dev_regressions':0,'train_regressions':0,
         'edit_elements':10,'delta_norm':1,'step':5},None)
    assert not autod.improve({'dev_regressions':1,'train_regressions':0,
         'edit_elements':3,'delta_norm':.1,'step':5},
        {'dev_regressions':0,'train_regressions':0,
         'edit_elements':10,'delta_norm':1,'step':5})

@pytest.mark.parametrize('regress,exported',[(False,True),(True,False)])
def test_full_runner_exports_only_if_postselection_holdout_passes(monkeypatch,tmp_path,regress,exported):
    """End-to-end staging smoke for the export/refusal branch without CUDA data."""
    root=tmp_path/'isolated'; root.mkdir()
    campaign=tmp_path/'campaign'; campaign.mkdir()
    output=tmp_path/'output'
    (root/'config.json').write_text('{}')
    m,old=model_and_old()
    picked='sign_head.weight'
    original_value=old[picked].view(-1)[0].item()
    defect={'a':-8,'b':5,'target':-3}
    control={'a':-6,'b':4,'target':-2}
    train=[dict(a=-100-i,b=99+i,target=-1) for i in range(36)]
    dev=[dict(a=-10,b=9,target=-1)]
    test=[dict(a=-20,b=19,target=-1)]
    monkeypatch.setattr(autod.cross,'snapshot',lambda *a,**k:old)
    monkeypatch.setattr(autod.cross,'sha256',lambda *a,**k:'synthetic-source-sha')
    monkeypatch.setattr(autod.lasso,'initialize',lambda *a,**k:(m,None,None,None,None))
    monkeypatch.setattr(autod.prior,'defects_from_csv',lambda *a:[defect])
    monkeypatch.setattr(autod.prior,'neighborhood',lambda *a,**k:[defect,control])
    monkeypatch.setattr(autod.prior,'build_sets',lambda *a,**k:{'train':train,'dev':dev,'test':test})
    monkeypatch.setattr(autod.prior,'capture_teacher',lambda *a,**k:{})
    def predictions(model,rows,device,batch):
        altered=float(model.state_dict()[picked].view(-1)[0]) > original_value+.5
        outcome=[]
        for r in rows:
            if r is defect and not altered:outcome.append(99)
            elif r is test[0] and regress and altered:outcome.append(99)
            else:outcome.append(r['target'])
        return outcome
    monkeypatch.setattr(autod.prior,'predictions',predictions)
    groups={'sign_head':autod.parameter_groups(m)['sign_head']}
    monkeypatch.setattr(autod,'gradient_discovery',lambda *a,**k:(groups,[{'group':'sign_head','score':1.}],['sign_head']))
    def opt(_m,_old,_groups,chosen,*a):
        state={k:v.clone() for k,v in old.items()}
        state[picked].view(-1)[0]+=1.
        return {'status':'qualified','groups':chosen},state
    monkeypatch.setattr(autod,'optimize_budget',opt)
    monkeypatch.setattr(autod,'ablate_groups',lambda _m,_o,state,_g,chosen,*a:(chosen,state,[]))
    monkeypatch.setattr(autod,'qualify',lambda model,*a:{'qualified':True,
         'defects_fixed':1,'local_damage':0,'train_regressions':0,'dev_regressions':0})
    args=autod.parser().parse_args(['--run',str(root),'--campaign',str(campaign),
                                   '--ladder-output',str(tmp_path/'ladder'),
                                   '--output',str(output),'--device','cpu',
                                   '--guard-per-radius','40'])
    result=autod.run(args)
    assert result['repair_exported'] is exported
    assert (output/'autodiscovered_repair.pt').is_file() is exported
    assert result['status']==('certified' if exported else 'test_rejected')
    assert result['final_test']['new_regressions']==int(regress)
    assert result['retained_groups']==['sign_head']


def test_no_qualified_candidate_does_not_read_test_predictions(monkeypatch,tmp_path):
    root=tmp_path/'source';root.mkdir()
    campaign=tmp_path/'campaign';campaign.mkdir()
    (root/'config.json').write_text('{}')
    m,old=model_and_old()
    defect={'a':-8,'b':5,'target':-3}
    control={'a':-6,'b':4,'target':-2}
    train=[dict(a=-100-i,b=99+i,target=-1) for i in range(36)]
    test=[dict(a=-20,b=19,target=-1)]
    monkeypatch.setattr(autod.cross,'snapshot',lambda *a,**k:old)
    monkeypatch.setattr(autod.cross,'sha256',lambda *a,**k:'src')
    monkeypatch.setattr(autod.lasso,'initialize',lambda *a,**k:(m,None,None,None,None))
    monkeypatch.setattr(autod.prior,'defects_from_csv',lambda *a:[defect])
    monkeypatch.setattr(autod.prior,'neighborhood',lambda *a,**k:[defect,control])
    monkeypatch.setattr(autod.prior,'build_sets',lambda *a,**k:{'train':train,'dev':[control],'test':test})
    monkeypatch.setattr(autod.prior,'capture_teacher',lambda *a,**k:{})
    def never_test(m,rs,device,batch):
        assert rs is not test, 'Blind TEST was accessed without qualifying candidate'
        return [99 if r is defect else r['target'] for r in rs]
    monkeypatch.setattr(autod.prior,'predictions',never_test)
    monkeypatch.setattr(autod,'gradient_discovery',lambda *a,**k:(
        {'query':['query']},[{'group':'query'}],['query']))
    monkeypatch.setattr(autod,'optimize_budget',lambda *a,**k:(
        {'status':'not_qualified','groups':['query']},None))
    args=autod.parser().parse_args(['--run',str(root),'--campaign',str(campaign),
           '--ladder-output',str(tmp_path/'ladder'),'--output',str(tmp_path/'out'),
           '--device','cpu'])
    result=autod.run(args)
    assert result['status']=='no_qualified_candidate'
    assert not (tmp_path/'out'/'autodiscovered_repair.pt').exists()
