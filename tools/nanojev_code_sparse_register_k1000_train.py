#!/usr/bin/env python3
"""Resume the migrated K=1000 self-organizing sparse-register lane.

This launcher is intentionally lane-specific. It reads the migrated lane's own
experiment/training metadata and invokes nanojev_code_sparse_register_train.py with
that established configuration so a normal resume cannot silently fall back to the
K=32 defaults.

K=1000 register telemetry is deliberately count-only. Neither the console nor the
persisted launcher log records slot identities or thousand-element coefficient arrays;
the only occupancy signal retained is how many of the 1000 coordinates are above the
configured active threshold. In normal mode the launcher emits one compact heartbeat
about every 30 seconds during training, plus cycle boundary/results events.
--verbose-output increases event frequency but remains count-only.

Only operational controls that are not part of training_config.json are exposed here:
cycles_this_run, keep_generations, local_files_only, heartbeat cadence, and console verbosity.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


TARGET_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_v1"
)
EXPECTED_OLD_CONTROL_SPACE_SIZE = 32
EXPECTED_CONTROL_SPACE_SIZE = 1000
EXPECTED_REGISTER_RANK = 32
COMPACT_LOG_NAME = "k1000_trainer_compact.log"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(mapping: dict, key: str):
    if key not in mapping:
        raise RuntimeError(f"migrated lane metadata is missing required field: {key}")
    return mapping[key]


def append_value(command: list[str], flag: str, value) -> None:
    command.extend([flag, str(value)])


def validate_lane(target: Path, experiment: dict, config: dict, state: dict) -> Path:
    control_space_size = int(require(config, "control_space_size"))
    register_rank = int(require(config, "register_rank"))
    if control_space_size != EXPECTED_CONTROL_SPACE_SIZE:
        raise RuntimeError(
            f"expected migrated K={EXPECTED_CONTROL_SPACE_SIZE} lane, got K={control_space_size}: {target}"
        )
    if register_rank != EXPECTED_REGISTER_RANK:
        raise RuntimeError(
            f"expected register rank {EXPECTED_REGISTER_RANK}, got {register_rank}: {target}"
        )
    if config.get("router_task_identity_input") is not False:
        raise RuntimeError("migrated lane no longer guarantees task-blind routing")
    if config.get("task_labels_reporting_only") is not True:
        raise RuntimeError("migrated lane no longer guarantees reporting-only task labels")

    migration = experiment.get("lane_migration")
    if not isinstance(migration, dict):
        raise RuntimeError("target experiment has no lane_migration record")
    if int(migration.get("old_control_space_size", -1)) != EXPECTED_OLD_CONTROL_SPACE_SIZE:
        raise RuntimeError("target lane was not migrated from the expected K=32 controller")
    if int(migration.get("new_control_space_size", -1)) != EXPECTED_CONTROL_SPACE_SIZE:
        raise RuntimeError("target lane migration record does not describe K=1000")
    if float(migration.get("residual_probe_max_abs_diff", float("inf"))) != 0.0:
        raise RuntimeError("target lane migration did not preserve the inherited residual map exactly")

    latest_raw = state.get("latest_generation")
    if not latest_raw:
        raise RuntimeError("target lane has no committed latest_generation")
    latest = Path(latest_raw).expanduser().resolve(strict=True)
    if not (latest / "head.safetensors").is_file():
        raise RuntimeError(f"latest K=1000 checkpoint is incomplete: {latest}")
    return latest


def build_trainer_command(
    *,
    trainer: Path,
    target: Path,
    experiment: dict,
    config: dict,
    cycles_this_run: int,
    keep_generations: int,
    local_files_only: bool,
) -> list[str]:
    command = [sys.executable, str(trainer)]

    # Bind the trainer to the exact data/source lineage recorded by the migrated lane.
    append_value(command, "--legacy-experiment-dir", require(experiment, "legacy_experiment"))
    append_value(command, "--mutation-experiment-dir", require(experiment, "mutation_experiment"))
    append_value(command, "--three-mode-experiment-dir", require(experiment, "three_mode_experiment"))
    append_value(command, "--experiment-dir", target)

    # Reconstruct every CLI-controlled field participating in training_config.json.
    fields = (
        ("cycle_seconds", "--cycle-seconds"),
        ("train_files_per_cycle", "--train-files-per-cycle"),
        ("train_units_per_cycle", "--train-units-per-cycle"),
        ("legacy_training_percent", "--legacy-training-percent"),
        ("mutation_training_percent", "--mutation-training-percent"),
        ("ast_training_percent", "--ast-training-percent"),
        ("consensus_training_percent", "--consensus-training-percent"),
        ("triad_training_percent", "--triad-training-percent"),
        ("consensus_dev_files", "--consensus-dev-files"),
        ("consensus_dev_records", "--consensus-dev-records"),
        ("mutation_max_code_tokens", "--mutation-max-code-tokens"),
        ("consensus_max_code_tokens", "--consensus-max-code-tokens"),
        ("batch_units", "--batch-units"),
        ("microbatch_questions", "--microbatch-questions"),
        ("max_microbatch_tokens", "--max-microbatch-tokens"),
        ("head_lr", "--head-lr"),
        ("weight_decay", "--weight-decay"),
        ("ranking_weight", "--ranking-weight"),
        ("ranking_margin", "--ranking-margin"),
        ("precision", "--precision"),
        ("soft_feedback_lr", "--register-lr"),
        ("soft_feedback_strength_init", "--soft-feedback-strength-init"),
        ("soft_feedback_control_eval_every", "--soft-feedback-control-eval-every"),
        ("control_space_size", "--control-space-size"),
        ("register_rank", "--register-rank"),
        ("register_sparsity_weight", "--register-sparsity-weight"),
        ("register_residual_weight", "--register-residual-weight"),
        ("register_orthogonality_weight", "--register-orthogonality-weight"),
        ("register_active_epsilon", "--register-active-epsilon"),
    )
    for key, flag in fields:
        append_value(command, flag, require(config, key))

    if bool(require(config, "disable_native_triton")):
        command.append("--disable-native-triton")
    if not bool(require(config, "soft_feedback_gradient_checkpointing")):
        command.append("--disable-soft-feedback-gradient-checkpointing")

    append_value(command, "--cycles-this-run", cycles_this_run)
    append_value(command, "--keep-generations", keep_generations)
    if local_files_only:
        command.append("--local-files-only")
    return command


def powershell_command(command: list[str]) -> str:
    def quote(value: str) -> str:
        if value.startswith("--"):
            return value
        return "'" + value.replace("'", "''") + "'"

    return " `\n  ".join(quote(value) for value in command)


def _usage_count_summary(usage):
    if not isinstance(usage, dict):
        return None
    summary = {}
    for key in (
        "questions",
        "active_epsilon",
        "active_slot_count",
        "aggregate_active_slot_count",
        "occupied_slot_count",
    ):
        if key in usage:
            summary[key] = usage[key]
    return summary or None


_REGISTER_IDENTITY_KEYS = {
    "aggregate_active_slots",
    "active_slots",
    "occupied_slots",
    "dormant_slots",
    "aggregate_mean_abs_coefficients",
    "mean_abs_coefficients",
    "slot_task_affinities",
    "slot_owners",
    "shared_slots",
    "single_task_slots",
    "multiply_claimed_slots",
    "exclusively_claimed_slots",
}


def strip_register_identity_telemetry(value):
    """Remove per-coordinate identities/magnitudes while preserving scalar counts."""
    if isinstance(value, dict):
        return {
            key: strip_register_identity_telemetry(item)
            for key, item in value.items()
            if key not in _REGISTER_IDENTITY_KEYS
        }
    if isinstance(value, list):
        return [strip_register_identity_telemetry(item) for item in value]
    return value


def compact_event(payload: dict) -> dict:
    """Return count-only K=1000 telemetry for console and persisted launcher logs."""
    event = payload.get("event")

    if event == "cycle_train_step":
        out = {
            key: payload[key]
            for key in (
                "event",
                "cycle",
                "cycle_step",
                "global_step",
                "mean_unit_nll",
                "top1_error",
                "unit_margin_loss",
                "mean_unit_margin",
                "unit_margin_satisfied_rate",
                "register_sparsity_loss",
                "register_residual_mse",
                "register_orthogonality_loss",
                "soft_feedback_grad_norm",
                "elapsed_training_seconds",
            )
            if key in payload
        }
        soft = payload.get("soft_feedback")
        if isinstance(soft, dict):
            out["soft_feedback"] = {
                key: soft[key]
                for key in (
                    "mode",
                    "dynamic_raw_rms",
                    "strength",
                    "control_space_size",
                    "register_rank",
                    "aggregate_active_slot_count",
                    "orthogonality_penalty",
                )
                if key in soft
            }
        return out

    if event == "sparse_register_usage":
        return {
            key: payload[key]
            for key in (
                "event",
                "cycle",
                "active_epsilon",
                "aggregate_questions",
                "occupied_slot_count",
                "routing_note",
            )
            if key in payload
        }

    if event == "cycle_result":
        out = {
            key: payload[key]
            for key in (
                "event",
                "cycle",
                "global_step",
                "steps",
                "legacy_dev_pair_win_rate",
                "mutation_dev_pair_win_rate",
                "ast_dev_pair_win_rate",
                "consensus_dev_accuracy",
                "triad_dev_relation_accuracy",
                "triad_dev_balanced_relation_accuracy",
                "triad_dev_all_three_relations_correct_rate",
                "triad_dev_topology_accuracy",
                "triad_dev_non_ambiguous_topology_accuracy",
                "body_grad_norm_last_step",
                "head_grad_norm_last_step",
                "soft_feedback_grad_norm_last_step",
                "training_seconds",
                "cycle_total_seconds",
                "checkpoint",
            )
            if key in payload
        }
        usage = _usage_count_summary(payload.get("register_usage"))
        if usage is not None:
            out["register_usage"] = usage
        soft = payload.get("soft_feedback_last_forward")
        if isinstance(soft, dict):
            out["soft_feedback_last_forward"] = {
                key: soft[key]
                for key in (
                    "dynamic_raw_rms",
                    "strength",
                    "control_space_size",
                    "register_rank",
                    "aggregate_active_slot_count",
                    "orthogonality_penalty",
                )
                if key in soft
            }
        return out

    return strip_register_identity_telemetry(payload)


def stream_trainer(command: list[str], target: Path, verbose_output: bool, heartbeat_seconds: float) -> int:
    compact_log = target / COMPACT_LOG_NAME
    print(
        json.dumps(
            {
                "event": "k1000_console_mode",
                "mode": "all_sanitized_events" if verbose_output else "30_second_heartbeat",
                "occupancy_logging": "count_only",
                "heartbeat_seconds": heartbeat_seconds,
                "compact_log": str(compact_log),
            },
            ensure_ascii=False,
            allow_nan=False,
        ),
        flush=True,
    )

    current_cycle = None
    requested_training_seconds = None
    next_heartbeat_seconds = heartbeat_seconds

    with compact_log.open("a", encoding="utf-8", newline="") as log:
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=child_env,
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                stripped = line.strip()
                try:
                    payload = json.loads(stripped)
                except (json.JSONDecodeError, TypeError):
                    log.write(line)
                    log.flush()
                    print(line, end="", flush=True)
                    continue

                event = payload.get("event")
                if event == "cycle_start":
                    current_cycle = payload.get("cycle")
                    requested_training_seconds = payload.get("requested_training_seconds")
                    next_heartbeat_seconds = heartbeat_seconds

                if verbose_output:
                    sanitized = strip_register_identity_telemetry(compact_event(payload))
                    encoded = json.dumps(sanitized, ensure_ascii=False, allow_nan=False)
                    log.write(encoded + "\n")
                    log.flush()
                    print(encoded, flush=True)
                    continue

                if event == "cycle_train_step":
                    elapsed = float(payload.get("elapsed_training_seconds", 0.0) or 0.0)
                    if elapsed < next_heartbeat_seconds:
                        continue
                    soft = payload.get("soft_feedback") if isinstance(payload.get("soft_feedback"), dict) else {}
                    heartbeat = {
                        "event": "k1000_heartbeat",
                        "cycle": payload.get("cycle", current_cycle),
                        "elapsed_training_seconds": elapsed,
                        "requested_training_seconds": requested_training_seconds,
                        "cycle_step": payload.get("cycle_step"),
                        "global_step": payload.get("global_step"),
                        "active_above_threshold": soft.get("aggregate_active_slot_count"),
                        "control_space_size": soft.get("control_space_size", EXPECTED_CONTROL_SPACE_SIZE),
                        "mean_unit_nll": payload.get("mean_unit_nll"),
                        "dynamic_raw_rms": soft.get("dynamic_raw_rms"),
                    }
                    while next_heartbeat_seconds <= elapsed:
                        next_heartbeat_seconds += heartbeat_seconds
                    encoded = json.dumps(heartbeat, ensure_ascii=False, allow_nan=False)
                    log.write(encoded + "\n")
                    log.flush()
                    print(encoded, flush=True)
                    continue

                if event not in {
                    "cycle_start",
                    "sparse_register_usage",
                    "cycle_result",
                    "checkpoint_committed",
                    "checkpoint_generation_deleted",
                }:
                    continue

                sanitized = strip_register_identity_telemetry(compact_event(payload))
                encoded = json.dumps(sanitized, ensure_ascii=False, allow_nan=False)
                log.write(encoded + "\n")
                log.flush()
                print(encoded, flush=True)
            return process.wait()
        except KeyboardInterrupt:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return 130

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles-this-run", type=int, default=100)
    parser.add_argument("--keep-generations", type=int, default=2)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--verbose-output", action="store_true", help="Print every sanitized trainer event; per-slot telemetry remains disabled")
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=30.0,
        help="Compact training heartbeat cadence in seconds (default: 30)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate the migrated lane and print the exact trainer invocation")
    args = parser.parse_args()

    if args.cycles_this_run <= 0:
        parser.error("--cycles-this-run must be positive")
    if args.keep_generations <= 0:
        parser.error("--keep-generations must be positive")
    if args.heartbeat_seconds <= 0:
        parser.error("--heartbeat-seconds must be positive")

    target = TARGET_EXPERIMENT.expanduser().resolve(strict=True)
    experiment = read_json(target / "experiment.json")
    config = read_json(target / "training_config.json")
    state = read_json(target / "training_state.json")
    latest = validate_lane(target, experiment, config, state)

    trainer = Path(__file__).resolve().with_name("nanojev_code_sparse_register_train.py")
    if not trainer.is_file():
        raise RuntimeError(f"base sparse-register trainer is missing: {trainer}")

    command = build_trainer_command(
        trainer=trainer,
        target=target,
        experiment=experiment,
        config=config,
        cycles_this_run=args.cycles_this_run,
        keep_generations=args.keep_generations,
        local_files_only=args.local_files_only,
    )

    print(
        json.dumps(
            {
                "event": "k1000_lane_resume",
                "experiment_dir": str(target),
                "latest_checkpoint": str(latest),
                "control_space_size": int(config["control_space_size"]),
                "register_rank": int(config["register_rank"]),
                "cycle_seconds": float(config["cycle_seconds"]),
                "register_sparsity_weight": float(config["register_sparsity_weight"]),
                "register_residual_weight": float(config["register_residual_weight"]),
                "register_orthogonality_weight": float(config["register_orthogonality_weight"]),
                "cycles_this_run": args.cycles_this_run,
                "keep_generations": args.keep_generations,
                "console_output": "all_sanitized_events" if args.verbose_output else "30_second_heartbeat",
                "occupancy_logging": "count_only",
                "heartbeat_seconds": args.heartbeat_seconds,
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=False,
            allow_nan=False,
        ),
        flush=True,
    )

    if args.dry_run:
        print(powershell_command(command))
        return

    raise SystemExit(stream_trainer(command, target, args.verbose_output, args.heartbeat_seconds))


if __name__ == "__main__":
    main()
