from __future__ import annotations

import json
import random
import hashlib
import threading
import time
from typing import Any, Iterator, Sequence
from urllib.parse import urlencode, urljoin

from tools.scheduler_lab.http_transport import (
    HubHttpResponse,
    HubStreamEvent,
    HubTransport,
    KeepAliveHubTransport,
)


class HubClient:
    """Small stdlib HTTP client for Hub lab processes.

    The lab intentionally talks to the Hub through HTTP. Workers and requesters
    should not mutate Hub memory or FoundationDB directly.
    """

    def __init__(
        self,
        base_url: str,
        *,
        base_urls: Sequence[str] | None = None,
        timeout_seconds: float = 10.0,
        retries: int = 0,
        rng: random.Random | None = None,
        transport: HubTransport | None = None,
    ) -> None:
        clean_urls = [str(url).strip().rstrip("/") + "/" for url in (base_urls or []) if str(url).strip()]
        if not clean_urls:
            clean_urls = [str(base_url).strip().rstrip("/") + "/"]
        self.base_url = clean_urls[0]
        self.base_urls = clean_urls
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.retries = max(0, int(retries))
        self.transport = transport or KeepAliveHubTransport()
        self._rng = rng or random.Random()
        self._rng_lock = threading.Lock()

    def _choose_base_url(self) -> str:
        """Choose the hub endpoint for one HTTP attempt.

        The scheduler lab deliberately does not pin a node to one hub service.
        Choosing per attempt simulates a non-sticky edge/load-balancer path and
        lets retry logic route around a dead experimental hub port.
        """

        if len(self.base_urls) <= 1:
            return self.base_url
        with self._rng_lock:
            return self._rng.choice(self.base_urls)

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> HubHttpResponse:
        last_response: HubHttpResponse | None = None
        last_base_url = self.base_url
        for attempt in range(self.retries + 1):
            base_url = self._choose_base_url()
            last_base_url = base_url
            url = urljoin(base_url, path.lstrip("/"))
            response = self.transport.request_json(
                method,
                url,
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
            response.base_url = base_url.rstrip("/")
            if isinstance(response.payload, dict):
                response.payload.setdefault("method", method.upper())
                response.payload.setdefault("path", path)
            last_response = response
            if response.status == 0 and attempt < self.retries:
                time.sleep(min(2.0, 0.15 * (2 ** attempt)))
                continue
            return response

        if last_response is not None:
            return last_response
        return HubHttpResponse(
            ok=False,
            status=0,
            payload={
                "error": "request failed",
                "error_type": "transport",
                "error_kind": "transport_error",
                "method": method.upper(),
                "path": path,
                "url": urljoin(last_base_url, path.lstrip("/")),
                "base_url": last_base_url.rstrip("/"),
                "transport_mode": "keepalive",
                "connection_reused": False,
                "connection_id": 0,
                "origin": "",
                "connection_error": "transport_error",
            },
            elapsed_ms=0.0,
            base_url=last_base_url.rstrip("/"),
        )

    def stream_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[HubStreamEvent]:
        base_url = self._choose_base_url()
        url = urljoin(base_url, path.lstrip("/"))
        for event in self.transport.stream_jsonl(
            method,
            url,
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            headers=headers,
        ):
            event.base_url = base_url.rstrip("/")
            if isinstance(event.payload, dict):
                event.payload.setdefault("method", method.upper())
                event.payload.setdefault("path", path)
            yield event

    def close(self) -> None:
        self.transport.close()

    def get_json(self, path: str) -> HubHttpResponse:
        return self.request_json("GET", path, None)

    def post_json(self, path: str, payload: dict[str, Any]) -> HubHttpResponse:
        return self.request_json("POST", path, payload)

    def request_multisession_key(self, signed_request: dict[str, Any]) -> HubHttpResponse:
        return self.post_json("/api/hub/v1/credits/multisession-keys/request", {"signed_request": signed_request})

    def import_wallet_funding(
        self,
        *,
        wallet_address: str,
        chain_id: int | str,
        credits: int,
        idempotency_key: str,
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> HubHttpResponse:
        wallet = str(wallet_address).strip().lower()
        seed = json.dumps(
            {
                "wallet_address": wallet,
                "chain_id": str(chain_id),
                "credits": int(credits),
                "idempotency_key": str(idempotency_key),
            },
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(seed).hexdigest()
        return self.post_json(
            "/api/hub/v1/credits/wallet-funding/import",
            {
                "wallet_address": wallet,
                "chain_id": int(str(chain_id), 0),
                "contract_address": "0x" + "0" * 39 + "1",
                "tx_hash": "0x" + digest,
                "log_index": 0,
                "block_number": 1,
                "payment_asset": "native",
                "payment_amount_base_units": max(1, int(credits)),
                "credits_granted_wei": max(1, int(credits)) * 10**18,
                "idempotency_key": str(idempotency_key),
                "memo": memo or f"scheduler lab wallet funding for {wallet}",
                "metadata": dict(metadata or {}),
            },
        )

    def register_worker(self, node: dict[str, Any]) -> HubHttpResponse:
        models = _json_list(node.get("models_json")) or [str(node.get("model") or "mock-ai-model-phase9")]
        min_credits = _as_int(node.get("min_accepted_credits"), 1)
        max_concurrency = _as_int(node.get("max_concurrency"), 1)
        payload = {
            "node_id": str(node.get("node_id")),
            "endpoint": f"http://127.0.0.1:1/scheduler-lab/{node.get('node_id')}",
            "model": str(node.get("model") or (models[0] if models else "mock-ai-model-phase9")),
            "models": models,
            "credits_per_request": min_credits,
            "queue_depth": 0,
            "active_requests": 0,
            "max_concurrency": max_concurrency,
            "pricing": {
                "pricing_type": "fixed_per_call_v0",
                "credits_per_request": min_credits,
                "minimum_accepted_credits": min_credits,
                "unit": "compute_credit",
                "execution_mode": "worker_pull_v0",
            },
            "execution": {
                "mode": "worker_pull_v0",
                "lab_node": True,
                "tags": _split_tags(node.get("tags")),
            },
            "capabilities": {
                "scheduler_lab": True,
                "cohort": node.get("cohort", ""),
                "tags": _split_tags(node.get("tags")),
                "network": node.get("network", "dev"),
                "ring": _as_int(node.get("ring"), 2),
                "minimum_accepted_credits": min_credits,
            },
        }
        auth = _node_multisession_authorization(node)
        if auth:
            payload["multisession_authorization"] = auth
            payload["wallet_address"] = auth["wallet_address"]
            payload["chain_id"] = auth.get("chain_id", node.get("chain_id", ""))
            payload["capabilities"]["wallet_address"] = auth["wallet_address"]
            payload["capabilities"]["multisession_key_id"] = auth["multisession_key_id"]
            payload["capabilities"]["auth_mode"] = "multisession-wallet"
        return self.post_json("/api/hub/v1/workers/register", payload)

    def heartbeat_worker(self, node: dict[str, Any], *, active_requests: int = 0, status: str = "available") -> HubHttpResponse:
        models = _json_list(node.get("models_json")) or [str(node.get("model") or "mock-ai-model-phase9")]
        payload = {
            "worker_node_id": str(node.get("node_id")),
            "status": str(status or "available"),
            "model": str(node.get("model") or (models[0] if models else "mock-ai-model-phase9")),
            "models": models,
            "queue_depth": 0,
            "active_requests": max(0, int(active_requests)),
            "max_concurrency": _as_int(node.get("max_concurrency"), 1),
            "capabilities": {
                "scheduler_lab": True,
                "cohort": node.get("cohort", ""),
                "tags": _split_tags(node.get("tags")),
                "network": node.get("network", "dev"),
                "ring": _as_int(node.get("ring"), 2),
            },
        }
        auth = _node_multisession_authorization(node)
        if auth:
            payload["multisession_authorization"] = auth
            payload["wallet_address"] = auth["wallet_address"]
            payload["chain_id"] = auth.get("chain_id", node.get("chain_id", ""))
            payload["capabilities"]["wallet_address"] = auth["wallet_address"]
            payload["capabilities"]["multisession_key_id"] = auth["multisession_key_id"]
            payload["capabilities"]["auth_mode"] = "multisession-wallet"
        return self.post_json("/api/hub/v1/workers/heartbeat", payload)

    def poll_worker(self, node: dict[str, Any], *, lease_seconds: float) -> HubHttpResponse:
        payload = {
            "worker_node_id": str(node.get("node_id")),
            "lease_seconds": max(1.0, float(lease_seconds)),
        }
        auth = _node_multisession_authorization(node)
        if auth:
            payload["multisession_authorization"] = auth
            payload["chain_id"] = auth.get("chain_id", node.get("chain_id", ""))
        return self.post_json("/api/hub/v1/workers/poll", payload)

    def submit_worker_stream_event(self, node: dict[str, Any], lease: dict[str, Any], event: dict[str, Any]) -> HubHttpResponse:
        payload = {
            "worker_node_id": str(node.get("node_id")),
            "request_id": str(lease.get("request_id") or ""),
            "lease_id": str(lease.get("lease_id") or ""),
            "event": dict(event),
        }
        if lease.get("worker_instance_id"):
            payload["worker_instance_id"] = str(lease.get("worker_instance_id") or "")
        auth = _node_multisession_authorization(node)
        if auth:
            payload["multisession_authorization"] = auth
            payload["chain_id"] = auth.get("chain_id", node.get("chain_id", ""))
        return self.post_json("/api/hub/v1/workers/stream-events", payload)

    def submit_worker_result(self, node: dict[str, Any], lease: dict[str, Any], result: dict[str, Any]) -> HubHttpResponse:
        payload = {
            "worker_node_id": str(node.get("node_id")),
            "request_id": str(lease.get("request_id") or ""),
            "lease_id": str(lease.get("lease_id") or ""),
            "result": result,
        }
        if lease.get("worker_instance_id"):
            payload["worker_instance_id"] = str(lease.get("worker_instance_id") or "")
        auth = _node_multisession_authorization(node)
        if auth:
            payload["multisession_authorization"] = auth
            payload["chain_id"] = auth.get("chain_id", node.get("chain_id", ""))
        return self.post_json("/api/hub/v1/workers/results", payload)

    def stream_request_events(
        self,
        request_id: str,
        *,
        after: int = 0,
        timeout_seconds: float | None = None,
        heartbeat_seconds: float | None = None,
    ) -> Iterator[HubStreamEvent]:
        query: dict[str, str] = {"after": str(max(0, int(after or 0)))}
        if timeout_seconds is not None:
            query["timeout_seconds"] = str(max(0.1, float(timeout_seconds)))
        if heartbeat_seconds is not None:
            query["heartbeat_seconds"] = str(max(0.25, float(heartbeat_seconds)))
        suffix = urlencode(query)
        path = f"/api/hub/v1/requests/{str(request_id).strip()}/stream"
        if suffix:
            path = f"{path}?{suffix}"
        return self.stream_request("GET", path)

    def submit_request(
        self,
        node: dict[str, Any],
        *,
        request_index: int,
        request_mode: str,
        account_id_prefix: str,
        prompt: str,
        scheduler_lab_run_id: str = "",
    ) -> HubHttpResponse:
        offered = _choose_from_distribution(node.get("offered_credits_distribution_json"), node.get("offered_credits"), request_index)
        model = _choose_from_distribution(node.get("model_distribution_json"), node.get("model"), request_index)
        node_id = str(node.get("node_id"))
        account_id = str(node.get("account_id") or f"{account_id_prefix}-{node_id}")
        metadata = {
            "scheduler_lab": True,
            "worker_pull_v0": request_mode == "worker_pull_v0",
            "execution_mode": request_mode,
            "offered_credits": offered,
            "network": node.get("network", "dev"),
            "ring": _as_int(node.get("ring"), 2),
            "cohort": node.get("cohort", ""),
            "request_index": request_index,
        }
        if scheduler_lab_run_id:
            metadata["scheduler_lab_run_id"] = str(scheduler_lab_run_id)
        idempotency_key = _scheduler_lab_idempotency_key(
            node_id=node_id,
            request_index=request_index,
            scheduler_lab_run_id=scheduler_lab_run_id,
        )
        metadata["idempotency_key"] = idempotency_key
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "model": model,
            "client_node_id": node_id,
            "idempotency_key": idempotency_key,
            "deadline_seconds": 60,
            "metadata": metadata,
        }
        if request_mode == "worker_pull_v0":
            payload["execution_mode"] = "worker_pull_v0"
            payload["account_id"] = account_id
            payload["max_credits"] = int(offered)
            payload["metadata"]["account_id"] = payload["account_id"]
            payload["metadata"]["max_credits"] = int(offered)
            auth = _node_multisession_authorization(node, max_authorized_credits=int(offered))
            if auth:
                payload["multisession_authorization"] = auth
                payload["payment_authorization"] = auth
                payload["wallet_address"] = auth["wallet_address"]
                payload["chain_id"] = auth.get("chain_id", node.get("chain_id", ""))
                payload["metadata"]["multisession_authorization"] = auth
                payload["metadata"]["wallet_address"] = auth["wallet_address"]
                payload["metadata"]["multisession_key_id"] = auth["multisession_key_id"]
                payload["metadata"]["auth_mode"] = "multisession-wallet"
        return self.post_json("/api/hub/v1/requests", payload)

    def get_credit_balance(self, account_id: str) -> HubHttpResponse:
        query = urlencode({"account_id": str(account_id)})
        return self.get_json(f"/api/hub/v1/credits/balance?{query}")

    def issue_credits(
        self,
        *,
        account_id: str,
        credits: int,
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> HubHttpResponse:
        return self.post_json(
            "/api/hub/v1/credits/admin/issue",
            {
                "account_id": str(account_id),
                "credits": max(0, int(credits)),
                "memo": str(memo),
                "metadata": dict(metadata or {}),
            },
        )




def _scheduler_lab_idempotency_key(*, node_id: str, request_index: int, scheduler_lab_run_id: str = "") -> str:
    """Return a Hub idempotency key scoped to one scheduler-lab run.

    The Hub deduplicates requester work by ``client_node_id`` and
    ``idempotency_key``. Scheduler-lab node ids and request indexes repeat across
    runs, so the run id must be part of the key when available. Otherwise a later
    clean run against the same Hub namespace can replay stale request records
    from a previous failed run instead of queuing fresh worker-pull work.
    """

    clean_node_id = str(node_id or "unknown-node").strip() or "unknown-node"
    clean_run_id = str(scheduler_lab_run_id or "").strip()
    try:
        clean_request_index = int(request_index)
    except Exception:
        clean_request_index = 0
    if clean_run_id:
        return f"{clean_run_id}:{clean_node_id}:{clean_request_index}"
    return f"{clean_node_id}-{clean_request_index}"


def _node_multisession_authorization(node: dict[str, Any], *, max_authorized_credits: int | None = None) -> dict[str, Any]:
    key_id = str(
        node.get("_multisession_key_id")
        or node.get("multisession_key_id")
        or ""
    ).strip()
    wallet_address = str(
        node.get("_wallet_address")
        or node.get("wallet_address")
        or ""
    ).strip().lower()
    if not key_id or not wallet_address:
        return {}
    auth: dict[str, Any] = {
        "kind": "multisession_key",
        "wallet_address": wallet_address,
        "multisession_key_id": key_id,
        "key_id": key_id,
        "chain_id": str(node.get("_multisession_chain_id") or node.get("chain_id") or ""),
    }
    if max_authorized_credits is not None:
        auth["max_authorized_credits"] = max(0, int(max_authorized_credits))
    return auth


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value == "":
            return default
        return int(value)
    except Exception:
        return default


def _split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    try:
        parsed = json.loads(str(value or "[]"))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def _choose_from_distribution(distribution_json: Any, fallback: Any, salt: int) -> Any:
    """Deterministically vary requester model/offer choices without sharing RNG state."""

    try:
        spec = json.loads(str(distribution_json or ""))
    except Exception:
        spec = None
    if not isinstance(spec, dict):
        return fallback
    values = spec.get("values")
    if not isinstance(values, list) or not values:
        return fallback
    total = sum(max(0.0, float(item.get("weight", 0))) for item in values if isinstance(item, dict))
    if total <= 0:
        return fallback
    pick = (hash((str(distribution_json), int(salt))) % 10_000_000) / 10_000_000 * total
    cursor = 0.0
    for item in values:
        if not isinstance(item, dict):
            continue
        cursor += max(0.0, float(item.get("weight", 0)))
        if pick <= cursor:
            return item.get("value", fallback)
    return values[-1].get("value", fallback) if isinstance(values[-1], dict) else fallback
