"""Focused offline verification of the arithmetic-controlled CLEF cutover."""
from __future__ import annotations
from collections import Counter,deque
import json
from pathlib import Path
import sqlite3
import sys

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_arithmetic_task_bank import (create,main as bank_main,parse as bank_parse,
    make_triples,render_prompt,label_for,LABELS,TARGET_BANDS,DISTANCE_BANDS)
from nanojev_arithmetic_runtime import (load_bank,shard_examples,validate_training_controls,
    pass_shard_order,SCHEMA,DEFAULT_BANK,DEFAULT_OUTPUT)
from nanojev_arithmetic_continuous_train import (parse as train_parse,train,
    checkpoint,next_cursor,make_shards,Logger)
from nanojev_arithmetic_probe import (parse as probe_parse,run as probe_run,compare_predictions)


def sample_bank(tmp_path,n=54):
    output=tmp_path/'bank'
    create(output_dir=output,train_groups=n,predev_groups=27,dev_groups=27,seed=42)
    return output


def test_generator_arithmetic_labels_and_counterfactuals(tmp_path):
    bank=sample_bank(tmp_path)
    train=load_bank(bank,split='train')
    assert train['count']==54
    assert Counter(row['label'] for _,row in train['groups'])=={'LESS':18,'EQUAL':18,'MORE':18}
    families={}
    for group_id,row in train['groups']:
        t=row['target']
        assert t['sum']==t['a']+t['b']
        assert t['signedDistance']==t['a']+t['b']-t['threshold']
        assert row['label']==label_for(t['a'],t['b'],t['threshold'])
        assert set(row['variants'])=={'forward','reverse'}
        for variant,q in row['variants'].items():
            assert [c['candidate_id'] for c in q['candidates']]==['LESS','EQUAL','MORE']
            a,b=t['a'],t['b']
            prompt=render_prompt(b,a,t['threshold']) if variant=='reverse' else render_prompt(a,b,t['threshold'])
            assert all(c['paths'][0]['prompt']==prompt for c in q['candidates'])
            assert [c['paths'][0]['answer'] for c in q['candidates']]==[' LESS',' EQUAL',' MORE']
        families.setdefault(row['anchor'],[]).append(t)
    assert len(families)==18
    for family in families.values():
        assert len(family)==3
        assert {x['label'] for x in family}==set(LABELS)
        assert len({x['threshold'] for x in family})==1
        assert len({x['a'] for x in family})==1
        sorted_fam=sorted(family,key=lambda t:t['signedDistance'])
        assert sorted_fam[0]['signedDistance']<0==sorted_fam[1]['signedDistance']<sorted_fam[2]['signedDistance']
        assert -sorted_fam[0]['signedDistance']==sorted_fam[2]['signedDistance']


def test_no_operand_pair_leakage_and_target_variation(tmp_path):
    bank=sample_bank(tmp_path,n=270)
    anchors={};operand_pairs={}
    targets=set();distances=set();classes=set();sizes=set();distance_bands=set()
    for split in ('train','predev','dev'):
        data=load_bank(bank,split=split)
        for _,r in data['groups']:
            t=r['target']
            targets.add(t['threshold']);distances.add(t['signedDistance']);classes.add(r['label'])
            sizes.add(t['targetBand']);distance_bands.add(t['distanceBand'])
            if r['anchor'] in anchors:
                assert anchors[r['anchor']]==split
            anchors[r['anchor']]=split
            pair=tuple(sorted((t['a'],t['b'])))
            assert pair not in operand_pairs
            operand_pairs[pair]=split
    assert len(targets)>30
    assert len(distances)>12
    assert classes==set(LABELS)
    assert sizes==set(TARGET_BANDS)
    assert distance_bands==set(DISTANCE_BANDS)
    assert all(operand_pairs.values())


def test_bank_is_immutable_hash_guard(tmp_path):
    bank=sample_bank(tmp_path)
    db=bank/'training-tasks.sqlite'
    with sqlite3.connect(db) as con:
        con.execute('UPDATE task_groups SET target_json=? WHERE group_id=(SELECT group_id FROM task_groups LIMIT 1)',('{}',))
    with pytest.raises(RuntimeError,match='DATABASE_HASH_MISMATCH'):
        load_bank(bank,split='train')


def test_missing_reverse_fail_closed_even_if_resealed(tmp_path):
    from nanojev_arithmetic_task_bank import file_sha256
    bank=sample_bank(tmp_path)
    db=bank/'training-tasks.sqlite'
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM presentations WHERE presentation_id IN (SELECT p.presentation_id FROM presentations p JOIN task_groups g ON p.group_id=g.group_id WHERE p.variant='reverse' AND g.split='predev' LIMIT 1)")
    manifest_path=bank/'manifest.json'
    manifest=json.loads(manifest_path.read_text())
    manifest['databaseSha256']=file_sha256(db)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='MISSING_OPERAND_ORIENTATION'):
        load_bank(bank,split='predev')


def test_controls_and_balanced_shard(tmp_path):
    bank=sample_bank(tmp_path)
    data=load_bank(bank,split='train')
    order=pass_shard_order(4,99,0)
    assert order==pass_shard_order(4,99,0)
    assert set(order)==set(range(4))
    shard=shard_examples(data['groups'],99,0,0)
    assert len(shard)==108
    assert Counter(next(row for k,row in data['groups'] if k==gid)['label'] for gid,_,_ in shard)=={
        'LESS':36,'EQUAL':36,'MORE':36}
    args=train_parse([])
    validate_training_controls(args)
    assert args.target_loss==.2
    assert args.head_lr==.0001
    args.target_loss=1.5
    with pytest.raises(ValueError,match='THREE_CLASS_RANGE'):
        validate_training_controls(args)


def test_train_and_probe_dry_runs_do_not_compute_accuracy(tmp_path,capsys):
    bank=sample_bank(tmp_path)
    args=train_parse(['--bank-dir',str(bank),'--output-dir',str(tmp_path/'train'),'--dry-run'])
    train(args)
    plan=json.loads(capsys.readouterr().out)
    assert plan['ok'] and plan['examples_per_pass']==108 and plan['train_groups']==54
    assert plan['config']['objective']=='three-class-plain-cross-entropy-no-label-smoothing-no-auxiliary'
    assert plan['no_generation'] and plan['accuracy_computed'] is False
    probe_run(probe_parse(['--bank-dir',str(bank),'--champion-only','--dry-run']))
    plan=json.loads(capsys.readouterr().out)
    assert plan['groups']==27 and plan['presentations']==54 and plan['no_training']
    assert plan['accuracy_computed'] is False
    with pytest.raises(SystemExit):
        probe_parse(['--split','train'])


def test_exact_original_cutover_configuration():
    from nanojev_binary_consensus_runtime import (PARENT_REPO,PARENT_RELEASE,PARENT_REVISION,load_model as established_load_model)
    from nanojev_arithmetic_runtime import load_model as arithmetic_load_model
    assert arithmetic_load_model is established_load_model
    assert PARENT_REPO=='johnrraymond/NanoJev-CLEF'
    assert PARENT_RELEASE=='clef-cycle-000136-reuse-002'
    assert PARENT_REVISION=='222a8e361121a5aa35e0ecd0e073d5f645ef54f9'
    from nanojev_arithmetic_runtime import deps
    assert callable(deps)


def test_same_optimizer_step_as_binary_trainer():
    arithmetic=Path(__file__).resolve().parents[1]/'tools'/'nanojev_arithmetic_continuous_train.py'
    binary=Path(__file__).resolve().parents[1]/'tools'/'nanojev_binary_consensus_continuous_train.py'
    if not binary.exists():
        binary=Path('/mnt/data/continuous_binary_patch_work/main_computer_test/tools/nanojev_binary_consensus_continuous_train.py')
    if not binary.exists():
        pytest.skip('Original continuous trainer not present in test checkout')
    a=arithmetic.read_text();b=binary.read_text()
    core='''optimizer.zero_grad(set_to_none=True)
                losses=[]
                for group_id,variant,serialized in batch:
                    question=champion.base.deserialize_question(serialized,objective)
                    evidence=evidence_on_cuda(champion,cache[(group_id,variant)])
                    logits=head(evidence)
                    loss=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                        torch.tensor([question.gold_index],dtype=torch.long,device=logits.device))'''
    assert core in a and core in b
    assert 'accuracy' not in a[a.index('def train(args):'):a.index('def parse(argv=None):')].replace("'accuracy_computed':False",'').replace('accuracy_computed=False','')


def test_real_three_class_loss_gradient():
    import torch
    model=torch.nn.Linear(3,3)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.1)
    x=torch.tensor([[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
    target=torch.tensor([0,1,2])
    before=torch.nn.functional.cross_entropy(model(x),target).item()
    for _ in range(100):
        optimizer.zero_grad()
        loss=torch.nn.functional.cross_entropy(model(x),target)
        loss.backward()
        optimizer.step()
    after=torch.nn.functional.cross_entropy(model(x),target).item()
    assert after<before and after<.20


def test_probe_paired_comparison_logic():
    b={'x':{'gold':'MORE','correct':False},'y':{'gold':'EQUAL','correct':True},
       'z':{'gold':'LESS','correct':True}}
    c={'x':{'gold':'MORE','correct':True},'y':{'gold':'EQUAL','correct':False},
       'z':{'gold':'LESS','correct':True}}
    result=compare_predictions(c,b)
    assert result['improved_cases']==1 and result['regressed_cases']==1
    assert result['net_gain_cases']==0
    with pytest.raises(RuntimeError,match='GOLD_DRIFT'):
        compare_predictions({'x':{'gold':'EQUAL','correct':True}},
                            {'x':{'gold':'LESS','correct':False}})


def test_three_class_checkpoint_optimizer_rng_roundtrip(tmp_path):
    import torch
    from safetensors.torch import load_file
    model=torch.nn.Linear(3,3)
    opt=torch.optim.AdamW(model.parameters(),lr=.01)
    x=torch.tensor([[1.,2.,3.]])
    for _ in range(2):
        opt.zero_grad()
        torch.nn.functional.cross_entropy(model(x),torch.tensor([2])).backward()
        opt.step()
    output=tmp_path/'run';(output/'checkpoints').mkdir(parents=True)
    cursor={'pass_index':0,'shard_position':0,'offset':4,'global_step':2,'examples_seen':8}
    rolling=deque([.75,.65],maxlen=4)
    checkpoint(torch=torch,output=output,head=model,optimizer=opt,cursor=cursor,
              rolling=rolling,bank_sha='trusted-bank',config={'target_loss':.2},logger=Logger(output))
    state=json.loads((output/'state.json').read_text())
    assert state['schema']==SCHEMA and state['cursor']==cursor
    restored=load_file(str(Path(state['checkpoint'])/'head.safetensors'))
    assert all(torch.equal(restored[k],v) for k,v in model.state_dict().items())
    assert torch.load(Path(state['checkpoint'])/'optimizer.pt',weights_only=False)['state']


def test_generator_rejects_invalid_counts(tmp_path):
    with pytest.raises(ValueError,match='MULTIPLE_OF_27'):
        create(output_dir=tmp_path/'bad',train_groups=50,predev_groups=27,dev_groups=27,seed=1)
    plan=bank_main(bank_parse(['--dry-run']))
    assert plan['counts']['train']==972 and plan['counts']['predev']==270
