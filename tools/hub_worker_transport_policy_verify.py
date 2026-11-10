"""P5.4d: read-only Worker-pull transport policy verification.

    python -m tools.hub_worker_transport_policy_verify

No network, Worker execution, Hub mutation, or payment.
"""
from __future__ import annotations

import inspect
import json

from main_computer import cli, hub, hub_worker_client
from main_computer.hub_cli_live_session import hub_uses_live_sessions
from tools.hub_worker_client_verify import verify_contract as verify_client
from tools.hub_worker_http_verify import verify_contract as verify_http


def verify() -> dict[str, object]:
    signature = inspect.signature(hub_worker_client.serve_hub_worker_pull)
    parameter = signature.parameters.get("allow_legacy_worker_rest")
    client = verify_client()
    worker_http = verify_http()
    parser_checks = {}
    for name, parser in (("captain", cli._build_captain_engage_options_parser()),
                         ("data", cli._build_data_engage_options_parser())):
        parser_checks[name] = (
            parser.parse_args([]).allow_legacy_worker_rest is False
            and parser.parse_args(["--allow-legacy-worker-rest"]).allow_legacy_worker_rest is True
        )
    checks = {
        "legacy_worker_rest_default_denied": parameter is not None and parameter.default is False,
        "live_session_selector_preserved": callable(hub_uses_live_sessions),
        "legacy_facade_worker_client_identity": hub.serve_hub_worker_pull is hub_worker_client.serve_hub_worker_pull,
        "captain_and_data_explicit_options": all(parser_checks.values()),
        "prior_client_verifier_passed": client["ok"] is True,
        "prior_http_verifier_passed": worker_http["ok"] is True,
    }
    return {"ok": all(checks.values()), "scope": "read-only: no Hub contact, Worker launch, FDB transaction or payment",
            "policy": "authenticated-live-preferred; legacy REST requires explicit per-command opt-in",
            "legacy_worker_rest_retained": True,
            "deletion_approved": [],
            "checks": checks}


def main() -> int:
    report = verify()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
