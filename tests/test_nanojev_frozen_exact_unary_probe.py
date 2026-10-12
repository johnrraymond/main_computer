"""Offline regression tests: genuine unary magnitudes, frozen logP, resumable results."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import sys

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import nanojev_frozen_exact_unary_bank as bank
import nanojev_frozen_exact_unary_probe as probe


def test_300_per_task_balanced_and_unique():
    cases=bank.generate()
    assert len(cases)==600
    assert bank.validate(cases,count=300,compare_max=100,add_operand_max=16)=={
        'compare':{'LESS':100,'EQUAL':100,'MORE':100},
        'add_compare':{'LESS':100,'EQUAL':100,'MORE':100}}
    assert bank.generate()==cases
    assert len({r['id'] for r in cases})==600
    assert len({(r['a'],r['b']) for r in cases if r['task']=='compare'})==300
    assert len({bank.canonical(r) for r in cases if r['task']=='add_compare'})==300
    # Fully independent deterministic mathematical oracle solves every case.
    assert all(bank.gold(row)==row['gold'] for row in cases)
    # No comparison reversal duplicates accidentally have the same answer.
    look={(r['a'],r['b']):r['gold'] for r in cases if r['task']=='compare'}
    for (a,b),g in look.items():
        if a!=b:assert look[(b,a)]!=g


def test_prompt_encodes_exact_quantities_and_correct_labels():
    for case in bank.generate(count=21,compare_max=8,add_operand_max=4):
        for rep in bank.REPRESENTATIONS:
            prompt=probe.render(case,rep)
            assert 'Answer:' in prompt
            assert 'LESS' in prompt and 'EQUAL' in prompt and 'MORE' in prompt
            if rep=='tally':
                expression=prompt.split('Expression: ',1)[1].split('\n',1)[0]
                if case['task']=='compare':
                    assert expression==f'{"|"*case["a"]} ? {"|"*case["b"]}'
                else:
                    assert expression==f'{"|"*case["a"]} + {"|"*case["b"]} ? {"|"*case["target"]}'
            else:
                assert '|' not in prompt
    # Previous log-length tally misrepresented 4 as 3 bars. Now exact.
    assert probe.tally(4)=='||||'
    assert probe.tally(8)=='||||||||'
    with pytest.raises(ValueError):probe.tally(0)
    with pytest.raises(ValueError):probe.tally(1.0)


def test_generator_rejects_bad_sizes():
    for kwargs in ({'count':10},{'count':303,'compare_max':100},
                   {'count':300,'add_operand_max':10}):
        with pytest.raises((ValueError,RuntimeError)):
            bank.generate(**kwargs)


def test_sealed_bank_replay_and_tampering(tmp_path):
    root=tmp_path/'run'
    cases,manifest=bank.prepare(root,count=30,compare_max=10,add_operand_max=5)
    loaded,meta=bank.load(root,count=30,compare_max=10,add_operand_max=5)
    assert cases==loaded and manifest==meta
    assert bank.bank_hash(loaded)==meta['cases_sha256']
    with pytest.raises(RuntimeError,match='BANK_EXISTS'):
        bank.prepare(root,count=30,compare_max=10,add_operand_max=5)
    altered=copy.deepcopy(loaded);altered[0]['gold']='MORE' if loaded[0]['gold']!='MORE' else 'LESS'
    (root/'cases.jsonl').write_text(''.join(bank._line(r) for r in altered))
    with pytest.raises(RuntimeError,match='HASH_DRIFT'):
        bank.load(root,count=30,compare_max=10,add_operand_max=5)
    manifest['cases_sha256']=bank.bank_hash(altered)
    (root/'bank_manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='INCORRECT_GOLD'):
        bank.load(root,count=30,compare_max=10,add_operand_max=5)


def score_for(label):
    labels=bank.LABELS
    logits=[-4.]*3
    logits[labels.index(label)]=-.01
    return {'sum_logp':logits,'mean_logp':logits,
            'token_counts':[1,1,1],'sum_probs':probe.direct.softmax(logits),
            'mean_probs':probe.direct.softmax(logits),'prefix_tokens':10}


def test_metrics_and_paired_predictions():
    cases=bank.generate(count=9,compare_max=3,add_operand_max=3)
    pair=[r for r in cases if r['task']=='compare']
    original=[probe.result_row(r,score_for(r['gold']),'decimal') for r in pair]
    target=[probe.result_row(r,score_for(r['gold']),'tally') for r in pair]
    assert probe.report(target)['by_scoring']['sum']['accuracy']==1.
    assert probe.report(target)['by_scoring']['sum']['operand_swap_consistency']==1.
    delta=probe.paired(original,target)
    assert delta['sum']=={'prediction_flips':0,'improved':0,'regressed':0,'net_correct':0}
    # Artificial one-case regression, with guaranteed class flip.
    flipped='EQUAL' if pair[0]['gold']!='EQUAL' else 'MORE'
    target[0]=probe.result_row(pair[0],score_for(flipped),'tally')
    delta=probe.paired(original,target)
    assert delta['sum']['prediction_flips']==1
    assert delta['sum']['regressed']==1
    assert delta['sum']['net_correct']==-1
    assert probe.report(target)['by_scoring']['sum']['accuracy']==pytest.approx(8/9)


def test_resume_provenance_and_corruption_guard(tmp_path):
    cases=bank.generate(count=9,compare_max=3,add_operand_max=3)[:9]
    manifest={'cases_sha256':'abc'}
    info={'resolved_commit':'fake'}
    expected=probe.metadata('qwen','compare','decimal',manifest,info)
    root=tmp_path/'r';root.mkdir()
    meta=root/'qwen.compare.decimal.meta.json';path=root/'qwen.compare.decimal.jsonl'
    probe.save_json(meta,expected)
    row=probe.result_row(cases[0],score_for(cases[0]['gold']),'decimal')
    path.write_text(json.dumps(row)+'\n')
    assert probe.inspect_rows(path,meta,expected,cases,'decimal')==[row]
    with pytest.raises(RuntimeError,match='INCOMPLETE_RESULTS'):
        probe.inspect_rows(path,meta,expected,cases,'decimal',complete=True)
    with pytest.raises(RuntimeError,match='RESUME_IDENTITY_DRIFT'):
        probe.inspect_rows(path,meta,{**expected,'bank_sha256':'something-else'},cases,'decimal')
    modified={**row,'gold':'EQUAL' if row['gold']!='EQUAL' else 'MORE'}
    path.write_text(json.dumps(modified)+'\n')
    with pytest.raises(RuntimeError,match='RECORDED_RESULT_DRIFT'):
        probe.inspect_rows(path,meta,expected,cases,'decimal')


def test_fake_frozen_backbone_end_to_end_without_ml(tmp_path,monkeypatch):
    output=tmp_path/'probe'
    args=probe.parse(['--output-root',str(output),'--count','9','--comparison-max','3',
                      '--addition-max-operand','3','--models','qwen',
                      '--report-every','3'])
    def fake_source(model,args):
        return None, object(), object(), 'cpu', {'model':'fake','resolved_commit':'fake','dtype':'float32','device':'cpu'}
    def fake_score(*,torch,model,tokenizer,prompt,device):
        expression=prompt.split('Expression: ',1)[1].split('\n')[0]
        values=expression.split(' ')
        is_tally='|' in expression
        parse_val=lambda t:len(t) if is_tally else int(t)
        if '+' in expression:
            a,b,t=map(parse_val,(values[0],values[2],values[4]));gold=bank.classify(a+b,t)
        else:
            a,b=map(parse_val,(values[0],values[2]));gold=bank.classify(a,b)
        return score_for(gold)
    monkeypatch.setattr(probe.direct,'load_source',fake_source)
    monkeypatch.setattr(probe.direct,'score_one',fake_score)
    probe.run(args)
    with (output/'summary.csv').open(encoding='utf-8',newline='') as f:
        data=list(csv.DictReader(f))
    assert len(data)==8  # 2 tasks * 2 representations * 2 scoring modes
    assert all(float(r['accuracy'])==1.0 for r in data)
    assert all(float(r['operand_swap_consistency'])==1.0 for r in data if r['task']=='compare')
    assert all(r['vs_decimal_net_correct']=='0' for r in data if r['representation']=='tally')
    args_resume=probe.parse(['--output-root',str(output),'--count','9','--comparison-max','3',
                             '--addition-max-operand','3','--models','qwen','--resume'])
    monkeypatch.setattr(probe.direct,'score_one',lambda **kwargs: (_ for _ in ()).throw(AssertionError('rescore')))
    probe.run(args_resume)
    with pytest.raises(RuntimeError,match='RESULTS_EXIST_USE_RESUME'):
        probe.run(args)
    prepared=tmp_path/'prepared'
    a=probe.parse(['--output-root',str(prepared),'--count','9','--comparison-max','3',
                   '--addition-max-operand','3','--prepare-only'])
    monkeypatch.setattr(probe.direct,'load_source',lambda *a,**k: (_ for _ in ()).throw(AssertionError('model load')))
    probe.run(a)
    assert not (prepared/'results').exists()


def test_cli_contract():
    options=probe.parse([])
    assert options.count==300
    assert options.tasks==['compare','add_compare']
    assert options.representations==['decimal','tally']
    assert options.models==['qwen','pythia','tinystories']
