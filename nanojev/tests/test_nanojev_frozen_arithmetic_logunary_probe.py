"""No pretrained models required; test log-length unary representation and pairing."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_frozen_arithmetic_logunary_probe as probe
import nanojev_frozen_arithmetic_direct_logp_probe as direct
from nanojev_arithmetic_task_bank import create as create_legacy
from nanojev_antirom_v2_stream_bank import create as create_v2


def example(a,b,t,id='fresh-000000'):
    return {'id':id, 'a':a, 'b':b, 'threshold':t,
            'gold':'EQUAL' if a+b==t else ('LESS' if a+b<t else 'MORE'),
            'scale':'small','distance':'exact','carry':False}


def fake_score(logps):
    return {'sum_logp':list(logps), 'mean_logp':list(logps),
            'token_counts':[1,1,1], 'sum_probs':direct.softmax(logps),
            'mean_probs':direct.softmax(logps), 'prefix_tokens':22}


def test_tally_is_exact_integer_log2_count_and_no_digits():
    samples = {1:1, 2:2, 3:2, 4:3, 7:3, 8:4, 16:5, 48:6, 19:5, 67:7, 137:8}
    for value,n in samples.items():
        assert probe.length(value)==n
        assert probe.tally(value).count('|')==n
        assert probe.tally(value).split(' ')==['|']*n
        assert not any(ch.isdigit() for ch in probe.tally(value))
    for invalid in (0,-1,0.5,True,'12',None):
        with pytest.raises(ValueError,match='POSITIVE_INTEGER'):
            probe.length(invalid)


def test_prompt_has_tally_lengths_and_preserves_false_equality_gold():
    c=example(4,4,8)
    prompt=probe.render_logunary(c)
    assert prompt.startswith('Calculate | | | + | | |. Is the sum LESS than, EQUAL to, or MORE than | | | |?')
    assert 'Answer with exactly LESS, EQUAL, or MORE.\nAnswer:' in prompt
    assert 'Calculate 4 + 4' not in prompt
    record=probe.make_record(c,fake_score([-1,-1,-1]))
    assert record['gold']=='EQUAL'
    assert record['tally_lengths']=={'a':3,'b':3,'threshold':4}
    assert record['literal_displayed_gold']=='MORE'
    assert record['deliberate_label_mismatch'] is True
    assert record['prompt_sha256']==probe.digest(prompt)
    assert record['numeric_transform']=='logunary'


def test_collision_counts_distinct_values_only():
    assert probe.has_encoding_collision(example(2,3,6)) is True  # 2,3 both two bars
    assert probe.has_encoding_collision(example(4,4,8)) is False # identical values are not collisions
    assert probe.has_encoding_collision(example(4,5,8)) is True
    assert probe.literal_displayed_gold(example(48,19,67))=='MORE'


def test_paired_comparisons_use_same_gold_and_ids():
    cases=[example(4,4,8,'a'),example(2,3,6,'b'),example(2,3,4,'c')]
    orig=[direct.record(c,fake_score([0,-1,-2]),'C0') for c in cases]
    new=[probe.make_record(c,fake_score([-2,-1,0])) for c in cases]
    result=probe.paired_report(cases,orig,new)
    assert result['paired']['sum']['prediction_flips']==3
    assert result['paired']['sum']['improved_cases']==1
    assert result['paired']['sum']['regressed_cases']==1
    assert result['paired']['sum']['accuracy_gain']==0
    assert result['encoding_collision_cases']==2
    assert result['deliberate_label_mismatch_cases']==2


def test_resume_verification_rejects_model_and_renderer_drift(tmp_path):
    c=example(4,4,8)
    root=tmp_path/'tally';(root/'results').mkdir(parents=True)
    path,meta=probe.paths(root,'qwen')
    expected=probe.expected_meta('qwen','identity',{'resolved_commit':'stable'})
    probe.atomic_json(meta,expected)
    item=probe.make_record(c,fake_score([-1,-2,0]))
    path.write_text(json.dumps(item)+'\n',encoding='utf-8')
    assert probe.check_records(path,meta,expected,[c],complete=True)==[item]
    drift=copy.deepcopy(expected);drift['renderer_sha256']='wrong'
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        probe.check_records(path,meta,drift,[c])
    modified=dict(item,prompt_sha256='changed')
    path.write_text(json.dumps(modified)+'\n',encoding='utf-8')
    with pytest.raises(RuntimeError,match='SAVED_RECORD_DRIFT'):
        probe.check_records(path,meta,expected,[c])
    path.unlink()
    with pytest.raises(RuntimeError,match='INCOMPLETE_RESULTS_PAIR'):
        probe.check_records(path,meta,expected,[c])


def test_e2e_fake_model_uses_original_bank_and_rejects_rewrites(tmp_path,monkeypatch):
    legacy=tmp_path/'legacy'
    create_legacy(output_dir=legacy,train_groups=54,predev_groups=27,dev_groups=27,seed=40)
    v2=tmp_path/'v2'
    create_v2(output=v2,seed=20261012,count=90,ood_count=30,source_bank=legacy)
    original=tmp_path/'baseline'
    manifest=direct.create_bank(original,legacy,v2,12,20261013)
    cases,_=direct.load_bank(original,legacy,v2,12,20261013)
    orig_path,orig_meta,orig_expected=direct.checkpoint(
        original,'qwen','C0',manifest['questions_sha256'],cases,{'revision':'stable'})
    orig_path.parent.mkdir(parents=True)
    orig_meta.write_text(json.dumps(orig_expected))
    with orig_path.open('w') as f:
        for c in cases:
            f.write(json.dumps(direct.record(c,fake_score([0,-1,-2]),'C0'))+'\n')
    model_info={'revision':'stable'}
    monkeypatch.setattr(direct,'load_source',lambda *a,**kw:(None,object(),object(),'cpu',model_info))
    calls=[]
    def fake_infer(**kw):
        calls.append(kw['prompt'])
        assert '| |' in kw['prompt']
        assert 'Calculate ' in kw['prompt']
        assert not any(char.isdigit() for char in kw['prompt'])
        return fake_score([0,-1,-2])
    monkeypatch.setattr(direct,'score_one',fake_infer)
    output=tmp_path/'tallies'
    argv=['--baseline-root',str(original),'--output-root',str(output),
          '--legacy-bank-dir',str(legacy),'--v2-bank-dir',str(v2),
          '--models','qwen','--count','12','--report-every','7']
    probe.run(probe.parse(argv))
    assert len(calls)==12
    assert (output/'summary.csv').exists()
    assert json.loads((output/'summary.json').read_text())['bank_sha256']==manifest['questions_sha256']
    assert orig_path.exists()
    assert len((output/'summary.csv').read_text().splitlines())==3  # header, sum, mean
    probe.run(probe.parse(argv+['--resume']))
    assert len(calls)==12
    with pytest.raises(RuntimeError,match='OUTPUT_EXISTS_USE_RESUME'):
        probe.run(probe.parse(argv))
    with pytest.raises(RuntimeError,match='OUTPUT_MUST_DIFFER'):
        probe.run(probe.parse(argv[:2]+['--output-root',str(original)]+argv[4:]+['--prepare-only']))


def test_magnitude_signature_is_monotone_but_not_injective():
    # Monotonicity survives compression, but exact magnitude does not.
    assert all(probe.length(n) <= probe.length(n+1) for n in range(1, 1024))
    assert probe.tally(4) == probe.tally(7)
    assert probe.tally(7) != probe.tally(8)
    assert probe.literal_displayed_gold(example(4,4,8)) == 'MORE'
    assert example(4,4,8)['gold'] == 'EQUAL'
