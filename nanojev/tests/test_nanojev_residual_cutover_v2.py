"""Regression: restore the proven 6-adapter policy and verify real weight movement."""
from __future__ import annotations
import json
from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_binary_consensus_runtime import (
    TRAINING_POLICY, SCHEMA as BINARY_SCHEMA, RESIDUAL_FIELDS,
    configure_residual_only, build_residual_optimizer,
    capture_residual_before, inspect_residual_step,
)
from nanojev_arithmetic_runtime import SCHEMA as ARITH_SCHEMA, DEFAULT_OUTPUT
from nanojev_arithmetic_residual_micro_overfit import select_fixed_32, parse as micro_parse,run as micro_run
from nanojev_arithmetic_task_bank import create
from nanojev_arithmetic_runtime import load_bank
from nanojev_binary_consensus_continuous_train import DEFAULT_OUTPUT as BINARY_OUTPUT


def test_frozen_champion_six_adapters_optimizer_and_actual_movement():
    import torch
    class FakeHead(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.core=torch.nn.Linear(2,2)
            self.tinystories_residual=torch.nn.ModuleDict({
                field: torch.nn.Linear(1536,768,bias=False)
                for field in RESIDUAL_FIELDS})
            for module in self.tinystories_residual.values():
                torch.nn.init.zeros_(module.weight)
    head=FakeHead().to(dtype=torch.bfloat16)
    tiny=torch.nn.Linear(2,2)
    for p in tiny.parameters():
        p.requires_grad_(False)
    policy=configure_residual_only(head)
    assert policy['trainable_parameters']==7077888
    assert len(policy['trainable_names'])==6
    assert policy['frozen_parameters']==6
    assert all(not p.requires_grad for p in head.core.parameters())
    optimizer=build_residual_optimizer(torch=torch,head=head,tiny=tiny,
                                      head_lr=1e-4,weight_decay=.01)
    assert len(optimizer.param_groups)==1
    group=optimizer.param_groups[0]
    assert group['group_name']=='tinystories_residual_taps'
    assert group['lr']==pytest.approx(1e-4)
    assert sum(p.numel() for p in group['params'])==7077888
    core_before=head.core.weight.detach().clone()
    before=capture_residual_before(head)
    loss=sum(module.weight.float().sum() for module in head.tinystories_residual.values())
    loss.backward()
    class Logger:
        records=[]
        def emit(self,name,**details):
            self.records.append((name,details))
    logger=Logger()
    optimizer.step()
    rows=inspect_residual_step(torch=torch,head=head,before=before,logger=logger,step=1)
    assert all(row['changed_elements']>0 for row in rows)
    assert all(row['gradient_max']>0 for row in rows)
    assert torch.equal(head.core.weight,core_before)
    assert logger.records[-1][0]=='residual_optimizer_movement'
    assert logger.records[-1][1]['changed_adapters']==6


def test_optimizer_movement_diagnostic_fails_closed_when_bf16_rounds_away():
    import torch
    class TinyFake(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.tinystories_residual=torch.nn.ModuleDict({
                field:torch.nn.Linear(1,1,bias=False) for field in RESIDUAL_FIELDS})
    head=TinyFake().to(dtype=torch.bfloat16)
    for p in head.parameters():
        p.requires_grad_(True)
    before=capture_residual_before(head)
    class Logger:
        def emit(self,*args,**kwargs):
            pass
    with pytest.raises(RuntimeError,match='NO_WEIGHT_MOVEMENT'):
        inspect_residual_step(torch=torch,head=head,before=before,logger=Logger(),step=1)


def test_micro_run_isolated_and_balanced_32(tmp_path,capsys):
    bank=tmp_path/'bank'
    create(output_dir=bank,train_groups=54,predev_groups=27,dev_groups=27,seed=21)
    loaded=load_bank(bank,split='train')
    rows=select_fixed_32(loaded,42)
    assert len(rows)==32
    assert len(set((group,variant) for group,variant,_ in rows))==32
    assert rows==select_fixed_32(loaded,42)
    from collections import Counter
    gold=Counter(row['gold_index'] for _,_,row in rows)
    assert sorted(gold.values())==[10,10,12]
    args=micro_parse(['--bank-dir',str(bank),'--dry-run'])
    micro_run(args)
    plan=json.loads(capsys.readouterr().out)
    assert plan['training_policy']==TRAINING_POLICY
    assert plan['accuracy_computed'] is False
    assert plan['no_generation'] and plan['examples']==32
    assert BINARY_SCHEMA.endswith('residual-v2')
    assert ARITH_SCHEMA.endswith('residual-v2')
    assert 'residual_v2' in str(DEFAULT_OUTPUT)
    assert 'residual_v2' in str(BINARY_OUTPUT)


def test_no_contamination_of_old_run_checkpoint_contracts():
    from nanojev_binary_consensus_continuous_train import parse as binary_parse, train as binary_train
    from nanojev_arithmetic_continuous_train import parse as arithmetic_parse,train as arithmetic_train
    assert binary_parse([]).head_lr==pytest.approx(1e-4)
    assert arithmetic_parse([]).head_lr==pytest.approx(1e-4)
    assert not hasattr(binary_parse([]),'clef_head_lr')
    assert not hasattr(arithmetic_parse([]),'clef_head_lr')
    binary_text=(TOOLS/'nanojev_binary_consensus_continuous_train.py').read_text()
    arith_text=(TOOLS/'nanojev_arithmetic_continuous_train.py').read_text()
    for script in (binary_text,arith_text):
        assert "'training_policy':TRAINING_POLICY" in script
        assert 'capture_residual_before(head)' in script
        assert 'inspect_residual_step(torch=torch' in script
        assert 'torch.nn.functional.cross_entropy' in script
        assert 'optimizer.step()' in script
        assert 'head-lr' in script
        assert 'clef-head-lr' not in script
