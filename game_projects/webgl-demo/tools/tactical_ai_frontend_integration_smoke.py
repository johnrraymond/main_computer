from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    raw = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"content-type": "application/json"} if raw is not None else {}
    request = urllib.request.Request(url, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"Expected JSON object from {url}: {result!r}")
    return result


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, separators=(",", ":")), flush=True)


def wait_for(base: str, predicate, *, deadline_seconds: float, poll_seconds: float, label: str) -> dict[str, Any]:
    started = time.monotonic()
    last: dict[str, Any] | None = None
    while time.monotonic() - started < deadline_seconds:
        last = request_json(base + "/status", timeout=5.0)
        if predicate(last):
            return last
        if last.get("phase") == "error":
            raise RuntimeError(f"{label} failed: {last.get('lastError') or last}")
        time.sleep(poll_seconds)
    raise RuntimeError(f"timed out waiting for {label} after {deadline_seconds:.1f}s; last={last}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise the browser-facing Tactical AI API against the same Space Captain runtime as the CLI smoke.")
    parser.add_argument("--viewport-url", default="http://127.0.0.1:8765")
    parser.add_argument("--time-step-seconds", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--consecutive-passes", type=int, default=3)
    parser.add_argument("--warmup-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--startup-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--battle-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--reset-after", action="store_true")
    args = parser.parse_args()

    base = args.viewport_url.rstrip("/") + "/api/applications/game/tactical-ai"
    initial = request_json(base + "/status", timeout=5.0)
    emit("initial_status", phase=initial.get("phase"), manager=initial.get("manager"))

    prepared = request_json(
        base + "/prepare",
        method="POST",
        payload={
            "time_step_seconds": args.time_step_seconds,
            "consecutive_passes": args.consecutive_passes,
            "warmup_timeout_seconds": args.warmup_timeout_seconds,
            "startup_timeout_seconds": args.startup_timeout_seconds,
            "request_timeout_seconds": max(args.startup_timeout_seconds, args.warmup_timeout_seconds),
        },
        timeout=10.0,
    )
    emit("prepare_started", phase=prepared.get("phase"))
    ready = wait_for(
        base,
        lambda status: status.get("performance", {}).get("ready") is True,
        deadline_seconds=args.startup_timeout_seconds + args.warmup_timeout_seconds + 30.0,
        poll_seconds=args.poll_seconds,
        label="performance readiness",
    )
    emit("performance_ready", performance=ready.get("performance"), adapter=ready.get("adapter"))

    started = request_json(
        base + "/battle/start",
        method="POST",
        payload={
            "seed": args.seed,
            "duration_seconds": args.duration_seconds,
            "time_step_seconds": args.time_step_seconds,
            "request_timeout_seconds": args.battle_timeout_seconds,
        },
        timeout=10.0,
    )
    emit("battle_start_requested", phase=started.get("phase"))
    running = wait_for(
        base,
        lambda status: status.get("phase") in {"running", "complete"},
        deadline_seconds=args.battle_timeout_seconds,
        poll_seconds=args.poll_seconds,
        label="primed battle start",
    )
    config = running.get("battleConfig") or {}
    if config.get("simulationReleasedAfterPrime") is not True or config.get("primedCaptainIds") != ["alpha", "beta"]:
        raise RuntimeError(f"battle released without both NanoJev captain plans: {config}")
    emit("battle_running", battleConfig=config)

    final = wait_for(
        base,
        lambda status: status.get("phase") == "complete",
        deadline_seconds=args.battle_timeout_seconds + args.duration_seconds + 30.0,
        poll_seconds=args.poll_seconds,
        label="battle completion",
    )
    result = (final.get("battle") or {}).get("result") or {}
    if result.get("ok") is not True:
        raise RuntimeError(f"tactical battle did not pass: {result}")
    emit("battle_complete", result=result)

    if args.reset_after:
        reset = request_json(base + "/reset", method="POST", payload={}, timeout=10.0)
        emit("reset", phase=reset.get("phase"))
    print(json.dumps({"ok": True, "schema": "game.tacticalAiFrontendIntegrationSmoke.v1", "result": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
