"""No-model tests for intentionally false log2 arithmetic rescaling."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_frozen_arithmetic_log2_probe as log2
import nanojev_frozen_arithmetic_direct_logp_probe as direct
from nanojev_arithmetic_task_bank import create as create_legacy
from nanojev_antirom_v2_stream_bank import create as create_v2


def example(a,b,t):
    return {'id':'fresh-000000','a':a,'b':b,'threshold':t,'gold':'EQUAL' if a+b==t else 'MORE' if a+b>t else 'LESS',
            'scale':'small','distance':'exact','carry':True}


def fake_score(logp):
    return {'sum_logp':logp,'mean_logp':logp,
            'token_counts':[1,1,1],'sum_probs':direct.softmax(logp),
            'mean_probs':direct.softmax(logp),'prefix_tokens':40}


def test_every_number_log2_and_wrong_gold_preserved():
    case=example(4,4,8)
    assert case['gold']=='EQUAL'
    prompt=log2.render_log2(case)
    assert 'Calculate 2.00000000 + 2.00000000' in prompt
    assert 'MORE than 3.00000000?' in prompt
    assert 'Answer with exactly LESS, EQUAL, or MORE.\nAnswer:' in prompt
    assert 'Calculate 4 + 4' not in prompt
    row=log2.make_record(case,fake_score([-1.,-1.,-1.]))
    assert row['gold']=='EQUAL'
    assert row['numeric_transform']=='log2'
    assert row['literal_displayed_gold']=='MORE'
    assert row['deliberate_label_mismatch'] is True
    assert row['prompt_sha256']==log2.digest(prompt)
    assert row['log2_addition_identity_error']==pytest.approx(1.)
    assert row['rescaled_values']=={'a':'2.00000000','b':'2.00000000','threshold':'3.00000000'}
    assert log2.render_log2(case)==prompt
    for invalid in (-4,0,1.5,'2'):
        with pytest.raises(ValueError,match='POSITIVE_INTEGER'):
            log2.log2_number(invalid)


def test_false_formula_does_not_transform_labels_or_continuations():
    for case in [example(3,5,8),example(3,5,9),example(3,5,7)]:
        record=log2.make_record(case,fake_score([-2.,-1.,-.5]))
        assert record['gold']==case['gold']
        assert record['sum_logp']==[-2.,-1.,-.5]
        assert record['sum_pred']=='MORE'
        assert math.isfinite(record['log2_addition_identity_error'])


def test_paired_report_baseline_comparison():
    cases=[dict(example(4,4,8),id='a'),dict(example(3,5,9),id='b'),dict(example(3,5,7),id='c')]
    first=[direct.record(r,fake_score([-2,-1,0]),'C0') for r in cases]
    latter=[log2.make_record(r,fake_score([0,-1,-2])) for r in cases]
    p=log2.paired_report(cases,first,latter)
    assert p['paired']['sum']['prediction_flips']==3
    assert p['paired']['sum']['improved_cases']==1
    assert p['paired']['sum']['regressed_cases']==1
    assert p['paired']['sum']['net_correct_gain']==0
    assert p['log2_c0']['modes']['sum']['correct']==1


def test_resume_identity_and_prefix(tmp_path):
    case=example(4,4,8)
    root=tmp_path/'log2';(root/'results').mkdir(parents=True)
    path,meta=log2.paths(root,'qwen')
    expected=log2.expected_meta('qwen','abc',{'resolved_commit':'rev'})
    log2.write_atomic_json(meta,expected)
    record=log2.make_record(case,fake_score([0,-1,-2]))
    path.write_text(json.dumps(record)+'\n',encoding='utf-8')
    assert log2.inspect_records(path,meta,expected,[case],complete=True)==[record]
    drift=copy.deepcopy(expected);drift['renderer_sha256']='untrusted'
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        log2.inspect_records(path,meta,drift,[case])
    tampered=dict(record,prompt_sha256='wrong')
    path.write_text(json.dumps(tampered)+'\n')
    with pytest.raises(RuntimeError,match='SAVED_RESULT_DRIFT'):
        log2.inspect_records(path,meta,expected,[case])
    path.unlink()
    with pytest.raises(RuntimeError,match='INCOMPLETE_RESULTS_PAIR'):
        log2.inspect_records(path,meta,expected,[case])


def test_existing_bank_paired_end_to_end_with_fake_model(tmp_path,monkeypatch):
    legacy=tmp_path/'legacy'
    create_legacy(output_dir=legacy,train_groups=54,predev_groups=27,dev_groups=27,seed=40)
    v2=tmp_path/'v2'
    create_v2(output=v2,seed=20261012,count=90,ood_count=30,source_bank=legacy)
    baseline_root=tmp_path/'baseline'
    manifest=direct.create_bank(baseline_root,legacy,v2,12,20261013)
    cases,_=direct.load_bank(baseline_root,legacy,v2,12,20261013)
    for model_name in ('qwen',):
        path,meta,expected=direct.checkpoint(baseline_root,model_name,'C0',manifest['questions_sha256'],cases,{'revision':'stable'})
        path.parent.mkdir(parents=True)
        meta.write_text(json.dumps(expected))
        with path.open('w') as f:
            for case in cases:
                f.write(json.dumps(direct.record(case,fake_score([0,-1,-2]),'C0'))+'\n')
    info={'revision':'stable'}
    monkeypatch.setattr(direct,'load_source',lambda *a,**kw:(None,object(),object(),'cpu',info))
    counter={'calls':0}
    def deterministic_fake(**kw):
        counter['calls']+=1
        assert '. Is the sum' in kw['prompt']
        return fake_score([0,-1,-2])
    monkeypatch.setattr(direct,'score_one',deterministic_fake)
    out=tmp_path/'new'
    argv=['--baseline-root',str(baseline_root),'--output-root',str(out),
          '--legacy-bank-dir',str(legacy),'--v2-bank-dir',str(v2),
          '--count','12','--models','qwen','--report-every','7']
    a=log2.parse(argv)
    log2.run(a)
    assert counter['calls']==12
    assert (out/'summary.csv').exists()
    assert (baseline_root/'results'/'qwen.C0.jsonl').exists()
    report=json.loads((out/'results'/'qwen.C0.log2.summary.json').read_text())
    assert report['results']['paired']['sum']['prediction_flips']==0
    log2.run(log2.parse(argv+['--resume']))
    assert counter['calls']==12
    with pytest.raises(RuntimeError,match='OUTPUT_EXISTS_USE_RESUME'):
        log2.run(a)
    with pytest.raises(RuntimeError,match='OUTPUT_MUST_DIFFER'):
        log2.run(log2.parse(argv[:2]+['--output-root',str(baseline_root)]+argv[4:]+['--prepare-only']))
