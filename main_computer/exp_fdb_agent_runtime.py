from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


DEFAULT_HEARTBEAT_SECONDS = 5.0
DEFAULT_HUB_HEALTH_PATH = "/api/hub/v1/health"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def append_event(run_dir: Path, event: str, **fields: Any) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": utc_now(),
        "event": event,
        **fields,
    }
    with (run_dir / "agent-events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(record) + "\n")


def load_run_record(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "agent-run.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def hub_health_url(hub_url: str, health_path: str = DEFAULT_HUB_HEALTH_PATH) -> str:
    base = str(hub_url or "").strip().rstrip("/")
    path = str(health_path or DEFAULT_HUB_HEALTH_PATH).strip() or DEFAULT_HUB_HEALTH_PATH
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def probe_hub(hub_url: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
    if not str(hub_url or "").strip():
        return {"ok": False, "error": "missing hub url"}
    url = hub_health_url(hub_url)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            raw = response.read(64 * 1024)
            status = int(getattr(response, "status", 0) or 0)
            content_type = str(response.headers.get("content-type", ""))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "url": url, "status": int(exc.code), "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}
    duration_ms = int((time.monotonic() - started) * 1000)
    payload: Any
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = raw.decode("utf-8", errors="replace")[:1000]
    return {
        "ok": 200 <= status < 300,
        "url": url,
        "status": status,
        "duration_ms": duration_ms,
        "content_type": content_type,
        "payload": payload,
    }


class AgentRuntime:
    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        hub_url: str,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        health_timeout_seconds: float = 3.0,
        child_command: Sequence[str] = (),
    ) -> None:
        self.run_id = str(run_id or "").strip()
        self.run_dir = run_dir
        self.hub_url = str(hub_url or "").strip().rstrip("/")
        self.heartbeat_seconds = max(1.0, float(heartbeat_seconds))
        self.health_timeout_seconds = max(0.1, float(health_timeout_seconds))
        self.child_command = [str(part) for part in child_command]
        self.stop_requested = False
        self.child: subprocess.Popen[Any] | None = None

    @property
    def state_path(self) -> Path:
        return self.run_dir / "agent-state.json"

    def install_signal_handlers(self) -> None:
        def _handle(signum: int, _frame: object) -> None:
            self.stop_requested = True
            append_event(self.run_dir, "agent.signal", run_id=self.run_id, signum=signum)
            self.stop_child()

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)

    def write_state(self, status: str, *, hub_health: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> None:
        record = load_run_record(self.run_dir)
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "status": status,
            "updated_at": utc_now(),
            "hub_url": self.hub_url,
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "child_pid": self.child.pid if self.child and self.child.poll() is None else None,
            "container_runtime": {
                "role": "exp-fdb-agent",
                "container_name": os.environ.get("MAIN_COMPUTER_AGENT_CONTAINER_NAME", ""),
                "image": os.environ.get("MAIN_COMPUTER_AGENT_IMAGE", ""),
            },
        }
        if record:
            payload["run_record"] = record
        if hub_health is not None:
            payload["hub_health"] = hub_health
        if extra:
            payload.update(extra)
        atomic_write_json(self.state_path, payload)

    def start_child(self) -> None:
        if not self.child_command:
            return
        append_event(self.run_dir, "agent.child.starting", run_id=self.run_id, command=self.child_command)
        self.child = subprocess.Popen(self.child_command)
        append_event(self.run_dir, "agent.child.started", run_id=self.run_id, pid=self.child.pid)

    def stop_child(self) -> None:
        child = self.child
        if child is None or child.poll() is not None:
            return
        append_event(self.run_dir, "agent.child.terminating", run_id=self.run_id, pid=child.pid)
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            append_event(self.run_dir, "agent.child.killing", run_id=self.run_id, pid=child.pid)
            child.kill()
            child.wait(timeout=10)

    def run(self) -> int:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.install_signal_handlers()
        append_event(self.run_dir, "agent.boot", run_id=self.run_id, hub_url=self.hub_url, child_command=self.child_command)
        self.write_state("booting")
        hub_health = probe_hub(self.hub_url, timeout_seconds=self.health_timeout_seconds)
        append_event(self.run_dir, "agent.hub_probe", run_id=self.run_id, hub_health=hub_health)
        self.start_child()
        self.write_state("running", hub_health=hub_health)

        exit_code = 0
        try:
            while not self.stop_requested:
                child = self.child
                if child is not None:
                    return_code = child.poll()
                    if return_code is not None:
                        exit_code = int(return_code)
                        append_event(self.run_dir, "agent.child.exited", run_id=self.run_id, returncode=exit_code)
                        break
                hub_health = probe_hub(self.hub_url, timeout_seconds=self.health_timeout_seconds)
                self.write_state("running", hub_health=hub_health)
                time.sleep(self.heartbeat_seconds)
        finally:
            if self.stop_requested:
                self.write_state("stopping")
            self.stop_child()
            final_status = "stopped" if self.stop_requested else ("completed" if exit_code == 0 else "failed")
            self.write_state(final_status, extra={"exit_code": exit_code})
            append_event(self.run_dir, "agent.shutdown", run_id=self.run_id, status=final_status, exit_code=exit_code)
        return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m main_computer.exp_fdb_agent_runtime",
        description="Run the small lifecycle process that inhabits one exp-FDB agent container.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    run = subparsers.add_parser("run", help="Run until SIGTERM/SIGINT or until the optional child command exits.")
    run.add_argument("--run-id", default=os.environ.get("AGENT_RUN_ID") or os.environ.get("MAIN_COMPUTER_AGENT_RUN_ID") or "")
    run.add_argument("--run-dir", type=Path, default=Path(os.environ.get("MAIN_COMPUTER_AGENT_RUN_DIR", "/agent-run")))
    run.add_argument("--hub-url", default=os.environ.get("HUB_BASE_URL") or os.environ.get("MAIN_COMPUTER_EXP_FDB_HUB_URL") or "")
    run.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    run.add_argument("--health-timeout-seconds", type=float, default=3.0)
    run.add_argument("child_command", nargs=argparse.REMAINDER, help="Optional command to supervise after --.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "run":
        run_id = str(args.run_id or "").strip()
        if not run_id:
            raise SystemExit("--run-id or AGENT_RUN_ID is required")
        child_command = list(args.child_command or [])
        if child_command and child_command[0] == "--":
            child_command = child_command[1:]
        runtime = AgentRuntime(
            run_id=run_id,
            run_dir=Path(args.run_dir),
            hub_url=str(args.hub_url or ""),
            heartbeat_seconds=float(args.heartbeat_seconds),
            health_timeout_seconds=float(args.health_timeout_seconds),
            child_command=child_command,
        )
        return runtime.run()
    raise SystemExit(f"unsupported action: {args.action}")


if __name__ == "__main__":
    raise SystemExit(main())
