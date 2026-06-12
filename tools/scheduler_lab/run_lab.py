from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.scheduler_lab.hub_client import HubClient, HubHttpResponse
from tools.scheduler_lab.node_list import (
    DEFAULT_HUB_BASE_URL,
    DEFAULT_SEED,
    build_document,
    build_nodes,
    infer_total_from_filename,
    load_nodes,
    normalize_hub_base_urls,
    write_nodes,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_lab_session_id() -> str:
    """Return a filesystem-safe, per-run session id.

    The session id is intentionally embedded into every scheduler-lab artifact
    filename so old rollup schemas cannot be accidentally appended to a fresh
    run and so child events can be joined back to exactly one parent run.
    """

    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def safe_filename_token(value: Any, default: str = "node") -> str:
    text = str(value or default)
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)
    return clean or default


def lab_request_key_for(node: dict[str, Any], request_index: int) -> str:
    return f"{node.get('node_id') or 'node'}-{int(request_index)}"


def _parse_event_epoch_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * max(0.0, min(100.0, float(pct))) / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return round(ordered[lower], 3)
    weight = rank - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


@dataclass
class NodeRuntimeState:
    local_busy_until: float = 0.0
    request_blocked_until: float = 0.0
    worker_force_until: float = 0.0


@dataclass(frozen=True)
class WorktimeDistribution:
    """Normal result-runtime distribution in seconds for simulated workers."""

    mean_seconds: float
    sigma_seconds: float
    source: str


def _parse_seconds_number(raw: str, *, label: str) -> float:
    text = str(raw).strip().lower().replace("_", "")
    for suffix in ("seconds", "second", "secs", "sec", "s"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if not text:
        raise ValueError(f"{label} is empty")
    value = float(text)
    if value < 0:
        raise ValueError(f"{label} must be >= 0")
    return value


def parse_worktime_spec(value: Any) -> WorktimeDistribution | None:
    """Parse a scheduler-lab worker runtime spec.

    The compact form is seconds-based: ``100mu,30sigma`` means a normal
    distribution with mean 100 seconds and standard deviation 30 seconds.
    Positional ``100,30`` and named ``mu=100,sigma=30`` forms are also accepted.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    mean: float | None = None
    sigma: float | None = None
    positional: list[float] = []

    for part in text.split(","):
        token = part.strip().lower().replace(" ", "")
        if not token:
            continue
        if token.startswith("mu="):
            mean = _parse_seconds_number(token[3:], label="worktime mu")
            continue
        if token.startswith("mean="):
            mean = _parse_seconds_number(token[5:], label="worktime mean")
            continue
        if token.endswith("mu"):
            mean = _parse_seconds_number(token[:-2], label="worktime mu")
            continue
        if token.startswith("sigma="):
            sigma = _parse_seconds_number(token[6:], label="worktime sigma")
            continue
        if token.startswith("sd="):
            sigma = _parse_seconds_number(token[3:], label="worktime sigma")
            continue
        if token.endswith("sigma"):
            sigma = _parse_seconds_number(token[:-5], label="worktime sigma")
            continue
        if token.endswith("sd"):
            sigma = _parse_seconds_number(token[:-2], label="worktime sigma")
            continue
        positional.append(_parse_seconds_number(token, label="worktime"))

    if positional:
        if mean is None:
            mean = positional[0]
        if len(positional) > 1 and sigma is None:
            sigma = positional[1]
        if len(positional) > 2:
            raise ValueError(f"too many positional worktime values in {text!r}")

    if mean is None:
        raise ValueError(f"worktime must include a mean, for example 100mu,30sigma; got {text!r}")
    if sigma is None:
        sigma = 0.0
    if mean <= 0:
        raise ValueError("worktime mean must be > 0 seconds")
    return WorktimeDistribution(mean_seconds=mean, sigma_seconds=sigma, source=text)



def parse_warm_spec(value: Any) -> WorktimeDistribution | None:
    """Parse a node warm-up delay distribution in seconds.

    The compact form matches --worktime: ``2mu,1sigma`` means a normal
    distribution with mean 2 seconds and standard deviation 1 second. Unlike
    --worktime, a zero mean is allowed so tests can request an immediate wall.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    mean: float | None = None
    sigma: float | None = None
    positional: list[float] = []
    for part in text.split(","):
        token = part.strip().lower().replace(" ", "")
        if not token:
            continue
        if token.startswith("mu="):
            mean = _parse_seconds_number(token[3:], label="warm mu")
            continue
        if token.startswith("mean="):
            mean = _parse_seconds_number(token[5:], label="warm mean")
            continue
        if token.endswith("mu"):
            mean = _parse_seconds_number(token[:-2], label="warm mu")
            continue
        if token.startswith("sigma="):
            sigma = _parse_seconds_number(token[6:], label="warm sigma")
            continue
        if token.startswith("sd="):
            sigma = _parse_seconds_number(token[3:], label="warm sigma")
            continue
        if token.endswith("sigma"):
            sigma = _parse_seconds_number(token[:-5], label="warm sigma")
            continue
        if token.endswith("sd"):
            sigma = _parse_seconds_number(token[:-2], label="warm sigma")
            continue
        positional.append(_parse_seconds_number(token, label="warm"))
    if positional:
        if mean is None:
            mean = positional[0]
        if len(positional) > 1 and sigma is None:
            sigma = positional[1]
        if len(positional) > 2:
            raise ValueError(f"too many positional warm values in {text!r}")
    if mean is None:
        raise ValueError(f"warm must include a mean, for example 2mu,1sigma; got {text!r}")
    if sigma is None:
        sigma = 0.0
    return WorktimeDistribution(mean_seconds=mean, sigma_seconds=sigma, source=text)


def sample_warm_seconds(rng: random.Random, spec: WorktimeDistribution | None) -> float:
    if spec is None:
        return 0.0
    if spec.sigma_seconds <= 0:
        return max(0.0, spec.mean_seconds)
    value = rng.normalvariate(spec.mean_seconds, spec.sigma_seconds)
    for _ in range(8):
        if value >= 0:
            break
        value = rng.normalvariate(spec.mean_seconds, spec.sigma_seconds)
    cap = max(0.0, spec.mean_seconds * 3.0, spec.mean_seconds + 6.0 * spec.sigma_seconds)
    return min(max(0.0, value), cap)


def sample_worktime_seconds(rng: random.Random, spec: WorktimeDistribution) -> float:
    """Sample a positive normal-distribution runtime in seconds.

    Negative draws are retried a few times, then clamped to a small positive
    runtime. A broad cap prevents one pathological draw from consuming a whole
    lab run while still allowing 100s/30s-style experiments to behave slowly.
    """

    if spec.sigma_seconds <= 0:
        return spec.mean_seconds
    value = rng.normalvariate(spec.mean_seconds, spec.sigma_seconds)
    for _ in range(8):
        if value > 0:
            break
        value = rng.normalvariate(spec.mean_seconds, spec.sigma_seconds)
    value = max(0.05, value)
    cap = max(1.0, spec.mean_seconds * 3.0, spec.mean_seconds + 6.0 * spec.sigma_seconds)
    return min(value, cap)


def effective_request_startup_mode(args: argparse.Namespace) -> str:
    """Return how aggressively requester-capable nodes should begin traffic.

    ``auto`` means recovery/re-attach runs with a funded population should surge
    immediately, while cold-start runs keep the natural per-node startup delays.
    """

    mode = str(getattr(args, "request_startup_mode", "auto") or "auto").strip().lower()
    if mode not in {"auto", "natural", "surge"}:
        return "auto"
    if mode == "auto":
        funded_percent = float(getattr(args, "funded", 0.0) or 0.0)
        return "surge" if funded_percent > 0.0 else "natural"
    return mode


def should_send_startup_request(node: dict[str, Any], args: argparse.Namespace) -> bool:
    """Whether this node should submit one immediate request on re-attach."""

    if effective_request_startup_mode(args) != "surge":
        return False
    if getattr(args, "request_mode", "worker_pull_v0") == "registration_only":
        return False
    if not node_can_request(node):
        return False
    funded_percent = float(getattr(args, "funded", 0.0) or 0.0)
    if funded_percent > 0.0:
        return bool(node.get("_assumed_prefunded"))
    return True


async def submit_request_once(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    state: NodeRuntimeState,
    client: HubClient,
    rng: random.Random,
    request_index: int,
    event_name: str,
) -> HubHttpResponse:
    prompt = f"scheduler lab request {request_index} from {node.get('node_id')}"
    request_created_ts = utc_now()
    lab_request_key = lab_request_key_for(node, request_index)
    request_fields = {
        "lab_request_key": lab_request_key,
        "request_index": int(request_index),
        "requester_node_id": node.get("node_id"),
        "request_created_ts": request_created_ts,
        "account_id": node_account_id(node, args),
    }
    await sink.emit(event_payload("requester.request.attempted", node, attempted_event_name=event_name, **request_fields))
    response = await http_call(
        sink,
        node,
        event_name,
        client.submit_request,
        node,
        request_index=request_index,
        request_mode=args.request_mode,
        account_id_prefix=args.account_id_prefix,
        prompt=prompt,
        _event_fields=request_fields,
    )
    if is_insufficient_credit_response(response):
        await handle_low_credit_remediation(node, args=args, sink=sink, client=client, rng=rng, state=state, response=response)
    return response


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value == "":
            return default
        return int(value)
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value == "":
            return default
        return float(value)
    except Exception:
        return default


def env_flag(name: str, default: bool) -> bool:
    fallback = "1" if default else "0"
    return str(os.environ.get(name, fallback)).strip().lower() in {"1", "true", "yes", "on"}


def env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except Exception as exc:
        raise SystemExit(f"{name} must be an integer when set; got {raw!r}") from exc


def parse_funded_percent(value: Any) -> float:
    """Parse an already-funded account percentage.

    Accepts either percent values (``90`` or ``90%``) or fractions
    (``0.9``). The return value is always a percentage in [0, 100].
    """

    if value is None:
        return 0.0
    text = str(value).strip().lower().replace("_", "")
    if not text:
        return 0.0
    if text.endswith("%"):
        text = text[:-1]
    try:
        numeric = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"funded percent must be numeric; got {value!r}") from exc
    if numeric < 0:
        raise argparse.ArgumentTypeError("funded percent must be >= 0")
    if numeric <= 1:
        numeric *= 100.0
    if numeric > 100:
        raise argparse.ArgumentTypeError("funded percent must be <= 100")
    return numeric


def mark_assumed_prefunded_nodes(nodes: list[dict[str, Any]], *, funded_percent: float, seed: int) -> int:
    """Mark a deterministic share of nodes as accounts that already have credit.

    This is used for recovery/re-attach experiments where most accounts are
    expected to already exist in FDB. Marked nodes skip the bootstrap
    balance/admin-issue calls so startup traffic is not dominated by funding.
    """

    for node in nodes:
        node["_assumed_prefunded"] = False

    percent = max(0.0, min(100.0, float(funded_percent)))
    eligible = [node for node in nodes if as_int(node.get("initial_credits"), 0) > 0]
    if not eligible or percent <= 0.0:
        return 0

    target = int(round(len(eligible) * (percent / 100.0)))
    target = max(0, min(len(eligible), target))
    rng = random.Random(int(seed) ^ 0xF00D5EED)
    shuffled = list(eligible)
    rng.shuffle(shuffled)
    for node in shuffled[:target]:
        node["_assumed_prefunded"] = True
    return target


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def sample_lognormal_ms(rng: random.Random, median_ms: float, sigma: float, *, clamp_min: float = 0.0, clamp_max: float = 60_000.0) -> float:
    median_ms = max(0.001, float(median_ms))
    sigma = max(0.0, float(sigma))
    value = median_ms if sigma == 0 else rng.lognormvariate(math.log(median_ms), sigma)
    return max(clamp_min, min(clamp_max, value))


def sample_exponential_ms(rng: random.Random, mean_ms: float, *, clamp_min: float = 10.0, clamp_max: float = 60_000.0) -> float:
    mean_ms = max(0.001, float(mean_ms))
    value = rng.expovariate(1.0 / mean_ms)
    return max(clamp_min, min(clamp_max, value))


def hazard_probability_per_tick(per_minute: float, tick_seconds: float) -> float:
    clean_per_minute = max(0.0, float(per_minute))
    clean_tick = max(0.0, float(tick_seconds))
    return max(0.0, min(1.0, 1.0 - math.exp(-(clean_per_minute / 60.0) * clean_tick)))


def event_payload(kind: str, node: dict[str, Any], **fields: Any) -> dict[str, Any]:
    node_id = str(node.get("node_id") or "")
    account_id = str(node.get("account_id") or "")
    return {
        "ts": utc_now(),
        "event": kind,
        "node_id": node_id,
        "node_kind": node.get("kind", ""),
        "behavior_mode": node.get("behavior_mode", ""),
        "cohort": node.get("cohort", ""),
        "actor_node_id": node_id,
        "actor_node_kind": node.get("kind", ""),
        "actor_account_id": account_id,
        "actor_behavior_mode": node.get("behavior_mode", ""),
        "actor_cohort": node.get("cohort", ""),
        **fields,
    }


class EventSink:
    def __init__(self, output_dir: Path, run_id: str | None = None) -> None:
        self.output_dir = output_dir
        self.run_id = run_id or new_lab_session_id()
        self.events_path = output_dir / f"scheduler-lab-events-{self.run_id}.jsonl"
        self.summary_path = output_dir / f"scheduler-lab-summary-{self.run_id}.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self.events_path.open("a", encoding="utf-8", newline="\n")
        self._lock = asyncio.Lock()
        self.counts: Counter[str] = Counter()
        self.latency_ms: dict[str, list[float]] = {}

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._lock:
            event.setdefault("run_id", self.run_id)
            name = str(event.get("event", "unknown"))
            self.counts[name] += 1
            elapsed = event.get("elapsed_ms")
            if isinstance(elapsed, (int, float)):
                self.latency_ms.setdefault(name, []).append(float(elapsed))
            self._handle.write(json.dumps(event, sort_keys=True) + "\n")
            self._handle.flush()

    async def close(self) -> None:
        async with self._lock:
            self._handle.close()

    def summary(self, *, nodes: list[dict[str, Any]], started_at: float) -> dict[str, Any]:
        kinds = Counter(str(node.get("kind", "")) for node in nodes)
        cohorts = Counter(str(node.get("cohort", "")) for node in nodes)
        behavior_modes = Counter(str(node.get("behavior_mode", "")) for node in nodes if str(node.get("behavior_mode", "")))
        funding_remediation = Counter(str(node.get("funding_remediation", "")) for node in nodes if str(node.get("funding_remediation", "")))
        problematic = sum(1 for node in nodes if "problematic" in str(node.get("tags", "")).split(","))
        initial_credits = sum(as_int(node.get("initial_credits"), 0) for node in nodes)
        assumed_prefunded = sum(1 for node in nodes if bool(node.get("_assumed_prefunded")))
        latency_summary: dict[str, dict[str, float]] = {}
        for event_name, values in self.latency_ms.items():
            if not values:
                continue
            ordered = sorted(values)
            p95_index = min(len(ordered) - 1, int(math.ceil(len(ordered) * 0.95)) - 1)
            latency_summary[event_name] = {
                "count": len(ordered),
                "mean_ms": round(sum(ordered) / len(ordered), 3),
                "p95_ms": round(ordered[p95_index], 3),
                "max_ms": round(ordered[-1], 3),
            }
        return {
            "schema": "main-computer-hub-lab-run-summary/v3",
            "schema_version": 3,
            "generated_at": utc_now(),
            "run_id": self.run_id,
            "duration_observed_seconds": round(time.monotonic() - started_at, 3),
            "node_count": len(nodes),
            "node_kinds": dict(kinds),
            "cohorts": dict(cohorts),
            "behavior_modes": dict(behavior_modes),
            "funding_remediation": dict(funding_remediation),
            "problematic_nodes": problematic,
            "configured_initial_credits": initial_credits,
            "assumed_prefunded_nodes": assumed_prefunded,
            "events": dict(self.counts),
            "latency": latency_summary,
            "events_path": str(self.events_path),
        }

    def write_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def http_call(sink: EventSink, node: dict[str, Any], event_name: str, func, *args: Any, **kwargs: Any) -> HubHttpResponse:
    event_fields = dict(kwargs.pop("_event_fields", {}) or {})
    response: HubHttpResponse = await asyncio.to_thread(func, *args, **kwargs)
    response_identity = _response_identity_fields(response.payload)
    response_identity.update(event_fields)
    await sink.emit(
        event_payload(
            event_name,
            node,
            ok=response.ok,
            status=response.status,
            elapsed_ms=round(response.elapsed_ms, 3),
            hub_base_url=response.base_url,
            response_summary=_short_payload(response.payload),
            **response_identity,
        )
    )
    return response


def _short_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in ("ok", "error", "reason_code", "request_count", "idempotent"):
        if key in payload:
            clean[key] = payload[key]
    if isinstance(payload.get("account"), dict):
        account = payload["account"]
        clean["account"] = {
            "account_id": account.get("account_id"),
            "available_credits": account.get("available_credits", account.get("available_credits_display")),
            "held_credits": account.get("held_credits", account.get("held_credits_display")),
            "spent_credits": account.get("spent_credits", account.get("spent_credits_display")),
        }
    if isinstance(payload.get("transaction"), dict):
        tx = payload["transaction"]
        clean["transaction"] = {
            "transaction_id": tx.get("transaction_id"),
            "account_id": tx.get("account_id"),
            "credits": tx.get("credits"),
            "transaction_type": tx.get("transaction_type"),
        }
    if isinstance(payload.get("request"), dict):
        request = payload["request"]
        clean["request"] = {
            "request_id": request.get("request_id"),
            "state": request.get("state"),
            "error": request.get("error"),
        }
    if isinstance(payload.get("lease"), dict):
        lease = payload["lease"]
        clean["lease"] = {
            "request_id": lease.get("request_id"),
            "lease_id": lease.get("lease_id"),
            "model": lease.get("model"),
            "credits_per_request": lease.get("credits_per_request"),
        }
    elif payload.get("lease") is None:
        clean["lease"] = None
    return clean


def _response_identity_fields(payload: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return fields
    request = payload.get("request")
    if isinstance(request, dict):
        if request.get("request_id") is not None:
            fields["request_id"] = request.get("request_id")
        if request.get("state") is not None:
            fields["request_state"] = request.get("state")
    lease = payload.get("lease")
    if isinstance(lease, dict):
        if lease.get("request_id") is not None:
            fields["request_id"] = lease.get("request_id")
        if lease.get("lease_id") is not None:
            fields["lease_id"] = lease.get("lease_id")
        if lease.get("worker_id") is not None:
            fields["worker_id"] = lease.get("worker_id")
    return fields


def node_account_id(node: dict[str, Any], args: argparse.Namespace) -> str:
    return str(node.get("account_id") or f"{args.account_id_prefix}-{node.get('node_id')}")


def request_probability(node: dict[str, Any]) -> float:
    return max(0.0, min(1.0, as_float(node.get("request_probability"), 1.0 if node.get("kind") == "requester" else 0.0)))


def worker_offer_probability(node: dict[str, Any]) -> float:
    return max(0.0, min(1.0, as_float(node.get("worker_offer_probability"), 1.0 if node.get("kind") == "worker" else 0.0)))


def node_can_request(node: dict[str, Any]) -> bool:
    return request_probability(node) > 0.0 and str(node.get("offered_credits", "")).strip() != ""


def node_can_work(node: dict[str, Any]) -> bool:
    if worker_offer_probability(node) <= 0.0:
        return False
    if as_int(node.get("max_concurrency"), 0) <= 0:
        return False
    models = parse_json(node.get("models_json"), [])
    return bool(models or str(node.get("model", "")).strip())


def account_available_credits(payload: dict[str, Any]) -> int:
    account = payload.get("account") if isinstance(payload, dict) else None
    if not isinstance(account, dict):
        return 0
    for key in ("available_credits", "available_credits_display"):
        value = account.get(key)
        try:
            return int(float(str(value or "0").split()[0]))
        except Exception:
            continue
    return 0


def is_insufficient_credit_response(response: HubHttpResponse) -> bool:
    if response.status not in {400, 402}:
        return False
    error = str(response.payload.get("error", "") if isinstance(response.payload, dict) else "")
    reason = str(response.payload.get("reason_code", "") if isinstance(response.payload, dict) else "")
    text = f"{reason} {error}".lower()
    return "insufficient" in text and "credit" in text


async def top_up_account_to(
    *,
    sink: EventSink,
    node: dict[str, Any],
    client: HubClient,
    account_id: str,
    desired_credits: int,
    reason: str,
) -> None:
    desired = max(0, int(desired_credits))
    if desired <= 0:
        await sink.emit(event_payload("node.funding.skipped_zero_target", node, account_id=account_id, reason=reason))
        return

    balance = await http_call(sink, node, "node.funding.balance_checked", client.get_credit_balance, account_id)
    available = account_available_credits(balance.payload)
    if balance.ok and available >= desired:
        await sink.emit(event_payload("node.funding.not_needed", node, account_id=account_id, available_credits=available, desired_credits=desired, reason=reason))
        return

    delta = desired if not balance.ok else max(0, desired - available)
    if delta <= 0:
        await sink.emit(event_payload("node.funding.not_needed", node, account_id=account_id, available_credits=available, desired_credits=desired, reason=reason))
        return

    await http_call(
        sink,
        node,
        "node.funding.issued",
        client.issue_credits,
        account_id=account_id,
        credits=delta,
        memo=f"scheduler lab {reason} for {node.get('node_id')}",
        metadata={
            "scheduler_lab": True,
            "node_id": node.get("node_id"),
            "behavior_mode": node.get("behavior_mode", ""),
            "reason": reason,
            "desired_credits": desired,
            "observed_available_credits": available,
        },
    )


async def bootstrap_node_funding(node: dict[str, Any], *, args: argparse.Namespace, sink: EventSink, client: HubClient) -> None:
    if not args.bootstrap_funding:
        return
    desired = as_int(node.get("initial_credits"), 0)
    account_id = node_account_id(node, args)
    if desired <= 0:
        await sink.emit(event_payload("node.funding.bootstrap.skipped_unfunded_start", node, account_id=account_id))
        return
    if bool(node.get("_assumed_prefunded")):
        await sink.emit(
            event_payload(
                "node.funding.bootstrap.assumed_prefunded",
                node,
                account_id=account_id,
                desired_credits=desired,
                funded_percent=float(getattr(args, "funded", 0.0) or 0.0),
            )
        )
        return
    await top_up_account_to(
        sink=sink,
        node=node,
        client=client,
        account_id=account_id,
        desired_credits=desired,
        reason="bootstrap",
    )


async def maybe_start_local_work(
    node: dict[str, Any],
    *,
    state: NodeRuntimeState,
    sink: EventSink,
    rng: random.Random,
    tick_seconds: float,
) -> bool:
    now = time.monotonic()
    if now < state.local_busy_until:
        return True
    hazard = as_float(node.get("local_busy_probability_per_minute"), 0.0)
    if hazard <= 0:
        return False
    if rng.random() >= hazard_probability_per_tick(hazard, tick_seconds):
        return False
    duration_ms = sample_lognormal_ms(
        rng,
        as_float(node.get("local_busy_median_ms"), 0.0),
        0.55,
        clamp_min=250.0,
        clamp_max=max(250.0, as_float(node.get("local_busy_max_ms"), 60_000.0)),
    )
    state.local_busy_until = now + duration_ms / 1000.0
    await sink.emit(event_payload("node.local_work.started", node, duration_ms=round(duration_ms, 3), busy_until_monotonic=round(state.local_busy_until, 3)))
    return True


async def run_worker_loop(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    stop_at: float,
    state: NodeRuntimeState,
    client: HubClient,
    rng: random.Random,
) -> None:
    await http_call(sink, node, "worker.register", client.register_worker, node)

    heartbeat_interval = max(0.1, as_float(node.get("heartbeat_interval_ms"), 2000.0) / 1000.0)
    heartbeat_drop = max(0.0, min(1.0, as_float(node.get("heartbeat_drop_probability"), 0.0)))
    poll_interval = max(0.05, float(args.worker_poll_interval_ms) / 1000.0)
    active_requests = 0
    next_heartbeat = time.monotonic()

    while time.monotonic() < stop_at:
        now = time.monotonic()
        busy_with_local_work = False
        if args.enable_local_busy:
            busy_with_local_work = await maybe_start_local_work(node, state=state, sink=sink, rng=rng, tick_seconds=poll_interval)

        if now >= next_heartbeat:
            if rng.random() >= heartbeat_drop:
                status = "busy" if busy_with_local_work else "available"
                await http_call(sink, node, "worker.heartbeat", client.heartbeat_worker, node, active_requests=active_requests, status=status)
            else:
                await sink.emit(event_payload("worker.heartbeat.dropped_by_lab", node))
            jitter = as_float(node.get("heartbeat_jitter_ms"), 0.0) / 1000.0
            next_heartbeat = now + heartbeat_interval + rng.uniform(0.0, max(0.0, jitter))

        if busy_with_local_work:
            await sink.emit(event_payload("worker.poll.skipped_local_work", node, remaining_ms=round(max(0.0, state.local_busy_until - time.monotonic()) * 1000.0, 3)))
            await asyncio.sleep(min(poll_interval, max(0.0, stop_at - time.monotonic())))
            continue

        offer_probability = worker_offer_probability(node)
        if time.monotonic() < state.worker_force_until:
            offer_probability = max(offer_probability, 0.95)
        if rng.random() > offer_probability:
            await asyncio.sleep(min(poll_interval, max(0.0, stop_at - time.monotonic())))
            continue

        response = await http_call(sink, node, "worker.poll", client.poll_worker, node, lease_seconds=args.lease_seconds, _event_fields={"worker_node_id": node.get("node_id")})
        lease = response.payload.get("lease") if isinstance(response.payload, dict) else None
        if isinstance(lease, dict):
            active_requests += 1
            try:
                await execute_lease(node, lease, client=client, sink=sink, rng=rng, args=args)
            finally:
                active_requests = max(0, active_requests - 1)
        await asyncio.sleep(min(poll_interval, max(0.0, stop_at - time.monotonic())))


async def execute_lease(
    node: dict[str, Any],
    lease: dict[str, Any],
    *,
    client: HubClient,
    sink: EventSink,
    rng: random.Random,
    args: argparse.Namespace,
) -> None:
    if rng.random() < max(0.0, min(1.0, as_float(node.get("post_ready_disconnect_probability"), 0.0))):
        await sink.emit(event_payload("worker.lease.disconnect_before_result", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return
    if rng.random() < max(0.0, min(1.0, as_float(node.get("model_load_failure_probability"), 0.0))):
        await http_call(
            sink,
            node,
            "worker.result.failure_submitted",
            client.submit_worker_result,
            node,
            lease,
            {
                "status": "failed",
                "error": "scheduler lab simulated model load failure",
                "provider": "scheduler-lab",
                "model": lease.get("model") or node.get("model"),
            },
        )
        return

    worktime: WorktimeDistribution | None = getattr(args, "worktime_distribution", None)
    if worktime is not None:
        runtime_seconds = sample_worktime_seconds(rng, worktime)
        runtime_ms = runtime_seconds * 1000.0
        execution_started_ts = utc_now()
        await sink.emit(
            event_payload(
                "worker.execution.started",
                node,
                lease_id=lease.get("lease_id"),
                request_id=lease.get("request_id"),
                worker_node_id=node.get("node_id"),
                execution_started_ts=execution_started_ts,
                runtime_ms=round(runtime_ms, 3),
                worktime_source=worktime.source,
                worktime_mu_seconds=worktime.mean_seconds,
                worktime_sigma_seconds=worktime.sigma_seconds,
            )
        )
    else:
        normal_ms = as_float(node.get("runtime_normal_median_ms"), 1600.0)
        slow_ms = as_float(node.get("runtime_slow_median_ms"), 5500.0)
        runtime_ms = sample_lognormal_ms(rng, slow_ms if rng.random() < 0.15 else normal_ms, 0.45, clamp_min=10, clamp_max=args.max_runtime_ms)
        execution_started_ts = utc_now()
        await sink.emit(event_payload("worker.execution.started", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id"), worker_node_id=node.get("node_id"), execution_started_ts=execution_started_ts, runtime_ms=round(runtime_ms, 3)))
    await asyncio.sleep(runtime_ms / 1000.0)
    execution_finished_ts = utc_now()
    await sink.emit(event_payload("worker.execution.finished", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id"), worker_node_id=node.get("node_id"), execution_started_ts=locals().get("execution_started_ts"), execution_finished_ts=execution_finished_ts, runtime_ms=round(runtime_ms, 3)))

    if rng.random() < max(0.0, min(1.0, as_float(node.get("execution_crash_probability"), 0.0))):
        await sink.emit(event_payload("worker.execution.crashed", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return

    if rng.random() < max(0.0, min(1.0, as_float(node.get("result_submit_drop_probability"), 0.0))):
        await sink.emit(event_payload("worker.result.dropped_by_lab", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return

    delay_ms = sample_lognormal_ms(rng, as_float(node.get("result_submit_delay_median_ms"), 80.0), 0.45, clamp_min=0, clamp_max=5000)
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000.0)
    result = {
        "status": "success",
        "content": f"scheduler lab result from {node.get('node_id')} for {lease.get('request_id')}",
        "provider": "scheduler-lab",
        "model": lease.get("model") or node.get("model"),
        "metadata": {
            "scheduler_lab": True,
            "worker_node_id": node.get("node_id"),
            "cohort": node.get("cohort"),
            "lease_id": lease.get("lease_id"),
        },
    }
    result_fields = {
        "lease_id": lease.get("lease_id"),
        "request_id": lease.get("request_id"),
        "worker_node_id": node.get("node_id"),
        "execution_finished_ts": locals().get("execution_finished_ts"),
        "result_submitted_ts": utc_now(),
    }
    await http_call(sink, node, "worker.result.submitted", client.submit_worker_result, node, lease, result, _event_fields=result_fields)
    if rng.random() < max(0.0, min(1.0, as_float(node.get("result_submit_duplicate_probability"), 0.0))):
        duplicate_fields = dict(result_fields)
        duplicate_fields["result_submitted_ts"] = utc_now()
        await http_call(sink, node, "worker.result.duplicate_submitted", client.submit_worker_result, node, lease, result, _event_fields=duplicate_fields)


async def handle_low_credit_remediation(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    client: HubClient,
    rng: random.Random,
    state: NodeRuntimeState,
    response: HubHttpResponse,
) -> None:
    account_id = node_account_id(node, args)
    configured = str(node.get("funding_remediation") or "work_to_earn").strip().lower()
    remediation = configured
    if configured == "mixed":
        remediation = "work_to_earn" if node_can_work(node) and rng.random() < 0.70 else "faucet"

    backoff_s = max(0.1, as_float(node.get("insufficient_credit_backoff_ms"), 3000.0) / 1000.0)
    work_seconds = max(backoff_s, as_float(node.get("low_credit_work_seconds"), 30.0))

    await sink.emit(
        event_payload(
            "requester.request.rejected.insufficient_credits",
            node,
            account_id=account_id,
            remediation=configured,
            chosen_remediation=remediation,
            status=response.status,
            error=str(response.payload.get("error", "")) if isinstance(response.payload, dict) else "",
        )
    )

    if remediation == "faucet":
        desired = max(
            as_int(node.get("faucet_top_up_credits"), 0),
            as_int(node.get("low_credit_threshold"), 0) + max(1, as_int(node.get("offered_credits"), 1)),
        )
        await sink.emit(event_payload("node.low_credit.remediation_faucet", node, account_id=account_id, desired_credits=desired))
        await top_up_account_to(sink=sink, node=node, client=client, account_id=account_id, desired_credits=desired, reason="low_credit_faucet")
        state.request_blocked_until = time.monotonic() + backoff_s
        return

    if remediation == "work_to_earn" and node_can_work(node):
        state.worker_force_until = time.monotonic() + work_seconds
        state.request_blocked_until = state.worker_force_until
        await sink.emit(event_payload("node.low_credit.remediation_work_to_earn", node, account_id=account_id, work_seconds=round(work_seconds, 3)))
        return

    state.request_blocked_until = time.monotonic() + max(work_seconds, backoff_s)
    await sink.emit(
        event_payload(
            "node.low_credit.remediation_dormant",
            node,
            account_id=account_id,
            dormant_seconds=round(max(work_seconds, backoff_s), 3),
            requested_remediation=configured,
        )
    )


async def run_requester_loop(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    stop_at: float,
    state: NodeRuntimeState,
    client: HubClient,
    rng: random.Random,
) -> None:
    if args.request_mode == "registration_only":
        await sink.emit(event_payload("requester.skipped_registration_only", node))
        return

    request_index = 0
    mean_interval = max(10.0, as_float(node.get("request_interval_mean_ms"), 1400.0))
    burst_probability_per_minute = max(0.0, as_float(node.get("burst_probability_per_minute"), 0.0))
    burst_multiplier = max(1.0, as_float(node.get("burst_multiplier_median"), 1.0))

    if should_send_startup_request(node, args):
        request_index += 1
        await submit_request_once(
            node,
            args=args,
            sink=sink,
            state=state,
            client=client,
            rng=rng,
            request_index=request_index,
            event_name="requester.request.startup_surge",
        )

    while time.monotonic() < stop_at:
        interval_ms = sample_exponential_ms(rng, mean_interval, clamp_min=100, clamp_max=args.max_request_interval_ms)
        # Bursts lower the next interval, but remain deterministic under the node seed.
        if rng.random() < min(1.0, burst_probability_per_minute / 60.0):
            interval_ms = max(25.0, interval_ms / burst_multiplier)
            await sink.emit(event_payload("requester.burst_interval", node, interval_ms=round(interval_ms, 3)))

        if args.enable_local_busy and await maybe_start_local_work(node, state=state, sink=sink, rng=rng, tick_seconds=interval_ms / 1000.0):
            await sink.emit(event_payload("requester.request.skipped_local_work", node, remaining_ms=round(max(0.0, state.local_busy_until - time.monotonic()) * 1000.0, 3)))
            await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))
            continue

        if time.monotonic() < state.request_blocked_until:
            await sink.emit(event_payload("requester.request.skipped_low_credit_mode", node, remaining_ms=round(max(0.0, state.request_blocked_until - time.monotonic()) * 1000.0, 3)))
            await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))
            continue

        if rng.random() > request_probability(node):
            await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))
            continue

        request_index += 1
        await submit_request_once(
            node,
            args=args,
            sink=sink,
            state=state,
            client=client,
            rng=rng,
            request_index=request_index,
            event_name="requester.request.submitted",
        )
        await asyncio.sleep(min(interval_ms / 1000.0, max(0.0, stop_at - time.monotonic())))


async def run_node(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: EventSink,
    stop_at: float,
    worker_enabled: bool,
    requester_enabled: bool,
) -> None:
    seed = as_int(node.get("sim_seed"), DEFAULT_SEED)
    rng = random.Random(seed)
    hub_base_urls = normalize_hub_base_urls(
        getattr(args, "hub_base_urls", "") or node.get("hub_base_urls_json"),
        str(args.hub_base_url or node.get("hub_base_url") or DEFAULT_HUB_BASE_URL),
    )
    client = HubClient(
        hub_base_urls[0],
        base_urls=hub_base_urls,
        timeout_seconds=args.http_timeout_seconds,
        retries=args.http_retries,
        rng=random.Random(seed ^ 0x10ADBEEF),
    )
    startup_delay = as_float(node.get("startup_delay_ms"), 0.0) / 1000.0
    if effective_request_startup_mode(args) == "surge":
        spread_seconds = max(0.0, float(getattr(args, "request_startup_spread_seconds", 0.0) or 0.0))
        startup_delay = rng.uniform(0.0, spread_seconds) if spread_seconds else 0.0
    if startup_delay:
        await asyncio.sleep(min(startup_delay, max(0.0, stop_at - time.monotonic())))

    state = NodeRuntimeState()
    await bootstrap_node_funding(node, args=args, sink=sink, client=client)

    tasks: list[asyncio.Task[None]] = []
    if worker_enabled:
        tasks.append(
            asyncio.create_task(
                run_worker_loop(
                    node,
                    args=args,
                    sink=sink,
                    stop_at=stop_at,
                    state=state,
                    client=client,
                    rng=random.Random(seed ^ 0xA51C0DE),
                )
            )
        )
    if requester_enabled:
        tasks.append(
            asyncio.create_task(
                run_requester_loop(
                    node,
                    args=args,
                    sink=sink,
                    stop_at=stop_at,
                    state=state,
                    client=client,
                    rng=random.Random(seed ^ 0xC0FFEE),
                )
            )
        )

    if not tasks:
        await sink.emit(event_payload("node.skipped_no_enabled_behavior", node, worker_enabled=worker_enabled, requester_enabled=requester_enabled))
        return
    await asyncio.gather(*tasks, return_exceptions=True)


def select_nodes(nodes: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    if role == "all":
        return nodes
    if role == "workers":
        return [node for node in nodes if node_can_work(node)]
    if role == "requesters":
        return [node for node in nodes if node_can_request(node)]
    raise ValueError(f"unsupported role: {role}")



def prepare_selected_nodes(args: argparse.Namespace) -> tuple[list[dict[str, Any]], Path, int]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    node_list_path = Path(args.node_list)
    if args.generate_node_list or not node_list_path.exists():
        total = args.nodes if getattr(args, "nodes", None) is not None else args.total if args.total is not None else infer_total_from_filename(node_list_path)
        nodes = build_nodes(
            total=total,
            workers=args.workers,
            requesters=args.requesters,
            seed=args.seed,
            hub_base_url=args.hub_base_url,
            hub_base_urls=normalize_hub_base_urls(args.hub_base_urls, args.hub_base_url) if args.hub_base_urls else None,
            network=args.network,
            ring=args.ring,
            chain_id=args.chain_id,
            problematic_worker_rate=args.problematic_worker_rate,
            problematic_requester_rate=args.problematic_requester_rate,
            problematic_failure_multiplier=args.problematic_failure_multiplier,
            disable_problematic=args.disable_problematic,
        )
        document = build_document(
            nodes,
            seed=args.seed,
            hub_base_url=args.hub_base_url,
            hub_base_urls=normalize_hub_base_urls(args.hub_base_urls, args.hub_base_url) if args.hub_base_urls else None,
            network=args.network,
            ring=args.ring,
            chain_id=args.chain_id,
        )
        write_nodes(node_list_path, document)
    else:
        nodes = load_nodes(node_list_path)

    selected_nodes = select_nodes(nodes, args.role)
    assumed_prefunded_count = mark_assumed_prefunded_nodes(selected_nodes, funded_percent=float(getattr(args, "funded", 0.0) or 0.0), seed=args.seed)
    return selected_nodes, node_list_path, assumed_prefunded_count


def write_runtime_node_list(path: Path, nodes: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for node in nodes:
            handle.write(json.dumps(node, sort_keys=True) + "\n")


def _process_bool_flag(enabled: bool, *, positive: str, negative: str) -> str:
    return positive if enabled else negative



PROCESS_PHASE_COUNTERS = (
    "nodes_started",
    "warm_finished",
    "startup_request_attempted",
    "startup_request_http_response",
    "startup_request_transport_failures",
    "bootstrap_attempted",
    "bootstrap_assumed_prefunded",
    "bootstrap_balance_checked",
    "worker_register_attempted",
    "worker_register_http_response",
    "entered_runtime_loop",
    "self_terminated_b2bfailures",
)


def _process_event_node_key(event: dict[str, Any]) -> str:
    node_id = str(event.get("node_id") or "").strip()
    if node_id:
        return node_id
    node_index = event.get("node_index")
    if node_index is not None:
        return f"node-index:{node_index}"
    return ""


def _mark_process_phase(phase_nodes: dict[str, set[str]], event: dict[str, Any], counter: str) -> None:
    node_key = _process_event_node_key(event)
    if node_key:
        phase_nodes.setdefault(counter, set()).add(node_key)


def record_process_phase_event(event: dict[str, Any], phase_nodes: dict[str, set[str]]) -> None:
    """Record one child-process event into parent-visible phase counters.

    Counters are node-oriented rather than raw event counts so a node stuck in an
    immediate retry loop does not drown out the number of nodes that reached a
    phase.
    """

    name = str(event.get("event") or "")
    status = event.get("status")

    if name == "node.process.started":
        _mark_process_phase(phase_nodes, event, "nodes_started")
    elif name == "node.process.warm_finished":
        _mark_process_phase(phase_nodes, event, "warm_finished")
    elif name == "requester.request.startup_surge.attempted":
        _mark_process_phase(phase_nodes, event, "startup_request_attempted")
    elif name.startswith("requester.request.startup_surge"):
        if status == 0:
            _mark_process_phase(phase_nodes, event, "startup_request_transport_failures")
        elif status is not None:
            _mark_process_phase(phase_nodes, event, "startup_request_http_response")
    elif name == "node.funding.bootstrap.attempted":
        _mark_process_phase(phase_nodes, event, "bootstrap_attempted")
    elif name == "node.funding.bootstrap.assumed_prefunded":
        _mark_process_phase(phase_nodes, event, "bootstrap_assumed_prefunded")
    elif name == "node.funding.balance_checked":
        _mark_process_phase(phase_nodes, event, "bootstrap_balance_checked")
    elif name == "worker.register.attempted":
        _mark_process_phase(phase_nodes, event, "worker_register_attempted")
    elif name == "worker.register" and status is not None and status != 0:
        _mark_process_phase(phase_nodes, event, "worker_register_http_response")
    elif name == "node.process.runtime_entered":
        _mark_process_phase(phase_nodes, event, "entered_runtime_loop")
    elif name == "node.self_terminated.b2bfailures":
        _mark_process_phase(phase_nodes, event, "self_terminated_b2bfailures")


def process_child_event_path(output_dir: Path, node: dict[str, Any], node_index: int, run_id: str | None = None) -> Path:
    """Return the child event path without touching the filesystem.

    This intentionally mirrors ``SyncEventSink`` in node_process.py so the
    parent can keep a fixed list of event files to sample after spawn. It must
    stay parent-memory-only and must not glob or open child event files during
    launch.
    """

    safe_node_id = safe_filename_token(node.get("node_id") or "node")
    if run_id:
        return output_dir / f"node-process-{safe_filename_token(run_id)}-{node_index:05d}-{safe_node_id}.events.jsonl"
    return output_dir / f"node-process-{node_index:05d}-{safe_node_id}.events.jsonl"


def collect_process_phase_counts(
    output_dir: Path,
    offsets: dict[Path, int],
    phase_nodes: dict[str, set[str]],
    rollup_stats: dict[str, Counter[str]] | None = None,
    *,
    event_paths: Sequence[Path] | None = None,
    max_scan_seconds: float = 0.0,
    max_events_per_file: int = 256,
    scan_cursor_state: dict[str, Any] | None = None,
    scan_state: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Incrementally scan child event files and return current process phase counts.

    When ``event_paths`` is supplied, only those parent-known files are sampled;
    this avoids repeated directory globbing in large process-mode runs.

    The scanner is deliberately fair rather than catch-up greedy: each sample
    starts from a rotating cursor and reads at most ``max_events_per_file`` from
    any one child file before moving on. A few retry-storming nodes therefore
    cannot permanently starve the rest of the fleet from rollup visibility.
    """

    paths = sorted(output_dir.glob("node-process-*.events.jsonl")) if event_paths is None else list(event_paths)
    total_paths = len(paths)
    started = time.monotonic()
    deadline = started + max(0.0, float(max_scan_seconds)) if max_scan_seconds and max_scan_seconds > 0 else None
    raw_per_file_limit = int(max_events_per_file or 0)
    per_file_limit: int | None = None if raw_per_file_limit <= 0 else max(1, raw_per_file_limit)
    start_index = 0
    if total_paths and scan_cursor_state is not None:
        try:
            start_index = int(scan_cursor_state.get("next_index", 0)) % total_paths
        except Exception:
            start_index = 0

    files_scanned = 0
    events_scanned = 0
    files_limited = 0
    truncated = False
    reasons: set[str] = set()
    next_index = start_index

    if not paths:
        if scan_state is not None:
            scan_state.clear()
            scan_state.update(
                {
                    "rollup_scan_truncated": False,
                    "rollup_scan_reason": "",
                    "rollup_partial": False,
                    "rollup_partial_reason": "",
                    "rollup_files_total": 0,
                    "rollup_files_scanned": 0,
                    "rollup_files_limited": 0,
                    "rollup_events_scanned": 0,
                    "rollup_scan_start_index": 0,
                    "rollup_scan_next_index": 0,
                    "rollup_events_per_file_limit": int(per_file_limit or 0),
                    "rollup_scan_elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
        if scan_cursor_state is not None:
            scan_cursor_state["next_index"] = 0
        return process_phase_count_summary(phase_nodes)

    for ordinal in range(total_paths):
        path_index = (start_index + ordinal) % total_paths
        events_path = paths[path_index]
        next_index = (path_index + 1) % total_paths
        if deadline is not None and time.monotonic() >= deadline:
            truncated = True
            reasons.add("time_budget")
            next_index = path_index
            break
        try:
            with events_path.open("rb") as handle:
                handle.seek(offsets.get(events_path, 0))
                events_this_file = 0
                while True:
                    if per_file_limit is not None and events_this_file >= per_file_limit:
                        files_limited += 1
                        truncated = True
                        reasons.add("per_file_event_budget")
                        break
                    line_start = handle.tell()
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    if not raw_line.endswith(b"\n"):
                        handle.seek(line_start)
                        break
                    try:
                        event = json.loads(raw_line.decode("utf-8"))
                    except Exception:
                        continue
                    if isinstance(event, dict):
                        events_scanned += 1
                        events_this_file += 1
                        record_process_phase_event(event, phase_nodes)
                        if rollup_stats is not None:
                            record_process_rollup_event(event, rollup_stats)
                    if deadline is not None and events_scanned % 128 == 0 and time.monotonic() >= deadline:
                        truncated = True
                        reasons.add("time_budget")
                        break
                offsets[events_path] = handle.tell()
                files_scanned += 1
                if "time_budget" in reasons:
                    break
        except OSError:
            continue

    if scan_cursor_state is not None:
        scan_cursor_state["next_index"] = next_index

    reason = ",".join(sorted(reasons))
    if scan_state is not None:
        scan_state.clear()
        scan_state.update(
            {
                "rollup_scan_truncated": bool(truncated),
                "rollup_scan_reason": reason,
                "rollup_partial": bool(truncated),
                "rollup_partial_reason": reason,
                "rollup_files_total": int(total_paths),
                "rollup_files_scanned": int(files_scanned),
                "rollup_files_limited": int(files_limited),
                "rollup_events_scanned": int(events_scanned),
                "rollup_scan_start_index": int(start_index),
                "rollup_scan_next_index": int(next_index),
                "rollup_events_per_file_limit": int(per_file_limit or 0),
                "rollup_scan_elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
    return process_phase_count_summary(phase_nodes)


def process_phase_count_summary(phase_nodes: dict[str, set[str]]) -> dict[str, int]:
    return {counter: len(phase_nodes.get(counter, set())) for counter in PROCESS_PHASE_COUNTERS}


def process_parent_runtime_due_flags(
    *,
    now: float,
    parent_status_interval: float,
    next_parent_status_at: float,
    parent_rollup_interval: float,
    next_parent_rollup_at: float,
) -> dict[str, bool]:
    """Return runtime-loop due flags without letting status imply scans.

    Parent status is a cheap console/parent-event heartbeat. It must not drive
    child event-file scans; only minute rollups should do that expensive work.
    """

    status_due = parent_status_interval > 0.0 and now >= next_parent_status_at
    rollup_due = parent_rollup_interval > 0.0 and now >= next_parent_rollup_at
    return {
        "status_due": status_due,
        "rollup_due": rollup_due,
        "scan_due": rollup_due,
    }


def format_process_phase_counts(counts: dict[str, int]) -> str:
    labels = (
        ("nodes_started", "started"),
        ("warm_finished", "warm_done"),
        ("startup_request_attempted", "startup_req_attempted"),
        ("startup_request_http_response", "startup_req_http"),
        ("startup_request_transport_failures", "startup_req_transport_failures"),
        ("bootstrap_attempted", "bootstrap"),
        ("bootstrap_assumed_prefunded", "bootstrap_prefunded"),
        ("bootstrap_balance_checked", "balance_checked"),
        ("worker_register_attempted", "register"),
        ("worker_register_http_response", "register_http"),
        ("entered_runtime_loop", "runtime"),
        ("self_terminated_b2bfailures", "b2b_exit"),
    )
    return "[worker-lab] " + " ".join(f"{label}={int(counts.get(counter, 0))}" for counter, label in labels)



class ProcessLifecycleLedger:
    """Incremental request lifecycle table built from child events.

    The parent scanner feeds this object once per child event.  It keeps enough
    state to distinguish queue wait, worker execution time, and settlement time
    without making the clients easier or hiding transport failures.
    """

    def __init__(self) -> None:
        self.requests: dict[str, dict[str, Any]] = {}
        self.request_id_to_key: dict[str, str] = {}
        self.last_totals: dict[str, int] = {}
        self.settle_seconds: list[float] = []
        self.queue_wait_seconds: list[float] = []
        self.work_seconds: list[float] = []
        self.execution_total_seconds: list[float] = []

    def _event_ts(self, event: dict[str, Any]) -> float | None:
        for key in ("ts", "request_created_ts", "request_accepted_ts", "lease_acquired_ts", "execution_started_ts", "execution_finished_ts", "result_submitted_ts"):
            parsed = _parse_event_epoch_seconds(event.get(key))
            if parsed is not None:
                return parsed
        return None

    def _key_for_event(self, event: dict[str, Any], *, create: bool = False) -> str | None:
        lab_key = str(event.get("lab_request_key") or "").strip()
        request_id = str(event.get("request_id") or "").strip()
        if not request_id:
            response_summary = event.get("response_summary")
            if isinstance(response_summary, dict):
                request = response_summary.get("request")
                if isinstance(request, dict):
                    request_id = str(request.get("request_id") or "").strip()
                lease = response_summary.get("lease")
                if isinstance(lease, dict):
                    request_id = str(lease.get("request_id") or request_id or "").strip()
        if lab_key:
            if request_id:
                self.request_id_to_key[request_id] = lab_key
            return lab_key
        if request_id and request_id in self.request_id_to_key:
            return self.request_id_to_key[request_id]
        if request_id:
            return f"request:{request_id}"
        if create and event.get("requester_node_id") is not None and event.get("request_index") is not None:
            return f"{event.get('requester_node_id')}-{event.get('request_index')}"
        return None

    def _state(self, key: str) -> dict[str, Any]:
        return self.requests.setdefault(
            key,
            {
                "lab_request_key": key,
                "status": "observed",
            },
        )

    @staticmethod
    def _has_response_lease(event: dict[str, Any]) -> bool:
        response_summary = event.get("response_summary")
        if not isinstance(response_summary, dict):
            return False
        return isinstance(response_summary.get("lease"), dict)

    def record(self, event: dict[str, Any]) -> None:
        name = str(event.get("event") or "")
        status = event.get("status")
        ok = bool(event.get("ok"))
        event_ts = self._event_ts(event)
        if event_ts is None:
            event_ts = time.time()

        if name == "requester.request.attempted":
            key = self._key_for_event(event, create=True)
            if not key:
                return
            state = self._state(key)
            state.setdefault("created_at", _parse_event_epoch_seconds(event.get("request_created_ts")) or event_ts)
            state["attempted_at"] = event_ts
            state["requester_node_id"] = event.get("requester_node_id") or event.get("actor_node_id") or event.get("node_id")
            state["request_index"] = event.get("request_index")
            state["status"] = "attempted"
            return

        if name.startswith("requester.request.") and not name.endswith(".attempted") and status is not None:
            key = self._key_for_event(event, create=True)
            if not key:
                return
            state = self._state(key)
            state.setdefault("created_at", _parse_event_epoch_seconds(event.get("request_created_ts")) or event_ts)
            state["last_request_response_at"] = event_ts
            state["requester_node_id"] = event.get("requester_node_id") or event.get("actor_node_id") or event.get("node_id")
            if event.get("request_id") is not None:
                state["request_id"] = event.get("request_id")
                self.request_id_to_key[str(event.get("request_id"))] = key
            if ok:
                state.setdefault("accepted_at", event_ts)
                state["status"] = "accepted"
            else:
                state.setdefault("rejected_at", event_ts)
                state["status"] = "rejected"
            return

        if name == "worker.poll" and self._has_response_lease(event):
            key = self._key_for_event(event)
            if not key:
                return
            state = self._state(key)
            state.setdefault("leased_at", event_ts)
            state["lease_acquired_at"] = state.get("leased_at")
            state["worker_node_id"] = event.get("worker_node_id") or event.get("actor_node_id") or event.get("node_id")
            if event.get("lease_id") is not None:
                state["lease_id"] = event.get("lease_id")
            if event.get("request_id") is not None:
                state["request_id"] = event.get("request_id")
                self.request_id_to_key[str(event.get("request_id"))] = key
            state["status"] = "leased"
            return

        if name == "worker.execution.started":
            key = self._key_for_event(event)
            if not key:
                return
            state = self._state(key)
            state.setdefault("execution_started_at", _parse_event_epoch_seconds(event.get("execution_started_ts")) or event_ts)
            state["worker_node_id"] = event.get("worker_node_id") or event.get("actor_node_id") or event.get("node_id")
            state["status"] = "executing"
            return

        if name == "worker.execution.finished":
            key = self._key_for_event(event)
            if not key:
                return
            state = self._state(key)
            state.setdefault("execution_finished_at", _parse_event_epoch_seconds(event.get("execution_finished_ts")) or event_ts)
            state["worker_node_id"] = event.get("worker_node_id") or event.get("actor_node_id") or event.get("node_id")
            state["status"] = "execution_finished"
            return

        if name.startswith("worker.result.") and status is not None:
            key = self._key_for_event(event)
            if not key:
                return
            state = self._state(key)
            state.setdefault("result_submitted_at", _parse_event_epoch_seconds(event.get("result_submitted_ts")) or event_ts)
            state["worker_node_id"] = event.get("worker_node_id") or event.get("actor_node_id") or event.get("node_id")
            if ok:
                if "result_accepted_at" not in state:
                    state["result_accepted_at"] = event_ts
                    self._record_latencies(state)
                state["status"] = "settled"
            else:
                state.setdefault("result_rejected_at", event_ts)
                state["status"] = "result_rejected"

    def _record_latencies(self, state: dict[str, Any]) -> None:
        accepted = state.get("accepted_at")
        leased = state.get("leased_at") or state.get("lease_acquired_at")
        execution_started = state.get("execution_started_at")
        execution_finished = state.get("execution_finished_at")
        submitted = state.get("result_submitted_at")
        settled = state.get("result_accepted_at")
        if isinstance(accepted, (int, float)) and isinstance(settled, (int, float)) and settled >= accepted:
            self.settle_seconds.append(float(settled - accepted))
        if isinstance(accepted, (int, float)) and isinstance(leased, (int, float)) and leased >= accepted:
            self.queue_wait_seconds.append(float(leased - accepted))
        if isinstance(execution_started, (int, float)) and isinstance(execution_finished, (int, float)) and execution_finished >= execution_started:
            self.work_seconds.append(float(execution_finished - execution_started))
        elif isinstance(execution_started, (int, float)) and isinstance(submitted, (int, float)) and submitted >= execution_started:
            self.work_seconds.append(float(submitted - execution_started))
        if isinstance(leased, (int, float)) and isinstance(settled, (int, float)) and settled >= leased:
            self.execution_total_seconds.append(float(settled - leased))

    @staticmethod
    def _latency_summary(prefix: str, values: list[float]) -> dict[str, Any]:
        return {
            f"{prefix}_count": len(values),
            f"{prefix}_p50": _percentile(values, 50) if values else "",
            f"{prefix}_p90": _percentile(values, 90) if values else "",
            f"{prefix}_p99": _percentile(values, 99) if values else "",
        }

    def snapshot(self, *, mark_delta: bool = True) -> dict[str, Any]:
        now = time.time()
        totals = {
            "requests_attempted_total": 0,
            "requests_accepted_total": 0,
            "requests_rejected_total": 0,
            "requests_leased_total": 0,
            "requests_execution_started_total": 0,
            "requests_execution_finished_total": 0,
            "requests_result_submitted_total": 0,
            "requests_settled_total": 0,
        }
        oldest_open_age = 0.0
        oldest_leased_age = 0.0
        for state in self.requests.values():
            if state.get("attempted_at") is not None or state.get("created_at") is not None:
                totals["requests_attempted_total"] += 1
            if state.get("accepted_at") is not None:
                totals["requests_accepted_total"] += 1
            if state.get("rejected_at") is not None:
                totals["requests_rejected_total"] += 1
            if state.get("leased_at") is not None or state.get("lease_acquired_at") is not None:
                totals["requests_leased_total"] += 1
            if state.get("execution_started_at") is not None:
                totals["requests_execution_started_total"] += 1
            if state.get("execution_finished_at") is not None:
                totals["requests_execution_finished_total"] += 1
            if state.get("result_submitted_at") is not None:
                totals["requests_result_submitted_total"] += 1
            if state.get("result_accepted_at") is not None:
                totals["requests_settled_total"] += 1

            accepted = state.get("accepted_at")
            settled = state.get("result_accepted_at")
            leased = state.get("leased_at") or state.get("lease_acquired_at")
            if isinstance(accepted, (int, float)) and settled is None:
                oldest_open_age = max(oldest_open_age, now - float(accepted))
            if isinstance(leased, (int, float)) and settled is None:
                oldest_leased_age = max(oldest_leased_age, now - float(leased))

        open_requests = max(0, totals["requests_accepted_total"] - totals["requests_settled_total"] - totals["requests_rejected_total"])
        leased_open = max(0, totals["requests_leased_total"] - totals["requests_settled_total"])
        executing_open = max(0, totals["requests_execution_started_total"] - totals["requests_execution_finished_total"])
        deltas: dict[str, int] = {}
        if mark_delta:
            for key, value in totals.items():
                deltas[key.replace("_total", "_delta")] = int(value) - int(self.last_totals.get(key, 0))
            deltas["open_requests_delta"] = open_requests - int(self.last_totals.get("open_requests_total", 0))
            self.last_totals = {**totals, "open_requests_total": open_requests}
        else:
            deltas = {key.replace("_total", "_delta"): 0 for key in totals}
            deltas["open_requests_delta"] = 0

        summary: dict[str, Any] = {
            **totals,
            **deltas,
            "open_requests_total": open_requests,
            "leased_open_total": leased_open,
            "executing_open_total": executing_open,
            "oldest_open_request_age_seconds": round(oldest_open_age, 3),
            "oldest_leased_request_age_seconds": round(oldest_leased_age, 3),
        }
        summary.update(self._latency_summary("settle_seconds", self.settle_seconds))
        summary.update(self._latency_summary("queue_wait_seconds", self.queue_wait_seconds))
        summary.update(self._latency_summary("work_seconds", self.work_seconds))
        summary.update(self._latency_summary("execution_total_seconds", self.execution_total_seconds))
        return summary


class BehaviorLedger:
    """Forgetful typed tat ledger for nodes, accounts, hubs, and edges."""

    HALF_LIFE_SECONDS = {
        "transport": 900.0,
        "performance": 1800.0,
        "scheduler": 1800.0,
        "protocol": 3600.0,
        "economic": 1800.0,
        "integrity": 7200.0,
        "positive": 900.0,
    }

    TAT_WEIGHTS = {
        "transport.no_http_response": 3.0,
        "transport.recovered": -2.0,
        "economic.insufficient_credit": 1.0,
        "protocol.http_4xx": 2.0,
        "protocol.http_5xx": 4.0,
        "integrity.duplicate_result": 6.0,
        "integrity.unknown_request_id": 4.0,
        "scheduler.request_rejected": 1.0,
        "scheduler.result_rejected": 3.0,
        "performance.execution_crashed": 5.0,
        "performance.result_dropped": 4.0,
        "performance.disconnect_before_result": 4.0,
        "positive.http_response": -0.5,
        "positive.request_accepted": -1.0,
        "positive.lease_acquired": -1.0,
        "positive.result_accepted": -2.0,
    }

    def __init__(self) -> None:
        self.subjects: dict[str, dict[str, Any]] = {}
        self.tat_counts: Counter[str] = Counter()

    @staticmethod
    def _family(tat_type: str) -> str:
        return tat_type.split(".", 1)[0] if "." in tat_type else tat_type

    def _event_ts(self, event: dict[str, Any]) -> float:
        return _parse_event_epoch_seconds(event.get("ts")) or time.time()

    def _subject(self, kind: str, subject_id: Any) -> tuple[str, dict[str, Any]] | None:
        text = str(subject_id or "").strip()
        if not text:
            return None
        key = f"{kind}:{text}"
        return key, self.subjects.setdefault(
            key,
            {
                "subject_kind": kind,
                "subject_id": text,
                "score": 0.0,
                "family_scores": {},
                "tat_counts": Counter(),
                "last_seen_at": 0.0,
                "last_good_at": 0.0,
                "last_bad_at": 0.0,
            },
        )

    def _apply_decay(self, subject: dict[str, Any], family: str, ts_seconds: float) -> None:
        last_seen = float(subject.get("last_seen_at") or 0.0)
        if last_seen <= 0.0 or ts_seconds <= last_seen:
            subject["last_seen_at"] = max(last_seen, ts_seconds)
            return
        elapsed = ts_seconds - last_seen
        half_life = max(1.0, float(self.HALF_LIFE_SECONDS.get(family, 1800.0)))
        factor = 0.5 ** (elapsed / half_life)
        subject["score"] = float(subject.get("score") or 0.0) * factor
        family_scores = dict(subject.get("family_scores") or {})
        for key, value in list(family_scores.items()):
            family_scores[key] = float(value) * factor
        subject["family_scores"] = family_scores
        subject["last_seen_at"] = ts_seconds

    def _subjects_for_event(self, event: dict[str, Any], tat_type: str) -> list[tuple[str, Any]]:
        name = str(event.get("event") or "")
        node_id = event.get("actor_node_id") or event.get("node_id")
        account_id = event.get("actor_account_id") or event.get("account_id")
        hub = str(event.get("hub_base_url") or "").strip().rstrip("/")
        endpoint = _process_rollup_endpoint_key(name) or name or "event"
        ip_addr = event.get("actor_ip_or_observed_addr") or event.get("observed_ip") or event.get("remote_addr")
        result: list[tuple[str, Any]] = []

        # Transport failures are often shared counterparty evidence. In a hub-wide
        # storm, charging every node/account with temporary-ban grade badness gives
        # the wrong driving instruction. Prefer the hub and directed edge; only
        # fall back to actor-level transport attribution when there is no observed
        # counterparty at all.
        if tat_type.startswith("transport.") and hub:
            result.append(("hub_endpoint", hub))
            if node_id:
                result.append(("edge", f"{node_id}->{hub}/{endpoint}"))
            return result

        if node_id:
            result.append(("node", node_id))
        if account_id:
            result.append(("account", account_id))
        if ip_addr:
            result.append(("ip", ip_addr))
        if hub:
            result.append(("hub_endpoint", hub))
        if node_id and hub:
            result.append(("edge", f"{node_id}->{hub}/{endpoint}"))
        return result

    def _classify_tats(self, event: dict[str, Any]) -> list[str]:
        name = str(event.get("event") or "")
        status = event.get("status")
        ok = bool(event.get("ok"))
        tats: list[str] = []

        try:
            status_int = int(status)
        except Exception:
            status_int = None

        if name == "node.transport_failure":
            # This is a synthetic companion event emitted after a concrete HTTP
            # operation has already logged status=0 with the endpoint and hub.
            # Keep it for phase/event counters, but do not feed it into the
            # behavior ledger or hub/node scores a second time.
            return tats
        if status_int == 0:
            tats.append("transport.no_http_response")
        elif status_int is not None:
            if is_insufficient_credit_response(
                HubHttpResponse(ok=False, status=status_int, payload=event.get("response_summary") if isinstance(event.get("response_summary"), dict) else {}, elapsed_ms=0.0)
            ):
                tats.append("economic.insufficient_credit")
            elif 400 <= status_int < 500:
                tats.append("protocol.http_4xx")
            elif status_int >= 500:
                tats.append("protocol.http_5xx")
            elif ok:
                tats.append("positive.http_response")

        if name.startswith("requester.request.") and status_int is not None and status_int != 0:
            tats.append("positive.request_accepted" if ok else "scheduler.request_rejected")
        if name == "worker.poll" and ProcessLifecycleLedger._has_response_lease(event):
            tats.append("positive.lease_acquired")
        if name == "worker.result.submitted" and status_int is not None and status_int != 0:
            tats.append("positive.result_accepted" if ok else "scheduler.result_rejected")
        if name == "worker.result.duplicate_submitted":
            tats.append("integrity.duplicate_result")
        if name == "worker.execution.crashed":
            tats.append("performance.execution_crashed")
        if name == "worker.result.dropped_by_lab":
            tats.append("performance.result_dropped")
        if name == "worker.lease.disconnect_before_result":
            tats.append("performance.disconnect_before_result")
        if name == "node.transport_failures.reset":
            tats.append("transport.recovered")

        return tats

    def record(self, event: dict[str, Any]) -> None:
        ts_seconds = self._event_ts(event)
        for tat_type in self._classify_tats(event):
            family = self._family(tat_type)
            weight = float(self.TAT_WEIGHTS.get(tat_type, 1.0))
            self.tat_counts[tat_type] += 1
            for kind, subject_id in self._subjects_for_event(event, tat_type):
                subject_pair = self._subject(kind, subject_id)
                if subject_pair is None:
                    continue
                _key, subject = subject_pair
                self._apply_decay(subject, family, ts_seconds)
                family_scores = dict(subject.get("family_scores") or {})
                family_scores[family] = float(family_scores.get(family, 0.0)) + weight
                subject["family_scores"] = family_scores
                subject["score"] = max(0.0, float(subject.get("score") or 0.0) + weight)
                subject["tat_counts"][tat_type] += 1
                if weight < 0:
                    subject["last_good_at"] = ts_seconds
                elif weight > 0:
                    subject["last_bad_at"] = ts_seconds
                subject["last_seen_at"] = ts_seconds

    @staticmethod
    def _state_for(subject: dict[str, Any]) -> str:
        score = float(subject.get("score") or 0.0)
        kind = str(subject.get("subject_kind") or "")
        family_scores = subject.get("family_scores") if isinstance(subject.get("family_scores"), dict) else {}
        integrity = float(family_scores.get("integrity", 0.0))
        protocol = float(family_scores.get("protocol", 0.0))
        transport = float(family_scores.get("transport", 0.0))
        non_transport = max(0.0, score - max(0.0, transport))
        if score < 1.0:
            return "allow"
        if kind == "hub_endpoint" and transport >= 9.0:
            return "probe"
        if kind in {"node", "account", "ip", "edge"} and transport >= 5.0 and non_transport < 3.0:
            # Pure no-response evidence says "monitor/deprioritize this path";
            # it is not enough to ban an actor because the counterparty may be the
            # thing on fire. Integrity/protocol/economic/performance evidence can
            # still escalate below.
            return "deprioritize" if score >= 18.0 else "monitor"
        if kind == "ip" and (integrity + protocol) >= 15.0:
            return "temporary_ban"
        if score >= 18.0:
            return "temporary_ban" if kind in {"node", "ip", "account"} else "quarantine"
        if score >= 10.0:
            return "quarantine"
        if score >= 5.0:
            return "deprioritize"
        return "monitor"

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        state_counts: Counter[str] = Counter()
        top: list[dict[str, Any]] = []
        for subject in self.subjects.values():
            state = self._state_for(subject)
            state_counts[state] += 1
            if state == "allow":
                continue
            top.append(
                {
                    "subject_kind": subject.get("subject_kind"),
                    "subject_id": subject.get("subject_id"),
                    "behavior_state": state,
                    "score": round(float(subject.get("score") or 0.0), 3),
                    "family_scores": {key: round(float(value), 3) for key, value in sorted(dict(subject.get("family_scores") or {}).items())},
                    "tat_counts": dict(subject.get("tat_counts", Counter())),
                    "last_seen_age_seconds": round(max(0.0, now - float(subject.get("last_seen_at") or now)), 3),
                    "forgetful": True,
                }
            )
        top.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)

        by_kind_state: Counter[str] = Counter()
        for item in top:
            by_kind_state[f"{item.get('subject_kind')}_{item.get('behavior_state')}"] += 1

        return {
            "tat_counts": dict(sorted(self.tat_counts.items())),
            "behavior_state_counts": dict(sorted(state_counts.items())),
            "behavior_nodes_monitor": by_kind_state.get("node_monitor", 0),
            "behavior_nodes_quarantine": by_kind_state.get("node_quarantine", 0),
            "behavior_nodes_temporary_ban": by_kind_state.get("node_temporary_ban", 0),
            "behavior_ips_temporary_ban": by_kind_state.get("ip_temporary_ban", 0),
            "behavior_hubs_probe": by_kind_state.get("hub_endpoint_probe", 0),
            "behavior_reinstated_delta": 0,
            "top_behavior": top[:10],
        }


def _ledger_from_stats(rollup_stats: dict[str, Any], key: str, factory) -> Any:
    value = rollup_stats.get(key)
    if value is None:
        value = factory()
        rollup_stats[key] = value
    return value


def classify_process_rollup(rollup: dict[str, Any]) -> dict[str, Any]:
    lifecycle = rollup.get("lifecycle", {}) if isinstance(rollup.get("lifecycle"), dict) else {}
    transport_ratio = float(rollup.get("transport_failure_ratio", 0.0) or 0.0)
    register_attempted = int(rollup.get("worker_register_attempted", 0) or 0)
    register_http = int(rollup.get("worker_register_http_response", 0) or 0)
    accepted_delta = int(lifecycle.get("requests_accepted_delta", 0) or 0)
    settled_delta = int(lifecycle.get("requests_settled_delta", 0) or 0)
    settled_total = int(lifecycle.get("requests_settled_total", 0) or 0)
    open_total = int(lifecycle.get("open_requests_total", 0) or 0)
    queue_p90 = lifecycle.get("queue_wait_seconds_p90")
    work_p50 = lifecycle.get("work_seconds_p50")
    reasons: list[str] = []

    if transport_ratio > 0.90:
        reasons.append(f"transport_failure_ratio={transport_ratio}")
        return {
            "transport_dominated": True,
            "settlement_metrics_representative": False,
            "lab_interpretation": "transport_dominated",
            "lab_interpretation_reason": "; ".join(reasons),
        }

    if register_attempted > 0 and register_http == 0:
        reasons.append("worker_register_http_response=0")
        return {
            "transport_dominated": False,
            "settlement_metrics_representative": False,
            "lab_interpretation": "registration_dominated",
            "lab_interpretation_reason": "; ".join(reasons),
        }

    if accepted_delta > settled_delta and register_http > 0:
        reasons.append(f"accepted_delta={accepted_delta} > settled_delta={settled_delta}")
        if queue_p90 not in {"", None}:
            reasons.append(f"queue_wait_seconds_p90={queue_p90}")
        if work_p50 not in {"", None}:
            reasons.append(f"work_seconds_p50={work_p50}")
        return {
            "transport_dominated": False,
            "settlement_metrics_representative": settled_total > 0,
            "lab_interpretation": "queue_dominated",
            "lab_interpretation_reason": "; ".join(reasons),
        }

    if settled_total == 0 and (accepted_delta > 0 or open_total > 0):
        reasons.append(f"settled_total=0; open_requests_total={open_total}")
        return {
            "transport_dominated": False,
            "settlement_metrics_representative": False,
            "lab_interpretation": "insufficient_data",
            "lab_interpretation_reason": "; ".join(reasons),
        }

    if settled_delta >= accepted_delta and settled_delta > 0:
        reasons.append(f"settled_delta={settled_delta} >= accepted_delta={accepted_delta}")
        return {
            "transport_dominated": False,
            "settlement_metrics_representative": True,
            "lab_interpretation": "settlement_healthy",
            "lab_interpretation_reason": "; ".join(reasons),
        }

    return {
        "transport_dominated": False,
        "settlement_metrics_representative": settled_total > 0,
        "lab_interpretation": "insufficient_data",
        "lab_interpretation_reason": "not enough accepted/settled lifecycle observations",
    }



PROCESS_ROLLUP_CSV_PHASE_COLUMNS = tuple(counter for counter in PROCESS_PHASE_COUNTERS if counter != "nodes_started")

PROCESS_ROLLUP_CSV_COLUMNS = (
    "schema_version",
    "run_id",
    "generated_at",
    "event",
    "rollup_scope",
    "rollup_source",
    "elapsed_seconds",
    "node_count",
    "nodes_started",
    "nodes_alive",
    "nodes_exited",
    "children_launched",
    "children_remaining",
    "rollup_scan_truncated",
    "rollup_scan_reason",
    "rollup_partial",
    "rollup_partial_reason",
    "rollup_files_total",
    "rollup_files_scanned",
    "rollup_files_limited",
    "rollup_events_scanned",
    "rollup_scan_start_index",
    "rollup_scan_next_index",
    "rollup_events_per_file_limit",
    "rollup_scan_elapsed_seconds",
    "transport_failures",
    "market_http_responses",
    "transport_failure_ratio",
    "transport_dominated",
    "settlement_metrics_representative",
    "lab_interpretation",
    "lab_interpretation_reason",
    *PROCESS_ROLLUP_CSV_PHASE_COLUMNS,
    "requests_attempted_total",
    "requests_accepted_total",
    "requests_rejected_total",
    "requests_leased_total",
    "requests_execution_started_total",
    "requests_execution_finished_total",
    "requests_result_submitted_total",
    "requests_settled_total",
    "open_requests_total",
    "leased_open_total",
    "executing_open_total",
    "requests_accepted_delta",
    "requests_leased_delta",
    "requests_settled_delta",
    "open_requests_delta",
    "settle_seconds_count",
    "settle_seconds_p50",
    "settle_seconds_p90",
    "settle_seconds_p99",
    "queue_wait_seconds_count",
    "queue_wait_seconds_p50",
    "queue_wait_seconds_p90",
    "queue_wait_seconds_p99",
    "work_seconds_count",
    "work_seconds_p50",
    "work_seconds_p90",
    "work_seconds_p99",
    "oldest_open_request_age_seconds",
    "oldest_leased_request_age_seconds",
    "behavior_nodes_monitor",
    "behavior_nodes_quarantine",
    "behavior_nodes_temporary_ban",
    "behavior_ips_temporary_ban",
    "behavior_hubs_probe",
    "behavior_reinstated_delta",
    "event_counts_json",
    "http_status_counts_json",
    "endpoint_counts_json",
    "hub_counts_json",
    "exit_codes_json",
    "lifecycle_json",
    "behavior_json",
    "top_behavior_json",
)



def new_process_rollup_stats() -> dict[str, Any]:
    return {
        "event_counts": Counter(),
        "http_status_counts": Counter(),
        "endpoint_counts": Counter(),
        "hub_counts": Counter(),
        "lifecycle_ledger": ProcessLifecycleLedger(),
        "behavior_ledger": BehaviorLedger(),
    }


def _process_rollup_status_key(status: Any) -> str:
    try:
        return str(int(status))
    except Exception:
        return str(status)


def _process_rollup_hub_key(event: dict[str, Any]) -> str:
    base_url = str(event.get("hub_base_url") or "").strip().rstrip("/")
    if not base_url:
        return ""
    suffix = base_url.rsplit(":", 1)[-1]
    if suffix.isdigit():
        return suffix
    return base_url


def _process_rollup_endpoint_key(event_name: str) -> str:
    if event_name.startswith("requester.request"):
        return "requests"
    if event_name == "node.funding.balance_checked":
        return "credits_balance"
    if event_name == "node.funding.issued":
        return "credits_issue"
    if event_name == "worker.register":
        return "workers_register"
    if event_name == "worker.heartbeat":
        return "workers_heartbeat"
    if event_name == "worker.poll":
        return "workers_poll"
    if event_name.startswith("worker.result"):
        return "worker_results"
    if event_name == "node.transport_failure":
        return "transport_failures"
    return ""


def record_process_rollup_event(event: dict[str, Any], rollup_stats: dict[str, Any]) -> None:
    """Record one child event into cumulative process-mode rollup counters."""

    name = str(event.get("event") or "unknown")
    rollup_stats.setdefault("event_counts", Counter())[name] += 1

    if "status" in event:
        rollup_stats.setdefault("http_status_counts", Counter())[_process_rollup_status_key(event.get("status"))] += 1

    hub_key = _process_rollup_hub_key(event)
    if hub_key:
        rollup_stats.setdefault("hub_counts", Counter())[hub_key] += 1

    endpoint_key = _process_rollup_endpoint_key(name)
    if endpoint_key:
        rollup_stats.setdefault("endpoint_counts", Counter())[endpoint_key] += 1

    _ledger_from_stats(rollup_stats, "lifecycle_ledger", ProcessLifecycleLedger).record(event)
    _ledger_from_stats(rollup_stats, "behavior_ledger", BehaviorLedger).record(event)


def build_process_rollup(
    *,
    run_id: str,
    started_at: float,
    node_count: int,
    assumed_prefunded_count: int,
    children: list[tuple[dict[str, Any], subprocess.Popen[bytes]]],
    phase_nodes: dict[str, set[str]],
    rollup_stats: dict[str, Counter[str]],
    event_name: str = "lab.process_parent.rollup",
) -> dict[str, Any]:
    phase_counts = process_phase_count_summary(phase_nodes)
    exit_codes = Counter(str(int(process.poll())) for _node, process in children if process.poll() is not None)
    nodes_alive = sum(1 for _node, process in children if process.poll() is None)
    event_counts = dict(sorted(rollup_stats.get("event_counts", Counter()).items()))
    http_status_counts = dict(sorted(rollup_stats.get("http_status_counts", Counter()).items()))
    endpoint_counts = dict(sorted(rollup_stats.get("endpoint_counts", Counter()).items()))
    hub_counts = dict(sorted(rollup_stats.get("hub_counts", Counter()).items()))
    transport_failures = max(int(endpoint_counts.get("transport_failures", 0)), int(http_status_counts.get("0", 0)))
    market_http_responses = sum(int(count) for status, count in http_status_counts.items() if str(status) != "0")
    total_observed_attempts = transport_failures + market_http_responses
    transport_failure_ratio = round(transport_failures / total_observed_attempts, 6) if total_observed_attempts else 0.0
    lifecycle = _ledger_from_stats(rollup_stats, "lifecycle_ledger", ProcessLifecycleLedger).snapshot(mark_delta=True)
    behavior = _ledger_from_stats(rollup_stats, "behavior_ledger", BehaviorLedger).snapshot()
    rollup: dict[str, Any] = {
        "event": event_name,
        "schema": "main-computer-hub-lab-process-rollup/v3",
        "schema_version": 3,
        "run_id": run_id,
        "generated_at": utc_now(),
        "rollup_scope": "complete",
        "rollup_source": "child_event_scan",
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "execution_mode": "process",
        "node_count": int(node_count),
        "nodes_started": len(children),
        "nodes_alive": nodes_alive,
        "nodes_exited": max(0, len(children) - nodes_alive),
        "assumed_prefunded_nodes": int(assumed_prefunded_count),
        "phase_counts": phase_counts,
        "event_counts": event_counts,
        "http_status_counts": http_status_counts,
        "endpoint_counts": endpoint_counts,
        "hub_counts": hub_counts,
        "exit_codes": dict(sorted(exit_codes.items())),
        "self_terminated_b2bfailures": int(exit_codes.get("75", 0)),
        "children_launched": len(children),
        "children_remaining": max(0, int(node_count) - len(children)),
        "rollup_scan_truncated": False,
        "rollup_scan_reason": "",
        "rollup_partial": False,
        "rollup_partial_reason": "",
        "rollup_files_total": 0,
        "rollup_files_scanned": 0,
        "rollup_files_limited": 0,
        "rollup_events_scanned": 0,
        "rollup_scan_start_index": 0,
        "rollup_scan_next_index": 0,
        "rollup_events_per_file_limit": 0,
        "rollup_scan_elapsed_seconds": 0.0,
        "transport_failures": transport_failures,
        "market_http_responses": int(market_http_responses),
        "transport_failure_ratio": transport_failure_ratio,
        "lifecycle": lifecycle,
        "behavior": behavior,
        "tat_counts": behavior.get("tat_counts", {}),
        "behavior_state_counts": behavior.get("behavior_state_counts", {}),
        "top_behavior": behavior.get("top_behavior", []),
    }
    rollup.update(phase_counts)
    rollup.update(lifecycle)
    rollup.update({key: value for key, value in behavior.items() if key.startswith("behavior_")})
    rollup.update(classify_process_rollup(rollup))
    return rollup


def build_process_launch_progress_rollup(
    *,
    run_id: str,
    started_at: float,
    node_count: int,
    assumed_prefunded_count: int,
    children: list[tuple[dict[str, Any], subprocess.Popen[bytes]]],
    event_name: str = "lab.process_parent.launch_progress",
) -> dict[str, Any]:
    """Build a parent-only launch progress rollup.

    This function must remain cheap: it does not glob, open, or parse child
    event files. It is safe to call while the parent is launching many child
    processes.
    """

    exit_codes = Counter(str(int(process.poll())) for _node, process in children if process.poll() is not None)
    nodes_alive = sum(1 for _node, process in children if process.poll() is None)
    empty_phase_counts = {counter: 0 for counter in PROCESS_PHASE_COUNTERS}
    empty_phase_counts["nodes_started"] = len(children)
    rollup: dict[str, Any] = {
        "event": event_name,
        "schema": "main-computer-hub-lab-process-rollup/v3",
        "schema_version": 3,
        "run_id": run_id,
        "generated_at": utc_now(),
        "rollup_scope": "partial",
        "rollup_source": "parent_only",
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "execution_mode": "process",
        "node_count": int(node_count),
        "nodes_started": len(children),
        "children_launched": len(children),
        "children_remaining": max(0, int(node_count) - len(children)),
        "nodes_alive": nodes_alive,
        "nodes_exited": max(0, len(children) - nodes_alive),
        "assumed_prefunded_nodes": int(assumed_prefunded_count),
        "phase_counts": empty_phase_counts,
        "event_counts": {},
        "http_status_counts": {},
        "endpoint_counts": {},
        "hub_counts": {},
        "exit_codes": dict(sorted(exit_codes.items())),
        "self_terminated_b2bfailures": int(exit_codes.get("75", 0)),
        "rollup_scan_truncated": False,
        "rollup_scan_reason": "",
        "rollup_partial": False,
        "rollup_partial_reason": "",
        "rollup_files_total": 0,
        "rollup_files_scanned": 0,
        "rollup_files_limited": 0,
        "rollup_events_scanned": 0,
        "rollup_scan_start_index": 0,
        "rollup_scan_next_index": 0,
        "rollup_events_per_file_limit": 0,
        "rollup_scan_elapsed_seconds": 0.0,
        "transport_failures": 0,
        "market_http_responses": 0,
        "transport_failure_ratio": 0.0,
        "transport_dominated": False,
        "settlement_metrics_representative": False,
        "lab_interpretation": "insufficient_data",
        "lab_interpretation_reason": "parent-only launch progress; child events not scanned",
        "lifecycle": {},
        "behavior": {},
        "tat_counts": {},
        "behavior_state_counts": {},
        "top_behavior": [],
    }
    rollup.update(empty_phase_counts)
    return rollup


def _process_rollup_csv_row(rollup: dict[str, Any]) -> dict[str, Any]:
    json_columns = {
        "event_counts_json",
        "http_status_counts_json",
        "endpoint_counts_json",
        "hub_counts_json",
        "exit_codes_json",
        "lifecycle_json",
        "behavior_json",
        "top_behavior_json",
    }
    row: dict[str, Any] = {column: "" for column in PROCESS_ROLLUP_CSV_COLUMNS}
    for column in PROCESS_ROLLUP_CSV_COLUMNS:
        if column in json_columns:
            continue
        value = rollup.get(column, "")
        row[column] = value
    row["event_counts_json"] = json.dumps(rollup.get("event_counts", {}), sort_keys=True)
    row["http_status_counts_json"] = json.dumps(rollup.get("http_status_counts", {}), sort_keys=True)
    row["endpoint_counts_json"] = json.dumps(rollup.get("endpoint_counts", {}), sort_keys=True)
    row["hub_counts_json"] = json.dumps(rollup.get("hub_counts", {}), sort_keys=True)
    row["exit_codes_json"] = json.dumps(rollup.get("exit_codes", {}), sort_keys=True)
    row["lifecycle_json"] = json.dumps(rollup.get("lifecycle", {}), sort_keys=True)
    row["behavior_json"] = json.dumps(rollup.get("behavior", {}), sort_keys=True)
    row["top_behavior_json"] = json.dumps(rollup.get("top_behavior", []), sort_keys=True)
    return row


def write_process_rollup_files(
    rollup: dict[str, Any],
    *,
    rollups_jsonl: Path,
    latest_rollup_json: Path,
    rollups_csv: Path,
) -> None:
    rollups_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with rollups_jsonl.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(rollup, sort_keys=True) + "\n")
    latest_rollup_json.write_text(json.dumps(rollup, indent=2, sort_keys=True), encoding="utf-8")

    csv_exists = rollups_csv.exists() and rollups_csv.stat().st_size > 0
    if csv_exists:
        try:
            with rollups_csv.open("r", encoding="utf-8", newline="") as existing:
                first_line = existing.readline().rstrip("\r\n")
            existing_columns = first_line.split(",") if first_line else []
        except OSError:
            existing_columns = []
        if existing_columns != list(PROCESS_ROLLUP_CSV_COLUMNS):
            mismatch_path = rollups_csv.with_name(f"{rollups_csv.stem}.schema-mismatch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{rollups_csv.suffix}")
            rollups_csv.replace(mismatch_path)
            csv_exists = False

    with rollups_csv.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PROCESS_ROLLUP_CSV_COLUMNS))
        if not csv_exists:
            writer.writeheader()
        writer.writerow(_process_rollup_csv_row(rollup))


def format_process_rollup(rollup: dict[str, Any]) -> str:
    endpoints = rollup.get("endpoint_counts", {}) if isinstance(rollup.get("endpoint_counts"), dict) else {}
    statuses = rollup.get("http_status_counts", {}) if isinstance(rollup.get("http_status_counts"), dict) else {}
    files_scanned = int(rollup.get("rollup_files_scanned", 0) or 0)
    files_total = int(rollup.get("rollup_files_total", 0) or 0)
    partial_marker = "!" if rollup.get("rollup_partial") or rollup.get("rollup_scan_truncated") else ""
    return (
        f"[worker-lab:{int(float(rollup.get('elapsed_seconds', 0)))}s run={rollup.get('run_id', '')}] "
        f"alive={int(rollup.get('nodes_alive', 0))} "
        f"exited={int(rollup.get('nodes_exited', 0))} "
        f"warm={int(rollup.get('warm_finished', 0))} "
        f"startup={int(rollup.get('startup_request_http_response', 0))}/{int(rollup.get('startup_request_attempted', 0))} "
        f"bootstrap={int(rollup.get('bootstrap_attempted', 0))} "
        f"balance={int(rollup.get('bootstrap_balance_checked', 0))} "
        f"register={int(rollup.get('worker_register_http_response', 0))}/{int(rollup.get('worker_register_attempted', 0))} "
        f"runtime={int(rollup.get('entered_runtime_loop', 0))} "
        f"req={int(endpoints.get('requests', 0))} "
        f"poll={int(endpoints.get('workers_poll', 0))} "
        f"hb={int(endpoints.get('workers_heartbeat', 0))} "
        f"http0={int(statuses.get('0', 0))} "
        f"transport={int(rollup.get('transport_failures', endpoints.get('transport_failures', 0)) or 0)} "
        f"market_http={int(rollup.get('market_http_responses', 0) or 0)} "
        f"scan={files_scanned}/{files_total}{partial_marker} "
        f"b2b={int(rollup.get('self_terminated_b2bfailures', 0))}"
    )


def format_process_launch_progress(rollup: dict[str, Any]) -> str:
    return (
        f"[worker-lab:launch {int(float(rollup.get('elapsed_seconds', 0)))}s run={rollup.get('run_id', '')}] "
        f"launched={int(rollup.get('children_launched', rollup.get('nodes_started', 0)))}/{int(rollup.get('node_count', 0))} "
        f"remaining={int(rollup.get('children_remaining', 0))} "
        f"alive={int(rollup.get('nodes_alive', 0))} "
        f"exited={int(rollup.get('nodes_exited', 0))}"
    )


def build_node_process_command(args: argparse.Namespace, *, runtime_node_list: Path, node_index: int) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "tools.scheduler_lab.node_process",
        "--node-list",
        str(runtime_node_list),
        "--node-index",
        str(node_index),
        "--role",
        str(args.role),
        "--hub-base-url",
        str(args.hub_base_url),
        "--output-dir",
        str(args.output_dir),
        "--run-id",
        str(getattr(args, "run_id", "") or ""),
        "--duration-seconds",
        str(float(args.duration_seconds)),
        "--request-mode",
        str(args.request_mode),
        "--account-id-prefix",
        str(args.account_id_prefix),
        "--lease-seconds",
        str(float(args.lease_seconds)),
        "--worker-poll-interval-ms",
        str(float(args.worker_poll_interval_ms)),
        "--max-request-interval-ms",
        str(float(args.max_request_interval_ms)),
        "--http-timeout-seconds",
        str(float(args.http_timeout_seconds)),
        "--http-retries",
        "0",
        "--funded",
        str(float(getattr(args, "funded", 0.0) or 0.0)),
        "--request-startup-mode",
        effective_request_startup_mode(args),
        "--request-startup-spread-seconds",
        str(float(getattr(args, "request_startup_spread_seconds", 0.0) or 0.0)),
        "--warm",
        str(getattr(args, "warm", "") or ""),
        "--b2bfailures",
        str(int(getattr(args, "b2bfailures", 0) or 0)),
        "--forced-alive",
        str(float(getattr(args, "forced_alive", 0.0) or 0.0)),
    ]
    if args.hub_base_urls:
        command.extend(["--hub-base-urls", str(args.hub_base_urls)])
    if args.worktime:
        command.extend(["--worktime", str(args.worktime)])
    command.append(_process_bool_flag(bool(args.enable_local_busy), positive="--enable-local-busy", negative="--disable-local-busy"))
    command.append(_process_bool_flag(bool(args.bootstrap_funding), positive="--bootstrap-funding", negative="--no-bootstrap-funding"))
    return command


def run_process_mode(args: argparse.Namespace) -> int:
    """Run one OS child process per simulated node.

    This is the Docker worker-lab default because it avoids the single-process
    asyncio/thread-pool bottleneck and lets the container/host scheduler handle
    each node as an independent process.
    """

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    run_id = str(getattr(args, "run_id", "") or new_lab_session_id())
    args.run_id = run_id
    selected_nodes, node_list_path, assumed_prefunded_count = prepare_selected_nodes(args)
    runtime_node_list = output_dir / f"scheduler-lab-runtime-nodes-{run_id}.jsonl"
    write_runtime_node_list(runtime_node_list, selected_nodes)

    stop_at = started_at + max(0.1, float(args.duration_seconds))
    parent_events = output_dir / f"scheduler-lab-process-parent-events-{run_id}.jsonl"
    parent_summary = output_dir / f"scheduler-lab-process-parent-summary-{run_id}.json"
    rollups_jsonl = output_dir / f"scheduler-lab-process-rollups-{run_id}.jsonl"
    latest_rollup_json = output_dir / f"scheduler-lab-process-rollup-latest-{run_id}.json"
    rollups_csv = output_dir / f"scheduler-lab-process-rollups-{run_id}.csv"
    children: list[tuple[dict[str, Any], subprocess.Popen[bytes]]] = []

    def emit_parent(event: dict[str, Any]) -> None:
        event = {"ts": utc_now(), "run_id": run_id, **event}
        with parent_events.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    emit_parent(
        {
            "event": "lab.process_parent.started",
            "run_id": run_id,
            "node_count": len(selected_nodes),
            "node_list": str(node_list_path),
            "runtime_node_list": str(runtime_node_list),
            "hub_base_url": args.hub_base_url,
            "hub_base_urls": normalize_hub_base_urls(args.hub_base_urls, args.hub_base_url) if args.hub_base_urls else [args.hub_base_url],
            "worktime": str(args.worktime or ""),
            "warm": str(getattr(args, "warm", "") or ""),
            "b2bfailures": int(getattr(args, "b2bfailures", 0) or 0),
            "forced_alive_seconds": float(getattr(args, "forced_alive", 0.0) or 0.0),
            "funded_percent": float(getattr(args, "funded", 0.0) or 0.0),
            "assumed_prefunded_nodes": assumed_prefunded_count,
            "execution_mode": "process",
            "rollups_jsonl": str(rollups_jsonl),
            "latest_rollup_json": str(latest_rollup_json),
            "rollups_csv": str(rollups_csv),
        }
    )
    print(
        "[worker-lab] process mode started "
        f"run={run_id} "
        f"nodes={len(selected_nodes)} assumed_prefunded={assumed_prefunded_count} "
        f"warm={str(getattr(args, 'warm', '') or 'none')} "
        f"b2bfailures={int(getattr(args, 'b2bfailures', 0) or 0)} "
        f"forced_alive={float(getattr(args, 'forced_alive', 0.0) or 0.0):g} "
        f"rollups={rollups_jsonl}",
        flush=True,
    )
    event_offsets: dict[Path, int] = {}
    child_event_paths: list[Path] = []
    phase_nodes: dict[str, set[str]] = {counter: set() for counter in PROCESS_PHASE_COUNTERS}
    rollup_stats = new_process_rollup_stats()
    parent_status_interval = max(0.0, float(getattr(args, "parent_status_interval", 2.0) or 0.0))
    parent_rollup_interval = max(0.0, float(getattr(args, "parent_rollup_interval", 60.0) or 0.0))
    parent_launch_progress_interval = max(0.0, float(getattr(args, "parent_launch_progress_interval", 10.0) or 0.0))
    parent_rollup_scan_seconds = max(0.0, float(getattr(args, "parent_rollup_scan_seconds", 2.0) or 0.0))
    parent_rollup_scan_events_per_file = max(1, int(getattr(args, "parent_rollup_scan_events_per_file", 256) or 256))
    scan_cursor_state: dict[str, Any] = {}
    next_parent_status_at = time.monotonic() + parent_status_interval
    next_parent_rollup_at = time.monotonic() + parent_rollup_interval
    next_launch_progress_at = time.monotonic() + parent_launch_progress_interval

    def emit_rollup(
        event_name: str = "lab.process_parent.rollup",
        *,
        scan_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rollup = build_process_rollup(
            run_id=run_id,
            started_at=started_at,
            node_count=len(selected_nodes),
            assumed_prefunded_count=assumed_prefunded_count,
            children=children,
            phase_nodes=phase_nodes,
            rollup_stats=rollup_stats,
            event_name=event_name,
        )
        if scan_state:
            rollup.update(scan_state)
        if rollup.get("rollup_partial") or rollup.get("rollup_scan_truncated"):
            rollup["rollup_scope"] = "partial"
        else:
            rollup["rollup_scope"] = "complete"
        rollup["rollup_source"] = "child_event_scan"
        write_process_rollup_files(
            rollup,
            rollups_jsonl=rollups_jsonl,
            latest_rollup_json=latest_rollup_json,
            rollups_csv=rollups_csv,
        )
        emit_parent(rollup)
        return rollup

    def emit_launch_progress(event_name: str = "lab.process_parent.launch_progress") -> dict[str, Any]:
        rollup = build_process_launch_progress_rollup(
            run_id=run_id,
            started_at=started_at,
            node_count=len(selected_nodes),
            assumed_prefunded_count=assumed_prefunded_count,
            children=children,
            event_name=event_name,
        )
        write_process_rollup_files(
            rollup,
            rollups_jsonl=rollups_jsonl,
            latest_rollup_json=latest_rollup_json,
            rollups_csv=rollups_csv,
        )
        emit_parent(rollup)
        return rollup

    try:
        # Launch progress is deliberately parent-only. It must not inspect child
        # event files while the parent is starting many OS processes.
        if parent_launch_progress_interval > 0.0 or parent_rollup_interval > 0.0:
            launch_rollup = emit_launch_progress("lab.process_parent.launch_progress.initial")
            print(format_process_launch_progress(launch_rollup), flush=True)

        for index, node in enumerate(selected_nodes):
            log_path = output_dir / f"node-process-{run_id}-{index:05d}-{safe_filename_token(node.get('node_id', 'node'))}.log"
            command = build_node_process_command(args, runtime_node_list=runtime_node_list, node_index=index)
            log_handle = log_path.open("ab")
            try:
                process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT)
            finally:
                log_handle.close()
            children.append((node, process))
            child_event_paths.append(process_child_event_path(output_dir, node, index, run_id=run_id))
            phase_nodes.setdefault("nodes_started", set()).add(str(node.get("node_id") or f"node-index:{index}"))

            now = time.monotonic()
            if parent_launch_progress_interval > 0.0 and now >= next_launch_progress_at:
                launch_rollup = emit_launch_progress()
                print(format_process_launch_progress(launch_rollup), flush=True)
                next_launch_progress_at = now + parent_launch_progress_interval

        if parent_launch_progress_interval > 0.0 or parent_rollup_interval > 0.0:
            launch_rollup = emit_launch_progress("lab.process_parent.launch_progress.post_spawn")
            print(format_process_launch_progress(launch_rollup), flush=True)

        # After spawn, a bounded scan can begin sampling child event files. The
        # scan uses fixed parent-known paths and offsets rather than globbing or
        # rereading full logs.
        scan_state: dict[str, Any] = {}
        phase_counts = collect_process_phase_counts(
            output_dir,
            event_offsets,
            phase_nodes,
            rollup_stats,
            event_paths=child_event_paths,
            max_scan_seconds=parent_rollup_scan_seconds,
            max_events_per_file=parent_rollup_scan_events_per_file,
            scan_cursor_state=scan_cursor_state,
            scan_state=scan_state,
        )
        if parent_rollup_interval > 0.0:
            initial_rollup = emit_rollup("lab.process_parent.rollup.post_spawn", scan_state=scan_state)
            print(format_process_rollup(initial_rollup), flush=True)
            next_parent_rollup_at = time.monotonic() + parent_rollup_interval
        next_parent_status_at = time.monotonic() + parent_status_interval

        while time.monotonic() < stop_at:
            now = time.monotonic()
            due_flags = process_parent_runtime_due_flags(
                now=now,
                parent_status_interval=parent_status_interval,
                next_parent_status_at=next_parent_status_at,
                parent_rollup_interval=parent_rollup_interval,
                next_parent_rollup_at=next_parent_rollup_at,
            )
            rollup_due = due_flags["rollup_due"]
            status_due = due_flags["status_due"]
            phase_counts = process_phase_count_summary(phase_nodes)
            scan_state: dict[str, Any] = {}
            if rollup_due:
                phase_counts = collect_process_phase_counts(
                    output_dir,
                    event_offsets,
                    phase_nodes,
                    rollup_stats,
                    event_paths=child_event_paths,
                    max_scan_seconds=parent_rollup_scan_seconds,
                    max_events_per_file=parent_rollup_scan_events_per_file,
                    scan_cursor_state=scan_cursor_state,
                    scan_state=scan_state,
                )
            if status_due:
                emit_parent(
                    {
                        "event": "lab.process_parent.phase_counts",
                        "run_id": run_id,
                        "phase_count_source": "memory",
                        "rollup_scan_skipped": not rollup_due,
                        **phase_counts,
                    }
                )
                print(format_process_phase_counts(phase_counts), flush=True)
                next_parent_status_at = now + parent_status_interval
            if rollup_due:
                rollup = emit_rollup(scan_state=scan_state)
                print(format_process_rollup(rollup), flush=True)
                next_parent_rollup_at = now + parent_rollup_interval
            if all(process.poll() is not None for _node, process in children):
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        emit_parent({"event": "lab.process_parent.interrupted", "run_id": run_id})
    finally:
        for _node, process in children:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10.0
        for _node, process in children:
            if process.poll() is None:
                timeout = max(0.0, deadline - time.monotonic())
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
        final_scan_state: dict[str, Any] = {}
        phase_counts = collect_process_phase_counts(
            output_dir,
            event_offsets,
            phase_nodes,
            rollup_stats,
            event_paths=child_event_paths,
            max_scan_seconds=0.0,
            max_events_per_file=0,
            scan_cursor_state=scan_cursor_state,
            scan_state=final_scan_state,
        )
        if parent_status_interval > 0.0:
            emit_parent({"event": "lab.process_parent.phase_counts.final", "run_id": run_id, **phase_counts, **final_scan_state})
            print(format_process_phase_counts(phase_counts), flush=True)
        final_rollup = emit_rollup("lab.process_parent.rollup.final", scan_state=final_scan_state)
        print(format_process_rollup(final_rollup), flush=True)
        exit_codes = [int(process.poll() if process.poll() is not None else -999) for _node, process in children]
        counts = Counter(str(code) for code in exit_codes)
        summary = {
            "schema": "main-computer-hub-lab-process-summary/v3",
            "schema_version": 3,
            "generated_at": utc_now(),
            "run_id": run_id,
            "execution_mode": "process",
            "node_count": len(selected_nodes),
            "nodes_started": len(children),
            "assumed_prefunded_nodes": assumed_prefunded_count,
            "phase_counts": phase_counts,
            "event_counts": final_rollup.get("event_counts", {}),
            "http_status_counts": final_rollup.get("http_status_counts", {}),
            "endpoint_counts": final_rollup.get("endpoint_counts", {}),
            "hub_counts": final_rollup.get("hub_counts", {}),
            "exit_codes": dict(counts),
            "exit_code_75_total": counts.get("75", 0),
            "self_terminated_b2bfailures": int(phase_counts.get("self_terminated_b2bfailures", 0) or 0),
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "runtime_node_list": str(runtime_node_list),
            "parent_events": str(parent_events),
            "rollups_jsonl": str(rollups_jsonl),
            "latest_rollup_json": str(latest_rollup_json),
            "rollups_csv": str(rollups_csv),
        }
        parent_summary.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        emit_parent({"event": "lab.process_parent.finished", **summary})
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0

async def run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    selected_nodes, node_list_path, assumed_prefunded_count = prepare_selected_nodes(args)
    run_id = str(getattr(args, "run_id", "") or new_lab_session_id())
    args.run_id = run_id
    sink = EventSink(output_dir, run_id=run_id)
    started_at = time.monotonic()
    stop_at = started_at + max(0.1, float(args.duration_seconds))
    stop_event = asyncio.Event()

    def _request_stop(*_unused: object) -> None:
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for signame in ("SIGINT", "SIGTERM"):
            signum = getattr(signal, signame, None)
            if signum is not None:
                try:
                    loop.add_signal_handler(signum, _request_stop)
                except (NotImplementedError, RuntimeError):
                    pass

        await sink.emit({
            "ts": utc_now(),
            "event": "lab.started",
            "role": args.role,
            "node_count": len(selected_nodes),
            "hub_base_url": args.hub_base_url,
            "hub_base_urls": normalize_hub_base_urls(args.hub_base_urls, args.hub_base_url) if args.hub_base_urls else [args.hub_base_url],
            "node_list": str(node_list_path),
            "worktime": str(args.worktime or ""),
            "funded_percent": float(getattr(args, "funded", 0.0) or 0.0),
            "assumed_prefunded_nodes": assumed_prefunded_count,
            "request_startup_mode": effective_request_startup_mode(args),
            "request_startup_spread_seconds": float(getattr(args, "request_startup_spread_seconds", 0.0) or 0.0),
        })
        tasks = []
        for node in selected_nodes:
            worker_enabled = args.role != "requesters" and node_can_work(node)
            requester_enabled = args.role != "workers" and node_can_request(node)
            tasks.append(asyncio.create_task(run_node(node, args=args, sink=sink, stop_at=stop_at, worker_enabled=worker_enabled, requester_enabled=requester_enabled)))

        while time.monotonic() < stop_at and not stop_event.is_set():
            await asyncio.sleep(0.25)

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await sink.emit({"ts": utc_now(), "event": "lab.finished", "role": args.role, "node_count": len(selected_nodes)})
        summary = sink.summary(nodes=selected_nodes, started_at=started_at)
        sink.write_summary(summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        await sink.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run simulated Hub scheduler-lab nodes with adaptive worker/requester behavior.")
    parser.add_argument("--role", choices=["all", "workers", "requesters"], default=os.environ.get("LAB_ROLE", "all"))
    parser.add_argument("--hub-base-url", default=os.environ.get("HUB_BASE_URL", DEFAULT_HUB_BASE_URL))
    parser.add_argument("--hub-base-urls", default=os.environ.get("HUB_BASE_URLS", ""))
    parser.add_argument("--node-list", default=os.environ.get("LAB_NODE_LIST", "/lab-output/120-First-Post.jsonl"))
    parser.add_argument("--generate-node-list", action="store_true", default=os.environ.get("GENERATE_NODE_LIST", "1").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--output-dir", default=os.environ.get("LAB_OUTPUT_DIR", "/lab-output"))
    parser.add_argument("--run-id", default=os.environ.get("LAB_RUN_ID", ""), help=argparse.SUPPRESS)
    parser.add_argument("--duration-seconds", type=float, default=float(os.environ.get("LAB_DURATION_SECONDS", "300")))
    parser.add_argument("--total", type=int, default=env_optional_int("LAB_TOTAL"))
    parser.add_argument("--nodes", type=int, default=env_optional_int("LAB_NODES"), help="Total generated lab nodes. Overrides --total when set.")
    parser.add_argument("--workers", type=int, default=env_optional_int("LAB_WORKERS"))
    parser.add_argument("--requesters", type=int, default=env_optional_int("LAB_REQUESTERS"))
    parser.add_argument("--seed", type=int, default=int(os.environ.get("LAB_SEED", str(DEFAULT_SEED))))
    parser.add_argument("--network", default=os.environ.get("LAB_NETWORK", "dev"))
    parser.add_argument("--ring", type=int, default=int(os.environ.get("LAB_RING", "2")))
    parser.add_argument("--chain-id", type=int, default=int(os.environ.get("LAB_CHAIN_ID", "42424242")))
    parser.add_argument("--problematic-worker-rate", type=float, default=float(os.environ.get("PROBLEMATIC_WORKER_RATE", "0.08")))
    parser.add_argument("--problematic-requester-rate", type=float, default=float(os.environ.get("PROBLEMATIC_REQUESTER_RATE", "0.05")))
    parser.add_argument("--problematic-failure-multiplier", type=float, default=float(os.environ.get("PROBLEMATIC_FAILURE_MULTIPLIER", "4.0")))
    parser.add_argument("--disable-problematic", action="store_true")
    parser.add_argument("--request-mode", choices=["worker_pull_v0", "legacy", "registration_only"], default=os.environ.get("REQUEST_MODE", "worker_pull_v0"))
    parser.add_argument("--account-id-prefix", default=os.environ.get("LAB_ACCOUNT_ID_PREFIX", "lab-account"))
    parser.add_argument(
        "--funded",
        type=parse_funded_percent,
        default=parse_funded_percent(os.environ.get("LAB_FUNDED", "0")),
        help="Percent of generated node accounts to treat as already funded in FDB; accepts 90 or 0.9.",
    )
    parser.add_argument("--bootstrap-funding", dest="bootstrap_funding", action="store_true", default=env_flag("LAB_BOOTSTRAP_FUNDING", True))
    parser.add_argument("--no-bootstrap-funding", dest="bootstrap_funding", action="store_false")
    parser.add_argument(
        "--request-startup-mode",
        choices=["auto", "natural", "surge"],
        default=os.environ.get("LAB_REQUEST_STARTUP_MODE", "auto"),
        help="How request-capable nodes begin traffic. auto surges for funded reattach runs.",
    )
    parser.add_argument(
        "--request-startup-spread-seconds",
        type=float,
        default=float(os.environ.get("LAB_REQUEST_STARTUP_SPREAD_SECONDS", "3")),
        help="Spread startup surge requests over this many seconds.",
    )
    parser.add_argument("--enable-local-busy", dest="enable_local_busy", action="store_true", default=env_flag("LAB_ENABLE_LOCAL_BUSY", True))
    parser.add_argument("--disable-local-busy", dest="enable_local_busy", action="store_false")
    parser.add_argument("--worker-poll-interval-ms", type=float, default=float(os.environ.get("WORKER_POLL_INTERVAL_MS", "500")))
    parser.add_argument("--lease-seconds", type=float, default=float(os.environ.get("LEASE_SECONDS", "45")))
    parser.add_argument(
        "--worktime",
        default=os.environ.get("LAB_WORKTIME", ""),
        help="Optional worker result runtime distribution in seconds, e.g. 100mu,30sigma where sigma is standard deviation.",
    )
    parser.add_argument("--max-runtime-ms", type=float, default=float(os.environ.get("MAX_RUNTIME_MS", "30000")))
    parser.add_argument("--max-request-interval-ms", type=float, default=float(os.environ.get("MAX_REQUEST_INTERVAL_MS", "15000")))
    parser.add_argument("--execution-mode", choices=["process", "async"], default=os.environ.get("LAB_EXECUTION_MODE", "process"), help="process starts one OS child process per node; async keeps the older single-process simulator.")
    parser.add_argument(
        "--warm",
        default=os.environ.get("LAB_WARM", ""),
        help="Optional node warm-up delay distribution in seconds before first hub contact, e.g. 2mu,1sigma.",
    )
    parser.add_argument("--b2bfailures", type=int, default=int(os.environ.get("B2B_FAILURES", "10")), help="Consecutive transport failures before a node self-terminates after --forced-alive has elapsed. 0 disables.")
    parser.add_argument("--forced-alive", type=float, default=float(os.environ.get("FORCED_ALIVE_SECONDS", "0")), help="Seconds a node must stay alive before b2b transport failures can self-terminate it.")
    parser.add_argument("--http-timeout-seconds", type=float, default=float(os.environ.get("HTTP_TIMEOUT_SECONDS", "1")))
    parser.add_argument("--http-retries", type=int, default=int(os.environ.get("HTTP_RETRIES", "1")))
    parser.add_argument(
        "--parent-status-interval",
        type=float,
        default=float(os.environ.get("LAB_PARENT_STATUS_INTERVAL", "2")),
        help="Seconds between parent process-mode phase summaries; 0 disables periodic summaries.",
    )
    parser.add_argument(
        "--parent-rollup-interval",
        type=float,
        default=float(os.environ.get("LAB_PARENT_ROLLUP_INTERVAL", "60")),
        help="Seconds between process-mode experiment rollups written to JSONL/CSV; 0 disables periodic rollups but still writes final rollup.",
    )
    parser.add_argument(
        "--parent-launch-progress-interval",
        type=float,
        default=float(os.environ.get("LAB_PARENT_LAUNCH_PROGRESS_INTERVAL", "10")),
        help="Seconds between cheap parent-only launch progress rollups while process-mode children are spawning; 0 disables launch progress.",
    )
    parser.add_argument(
        "--parent-rollup-scan-seconds",
        type=float,
        default=float(os.environ.get("LAB_PARENT_ROLLUP_SCAN_SECONDS", "2")),
        help="Maximum seconds a process-mode rollup may spend scanning child event files per sample; 0 disables the scan time budget.",
    )
    parser.add_argument(
        "--parent-rollup-scan-events-per-file",
        type=int,
        default=int(os.environ.get("LAB_PARENT_ROLLUP_SCAN_EVENTS_PER_FILE", "256")),
        help="Maximum new child events to read from one node event file per rollup sample before rotating to the next file.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    if args.hub_base_urls:
        normalized_urls = normalize_hub_base_urls(args.hub_base_urls, args.hub_base_url)
        args.hub_base_urls = ",".join(normalized_urls)
        args.hub_base_url = normalized_urls[0]
    if args.b2bfailures < 0:
        raise SystemExit("--b2bfailures must be >= 0")
    if args.forced_alive < 0:
        raise SystemExit("--forced-alive must be >= 0")
    try:
        args.worktime_distribution = parse_worktime_spec(args.worktime)
        args.warm_distribution = parse_warm_spec(args.warm)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        if args.execution_mode == "process":
            return run_process_mode(args)
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
