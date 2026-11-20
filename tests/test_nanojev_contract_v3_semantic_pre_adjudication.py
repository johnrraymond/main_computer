"""Fail-closed semantic pre-adjudication, evidence-binding and honesty tests."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools import nanojev_contract_v3_semantic_pre_adjudication as draft
from tools import nanojev_contract_v3_independent_behavior as gate

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'runtime/reports/nanojev-contract-v3-governance-heldout-forge-0001.json'
GOLD=ROOT/'runtime/reports/nanojev-contract-v3-independent-gold.v1.json'
MUTATION=ROOT/'runtime/reports/nanojev-contract-v3-adversarial-mutations-0001.json'
if not MUTATION.exists():
    MUTATION=Path('/mnt/data/Pasted text(20261119-233737).txt')

@pytest.fixture(scope='module')
def real_evidence():
    # Packaging this test into the user's repository cannot depend on our
    # ephemeral tool filesystem. The stable integrity tests below use an
    # in-memory certified-shape fixture instead; this is optional integration.
    if not MUTATION.exists() or not REPORT.exists() or not GOLD.exists():pytest.skip('local integrated real evidence unavailable')
    return tuple(json.loads(p.read_text()) for p in (REPORT,GOLD,MUTATION))

def test_exact_fourteen_questions_and_no_generated_authority():
    assert len(draft.ANNOTATIONS)==len(draft.semantic.CLAIMS)==14
    assert set(draft.ANNOTATIONS)=={r[0] for r in draft.semantic.CLAIMS}
    assert all('Generated Solidity' not in item[0] for item in draft.ANNOTATIONS.values())

def test_real_campaign_full_draft_is_never_expert_certified(real_evidence):
    report,gold,mutation=real_evidence
    d=draft.make_dossier(report,gold,mutation)
    assert d['total_claims']==d['pending_external_decisions']==14
    assert d['status']==draft.STATUS
    assert d['expert_gold_independence_verified'] is False
    assert d['external_primary_terms_supplied'] is False
    assert {x['id'] for x in d['claims']}=={r[0] for r in draft.semantic.CLAIMS}
    assert all(x['certification_disposition']=='UNRESOLVED_REQUIRES_OUTSIDE_ADJUDICATION' for x in d['claims'])
    assert all(x['unresolved_ambiguity'] and x['rationale'] for x in d['claims'])
    assert d['mutation_report_sha256']==gate.digest_json(mutation)

def test_mutation_missing_or_invalid_cannot_produce_review_draft(real_evidence):
    r,g,m=real_evidence
    for delta in ({'killed_by_Forge_assertion':19},{'invalid_runs':['M01']},{'survived':['M01']},
                  {'frozen_inputs_reverified':False},{'pristine_probe_tests_passed':5},
                  {'status':'FAIL_MUTATION_CAMPAIGN_INCOMPLETE'},
                  {'expert_gold_independence_verified':True}):
        bad={**m,**delta}
        with pytest.raises(gate.CertificationFailure):draft.make_dossier(r,g,bad)

def test_nonassertion_kill_is_invalid(real_evidence):
    r,g,m=real_evidence
    bad=deepcopy(m)
    bad['mutations'][0]['execution']['outcome']='COMPILE_FAILED'
    with pytest.raises(gate.CertificationFailure):draft.make_dossier(r,g,bad)

def test_wrong_reference_or_gold_hash_is_invalid(real_evidence):
    r,g,m=real_evidence
    bad=deepcopy(m);bad['source_report_sha256']='0'*64
    with pytest.raises(gate.CertificationFailure):draft.make_dossier(r,g,bad)
    bad=deepcopy(m);bad['independent_gold_sha256']='0'*64
    with pytest.raises(gate.CertificationFailure):draft.make_dossier(r,g,bad)

def test_evidence_seal_files_and_review_status(real_evidence,tmp_path):
    r,g,m=real_evidence
    d=draft.make_dossier(r,g,m)
    paths=[]
    for n,value in [('report.json',r),('gold.json',g),('mutation.json',m)]:
        path=tmp_path/n;path.write_text(json.dumps(value));paths.append(path)
    seal=draft.make_seal(d,m,report_path=paths[0],gold_path=paths[1],mutation_path=paths[2],
                         baseline_path=None,evidence_lock_path=None)
    assert seal['mutants_killed_by_actual_assertion']==20
    assert seal['pristine_boundary_probes_passed']==6
    assert seal['expert_gold_independence_verified'] is False
    assert all(v['sha256_bytes']==gate.digest(paths[i].read_bytes()) for i,v in enumerate(seal['evidence'].values()))

def test_purpose_is_draft_not_external_review():
    assert 'EXTERNAL' in draft.STATUS
    assert 'UNAUTHENTICATED' in draft.ANNOTATIONS['R01'][0] or 'design brief' in draft.ANNOTATIONS['R01'][0]
    assert len([r for r in draft.semantic.CLAIMS if r[0] in draft.ANNOTATIONS])==14
