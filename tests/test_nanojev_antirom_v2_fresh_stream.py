"""AntiROM v2 CPU tests: unique streams, gold authority, resumed optimizer, six-way orchestration."""
from __future__ import annotations
from collections import Counter
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))

from nanojev_arithmetic_task_bank import create as create_old, LABELS
from nanojev_arithmetic_runtime import load_bank
from nanojev_antirom_compression import LEVELS,render
import nanojev_antirom_v2_stream_bank as fresh
import nanojev_antirom_v2_fresh_train as trainer
import nanojev_antirom_v2_independent_probe as probe
import nanojev_antirom_v2_sweep as sweep


@pytest.fixture
def legacy(tmp_path):
    root=tmp_path/'legacy'
    create_old(output_dir=root,train_groups=54,predev_groups=27,dev_groups=27,seed=144)
    return root


def made(legacy,tmp_path,n=12,ood=6,seed=20261012):
    root=tmp_path/'fresh'
    fresh.create(output=root,seed=seed,count=n,ood_count=ood,source_bank=legacy)
    return root


def test_12000_distinct_and_balanced_no_overlap_prior_bank(legacy,tmp_path):
    bank=made(legacy,tmp_path,n=12000,ood=600)
    x=fresh.load(bank_dir=bank,legacy_bank_dir=legacy,split='train')
    o=fresh.load(bank_dir=bank,legacy_bank_dir=legacy,split='ood')
    assert len(x['rows'])==12000 and len(o['rows'])==600
    assert Counter(r['label'] for r in x['rows'])=={'LESS':4000,'EQUAL':4000,'MORE':4000}
    assert Counter(r['label'] for r in o['rows'])=={'LESS':200,'EQUAL':200,'MORE':200}
    pairs={fresh.canonical_pair(r['a'],r['b']) for r in x['rows']+o['rows']}
    assert len(pairs)==12600
    all_prior=set()
    for s in ('train','predev','dev'):
        for _,g in load_bank(legacy,split=s)['groups']:
            t=g['target'];all_prior.add(fresh.canonical_pair(t['a'],t['b']))
    assert not (pairs & all_prior)
    assert all(len(set((r['a'],r['b'],r['threshold'])))>=2 for r in x['rows'])
    assert all(r['split']=='train' for r in x['rows'])
    assert all(r['split']=='ood' and r['threshold']>=1500 for r in o['rows'])


def test_six_renderings_only_change_prompt(legacy,tmp_path):
    bank=made(legacy,tmp_path)
    rows=fresh.load(bank_dir=bank,legacy_bank_dir=legacy)['rows']
    for r in rows:
        expected=None
        for level in LEVELS:
            sample=fresh.build_sample(r,level)
            assert sample['gold_index']==r['gold_index']
            assert [c['candidate_id'] for c in sample['candidates']]==list(LABELS)
            assert [c['paths'][0]['answer'] for c in sample['candidates']]==[' LESS',' EQUAL',' MORE']
            assert sample['candidates'][0]['paths'][0]['prompt']==render(level,r['a'],r['b'],r['threshold'])
            now=(sample['question_id'],sample['gold_index'],tuple(c['paths'][0]['answer'] for c in sample['candidates']))
            if expected is None:expected=now
            else:assert now==expected


def test_corruption_and_resealed_forgery_detected(legacy,tmp_path):
    root=made(legacy,tmp_path)
    path=root/'train.jsonl'
    rows=[json.loads(x) for x in path.read_text().splitlines()]
    rows[1]['a']=rows[0]['a'];rows[1]['b']=rows[0]['b']
    path.write_text(''.join(fresh.canonical_row(x) for x in rows))
    with pytest.raises(RuntimeError,match='ANTIROM_V2_BANK_HASH_MISMATCH'):
        fresh.load(bank_dir=root,legacy_bank_dir=legacy)
    manifest=json.loads((root/'manifest.json').read_text())
    manifest['file_sha256']['train']=fresh.sha_file(path)
    (root/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='ANTIROM_V2_REPEATED_OR_LEAKING_PROBLEM'):
        fresh.load(bank_dir=root,legacy_bank_dir=legacy)


def test_changed_but_valid_bank_rejected_by_generator_replay(legacy,tmp_path):
    root=made(legacy,tmp_path)
    p=root/'train.jsonl';rows=[json.loads(x) for x in p.read_text().splitlines()]
    rows[0],rows[1]=rows[1],rows[0]
    p.write_text(''.join(fresh.canonical_row(x) for x in rows))
    m=json.loads((root/'manifest.json').read_text());m['file_sha256']['train']=fresh.sha_file(p)
    (root/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(RuntimeError,match='ANTIROM_V2_GENERATOR_REPLAY_DRIFT'):
        fresh.load(bank_dir=root,legacy_bank_dir=legacy)


def test_duplicate_source_operand_pair_and_gold_strata(legacy,tmp_path):
    prior={fresh.canonical_pair(g['target']['a'],g['target']['b'])
           for s in ('train','predev','dev') for _,g in load_bank(legacy,split=s)['groups']}
    cases=fresh.stream_plan(seed=22,count=120,ood_count=12,reserved=prior)
    assert fresh.check_rows(cases,prior,120,12)
    fake=copy.deepcopy(cases)
    fake[0]['label']='LESS' if fake[0]['label']!='LESS' else 'MORE'
    with pytest.raises(RuntimeError,match='ANTIROM_V2_GOLD_OR_METADATA_MISMATCH'):
        fresh.check_rows(fake,prior,120,12)
    fake=copy.deepcopy(cases);pos=next(i for i,r in enumerate(fake) if r['label']!='EQUAL');fake[pos]['distance_band']='far' if fake[pos]['distance_band']!='far' else 'near'
    with pytest.raises(RuntimeError,match='ANTIROM_V2_DISTANCE'):
        fresh.check_rows(fake,prior,120,12)


def test_plan_and_budget_mismatch_no_gpu(legacy,tmp_path,capsys):
    bank=made(legacy,tmp_path)
    out=tmp_path/'runs'
    a=trainer.parse(['--legacy-bank-dir',str(legacy),'--bank-dir',str(bank),
         '--output-root',str(out),'--level','C3','--max-steps','3',
         '--grad-accumulation','4','--dry-run'])
    trainer.train(a)
    o=json.loads(capsys.readouterr().out)
    assert o['train_count']==12 and o['distinct_operand_pairs']==12
    assert o['sample_prompt'].startswith('ADD.CMP3')
    assert not out.exists()
    a.max_steps=4
    with pytest.raises(RuntimeError,match='ANTIROM_V2_NO_REPEAT_BUDGET_MISMATCH'):
        trainer.train(a)
    pr=probe.parse(['--bank-dir',str(bank),'--legacy-bank-dir',str(legacy),
          '--output-root',str(out),'--level','C3','--step','0',
          '--split','dev','--results-file',str(tmp_path/'result.json'),'--dry-run'])
    probe.evaluate(pr)
    o=json.loads(capsys.readouterr().out)
    assert o['examples']==54 and o['checkpoint']=='pinned-cycle-136-champion'
    assert not out.exists()


def test_cpu_mocked_training_visits_every_question_exactly_once(legacy,tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    root=made(legacy,tmp_path)
    visited=[]
    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__();self.linear=torch.nn.Linear(3,3)
        def forward(self,rows):
            return self.linear(rows['values']).view(-1)
    class Champion:
        TRAINABLE_LABEL='tiny'
        def __init__(self):
            self.base=SimpleNamespace(deserialize_question=lambda data,obj:
                   SimpleNamespace(gold_index=data['gold_index'],sample=data))
        def joint_parameters(self,h,lm):return list(h.parameters())
    champ=Champion()
    monkeypatch.setattr(trainer,'deps',lambda:(champ,None,object()))
    def fake_load_model(*,torch,args,source,logger,checkpoint=None):
        head=Head();opt=torch.optim.AdamW(head.parameters(),lr=1e-3)
        if checkpoint is not None:
            from safetensors.torch import load_file
            head.load_state_dict(load_file(str(checkpoint/'head.safetensors')))
            opt.load_state_dict(torch.load(checkpoint/'optimizer.pt',weights_only=False))
        return head,{'tiny':SimpleNamespace(lm=SimpleNamespace(eval=lambda:None))},opt,None
    monkeypatch.setattr(trainer,'load_model',fake_load_model)
    def evidence(*,torch,champion,bundles,question,args):
        p=question.sample['candidates'][0]['paths'][0]['prompt']
        data=[int(x) for x in re.findall(r'\d+',p)]
        assert len(data)>=3
        visited.append(question.sample['question_id'])
        return {'values':torch.tensor(data[-3:],dtype=torch.float32)}
    monkeypatch.setattr(trainer,'frozen_evidence',evidence)
    monkeypatch.setattr(trainer,'evidence_on_cuda',lambda champ,ev:ev)
    monkeypatch.setattr(trainer,'verify_prompt_visibility',lambda **kwargs:{'fake':{'prompt_tokens':3}})
    monkeypatch.setattr(trainer,'capture_residual_before',lambda h:{})
    monkeypatch.setattr(trainer,'inspect_residual_step',lambda **kwargs:None)
    out=tmp_path/'runs'
    a=trainer.parse(['--legacy-bank-dir',str(legacy),'--bank-dir',str(root),
         '--output-root',str(out),'--level','C5','--max-steps','3',
         '--grad-accumulation','4','--checkpoint-steps','2','--report-steps','1'])
    trainer.train(a)
    assert len(visited)==12 and len(set(visited))==12
    state=json.loads((out/'C5/state.json').read_text())
    assert state['cursor']['examples_seen']==12 and state['cursor']['global_step']==3
    assert (out/'C5/checkpoints/step-000000002/head.safetensors').is_file()
    assert (out/'C5/checkpoints/step-000000003/head.safetensors').is_file()
    # Complete resumes never replay a single question.
    visited.clear();a.resume=True
    trainer.train(a)
    assert visited==[]


def test_sweep_plan_and_level_dryrun(legacy,tmp_path,capsys):
    output=tmp_path/'sweep'
    a=sweep.parse(['--legacy-bank-dir',str(legacy),'--bank-dir',str(tmp_path/'fresh'),
         '--output-root',str(output),'--dry-run'])
    sweep.execute(a)
    data=json.loads(capsys.readouterr().out)
    assert data['train_unique_cases_per_level']==12000
    assert data['dev_probe_steps']==[0,1000,2000,3000]
    assert len(data['levels'])==6 and not output.exists()


def test_cross_level_checkpoints_rejected(legacy,tmp_path):
    root=made(legacy,tmp_path)
    bank=fresh.load(bank_dir=root,legacy_bank_dir=legacy)
    run=tmp_path/'out/C3'
    run.mkdir(parents=True)
    cfg={'bank_sha256':bank['bank_sha256'],'legacy_bank_sha256':bank['legacy_bank_sha256'],
         'level':'C3','representation':'antirom-arithmetic-compression-v1:C3',
         'renderer_sha256':'wrong','seed':20261012,'head_lr':1e-4,'weight_decay':.01}
    (run/'experiment.json').write_text(json.dumps({'schema':trainer.SCHEMA,
              'mode':'fresh-unique-training-no-reexposure',
              'bankManifestSha256':bank['bank_sha256'],'config':cfg}))
    args=probe.parse(['--bank-dir',str(root),'--legacy-bank-dir',str(legacy),
         '--output-root',str(tmp_path/'out'),'--level','C3','--step','2',
         '--results-file',str(tmp_path/'irrelevant.json')])
    with pytest.raises(RuntimeError,match='ANTIROM_V2_PROBE_TRAINING_IDENTITY_MISMATCH'):
        probe.checkpoint_identity(args,bank)


def test_sweep_train_only_orchestration_no_actual_gpu(legacy,tmp_path,monkeypatch):
    root=made(legacy,tmp_path)
    out=tmp_path/'out'
    jobs=[]
    def fake_stage(cmd,kind,level):
        assert kind=='train' and '--resume' not in cmd
        jobs.append(level)
        run=out/level
        run.mkdir(parents=True)
        cfg={'schema':trainer.SCHEMA,'level':level,'representation':sweep.representation(level),
             'renderer_sha256':sweep.renderer_fingerprint(level),'bank_sha256':fresh.load(
                     bank_dir=root,legacy_bank_dir=legacy)['bank_sha256'],
             'training_policy':'cycle136-frozen-core-six-tinystories-residual-adapters-adamw-v2',
             'head_lr':1e-4,'weight_decay':.01,'max_steps':3,'grad_accumulation':4,'seed':20261012}
        bsha=cfg['bank_sha256']
        (run/'experiment.json').write_text(json.dumps({'schema':trainer.SCHEMA,
             'mode':'fresh-unique-training-no-reexposure','config':cfg,'bankManifestSha256':bsha}))
        cursor={'global_step':3,'examples_seen':12}
        cp=run/'checkpoints/step-000000003';cp.mkdir(parents=True)
        (cp/'head.safetensors').touch()
        (cp/'meta.json').write_text(json.dumps({'config':cfg,'bankManifestSha256':bsha,'cursor':cursor}))
        (run/'state.json').write_text(json.dumps({'config':cfg,'bankManifestSha256':bsha,
                               'cursor':cursor,'checkpoint':str(cp.resolve())}))
        events=[{'event':'antirom_v2_prompt_visibility','level':level,
                 'backbones':{'qwen':{'prompt_tokens':20}}},
                {'event':'antirom_v2_online_training_ce','level':level,
                 'step':3,'seen':12,'rolling_ce':.9}]
        (run/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
    monkeypatch.setattr(sweep,'stage',fake_stage)
    a=sweep.parse(['--legacy-bank-dir',str(legacy),'--bank-dir',str(root),
         '--output-root',str(out),'--max-steps','3',
         '--grad-accumulation','4','--checkpoint-steps','1','--ood-count','6','--train-only'])
    sweep.execute(a)
    assert jobs==list(LEVELS)
    summary=json.loads((out/'summary.json').read_text())
    assert len(summary['levels'])==6
    assert [e['seen'] for e in summary['levels']]==[12]*6
    jobs.clear();a.resume=True;sweep.execute(a)
    assert not jobs


def test_independent_probe_dev_and_ood_cpu_mock(legacy,tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    root=made(legacy,tmp_path)
    touched=[]
    class FakeHead:
        def eval(self):pass
        def __call__(self,rows):
            # Synthetic perfect classifier validates evaluator mapping, not arithmetic intelligence.
            y=rows['gold']
            return torch.tensor([5.0 if i==y else -5.0 for i in range(3)],dtype=torch.float32)
    class FakeChampion:
        def __init__(self):
            self.base=SimpleNamespace(deserialize_question=lambda sample,obj:
                 SimpleNamespace(gold_index=sample['gold_index'],sample=sample))
    fake=FakeChampion()
    monkeypatch.setattr(probe,'deps',lambda:(fake,None,object()))
    monkeypatch.setattr(probe,'load_model',lambda **kwargs:(FakeHead(),{'fake':None},None,None))
    def ev(*,torch,champion,bundles,question,args):
        touched.append(question.sample['question_id'])
        return {'gold':question.gold_index}
    monkeypatch.setattr(probe,'frozen_evidence',ev)
    monkeypatch.setattr(probe,'evidence_on_cuda',lambda ch,rows:rows)
    monkeypatch.setattr(probe,'verify_prompt_visibility',lambda **kwargs:{'mock':{}})
    for split,n in [('dev',54),('ood',6)]:
        output=tmp_path/(split+'.json')
        args=probe.parse(['--bank-dir',str(root),'--legacy-bank-dir',str(legacy),
           '--output-root',str(tmp_path/'runs'),'--level','C5','--step','0',
           '--split',split,'--results-file',str(output)])
        touched.clear()
        probe.evaluate(args)
        data=json.loads(output.read_text())
        assert data['overall']['accuracy']==1.0
        assert data['overall']['examples']==n
        assert len(touched)==n
        assert all(data['predictions'][id]['correct'] for id in data['predictions'])
        assert data['weights_modified'] is False
        if split=='dev':assert set(touched).isdisjoint(r['id'] for r in fresh.load(
             bank_dir=root,legacy_bank_dir=legacy,split='train')['rows'])


def test_partial_checkpoint_resume_restarts_only_uncommitted_batch(legacy,tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    root=made(legacy,tmp_path,n=12)
    visited=[];abort=[True]
    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__();self.linear=torch.nn.Linear(3,3)
        def forward(self,rows):return self.linear(rows['features']).reshape(-1)
    champ=SimpleNamespace(TRAINABLE_LABEL='tiny',
        joint_parameters=lambda h,lm:list(h.parameters()),
        base=SimpleNamespace(deserialize_question=lambda data,obj:SimpleNamespace(
            gold_index=data['gold_index'],sample=data)))
    monkeypatch.setattr(trainer,'deps',lambda:(champ,None,object()))
    def model(*,torch,args,source,logger,checkpoint=None):
        h=Head();optim=torch.optim.AdamW(h.parameters(),lr=.0001)
        if checkpoint is not None:
            from safetensors.torch import load_file
            h.load_state_dict(load_file(str(checkpoint/'head.safetensors')))
            optim.load_state_dict(torch.load(checkpoint/'optimizer.pt',weights_only=False))
        return h,{'tiny':SimpleNamespace(lm=SimpleNamespace(eval=lambda:None))},optim,None
    monkeypatch.setattr(trainer,'load_model',model)
    monkeypatch.setattr(trainer,'verify_prompt_visibility',lambda **kwargs:{'mock':{}})
    monkeypatch.setattr(trainer,'evidence_on_cuda',lambda c,x:x)
    monkeypatch.setattr(trainer,'capture_residual_before',lambda h:{})
    monkeypatch.setattr(trainer,'inspect_residual_step',lambda **kwargs:None)
    def evidence(*,torch,champion,bundles,question,args):
        visited.append(question.sample['question_id'])
        if abort[0] and len(visited)==9:
            raise RuntimeError('simulated power interruption')
        text=question.sample['candidates'][0]['paths'][0]['prompt']
        a,b,t=map(int,re.findall(r'\d+',text)[-3:])
        return {'features':torch.tensor([a,b,t],dtype=torch.float32)}
    monkeypatch.setattr(trainer,'frozen_evidence',evidence)
    output=tmp_path/'output'
    a=trainer.parse(['--bank-dir',str(root),'--legacy-bank-dir',str(legacy),
       '--output-root',str(output),'--level','C5','--max-steps','3',
       '--grad-accumulation','4','--checkpoint-steps','2'])
    with pytest.raises(RuntimeError,match='simulated power interruption'):
        trainer.train(a)
    state=json.loads((output/'C5/state.json').read_text())
    assert state['cursor']['global_step']==2
    first_eight=list(visited[:8])
    abort[0]=False;a.resume=True
    trainer.train(a)
    after=visited[9:]
    assert len(after)==4 and len(set(after))==4
    assert not set(after)&set(first_eight)
    assert json.loads((output/'C5/state.json').read_text())['cursor']['examples_seen']==12


def test_full_sweep_csv_includes_both_dev_and_ood_ce_without_retraining(tmp_path):
    """Regression for the crash after six completed levels and completed probes."""
    path=tmp_path/'summary.csv'
    path.write_text('partial header\n',encoding='utf-8')
    rows=[]
    for level in LEVELS:
        rows.append({
            'level':level,'steps':3000,'seen':12000,'training_ce_final':1.099,
            'dev':{'baseline':{'accuracy':.31,'mean_cross_entropy':1.54},
                   'final':{'accuracy':.35,'mean_cross_entropy':1.10}},
            'ood':{'baseline':{'accuracy':.32,'mean_cross_entropy':1.48},
                   'final':{'accuracy':.34,'mean_cross_entropy':1.11}},
        })
    sweep.write_summary_csv(path,rows)
    with path.open(encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f)
        saved=list(reader)
        assert len(saved)==6
        assert reader.fieldnames==sweep.SUMMARY_CSV_FIELDS
        assert [x['level'] for x in saved]==list(LEVELS)
        assert all(x['dev_ce_before']=='1.54' and x['dev_ce_after']=='1.1' for x in saved)
        assert all(x['ood_ce_before']=='1.48' and x['ood_ce_after']=='1.11' for x in saved)
        assert all(x['seen']=='12000' for x in saved)
    assert not path.with_name('summary.csv.tmp').exists()
