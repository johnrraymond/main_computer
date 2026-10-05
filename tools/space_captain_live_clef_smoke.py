#!/usr/bin/env python3
"""Run the ordered captain-plan smoke against the evolving TinyStories+CLEF checkpoint."""
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



def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def timing_summary(values: list[float]) -> dict:
    samples = [float(value) for value in values]
    if not samples:
        return {
            "sampleCount": 0,
            "minMs": 0.0,
            "p50Ms": 0.0,
            "p95Ms": 0.0,
            "meanMs": 0.0,
            "maxMs": 0.0,
        }
    return {
        "sampleCount": len(samples),
        "minMs": min(samples),
        "p50Ms": quantile(samples, 0.50),
        "p95Ms": quantile(samples, 0.95),
        "meanMs": sum(samples) / len(samples),
        "maxMs": max(samples),
    }


def run_contract_smoke(
    *,
    evaluate_url: str,
    health: dict,
    args,
    evidence_mode: str,
    include_call_snapshots: bool,
) -> tuple[subprocess.CompletedProcess[str], dict]:
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
        "--evidence-execution-mode", evidence_mode,
    ]
    if include_call_snapshots:
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
    return proc, result


def speed_ab_benchmark(*, evaluate_url: str, health: dict, args) -> dict:
    repeats = int(args.speed_ab_repeats)
    if repeats < 1:
        raise ValueError("--speed-ab-repeats must be at least 1")

    # Alternate AB then BA so launch/order/thermal drift is not systematically assigned
    # to either execution path. Each pass contains 27 calls by default; its first call is
    # treated as a mode-specific warm-up and excluded from the steady-state comparison.
    order: list[str] = []
    for repeat_index in range(repeats):
        order.extend(
            ("prefix-cache", "full-batch")
            if repeat_index % 2 == 0
            else ("full-batch", "prefix-cache")
        )

    pass_rows = []
    by_mode = {
        "prefix-cache": {"wall": [], "backend": [], "forwardCounts": set()},
        "full-batch": {"wall": [], "backend": [], "forwardCounts": set()},
    }
    errors = []

    for pass_index, mode in enumerate(order):
        proc, result = run_contract_smoke(
            evaluate_url=evaluate_url,
            health=health,
            args=args,
            evidence_mode=mode,
            include_call_snapshots=True,
        )
        calls = list(result.get("callSnapshots") or [])
        if len(calls) < 2:
            errors.append({
                "passIndex": pass_index,
                "mode": mode,
                "error": result.get("error") or "benchmark pass returned fewer than two calls",
                "returnCode": proc.returncode,
                "stderr": proc.stderr[-2000:] if proc.stderr else "",
            })
            continue

        actual_modes = {str(call.get("evidenceExecutionModeActual") or "") for call in calls}
        expected_forward_batches = 6 if mode == "prefix-cache" else 3
        forward_counts = {int(call.get("backboneForwardBatchCount") or 0) for call in calls}
        mode_contract_ok = actual_modes == {mode} and forward_counts == {expected_forward_batches}
        if not mode_contract_ok:
            errors.append({
                "passIndex": pass_index,
                "mode": mode,
                "error": "backend did not execute the requested evidence path",
                "actualModes": sorted(actual_modes),
                "forwardBatchCounts": sorted(forward_counts),
                "expectedForwardBatchCount": expected_forward_batches,
            })
            continue

        warmup = calls[0]
        steady_calls = calls[1:]
        wall = [float(call["measuredCallLatencyMs"]) for call in steady_calls]
        backend = [float(call["backendModelLatencyMs"]) for call in steady_calls]
        by_mode[mode]["wall"].extend(wall)
        by_mode[mode]["backend"].extend(backend)
        by_mode[mode]["forwardCounts"].update(forward_counts)
        pass_rows.append({
            "passIndex": pass_index,
            "mode": mode,
            "semanticResultIgnored": True,
            "contractReturnCode": proc.returncode,
            "warmupExcludedMs": float(warmup["measuredCallLatencyMs"]),
            "steadyWall": timing_summary(wall),
            "steadyBackend": timing_summary(backend),
            "backboneForwardBatchCount": expected_forward_batches,
            "sharedPrefixCacheUsed": bool(mode == "prefix-cache"),
        })

    aggregates = {}
    for mode, samples in by_mode.items():
        aggregates[mode] = {
            "steadyWall": timing_summary(samples["wall"]),
            "steadyBackend": timing_summary(samples["backend"]),
            "backboneForwardBatchCounts": sorted(samples["forwardCounts"]),
        }

    prefix_p95 = aggregates["prefix-cache"]["steadyWall"]["p95Ms"]
    full_p95 = aggregates["full-batch"]["steadyWall"]["p95Ms"]
    prefix_mean = aggregates["prefix-cache"]["steadyWall"]["meanMs"]
    full_mean = aggregates["full-batch"]["steadyWall"]["meanMs"]
    enough_samples = (
        aggregates["prefix-cache"]["steadyWall"]["sampleCount"] > 0
        and aggregates["full-batch"]["steadyWall"]["sampleCount"] > 0
    )
    winner = None
    if enough_samples:
        winner = "prefix-cache" if prefix_p95 < full_p95 else "full-batch"
    slower_p95 = max(prefix_p95, full_p95) if enough_samples else 0.0
    faster_p95 = min(prefix_p95, full_p95) if enough_samples else 0.0

    return {
        "ok": not errors and enough_samples,
        "timingOnly": True,
        "semanticResultsIgnored": True,
        "comparisonMetric": "aggregate-steady-wall-p95-ms",
        "checkpointId": health.get("checkpointId"),
        "checkpointSha256": health.get("checkpointSha256"),
        "questionsPerCall": int(args.questions_per_call),
        "captainCallsPerPass": int(args.steps) * 3,
        "warmupCallsExcludedPerPass": 1,
        "repeatsPerMode": repeats,
        "passOrder": order,
        "passes": pass_rows,
        "modes": aggregates,
        "winner": winner,
        "p95Speedup": (slower_p95 / faster_p95) if faster_p95 > 0 else None,
        "prefixCacheVsFullBatchP95Ratio": (prefix_p95 / full_p95) if full_p95 > 0 else None,
        "prefixCacheVsFullBatchMeanRatio": (prefix_mean / full_mean) if full_mean > 0 else None,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Launch the real TinyStories+CLEF checkpoint backend, then characterize ordered "
            "captain planning where one model call is the primary temporal unit."
        )
    )
    parser.add_argument("--nanojev-python", type=Path, default=None)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--steps", type=int, default=9)
    parser.add_argument("--decision-interval-seconds", type=float, default=1.0)
    parser.add_argument("--questions-per-call", type=int, default=20)
    parser.add_argument("--normal-time-scale", type=float, default=60.0)
    parser.add_argument("--startup-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--include-call-snapshots", action="store_true")
    parser.add_argument(
        "--speed-ab",
        action="store_true",
        help=(
            "Timing-only A/B of forced shared-prefix cache (6 backbone forwards) versus "
            "forced full-batch execution (3 backbone forwards) on one loaded checkpoint."
        ),
    )
    parser.add_argument(
        "--speed-ab-repeats",
        type=int,
        default=2,
        help="Balanced AB/BA passes per execution mode for --speed-ab (default: 2).",
    )
    args = parser.parse_args()

    if args.steps < 9:
        raise SystemExit("--steps must be at least 9 to exercise the ordered plan/replan contract")
    if args.questions_per_call < 10:
        raise SystemExit("--questions-per-call must be at least 10")
    if args.speed_ab_repeats < 1:
        raise SystemExit("--speed-ab-repeats must be at least 1")

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

        live_clef = {
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

        if args.speed_ab:
            benchmark = speed_ab_benchmark(
                evaluate_url=evaluate_url,
                health=health,
                args=args,
            )
            result = {
                "ok": bool(benchmark.get("ok")),
                "speedBenchmark": benchmark,
                "liveClef": live_clef,
            }
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1

        proc, result = run_contract_smoke(
            evaluate_url=evaluate_url,
            health=health,
            args=args,
            evidence_mode="auto",
            include_call_snapshots=bool(args.include_call_snapshots),
        )
        result["liveClef"] = live_clef
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
