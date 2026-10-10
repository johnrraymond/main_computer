"""Captain/Data CLI adapter for the experimental Hub's authenticated live sessions.

Do not register a legacy REST worker on an experimental Hub.  The Hub's live
WebSocket owns worker liveness, delivery and settlement.  This module reuses
its viewport transport rather than implementing another WebSocket protocol.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from main_computer.hub import HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID
from main_computer.models import ChatMessage, ChatResponse
from main_computer.multisession_key_signing import build_personal_sign_blob, normalize_address


def _hub_post(hub_url: str, path: str, body: dict[str, Any], *, timeout_s: float = 15.0) -> dict[str, Any]:
    url = hub_url.rstrip("/") + path
    request = Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "main-computer-cli-live-session/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, timeout_s)) as response:
            value = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Hub {path} returned HTTP {exc.code}: {detail[:800]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Hub {path} is unreachable: {exc}") from exc
    if not isinstance(value, dict) or value.get("ok") is False:
        raise RuntimeError(f"Hub {path} failed: {str(value)[:800]}")
    return value


def hub_uses_live_sessions(hub_url: str, *, timeout_s: float = 5.0) -> bool:
    """Detect the deployed Hub implementation rather than guessing from hostname."""
    request = Request(
        hub_url.rstrip("/") + "/api/hub/v1/hub-identity",
        headers={"Accept": "application/json", "User-Agent": "main-computer-cli-live-session/1.0"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            identity = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, ValueError) as exc:
        raise RuntimeError(f"Cannot identify Hub protocol before worker/request submission: {exc}") from exc
    if not isinstance(identity, dict):
        raise RuntimeError("Hub identity response was not an object.")
    return str(identity.get("service") or "") == "main_computer.exp_fdb_hub"


def _officer_wallet(selector: str, *, cwd: Path | None = None) -> Any:
    """Reuse the existing O0/O3 identity resolver without creating another wallet."""
    from main_computer.captain_cli import _default_state_path, _load_json, resolve_captain_wallet

    root = (cwd or Path.cwd()).resolve()
    deployment = _default_state_path("mainnet", root)
    wallet = resolve_captain_wallet(
        selector, deployment=_load_json(deployment), deployment_path=deployment,
    )
    if not wallet.private_key:
        raise RuntimeError(
            f"{selector.upper()} has no configured local private key. "
            "The Hub requires a wallet-signed multi-session key. "
            "Configure that existing officer wallet before engaging a paid live worker."
        )
    return wallet


def issue_cli_multisession_authorization(
    hub_url: str,
    *,
    wallet: Any,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Ask the Hub for its existing wallet's active key, without storing secrets.

    The deployed Hub defines the multi-session signature domain independently
    from the chain used for escrow. Follow its expected signature domain; do
    not misrepresent the Mainnet escrow chain ID as the signing domain.
    """
    wallet_address = normalize_address(wallet.address)
    if not getattr(wallet, "private_key", ""):
        raise RuntimeError("An existing wallet private key is required for Hub multi-session authorization.")
    now = datetime.now(timezone.utc)
    message = {
        "purpose": "request_multi_session_key",
        "request_id": "msk_req_" + uuid.uuid4().hex,
        "wallet_address": wallet_address,
        "chain_id": HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
        "origin": "main-computer-cli",
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        "version": "main-computer-multisession-key-request-v1",
    }
    signed_request = build_personal_sign_blob(
        message=message, private_key=wallet.private_key,
        wallet_address=wallet_address, chain_id=HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
    )
    result = _hub_post(
        hub_url, "/api/hub/v1/credits/multisession-keys/request",
        {"signed_request": signed_request}, timeout_s=timeout_s,
    )
    key = result.get("key") if isinstance(result.get("key"), dict) else {}
    key_id = str(key.get("id") or "").strip()
    if not key_id or str(key.get("status") or "") != "active":
        raise RuntimeError("Hub did not return an active multi-session key for the existing wallet.")
    if normalize_address(key.get("wallet_address")) != wallet_address:
        raise RuntimeError("Hub returned a multi-session key for the wrong wallet.")
    return {
        "kind": "multisession_key",
        "wallet_address": wallet_address,
        "key_id": key_id,
        "multisession_key_id": key_id,
        "chain_id": HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
    }


def cli_request_authorization(hub_url: str, *, officer: str = "o0") -> dict[str, Any]:
    return issue_cli_multisession_authorization(hub_url, wallet=_officer_wallet(officer))


def _chat_result(chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse], offer: dict[str, Any]) -> dict[str, Any]:
    work = offer.get("work") if isinstance(offer.get("work"), dict) else {}
    supplied = work.get("messages") if isinstance(work.get("messages"), list) else []
    messages = [ChatMessage(role=str(item.get("role") or "user"), content=str(item.get("content") or ""))
                for item in supplied if isinstance(item, dict)]
    if not messages:
        prompt = work.get("input") if isinstance(work.get("input"), dict) else {}
        messages = [ChatMessage(role="user", content=str(prompt.get("prompt") or work.get("prompt") or ""))]
    response = chat_fn(messages)
    return {
        "status": "success",
        "response": {
            "role": "assistant",
            "content": response.content,
            "provider": response.provider,
            "model": response.model,
            "metadata": dict(response.metadata) if isinstance(response.metadata, dict) else {},
        },
        "transport": "websocket-live-session",
    }


def serve_cli_live_worker(
    *, hub_url: str, worker_node_id: str, model: str, chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    assigned_ring: int, credits_per_request: Any, officer: str,
    verbose: bool = True, max_requests: int | None = None,
    timeout_s: float = 10.0, poll_interval_s: float = 0.25,
) -> None:
    from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient

    auth = issue_cli_multisession_authorization(hub_url, wallet=_officer_wallet(officer), timeout_s=timeout_s)
    ring = f"ring-{int(assigned_ring)}"
    market = {
        "rings": [ring], "capabilities": ["chat.completions"], "models": [model],
        "price": {"amount": str(credits_per_request), "unit": "compute_credit"},
        "max_concurrency": 1, "active_sessions": 0,
    }
    auth_message = {
        "type": "worker.auth", "chain_id": auth["chain_id"],
        "status": "available", "model": model, "models": [model],
        "queue_depth": 0, "active_requests": 0, "max_concurrency": 1,
        "capabilities": {"provider": "ollama", "assigned_ring": int(assigned_ring),
                         "capabilities": ["chat.completions"]},
        "market": market, "multisession_authorization": auth,
    }
    client = _WorkerHubLiveSessionClient(
        hub_url=hub_url, worker_id=worker_node_id, auth_message=auth_message,
        timeout_s=timeout_s, work_executor=lambda offer: _chat_result(chat_fn, offer),
    )
    try:
        snapshot = client.start()
        if not (snapshot.get("accepted") or {}).get("ok"):
            raise RuntimeError("Hub did not accept the live worker WebSocket.")
        if verbose:
            print(f"Connected authenticated live worker {worker_node_id} to {hub_url} on {ring}.")
            print("Waiting for WebSocket work offers. Keep this window open.")
        processed = 0
        last_session = ""
        while client.is_alive:
            result = client.last_result
            if isinstance(result, dict):
                session = str(result.get("session_id") or "")
                if session and session != last_session and result.get("type") in {"hub.work.result.accepted", "hub.work.failed.accepted", "hub.work.terminal.accepted"}:
                    processed += 1
                    last_session = session
                    if verbose:
                        print(f"Hub accepted worker result request={result.get('request_id', '')} type={result.get('type', '')}")
                    if max_requests is not None and processed >= int(max_requests):
                        return
            time.sleep(max(0.1, float(poll_interval_s)))
        raise RuntimeError(f"Hub worker live-session disconnected: {client.last_error or client.close_reason or 'socket closed'}")
    except KeyboardInterrupt:
        if verbose:
            print("\nHub worker live-session stopped.")
    finally:
        client.close(reason="cli_worker_exit")
