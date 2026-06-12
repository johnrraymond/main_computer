from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from tools.scheduler_lab.hub_client import HubClient, HubHttpResponse
from tools.scheduler_lab.node_list import DEFAULT_HUB_BASE_URL, DEFAULT_SEED, load_nodes, normalize_hub_base_urls
from tools.scheduler_lab.run_lab import (
    NodeRuntimeState,
    WorktimeDistribution,
    account_available_credits,
    as_float,
    as_int,
    event_payload,
    hazard_probability_per_tick,
    is_insufficient_credit_response,
    node_account_id,
    node_can_request,
    node_can_work,
    parse_funded_percent,
    parse_warm_spec,
    parse_worktime_spec,
    request_probability,
    sample_exponential_ms,
    sample_lognormal_ms,
    sample_warm_seconds,
    sample_worktime_seconds,
    should_send_startup_request,
    utc_now,
    worker_offer_probability,
)


SELF_TERMINATED_B2B_FAILURES_EXIT_CODE = 75


class SyncEventSink:
    """Per-node event sink.

    Child processes write one event file each, avoiding cross-process file locks
    and keeping the hot path free of a shared Python scheduler/thread pool.
    """

    def __init__(self, output_dir: Path, node: dict[str, Any], node_index: int, run_id: str = "") -> None:
        self.output_dir = output_dir
        self.run_id = str(run_id or "")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_node_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(node.get("node_id") or "node"))
        if self.run_id:
            self.events_path = self.output_dir / f"node-process-{self.run_id}-{node_index:05d}-{safe_node_id}.events.jsonl"
        else:
            self.events_path = self.output_dir / f"node-process-{node_index:05d}-{safe_node_id}.events.jsonl"
        self._handle = self.events_path.open("a", encoding="utf-8", newline="\n")

    def emit(self, event: dict[str, Any]) -> None:
        if self.run_id:
            event.setdefault("run_id", self.run_id)
        self._handle.write(json.dumps(event, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class NodeHttpRunner:
    """HTTP wrapper with immediate retry and b2b transport-failure exit."""

    def __init__(
        self,
        *,
        node: dict[str, Any],
        sink: SyncEventSink,
        b2bfailures: int,
        forced_alive_seconds: float = 0.0,
        started_at: float | None = None,
    ) -> None:
        self.node = node
        self.sink = sink
        self.b2bfailures = max(0, int(b2bfailures))
        self.forced_alive_seconds = max(0.0, float(forced_alive_seconds))
        self.started_at = time.monotonic() if started_at is None else float(started_at)
        self.consecutive_transport_failures = 0

    def alive_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def b2b_detection_enabled(self) -> bool:
        return self.alive_seconds() >= self.forced_alive_seconds

    def _emit_response_event(self, event_name: str, response: HubHttpResponse, event_fields: dict[str, Any] | None = None) -> None:
        response_identity = _response_identity_fields(response.payload)
        response_identity.update(dict(event_fields or {}))
        self.sink.emit(
            event_payload(
                event_name,
                self.node,
                ok=response.ok,
                status=response.status,
                elapsed_ms=round(response.elapsed_ms, 3),
                hub_base_url=response.base_url,
                response_summary=_short_payload(response.payload),
                consecutive_transport_failures=self.consecutive_transport_failures,
                **response_identity,
            )
        )

    def _record_transport_failure(self, response: HubHttpResponse) -> None:
        alive_seconds = self.alive_seconds()
        detection_enabled = self.b2b_detection_enabled()
        if detection_enabled:
            self.consecutive_transport_failures += 1
        else:
            self.consecutive_transport_failures = 0
        self.sink.emit(
            event_payload(
                "node.transport_failure",
                self.node,
                consecutive_transport_failures=self.consecutive_transport_failures,
                b2bfailures=self.b2bfailures,
                forced_alive_seconds=self.forced_alive_seconds,
                alive_seconds=round(alive_seconds, 3),
                b2b_detection_enabled=detection_enabled,
                hub_base_url=response.base_url,
                error=str(response.payload.get("error", "")) if isinstance(response.payload, dict) else "",
            )
        )

    def _reset_transport_failures_on_response(self, response: HubHttpResponse) -> None:
        if self.consecutive_transport_failures:
            self.sink.emit(
                event_payload(
                    "node.transport_failures.reset",
                    self.node,
                    previous_consecutive_transport_failures=self.consecutive_transport_failures,
                    status=response.status,
                    hub_base_url=response.base_url,
                )
            )
        self.consecutive_transport_failures = 0

    def call_once(self, event_name: str, func, *args: Any, **kwargs: Any) -> HubHttpResponse:
        """Make one HTTP attempt and return even on transport failure."""

        event_fields = dict(kwargs.pop("_event_fields", {}) or {})
        response: HubHttpResponse = func(*args, **kwargs)
        self._emit_response_event(event_name, response, event_fields=event_fields)
        if response.status != 0:
            self._reset_transport_failures_on_response(response)
            return response
        self._record_transport_failure(response)
        return response

    def call(self, event_name: str, func, *args: Any, **kwargs: Any) -> HubHttpResponse:
        while True:
            response = self.call_once(event_name, func, *args, **kwargs)
            if response.status != 0:
                return response

            if self.b2b_detection_enabled() and self.b2bfailures and self.consecutive_transport_failures >= self.b2bfailures:
                self.sink.emit(
                    event_payload(
                        "node.self_terminated.b2bfailures",
                        self.node,
                        consecutive_transport_failures=self.consecutive_transport_failures,
                        b2bfailures=self.b2bfailures,
                        forced_alive_seconds=self.forced_alive_seconds,
                        alive_seconds=round(self.alive_seconds(), 3),
                    )
                )
                self.sink.close()
                raise SystemExit(SELF_TERMINATED_B2B_FAILURES_EXIT_CODE)
            # Intentionally no backoff here. A dead advertised hub should cause
            # an immediate random retry against the hub list; all-dead hub fleets
            # drain via --b2bfailures.


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


def top_up_account_to(
    *,
    runner: NodeHttpRunner,
    sink: SyncEventSink,
    node: dict[str, Any],
    client: HubClient,
    account_id: str,
    desired_credits: int,
    reason: str,
) -> None:
    desired = max(0, int(desired_credits))
    if desired <= 0:
        sink.emit(event_payload("node.funding.skipped_zero_target", node, account_id=account_id, reason=reason))
        return

    balance = runner.call("node.funding.balance_checked", client.get_credit_balance, account_id)
    available = account_available_credits(balance.payload)
    if balance.ok and available >= desired:
        sink.emit(event_payload("node.funding.not_needed", node, account_id=account_id, available_credits=available, desired_credits=desired, reason=reason))
        return

    delta = desired if not balance.ok else max(0, desired - available)
    if delta <= 0:
        sink.emit(event_payload("node.funding.not_needed", node, account_id=account_id, available_credits=available, desired_credits=desired, reason=reason))
        return

    runner.call(
        "node.funding.issued",
        client.issue_credits,
        account_id=account_id,
        credits=delta,
        memo=f"scheduler lab {reason} for {node.get('node_id')}",
        metadata={
            "scheduler_lab": True,
            "node_id": node.get("node_id"),
            "node_kind": node.get("kind"),
            "behavior_mode": node.get("behavior_mode"),
            "reason": reason,
        },
    )


def bootstrap_node_funding_sync(node: dict[str, Any], *, args: argparse.Namespace, sink: SyncEventSink, client: HubClient, runner: NodeHttpRunner) -> None:
    account_id = node_account_id(node, args)
    sink.emit(
        event_payload(
            "node.funding.bootstrap.attempted",
            node,
            account_id=account_id,
            bootstrap_funding_enabled=bool(args.bootstrap_funding),
        )
    )
    if not args.bootstrap_funding:
        sink.emit(event_payload("node.funding.bootstrap.disabled", node))
        return
    desired = as_int(node.get("initial_credits"), 0)
    if desired <= 0:
        sink.emit(event_payload("node.funding.bootstrap.skipped_unfunded_start", node, account_id=account_id))
        return
    if bool(node.get("_assumed_prefunded")):
        # The account is expected to exist already, so do not issue bootstrap
        # credits. Still make one non-blocking balance probe so the recovery lab
        # proves that prefunded nodes can reach the shared FDB-backed credit
        # state without letting a dead advertised port trap the node before
        # worker registration/runtime traffic.
        balance = runner.call_once("node.funding.balance_checked", client.get_credit_balance, account_id)
        sink.emit(
            event_payload(
                "node.funding.bootstrap.assumed_prefunded",
                node,
                account_id=account_id,
                desired_credits=desired,
                funded_percent=float(getattr(args, "funded", 0.0) or 0.0),
                balance_ok=balance.ok,
                balance_status=balance.status,
                balance_hub_base_url=balance.base_url,
            )
        )
        return
    top_up_account_to(runner=runner, sink=sink, node=node, client=client, account_id=account_id, desired_credits=desired, reason="bootstrap")


def handle_low_credit_remediation_sync(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: SyncEventSink,
    client: HubClient,
    runner: NodeHttpRunner,
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

    sink.emit(
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
        sink.emit(event_payload("node.low_credit.remediation_faucet", node, account_id=account_id, desired_credits=desired))
        top_up_account_to(runner=runner, sink=sink, node=node, client=client, account_id=account_id, desired_credits=desired, reason="low_credit_faucet")
        state.request_blocked_until = time.monotonic() + backoff_s
        return

    if remediation == "work_to_earn" and node_can_work(node):
        state.worker_force_until = time.monotonic() + work_seconds
        state.request_blocked_until = state.worker_force_until
        sink.emit(event_payload("node.low_credit.remediation_work_to_earn", node, account_id=account_id, work_seconds=round(work_seconds, 3)))
        return

    state.request_blocked_until = time.monotonic() + max(work_seconds, backoff_s)
    sink.emit(
        event_payload(
            "node.low_credit.remediation_dormant",
            node,
            account_id=account_id,
            dormant_seconds=round(max(work_seconds, backoff_s), 3),
            requested_remediation=configured,
        )
    )


def submit_request_once_sync(
    node: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: SyncEventSink,
    client: HubClient,
    runner: NodeHttpRunner,
    rng: random.Random,
    state: NodeRuntimeState,
    request_index: int,
    event_name: str,
    retry_transport: bool = True,
) -> HubHttpResponse:
    prompt = f"scheduler lab request {request_index} from {node.get('node_id')}"
    request_created_ts = utc_now()
    lab_request_key = f"{node.get('node_id') or 'node'}-{int(request_index)}"
    request_fields = {
        "lab_request_key": lab_request_key,
        "request_index": int(request_index),
        "requester_node_id": node.get("node_id"),
        "request_created_ts": request_created_ts,
        "account_id": node_account_id(node, args),
    }
    sink.emit(event_payload("requester.request.attempted", node, attempted_event_name=event_name, **request_fields))
    call = runner.call if retry_transport else runner.call_once
    response = call(
        event_name,
        client.submit_request,
        node,
        request_index=request_index,
        request_mode=args.request_mode,
        account_id_prefix=args.account_id_prefix,
        prompt=prompt,
        _event_fields=request_fields,
    )
    if response.status == 0:
        return response
    if is_insufficient_credit_response(response):
        handle_low_credit_remediation_sync(node, args=args, sink=sink, client=client, runner=runner, rng=rng, state=state, response=response)
    return response


def maybe_start_local_work_sync(
    node: dict[str, Any],
    *,
    state: NodeRuntimeState,
    sink: SyncEventSink,
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
    sink.emit(event_payload("node.local_work.started", node, duration_ms=round(duration_ms, 3), busy_until_monotonic=round(state.local_busy_until, 3)))
    return True


def execute_lease_sync(
    node: dict[str, Any],
    lease: dict[str, Any],
    *,
    args: argparse.Namespace,
    sink: SyncEventSink,
    client: HubClient,
    runner: NodeHttpRunner,
    rng: random.Random,
) -> None:
    if rng.random() < max(0.0, min(1.0, as_float(node.get("post_ready_disconnect_probability"), 0.0))):
        sink.emit(event_payload("worker.lease.disconnect_before_result", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return
    if rng.random() < max(0.0, min(1.0, as_float(node.get("model_load_failure_probability"), 0.0))):
        runner.call(
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
        sink.emit(
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
        sink.emit(event_payload("worker.execution.started", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id"), worker_node_id=node.get("node_id"), execution_started_ts=execution_started_ts, runtime_ms=round(runtime_ms, 3)))
    time.sleep(runtime_ms / 1000.0)
    execution_finished_ts = utc_now()
    sink.emit(event_payload("worker.execution.finished", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id"), worker_node_id=node.get("node_id"), execution_started_ts=locals().get("execution_started_ts"), execution_finished_ts=execution_finished_ts, runtime_ms=round(runtime_ms, 3)))

    if rng.random() < max(0.0, min(1.0, as_float(node.get("execution_crash_probability"), 0.0))):
        sink.emit(event_payload("worker.execution.crashed", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return
    if rng.random() < max(0.0, min(1.0, as_float(node.get("result_submit_drop_probability"), 0.0))):
        sink.emit(event_payload("worker.result.dropped_by_lab", node, lease_id=lease.get("lease_id"), request_id=lease.get("request_id")))
        return

    delay_ms = sample_lognormal_ms(rng, as_float(node.get("result_submit_delay_median_ms"), 80.0), 0.45, clamp_min=0, clamp_max=5000)
    if delay_ms:
        time.sleep(delay_ms / 1000.0)

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
    runner.call("worker.result.submitted", client.submit_worker_result, node, lease, result, _event_fields=result_fields)
    if rng.random() < max(0.0, min(1.0, as_float(node.get("result_submit_duplicate_probability"), 0.0))):
        duplicate_fields = dict(result_fields)
        duplicate_fields["result_submitted_ts"] = utc_now()
        runner.call("worker.result.duplicate_submitted", client.submit_worker_result, node, lease, result, _event_fields=duplicate_fields)


def run_node_process(args: argparse.Namespace) -> int:
    nodes = load_nodes(Path(args.node_list))
    if args.node_index < 0 or args.node_index >= len(nodes):
        raise SystemExit(f"--node-index {args.node_index} is outside node list length {len(nodes)}")
    node = nodes[args.node_index]

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
        retries=0,
        rng=random.Random(seed ^ 0x10ADBEEF),
    )
    sink = SyncEventSink(Path(args.output_dir), node, args.node_index, run_id=str(getattr(args, "run_id", "") or ""))
    process_started_at = time.monotonic()
    runner = NodeHttpRunner(
        node=node,
        sink=sink,
        b2bfailures=args.b2bfailures,
        forced_alive_seconds=args.forced_alive,
        started_at=process_started_at,
    )
    state = NodeRuntimeState()
    stop_at = time.monotonic() + max(0.1, float(args.duration_seconds))
    request_index = 0
    worker_enabled = args.role != "requesters" and node_can_work(node)
    requester_enabled = args.role != "workers" and node_can_request(node)

    try:
        warm_seconds = sample_warm_seconds(rng, args.warm_distribution)
        sink.emit(
            event_payload(
                "node.process.started",
                node,
                node_index=args.node_index,
                worker_enabled=worker_enabled,
                requester_enabled=requester_enabled,
                warm_seconds=round(warm_seconds, 3),
                b2bfailures=args.b2bfailures,
                forced_alive_seconds=float(args.forced_alive),
                hub_base_urls=hub_base_urls,
            )
        )
        if warm_seconds:
            time.sleep(min(warm_seconds, max(0.0, stop_at - time.monotonic())))
        sink.emit(event_payload("node.process.warm_finished", node, node_index=args.node_index))

        startup_request_sent = False
        if requester_enabled and should_send_startup_request(node, args):
            sink.emit(
                event_payload(
                    "requester.request.startup_surge.attempted",
                    node,
                    node_index=args.node_index,
                    pre_bootstrap=True,
                    assumed_prefunded=bool(node.get("_assumed_prefunded")),
                )
            )
            request_index += 1
            startup_response = submit_request_once_sync(
                node,
                args=args,
                sink=sink,
                client=client,
                runner=runner,
                rng=rng,
                state=state,
                request_index=request_index,
                event_name="requester.request.startup_surge.pre_bootstrap",
                retry_transport=False,
            )
            startup_request_sent = startup_response.status != 0

        bootstrap_node_funding_sync(node, args=args, sink=sink, client=client, runner=runner)

        if worker_enabled:
            sink.emit(event_payload("worker.register.attempted", node, node_index=args.node_index))
            runner.call("worker.register", client.register_worker, node)

        if requester_enabled and should_send_startup_request(node, args) and not startup_request_sent:
            sink.emit(
                event_payload(
                    "requester.request.startup_surge.attempted",
                    node,
                    node_index=args.node_index,
                    pre_bootstrap=False,
                    assumed_prefunded=bool(node.get("_assumed_prefunded")),
                )
            )
            request_index += 1
            submit_request_once_sync(
                node,
                args=args,
                sink=sink,
                client=client,
                runner=runner,
                rng=rng,
                state=state,
                request_index=request_index,
                event_name="requester.request.startup_surge",
            )
            startup_request_sent = True

        sink.emit(event_payload("node.process.runtime_entered", node, node_index=args.node_index))

        heartbeat_interval = max(0.1, as_float(node.get("heartbeat_interval_ms"), 2000.0) / 1000.0)
        heartbeat_drop = max(0.0, min(1.0, as_float(node.get("heartbeat_drop_probability"), 0.0)))
        poll_interval = max(0.01, float(args.worker_poll_interval_ms) / 1000.0)
        mean_interval = max(10.0, as_float(node.get("request_interval_mean_ms"), 1400.0))
        burst_probability_per_minute = max(0.0, as_float(node.get("burst_probability_per_minute"), 0.0))
        burst_multiplier = max(1.0, as_float(node.get("burst_multiplier_median"), 1.0))

        next_heartbeat = time.monotonic()
        next_poll = time.monotonic()
        next_request = time.monotonic()
        active_requests = 0

        while time.monotonic() < stop_at:
            now = time.monotonic()

            busy_with_local_work = False
            if args.enable_local_busy:
                busy_with_local_work = maybe_start_local_work_sync(node, state=state, sink=sink, rng=rng, tick_seconds=0.1)

            if worker_enabled and now >= next_heartbeat:
                if rng.random() >= heartbeat_drop:
                    runner.call("worker.heartbeat", client.heartbeat_worker, node, active_requests=active_requests, status="busy" if busy_with_local_work else "available")
                else:
                    sink.emit(event_payload("worker.heartbeat.dropped_by_lab", node))
                jitter = as_float(node.get("heartbeat_jitter_ms"), 0.0) / 1000.0
                next_heartbeat = now + heartbeat_interval + rng.uniform(0.0, max(0.0, jitter))

            if requester_enabled and now >= next_request:
                interval_ms = sample_exponential_ms(rng, mean_interval, clamp_min=100, clamp_max=args.max_request_interval_ms)
                if rng.random() < min(1.0, burst_probability_per_minute / 60.0):
                    interval_ms = max(25.0, interval_ms / burst_multiplier)
                    sink.emit(event_payload("requester.burst_interval", node, interval_ms=round(interval_ms, 3)))
                next_request = now + interval_ms / 1000.0

                if args.request_mode != "registration_only" and now >= state.request_blocked_until and not busy_with_local_work and rng.random() <= request_probability(node):
                    request_index += 1
                    submit_request_once_sync(
                        node,
                        args=args,
                        sink=sink,
                        client=client,
                        runner=runner,
                        rng=rng,
                        state=state,
                        request_index=request_index,
                        event_name="requester.request.submitted",
                    )

            if worker_enabled and now >= next_poll:
                next_poll = now + poll_interval
                offer_probability = worker_offer_probability(node)
                if time.monotonic() < state.worker_force_until:
                    offer_probability = max(offer_probability, 0.95)
                if not busy_with_local_work and rng.random() <= offer_probability:
                    response = runner.call("worker.poll", client.poll_worker, node, lease_seconds=args.lease_seconds, _event_fields={"worker_node_id": node.get("node_id")})
                    lease = response.payload.get("lease") if isinstance(response.payload, dict) else None
                    if isinstance(lease, dict):
                        active_requests += 1
                        try:
                            execute_lease_sync(node, lease, args=args, sink=sink, client=client, runner=runner, rng=rng)
                        finally:
                            active_requests = max(0, active_requests - 1)

            next_times = []
            if worker_enabled:
                next_times.extend([next_heartbeat, next_poll])
            if requester_enabled:
                next_times.append(next_request)
            sleep_for = 0.02
            if next_times:
                sleep_for = max(0.0, min(0.05, min(next_times) - time.monotonic()))
            if sleep_for:
                time.sleep(min(sleep_for, max(0.0, stop_at - time.monotonic())))

        sink.emit(event_payload("node.process.finished", node, node_index=args.node_index))
        return 0
    finally:
        sink.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one scheduler-lab node as one OS process.")
    parser.add_argument("--node-list", required=True)
    parser.add_argument("--node-index", type=int, required=True)
    parser.add_argument("--role", choices=["all", "workers", "requesters"], default=os.environ.get("LAB_ROLE", "all"))
    parser.add_argument("--hub-base-url", default=os.environ.get("HUB_BASE_URL", DEFAULT_HUB_BASE_URL))
    parser.add_argument("--hub-base-urls", default=os.environ.get("HUB_BASE_URLS", ""))
    parser.add_argument("--output-dir", default=os.environ.get("LAB_OUTPUT_DIR", "/lab-output"))
    parser.add_argument("--run-id", default=os.environ.get("LAB_RUN_ID", ""))
    parser.add_argument("--duration-seconds", type=float, default=float(os.environ.get("LAB_DURATION_SECONDS", "300")))
    parser.add_argument("--request-mode", choices=["worker_pull_v0", "legacy", "registration_only"], default=os.environ.get("REQUEST_MODE", "worker_pull_v0"))
    parser.add_argument("--account-id-prefix", default=os.environ.get("LAB_ACCOUNT_ID_PREFIX", "lab-account"))
    parser.add_argument("--funded", type=parse_funded_percent, default=parse_funded_percent(os.environ.get("LAB_FUNDED", "0")))
    parser.add_argument("--bootstrap-funding", dest="bootstrap_funding", action="store_true", default=str(os.environ.get("LAB_BOOTSTRAP_FUNDING", "1")).lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--no-bootstrap-funding", dest="bootstrap_funding", action="store_false")
    parser.add_argument("--request-startup-mode", choices=["auto", "natural", "surge"], default=os.environ.get("LAB_REQUEST_STARTUP_MODE", "auto"))
    parser.add_argument("--request-startup-spread-seconds", type=float, default=float(os.environ.get("LAB_REQUEST_STARTUP_SPREAD_SECONDS", "0")))
    parser.add_argument("--enable-local-busy", dest="enable_local_busy", action="store_true", default=str(os.environ.get("LAB_ENABLE_LOCAL_BUSY", "1")).lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--disable-local-busy", dest="enable_local_busy", action="store_false")
    parser.add_argument("--worker-poll-interval-ms", type=float, default=float(os.environ.get("WORKER_POLL_INTERVAL_MS", "500")))
    parser.add_argument("--lease-seconds", type=float, default=float(os.environ.get("LEASE_SECONDS", "45")))
    parser.add_argument("--worktime", default=os.environ.get("LAB_WORKTIME", ""))
    parser.add_argument("--warm", default=os.environ.get("LAB_WARM", ""))
    parser.add_argument("--b2bfailures", type=int, default=int(os.environ.get("B2B_FAILURES", "10")))
    parser.add_argument("--forced-alive", type=float, default=float(os.environ.get("FORCED_ALIVE_SECONDS", "0")))
    parser.add_argument("--max-runtime-ms", type=float, default=float(os.environ.get("MAX_RUNTIME_MS", "30000")))
    parser.add_argument("--max-request-interval-ms", type=float, default=float(os.environ.get("MAX_REQUEST_INTERVAL_MS", "15000")))
    parser.add_argument("--http-timeout-seconds", type=float, default=float(os.environ.get("HTTP_TIMEOUT_SECONDS", "1")))
    parser.add_argument("--http-retries", type=int, default=0)
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
    return run_node_process(args)


if __name__ == "__main__":
    raise SystemExit(main())
