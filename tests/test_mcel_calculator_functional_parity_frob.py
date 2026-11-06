"""The parity oracle must fail closed when incumbent or candidate evidence drifts."""
from __future__ import annotations

import copy
from pathlib import Path

from main_computer.mcel_calculator_expanded_host import compile_expanded
from main_computer.mcel_calculator_host_projection import project
from tools import mcel_calculator_functional_parity_frob as frob

ROOT = Path(__file__).resolve().parents[1]


def test_source_inventory_is_grounded_in_actual_incumbent_and_paired_ir():
    result=frob.source_inventory(ROOT)
    assert len(result['features'])==13
    assert result['features']['arithmetic']['status']=='SOURCE_COVERED'
    assert result['features']['expression-keypad']['status']=='SOURCE_COVERED'
    assert result['features']['symbolic-evaluate']['status']=='SOURCE_COVERED'
    assert result['features']['contextual-chat']['status']=='SOURCE_COVERED'
    assert not result['features']['contextual-chat']['candidate']['missingIntentIds']
    assert result['features']['arithmetic-model-handoff']['status']=='SOURCE_COVERED'
    assert result['features']['graph-editor']['status']=='SOURCE_COVERED'
    assert len(result['sourceEvidence']['incumbentProjectionSha256'])==64


def test_incumbent_missing_control_not_silently_filled_by_candidate():
    markup=project(ROOT).replace('id="calculator-chat-panel"', 'id="calculator-chat-panel-removed"')
    result=frob.source_inventory(ROOT,markup=markup)
    assert result['features']['contextual-chat']['status']=='UNVERIFIED'
    assert result['features']['contextual-chat']['incumbent']['missingMarkupIds']==['calculator-chat-panel']


def test_missing_semantic_intent_fails_even_when_existing_visuals_look_similar():
    ir=copy.deepcopy(compile_expanded(ROOT))
    ir['intents']=[x for x in ir['intents'] if x['id']!='intent:evaluate']
    features=frob.source_inventory(ROOT,ir=ir)['features']
    assert features['arithmetic']['status']=='MISSING'
    assert features['arithmetic']['candidate']['missingIntentIds']==['intent:evaluate']
    assert features['symbolic-evaluate']['status']=='SOURCE_COVERED'


def test_generated_keypad_requires_real_runtime_path():
    runtime=(ROOT/frob.CANDIDATE_RUNTIME).read_text(encoding='utf8')
    result=frob.source_inventory(ROOT,candidate_runtime=runtime.replace('data-mcel-expression-pad','removed-pad'))
    assert result['features']['expression-keypad']['status']=='MISSING'
    assert result['features']['expression-keypad']['candidate']['missingRuntimeSignatures']==['data-mcel-expression-pad']


def test_no_browser_cannot_be_reported_as_verified_or_promoted():
    report=frob.assess(ROOT,browser=False)
    assert report['status']=='INCOMPLETE'
    assert report['promotionAuthorized'] is False
    assert report['browser']['status']=='UNVERIFIED'
    assert report['features']['arithmetic']['status']=='UNVERIFIED'
    assert report['summary']['featureCount']==13
    assert report['summary']['browserCases']==0


def test_comparisons_expose_actual_values_and_mock_provenance():
    same=frob._comparison('same','arithmetic','5','5',True)
    different=frob._comparison('different','result-question',{'a':1},{'b':1},False,mocked=True)
    assert same['status']=='PASS' and same['mockedHttp'] is False
    assert different['status']=='MISMATCH' and different['mockedHttp'] is True
    assert different['incumbent']!={'b':1}


def test_incumbent_probe_is_real_projected_dom_not_fake_feature_fixture():
    html=frob.incumbent_probe_html(ROOT)
    assert 'id="calculator-app"' in html
    assert 'id="calculator-chat-panel"' in html
    assert 'window.MainComputerCalculatorStandaloneMode=true' in html
    assert 'function askCalculatorModel()' in html
    assert 'function drawCalculatorGraph()' in html


def test_complete_browser_differential_and_explicit_gaps():
    report=frob.assess(ROOT,browser=True)
    assert report['status']=='PASS_ISOLATED'
    assert report['promotionAuthorized'] is False
    assert report['liveProviderParity']=='UNVERIFIED'
    assert report['humanDesignApproval']=='UNVERIFIED'
    assert report['browser']['status']=='PASS'
    assert report['browser'].get('reason') is None, report['browser'].get('reason')
    assert len(report['browser']['cases'])==21
    assert not report['browser']['pageErrors']['incumbent']
    assert not report['browser']['pageErrors']['candidate']
    cases={x['id']:x for x in report['browser']['cases']}
    required=(
        'arithmetic:3 m + 40 cm','keypad-equals','graph:x^2','symbolic:evaluate',
        'mode-preserves-arithmetic-input','arithmetic:incompatible-units',
        'graph-reset-defaults','graph-reset-redraw','qa-context-keys',
        'arithmetic-helper-applies-result','graph-helper-applies-graph',
        'symbolic-helper-populates-input','symbolic-clear-output',
        'graph-editor:sin()','graph-editor:x','graph-editor:backspace','graph-editor:clear',
        'contextual-chat:controller-thread-context',
    )
    for case_id in required:
        assert cases[case_id]['status']=='PASS',case_id
    assert all(row['status']=='PASS' for row in cases.values())
    assert cases['symbolic:evaluate']['mockedHttp'] is True
    assert report['summary']['browserMismatches']==0
    assert report['summary']['byStatus']=={'PASS_ISOLATED':13}
    assert report['features']['graph-reset']['status']=='PASS_ISOLATED'
    assert report['features']['contextual-chat']['status']=='PASS_ISOLATED'


def test_mid_browser_failure_keeps_prior_cases_and_never_greenlights(monkeypatch):
    partial=frob._comparison('prior','graph-reset',{'x':1},{'x':2},False)
    def fail(_repo):
        raise frob.BrowserProbeError('simulated stage error',[partial],{'incumbent':[],'candidate':[]})
    monkeypatch.setattr(frob,'browser_comparison',fail)
    report=frob.assess(ROOT,browser=True)
    assert report['browser']['status']=='INCOMPLETE'
    assert report['browser']['cases']==[partial]
    assert report['features']['graph-reset']['status']=='BROKEN'
    assert report['summary']['browserMismatches']==1
    assert report['promotionAuthorized'] is False


def test_cli_writes_diagnostic_report_without_canonical_mutation(tmp_path):
    target=tmp_path/'report.json'
    exit_code=frob.main(['--repo',str(ROOT),'--report',str(target),'--no-browser'])
    assert exit_code==1  # Functional parity is incomplete, not a command crash.
    import json
    payload=json.loads(target.read_text(encoding='utf-8'))
    assert payload['status']=='INCOMPLETE'
    assert payload['promotionAuthorized'] is False
    assert payload['scope']=='isolated-read-only-no-production-promotion'


def test_isolated_gate_can_turn_green_without_ever_authorizing_promotion(monkeypatch):
    inventory=frob.source_inventory(ROOT)
    for row in inventory['features'].values():
        row['status']='SOURCE_COVERED'
    monkeypatch.setattr(frob,'source_inventory',lambda _repo: copy.deepcopy(inventory))
    cases=[frob._comparison('case-'+fid,fid,1,1,True) for fid in inventory['features']]
    monkeypatch.setattr(frob,'browser_comparison',lambda _repo: {
        'status':'PASS','cases':cases,'pageErrors':{'incumbent':[],'candidate':[]}})
    result=frob.assess(ROOT,browser=True)
    assert result['status']=='PASS_ISOLATED'
    assert result['promotionAuthorized'] is False
    assert result['liveProviderParity']=='UNVERIFIED'
    assert result['humanDesignApproval']=='UNVERIFIED'
