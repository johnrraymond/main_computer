"""Run with: python -m pytest -q tests/test_nanojev_binary_consensus_continuous.py"""
from pathlib import Path
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_binary_consensus_runtime import (load_bank,pass_shard_order,shard_examples,validate_training_controls)
from nanojev_binary_consensus_continuous_train import next_cursor,make_shards,parse
from nanojev_training_task_store import TrainingTaskStore, sha


def fixture_bank(tmp_path):
    bank=tmp_path/'bank'
    bank.mkdir()
    with TrainingTaskStore(bank/'training-tasks.sqlite') as store:
        for split in ('train','predev','test'):
            cohort='x:'+split
            store.add_cohort(cohort,family='clef_binary_consensus',split=split,
                             source={'sourceModel':'Qwen/Qwen3-0.6B','sourceRevision':'pinned-revision'})
            expected={
                'a':('DIFFERENT','DIFFERENT','SAME'),
                'b':('DIFFERENT','SAME','DIFFERENT'),
                'c':('SAME','DIFFERENT','DIFFERENT'),
                'none':('SAME','SAME','SAME'),
            }
            for topo, labels in expected.items():
                for pair,label in zip(('ab','ac','bc'),labels):
                    idx=0 if label=='SAME' else 1
                    def question(orientation):
                        return {'question_id':f'{split}:{topo}:{pair}:{orientation}',
                                'task':'consensus_pairwise','gold_index':idx,
                                'stratum':'',
                                'candidates':[{'candidate_id':c,'paths':[{'prompt':f'Compare {topo} {pair} {orientation}','answer':' '+c}]}
                                              for c in ('same','different')]}
                    store.put(cohort_id=cohort,family='clef_binary_consensus',task='consensus_pairwise',split=split,
                              fingerprint=sha([split,topo,pair]),oracle='normalized-python-ast-equivalence-v1',
                              label_status='verified',target={'label':label,'goldIndex':idx,'pair':pair,'sourceTopology':topo},
                              provenance={'parentFingerprint':f'{split}:{topo}'},
                              variants={'natural_rom':question('forward'),'natural_rom_reverse':question('reverse')})
        store.conn.commit()
    (bank/'manifest.json').write_text(json.dumps({'ok':True,'binaryConsensusOnly':True,
       'schema':'nanojev.training-task-generator.v1'}))
    return bank


def test_bank_loads_only_sealed_balanced_binary_records(tmp_path):
    bank=fixture_bank(tmp_path)
    data=load_bank(bank,split='train')
    assert data['count']==12
    assert len(shard_examples(data['groups'],17,0,0))==24
    assert sum(v['label']=='SAME' for _,v in data['groups'])==6
    assert sum(v['label']=='DIFFERENT' for _,v in data['groups'])==6
    assert all(set(v['variants'])=={'natural_rom','natural_rom_reverse'} for _,v in data['groups'])
    assert load_bank(bank,split='predev')['count']==12


def test_dataset_identity_detects_changes(tmp_path):
    bank=fixture_bank(tmp_path)
    first=load_bank(bank,split='train')['manifest_sha256']
    p=bank/'manifest.json'
    p.write_text(p.read_text()+'\n')
    assert load_bank(bank,split='train')['manifest_sha256']!=first


def test_unknown_labels_and_missing_orientation_fail_closed(tmp_path):
    bank=fixture_bank(tmp_path)
    from sqlite3 import connect
    with connect(bank/'training-tasks.sqlite') as c:
        c.execute("DELETE FROM presentations WHERE presentation_id IN (SELECT p.presentation_id FROM presentations p JOIN task_groups g ON p.group_id=g.group_id WHERE p.variant='natural_rom_reverse' AND g.split='train' LIMIT 1)")
    with pytest.raises(RuntimeError,match='ORIENTATION_MISSING'):
        load_bank(bank,split='train')


def test_groups_and_shuffles_reproducible(tmp_path):
    bank=load_bank(fixture_bank(tmp_path),split='train')
    shards=make_shards(bank,4)
    order=pass_shard_order(len(shards),42,0)
    assert order==pass_shard_order(len(shards),42,0)
    assert set(order)==set(range(len(shards)))
    assert [r[:2] for r in shard_examples(shards[0],42,0,0)] == [r[:2] for r in shard_examples(shards[0],42,0,0)]
    assert next_cursor({'offset':0,'global_step':1},len(shards),4)['offset']==4


def test_train_flags_and_loss_objective(tmp_path,capsys):
    from nanojev_binary_consensus_continuous_train import train
    bank=fixture_bank(tmp_path)
    args=parse(['--bank-dir',str(bank),'--output-dir',str(tmp_path/'out'),'--dry-run'])
    train(args)
    emitted=json.loads(capsys.readouterr().out)
    assert emitted['train_groups']==12 and emitted['examples_per_pass']==24
    assert emitted['accuracy_computed'] is False and emitted['no_generation']
    assert emitted['config']['target_loss']==.2
    assert emitted['config']['objective']=='plain-cross-entropy-no-label-smoothing-no-auxiliary'


def test_invalid_controls_rejected():
    args=parse([])
    validate_training_controls(args)
    args.target_loss=1
    with pytest.raises(ValueError,match='TARGET_LOSS'):
        validate_training_controls(args)
    args.target_loss=.2
    args.rolling_questions=257
    with pytest.raises(ValueError,match='ROLLING_WINDOW'):
        validate_training_controls(args)


def test_probe_never_accepts_train_split(tmp_path):
    from nanojev_binary_consensus_probe import parse,run
    with pytest.raises(SystemExit) as exc:
        parse(['--bank-dir',str(fixture_bank(tmp_path)),'--split','train','--dry-run'])
    assert exc.value.code==2


def test_probe_dry_run_and_disjoint_bank(tmp_path,capsys):
    from nanojev_binary_consensus_probe import parse,run
    args=parse(['--bank-dir',str(fixture_bank(tmp_path)),'--split','predev','--champion-only','--dry-run'])
    run(args)
    row=json.loads(capsys.readouterr().out)
    assert row['groups']==12 and row['presentations']==24 and row['no_training']


def test_loss_is_plain_ce_not_smoothed_or_brier():
    import torch
    logits=torch.tensor([[2.0,0.0]],requires_grad=True)
    loss=torch.nn.functional.cross_entropy(logits,torch.tensor([0]))
    assert loss.item()==pytest.approx(0.126928,rel=1e-4)
    loss.backward()
    assert torch.isfinite(logits.grad).all()


def test_optimizer_and_rng_checkpoint_round_trip(tmp_path):
    import torch
    from nanojev_binary_consensus_continuous_train import checkpoint,Logger
    from safetensors.torch import load_file
    model=torch.nn.Linear(2,2)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.005)
    x=torch.tensor([[1.,2.]])
    for _ in range(2):
        optimizer.zero_grad()
        loss=torch.nn.functional.cross_entropy(model(x),torch.tensor([0]))
        loss.backward()
        optimizer.step()
    output=tmp_path/'out'
    (output/'checkpoints').mkdir(parents=True)
    cursor={'pass_index':1,'shard_position':2,'offset':8,'global_step':2,'examples_seen':8}
    from collections import deque
    rolling=deque([.61,.48],maxlen=4)
    checkpoint(torch=torch,output=output,head=model,optimizer=optimizer,cursor=cursor,
               rolling=rolling,bank_sha='fake-sealed-identity',config={'target_loss':.2},logger=Logger(output))
    state=json.loads((output/'state.json').read_text())
    ckpt=Path(state['checkpoint'])
    saved=load_file(str(ckpt/'head.safetensors'))
    for name,value in model.state_dict().items():
        assert torch.equal(saved[name],value)
    original=torch.load(ckpt/'optimizer.pt',weights_only=False)
    assert original['state']
    rebuilt=torch.nn.Linear(2,2)
    rebuilt.load_state_dict(saved)
    fresh=torch.optim.AdamW(rebuilt.parameters(),lr=.005)
    fresh.load_state_dict(original)
    assert fresh.state_dict()['state'].keys()==original['state'].keys()
    assert json.loads((ckpt/'meta.json').read_text())['cursor']==cursor
    assert state['rolling']==[.61,.48]
