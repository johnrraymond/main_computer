#!/usr/bin/env python3
"""Run the ordered captain-plan smoke against the evolving TinyStories+CLEF checkpoint."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tools" / "space_captain_clef_backend.py"
CONTRACT_SMOKE = ROOT / "tools" / "space_captain_differentiable_smoke.py"
ASYNC_GAME_LOOP_SMOKE = ROOT / "tools" / "space_captain_async_game_loop_smoke.py"
BATTLE2_SMOKE = ROOT / "tools" / "space_captain_battle2_smoke.py"
GENERATION_ROOT = ROOT / "runtime" / "captain_generation"
_BATTLE2_PROMPT_MODULE = None
DEFAULT_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_structured_supervision_train_v1"
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


def post_json(url: str, payload: dict, timeout: float = 180.0) -> dict:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
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


def linear_fit(points: list[tuple[float, float]]) -> dict:
    if len(points) < 2:
        raise ValueError("linear fit requires at least two points")
    xs = [float(x) for x, _ in points]
    ys = [float(y) for _, y in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        raise ValueError("linear fit requires at least two distinct x values")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x
    residual_ss = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    total_ss = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 if total_ss <= 1e-12 else 1.0 - residual_ss / total_ss
    return {
        "model": "T(x)=m*x+b",
        "slopeMsPerJudgment": slope,
        "interceptMs": intercept,
        "fixedInvocationMsEstimate": intercept,
        "rSquared": r_squared,
        "linearEnough": bool(r_squared >= 0.98),
    }


def parse_speed_scaling_counts(raw: str) -> list[int]:
    values = []
    for piece in str(raw).split(","):
        piece = piece.strip()
        if not piece:
            continue
        value = int(piece)
        if value < 1 or value > 64:
            raise ValueError("speed scaling question counts must be in [1, 64]")
        values.append(value)
    values = sorted(set(values))
    if len(values) < 2:
        raise ValueError("speed scaling requires at least two distinct question counts")
    return values


def canonical_speed_scaling_questions(count: int) -> list[dict]:
    options = {
        "uncertain-threat": "Treat the contact as uncertain and preserve observation before committing.",
        "immediate-threat": "Treat the contact as an immediate danger requiring urgent protective action.",
        "low-threat": "Treat the contact as a low immediate danger while maintaining routine awareness.",
    }
    base_pairs = [
        ("uncertain-threat", "immediate-threat"),
        ("uncertain-threat", "low-threat"),
        ("immediate-threat", "low-threat"),
    ]
    lenses = ("crew", "mission", "uncertainty", "survival", "initiative", "range", "evidence", "risk")
    rows = []
    for index in range(int(count)):
        a, b = base_pairs[index % len(base_pairs)]
        if (index // len(base_pairs)) % 2:
            a, b = b, a
        rows.append({
            "id": f"timing.appraisal.q{index + 1:02d}",
            "optionA": a,
            "optionB": b,
            "optionAText": options[a],
            "optionBText": options[b],
            "semanticMode": "grounded-compact-pairwise-v2",
            "text": f"Lens={lenses[index % len(lenses)]}. Which alternative better fits the current threat appraisal?",
        })
    return rows


def speed_scaling_request(*, health: dict, mode: str, question_count: int) -> dict:
    return {
        "schema": "game.captainDecisionRequest.v6",
        "checkpoint": {
            "family": "tinystories-clef",
            "checkpointId": str(health["checkpointId"]),
            "sha256": str(health["checkpointSha256"]),
        },
        "semanticContext": {
            "mode": "compact-shared-context-v2",
            "text": (
                "Doctrine: protect people and ship. Current state: uncertain contact at moderate "
                "range; preserve observation while avoiding unnecessary commitment."
            ),
        },
        "execution": {"evidenceMode": mode},
        "questions": canonical_speed_scaling_questions(question_count),
    }


def fit_timing_points(points: list[dict], metric: str, summary_field: str) -> dict:
    return linear_fit([
        (float(point["questions"]), float(point[metric][summary_field]))
        for point in points
    ])


def fit_decomposition(fit: dict, question_count: int) -> dict:
    x = float(question_count)
    fixed = float(fit["interceptMs"])
    variable = float(fit["slopeMsPerJudgment"]) * x
    predicted = fixed + variable
    return {
        "questions": int(question_count),
        "predictedMs": predicted,
        "fixedInvocationMs": fixed,
        "variableJudgmentMs": variable,
        "fixedFraction": (fixed / predicted) if predicted else None,
        "variableFraction": (variable / predicted) if predicted else None,
    }


def speed_scaling_benchmark(*, evaluate_url: str, health: dict, args) -> dict:
    counts = parse_speed_scaling_counts(args.speed_scaling_counts)
    repeats = int(args.speed_scaling_repeats)
    if repeats < 2:
        raise ValueError("--speed-scaling-repeats must be at least 2")
    modes = (
        ["prefix-cache", "full-batch"]
        if args.speed_scaling_modes == "both"
        else [str(args.speed_scaling_modes)]
    )
    expected_forwards = {"prefix-cache": 6, "full-batch": 3}
    samples = {
        mode: {count: {"wall": [], "backend": [], "http": []} for count in counts}
        for mode in modes
    }
    errors = []
    warmups = []

    # Warm both the smallest and largest batch shapes before timing. This keeps model load,
    # first-kernel compilation, and first-allocation effects out of the fitted intercept.
    for mode in modes:
        for count in (counts[0], counts[-1]):
            started = time.perf_counter()
            response = post_json(
                evaluate_url,
                speed_scaling_request(health=health, mode=mode, question_count=count),
                timeout=max(180.0, float(args.startup_timeout_seconds)),
            )
            wall_ms = (time.perf_counter() - started) * 1000.0
            warmups.append({
                "mode": mode,
                "questions": count,
                "wallMs": wall_ms,
                "backendMs": float(response.get("modelLatencyMs") or 0.0),
            })

    # Snake question-count order and alternate mode order to reduce thermal/order bias.
    for repeat_index in range(repeats):
        ordered_counts = counts if repeat_index % 2 == 0 else list(reversed(counts))
        for count_index, count in enumerate(ordered_counts):
            ordered_modes = modes if (repeat_index + count_index) % 2 == 0 else list(reversed(modes))
            for mode in ordered_modes:
                payload = speed_scaling_request(health=health, mode=mode, question_count=count)
                started = time.perf_counter()
                try:
                    response = post_json(
                        evaluate_url,
                        payload,
                        timeout=max(180.0, float(args.startup_timeout_seconds)),
                    )
                except Exception as exc:
                    errors.append({
                        "repeat": repeat_index,
                        "mode": mode,
                        "questions": count,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                wall_ms = (time.perf_counter() - started) * 1000.0
                backend_ms = float(response.get("modelLatencyMs") or 0.0)
                actual_mode = str(response.get("evidenceExecutionModeActual") or "")
                forward_count = int(response.get("backboneForwardBatchCount") or 0)
                answer_count = int(response.get("independentJudgmentCount") or 0)
                if (
                    actual_mode != mode
                    or forward_count != expected_forwards[mode]
                    or answer_count != count
                ):
                    errors.append({
                        "repeat": repeat_index,
                        "mode": mode,
                        "questions": count,
                        "error": "backend did not execute requested scaling workload",
                        "actualMode": actual_mode,
                        "backboneForwardBatchCount": forward_count,
                        "independentJudgmentCount": answer_count,
                    })
                    continue
                samples[mode][count]["wall"].append(wall_ms)
                samples[mode][count]["backend"].append(backend_ms)
                samples[mode][count]["http"].append(wall_ms - backend_ms)

    mode_results = {}
    for mode in modes:
        points = []
        for count in counts:
            row = samples[mode][count]
            points.append({
                "questions": count,
                "logicalCandidateSequences": count * 2 * 3,
                "wall": timing_summary(row["wall"]),
                "backend": timing_summary(row["backend"]),
                "httpAndSerialization": timing_summary(row["http"]),
                "backboneForwardBatchCount": expected_forwards[mode],
                "clefHeadForwardCount": 1,
            })
        fits = {
            "wallMean": fit_timing_points(points, "wall", "meanMs"),
            "wallP50": fit_timing_points(points, "wall", "p50Ms"),
            "wallP95": fit_timing_points(points, "wall", "p95Ms"),
            "backendMean": fit_timing_points(points, "backend", "meanMs"),
            "backendP50": fit_timing_points(points, "backend", "p50Ms"),
            "backendP95": fit_timing_points(points, "backend", "p95Ms"),
        }
        configured_count = int(args.questions_per_call)
        mode_results[mode] = {
            "expectedBackboneForwardBatchCount": expected_forwards[mode],
            "points": points,
            "fits": fits,
            "decompositionAtConfiguredQuestionsPerCall": {
                "wallMean": fit_decomposition(fits["wallMean"], configured_count),
                "backendMean": fit_decomposition(fits["backendMean"], configured_count),
            },
            "observedOneJudgment": next((point for point in points if point["questions"] == 1), None),
        }

    crossover = None
    if set(modes) == {"prefix-cache", "full-batch"}:
        a = mode_results["prefix-cache"]["fits"]["wallMean"]
        b = mode_results["full-batch"]["fits"]["wallMean"]
        denominator = float(a["slopeMsPerJudgment"]) - float(b["slopeMsPerJudgment"])
        if abs(denominator) > 1e-12:
            x = (float(b["interceptMs"]) - float(a["interceptMs"])) / denominator
            crossover = {
                "metric": "wall-mean-fit",
                "questions": x,
                "withinMeasuredRange": bool(counts[0] <= x <= counts[-1]),
            }

    enough_samples = all(
        len(samples[mode][count]["wall"]) == repeats
        for mode in modes
        for count in counts
    )
    return {
        "ok": bool(not errors and enough_samples),
        "timingOnly": True,
        "semanticResultsIgnored": True,
        "model": "T(x)=m*x+b",
        "x": "independent-judgment-count",
        "counts": counts,
        "repeatsPerCount": repeats,
        "modesRequested": modes,
        "warmupsExcluded": warmups,
        "modes": mode_results,
        "wallMeanCrossover": crossover,
        "errors": errors,
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


def _safe_path_token(value: Any) -> str:
    text_value = str(value or "generation")
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in text_value)
    return cleaned.strip("-") or "generation"


def _parse_child_json(proc: subprocess.CompletedProcess[str], label: str) -> dict:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"{label} did not return JSON: {exc}",
            "stdoutTail": proc.stdout[-8000:],
            "stderrTail": proc.stderr[-8000:],
        }


def _battle2_command(*, evaluate_url: str, health: dict, args) -> list[str]:
    return [
        sys.executable, str(BATTLE2_SMOKE),
        "--backend-url", evaluate_url,
        "--checkpoint-id", str(health["checkpointId"]),
        "--checkpoint-sha256", str(health["checkpointSha256"]),
        "--questions-per-thought", str(min(20, int(args.questions_per_call))),
        "--duration-seconds", str(args.battle_2_duration_seconds),
        "--control-interval-seconds", str(args.battle_2_control_interval_seconds),
        "--viewport-hz", str(args.battle_2_viewport_hz),
        "--request-timeout-seconds", str(max(180.0, float(args.startup_timeout_seconds))),
    ]


def _battle2_prompt_module():
    global _BATTLE2_PROMPT_MODULE
    if _BATTLE2_PROMPT_MODULE is not None:
        return _BATTLE2_PROMPT_MODULE
    spec = importlib.util.spec_from_file_location("space_captain_battle2_prompt_helpers", BATTLE2_SMOKE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load battle-2 prompt helpers from {BATTLE2_SMOKE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _BATTLE2_PROMPT_MODULE = module
    return module


def _regenerated_personality_prompt(
    *,
    captain_id: str,
    trigger: str,
    state_values: list | tuple | None,
    stored_jacket: dict | None = None,
) -> dict:
    if not isinstance(state_values, (list, tuple)):
        return {
            "status": "unavailable",
            "reason": "saved state does not contain the canonical state array needed for current-template regeneration",
        }
    module = _battle2_prompt_module()
    jacket = dict(stored_jacket or {})
    current_jacket = module.personality_jacket(
        captain_id,
        label=jacket.get("label"),
        doctrine=jacket.get("goal"),
    )
    try:
        prompt = module.personality_prompt_from_state_values(
            captain_id,
            list(state_values),
            trigger=trigger,
            label=current_jacket.get("label"),
            doctrine=current_jacket.get("goal"),
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": str(exc),
            "jacket": current_jacket,
        }
    return {
        "status": "regenerated-current-template",
        "historicalExecution": False,
        "templateId": module.PERSONALITY_PROMPT_TEMPLATE_ID,
        "jacket": current_jacket,
        "promptText": prompt,
    }


def _resolve_generation_run(selector: str | None) -> Path:
    value = str(selector or "latest").strip()
    if not value or value.lower() == "latest":
        root = GENERATION_ROOT
        if not root.is_dir():
            raise FileNotFoundError(f"generation root does not exist: {root}")
        candidates = [
            child for child in root.iterdir()
            if child.is_dir() and (child / "manifest.json").is_file()
        ]
        if not candidates:
            raise FileNotFoundError(f"no generation runs found under {root}")
        return max(candidates, key=lambda child: (child.name, child.stat().st_mtime_ns)).resolve()

    raw = Path(value).expanduser()
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend((GENERATION_ROOT / raw, ROOT / raw, raw))
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.is_file() and candidate.name == "manifest.json":
            candidate = candidate.parent
        if candidate.is_dir() and (candidate / "manifest.json").is_file():
            return candidate
    raise FileNotFoundError(
        f"generation run not found: {value!r}; pass a run directory, manifest.json path, "
        f"or a directory name under {GENERATION_ROOT}"
    )


def _read_generation_training(path: Path) -> tuple[dict[str, int], list[dict]]:
    counts: dict[str, int] = {}
    completed_rows: list[dict] = []
    if not path.is_file():
        return counts, completed_rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL in {path} line {line_number}: {exc}") from exc
            measurement = dict(row.get("measurement") or {})
            status = str(measurement.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
            if status == "completed":
                completed_rows.append(row)
    return counts, completed_rows


def _read_generation_states(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL in {path} line {line_number}: {exc}") from exc
            state_id = str(row.get("stateId") or "")
            if state_id:
                rows[state_id] = row
    return rows


def _generation_example(row: dict, state_rows: dict[str, dict] | None = None) -> dict:
    state_rows = state_rows or {}
    state_record = state_rows.get(str(row.get("stateId") or ""), {})
    executed_request = state_record.get("executedModelRequest")
    sample_question = None
    regenerated = _regenerated_personality_prompt(
        captain_id=str(row.get("captainId") or ""),
        trigger=str(row.get("trigger") or "initial"),
        state_values=(state_record.get("stateArray") or dict(row.get("canonical") or {}).get("stateArray")),
        stored_jacket=dict(state_record.get("jacket") or {}),
    )
    if isinstance(executed_request, dict):
        question_id = str(row.get("questionId") or "")
        for question in list(executed_request.get("questions") or []):
            if str(question.get("id") or "") == question_id:
                sample_question = question
                break
        semantic_context = dict(executed_request.get("semanticContext") or {})
        exact_choice_prompt = None
        if isinstance(sample_question, dict):
            exact_choice_prompt = _battle2_prompt_module().assembled_pairwise_prompt(
                str(semantic_context.get("text") or ""),
                sample_question,
            )
        executed_model_input = {
            "status": "captured",
            "requestSha256": state_record.get("executedModelRequestSha256"),
            "sampleQuestion": sample_question,
            "sampleChoicePrompt": exact_choice_prompt,
            "fullBatchedRequest": executed_request,
            "currentTemplateRegeneration": regenerated,
        }
    else:
        executed_model_input = {
            "status": "not-captured",
            "reason": "generation run predates exact executed-request capture; regenerate with the current smoke to record it",
            "currentTemplateRegeneration": regenerated,
        }
    return {
        "simulationId": row.get("simulationId"),
        "simulationSeed": row.get("simulationSeed"),
        "sampleId": row.get("sampleId"),
        "stateId": row.get("stateId"),
        "captainId": row.get("captainId"),
        "thoughtSequence": row.get("thoughtSequence"),
        "trigger": row.get("trigger"),
        "questionId": row.get("questionId"),
        "counterfactualPairId": row.get("counterfactualPairId"),
        "canonical": row.get("canonical"),
        "views": row.get("views"),
        "executedModelInput": executed_model_input,
        "measurement": row.get("measurement"),
    }


def _captain_prompt_for_state(state_record: dict) -> dict:
    captain_id = str(state_record.get("captainId") or "")
    executed_request = state_record.get("executedModelRequest")
    base = {
        "captainId": captain_id or None,
        "stateId": state_record.get("stateId"),
        "thoughtSequence": state_record.get("thoughtSequence"),
        "trigger": state_record.get("trigger"),
    }
    regenerated = _regenerated_personality_prompt(
        captain_id=captain_id,
        trigger=str(state_record.get("trigger") or "initial"),
        state_values=state_record.get("stateArray"),
        stored_jacket=dict(state_record.get("jacket") or {}),
    )
    if not isinstance(executed_request, dict):
        return {
            **base,
            "status": "not-captured",
            "reason": "generation run predates exact executed-request capture; regenerate with the current smoke to record it",
            "regeneratedPersonalityPrompt": regenerated,
        }
    semantic_context = dict(executed_request.get("semanticContext") or {})
    jacket = dict(executed_request.get("jacket") or {})
    questions = list(executed_request.get("questions") or [])
    return {
        **base,
        "status": "captured",
        "requestSha256": state_record.get("executedModelRequestSha256"),
        "jacket": jacket,
        "promptText": semantic_context.get("text"),
        "semanticContext": semantic_context,
        "questionCount": len(questions),
        "questions": questions,
        "fullBatchedRequest": executed_request,
        "regeneratedPersonalityPrompt": regenerated,
    }


def _generation_captain_prompts(
    state_rows: dict[str, dict],
    *,
    preferred_simulation_id: str | None = None,
) -> dict:
    rows = list(state_rows.values())
    simulation_ids = sorted({str(row.get("simulationId") or "") for row in rows if row.get("simulationId")})
    selected_simulation_id = None
    if preferred_simulation_id:
        selected_simulation_id = preferred_simulation_id
    elif simulation_ids:
        selected_simulation_id = simulation_ids[0]

    if selected_simulation_id is not None:
        rows = [row for row in rows if str(row.get("simulationId") or "") == selected_simulation_id]

    # Prefer each captain's initial thought; otherwise use the earliest available thought.
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        captain_id = str(row.get("captainId") or "")
        if captain_id:
            grouped.setdefault(captain_id, []).append(row)

    prompts = []
    for captain_id in sorted(grouped):
        candidates = sorted(
            grouped[captain_id],
            key=lambda row: (
                0 if str(row.get("trigger") or "") == "initial" else 1,
                int(row.get("thoughtSequence", 10**9) or 10**9),
                str(row.get("stateId") or ""),
            ),
        )
        prompts.append(_captain_prompt_for_state(candidates[0]))

    return {
        "simulationId": selected_simulation_id,
        "policy": "same-simulation-earliest-initial-thought-per-captain",
        "captainCount": len(prompts),
        "captains": prompts,
    }


def _select_generation_examples(completed_rows: list[dict], limit: int, *, state_rows: dict[str, dict] | None = None) -> list[dict]:
    if not completed_rows:
        return []
    if limit == 0:
        return [_generation_example(row, state_rows) for row in completed_rows]

    grouped: dict[str, list[dict]] = {}
    for row in completed_rows:
        simulation_id = str(row.get("simulationId") or "unknown")
        grouped.setdefault(simulation_id, []).append(row)
    simulation_ids = sorted(grouped)
    cursors = {simulation_id: index % len(grouped[simulation_id]) for index, simulation_id in enumerate(simulation_ids)}
    selected: list[dict] = []
    used: dict[str, set[int]] = {simulation_id: set() for simulation_id in simulation_ids}

    while len(selected) < limit:
        added = False
        for simulation_id in simulation_ids:
            rows = grouped[simulation_id]
            if len(used[simulation_id]) >= len(rows):
                continue
            start = cursors[simulation_id]
            for offset in range(len(rows)):
                index = (start + offset) % len(rows)
                if index in used[simulation_id]:
                    continue
                used[simulation_id].add(index)
                cursors[simulation_id] = (index + 1) % len(rows)
                selected.append(_generation_example(rows[index], state_rows))
                added = True
                break
            if len(selected) >= limit:
                break
        if not added:
            break
    return selected



def _narration_simulation_id(
    simulations: list[dict],
    selector: str | None,
    *,
    preferred_simulation_id: str | None = None,
) -> str:
    available = [str(row.get("simulationId") or "") for row in simulations if row.get("simulationId")]
    if not available:
        raise ValueError("generation manifest contains no simulations to narrate")
    value = str(selector or "auto").strip()
    if not value or value.lower() in {"auto", "sample", "selected"}:
        if preferred_simulation_id and preferred_simulation_id in available:
            return preferred_simulation_id
        return available[0]
    if value.isdigit():
        value = f"simulation-{int(value):03d}"
    if value not in available:
        raise ValueError(
            f"narration simulation not found: {selector!r}; available simulations include "
            + ", ".join(available[:20])
        )
    return value


def _generation_battle_path(run_dir: Path, simulation_row: dict) -> Path:
    simulation_id = str(simulation_row.get("simulationId") or "")
    generation = dict(simulation_row.get("generation") or {})
    path_raw = dict(generation.get("paths") or {}).get("battle")
    candidates: list[Path] = []
    if path_raw:
        candidates.append(Path(str(path_raw)).expanduser())
    if simulation_id:
        candidates.append(run_dir / simulation_id / "battle.json")
    for candidate in candidates:
        if not candidate.is_absolute():
            candidate = run_dir / candidate
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"saved battle artifact not found for {simulation_id or 'selected simulation'}; "
        f"expected {run_dir / simulation_id / 'battle.json'}"
    )


def _narration_initial_state(
    state_rows: dict[str, dict],
    simulation_id: str,
    state_array_schema: list[str] | None,
) -> dict:
    schema = list(state_array_schema or [])
    rows = [
        row for row in state_rows.values()
        if str(row.get("simulationId") or "") == simulation_id
        and str(row.get("trigger") or "") == "initial"
    ]
    rows.sort(key=lambda row: (str(row.get("captainId") or ""), int(row.get("thoughtSequence", 0) or 0)))
    result: dict[str, dict] = {}
    for row in rows:
        captain_id = str(row.get("captainId") or "")
        if not captain_id or captain_id in result:
            continue
        values = list(row.get("stateArray") or [])
        mapped = {name: values[index] for index, name in enumerate(schema) if index < len(values)}
        result[captain_id] = {
            "stateId": row.get("stateId"),
            "stateArray": values,
            "fields": mapped,
        }
    return result


def _fmt_number(value: Any, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _narrated_action(label: str, action: dict) -> str:
    maneuver = str(action.get("maneuver") or "").strip().lower()
    maneuver_text = {
        "close": "close the range",
        "hold": "hold the current range",
        "withdraw": "open the range",
        "disengage": "open the range",
        "pursue": "press forward",
    }.get(maneuver, maneuver.replace("-", " ") if maneuver else "continue its current maneuver")
    fire_text = "keep firing" if bool(action.get("fire")) else "hold fire"
    return f"{label} chose to {maneuver_text} and {fire_text}."


def narrate_generation_battle(
    run_dir: Path,
    manifest: dict,
    simulations: list[dict],
    state_rows: dict[str, dict],
    selector: str | None,
    *,
    preferred_simulation_id: str | None = None,
) -> dict:
    simulation_id = _narration_simulation_id(
        simulations,
        selector,
        preferred_simulation_id=preferred_simulation_id,
    )
    simulation_row = next(row for row in simulations if str(row.get("simulationId") or "") == simulation_id)
    battle_path = _generation_battle_path(run_dir, simulation_row)
    battle = json.loads(battle_path.read_text(encoding="utf-8"))
    captain_rows = dict(battle.get("captains") or {})
    labels = {
        captain_id: str(dict(row or {}).get("label") or captain_id)
        for captain_id, row in captain_rows.items()
    }
    ship_to_captain = {
        str(dict(row or {}).get("shipId") or ""): captain_id
        for captain_id, row in captain_rows.items()
        if dict(row or {}).get("shipId")
    }
    initial_state = _narration_initial_state(
        state_rows,
        simulation_id,
        list(manifest.get("stateArraySchema") or []),
    )

    events: list[dict] = []
    for launch in list(battle.get("thoughtLaunches") or []):
        captain_id = str(launch.get("captainId") or "")
        trigger = str(launch.get("trigger") or "thought")
        sequence = int(launch.get("thoughtSequence", 0) or 0)
        time_s = float(launch.get("launchSimulationSeconds", 0.0) or 0.0)
        label = labels.get(captain_id, captain_id)
        if trigger.lower() == "initial":
            text = f"{label} began deciding how to engage."
        elif trigger.lower() == "impact":
            text = f"{label} began reconsidering after the recent impacts."
        else:
            text = f"{label} began reconsidering its next move."
        events.append({
            "simulationSeconds": time_s,
            "order": 10,
            "kind": "thought-launch",
            "captainId": captain_id,
            "thoughtSequence": sequence,
            "text": text,
        })

    for captain_id, captain in captain_rows.items():
        for lock in list(dict(captain or {}).get("impactOverloadLocks") or []):
            time_s = float(lock.get("simulationSeconds", 0.0) or 0.0)
            until_s = float(lock.get("availabilityLockUntilSeconds", time_s) or time_s)
            count = int(lock.get("impactCountInWindow", 0) or 0)
            events.append({
                "simulationSeconds": time_s,
                "order": 20,
                "kind": "availability-lock",
                "captainId": captain_id,
                "text": (
                    f"{labels.get(captain_id, captain_id)} was briefly unable to respond after {count} impacts "
                    f"in quick succession, until {_fmt_number(until_s)} seconds."
                ),
            })

    for impact in list(battle.get("impacts") or []):
        source_ship = str(impact.get("sourceShipId") or "")
        target_ship = str(impact.get("targetShipId") or "")
        source_captain = ship_to_captain.get(source_ship)
        target_captain = str(impact.get("targetCaptainId") or ship_to_captain.get(target_ship) or "")
        source_name = labels.get(source_captain, source_ship or "unknown source")
        target_name = labels.get(target_captain, target_ship or "unknown target")
        sequence = int(impact.get("sequence", 0) or 0)
        time_s = float(impact.get("simulationSeconds", 0.0) or 0.0)
        events.append({
            "simulationSeconds": time_s,
            "order": 30,
            "kind": "Impact",
            "impactSequence": sequence,
            "sourceCaptainId": source_captain,
            "targetCaptainId": target_captain,
            "text": (
                f"{source_name}'s projectile hit {target_name}. The strike changed {target_name}'s velocity by "
                f"{_fmt_number(impact.get('deltaVMps'))} m/s and caused "
                f"{_fmt_number(impact.get('damageDelta'))} damage."
            ),
        })

    for publication in list(battle.get("actionPublications") or []):
        captain_id = str(publication.get("captainId") or "")
        sequence = int(publication.get("thoughtSequence", 0) or 0)
        time_s = float(publication.get("publishedSimulationSeconds", 0.0) or 0.0)
        action = dict(publication.get("action") or {})
        events.append({
            "simulationSeconds": time_s,
            "order": 40,
            "kind": "action-publication",
            "captainId": captain_id,
            "thoughtSequence": sequence,
            "text": _narrated_action(labels.get(captain_id, captain_id), action),
        })

    events.sort(key=lambda row: (float(row.get("simulationSeconds", 0.0)), int(row.get("order", 0))))
    for event in events:
        event.pop("order", None)

    metrics = dict(battle.get("metrics") or simulation_row.get("metrics") or {})
    duration = float(metrics.get("finalSimulationSeconds", metrics.get("battleDurationSeconds", 0.0)) or 0.0)
    lines = [
        f"Battle {simulation_id} — seed {simulation_row.get('simulationSeed')}.",
        f"The encounter ran for {_fmt_number(duration)} seconds.",
    ]
    opening_ranges: list[float] = []
    for captain_id in sorted(initial_state):
        fields = dict(initial_state[captain_id].get("fields") or {})
        if fields:
            speed = float(fields.get("own_v_mps", 0.0) or 0.0)
            damage = float(fields.get("own_damage_fraction", 0.0) or 0.0)
            motion = "stationary" if abs(speed) < 1e-9 else f"moving at {_fmt_number(speed)} m/s"
            condition = "undamaged" if abs(damage) < 1e-12 else f"carrying {_fmt_number(damage)} damage"
            lines.append(f"{labels.get(captain_id, captain_id)} entered the fight {motion} and {condition}.")
            try:
                opening_ranges.append(float(fields.get("range_m")))
            except (TypeError, ValueError):
                pass
    if opening_ranges:
        lines.append(f"The ships began about {_fmt_number(opening_ranges[0])} m apart.")
    for event in events:
        time_s = float(event.get("simulationSeconds", 0.0) or 0.0)
        prefix = "At the opening" if abs(time_s) < 1e-12 else f"At {_fmt_number(time_s)} seconds"
        lines.append(f"{prefix}, {event['text']}")

    final_ships = dict(metrics.get("finalShips") or {})
    ending = (
        f"The encounter ended at {_fmt_number(duration)} seconds after "
        f"{int(metrics.get('impactCount', len(battle.get('impacts') or [])) or 0)} impacts."
    )
    captain_endings = []
    for captain_id, captain in sorted(captain_rows.items()):
        captain = dict(captain or {})
        ship = dict(final_ships.get(str(captain.get("shipId") or "")) or {})
        status_bits = []
        if ship:
            speed = float(ship.get("vMps", 0.0) or 0.0)
            damage = float(ship.get("damageFraction", 0.0) or 0.0)
            status_bits.append(
                f"finished moving at {_fmt_number(speed)} m/s with {_fmt_number(damage)} damage"
            )
        if captain.get("thoughtInFlightAtEnd"):
            inflight = dict(captain.get("inFlightThought") or {})
            trigger = str(inflight.get("trigger") or "").lower()
            if trigger == "impact":
                status_bits.append("was still reconsidering the fight when the simulation stopped")
            else:
                status_bits.append("was still deciding when the simulation stopped")
        if captain.get("completedUnpublishedThoughtAtEnd"):
            status_bits.append("had made a decision that had not yet taken effect")
        if status_bits:
            captain_endings.append(f"{labels.get(captain_id, captain_id)}: " + "; ".join(status_bits) + ".")
    lines.append(ending)
    lines.extend(captain_endings)

    return {
        "schema": "game.captainBattleNarration.v1",
        "simulationId": simulation_id,
        "simulationSeed": simulation_row.get("simulationSeed"),
        "selection": str(selector or "auto"),
        "sourceBattlePath": str(battle_path),
        "historicalOnly": True,
        "reranBattle": False,
        "initialState": initial_state,
        "captainPromptsLocation": "battle2GenerationView.captainPrompts",
        "timelineEventCount": len(events),
        "timeline": events,
        "finalShips": final_ships,
        "text": "\n\n".join(lines),
    }


def show_generation_run(
    selector: str | None = None,
    *,
    example_limit: int = 20,
    narrate_selector: str | None = None,
) -> dict:
    run_dir = _resolve_generation_run(selector)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    simulations = list(manifest.get("simulations") or [])
    training_path_raw = dict(manifest.get("paths") or {}).get("training")
    training_path = Path(training_path_raw) if training_path_raw else (run_dir / "training.jsonl")
    if not training_path.is_file():
        training_path = run_dir / "training.jsonl"
    measurement_status_counts, completed_rows = _read_generation_training(training_path)
    states_path_raw = dict(manifest.get("paths") or {}).get("states")
    states_path = Path(states_path_raw) if states_path_raw else (run_dir / "states.jsonl")
    if not states_path.is_file():
        states_path = run_dir / "states.jsonl"
    state_rows = _read_generation_states(states_path)
    examples = _select_generation_examples(completed_rows, int(example_limit), state_rows=state_rows)
    preferred_prompt_simulation_id = str(examples[0].get("simulationId") or "") if examples else None
    narration = None
    if narrate_selector is not None:
        narrated_simulation_id = _narration_simulation_id(
            simulations,
            narrate_selector,
            preferred_simulation_id=preferred_prompt_simulation_id or None,
        )
        preferred_prompt_simulation_id = narrated_simulation_id
        narration = narrate_generation_battle(
            run_dir,
            manifest,
            simulations,
            state_rows,
            narrated_simulation_id,
            preferred_simulation_id=narrated_simulation_id,
        )
    captain_prompts = _generation_captain_prompts(
        state_rows, preferred_simulation_id=preferred_prompt_simulation_id or None
    )

    aggregate_metric_names = (
        "initialThoughtLaunches",
        "impactTriggeredThoughtLaunches",
        "impactTriggeredThoughtCompletions",
        "impactTriggeredActionPublications",
        "impactRethinksInFlightAtEnd",
        "impactCount",
        "impactWhileThoughtInFlightCount",
        "actionPublicationCount",
        "projectileCount",
        "ordinaryPhysicsRethinkLaunches",
        "physicsVelocityDiscontinuityCount",
        "nonImpactVelocityDiscontinuityCount",
    )
    aggregate_metrics = {
        name: sum(int(dict(row.get("metrics") or {}).get(name, 0) or 0) for row in simulations)
        for name in aggregate_metric_names
    }
    per_simulation = []
    for row in simulations:
        metrics = dict(row.get("metrics") or {})
        generation = dict(row.get("generation") or {})
        per_simulation.append({
            "simulationId": row.get("simulationId"),
            "simulationSeed": row.get("simulationSeed"),
            "ok": bool(row.get("ok")),
            "failedChecks": list(row.get("failedChecks") or []),
            "stateRows": int(generation.get("stateRows", 0) or 0),
            "trainingRows": int(generation.get("trainingRows", 0) or 0),
            "completedMeasurements": int(generation.get("completedMeasurements", 0) or 0),
            "impactCount": int(metrics.get("impactCount", 0) or 0),
            "impactTriggeredThoughtLaunches": int(metrics.get("impactTriggeredThoughtLaunches", 0) or 0),
            "impactTriggeredThoughtCompletions": int(metrics.get("impactTriggeredThoughtCompletions", 0) or 0),
            "impactTriggeredActionPublications": int(metrics.get("impactTriggeredActionPublications", 0) or 0),
            "impactRethinksInFlightAtEnd": int(metrics.get("impactRethinksInFlightAtEnd", 0) or 0),
            "actionPublicationCount": int(metrics.get("actionPublicationCount", 0) or 0),
            "finalShips": dict(metrics.get("finalShips") or {}),
        })

    training_rows = int(manifest.get("trainingRows", 0) or 0)
    completed_measurements = int(manifest.get("completedMeasurements", 0) or 0)
    return {
        "ok": True,
        "schema": "game.captainBattleGenerationView.v1",
        "selector": str(selector or "latest"),
        "runId": run_dir.name,
        "runDir": str(run_dir),
        "manifestPath": str(manifest_path),
        "checkpointId": manifest.get("checkpointId"),
        "checkpointSha256": manifest.get("checkpointSha256"),
        "simulationCountRequested": int(manifest.get("simulationCountRequested", 0) or 0),
        "simulationCountCompleted": int(manifest.get("simulationCountCompleted", 0) or 0),
        "failedSimulationIds": list(manifest.get("failedSimulationIds") or []),
        "stateRows": int(manifest.get("stateRows", 0) or 0),
        "trainingRows": training_rows,
        "completedMeasurements": completed_measurements,
        "measurementCoverageFraction": (
            completed_measurements / training_rows if training_rows else 0.0
        ),
        "measurementStatusCounts": measurement_status_counts,
        "templates": dict(manifest.get("templates") or {}),
        "aggregateMetrics": aggregate_metrics,
        "simulations": per_simulation,
        "exampleSelection": {
            "policy": "round-robin-across-simulations-with-question-offset",
            "requestedLimit": int(example_limit),
            "zeroMeansAllCompleted": True,
            "eligibleCompletedMeasurements": len(completed_rows),
            "returnedExamples": len(examples),
        },
        "captainPrompts": captain_prompts,
        **({"narration": narration} if narration is not None else {}),
        "examples": examples,
    }


def run_battle2_generation(*, evaluate_url: str, health: dict, args) -> dict:
    count = int(args.generate_count)
    if args.generate_output_dir:
        output_root = Path(args.generate_output_dir).expanduser()
        if not output_root.is_absolute():
            output_root = ROOT / output_root
        output_root = output_root.resolve()
    else:
        stamp = time.strftime("%Y%m%dT%H%M%S")
        checkpoint_token = _safe_path_token(str(health.get("checkpointId") or "checkpoint"))
        output_root = (ROOT / "runtime" / "captain_generation" / f"{stamp}-{checkpoint_token}").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    aggregate_states = output_root / "states.jsonl"
    aggregate_training = output_root / "training.jsonl"
    aggregate_states.write_text("", encoding="utf-8")
    aggregate_training.write_text("", encoding="utf-8")

    simulations = []
    state_rows = 0
    training_rows = 0
    completed_measurements = 0
    state_array_schema = None
    counterfactual_array_schema = None
    for index in range(1, count + 1):
        simulation_id = f"simulation-{index:03d}"
        simulation_seed = int(args.generate_seed) + index - 1
        simulation_dir = output_root / simulation_id
        command = _battle2_command(evaluate_url=evaluate_url, health=health, args=args)
        command.extend([
            "--generate-samples",
            "--generation-drain-seconds", str(float(getattr(args, "generate_drain_seconds", 60.0))),
            "--generation-output-dir", str(simulation_dir),
            "--simulation-id", simulation_id,
            "--simulation-seed", str(simulation_seed),
        ])
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        battle = _parse_child_json(proc, f"{simulation_id} battle-2 smoke")
        simulation_dir.mkdir(parents=True, exist_ok=True)
        (simulation_dir / "runner-result.json").write_text(json.dumps({
            "returnCode": proc.returncode,
            "result": battle,
            "stderr": proc.stderr[-8000:] if proc.stderr else "",
        }, indent=2), encoding="utf-8")
        generation = dict(battle.get("generation") or {})
        if generation.get("stateArraySchema"):
            state_array_schema = list(generation["stateArraySchema"])
        if generation.get("counterfactualArraySchema"):
            counterfactual_array_schema = list(generation["counterfactualArraySchema"])
        state_rows += int(generation.get("stateRows", 0) or 0)
        training_rows += int(generation.get("trainingRows", 0) or 0)
        completed_measurements += int(generation.get("completedMeasurements", 0) or 0)
        state_path = simulation_dir / "states.jsonl"
        training_path = simulation_dir / "training.jsonl"
        if state_path.is_file():
            with aggregate_states.open("a", encoding="utf-8") as handle:
                handle.write(state_path.read_text(encoding="utf-8"))
        if training_path.is_file():
            with aggregate_training.open("a", encoding="utf-8") as handle:
                handle.write(training_path.read_text(encoding="utf-8"))
        simulations.append({
            "simulationId": simulation_id,
            "simulationSeed": simulation_seed,
            "ok": bool(battle.get("ok")),
            "returnCode": proc.returncode,
            "error": battle.get("error"),
            "stdoutTail": battle.get("stdoutTail"),
            "stderrTail": battle.get("stderrTail") or (proc.stderr[-8000:] if proc.stderr else ""),
            "failedChecks": list(battle.get("failedChecks") or []),
            "metrics": dict(battle.get("metrics") or {}),
            "providerDiagnostics": dict(battle.get("providerDiagnostics") or {}),
            "generation": generation,
        })

    failed = [row["simulationId"] for row in simulations if not row["ok"]]
    manifest = {
        "schema": "game.captainBattleGenerationBatch.v1",
        "mode": "battle-2",
        "simulationCountRequested": count,
        "simulationCountCompleted": len(simulations),
        "defaultSimulationCount": 4,
        "baseSeed": int(args.generate_seed),
        "generationDrainSeconds": float(getattr(args, "generate_drain_seconds", 60.0)),
        "checkpointId": str(health["checkpointId"]),
        "checkpointSha256": str(health["checkpointSha256"]),
        "stateRows": state_rows,
        "trainingRows": training_rows,
        "completedMeasurements": completed_measurements,
        "stateArraySchema": state_array_schema,
        "counterfactualArraySchema": counterfactual_array_schema,
        "templates": {
            "human": "battle-human-readable-jacket-v2",
            "learning": "battle-rigid-array-jacket-v2",
            "executed": "battle-live-personality-jacket-v1",
            "roles": {
                "battle-human-readable-jacket-v2": "human-readable-jacket-conditioned-reference-view",
                "battle-rigid-array-jacket-v2": "learning-oriented-jacket-conditioned-candidate-view",
                "battle-live-personality-jacket-v1": "personality-jacket-conditioned-live-execution-view",
            },
        },
        "paths": {
            "outputDir": str(output_root),
            "states": str(aggregate_states),
            "training": str(aggregate_training),
            "manifest": str(output_root / "manifest.json"),
        },
        "failedSimulationIds": failed,
        "simulations": simulations,
    }
    manifest["ok"] = len(simulations) == count and not failed
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


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
    parser.add_argument(
        "--speed-scaling",
        action="store_true",
        help=(
            "Timing-only scaling curve versus independent judgment count. Fits T(x)=m*x+b "
            "for wall/backend mean, P50, and P95, with b as per-invocation baseline."
        ),
    )
    parser.add_argument(
        "--async-game-loop",
        action="store_true",
        help=(
            "Exercise three captain requests asynchronously while a 0.5-2 second authoritative "
            "physics/control horizon continues to feed smooth gradient-based viewport updates."
        ),
    )
    parser.add_argument(
        "--battle-2",
        action="store_true",
        help=(
            "Run two captains in one live local battle. Ordinary physics remains continuous; "
            "Impact(...) is the only post-bootstrap rethink trigger, and captain thoughts never block physics."
        ),
    )
    parser.add_argument(
        "--battle-2-duration-seconds",
        type=float,
        default=24.0,
        help="Wall/simulation duration for --battle-2 (default: 24 seconds).",
    )
    parser.add_argument(
        "--battle-2-control-interval-seconds",
        type=float,
        default=0.5,
        help="Authoritative action publication interval for --battle-2 (default: 0.5 seconds).",
    )
    parser.add_argument(
        "--battle-2-viewport-hz",
        type=float,
        default=60.0,
        help="Viewport prediction heartbeat for --battle-2 (default: 60 Hz).",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help=(
            "Add corpus generation to --battle-2. Runs multiple complete battles while emitting "
            "canonical numeric arrays plus paired human-readable and rigid learning-oriented views."
        ),
    )
    parser.add_argument(
        "--generate-count",
        type=int,
        default=4,
        help="Complete battle simulations produced by --battle-2 --generate (default: 4).",
    )
    parser.add_argument(
        "--generate-seed",
        type=int,
        default=1,
        help="Base deterministic simulation seed for --generate (default: 1).",
    )
    parser.add_argument(
        "--generate-drain-seconds",
        type=float,
        default=60.0,
        help=(
            "Per-simulation generation-only wall-time budget for harvesting already-launched "
            "captain cognition after the battle physics horizon (default: 60)."
        ),
    )
    parser.add_argument(
        "--generate-output-dir",
        type=Path,
        default=None,
        help="Optional corpus output directory; defaults under runtime/captain_generation/.",
    )
    parser.add_argument(
        "--show-generation",
        nargs="?",
        const="latest",
        default=None,
        metavar="RUN",
        help=(
            "Show a previous --battle-2 --generate run without starting the CLEF backend. "
            "With no RUN, shows the latest saved run; RUN may be a generation directory name or path."
        ),
    )
    parser.add_argument(
        "--show-generation-examples",
        type=int,
        default=20,
        metavar="N",
        help=(
            "Full completed judgment examples returned by --show-generation (default: 20, "
            "selected across simulations; 0 shows all completed measurements)."
        ),
    )
    parser.add_argument(
        "--narrate",
        nargs="?",
        const="auto",
        default=None,
        metavar="SIMULATION",
        help=(
            "With --show-generation, print a clean text narration of one saved battle. "
            "With no SIMULATION, narrates the sampled simulation; accepts simulation-015 or 15."
        ),
    )
    parser.add_argument(
        "--async-game-loop-viewport-hz",
        type=float,
        default=60.0,
        help="Viewport heartbeat used by --async-game-loop (default: 60 Hz).",
    )
    parser.add_argument(
        "--async-game-loop-intervals-seconds",
        type=str,
        default="0.5,1,2",
        help="Control deadlines characterized by --async-game-loop (default: 0.5,1,2).",
    )
    parser.add_argument(
        "--speed-scaling-counts",
        type=str,
        default="1,2,4,8,12,16,20",
        help="Comma-separated judgment counts for --speed-scaling (default: 1,2,4,8,12,16,20).",
    )
    parser.add_argument(
        "--speed-scaling-repeats",
        type=int,
        default=5,
        help="Measured calls per question-count and execution mode (default: 5).",
    )
    parser.add_argument(
        "--speed-scaling-modes",
        choices=("both", "prefix-cache", "full-batch"),
        default="both",
        help="Execution paths to fit for --speed-scaling (default: both).",
    )
    args = parser.parse_args()

    if args.show_generation is not None:
        conflicting = [
            name for name, enabled in (
                ("--speed-ab", args.speed_ab),
                ("--speed-scaling", args.speed_scaling),
                ("--async-game-loop", args.async_game_loop),
                ("--battle-2", args.battle_2),
                ("--generate", args.generate),
            )
            if enabled
        ]
        if conflicting:
            raise SystemExit(
                "--show-generation is a read-only mode and cannot be combined with " + ", ".join(conflicting)
            )
        if args.show_generation_examples < 0:
            raise SystemExit("--show-generation-examples must be 0 or greater")
        try:
            view = show_generation_run(
                args.show_generation,
                example_limit=int(args.show_generation_examples),
                narrate_selector=args.narrate,
            )
        except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(json.dumps({
                "ok": False,
                "schema": "game.captainBattleGenerationView.v1",
                "selector": str(args.show_generation),
                "error": str(exc),
            }, indent=2))
            return 2
        if args.narrate is not None:
            narration = dict(view.get("narration") or {})
            print(str(narration.get("text") or "").strip())
            return 0
        print(json.dumps({"ok": True, "battle2GenerationView": view}, indent=2))
        return 0

    if args.narrate is not None:
        raise SystemExit("--narrate is a read-only modifier for --show-generation")

    if args.steps < 9:
        raise SystemExit("--steps must be at least 9 to exercise the ordered plan/replan contract")
    if args.questions_per_call < 10:
        raise SystemExit("--questions-per-call must be at least 10")
    if args.speed_ab_repeats < 1:
        raise SystemExit("--speed-ab-repeats must be at least 1")
    if args.speed_scaling_repeats < 2:
        raise SystemExit("--speed-scaling-repeats must be at least 2")
    selected_benchmarks = int(bool(args.speed_ab)) + int(bool(args.speed_scaling)) + int(bool(args.async_game_loop)) + int(bool(args.battle_2))
    if selected_benchmarks > 1:
        raise SystemExit("--speed-ab, --speed-scaling, --async-game-loop, and --battle-2 are mutually exclusive")
    if args.generate and not args.battle_2:
        raise SystemExit("--generate is an additive modifier for --battle-2; pass --battle-2 --generate")
    if args.generate_count < 1:
        raise SystemExit("--generate-count must be at least 1")
    if float(args.generate_drain_seconds) < 0:
        raise SystemExit("--generate-drain-seconds must be non-negative")
    if args.async_game_loop_viewport_hz <= 0:
        raise SystemExit("--async-game-loop-viewport-hz must be positive")
    if args.battle_2_duration_seconds <= 0:
        raise SystemExit("--battle-2-duration-seconds must be positive")
    if args.battle_2_control_interval_seconds <= 0:
        raise SystemExit("--battle-2-control-interval-seconds must be positive")
    if args.battle_2_viewport_hz <= 0:
        raise SystemExit("--battle-2-viewport-hz must be positive")
    try:
        parse_speed_scaling_counts(args.speed_scaling_counts)
    except (TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

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

        if args.speed_scaling:
            benchmark = speed_scaling_benchmark(
                evaluate_url=evaluate_url,
                health=health,
                args=args,
            )
            result = {
                "ok": bool(benchmark.get("ok")),
                "speedScaling": benchmark,
                "liveClef": live_clef,
            }
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1

        if args.async_game_loop:
            command = [
                sys.executable, str(ASYNC_GAME_LOOP_SMOKE),
                "--backend-url", evaluate_url,
                "--checkpoint-id", str(health["checkpointId"]),
                "--checkpoint-sha256", str(health["checkpointSha256"]),
                "--questions-per-captain", str(args.questions_per_call),
                "--viewport-hz", str(args.async_game_loop_viewport_hz),
                "--intervals-seconds", str(args.async_game_loop_intervals_seconds),
                "--request-timeout-seconds", str(max(180.0, float(args.startup_timeout_seconds))),
            ]
            proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
            try:
                game_loop = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                game_loop = {
                    "ok": False,
                    "error": f"async game-loop smoke did not return JSON: {exc}",
                    "stdoutTail": proc.stdout[-8000:],
                }
            result = {
                "ok": bool(game_loop.get("ok")),
                "asyncGameLoop": game_loop,
                "liveClef": live_clef,
            }
            if proc.stderr:
                result["asyncGameLoopStderr"] = proc.stderr[-8000:]
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1

        if args.battle_2:
            if args.generate:
                generation = run_battle2_generation(
                    evaluate_url=evaluate_url,
                    health=health,
                    args=args,
                )
                result = {
                    "ok": bool(generation.get("ok")),
                    "battle2Generation": generation,
                    "liveClef": live_clef,
                }
                print(json.dumps(result, indent=2))
                return 0 if result["ok"] else 1

            command = _battle2_command(evaluate_url=evaluate_url, health=health, args=args)
            proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
            battle = _parse_child_json(proc, "battle-2 smoke")
            result = {
                "ok": bool(battle.get("ok")),
                "battle2": battle,
                "liveClef": live_clef,
            }
            if proc.stderr:
                result["battle2Stderr"] = proc.stderr[-8000:]
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1

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
