"""Test that the full regression is complete, independent and strict."""
import json
from fractions import Fraction
from types import SimpleNamespace

import pytest
from tools import nanojev_gamer_v1_v31_live_regression as battery


def fake_deck():
    result = []
    for key, size in battery.EXPECTED.items():
        for i in range(size):
            sign = (-1, 0, 1)[i % 3]
            result.append({'id': f'{key}.{i}', 'prompt': 'frozen',
                           'expected': str(sign), 'scope_kind': key,
                           'surface': ('spine_difference' if i % 2 else 'source_native'),
                           'sign': sign, 'heldout_train': False})
    assert len(result) == 624
    assert {i: sum(row['sign']==i for row in result) for i in (-1,0,1)} == battery.SIGN_COUNTS
    return result


def fake_report(deck):
    return {'status': 'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
            'production_modified': False, 'promotion_authorized': False,
            'wide_head_untouched': True,
            'activation_source': 'frozen_base_qwen_layer18_no_lora_adapter',
            'cases': [{'id': x['id'], 'expected': x['expected'],
                       'difference': x['expected'], 'exact': True,
                       'oracle_used_for_prediction': False,
                       'calculation_authority': 'exact_fraction_postprocess'}
                      for x in deck]}


def test_624_complete_gate_strict_and_all_signs():
    deck=fake_deck()
    summary=battery.summarize(deck, fake_report(deck), {'sha':'source'})
    assert summary['all_gates_met']
    assert summary['exact'] == 624
    assert all(v['exact']==v['total'] for v in summary['by_scope_kind'].values())
    assert summary['by_sign']['0'] == {'exact':208,'total':208}
    assert summary['by_sign']['1'] == {'exact':208,'total':208}
    assert summary['by_sign']['-1'] == {'exact':208,'total':208}
    assert not summary['promotion_authorized']


def test_one_protected_regression_fails():
    deck=fake_deck()
    report=fake_report(deck)
    report['cases'][0]['difference']='999'
    report['cases'][0]['exact']=False
    result=battery.summarize(deck,report,{})
    assert result['exact']==623 and not result['all_gates_met']
    assert not result['acceptance_gates']['all_288_protected_train_exact']
    assert result['failed_cases'][0]['id']==deck[0]['id']


def test_one_stress_zero_regression_fails():
    deck=fake_deck(); report=fake_report(deck)
    idx=next(i for i,d in enumerate(deck) if d['scope_kind']=='stress_role_reordered' and d['sign']==0)
    report['cases'][idx]['difference']='-8'
    report['cases'][idx]['exact']=False
    summary=battery.summarize(deck,report,{})
    assert not summary['acceptance_gates']['all_192_stress_exact']
    assert not summary['acceptance_gates']['all_208_zero_exact']
    assert summary['failed_cases'][0]['id']==deck[idx]['id']


def test_missing_case_or_prediction_or_wrong_oracle_provenance_refused():
    deck=fake_deck(); report=fake_report(deck)
    report['cases'].pop()
    with pytest.raises(ValueError,match='CASE'):
        battery.summarize(deck,report,{})
    report=fake_report(deck)
    report['cases'][4]['oracle_used_for_prediction']=True
    with pytest.raises(ValueError,match='SCORING_INTEGRITY'):
        battery.summarize(deck,report,{})
    report=fake_report(deck)
    report['cases'][4]['expected']='999'
    with pytest.raises(ValueError,match='SCORING_INTEGRITY'):
        battery.summarize(deck,report,{})


def test_scope_classification_refuses_test_and_unknown():
    assert battery._scope_kind({'scope':'train','kind':'original'})=='protected_train'
    assert battery._scope_kind({'scope':'dev_stress','kind':'role_reordered'})=='stress_role_reordered'
    with pytest.raises(ValueError,match='UNKNOWN_SCOPE'):
        battery._scope_kind({'scope':'test','kind':'original'})


def test_command_one_stage_no_existing_output(monkeypatch,tmp_path):
    deck=fake_deck()
    monkeypatch.setattr(battery,'regenerate',lambda *a: (deck,{'source':'test'}))
    def fake_live(options):
        assert options.stage=='v30-live' and options.v30_prompt is None
        assert options.v30_cases_jsonl is not None
        recorded=[json.loads(x) for x in open(options.v30_cases_jsonl, encoding='utf-8')]
        assert len(recorded)==624
        assert not any('oracle' in x for x in recorded)
        report=fake_report(recorded)
        with open(options.v30_report,'w',encoding='utf-8') as h:
            json.dump(report,h)
        return report
    monkeypatch.setattr(battery.live,'run',fake_live)
    args=SimpleNamespace(run='existing',input='input',v27_run='v27',
        v29_run='v29',v30_run='v30',output=str(tmp_path/'results'),
        device='cpu',max_length=1024)
    report=battery.run(args)
    assert report['all_gates_met']
    assert (tmp_path/'results'/'acceptance_report.json').exists()
    with pytest.raises(ValueError,match='OUTPUT_ALREADY_EXISTS'):
        battery.run(args)


def test_failed_acceptance_returns_failure_exit(monkeypatch,tmp_path):
    deck=fake_deck()
    monkeypatch.setattr(battery,'regenerate',lambda *a: (deck,{}))
    def bad_run(options):
        report=fake_report(deck)
        report['cases'][0]['difference']='100'
        report['cases'][0]['exact']=False
        with open(options.v30_report,'w') as h:
            json.dump(report,h)
        return report
    monkeypatch.setattr(battery.live,'run',bad_run)
    args=SimpleNamespace(run='existing',input='input',v27_run='v27',
        v29_run='v29',v30_run='v30',output=str(tmp_path/'failed'),
        device='cpu',max_length=1024)
    with pytest.raises(SystemExit) as ex:
        battery.run(args)
    assert ex.value.code==1
    accepted=json.loads((tmp_path/'failed'/'acceptance_report.json').read_text())
    assert accepted['status'].startswith('FAIL_')
