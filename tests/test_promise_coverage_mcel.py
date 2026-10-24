"""The promise projection must never turn structural evidence into runtime proof."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import os
import shutil

import pytest

from tools.promise_coverage.mcel_promises import coverage, admitted_probe, PROBE_SCHEMA, manifest_digest, check_browser_source_binding, browser_loaded_engine_source, canonical_acceptance_failures, canonical_runtime_binding_diagnostics, markdown

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT/'contracts/mcel_promise_evidence.v1.json').read_text(encoding='utf-8'))
NOW = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc)


def audit(*, exact=False, proven=False):
    def item():
        return {'repositoryBinding':{'status':'exact' if exact else 'absent'},
                'evidenceScope':{'canonical': exact}, 'freshness':{'fresh':exact}}
    return {'schema':'mcel-repository-truth-audit-v1',
            'repositoryProvenance':{'fingerprint':'abc123'},
            'summary':{'mode':'report-only', 'status':'pass',
                       'evidenceBindingStatuses':{},'evidenceInputStatuses':{},
                       'semanticRuntimeProvenCount':2 if proven else 0},
            'evidenceInputs':{'runtime':item(),'acceptance':item()},
            'apps':[{'appId':app,'declaredLevel':'semantic-runtime',
                     'claims':{'semanticRuntimeProven':proven}} for app in ('calculator','code-editor')],
            'portableCertification':{'status':'missing','passed':False}}


def probe(ts='2026-10-21T22:00:00Z', fp='abc123', failed=False):
    contracts = {g for p in MANIFEST['promises'] for g in p.get('guarantees',[])}
    tests = [{'name':'independent-test-'+str(i), 'passed':not failed, 'guarantees':[g]} for i,g in enumerate(sorted(contracts))]
    entries = [{'id':g,'passed':not failed,'supportingTests':[tests[i]['name']]}
               for i,g in enumerate(sorted(contracts))]
    return {'schema':PROBE_SCHEMA,'producer':'McelLabEngine.runContractTests',
            'generatedAt':ts,'repositoryProvenance':{'fingerprint':fp},
            'manifestSha256':manifest_digest(MANIFEST),
            'browserSourceBinding':{'status':'pass', 'reason':'served-engine-exact-source-match',
                                    'localEngineSha256':'f'*64},
            'result':{'passed':0 if failed else len(tests),'failed':len(tests) if failed else 0,
                      'guaranteeResults':entries,'tests':tests,
                      'uncoveredGuarantees':[],
                      'failedGuarantees':[g for g in sorted(contracts)] if failed else []}}


def test_manifest_covers_exactly_live_user_clauses_and_references_existing_guarantees():
    source=(ROOT/'main_computer/web/applications/scripts/mcel-contract.js').read_text(encoding='utf-8')
    live_users=set(re.findall(r'id: "(mcel\.user\.[^"]+)"',source))
    live_guarantees=set(re.findall(r'id: "(mcel\.contract\.[^"]+)"',source))
    promises=MANIFEST['promises']
    assert {p['id'] for p in promises if p['id'].startswith('mcel.user.')} == live_users
    assert {g for p in promises for g in p.get('guarantees',[])} <= live_guarantees
    assert len({p['id'] for p in promises}) == len(promises)


def test_missing_reports_are_unverified_not_green_despite_audit_pass():
    report=coverage(MANIFEST,audit(),None,now=NOW)
    assert report['truthAudit']['status']=='pass'
    assert report['status']=='incomplete'
    assert report['counts']=={'unverified':len(MANIFEST['promises'])}


def test_fresh_bound_engine_receipts_prove_only_bounded_clauses():
    report=coverage(MANIFEST,audit(),probe(),now=NOW)
    verified=[p['id'] for p in report['promises'] if p['status']=='verified']
    assert len(verified)==6
    assert all(v.startswith('mcel.user.') for v in verified)
    assert report['status']=='incomplete'
    assert next(p for p in report['promises'] if p['id']=='mcel.platform.development-advantage.v1')['status']=='unverified'


def test_stale_or_mismatched_probe_refused():
    for receipt in (probe(fp='wrong'),probe(ts='2026-10-01T00:00:00Z'),probe(ts='garbage')):
        report=coverage(MANIFEST,audit(),receipt,now=NOW)
        assert report['counts'].get('verified',0)==0
        assert report['engineProbeAdmission']!='admitted'


def test_manifest_change_invalidates_engine_probe_without_mcel_provenance_change():
    bad=probe()
    bad['manifestSha256']='0'*64
    report=coverage(MANIFEST,audit(),bad,now=NOW)
    assert report['engineProbeAdmission']=='promise-manifest-mismatch'
    assert report['counts'].get('verified',0)==0


def test_no_inconsistent_test_verdict_admitted():
    bad=probe()
    bad['result']['guaranteeResults'][0]['passed']=False
    report=coverage(MANIFEST,audit(),bad,now=NOW)
    assert report['engineProbeAdmission']=='inconsistent-test-and-guarantee-outcomes'
    assert report['counts'].get('verified',0)==0


def test_canonical_application_trust_requires_all_scopes_and_full_coverage():
    all_passing=coverage(MANIFEST,audit(exact=True,proven=True),None,now=NOW)
    entry=next(p for p in all_passing['promises'] if p['id']=='mcel.platform.declared-app-semantic-runtime.v1')
    assert entry['status']=='verified'
    missing=coverage(MANIFEST,audit(exact=False,proven=True),None,now=NOW)
    assert next(p for p in missing['promises'] if p['id']==entry['id'])['status']=='unverified'
    failure=coverage(MANIFEST,audit(exact=True,proven=False),None,now=NOW)
    assert next(p for p in failure['promises'] if p['id']==entry['id'])['status']=='failed'


def test_unavailable_audit_blocks_projection():
    report=coverage(MANIFEST,None,probe(),now=NOW)
    assert report['status']=='blocked'
    assert all(x['status']=='blocked' for x in report['promises'])


def test_engine_failure_report_identifies_specific_test_and_details():
    p=probe(failed=True)
    p['result']['tests'][0]['details']='deliberately invalid semantic preservation'
    report=coverage(MANIFEST,audit(),p,now=NOW)
    first=next(item for item in report['promises'] if item['id'].startswith('mcel.user.') and item['status']=='failed')
    assert first['evidence']['failedTests']
    assert first['evidence']['failedTests'][0]['name'] in first['reason']
    assert 'supportingTests' in first['evidence']


def test_portable_authority_reports_reasons_without_recasting_missing_as_failed():
    missing=coverage(MANIFEST,audit(),None,now=NOW)
    portable=lambda report:next(x for x in report['promises'] if x['id']=='mcel.platform.portable-intent-fixtures.v1')
    assert portable(missing)['status']=='unverified'
    a=audit()
    a['portableCertification']={'status':'refused','passed':False,'reasons':['stale-source-fingerprint'], 'eligibleAppIds':[]}
    refused=portable(coverage(MANIFEST,a,None,now=NOW))
    assert refused['status']=='failed'
    assert refused['evidence']['reasons']==['stale-source-fingerprint']
    assert 'stale-source-fingerprint' in refused['reason']


def test_app_failure_identifies_unproven_apps_and_finding_codes():
    a=audit(exact=True,proven=False)
    a['apps'][0]['findingCodes']=['acceptance-test-missing']
    item=next(x for x in coverage(MANIFEST,a,None,now=NOW)['promises'] if x['id']=='mcel.platform.declared-app-semantic-runtime.v1')
    assert item['status']=='failed'
    assert [r['appId'] for r in item['evidence']['unproven']]==['calculator','code-editor']
    assert item['evidence']['unproven'][0]['findingCodes']==['acceptance-test-missing']


def test_live_page_requires_byte_exact_source_not_just_engine_marker():
    local = (ROOT/'main_computer/web/applications/scripts/mcel-engine.js').read_text(encoding='utf-8')
    assert check_browser_source_binding(ROOT, '<script>'+local+'</script>')['status'] == 'pass'
    old_page = '<script>'+local.replace('traitsPreserved=', 'oldTestDetails=',1)+'</script>'
    refused = check_browser_source_binding(ROOT,old_page)
    assert refused['status'] == 'blocked'
    assert refused['reason'] == 'served-engine-source-mismatch'


def test_probe_without_verified_served_source_never_admitted():
    stale = probe()
    stale.pop('browserSourceBinding')
    report = coverage(MANIFEST,audit(),stale,now=NOW)
    assert report['engineProbeAdmission'] == 'live-engine-source-unbound'
    assert report['counts'].get('verified',0) == 0


def test_acceptance_diagnostics_are_source_bound_and_show_failing_selectors(tmp_path):
    path = tmp_path / 'runtime/reports/mcel-acceptance/mcel-acceptance-report.json'
    path.parent.mkdir(parents=True)
    evidence = {'schema':'mcel-acceptance-evidence-report-v1',
                'repositoryProvenance':{'fingerprint':'abc123'},
                'evidenceScope':{'canonical':True},
                'results':[{'appId':'calculator','contracts':[
                    {'contractId':'calculator-c1','enforceable':True,
                     'status':'fail','reason':'one or more bound tests failed',
                     'selectors':['tests/test_calculator.py::test_example'],
                     'stdout':'AssertionError: sample test failed'}]}]}
    path.write_text(json.dumps(evidence),encoding='utf8')
    found=canonical_acceptance_failures(tmp_path,audit())
    assert found['status']=='admitted'
    assert found['failedContracts'][0]['selectors']==['tests/test_calculator.py::test_example']
    evidence['repositoryProvenance']['fingerprint']='wrong'
    path.write_text(json.dumps(evidence),encoding='utf8')
    assert canonical_acceptance_failures(tmp_path,audit())['status']=='refused-noncanonical-or-source-mismatched'


def test_browser_source_binding_uses_executed_page_scripts_without_network_body():
    sync = pytest.importorskip('playwright.sync_api')
    engine = (ROOT/'main_computer/web/applications/scripts/mcel-engine.js').read_text(encoding='utf8')
    with sync.sync_playwright() as playwright:
        chromium = (os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
                    or os.environ.get('CHROMIUM_EXECUTABLE')
                    or shutil.which('chromium') or shutil.which('chromium-browser'))
        options = {'headless': True}
        if chromium:
            options.update(executable_path=chromium,
                           args=['--no-sandbox', '--disable-dev-shm-usage'])
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page()
            # Script text is present in the parsed page even if DevTools has
            # evicted the HTTP response body. Do not evaluate an incomplete
            # standalone engine that lacks the surrounding MCEL contract.
            page.set_content('<script type="text/plain">' + engine + '</script>')
            observed = browser_loaded_engine_source(page)
            assert engine in observed
            assert check_browser_source_binding(ROOT,observed)['status']=='pass'
            page.set_content('<script type="text/plain">' +
                             engine.replace('traitsPreserved=', 'oldDetails=', 1) + '</script>')
            stale = browser_loaded_engine_source(page)
            assert check_browser_source_binding(ROOT,stale)['reason']=='served-engine-source-mismatch'
        finally:
            browser.close()


def test_runtime_binding_diagnostics_source_bound_and_identifies_missing_intents(tmp_path):
    path=tmp_path/'runtime/reports/flog/mcel-runtime/mcel-runtime-flog-report.json'
    path.parent.mkdir(parents=True)
    runtime={'repositoryProvenance':{'fingerprint':'abc123'},
             'evidenceScope':{'canonical':True}, 'trials':[
                 {'app':'file-explorer', 'semanticBinding':{'appId':'file-explorer',
                     'adapterId':'file-explorer-adapter','runtimeBindingAuditReady':False,
                     'operationalSemanticRuntimeReady':False,
                     'runtimeBindingCoverage':{'entries':[{'intentId':'listRoots',
                         'status':'unbound','runtimeBound':False,'bindingSource':'missing:roots'}]}}}]}
    path.write_text(json.dumps(runtime),encoding='utf8')
    report=canonical_runtime_binding_diagnostics(tmp_path,audit())
    assert report['status']=='admitted'
    assert report['applications'][0]['unbound']==[{'intentId':'listRoots','bindingSource':'missing:roots'}]
    assert report['applications'][1]['status']=='witness-absent'
    runtime['trials'][0]['semanticBindingSource'] = {'status': 'blocked', 'reason': 'served-binding-source-mismatch', 'missingSources': ['file-explorer-semantic-adapter.js']}
    runtime['trials'][0]['semanticBindingProbe'] = {'registryPresent': True, 'registeredAdapterPresent': True, 'exportedAdapterPresent': True}
    path.write_text(json.dumps(runtime),encoding='utf8')
    mismatched = canonical_runtime_binding_diagnostics(tmp_path,audit())
    assert mismatched['applications'][0]['status'] == 'served-source-unbound'
    assert 'file-explorer-semantic-adapter.js' in markdown({'status':'failed','generatedAt':'2026-10-22','counts':{},'promises':[],'runtimeBindingDiagnostics':mismatched})
    runtime['trials'][0]['semanticBinding'] = None
    runtime['trials'][0]['semanticBindingSource'] = {'status': 'exact'}
    runtime['trials'][0]['semanticBindingProbe'] = {'registryPresent': False, 'registeredAdapterPresent': False}
    path.write_text(json.dumps(runtime),encoding='utf8')
    missing_registry = canonical_runtime_binding_diagnostics(tmp_path,audit())
    assert missing_registry['applications'][0]['status'] == 'browser-registry-unavailable'
    runtime['trials'][0]['semanticBinding'] = {'appId':'file-explorer','runtimeBindingAuditReady':False,'runtimeBindingCoverage':{'entries':[{'intentId':'listRoots','runtimeBound':False,'status':'unbound','bindingSource':'missing:roots'}]}}
    text=markdown({'status':'failed','generatedAt':'2026-10-22','counts':{},'promises':[],
                   'runtimeBindingDiagnostics':report})
    assert 'Unbound intent `listRoots`' in text
    runtime['repositoryProvenance']['fingerprint']='wrong'
    path.write_text(json.dumps(runtime),encoding='utf8')
    assert canonical_runtime_binding_diagnostics(tmp_path,audit())['status']=='refused-noncanonical-or-source-mismatched'


def test_host_bound_proof_stage_is_never_presented_as_full_semantic_runtime():
    from tools.promise_coverage import mcel_promises
    source=(ROOT/'tools/promise_coverage/mcel_promises.py').read_text(encoding='utf8')
    assert 'tools/mcel_phase1_host_bound_prove.py' in source
    assert "'main_computer/mcel_app_prove.py','--app'" not in source
    assert "stage['fullSemanticRuntimeProven'] = False" in source
    stage={'status':'pass','exitCode':0,'proofScope':'host-bound-ir-native-authoritative',
           'fullSemanticRuntimeProven':False}
    text=markdown({'status':'failed','generatedAt':'2026-10-22','counts':{},'promises':[],
                   'stages':{'host-bound-proof-calculator':stage}})
    assert 'not full semantic-runtime proof' in text
