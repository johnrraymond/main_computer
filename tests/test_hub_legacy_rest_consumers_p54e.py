"""P5.4e: bounded read-only audit, not endpoint decommission approval."""
from __future__ import annotations

from pathlib import Path

from tools.hub_legacy_rest_consumer_gate import SOURCE_ANCHORS, audit


def test_current_source_evidence_and_unknown_external_consumers():
    result = audit()
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["missing_evidence"] == []
    assert result["unclassified_source_references"] == []
    assert result["external_consumers_verified_absent"] is False
    assert result["migration_accepted"] is False
    assert result["deletion_approved"] == []


def test_worker_page_migrated_and_separate_legacy_clients_retained():
    result = audit()
    by_source = {r["source"]: r for r in result["references"]}
    assert "main_computer/viewport_routes_energy.py" not in by_source
    assert result["worker_page_direct_registration_present"] is False
    gates = {g["surface"]: g for g in result["retirement_gates"]}
    assert gates["worker_page_offer_registration"]["status"] == "migrated_authenticated"
    assert gates["legacy_hub_rest_server"]["status"] == "retain"


def test_dev_test_block_and_legacy_server_not_counted_as_clients():
    result = audit()
    by_source = {r["source"]: r for r in result["references"]}
    assert by_source["main_computer/exp_fdb_hub.py"]["role"] == "dev_test_explicitly_denied"
    assert by_source["main_computer/hub_http_legacy_handler.py"]["role"] == "legacy_hub_server_implementation"
    assert by_source["main_computer/hub.py"]["role"] == "legacy_hub_operator_help"
    assert result["dev_test_old_rest_routes_explicitly_blocked"] is True


def test_scheduler_and_operator_smoke_still_consume_legacy_rest():
    result = audit()
    by_source = {r["source"]: r for r in result["references"]}
    for name in (
        "tools/scheduler_lab/hub_client.py",
        "scripts/run_phase10_deterministic_worker_runtime_contract_smoke.py",
        "scripts/run_bridge_escrow_withdrawal_reconciliation_smoke.py",
    ):
        assert by_source[name]["route_operations"] == ["heartbeat", "poll", "register", "results"]
        assert by_source[name]["role"].endswith("_client")
    assert by_source["scripts/smoke_hub_network_client.py"]["route_operations"] == ["register"]


def test_all_retirement_gates_require_independent_evidence():
    result = audit()
    gates = {g["surface"]: g for g in result["retirement_gates"]}
    assert len(gates) == 8
    assert all(g["requires"] for g in gates.values())
    assert gates["dev_test_live_worker_sessions"]["status"] == "protected_operational"
    assert gates["captain_data_opt_in_rest_client"]["status"] == "retain_explicit_opt_in"
    assert result["deletion_approved"] == []


def test_fails_closed_on_missing_source_root(tmp_path):
    result = audit(tmp_path)
    assert result["ok"] is False
    assert result["missing_evidence"]
    assert result["deletion_approved"] == []
    assert result["source_files_with_route_strings"] == 0


def test_new_source_route_literal_is_unclassified_not_ignored(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir()
    (folder / "future_worker_registration.py").write_text(
        "ENDPOINT = '/api/hub/v1/workers/register'\n", encoding="utf-8"
    )
    result = audit(tmp_path)
    assert result["ok"] is False
    assert "scripts/future_worker_registration.py" in result["unclassified_source_references"]
    assert result["deletion_approved"] == []


def test_scan_does_not_descend_into_export_excluded_trees(tmp_path):
    (tmp_path / "main_computer" / "node_modules").mkdir(parents=True)
    (tmp_path / "tools" / "patching").mkdir(parents=True)
    (tmp_path / "main_computer" / "node_modules" / "shadow.py").write_text(
        "'/api/hub/v1/workers/register'", encoding="utf-8"
    )
    (tmp_path / "tools" / "patching" / "shadow.py").write_text(
        "'/api/hub/v1/workers/results'", encoding="utf-8"
    )
    result = audit(tmp_path)
    assert result["references"] == []
    assert result["source_files_with_route_strings"] == 0


def test_source_anchors_are_targeted_not_repository_scan():
    assert "main_computer/viewport_routes_energy.py" in SOURCE_ANCHORS
    assert "docker-compose.dev.yml" in SOURCE_ANCHORS
    assert "main_computer/hub_admin_site.py" in SOURCE_ANCHORS
    assert "tools/scheduler_lab/hub_client.py" in SOURCE_ANCHORS
