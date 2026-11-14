"""The raw-ROM trainer must share its real model and optimizer with the fixed-32 control."""
from __future__ import annotations
import json
from pathlib import Path
import sys

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_arithmetic_task_bank import create as create_arithmetic, SOURCE
from nanojev_binary_raw_rom_arithmetic_control import (
    load_control_plan, plan_identity, train_step, fixed_loss,
)
from nanojev_binary_raw_rom_continuous_train import parse, train, DEFAULT_CONTROL_OUTPUT, DEFAULT_OUTPUT
from nanojev_binary_raw_rom_bank import create as create_raw_rom
from test_nanojev_binary_raw_rom_cutover import fixture_source


def arithmetic_fixture(tmp_path):
    directory=tmp_path/'arithmetic'
    create_arithmetic(output_dir=directory,train_groups=27,predev_groups=27,dev_groups=27,seed=97531)
    return directory


def test_exact_previous_fixed32_selection_and_gold(tmp_path):
    directory=arithmetic_fixture(tmp_path)
    source={'model':SOURCE['sourceModel'],'revision':SOURCE['sourceRevision']}
    plan=load_control_plan(bank_dir=directory,seed=20261010,expected_source=source)
    assert len(plan['examples'])==32
    assert plan['label_counts']=={'LESS':12,'EQUAL':10,'MORE':10}
    assert {v for _,v,_ in plan['examples']} == {'forward','reverse'}
    assert all(s['task']=='arithmetic_comparison' for _,_,s in plan['examples'])
    assert all(len(s['candidates'])==3 for _,_,s in plan['examples'])
    second=load_control_plan(bank_dir=directory,seed=20261010,expected_source=source)
    assert plan['selection_sha256']==second['selection_sha256']
    from nanojev_arithmetic_residual_micro_overfit import select_fixed_32
    assert plan['examples']==select_fixed_32(plan['bank'],20261010)
    with pytest.raises(RuntimeError,match='SOURCE_MISMATCH'):
        load_control_plan(bank_dir=directory,seed=20261010,
                          expected_source={'model':'wrong','revision':'wrong'})


def test_raw_rom_control_dry_run_and_fresh_lineage(tmp_path,capsys,monkeypatch):
    arith=arithmetic_fixture(tmp_path)
    original=fixture_source(tmp_path)
    raw=tmp_path/'raw'
    create_raw_rom(source_bank=original,output_dir=raw)
    # This fixture's Qwen revision is intentionally different from the sealed
    # arithmetic bank; production must fail closed on provenance drift.
    args=parse(['--bank-dir',str(raw),'--arithmetic-control-bank-dir',str(arith),
                '--arithmetic-control','--dry-run'])
    assert args.output_dir == DEFAULT_CONTROL_OUTPUT != DEFAULT_OUTPUT
    with pytest.raises(RuntimeError,match='SOURCE_MISMATCH'):
        train(args)
    from nanojev_binary_raw_rom_bank import load_raw_bank
    real_load=load_raw_bank
    def with_valid_source(*a,**kw):
        result=real_load(*a,**kw)
        result['source']={'model':SOURCE['sourceModel'],'revision':SOURCE['sourceRevision']}
        return result
    monkeypatch.setattr('nanojev_binary_raw_rom_continuous_train.load_bank',with_valid_source)
    train(args)
    result=json.loads(capsys.readouterr().out)
    assert result['ok'] and result['arithmetic_control']
    assert result['no_generation'] and not result['accuracy_computed']
    assert result['config']['arithmetic_control']['control_examples']==32
    assert result['config']['arithmetic_control']['control_every_rom_steps']==4
    assert result['config']['training_policy'].endswith('adamw-v2')
    assert parse([]).output_dir == DEFAULT_OUTPUT
    with pytest.raises(RuntimeError,match='INVALID_SCHEDULE'):
        train(parse(['--bank-dir',str(raw),'--arithmetic-control','--arithmetic-control-every','0',
                     '--arithmetic-control-bank-dir',str(arith),'--dry-run']))


def test_shared_head_shared_optimizer_three_way_gradient_and_fixed_loss(monkeypatch):
    torch=pytest.importorskip('torch')
    from types import SimpleNamespace
    import nanojev_binary_raw_rom_arithmetic_control as c
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.tinystories_residual=torch.nn.ModuleDict({n:torch.nn.Linear(3,3,bias=False)
                for n in ('global','memory','option_context','option_predictor','option_question','option_terminal')})
            for m in self.tinystories_residual.values():
                torch.nn.init.zeros_(m.weight)
        def forward(self,rows):
            return sum(layer(rows['x']) for layer in self.tinystories_residual.values())
    class Log:
        def __init__(self):self.events=[]
        def emit(self,e,**kwargs):self.events.append((e,kwargs))
    head=Model()
    optimizer=torch.optim.AdamW(head.parameters(),lr=0.02)
    prepared=[('id'+str(i),'forward',i%3,{'x':torch.nn.functional.one_hot(
        torch.tensor(i%3),3).float()}) for i in range(32)]
    monkeypatch.setattr(c,'evidence_on_cuda',lambda champion,rows: rows)
    args=SimpleNamespace(grad_accumulation=4,grad_clip=1.0,arithmetic_control_report_every=25)
    logger=Log()
    start=fixed_loss(torch=torch,champion=None,head=head,prepared=prepared)
    for step in range(80):
        train_step(torch=torch,champion=None,head=head,optimizer=optimizer,
                   params=list(head.parameters()),prepared=prepared,args=args,
                   control_steps=step,logger=logger)
    end=fixed_loss(torch=torch,champion=None,head=head,prepared=prepared)
    assert start>1.09 and end<0.20, (start,end)
    assert {e for e,_ in logger.events}=={'residual_optimizer_movement'}
    assert all(x['changed_adapters']==6 for e,x in logger.events)
    # All six updates used the same optimizer, with no alternate model.
    assert len(optimizer.param_groups)==1
