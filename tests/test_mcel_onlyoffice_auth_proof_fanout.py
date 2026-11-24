"""Acceptance harness regression: no missing/skipped incumbent authentication tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json

import pytest

from tools.mcel_onlyoffice_auth_proof_fanout import (
    Outcome, PROOF_TEST, REQUIRED_CASES, ROOT, discover_cases, run_suite,
)


def test_discovery_requires_every_original_proof_security_case():
    found = discover_cases(ROOT / PROOF_TEST)
    assert set(found) == REQUIRED_CASES  # Snapshot has exactly these eight.


def test_discovery_rejects_truncated_suite(tmp_path):
    path = tmp_path / 'test_truncated.py'
    path.write_text('def test_other_windows_user_cannot_be_modeled_by_disposable_directory(): pass\n')
    with pytest.raises(RuntimeError, match='AUTH_PROOF_SUITE_INCOMPLETE'):
        discover_cases(path)


def test_all_cases_invoked_and_failure_blocks(tmp_path):
    calls = []
    def fake(name, *, root, env, deadline):
        calls.append(name)
        assert 'MAIN_COMPUTER_HARDENED_CANDIDATE' not in env
        return Outcome(name, 'FAIL' if 'wrong_key' in name else 'PASS', 0.01, 'evidence\n')
    report = tmp_path / 'report'
    verdict = run_suite(report_dir=report, workers=3, run_case=fake)
    assert len(calls) == 8 and set(calls) == REQUIRED_CASES
    assert verdict['status'] == 'BLOCKED'
    assert verdict['counts'] == {'PASS': 7, 'FAIL': 1, 'UNVERIFIED': 0}
    assert (report / 'test_wrong_key_and_corrupt_proof_deny_both_cookie_and_challenge.log').is_file()


def test_all_cases_pass_only_when_all_proofs_pass(tmp_path):
    def fake(name, *, root, env, deadline):
        return Outcome(name, 'PASS', 0.01, '1 passed\n')
    result = run_suite(report_dir=tmp_path / 'passed', run_case=fake)
    assert result['status'] == 'PASS'
    assert result['windows_acl_checks_stubbed'] is False
    assert result['counts']['PASS'] == 8
