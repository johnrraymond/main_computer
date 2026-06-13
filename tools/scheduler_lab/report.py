"""Build a consolidated scheduler-lab node behavior report.

The scheduler lab intentionally writes many small JSONL files while a run is in
progress: one runtime node list, parent events, child process event streams, and
periodic rollups.  This module is the post-run reader for those artifacts.  It
keeps the raw per-node evidence visible, but it first rates the run environment
so shared hub/network failures are not misclassified as bad worker behavior.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPORT_SCHEMA = "main-computer-hub-lab-node-behavior-report/v4"

TRANSPORT_OUTAGE_RATIO = 0.90
TRANSPORT_DEGRADED_RATIO = 0.50
SELF_TERMINATED_OUTAGE_RATIO = 0.50

PIPELINE_ADEQUACY_REQUIREMENTS = {
    "requests_attempted": 20,
    "requests_accepted": 10,
    "worker_heartbeats": 20,
    "worker_polls": 20,
    "leases": 10,
    "executions_started": 10,
    "results_success": 10,
}


def _safe_text(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _percent(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return max(0.0, min(100.0, (float(numerator) / float(denominator)) * 100.0))


def _format_percent(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    if abs(value - round(value)) < 0.005:
        return f"{round(value):.0f}%"
    return f"{value:.2f}%"


def _status_from_event(event: dict[str, Any]) -> int | None:
    if "status" not in event:
        return None
    try:
        return int(event.get("status"))
    except (TypeError, ValueError):
        return None


def _response_summary(event: dict[str, Any]) -> dict[str, Any]:
    summary = event.get("response_summary")
    return summary if isinstance(summary, dict) else {}


def _response_error_text(event: dict[str, Any]) -> str:
    pieces: list[str] = []
    summary = _response_summary(event)
    for container in (event, summary):
        error = container.get("error") if isinstance(container, dict) else None
        if error:
            pieces.append(str(error))
    request = summary.get("request")
    if isinstance(request, dict):
        for key in ("error", "reason_code", "state"):
            if request.get(key):
                pieces.append(str(request.get(key)))
    return " ".join(pieces).lower()


def _is_insufficient_credit_event(event: dict[str, Any]) -> bool:
    text = _response_error_text(event)
    return "insufficient" in text and "credit" in text


def _request_response_event_name(name: str) -> bool:
    return name.startswith("requester.request.") and name != "requester.request.attempted"


def _request_was_accepted(event: dict[str, Any]) -> bool:
    status = _status_from_event(event)
    if status == 0 or status is None:
        return False
    summary = _response_summary(event)
    request_state = _safe_text(event.get("request_state") or summary.get("request_state")).lower()
    request = summary.get("request")
    if isinstance(request, dict):
        request_state = _safe_text(request.get("state") or request_state).lower()
        if request.get("request_id"):
            # A failed 200 response can include a request id.  Do not count it as
            # accepted unless the state is non-failed.
            if request_state and request_state in {"failed", "rejected", "error"}:
                return False
            return True
    if request_state in {"failed", "rejected", "error"}:
        return False
    if event.get("request_id") or summary.get("request_id"):
        return True
    return bool(event.get("ok")) and 200 <= status < 300 and not _is_insufficient_credit_event(event)


def _has_lease(event: dict[str, Any]) -> bool:
    summary = _response_summary(event)
    lease = summary.get("lease") if isinstance(summary, dict) else None
    if isinstance(lease, dict) and (lease.get("lease_id") or lease.get("request_id")):
        return True
    return bool(event.get("lease_id") and event.get("request_id"))


def _event_endpoint(event: dict[str, Any]) -> str:
    summary = _response_summary(event)
    for key in ("hub_base_url", "base_url", "endpoint"):
        value = event.get(key)
        if value:
            return _safe_text(value)
    if isinstance(summary, dict):
        for key in ("hub_base_url", "base_url", "endpoint"):
            value = summary.get(key)
            if value:
                return _safe_text(value)
    return ""


@dataclass
class EndpointMetrics:
    endpoint: str
    http_attempts: int = 0
    http_successes: int = 0
    transport_failures: int = 0
    synthetic_transport_failures: int = 0
    affected_nodes: set[str] = field(default_factory=set)
    request_accepted: int = 0
    leases: int = 0
    result_success: int = 0

    def record_http(self, status: int, node_id: str) -> None:
        self.affected_nodes.add(node_id)
        self.http_attempts += 1
        if status == 0:
            self.transport_failures += 1
        elif 200 <= status < 500:
            self.http_successes += 1

    def record_synthetic_transport_failure(self, node_id: str) -> None:
        self.affected_nodes.add(node_id)
        self.synthetic_transport_failures += 1

    def merge(self, other: "EndpointMetrics") -> None:
        self.http_attempts += other.http_attempts
        self.http_successes += other.http_successes
        self.transport_failures += other.transport_failures
        self.synthetic_transport_failures += other.synthetic_transport_failures
        self.affected_nodes.update(other.affected_nodes)
        self.request_accepted += other.request_accepted
        self.leases += other.leases
        self.result_success += other.result_success

    @property
    def effective_transport_failure_count(self) -> int:
        if self.http_attempts:
            return min(self.transport_failures, self.http_attempts)
        return self.synthetic_transport_failures

    @property
    def transport_failure_ratio(self) -> float:
        denominator = self.http_attempts or self.synthetic_transport_failures
        if not denominator:
            return 0.0
        return min(1.0, self.effective_transport_failure_count / denominator)

    @property
    def transport_success_percent(self) -> float | None:
        denominator = self.http_attempts or self.synthetic_transport_failures
        if not denominator:
            return None
        return _percent(denominator - self.effective_transport_failure_count, denominator)

    @property
    def market_activity_count(self) -> int:
        return self.request_accepted + self.leases + self.result_success

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "http_attempts": self.http_attempts,
            "http_successes": self.http_successes,
            "transport_failures": self.effective_transport_failure_count,
            "synthetic_transport_failure_events": self.synthetic_transport_failures,
            "transport_failure_ratio": self.transport_failure_ratio,
            "transport_success_percent": self.transport_success_percent,
            "affected_nodes": len(self.affected_nodes),
            "market_activity_count": self.market_activity_count,
            "request_accepted": self.request_accepted,
            "leases": self.leases,
            "result_success": self.result_success,
        }


@dataclass(frozen=True)
class PipelineStageAdequacy:
    stage: str
    observed: int
    required: int

    @property
    def usable(self) -> str:
        if self.observed >= self.required:
            return "yes"
        if self.observed > 0:
            return "partial"
        return "no"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "observed": self.observed,
            "required": self.required,
            "usable": self.usable,
        }


def _pipeline_stage(stage: str, observed: int) -> PipelineStageAdequacy:
    return PipelineStageAdequacy(stage=stage, observed=int(observed), required=int(PIPELINE_ADEQUACY_REQUIREMENTS[stage]))


def assess_pipeline_adequacy(nodes: list["NodeMetrics"]) -> dict[str, Any]:
    observed = [node for node in nodes if node.event_counts]
    totals = {
        "requests_attempted": sum(node.request_attempts for node in observed),
        "requests_accepted": sum(node.request_accepted for node in observed),
        "worker_heartbeats": sum(node.heartbeats for node in observed),
        "worker_polls": sum(node.polls for node in observed),
        "leases": sum(node.leases for node in observed),
        "executions_started": sum(node.execution_started for node in observed),
        "results_success": sum(node.result_success for node in observed),
    }
    stages = [_pipeline_stage(stage, totals[stage]) for stage in PIPELINE_ADEQUACY_REQUIREMENTS]
    requester_samples_usable = (
        totals["requests_attempted"] >= PIPELINE_ADEQUACY_REQUIREMENTS["requests_attempted"]
        and totals["requests_accepted"] >= PIPELINE_ADEQUACY_REQUIREMENTS["requests_accepted"]
    )
    worker_pipeline_usable = (
        totals["worker_polls"] >= PIPELINE_ADEQUACY_REQUIREMENTS["worker_polls"]
        and (
            totals["leases"] >= PIPELINE_ADEQUACY_REQUIREMENTS["leases"]
            or totals["executions_started"] >= PIPELINE_ADEQUACY_REQUIREMENTS["executions_started"]
            or totals["results_success"] >= PIPELINE_ADEQUACY_REQUIREMENTS["results_success"]
        )
    )
    if requester_samples_usable and worker_pipeline_usable:
        recommendation = "Run has enough requester and worker-pipeline samples for reliability scoring."
    elif requester_samples_usable:
        recommendation = (
            "Run is not usable for worker reliability scoring: requester samples exist, "
            "but worker heartbeat/poll/lease/execution/result samples are inadequate."
        )
    elif worker_pipeline_usable:
        recommendation = (
            "Run has worker-pipeline samples, but requester samples are inadequate for requester reliability scoring."
        )
    else:
        recommendation = (
            "Run is not usable for reliability scoring: requester and worker-pipeline sample minimums were not met."
        )
    return {
        "stages": [stage.to_dict() for stage in stages],
        "requester_samples_usable": requester_samples_usable,
        "worker_pipeline_usable": worker_pipeline_usable,
        "usable_for_worker_reliability_scoring": requester_samples_usable and worker_pipeline_usable,
        "recommendation": recommendation,
    }


def aggregate_endpoint_metrics(nodes: list["NodeMetrics"]) -> list[EndpointMetrics]:
    by_endpoint: dict[str, EndpointMetrics] = {}
    for node in nodes:
        for endpoint, metrics in node.endpoint_stats.items():
            aggregate = by_endpoint.get(endpoint)
            if aggregate is None:
                aggregate = EndpointMetrics(endpoint=endpoint)
                by_endpoint[endpoint] = aggregate
            aggregate.merge(metrics)
    return sorted(
        by_endpoint.values(),
        key=lambda item: (
            -item.effective_transport_failure_count,
            -item.http_attempts,
            item.endpoint,
        ),
    )


def classify_failure_scope(endpoint_breakdown: list[dict[str, Any]], transport_ratio: float) -> tuple[str, str | None]:
    if transport_ratio < TRANSPORT_DEGRADED_RATIO:
        return "not_transport_dominated", None
    if not endpoint_breakdown:
        return "unknown_endpoint_scope", None

    top_endpoint = endpoint_breakdown[0]["endpoint"] if endpoint_breakdown else None
    endpoints_with_evidence = [
        endpoint
        for endpoint in endpoint_breakdown
        if endpoint.get("http_attempts", 0) or endpoint.get("synthetic_transport_failure_events", 0)
    ]
    if len(endpoints_with_evidence) <= 1:
        return "single_endpoint_or_shared_network", top_endpoint

    failing = [endpoint for endpoint in endpoints_with_evidence if float(endpoint.get("transport_failure_ratio", 0.0)) >= TRANSPORT_DEGRADED_RATIO]
    healthy = [endpoint for endpoint in endpoints_with_evidence if float(endpoint.get("transport_failure_ratio", 0.0)) < 0.20]

    if failing and healthy:
        return "endpoint_specific_transport_outage", top_endpoint
    if len(failing) == len(endpoints_with_evidence):
        return "all_observed_endpoints_transport_outage", top_endpoint
    return "mixed_endpoint_transport_degradation", top_endpoint


@dataclass
class NodeMetrics:
    node_id: str
    configured_kind: str = ""
    cohort: str = ""
    behavior_mode: str = ""
    funding_remediation: str = ""
    worker_enabled: bool | None = None
    requester_enabled: bool | None = None
    event_counts: Counter[str] = field(default_factory=Counter)
    endpoint_stats: dict[str, EndpointMetrics] = field(default_factory=dict)
    http_attempts: int = 0
    http_successes: int = 0
    transport_failures: int = 0
    synthetic_transport_failures: int = 0
    request_attempts: int = 0
    request_accepted: int = 0
    request_failed: int = 0
    request_transport_failed: int = 0
    request_insufficient_credit: int = 0
    heartbeats: int = 0
    heartbeat_success: int = 0
    polls: int = 0
    poll_success: int = 0
    leases: int = 0
    execution_started: int = 0
    execution_finished: int = 0
    execution_crashed: int = 0
    result_success: int = 0
    result_failed: int = 0
    result_dropped: int = 0
    duplicate_results: int = 0
    self_terminated_b2b: int = 0
    local_work_started: int = 0
    started: int = 0
    finished: int = 0
    observed_worker_events: int = 0
    observed_requester_events: int = 0

    @classmethod
    def from_node(cls, node: dict[str, Any]) -> "NodeMetrics":
        node_id = _safe_text(node.get("node_id"), "unknown-node")
        return cls(
            node_id=node_id,
            configured_kind=_safe_text(node.get("kind")),
            cohort=_safe_text(node.get("cohort")),
            behavior_mode=_safe_text(node.get("behavior_mode")),
            funding_remediation=_safe_text(node.get("funding_remediation")),
            worker_enabled=_as_bool(node.get("worker_enabled")) if "worker_enabled" in node else None,
            requester_enabled=_as_bool(node.get("requester_enabled")) if "requester_enabled" in node else None,
        )

    def record(self, event: dict[str, Any]) -> None:
        name = _safe_text(event.get("event"))
        if not name:
            return
        self.event_counts[name] += 1

        if name.startswith("worker."):
            self.observed_worker_events += 1
        if name.startswith("requester."):
            self.observed_requester_events += 1

        status = _status_from_event(event)
        endpoint_name = _event_endpoint(event)
        endpoint_metrics = self.endpoint_stats.get(endpoint_name) if endpoint_name else None
        if endpoint_name and endpoint_metrics is None:
            endpoint_metrics = EndpointMetrics(endpoint=endpoint_name)
            self.endpoint_stats[endpoint_name] = endpoint_metrics

        if status is not None and name != "node.transport_failure":
            # Count concrete HTTP responses only.  node.transport_failure is a
            # synthetic companion event emitted after a status-0 response, so it
            # is tracked separately and never counted as another HTTP attempt.
            self.http_attempts += 1
            if endpoint_metrics is not None:
                endpoint_metrics.record_http(status, self.node_id)
            if status == 0:
                self.transport_failures += 1
            elif 200 <= status < 500:
                self.http_successes += 1

        if name == "node.transport_failure":
            # Preserve this as raw evidence, but do not add it to
            # transport_failures when concrete status-0 HTTP events are present.
            self.synthetic_transport_failures += 1
            if endpoint_metrics is not None:
                endpoint_metrics.record_synthetic_transport_failure(self.node_id)

        if name == "node.process.started":
            self.started += 1
            if "worker_enabled" in event:
                self.worker_enabled = _as_bool(event.get("worker_enabled"))
            if "requester_enabled" in event:
                self.requester_enabled = _as_bool(event.get("requester_enabled"))
        elif name == "node.process.finished":
            self.finished += 1
        elif name == "node.self_terminated.b2bfailures":
            self.self_terminated_b2b += 1
        elif name == "node.local_work.started":
            self.local_work_started += 1

        if name == "requester.request.attempted":
            self.request_attempts += 1
        elif _request_response_event_name(name):
            if status == 0:
                self.request_transport_failed += 1
            elif _is_insufficient_credit_event(event):
                self.request_failed += 1
                self.request_insufficient_credit += 1
            elif _request_was_accepted(event):
                self.request_accepted += 1
                if endpoint_metrics is not None:
                    endpoint_metrics.request_accepted += 1
            elif status is not None:
                self.request_failed += 1

        if name == "worker.heartbeat":
            self.heartbeats += 1
            if status and 200 <= status < 300:
                self.heartbeat_success += 1
        elif name == "worker.poll":
            self.polls += 1
            if status and 200 <= status < 300:
                self.poll_success += 1
            if _has_lease(event):
                self.leases += 1
                if endpoint_metrics is not None:
                    endpoint_metrics.leases += 1
        elif name == "worker.execution.started":
            self.execution_started += 1
        elif name == "worker.execution.finished":
            self.execution_finished += 1
        elif name == "worker.execution.crashed":
            self.execution_crashed += 1
        elif name == "worker.result.dropped_by_lab":
            self.result_dropped += 1
        elif name == "worker.result.submitted":
            if status == 0:
                self.transport_failures += 0
            elif bool(event.get("ok")) and (status is None or 200 <= status < 300):
                self.result_success += 1
                if endpoint_metrics is not None:
                    endpoint_metrics.result_success += 1
            elif status is not None:
                self.result_failed += 1
        elif name == "worker.result.failure_submitted":
            self.result_failed += 1
        elif name == "worker.result.duplicate_submitted":
            self.duplicate_results += 1

    @property
    def observed(self) -> bool:
        return bool(self.event_counts or self.configured_kind)

    @property
    def effective_transport_failure_count(self) -> int:
        if self.http_attempts:
            return min(self.transport_failures, self.http_attempts)
        return self.synthetic_transport_failures

    @property
    def transport_success_percent(self) -> float | None:
        denominator = self.http_attempts or self.synthetic_transport_failures
        if not denominator:
            return None
        failures = min(self.effective_transport_failure_count, denominator)
        return _percent(denominator - failures, denominator)

    @property
    def request_accept_percent(self) -> float | None:
        return _percent(self.request_accepted, self.request_attempts)

    @property
    def lease_completion_percent(self) -> float | None:
        if self.leases:
            return _percent(self.result_success, self.leases)
        if self.execution_started:
            return _percent(self.execution_finished - self.execution_crashed - self.result_dropped, self.execution_started)
        return None

    @property
    def execution_finish_percent(self) -> float | None:
        return _percent(self.execution_finished, self.execution_started)

    def observed_role(self) -> str:
        roles: list[str] = []
        if self.worker_enabled is True or self.observed_worker_events:
            roles.append("worker")
        if self.requester_enabled is True or self.observed_requester_events:
            roles.append("requester")
        return "+".join(roles) if roles else "unobserved"

    def reliability_percent(self) -> float | None:
        samples: list[tuple[float, float]] = []
        transport = self.transport_success_percent
        if transport is not None:
            samples.append((transport, 0.45))
        request_accept = self.request_accept_percent
        if request_accept is not None:
            samples.append((request_accept, 0.20))
        lease_completion = self.lease_completion_percent
        if lease_completion is not None:
            samples.append((lease_completion, 0.25))
        heartbeat = _percent(self.heartbeat_success, self.heartbeats)
        if heartbeat is not None:
            samples.append((heartbeat, 0.05))
        poll = _percent(self.poll_success, self.polls)
        if poll is not None:
            samples.append((poll, 0.05))
        if not samples:
            return None
        total_weight = sum(weight for _, weight in samples)
        return sum(value * weight for value, weight in samples) / total_weight

    def raw_summary(self) -> str:
        parts = [self.observed_role()]
        if self.started:
            parts.append("started")
        if self.request_attempts:
            parts.append(f"requests {self.request_accepted}/{self.request_attempts} accepted")
        if self.leases:
            parts.append(f"leases {self.result_success}/{self.leases} completed")
        transport = self.transport_success_percent
        if transport is not None:
            parts.append(f"transport {_format_percent(transport)}")
        request_accept = self.request_accept_percent
        if request_accept is not None:
            parts.append(f"request_accept {_format_percent(request_accept)}")
        lease_completion = self.lease_completion_percent
        if lease_completion is not None:
            parts.append(f"lease_completion {_format_percent(lease_completion)}")
        if self.request_insufficient_credit:
            parts.append(f"insufficient_credit {self.request_insufficient_credit}")
        if self.execution_crashed:
            parts.append(f"crashed {self.execution_crashed}")
        if self.result_dropped:
            parts.append(f"result_dropped {self.result_dropped}")
        if self.self_terminated_b2b:
            parts.append("self_terminated_b2b")
        return "; ".join(parts)


@dataclass
class RunHealth:
    category: str
    score_nodes: bool
    reason: str
    total_http_attempts: int
    transport_failures: int
    synthetic_transport_failure_events: int
    transport_failure_ratio: float
    node_count: int
    observed_node_count: int
    self_terminated_b2b_nodes: int
    market_activity_count: int
    request_accepted: int
    leases: int
    result_success: int
    failure_scope: str = "unknown_endpoint_scope"
    top_transport_endpoint: str | None = None
    endpoint_breakdown: list[dict[str, Any]] = field(default_factory=list)
    pipeline_adequacy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "score_nodes": self.score_nodes,
            "reason": self.reason,
            "total_http_attempts": self.total_http_attempts,
            "transport_failures": self.transport_failures,
            "synthetic_transport_failure_events": self.synthetic_transport_failure_events,
            "transport_failure_ratio": self.transport_failure_ratio,
            "node_count": self.node_count,
            "observed_node_count": self.observed_node_count,
            "self_terminated_b2b_nodes": self.self_terminated_b2b_nodes,
            "market_activity_count": self.market_activity_count,
            "request_accepted": self.request_accepted,
            "leases": self.leases,
            "result_success": self.result_success,
            "failure_scope": self.failure_scope,
            "top_transport_endpoint": self.top_transport_endpoint,
            "endpoint_breakdown": self.endpoint_breakdown,
            "pipeline_adequacy": self.pipeline_adequacy,
        }


CATEGORY_EXPLANATIONS = {
    "excellent": "High transport, request, and worker completion scores.",
    "reliable": "Healthy enough for scheduler preference.",
    "degraded": "Usable, but some dimension shows measurable problems.",
    "unreliable": "Low overall reliability after the run environment was considered rateable.",
    "transport_failed": "This node had a local transport failure pattern in an otherwise rateable run.",
    "transport_unstable": "This node had significant transport failures, but not enough to suppress the whole run.",
    "worker_result_unreliable": "The node accepted/started work but failed, crashed, or dropped results.",
    "credit_blocked": "The node mostly failed because its account lacked usable credits.",
    "request_rejected": "The node sent requests but the scheduler/market rejected most of them.",
    "not_observed": "The node was listed but no behavior events were found.",
    "unscored_transport_outage": "The run had a shared transport outage, so the report preserves raw evidence but does not blame the node.",
    "unscored_no_valid_market_activity": "The run had no accepted requests, leases, or successful results, so individual scoring would be misleading.",
    "unscored_sample_inadequate": "The run had some market activity, but not enough requester and worker-pipeline samples to score reliability.",
}


def categorize_node(metrics: NodeMetrics, health: RunHealth) -> str:
    if not metrics.event_counts:
        return "not_observed"

    if not health.score_nodes:
        if health.category in {"transport_outage", "transport_degraded"}:
            return "unscored_transport_outage"
        if health.category == "sample_inadequate":
            return "unscored_sample_inadequate"
        return "unscored_no_valid_market_activity"

    transport = metrics.transport_success_percent
    request_accept = metrics.request_accept_percent
    lease_completion = metrics.lease_completion_percent
    reliability = metrics.reliability_percent()

    if metrics.self_terminated_b2b and transport is not None and transport < 50.0:
        return "transport_failed"
    if transport is not None and metrics.http_attempts >= 3 and transport < 40.0:
        return "transport_failed"
    if transport is not None and metrics.http_attempts >= 3 and transport < 80.0:
        return "transport_unstable"
    if metrics.request_insufficient_credit and metrics.request_accepted == 0 and metrics.request_attempts:
        return "credit_blocked"
    if (
        metrics.result_dropped
        or metrics.execution_crashed
        or metrics.result_failed
        or (lease_completion is not None and metrics.leases and lease_completion < 80.0)
    ):
        return "worker_result_unreliable"
    if request_accept is not None and metrics.request_attempts >= 2 and request_accept < 50.0:
        return "request_rejected"
    if reliability is None:
        return "degraded"
    if reliability >= 97.0:
        return "excellent"
    if reliability >= 90.0:
        return "reliable"
    if reliability >= 70.0:
        return "degraded"
    return "unreliable"


def assess_run_health(nodes: list[NodeMetrics]) -> RunHealth:
    observed = [node for node in nodes if node.event_counts]
    endpoint_breakdown = [endpoint.to_dict() for endpoint in aggregate_endpoint_metrics(observed)]
    pipeline_adequacy = assess_pipeline_adequacy(nodes)
    failure_scope, top_transport_endpoint = classify_failure_scope(endpoint_breakdown, 0.0)

    def scoped(health: RunHealth) -> RunHealth:
        scope, top_endpoint = classify_failure_scope(endpoint_breakdown, health.transport_failure_ratio)
        health.failure_scope = scope
        health.top_transport_endpoint = top_endpoint
        health.endpoint_breakdown = endpoint_breakdown
        health.pipeline_adequacy = pipeline_adequacy
        return health

    total_http = sum(node.http_attempts for node in observed)
    concrete_transport_failures = sum(node.transport_failures for node in observed)
    synthetic_transport_failures = sum(node.synthetic_transport_failures for node in observed)
    if total_http:
        transport_failures = min(concrete_transport_failures, total_http)
        transport_denominator = total_http
    else:
        # Older event streams may contain only node.transport_failure companion
        # events.  Use them as fallback transport evidence when no concrete
        # HTTP response events are available.
        transport_failures = synthetic_transport_failures
        transport_denominator = synthetic_transport_failures
    transport_ratio = (transport_failures / transport_denominator) if transport_denominator else 0.0
    self_terminated_nodes = sum(1 for node in observed if node.self_terminated_b2b)
    self_terminated_ratio = (self_terminated_nodes / len(observed)) if observed else 0.0
    request_accepted = sum(node.request_accepted for node in observed)
    leases = sum(node.leases for node in observed)
    result_success = sum(node.result_success for node in observed)
    market_activity = request_accepted + leases + result_success

    if not observed:
        return scoped(RunHealth(
            category="no_events",
            score_nodes=False,
            reason="No node event files were found for this run.",
            total_http_attempts=0,
            transport_failures=0,
            synthetic_transport_failure_events=0,
            transport_failure_ratio=0.0,
            node_count=len(nodes),
            observed_node_count=0,
            self_terminated_b2b_nodes=0,
            market_activity_count=0,
            request_accepted=0,
            leases=0,
            result_success=0,
        ))

    if transport_denominator == 0:
        return scoped(RunHealth(
            category="no_http_attempts",
            score_nodes=False,
            reason="Nodes emitted lifecycle events, but no HTTP response attempts were observed.",
            total_http_attempts=0,
            transport_failures=0,
            synthetic_transport_failure_events=0,
            transport_failure_ratio=0.0,
            node_count=len(nodes),
            observed_node_count=len(observed),
            self_terminated_b2b_nodes=self_terminated_nodes,
            market_activity_count=market_activity,
            request_accepted=request_accepted,
            leases=leases,
            result_success=result_success,
        ))

    if transport_ratio >= TRANSPORT_OUTAGE_RATIO or (
        transport_ratio >= TRANSPORT_DEGRADED_RATIO and self_terminated_ratio >= SELF_TERMINATED_OUTAGE_RATIO
    ):
        return scoped(RunHealth(
            category="transport_outage",
            score_nodes=False,
            reason=(
                "Transport failures dominated the run. Treat this as hub/network/environment "
                "failure first; per-node reliability scores are suppressed."
            ),
            total_http_attempts=total_http,
            transport_failures=transport_failures,
            synthetic_transport_failure_events=synthetic_transport_failures,
            transport_failure_ratio=transport_ratio,
            node_count=len(nodes),
            observed_node_count=len(observed),
            self_terminated_b2b_nodes=self_terminated_nodes,
            market_activity_count=market_activity,
            request_accepted=request_accepted,
            leases=leases,
            result_success=result_success,
        ))

    if transport_ratio >= TRANSPORT_DEGRADED_RATIO:
        return scoped(RunHealth(
            category="transport_degraded",
            score_nodes=False,
            reason=(
                "Transport failures were high enough to make node scoring provisional. "
                "Fix hub reachability before rating individual nodes."
            ),
            total_http_attempts=total_http,
            transport_failures=transport_failures,
            synthetic_transport_failure_events=synthetic_transport_failures,
            transport_failure_ratio=transport_ratio,
            node_count=len(nodes),
            observed_node_count=len(observed),
            self_terminated_b2b_nodes=self_terminated_nodes,
            market_activity_count=market_activity,
            request_accepted=request_accepted,
            leases=leases,
            result_success=result_success,
        ))

    if market_activity == 0:
        return scoped(RunHealth(
            category="no_valid_market_activity",
            score_nodes=False,
            reason=(
                "The run had HTTP reachability but no accepted requests, worker leases, "
                "or successful worker results. Raw metrics are shown, but node scoring is suppressed."
            ),
            total_http_attempts=total_http,
            transport_failures=transport_failures,
            synthetic_transport_failure_events=synthetic_transport_failures,
            transport_failure_ratio=transport_ratio,
            node_count=len(nodes),
            observed_node_count=len(observed),
            self_terminated_b2b_nodes=self_terminated_nodes,
            market_activity_count=market_activity,
            request_accepted=request_accepted,
            leases=leases,
            result_success=result_success,
        ))

    if not pipeline_adequacy.get("usable_for_worker_reliability_scoring"):
        return scoped(RunHealth(
            category="sample_inadequate",
            score_nodes=False,
            reason=str(pipeline_adequacy.get("recommendation") or "The run did not meet sample minimums for reliability scoring."),
            total_http_attempts=total_http,
            transport_failures=transport_failures,
            synthetic_transport_failure_events=synthetic_transport_failures,
            transport_failure_ratio=transport_ratio,
            node_count=len(nodes),
            observed_node_count=len(observed),
            self_terminated_b2b_nodes=self_terminated_nodes,
            market_activity_count=market_activity,
            request_accepted=request_accepted,
            leases=leases,
            result_success=result_success,
        ))

    return scoped(RunHealth(
        category="market_activity_observed",
        score_nodes=True,
        reason="The run had enough hub reachability and market activity for per-node scoring.",
        total_http_attempts=total_http,
        transport_failures=transport_failures,
            synthetic_transport_failure_events=synthetic_transport_failures,
        transport_failure_ratio=transport_ratio,
        node_count=len(nodes),
        observed_node_count=len(observed),
        self_terminated_b2b_nodes=self_terminated_nodes,
        market_activity_count=market_activity,
        request_accepted=request_accepted,
        leases=leases,
        result_success=result_success,
    ))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL record: {exc}") from exc
            if isinstance(value, dict):
                records.append(value)
    return records


def discover_run_id(output_dir: Path, run_id: str | None = None) -> str | None:
    if run_id:
        return run_id
    candidates = sorted(output_dir.glob("scheduler-lab-runtime-nodes-*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name))
    if candidates:
        name = candidates[-1].name
        prefix = "scheduler-lab-runtime-nodes-"
        return name[len(prefix) : -len(".jsonl")]
    rollup = output_dir / "scheduler-lab-process-rollup-latest.json"
    if rollup.exists():
        try:
            data = json.loads(rollup.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and data.get("run_id"):
            return str(data["run_id"])
    return None


def find_node_list(output_dir: Path, run_id: str | None) -> Path | None:
    if run_id:
        path = output_dir / f"scheduler-lab-runtime-nodes-{run_id}.jsonl"
        if path.exists():
            return path
    candidates = sorted(output_dir.glob("scheduler-lab-runtime-nodes-*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name))
    return candidates[-1] if candidates else None


def find_event_files(output_dir: Path, run_id: str | None) -> list[Path]:
    if run_id:
        safe_run_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in run_id)
        patterns = [
            f"node-process-{safe_run_id}-*.events.jsonl",
            f"scheduler-lab-process-parent-events-{run_id}.jsonl",
        ]
        files: list[Path] = []
        for pattern in patterns:
            files.extend(output_dir.glob(pattern))
        if files:
            return sorted(set(files))
    return sorted(
        [
            *output_dir.glob("node-process-*.events.jsonl"),
            *output_dir.glob("scheduler-lab-process-parent-events-*.jsonl"),
        ]
    )


def load_report_data(output_dir: Path, run_id: str | None = None) -> tuple[str | None, Path | None, list[Path], list[NodeMetrics], int]:
    resolved_run_id = discover_run_id(output_dir, run_id)
    node_list = find_node_list(output_dir, resolved_run_id)
    node_by_id: dict[str, NodeMetrics] = {}
    if node_list:
        for node in read_jsonl(node_list):
            node_id = _safe_text(node.get("node_id"))
            if not node_id:
                continue
            node_by_id[node_id] = NodeMetrics.from_node(node)

    event_files = find_event_files(output_dir, resolved_run_id)
    events_read = 0
    for event_file in event_files:
        for event in read_jsonl(event_file):
            events_read += 1
            node_id = _safe_text(event.get("node_id"))
            if not node_id:
                continue
            metrics = node_by_id.get(node_id)
            if metrics is None:
                metrics = NodeMetrics(node_id=node_id)
                node_by_id[node_id] = metrics
            metrics.record(event)

    nodes = sorted(node_by_id.values(), key=lambda node: (categorize_sort_hint(node), node.node_id))
    return resolved_run_id, node_list, event_files, nodes, events_read


def categorize_sort_hint(metrics: NodeMetrics) -> tuple[int, float, str]:
    reliability = metrics.reliability_percent()
    return (0 if metrics.event_counts else 1, reliability if reliability is not None else -1.0, metrics.node_id)


def build_report(output_dir: Path, run_id: str | None = None) -> dict[str, Any]:
    resolved_run_id, node_list, event_files, nodes, events_read = load_report_data(output_dir, run_id=run_id)
    health = assess_run_health(nodes)
    rows = []
    categories: Counter[str] = Counter()
    reliabilities: list[float] = []

    # Sort by category, then ascending reliability so the most concerning nodes
    # remain near the top even when the run is rateable.
    enriched: list[tuple[str, NodeMetrics, float | None, float | None]] = []
    for node in nodes:
        category = categorize_node(node, health)
        raw_reliability = node.reliability_percent()
        reported_reliability = raw_reliability if health.score_nodes else None
        enriched.append((category, node, reported_reliability, raw_reliability))
    enriched.sort(key=lambda item: (item[0], item[2] if item[2] is not None else -1.0, item[1].node_id))

    for category, node, reliability, raw_reliability in enriched:
        categories[category] += 1
        if health.score_nodes and reliability is not None:
            reliabilities.append(reliability)
        rows.append(
            {
                "node_id": node.node_id,
                "category": category,
                "reliability_percent": reliability,
                "raw_reliability_percent": raw_reliability,
                "transport_success_percent": node.transport_success_percent,
                "request_accept_percent": node.request_accept_percent,
                "lease_completion_percent": node.lease_completion_percent,
                "observed_role": node.observed_role(),
                "configured_kind": node.configured_kind,
                "cohort": node.cohort,
                "behavior_mode": node.behavior_mode,
                "funding_remediation": node.funding_remediation,
                "behavior": node.raw_summary(),
                "http_attempts": node.http_attempts,
                "transport_failures": node.transport_failures,
                "synthetic_transport_failure_events": node.synthetic_transport_failures,
                "request_attempts": node.request_attempts,
                "request_accepted": node.request_accepted,
                "request_failed": node.request_failed,
                "request_insufficient_credit": node.request_insufficient_credit,
                "heartbeats": node.heartbeats,
                "polls": node.polls,
                "leases": node.leases,
                "execution_started": node.execution_started,
                "execution_finished": node.execution_finished,
                "execution_crashed": node.execution_crashed,
                "result_success": node.result_success,
                "result_dropped": node.result_dropped,
                "self_terminated_b2b": node.self_terminated_b2b,
                "local_work_started": node.local_work_started,
                "top_events": dict(node.event_counts.most_common(10)),
            }
        )

    average = sum(reliabilities) / len(reliabilities) if reliabilities else None
    median = None
    if reliabilities:
        ordered = sorted(reliabilities)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0

    return {
        "schema": REPORT_SCHEMA,
        "run_id": resolved_run_id,
        "output_dir": str(output_dir),
        "node_list": str(node_list) if node_list else None,
        "event_files": [str(path) for path in event_files],
        "events_read": events_read,
        "run_health": health.to_dict(),
        "pipeline_adequacy": health.pipeline_adequacy,
        "node_count": len(nodes),
        "observed_node_count": sum(1 for node in nodes if node.event_counts),
        "average_reliability_percent": average,
        "median_reliability_percent": median,
        "category_counts": dict(sorted(categories.items())),
        "nodes": rows,
        "category_explanations": {key: CATEGORY_EXPLANATIONS[key] for key in sorted(categories) if key in CATEGORY_EXPLANATIONS},
    }


def _markdown_table(headers: list[str], rows: Iterable[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        clean = [str(cell).replace("\n", " ").replace("|", "\\|") for cell in row]
        lines.append("| " + " | ".join(clean) + " |")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    health = report["run_health"]
    lines = ["# Scheduler Lab Node Behavior Report", ""]

    lines.extend(
        [
            "## Run Health Assessment",
            "",
            _markdown_table(
                ["field", "value"],
                [
                    ["category", health["category"]],
                    ["score_nodes", str(bool(health["score_nodes"])).lower()],
                    ["reason", health["reason"]],
                    ["failure_scope", health.get("failure_scope", "unknown_endpoint_scope")],
                    ["top_transport_endpoint", health.get("top_transport_endpoint") or "n/a"],
                    ["transport_failure_ratio", _format_percent(float(health["transport_failure_ratio"]) * 100.0)],
                    ["http_attempts", health["total_http_attempts"]],
                    ["transport_failures", health["transport_failures"]],
                    ["synthetic_transport_failure_events", health.get("synthetic_transport_failure_events", 0)],
                    ["self_terminated_b2b_nodes", health["self_terminated_b2b_nodes"]],
                    ["market_activity_count", health["market_activity_count"]],
                ],
            ),
            "",
        ]
    )

    if not health["score_nodes"]:
        lines.extend(
            [
                "> Individual node scoring is suppressed for this run. The raw node metrics are still shown, "
                "but the categories below should be read as environment/run-health outcomes rather than proof "
                "that each node behaved badly.",
                "",
            ]
        )

    pipeline_adequacy = report.get("pipeline_adequacy") or health.get("pipeline_adequacy") or {}
    if pipeline_adequacy:
        lines.extend(["## Pipeline Adequacy", ""])
        lines.append(str(pipeline_adequacy.get("recommendation") or ""))
        lines.append("")
        lines.append(
            _markdown_table(
                ["stage", "observed", "required", "usable"],
                [
                    [
                        stage.get("stage", ""),
                        stage.get("observed", 0),
                        stage.get("required", 0),
                        stage.get("usable", "no"),
                    ]
                    for stage in pipeline_adequacy.get("stages", [])
                ],
            )
        )
        lines.append("")

    endpoint_breakdown = health.get("endpoint_breakdown") or []
    if endpoint_breakdown:
        lines.extend(["## Hub endpoint health", ""])
        lines.append(
            _markdown_table(
                [
                    "endpoint",
                    "transport_success",
                    "http_attempts",
                    "transport_failures",
                    "synthetic_transport_failure_events",
                    "affected_nodes",
                    "market_activity",
                ],
                [
                    [
                        endpoint["endpoint"],
                        _format_percent(endpoint.get("transport_success_percent")),
                        endpoint.get("http_attempts", 0),
                        endpoint.get("transport_failures", 0),
                        endpoint.get("synthetic_transport_failure_events", 0),
                        endpoint.get("affected_nodes", 0),
                        endpoint.get("market_activity_count", 0),
                    ]
                    for endpoint in endpoint_breakdown
                ],
            )
        )
        lines.append("")

    lines.extend(["## Node behavior ratings", ""])
    lines.append(
        _markdown_table(
            [
                "node_id",
                "category",
                "reliability",
                "transport",
                "request_accept",
                "lease_completion",
                "observed_role",
                "configured_kind",
                "behavior",
            ],
            [
                [
                    row["node_id"],
                    row["category"],
                    _format_percent(row["reliability_percent"]),
                    _format_percent(row["transport_success_percent"]),
                    _format_percent(row["request_accept_percent"]),
                    _format_percent(row["lease_completion_percent"]),
                    row["observed_role"],
                    row["configured_kind"],
                    row["behavior"],
                ]
                for row in report["nodes"]
            ],
        )
    )
    lines.append("")

    lines.extend(["## Run summary", ""])
    summary_rows = [
        ["schema", f"`{report['schema']}`"],
        ["run_id", f"`{report.get('run_id') or ''}`"],
        ["output_dir", f"`{report.get('output_dir') or ''}`"],
        ["node_list", f"`{report.get('node_list') or ''}`"],
        ["event_files", len(report.get("event_files") or [])],
        ["events_read", report.get("events_read", 0)],
        ["node_count", report.get("node_count", 0)],
        ["observed_node_count", report.get("observed_node_count", 0)],
        ["average_reliability_percent", _format_percent(report.get("average_reliability_percent"))],
        ["median_reliability_percent", _format_percent(report.get("median_reliability_percent"))],
    ]
    lines.append(_markdown_table(["field", "value"], summary_rows))
    lines.append("")

    lines.extend(["## Category counts", ""])
    lines.append(_markdown_table(["category", "nodes"], [[key, value] for key, value in report["category_counts"].items()]))
    lines.append("")

    if report.get("category_explanations"):
        lines.extend(["## Category explanations", ""])
        lines.append(
            _markdown_table(
                ["category", "meaning"],
                [[key, value] for key, value in report["category_explanations"].items()],
            )
        )
        lines.append("")

    lines.extend(["## Node metric details", ""])
    detail_headers = [
        "node_id",
        "http_attempts",
        "transport_failures",
        "synthetic_transport_failure_events",
        "request_attempts",
        "request_accepted",
        "request_failed",
        "request_insufficient_credit",
        "heartbeats",
        "polls",
        "leases",
        "execution_started",
        "execution_finished",
        "execution_crashed",
        "result_success",
        "result_dropped",
        "self_terminated_b2b",
        "local_work_started",
    ]
    lines.append(_markdown_table(detail_headers, [[row.get(header, "") for header in detail_headers] for row in report["nodes"]]))
    lines.append("")

    lines.extend(["## Top per-node event counts", ""])
    if report["nodes"]:
        for row in report["nodes"]:
            top_events = row.get("top_events") or {}
            event_summary = ", ".join(f"{key}={value}" for key, value in top_events.items()) or "no events"
            lines.append(f"- `{row['node_id']}`: {event_summary}")
    else:
        lines.append("- no nodes found")
    lines.append("")

    return "\n".join(lines)


CSV_FIELDS = [
    "node_id",
    "category",
    "reliability_percent",
    "transport_success_percent",
    "request_accept_percent",
    "lease_completion_percent",
    "observed_role",
    "configured_kind",
    "cohort",
    "behavior_mode",
    "funding_remediation",
    "http_attempts",
    "transport_failures",
    "synthetic_transport_failure_events",
    "request_attempts",
    "request_accepted",
    "request_failed",
    "request_insufficient_credit",
    "heartbeats",
    "polls",
    "leases",
    "execution_started",
    "execution_finished",
    "execution_crashed",
    "result_success",
    "result_dropped",
    "self_terminated_b2b",
    "local_work_started",
    "behavior",
]


def write_csv(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in report["nodes"]:
            writer.writerow({field: row.get(field) for field in CSV_FIELDS})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build one consolidated scheduler-lab node behavior report.")
    parser.add_argument("--output-dir", required=True, help="Directory containing scheduler-lab runtime node and event JSONL files.")
    parser.add_argument("--run-id", default="", help="Specific run id to report. Defaults to the newest runtime node list.")
    parser.add_argument("--output", default="", help="Markdown output path. Defaults to <output-dir>/node-behavior-report.md.")
    parser.add_argument("--json-output", default="", help="Optional JSON output path.")
    parser.add_argument("--csv-output", default="", help="Optional CSV output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    report = build_report(output_dir, run_id=args.run_id or None)
    output_path = Path(args.output) if args.output else output_dir / "node-behavior-report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(report), encoding="utf-8")

    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.csv_output:
        write_csv(report, Path(args.csv_output))

    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
