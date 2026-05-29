#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: ./start.sh [--open-browser] [--host HOST] [--port PORT] [--wait-s SECONDS]

Start Main Computer from this source tree on Linux.

This Linux launcher starts the Python app/control supervisor in the background
and writes logs under runtime/service_supervisor/. Docker stack startup remains
owned by the Python services and existing tooling.
USAGE
}

MC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MC_OPEN_BROWSER=0
MC_HOST="${MAIN_COMPUTER_BIND_HOST:-127.0.0.1}"
MC_PORT="${MAIN_COMPUTER_CONTROL_PORT:-8765}"
MC_WAIT_SECONDS="${MC_START_WAIT_SECONDS:-30}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --open-browser|-OpenBrowser|/OpenBrowser)
      MC_OPEN_BROWSER=1
      shift
      ;;
    --host)
      if [ "$#" -lt 2 ]; then
        echo "Missing value for --host" >&2
        exit 2
      fi
      MC_HOST="$2"
      shift 2
      ;;
    --port)
      if [ "$#" -lt 2 ]; then
        echo "Missing value for --port" >&2
        exit 2
      fi
      MC_PORT="$2"
      shift 2
      ;;
    --wait-s)
      if [ "$#" -lt 2 ]; then
        echo "Missing value for --wait-s" >&2
        exit 2
      fi
      MC_WAIT_SECONDS="$2"
      shift 2
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
export MAIN_COMPUTER_INSTALL_ROOT="${MAIN_COMPUTER_INSTALL_ROOT:-$MC_ROOT}"
export MAIN_COMPUTER_WORKSPACE="${MAIN_COMPUTER_WORKSPACE:-$MC_ROOT}"
export MAIN_COMPUTER_CONTROL_ROOT="${MAIN_COMPUTER_CONTROL_ROOT:-$MC_ROOT}"
export MAIN_COMPUTER_CONTROL_PORT="$MC_PORT"
export MAIN_COMPUTER_HEARTBEAT_PORT="${MAIN_COMPUTER_HEARTBEAT_PORT:-$((MC_PORT + 1))}"
export MAIN_COMPUTER_PATH_MODE="${MAIN_COMPUTER_PATH_MODE:-local}"
export MAIN_COMPUTER_HOST_OS="${MAIN_COMPUTER_HOST_OS:-linux}"
export MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT="${MAIN_COMPUTER_APPLICATIONS_COMPOSE_PROJECT:-main-computer-applications}"

# Do not let a shell-level Coolify/Compose project leak into the normal app stack.
unset MAIN_COMPUTER_COOLIFY_PROJECT
unset COOLIFY_COMPOSE_PROJECT
unset COMPOSE_PROJECT_NAME

mkdir -p "$MC_ROOT/runtime/service_supervisor" "$MC_ROOT/runtime/start_stop"

clear_stale_supervisor_shutdown_requests() {
  "$MC_PYTHON" - "$MC_ROOT" <<'PY' || true
from __future__ import annotations

import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
queue_dir = root / "runtime" / "service_control" / "supervisor" / "queue"
if not queue_dir.exists():
    raise SystemExit(0)

removed = 0
for path in sorted(queue_dir.glob("*.json")):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    action = str(payload.get("action") or "").strip().lower()
    source = str(payload.get("source") or "").strip().lower()
    if action in {"shutdown", "stop", "halt"} and source in {"stop.sh", "./stop.sh", "start.sh"}:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass

if removed:
    print(f"Cleared {removed} stale supervisor shutdown request(s).")
PY
}

if [ -f "$MC_ROOT/stop.sh" ]; then
  echo "Force-stopping current Main Computer app processes before launch; Docker stacks are left alone..."
  bash "$MC_ROOT/stop.sh" --no-docker --quiet || true
fi

# stop.sh can leave a shutdown request behind when no live supervisor was
# available to consume it. Clear those stale requests before the new supervisor
# starts, otherwise the fresh supervisor exits immediately with state=stopped.
clear_stale_supervisor_shutdown_requests

MC_STAMP="$(date -u +%Y%m%d-%H%M%S)"
MC_STDOUT="$MC_ROOT/runtime/service_supervisor/service_supervisor-$MC_STAMP.stdout.log"
MC_STDERR="$MC_ROOT/runtime/service_supervisor/service_supervisor-$MC_STAMP.stderr.log"

(
  cd "$MC_ROOT"
  exec "$MC_PYTHON" -m main_computer.app_control \
    --root "$MC_ROOT" \
    --host "$MC_HOST" \
    --port "$MC_PORT" \
    --python-command "$MC_PYTHON" \
    bootstrap
) >"$MC_STDOUT" 2>"$MC_STDERR" &

MC_LAUNCHER_PID=$!

"$MC_PYTHON" - "$MC_ROOT" "$MC_LAUNCHER_PID" "$MC_STDOUT" "$MC_STDERR" "$MC_PYTHON" "$MC_HOST" "$MC_PORT" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
pid = int(sys.argv[2])
stdout = Path(sys.argv[3]).resolve()
stderr = Path(sys.argv[4]).resolve()
python_command = sys.argv[5]
host = sys.argv[6]
port = sys.argv[7]

runtime = root / "runtime" / "start_stop"
runtime.mkdir(parents=True, exist_ok=True)

payload = {
    "schema_version": 1,
    "started_at": datetime.now(timezone.utc).isoformat(),
    "root": str(root),
    "started_by": "start.sh",
    "launcher": {
        "pid": pid,
        "stdout": str(stdout),
        "stderr": str(stderr),
        "command": [
            python_command,
            "-m",
            "main_computer.app_control",
            "--root",
            str(root),
            "--host",
            host,
            "--port",
            str(port),
            "--python-command",
            python_command,
            "bootstrap",
        ],
    },
    "managed_pid_files": [
        str(root / ".main_computer_service_supervisor.pid"),
        str(root / ".main_computer_viewport.pid"),
        str(root / ".main_computer_heartbeat.pid"),
        str(root / ".main_computer_executor_service.pid"),
        str(root / ".main_computer_applications_service.pid"),
    ],
    "docker_stacks": [],
}
(runtime / "start-session.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "Main Computer Linux supervisor launch requested, detached as PID $MC_LAUNCHER_PID."
echo "Python:           $MC_PYTHON"
echo "Root:             $MC_ROOT"
echo "Control URL:      http://127.0.0.1:$MC_PORT"
echo "Start session:    $MC_ROOT/runtime/start_stop/start-session.json"
echo "Supervisor state: $MC_ROOT/runtime/service_supervisor/state.json"
echo "stdout:           $MC_STDOUT"
echo "stderr:           $MC_STDERR"
echo
echo "Waiting briefly for startup status..."

if ! (cd "$MC_ROOT" && "$MC_PYTHON" -m main_computer.service_supervisor --root "$MC_ROOT" status --summary --wait-s "$MC_WAIT_SECONDS" --interval-s 2); then
  echo "Startup was requested, but status did not report cleanly." >&2
  exit 1
fi

if ! "$MC_PYTHON" - "$MC_ROOT" "$MC_STDERR" <<'PY'; then
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
stderr_path = Path(sys.argv[2])
state_path = root / "runtime" / "service_supervisor" / "state.json"

try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"Supervisor did not write a readable state file: {state_path} ({exc})", file=sys.stderr)
    raise SystemExit(1)

service = state.get("service") if isinstance(state.get("service"), dict) else {}
pid = service.get("pid")
try:
    pid_int = int(pid)
except Exception:
    pid_int = 0

alive = False
if pid_int > 0:
    try:
        os.kill(pid_int, 0)
        alive = True
    except PermissionError:
        alive = True
    except OSError:
        alive = False

state_name = str(state.get("state") or "").strip().lower()
if state_name in {"stopped", "missing", "corrupt"} or not alive:
    print(f"Supervisor exited before startup completed: state={state_name or 'unknown'} pid={pid or '?'}", file=sys.stderr)
    if stderr_path.exists():
        lines = stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        if lines:
            print(f"Last stderr lines from {stderr_path}:", file=sys.stderr)
            for line in lines:
                print(line, file=sys.stderr)
    raise SystemExit(1)
PY
  echo "Startup did not leave a live supervisor running." >&2
  exit 1
fi

if [ "$MC_OPEN_BROWSER" = "1" ]; then
  if command -v xdg-open >/dev/null 2>&1; then
    (xdg-open "http://127.0.0.1:$MC_PORT" >/dev/null 2>&1 || true) &
  else
    echo "xdg-open is not available; open http://127.0.0.1:$MC_PORT manually."
  fi
fi

echo
echo "Refresh status by running:"
echo "  bash start.sh"
echo "Stop app processes with:"
echo "  bash stop.sh"
