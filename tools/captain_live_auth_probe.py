"""Diagnose Captain's real worker.auth handshake without running Ollama jobs.

Uses the same wallet authorization and WebSocket implementation as Captain.
Logs *only* phase timings, Hub identifiers, and inbound frame types (never keys,
private wallets, or raw authorization frames).

Examples:
  python tools/captain_live_auth_probe.py --timeout-s 45
  python tools/captain_live_auth_probe.py --all-hubs --timeout-s 45
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# This is also launched directly as tools/captain_live_auth_probe.py.
# In that mode Python adds tools/ (not the repository root) to sys.path.
# Captain's wallet resolver imports tools.mother; ensure it resolves locally.
if __package__ in (None, ""):
    repository_root = str(Path(__file__).resolve().parent.parent)
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
from datetime import datetime, timezone
from typing import Any

from main_computer.hub_cli_live_session import (
    _officer_wallet,
    identify_hub_protocol,
    issue_cli_multisession_authorization,
)
from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient

HUB_URLS = (
    "https://mainnet-hub.greatlibrary.io",
    "https://mainneta-hub1.greatlibrary.io",
    "https://mainnetc-hub1.greatlibrary.io",
)


class TracingClient(_WorkerHubLiveSessionClient):
    def __init__(self, *, on_event: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._on_event = on_event
        self.received_types: list[str] = []

    def _open_socket(self):
        self._on_event("WS_UPGRADE_BEGIN")
        result = super()._open_socket()
        self._on_event("WS_UPGRADE_101")
        return result

    def _send_json(self, payload: dict[str, Any]) -> None:
        message_type = str(payload.get("type") or "")
        if message_type == "worker.auth":
            self._on_event("WORKER_AUTH_SENT")
        super()._send_json(payload)

    def _recv_json(self) -> dict[str, Any]:
        message = super()._recv_json()
        kind = str(message.get("type") or "unknown")
        self.received_types.append(kind)
        # Never emit a raw Hub message: accepted frames contain wallet and key metadata.
        extras = ""
        if kind == "hub.auth.rejected":
            extras = " reason_code=" + str(message.get("reason_code") or "unspecified")[:120]
        elif kind == "hub.error":
            extras = " error=" + str(message.get("error") or "unspecified")[:120]
        self._on_event("FRAME type=" + kind + extras)
        return message


def probe(hub_url: str, *, officer: str, model: str, timeout_s: float, ring: int, price: str) -> bool:
    started = time.monotonic()

    def log(message: str) -> None:
        elapsed = time.monotonic() - started
        print(f"  [{elapsed:7.2f}s] {message}", flush=True)

    print(f"\nTARGET {hub_url}", flush=True)
    client: TracingClient | None = None
    try:
        log("IDENTITY_BEGIN")
        identity = identify_hub_protocol(hub_url, timeout_s=min(timeout_s, 15))
        contract = identity.get("contract") if isinstance(identity.get("contract"), dict) else {}
        log(
            "IDENTITY_OK service=" + str(identity.get("service") or "?")
            + " hub_id=" + str(identity.get("hub_id") or "?")
            + " worker_identity=" + str(contract.get("worker_identity") or "not-advertised")
        )
        log("MSK_REQUEST_BEGIN (signing locally; no private key displayed)")
        wallet = _officer_wallet(officer)
        kwargs: dict[str, Any] = {"stable_identity": identity} if identity.get("service") == "main_computer.stable_hub" else {}
        auth = issue_cli_multisession_authorization(hub_url, wallet=wallet, timeout_s=max(15, timeout_s), **kwargs)
        log("MSK_REQUEST_OK")
        market = {
            "rings": [f"ring-{ring}"],
            "capabilities": ["chat.completions"],
            "models": [model],
            "price": {"amount": price, "unit": "compute_credit"},
            "max_concurrency": 1,
            "active_sessions": 0,
        }
        auth_message = {
            "type": "worker.auth",
            "chain_id": auth["chain_id"],
            "status": "available",
            "model": model,
            "models": [model],
            "queue_depth": 0,
            "active_requests": 0,
            "max_concurrency": 1,
            "capabilities": {
                "provider": "ollama", "assigned_ring": ring, "capabilities": ["chat.completions"]
            },
            "market": market,
            "multisession_authorization": auth,
        }
        client = TracingClient(
            hub_url=hub_url,
            worker_id="diagnostic-label-only",
            auth_message=auth_message,
            timeout_s=timeout_s,
            on_event=log,
        )
        try:
            snapshot = client.start()
        except Exception as exc:
            log("AUTH_FAILED exception=" + type(exc).__name__ + " message=" + str(exc)[:240])
            log("FRAMES_RECEIVED=" + repr(client.received_types))
            return False
        accepted = snapshot.get("accepted") if isinstance(snapshot, dict) else None
        log("AUTH_ACCEPTED ok=" + str(bool(isinstance(accepted, dict) and accepted.get("ok"))))
        log("FRAMES_RECEIVED=" + repr(client.received_types))
        return bool(isinstance(accepted, dict) and accepted.get("ok"))
    except Exception as exc:
        log("PREFLIGHT_FAILED exception=" + type(exc).__name__ + " message=" + str(exc)[:240])
        return False
    finally:
        if client is not None:
            client.close(reason="diagnostic_probe_complete")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trace Captain worker.auth across public and direct Hub endpoints without running model inference.")
    parser.add_argument("--url", action="append", help="Explicit Hub base URL; may be repeated")
    parser.add_argument("--all-hubs", action="store_true", help="Probe public ingress, Hub A and Hub C in sequence")
    parser.add_argument("--timeout-s", type=float, default=45.0, help="WebSocket authentication deadline in seconds")
    parser.add_argument("--officer", default="o0", help="Local Captain wallet selector")
    parser.add_argument("--model", default="gemma4:26b-a4b-it-q4_K_M")
    parser.add_argument("--ring", type=int, default=3)
    parser.add_argument("--price", default="1.024")
    args = parser.parse_args(argv)
    if args.timeout_s < 10:
        parser.error("--timeout-s must be at least 10")
    if args.all_hubs and args.url:
        parser.error("choose either --all-hubs or --url")
    urls = list(HUB_URLS if args.all_hubs else (args.url or [HUB_URLS[0]]))
    results = []
    for url in urls:
        success = probe(url.rstrip("/"), officer=args.officer, model=args.model, timeout_s=args.timeout_s, ring=args.ring, price=args.price)
        results.append((url, success))
    print("\nSUMMARY " + " ".join(f"{url}={'ACCEPTED' if ok else 'FAILED'}" for url, ok in results), flush=True)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
