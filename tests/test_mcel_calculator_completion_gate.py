"""Completion gate refuses planned/skipped/stale or host-bound-to-global proof upgrades."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from tools.mcel_calculator_completion import REQUIRED_CONTRACTS, assess_completion, run_completion

SHA = 'sha256:'+'a'*64


def fixtures():
    contracts={key:{'status':'pass','enforceable':True,'executed':True,
                    'bindingId':key+'.binding','summary':{'passed':2,'skipped':0}} for key in REQUIRED_CONTRACTS}
    proof={'appId':'calculator','passed':True,'truthStatus':'host-bound-ir-native-authoritative',
           'fullSemanticRuntimeProven':False,'pendingAcceptanceContracts':[],
           'acceptanceContracts':contracts, 'semanticFingerprint':SHA,
           'repositoryFingerprint':'a'*64,'packageFingerprint':SHA,'runtimeProjectionFingerprint':SHA,
           'intentCoverage':{'passed':True,'declaredIntentCount':11,'coveredIntentCount':11},
           'evidence':{'browserObservation':{'fresh':True},'effects':{'status':'closed'}}}
    receipt={'schema':'mcel.calculator.ai-tool-receipt.v1','ok':True,'action':'calculator.evaluate',
             'intentId':'intent:calculator.evaluate-expression','semanticFingerprint':SHA,
             'sourceBindingFingerprint':SHA,'receiptSha256':SHA,'coreSha256':SHA,'runnerSha256':SHA,'executionLane':'local-arithmetic',
             'effectStatus':'none','evaluation':{'ok':True,'displayValue':'3.4 m'}}
    canonical = json.dumps({
        'action': 'calculator.evaluate', 'expression': '3 m + 40 cm',
        'evaluation': receipt['evaluation'], 'semanticFingerprint': receipt['semanticFingerprint'],
        'sourceBindingFingerprint': receipt['sourceBindingFingerprint'],
        'coreSha256': receipt['coreSha256'], 'runnerSha256': receipt['runnerSha256'],
    }, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    receipt['receiptSha256'] = 'sha256:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return proof,receipt


def test_accepts_complete_host_bound_scope_but_never_global_truth():
    proof,receipt=fixtures()
    report=assess_completion(proof,receipt)
    assert report['passed'] is True and report['hostBoundApplicationComplete'] is True
    assert report['fullSemanticRuntimeProven'] is False
    assert report['packageHostTruthGateProven'] is False
    assert report['liveOllamaModelInvoked'] is False


def test_fails_if_contract_is_planned_skipped_or_unexecuted():
    for value in ('missing','planned','skipped','unexecuted','unbound'):
        proof,receipt=fixtures()
        key=REQUIRED_CONTRACTS[1]
        if value=='missing':del proof['acceptanceContracts'][key]
        if value=='planned':proof['acceptanceContracts'][key]['status']='not-due'
        if value=='skipped':proof['acceptanceContracts'][key]['summary']['skipped']=1
        if value=='unexecuted':proof['acceptanceContracts'][key]['executed']=False
        if value=='unbound':proof['acceptanceContracts'][key]['bindingId']=''
        assert assess_completion(proof,receipt)['passed'] is False, value


def test_fails_on_stale_fingerprint_missing_effects_or_ai_only_claims():
    for variant in ('fingerprint','no-effects','wrong-receipt','forged-digest','not-fresh','wrong-scope','outstanding'):
        proof,receipt=fixtures()
        if variant=='fingerprint':receipt['semanticFingerprint']='sha256:'+'b'*64
        if variant=='no-effects':proof['evidence']['effects']['status']='open'
        if variant=='wrong-receipt':receipt['evaluation']['displayValue']='3.5 m'
        if variant=='forged-digest':receipt['receiptSha256']=SHA
        if variant=='not-fresh':proof['evidence']['browserObservation']['fresh']=False
        if variant=='wrong-scope':proof['truthStatus']='semantic-runtime-proven'
        if variant=='outstanding':proof['pendingAcceptanceContracts']=['calculator.acceptance.layout-inference']
        assert assess_completion(proof,receipt)['passed'] is False, variant


def test_real_completion_calls_existing_authorities_once_without_repair(tmp_path):
    proof,receipt=fixtures()
    seen=[]
    def prove(**kwargs):seen.append(('prove',kwargs['app_id']));return deepcopy(proof)
    def evaluate(request,**kwargs):seen.append(('evaluate',request['action']));return deepcopy(receipt)
    result=run_completion(tmp_path,prove=prove,evaluate=evaluate)
    assert result['passed'] is True
    assert seen==[('prove','calculator'),('evaluate','calculator.evaluate')]


def test_fresh_host_bound_proof_uses_bounded_existing_authority(monkeypatch, tmp_path):
    import json
    import subprocess
    from tools.mcel_calculator_completion import _run_fresh_host_bound_authority

    seen = []

    def completed(cmd, **kwargs):
        seen.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({'passed': True, 'appId':'calculator'}))

    monkeypatch.setattr('tools.mcel_calculator_completion.subprocess.run', completed)
    report = _run_fresh_host_bound_authority(repo=tmp_path, app_id='calculator', headed=False)
    assert report['passed'] is True
    assert seen[0][1]['timeout'] == 300
    assert Path(seen[0][0][1]).name == 'mcel_phase1_host_bound_prove.py'
    assert '--check' in seen[0][0] and '--json' in seen[0][0]


def test_fresh_host_bound_proof_rejects_nongenuine_report(monkeypatch, tmp_path):
    import subprocess
    import pytest
    from tools.mcel_calculator_completion import _run_fresh_host_bound_authority
    monkeypatch.setattr('tools.mcel_calculator_completion.subprocess.run',
                        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout='{}'))
    with pytest.raises(RuntimeError, match='passing proof'):
        _run_fresh_host_bound_authority(repo=tmp_path, app_id='calculator', headed=False)


def test_repository_fingerprint_is_raw_sha256_hex_from_actual_producer():
    proof, receipt = fixtures()
    report = assess_completion(proof, receipt)
    assert report['passed'] is True
    assert report['provenanceChecks']['repositoryFingerprintValid'] is True
    assert report['provenanceChecks']['semanticFingerprintsMatch'] is True


def test_provenance_reports_and_refuses_bad_digest_formats():
    cases = (
        ('repositoryFingerprint', 'sha256:' + 'a'*64, 'repositoryFingerprintValid'),
        ('repositoryFingerprint', 'a'*63, 'repositoryFingerprintValid'),
        ('repositoryFingerprint', 'g'*64, 'repositoryFingerprintValid'),
        ('packageFingerprint', 'a'*64, 'packageFingerprintValid'),
        ('runtimeProjectionFingerprint', 'sha256:' + 'x'*64, 'runtimeProjectionFingerprintValid'),
    )
    for field, malformed, check in cases:
        proof, receipt = fixtures()
        proof[field] = malformed
        result = assess_completion(proof, receipt)
        assert result['passed'] is False, field
        assert result['provenanceChecks'][check] is False, field


def test_source_binding_and_semantic_mismatch_still_fail_closed():
    proof, receipt = fixtures()
    receipt['sourceBindingFingerprint'] = 'sha256:' + 'z'*64
    result = assess_completion(proof, receipt)
    assert result['provenanceChecks']['sourceBindingFingerprintValid'] is False
    assert result['passed'] is False

    proof, receipt = fixtures()
    receipt['semanticFingerprint'] = 'sha256:' + 'b'*64
    result = assess_completion(proof, receipt)
    assert result['provenanceChecks']['semanticFingerprintsValid'] is True
    assert result['provenanceChecks']['semanticFingerprintsMatch'] is False
    assert result['passed'] is False
