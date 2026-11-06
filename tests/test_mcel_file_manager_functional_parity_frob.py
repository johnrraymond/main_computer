"""Independent source, browser and security guards for File Manager parity."""
from __future__ import annotations

import copy
from pathlib import Path

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from tools import mcel_file_manager_functional_parity_frob as frob

ROOT=Path(__file__).resolve().parents[1]


def test_source_inventory_is_incumbent_grounded():
    report=frob.source_inventory(ROOT)
    assert len(report['features'])==12
    assert all(x['status']=='SOURCE_COVERED' for x in report['features'].values())
    assert {'intent:enter-directory','intent:search','intent:preview-entry','intent:open-with'} <= set(report['declaredIntents'])
    assert len(report['sourceEvidence']['incumbentScriptSha256'])==64


def test_missing_original_preview_is_unverified():
    markup=frob.read(ROOT,frob.INCUMBENT_MARKUP).replace('id="file-explorer-preview"','id="file-explorer-preview-removed"')
    report=frob.source_inventory(ROOT,markup=markup)
    assert report['features']['preview']['status']=='UNVERIFIED'
    assert report['features']['preview']['incumbent']['missingDomIds']==['file-explorer-preview']
    assert report['features']['search']['status']=='SOURCE_COVERED'


def test_missing_incumbent_behavior_is_unverified():
    script=frob.read(ROOT,frob.INCUMBENT_SCRIPT).replace('function searchSystemFileExplorer()', 'function searchMissingSystemFileExplorer()')
    report=frob.source_inventory(ROOT,incumbent_js=script)
    assert report['features']['search']['status']=='UNVERIFIED'


def test_missing_candidate_intent_is_missing_even_if_html_looks_right():
    ir=copy.deepcopy(compile_semantic_app('file-manager',ROOT))
    ir['intents']=[intent for intent in ir['intents'] if intent['id']!='intent:preview-entry']
    report=frob.source_inventory(ROOT,ir=ir)
    assert report['features']['preview']['status']=='MISSING'
    assert report['features']['metadata-only']['status']=='MISSING'
    assert report['features']['preview']['candidate']['missingIntentIds']==['intent:preview-entry']


def test_fixture_is_created_from_real_files_and_has_distinct_path_identities(tmp_path):
    data=frob.fixture_payloads(tmp_path)
    assert (tmp_path/'docs/nested/todo.md').read_text()=='Nested todo list.\n'
    assert {v['relative_path'] for v in data['listing']['docs']}=={'docs/guide.txt','docs/nested'}
    assert data['content']['README.md']=='# Project overview\n'
    assert 'assets/preview.bin' not in data['content']
    assert data['roots'][0]['id']=='workspace'
    assert data['roots'][1]['id']=='scratch'


def test_browser_disabled_cannot_promote_or_pass():
    report=frob.assess(ROOT,browser=False)
    assert report['status']=='INCOMPLETE'
    assert report['promotionAuthorized'] is False
    assert report['liveWindowsParity']=='UNVERIFIED'
    assert report['summary']['unverifiedFeatures']


def test_case_mismatches_are_independent_of_source_contract():
    items=[]
    frob._case(items,'listing','deliberate-row-mismatch',['README.md'],['other.md'],False)
    assert items[0]['status']=='MISMATCH'
    assert items[0]['incumbent']!=items[0]['candidate']


def test_both_real_browser_handlers_are_exercised_without_live_authority():
    report=frob.assess(ROOT,browser=True)
    assert report['browser']['status']=='PASS',report['browser'].get('reason')
    assert len(report['browser']['cases'])==16
    assert report['summary']['browserMismatches']==0
    assert report['status']=='PASS_ISOLATED'
    assert not any(report['browser']['pageErrors'].values())
    assert report['promotionAuthorized'] is False
    assert report['designApproval']=='UNVERIFIED'
    assert report['liveWindowsParity']=='UNVERIFIED'
    assert all(c['mockedHttp'] is True for c in report['browser']['cases'])
    assert {'suggested-application-route-only','startup-default-location-and-listing','root-selection-opens-directory'} <= {c['id'] for c in report['browser']['cases']}
