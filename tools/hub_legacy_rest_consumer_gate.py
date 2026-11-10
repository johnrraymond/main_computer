"""P5.4e: bounded, read-only census of remaining legacy Worker REST consumers.

    python -m tools.hub_legacy_rest_consumer_gate

The census enumerates explicit source roots, never the runtime tree, export
exclusions, installed packages or remote services.  A source reference is not
proof of a live caller; an absent reference is not proof that external clients
are absent. No deletion authority is granted by this tool.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROUTE = re.compile(r"/api/hub(?:/v1)?/workers/(register|heartbeat|poll|results)\b")
ROUTE_NAMES = ("register", "heartbeat", "poll", "results")

# The classification is explicit: new source callers must be reviewed before
# any retirement decision. Text references to denied routes and documentation
# cannot be counted as active Worker REST consumers.
SOURCE_ROLES = {
    "main_computer/hub.py": "legacy_hub_operator_help",
    "main_computer/hub_http_legacy_handler.py": "legacy_hub_server_implementation",
    "main_computer/exp_fdb_hub.py": "dev_test_explicitly_denied",
    "main_computer/hub_worker_client.py": "opt_in_legacy_rest_client",
    "main_computer/hub_admin_site.py": "legacy_hub_admin_ui",
    "main_computer/node_behavior_chaos.py": "chaos_lab_client",
    "main_computer/temporal_fdb_hub_multi_hub_smoke.py": "temporal_lab_client",
    "main_computer/temporal_fdb_hub_node_market_smoke.py": "temporal_lab_client",
    "main_computer/temporal_fdb_hub_stress_smoke.py": "temporal_lab_client",
    "scripts/run_bridge_escrow_paid_mock_spend_smoke.py": "operator_smoke_client",
    "scripts/run_bridge_escrow_withdrawal_reconciliation_smoke.py": "operator_smoke_client",
    "scripts/run_paid_mock_worker_smoke.py": "operator_smoke_client",
    "scripts/run_phase10_deterministic_worker_runtime_contract_smoke.py": "operator_smoke_client",
    "scripts/run_phase9_market_backed_paid_ai_request_smoke.py": "operator_smoke_client",
    "scripts/smoke_hub_network_client.py": "operator_smoke_client",
    "tools/scheduler_lab/hub_client.py": "scheduler_lab_client",
}

# Substantive evidence beyond a route string. A no-longer-present entry fails
# closed; it is not a signal that the endpoint may be retired.
SOURCE_ANCHORS: dict[str, tuple[str, ...]] = {
    "main_computer/cli.py": (
        'def cmd_hub_worker(', 'def cmd_hub_register_worker(',
        'sub.add_parser("hub-worker"', 'sub.add_parser("hub-register-worker"',
        '--allow-legacy-worker-rest',
    ),
    "docker-compose.dev.yml": (
        'hub-worker:', 'profiles: ["worker"]', 'main_computer.cli',
    ),
    "main_computer/hub_worker_http.py": ('class HubWorkerHttpServer(',),
    "main_computer/hub.py": ('def serve_hub(', 'def serve_hub_worker('),
    "main_computer/hub_worker_client.py": (
        'def register_worker_with_hub(', 'def serve_hub_worker_pull(',
        'if not allow_legacy_worker_rest:',
    ),
    "main_computer/hub_cli_live_session.py": (
        'def hub_uses_live_sessions(',
    ),
    "main_computer/viewport_routes_energy.py": (
        'def _handle_worker_offer_sync(',
        'def _worker_offer_sync_pending(',
        'def _post_worker_start_to_hub(',
        'def _handle_worker_network_work_now(',
        'does not match selected network',
    ),
    "main_computer/viewport_route_dispatch.py": (
        '"/api/applications/worker/offer-sync"',
    ),
    "main_computer/hub_admin_site.py": (
        "$('registerForm').addEventListener", 
    ),
    "main_computer/hub_http_legacy_handler.py": (
        '"/api/hub/v1/workers/register"', '"/api/hub/v1/workers/results"',
    ),
    "main_computer/exp_fdb_hub.py": (
        '"/api/hub/v1/workers/register"',
    ),
    "tools/scheduler_lab/hub_client.py": (
        'def register_worker(', '/api/hub/v1/workers/heartbeat',
        '/api/hub/v1/workers/poll', '/api/hub/v1/workers/results',
    ),
    "scripts/run_phase10_deterministic_worker_runtime_contract_smoke.py": (
        '/api/hub/v1/workers/register', '/api/hub/v1/workers/heartbeat',
        '/api/hub/v1/workers/poll', '/api/hub/v1/workers/results',
    ),
    "scripts/run_bridge_escrow_withdrawal_reconciliation_smoke.py": (
        '/api/hub/v1/workers/register', '/api/hub/v1/workers/heartbeat',
        '/api/hub/v1/workers/poll', '/api/hub/v1/workers/results',
    ),
    "scripts/run_phase9_market_backed_paid_ai_request_smoke.py": (
        '/api/hub/v1/workers/register', '/api/hub/v1/workers/poll',
        '/api/hub/v1/workers/results',
    ),
}

# These entry points are a protected baseline while retirement is undecided.
MUST_RETAIN = (
    "legacy_http_worker_server_and_cli",
    "explicit_worker_registration_cli",
    "captain_data_opt_in_rest_client",
    "worker_page_offer_registration",
    "legacy_hub_admin_registration",
    "scheduler_lab_and_smoke_consumers",
    "legacy_hub_rest_server",
    "dev_test_live_worker_sessions",
)


def _source_files(root: Path):
    """Bounded export-respecting globs; never recurse into arbitrary subtrees."""
    for folder in ("main_computer", "scripts", "tools/scheduler_lab"):
        base = root / folder
        if base.is_dir():
            yield from sorted(base.glob("*.py"))


def audit(repo: Path | None = None) -> dict[str, Any]:
    root = Path(repo or Path(__file__).resolve().parents[1]).resolve()
    missing: list[dict[str, str]] = []
    for rel, anchors in SOURCE_ANCHORS.items():
        path = root / rel
        content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        for marker in anchors:
            if marker not in content:
                missing.append({"source": rel, "expected": marker})

    references = []
    unclassified = []
    for path in _source_files(root):
        rel = path.relative_to(root).as_posix()
        matches = tuple(sorted(set(ROUTE.findall(path.read_text(encoding="utf-8-sig")))))
        if not matches:
            continue
        role = SOURCE_ROLES.get(rel)
        record = {"source": rel, "role": role or "unclassified", "route_operations": list(matches)}
        references.append(record)
        if role is None:
            unclassified.append(rel)

    # Route-string counts don't imply the caller has been exercised on the
    # public network. These are explicit obligations, not migration approvals.
    gates = [
        {"surface": "legacy_http_worker_server_and_cli", "status": "retain",
         "requires": "Docker worker-profile replacement and encrypted HTTP ingress acceptance"},
        {"surface": "explicit_worker_registration_cli", "status": "retain",
         "requires": "Documented replacement and registration/credit-accounting parity"},
        {"surface": "captain_data_opt_in_rest_client", "status": "retain_explicit_opt_in",
         "requires": "End-to-end Captain/Data migration including historical Hub compatibility"},
        {"surface": "worker_page_offer_registration", "status": "migrated_authenticated",
         "requires": "Authenticated offer-update regression acceptance; legacy CLI and lab REST consumers remain separately protected"},
        {"surface": "legacy_hub_admin_registration", "status": "retain_admin_compatibility",
         "requires": "Migrate HTML administrative registration and test the new admin workflow"},
        {"surface": "scheduler_lab_and_smoke_consumers", "status": "retain_test_oracle",
         "requires": "Migrate scheduler, chaos, temporal, phase and bridge smoke oracles while preserving accounting tests"},
        {"surface": "legacy_hub_rest_server", "status": "retain",
         "requires": "All internal and external clients migrated; endpoint retirement explicitly approved"},
        {"surface": "dev_test_live_worker_sessions", "status": "protected_operational",
         "requires": "Not a legacy REST removal candidate; preserve Dev-Test and Stable authenticated live-session protocols"},
    ]
    assert tuple(g["surface"] for g in gates) == MUST_RETAIN
    ok = not missing and not unclassified and bool(references)
    return {
        "schema": "main-computer.p54e.legacy-worker-rest-consumer-gate.v1",
        "ok": ok,
        "read_only": True,
        "scope": "bounded source census only; no Hub, Worker, FDB, payment or external traffic",
        "references": references,
        "source_files_with_route_strings": len(references),
        "unclassified_source_references": unclassified,
        "missing_evidence": missing,
        "retirement_gates": gates,
        "worker_page_direct_registration_present": any(r["role"] == "worker_page_offer_registration" for r in references),
        "dev_test_old_rest_routes_explicitly_blocked": any(r["role"] == "dev_test_explicitly_denied" for r in references),
        "external_consumers_verified_absent": False,
        "migration_accepted": False,
        "deletion_approved": [],
        "limitations": [
            "Route literals may represent implementation, denial, test harness, or actual HTTP dispatch; see role.",
            "Source presence does not prove live invocation or protocol compatibility.",
            "External consumers, running deployments, and connected plugins were not inspected.",
            "The Worker page offer action has migrated; other REST clients remain protected until separately retired.",
        ],
    }


def main() -> int:
    report = audit()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
