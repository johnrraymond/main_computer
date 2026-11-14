"""ROM language isolate: same proven arithmetic control, only question text changes."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_arithmetic_task_bank import create, render_prompt, LABELS
from nanojev_arithmetic_runtime import load_bank, DEFAULT_OUTPUT
from nanojev_arithmetic_residual_micro_overfit import parse as micro_parse,run as micro_run,select_fixed_32
from nanojev_arithmetic_probe import parse as probe_parse,run as probe_run
from nanojev_rom_arithmetic_language import (
    ROM_OUTPUT, REPRESENTATION, render_rom,convert_presentation,
    selection_identity,representation_fingerprint,
)


def _bank(tmp_path):
    directory=tmp_path/'arithmetic-bank'
    create(output_dir=directory,train_groups=54,predev_groups=27,dev_groups=27,seed=27)
    return directory


def test_rom_is_short_english_like_without_oracle_answer():
    same=render_rom(137,84,220)
    assert same.startswith('ROM 1.0 - Arithmetic comparison\n')
    assert 'Let RESULT be the sum of 137 and 84.' in same
    assert 'Compare RESULT with 220.' in same
    assert '221' not in same
    assert not any(p in same for p in ('{"op"','"gold_index"','SAME / SAME / SAME','NORMALIZED_AST'))
    assert same.endswith('Answer:')
    assert len(same)<512
    assert render_rom(84,137,220)!=same
    with pytest.raises(ValueError,match='ROM_ARITH_INVALID_OPERANDS'):
        render_rom(-2,1,3)


def test_sealed_gold_identical_and_presentation_unchanged(tmp_path):
    bank=load_bank(_bank(tmp_path),split='train')
    for gid,g in bank['groups']:
        for variant,sample in g['variants'].items():
            original=copy.deepcopy(sample)
            changed=convert_presentation(sample,g['target'],variant)
            assert sample==original
            assert changed['gold_index']==sample['gold_index']
            assert changed['question_id']==sample['question_id']
            assert [c['candidate_id'] for c in changed['candidates']]==list(LABELS)
            assert [c['paths'][0]['answer'] for c in changed['candidates']]==[' LESS',' EQUAL',' MORE']
            assert len({c['paths'][0]['prompt'] for c in changed['candidates']})==1
            assert changed['candidates'][0]['paths'][0]['prompt'].startswith('ROM 1.0')
            assert changed['candidates'][0]['paths'][0]['prompt']!=sample['candidates'][0]['paths'][0]['prompt']
            bad=copy.deepcopy(sample)
            bad['candidates'][0]['paths'][0]['prompt']='forged'
            with pytest.raises(RuntimeError,match='ROM_ARITH_ENGLISH_SOURCE_MISMATCH'):
                convert_presentation(bad,g['target'],variant)
            bad=copy.deepcopy(sample)
            bad['gold_index']=(bad['gold_index']+1)%3
            with pytest.raises(RuntimeError,match='ROM_ARITH_PRESENTATION_GOLD_DRIFT'):
                convert_presentation(bad,g['target'],variant)


def test_fixed32_same_selection_and_dev_separate(tmp_path):
    p=_bank(tmp_path)
    train=load_bank(p,split='train')
    fixed=select_fixed_32(train,20261010)
    assert len(fixed)==32
    lookup=dict(train['groups'])
    h=selection_identity(fixed,lookup)
    assert h==selection_identity(fixed,lookup) and len(h)==64
    assert len(representation_fingerprint())==64
    dev=load_bank(p,split='dev')
    assert not ({gid for gid,_,_ in fixed} & {gid for gid,_ in dev['groups']})


def test_plan_is_isolated_has_identical_fixed32_and_no_gpu(tmp_path,capsys):
    bank=_bank(tmp_path)
    args=micro_parse(['--bank-dir',str(bank),'--rom-language','--dry-run'])
    assert args.output_dir==ROM_OUTPUT
    micro_run(args)
    plan=json.loads(capsys.readouterr().out)
    assert plan['no_generation'] and not plan['accuracy_computed']
    assert plan['examples']==32 and plan['representation']==REPRESENTATION
    assert plan['example_prompt'].startswith('ROM 1.0')
    assert len(plan['selection_sha256'])==64
    assert micro_parse([]).output_dir!=args.output_dir
    assert probe_parse(['--rom-language']).training_dir==ROM_OUTPUT
    assert probe_parse([]).training_dir==DEFAULT_OUTPUT
    assert not probe_parse([]).rom_language


def test_dev_probe_uses_exact_representation_and_no_training(tmp_path,capsys):
    bank=_bank(tmp_path)
    args=probe_parse(['--bank-dir',str(bank),'--rom-language','--split','dev','--champion-only','--dry-run'])
    probe_run(args)
    plan=json.loads(capsys.readouterr().out)
    assert plan['presentations']==54
    assert plan['representation']==REPRESENTATION
    assert plan['checkpoint']=='pinned-cycle-136-champion'
    assert plan['no_training'] and not plan['accuracy_computed']


def test_wrong_checkpoint_representation_fails_before_gpu(tmp_path):
    bank=_bank(tmp_path)
    train=load_bank(bank,split='train')
    output=tmp_path/'old-run'
    output.mkdir()
    (output/'state.json').write_text(json.dumps({'schema':'nanojev.arithmetic-continuous-control.residual-v2',
        'bankManifestSha256':train['manifest_sha256'],
        'config':{'representation':'english-arithmetic-original-v1'},
        'checkpoint':str(output/'checkpoint')}))
    (output/'checkpoint').mkdir()
    args=probe_parse(['--bank-dir',str(bank),'--rom-language','--training-dir',str(output),
                      '--dry-run','--split','dev'])
    with pytest.raises(RuntimeError,match='ROM_ARITH_PROBE_REPRESENTATION_OR_EVIDENCE_MISMATCH'):
        probe_run(args)


def test_english_baseline_cannot_be_miscompared_as_rom(tmp_path):
    bank=_bank(tmp_path)
    p=load_bank(bank,split='dev')
    assert p['count']==27
    # A baseline file from English has no ROM identity; fail closed.
    from nanojev_arithmetic_probe import compare_predictions
    # Regression compare contract includes representation equality before per-case comparison.
    src=(TOOLS/'nanojev_arithmetic_probe.py').read_text()
    assert "baseline.get('representation','english-arithmetic-original-v1')!=representation" in src


def test_rom_promotion_gate_requires_both_memorization_and_generalization(tmp_path,monkeypatch):
    from nanojev_rom_arithmetic_gate import evaluate
    output=tmp_path/'run'
    output.mkdir()
    checkpoint=output/'checkpoints'/'step-000000300'
    checkpoint.mkdir(parents=True)
    (checkpoint/'head.safetensors').touch()
    bank='sealed-bank-test'
    config={'representation':REPRESENTATION,'renderer_sha256':representation_fingerprint()}
    (output/'experiment.json').write_text(json.dumps({'mode':'fixed32-rom-arithmetic-language-control',
                                                        'config':config,'bankManifestSha256':bank}))
    (output/'state.json').write_text(json.dumps({'config':config,'bankManifestSha256':bank,
                                                 'checkpoint':str(checkpoint.resolve())}))
    (output/'events.jsonl').write_text(json.dumps({'event':'arithmetic_fixed32_complete',
                            'representation':REPRESENTATION,'final_fixed_loss':.04,
                            'target_loss':.05,'loss_target_reached':True})+'\n')
    def result(checkpoint_name,accuracy,ce,preds):
        return {'representation':REPRESENTATION,'split':'dev','bankManifestSha256':bank,
                'checkpoint':checkpoint_name,'predictions':preds,
                'result':{'representation':REPRESENTATION,'overall':{
                    'accuracy':accuracy,'mean_cross_entropy':ce}}}
    old={'a':{'gold':'MORE','correct':False},'b':{'gold':'LESS','correct':True}}
    new={'a':{'gold':'MORE','correct':True},'b':{'gold':'LESS','correct':True}}
    base=tmp_path/'baseline.json';later=tmp_path/'trained.json'
    base.write_text(json.dumps(result('pinned-cycle-136-champion',.5,.8,old)))
    later.write_text(json.dumps(result(str(checkpoint.resolve()),1.,.4,new)))
    row=evaluate(output=output,baseline_file=base,trained_file=later)
    assert row['passed'] and row['next_stage']=='teach-one-ROM-primitive'
    (output/'events.jsonl').write_text(json.dumps({'event':'arithmetic_fixed32_complete',
                            'representation':REPRESENTATION,'final_fixed_loss':.65,
                            'target_loss':.05,'loss_target_reached':False})+'\n')
    assert not evaluate(output=output,baseline_file=base,trained_file=later)['passed']


def test_visibility_check_prevents_truncation():
    from nanojev_rom_arithmetic_language import verify_prompt_visibility
    class FakeTokenizer:
        def encode(self,txt,add_special_tokens=False):
            return list(txt)
    bundle=SimpleNamespace(tokenizer=FakeTokenizer(),max_positions=1000)
    args=SimpleNamespace(max_prompt_tokens=500)
    sample={'candidates':[{'paths':[{'prompt':'short','answer':' LESS'}]}]}
    assert verify_prompt_visibility(samples=[sample],bundles={'fake':bundle},args=args)['fake']['prompt_tokens']==5
    args.max_prompt_tokens=4
    with pytest.raises(RuntimeError,match='ROM_ARITH_WOULD_TRUNCATE'):
        verify_prompt_visibility(samples=[sample],bundles={'fake':bundle},args=args)
