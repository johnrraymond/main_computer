"""P5.4a static deletion audit: no Hub operations or source mutation."""
from __future__ import annotations

import pytest

from tools.hub_legacy_deletion_inventory import inventory


@pytest.fixture(scope="module")
def report():
    return inventory()


def test_inventory_is_read_only_and_does_not_approve_deletion(report):
    assert report["ok"] is True
    assert report["read_only"] is True
    assert report["removal_approved"] == []
    assert report["parse_failures"] == []
    assert "no payment" in report["scope"]


def test_facade_still_owns_only_its_two_server_entrypoints(report):
    assert report["facade_locally_defined"] == ["serve_hub", "serve_hub_worker"]
    assert report["facade_is_entrypoint_only"]


def test_live_cli_still_imports_hub_compatibility_surface(report):
    callers = {x["symbol"]: x for x in report["historical_symbol_callers"]}
    assert "main_computer/cli.py" in callers["serve_hub"]["production_callers"]
    assert "main_computer/cli.py" in callers["register_worker_with_hub"]["production_callers"]
    assert "main_computer/hub_cli_live_session.py" in callers["HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID"]["production_callers"]


def test_legacy_worker_rest_still_has_harness_callers(report):
    markers = {x["route_family"]: x for x in report["legacy_protocol_source_references"]}
    for marker in (
        "legacy_worker_rest_register", "legacy_worker_rest_heartbeat",
        "legacy_worker_rest_poll", "legacy_worker_rest_results",
    ):
        assert markers[marker]["main_computer_files"]
        assert markers[marker]["scripts_and_tools"]


def test_dev_test_boundary_blocks_old_worker_rest_without_blocking_legacy_compatibility(report):
    assert report["dev_test_route_allowlist_present"]
    assert report["dev_test_legacy_worker_rest_still_blocked"]
    markers = {x["route_family"]: x for x in report["legacy_protocol_source_references"]}
    assert markers["dev_test_safe_chat_simulation"]["main_computer_files"]


def test_extracted_runtime_depends_on_direct_modules_not_legacy_facade(report):
    edges = {row["source"]: set(row["direct_imports"]) for row in report["important_module_dependencies"]}
    assert "main_computer.hub_http_legacy_handler" in edges["main_computer/exp_fdb_hub.py"]
    assert "main_computer.hub_http_runtime" in edges["main_computer/exp_fdb_hub.py"]
    assert "main_computer.hub" not in edges["main_computer/exp_fdb_hub.py"]
    assert "main_computer.hub_registry_json" in edges["main_computer/exp_fdb_hub_state.py"]


def test_scan_prunes_non_exported_nested_directories_before_reading_them(tmp_path):
    """An excluded local dependency tree must not affect audit content or scale."""
    from tools.hub_legacy_deletion_inventory import exported_python_sources

    for included in ("main_computer/hub.py", "tools/allowed.py", "tests/allowed.py", "scripts/allowed.py"):
        p = tmp_path / included
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("from main_computer.hub import serve_hub\n", encoding="utf-8")

    for excluded in (
        "tools/node_modules/pkg/poison.py",
        "tools/patching/generated/poison.py",
        "tools/.venv/Lib/site-packages/poison.py",
        "tools/example.egg-info/poison.py",
        "tools/documentation/plan-draft/poison.py",
        "tests/cache/poison.py",
        "tests/__pycache__/poison.py",
        "scripts/.pytest_cache/poison.py",
        "scripts/chat_console_shared_variables/poison.py",
        "main_computer/debug_assets/poison.py",
        "main_computer/.main_computer_browser_profile/poison.py",
        "main_computer/migration/poison.py",
    ):
        p = tmp_path / excluded
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("from main_computer.hub import NOT_AN_ACTUAL_EXPORT\n", encoding="utf-8")

    paths = {p.relative_to(tmp_path).as_posix() for p in exported_python_sources(tmp_path)}
    assert paths == {"main_computer/hub.py", "tools/allowed.py", "tests/allowed.py", "scripts/allowed.py"}


def test_unexported_legacy_protocol_references_do_not_enter_inventory(tmp_path):
    from tools.hub_legacy_deletion_inventory import REQUIRED_MODULES

    for module in REQUIRED_MODULES:
        p = tmp_path / "main_computer" / module
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# test module\n", encoding="utf-8")
    (tmp_path / "main_computer/hub.py").write_text(
        "def serve_hub(): pass\ndef serve_hub_worker(): pass\n", encoding="utf-8"
    )
    (tmp_path / "main_computer/dev_test_hub_legacy_routes.py").write_text(
        "# inherited_legacy_hub_route\n", encoding="utf-8"
    )
    allowed = tmp_path / "tools/allowed.py"
    allowed.parent.mkdir(parents=True, exist_ok=True)
    allowed.write_text("from main_computer.hub import serve_hub\n", encoding="utf-8")
    excluded = tmp_path / "tools/node_modules/dep/legacy.py"
    excluded.parent.mkdir(parents=True, exist_ok=True)
    excluded.write_text(
        "from main_computer.hub import NOT_EXPORTED\n"
        "ROUTE = '/api/hub/v1/workers/register'\n", encoding="utf-8"
    )
    report = inventory(tmp_path)
    assert report["ok"] is True
    assert report["files_scanned"] == len(REQUIRED_MODULES) + 1
    assert {row["symbol"] for row in report["historical_symbol_callers"]} == {"serve_hub"}
    marker = next(x for x in report["legacy_protocol_source_references"]
                  if x["route_family"] == "legacy_worker_rest_register")
    assert marker["scripts_and_tools"] == []
    assert marker["test_file_count"] == 0
    assert marker["main_computer_files"] == []
