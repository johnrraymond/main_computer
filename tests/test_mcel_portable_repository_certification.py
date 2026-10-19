"""Patch 19: source-bound, fail-closed portable semantic proof promotion."""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from main_computer import mcel_portable_certification as cert
from main_computer import mcel_truth_audit as audit

NOW=datetime(2026,10,18,20,0,tzinfo=timezone.utc)
PROVENANCE={
    'schema':'mcel-repository-provenance-v3',
    'algorithm':'sha256-source-path-content-v2',
    'fingerprint':'representative-source-fingerprint',
    'scope':'snapshot-mcel-authority-v3',
    'selectionMethod':'snapshot-source-roots',
}
BINDINGS={app: {
    'source': {'packageFingerprint': f'sha256:{app}-test-package'},
    'declaredIntents':sorted(intents),
    'assetDigests':{'portable.js':f'sha256:{app}-portable-runtime'},
    'presentationSha256':f'sha256:{app}-presentation',
} for app,intents in cert.APP_INTENTS.items()}


def fixture_proof():
    outputs={
        'askModelForExpression':'2+3',
        'askModelForGraphExpression':'2*x',
        'askModelForMathicsExpression':'Expand[(x + 1)^2]',
        'evaluateMathics':'5',
        'askResultQuestion':'5',
    }
    receipts=[];backend=[];attempts=[]
    for name,text in outputs.items():
        provider='mathics3-local-worker' if name=='evaluateMathics' else 'ollama-loopback:test-model'
        out_sha=cert.sha(cert.canonical_bytes(text)).removeprefix('sha256:')
        digest=cert.sha(cert.canonical_bytes(cert.EXPECTED_INPUTS[name])).removeprefix('sha256:')
        e={'schema':'mcel.external-provider-receipt.v1','verifiedExecution':True,
            'intent':name,'backend':provider,'inputSha256':digest,'outputSha256':out_sha}
        receipts.append({'intent':name,'receipt':{'text':text,'provider':provider,'evidence':e}})
        backend.append({'intent':name,'backend':provider,'inputSha256':digest,'outputSha256':out_sha})
        if name!='evaluateMathics':
            attempts.append({'intent':name,'attempt':1,'accepted':True,
                'rawSha256':hashlib.sha256(text.encode()).hexdigest(),
                'rawChars':len(text),'rawLines':1,
                'normalizedSha256':out_sha})
    calc={'appId':'calculator','status':'pass','ready':True,'portableMode':True,
          'packageFingerprint':BINDINGS['calculator']['source']['packageFingerprint'],
          'sourceBindings':copy.deepcopy(BINDINGS['calculator']),
          'declaredIntents':sorted(cert.APP_INTENTS['calculator']),
          'executed':list(cert.EXPECTED_EXECUTION['calculator']),
          'distinctExecutedIntents':11,'successfulCallCount':11,
          'refusals':sorted(cert.EXTERNAL),'safetyRefusals':sorted(cert.EXTERNAL),
          'externalInputRefusals':sorted(cert.EXTERNAL),
          'externalSemanticChecks':sorted(cert.EXTERNAL),
          'externalProviderVerified':True,'localValidated':True,
          'externalProviderReceipts':backend,'externalReceipts':receipts,
          'externalModelAttempts':attempts,
          'calculatorFinalState':dict(zip(
              ('expression','graphExpression','mathicsExpression','mathicsResult','answer'),outputs.values()))}
    effects=[
        {'intent':'saveFile','path':'example.txt','beforeSha256':hashlib.sha256(b'original\n').hexdigest(),
         'afterSha256':hashlib.sha256(b'saved\n').hexdigest()},
        {'intent':'applyReviewedPatch','path':'example.txt','handle':'test-handle','beforeSha256':hashlib.sha256(b'saved\n').hexdigest(),
         'afterSha256':hashlib.sha256(b'reviewed\n').hexdigest()},
    ]
    editor={'appId':'code-editor','status':'pass','ready':True,'portableMode':True,
            'packageFingerprint':BINDINGS['code-editor']['source']['packageFingerprint'],
            'sourceBindings':copy.deepcopy(BINDINGS['code-editor']),
            'declaredIntents':sorted(cert.APP_INTENTS['code-editor']),
            'executed':list(cert.EXPECTED_EXECUTION['code-editor']),
            'successfulCallCount':10,'distinctExecutedIntents':8,
            'refusals':[],'safetyRefusals':['applyReviewedPatch'],
            'sourceTextAfterProof':'reviewed\n','filesystemEffects':effects}
    return {'schema':cert.PROOF_SCHEMA,'scope':'independent-package-domain-operations',
            'generatedAt':NOW.isoformat().replace('+00:00','Z'),
            'repositoryProvenance':copy.deepcopy(PROVENANCE),'applications':[calc,editor],
            'status':'pass','passed':True,'portableLocalAndWorkspacePassed':True,
            'externalProviderProven':True,'fullSemanticRuntimeProven':True,
            'liveProviderProofRequested':True,'missingExternalProviderProof':[],
            'reason':'bounded-live-19-intent-semantic-fixtures-passed'}


def verify(proof):
    return cert.verify_proof(proof,Path('.'),now=NOW,
        expected_bindings=BINDINGS,expected_provenance=PROVENANCE)


def test_source_bound_19_intent_evidence_passes():
    evidence=fixture_proof()
    assert verify(evidence)['eligibleAppIds']==['calculator','code-editor']


@pytest.mark.parametrize('mutation,expected_reason', [
    ('legacy-proof','proof-missing-stale-or-future-timestamp'),
    ('stale','proof-missing-stale-or-future-timestamp'),
    ('repository','proof-repository-provenance-mismatch'),
    ('package','calculator-generated-package-binding-mismatch'),
    ('asset','code-editor-generated-package-binding-mismatch'),
    ('intent','calculator-intent-proof-invalid'),
    ('external','askModelForExpression-receipt-invalid'),
    ('wrong-input','askModelForExpression-receipt-invalid'),
    ('graph','graph-fixture-invalid'),
    ('model-state','calculator-state-receipt-mismatch'),
    ('file-bytes','code-editor-filesystem-effects-invalid'),
    ('file-path','code-editor-filesystem-effects-invalid'),
    ('authorization','calculator-safety-or-semantic-proof-invalid'),
    ('scope','top-level-fixture-verdict-not-passing'),
])
def test_invalid_or_replayed_evidence_cannot_promote(mutation, expected_reason):
    proof=fixture_proof()
    c,e=proof['applications']
    if mutation=='legacy-proof':proof.pop('generatedAt')
    elif mutation=='stale':proof['generatedAt']=(NOW-timedelta(days=8)).isoformat()
    elif mutation=='repository':proof['repositoryProvenance']['fingerprint']='obsolete'
    elif mutation=='package':c['sourceBindings']['source']['packageFingerprint']='obsolete'
    elif mutation=='asset':e['sourceBindings']['assetDigests']['portable.js']='tampered'
    elif mutation=='intent':c['executed'].pop()
    elif mutation=='external':c['externalReceipts'][0]['receipt']['text']='not the hash'
    elif mutation=='wrong-input':
        c['externalReceipts'][0]['receipt']['evidence']['inputSha256']='wrong'
        c['externalProviderReceipts'][0]['inputSha256']='wrong'
    elif mutation=='graph':
        c['externalReceipts'][1]['receipt']['text']='2*x+1'
        digest=cert.sha(cert.canonical_bytes('2*x+1')).removeprefix('sha256:')
        c['externalReceipts'][1]['receipt']['evidence']['outputSha256']=digest
        c['externalProviderReceipts'][1]['outputSha256']=digest
    elif mutation=='model-state':c['calculatorFinalState']['answer']='42'
    elif mutation=='file-bytes':e['filesystemEffects'][0]['afterSha256']='wrong'
    elif mutation=='file-path':e['filesystemEffects'][1]['path']='unrelated.txt'
    elif mutation=='authorization':c['externalInputRefusals'].pop()
    elif mutation=='scope':proof['fullSemanticRuntimeProven']=False
    result=verify(proof)
    assert result['status']=='refused'
    assert expected_reason in result['reasons']
    assert result['eligibleAppIds']==[]


def test_certificate_revalidated_instead_of_trusting_saved_pass(monkeypatch):
    proof=fixture_proof()
    monkeypatch.setattr(cert,'build_repository_provenance',lambda _:PROVENANCE)
    monkeypatch.setattr(cert,'current_bindings',lambda _:BINDINGS)
    certificate=cert.build_certificate(proof,Path('.'),now=NOW)
    assert cert.verify_certificate(certificate,Path('.'),now=NOW)['passed'] is True
    tampered=copy.deepcopy(certificate)
    tampered['proof']['applications'][1]['sourceBindings']['assetDigests']['portable.js']='changed'
    assert 'certificate-proof-hash-mismatch' in cert.verify_certificate(tampered,Path('.'),now=NOW)['reasons']
    tampered=copy.deepcopy(certificate)
    tampered['verification']['passed']=False
    assert 'certificate-verdict-mismatch' in cert.verify_certificate(tampered,Path('.'),now=NOW)['reasons']
    monkeypatch.setattr(cert,'build_repository_provenance',lambda _:{**PROVENANCE,'fingerprint':'new-source'})
    assert cert.verify_certificate(certificate,Path('.'),now=NOW)['passed'] is False


def audit_stub():
    return {'apps':[{'appId':a,'enforcement':{'blocking':False}} for a in ('calculator','code-editor','other')],
            'configuration':{},'summary':{'status':'pass','blockingReasonCount':0},
            'auditLevelReasons':[]}


def test_release_policy_requires_canonical_verified_certificate(tmp_path,monkeypatch):
    missing=audit.apply_portable_certification(audit_stub(),repo=tmp_path,
        certificate_path=Path('absent.json'),required=True,now=NOW.isoformat(),
        max_evidence_age_hours=168)
    assert missing['summary']['status']=='fail'
    assert missing['summary']['portablePromotionEligibleAppIds']==[]
    assert missing['auditLevelReasons'][0]['code']=='audit-portable-certification-not-proven'

    import json
    path=tmp_path/'canonical.json';path.write_text(json.dumps({'schema':cert.CERTIFICATE_SCHEMA}))
    monkeypatch.setattr(cert,'verify_certificate',lambda *args,**kwargs: {
        'status':'pass','passed':True,'reasons':[],
        'eligibleAppIds':['calculator','code-editor']})
    passed=audit.apply_portable_certification(audit_stub(),repo=tmp_path,
        certificate_path=path,required=True,now=NOW.isoformat(),
        max_evidence_age_hours=168)
    assert passed['summary']['status']=='pass'
    assert passed['summary']['portablePromotionEligibleAppIds']==['calculator','code-editor']
    assert passed['apps'][0]['portablePromotion']['sourceAuthorityModified'] is False
    assert 'portablePromotion' not in passed['apps'][2]


def test_audit_blocks_promotion_if_existing_truth_blocks_app(tmp_path,monkeypatch):
    path=tmp_path/'canonical.json';path.write_text('{}')
    monkeypatch.setattr(cert,'verify_certificate',lambda *args,**kwargs: {
        'status':'pass','passed':True,'reasons':[],
        'eligibleAppIds':['calculator','code-editor']})
    report=audit_stub();report['apps'][0]['enforcement']['blocking']=True
    audited=audit.apply_portable_certification(report,repo=tmp_path,
        certificate_path=path,required=True,now=NOW.isoformat(),max_evidence_age_hours=168)
    assert audited['summary']['status']=='fail'
    assert audited['summary']['portablePromotionEligibleAppIds']==['code-editor']


def test_generated_package_manifest_is_verified_byte_for_byte(tmp_path):
    from main_computer.mcel_standalone_package import build_standalone_package
    repo=Path(__file__).resolve().parents[1]
    manifest=build_standalone_package(repo,'calculator',tmp_path,portable=True)
    directory=tmp_path/'calculator'
    actual=cert.generated_binding(manifest,directory)
    assert actual['source']['packageFingerprint'].startswith('sha256:')
    assert actual['assetDigests']['portable.js']==cert.sha((directory/'portable.js').read_bytes())
    (directory/'portable.js').write_bytes(b'// tampered')
    with pytest.raises(ValueError,match='generated-asset-digest-mismatch'):
        cert.generated_binding(manifest,directory)
