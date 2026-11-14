#!/usr/bin/env python3
"""Fork the latest committed K=1000 S-first lane into a three-pass R2 branch.

The fork preserves the latest committed S-first model, optimizer state, RNG state,
probes, history, and curriculum.  It adds a second recurrence controller that:

* reuses the existing learned 1000-vector control bank;
* is initialized from the trained R1 router/controller;
* has an exact-zero recurrent gain, so R2 == 0 at the fork;
* therefore leaves the inherited S-first answer unchanged at cut-over.

The source S-first lane is never modified.
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

SOURCE_DEFAULT = Path(r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_v1")
TARGET_DEFAULT = Path(r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_v1")
SOURCE_BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-branch-v1"
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-branch-v1"
SOURCE_GENERATOR = "frozen_static_token_prefixed_sensor_plus_self_routing_sparse_overcomplete_shared_control_dictionary_v1"
R2_GENERATOR = "s_first_recurrent_r2_shared_sparse_control_dictionary_v1"
R2_PHASE = "frozen_qwen_frozen_head_frozen_static_token_s_first_recurrent_r2_shared_sparse_registers_plus_balanced_pairwise"
R2_OBJECTIVE = "s_first_r1_plus_zero_init_recurrent_r2_over_shared_sparse_register_bank_three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
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
    for name in ("head.safetensors", "optimizer.pt", "rng_state.pt", "config.json", "meta.json"):
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
    for filename in ("experiment.json", "training_config.json", "training_state.json", "s_first_branch.json"):
        if not (source / filename).is_file():
            raise RuntimeError(f"source S-first lane missing {filename}: {source}")
    experiment = read_json(source / "experiment.json")
    config = read_json(source / "training_config.json")
    state = read_json(source / "training_state.json")
    s_first_branch = read_json(source / "s_first_branch.json")
    if s_first_branch.get("schema_version") != SOURCE_BRANCH_SCHEMA:
        raise RuntimeError(f"source is not the expected S-first lane: {source}")
    if experiment.get("soft_feedback_generator") != SOURCE_GENERATOR:
        raise RuntimeError("source experiment is not using the S-first generator")
    if config.get("soft_feedback_generator") != SOURCE_GENERATOR:
        raise RuntimeError("source training_config is not using the S-first generator")
    if (int(config.get("control_space_size", -1)), int(config.get("register_rank", -1))) != (EXPECTED_K, EXPECTED_RANK):
        raise RuntimeError(f"expected source K={EXPECTED_K}, rank={EXPECTED_RANK}")
    latest = state.get("latest_generation")
    if not latest:
        raise RuntimeError("source S-first lane has no committed latest_generation")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    cp_config, cp_meta = require_checkpoint(checkpoint)
    cycle = int(state.get("cycle", -1))
    cp_cycle = int(cp_config.get("main_computer_cycle", cp_meta.get("cycle", -2)))
    if cp_cycle != cycle:
        raise RuntimeError(f"source latest checkpoint cycle mismatch: checkpoint={cp_cycle} state={cycle}")
    feedback = cp_config.get("soft_feedback")
    if not isinstance(feedback, dict):
        raise RuntimeError("source checkpoint has no soft_feedback contract")
    if int(feedback.get("control_space_size", -1)) != EXPECTED_K or int(feedback.get("register_rank", -1)) != EXPECTED_RANK:
        raise RuntimeError("source checkpoint is not K=1000 rank=32")
    return {
        "experiment": experiment,
        "config": config,
        "state": state,
        "s_first_branch": s_first_branch,
        "checkpoint": checkpoint,
        "checkpoint_config": cp_config,
        "checkpoint_meta": cp_meta,
        "cycle": cycle,
        "global_step": int(state.get("global_step", cp_config.get("main_computer_global_step", -1))),
    }


def expand_head_for_r2(head_path: Path) -> tuple[str, str]:
    try:
        import torch
        from safetensors.torch import load_file, save_file
    except ImportError as exc:
        raise RuntimeError("cut-over requires torch and safetensors; run with the NanoJev venv") from exc

    source_sha = sha256_file(head_path)
    weights = load_file(str(head_path), device="cpu")
    mapping = {
        "soft_feedback.r2_router_down.weight": "soft_feedback.router_down.weight",
        "soft_feedback.r2_router_score.weight": "soft_feedback.router_score.weight",
        "soft_feedback.r2_router_coeff.weight": "soft_feedback.router_coeff.weight",
    }
    for dst, src in mapping.items():
        if src not in weights:
            raise RuntimeError(f"source head lacks {src}")
        if dst in weights:
            raise RuntimeError(f"source head already contains R2 tensor {dst}")
    if "soft_feedback.r2_gain" in weights:
        raise RuntimeError("source head already contains soft_feedback.r2_gain")

    expanded = {key: value.detach().cpu().contiguous().clone() for key, value in weights.items()}
    for dst, src in mapping.items():
        expanded[dst] = weights[src].detach().cpu().contiguous().clone()
    dtype = weights["soft_feedback.router_coeff.weight"].dtype
    expanded["soft_feedback.r2_gain"] = torch.zeros(1, dtype=dtype)

    tmp = head_path.with_name(head_path.name + ".tmp")
    save_file(expanded, str(tmp))
    os.replace(tmp, head_path)
    return source_sha, sha256_file(head_path)


def expand_optimizer_for_r2(optimizer_path: Path) -> tuple[str, str, int]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("cut-over requires torch; run with the NanoJev venv") from exc

    source_sha = sha256_file(optimizer_path)
    payload = torch.load(optimizer_path, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict) or not isinstance(payload.get("param_groups"), list):
        raise RuntimeError("unexpected optimizer state format")
    groups = payload["param_groups"]
    if len(groups) != 1:
        raise RuntimeError(f"expected one AdamW parameter group, found {len(groups)}")
    params = list(groups[0].get("params", []))
    if len(params) != 4:
        raise RuntimeError(
            "expected four inherited dynamic parameters "
            "(bank, R1 down, R1 score, R1 coeff); found " + str(len(params))
        )
    all_ids = [int(v) for group in groups for v in group.get("params", [])]
    state_ids = [int(v) for v in payload.get("state", {}).keys()]
    next_id = max(all_ids + state_ids, default=-1) + 1
    new_ids = list(range(next_id, next_id + 4))
    groups[0]["params"] = params + new_ids
    # AdamW accepts parameters with no state entry; their moments are created on first update.
    tmp = optimizer_path.with_name(optimizer_path.name + ".tmp")
    torch.save(payload, tmp)
    os.replace(tmp, optimizer_path)
    return source_sha, sha256_file(optimizer_path), len(new_ids)


def branch(source: Path, target: Path, *, dry_run: bool) -> None:
    source = source.expanduser().resolve(strict=True)
    target = target.expanduser().resolve()
    if source == target:
        raise RuntimeError("source and target experiment directories must differ")
    snapshot = validate_source(source)
    checkpoint: Path = snapshot["checkpoint"]
    cycle = int(snapshot["cycle"])
    global_step = int(snapshot["global_step"])

    if target.exists() and any(target.iterdir()):
        marker = target / "s_first_r2_branch.json"
        if marker.is_file():
            existing = read_json(marker)
            emit(
                "s_first_r2_branch_already_exists",
                target=str(target),
                source=existing.get("source_experiment"),
                fork_cycle=existing.get("fork_cycle"),
            )
            return
        raise RuntimeError(f"target branch directory is non-empty: {target}")

    emit(
        "s_first_r2_branch_plan",
        source_experiment=str(source),
        source_checkpoint=str(checkpoint),
        fork_cycle=cycle,
        fork_global_step=global_step,
        target_experiment=str(target),
        control_space_size=EXPECTED_K,
        register_rank=EXPECTED_RANK,
        pass1="Qwen([S,prompt]) -> R1",
        pass2="Qwen([S+R1,prompt]) -> R2",
        pass3="Qwen([S+R1+R2,prompt]) -> answer",
        shared_control_bank=True,
        r2_router_initialization="copy_R1_router",
        r2_gain_initialization=0.0,
        parent_behavior_preserved_at_cutover=True,
        dry_run=dry_run,
    )
    if dry_run:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent)))
    try:
        for rel in (
            "probes", "shards/legacy", "shards/mutation", "shards/ast",
            "shards/consensus", "shards/triad", "checkpoints/generations",
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
        source_head_sha, target_head_sha = expand_head_for_r2(target_checkpoint / "head.safetensors")
        source_opt_sha, target_opt_sha, new_optimizer_params = expand_optimizer_for_r2(target_checkpoint / "optimizer.pt")
        source_rng_sha = sha256_file(checkpoint / "rng_state.pt")
        if sha256_file(target_checkpoint / "rng_state.pt") != source_rng_sha:
            raise RuntimeError("copied RNG hash differs from source")

        parent_s_first_branch = snapshot["s_first_branch"]
        lineage = {
            "schema_version": BRANCH_SCHEMA,
            "source_experiment": str(source),
            "source_checkpoint": str(checkpoint),
            "fork_cycle": cycle,
            "fork_global_step": global_step,
            "source_head_sha256": source_head_sha,
            "target_head_sha256": target_head_sha,
            "source_optimizer_sha256": source_opt_sha,
            "target_optimizer_sha256": target_opt_sha,
            "parent_s_first_fork_cycle": parent_s_first_branch.get("fork_cycle"),
            "architecture_change": (
                "preserve S-first R1; add pass-2 observation and zero-gated R2 router cloned from R1; "
                "R2 reuses the existing K=1000 bank; final pass uses H(S+R1+R2)"
            ),
        }

        experiment = dict(snapshot["experiment"])
        target_consensus_probe = temp / "probes" / "consensus_dev.jsonl"
        if target_consensus_probe.is_file():
            experiment["consensus_dev_probe"] = str(target / "probes" / "consensus_dev.jsonl")
        experiment["parent_s_first_branch_lineage"] = experiment.get("branch_lineage")
        experiment["branch_lineage"] = lineage
        experiment["soft_feedback_generator"] = R2_GENERATOR
        experiment["soft_feedback_first_pass"] = "Qwen([S,prompt]) -> frozen head sensor -> R1 router"
        experiment["soft_feedback_second_pass"] = "Qwen([S+R1,prompt]) -> frozen head sensor -> R2 router"
        experiment["soft_feedback_third_pass"] = "Qwen([S+R1+R2,prompt]) -> frozen head answer"
        experiment["soft_feedback_backbone_reads_dynamic"] = 3
        experiment["recurrent_r2_enabled"] = True
        experiment["recurrent_r2_shared_control_bank"] = True
        experiment["recurrent_r2_router_initialization"] = "copy_R1_router"
        experiment["recurrent_r2_gain_initialization"] = 0.0
        experiment["recurrent_r2_cutover_invariant"] = "R2=0 exactly, so final H(S+R1+R2) equals inherited H(S+R1)"
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})

        config = dict(snapshot["config"])
        config["soft_feedback_generator"] = R2_GENERATOR

        cp_config = read_json(target_checkpoint / "config.json")
        cp_config["main_computer_experiment_sha256"] = experiment["experiment_sha256"]
        feedback = dict(cp_config.get("soft_feedback") or {})
        feedback.update({
            "generator": R2_GENERATOR,
            "backbone_reads_dynamic": 3,
            "recurrent_r2_enabled": True,
            "recurrent_r2_shared_control_bank": True,
            "recurrent_r2_router_initialization": "copy_R1_router",
            "recurrent_r2_gain": 0.0,
            "recurrent_r2_cutover_exact_noop": True,
        })
        cp_config["soft_feedback"] = feedback
        atomic_json(target_checkpoint / "config.json", cp_config)

        state = dict(snapshot["state"])
        state["experiment_sha256"] = experiment["experiment_sha256"]
        state["latest_generation"] = str(target / "checkpoints" / "generations" / checkpoint.name)
        state["phase"] = R2_PHASE
        state["training_objective"] = R2_OBJECTIVE
        state["branch_lineage"] = lineage
        state["status"] = "branched_s_first_r2_ready"
        if isinstance(state.get("last_result"), dict):
            last_result = dict(state["last_result"])
            if last_result.get("checkpoint"):
                last_result["checkpoint"] = str(target / "checkpoints" / "generations" / checkpoint.name)
            state["last_result"] = last_result

        atomic_json(temp / "experiment.json", experiment)
        atomic_json(temp / "training_config.json", config)
        atomic_json(temp / "training_state.json", state)
        (temp / "history.jsonl").write_text(filtered_history(source / "history.jsonl", cycle), encoding="utf-8")

        branch_record = {
            **lineage,
            "target_experiment": str(target),
            "target_checkpoint": str(target / "checkpoints" / "generations" / checkpoint.name),
            "control_space_size": EXPECTED_K,
            "register_rank": EXPECTED_RANK,
            "optimizer_new_parameter_slots": new_optimizer_params,
            "rng_state_sha256": source_rng_sha,
            "rng_preserved": True,
            "old_optimizer_state_preserved": True,
            "new_r2_optimizer_state": "empty_until_first_update",
            "r2_parameters": [
                "soft_feedback.r2_router_down.weight",
                "soft_feedback.r2_router_score.weight",
                "soft_feedback.r2_router_coeff.weight",
                "soft_feedback.r2_gain",
            ],
            "pass1": "Qwen([S,prompt]) -> C1 -> R1",
            "pass2": "Qwen([S+R1,prompt]) -> C2 -> R2",
            "pass3": "Qwen([S+R1+R2,prompt]) -> answer",
            "source_lane_modified": False,
        }
        atomic_json(temp / "s_first_r2_branch.json", branch_record)

        # The cut-over is permitted to add only R2 tensors and optimizer slots.
        if float(read_json(target_checkpoint / "config.json")["soft_feedback"]["recurrent_r2_gain"]) != 0.0:
            raise RuntimeError("R2 cut-over gain is not exact zero")

        latest_state = read_json(source / "training_state.json")
        if int(latest_state.get("cycle", -1)) != cycle or Path(str(latest_state.get("latest_generation"))).resolve() != checkpoint:
            raise RuntimeError("source S-first lane advanced while branching; rerun to fork the new latest checkpoint")

        if target.exists():
            if any(target.iterdir()):
                raise RuntimeError(f"target became non-empty while branching: {target}")
            target.rmdir()
        temp.rename(target)
        emit(
            "s_first_r2_branch_committed",
            target_experiment=str(target),
            fork_cycle=cycle,
            fork_global_step=global_step,
            checkpoint=str(target / "checkpoints" / "generations" / checkpoint.name),
            source_head_sha256=source_head_sha,
            target_head_sha256=target_head_sha,
            r2_gain=0.0,
            shared_control_bank=True,
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
