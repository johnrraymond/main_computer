"""Read-only P5.3b module/contract import verifier.

    python -m tools.hub_registry_shared_verify

No Hub startup, database reads, network calls, Worker registration, or payment.
"""
from __future__ import annotations

import json

from main_computer import hub, hub_registry_shared as shared
from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbRegistry


def inspect_contract() -> dict[str, object]:
    model_identity = hub.HubWorker is shared.HubWorker and hub.HubUpstream is shared.HubUpstream
    helper_identity = all(getattr(hub,name) is getattr(shared,name) for name in (
        "_hub_pricing_credit_wei", "_phase9_worker_offer_from_payload", "_require_allowed_transport",
    ))
    state_compatibility = issubclass(ExperimentalFoundationDbRegistry,hub.HubRegistry)
    out = {
        "ok": model_identity and helper_identity and state_compatibility,
        "shared_model_identity":model_identity,
        "shared_pricing_and_transport_helpers":helper_identity,
        "fdb_registry_legacy_storage_facade_preserved":state_compatibility,
        "shared_module":"main_computer.hub_registry_shared",
        "legacy_registry_still_in":"main_computer.hub",
        "scope":"read-only, no live Hub request or payment",
    }
    return out


def main() -> int:
    result=inspect_contract()
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
