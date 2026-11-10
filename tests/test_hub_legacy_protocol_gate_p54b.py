"""P5.4b source-only protocol-retirement decision gates."""
from __future__ import annotations

import pytest

from tools.hub_legacy_protocol_gate import SURFACES, assess


@pytest.fixture(scope="module")
def report():
    return assess()


def test_evidence_is_present_and_no_protocol_deletion_is_approved(report):
    assert report["ok"] is True
    assert report["missing_evidence"] == []
    assert report["removal_approved"] == []
    assert report["external_consumers_verified_absent"] is False
    assert report["source_only"] is True


def test_every_surface_has_explicit_retirement_condition(report):
    assert report["surface_count"] == len(SURFACES)
    assert all(s["retirement_requires"] and not s["deletion_authorized"] for s in report["surfaces"])


def test_legacy_worker_rest_is_denied_by_dev_test_but_still_has_callers(report):
    by_id = {s["surface"]: s for s in report["surfaces"]}
    assert by_id["legacy_worker_rest"]["classification"] == "blocked_on_dev_test_but_retained_elsewhere"
    assert "main_computer/hub_http_legacy_handler.py" in by_id["legacy_worker_rest"]["source_files"]
    assert "main_computer/exp_fdb_hub.py" in by_id["legacy_worker_rest"]["source_files"]
    assert by_id["legacy_worker_cli_and_pull"]["classification"] == "retained_cli_and_harness_contract"


def test_real_live_worker_transport_is_not_mistaken_for_legacy_rest(report):
    by_id = {s["surface"]: s for s in report["surfaces"]}
    live = by_id["authenticated_live_worker_session"]
    assert live["classification"] == "protected_operational_surface"
    assert set(live["source_files"]) == {"main_computer/exp_fdb_hub.py", "main_computer/stable_hub.py"}


def test_safe_chat_simulation_kept_distinct_from_paid_execution(report):
    by_id = {s["surface"]: s for s in report["surfaces"]}
    assert by_id["dev_test_safe_chat_simulation"]["classification"] == "retained_simulation_not_paid_execution"
    assert by_id["msk_funding_authorization"]["classification"] == "protected_authorization_surface"


def test_missing_source_evidence_fails_closed(tmp_path):
    result = assess(tmp_path)
    assert result["ok"] is False
    assert result["missing_evidence"]
    assert result["removal_approved"] == []
