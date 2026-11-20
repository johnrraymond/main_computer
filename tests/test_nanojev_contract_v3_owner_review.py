import copy
import json
import shutil
from pathlib import Path
import pytest
from tools import nanojev_contract_v3_owner_review as m

HERE=Path(__file__).resolve().parents[1]
SPEC=HERE/'docs'/'nanojev'/'v3-authoritative-specification.v1.json'
PROPOSAL=HERE/'docs'/'nanojev'

@pytest.fixture
def project(tmp_path):
    docs=tmp_path/'docs'/'nanojev';docs.mkdir(parents=True)
    shutil.copy2(SPEC,docs/m.SPEC)
    for p in (m.INSTANCE,m.COMPILER,m.MANIFEST):
        shutil.copy2(PROPOSAL/p, docs/p)
    for p in (m.REVIEW,m.ACCEPTANCE,m.RATIFICATION):
        shutil.copy2(HERE/'docs'/'nanojev'/p,docs/p)
    return tmp_path

def test_pending_without_approval(project):
    o=m.assess(project)
    assert o['status']=='OWNER_REVIEW_READY_RATIFICATION_PENDING'
    assert o['clauses_reviewed']==14 and o['parameters_reviewed']==16
    assert o['deployment_authorized'] is False
    assert len(o['blockers'])==2

def test_template_stays_unapproved(project):
    docs=project/'docs'/'nanojev'
    o=m.assess(project,docs/m.RATIFICATION,docs/m.ACCEPTANCE)
    assert o['status']=='OWNER_REVIEW_READY_RATIFICATION_PENDING'
    assert len(o['blockers'])>20
    assert o['owner_approved'] is False

def test_refuse_mutated_spec(project):
    docs=project/'docs'/'nanojev';p=docs/m.SPEC
    p.write_bytes(p.read_bytes()+b'\n')
    assert m.assess(project)['status']=='FAIL_OWNER_REVIEW_INPUT_INTEGRITY'

def test_refuse_mutated_amount(project):
    docs=project/'docs'/'nanojev';p=docs/m.INSTANCE
    raw=json.loads(p.read_text());raw['amount_wei']+=1
    p.write_text(json.dumps(raw))
    assert m.assess(project)['status']=='FAIL_OWNER_REVIEW_INPUT_INTEGRITY'

def test_refuse_changed_parameter_review(project):
    docs=project/'docs'/'nanojev';p=docs/m.REVIEW
    raw=json.loads(p.read_text());raw['instance_parameter_reviews'][0]['proposed_value']=3
    p.write_text(json.dumps(raw))
    assert m.assess(project)['status']=='FAIL_OWNER_REVIEW_INPUT_INTEGRITY'

def test_refuse_authorized_claim_in_review(project):
    docs=project/'docs'/'nanojev';p=docs/m.REVIEW
    raw=json.loads(p.read_text());raw['owner_approved']=True
    p.write_text(json.dumps(raw))
    assert m.assess(project)['status']=='FAIL_OWNER_REVIEW_INPUT_INTEGRITY'

def test_refuse_ratification_without_risk_consent(project):
    docs=project/'docs'/'nanojev';p=docs/m.RATIFICATION
    rat=json.loads(p.read_text());rat['owner_name']='Owner'
    p.write_text(json.dumps(rat))
    o=m.assess(project,p,docs/m.ACCEPTANCE)
    assert any('Risk not expressly accepted:' in x for x in o['blockers'])
    assert o['deployment_authorized'] is False

def test_refuse_mismatching_ratification_hash(project):
    docs=project/'docs'/'nanojev';p=docs/m.RATIFICATION
    rat=json.loads(p.read_text());rat['instance_sha256']='0'*64
    p.write_text(json.dumps(rat))
    o=m.assess(project,p,docs/m.ACCEPTANCE)
    assert any('instance_sha256' in x for x in o['blockers'])

def test_refuse_bogus_acceptance_without_contract_agreement(project):
    docs=project/'docs'/'nanojev';p=docs/m.ACCEPTANCE
    sche=json.loads(p.read_text())
    assert len(m.check_acceptance(sche,json.loads((docs/m.INSTANCE).read_text())))>20

def test_reject_rule_hash_change(project):
    docs=project/'docs'/'nanojev';p=docs/m.REVIEW
    raw=json.loads(p.read_text());raw['normative_clause_reviews'][0]['proposed_rule_sha256']='0'*64
    p.write_text(json.dumps(raw))
    assert m.assess(project)['status']=='FAIL_OWNER_REVIEW_INPUT_INTEGRITY'

def test_requires_every_policy_parameter(project):
    docs=project/'docs'/'nanojev'
    inst=json.loads((docs/m.INSTANCE).read_text())
    pairs=m.parameters(inst)
    assert len(pairs)==16
    assert pairs[-1]==('policy.on_arbitrator_silence','refund_payer')

def test_exact_declaration_not_in_template(project):
    docs=project/'docs'/'nanojev'
    rat=json.loads((docs/m.RATIFICATION).read_text())
    assert rat['owner_declaration'] is None
    assert rat['owner_declaration']!=m.APPROVAL_DECLARATION


def test_structurally_complete_self_attestation_never_authorizes_deploy(project):
    docs=project/'docs'/'nanojev'
    s=docs/m.ACCEPTANCE;r=docs/m.RATIFICATION
    acceptance=json.loads(s.read_text())
    h='a'*64
    acceptance['status']='ACCEPTANCE_SCHEDULE_SELF_ATTESTED'
    for line in acceptance['milestones']:
        for k in ('deliverable_scope_sha256','acceptance_test_plan_sha256',
                  'build_and_runtime_environment_sha256'):
            line[k]=h
        for k in ('deliverable_scope_reference','acceptance_test_plan_reference',
                  'acceptance_thresholds','submission_evidence_requirements',
                  'permitted_rejection_grounds',
                  'payer_acceptance_acknowledgement','worker_acceptance_acknowledgement'):
            line[k]='human-supplied-test-value'
    party=acceptance['party_contract']
    party['chain_id']=1
    for k in ('jurisdiction_or_governing_terms_sha256','payer_agreement_sha256','worker_agreement_sha256'):
        party[k]=h
    for k in ('payer_address','worker_address','block_timestamp_tolerance_agreed'):
        party[k]='human-supplied-test-value'
    acceptance['approved']=True
    s.write_text(json.dumps(acceptance))
    rat=json.loads(r.read_text())
    rat.update(owner_name='HumanTest',owner_authority_basis='test',approved_at_utc='2026-11-19T00:00:00Z',
               owner_declaration=m.APPROVAL_DECLARATION,acceptance_schedule_sha256=m.sha(s),approval_document_sha256=h)
    for d in rat['normative_clause_decisions']:
        d.update(decision='APPROVE',authority_reference='test',rationale='test')
    for d in rat['instance_parameter_decisions']:
        d.update(decision='APPROVE',rationale='test')
    for k in m.RISKS:
        rat['explicit_economic_risk_acknowledgements'][k]=True
    r.write_text(json.dumps(rat))
    out=m.assess(project,r,s)
    assert out['status']=='OWNER_RATIFICATION_SELF_ATTESTED_NOT_PARTY_OR_EXPERT_CERTIFIED'
    assert out['deployment_authorized'] is False
    assert out['expert_gold_independence_verified'] is False
