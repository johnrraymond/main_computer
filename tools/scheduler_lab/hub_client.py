from __future__ import annotations

import json
import random
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
        return self.post_json("/api/hub/v1/workers/heartbeat", payload)

    def poll_worker(self, node: dict[str, Any], *, lease_seconds: float) -> HubHttpResponse:
        return self.post_json(
            "/api/hub/v1/workers/poll",
            {
                "worker_node_id": str(node.get("node_id")),
                "lease_seconds": max(1.0, float(lease_seconds)),
            },
        )

    def submit_worker_result(self, node: dict[str, Any], lease: dict[str, Any], result: dict[str, Any]) -> HubHttpResponse:
        return self.post_json(
            "/api/hub/v1/workers/results",
            {
                "worker_node_id": str(node.get("node_id")),
                "request_id": str(lease.get("request_id") or ""),
                "lease_id": str(lease.get("lease_id") or ""),
                "result": result,
            },
        )

    def submit_request(
        self,
        node: dict[str, Any],
        *,
        request_index: int,
        request_mode: str,
        account_id_prefix: str,
        prompt: str,
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
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "model": model,
            "client_node_id": node_id,
            "idempotency_key": f"{node_id}-{request_index}",
            "deadline_seconds": 60,
            "metadata": metadata,
        }
        if request_mode == "worker_pull_v0":
            payload["execution_mode"] = "worker_pull_v0"
            payload["account_id"] = account_id
            payload["max_credits"] = int(offered)
            payload["metadata"]["account_id"] = payload["account_id"]
            payload["metadata"]["max_credits"] = int(offered)
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
