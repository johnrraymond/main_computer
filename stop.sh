#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: ./stop.sh [--no-docker] [--with-docker] [--quiet]

Stop Main Computer app processes from this source tree on Linux.

This Linux stopper asks the Python service supervisor to shut down, then uses
root-owned PID/state files as a fallback. Docker stacks are left alone;
--with-docker is accepted for command compatibility but is not implemented yet.
USAGE
}

MC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MC_WITH_DOCKER=0
MC_QUIET=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-docker|/no-docker)
      MC_WITH_DOCKER=0
      shift
      ;;
    --with-docker|--docker|/with-docker)
      MC_WITH_DOCKER=1
      shift
      ;;
    --quiet|-q)
      MC_QUIET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

resolve_python() {
  if [ -n "${MAIN_COMPUTER_PYTHON_COMMAND:-}" ]; then
    printf '%s\n' "$MAIN_COMPUTER_PYTHON_COMMAND"
    return 0
  fi

  local candidate
  for candidate in \
    "$MC_ROOT/.venv/bin/python" \
    "$(dirname "$MC_ROOT")/.venv/bin/python" \
    python3 \
    python
  do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  echo "Could not find Python. Set MAIN_COMPUTER_PYTHON_COMMAND or install python3." >&2
  return 1
}

MC_PYTHON="$(resolve_python)"
export MAIN_COMPUTER_PYTHON_COMMAND="$MC_PYTHON"

if [ "$MC_WITH_DOCKER" = "1" ] && [ "$MC_QUIET" != "1" ]; then
  echo "Note: Linux Docker-stack teardown is not implemented in stop.sh yet; Docker stacks are left alone."
fi

"$MC_PYTHON" - "$MC_ROOT" "$MC_WITH_DOCKER" "$MC_QUIET" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

root = Path(sys.argv[1]).resolve()
with_docker = sys.argv[2] == "1"
quiet = sys.argv[3] == "1"
sys.path.insert(0, str(root))


def say(message: str) -> None:
    if not quiet:
        print(message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            value = payload.get("pid")
        else:
            value = payload
        pid = int(value)
        return pid if pid > 0 else None
    except Exception:
        pass
    try:
        pid = int(raw.splitlines()[0].strip())
        return pid if pid > 0 else None
    except Exception:
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def command_line(pid: int) -> str:
    proc_path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = proc_path.read_bytes()
        if raw:
            return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return ""
    return (result.stdout or "").strip()


def owned_main_computer(pid: int) -> bool:
    cmd = command_line(pid)
    if not cmd:
        return False
    normalized = " ".join(cmd.split()).casefold()
    root_text = str(root).casefold()
    has_marker = (
        "main_computer" in normalized
        or "main-computer" in normalized
        or ".main_computer_" in normalized
    )
    has_root = root_text in normalized or root_text.replace("\\", "/") in normalized
    return bool(has_marker and has_root)


def add_candidate(candidates: dict[int, dict[str, Any]], pid: int | None, role: str, source: str, order: int) -> None:
    if not pid or pid <= 0:
        return
    entry = candidates.setdefault(pid, {"pid": pid, "role": role, "sources": [], "order": order})
    entry["sources"].append(source)
    entry["order"] = min(int(entry.get("order", order)), order)


def queued_stop_request(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    action = str(payload.get("action") or "").strip().lower()
    source = str(payload.get("source") or "").strip().lower()
    return action in {"shutdown", "stop", "halt"} and source in {"stop.sh", "./stop.sh", "start.sh"}


def remove_queued_stop_requests() -> list[str]:
    queue_dir = root / "runtime" / "service_control" / "supervisor" / "queue"
    removed: list[str] = []
    if not queue_dir.exists():
        return removed
    for path in sorted(queue_dir.glob("*.json")):
        if not queued_stop_request(path):
            continue
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            pass
    return removed


def enqueue_shutdown() -> bool:
    try:
        from main_computer.service_control import enqueue_supervisor_action
    except Exception as exc:
        say(f"Could not import service control; falling back to PID cleanup: {exc}")
        return False
    try:
        result = enqueue_supervisor_action(
            root,
            action="shutdown",
            target="system",
            source="stop.sh",
            parameters={"with_docker_requested": with_docker},
        )
        if result.get("ok"):
            say(f"Queued supervisor shutdown request: {result.get('path')}")
            return True
        say(f"Supervisor shutdown request was not accepted: {result}")
    except Exception as exc:
        say(f"Could not queue supervisor shutdown request: {exc}")
    return False


def collect_candidates() -> dict[int, dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    state_path = root / "runtime" / "service_supervisor" / "state.json"
    state = read_json(state_path)
    if isinstance(state, dict):
        service = state.get("service") if isinstance(state.get("service"), dict) else {}
        add_candidate(candidates, int(service.get("pid") or 0), "supervisor", str(state_path) + " service.pid", 90)
        children = state.get("children") if isinstance(state.get("children"), dict) else {}
        for name, child in children.items():
            if isinstance(child, dict):
                add_candidate(candidates, int(child.get("pid") or 0), str(name), str(state_path) + f" children.{name}.pid", 20)

    pid_files = [
        (root / ".main_computer_viewport.pid", "app", 20),
        (root / ".main_computer_heartbeat.pid", "heartbeat", 20),
        (root / ".main_computer_executor_service.pid", "executor", 20),
        (root / ".main_computer_applications_service.pid", "applications", 20),
        (root / ".main_computer_service_supervisor.pid", "supervisor", 90),
    ]
    for path, role, order in pid_files:
        add_candidate(candidates, read_pid(path), role, str(path), order)
    return candidates


def live_owned_pids() -> list[int]:
    return [pid for pid in collect_candidates() if alive(pid) and owned_main_computer(pid)]


def wait_for_graceful_stop(seconds: float = 30.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = read_json(root / "runtime" / "service_supervisor" / "state.json")
        state_text = state.get("state") if isinstance(state, dict) else None
        if not live_owned_pids() or state_text == "stopped":
            return
        time.sleep(1.0)


def terminate_candidates(candidates: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for entry in sorted(candidates.values(), key=lambda item: (int(item["order"]), int(item["pid"]))):
        pid = int(entry["pid"])
        role = str(entry["role"])
        if not alive(pid):
            results.append({"pid": pid, "role": role, "state": "not-running", "sources": entry["sources"]})
            continue
        if not owned_main_computer(pid):
            results.append({"pid": pid, "role": role, "state": "skipped-not-owned", "sources": entry["sources"], "command_line": command_line(pid)})
            continue

        try:
            os.kill(pid, signal.SIGTERM)
            state = "terminated"
        except ProcessLookupError:
            results.append({"pid": pid, "role": role, "state": "not-running", "sources": entry["sources"]})
            continue
        except Exception as exc:
            results.append({"pid": pid, "role": role, "state": "terminate-failed", "sources": entry["sources"], "error": str(exc)})
            continue

        deadline = time.time() + 5.0
        while time.time() < deadline and alive(pid):
            time.sleep(0.25)

        if alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                state = "killed"
            except Exception as exc:
                results.append({"pid": pid, "role": role, "state": "kill-failed", "sources": entry["sources"], "error": str(exc)})
                continue

        results.append({"pid": pid, "role": role, "state": state, "sources": entry["sources"]})
    return results


def remove_stale_pid_files() -> list[str]:
    removed: list[str] = []
    for path in [
        root / ".main_computer_service_supervisor.pid",
        root / ".main_computer_viewport.pid",
        root / ".main_computer_heartbeat.pid",
        root / ".main_computer_executor_service.pid",
        root / ".main_computer_applications_service.pid",
    ]:
        pid = read_pid(path)
        if pid and alive(pid) and owned_main_computer(pid):
            continue
        try:
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return removed


runtime = root / "runtime" / "start_stop"
runtime.mkdir(parents=True, exist_ok=True)

say("Requesting Main Computer supervisor shutdown...")
had_live_processes_before_queue = bool(live_owned_pids())
queued = enqueue_shutdown() if had_live_processes_before_queue else False
if queued:
    wait_for_graceful_stop(30.0)

candidates = collect_candidates()
results = terminate_candidates(candidates)
removed_pid_files = remove_stale_pid_files()
removed_queue_files = remove_queued_stop_requests()

report = {
    "schema_version": 1,
    "action": "stop",
    "root": str(root),
    "with_docker_requested": with_docker,
    "docker_state": "left-alone",
    "queued_shutdown": queued,
    "had_live_processes_before_queue": had_live_processes_before_queue,
    "process_results": results,
    "removed_pid_files": removed_pid_files,
    "removed_queued_stop_requests": removed_queue_files,
    "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
report_path = runtime / f"stop-report-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.json"
report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if not quiet:
    print("")
    print("Main Computer Linux stop completed.")
    print(f"Stop report: {report_path}")
    if removed_queue_files:
        print(f"Removed {len(removed_queue_files)} stale queued stop request(s).")
    if with_docker:
        print("Docker infrastructure was left running; Linux Docker teardown is not implemented in this script yet.")
PY
