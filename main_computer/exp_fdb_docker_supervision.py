"""Bounded, detached orchestration and durable evidence for Experimental Docker lab.

Lab-only. Never drives Hub accounting or acts as a settlement authority.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunJournal:
    """One journal per run ID; refuse to replay an ambiguous payout attempt."""

    def __init__(self, path: Path, *, run_id: str, live_chain: bool):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise RuntimeError(
                f"Run {run_id} already has an execution journal at {self.path}. "
                "Do not replay an existing run or uncertain chain payout. "
                "Inspect/reconcile it before beginning any new test."
            )
        self.document: dict[str, Any] = {
            "version": 1, "run_id": run_id, "live_chain": bool(live_chain),
            "phase": "initialized", "payout_outcome": "not_started",
            "events": [],
        }
        self.advance("initialized")

    def advance(self, phase: str, *, payout_outcome: str | None = None, **details: Any) -> None:
        self.document["phase"] = phase
        if payout_outcome is not None:
            self.document["payout_outcome"] = payout_outcome
        event = {"at": _stamp(), "phase": phase, **details}
        self.document["events"].append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # fsync and atomic replace: do not begin external payment until this
        # durable unknown-outcome marker has been recorded.
        fd, filename = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(self.document, file, sort_keys=True, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(filename, self.path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
        print(f"Experimental lab stage: {phase} (journal={self.path})", flush=True)


class DetachedComposeWorker:
    """Start Compose detached, wait for the *container* exit, clean up separately.

    This is for live-session lab only. Legacy polling-mode Compose remains as-is.
    """

    def __init__(
        self, start_process: subprocess.Popen, *, compose_base: Sequence[str],
        container_base: Sequence[str], repo_root: Path, environment: dict[str, str],
        startup_timeout: float, execution_timeout: float, cleanup_timeout: float = 25.0,
    ):
        self.start_process = start_process
        self.compose_base = list(compose_base)
        self.container_base = list(container_base)
        self.repo_root = Path(repo_root)
        self.environment = environment
        self.startup_timeout = float(startup_timeout)
        self.execution_timeout = float(execution_timeout)
        self.cleanup_timeout = float(cleanup_timeout)
        self.exit_code: int | None = None
        self.container_id = ""
        self.cleaned = False
        self.cleanup_ok = True

    def _run(self, command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=str(self.repo_root), env=self.environment,
                              capture_output=True, text=True, timeout=timeout, check=False)

    def _logs(self) -> None:
        if not self.container_id:
            return
        try:
            result = self._run([*self.container_base, "logs", self.container_id], timeout=20)
            if result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
            if result.stderr:
                print(result.stderr, file=sys.stderr, flush=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"Experimental Docker logs unavailable: {exc}", file=sys.stderr, flush=True)

    def _cleanup(self) -> None:
        if self.cleaned:
            return
        self.cleaned = True
        try:
            result = self._run([*self.compose_base, "down", "--remove-orphans"], timeout=self.cleanup_timeout)
            if result.returncode:
                self.cleanup_ok = False
                print(f"Experimental Docker cleanup failed: {result.stderr.strip()}", file=sys.stderr, flush=True)
            else:
                print("Experimental Docker cleanup: complete", flush=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.cleanup_ok = False
            print(f"Experimental Docker cleanup timed out/failed: {exc}", file=sys.stderr, flush=True)

    def wait(self, timeout: float | None = None) -> int:
        if self.exit_code is not None:
            return self.exit_code
        try:
            start_deadline = self.startup_timeout if timeout is None else min(self.startup_timeout, timeout)
            startup_code = self.start_process.wait(timeout=start_deadline)
            if startup_code:
                raise RuntimeError(f"detached Docker Compose startup exited {startup_code}")
            located = self._run([*self.compose_base, "ps", "--all", "--quiet", "worker-lab"], timeout=20)
            ids = [line.strip() for line in located.stdout.splitlines() if line.strip()]
            if located.returncode or len(ids) != 1:
                raise RuntimeError(f"expected one worker-lab container, got {ids}: {located.stderr.strip()}")
            self.container_id = ids[0]
            print(f"Experimental Docker worker container: {self.container_id}; waiting up to {self.execution_timeout:g}s", flush=True)
            finished = self._run([*self.container_base, "wait", self.container_id], timeout=self.execution_timeout)
            if finished.returncode:
                raise RuntimeError(f"docker wait failed: {finished.stderr.strip()}")
            raw_codes = [part.strip() for part in finished.stdout.splitlines() if part.strip()]
            if len(raw_codes) != 1:
                raise RuntimeError(f"docker wait returned invalid exit status: {finished.stdout!r}")
            self.exit_code = int(raw_codes[0])
            print(f"Experimental Docker worker container exited: {self.exit_code}", flush=True)
        except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError) as exc:
            self.exit_code = 1
            print(f"Experimental Docker worker: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        finally:
            self._logs()
            self._cleanup()
            if not self.cleanup_ok:
                self.exit_code = 1
        return self.exit_code

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        if self.start_process.poll() is None:
            self.start_process.terminate()
        self._cleanup()

    def kill(self) -> None:
        if self.start_process.poll() is None:
            self.start_process.kill()
        self._cleanup()


def verify_checkpoint_bounded(*, repo_root: Path, cluster_file: Path, session_id: str,
                              checkpoint: Path, temporal_address: str, timeout: float) -> dict[str, Any]:
    command = [sys.executable, "-m", "tools.exp_hub_lab.run_lab", "--verify-exp-temporal",
               "--cluster-file", str(cluster_file), "--temporal-session-id", session_id,
               "--temporal-address", temporal_address, "--temporal-checkpoint", str(checkpoint), "--json"]
    result = subprocess.run(command, cwd=str(repo_root), capture_output=True, text=True,
                            timeout=timeout, check=False)
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"Temporal verification emitted non-JSON output: {result.stdout[-400:]} {result.stderr[-400:]}") from exc
    if result.returncode or payload.get("ok") is not True:
        raise RuntimeError(str(payload.get("error") or result.stderr or "Temporal verification failed"))
    return payload


def verify_live_chain_bounded(*, repo_root: Path, hub_urls: Sequence[str], dev_chain_state: Path,
                              rpc_url: str, chain_id: int, timeout: float, report_path: Path) -> dict[str, Any]:
    command = [sys.executable, "-m", "tools.exp_hub_lab.p2b_live_verifier",
               "--hub-urls", ",".join(hub_urls), "--dev-chain-state", str(dev_chain_state),
               "--rpc-url", rpc_url, "--chain-id", str(chain_id), "--stress-requests", "3",
               "--timeout", str(min(30.0, timeout)), "--report-path", str(report_path), "--json"]
    # Timeout does not imply failed chain transaction. Caller must retain
    # payout_outcome=unknown until chain reconciliation has been performed.
    result = subprocess.run(command, cwd=str(repo_root), capture_output=True, text=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"real-chain verifier exited {result.returncode}; output={result.stdout[-600:]} stderr={result.stderr[-600:]}")
    if not report_path.is_file():
        raise RuntimeError("real-chain verifier returned successfully without durable settlement report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("ok") is not True:
        raise RuntimeError(str(report.get("error") or "real-chain proof failed"))
    return report
