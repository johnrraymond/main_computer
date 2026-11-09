"""P5.3 Dev-Test Hub inherited-HTTP-API audit; no Hub connection or mutation.

Usage: python -m tools.dev_test_hub_inheritance_verify
"""
from __future__ import annotations

import json

from main_computer.dev_test_hub_legacy_routes import inherited_legacy_hub_route
from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler
from main_computer.hub import HubServerHandler


def verify_contract() -> dict[str, object]:
    checks = {
        "dev_test_handler_still_inherits_historical_handler":
            issubclass(ExperimentalFoundationDbHubServerHandler, HubServerHandler),
        "legacy_health_preserved": inherited_legacy_hub_route("GET", "/api/hub/v1/health"),
        "legacy_status_preserved": inherited_legacy_hub_route("GET", "/api/hub/status"),
        "legacy_safe_chat_dev_simulation_preserved": inherited_legacy_hub_route("POST", "/api/hub/remote-overflow/safe-chat"),
        "legacy_multisession_auth_preserved": inherited_legacy_hub_route("POST", "/api/hub/v1/credits/multisession-keys/request"),
        "legacy_wallet_funding_preserved": inherited_legacy_hub_route("POST", "/api/hub/v1/credits/wallet-funding/complete"),
        "private_credit_get_delegates_to_existing_denial": inherited_legacy_hub_route("GET", "/api/hub/v1/credits/charges"),
        "new_unowned_get_blocked": not inherited_legacy_hub_route("GET", "/api/hub/unowned-future-route"),
        "new_unowned_post_blocked": not inherited_legacy_hub_route("POST", "/api/hub/unowned-future-route"),
        "removed_worker_rest_blocked": not inherited_legacy_hub_route("POST", "/api/hub/v1/workers/register"),
        "removed_credit_import_blocked": not inherited_legacy_hub_route("POST", "/api/hub/v1/credits/deposits/import"),
        "new_safe_chat_protocol_blocked": not inherited_legacy_hub_route("POST", "/api/hub/remote-overflow/v2/chat"),
    }
    return {"ok": all(checks.values()), "scope": "dev-test-hub-inherited-http-route-boundary", "checks": checks,
            "note": "Read-only static route verification; no running Hub or paid Worker operation checked."}


def main() -> int:
    result = verify_contract()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
