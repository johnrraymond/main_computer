"""No-CUDA, no-Qwen, nonmutating radius/operand-coverage telemetry tests."""
from pathlib import Path
import json
import torch
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed


def _ordered_state(index=0):
    return {'status':'TRAINING','step':135,'task_index':index,
            'seed_state':{'rung':1,'radius_index':1},
            'policy':{'levels':2,'operand_boundary':1024}}


def test_seed_index_is_not_the_legacy_radius_value(tmp_path):
    source=tmp_path/'seed_snapshot.pt'
    seed={'schema':ladder.SCHEMA,
          'state':{'radius_index':1,'config':{'radii':[0,3,10]}}}
    torch.save(seed,source)
    assert fr._legacy_seed_radius(source)==3
    assert fr._legacy_seed_radius(tmp_path/'absent.pt') is None
    seed['state']['config']['radii']=[]
    torch.save(seed,source)
    assert fr._legacy_seed_radius(source) is None  # observational only


def test_current_level_anchor_is_not_a_legacy_radius():
    # With the default order, 9 tasks in L0, then task 0 in L1.
    v=fr._radius_info(_ordered_state(index=9),phase='systematic',seed_radius=3)
    assert v['active_task']=='signed_arithmetic'
    assert v['active_level']==1
    assert v['active_level_anchor']==2048
    assert v['level_anchors']==[1024,2048]
    assert v['legacy_seed_signed_result_radius']==3
    assert v['active_signed_result_radius'] is None
    assert v['radius_increases_during_training'] is False
    assert v['frontier_task_level_cells']==18


def test_geometry_anchor_not_falsely_applied_as_operand_limit():
    v=fr._radius_info(_ordered_state(index=6),phase='systematic',seed_radius=0)
    assert v['active_task']=='coordinates_geometry'
    assert v['other_tasks_use_own_difficulty_scales'] is True
    assert v['anchor_used_for']==['signed_arithmetic','arithmetic_operators']


def test_phase_one_radius_command_reads_checkpoint_without_writing(tmp_path,capsys):
    out=tmp_path/'ordered';out.mkdir()
    torch.save({'schema':fr.SCHEMA,'state':_ordered_state(9)},out/'working.pt')
    torch.save({'schema':ladder.SCHEMA,
                'state':{'radius_index':1,'config':{'radii':[0,4]}}},out/'seed_snapshot.pt')
    h=[fr._sha256(p) for p in (out/'working.pt',out/'seed_snapshot.pt')]
    r=fr.main(['--phase','systematic','--stage','radius','--output',str(out)])
    assert r['legacy_seed_signed_result_radius']==4
    assert r['active_level_anchor']==2048
    assert 'QWEN_FRONTIER_RADIUS=' in capsys.readouterr().out
    assert h==[fr._sha256(p) for p in (out/'working.pt',out/'seed_snapshot.pt')]


def test_phase_two_radius_command_shows_all_cells_without_reset(tmp_path,capsys):
    ordered=tmp_path/'ordered';ordered.mkdir()
    out=tmp_path/'ordered_mixed_v1';out.mkdir()
    state={'schema':mixed.SCHEMA,'status':'PAUSED','phase_step':15,'step':999,
           'seed_state':{'rung':1,'radius_index':0},
           'policy':{'source_levels':2,'operand_boundary':1024}}
    torch.save({'schema':mixed.SCHEMA,'state':state},out/'working.pt')
    torch.save({'schema':ladder.SCHEMA,
                'state':{'radius_index':0,'config':{'radii':[0,4]}}},ordered/'seed_snapshot.pt')
    original=fr._sha256(out/'working.pt')
    r=fr.main(['--phase','samples','--stage','radius','--output',str(ordered)])
    assert r['mixed_phase_samples_all_configured_levels']
    assert r['active_task'] is None
    assert r['legacy_seed_signed_result_radius']==0
    assert r['level_anchors']==[1024,2048]
    assert 'QWEN_MIXED_RADIUS=' in capsys.readouterr().out
    assert fr._sha256(out/'working.pt')==original


def test_incomplete_historical_status_does_not_crash_due_to_telemetry(tmp_path):
    source=tmp_path/'seed_snapshot.pt'
    torch.save({'schema':ladder.SCHEMA,'state':{'radius_index':4,'config':{'radii':[]}}},source)
    assert fr._legacy_seed_radius(source) is None
    assert fr._radius_info({'status':'PAUSED'},phase='samples')['coverage_known'] is False
