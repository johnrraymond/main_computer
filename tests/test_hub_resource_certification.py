"""Patch 2F must refuse any premature FULL PASS."""
import hashlib
import json
from pathlib import Path
from main_computer.hub_resource_limits import load_hub_resource_policy
from tools.hub_resource_lab.certify import (
    MANDATORY_LOCAL, MANDATORY_FULL, PINNED_FILES,
    acceptance_status, source_fingerprints, run_certification,
)


def test_required_security_gates_fail_closed():
    assert acceptance_status({}) == 'INCOMPLETE'
    assert acceptance_status({g: {'status': 'PASS'} for g in MANDATORY_LOCAL}) == 'INCOMPLETE'
    assert acceptance_status({g: {'status': 'PASS'} for g in MANDATORY_FULL}) == 'PASS'
    gates = {g: {'status': 'PASS'} for g in MANDATORY_FULL}
    gates['R6_DURABLE_CRASH_FAILOVER']['status'] = 'FAIL'
    assert acceptance_status(gates) == 'FAIL'


def test_fingerprints_bind_exact_runtime_source():
    pins = source_fingerprints()
    assert set(pins) == set(PINNED_FILES)
    assert all(len(digest) == 64 for digest in pins.values())
    assert pins['main_computer/hub_resource_limits.py'] == hashlib.sha256(
        Path('main_computer/hub_resource_limits.py').read_bytes()).hexdigest()


def test_preflight_does_not_run_children_or_certify(tmp_path):
    result = run_certification(mode='preflight', report_dir=tmp_path, cluster_file=tmp_path/'missing.cluster')
    assert result['status'] == 'PLANNED_ONLY'
    assert result['full_promotion_allowed'] is False
    assert result['gates']['R6_DURABLE_CRASH_FAILOVER']['status'] == 'INCOMPLETE'
    assert result['gates']['DEPLOYED_REVISION']['status'] == 'INCOMPLETE'
    assert json.loads((tmp_path / 'certification.json').read_text())['status'] == 'PLANNED_ONLY'


def test_local_result_never_promotes_without_durable_and_deployed_evidence():
    gates = {name: {'status': 'PASS'} for name in MANDATORY_LOCAL}
    gates['R6_DURABLE_CRASH_FAILOVER'] = {'status': 'INCOMPLETE'}
    gates['DEPLOYED_REVISION'] = {'status': 'INCOMPLETE'}
    assert acceptance_status(gates, full=False) == 'PASS'
    assert acceptance_status(gates, full=True) == 'INCOMPLETE'


def test_failed_lab_gate_includes_exact_assertions(tmp_path):
    from tools.hub_resource_lab.certify import _run_stage
    import sys
    script = (
        "import json,sys;"
        "open(sys.argv[1], 'w').write(json.dumps("
        "{'result':'FAIL','passed':0,'total':1,'tests':["
        "{'hub':'stable','name':'capacity','expected':503,'observed':None,'passed':False}]}));"
        "sys.exit(1)"
    )
    evidence = tmp_path / 'admission.json'
    result = _run_stage('R3_R4_ADMISSION', [sys.executable, '-c', script, str(evidence)],
                        tmp_path / 'admission.log', 10,
                        evidence=evidence, json_evidence=True)
    assert result['status'] == 'FAIL'
    assert result['reason'] == 'LAB_ASSERTIONS_FAILED'
    assert result['checks'] == 0 and result['total'] == 1
    assert result['failed_assertions'][0]['observed'] is None
    assert result['failed_assertions'][0]['name'] == 'capacity'
