#!/usr/bin/env python3
"""Fork the latest committed K=1000 sparse-register lane for S-first sensing.

The fork is an exact training-state branch: latest committed head, optimizer, RNG,
cycle/global-step state, probes, history, and training configuration are preserved.
Only branch metadata/training contract is changed so the companion trainer can
resume with static S prepended on the *first* Qwen pass.

The source lane is never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

SOURCE_DEFAULT = Path(r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_v1")
TARGET_DEFAULT = Path(r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_v1")
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-branch-v1"
S_FIRST_GENERATOR = "frozen_static_token_prefixed_sensor_plus_self_routing_sparse_overcomplete_shared_control_dictionary_v1"
S_FIRST_PHASE = "frozen_qwen_frozen_head_frozen_static_token_s_conditioned_first_pass_self_organizing_sparse_registers_plus_balanced_pairwise"
S_FIRST_OBJECTIVE = "s_conditioned_first_pass_self_routing_sparse_registers_over_frozen_static_token_three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
EXPECTED_K = 1000
EXPECTED_RANK = 32


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def require_checkpoint(checkpoint: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    required = ("head.safetensors", "optimizer.pt", "rng_state.pt", "config.json", "meta.json")
    for name in required:
        if not (checkpoint / name).is_file():
            raise RuntimeError(f"source checkpoint missing {name}: {checkpoint}")
    return read_json(checkpoint / "config.json"), read_json(checkpoint / "meta.json")


def filtered_history(source: Path, fork_cycle: int) -> str:
    if not source.is_file():
        return ""
    kept: list[str] = []
    with source.open("r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid history JSONL at {source}:{lineno}: {exc}") from exc
            if not isinstance(row, dict) or row.get("cycle") is None:
                raise RuntimeError(f"history row has no cycle at {source}:{lineno}")
            if int(row["cycle"]) <= fork_cycle:
                kept.append(json.dumps(row, ensure_ascii=False, allow_nan=False))
    return "".join(line + "\n" for line in kept)


def validate_source(source: Path) -> dict[str, Any]:
    experiment_path = source / "experiment.json"
    config_path = source / "training_config.json"
    state_path = source / "training_state.json"
    for path in (experiment_path, config_path, state_path):
        if not path.is_file():
            raise RuntimeError(f"source lane missing {path.name}: {source}")

    experiment = read_json(experiment_path)
    config = read_json(config_path)
    state = read_json(state_path)
    latest = state.get("latest_generation")
    if not latest:
        raise RuntimeError("source lane has no committed latest_generation")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    cp_config, cp_meta = require_checkpoint(checkpoint)

    state_cycle = int(state.get("cycle", -1))
    checkpoint_cycle = int(cp_config.get("main_computer_cycle", cp_meta.get("cycle", -2)))
    if checkpoint_cycle != state_cycle:
        raise RuntimeError(
            f"source latest_generation is not the committed state cycle: checkpoint={checkpoint_cycle} state={state_cycle}"
        )
    k = int(config.get("control_space_size", -1))
    rank = int(config.get("register_rank", -1))
    if (k, rank) != (EXPECTED_K, EXPECTED_RANK):
        raise RuntimeError(f"expected K={EXPECTED_K}, rank={EXPECTED_RANK}; source has K={k}, rank={rank}")
    feedback = cp_config.get("soft_feedback")
    if not isinstance(feedback, dict) or int(feedback.get("control_space_size", -1)) != EXPECTED_K:
        raise RuntimeError("latest checkpoint is not the expected K=1000 sparse-register checkpoint")

    return {
        "experiment": experiment,
        "training_config": config,
        "state": state,
        "checkpoint": checkpoint,
        "checkpoint_config": cp_config,
        "checkpoint_meta": cp_meta,
        "cycle": state_cycle,
        "global_step": int(state.get("global_step", cp_config.get("main_computer_global_step", -1))),
    }


def branch(source: Path, target: Path, *, dry_run: bool) -> None:
    source = source.expanduser().resolve(strict=True)
    target = target.expanduser().resolve()
    if source == target:
        raise RuntimeError("source and target experiment directories must differ")
    snapshot = validate_source(source)
    checkpoint: Path = snapshot["checkpoint"]
    cycle = snapshot["cycle"]
    global_step = snapshot["global_step"]

    if target.exists() and any(target.iterdir()):
        branch_file = target / "s_first_branch.json"
        if branch_file.is_file():
            existing = read_json(branch_file)
            emit(
                "s_first_branch_already_exists",
                target=str(target),
                source=existing.get("source_experiment"),
                fork_cycle=existing.get("fork_cycle"),
                fork_checkpoint=existing.get("source_checkpoint"),
            )
            return
        raise RuntimeError(f"target branch directory is non-empty: {target}")

    source_head_sha = sha256_file(checkpoint / "head.safetensors")
    source_optimizer_sha = sha256_file(checkpoint / "optimizer.pt")
    source_rng_sha = sha256_file(checkpoint / "rng_state.pt")
    emit(
        "s_first_branch_plan",
        source_experiment=str(source),
        source_checkpoint=str(checkpoint),
        fork_cycle=cycle,
        fork_global_step=global_step,
        target_experiment=str(target),
        control_space_size=EXPECTED_K,
        register_rank=EXPECTED_RANK,
        first_pass="[S, original_path_embeddings]",
        second_pass="[S+R1(x), original_path_embeddings]",
        preserve_optimizer=True,
        preserve_rng=True,
        dry_run=dry_run,
    )
    if dry_run:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent)))
    try:
        for rel in (
            "probes",
            "shards/legacy",
            "shards/mutation",
            "shards/ast",
            "shards/consensus",
            "shards/triad",
            "checkpoints/generations",
        ):
            (temp / rel).mkdir(parents=True, exist_ok=True)

        if (source / "probes").is_dir():
            shutil.copytree(source / "probes", temp / "probes", dirs_exist_ok=True)
        for filename in ("orbit_consistency_training.json", "orbit_supervision_training.json"):
            src = source / filename
            if src.is_file():
                shutil.copy2(src, temp / filename)

        target_checkpoint = temp / "checkpoints" / "generations" / checkpoint.name
        shutil.copytree(checkpoint, target_checkpoint)

        experiment = dict(snapshot["experiment"])
        target_consensus_probe = temp / "probes" / "consensus_dev.jsonl"
        if target_consensus_probe.is_file():
            # Store the final target path, not the temporary construction path.
            experiment["consensus_dev_probe"] = str(target / "probes" / "consensus_dev.jsonl")
        experiment["soft_feedback_generator"] = S_FIRST_GENERATOR
        experiment["soft_feedback_first_pass"] = "frozen_static_S_prefixed_qwen_plus_frozen_head_sensor"
        experiment["soft_feedback_second_pass"] = "one_S_plus_dynamic_residual_helper_embedding_prepended_to_original_path_embeddings"
        experiment["branch_lineage"] = {
            "schema_version": BRANCH_SCHEMA,
            "source_experiment": str(source),
            "source_checkpoint": str(checkpoint),
            "fork_cycle": cycle,
            "fork_global_step": global_step,
            "source_head_sha256": source_head_sha,
            "architecture_change": "dynamic first-pass sensor changes from Qwen(prompt) to Qwen([S,prompt]); second pass remains Qwen([S+R1(x),prompt])",
        }
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})

        training_config = dict(snapshot["training_config"])
        training_config["soft_feedback_generator"] = S_FIRST_GENERATOR

        state = dict(snapshot["state"])
        state["experiment_sha256"] = experiment["experiment_sha256"]
        state["latest_generation"] = str(target / "checkpoints" / "generations" / checkpoint.name)
        state["phase"] = S_FIRST_PHASE
        state["training_objective"] = S_FIRST_OBJECTIVE
        state["branch_lineage"] = experiment["branch_lineage"]
        state["status"] = "branched_s_first_ready"

        atomic_json(temp / "experiment.json", experiment)
        atomic_json(temp / "training_config.json", training_config)
        atomic_json(temp / "training_state.json", state)
        (temp / "history.jsonl").write_text(filtered_history(source / "history.jsonl", cycle), encoding="utf-8")

        branch_record = {
            "schema_version": BRANCH_SCHEMA,
            "source_experiment": str(source),
            "target_experiment": str(target),
            "source_checkpoint": str(checkpoint),
            "target_checkpoint": str(target / "checkpoints" / "generations" / checkpoint.name),
            "fork_cycle": cycle,
            "fork_global_step": global_step,
            "control_space_size": EXPECTED_K,
            "register_rank": EXPECTED_RANK,
            "head_sha256": source_head_sha,
            "optimizer_sha256": source_optimizer_sha,
            "rng_state_sha256": source_rng_sha,
            "optimizer_preserved": True,
            "rng_preserved": True,
            "first_pass": "Qwen([S,prompt]) -> frozen head sensor -> router/controller",
            "second_pass": "Qwen([S+R1(x),prompt]) -> frozen head",
            "source_lane_modified": False,
        }
        atomic_json(temp / "s_first_branch.json", branch_record)

        copied = target_checkpoint
        if sha256_file(copied / "head.safetensors") != source_head_sha:
            raise RuntimeError("copied head hash differs from source")
        if sha256_file(copied / "optimizer.pt") != source_optimizer_sha:
            raise RuntimeError("copied optimizer hash differs from source")
        if sha256_file(copied / "rng_state.pt") != source_rng_sha:
            raise RuntimeError("copied RNG hash differs from source")

        # Re-read source state after the copy so this command really means latest.
        latest_state = read_json(source / "training_state.json")
        if int(latest_state.get("cycle", -1)) != cycle or Path(str(latest_state.get("latest_generation"))).resolve() != checkpoint:
            raise RuntimeError("source lane advanced while branching; rerun so the fork uses the new latest committed checkpoint")

        if target.exists():
            if any(target.iterdir()):
                raise RuntimeError(f"target became non-empty while branching: {target}")
            target.rmdir()
        temp.rename(target)
        emit(
            "s_first_branch_committed",
            target_experiment=str(target),
            fork_cycle=cycle,
            fork_global_step=global_step,
            checkpoint=str(target / "checkpoints" / "generations" / checkpoint.name),
            head_sha256=source_head_sha,
            optimizer_preserved=True,
            rng_preserved=True,
        )
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment-dir", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--target-experiment-dir", type=Path, default=TARGET_DEFAULT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    branch(args.source_experiment_dir, args.target_experiment_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
