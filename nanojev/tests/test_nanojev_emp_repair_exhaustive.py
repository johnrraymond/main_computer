"""Exhaustive input-tensor repair experiments and post-selection guard invariants."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_emp_repair_exhaustive as m
from tools import nanojev_emp_targeted_repair as targeted
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_isolated_defects as iso


KEYS=tuple(sorted('input.'+name+'.weight' for name in ('digit','place','sign','role','norm')))


def toy_states():
    prior={k:torch.zeros(3,3) if not k.endswith('norm.weight') else torch.zeros(3)
           for k in KEYS}
    prior.update({'sign_head.weight':torch.zeros(3,3),'sign_head.bias':torch.zeros(3),
                  'digit_head.weight':torch.zeros(4,3),'digit_head.bias':torch.zeros(4)})
    later={k:(v+1 if k in KEYS else v.clone()) for k,v in prior.items()}
    return prior,later


def evidence():
    return [{'a':-456,'b':443,'target':-13,'before':-113,'after':-13,'previously_wrong':True},
            {'a':-457,'b':444,'target':-13,'before':-113,'after':-13,'previously_wrong':True},
            {'a':-400,'b':390,'target':-10,'before':-10,'after':-10,'previously_wrong':False}]


def fake_arm(before):
    def evaluate(model, state, deck, device, batch):
        good=bool(state['input.digit.weight'][1,0] != before['input.digit.weight'][1,0]
                  and state['input.place.weight'][0,2] != before['input.place.weight'][0,2])
        bad_guard=bool(state['input.sign.weight'][0,0]!=before['input.sign.weight'][0,0] and
                       state['input.role.weight'][0,0]==before['input.role.weight'][0,0])
        result=[]
        for card in deck:
            if card['a']==999 and bad_guard:
                predicted=card['target']+2
            elif card['a']==998 and state['input.role.weight'][0,0]!=before['input.role.weight'][0,0]:
                predicted=card['target']+2
            elif card.get('previously_wrong') and not good:
                predicted=card['target']-100
            else:
                predicted=card['target']
            result.append({'prediction':predicted,'gold_log_score':float(predicted==card['target']),
                           'correct':predicted==card['target']})
        return result
    return evaluate


def test_exact_32_subsets_unique_and_stable():
    pairs=m.combinations(KEYS)
    assert len(pairs)==32
    assert len({i for i,_ in pairs})==32
    assert pairs[0]==(0,())
    assert pairs[-1]==(31,KEYS)
    assert sorted(k for mask,subset in pairs for k in subset).count(KEYS[0])==16
    with pytest.raises(ValueError,match='1_TO_10'):
        m.combinations(['a','a'])


def test_exact_subset_matrix_and_guard_rejection(monkeypatch):
    before,after=toy_states()
    monkeypatch.setattr(m.crossover,'evaluate_arm',fake_arm(before))
    records,winners=m.enumerate_verified(None,before,after,KEYS,evidence(),
                         [{'a':999,'b':0,'target':0}],'cpu',10)
    assert len(records)==32
    assert records[0]['local_fixed']==0
    assert records[-1]['local_fixed']==2
    assert records[-1]['local_pass']
    assert records[-1]['screen_pass']  # certified full donor remains safe
    assert any(r['local_pass'] and not r['screen_pass'] for r in records)  # sign alone hurts guard
    assert winners[0]['tensors']==['input.digit.weight','input.place.weight']
    assert winners[0]['tensor_count']==2
    assert winners[0]['guard_regressions']==0
    assert len([r for r in records if r['screen_pass']])>0


def test_row_and_dimension_refinement_keep_only_required_coordinates(monkeypatch):
    old,new=toy_states()
    monkeypatch.setattr(m.crossover,'evaluate_arm',fake_arm(old))
    keys=('input.digit.weight','input.place.weight')
    initial=targeted.mix_by_keys(old,new,keys)
    row_state,row_trials=m.refine_rows(None,old,initial,keys,evidence(),
                      [{'a':999,'b':0,'target':0}],'cpu',10)
    assert any(t['accepted'] for t in row_trials)
    assert torch.equal(row_state['input.digit.weight'][0],old['input.digit.weight'][0])
    assert torch.equal(row_state['input.place.weight'][2],old['input.place.weight'][2])
    last,col_trials=m.refine_dimensions(None,old,row_state,keys,evidence(),
                      [{'a':999,'b':0,'target':0}],'cpu',10)
    assert len(col_trials)>0
    assert m.active_delta_count(old,last,keys)==4
    delta=m.delta_manifest(old,new,last,keys)
    assert delta[0]['changed_elements']==2
    assert delta[1]['changed_elements']==2
    assert delta[0]['active_rows'][0]['dimensions']==[0,2]
    assert delta[1]['active_rows'][0]['dimensions']==[0,2]
    assert all(torch.equal(last[k],old[k]) for k in old if k not in keys)
    assert m.local_eval(None,last,evidence(),'cpu',10)['passes']


def test_reject_invalid_donor_masks():
    old,new=toy_states()
    right=targeted.mix_by_keys(old,new,KEYS)
    header={'schema':targeted.SCHEMA+'.checkpoint','selected_tensors':KEYS,'model':right}
    info={'status':'certified','repair_exported':True,'selected_tensors':list(KEYS)}
    assert all(torch.equal(m.validate_donor(old,new,header,KEYS,info)[k], right[k]) for k in right)
    wrong={**right,'input.digit.weight':right['input.digit.weight'].clone()}
    wrong['input.digit.weight'][1,0]=0.5
    with pytest.raises(RuntimeError,match='PARAMETER_DRIFT'):
        m.validate_donor(old,new,{**header,'model':wrong},KEYS,info)
    with pytest.raises(RuntimeError,match='NOT_INPUT_ONLY'):
        m.validate_donor(old,new,{**header,'selected_tensors':('sign_head.weight',)},
                         ('sign_head.weight',),{**info,'selected_tensors':['sign_head.weight']})


def make_saved_inputs(tmp_path):
    base=tmp_path/'run';base.mkdir()
    cfg={'parameters':{'seed':42,'start_radius':32,'radius_step':16,
         'initial_delta':8,'delta_step':1,'max_delta':32}}
    (base/'config.json').write_text(json.dumps(cfg))
    old,new=toy_states()
    paths=[]
    for i,state in ((27,old),(28,new)):
        p=base/f'stage_{i:03d}'/'mastered_snapshot.pt'
        p.parent.mkdir();torch.save({'schema':iso.SCHEMA+'.mastery-snapshot',
            'stage':i,'config':cfg,'model':state},p)
        paths.append(p)
    cross_dir=tmp_path/'cross';cross_dir.mkdir()
    (cross_dir/'config.json').write_text(json.dumps({'schema':cross.SCHEMA,
        'before_stage':27,'after_stage':28,'source_run':str(base),
        'before_sha256':cross.sha256(paths[0]),'after_sha256':cross.sha256(paths[1])}))
    (cross_dir/'result.json').write_text(json.dumps({'schema':cross.SCHEMA,
        'before_stage':27,'after_stage':28}))
    import csv
    with (cross_dir/'natural_repair.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['a','b','target','before','after',
                                           'previously_wrong','repaired','regressed'])
        writer.writeheader()
        for r in evidence():
            writer.writerow({**r,'repaired':r['previously_wrong'],'regressed':False})
    targeted_dir=tmp_path/'targeted';targeted_dir.mkdir()
    camp=tmp_path/'campaign';camp.mkdir()
    tcfg={'run':str(base),'cross_transfer':str(cross_dir),'campaign':str(camp),
          'before_stage':27,'after_stage':28,'before_sha256':cross.sha256(paths[0]),
          'after_sha256':cross.sha256(paths[1]),
          'evidence_sha256':cross.sha256(cross_dir/'natural_repair.csv'),
          'guard_radii':[128,256,384,464], 'guard_per_radius':1024}
    (targeted_dir/'config.json').write_text(json.dumps(tcfg))
    repair=targeted.mix_by_keys(old,new,KEYS)
    torch.save({'schema':targeted.SCHEMA+'.checkpoint',
                'selected_tensors':KEYS,'model':repair},targeted_dir/'targeted_repair.pt')
    result={'status':'certified','repair_exported':True,
            'selected_tensors':list(KEYS),
            'repair_checkpoint_sha256':cross.sha256(targeted_dir/'targeted_repair.pt'),
            'safety_guard':{'new_regressions':0,'cards':1}}
    (targeted_dir/'result.json').write_text(json.dumps(result))
    (targeted_dir/'guard_cases.csv').write_text(
       'a,b,target,old_correct,new_correct\n999,0,0,True,True\n')
    args=argparse.Namespace(targeted=str(targeted_dir),campaign=str(camp),
        ladder_output=str(tmp_path/'ladder'),hard_state_dir='hard_emp_frontier_v2',
        output=str(tmp_path/'output'),device='cpu',batch=16,
        holdout_radii='160,224,320,448',holdout_per_radius=1024,
        guard_per_radius=None,max_guard_regressions=0,
        refine_rows=True,refine_dimensions=True)
    return args,old,new


def test_end_to_end_certified_export_and_post_selection_refusal(tmp_path,monkeypatch):
    args,old,new=make_saved_inputs(tmp_path)
    class Toy(nn.Module):
        def __init__(self):
            super().__init__()
            self.input=nn.Module()
            for name in ('digit','place','role','sign','norm'):
                x=torch.zeros(3,3) if name!='norm' else torch.zeros(3)
                setattr(self.input,name,nn.Module())
                getattr(self.input,name).weight=nn.Parameter(x)
            self.sign_head=nn.Linear(3,3,bias=True)
            self.digit_head=nn.Linear(3,4,bias=True)
    monkeypatch.setattr(m.lasso,'initialize',lambda *a,**kw:(Toy(),None,None,'',None))
    monkeypatch.setattr(m.crossover,'evaluate_arm',fake_arm(old))
    monkeypatch.setattr(m.targeted,'guards',lambda cfg,radii,count,stage,excluded:
        [{'a':999 if 128 in radii else 998,'b':0,'target':0}])
    result=m.run(args)
    assert result['status']=='certified'
    assert result['combinations']==32
    assert result['smallest_screened_tensor_subset']==['input.digit.weight','input.place.weight']
    assert result['updated_elements']==4
    assert (Path(args.output)/'combinations.csv').is_file()
    saved=torch.load(Path(args.output)/'exhaustive_repair.pt',weights_only=True)
    assert saved['updated_elements']==4
    assert torch.equal(saved['model']['sign_head.weight'],old['sign_head.weight'])
    # Holdout-only defect: no alternative may be selected after this is observed.
    args.output=str(tmp_path/'rejected')
    monkeypatch.setattr(m.crossover,'evaluate_arm',lambda model,state,deck,device,batch:
        [dict(prediction=(r['target']+1 if r['a']==998 and
                        state['input.digit.weight'][1,0] != old['input.digit.weight'][1,0]
                        else fake_arm(old)(model,state,[r],device,batch)[0]['prediction']),
              correct=not (r['a']==998 and state['input.digit.weight'][1,0] != old['input.digit.weight'][1,0])
              and fake_arm(old)(model,state,[r],device,batch)[0]['correct'],
              gold_log_score=0.0) for r in deck])
    refused=m.run(args)
    assert refused['status']=='final_holdout_rejected'
    assert refused['qualified_tensor_subsets']>0
    assert not (Path(args.output)/'exhaustive_repair.pt').exists()


def test_no_overlap_and_invalid_cli():
    with pytest.raises(ValueError,match='HOLDOUT'):
        m.parse_radii('160,160')
    assert m.parse_radii('160,224')==(160,224)
