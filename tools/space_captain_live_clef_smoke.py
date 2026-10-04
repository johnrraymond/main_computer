#!/usr/bin/env python3
"""Actually run the 1-second captain smoke against the evolving TinyStories+CLEF checkpoint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tools" / "space_captain_clef_backend.py"
CONTRACT_SMOKE = ROOT / "tools" / "space_captain_differentiable_smoke.py"
DEFAULT_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique2560_stream1_train_v1"
)


def default_nanojev_python() -> Path:
    home = Path.home()
    windows = home / "NanoJev" / ".venv" / "Scripts" / "python.exe"
    if windows.is_file():
        return windows
    posix = home / "NanoJev" / ".venv" / "bin" / "python"
    if posix.is_file():
        return posix
    raise RuntimeError("could not find NanoJev venv Python; pass --nanojev-python")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Launch the real TinyStories+CLEF checkpoint backend, then characterize one "
            "captain model call as the primary temporal unit while physics advances through it."
        )
    )
    parser.add_argument("--nanojev-python", type=Path, default=None)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--decision-interval-seconds", type=float, default=1.0)
    parser.add_argument("--questions-per-call", type=int, default=20)
    parser.add_argument("--normal-time-scale", type=float, default=60.0)
    parser.add_argument("--startup-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--include-call-snapshots", action="store_true")
    args = parser.parse_args()

    if args.steps < 1:
        raise SystemExit("--steps must be at least 1")
    if args.questions_per_call < 10:
        raise SystemExit("--questions-per-call must be at least 10")

    nanojev_python = (
        Path(args.nanojev_python).expanduser().resolve(strict=True)
        if args.nanojev_python
        else default_nanojev_python().resolve(strict=True)
    )
    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    runtime_dir = ROOT / "runtime" / "captain_live_clef"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    log_path = runtime_dir / f"backend-{int(time.time())}-{port}.log"
    command = [
        str(nanojev_python), str(BACKEND), "--run-dir", str(run_dir), "--port", str(port)
    ]
    if args.checkpoint:
        command.extend(["--checkpoint", str(Path(args.checkpoint).expanduser().resolve(strict=True))])

    with log_path.open("w", encoding="utf-8") as log_handle:
        backend = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    health_url = f"http://127.0.0.1:{port}/health"
    evaluate_url = f"http://127.0.0.1:{port}/captain/evaluate"
    health = None
    started = time.monotonic()
    try:
        while time.monotonic() - started < float(args.startup_timeout_seconds):
            code = backend.poll()
            if code is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
                print(json.dumps({
                    "ok": False,
                    "error": f"live CLEF backend exited during startup with code {code}",
                    "backendLog": str(log_path),
                    "backendLogTail": tail,
                }, indent=2))
                return 2
            try:
                health = get_json(health_url)
                if health.get("ok") is True:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                pass
            time.sleep(0.25)
        if not health or health.get("ok") is not True:
            print(json.dumps({
                "ok": False,
                "error": "live CLEF backend did not become healthy",
                "backendLog": str(log_path),
            }, indent=2))
            return 2

        smoke_command = [
            sys.executable,
            str(CONTRACT_SMOKE),
            "--backend-url", evaluate_url,
            "--checkpoint-id", str(health["checkpointId"]),
            "--checkpoint-sha256", str(health["checkpointSha256"]),
            "--steps", str(int(args.steps)),
            "--decision-interval-seconds", str(float(args.decision_interval_seconds)),
            "--questions-per-call", str(int(args.questions_per_call)),
            "--normal-time-scale", str(float(args.normal_time_scale)),
        ]
        if args.include_call_snapshots:
            smoke_command.append("--include-call-snapshots")
        proc = subprocess.run(
            smoke_command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = {
                "ok": False,
                "error": "captain contract smoke did not return JSON",
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        result["liveClef"] = {
            "backendActuallyUsed": True,
            "provider": health.get("provider"),
            "runDir": health.get("runDir"),
            "checkpointPath": health.get("checkpointPath"),
            "checkpointId": health.get("checkpointId"),
            "checkpointSha256": health.get("checkpointSha256"),
            "cycle": health.get("cycle"),
            "reuseEpoch": health.get("reuseEpoch"),
            "parameterCounts": health.get("parameterCounts"),
            "backendLog": str(log_path),
        }
        if proc.stderr:
            result["contractSmokeStderr"] = proc.stderr[-8000:]
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") is True else 1
    finally:
        if backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
