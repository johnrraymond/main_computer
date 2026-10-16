"""Focused cutover invariants: prompt parity, targets, state, independent lineage."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_parallel_result_register_order_v3_8 as alt
from nanojev_register_stability_v3_generate import FIRST,SECOND,OPERATIONS,number_repr,render


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize('pair',[(17,-9),(-2000,13),(0,0),(2000000,-2000000)])
@pytest.mark.parametrize('op', [OPERATIONS[0],OPERATIONS[1],OPERATIONS[2],OPERATIONS[22]])
def test_prompt_changes_only_registration_order(pair,op):
    a,b=pair
    row={'a':a,'b':b,'operation':op[0],'family':op[1],
         'prompt':render(a,b,op)}
    swapped=alt.reorder_registration(row,True)
    assert alt.reorder_registration(row,False) is row
    assert swapped is not row
    assert {k:v for k,v in swapped.items() if k!='prompt'}=={k:v for k,v in row.items() if k!='prompt'}
    original_lines=row['prompt'].splitlines()
    reverse_lines=swapped['prompt'].splitlines()
    assert reverse_lines[:2]==[original_lines[1],original_lines[0]]
    assert reverse_lines[2:]==original_lines[2:]
    assert reverse_lines[0]==f'{SECOND} = {number_repr(b)}'
    assert reverse_lines[1]==f'{FIRST} = {number_repr(a)}'


def test_unknown_prompt_fails_closed():
    with pytest.raises(RuntimeError,match='ALTREG_UNEXPECTED_ORIGINAL_PROMPT_FORMAT'):
        alt.reorder_registration({'a':1,'b':2,'prompt':'bad'},True)


def test_global_per_record_alternation_survives_resume_and_even_batches():
    def original_batch(stream,count):
        op=OPERATIONS[1]
        rows=[]
        for _ in range(count):
            stream.drawn+=1
            rows.append({'a':12,'b':3,'operation':op[0],'prompt':render(12,3,op)})
        return rows
    class FakeStream:drawn=12000
    stream=FakeStream()
    generator=alt.alternate_batch(original_batch)
    all_rows=generator(stream,16)+generator(stream,16)+generator(stream,5)
    expect=[i%2==0 for i in range(37)]
    assert [r['prompt'].startswith(FIRST+' = ') for r in all_rows]==expect
    assert len([r for r in all_rows if r['prompt'].startswith(SECOND+' = ')])==18
    # The old exact operand order and instruction are preserved on every row.
    for row in all_rows:
        assert row['a']==12 and row['b']==3
        assert row['prompt'].endswith('operation: XXXXXX - YYYYYY =\nAnswer:')


def fake_parent(path,step,original_config=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    data={'schema':alt.v37.SCHEMA,'step':step,
          'config':original_config or {'old_config':'untouched'},
          'net':{'a':torch.arange(8,dtype=torch.float32)},
          'optimizer':{'state':{'moments':torch.tensor([7.,8.])}},
          'stream':{'drawn':128,'rng_state':[1,2,3]},
          'replay':{'records':[1,2,3]},
          'bottom_phase_tick':19,'bottom_phase_counts':{'intermediate':16,'result':3,'final':0}}
    torch.save(data,path)
    return data


def test_safe_fork_preserves_all_state_and_parent_bytes(tmp_path):
    parent=tmp_path/'v37';destination=tmp_path/'v38'
    original=fake_parent(parent/'latest.pt',10000)
    fake_parent(parent/'asinh_intermediary'/'best.pt',9900)
    fake_parent(parent/'asinh_result'/'best.pt',8000)
    before={str(p.relative_to(parent)):digest(p) for p in parent.rglob('*.pt')}
    manifest=alt.create_fork(parent,destination)
    assert manifest['fork_step']==10000
    assert (destination/alt.MARKER).exists()
    assert json.loads((destination/alt.MARKER).read_text())['order']==alt.ORDER_VALUE
    for name in ['latest.pt','asinh_result/best.pt','asinh_intermediary/best.pt']:
        new=torch.load(destination/name,weights_only=True)
        old=torch.load(parent/name,weights_only=True)
        assert new['config']=={**old['config'],'register_prompt_order':alt.ORDER_VALUE}
        assert new['schema']==alt.v37.SCHEMA
        assert torch.equal(new['optimizer']['state']['moments'],old['optimizer']['state']['moments'])
        assert torch.equal(new['net']['a'],old['net']['a'])
        assert new['stream']==old['stream'] and new['replay']==old['replay']
    assert before=={str(p.relative_to(parent)):digest(p) for p in parent.rglob('*.pt')}
    with pytest.raises(RuntimeError,match='ALTREG_TARGET_ROOT_NOT_EMPTY'):
        alt.create_fork(parent,destination)
    with pytest.raises(RuntimeError,match='ALTREG_UNSAFE_FORK_ROOT'):
        alt.create_fork(parent,parent/'nested')
    with pytest.raises(ValueError,match='ALTREG_INVALID_FORK_CHECKPOINT'):
        alt.create_fork(parent,tmp_path/'other','../private.pt')


def test_wrapper_enforces_cutover_marker_and_augmented_config(tmp_path,monkeypatch):
    parent=tmp_path/'source';output=tmp_path/'cutover'
    fake_parent(parent/'latest.pt',500)
    original_batch=alt.v37.base.previous.FreshPairs.batch
    original_config=alt.v37.config_for
    calls=[]
    def fake_execute(args):
        calls.append(('called',args.resume))
        conf=alt.v37.config_for(None,None,None,None)
        assert conf['register_prompt_order']==alt.ORDER_VALUE
        assert alt.v37.base.previous.FreshPairs.batch is not original_batch
        assert callable(alt.v37.base.previous.FreshPairs.batch)
        return 0
    monkeypatch.setattr(alt.v37,'execute',fake_execute)
    monkeypatch.setattr(alt.v37,'config_for',lambda *a,**k:{'old_config':'untouched'})
    args=alt.parser().parse_args(['--fork-v3-7-root',str(parent),
                                 '--results-root',str(output),'--additional-steps','2'])
    assert alt.execute(args)==0
    assert calls==[('called',True)]
    assert alt.v37.config_for(None,None,None,None)=={'old_config':'untouched'}
    assert alt.v37.base.previous.FreshPairs.batch is original_batch
    # Even if a run later resumes, only the alternating wrapper may resume it.
    marker=json.loads((output/alt.MARKER).read_text())
    assert marker['schema']==alt.CUTOVER_SCHEMA


def test_self_test_no_downloads():
    assert alt.self_test()==0
