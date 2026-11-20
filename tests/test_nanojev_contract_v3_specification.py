import copy
import json
from pathlib import Path
from tools import nanojev_contract_v3_specification as v

DOCS=Path(__file__).resolve().parents[1]/'docs'/'nanojev'
SPEC=DOCS/'v3-authoritative-specification.v1.json'
INSTANCE=DOCS/'v3-instance-authorization-TEMPLATE.json'


def s():return json.loads(SPEC.read_text(encoding='utf-8'))
def i():return json.loads(INSTANCE.read_text(encoding='utf-8'))

def test_spec_has_all_14_distinct_unratified():
    assert v.verify_spec(s())==[]
    assert len({r['domain'] for r in s()['normative_decisions']})==14
    assert all(r['decision_status']=='PROPOSED_REQUIRES_OWNER_APPROVAL' for r in s()['normative_decisions'])

def test_original_intent_kept_separate():
    data=s()
    assert [b['id'] for b in data['recorded_user_design_intent']] == [f'B{k:02d}' for k in range(1,9)]
    assert data['adjudication_requirements']['external_expert_verified'] is False

def test_approval_template_cannot_self_certify():
    data=s();a=v.approval_template(data,v.sha(SPEC))
    assert len(a['decisions'])==14
    assert v.check_approval(data,v.sha(SPEC),a)

def test_forged_approval_hash_denied():
    data=s();a=v.approval_template(data,'0'*64)
    assert 'SHA-256' in ' '.join(v.check_approval(data,v.sha(SPEC),a))

def test_partial_approval_denied():
    data=s();a=v.approval_template(data,v.sha(SPEC))
    a.update(owner_name='x',owner_authority_basis='project owner',approved_at_utc='2026-11-19T00:00:00Z',declaration='I approve every R01-R14 clause in this exact NanoJev V3 specification version as the project requirements owner.')
    assert any('R01:' in e for e in v.check_approval(data,v.sha(SPEC),a))

def test_self_attestation_not_expert_approval(tmp_path):
    data=s();a=v.approval_template(data,v.sha(SPEC))
    a.update(owner_name='owner',owner_authority_basis='owner-supplied authorization',approved_at_utc='2026-11-19T00:00:00Z',declaration='I approve every R01-R14 clause in this exact NanoJev V3 specification version as the project requirements owner.')
    for d in a['decisions']:
        d.update(decision='APPROVE',authority_reference='project decision',rationale='Owner explicitly approves this clause')
    ap=tmp_path/'approval.json';ap.write_text(json.dumps(a))
    result=v.run(SPEC,approval_path=ap)
    assert result['owner_approved'] is True
    assert result['expert_gold_independence_verified'] is False
    assert result['deployment_authorized'] is False

def test_mismatched_review_question_fails():
    data=s()
    r={'claims':[{'id':d['id'],'question':d['question'],'certification_disposition':'UNRESOLVED_REQUIRES_OUTSIDE_ADJUDICATION'} for d in data['normative_decisions']], 'pending_external_decisions':14,'expert_gold_independence_verified':False,'source_report_sha256':data['provenance']['source_report_sha256']}
    assert v.check_review(data,r)==[]
    r['claims'][4]['question']='Edited question'
    assert 'R05:' in ' '.join(v.check_review(data,r))

def test_incomplete_instance_rejected():
    errors=v.check_instance(s(),i())
    assert len(errors)>5
    assert any('on_review_silence' in e for e in errors)
    assert any('max_extensions' in e for e in errors)

def full_instance():
    x=i()
    for m in x['milestones']:
        m.update(deadline_seconds=86400,extension_decision_seconds=3600,max_extensions=1)
    x['policy'].update(review_seconds=86400,dispute_filing_seconds=86400,appointment_seconds=86400,ruling_seconds=172800,
                       on_review_silence='refund_payer',on_appointment_failure='refund_payer',on_arbitrator_silence='pay_worker')
    return x

def test_complete_instance_structurally_conforms_not_authorized():
    assert v.check_instance(s(),full_instance())==[]

def test_changed_amount_rejected():
    x=full_instance();x['milestones'][0]['amount_wei']+=1
    assert any('sum exactly' in e for e in v.check_instance(s(),x))

def test_bad_policy_rejected():
    x=full_instance();x['policy']['on_review_silence']='unspecified'
    assert any('on_review_silence' in e for e in v.check_instance(s(),x))

def test_wrong_extension_bounds_rejected():
    x=full_instance();x['milestones'][0]['extension_decision_seconds']=604800
    assert any('shorter than extension' in e for e in v.check_instance(s(),x))

def test_draft_run_creates_no_approval(tmp_path):
    output=tmp_path/'report.json';template=tmp_path/'approval.json'
    result=v.run(SPEC,instance_path=INSTANCE,template_path=template,out_path=output)
    assert result['status']=='DRAFT_INSTANCE_INCOMPLETE_OR_NONCONFORMING'
    assert result['owner_approved'] is False
    assert output.exists() and template.exists()
    assert json.loads(template.read_text())['status']=='OWNER_APPROVAL_NOT_PROVIDED'

def test_tamper_review_source_rejected():
    data=s();data['status']='OWNER_APPROVED'
    assert v.verify_spec(data)


def test_report_digest_uses_original_frozen_v3_canonical_json_convention(tmp_path):
    # The frozen V3 evidence pipeline uses json.dumps defaults for ASCII escaping.
    import hashlib
    doc = {'z': {'value': 'café'}, 'a': [1, True, None]}
    file = tmp_path / 'report.json'
    file.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    expected = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert v.canonical_report_sha(file) == expected
    assert v.sha(file) != expected


def test_report_whitespace_and_line_endings_do_not_invalidate_evidence(tmp_path):
    doc = {'z': {'label': 'arbitration'}, 'a': [1, 2, 3]}
    raw = tmp_path / 'raw.json'
    reformatted = tmp_path / 'reformatted.json'
    raw.write_bytes((json.dumps(doc, indent=2) + '\n').replace('\n', '\r\n').encode())
    reformatted.write_text(json.dumps(doc, separators=(',', ':'), sort_keys=True) + '\n', encoding='utf-8')
    assert v.sha(raw) != v.sha(reformatted)
    spec = s()
    spec['provenance']['source_report_sha256'] = v.canonical_report_sha(raw)
    specfile = tmp_path / 'spec.json'
    specfile.write_text(json.dumps(spec), encoding='utf-8')
    for report_path in (raw, reformatted):
        result = v.run(specfile, report_path=report_path)
        assert result['status'] == 'DRAFT_COMPLETE_OWNER_AUTHORIZATION_REQUIRED'
        assert result['errors'] == []
        assert result['source_report_canonical_sha256'] == spec['provenance']['source_report_sha256']
        assert result['source_report_digest_method'] == 'canonical_sorted_ascii_json_v1'


def test_report_semantic_tampering_still_refused(tmp_path):
    doc = {'passed_forge_tests': 203, 'status': 'PASS'}
    report = tmp_path / 'report.json'
    report.write_text(json.dumps(doc), encoding='utf-8')
    spec = s()
    spec['provenance']['source_report_sha256'] = v.canonical_report_sha(report)
    specfile = tmp_path / 'spec.json'
    specfile.write_text(json.dumps(spec), encoding='utf-8')
    assert v.run(specfile, report_path=report)['errors'] == []
    report.write_text(json.dumps({'passed_forge_tests': 204, 'status': 'PASS'}), encoding='utf-8')
    result = v.run(specfile, report_path=report)
    assert result['status'] == 'FAIL_SPECIFICATION_INTEGRITY'
    assert 'canonical JSON digest differs' in ' '.join(result['errors'])
    assert result['deployment_authorized'] is False


def test_spec_approval_uses_raw_bytes_not_report_canonical_hash(tmp_path):
    data = s()
    original = tmp_path / 'spec.json'
    changed_formatting = tmp_path / 'reformatted_spec.json'
    original.write_text(json.dumps(data, indent=2), encoding='utf-8')
    changed_formatting.write_text(json.dumps(data, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    assert v.verify_spec(v.load(original)) == []
    assert v.verify_spec(v.load(changed_formatting)) == []
    assert v.sha(original) != v.sha(changed_formatting)
