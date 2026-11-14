"""No-training targeted repair; safety gate and provenance tests."""
import argparse
import csv
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_emp_targeted_repair as m
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_isolated_defects as iso


class Toy(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone=nn.Module()
        self.backbone.layers=nn.ModuleList([nn.Linear(2,2), nn.Linear(2,2)])
        self.sign_head=nn.Linear(2,3)
        self.digit_head=nn.Linear(2,70)
    def forward(self,x):
        raise NotImplementedError


def states():
    torch.manual_seed(31)
    base={k:v.detach().clone() for k,v in Toy().state_dict().items()}
    newer={k:(v+1. if not k.startswith(('sign_head.','digit_head.')) else v+.05)
           for k,v in base.items()}
    return base,newer


def rows():
    return [
        {'a':-456,'b':443,'target':-13,'before':-113,'after':-13,'previously_wrong':True},
        {'a':-457,'b':444,'target':-13,'before':-113,'after':-13,'previously_wrong':True},
        {'a':-400,'b':390,'target':-10,'before':-10,'after':-10,'previously_wrong':False},
    ]


def fake_evaluate(before,after):
    """Only second backbone layer's weight delta is actually sufficient."""
    def f(model, state, batch, device, chunk):
        corrected=torch.equal(state['backbone.layers.1.weight'],after['backbone.layers.1.weight'])
        result=[]
        for row in batch:
            is_known_wrong=(row['a'],row['b']) in {(-456,443),(-457,444)}
            predicted=(row['target']-100 if is_known_wrong and not corrected else row['target'])
            result.append({'prediction':predicted,'gold_log_score':float(predicted==row['target']),
                           'correct':predicted==row['target']})
        return result
    return f


def test_partition_and_exact_old_head_immutability():
    before,after=states()
    keys=m.changed_keys(before,after)
    assert len(keys)==4
    assert m.component('backbone.layers.1.linear1.weight')=='backbone.layers.1'
    assert m.component('input.encoder.0.weight')=='input'
    assert m.component('query')=='query'
    assert set(m.tensors_by_group(keys))=={'backbone.layers.0','backbone.layers.1'}
    repaired=m.mix_by_keys(before,after,{'backbone.layers.1.weight'})
    assert torch.equal(repaired['backbone.layers.1.weight'],after['backbone.layers.1.weight'])
    assert torch.equal(repaired['sign_head.weight'],before['sign_head.weight'])
    assert torch.equal(repaired['digit_head.bias'],before['digit_head.bias'])
    with pytest.raises(RuntimeError,match='UNKNOWN_OR_READOUT'):
        m.mix_by_keys(before,after,{'digit_head.weight'})


def test_backward_pruning_leaves_only_sufficient_tensor(monkeypatch):
    before,after=states()
    monkeypatch.setattr(m.crossover,'evaluate_arm',fake_evaluate(before,after))
    log=[]
    result=m.greedy_prune(before,after,rows(),Toy(),'cpu',32,log)
    assert result['baseline']['fixed']==0
    assert result['all_backbone']['fixed']==2
    assert result['selected']==('backbone.layers.1.weight',)
    assert result['final']['damaged_controls']==0
    assert len(log)>=4
    assert all(r['phase'] in ('baseline','all_backbone','group_delete','tensor_delete','selected') for r in log)


def test_control_damage_blocks_repair():
    cohort=rows()
    outputs=[{'prediction':-13,'gold_log_score':0},{'prediction':-13,'gold_log_score':0},
             {'prediction':-99,'gold_log_score':0}]
    measured=m.verdict(cohort,outputs)
    assert measured['fixed']==2 and measured['damaged_controls']==1
    assert not measured['passes']


@pytest.mark.parametrize('nested', [False, True])
def test_guard_uses_fixed_test_bucket_and_disjoint_inputs(nested):
    params={'seed':42,'start_radius':32,'radius_step':16,
            'initial_delta':8,'delta_step':1,'max_delta':32}
    cfg={'schema':iso.SCHEMA,'parameters':params} if nested else params
    first=m.guards(cfg,(32,48),64,1,rows())
    second=m.guards(cfg,(32,48),64,1,rows())
    assert first==second
    pairs=[(x['a'],x['b']) for x in first]
    assert len(pairs)==len(set(pairs))
    assert all((a,b) not in {(-456,443),(-457,444),(-400,390)} for a,b in pairs)
    with pytest.raises(ValueError,match='GUARD_RADIUS'):
        m.guards(cfg,(35,),64,1,rows())


def test_guard_uses_saved_seed_not_default_and_requires_provenance():
    params={'seed':42,'start_radius':32,'radius_step':16,
            'initial_delta':8,'delta_step':1,'max_delta':32}
    nested={'schema':iso.SCHEMA,'parameters':params}
    same=m.guards(nested,(32,48),64,1,rows())
    assert same==m.guards(params,(32,48),64,1,rows())
    other={'schema':iso.SCHEMA,'parameters':{**params,'seed':43}}
    assert same!=m.guards(other,(32,48),64,1,rows())
    with pytest.raises(RuntimeError,match='MISSING_RUN_PARAMETERS:seed'):
        m.guards({'schema':iso.SCHEMA,'parameters':{'start_radius':32}},(32,),64,0,rows())
    with pytest.raises(RuntimeError,match='INVALID_RUN_PARAMETERS'):
        m.guards({'parameters':[]},(32,),64,0,rows())


def make_fixture(tmp_path):
    run=tmp_path/'isolated';run.mkdir()
    config={'schema':iso.SCHEMA, 'parameters':
            {'seed':42,'start_radius':32,'radius_step':16,
             'initial_delta':8,'delta_step':1,'max_delta':32}}
    (run/'config.json').write_text(json.dumps(config))
    before,after=states()
    paths=[]
    for stage,state in ((27,before),(28,after)):
        folder=run/f'stage_{stage:03d}';folder.mkdir()
        p=folder/'mastered_snapshot.pt'
        torch.save({'schema':iso.SCHEMA+'.mastery-snapshot','stage':stage,
                    'config':config,'model':state},p)
        paths.append(p)
    evidence=tmp_path/'cross';evidence.mkdir()
    (evidence/'config.json').write_text(json.dumps({'schema':cross.SCHEMA,
          'before_stage':27,'after_stage':28,'source_run':str(run),
          'before_sha256':cross.sha256(paths[0]),'after_sha256':cross.sha256(paths[1])}))
    (evidence/'result.json').write_text(json.dumps({'schema':cross.SCHEMA,'before_stage':27,'after_stage':28}))
    with (evidence/'natural_repair.csv').open('w',newline='') as f:
        fields=['a','b','target','before','after','previously_wrong','repaired','regressed']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows():
            writer.writerow({**row,'repaired':row['previously_wrong'],
                             'regressed':False})
    return run,evidence,paths,before,after


def test_end_to_end_export_and_refuse_unsafe(tmp_path,monkeypatch):
    run,evidence,paths,before,after=make_fixture(tmp_path)
    fingerprint=[cross.sha256(p) for p in paths]
    monkeypatch.setattr(m.lasso,'initialize',lambda *a,**k:(Toy(),None,None,'',None))
    monkeypatch.setattr(m.crossover,'evaluate_arm',fake_evaluate(before,after))
    def guard_ok(model,before_state,candidate,guard,device,batch):
        subset_good=torch.equal(candidate['backbone.layers.1.weight'],after['backbone.layers.1.weight'])
        return ({'cards':1,'previously_correct':1,'previously_wrong':0,
                 'new_regressions':0 if subset_good else 1,'new_repairs':0,'new_correct':1},
                 [{'a':1,'b':-1,'target':0,'old_prediction':0,'new_prediction':0,
                   'old_correct':True,'new_correct':True,'new_regression':False,'new_repair':False}])
    monkeypatch.setattr(m,'score_guard',guard_ok)
    args=argparse.Namespace(run=str(run),cross_transfer=str(evidence),campaign=str(tmp_path/'campaign'),
         ladder_output=str(tmp_path/'ladder'),hard_state_dir='hard_emp_frontier_v2',
         output=str(tmp_path/'fixed'),device='cpu',before_stage=27,after_stage=28,batch=16,
         guard_radii='32,48',guard_per_radius=64,max_guard_regressions=0)
    result=m.run(args)
    assert result['status']=='certified'
    assert result['selected_tensors']==['backbone.layers.1.weight']
    assert result['local']['fixed']==2
    artifact=torch.load(tmp_path/'fixed'/'targeted_repair.pt',map_location='cpu',weights_only=True)
    assert artifact['schema']==m.SCHEMA+'.checkpoint'
    assert torch.equal(artifact['model']['sign_head.weight'],before['sign_head.weight'])
    assert [cross.sha256(p) for p in paths]==fingerprint
    # The same local repair must NOT be exported if the untouched guard fails.
    def unsafe(*a,**k):
        stats,samples=guard_ok(*a,**k)
        return {**stats,'new_regressions':1},samples
    monkeypatch.setattr(m,'score_guard',unsafe)
    args.output=str(tmp_path/'unsafe')
    rejected=m.run(args)
    assert rejected['status']=='guard_rejected'
    assert not (tmp_path/'unsafe'/'targeted_repair.pt').exists()


def test_mixed_readout_cannot_be_written_when_checkpoint_is_repaired():
    old,new=states()
    candidate=m.mix_by_keys(old,new,m.changed_keys(old,new))
    for k in ('sign_head.weight','sign_head.bias','digit_head.weight','digit_head.bias'):
        assert torch.equal(candidate[k],old[k])
