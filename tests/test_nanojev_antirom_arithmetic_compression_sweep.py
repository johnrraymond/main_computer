"""No-GPU AntiROM sweep contract tests: unchanged bank, independent runs, identity gates."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_arithmetic_task_bank import create, render_prompt
from nanojev_arithmetic_runtime import load_bank
from nanojev_arithmetic_residual_micro_overfit import parse as train_parse, run as train_run, select_fixed_32
from nanojev_arithmetic_probe import parse as probe_parse, run as probe_run
from nanojev_antirom_compression import (
    LEVELS, SCHEMA, render, convert_presentation, renderer_fingerprint,
    representation, selection_fingerprint,
)
import nanojev_antirom_compression_sweep as sweep


def make_bank(tmp_path):
    root=tmp_path/'bank'
    create(output_dir=root,train_groups=54,predev_groups=27,dev_groups=27,seed=23)
    return root


def test_six_prompts_have_identical_operands_and_candidate_answers(tmp_path):
    root=make_bank(tmp_path)
    bank=load_bank(root,split='train')
    original_ids=[]
    for level in LEVELS:
        this_ids=[]
        for gid,g in bank['groups']:
            for orientation,orig in g['variants'].items():
                original=copy.deepcopy(orig)
                changed=convert_presentation(orig,g['target'],orientation,level)
                assert orig==original
                assert changed['gold_index']==orig['gold_index']
                assert changed['question_id']==orig['question_id']
                assert changed['stratum']==orig['stratum']
                assert [(c['candidate_id'],c['paths'][0]['answer']) for c in changed['candidates']]==[
                    ('LESS',' LESS'),('EQUAL',' EQUAL'),('MORE',' MORE')]
                assert len({c['paths'][0]['prompt'] for c in changed['candidates']})==1
                a,b=g['target']['a'],g['target']['b']
                if orientation=='reverse': a,b=b,a
                assert changed['candidates'][0]['paths'][0]['prompt']==render(level,a,b,g['target']['threshold'])
                if level=='C0':
                    assert changed==orig
                    assert render(level,a,b,g['target']['threshold'])==render_prompt(a,b,g['target']['threshold'])
                assert str(a) in render(level,a,b,g['target']['threshold'])
                assert str(b) in render(level,a,b,g['target']['threshold'])
                assert str(g['target']['threshold']) in render(level,a,b,g['target']['threshold'])
                this_ids.append((gid,orientation,changed['question_id'],changed['gold_index']))
        if not original_ids: original_ids=this_ids
        else: assert this_ids==original_ids
    assert len(LEVELS)==6 and len(set(renderer_fingerprint(level) for level in LEVELS))==6


def test_corrupted_source_and_golds_fail_closed(tmp_path):
    bank=load_bank(make_bank(tmp_path),split='train')
    gid,g=bank['groups'][0]
    original=g['variants']['forward']
    with pytest.raises(ValueError,match='ANTIROM_UNKNOWN'):
        render('C6',1,2,3)
    with pytest.raises(ValueError,match='ANTIROM_INVALID_OPERANDS'):
        render('C3',-1,2,3)
    fake=copy.deepcopy(original)
    fake['candidates'][0]['paths'][0]['prompt']='forgery'
    with pytest.raises(RuntimeError,match='ROM_ARITH_ENGLISH_SOURCE_MISMATCH'):
        convert_presentation(fake,g['target'],'forward','C3')
    fake=copy.deepcopy(original)
    fake['gold_index']=(fake['gold_index']+1)%3
    with pytest.raises(RuntimeError,match='ROM_ARITH_PRESENTATION_GOLD_DRIFT'):
        convert_presentation(fake,g['target'],'forward','C2')


def test_all_six_select_same_fixed32_and_holdout_separate(tmp_path):
    root=make_bank(tmp_path)
    bank=load_bank(root,split='train')
    fixed=select_fixed_32(bank,20261010)
    assert len(fixed)==32
    ids={(gid,orient) for gid,orient,_ in fixed}
    assert len(ids)==32
    dev=load_bank(root,split='dev')
    assert not ({gid for gid,_ in ids} & {gid for gid,_ in dev['groups']})
    hashes=[selection_fingerprint(fixed,dict(bank['groups']),level) for level in LEVELS]
    assert len(set(hashes))==6
    assert all(len(h)==64 for h in hashes)


def test_train_and_probe_dryrun_and_flag_isolation(tmp_path,capsys):
    root=make_bank(tmp_path)
    for level in LEVELS:
        train_args=train_parse(['--bank-dir',str(root),'--antirom-level',level,
                               '--full-budget','--dry-run'])
        train_run(train_args)
        p=json.loads(capsys.readouterr().out)
        assert p['representation']==representation(level)
        assert p['examples']==32 and p['full_budget']
        assert p['no_generation'] and p['accuracy_computed'] is False
        assert p['example_prompt'].endswith('Answer:' if level in ('C0','C1') else 'ANS:')
        baseline_args=probe_parse(['--bank-dir',str(root),'--antirom-level',level,
                                   '--champion-only','--split','dev','--dry-run'])
        probe_run(baseline_args)
        probe=json.loads(capsys.readouterr().out)
        assert probe['representation']==representation(level)
        assert probe['checkpoint']=='pinned-cycle-136-champion'
        assert probe['presentations']==54
    assert not train_parse([]).antirom_level
    assert not probe_parse([]).antirom_level
    assert not train_parse([]).full_budget
    with pytest.raises(SystemExit):
        train_parse(['--rom-language','--antirom-level','C0'])


def test_checkpoint_identity_prevents_cross_level_probing(tmp_path):
    root=make_bank(tmp_path)
    bank=load_bank(root,split='train')
    run=tmp_path/'C3'
    cp=run/'checkpoints'/'step-000000300'
    cp.mkdir(parents=True)
    (cp/'head.safetensors').touch()
    config={'representation':representation('C3'),'antirom_level':'C3',
            'renderer_sha256':renderer_fingerprint('C3'),
            'evidence':dict(max_prompt_tokens=512,max_answer_tokens=64,
              prompt_evidence_tokens=6,answer_evidence_tokens=4,
              path_batch=4,tinystories_path_batch=4)}
    (run/'state.json').write_text(json.dumps({'schema':'nanojev.arithmetic-continuous-control.residual-v2',
                    'bankManifestSha256':bank['manifest_sha256'],'config':config,'checkpoint':str(cp)}))
    (run/'experiment.json').write_text(json.dumps({'mode':'fixed32-antirom-compression-sweep-v1',
                    'config':config}))
    probe_run(probe_parse(['--bank-dir',str(root),'--antirom-level','C3',
                           '--training-dir',str(run),'--split','dev','--dry-run']))
    bad=probe_parse(['--bank-dir',str(root),'--antirom-level','C4',
                     '--training-dir',str(run),'--split','dev','--dry-run'])
    with pytest.raises(RuntimeError,match='ANTIROM_PROBE_REPRESENTATION_OR_EVIDENCE_MISMATCH'):
        probe_run(bad)
    with pytest.raises(RuntimeError,match='ANTIROM_PROBE_REQUIRES_CORRECT_LEVEL'):
        probe_run(probe_parse(['--bank-dir',str(root),'--training-dir',str(run),
                               '--split','dev','--dry-run']))


def test_sweep_dry_run_has_six_independent_jobs_without_gpu(tmp_path,capsys):
    root=make_bank(tmp_path)
    output=tmp_path/'results'
    sweep.execute(sweep.cli(['--bank-dir',str(root),'--output-root',str(output),
                             '--dry-run']))
    payload=json.loads(capsys.readouterr().out)
    assert payload['no_training'] and payload['max_steps']==300
    assert [x['level'] for x in payload['levels']]==list(LEVELS)
    assert len({x['output_dir'] for x in payload['levels']})==6
    assert payload['levels'][0]['sample_prompt'].startswith('Calculate ')
    assert payload['levels'][-1]['sample_prompt'].endswith('ANS:')
    assert not output.exists()


def test_sweep_orchestrates_six_independent_full_budget_trainings(tmp_path,monkeypatch):
    root=make_bank(tmp_path)
    output=tmp_path/'runs'
    jobs=[]
    def fake_stage(cmd,stage_name,level):
        jobs.append((stage_name,level,cmd))
        assert stage_name=='training'
        assert cmd.count('--full-budget')==1
        assert cmd[cmd.index('--antirom-level')+1]==level
        assert cmd[cmd.index('--max-steps')+1]=='300'
        assert cmd[cmd.index('--seed')+1]=='20261010'
        assert '--rom-language' not in cmd
        training=Path(cmd[cmd.index('--output-dir')+1])
        training.mkdir(parents=True)
        cp=training/'checkpoints'/'step-000000300'
        cp.mkdir(parents=True)
        (cp/'head.safetensors').touch()
        bank=load_bank(root,split='train')
        config={'representation':representation(level),'antirom_level':level,
                 'renderer_sha256':renderer_fingerprint(level),
                 'seed':20261010,'target_loss':.05,'full_budget':True,
                 'head_lr':1e-4,'weight_decay':.01,'grad_clip':1.0,'fixed_examples':32}
        (training/'experiment.json').write_text(json.dumps({'config':config,
                 'mode':'fixed32-antirom-compression-sweep-v1',
                 'bankManifestSha256':bank['manifest_sha256']}))
        (training/'state.json').write_text(json.dumps({'config':config,'checkpoint':str(cp.resolve()),
                 'bankManifestSha256':bank['manifest_sha256'],'cursor':{'global_step':300}}))
        events=[{'event':'rom_arithmetic_visibility_verified','backbones':{
                    'qwen':{'prompt_tokens':22-levels.index(level)},
                    'pythia':{'prompt_tokens':23},'tinystories':{'prompt_tokens':24}}},
                {'event':'arithmetic_fixed32_baseline_loss','representation':representation(level),'loss':1.5},
                {'event':'arithmetic_fixed32_loss','representation':representation(level),
                 'fixed_loss':.07,'step':225},
                {'event':'arithmetic_fixed32_loss','representation':representation(level),
                 'fixed_loss':.09,'step':300},
                {'event':'arithmetic_fixed32_complete','representation':representation(level),
                 'steps':300,'final_fixed_loss':.09,'target_loss':.05}]
        (training/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    levels=list(LEVELS)
    monkeypatch.setattr(sweep,'stage',fake_stage)
    args=sweep.cli(['--bank-dir',str(root),'--output-root',str(output),'--train-only'])
    sweep.execute(args)
    assert len(jobs)==6
    assert [j[1] for j in jobs]==levels
    summary=json.loads((output/'summary.json').read_text())
    assert summary['eval_split'] is None
    assert len(summary['levels'])==6
    assert [r['best_step'] for r in summary['levels']]==[225]*6
    assert [r['steps'] for r in summary['levels']]==[300]*6
    assert (output/'summary.csv').is_file()
    jobs.clear()
    sweep.execute(sweep.cli(['--bank-dir',str(root),'--output-root',str(output),
                             '--train-only','--resume']))
    assert not jobs


def test_summary_probe_does_not_accept_drift():
    level='C3'
    state={'checkpoint':'checkpoint'}
    events=[{'event':'arithmetic_fixed32_baseline_loss','representation':representation(level),'loss':1.5},
            {'event':'arithmetic_fixed32_loss','representation':representation(level),
             'step':300,'fixed_loss':.05},
            {'event':'arithmetic_fixed32_complete','representation':representation(level),
             'steps':300,'final_fixed_loss':.05,'target_loss':.05},
            {'event':'rom_arithmetic_visibility_verified','backbones':{'qwen':{'prompt_tokens':20}}}]
    def probe(cp):
        return {'representation':representation(level),'checkpoint':cp,
                'bankManifestSha256':'bank','predictions':{'a':{'gold':'EQUAL','correct':True}},
                'result':{'overall':{'accuracy':1.,'mean_cross_entropy':.2}}}
    assert sweep.summarize_level(level,state,events,'bank',probe('pinned-cycle-136-champion'),
                                  probe('checkpoint'))['dev_accuracy_gain']==0.
    damaged=probe('checkpoint')
    damaged['predictions']['a']['gold']='LESS'
    with pytest.raises(RuntimeError,match='ANTIROM_PROBE_CASES_DIFFER'):
        sweep.summarize_level(level,state,events,'bank',probe('pinned-cycle-136-champion'),damaged)
