from __future__ import annotations

import argparse
import csv
import html
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA = "main-computer-hub-lab-node-grid/v2"

DEFAULT_TOTAL_NODES = 120
DEFAULT_WORKER_RATIO = 5.0 / 6.0
DEFAULT_SEED = 424242
DEFAULT_HUB_BASE_URL = "http://host.docker.internal:8870"
DEFAULT_HUB_BASE_URLS = [DEFAULT_HUB_BASE_URL]

WORKER_COHORTS: list[dict[str, Any]] = [
    {
        "name": "cheap-fast",
        "default_count": 30,
        "models": ["mock-ai-model-phase9"],
        "min_accepted_credits": {"distribution": "discrete", "values": [{"value": 1, "weight": 90}, {"value": 2, "weight": 10}]},
        "max_concurrency": {"distribution": "discrete", "values": [{"value": 1, "weight": 92}, {"value": 2, "weight": 8}]},
        "latency_multiplier": {"distribution": "lognormal", "median": 0.75, "sigma": 0.18, "clamp_min": 0.35, "clamp_max": 1.25},
        "failure_multiplier": {"distribution": "lognormal", "median": 0.75, "sigma": 0.20, "clamp_min": 0.25, "clamp_max": 1.50},
        "ready_offsets": {"ready_fast": -0.90, "ready_normal": -0.30, "ready_slow": 0.45, "decline": 0.65, "no_response": 0.95},
    },
    {
        "name": "normal",
        "default_count": 35,
        "models": ["mock-ai-model-phase9"],
        "min_accepted_credits": {"distribution": "discrete", "values": [{"value": 1, "weight": 10}, {"value": 2, "weight": 80}, {"value": 5, "weight": 10}]},
        "max_concurrency": {"distribution": "discrete", "values": [{"value": 1, "weight": 88}, {"value": 2, "weight": 10}, {"value": 4, "weight": 2}]},
        "latency_multiplier": {"distribution": "lognormal", "median": 1.00, "sigma": 0.22, "clamp_min": 0.45, "clamp_max": 1.90},
        "failure_multiplier": {"distribution": "lognormal", "median": 1.00, "sigma": 0.25, "clamp_min": 0.40, "clamp_max": 2.25},
        "ready_offsets": {"ready_fast": 0.00, "ready_normal": -0.15, "ready_slow": 0.05, "decline": 0.00, "no_response": 0.00},
    },
    {
        "name": "expensive-fast",
        "default_count": 15,
        "models": ["mock-ai-model-phase9", "mock-ai-model-phase9-premium"],
        "min_accepted_credits": {"distribution": "discrete", "values": [{"value": 5, "weight": 88}, {"value": 8, "weight": 12}]},
        "max_concurrency": {"distribution": "discrete", "values": [{"value": 1, "weight": 72}, {"value": 2, "weight": 22}, {"value": 4, "weight": 6}]},
        "latency_multiplier": {"distribution": "lognormal", "median": 0.70, "sigma": 0.20, "clamp_min": 0.30, "clamp_max": 1.35},
        "failure_multiplier": {"distribution": "lognormal", "median": 0.65, "sigma": 0.20, "clamp_min": 0.25, "clamp_max": 1.60},
        "ready_offsets": {"ready_fast": -0.80, "ready_normal": -0.25, "ready_slow": 0.55, "decline": 0.85, "no_response": 1.10},
    },
    {
        "name": "slow-distant",
        "default_count": 15,
        "models": ["mock-ai-model-phase9"],
        "min_accepted_credits": {"distribution": "discrete", "values": [{"value": 1, "weight": 75}, {"value": 2, "weight": 25}]},
        "max_concurrency": {"distribution": "discrete", "values": [{"value": 1, "weight": 94}, {"value": 2, "weight": 6}]},
        "latency_multiplier": {"distribution": "lognormal", "median": 2.10, "sigma": 0.28, "clamp_min": 1.10, "clamp_max": 4.50},
        "failure_multiplier": {"distribution": "lognormal", "median": 1.65, "sigma": 0.30, "clamp_min": 0.70, "clamp_max": 4.00},
        "ready_offsets": {"ready_fast": 1.05, "ready_normal": 0.45, "ready_slow": -0.90, "decline": 0.05, "no_response": -0.20},
    },
    {
        "name": "flaky",
        "default_count": 5,
        "always_problematic": True,
        "models": ["mock-ai-model-phase9"],
        "min_accepted_credits": {"distribution": "discrete", "values": [{"value": 1, "weight": 80}, {"value": 2, "weight": 20}]},
        "max_concurrency": {"distribution": "discrete", "values": [{"value": 1, "weight": 98}, {"value": 2, "weight": 2}]},
        "latency_multiplier": {"distribution": "lognormal", "median": 1.25, "sigma": 0.45, "clamp_min": 0.40, "clamp_max": 5.00},
        "failure_multiplier": {"distribution": "lognormal", "median": 3.00, "sigma": 0.45, "clamp_min": 1.50, "clamp_max": 10.0},
        "ready_offsets": {"ready_fast": 0.30, "ready_normal": 0.10, "ready_slow": -0.25, "decline": -0.80, "no_response": -1.10},
    },
]

REQUESTER_COHORTS: list[dict[str, Any]] = [
    {
        "name": "cheap-traffic",
        "default_count": 10,
        "model_weights": [{"value": "mock-ai-model-phase9", "weight": 100}],
        "offered_credits": {"distribution": "discrete", "values": [{"value": 1, "weight": 90}, {"value": 2, "weight": 10}]},
        "request_interval_ms": {"distribution": "exponential", "mean": 1600, "clamp_min": 250, "clamp_max": 6000},
    },
    {
        "name": "normal-traffic",
        "default_count": 7,
        "model_weights": [{"value": "mock-ai-model-phase9", "weight": 96}, {"value": "mock-ai-model-phase9-premium", "weight": 4}],
        "offered_credits": {"distribution": "discrete", "values": [{"value": 2, "weight": 80}, {"value": 5, "weight": 20}]},
        "request_interval_ms": {"distribution": "exponential", "mean": 1300, "clamp_min": 200, "clamp_max": 5000},
    },
    {
        "name": "premium-traffic",
        "default_count": 3,
        "model_weights": [{"value": "mock-ai-model-phase9", "weight": 45}, {"value": "mock-ai-model-phase9-premium", "weight": 55}],
        "offered_credits": {"distribution": "discrete", "values": [{"value": 5, "weight": 80}, {"value": 8, "weight": 20}]},
        "request_interval_ms": {"distribution": "exponential", "mean": 2400, "clamp_min": 400, "clamp_max": 8000},
    },
]

NODE_BEHAVIOR_MODES: dict[str, dict[str, Any]] = {
    "worker_centric": {
        "request_probability": 0.04,
        "worker_offer_probability": 0.96,
        "initial_credits": {"distribution": "discrete", "values": [{"value": 0, "weight": 25}, {"value": 2, "weight": 50}, {"value": 8, "weight": 25}]},
        "funding_remediation": "work_to_earn",
        "low_credit_threshold": 2,
        "low_credit_work_seconds": 35,
        "faucet_top_up_credits": 0,
        "insufficient_credit_backoff_ms": 2500,
        "local_busy_probability_per_minute": 0.20,
        "local_busy_median_ms": 6500,
        "local_busy_max_ms": 45000,
        "request_interval_mean_ms": 9000,
    },
    "requester_centric": {
        "request_probability": 0.96,
        "worker_offer_probability": 0.08,
        "initial_credits": {"distribution": "discrete", "values": [{"value": 0, "weight": 10}, {"value": 6, "weight": 45}, {"value": 24, "weight": 35}, {"value": 80, "weight": 10}]},
        "funding_remediation": "faucet",
        "low_credit_threshold": 4,
        "low_credit_work_seconds": 20,
        "faucet_top_up_credits": 20,
        "insufficient_credit_backoff_ms": 4000,
        "local_busy_probability_per_minute": 0.15,
        "local_busy_median_ms": 5000,
        "local_busy_max_ms": 30000,
        "request_interval_mean_ms": 1700,
    },
    "mixed_market": {
        "request_probability": 0.35,
        "worker_offer_probability": 0.62,
        "initial_credits": {"distribution": "discrete", "values": [{"value": 0, "weight": 12}, {"value": 3, "weight": 28}, {"value": 12, "weight": 42}, {"value": 40, "weight": 18}]},
        "funding_remediation": "mixed",
        "low_credit_threshold": 3,
        "low_credit_work_seconds": 30,
        "faucet_top_up_credits": 12,
        "insufficient_credit_backoff_ms": 3000,
        "local_busy_probability_per_minute": 0.35,
        "local_busy_median_ms": 8500,
        "local_busy_max_ms": 60000,
        "request_interval_mean_ms": 4200,
    },
    "bursty_local": {
        "request_probability": 0.24,
        "worker_offer_probability": 0.58,
        "initial_credits": {"distribution": "discrete", "values": [{"value": 0, "weight": 18}, {"value": 2, "weight": 30}, {"value": 10, "weight": 37}, {"value": 30, "weight": 15}]},
        "funding_remediation": "work_to_earn",
        "low_credit_threshold": 2,
        "low_credit_work_seconds": 45,
        "faucet_top_up_credits": 0,
        "insufficient_credit_backoff_ms": 5000,
        "local_busy_probability_per_minute": 0.95,
        "local_busy_median_ms": 18000,
        "local_busy_max_ms": 120000,
        "request_interval_mean_ms": 5200,
    },
    "low_funded_bootstrap": {
        "request_probability": 0.42,
        "worker_offer_probability": 0.80,
        "initial_credits": {"distribution": "discrete", "values": [{"value": 0, "weight": 55}, {"value": 1, "weight": 30}, {"value": 2, "weight": 15}]},
        "funding_remediation": "work_to_earn",
        "low_credit_threshold": 1,
        "low_credit_work_seconds": 60,
        "faucet_top_up_credits": 0,
        "insufficient_credit_backoff_ms": 7000,
        "local_busy_probability_per_minute": 0.25,
        "local_busy_median_ms": 7000,
        "local_busy_max_ms": 45000,
        "request_interval_mean_ms": 3600,
    },
    "dormant_when_broke": {
        "request_probability": 0.18,
        "worker_offer_probability": 0.18,
        "initial_credits": {"distribution": "discrete", "values": [{"value": 0, "weight": 35}, {"value": 4, "weight": 45}, {"value": 16, "weight": 20}]},
        "funding_remediation": "dormant",
        "low_credit_threshold": 2,
        "low_credit_work_seconds": 90,
        "faucet_top_up_credits": 0,
        "insufficient_credit_backoff_ms": 15000,
        "local_busy_probability_per_minute": 0.20,
        "local_busy_median_ms": 12000,
        "local_busy_max_ms": 90000,
        "request_interval_mean_ms": 7000,
    },
}

WORKER_BEHAVIOR_MODE_WEIGHTS = {
    "distribution": "discrete",
    "values": [
        {"value": "worker_centric", "weight": 54},
        {"value": "mixed_market", "weight": 22},
        {"value": "bursty_local", "weight": 14},
        {"value": "low_funded_bootstrap", "weight": 7},
        {"value": "dormant_when_broke", "weight": 3},
    ],
}

REQUESTER_BEHAVIOR_MODE_WEIGHTS = {
    "distribution": "discrete",
    "values": [
        {"value": "requester_centric", "weight": 42},
        {"value": "mixed_market", "weight": 30},
        {"value": "bursty_local", "weight": 14},
        {"value": "low_funded_bootstrap", "weight": 10},
        {"value": "dormant_when_broke", "weight": 4},
    ],
}


GRID_COLUMNS = [
    "node_id", "kind", "behavior_mode", "cohort", "tags", "account_id", "hub_base_url", "hub_base_urls_json", "network", "ring", "chain_id", "sim_seed",
    "model", "models_json", "min_accepted_credits", "offered_credits", "max_concurrency",
    "initial_credits", "low_credit_threshold", "funding_remediation", "faucet_top_up_credits",
    "low_credit_work_seconds", "insufficient_credit_backoff_ms",
    "request_probability", "worker_offer_probability",
    "local_busy_probability_per_minute", "local_busy_median_ms", "local_busy_max_ms",
    "startup_delay_ms", "heartbeat_interval_ms", "heartbeat_jitter_ms", "heartbeat_drop_probability",
    "disconnect_hazard_per_minute", "stale_hazard_per_minute", "recovery_median_seconds",
    "probe_delivery_drop_probability", "probe_delivery_delay_median_ms", "ready_temperature",
    "ready_fast_median_ms", "ready_normal_median_ms", "ready_slow_median_ms", "decline_energy", "no_response_energy",
    "post_ready_disconnect_probability", "post_ready_capacity_race_probability", "model_load_failure_probability",
    "runtime_normal_median_ms", "runtime_slow_median_ms", "execution_disconnect_hazard_per_minute", "execution_crash_probability",
    "result_submit_delay_median_ms", "result_submit_drop_probability", "result_submit_duplicate_probability",
    "request_interval_mean_ms", "burst_probability_per_minute", "burst_multiplier_median",
    "offered_credits_distribution_json", "model_distribution_json",
]


def clamp(value: float, low: float | None = None, high: float | None = None) -> float:
    if low is not None and value < low:
        value = low
    if high is not None and value > high:
        value = high
    return value


def sample_distribution(spec: dict[str, Any], rng: random.Random) -> Any:
    dist = str(spec.get("distribution", "deterministic")).lower()
    if dist == "deterministic":
        return spec.get("value")
    if dist == "discrete":
        values = list(spec.get("values", []))
        total = sum(max(0.0, float(item.get("weight", 0))) for item in values)
        if total <= 0:
            return values[0].get("value") if values else None
        pick = rng.random() * total
        cursor = 0.0
        for item in values:
            cursor += max(0.0, float(item.get("weight", 0)))
            if pick <= cursor:
                return item.get("value")
        return values[-1].get("value")
    if dist == "categorical":
        return sample_distribution({"distribution": "discrete", "values": spec.get("values", [])}, rng)
    if dist == "lognormal":
        median = float(spec.get("median", 1.0))
        sigma = max(0.0, float(spec.get("sigma", 0.0)))
        value = median if sigma == 0 else rng.lognormvariate(math.log(max(median, 0.000001)), sigma)
        return clamp(value, spec.get("clamp_min"), spec.get("clamp_max"))
    if dist == "normal":
        mu = float(spec.get("mu", 0.0))
        sigma = max(0.0, float(spec.get("sigma", 0.0)))
        value = mu if sigma == 0 else rng.gauss(mu, sigma)
        return clamp(value, spec.get("clamp_min"), spec.get("clamp_max"))
    if dist == "exponential":
        mean = max(0.000001, float(spec.get("mean", 1.0)))
        value = rng.expovariate(1.0 / mean)
        return clamp(value, spec.get("clamp_min"), spec.get("clamp_max"))
    raise ValueError(f"Unsupported distribution: {dist}")


def infer_total_from_filename(path: str | Path | None) -> int:
    if not path:
        return DEFAULT_TOTAL_NODES
    name = Path(path).name
    match = re.match(r"^(\d+)(?:[-_.]|$)", name)
    return int(match.group(1)) if match else DEFAULT_TOTAL_NODES


def infer_counts(total: int, workers: int | None = None, requesters: int | None = None) -> tuple[int, int]:
    if workers is not None and requesters is not None:
        return max(0, workers), max(0, requesters)
    if workers is not None:
        return max(0, workers), max(0, total - workers)
    if requesters is not None:
        return max(0, total - requesters), max(0, requesters)
    worker_count = int(round(total * DEFAULT_WORKER_RATIO))
    return worker_count, max(0, total - worker_count)


def allocate_counts(total: int, cohorts: list[dict[str, Any]]) -> dict[str, int]:
    if total <= 0:
        return {str(c["name"]): 0 for c in cohorts}
    defaults = [max(0, int(c.get("default_count", 0))) for c in cohorts]
    denom = sum(defaults) or len(cohorts)
    raw = [total * count / denom for count in defaults]
    counts = [int(math.floor(value)) for value in raw]
    remainder = total - sum(counts)
    order = sorted(range(len(cohorts)), key=lambda idx: (raw[idx] - counts[idx], defaults[idx]), reverse=True)
    for idx in order[:remainder]:
        counts[idx] += 1
    return {str(cohorts[idx]["name"]): counts[idx] for idx in range(len(cohorts))}


def as_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalize_hub_base_urls(value: str | Sequence[str] | None, fallback: str = DEFAULT_HUB_BASE_URL) -> list[str]:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                values = [str(item).strip() for item in parsed]
            else:
                values = [part.strip() for part in raw.split(",")]
        else:
            values = [part.strip() for part in raw.split(",")]
    elif value is None:
        values = []
    else:
        values = [str(item).strip() for item in value]
    clean = [url.rstrip("/") for url in values if url]
    if not clean:
        clean = [str(fallback).strip().rstrip("/")]
    return clean


def behavior_profile(mode: str) -> dict[str, Any]:
    return dict(NODE_BEHAVIOR_MODES.get(str(mode), NODE_BEHAVIOR_MODES["mixed_market"]))


def choose_behavior_mode(kind: str, rng: random.Random) -> str:
    weights = WORKER_BEHAVIOR_MODE_WEIGHTS if kind == "worker" else REQUESTER_BEHAVIOR_MODE_WEIGHTS
    return str(sample_distribution(weights, rng))


def model_distribution_from_models(models: Sequence[str]) -> dict[str, Any]:
    clean = [str(model) for model in models if str(model)]
    if not clean:
        clean = ["mock-ai-model-phase9"]
    weight = max(1, int(100 / len(clean)))
    return {"distribution": "discrete", "values": [{"value": model, "weight": weight} for model in clean]}


def unique_weighted_values(items: Sequence[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in items:
        value = str(item.get("value", "")).strip() if isinstance(item, dict) else ""
        if value and value not in values:
            values.append(value)
    return values or ["mock-ai-model-phase9"]


def worker_request_offer_distribution(models: Sequence[str]) -> dict[str, Any]:
    if any("premium" in str(model).lower() for model in models):
        return {"distribution": "discrete", "values": [{"value": 2, "weight": 55}, {"value": 5, "weight": 35}, {"value": 8, "weight": 10}]}
    return {"distribution": "discrete", "values": [{"value": 1, "weight": 60}, {"value": 2, "weight": 35}, {"value": 5, "weight": 5}]}


def common_behavior_fields(node_id: str, behavior_mode: str, rng: random.Random) -> dict[str, Any]:
    profile = behavior_profile(behavior_mode)
    return {
        "behavior_mode": behavior_mode,
        "account_id": f"lab-account-{node_id}",
        "initial_credits": int(sample_distribution(profile["initial_credits"], rng)),
        "low_credit_threshold": int(profile["low_credit_threshold"]),
        "funding_remediation": str(profile["funding_remediation"]),
        "faucet_top_up_credits": int(profile["faucet_top_up_credits"]),
        "low_credit_work_seconds": int(profile["low_credit_work_seconds"]),
        "insufficient_credit_backoff_ms": int(profile["insufficient_credit_backoff_ms"]),
        "request_probability": float(profile["request_probability"]),
        "worker_offer_probability": float(profile["worker_offer_probability"]),
        "local_busy_probability_per_minute": float(profile["local_busy_probability_per_minute"]),
        "local_busy_median_ms": int(profile["local_busy_median_ms"]),
        "local_busy_max_ms": int(profile["local_busy_max_ms"]),
    }


def make_worker_node(
    *,
    rng: random.Random,
    seed: int,
    global_index: int,
    cohort_index: int,
    cohort: dict[str, Any],
    hub_base_url: str,
    hub_base_urls: Sequence[str] | None,
    network: str,
    ring: int,
    chain_id: int,
    problematic_worker_rate: float,
    problematic_failure_multiplier: float,
    disable_problematic: bool,
) -> dict[str, Any]:
    node_seed = rng.randrange(1, 2**31)
    node_rng = random.Random(node_seed)
    node_id = f"worker-{global_index:04d}"
    cohort_name = str(cohort["name"])
    behavior_mode = choose_behavior_mode("worker", node_rng)
    common_fields = common_behavior_fields(node_id, behavior_mode, node_rng)
    latency_multiplier = float(sample_distribution(cohort["latency_multiplier"], node_rng))
    failure_multiplier = float(sample_distribution(cohort["failure_multiplier"], node_rng))
    problematic = bool(cohort.get("always_problematic", False))
    if not problematic and not disable_problematic and node_rng.random() < problematic_worker_rate:
        problematic = True
    if problematic:
        failure_multiplier *= problematic_failure_multiplier
    tags = [cohort_name]
    if problematic:
        tags.append("problematic")
    if failure_multiplier >= 2.0:
        tags.append("elevated-failure")
    models = list(cohort.get("models", ["mock-ai-model-phase9"]))
    min_credits = int(sample_distribution(cohort["min_accepted_credits"], node_rng))
    max_concurrency = int(sample_distribution(cohort["max_concurrency"], node_rng))
    request_offer_distribution = worker_request_offer_distribution(models)
    offered_credits = sample_distribution(request_offer_distribution, node_rng)
    model_distribution = model_distribution_from_models(models)
    ready_offsets = dict(cohort.get("ready_offsets", {}))
    heartbeat_interval = int(round(2000 * clamp(node_rng.lognormvariate(math.log(1.0), 0.07), 0.80, 1.25)))
    startup_delay = int(round(sample_distribution({"distribution": "lognormal", "median": 1200, "sigma": 0.75, "clamp_min": 25, "clamp_max": 18000}, node_rng)))
    return {
        "node_id": node_id,
        "kind": "worker",
        **common_fields,
        "cohort": cohort_name,
        "tags": ",".join(tags),
        "hub_base_url": hub_base_url,
        "hub_base_urls_json": as_json(normalize_hub_base_urls(hub_base_urls, hub_base_url)),
        "network": network,
        "ring": ring,
        "chain_id": chain_id,
        "sim_seed": node_seed,
        "model": models[0],
        "models_json": as_json(models),
        "min_accepted_credits": min_credits,
        "offered_credits": offered_credits,
        "max_concurrency": max_concurrency,
        "startup_delay_ms": startup_delay,
        "heartbeat_interval_ms": heartbeat_interval,
        "heartbeat_jitter_ms": int(round(90 * latency_multiplier)),
        "heartbeat_drop_probability": round(min(0.50, 0.006 * failure_multiplier), 6),
        "disconnect_hazard_per_minute": round(min(1.0, 0.004 * failure_multiplier), 6),
        "stale_hazard_per_minute": round(min(1.0, 0.003 * failure_multiplier), 6),
        "recovery_median_seconds": round(25 * clamp(failure_multiplier, 0.5, 5.0), 3),
        "probe_delivery_drop_probability": round(min(0.50, 0.0025 * failure_multiplier), 6),
        "probe_delivery_delay_median_ms": int(round(35 * latency_multiplier)),
        "ready_temperature": 0.75,
        "ready_fast_median_ms": int(round(120 * latency_multiplier)),
        "ready_normal_median_ms": int(round(450 * latency_multiplier)),
        "ready_slow_median_ms": int(round(1800 * latency_multiplier)),
        "decline_energy": round(2.3 + float(ready_offsets.get("decline", 0.0)), 4),
        "no_response_energy": round(3.2 + float(ready_offsets.get("no_response", 0.0)), 4),
        "post_ready_disconnect_probability": round(min(0.75, 0.0035 * failure_multiplier), 6),
        "post_ready_capacity_race_probability": round(min(0.50, 0.0015 * failure_multiplier), 6),
        "model_load_failure_probability": round(min(0.50, 0.0025 * failure_multiplier), 6),
        "runtime_normal_median_ms": int(round(1600 * latency_multiplier)),
        "runtime_slow_median_ms": int(round(5500 * latency_multiplier)),
        "execution_disconnect_hazard_per_minute": round(min(1.0, 0.015 * failure_multiplier), 6),
        "execution_crash_probability": round(min(0.75, 0.0035 * failure_multiplier), 6),
        "result_submit_delay_median_ms": int(round(80 * latency_multiplier)),
        "result_submit_drop_probability": round(min(0.50, 0.0025 * failure_multiplier), 6),
        "result_submit_duplicate_probability": round(min(0.50, 0.0008 * failure_multiplier), 6),
        "request_interval_mean_ms": int(behavior_profile(behavior_mode)["request_interval_mean_ms"]),
        "burst_probability_per_minute": 0.04,
        "burst_multiplier_median": 2.0,
        "offered_credits_distribution_json": as_json(request_offer_distribution),
        "model_distribution_json": as_json(model_distribution),
    }


def make_requester_node(
    *,
    rng: random.Random,
    seed: int,
    global_index: int,
    cohort_index: int,
    cohort: dict[str, Any],
    hub_base_url: str,
    hub_base_urls: Sequence[str] | None,
    network: str,
    ring: int,
    chain_id: int,
    problematic_requester_rate: float,
    problematic_failure_multiplier: float,
    disable_problematic: bool,
) -> dict[str, Any]:
    node_seed = rng.randrange(1, 2**31)
    node_rng = random.Random(node_seed)
    node_id = f"requester-{global_index:04d}"
    cohort_name = str(cohort["name"])
    behavior_mode = choose_behavior_mode("requester", node_rng)
    common_fields = common_behavior_fields(node_id, behavior_mode, node_rng)
    problematic = False if disable_problematic else node_rng.random() < problematic_requester_rate
    tags = [cohort_name]
    if problematic:
        tags.append("problematic")
    failure_multiplier = problematic_failure_multiplier if problematic else 1.0
    interval_spec = dict(cohort["request_interval_ms"])
    profile_interval = int(behavior_profile(behavior_mode)["request_interval_mean_ms"])
    mean_interval = int(round((int(interval_spec.get("mean", 1400)) + profile_interval) / 2))
    first_offer = sample_distribution(cohort["offered_credits"], node_rng)
    first_model = sample_distribution({"distribution": "discrete", "values": cohort["model_weights"]}, node_rng)
    worker_models = unique_weighted_values(cohort["model_weights"])
    min_credits = int(sample_distribution({"distribution": "discrete", "values": [{"value": 1, "weight": 70}, {"value": 2, "weight": 25}, {"value": 5, "weight": 5}]}, node_rng))
    max_concurrency = int(sample_distribution({"distribution": "discrete", "values": [{"value": 1, "weight": 88}, {"value": 2, "weight": 12}]}, node_rng))
    worker_latency_multiplier = float(sample_distribution({"distribution": "lognormal", "median": 1.2, "sigma": 0.30, "clamp_min": 0.55, "clamp_max": 3.20}, node_rng))
    return {
        "node_id": node_id,
        "kind": "requester",
        **common_fields,
        "cohort": cohort_name,
        "tags": ",".join(tags),
        "hub_base_url": hub_base_url,
        "hub_base_urls_json": as_json(normalize_hub_base_urls(hub_base_urls, hub_base_url)),
        "network": network,
        "ring": ring,
        "chain_id": chain_id,
        "sim_seed": node_seed,
        "model": first_model,
        "models_json": as_json(worker_models),
        "min_accepted_credits": min_credits,
        "offered_credits": first_offer,
        "max_concurrency": max_concurrency,
        "startup_delay_ms": int(round(sample_distribution({"distribution": "lognormal", "median": 700, "sigma": 0.70, "clamp_min": 25, "clamp_max": 12000}, node_rng))),
        "heartbeat_interval_ms": int(round(2600 * clamp(worker_latency_multiplier, 0.75, 2.50))),
        "heartbeat_jitter_ms": int(round(140 * worker_latency_multiplier)),
        "heartbeat_drop_probability": round(min(0.50, 0.004 * failure_multiplier), 6),
        "disconnect_hazard_per_minute": round(min(1.0, 0.0015 * failure_multiplier), 6),
        "stale_hazard_per_minute": round(min(1.0, 0.002 * failure_multiplier), 6),
        "recovery_median_seconds": round(35 * clamp(failure_multiplier, 0.5, 5.0), 3),
        "probe_delivery_drop_probability": round(min(0.50, 0.002 * failure_multiplier), 6),
        "probe_delivery_delay_median_ms": int(round(45 * worker_latency_multiplier)),
        "ready_temperature": 0.8,
        "ready_fast_median_ms": int(round(160 * worker_latency_multiplier)),
        "ready_normal_median_ms": int(round(600 * worker_latency_multiplier)),
        "ready_slow_median_ms": int(round(2200 * worker_latency_multiplier)),
        "decline_energy": 2.4,
        "no_response_energy": 3.3,
        "post_ready_disconnect_probability": round(min(0.75, 0.003 * failure_multiplier), 6),
        "post_ready_capacity_race_probability": round(min(0.50, 0.001 * failure_multiplier), 6),
        "model_load_failure_probability": round(min(0.50, 0.002 * failure_multiplier), 6),
        "runtime_normal_median_ms": int(round(1900 * worker_latency_multiplier)),
        "runtime_slow_median_ms": int(round(6500 * worker_latency_multiplier)),
        "execution_disconnect_hazard_per_minute": round(min(1.0, 0.010 * failure_multiplier), 6),
        "execution_crash_probability": round(min(0.75, 0.003 * failure_multiplier), 6),
        "result_submit_delay_median_ms": int(round(100 * worker_latency_multiplier)),
        "result_submit_drop_probability": round(min(0.50, 0.001 * failure_multiplier), 6),
        "result_submit_duplicate_probability": "",
        "request_interval_mean_ms": mean_interval,
        "burst_probability_per_minute": 0.12 if not problematic else round(min(0.75, 0.12 * failure_multiplier), 6),
        "burst_multiplier_median": 3.0 if not problematic else round(3.0 * min(failure_multiplier, 4.0), 3),
        "offered_credits_distribution_json": as_json(cohort["offered_credits"]),
        "model_distribution_json": as_json({"distribution": "discrete", "values": cohort["model_weights"]}),
    }


def build_nodes(
    *,
    total: int = DEFAULT_TOTAL_NODES,
    workers: int | None = None,
    requesters: int | None = None,
    seed: int = DEFAULT_SEED,
    hub_base_url: str = DEFAULT_HUB_BASE_URL,
    hub_base_urls: Sequence[str] | None = None,
    network: str = "dev",
    ring: int = 2,
    chain_id: int = 42424242,
    problematic_worker_rate: float = 0.08,
    problematic_requester_rate: float = 0.05,
    problematic_failure_multiplier: float = 4.0,
    disable_problematic: bool = False,
) -> list[dict[str, Any]]:
    worker_count, requester_count = infer_counts(total, workers, requesters)
    rng = random.Random(seed)
    hub_base_urls = normalize_hub_base_urls(hub_base_urls, hub_base_url)
    hub_base_url = hub_base_urls[0]
    nodes: list[dict[str, Any]] = []
    worker_counts = allocate_counts(worker_count, WORKER_COHORTS)
    requester_counts = allocate_counts(requester_count, REQUESTER_COHORTS)

    global_index = 0
    for cohort in WORKER_COHORTS:
        for cohort_index in range(1, worker_counts[str(cohort["name"])] + 1):
            global_index += 1
            nodes.append(
                make_worker_node(
                    rng=rng,
                    seed=seed,
                    global_index=global_index,
                    cohort_index=cohort_index,
                    cohort=cohort,
                    hub_base_url=hub_base_url,
                    hub_base_urls=hub_base_urls,
                    network=network,
                    ring=ring,
                    chain_id=chain_id,
                    problematic_worker_rate=problematic_worker_rate,
                    problematic_failure_multiplier=problematic_failure_multiplier,
                    disable_problematic=disable_problematic,
                )
            )

    global_index = 0
    for cohort in REQUESTER_COHORTS:
        for cohort_index in range(1, requester_counts[str(cohort["name"])] + 1):
            global_index += 1
            nodes.append(
                make_requester_node(
                    rng=rng,
                    seed=seed,
                    global_index=global_index,
                    cohort_index=cohort_index,
                    cohort=cohort,
                    hub_base_url=hub_base_url,
                    hub_base_urls=hub_base_urls,
                    network=network,
                    ring=ring,
                    chain_id=chain_id,
                    problematic_requester_rate=problematic_requester_rate,
                    problematic_failure_multiplier=problematic_failure_multiplier,
                    disable_problematic=disable_problematic,
                )
            )
    return nodes


def build_document(
    nodes: list[dict[str, Any]],
    *,
    seed: int,
    hub_base_url: str,
    hub_base_urls: Sequence[str] | None = None,
    network: str,
    ring: int,
    chain_id: int,
) -> dict[str, Any]:
    summary: dict[str, int] = {}
    behavior_modes: dict[str, int] = {}
    funding_remediation: dict[str, int] = {}
    for node in nodes:
        summary[node["kind"]] = summary.get(node["kind"], 0) + 1
        mode = str(node.get("behavior_mode", ""))
        if mode:
            behavior_modes[mode] = behavior_modes.get(mode, 0) + 1
        remediation = str(node.get("funding_remediation", ""))
        if remediation:
            funding_remediation[remediation] = funding_remediation.get(remediation, 0) + 1
    clean_hub_base_urls = normalize_hub_base_urls(hub_base_urls, hub_base_url)
    hub_base_url = clean_hub_base_urls[0]
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lab": {
            "name": "hub-lab-instance",
            "seed": seed,
            "hub_base_url": hub_base_url,
            "hub_base_urls": clean_hub_base_urls,
            "network": network,
            "ring": ring,
            "chain_id": chain_id,
        },
        "summary": {
            **summary,
            "total_nodes": len(nodes),
            "behavior_modes": behavior_modes,
            "funding_remediation": funding_remediation,
        },
        "columns": GRID_COLUMNS,
        "nodes": nodes,
    }


def write_jsonl(path: Path, nodes: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for node in nodes:
            handle.write(json.dumps(node, sort_keys=True) + "\n")


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, nodes: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GRID_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for node in nodes:
            writer.writerow(node)


def write_html(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes = document["nodes"]
    rows = []
    for node in nodes:
        cells = "".join(f"<td>{html.escape(str(node.get(col, '')))}</td>" for col in GRID_COLUMNS)
        rows.append(f"<tr>{cells}</tr>")
    header = "".join(f"<th>{html.escape(col)}</th>" for col in GRID_COLUMNS)
    summary = html.escape(json.dumps(document["summary"], sort_keys=True))
    content = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Hub Lab Node Grid</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 1rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
th, td {{ border: 1px solid #ddd; padding: 4px 6px; vertical-align: top; }}
th {{ position: sticky; top: 0; background: #f8f8f8; }}
tr:nth-child(even) {{ background: #fafafa; }}
code {{ background: #f2f2f2; padding: 2px 4px; }}
</style>
</head>
<body>
<h1>Hub Lab Node Grid</h1>
<p>Schema: <code>{html.escape(document["schema"])}</code></p>
<p>Generated: <code>{html.escape(document["generated_at"])}</code></p>
<p>Summary: <code>{summary}</code></p>
<table>
<thead><tr>{header}</tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</body>
</html>
"""
    path.write_text(content, encoding="utf-8")


def write_nodes(path: Path, document: dict[str, Any]) -> None:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        write_jsonl(path, document["nodes"])
    elif suffix == ".json":
        write_json(path, document)
    elif suffix == ".csv":
        write_csv(path, document["nodes"])
    elif suffix in {".html", ".htm"}:
        write_html(path, document)
    else:
        raise ValueError(f"Unsupported output suffix {suffix!r}; use .jsonl, .json, .csv, or .html")


def load_nodes(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        nodes: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if text:
                    item = json.loads(text)
                    if isinstance(item, dict):
                        nodes.append(item)
        return nodes
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("nodes"), list):
            return [dict(item) for item in payload["nodes"] if isinstance(item, dict)]
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        raise ValueError(f"JSON file does not contain a nodes array: {path}")
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raise ValueError(f"Unsupported input suffix {suffix!r}; use .jsonl, .json, or .csv")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a flat Hub lab node-variable grid.")
    parser.add_argument("output", nargs="?", default="120-First-Post.jsonl", help="Output path. Leading integer infers total rows, e.g. 120-First-Post.jsonl.")
    parser.add_argument("--total", type=int, help="Total node rows. Defaults to the leading integer in the output filename.")
    parser.add_argument("--workers", type=int, help="Worker row count. Defaults to 5/6 of total, so 120 => 100.")
    parser.add_argument("--requesters", type=int, help="Requester row count. Defaults to total - workers, so 120 => 20.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--hub-base-url", default=DEFAULT_HUB_BASE_URL)
    parser.add_argument("--hub-base-urls", default="")
    parser.add_argument("--network", default="dev")
    parser.add_argument("--ring", type=int, default=2)
    parser.add_argument("--chain-id", type=int, default=42424242)
    parser.add_argument("--problematic-worker-rate", type=float, default=0.08)
    parser.add_argument("--problematic-requester-rate", type=float, default=0.05)
    parser.add_argument("--problematic-failure-multiplier", type=float, default=4.0)
    parser.add_argument("--disable-problematic", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    output = Path(args.output)
    total = args.total if args.total is not None else infer_total_from_filename(output)
    if total < 0:
        raise SystemExit("--total must be >= 0")
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
    write_nodes(output, document)
    if output.suffix.lower() == ".jsonl":
        sys.stderr.write(f"wrote {len(nodes)} JSONL rows to {output}\n")
    else:
        sys.stderr.write(f"wrote {len(nodes)} node rows to {output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
