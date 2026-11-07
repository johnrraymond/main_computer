"""Task Manager differential frob must fail closed under contract drift.

Browser effects are in-memory only. No real Windows target, controller, service,
provider, production schedule, or host is ever accessed by these tests.
"""
from __future__ import annotations

import json

import pytest

from tools import mcel_task_manager_functional_parity_frob as frob


def test_exact_incumbent_contract_and_paired_intents():
    report=frob.source_inventory(frob.ROOT)
    assert len(report['features'])==len(frob.FEATURES)==19
    assert set(v['status'] for v in report['features'].values())=={'SOURCE_COVERED'}
    assert report['sourceEvidence']['semanticIntentCount']==19
    assert report['sourceEvidence']['capabilityRisks']==['consequential','external-read']
    assert report['sourceEvidence']['layoutFree'] is True


def test_missing_incumbent_control_never_counts_as_pass():
    html=frob._text(frob.ROOT,frob.INCUMBENT_HTML)
    changed=html.replace('id="task-ai-prompt"','id="removed-ai-prompt"')
    assert changed!=html
    record=frob.source_inventory(frob.ROOT,overrides={str(frob.INCUMBENT_HTML):changed})
    assert record['features']['ai']['status']=='UNVERIFIED'
    assert record['features']['ai']['incumbent']['missingDomIds']==['task-ai-prompt']


def test_missing_incumbent_handler_never_counts_as_pass():
    script=frob._text(frob.ROOT,frob.INCUMBENT_OPERATIONS)
    changed=script.replace('async function createTaskSchedule()', 'async function removedScheduleFn()')
    assert changed!=script
    record=frob.source_inventory(frob.ROOT,overrides={str(frob.INCUMBENT_OPERATIONS):changed})
    assert record['features']['schedule-create']['status']=='UNVERIFIED'
    assert any('createTaskSchedule' in v for v in record['features']['schedule-create']['incumbent']['missingHandlerSignatures'])


def test_missing_semantic_action_detected(monkeypatch):
    real=frob.compile_semantic_app
    def broken(*args,**kwargs):
        ir=real(*args,**kwargs)
        ir['intents']=[x for x in ir['intents'] if x['id']!='intent:run-schedule']
        return ir
    monkeypatch.setattr(frob,'compile_semantic_app',broken)
    record=frob.source_inventory(frob.ROOT)
    assert record['features']['schedule-run']['status']=='MISSING'
    assert 'intent:run-schedule' in record['features']['schedule-run']['candidate']['missingIntentIds']


def test_browser_isolated_boundaries_and_required_witnesses():
    report=frob.browser_comparison(frob.ROOT)
    assert report['status']=='PASS', [c for c in report['cases'] if c['status']!='PASS']
    assert len(report['cases'])>=32 and report['pageErrors']==[]
    cases={case['id']:case for case in report['cases']}
    for name in ('automatic-first-snapshot','read-only-poll-pause-resume',
                 'generation-bound-terminate','generation-bound-kill','forged-generation-rejected',
                 'consumed-target-refuses-repeat','dismissed-confirmation-has-no-effect',
                 'stale-schedule-revision-refused','stale-schedule-delete-refused',
                 'recorded-schedule-revision','explicit-advisory-ai','local-csrf-all-mutations',
                 'visible-cpu-gpu-measurements','telemetry-uses-primary-workspace','unavailable-gpu-is-not-zero'):
        assert cases[name]['status']=='PASS'
    assert report['httpTransport'].startswith('in-memory-')


def test_browser_rejects_missing_process_birth(monkeypatch):
    # Changing the fixture's observed identity must not produce the original
    # successful process-action evidence; we do not weaken the oracle instead.
    malformed=frob.FIXTURE_JS.replace("created_at:birth,memory_human", "created_at:'',memory_human")
    assert malformed!=frob.FIXTURE_JS
    monkeypatch.setattr(frob,'FIXTURE_JS',malformed)
    report=frob.browser_comparison(frob.ROOT)
    assert report['status']=='INCOMPLETE'
    assert any(c['status']!='PASS' for c in report['cases'])


def test_missing_functional_evidence_blocks_promotion(monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(frob,'browser_comparison',lambda repo:{
        'status':'INCOMPLETE','cases':[{'feature':'terminate','id':'failure','status':'MISMATCH'}],
        'pageErrors':[],'backend':'stub','httpTransport':'in-memory-only'})
    destination=tmp_path/'task-report.json'
    assert frob.run(frob.ROOT,destination)==1
    output=json.loads(destination.read_text())
    assert output['status']=='INCOMPLETE'
    assert output['features']['terminate']['status']=='BROKEN'
    assert output['promotionAuthorized'] is False
    assert output['liveWindowsParity']=='UNVERIFIED'


def test_successful_boundary_remains_nonproduction(monkeypatch,tmp_path):
    simulated={key:[{'feature':key,'id':key+'-witness','status':'PASS'}] for key in (f.key for f in frob.FEATURES)}
    monkeypatch.setattr(frob,'browser_comparison',lambda repo:{
        'status':'PASS','cases':sum(simulated.values(),[]),
        'pageErrors':[],'backend':'stub','httpTransport':'in-memory-only'})
    destination=tmp_path/'task-report.json'
    assert frob.run(frob.ROOT,destination)==0
    output=json.loads(destination.read_text())
    assert output['status']=='PASS_ISOLATED'
    assert output['promotionAuthorized'] is False
    assert output['designApproval']=='UNVERIFIED'
    assert output['liveWindowsParity']=='UNVERIFIED'
    assert output['summary']['browserMismatches']==0
