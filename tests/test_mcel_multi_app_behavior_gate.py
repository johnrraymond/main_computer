"""The aggregate report must never hide skipped browser tests or bad authority."""
from __future__ import annotations

import subprocess

import pytest

from tools.mcel_phase1_multi_app_behavior import _json_pass, _require_pytest_proof


def _completed(output: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["pytest"], code, stdout=output, stderr="")


def test_browser_suite_must_have_thirteen_executed_tests_and_zero_skips():
    assert _require_pytest_proof(_completed("13 passed in 8.3s")) == 13
    with pytest.raises(ValueError, match="did not execute"):
        _require_pytest_proof(_completed("13 passed, 1 skipped in 8.3s"))
    with pytest.raises(ValueError, match="fewer than thirteen"):
        _require_pytest_proof(_completed("12 passed in 8.3s"))
    with pytest.raises(ValueError, match="failed"):
        _require_pytest_proof(_completed("4 failed, 3 passed", 1))


def test_authority_must_report_matching_app_identity_and_pass():
    import json
    valid = _completed(json.dumps({"appId": "code-editor", "status": "pass"}))
    assert _json_pass(valid, "code-editor")["status"] == "pass"
    with pytest.raises(ValueError, match="mismatched"):
        _json_pass(valid, "calculator")
    with pytest.raises(ValueError, match="exited"):
        _json_pass(_completed("{}", 1), "code-editor")
