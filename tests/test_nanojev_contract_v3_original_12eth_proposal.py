import copy
import json
from pathlib import Path
from tools import nanojev_contract_v3_specification as authority
from tools import nanojev_contract_v3_governance_language as language

ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT/'docs'/'nanojev'
SPEC=DOCS/'v3-authoritative-specification.v1.json'
HUMAN=DOCS/'v3-original-12eth-instance-PROPOSED.json'
STRICT=DOCS/'v3-original-12eth-compiler-input-PROPOSED.json'
MANIFEST=DOCS/'v3-original-12eth-manifest-PROPOSED.json'

def read(p):return json.loads(p.read_text(encoding='utf-8'))

def test_all_sixteen_parameters_are_populated_and_conform():
    record=read(HUMAN)
    assert authority.check_instance(read(SPEC),record)==[]
    assert [m['deadline_seconds'] for m in record['milestones']]==[14*86400,21*86400,28*86400]
    assert all(m['extension_seconds']==7*86400 and m['extension_decision_seconds']==2*86400 and m['max_extensions']==1 for m in record['milestones'])
    assert record['policy']['appointment_seconds'] < record['policy']['ruling_seconds']

def test_strict_language_validator_accepts_compiler_projection():
    spec=read(STRICT)
    assert language.validate(spec)==spec
    assert set(spec)==set(language.ROOT)|{'amount_wei','milestones','policy'}
    assert all(set(m)==language.MILESTONE_KEYS for m in spec['milestones'])
    assert set(spec['policy'])==language.POLICY_KEYS

def test_two_copies_agree_on_all_executable_fields():
    human=read(HUMAN)
    strict=read(STRICT)
    for k in ('language','asset','settlement_mode','payer','worker','arbitrator','amount_wei','policy'):
        assert strict[k]==human[k]
    for a,b in zip(strict['milestones'],human['milestones']):
        assert all(a[k]==b[k] for k in a)

def test_escrow_accounting_exact_and_three_distinct_milestones():
    spec=read(STRICT)
    assert spec['amount_wei']==12*10**18
    assert [m['amount_wei'] for m in spec['milestones']]==[3*10**18,4*10**18,5*10**18]
    assert len({m['id'] for m in spec['milestones']})==3
    assert sum(m['amount_wei'] for m in spec['milestones'])==spec['amount_wei']

def test_human_review_carries_acceptance_checklists_and_no_approvals():
    human=read(HUMAN)
    assert human['status']=='DRAFT_INSTANCE_PROPOSED_NOT_AUTHORIZED'
    assert all('checklist' in m['acceptance_criteria'] for m in human['milestones'])
    assert human['authorization']['owner'] is None
    assert human['authorization']['owner_approval_record_sha256'] is None
    assert not human['authorization']['payer_and_worker_approval_documents']
    assert human['authorization']['jurisdiction_or_external_terms'] is None

def test_manifest_pins_both_files_and_prohibits_promotion():
    from hashlib import sha256
    m=read(MANIFEST)
    assert m['proposal_sha256']==sha256(HUMAN.read_bytes()).hexdigest()
    assert m['compiler_input_sha256']==sha256(STRICT.read_bytes()).hexdigest()
    assert m['target_spec_sha256']==sha256(SPEC.read_bytes()).hexdigest()
    assert m['declared_parameters']==16
    assert m['deployment_authorized'] is False
    assert m['expert_gold_independence_verified'] is False
    assert m['owner_approved'] is False

def test_authority_gate_checks_instance_without_conferring_approval():
    result=authority.run(SPEC,instance_path=HUMAN)
    assert result['status']=='DRAFT_COMPLETE_OWNER_AUTHORIZATION_REQUIRED'
    assert result['instance_errors']==[] and result['errors']==[]
    assert result['owner_approved'] is False
    assert result['deployment_authorized'] is False

def test_wrong_milestone_value_is_rejected():
    record=read(HUMAN)
    record['milestones'][2]['amount_wei']-=1
    assert any('sum exactly' in e for e in authority.check_instance(read(SPEC),record))

def test_unsupported_silence_policy_rejected():
    spec=read(STRICT)
    spec['policy']['on_arbitrator_silence']='hold_indefinitely'
    try:
        language.validate(spec)
    except Exception:
        pass
    else:
        raise AssertionError('Unsupported silence outcome was accepted')

def test_metadata_must_not_be_passed_to_frozen_compiler():
    record=read(HUMAN)
    try:
        language.validate(record)
    except Exception:
        pass
    else:
        raise AssertionError('Human-review metadata was accepted as compiler source')
