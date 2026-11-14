"""Read-only four-bank EMP diagnostic: measurement, alignment, checkpoint safety."""
from __future__ import annotations

import copy
import json
from argparse import Namespace

import pytest
import torch

from tools import nanojev_emp_dual_bank_diagnostic as diag
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_addition_curriculum import AdditionNet
from tools.nanojev_emp_population import Population, SCHEMA as POP_SCHEMA


def test_inward_overshoot_sign_zero_and_buckets():
    assert diag.error_type(100,80)=='toward_zero'
    assert diag.error_type(-100,-80)=='toward_zero'
    assert diag.error_type(100,120)=='overshoot'
    assert diag.error_type(-100,-120)=='overshoot'
    assert diag.error_type(-100,100)=='tipping'
    assert diag.error_type(100,0)=='tipping'
    assert diag.error_type(0,0)=='exact_zero'
    assert diag.error_type(0,1)=='false_nonzero'
    assert diag.correct(100,105) and diag.correct(-100,-95)
    assert not diag.correct(100,106)
    assert diag.shell(0)=='zero'
    assert diag.shell(1024)=='1024..2047'
    assert diag.shell(-8192)=='8192..16383'


def test_case_level_disagreement_and_bias():
    rows=[(500,-400,100,0,500),(-500,400,-100,0,500),
          (2048,-1024,1024,1,2048),(8192,-8192,0,2,8192)]
    preds={
        'left.left':[0,-100,512,0],
        'left.right':[100,-100,1024,0],
        'right.left':[100,-100,1024,1],
        'right.right':[100,-100,1024,0],
    }
    s=diag.analyze_deck(rows,preds,top=12)
    assert all(s['pair_left_left_vs_right_right'][name]==value for name,value in {'both_correct':2,'left_only':0,'right_only':2,'both_wrong':0}.items())
    assert s['within_emp_pairs']['left']['right_only']==2
    assert s['within_emp_pairs']['left']['right_lower_absolute_error']==2
    assert s['summary']['left.left']['error_types']['tipping']==1
    assert s['summary']['left.left']['scale_bias']==pytest.approx((-1+0-.5)/3)
    assert s['summary']['right.right']['within_5pct']==1
    assert s['groups']['residual_shell']['1024..2047']['left.left']['n']==1
    assert s['groups']['residual_sign']['negative']['left.left']['n']==1
    assert {x['target'] for x in s['right_correct_left_wrong_examples']}=={100,1024}
    assert s['right_correct_left_wrong_total']==2
    with pytest.raises(ValueError,match='ALIGNMENT'):
        diag.analyze_deck(rows,{'left.left':[1]})


def test_optimizer_controls_and_steps():
    model=AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1)
    cp={'controls':dual.default_controls(),'optimizers':{n:{'state':{},'param_groups':[{'section':'backbone','lr':1e-5}]}
                                        for n in dual.BANKS}}
    state=diag.optimizer_diagnostics(cp)
    assert state['banks']['left.left']['settings']['frontier_boost']==8
    assert state['banks']['right.right']['settings']['frontier_boost']==1
    assert state['banks']['left.left']['optimizer_step_max'] is None
    cp['optimizers']['left.left']['state']={1:{'step':torch.tensor(120)},2:{'step':torch.tensor(119)}}
    updated=diag.optimizer_diagnostics(cp)
    assert updated['banks']['left.left']['optimizer_step_min']==119
    assert updated['banks']['left.left']['optimizer_step_max']==120


def synthetic_campaign(tmp_path,monkeypatch):
    parent=AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1)
    pop=Population(copy.deepcopy(parent))
    pop_ckpt={'schema':POP_SCHEMA+'.checkpoint','step':1,'model':pop.state_dict()}
    banks=dual.construct_banks(parent,pop_ckpt,pop_ckpt)
    controls=dual.default_controls()
    optimizers=dual.make_bank_optimizers(banks,controls)
    root=tmp_path/'campaign';root.mkdir()
    for name in ('left','right'):
        torch.save(pop_ckpt,root/f'source_{name}.pt')
    cfg={'schema':ladder.SCHEMA,'seed':47,'dev_cards':4,'breadth_anchors':1,'breadth_outer_per_anchor':1,
         'left_source_sha256':diag.sha256(root/'source_left.pt'),
         'right_source_sha256':diag.sha256(root/'source_right.pt')}
    ladder.atomic_json(root/'config.json',cfg)
    def save(rad,name,step):
        stage=root/'stages'/f'outward_{rad}';stage.mkdir(parents=True,exist_ok=True)
        cp={'schema':ladder.SCHEMA+'.checkpoint','radius':rad,'step':step,'banks':banks.state_dict(),
            'optimizers':{n:o.state_dict() for n,o in optimizers.items()},'controls':controls}
        torch.save(cp,stage/name)
        ladder.atomic_json(stage/'config.json',{'radius':rad,'campaign_config_sha256':diag.sha256(root/'config.json')})
        return stage
    stage=save(8192,'latest.pt',300000)
    parent_stage=save(4096,'accepted.pt',1000)
    # Previous accepted stage also has latest.pt, like a real ladder.
    (parent_stage/'latest.pt').write_bytes((parent_stage/'accepted.pt').read_bytes())
    ladder.atomic_json(parent_stage/'acceptance.json',{'checkpoint_sha256':diag.sha256(parent_stage/'accepted.pt')})
    monkeypatch.setattr(ladder,'load_certified_model',lambda *args,**kw:(copy.deepcopy(parent),{}))
    cards=[(100,-99,1,1,100),(512,-200,312,1,512),(-512,200,-312,1,512),(-1024,1024,0,1,1024)]
    monkeypatch.setattr(diag,'dev_cards',lambda cfg,radius:{'frontier':[cards,cards], 'breadth':[cards,cards]})
    args=Namespace(output=str(root),ladder_output='unused',radius=8192,device='cpu',
                   eval_batch_size=16,cpu_threads=1,top=2,compare_parent=True,
                   report=str(tmp_path/'diagnostic.json'))
    return args,root


def test_inspection_only_preserves_checkpoint_and_runs_parent_comparison(tmp_path,monkeypatch,capsys):
    args,root=synthetic_campaign(tmp_path,monkeypatch)
    current=root/'stages'/'outward_8192'/'latest.pt'
    older=root/'stages'/'outward_4096'/'accepted.pt'
    before=(diag.sha256(current),diag.sha256(older))
    report=diag.run(args)
    assert (diag.sha256(current),diag.sha256(older))==before
    assert report['split']=='dev' and report['step']==300000
    assert set(report['current'])=={'frontier.dev_0','frontier.dev_1','outer.dev_0','outer.dev_1','inner.dev_0','inner.dev_1','zero.dev_0','zero.dev_1'}
    assert report['parent_comparison']['accepted_radius']==4096
    assert set(report['current']['frontier.dev_0']['summary'])==set(dual.BANKS)
    assert (tmp_path/'diagnostic.json').is_file()
    assert not (root/'diagnostic.json').exists()
    assert 'EMP_DIAG_SUMMARY=' in capsys.readouterr().out


def test_refuses_report_inside_campaign_and_bad_provenance(tmp_path,monkeypatch):
    args,root=synthetic_campaign(tmp_path,monkeypatch)
    args.compare_parent=False
    args.report=str(root/'diagnostic.json')
    with pytest.raises(ValueError,match='OUTSIDE_CAMPAIGN'):
        diag.run(args)
    args.report=str(tmp_path/'new.json')
    cfg=json.loads((root/'config.json').read_text());cfg['seed']=888
    ladder.atomic_json(root/'config.json',cfg)
    with pytest.raises(RuntimeError,match='PROVENANCE_DRIFT'):
        diag.run(args)


def test_hard_miner_train_only_deterministic_and_reserved(monkeypatch):
    from tools import nanojev_zero_line_sampled_radius_ladder as old
    original = old.stable_fold
    inspected = []
    def guarded_fold(m, seed):
        result = original(m, seed)
        inspected.append(result)
        return result
    monkeypatch.setattr(old, 'stable_fold', guarded_fold)
    a,info_a = diag.mine_train_cards(8192, 47, 256)
    b,info_b = diag.mine_train_cards(8192, 47, 256)
    assert a == b and info_a == info_b
    assert len(a) == 256
    assert len({(row[0], row[1]) for row in a}) == 256
    assert all(diag.specialist_partition(row[4], 47) == 'mine' for row in a)
    assert all(original(row[4],47) == 'train' for row in a)
    assert 'test' not in (original(row[4],47) for row in a)
    # The sampling logic calls only the explicitly TRAIN generator. The
    # reserved anchor families never appear in the evaluated case set.
    assert info_a['reserved_skipped'] > 0
    assert inspected


def test_hard_case_ranking_shared_failures_and_independent_ownership():
    rows=[(100,-50,50,1,100),(201,-100,101,2,201),
          (-200,100,-100,2,200),(500,-500,0,3,500),
          (-300,100,-200,2,300)]
    preds={
        'left.left':[0,101,-100,1,-200],
        'left.right':[0,101,-100,0,0],
        'right.left':[0,500,100,0,0],
        'right.right':[0,101,100,0,0],
    }
    selection, meta=diag.rank_hard_cases(rows,preds,('right.left','right.right'),max_cases=10,max_per_anchor=2,min_failures=2)
    assert meta['breakdown']['all_four_wrong']==1
    assert meta['breakdown']['both_selected_wrong']>=2
    assert selection[0]['target']==50 and selection[0]['all_four_wrong']
    assert all(row['split']=='train' and row['specialist_partition']=='mine' for row in selection)
    assert selection[0]['wrong_count']==4
    assert not any(x['target']==101 for x in selection)  # only one bank wrong
    assert diag.high_resolution_error(rows,preds['right.right'])['absolute_relative_error']['p99'] >= 1
    with pytest.raises(ValueError,match='BAD_SELECTED_PAIR'):
        diag.rank_hard_cases(rows,preds,('right.left','right.left'),10)


def test_end_to_end_train_hard_export_not_campaign_mutation(tmp_path,monkeypatch,capsys):
    args,root=synthetic_campaign(tmp_path,monkeypatch)
    args.mine_hard=True
    args.hard_candidates=128
    args.hard_max_cases=30
    args.hard_per_anchor=2
    args.hard_min_failures=1
    args.hard_export=str(tmp_path/'specialist_train_candidates.jsonl')
    before=(diag.sha256(root/'stages'/'outward_8192'/'latest.pt'),
            diag.sha256(root/'config.json'))
    report=diag.run(args)
    after=(diag.sha256(root/'stages'/'outward_8192'/'latest.pt'),
           diag.sha256(root/'config.json'))
    assert before==after
    hard=report['hard_mining']
    assert hard['train_pool']['requested']==128
    assert hard['source_split']=='train'
    assert hard['summary']['scanned']==128
    assert hard['partition_algorithm']==diag.HARD_SPLIT_ALGORITHM
    assert 'EMP_DIAG_HARD_CASES=' in capsys.readouterr().out
    path=tmp_path/'specialist_train_candidates.jsonl'
    assert path.is_file() and hard['export_sha256']==diag.sha256(path)
    examples=[json.loads(x) for x in path.read_text('utf-8').splitlines()]
    assert len(examples)==hard['summary']['exported']
    assert all(diag.specialist_partition(x['anchor'],47)=='mine' for x in examples)
    assert all(x['split']=='train' and x['checkpoint_sha256']==before[0] for x in examples)
    assert 'error_percentiles' in report['current']['frontier.dev_0']['summary']['left.left']
    args.hard_export=str(root/'train_cases.jsonl')
    with pytest.raises(ValueError,match='OUTSIDE_CAMPAIGN'):
        diag.run(args)
