"""Independent Code Editor source/oracle checks and isolated browser witnesses.

The browser exercises the real semantic runtime and read port against only an
in-memory fake service; these tests must never claim Windows-host acceptance.
"""
from __future__ import annotations

import copy
import json

from tools import mcel_code_editor_functional_parity_frob as frob


def test_incumbent_source_and_candidate_intent_inventory():
    record=frob.source_inventory(frob.ROOT)
    assert len(record['features'])==len(frob.FEATURES)==18
    assert {v['status'] for v in record['features'].values()}=={'SOURCE_COVERED'}
    assert record['sourceEvidence']['semanticIntentCount']==39
    assert len(record['declaredIntentIds'])==39
    assert record['sourceEvidence']['layoutFree'] is True
    assert all(len(sha)==64 for sha in record['sourceEvidence']['sha256'].values())


def test_missing_original_control_fails_source_inventory():
    src=(frob.ROOT/frob.LEGACY).read_text('utf8')
    changed=src.replace('id="file-map-refresh"','id="removed-file-map-refresh"')
    assert src!=changed
    dynamic=(frob.ROOT/frob.LEGACY_JS).read_text('utf8')
    removed_dynamic=dynamic.replace('id="file-map-refresh"', 'id="removed-file-map-refresh"')
    assert removed_dynamic!=dynamic
    report=frob.source_inventory(frob.ROOT,{str(frob.LEGACY):changed,str(frob.LEGACY_JS):removed_dynamic})
    assert report['features']['file-inventory']['status']=='UNVERIFIED'
    assert report['features']['file-inventory']['incumbent']['missingDomIds']==['file-map-refresh']
    assert report['features']['explicit-save']['status']=='SOURCE_COVERED'


def test_missing_original_save_handler_fails_source_inventory():
    src=(frob.ROOT/frob.LEGACY_CORE).read_text('utf8')
    changed=src.replace('applySavedFile(', 'removedSavedFile(')
    assert src!=changed
    report=frob.source_inventory(frob.ROOT,{str(frob.LEGACY_CORE):changed})
    assert report['features']['save-receipt']['status']=='UNVERIFIED'
    assert report['features']['save-receipt']['incumbent']['missingHandlerSignatures']==['applySavedFile(']


def test_missing_semantic_save_intent_fails_inventory(monkeypatch):
    compile_original=frob.compile_semantic_app
    def remove_intent(*args,**kwargs):
        ir=copy.deepcopy(compile_original(*args,**kwargs))
        ir['intents']=[x for x in ir['intents'] if x['id']!='intent:save-source']
        return ir
    monkeypatch.setattr(frob,'compile_semantic_app',remove_intent)
    report=frob.source_inventory(frob.ROOT)
    assert report['features']['explicit-save']['status']=='MISSING'
    assert report['features']['save-receipt']['status']=='MISSING'
    assert report['features']['explicit-save']['candidate']['missingIntentIds']==['save-source']


def test_browser_witnesses_cover_multidocument_and_all_declared_intents():
    result=frob.run(frob.ROOT)
    assert result['status']=='PASS_ISOLATED',result['browser'].get('error')
    assert result['browser']['status']=='PASS'
    assert len(result['browser']['cases'])>=70
    assert result['summary']['browserMismatches']==0
    assert result['missingMandatoryScenarios']==[]
    assert result['browser']['pageErrors']==[]
    assert result['intentCoverage']['sourceOnly']==[]
    assert len(result['intentCoverage']['observed'])==39
    assert all(x['mockedHttp'] is True for x in result['browser']['cases'])
    assert result['browser']['httpTransport'].startswith('in-memory-')
    assert result['nativeMonaco']=='UNVERIFIED'
    assert result['liveWindowsParity']=='UNVERIFIED'
    assert result['promotionAuthorized'] is False


def test_browser_import_failure_is_incomplete_not_success(monkeypatch):
    def missing(_):raise ModuleNotFoundError("No module named 'playwright'")
    monkeypatch.setattr(frob,'browser_acceptance',missing)
    result=frob.run(frob.ROOT)
    assert result['status']=='INCOMPLETE'
    assert result['summary']['browserCases']==0
    assert result['summary']['byStatus']['PASS_ISOLATED']==0
    assert result['missingMandatoryScenarios']
    assert result['promotionAuthorized'] is False


def test_browser_mismatch_blocks_acceptance(monkeypatch):
    monkeypatch.setattr(frob,'browser_acceptance',lambda repo: {
        'status':'MISMATCH', 'cases':[{'feature':'explicit-save','id':'save-exact-target','status':'MISMATCH'}],
        'pageErrors':[], 'observedIntents':['intent:save-source']})
    result=frob.run(frob.ROOT)
    assert result['status']=='INCOMPLETE'
    assert result['summary']['browserMismatches']==1
    assert result['intentCoverage']['sourceOnly']
    assert result['promotionAuthorized'] is False


def test_declared_intents_without_mandatory_witnesses_cannot_pass(monkeypatch):
    # A deceptively green browser summary with one token case per feature is
    # insufficient even if every intent is claimed as observed.
    claims=[{'feature':f.key,'id':f.key+'-token','status':'PASS'} for f in frob.FEATURES]
    monkeypatch.setattr(frob,'browser_acceptance',lambda repo: {
        'status':'PASS','cases':claims, 'pageErrors':[],
        'observedIntents':frob.source_inventory(repo)['declaredIntentIds']})
    result=frob.run(frob.ROOT)
    assert result['status']=='INCOMPLETE'
    assert 'dirty-a-survives-b' in result['missingMandatoryScenarios']
    assert result['summary']['byStatus']['PASS_ISOLATED']==18
    assert result['promotionAuthorized'] is False


def test_report_cli_writes_nonproduction_evidence(tmp_path,monkeypatch,capsys):
    # Use completed controlled evidence from a single stub to avoid a second
    # browser cost in the CLI's file-writing test.
    repo=frob.ROOT
    records=frob.source_inventory(repo)
    cases=[{'feature':f.key,'id':f.key+'-witness','status':'PASS'} for f in frob.FEATURES]
    cases.extend({'feature':'authorization','id':id,'status':'PASS'} for id in frob.MANDATORY_BROWSER_WITNESSES)
    monkeypatch.setattr(frob,'browser_acceptance',lambda _: {
        'status':'PASS','cases':cases,'pageErrors':[],
        'observedIntents':records['declaredIntentIds']})
    destination=tmp_path/'result.json'
    assert frob.main(['--repo',str(repo),'--report',str(destination)])==0
    output=json.loads(destination.read_text('utf8'))
    assert output['status']=='PASS_ISOLATED'
    assert output['promotionAuthorized'] is False
    assert output['designApproval']=='UNVERIFIED'
    assert output['nativeMonaco']=='UNVERIFIED'
