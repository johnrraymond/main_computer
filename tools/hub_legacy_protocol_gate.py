"""Read-only P5.4b retirement gates for historical Hub protocols.

This is a source-evidence gate, *not* proof of absence of deployed clients.
Neither this tool nor its tests contact a Hub, launch Workers, or change state.

Usage: python -m tools.hub_legacy_protocol_gate
"""
from __future__ import annotations

import json
from pathlib import Path


# Every evidence marker is an exact source anchor, not a generic filename guess.
# 'retained' means an in-tree dependency remains. None of these outcomes
# authorize deletion or certify external callers absent.
SURFACES: tuple[dict[str, object], ...] = (
    {
        "id": "legacy_development_hub_cli",
        "status": "retained_cli_contract",
        "evidence": (
            ("main_computer/cli.py", 'sub.add_parser("hub",'),
            ("main_computer/cli.py", "validate_dev_test_config(config)"),
            ("main_computer/hub.py", "def serve_hub("),
        ),
        "retirement_requires": "Explicit CLI retirement decision, replacement operator workflow, and Dev/Test startup acceptance.",
    },
    {
        "id": "dev_test_hub_cli",
        "status": "protected_operational_surface",
        "evidence": (
            ("main_computer/cli.py", 'sub.add_parser("dev-test-hub",'),
            ("main_computer/exp_fdb_hub.py", "class ExperimentalFoundationDbHubServerHandler"),
        ),
        "retirement_requires": "Not a legacy deletion candidate; preserve default local Dev-Test startup.",
    },
    {
        "id": "legacy_worker_cli_and_pull",
        "status": "retained_cli_and_harness_contract",
        "evidence": (
            ("main_computer/cli.py", 'sub.add_parser("hub-worker",'),
            ("main_computer/cli.py", 'sub.add_parser("hub-register-worker",'),
            ("main_computer/hub_worker_client.py", "def serve_hub_worker_pull("),
            ("main_computer/hub_worker_client.py", "def register_worker_with_hub("),
        ),
        "retirement_requires": "Replace or intentionally retire CLI and captain/lab/smoke consumers, with an end-to-end Worker acceptance test.",
    },
    {
        "id": "legacy_worker_rest",
        "status": "blocked_on_dev_test_but_retained_elsewhere",
        "evidence": (
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/register"'),
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/heartbeat"'),
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/poll"'),
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/results"'),
            ("main_computer/exp_fdb_hub.py", "def _exp_legacy_worker_route("),
            ("main_computer/exp_fdb_hub.py", "Deprecated exp worker REST endpoint was removed"),
        ),
        "retirement_requires": "Migrate supported legacy Hub, worker-client, CLI, and smoke callers; prove no external consumers before removing endpoints.",
    },
    {
        "id": "authenticated_live_worker_session",
        "status": "protected_operational_surface",
        "evidence": (
            ("main_computer/exp_fdb_hub.py", 'path == "/api/hub/v1/workers/live-session"'),
            ("main_computer/stable_hub.py", 'path == "/api/hub/v1/workers/live-session"'),
        ),
        "retirement_requires": "Not a legacy deletion candidate; preserve production and Dev-Test live Worker sessions.",
    },
    {
        "id": "dev_test_encrypted_chat_sessions",
        "status": "retained_dev_test_compatibility",
        "evidence": (
            ("main_computer/dev_test_hub_legacy_routes.py", '"/api/hub/sessions/start"'),
            ("main_computer/dev_test_hub_legacy_routes.py", '"/api/hub/sessions/chat"'),
            ("main_computer/providers/hub.py", '"/api/hub/sessions/start"'),
            ("main_computer/providers/hub.py", '"/api/hub/sessions/chat"'),
        ),
        "retirement_requires": "Identify remaining provider consumers and provide an explicit Dev-Test replacement with encrypted-session regression tests.",
    },
    {
        "id": "dev_test_safe_chat_simulation",
        "status": "retained_simulation_not_paid_execution",
        "evidence": (
            ("main_computer/dev_test_hub_legacy_routes.py", '"/api/hub/remote-overflow/safe-chat"'),
            ("main_computer/providers/hub.py", "def remote_overflow_safe_chat("),
            ("main_computer/remote_overflow.py", '"execution_mode": "dev_test_simulation"'),
        ),
        "retirement_requires": "Decide whether to retain Dev-Test simulation; never replace it with an unverified paid request path.",
    },
    {
        "id": "msk_funding_authorization",
        "status": "protected_authorization_surface",
        "evidence": (
            ("main_computer/dev_test_hub_legacy_routes.py", '"/api/hub/v1/credits/multisession-keys/request"'),
            ("main_computer/dev_test_hub_legacy_routes.py", '"/api/hub/v1/credits/wallet-funding/complete"'),
            ("main_computer/stable_hub.py", '"/api/hub/v1/credits/multisession-keys/request"'),
        ),
        "retirement_requires": "Protected financial/authentication contracts; any migration requires separate security and settlement acceptance.",
    },
    {
        "id": "historical_hub_facade",
        "status": "retained_import_contract",
        "evidence": (
            ("main_computer/cli.py", "from main_computer.hub import"),
            ("main_computer/hub_cli_live_session.py", "from main_computer.hub import"),
            ("main_computer/hub.py", "def serve_hub("),
            ("main_computer/hub.py", "def serve_hub_worker("),
        ),
        "retirement_requires": "Migrate verified internal imports, preserve/explicitly deprecate documented public imports, and re-run P5.3 compatibility acceptance.",
    },
)


def assess(repo: Path | None = None) -> dict[str, object]:
    repo = Path(repo or Path(__file__).resolve().parents[1]).resolve()
    assessed: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []
    for surface in SURFACES:
        found: list[str] = []
        for relative, marker in surface["evidence"]:
            path = repo / relative
            if not path.is_file() or marker not in path.read_text(encoding="utf-8-sig"):
                missing.append({"surface": str(surface["id"]), "file": relative, "marker": marker})
            else:
                if relative not in found:
                    found.append(relative)
        assessed.append({
            "surface": surface["id"], "classification": surface["status"],
            "source_files": found, "retirement_requires": surface["retirement_requires"],
            "deletion_authorized": False,
        })
    return {
        "schema": "main-computer.p54b.legacy-protocol-retirement-gates.v1",
        "ok": not missing,
        "scope": "read-only source evidence; no Hub requests, Workers, FDB transactions, payment, or mutation",
        "source_only": True,
        "external_consumers_verified_absent": False,
        "removal_approved": [],
        "surface_count": len(assessed),
        "surfaces": assessed,
        "missing_evidence": missing,
        "limitations": [
            "Source anchors establish in-tree dependencies, not observed network traffic.",
            "External clients, deployments and plugin integrations have not been certified absent.",
            "Absence of a source marker is an audit failure, not permission to delete a protocol.",
        ],
    }


def main() -> int:
    report = assess()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
