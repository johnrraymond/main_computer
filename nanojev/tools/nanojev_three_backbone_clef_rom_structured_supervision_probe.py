#!/usr/bin/env python3
"""Read-only probe for the three-backbone CLEF structured-ROM training run.

This script never imports torch, never opens model weights, and never mutates the
training directory.  It is safe to run while the trainer is active.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_OUTPUT = Path.home() / "NanoJev" / "runs" / "three_backbone_clef_rom_structured_supervision_train_v3"
DEFAULT_LOSS_OUTPUT = Path.home() / "NanoJev" / "runs" / "three_backbone_clef_rom_structured_supervision_train_v3_loss"
DEFAULT_MEMORIZE_OUTPUT_ROOT = Path.home() / "NanoJev" / "runs" / "three_backbone_clef_rom_structured_supervision_train_v5_hammer"
DEFAULT_LOSS_MEMORIZE_OUTPUT_ROOT = Path.home() / "NanoJev" / "runs" / "three_backbone_clef_rom_structured_supervision_train_v5_loss_hammer"

TRAINER_BASENAME = "nanojev_three_backbone_clef_rom_structured_supervision_train.py"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"_read_error": f"{type(exc).__name__}: {exc}"}


def age_seconds(path: Path, now: float) -> float | None:
    try:
        return max(0.0, now - path.stat().st_mtime)
    except FileNotFoundError:
        return None


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "missing"
    if seconds < 1:
        return f"{seconds:.1f}s"
    if seconds < 120:
        return f"{seconds:.0f}s"
    if seconds < 7200:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def fmt_pct(numer: int | float, denom: int | float) -> str:
    if not denom:
        return "n/a"
    return f"{100.0 * float(numer) / float(denom):.1f}%"


def fmt_float(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


@dataclass
class PhaseAgg:
    count: int = 0
    total: int = 0
    correct: int = 0
    loss_sum: float = 0.0
    ce_sum: float = 0.0
    brier_sum: float = 0.0
    last_index: int = 0
    last_task: str | None = None
    last_question_id: str | None = None

    def add(self, row: dict[str, Any]) -> None:
        self.count += 1
        self.total = max(self.total, int(row.get("total") or 0))
        self.last_index = int(row.get("index") or self.last_index)
        self.last_task = str(row.get("task")) if row.get("task") is not None else self.last_task
        self.last_question_id = str(row.get("question_id")) if row.get("question_id") is not None else self.last_question_id
        if bool(row.get("correct")):
            self.correct += 1
        try:
            self.loss_sum += float(row.get("loss", 0.0))
        except (TypeError, ValueError):
            pass
        try:
            self.ce_sum += float(row.get("cross_entropy", 0.0))
        except (TypeError, ValueError):
            pass
        try:
            self.brier_sum += float(row.get("brier", 0.0))
        except (TypeError, ValueError):
            pass


@dataclass
class EventScan:
    line_count: int = 0
    malformed_lines: int = 0
    last_event: dict[str, Any] | None = None
    last_stage: dict[str, Any] | None = None
    last_heartbeat: dict[str, Any] | None = None
    last_checkpoint: dict[str, Any] | None = None
    last_new_best: dict[str, Any] | None = None
    last_cycle_complete: dict[str, Any] | None = None
    last_evidence_cache: dict[str, Any] | None = None
    last_optimizer_step: dict[str, Any] | None = None
    last_memorization_epoch: dict[str, Any] | None = None
    last_memorization_stop: dict[str, Any] | None = None
    last_eval: dict[str, Any] | None = None
    phases: dict[str, PhaseAgg] | None = None


def scan_events(path: Path) -> EventScan:
    result = EventScan(phases={})
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return result

    with handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            result.line_count += 1
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                result.malformed_lines += 1
                continue
            if not isinstance(row, dict):
                continue
            result.last_event = row
            event = row.get("event")
            if event == "clef_rom_train_stage":
                result.last_stage = row
            elif event == "clef_rom_train_heartbeat":
                result.last_heartbeat = row
            elif event == "clef_rom_checkpoint_saved":
                result.last_checkpoint = row
            elif event == "clef_rom_new_best":
                result.last_new_best = row
            elif event == "clef_rom_cycle_complete":
                result.last_cycle_complete = row
            elif event == "clef_rom_training_evidence_cached":
                result.last_evidence_cache = row
            elif event == "clef_rom_optimizer_step":
                result.last_optimizer_step = row
            elif event == "clef_rom_memorization_epoch":
                result.last_memorization_epoch = row
            elif event == "clef_rom_memorization_stop":
                result.last_memorization_stop = row
            elif event == "clef_rom_eval_question":
                result.last_eval = row
                phase = str(row.get("phase") or "unknown")
                agg = result.phases.setdefault(phase, PhaseAgg())
                agg.add(row)
    return result


ROM_BASELINE_PHASE = "cycle-000000-rom-selection"
ROM_REUSE_PHASE_RE = re.compile(r"^cycle-(?P<cycle>\d+)-reuse-(?P<reuse>\d+)-rom-selection$")


def rom_phase_identity(phase: str) -> tuple[int, int] | None:
    """Return (cycle, reuse_depth) for ROM selection phases."""
    if phase == ROM_BASELINE_PHASE:
        return (0, 0)
    match = ROM_REUSE_PHASE_RE.match(phase)
    if not match:
        return None
    return (int(match.group("cycle")), int(match.group("reuse")))


def agg_accuracy(agg: PhaseAgg | None) -> float | None:
    if agg is None or agg.count <= 0:
        return None
    return agg.correct / agg.count


def agg_mean_loss(agg: PhaseAgg | None) -> float | None:
    if agg is None or agg.count <= 0:
        return None
    return agg.loss_sum / agg.count


def agg_complete(agg: PhaseAgg | None) -> bool:
    if agg is None:
        return False
    total = agg.total or agg.count
    return total > 0 and agg.last_index >= total and agg.count >= total


def rom_selection_rows(scan: EventScan) -> list[tuple[int, int, str, PhaseAgg]]:
    rows: list[tuple[int, int, str, PhaseAgg]] = []
    for phase, agg in (scan.phases or {}).items():
        ident = rom_phase_identity(phase)
        if ident is None:
            continue
        cycle, reuse = ident
        rows.append((cycle, reuse, phase, agg))
    rows.sort(key=lambda item: (item[0], item[1]))
    return rows


def nvidia_snapshot() -> dict[str, str] | None:
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    first = completed.stdout.strip().splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    if len(parts) < 6:
        return {"raw": first}
    return {
        "name": parts[0],
        "util_pct": parts[1],
        "memory_used_mb": parts[2],
        "memory_total_mb": parts[3],
        "temperature_c": parts[4],
        "power_w": parts[5],
    }



def trainer_processes() -> list[dict[str, str]] | None:
    """Return live Python trainer processes, or None if process inspection fails."""
    if os.name == "nt":
        script = (
            "$ErrorActionPreference='Stop'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "($_.Name -match '^python(w)?\\.exe$') -and "
            f"($_.CommandLine -like '*{TRAINER_BASENAME}*') "
            "} | ForEach-Object { "
            "[Console]::WriteLine(('{0}`t{1}' -f $_.ProcessId,$_.CommandLine)) "
            "}"
        )
        cmd = ["powershell.exe", "-NoProfile", "-Command", script]
    else:
        cmd = ["ps", "-eo", "pid=,args="]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None

    found: list[dict[str, str]] = []
    for raw in completed.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if os.name == "nt":
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            pid, command = parts
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid, command = parts
            if TRAINER_BASENAME not in command:
                continue
        found.append({"pid": pid.strip(), "command": command.strip()})
    return found


def source_change_gate(
    *, status: str, trainer_procs: list[dict[str, str]] | None,
    progress: dict[str, Any] | None, scan: EventScan, events_age: float | None = None,
) -> tuple[str, str]:
    """Conservative source-mutation gate for switching experiment code/curriculum."""
    event_stream_active = status == "ACTIVE" and events_age is not None and events_age <= 120.0
    if trainer_procs:
        pids = ",".join(proc.get("pid", "?") for proc in trainer_procs)
        return (
            "NO",
            f"trainer process still running (pid={pids}); wait for finalization and process exit",
        )
    if event_stream_active:
        return (
            "NO",
            "fresh trainer events are still arriving; wait for status COMPLETE or terminal FAILED",
        )
    if trainer_procs is None:
        return (
            "UNKNOWN",
            "could not inspect trainer processes and the event stream is not fresh enough to prove liveness",
        )
    if status == "FAILED":
        return (
            "YES",
            "trainer exited after a terminal failure; logs/checkpoints are stable and source changes cannot affect the old run",
        )
    if status != "COMPLETE":
        return "NO", f"run is not finalized (status={status}); wait for status COMPLETE or terminal FAILED"
    if not progress or progress.get("stage") != "complete":
        return "NO", "progress.json has not committed stage=complete"
    if scan.last_cycle_complete is None:
        return "NO", "final cycle-complete event is missing"
    return "YES", "run finalized, cycle result committed, and trainer process has exited"

def checkpoint_names(root: Path) -> list[str]:
    cp = root / "checkpoints"
    try:
        return sorted(p.name for p in cp.iterdir() if p.is_dir())
    except FileNotFoundError:
        return []


def infer_status(
    *, root: Path, progress: dict[str, Any] | None, events_age: float | None,
    error_age: float | None, error_payload: dict[str, Any] | None,
) -> str:
    if progress and progress.get("stage") == "complete":
        return "COMPLETE"
    # error.json is intentionally persistent; only treat it as current if it is
    # at least as new as the live event stream.
    if error_payload and "_read_error" not in error_payload:
        if error_age is not None and (events_age is None or error_age <= events_age + 1.0):
            return "FAILED"
    if events_age is not None and events_age <= 120.0:
        return "ACTIVE"
    if (root / "training_state.json").is_file():
        return "STALE/IDLE"
    if root.exists():
        return "INITIALIZING/STALE"
    return "MISSING"


def render(root: Path, *, include_gpu: bool = True) -> int:
    now = time.time()
    progress_path = root / "progress.json"
    state_path = root / "training_state.json"
    experiment_path = root / "experiment.json"
    error_path = root / "error.json"
    events_path = root / "events.jsonl"

    progress = read_json(progress_path)
    state = read_json(state_path)
    experiment = read_json(experiment_path)
    error_payload = read_json(error_path)
    scan = scan_events(events_path)

    events_age = age_seconds(events_path, now)
    progress_age = age_seconds(progress_path, now)
    state_age = age_seconds(state_path, now)
    error_age = age_seconds(error_path, now)
    status = infer_status(
        root=root,
        progress=progress,
        events_age=events_age,
        error_age=error_age,
        error_payload=error_payload,
    )

    trainer_procs = trainer_processes()
    safe_change, safe_reason = source_change_gate(
        status=status, trainer_procs=trainer_procs, progress=progress, scan=scan, events_age=events_age
    )

    print("ROM TRAINING RUN PROBE")
    print(f"output_dir: {root}")
    print(f"status:     {status}")
    event_stream_active = status == "ACTIVE" and events_age is not None and events_age <= 120.0
    if trainer_procs:
        pids = ",".join(proc.get("pid", "?") for proc in trainer_procs)
        print(f"trainer:    RUNNING pid={pids}")
    elif event_stream_active:
        suffix = " (process lookup missed)" if trainer_procs == [] else " (process check unavailable)"
        print(f"trainer:    ACTIVE via event stream{suffix}")
    elif trainer_procs is None:
        print("trainer:    process check unavailable")
    else:
        print("trainer:    not running")
    print(f"safe_patch:  {safe_change} - {safe_reason}")
    print(f"events:     {scan.line_count} lines, age={fmt_age(events_age)}, malformed={scan.malformed_lines}")

    if experiment and "_read_error" not in experiment:
        parent = experiment.get("parent_hf") or experiment.get("parent") or {}
        if isinstance(parent, dict):
            repo = parent.get("repo_id")
            rev = parent.get("resolved_revision")
            release = parent.get("release_name")
            if repo or rev or release:
                print(f"parent:     {repo or '?'} @ {rev or '?'} ({release or '?'})")

    stage = None
    if progress and "_read_error" not in progress:
        stage = progress.get("stage")
        extras = []
        for key in ("cycle", "reuse_depth", "winning_reuse_depth", "data_cycle"):
            if key in progress:
                extras.append(f"{key}={progress[key]}")
        suffix = " " + " ".join(extras) if extras else ""
        print(f"stage:      {stage}{suffix} (progress age={fmt_age(progress_age)})")
    elif scan.last_stage:
        print(f"stage:      {scan.last_stage.get('stage', '?')} (from events)")

    contract = {}
    if experiment and "_read_error" not in experiment:
        contract = experiment.get("contract") or {}
    winner_criterion = str(contract.get("winner_criterion") or "")
    use_loss = "loss" in winner_criterion
    memorization = contract.get("memorization") if isinstance(contract, dict) else None
    if isinstance(memorization, dict) and memorization.get("enabled"):
        print("MEMORIZATION")
        hammer = memorization.get("hammer") if isinstance(memorization.get("hammer"), dict) else {}
        print(
            f"contract:    max_epochs={memorization.get('max_epochs', '?')} "
            f"target_acc={fmt_float(memorization.get('target_accuracy'))} "
            f"target_loss={fmt_float(memorization.get('target_loss'))} "
            f"selection_every={memorization.get('selection_eval_every', '?')} epochs"
        )
        if hammer:
            print(
                f"scheme:      requested={hammer.get('requested_scheme', '?')} "
                f"resolved={hammer.get('resolved_scheme', '?')} "
                f"strength={fmt_float(hammer.get('strength'), 2)} "
                f"budget={fmt_float(hammer.get('budget'), 2)}x "
                f"refresh={fmt_pct(float(hammer.get('refresh_fraction', 0.0)), 1.0)} "
                f"auto_backoff={'yes' if hammer.get('auto_backoff') else 'no'}"
            )
        if scan.last_memorization_epoch:
            row = scan.last_memorization_epoch
            global_loss = row.get("global_train_loss", row.get("exact_train_loss", row.get("online_loss")))
            global_acc = row.get("global_train_accuracy", row.get("exact_train_accuracy", row.get("online_accuracy")))
            base_exposures = row.get("base_exposures", row.get("unique_examples", "?"))
            replay_exposures = row.get("replay_exposures")
            replay_text = "?" if replay_exposures is None else str(replay_exposures)
            print(
                f"train:       epoch={row.get('epoch', '?')} "
                f"global_acc={fmt_pct(float(global_acc or 0.0), 1.0)} "
                f"global_loss={fmt_float(global_loss)} "
                f"base={base_exposures} replay={replay_text} "
                f"coverage={'yes' if row.get('full_bank_coverage', True) else 'NO'}"
            )
            if row.get("sample_loss") is not None:
                print(
                    f"sample:      exposures={row.get('sample_exposures', '?')} "
                    f"unique={row.get('unique_examples', '?')} "
                    f"accuracy={fmt_pct(float(row.get('sample_accuracy', 0.0)), 1.0)} "
                    f"loss={fmt_float(row.get('sample_loss'))}"
                )
            print(
                f"hammer:      strength={fmt_float(row.get('hammer_strength'), 2)} "
                f"budget={fmt_float(row.get('hammer_budget'), 2)}x "
                f"refresh={fmt_pct(float(row.get('hammer_refresh_fraction', 0.0)), 1.0)} "
                f"effectiveness={fmt_float(row.get('hammer_effectiveness'), 5)} loss/100steps "
                f"backoff={row.get('hammer_backoff_action', '?')} count={row.get('hammer_backoff_count', '?')}"
            )
        if scan.last_memorization_stop:
            row = scan.last_memorization_stop
            print(
                f"stop:        epoch={row.get('epoch', '?')} reason={row.get('reason', '?')} "
                f"accuracy={fmt_pct(float(row.get('train_accuracy', 0.0)), 1.0)} "
                f"loss={fmt_float(row.get('train_loss'))}"
            )

    rom_rows = rom_selection_rows(scan)
    baseline_agg = next((agg for cycle, reuse, _phase, agg in rom_rows if cycle == 0 and reuse == 0), None)
    baseline_acc = agg_accuracy(baseline_agg)
    baseline_loss = agg_mean_loss(baseline_agg)
    if state and "_read_error" not in state:
        if baseline_acc is None and state.get("rom_baseline_accuracy") is not None:
            baseline_acc = float(state.get("rom_baseline_accuracy"))
        if baseline_loss is None and state.get("rom_baseline_loss") is not None:
            baseline_loss = float(state.get("rom_baseline_loss"))
        if baseline_loss is None and state.get("best_selection_loss") is not None and int(state.get("cycle") or 0) == 0:
            baseline_loss = float(state.get("best_selection_loss"))

    print("ROM CUTOVER")
    if winner_criterion:
        print(f"criterion:   {'loss' if use_loss else 'accuracy'} ({winner_criterion})")
    if baseline_agg is not None:
        baseline_total = baseline_agg.total or baseline_agg.count
        baseline_status = "COMPLETE" if agg_complete(baseline_agg) else f"{baseline_agg.last_index}/{baseline_total}"
        print(
            f"baseline:    reuse=0 {baseline_status} "
            f"accuracy={fmt_pct(baseline_agg.correct, baseline_agg.count)} "
            f"loss={fmt_float(baseline_loss)}"
        )
    else:
        print(
            f"baseline:    reuse=0 accuracy={fmt_pct(baseline_acc or 0.0, 1.0) if baseline_acc is not None else 'n/a'} "
            f"loss={fmt_float(baseline_loss)}"
        )

    print("ROM PROGRESS")
    reuse_rows = [(cycle, reuse, phase, agg) for cycle, reuse, phase, agg in rom_rows if reuse > 0]
    if reuse_rows:
        for cycle, reuse, phase, agg in reuse_rows:
            total = agg.total or agg.count
            complete = agg_complete(agg)
            acc = agg_accuracy(agg)
            loss = agg_mean_loss(agg)
            delta = None if acc is None or baseline_acc is None else acc - baseline_acc
            loss_delta = None if loss is None or baseline_loss is None else loss - baseline_loss
            status_text = "COMPLETE" if complete else f"{agg.last_index}/{total} ({fmt_pct(agg.last_index, total)})"
            delta_text = "n/a" if delta is None else f"{delta * 100:+.2f}pp"
            loss_delta_text = "n/a" if loss_delta is None else f"{loss_delta:+.4f}"
            print(
                f"reuse {reuse}:    {status_text} accuracy={fmt_pct(agg.correct, agg.count)} "
                f"loss={fmt_float(loss)} acc_delta={delta_text} loss_delta={loss_delta_text}"
            )
    else:
        if stage in {"question_generation", "training_evidence_cache", "training", "memorization"}:
            print(f"candidate:   not evaluated yet; current stage={stage}")
        else:
            print("candidate:   no ROM-trained reuse evaluation yet")

    completed_reuses = [row for row in reuse_rows if agg_complete(row[3])]
    if completed_reuses:
        if use_loss:
            best_cycle, best_reuse, _best_phase, best_agg = min(
                completed_reuses,
                key=lambda row: (
                    agg_mean_loss(row[3]) if agg_mean_loss(row[3]) is not None else float("inf"),
                    row[1],
                ),
            )
        else:
            best_cycle, best_reuse, _best_phase, best_agg = max(
                completed_reuses,
                key=lambda row: (
                    agg_accuracy(row[3]) if agg_accuracy(row[3]) is not None else -1.0,
                    -row[1],
                ),
            )
        best_acc_calc = agg_accuracy(best_agg)
        best_loss_calc = agg_mean_loss(best_agg)
        best_delta = None if best_acc_calc is None or baseline_acc is None else best_acc_calc - baseline_acc
        best_loss_delta = None if best_loss_calc is None or baseline_loss is None else best_loss_calc - baseline_loss
        print(
            f"best_seen:   reuse={best_reuse} accuracy={fmt_pct(best_agg.correct, best_agg.count)} "
            f"loss={fmt_float(best_loss_calc)} "
            f"acc_delta={'n/a' if best_delta is None else f'{best_delta * 100:+.2f}pp'} "
            f"loss_delta={'n/a' if best_loss_delta is None else f'{best_loss_delta:+.4f}'}"
        )

    if scan.last_evidence_cache:
        row = scan.last_evidence_cache
        idx, total = int(row.get("index") or 0), int(row.get("total") or 0)
        if stage == "training_evidence_cache" or (scan.last_eval is None):
            print(
                f"evidence:   {idx}/{total} ({fmt_pct(idx, total)}) "
                f"task={row.get('task', '?')}"
            )

    if scan.last_optimizer_step:
        row = scan.last_optimizer_step
        print(
            f"optimizer:  cycle={row.get('cycle', '?')} epoch={row.get('epoch', '?')} "
            f"step_in_epoch={row.get('optimizer_step_in_epoch', '?')} "
            f"global_step={row.get('global_step', '?')} "
            f"grad={fmt_float(row.get('grad_norm_preclip'))}"
        )

    if state and "_read_error" not in state:
        baseline = state.get("rom_baseline_accuracy")
        best = state.get("best_selection_accuracy")
        gain = None if baseline is None or best is None else float(best) - float(baseline)
        print(
            f"state:      cycle={state.get('cycle', '?')} global_step={state.get('global_step', '?')} "
            f"committed_best_acc={fmt_float(best)} committed_gain={fmt_float(gain)} "
            f"age={fmt_age(state_age)}"
        )
        if state.get("winning_reuse_depth") is not None:
            print(f"winner:     reuse_depth={state.get('winning_reuse_depth')}")
        if state.get("best_checkpoint"):
            print(f"best_cp:    {state.get('best_checkpoint')}")

    if scan.last_checkpoint:
        print(
            f"last_cp:    {scan.last_checkpoint.get('checkpoint', '?')} "
            f"cycle={scan.last_checkpoint.get('cycle', '?')} "
            f"reuse={scan.last_checkpoint.get('reuse_depth', scan.last_checkpoint.get('reuse_epoch', '?'))}"
        )
    checkpoints = checkpoint_names(root)
    if checkpoints:
        print(f"checkpoints:{' ' if checkpoints else ''}{', '.join(checkpoints[-8:])}")

    if scan.last_new_best:
        row = scan.last_new_best
        print(
            f"new_best:   cycle={row.get('cycle', '?')} reuse={row.get('reuse_depth', '?')} "
            f"accuracy={fmt_float(row.get('selection_accuracy'))} "
            f"loss={fmt_float(row.get('selection_loss'))}"
        )
    if scan.last_cycle_complete:
        row = scan.last_cycle_complete
        print(
            f"completed:  cycle={row.get('cycle', '?')} winner_reuse={row.get('winning_reuse_depth', '?')} "
            f"best_acc={fmt_float(row.get('best_selection_accuracy'))} "
            f"dev_acc={fmt_float(row.get('fresh_dev_accuracy'))}"
        )

    # Only surface error.json when it belongs to the current/latest activity.
    if status == "FAILED" and error_payload and "_read_error" not in error_payload:
        print(
            f"failure:    {error_payload.get('exception_type', '?')}: "
            f"{error_payload.get('exception', '?')}"
        )
    elif error_payload and error_age is not None and events_age is not None and error_age > events_age + 1.0:
        print(f"old_error:  ignored stale error.json (age={fmt_age(error_age)})")

    if include_gpu:
        gpu = nvidia_snapshot()
        if gpu:
            if "raw" in gpu:
                print(f"gpu:        {gpu['raw']}")
            else:
                print(
                    f"gpu:        {gpu['name']} util={gpu['util_pct']}% "
                    f"vram={gpu['memory_used_mb']}/{gpu['memory_total_mb']} MiB "
                    f"temp={gpu['temperature_c']}C power={gpu['power_w']}W"
                )

    if scan.last_event:
        print(f"last_event: {scan.last_event.get('event', '?')}")
    return 0 if status not in {"FAILED", "MISSING"} else 1


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--use-loss", action="store_true", help="probe the default loss-selected lineage")
    parser.add_argument("--memorize", action="store_true", help="probe the default memorization lineage")
    parser.add_argument("--training-scheme", choices=("soft", "medium", "hard", "custom"), default="medium",
                        help="select the default hammer lineage when --memorize is used")
    parser.add_argument("--watch", type=float, default=0.0, metavar="SECONDS",
                        help="repeat the probe every N seconds; 0 means one shot")
    parser.add_argument("--no-gpu", action="store_true", help="skip nvidia-smi")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.output_dir is None:
        if args.memorize:
            root = DEFAULT_LOSS_MEMORIZE_OUTPUT_ROOT if args.use_loss else DEFAULT_MEMORIZE_OUTPUT_ROOT
            args.output_dir = Path(f"{root}_{args.training_scheme}")
        elif args.use_loss:
            args.output_dir = DEFAULT_LOSS_OUTPUT
        else:
            args.output_dir = DEFAULT_OUTPUT
    root = args.output_dir.expanduser().resolve()

    if args.watch <= 0:
        return render(root, include_gpu=not args.no_gpu)

    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            render(root, include_gpu=not args.no_gpu)
            print(f"\nrefreshing every {args.watch:g}s; Ctrl+C to stop")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
