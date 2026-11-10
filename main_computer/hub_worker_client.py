"""Worker registration and REST-pull client transport for the legacy Hub protocol.

P5.3h relocates unchanged function bodies out of the Hub server facade. Worker
selection still delegates to the authenticated Stable Hub live-session client
when supported. Importing this module opens no network connection, creates no
Worker process, and does not start a Hub.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub_http_legacy_support import chat_message_from_dict
from main_computer.hub_registry_shared import (
    PHASE9_PRICING_TYPE, PHASE9_EXECUTION_MODE,
    _require_allowed_transport, _hub_credit_wei_from_value,
    _hub_credit_public_value_from_wei,
)
from main_computer.models import ChatMessage, ChatResponse

HUB_WORKER_API_USER_AGENT = "main-computer-worker-cli/1.0 (+https://greatlibrary.io)"

def _hub_worker_api_headers(*, json_body: bool = False) -> dict[str, str]:
    """Return explicit API-client headers for remote Hub worker-pull requests."""

    user_agent = str(os.environ.get("MAIN_COMPUTER_HUB_USER_AGENT") or "").strip() or HUB_WORKER_API_USER_AGENT
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
        "X-Main-Computer-Client": "main-computer-worker-cli",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers

def _hub_worker_http_error_message(url: str, exc: HTTPError, body: str) -> str:
    message = f"Hub request failed for {url} with HTTP {exc.code}: {body}"
    if exc.code == 403 and "error code: 1010" in body.lower():
        message += (
            "\nCloudflare rejected this HTTP client signature. The worker command now sends explicit API client "
            "headers; if this still fails, the mainnet hub Cloudflare rules need to allow "
            f"{HUB_WORKER_API_USER_AGENT!r} or set MAIN_COMPUTER_HUB_USER_AGENT to an allowed client signature."
        )
    return message

def register_worker_with_hub(
    *,
    hub_url: str,
    node_id: str,
    endpoint: str,
    model: str = "",
    models: Sequence[str] | None = None,
    credits_per_request: Any = 1,
    timeout_s: float = 10.0,
    allow_insecure_dev_network: bool = False,
    assigned_ring: int | None = None,
    execution_mode: str | None = None,
    pricing: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    worker_instance_id: str = "",
) -> dict[str, Any]:
    _require_allowed_transport(hub_url, role="Hub", allow_insecure_dev_network=allow_insecure_dev_network)
    _require_allowed_transport(endpoint, role="Worker", allow_insecure_dev_network=allow_insecure_dev_network)
    credit_price_wei = _hub_credit_wei_from_value(credits_per_request, "1", minimum_wei=1)
    payload: dict[str, Any] = {
        "node_id": node_id,
        "endpoint": endpoint,
        "model": model,
        "credits_per_request": _hub_credit_public_value_from_wei(credit_price_wei),
        "credits_per_request_wei": str(credit_price_wei),
    }
    clean_models = [str(item).strip() for item in (models or []) if str(item).strip()]
    if model and model not in clean_models:
        clean_models.insert(0, str(model))
    if clean_models:
        payload["models"] = clean_models
    if worker_instance_id:
        payload["worker_instance_id"] = worker_instance_id
    if assigned_ring is not None:
        payload["assigned_ring"] = int(assigned_ring)
    if execution_mode:
        payload["execution_mode"] = str(execution_mode)
    if pricing is not None:
        payload["pricing"] = dict(pricing)
    if capabilities is not None:
        payload["capabilities"] = dict(capabilities)
    url = hub_url.rstrip("/") + "/api/hub/v1/workers/register"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_hub_worker_api_headers(json_body=True),
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout_s or 10.0))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_hub_worker_http_error_message(url, exc, body)) from exc
    except URLError as exc:
        raise RuntimeError(f"Hub request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Hub returned a non-object registration response.")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data

def _post_hub_worker_json(
    *,
    hub_url: str,
    path: str,
    payload: dict[str, Any],
    timeout_s: float,
    allow_insecure_dev_network: bool = False,
) -> dict[str, Any]:
    _require_allowed_transport(hub_url, role="Hub", allow_insecure_dev_network=allow_insecure_dev_network)
    url = hub_url.rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_hub_worker_api_headers(json_body=True),
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout_s or 10.0))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_hub_worker_http_error_message(url, exc, body)) from exc
    except URLError as exc:
        raise RuntimeError(f"Hub request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Hub returned a non-object response.")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data

def _worker_pull_response_payload(
    *,
    chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    lease: dict[str, Any],
) -> dict[str, Any]:
    messages_payload = lease.get("messages", [])
    messages = [
        chat_message_from_dict(item)
        for item in messages_payload
        if isinstance(item, dict)
    ]
    if not messages:
        messages = [ChatMessage(role="user", content=str(lease.get("prompt") or ""))]
    response = chat_fn(messages)
    metadata = dict(response.metadata) if isinstance(response.metadata, dict) else {}
    metadata.setdefault("worker_pull_v0", True)
    return {
        "status": "success",
        "response": {
            "content": response.content,
            "provider": response.provider,
            "model": response.model,
            "metadata": metadata,
        },
    }

def serve_hub_worker_pull(
    config: MainComputerConfig,
    chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    *,
    hub_url: str | None = None,
    public_endpoint: str | None = None,
    assigned_ring: int = 3,
    execution_mode: str = PHASE9_EXECUTION_MODE,
    poll_interval_s: float = 2.0,
    heartbeat_interval_s: float = 30.0,
    lease_seconds: float | None = None,
    verbose: bool = True,
    max_requests: int | None = None,
    officer: str = "o0",
    solve_fn: Callable[..., dict[str, Any]] | None = None,
    allow_legacy_worker_rest: bool = False,
) -> None:
    """Run a foreground worker-pull loop using the Hub's advertised transport.

    Legacy REST-pull is retained only as an explicit compatibility choice.
    An unrecognized service cannot silently select it. Failed identity probes
    continue to refuse before either transport is used.
    """

    from main_computer.hub_cli_live_session import hub_uses_live_sessions, serve_cli_live_worker

    resolved_hub = (hub_url or config.hub_url).rstrip("/")
    if hub_uses_live_sessions(resolved_hub):
        serve_cli_live_worker(
            hub_url=resolved_hub, worker_node_id=config.hub_worker_node_id,
            model=config.model, chat_fn=chat_fn, assigned_ring=assigned_ring,
            credits_per_request=config.hub_credits_per_request,
            officer=officer, verbose=verbose, max_requests=max_requests,
            timeout_s=min(15.0, max(3.0, config.hub_timeout_s)),
            poll_interval_s=poll_interval_s,
            provider_name=config.provider, solve_fn=solve_fn,
        )
        return

    if config.provider == "nanojev":
        raise RuntimeError("NanoJev/CLEF requires the existing Hub's authenticated live-session transport")
    if not allow_legacy_worker_rest:
        raise RuntimeError(
            "Hub does not advertise an authenticated live Worker session. "
            "Refusing implicit legacy REST Worker-pull fallback. To use a "
            "legacy-compatible Hub intentionally, select --allow-legacy-worker-rest."
        )

    clean_hub_url = (hub_url or config.hub_url).rstrip("/")
    clean_worker_instance_id = config.hub_worker_node_id
    endpoint = (
        public_endpoint
        or config.hub_worker_endpoint
        or f"https://worker-pull.main-computer.local/{config.hub_worker_node_id}"
    ).rstrip("/")
    credit_price_wei = _hub_credit_wei_from_value(config.hub_credits_per_request, "1", minimum_wei=1)
    pricing = {
        "pricing_type": PHASE9_PRICING_TYPE,
        "credits_per_request": _hub_credit_public_value_from_wei(credit_price_wei),
        "credits_per_request_wei": str(credit_price_wei),
        "unit": "compute_credit",
        "execution_mode": execution_mode,
    }
    capabilities = {
        "provider": config.provider,
        "worker_pull_v0": True,
        "assigned_ring": int(assigned_ring),
        "requested_ring": int(assigned_ring),
        "execution_mode": execution_mode,
    }
    report = register_worker_with_hub(
        hub_url=clean_hub_url,
        node_id=config.hub_worker_node_id,
        endpoint=endpoint,
        model=config.model,
        models=[config.model],
        credits_per_request=config.hub_credits_per_request,
        timeout_s=10.0,
        allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        assigned_ring=int(assigned_ring),
        execution_mode=execution_mode,
        pricing=pricing,
        capabilities=capabilities,
        worker_instance_id=clean_worker_instance_id,
    )
    if verbose:
        worker_payload = report.get("worker", {}) if isinstance(report.get("worker"), dict) else {}
        offer_payload = worker_payload.get("offer", {}) if isinstance(worker_payload.get("offer"), dict) else {}
        print(
            "Registered worker-pull local AI "
            f"{config.hub_worker_node_id} on ring {int(assigned_ring)} "
            f"for model {config.model} at {clean_hub_url}"
        )
        if offer_payload:
            print(f"Advertised worker offer: {offer_payload.get('offer_id', '')} price={offer_payload.get('credits_per_request_display', offer_payload.get('credits_per_request', ''))}")
        print("Polling for worker-pull leases. Leave this window open while the smoke command runs.")

    poll_sleep = max(0.1, float(poll_interval_s or 2.0))
    heartbeat_every = max(1.0, float(heartbeat_interval_s or 30.0))
    processed = 0
    next_heartbeat_at = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_heartbeat_at:
                _post_hub_worker_json(
                    hub_url=clean_hub_url,
                    path="/api/hub/v1/workers/heartbeat",
                    payload={
                        "worker_node_id": config.hub_worker_node_id,
                        "worker_instance_id": clean_worker_instance_id,
                        "status": "available",
                        "model": config.model,
                        "models": [config.model],
                        "active_requests": 0,
                        "queue_depth": 0,
                        "capabilities": capabilities,
                    },
                    timeout_s=min(10.0, config.hub_timeout_s),
                    allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
                )
                next_heartbeat_at = now + heartbeat_every

            poll_payload: dict[str, Any] = {
                "worker_node_id": config.hub_worker_node_id,
                "worker_instance_id": clean_worker_instance_id,
            }
            if lease_seconds is not None:
                poll_payload["lease_seconds"] = float(lease_seconds)
            poll = _post_hub_worker_json(
                hub_url=clean_hub_url,
                path="/api/hub/v1/workers/poll",
                payload=poll_payload,
                timeout_s=min(10.0, config.hub_timeout_s),
                allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            )
            lease = poll.get("lease")
            if not isinstance(lease, dict):
                time.sleep(poll_sleep)
                continue

            request_id = str(lease.get("request_id", ""))
            lease_id = str(lease.get("lease_id", ""))
            if verbose:
                print(f"Accepted worker-pull lease {lease_id} for request {request_id}")
            _post_hub_worker_json(
                hub_url=clean_hub_url,
                path="/api/hub/v1/workers/heartbeat",
                payload={
                    "worker_node_id": config.hub_worker_node_id,
                    "worker_instance_id": clean_worker_instance_id,
                    "status": "busy",
                    "model": config.model,
                    "models": [config.model],
                    "active_requests": 1,
                    "queue_depth": 0,
                    "capabilities": capabilities,
                },
                timeout_s=min(10.0, config.hub_timeout_s),
                allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            )
            try:
                result = _worker_pull_response_payload(chat_fn=chat_fn, lease=lease)
            except Exception as exc:
                result = {
                    "status": "failed",
                    "error": str(exc),
                    "message": str(exc),
                }
            completed = _post_hub_worker_json(
                hub_url=clean_hub_url,
                path="/api/hub/v1/workers/results",
                payload={
                    "worker_node_id": config.hub_worker_node_id,
                    "worker_instance_id": clean_worker_instance_id,
                    "request_id": request_id,
                    "lease_id": lease_id,
                    "result": result,
                },
                timeout_s=max(10.0, config.hub_timeout_s),
                allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            )
            processed += 1
            if verbose:
                request_payload = completed.get("request", {}) if isinstance(completed.get("request"), dict) else {}
                print(f"Submitted result for {request_id}: state={request_payload.get('state', '') or completed.get('state', '')}")
            next_heartbeat_at = 0.0
            if max_requests is not None and processed >= int(max_requests):
                return
    except KeyboardInterrupt:
        print("\nHub worker-pull loop stopped.")
