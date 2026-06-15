from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from main_computer.temporal_fdb_node_market_smoke import (
    DEFAULT_NODE_MARKET_REPORT_PATH,
    NODE_MARKET_TASK_QUEUE_PREFIX,
    NodeMarketSmokeConfig,
    NodeMarketSmokeError,
    RequestSpec,
    WorkerMatch,
    _ProgressReporter,
    _execute_requests,
    _make_request_payload,
    _positive_float,
    _positive_int,
    build_worker_nodes,
)
from tools.temporal_lab.event_log import DEFAULT_EVENT_LOG_PATH, fake_token_text, read_jsonl_events
from tools.temporal_lab.local_temporal import DEFAULT_NAMESPACE


DEFAULT_HUB_NODE_MARKET_REPORT_PATH = Path("runtime") / "temporal_lab" / "temporal_fdb_hub_node_market_report.json"
DEFAULT_HUB_URL = "http://127.0.0.1:8870"
DEFAULT_AUTO_HUB_NAMESPACE_PREFIX = "main-computer-hub-node-market"
DEFAULT_AUTO_HUB_ROOT = Path("runtime") / "temporal_lab" / "exp-fdb-hub-node-market"
DEFAULT_AUTO_HUB_CLUSTER_FILE = Path(".foundationdb") / "docker.cluster"
DEFAULT_HTTP_RETRY_ATTEMPTS = 8
DEFAULT_HTTP_RETRY_BASE_DELAY_SECONDS = 0.25
DEFAULT_HTTP_RETRY_MAX_DELAY_SECONDS = 5.0
_RETRYABLE_HTTP_STATUS_CODES = {429, 503, 504}



@dataclass(frozen=True)
class HubNodeMarketSmokeConfig:
    repo_root: Path
    hub_url: str = DEFAULT_HUB_URL
    execution_mode: str = "live-temporal"
    temporal_address: str = "localhost:7233"
    namespace: str = DEFAULT_NAMESPACE
    report_path: Path | None = DEFAULT_HUB_NODE_MARKET_REPORT_PATH
    event_log_path: Path = DEFAULT_EVENT_LOG_PATH
    node_count: int = 50
    request_count: int = 20
    requested_ring: int = 2
    max_price_credits: int = 2
    deposit_credits: int = 100
    token_count: int = 5
    token_interval_seconds: float = 0.02
    keepalive_interval_seconds: float = 2.0
    account_id: str = "temporal-fdb-hub-node-market-client"
    requester_wallet_address: str = "0x0000000000000000000000000000000000000aa1"
    model: str = "temporal-fdb-hub-node-market-model"
    task_queue_prefix: str = NODE_MARKET_TASK_QUEUE_PREFIX + "-hub"
    run_id: str | None = None
    require_foundationdb_backends: bool = True
    emit_progress: bool = False
    progress_interval_seconds: float = 2.0
    http_timeout_seconds: float = 10.0
    http_retry_attempts: int = DEFAULT_HTTP_RETRY_ATTEMPTS
    hub_start_mode: str = "auto"
    hub_start_timeout_seconds: float = 60.0
    hub_namespace_prefix: str = DEFAULT_AUTO_HUB_NAMESPACE_PREFIX
    hub_root: Path = DEFAULT_AUTO_HUB_ROOT
    cluster_file: Path = DEFAULT_AUTO_HUB_CLUSTER_FILE

    def resolved_report_path(self) -> Path | None:
        if self.report_path is None:
            return None
        return self.report_path if self.report_path.is_absolute() else self.repo_root / self.report_path

    def resolved_event_log_path(self) -> Path:
        return self.event_log_path if self.event_log_path.is_absolute() else self.repo_root / self.event_log_path

    def resolved_hub_root(self) -> Path:
        return self.hub_root if self.hub_root.is_absolute() else self.repo_root / self.hub_root

    def resolved_cluster_file(self) -> Path:
        return self.cluster_file if self.cluster_file.is_absolute() else self.repo_root / self.cluster_file


def _decode_error_payload(body: str) -> dict[str, Any]:
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"error": body}
    return payload if isinstance(payload, dict) else {"error": payload}


def _retry_delay_seconds(error_payload: dict[str, Any], exc: HTTPError, attempt_index: int) -> float:
    raw_retry_after = error_payload.get("retry_after_seconds")
    if raw_retry_after is None:
        raw_retry_after = error_payload.get("retry_after")
    if raw_retry_after is None:
        raw_retry_after = exc.headers.get("Retry-After")

    try:
        delay = float(raw_retry_after)
    except (TypeError, ValueError):
        delay = DEFAULT_HTTP_RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt_index - 1))

    if delay < 0:
        delay = DEFAULT_HTTP_RETRY_BASE_DELAY_SECONDS
    return min(DEFAULT_HTTP_RETRY_MAX_DELAY_SECONDS, max(DEFAULT_HTTP_RETRY_BASE_DELAY_SECONDS, delay))


def _json_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 10.0,
    retry_attempts: int = DEFAULT_HTTP_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    attempts = max(1, int(retry_attempts or 1))
    last_retryable_error: tuple[int, dict[str, Any]] | None = None
    body = ""
    for attempt_index in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=max(1.0, float(timeout or 10.0))) as response:
                body = response.read().decode("utf-8")
            break
        except HTTPError as exc:
            body = exc.read().decode("utf-8")
            error_payload = _decode_error_payload(body)
            if exc.code in _RETRYABLE_HTTP_STATUS_CODES and attempt_index < attempts:
                last_retryable_error = (exc.code, error_payload)
                time.sleep(_retry_delay_seconds(error_payload, exc, attempt_index))
                continue
            if exc.code in _RETRYABLE_HTTP_STATUS_CODES and last_retryable_error is not None:
                raise NodeMarketSmokeError(
                    f"{method} {url} kept returning retryable HTTP {exc.code} after {attempts} attempts: {error_payload}. "
                    "The Hub is applying backpressure; reduce smoke concurrency, increase route capacity, or rerun after the Hub drains."
                ) from exc
            raise NodeMarketSmokeError(f"{method} {url} failed with HTTP {exc.code}: {error_payload}") from exc
        except URLError as exc:
            raise NodeMarketSmokeError(f"{method} {url} could not reach the Hub: {exc.reason}") from exc
        except TimeoutError as exc:
            raise NodeMarketSmokeError(f"{method} {url} timed out while waiting for the Hub.") from exc
    else:  # pragma: no cover - loop should always break or raise
        raise NodeMarketSmokeError(f"{method} {url} failed without a response.")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise NodeMarketSmokeError(f"{method} {url} returned non-JSON response: {body[:200]!r}") from exc
    if not isinstance(result, dict):
        raise NodeMarketSmokeError(f"{method} {url} returned non-object JSON: {type(result).__name__}")
    if result.get("error"):
        raise NodeMarketSmokeError(f"{method} {url} failed: {result['error']}")
    return result


def _get_json(hub_url: str, path: str, *, timeout: float, retry_attempts: int = DEFAULT_HTTP_RETRY_ATTEMPTS) -> dict[str, Any]:
    return _json_request("GET", hub_url.rstrip("/") + path, timeout=timeout, retry_attempts=retry_attempts)


def _post_json(
    hub_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    retry_attempts: int = DEFAULT_HTTP_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    return _json_request("POST", hub_url.rstrip("/") + path, payload, timeout=timeout, retry_attempts=retry_attempts)



def _post_json_expect_http_error(
    hub_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    expected_status: int,
) -> dict[str, Any]:
    """POST JSON and return the error payload when the Hub rejects as expected."""

    url = hub_url.rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout or 10.0))) as response:
            body = response.read().decode("utf-8")
        raise NodeMarketSmokeError(
            f"Expected POST {url} to fail with HTTP {expected_status}, but it succeeded: {body[:300]!r}"
        )
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        error_payload = _decode_error_payload(body)
        error_payload["_http_status"] = exc.code
        if exc.code != expected_status:
            raise NodeMarketSmokeError(
                f"Expected POST {url} to fail with HTTP {expected_status}, got HTTP {exc.code}: {error_payload}"
            ) from exc
        return error_payload
    except URLError as exc:
        raise NodeMarketSmokeError(f"POST {url} could not reach the Hub: {exc.reason}") from exc



def _hub_host_port(hub_url: str) -> tuple[str, int]:
    parsed = urlparse(hub_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NodeMarketSmokeError(f"Invalid --hub-url {hub_url!r}; expected an http://host:port URL.")
    if parsed.scheme == "https":
        default_port = 443
    else:
        default_port = 80
    return parsed.hostname, int(parsed.port or default_port)


def _tcp_accepts_connections(host: str, port: int, *, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=max(0.2, min(float(timeout), 2.0))):
            return True
    except OSError:
        return False


def _hub_is_local(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"} or host.startswith("127.")


def _auto_hub_namespace(config: HubNodeMarketSmokeConfig) -> str:
    prefix = str(config.hub_namespace_prefix or DEFAULT_AUTO_HUB_NAMESPACE_PREFIX).strip() or DEFAULT_AUTO_HUB_NAMESPACE_PREFIX
    suffix = str(config.run_id or "run").strip() or "run"
    return f"{prefix}-{suffix}"


def _auto_hub_command(config: HubNodeMarketSmokeConfig) -> list[str]:
    host, port = _hub_host_port(config.hub_url)
    exp_hub_script = config.repo_root / "exp-fdb-hub.py"
    command = [
        sys.executable,
        str(exp_hub_script),
        "--host",
        host,
        "--port",
        str(port),
        "--repo-root",
        str(config.repo_root),
        "--namespace",
        _auto_hub_namespace(config),
        "--hub-root",
        str(config.resolved_hub_root() / str(config.run_id or "run")),
        "--cluster-file",
        str(config.resolved_cluster_file()),
        "--noverbose",
    ]
    return command


def _format_command(command: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def _manual_hub_start_help(config: HubNodeMarketSmokeConfig) -> str:
    command = _auto_hub_command(config)
    return (
        "Start the Hub in another terminal, then rerun this smoke:\n"
        f"  {_format_command(command)}\n"
        "Or let the smoke start it automatically by omitting --hub-start-mode never."
    )


def _foundationdb_lab_startup_help(config: HubNodeMarketSmokeConfig) -> str:
    cluster_file = config.resolved_cluster_file()
    return (
        "FoundationDB is required for this Hub/FDB smoke. After a reboot, start or refresh the local "
        "FoundationDB smoke container from the repository root in an activated virtualenv:\n"
        "  python .\\scripts\\smoke_foundationdb_credit_ledger_primitives.py --keep-container\n"
        f"Expected cluster file: {cluster_file}\n"
        "The helper writes/refreshes the cluster file and keeps the Docker container running for the Hub smokes."
    )


def _temporal_lab_startup_commands(config: HubNodeMarketSmokeConfig) -> str:
    return (
        "Temporal is required for --execution-mode live-temporal. After a reboot, start the local Temporal dev server:\n"
        "  python -m tools.temporal_lab.local_temporal up --pull\n"
        "  python -m tools.temporal_lab.local_temporal status\n"
        f"Expected Temporal address: {config.temporal_address}\n"
        f"Expected Temporal namespace: {config.namespace}"
    )


def _local_lab_startup_help(config: HubNodeMarketSmokeConfig) -> str:
    return (
        "Local lab bring-up commands:\n"
        "  python -m tools.temporal_lab.local_temporal up --pull\n"
        "  python .\\scripts\\smoke_foundationdb_credit_ledger_primitives.py --keep-container\n"
        "Then rerun this smoke. The Hub itself is auto-started by default; for manual Hub mode use:\n"
        f"  {_format_command(_auto_hub_command(config))}"
    )


class _StartedHubProcess:
    def __init__(self, process: subprocess.Popen[str], command: list[str], *, max_output_lines: int = 120) -> None:
        self.process = process
        self.command = command
        self.output: deque[str] = deque(maxlen=max_output_lines)
        self._reader_thread = threading.Thread(target=self._read_output, name="exp-fdb-hub-smoke-output", daemon=True)
        self._reader_thread.start()

    def _read_output(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                self.output.append(line.rstrip())
        except Exception:
            return

    def output_tail(self) -> str:
        return "\n".join(self.output)

    def stop(self) -> None:
        if self.process.poll() is not None:
            self._reader_thread.join(timeout=2)
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=8)
        self._reader_thread.join(timeout=2)


def _ensure_hub_running(config: HubNodeMarketSmokeConfig, *, progress: _ProgressReporter) -> _StartedHubProcess | None:
    host, port = _hub_host_port(config.hub_url)
    mode = str(config.hub_start_mode or "auto").strip().lower()
    if mode not in {"auto", "never"}:
        raise NodeMarketSmokeError("--hub-start-mode must be 'auto' or 'never'.")

    if _tcp_accepts_connections(host, port, timeout=config.http_timeout_seconds):
        progress.emit("hub_tcp_probe_ok", hub_url=config.hub_url)
        return None

    if mode == "never":
        raise NodeMarketSmokeError(
            f"No Hub is listening at {config.hub_url}.\n"
            + _manual_hub_start_help(config)
        )

    if not _hub_is_local(host):
        raise NodeMarketSmokeError(
            f"No Hub is listening at non-local --hub-url {config.hub_url}; the smoke can only auto-start local Hubs.\n"
            + _manual_hub_start_help(config)
        )

    exp_hub_script = config.repo_root / "exp-fdb-hub.py"
    if not exp_hub_script.exists():
        raise NodeMarketSmokeError(
            f"Cannot auto-start the Hub because exp-fdb-hub.py was not found at {exp_hub_script}.\n"
            + _manual_hub_start_help(config)
        )

    command = _auto_hub_command(config)
    progress.emit("hub_autostart_start", hub_url=config.hub_url, command=_format_command(command))
    try:
        process = subprocess.Popen(
            command,
            cwd=str(config.repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise NodeMarketSmokeError(
            f"Could not start exp-fdb-hub.py: {exc}\n"
            + _manual_hub_start_help(config)
        ) from exc

    started = _StartedHubProcess(process, command)
    deadline = time.monotonic() + max(1.0, float(config.hub_start_timeout_seconds or 60.0))
    last_error = ""
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            tail = started.output_tail()
            raise NodeMarketSmokeError(
                "exp-fdb-hub.py exited before it became reachable.\n"
                f"Command:\n  {_format_command(command)}\n"
                f"Exit code: {return_code}\n"
                f"Recent output:\n{tail or '(no output captured)'}\n\n"
                + _foundationdb_lab_startup_help(config)
            )
        if _tcp_accepts_connections(host, port, timeout=1.0):
            try:
                health = _get_json(config.hub_url, "/api/hub/v1/health", timeout=max(1.0, min(config.http_timeout_seconds, 5.0)))
                if health.get("ok") is True:
                    progress.emit("hub_autostart_ok", hub_url=config.hub_url, namespace=_auto_hub_namespace(config))
                    return started
                last_error = f"health response was not ok: {health}"
            except Exception as exc:  # pragma: no cover - only used for startup diagnostics
                last_error = str(exc)
        time.sleep(0.25)

    tail = started.output_tail()
    started.stop()
    raise NodeMarketSmokeError(
        f"Timed out waiting for exp-fdb-hub.py to listen at {config.hub_url}.\n"
        f"Command:\n  {_format_command(command)}\n"
        f"Last health error: {last_error or '(none)'}\n"
        f"Recent output:\n{tail or '(no output captured)'}\n\n"
        + _foundationdb_lab_startup_help(config)
    )


def _smoke_wallet_address(label: str) -> str:
    digest = hashlib.sha256(str(label or "").encode("utf-8")).hexdigest()
    return "0x" + digest[:40]


def _worker_wallet_address(spec: Any) -> str:
    return _smoke_wallet_address(f"worker:{getattr(spec, 'node_id', '')}")


def _worker_payload(spec: Any, *, model: str) -> dict[str, Any]:
    wallet_address = _worker_wallet_address(spec)
    return {
        "node_id": spec.node_id,
        "endpoint": spec.endpoint,
        "model": model,
        "models": [model],
        "assigned_ring": spec.ring,
        "credits_per_request": spec.price_credits,
        "execution_mode": "worker_pull_v0",
        "wallet_address": wallet_address,
        "pricing": {
            "pricing_type": "fixed_per_call_v0",
            "credits_per_request": spec.price_credits,
            "unit": "compute_credit",
        },
        "capabilities": {
            "provider": "temporal-lab-fake-token",
            "worker_pull_v0": True,
            "assigned_ring": spec.ring,
            "task_queue": spec.task_queue,
            "wallet_address": wallet_address,
            "protected_node_market_smoke": True,
            "keepalive": {"mode": "periodic-http-heartbeat"},
        },
        "max_concurrency": spec.max_concurrency,
    }


async def _heartbeat_loop(
    *,
    config: HubNodeMarketSmokeConfig,
    node: Any,
    stop_event: asyncio.Event,
) -> None:
    payload = {
        "worker_node_id": node.node_id,
        "status": "available",
        "assigned_ring": node.ring,
        "wallet_address": _worker_wallet_address(node),
        "capabilities": {
            "worker_pull_v0": True,
            "assigned_ring": node.ring,
            "task_queue": node.task_queue,
            "wallet_address": _worker_wallet_address(node),
            "keepalive": {"mode": "periodic-http-heartbeat"},
        },
        "max_concurrency": node.max_concurrency,
    }
    interval = max(0.2, float(config.keepalive_interval_seconds or 2.0))
    suffix_match = re.search(r"(\d+)$", str(node.node_id))
    if suffix_match:
        worker_number = int(suffix_match.group(1))
        phase_delay = (worker_number % max(1, config.node_count)) / max(1, config.node_count) * interval
    else:
        phase_delay = 0.0
    if phase_delay:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=phase_delay)
            return
        except asyncio.TimeoutError:
            pass
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(
                _post_json,
                config.hub_url,
                "/api/hub/v1/workers/heartbeat",
                payload,
                timeout=config.http_timeout_seconds,
                retry_attempts=max(1, min(3, config.http_retry_attempts)),
            )
        except Exception:
            # The main smoke verifies final liveness/settlement.  A transient
            # heartbeat failure should not mask the real failure point.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


def _verify_backends(config: HubNodeMarketSmokeConfig, *, progress: _ProgressReporter) -> None:
    try:
        health = _get_json(config.hub_url, "/api/hub/v1/health", timeout=config.http_timeout_seconds)
        if health.get("ok") is not True:
            raise NodeMarketSmokeError(f"Hub health check failed: {health}")
        status = _get_json(config.hub_url, "/api/hub/v1/status", timeout=config.http_timeout_seconds)
        credits = _get_json(config.hub_url, "/api/hub/v1/credits", timeout=config.http_timeout_seconds)
    except NodeMarketSmokeError as exc:
        raise NodeMarketSmokeError(
            f"Could not verify Hub/FDB backends at {config.hub_url}.\n"
            + _foundationdb_lab_startup_help(config)
            + f"\nOriginal Hub probe error: {exc}"
        ) from exc

    progress.emit(
        "hub_probe_ok",
        hub_url=config.hub_url,
        registry_backend=status.get("backend", "json"),
        credit_ledger_backend=credits.get("backend", "json"),
    )
    if config.require_foundationdb_backends:
        if status.get("backend") != "foundationdb":
            raise NodeMarketSmokeError(
                f"Hub registry backend is not foundationdb: {status.get('backend')!r}.\n"
                + _foundationdb_lab_startup_help(config)
            )
        if credits.get("backend") != "foundationdb":
            raise NodeMarketSmokeError(
                f"Hub credit ledger backend is not foundationdb: {credits.get('backend')!r}.\n"
                + _foundationdb_lab_startup_help(config)
            )


def _bridge_fund_requester(config: HubNodeMarketSmokeConfig, *, progress: _ProgressReporter) -> dict[str, Any]:
    wallet_address = str(config.requester_wallet_address or "").strip()
    if not wallet_address:
        wallet_address = _smoke_wallet_address(f"requester:{config.account_id}:{config.run_id}")
    try:
        mint = _post_json(
            config.hub_url,
            "/api/hub/v1/bridge/mock-chain/mint",
            {
                "wallet_address": wallet_address,
                "credits": config.deposit_credits,
                "idempotency_key": f"{config.run_id}-requester-mint",
                "memo": "temporal fdb hub node-market mock-chain requester funding",
                "metadata": {"run_id": config.run_id, "smoke": "temporal_fdb_hub_node_market"},
            },
            timeout=config.http_timeout_seconds,
        )
    except NodeMarketSmokeError:
        if config.require_foundationdb_backends:
            raise
        issue = _post_json(
            config.hub_url,
            "/api/hub/v1/credits/admin/issue",
            {
                "account_id": config.account_id,
                "credits": config.deposit_credits,
                "memo": "temporal fdb hub node-market JSON-hub fallback funding",
                "owner_address": wallet_address,
                "metadata": {"run_id": config.run_id, "smoke": "temporal_fdb_hub_node_market", "bridge_fallback": True},
            },
            timeout=config.http_timeout_seconds,
        )
        if issue.get("ok") is False:
            raise NodeMarketSmokeError(f"Requester fallback funding failed: {issue}")
        progress.emit("hub_credit_issue_ok", account_id=config.account_id, credits=config.deposit_credits, bridge_fallback=True)
        return {"wallet_address": wallet_address, "bridge_available": False, "deposit": {}, "confirmed": issue}
    deposit = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/deposits",
        {
            "wallet_address": wallet_address,
            "account_id": config.account_id,
            "credits": config.deposit_credits,
            "idempotency_key": f"{config.run_id}-requester-bridge-deposit",
            "memo": "temporal fdb hub node-market bridge deposit",
            "metadata": {"run_id": config.run_id, "smoke": "temporal_fdb_hub_node_market"},
        },
        timeout=config.http_timeout_seconds,
    )
    deposit_payload = deposit.get("deposit", {}) if isinstance(deposit.get("deposit"), dict) else {}
    deposit_id = str(deposit_payload.get("deposit_id", ""))
    if not deposit_id:
        raise NodeMarketSmokeError(f"Bridge deposit did not return deposit_id: {deposit}")
    confirmed = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/deposits/confirm",
        {
            "deposit_id": deposit_id,
            "metadata": {"run_id": config.run_id, "smoke": "temporal_fdb_hub_node_market"},
        },
        timeout=config.http_timeout_seconds,
    )
    account = confirmed.get("account", {}) if isinstance(confirmed.get("account"), dict) else {}
    if int(account.get("available_credits", 0) or 0) < config.deposit_credits:
        raise NodeMarketSmokeError(f"Bridge deposit did not fund requester account {config.account_id}: {confirmed}")
    progress.emit(
        "hub_bridge_deposit_ok",
        account_id=config.account_id,
        wallet_address=wallet_address,
        credits=config.deposit_credits,
        deposit_id=deposit_id,
    )
    return {
        "wallet_address": wallet_address,
        "bridge_available": True,
        "mint": mint,
        "deposit": deposit_payload,
        "confirmed": confirmed,
    }


def _verify_surprise_payout_rejected_during_active_work(
    config: HubNodeMarketSmokeConfig,
    *,
    worker: Any,
    progress: _ProgressReporter,
) -> dict[str, Any]:
    """A payout may arrive out of the blue; while the wallet has an active lease it must be rejected."""

    wallet_address = _worker_wallet_address(worker)
    error = _post_json_expect_http_error(
        config.hub_url,
        "/api/hub/v1/bridge/payouts",
        {
            "wallet_address": wallet_address,
            "credits": max(1, int(config.max_price_credits or 1)),
            "idempotency_key": f"{config.run_id}-{worker.node_id}-surprise-payout-while-active",
            "memo": "surprise payout while worker has an active lease should be rejected",
            "metadata": {
                "run_id": config.run_id,
                "smoke": "temporal_fdb_hub_node_market",
                "expected_rejection": "active_worker_lease",
            },
        },
        timeout=config.http_timeout_seconds,
        expected_status=409,
    )
    if error.get("error_type") != "wallet_active_worker_leases":
        raise NodeMarketSmokeError(
            f"Surprise payout for active worker {worker.node_id} was rejected for the wrong reason: {error}"
        )
    active_ids = {str(item) for item in error.get("active_worker_node_ids", [])}
    if worker.node_id not in active_ids:
        raise NodeMarketSmokeError(
            f"Surprise payout rejection did not name the active worker {worker.node_id}: {error}"
        )
    progress.emit(
        "hub_bridge_payout_rejected_active_work",
        wallet_address=wallet_address,
        worker_node_id=worker.node_id,
        error_type=error.get("error_type"),
    )
    return {
        "wallet_address": wallet_address,
        "worker_node_id": worker.node_id,
        "error": error,
        "rejected": True,
    }


def _mock_chain_wallet_available_wei(config: HubNodeMarketSmokeConfig, wallet_address: str) -> int:
    wallet = _get_json(
        config.hub_url,
        f"/api/hub/v1/bridge/mock-chain/wallets?{urlencode({'wallet_address': wallet_address})}",
        timeout=config.http_timeout_seconds,
    )
    wallet_payloads = wallet.get("wallets", []) if isinstance(wallet.get("wallets"), list) else []
    return int(wallet_payloads[0].get("available_credit_wei", 0) or 0) if wallet_payloads else 0


def _bridge_audit_events(
    config: HubNodeMarketSmokeConfig,
    *,
    wallet_address: str = "",
    account_id: str = "",
    worker_node_id: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = {
        "limit": str(limit),
    }
    if wallet_address:
        query["wallet_address"] = wallet_address
    if account_id:
        query["account_id"] = account_id
    if worker_node_id:
        query["worker_node_id"] = worker_node_id
    audit = _get_json(
        config.hub_url,
        f"/api/hub/v1/bridge/audit?{urlencode(query)}",
        timeout=config.http_timeout_seconds,
    )
    events = audit.get("events", [])
    return [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []


def _bridge_audit_types(events: list[dict[str, Any]]) -> set[str]:
    return {str(event.get("event_type", "")) for event in events if isinstance(event, dict)}


def _verify_expected_audit_types(
    *,
    label: str,
    events: list[dict[str, Any]],
    expected_types: set[str],
) -> set[str]:
    event_types = _bridge_audit_types(events)
    missing = sorted(expected_types - event_types)
    if missing:
        raise NodeMarketSmokeError(
            f"Missing bridge audit events for {label}: missing={missing} "
            f"seen={sorted(event_types)} events={events[:10]}"
        )
    return event_types


def _exercise_failed_payout_recovery(
    config: HubNodeMarketSmokeConfig,
    *,
    wallet_address: str,
    worker_node_id: str,
    payout_credits: int,
    progress: _ProgressReporter,
) -> dict[str, Any]:
    before_chain_available_wei = _mock_chain_wallet_available_wei(config, wallet_address)
    payout = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/payouts",
        {
            "wallet_address": wallet_address,
            "worker_node_id": worker_node_id,
            "credits": payout_credits,
            "idempotency_key": f"{config.run_id}-{worker_node_id}-mock-chain-payout-failure",
            "memo": "temporal fdb hub node-market mock-chain payout failure rehearsal",
            "metadata": {
                "run_id": config.run_id,
                "smoke": "temporal_fdb_hub_node_market",
                "expected_outcome": "mock_chain_failure_recovery",
            },
        },
        timeout=config.http_timeout_seconds,
    )
    payout_payload = payout.get("payout", {}) if isinstance(payout.get("payout"), dict) else {}
    payout_id = str(payout_payload.get("payout_id", ""))
    if not payout_id:
        raise NodeMarketSmokeError(f"Failed-payout rehearsal did not return payout_id: {payout}")

    lock_status = _get_json(
        config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=config.http_timeout_seconds,
    )
    if lock_status.get("locked") is not True:
        raise NodeMarketSmokeError(f"Failed-payout rehearsal did not lock wallet {wallet_address}: {lock_status}")

    failed = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/payouts/fail",
        {
            "payout_id": payout_id,
            "reason": "mock_chain_rejected_by_smoke",
            "metadata": {"run_id": config.run_id, "smoke": "temporal_fdb_hub_node_market"},
        },
        timeout=config.http_timeout_seconds,
    )

    final_lock = _get_json(
        config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=config.http_timeout_seconds,
    )
    if final_lock.get("locked") is True:
        raise NodeMarketSmokeError(f"Failed payout did not unlock wallet {wallet_address}: {final_lock}")

    after_chain_available_wei = _mock_chain_wallet_available_wei(config, wallet_address)
    if after_chain_available_wei != before_chain_available_wei:
        raise NodeMarketSmokeError(
            "Failed payout changed mock-chain wallet balance: "
            f"before={before_chain_available_wei} after={after_chain_available_wei}"
        )

    events = _bridge_audit_events(config, wallet_address=wallet_address, worker_node_id=worker_node_id, limit=50)
    audit_types = _verify_expected_audit_types(
        label=f"failed payout {payout_id}",
        events=events,
        expected_types={
            "bridge.wallet.locked",
            "bridge.payout.requested",
            "bridge.payout.failed",
            "bridge.wallet.unlocked",
        },
    )
    progress.emit(
        "hub_bridge_payout_failed_recovered",
        wallet_address=wallet_address,
        worker_node_id=worker_node_id,
        payout_id=payout_id,
        payout_credits=payout_credits,
    )
    return {
        "payout": payout_payload,
        "failed": failed,
        "wallet_lock_released": True,
        "chain_balance_unchanged": True,
        "before_chain_available_wei": str(before_chain_available_wei),
        "after_chain_available_wei": str(after_chain_available_wei),
        "audit_event_types": sorted(audit_types),
    }


def _exercise_worker_payout(
    config: HubNodeMarketSmokeConfig,
    *,
    nodes_by_id: dict[str, Any],
    selected_worker_ids: list[str],
    progress: _ProgressReporter,
) -> dict[str, Any]:
    if not selected_worker_ids:
        raise NodeMarketSmokeError("Cannot exercise mock-chain payout without a selected worker.")
    payout_worker_id = selected_worker_ids[0]
    worker = nodes_by_id[payout_worker_id]
    wallet_address = _worker_wallet_address(worker)
    payout_credits = max(1, int(config.max_price_credits or 1))

    failed_recovery = _exercise_failed_payout_recovery(
        config,
        wallet_address=wallet_address,
        worker_node_id=payout_worker_id,
        payout_credits=payout_credits,
        progress=progress,
    )

    payout = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/payouts",
        {
            "wallet_address": wallet_address,
            "worker_node_id": payout_worker_id,
            "credits": payout_credits,
            "idempotency_key": f"{config.run_id}-{payout_worker_id}-mock-chain-payout",
            "memo": "temporal fdb hub node-market worker payout",
            "metadata": {"run_id": config.run_id, "smoke": "temporal_fdb_hub_node_market"},
        },
        timeout=config.http_timeout_seconds,
    )
    payout_payload = payout.get("payout", {}) if isinstance(payout.get("payout"), dict) else {}
    payout_id = str(payout_payload.get("payout_id", ""))
    if not payout_id:
        raise NodeMarketSmokeError(f"Worker payout did not return payout_id: {payout}")
    lock_status = _get_json(
        config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=config.http_timeout_seconds,
    )
    if lock_status.get("locked") is not True:
        raise NodeMarketSmokeError(f"Payout did not lock worker wallet {wallet_address}: {lock_status}")
    probe_quote = _post_json(
        config.hub_url,
        "/api/hub/v1/requests/quote",
        {
            "account_id": config.account_id,
            "client_node_id": config.account_id,
            "model": config.model,
            "prompt": "Temporal Hub FDB node-market locked-wallet quote probe",
            "max_price_credits": config.max_price_credits,
            "requested_ring": config.requested_ring,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": f"{config.run_id}-locked-wallet-quote-probe",
        },
        timeout=config.http_timeout_seconds,
    )["quote"]
    selected_offer = probe_quote.get("selected_offer", {}) if isinstance(probe_quote.get("selected_offer"), dict) else {}
    probe_worker_id = str(selected_offer.get("worker_node_id") or probe_quote.get("selected_worker_node_id") or "")
    if probe_worker_id == payout_worker_id:
        raise NodeMarketSmokeError(
            f"Hub selected locked payout worker {payout_worker_id} during quote probe: {probe_quote}"
        )
    confirmed = _post_json(
        config.hub_url,
        "/api/hub/v1/bridge/payouts/confirm",
        {
            "payout_id": payout_id,
            "metadata": {"run_id": config.run_id, "smoke": "temporal_fdb_hub_node_market"},
        },
        timeout=config.http_timeout_seconds,
    )
    final_lock = _get_json(
        config.hub_url,
        f"/api/hub/v1/bridge/wallet-locks?{urlencode({'wallet_address': wallet_address})}",
        timeout=config.http_timeout_seconds,
    )
    if final_lock.get("locked") is True:
        raise NodeMarketSmokeError(f"Payout confirmation did not unlock wallet {wallet_address}: {final_lock}")
    chain_available = _mock_chain_wallet_available_wei(config, wallet_address)
    if chain_available <= int(failed_recovery.get("after_chain_available_wei", "0") or 0):
        raise NodeMarketSmokeError(
            f"Confirmed payout did not increase mock-chain worker wallet balance: "
            f"before_confirm={failed_recovery.get('after_chain_available_wei')} after_confirm={chain_available}"
        )
    worker_events = _bridge_audit_events(config, worker_node_id=payout_worker_id, limit=100)
    audit_types = _verify_expected_audit_types(
        label=f"worker payout recovery+confirm for {payout_worker_id}",
        events=worker_events,
        expected_types={
            "hub.worker.earning.recorded",
            "bridge.wallet.locked",
            "bridge.payout.requested",
            "bridge.payout.failed",
            "bridge.wallet.unlocked",
            "bridge.payout.confirmed",
        },
    )
    progress.emit(
        "hub_bridge_payout_ok",
        wallet_address=wallet_address,
        worker_node_id=payout_worker_id,
        payout_id=payout_id,
        payout_credits=payout_credits,
    )
    return {
        "wallet_address": wallet_address,
        "worker_node_id": payout_worker_id,
        "payout": payout_payload,
        "confirmed": confirmed,
        "failed_recovery": failed_recovery,
        "quote_probe_selected_worker_id": probe_worker_id,
        "lock_verified": True,
        "locked_wallet_excluded_from_new_work": probe_worker_id != payout_worker_id,
        "audit_event_types": sorted(audit_types),
        "audit_events": worker_events,
    }



def _verify_bridge_audit_readback(
    config: HubNodeMarketSmokeConfig,
    *,
    bridge_funding: dict[str, Any],
    worker_payout: dict[str, Any],
    progress: _ProgressReporter,
) -> dict[str, Any]:
    if not bridge_funding.get("bridge_available", True):
        return {"ok": True, "bridge_available": False}

    requester_wallet = str(bridge_funding.get("wallet_address", ""))
    worker_wallet = str(worker_payout.get("wallet_address", ""))
    worker_node_id = str(worker_payout.get("worker_node_id", ""))

    requester_events = _bridge_audit_events(config, wallet_address=requester_wallet, account_id=config.account_id, limit=250)
    requester_types = _verify_expected_audit_types(
        label=f"requester bridge/work audit {config.account_id}",
        events=requester_events,
        expected_types={
            "bridge.deposit.requested",
            "bridge.deposit.confirmed",
            "hub.hold.created",
            "hub.hold.charged",
        },
    )

    requester_wallet_events = _bridge_audit_events(config, wallet_address=requester_wallet, limit=250)
    requester_wallet_types = _verify_expected_audit_types(
        label=f"requester wallet audit {requester_wallet}",
        events=requester_wallet_events,
        expected_types={
            "mock_chain.mint",
            "bridge.deposit.requested",
            "bridge.deposit.confirmed",
            "hub.hold.created",
            "hub.hold.charged",
        },
    )

    worker_events = _bridge_audit_events(config, worker_node_id=worker_node_id, limit=250)
    worker_types = _verify_expected_audit_types(
        label=f"worker payout audit {worker_node_id}",
        events=worker_events,
        expected_types={
            "hub.worker.earning.recorded",
            "bridge.payout.requested",
            "bridge.payout.failed",
            "bridge.payout.confirmed",
            "bridge.wallet.locked",
            "bridge.wallet.unlocked",
        },
    )

    progress.emit(
        "hub_bridge_audit_readback_ok",
        requester_event_count=len(requester_wallet_events),
        worker_event_count=len(worker_events),
        worker_node_id=worker_node_id,
    )
    return {
        "ok": True,
        "bridge_available": True,
        "requester_wallet_event_count": len(requester_wallet_events),
        "requester_account_event_count": len(requester_events),
        "worker_event_count": len(worker_events),
        "requester_wallet_event_types": sorted(requester_wallet_types),
        "requester_account_event_types": sorted(requester_types),
        "worker_event_types": sorted(worker_types),
    }



def _register_workers(config: HubNodeMarketSmokeConfig, nodes: list[Any], *, progress: _ProgressReporter) -> None:
    for index, node in enumerate(nodes, start=1):
        result = _post_json(
            config.hub_url,
            "/api/hub/v1/workers/register",
            _worker_payload(node, model=config.model),
            timeout=config.http_timeout_seconds,
        )
        worker = result.get("worker", {}) if isinstance(result.get("worker"), dict) else {}
        offer = worker.get("offer", {}) if isinstance(worker.get("offer"), dict) else {}
        if offer.get("assigned_ring") != node.ring:
            raise NodeMarketSmokeError(
                f"Hub did not preserve assigned_ring for {node.node_id}: expected {node.ring}, got {offer.get('assigned_ring')!r}"
            )
        if index == 1 or index == len(nodes) or index % 10 == 0:
            progress.emit(
                "hub_worker_registered",
                worker_index=index,
                workers_total=len(nodes),
                node_id=node.node_id,
                ring=node.ring,
                price_credits=node.price_credits,
                task_queue=node.task_queue,
            )


def _quote_and_submit_requests(
    config: HubNodeMarketSmokeConfig,
    nodes_by_id: dict[str, Any],
    *,
    progress: _ProgressReporter,
) -> tuple[list[tuple[WorkerMatch, Any]], list[dict[str, Any]]]:
    request_jobs: list[tuple[WorkerMatch, Any]] = []
    submitted_records: list[dict[str, Any]] = []
    for offset in range(config.request_count):
        logical_id = f"hub-node-market-{config.run_id}-{offset + 1:04d}"
        quote_payload = {
            "account_id": config.account_id,
            "client_node_id": config.account_id,
            "model": config.model,
            "prompt": f"Temporal Hub FDB node-market request {offset + 1}",
            "max_price_credits": config.max_price_credits,
            "requested_ring": config.requested_ring,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": f"{logical_id}-quote",
        }
        quote = _post_json(
            config.hub_url,
            "/api/hub/v1/requests/quote",
            quote_payload,
            timeout=config.http_timeout_seconds,
        )["quote"]
        selected_offer = quote.get("selected_offer", {}) if isinstance(quote.get("selected_offer"), dict) else {}
        selected_worker_id = str(selected_offer.get("worker_node_id") or quote.get("selected_worker_node_id") or "")
        selected = nodes_by_id.get(selected_worker_id)
        if selected is None:
            raise NodeMarketSmokeError(f"Hub selected unknown worker {selected_worker_id!r} for {logical_id}.")
        if selected.ring > config.requested_ring or selected.price_credits > config.max_price_credits:
            raise NodeMarketSmokeError(
                f"Hub selected ineligible worker {selected.node_id}: "
                f"ring={selected.ring} requested_ring={config.requested_ring} "
                f"price={selected.price_credits} max_price={config.max_price_credits}"
            )
        submit_payload = {
            **quote_payload,
            "quote_id": quote["quote_id"],
            "metadata": {
                "worker_pull_v0": True,
                "requested_ring": config.requested_ring,
                "expected_token_count": config.token_count,
            },
            "idempotency_key": f"{logical_id}-submit",
        }
        submitted = _post_json(
            config.hub_url,
            "/api/hub/v1/requests",
            submit_payload,
            timeout=config.http_timeout_seconds,
        )["request"]
        request = RequestSpec(
            request_id=str(submitted["request_id"]),
            account_id=config.account_id,
            requested_ring=config.requested_ring,
            max_price_credits=config.max_price_credits,
            token_count=config.token_count,
            token_interval_seconds=config.token_interval_seconds,
        )
        match = WorkerMatch(
            request=request,
            worker=selected,
            partition_size=0,
            candidate_node_ids=(),
        )
        request_jobs.append((match, _make_request_payload(match)))
        submitted_records.append(submitted)
        if (offset + 1) == 1 or (offset + 1) == config.request_count or (offset + 1) % 5 == 0:
            progress.emit(
                "hub_request_submitted",
                submitted=offset + 1,
                total=config.request_count,
                request_id=submitted["request_id"],
                selected_node=selected.node_id,
                selected_ring=selected.ring,
                selected_price_credits=selected.price_credits,
            )
    return request_jobs, submitted_records


def _validate_lease(
    config: HubNodeMarketSmokeConfig,
    *,
    lease: dict[str, Any],
    worker_node_id: str,
    pending_request_ids: set[str],
) -> str:
    request_id = str(lease.get("request_id") or "")
    if request_id not in pending_request_ids:
        raise NodeMarketSmokeError(
            f"Worker {worker_node_id} received unexpected lease {request_id!r}; "
            f"pending requests={sorted(pending_request_ids)[:10]}"
        )
    selected_offer = lease.get("selected_offer", {}) if isinstance(lease.get("selected_offer"), dict) else {}
    if selected_offer.get("assigned_ring") is not None and int(selected_offer["assigned_ring"]) > config.requested_ring:
        raise NodeMarketSmokeError(f"Lease selected_offer has ineligible assigned_ring: {selected_offer}")
    if selected_offer.get("credits_per_request") is not None and int(selected_offer["credits_per_request"]) > config.max_price_credits:
        raise NodeMarketSmokeError(f"Lease selected_offer exceeds max_price_credits: {selected_offer}")
    if "account_id" in lease or "ledger" in lease:
        raise NodeMarketSmokeError(f"Lease leaked requester/accounting internals: keys={sorted(lease)}")
    lease["worker_node_id"] = worker_node_id
    return request_id


async def _execute_and_settle_worker_pull_requests(
    config: HubNodeMarketSmokeConfig,
    *,
    node_config: NodeMarketSmokeConfig,
    nodes_by_id: dict[str, Any],
    request_jobs: list[tuple[WorkerMatch, Any]],
    event_log_path: Path,
    progress: _ProgressReporter,
    verify_active_payout_rejection: bool = False,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Poll Hub leases like real workers, execute leased work, then release capacity via results.

    Earlier versions of this smoke quoted/submitted every request and then tried to
    prefetch every lease before any worker result was posted. That is not how the
    worker-pull path behaves under one-active-lease capacity: a worker should poll,
    execute, submit its result, and only then take more work.  This loop keeps the
    Hub as the lifecycle owner and avoids assuming a specific lease order.
    """

    pending: dict[str, tuple[WorkerMatch, Any]] = {match.request.request_id: (match, request) for match, request in request_jobs}
    leases: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []
    surprise_payout_rejection: dict[str, Any] = {}
    deadline = time.perf_counter() + max(30.0, float(config.request_count) * max(2.0, config.http_timeout_seconds))
    idle_rounds = 0

    while pending:
        if time.perf_counter() > deadline:
            raise NodeMarketSmokeError(
                "Timed out waiting for Hub worker-pull leases/results. "
                f"completed={len(completed)} pending={len(pending)} pending_request_ids={list(pending)[:10]}"
            )

        batch: list[tuple[WorkerMatch, Any]] = []
        batch_leases: dict[str, dict[str, Any]] = {}
        polled_workers_this_round: set[str] = set()

        # Requests are submitted with a quote-selected worker. Poll each selected
        # worker, but accept any still-pending request that worker receives. This
        # lets the Hub decide lease order and respects worker capacity.
        for quoted_match, _quoted_request in list(pending.values()):
            worker = quoted_match.worker
            if worker.node_id in polled_workers_this_round:
                continue
            polled_workers_this_round.add(worker.node_id)
            result = _post_json(
                config.hub_url,
                "/api/hub/v1/workers/poll",
                {"worker_node_id": worker.node_id},
                timeout=config.http_timeout_seconds,
            )
            lease = result.get("lease")
            if not isinstance(lease, dict):
                continue
            request_id = _validate_lease(
                config,
                lease=lease,
                worker_node_id=worker.node_id,
                pending_request_ids=set(pending),
            )
            original_match, _original_request = pending.pop(request_id)
            actual_worker = nodes_by_id.get(worker.node_id, worker)
            actual_match = WorkerMatch(
                request=original_match.request,
                worker=actual_worker,
                partition_size=original_match.partition_size,
                candidate_node_ids=original_match.candidate_node_ids,
            )
            batch.append((actual_match, _make_request_payload(actual_match)))
            batch_leases[request_id] = lease
            leases[request_id] = lease
            leased_count = len(leases)
            if leased_count == 1 or leased_count == len(request_jobs) or leased_count % 5 == 0:
                progress.emit(
                    "hub_lease_received",
                    leased=leased_count,
                    total=len(request_jobs),
                    worker_node_id=worker.node_id,
                    request_id=request_id,
                )

        if not batch:
            idle_rounds += 1
            if idle_rounds == 1 or idle_rounds % 10 == 0:
                progress.emit(
                    "hub_lease_waiting",
                    completed=len(completed),
                    pending=len(pending),
                    polled_workers=len(polled_workers_this_round),
                )
            await asyncio.sleep(0.2)
            continue

        idle_rounds = 0
        if verify_active_payout_rejection and not surprise_payout_rejection and batch:
            surprise_payout_rejection = _verify_surprise_payout_rejected_during_active_work(
                config,
                worker=batch[0][0].worker,
                progress=progress,
            )

        unique_batch_nodes: dict[str, Any] = {}
        for match, _request in batch:
            unique_batch_nodes[match.worker.node_id] = match.worker
        batch_nodes = sorted(unique_batch_nodes.values(), key=lambda node: node.node_id)
        execution_results = await _execute_requests(
            config=node_config,
            nodes=batch_nodes,
            requests=batch,
            event_log_path=event_log_path,
            progress=progress,
        )
        completed.extend(_submit_results_and_verify(config, execution_results, batch_leases, progress=progress))

    return leases, completed, surprise_payout_rejection


def _response_content(token_count: int) -> str:
    return "".join(fake_token_text(seq) for seq in range(1, token_count + 1))


def _submit_results_and_verify(
    config: HubNodeMarketSmokeConfig,
    execution_results: list[dict[str, Any]],
    leases: dict[str, dict[str, Any]],
    *,
    progress: _ProgressReporter,
) -> list[dict[str, Any]]:
    completed: list[dict[str, Any]] = []
    for index, result in enumerate(sorted(execution_results, key=lambda item: str(item.get("request_id", ""))), start=1):
        request_id = str(result["request_id"])
        lease = leases[request_id]
        workflow_result = result.get("workflow_result", {}) if isinstance(result.get("workflow_result"), dict) else {}
        token_count = int(workflow_result.get("token_count", config.token_count) or config.token_count)
        response = {
            "status": "success",
            "response": {
                "content": _response_content(token_count),
                "provider": "temporal-lab-fake-token",
                "model": config.model,
                "metadata": {
                    "temporal_lab": True,
                    "token_count": token_count,
                    "workflow_result": workflow_result,
                    "requester_visible_token_events": token_count,
                },
            },
        }
        completion = _post_json(
            config.hub_url,
            "/api/hub/v1/workers/results",
            {
                "worker_node_id": str(result["worker_node_id"]),
                "request_id": request_id,
                "lease_id": lease["lease_id"],
                "result": response,
            },
            timeout=config.http_timeout_seconds,
        )["request"]
        if completion.get("state") != "completed":
            raise NodeMarketSmokeError(f"Hub did not complete {request_id}: {completion}")
        replay = _post_json(
            config.hub_url,
            "/api/hub/v1/workers/results",
            {
                "worker_node_id": str(result["worker_node_id"]),
                "request_id": request_id,
                "lease_id": lease["lease_id"],
                "result": response,
            },
            timeout=config.http_timeout_seconds,
        )
        if replay.get("duplicate_completion_additional_charge") != 0:
            raise NodeMarketSmokeError(f"Duplicate completion replay charged again for {request_id}: {replay}")
        charges = _get_json(config.hub_url, f"/api/hub/v1/requests/{request_id}/charges", timeout=config.http_timeout_seconds)
        if int(charges.get("charge_count", 0) or 0) != 1:
            raise NodeMarketSmokeError(f"Expected one charge for {request_id}, got: {charges}")
        earnings_query = urlencode({"worker_node_id": str(result["worker_node_id"]), "request_id": request_id})
        earnings = _get_json(config.hub_url, f"/api/hub/v1/credits/worker-earnings?{earnings_query}", timeout=config.http_timeout_seconds)
        if int(earnings.get("worker_earning_count", 0) or 0) < 1:
            raise NodeMarketSmokeError(f"Expected worker earning for {request_id}, got: {earnings}")
        events = _get_json(config.hub_url, f"/api/hub/v1/requests/{request_id}/events", timeout=config.http_timeout_seconds)
        event_types = [str(event.get("event_type") or event.get("type") or "") for event in events.get("events", []) if isinstance(event, dict)]
        if not any("completed" in event_type for event_type in event_types):
            raise NodeMarketSmokeError(f"Hub request events did not expose completion for {request_id}: {events}")
        completed.append(completion)
        if index == 1 or index == len(execution_results) or index % 5 == 0:
            progress.emit("hub_result_completed", completed=index, total=len(execution_results), request_id=request_id)
    return completed


async def run_temporal_fdb_hub_node_market_smoke(config: HubNodeMarketSmokeConfig) -> dict[str, Any]:
    progress = _ProgressReporter(
        enabled=config.emit_progress,
        interval_seconds=config.progress_interval_seconds,
    )
    run_id = config.run_id or f"{time.time_ns():x}"[-10:]
    object.__setattr__(config, "run_id", run_id)
    event_log_path = config.resolved_event_log_path()
    event_log_path.parent.mkdir(parents=True, exist_ok=True)
    event_log_path.write_text("", encoding="utf-8")

    progress.emit(
        "start",
        hub_url=config.hub_url,
        execution_mode=config.execution_mode,
        nodes=config.node_count,
        requests=config.request_count,
        requested_ring=config.requested_ring,
        max_price_credits=config.max_price_credits,
    )
    started_hub = _ensure_hub_running(config, progress=progress)
    try:
        _verify_backends(config, progress=progress)

        bridge_funding = _bridge_fund_requester(config, progress=progress)

        nodes = build_worker_nodes(node_count=config.node_count, run_id=run_id, task_queue_prefix=config.task_queue_prefix)
        _register_workers(config, nodes, progress=progress)

        stop_event = asyncio.Event()
        heartbeat_tasks = [asyncio.create_task(_heartbeat_loop(config=config, node=node, stop_event=stop_event)) for node in nodes]
        try:
            request_jobs, submitted = _quote_and_submit_requests(
                config,
                {node.node_id: node for node in nodes},
                progress=progress,
            )
            node_config = NodeMarketSmokeConfig(
                repo_root=config.repo_root,
                execution_mode=config.execution_mode,  # type: ignore[arg-type]
                ledger_backend="foundationdb",
                report_path=None,
                event_log_path=event_log_path,
                temporal_address=config.temporal_address,
                namespace=config.namespace,
                node_count=config.node_count,
                request_count=config.request_count,
                requested_ring=config.requested_ring,
                max_price_credits=config.max_price_credits,
                deposit_credits=config.deposit_credits,
                token_count=config.token_count,
                token_interval_seconds=config.token_interval_seconds,
                task_queue_prefix=config.task_queue_prefix,
                run_id=run_id,
                emit_progress=config.emit_progress,
                progress_interval_seconds=config.progress_interval_seconds,
            )
            leases, completed, surprise_payout_rejection = await _execute_and_settle_worker_pull_requests(
                config,
                node_config=node_config,
                nodes_by_id={node.node_id: node for node in nodes},
                request_jobs=request_jobs,
                event_log_path=event_log_path,
                progress=progress,
                verify_active_payout_rejection=bool(bridge_funding.get("bridge_available", True)),
            )
        finally:
            stop_event.set()
            await asyncio.gather(*heartbeat_tasks, return_exceptions=True)

        selected_worker_ids_pre_report = sorted({str(record.get("selected_worker_node_id", "")) for record in completed})
        if bridge_funding.get("bridge_available", True):
            worker_payout = _exercise_worker_payout(
                config,
                nodes_by_id={node.node_id: node for node in nodes},
                selected_worker_ids=selected_worker_ids_pre_report,
                progress=progress,
            )
            bridge_audit_readback = _verify_bridge_audit_readback(
                config,
                bridge_funding=bridge_funding,
                worker_payout=worker_payout,
                progress=progress,
            )
        else:
            worker_payout = {"wallet_address": "", "worker_node_id": "", "payout": {}, "bridge_available": False}
            bridge_audit_readback = {"ok": True, "bridge_available": False}

        credit_status = _get_json(config.hub_url, "/api/hub/v1/credits", timeout=config.http_timeout_seconds)
        mock_chain_status = credit_status.get("mock_chain", {}) if isinstance(credit_status.get("mock_chain"), dict) else {}
        bridge_reconciliation_ok = (
            int(mock_chain_status.get("active_wallet_lock_count", 0) or 0) == 0
            and int(mock_chain_status.get("pending_payout_credit_wei", 0) or 0) == 0
            and int(mock_chain_status.get("pending_deposit_credit_wei", 0) or 0) == 0
        )
        if mock_chain_status and not bridge_reconciliation_ok:
            raise NodeMarketSmokeError(f"Mock chain bridge did not reconcile to a quiet final state: {mock_chain_status}")
        token_events = [event for event in read_jsonl_events(event_log_path) if event.get("event") == "token"]
        expected_spend = config.request_count * config.max_price_credits
        final_spent = int(credit_status.get("totals", {}).get("spent_credits", 0) or 0)
        if final_spent < expected_spend:
            raise NodeMarketSmokeError(f"Final spent credits {final_spent} is below expected smoke spend {expected_spend}.")
        if len(token_events) != config.request_count * config.token_count:
            raise NodeMarketSmokeError(
                f"Expected {config.request_count * config.token_count} fake token events, got {len(token_events)}."
            )

        selected_worker_ids = sorted({str(record.get("selected_worker_node_id", "")) for record in completed})
        selected_nodes = [node for node in nodes if node.node_id in selected_worker_ids]
        eligible_worker_count = sum(
            1 for node in nodes if node.ring <= config.requested_ring and node.price_credits <= config.max_price_credits
        )
        if config.request_count > 1 and eligible_worker_count > 1 and len(selected_worker_ids) < 2:
            raise NodeMarketSmokeError(
                "Hub selected only one worker even though multiple eligible workers were registered. "
                f"selected_worker_ids={selected_worker_ids} eligible_worker_count={eligible_worker_count}"
            )
        report = {
            "ok": True,
            "run_id": run_id,
            "hub_url": config.hub_url,
            "execution_mode": config.execution_mode,
            "registry_backend": _get_json(config.hub_url, "/api/hub/v1/status", timeout=config.http_timeout_seconds).get("backend"),
            "credit_ledger_backend": credit_status.get("backend"),
            "nodes_registered": len(nodes),
            "requests_submitted": len(submitted),
            "requests_completed": len(completed),
            "requested_ring": config.requested_ring,
            "max_price_credits": config.max_price_credits,
            "selected_worker_count": len(selected_worker_ids),
            "selected_worker_ids": selected_worker_ids,
            "eligible_worker_count": eligible_worker_count,
            "selected_worker_rings": sorted({node.ring for node in selected_nodes}),
            "selected_worker_prices": sorted({node.price_credits for node in selected_nodes}),
            "token_events": len(token_events),
            "expected_spend_credits": expected_spend,
            "final_spent_credits_total": final_spent,
            "active_hold_count_total": int(credit_status.get("active_hold_count", 0) or 0),
            "charge_count_total": int(credit_status.get("charge_count", 0) or 0),
            "worker_earning_count_total": int(credit_status.get("worker_earning_count", 0) or 0),
            "requester_wallet_address": bridge_funding["wallet_address"],
            "bridge_deposit_id": str(bridge_funding.get("deposit", {}).get("deposit_id", "")),
            "worker_payout_id": str(worker_payout.get("payout", {}).get("payout_id", "")),
            "worker_payout_wallet_address": str(worker_payout.get("wallet_address", "")),
            "worker_payout_node_id": str(worker_payout.get("worker_node_id", "")),
            "surprise_payout_rejected_active_work": bool(surprise_payout_rejection.get("rejected")),
            "surprise_payout_rejection_worker_node_id": str(surprise_payout_rejection.get("worker_node_id", "")),
            "surprise_payout_rejection_wallet_address": str(surprise_payout_rejection.get("wallet_address", "")),
            "payout_lock_verified": bool(worker_payout.get("lock_verified")),
            "locked_wallet_excluded_from_new_work": bool(worker_payout.get("locked_wallet_excluded_from_new_work")),
            "bridge_audit_event_types": sorted(set(worker_payout.get("audit_event_types", []))),
            "failed_worker_payout_id": str(worker_payout.get("failed_recovery", {}).get("payout", {}).get("payout_id", "")),
            "payout_failure_recovered": bool(worker_payout.get("failed_recovery", {}).get("wallet_lock_released"))
            and bool(worker_payout.get("failed_recovery", {}).get("chain_balance_unchanged")),
            "failed_payout_chain_balance_unchanged": bool(worker_payout.get("failed_recovery", {}).get("chain_balance_unchanged")),
            "failed_payout_wallet_unlocked": bool(worker_payout.get("failed_recovery", {}).get("wallet_lock_released")),
            "bridge_audit_readback_ok": bool(bridge_audit_readback.get("ok")),
            "bridge_audit_readback": bridge_audit_readback,
            "bridge_reconciliation_ok": bridge_reconciliation_ok,
            "bridge_audit_event_count": int(mock_chain_status.get("audit_event_count", 0) or 0),
            "mock_chain": mock_chain_status,
            "event_log_path": str(event_log_path),
            "hub_started_by_smoke": started_hub is not None,
            "hub_namespace": _auto_hub_namespace(config) if started_hub is not None else "",
        }
        report_path = config.resolved_report_path()
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            progress.emit("report_write_ok", report_path=report_path)
        progress.emit("done", ok=True)
        return report
    finally:
        if started_hub is not None:
            progress.emit("hub_autostart_stop", hub_url=config.hub_url)
            started_hub.stop()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test Hub-backed Temporal/FDB worker/requester node-market flow.")
    parser.add_argument("--repo-root", default=".", help="Repository root used to resolve runtime paths.")
    parser.add_argument("--hub-url", default=DEFAULT_HUB_URL, help="Base URL of a running exp-fdb-hub.py instance.")
    parser.add_argument("--execution-mode", choices=["live-temporal", "direct-activity"], default="live-temporal")
    parser.add_argument("--temporal-address", default="localhost:7233")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--node-count", type=int, default=50)
    parser.add_argument("--request-count", type=int, default=20)
    parser.add_argument("--requested-ring", type=int, default=2)
    parser.add_argument("--max-price-credits", type=int, default=2)
    parser.add_argument("--deposit-credits", type=int, default=100)
    parser.add_argument("--token-count", type=int, default=5)
    parser.add_argument("--token-interval-seconds", type=float, default=0.02)
    parser.add_argument("--account-id", default="temporal-fdb-hub-node-market-client")
    parser.add_argument("--requester-wallet-address", default="0x0000000000000000000000000000000000000aa1")
    parser.add_argument("--model", default="temporal-fdb-hub-node-market-model")
    parser.add_argument("--task-queue-prefix", default=NODE_MARKET_TASK_QUEUE_PREFIX + "-hub")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--report-path", default=str(DEFAULT_HUB_NODE_MARKET_REPORT_PATH))
    parser.add_argument("--event-log-path", default=str(DEFAULT_EVENT_LOG_PATH))
    parser.add_argument("--allow-json-hub", action="store_true", help="Do not require exp-fdb-hub FoundationDB backends.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress events.")
    parser.add_argument("--progress-interval-seconds", type=float, default=2.0)
    parser.add_argument("--http-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--http-retry-attempts", type=int, default=DEFAULT_HTTP_RETRY_ATTEMPTS)
    parser.add_argument("--keepalive-interval-seconds", type=float, default=2.0)
    parser.add_argument(
        "--hub-start-mode",
        choices=["auto", "never"],
        default="auto",
        help="auto starts exp-fdb-hub.py when --hub-url is local and not listening; never only prints the manual start command.",
    )
    parser.add_argument("--hub-start-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--hub-namespace-prefix", default=DEFAULT_AUTO_HUB_NAMESPACE_PREFIX)
    parser.add_argument("--hub-root", default=str(DEFAULT_AUTO_HUB_ROOT))
    parser.add_argument("--cluster-file", default=str(DEFAULT_AUTO_HUB_CLUSTER_FILE))
    return parser


def _config_from_args(args: argparse.Namespace) -> HubNodeMarketSmokeConfig:
    repo_root = Path(args.repo_root).resolve()
    report_path = None if str(args.report_path).strip().lower() in {"", "none", "null"} else Path(args.report_path)
    return HubNodeMarketSmokeConfig(
        repo_root=repo_root,
        hub_url=str(args.hub_url).rstrip("/"),
        execution_mode=str(args.execution_mode),
        temporal_address=str(args.temporal_address),
        namespace=str(args.namespace),
        report_path=report_path,
        event_log_path=Path(args.event_log_path),
        node_count=_positive_int(args.node_count, field_name="node_count"),
        request_count=_positive_int(args.request_count, field_name="request_count"),
        requested_ring=int(args.requested_ring),
        max_price_credits=_positive_int(args.max_price_credits, field_name="max_price_credits"),
        deposit_credits=_positive_int(args.deposit_credits, field_name="deposit_credits"),
        token_count=_positive_int(args.token_count, field_name="token_count"),
        token_interval_seconds=_positive_float(args.token_interval_seconds, field_name="token_interval_seconds", minimum=0.0),
        account_id=str(args.account_id),
        requester_wallet_address=str(args.requester_wallet_address),
        model=str(args.model),
        task_queue_prefix=str(args.task_queue_prefix),
        run_id=str(args.run_id or "") or None,
        require_foundationdb_backends=not bool(args.allow_json_hub),
        emit_progress=not bool(args.quiet),
        progress_interval_seconds=_positive_float(args.progress_interval_seconds, field_name="progress_interval_seconds", minimum=0.1),
        http_timeout_seconds=_positive_float(args.http_timeout_seconds, field_name="http_timeout_seconds", minimum=1.0),
        http_retry_attempts=_positive_int(args.http_retry_attempts, field_name="http_retry_attempts"),
        keepalive_interval_seconds=_positive_float(args.keepalive_interval_seconds, field_name="keepalive_interval_seconds", minimum=0.2),
        hub_start_mode=str(args.hub_start_mode),
        hub_start_timeout_seconds=_positive_float(args.hub_start_timeout_seconds, field_name="hub_start_timeout_seconds", minimum=1.0),
        hub_namespace_prefix=str(args.hub_namespace_prefix),
        hub_root=Path(args.hub_root),
        cluster_file=Path(args.cluster_file),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = _config_from_args(args)
    try:
        report = asyncio.run(run_temporal_fdb_hub_node_market_smoke(config))
    except Exception as exc:
        print(f"FAIL: Temporal FDB Hub node-market smoke failed: {exc}", file=sys.stderr)
        return 1
    print("PASS: Temporal FDB Hub node-market smoke succeeded")
    print(f"hub_url: {report['hub_url']}")
    print(f"execution_mode: {report['execution_mode']}")
    print(f"registry_backend: {report['registry_backend']}")
    print(f"credit_ledger_backend: {report['credit_ledger_backend']}")
    print(f"hub_started_by_smoke: {report.get('hub_started_by_smoke', False)}")
    if report.get("hub_namespace"):
        print(f"hub_namespace: {report['hub_namespace']}")
    print(f"nodes_registered: {report['nodes_registered']}")
    print(f"requests_completed: {report['requests_completed']}")
    print(f"selected_worker_count: {report.get('selected_worker_count', 0)}")
    print(f"selected_worker_ids: {report.get('selected_worker_ids', [])}")
    print(f"token_events: {report['token_events']}")
    print(f"expected_spend_credits: {report['expected_spend_credits']}")
    print(f"bridge_deposit_id: {report.get('bridge_deposit_id', '')}")
    print(f"worker_payout_id: {report.get('worker_payout_id', '')}")
    print(f"worker_payout_wallet_address: {report.get('worker_payout_wallet_address', '')}")
    print(f"surprise_payout_rejected_active_work: {report.get('surprise_payout_rejected_active_work', False)}")
    print(f"payout_lock_verified: {report.get('payout_lock_verified', False)}")
    print(f"locked_wallet_excluded_from_new_work: {report.get('locked_wallet_excluded_from_new_work', False)}")
    print(f"payout_failure_recovered: {report.get('payout_failure_recovered', False)}")
    print(f"bridge_audit_readback_ok: {report.get('bridge_audit_readback_ok', False)}")
    print(f"bridge_reconciliation_ok: {report.get('bridge_reconciliation_ok', False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
