"""Read-only installation smoke for the P2-B authoritative request ID lookup.

Run from the repository root:
    python scripts/verify_p2b_canonical_request_install.py

This imports the active in-repository verifier, fakes the earnings HTTP response,
and proves that the Hub-assigned offer ID (not the test caller's submitted ID)
is used to retrieve a specific worker earning. It touches no live service.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    import tools.exp_hub_lab.run_lab as lab

    module_file = Path(lab.__file__).resolve()
    expected_file = (root / "tools" / "exp_hub_lab" / "run_lab.py").resolve()
    if module_file != expected_file:
        raise RuntimeError(f"Wrong active module: {module_file}; expected {expected_file}")

    wallet = "0xAbCdEf1234567890"
    worker = "live_" + hashlib.sha256(wallet.lower().encode("utf-8")).hexdigest()[:32]
    caller = "exp_handoff_lab_integration_req_1"
    canonical = "hub_authoritative_canonical_test_1"
    session = "sess_install_check_1"
    earning = "earn_install_check_1"
    handoff = {
        "multi_session_auth": {"required": True, "worker_wallet_address": wallet},
        "requests": [
            {
                "request_id": caller,
                "offer": {"request_id": canonical, "session_id": session},
                "response": {
                    "ok": True, "accepted": True, "request_id": canonical,
                    "session_id": session,
                },
                "terminal_ack": {
                    "ok": True, "request_id": canonical, "session_id": session,
                    "payout": {"status": "charged", "worker_earning_id": earning},
                },
            }
        ],
    }
    queried: list[str] = []

    def fake_earnings(url: str, *, timeout: float) -> dict:
        del timeout
        request_ids = parse_qs(urlparse(url).query).get("request_id", [])
        if request_ids != [canonical] or caller in url:
            raise RuntimeError(
                f"P2-B CANONICAL INSTALL FAIL: verifier queried {request_ids}; "
                f"expected {[canonical]} (never caller ID {caller})"
            )
        queried.append(url)
        return {"ok": True, "worker_earnings": [
            {"request_id": canonical, "earning_id": earning, "worker_node_id": worker}
        ]}

    with patch.object(lab, "_read_json_url", side_effect=fake_earnings):
        resolved_worker, resolved_ids = lab._resolve_settlement_worker_from_handoff(
            handoff, owner_url="http://verification-only.invalid", timeout=0.5,
        )

    if (resolved_worker, resolved_ids) != (worker, [earning]) or len(queried) != 1:
        raise RuntimeError("P2-B CANONICAL INSTALL FAIL: earning/worker identity differs")
    print("P2-B CANONICAL INSTALL: PASS")
    print(f"  active source: {module_file}")
    print(f"  caller ID: {caller}")
    print(f"  queried Hub ID: {canonical}")
    print("  financial operations: none (HTTP lookup mocked)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"P2-B CANONICAL INSTALL: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
