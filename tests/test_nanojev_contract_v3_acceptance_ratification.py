import copy
import json
import shutil
from pathlib import Path

import pytest

from tools import nanojev_contract_v3_acceptance_ratification as draft
from tools import nanojev_contract_v3_owner_review as owner

HERE = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path):
    source = HERE / 'docs' / 'nanojev'
    target = tmp_path / 'docs' / 'nanojev'
    target.mkdir(parents=True)
    for key in draft.FILES.values():
        shutil.copy2(source / key, target / key)
    for key in (owner.SPEC, owner.INSTANCE, owner.COMPILER, owner.MANIFEST, owner.REVIEW):
        shutil.copy2(source / key, target / key)
    return tmp_path


def update(root, file, callback):
    p = root / 'docs' / 'nanojev' / file
    obj = json.loads(p.read_text())
    callback(obj)
    p.write_text(json.dumps(obj, indent=2) + '\n')


def test_draft_passes_but_cannot_authorize(root):
    x = draft.inspect(root)
    assert x['status'] == 'PASS_ACCEPTANCE_SCHEDULE_AND_RATIFICATION_DRAFT_NOT_AUTHORIZED', x['errors']
    assert x['errors'] == []
    assert x['milestones'] == 3 and x['planned_mandatory_tests'] == 15
    assert x['normative_decisions_prepared'] == 14
    assert x['parameter_decisions_prepared'] == 16
    assert x['owner_review_status'] == 'OWNER_REVIEW_READY_RATIFICATION_PENDING'
    assert not x['owner_approved'] and not x['party_agreed'] and not x['deployment_authorized']


def test_existing_owner_gate_rejects_unapproved_schedule(root):
    d = root / 'docs' / 'nanojev'
    o = owner.assess(root, d / draft.FILES['ratification'], d / draft.FILES['schedule'])
    assert o['status'] == 'OWNER_REVIEW_READY_RATIFICATION_PENDING'
    assert o['owner_approved'] is False and o['deployment_authorized'] is False
    assert any('not expressly approved' in b for b in o['blockers'])


def test_risk_acknowledgements_not_populated(root):
    rat = json.loads((root/'docs'/'nanojev'/draft.FILES['ratification']).read_text())
    assert rat['explicit_economic_risk_acknowledgements'] == {r: False for r in owner.RISKS}


def test_detects_scope_mutation(root):
    update(root, draft.FILES['scope'], lambda x: x.update(project_title='Changed'))
    assert any('digest mismatch' in x or 'attachment hash mismatch' in x
               for x in draft.inspect(root)['errors'])


def test_detects_test_plan_mutation(root):
    update(root, draft.FILES['plan'], lambda x: x['milestones'][0]['tests'][0].update(mandatory=False))
    assert draft.inspect(root)['status'].startswith('FAIL_')


def test_detects_environment_mutation(root):
    update(root, draft.FILES['environment'], lambda x: x['runtime'].update(operating_system='Windows'))
    assert draft.inspect(root)['status'].startswith('FAIL_')


def test_detects_schedule_mutation(root):
    update(root, draft.FILES['schedule'], lambda x: x['milestones'][0]['proposed_technical_thresholds'].update(mandatory_pass_percentage=0))
    assert draft.inspect(root)['status'].startswith('FAIL_')


def test_detects_ratification_approval_spoof(root):
    update(root, draft.FILES['ratification'], lambda x: x.update(owner_declaration=owner.APPROVAL_DECLARATION))
    assert any('falsely asserts approval' in x for x in draft.inspect(root)['errors'])


def test_detects_clause_mismatch(root):
    update(root, draft.FILES['ratification'], lambda x: x['normative_clause_decisions'][0].update(proposed_rule_sha256='0'*64))
    assert any('ratification clause' in x for x in draft.inspect(root)['errors'])


def test_detects_parameter_mismatch(root):
    update(root, draft.FILES['ratification'], lambda x: x['instance_parameter_decisions'][0].update(proposed_value=123))
    assert any('parameter approval' in x for x in draft.inspect(root)['errors'])


def test_refuses_fabricated_scope_agreement(root):
    update(root, draft.FILES['scope'], lambda x: x['milestones'][0].update(release_scope_agreed=True))
    assert any('unsupported approval' in x for x in draft.inspect(root)['errors'])


def test_refuses_missing_milestone_test(root):
    update(root, draft.FILES['plan'], lambda x: x['milestones'][2]['tests'].pop())
    assert any('test IDs changed' in x for x in draft.inspect(root)['errors'])


def test_refuses_mutated_original_instance(root):
    update(root, owner.INSTANCE, lambda x: x.update(amount_wei=10**18))
    assert draft.inspect(root)['status'].startswith('FAIL_')


def test_refuses_missing_attached_artifact(root):
    (root/'docs'/'nanojev'/draft.FILES['scope']).unlink()
    assert draft.inspect(root)['status'].startswith('FAIL_')


def test_all_tests_have_clear_actions_and_outcomes(root):
    p = json.loads((root/'docs'/'nanojev'/draft.FILES['plan']).read_text())
    test_ids = [t['id'] for stage in p['milestones'] for t in stage['tests']]
    assert len(set(test_ids)) == 15
    assert all(t['procedure'] and t['observable_pass_condition'] and t['mandatory']
               for stage in p['milestones'] for t in stage['tests'])
