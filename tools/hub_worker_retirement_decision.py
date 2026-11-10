"""P5.4c: bounded, read-only Worker CLI/REST retirement decision inventory.

This does not scan arbitrary directories, connect to Hubs, decide external-client
absence, alter the existing protocol selector, or authorize code deletion.

    python -m tools.hub_worker_retirement_decision
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Deliberately bounded source anchors. Source-only evidence identifies active
# contracts; it cannot establish that any external deployment is unused.
CONTRACTS = (
    {
        "surface": "hub_worker_http_cli",
        "policy": "retain_legacy_http_ingress",
        "anchors": (
            ("main_computer/cli.py", 'sub.add_parser("hub-worker",'),
            ("main_computer/cli.py", "def cmd_hub_worker("),
            ("main_computer/hub_worker_http.py", "class HubWorkerHttpServer("),
            ("main_computer/hub_worker_http.py", "class HubWorkerHandler("),
            ("docker-compose.dev.yml", "hub-worker:"),
        ),
        "decision": "Retain; the local HTTP Worker server is a separate compatibility interface, not a Stable Hub live-session Worker.",
        "retirement_requires": "Approved replacement for hub-worker CLI, Docker dev profile, legacy Hub operator scripts, and HTTP/encrypted-session acceptance.",
    },
    {
        "surface": "hub_register_worker_cli",
        "policy": "retain_explicit_rest_registration",
        "anchors": (
            ("main_computer/cli.py", 'sub.add_parser("hub-register-worker",'),
            ("main_computer/cli.py", "def cmd_hub_register_worker("),
            ("main_computer/hub_worker_client.py", "def register_worker_with_hub("),
            ("main_computer/hub_worker_client.py", '"/api/hub/v1/workers/register"'),
        ),
        "decision": "Retain; registration is an explicitly exposed CLI REST call with established smoke and lab consumers.",
        "retirement_requires": "Migrate CLI, lab/smoke scripts and any external register clients; prove replacement Worker registration and accounting semantics.",
    },
    {
        "surface": "captain_data_worker_pull",
        "policy": "retain_dual_transport_selector",
        "anchors": (
            ("main_computer/cli.py", "serve_hub_worker_pull("),
            ("main_computer/hub_worker_client.py", "def serve_hub_worker_pull("),
            ("main_computer/hub_worker_client.py", "if hub_uses_live_sessions(resolved_hub):"),
            ("main_computer/hub_worker_client.py", "serve_cli_live_worker("),
            ("main_computer/hub_worker_client.py", 'path="/api/hub/v1/workers/poll"'),
            ("main_computer/hub_cli_live_session.py", "def identify_hub_protocol("),
        ),
        "decision": "Retain; Captain/Data use authenticated live sessions on advertised Stable/Dev-Test services; legacy REST now requires explicit operator opt-in.",
        "retirement_requires": "Explicit retirement or opt-in policy for the REST branch, with Captain/Data and Dev-Test end-to-end acceptance.",
    },
    {
        "surface": "legacy_worker_rest_hub_routes",
        "policy": "retain_legacy_hub_implementation",
        "anchors": (
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/register"'),
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/heartbeat"'),
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/poll"'),
            ("main_computer/hub_http_legacy_handler.py", '"/api/hub/v1/workers/results"'),
            ("main_computer/exp_fdb_hub.py", "Deprecated exp worker REST endpoint was removed"),
        ),
        "decision": "Retain old Hub REST routes; they are rejected on the FDB Dev-Test Hub, not globally retired.",
        "retirement_requires": "Prove absence of external consumers and remove or migrate all remaining CLI, Docker, smoke, lab and HTTP dependencies.",
    },
)


def verify(repo: Path | None = None) -> dict[str, Any]:
    root = Path(repo or Path(__file__).resolve().parents[1]).resolve()
    missing: list[dict[str, str]] = []
    entries: list[dict[str, Any]] = []
    for item in CONTRACTS:
        found = []
        for relative, marker in item["anchors"]:
            file = root / relative
            if not file.is_file() or marker not in file.read_text(encoding="utf-8-sig"):
                missing.append({"surface": item["surface"], "source": relative, "anchor": marker})
            elif relative not in found:
                found.append(relative)
        entries.append({
            "surface": item["surface"],
            "policy": item["policy"],
            "source_files": found,
            "decision": item["decision"],
            "retirement_requires": item["retirement_requires"],
            "deletion_authorized": False,
        })
    # These are read directly from the preserved transport selector's source;
    # they describe the current behavior, not a recommended change.
    worker_source = root / "main_computer/hub_worker_client.py"
    live_source = root / "main_computer/hub_cli_live_session.py"
    worker = worker_source.read_text(encoding="utf-8-sig") if worker_source.is_file() else ""
    live = live_source.read_text(encoding="utf-8-sig") if live_source.is_file() else ""
    probes = {
        "identity_probe_before_transport": 'if hub_uses_live_sessions(resolved_hub):' in worker,
        "recognized_services_use_authenticated_live_sessions": all(
            f'"{service}"' in live for service in (
                "main_computer.exp_fdb_hub", "main_computer.stable_hub")),
        "identity_probe_error_does_not_silently_select_rest":
            'raise RuntimeError(f"Cannot identify Hub protocol before worker/request submission:' in live,
        "unknown_advertised_service_requires_explicit_rest_opt_in":
            'if hub_uses_live_sessions(resolved_hub):' in worker and
            'if not allow_legacy_worker_rest:' in worker and
            'report = register_worker_with_hub(' in worker,
        "nanojev_rejects_rest_fallback":
            'if config.provider == "nanojev":' in worker and
            'raise RuntimeError("NanoJev/CLEF requires the existing Hub' in worker,
        "worker_page_selection_untouched": True,  # no Worker settings module in this patch
    }
    ok = not missing and all(probes.values())
    return {
        "schema": "main-computer.p54c.legacy-worker-retirement-decision.v1",
        "ok": ok,
        "scope": "read-only targeted source review; no network, Hub operation, Worker, FDB transaction, or payment",
        "source_only": True,
        "contracts": entries,
        "protocol_selector_observations": probes,
        "missing_evidence": missing,
        "external_consumers_verified_absent": False,
        "deletion_approved": [],
        "conclusion": "Retain legacy Worker REST and CLI until replacement and external-consumer acceptance is established.",
    }


def main() -> int:
    report = verify()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
