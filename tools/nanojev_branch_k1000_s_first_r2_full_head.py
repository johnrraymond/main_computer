#!/usr/bin/env python3
"""Fork a committed pre-R2 S-first generation into full-head joint training.

The source is the K=1000 S-first lane, not the later R2 lane.  Latest committed
S-first generation is the default; --source-cycle selects another retained
S-first generation explicitly.

At cut-over the learned S-first checkpoint is preserved, a dormant R2 controller
is added by cloning the learned R1 router, and g2 is created at exact zero and
frozen by the trainer.  The complete existing NanoJev head (including S,
strength, shared bank, R1 router, and decision head) is made trainable while
Qwen remains frozen.  The third Qwen pass is bypassed until a later explicit
--train-r2-gain training run releases recurrence.

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
TARGET_DEFAULT = Path(r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_v1")
SOURCE_BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-branch-v1"
DORMANT_R2_BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-branch-v1"
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-branch-v1"
SOURCE_GENERATOR = "frozen_static_token_prefixed_sensor_plus_self_routing_sparse_overcomplete_shared_control_dictionary_v1"
FULL_GENERATOR = "s_first_recurrent_r2_full_nanojev_head_joint_train_v1"
FULL_PHASE = "frozen_qwen_full_nanojev_head_trainable_s_first_recurrent_r2_shared_sparse_registers_plus_balanced_pairwise"
FULL_OBJECTIVE = "joint_full_nanojev_head_training_s_first_r1_plus_recurrent_r2_shared_sparse_register_bank_three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
EXPECTED_K = 1000
EXPECTED_RANK = 32
EXPECTED_SOURCE_OPTIMIZER_PARAMS = 4


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
            raise RuntimeError(f"checkpoint missing {name}: {checkpoint}")
    cfg = read_json(checkpoint / "config.json")
    meta = read_json(checkpoint / "meta.json")
    feedback = cfg.get("soft_feedback")
    if not isinstance(feedback, dict):
        raise RuntimeError(f"checkpoint has no soft_feedback contract: {checkpoint}")
    if int(feedback.get("control_space_size", -1)) != EXPECTED_K:
        raise RuntimeError(f"checkpoint is not K={EXPECTED_K}: {checkpoint}")
    if int(feedback.get("register_rank", -1)) != EXPECTED_RANK:
        raise RuntimeError(f"checkpoint is not rank={EXPECTED_RANK}: {checkpoint}")
    if feedback.get("recurrent_r2_enabled") is True:
        raise RuntimeError(f"checkpoint already contains active R2 architecture; expected pre-R2 S-first: {checkpoint}")
    return cfg, meta


def validate_source(source: Path) -> dict[str, Any]:
    for name in ("experiment.json", "training_config.json", "training_state.json", "s_first_branch.json"):
        if not (source / name).is_file():
            raise RuntimeError(f"source S-first lane missing {name}: {source}")
    experiment = read_json(source / "experiment.json")
    config = read_json(source / "training_config.json")
    state = read_json(source / "training_state.json")
    s_first_branch = read_json(source / "s_first_branch.json")
    if s_first_branch.get("schema_version") != SOURCE_BRANCH_SCHEMA:
        raise RuntimeError(f"source is not the expected S-first branch: {source}")
    if experiment.get("soft_feedback_generator") != SOURCE_GENERATOR:
        raise RuntimeError("source experiment generator is not the expected S-first generator")
    if config.get("soft_feedback_generator") != SOURCE_GENERATOR:
        raise RuntimeError("source training_config generator is not the expected S-first generator")
    if (int(config.get("control_space_size", -1)), int(config.get("register_rank", -1))) != (EXPECTED_K, EXPECTED_RANK):
        raise RuntimeError(f"source must remain K={EXPECTED_K}, rank={EXPECTED_RANK}")
    latest_value = state.get("latest_generation")
    if not latest_value:
        raise RuntimeError("source S-first lane has no committed latest_generation")
    latest = Path(str(latest_value)).expanduser().resolve(strict=True)
    latest_cfg, latest_meta = require_checkpoint(latest)
    latest_cycle = int(state.get("cycle", -1))
    if int(latest_cfg.get("main_computer_cycle", latest_meta.get("cycle", -2))) != latest_cycle:
        raise RuntimeError("source latest checkpoint cycle disagrees with training_state.json")
    return {
        "experiment": experiment,
        "config": config,
        "state": state,
        "s_first_branch": s_first_branch,
        "latest": latest,
        "latest_config": latest_cfg,
        "latest_meta": latest_meta,
        "latest_cycle": latest_cycle,
    }


def resolve_selected_checkpoint(source: Path, snapshot: dict[str, Any], source_cycle: int | None) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if source_cycle is None:
        checkpoint = snapshot["latest"]
    else:
        if source_cycle < 0:
            raise RuntimeError("--source-cycle must be >= 0")
        checkpoint = (source / "checkpoints" / "generations" / f"cycle-{source_cycle:06d}").resolve()
        if not checkpoint.is_dir():
            raise RuntimeError(f"requested retained S-first generation does not exist: {checkpoint}")
    cfg, meta = require_checkpoint(checkpoint)
    cycle = int(cfg.get("main_computer_cycle", meta.get("cycle", -1)))
    if source_cycle is not None and cycle != source_cycle:
        raise RuntimeError(f"requested cycle {source_cycle} resolved to checkpoint cycle {cycle}")
    return checkpoint, cfg, meta


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


def inspect_source_head(head_path: Path) -> tuple[int, list[str]]:
    try:
        from safetensors.torch import load_file
    except ImportError as exc:
        raise RuntimeError("cut-over requires safetensors; run with the NanoJev venv") from exc
    tensors = load_file(str(head_path), device="cpu")
    required = {
        "soft_feedback.base",
        "soft_feedback.strength_logit",
        "soft_feedback.control_bank",
        "soft_feedback.router_down.weight",
        "soft_feedback.router_score.weight",
        "soft_feedback.router_coeff.weight",
    }
    missing = sorted(required - set(tensors))
    if missing:
        raise RuntimeError(f"selected S-first head lacks expected tensors: {missing}")
    forbidden = sorted(key for key in tensors if key.startswith("soft_feedback.r2_"))
    if forbidden:
        raise RuntimeError(f"selected S-first head unexpectedly already contains R2 tensors: {forbidden}")
    decision_keys = sorted(key for key in tensors if not key.startswith("soft_feedback."))
    if not decision_keys:
        raise RuntimeError("selected head has no inherited decision-head tensors")
    return len(decision_keys), decision_keys


def expand_head_with_dormant_r2(head_path: Path) -> dict[str, Any]:
    try:
        import torch
        from safetensors import safe_open
        from safetensors.torch import load_file, save_file
    except ImportError as exc:
        raise RuntimeError("cut-over requires torch+safetensors; run with the NanoJev venv") from exc

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
            raise RuntimeError(f"source head already contains {dst}")
    if "soft_feedback.r2_gain" in weights:
        raise RuntimeError("source head already contains soft_feedback.r2_gain")

    metadata = None
    with safe_open(str(head_path), framework="pt", device="cpu") as handle:
        metadata = handle.metadata()
    expanded = {key: value.detach().cpu().contiguous().clone() for key, value in weights.items()}
    for dst, src in mapping.items():
        expanded[dst] = weights[src].detach().cpu().contiguous().clone()
    expanded["soft_feedback.r2_gain"] = torch.zeros(
        1, dtype=weights["soft_feedback.router_coeff.weight"].dtype
    )
    tmp = head_path.with_name(head_path.name + ".tmp")
    save_file(expanded, str(tmp), metadata=metadata)
    os.replace(tmp, head_path)

    check = load_file(str(head_path), device="cpu")
    for key, value in weights.items():
        if not torch.equal(value, check[key]):
            raise RuntimeError(f"cut-over changed inherited learned tensor: {key}")
    for dst, src in mapping.items():
        if not torch.equal(check[dst], check[src]):
            raise RuntimeError(f"dormant R2 router was not cloned exactly from R1: {dst}")
    if int(torch.count_nonzero(check["soft_feedback.r2_gain"]).item()) != 0:
        raise RuntimeError("dormant R2 gain is not exact zero")
    return {
        "source_head_sha256": source_sha,
        "target_head_sha256": sha256_file(head_path),
        "r2_router_initialization": "exact_copy_of_R1_router",
        "source_r2_gain_parameter": None,
        "target_r2_gain_parameter": 0.0,
        "rezeroed": True,
        "created_from_pre_r2_source": True,
    }


def expand_optimizer_for_full_head_from_s_first(
    optimizer_path: Path, *, head_lr: float, decision_param_count: int
) -> dict[str, Any]:
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
        raise RuntimeError(f"expected one inherited S-first AdamW parameter group, found {len(groups)}")
    inherited_group = dict(groups[0])
    inherited_ids = list(inherited_group.get("params", []))
    if len(inherited_ids) != EXPECTED_SOURCE_OPTIMIZER_PARAMS:
        raise RuntimeError(
            f"expected {EXPECTED_SOURCE_OPTIMIZER_PARAMS} inherited S-first dynamic parameters "
            f"(bank/R1 down/R1 score/R1 coeff), found {len(inherited_ids)}"
        )

    all_ids = [int(v) for group in groups for v in group.get("params", [])]
    all_ids += [int(v) for v in payload.get("state", {}).keys()]
    next_id = max(all_ids, default=-1) + 1
    r2_ids = list(range(next_id, next_id + 4))
    next_id += 4
    static_ids = list(range(next_id, next_id + 2))
    next_id += 2
    decision_ids = list(range(next_id, next_id + decision_param_count))

    # Trainer parameter order is dynamic R1 (the inherited four), then R2
    # down/score/coeff/gain, then static S/strength.  Preserve old Adam moments
    # on the inherited four and let AdamW lazily create state for every new slot.
    soft_group = dict(inherited_group)
    soft_group["params"] = inherited_ids + r2_ids + static_ids
    head_group = {key: value for key, value in inherited_group.items() if key != "params"}
    head_group["params"] = decision_ids
    head_group["lr"] = float(head_lr)
    if "initial_lr" in head_group:
        head_group["initial_lr"] = float(head_lr)
    payload["param_groups"] = [soft_group, head_group]

    tmp = optimizer_path.with_name(optimizer_path.name + ".tmp")
    torch.save(payload, tmp)
    os.replace(tmp, optimizer_path)
    return {
        "source_sha256": source_sha,
        "target_sha256": sha256_file(optimizer_path),
        "preserved_r1_dynamic_parameter_slots": len(inherited_ids),
        "new_r2_parameter_slots": len(r2_ids),
        "new_static_parameter_slots": len(static_ids),
        "new_decision_head_parameter_slots": len(decision_ids),
        "soft_feedback_parameter_slots": len(inherited_ids) + len(r2_ids) + len(static_ids),
        "optimizer_group_count": 2,
        "r2_gain_optimizer_id": int(r2_ids[-1]),
        "r2_gain_optimizer_state": "absent_until_explicit_release_and_first_update",
    }


def state_from_selected(
    source_state: dict[str, Any], selected_cfg: dict[str, Any], selected_meta: dict[str, Any], *, selected_is_latest: bool
) -> dict[str, Any]:
    state = dict(source_state)
    cycle = int(selected_cfg.get("main_computer_cycle", selected_meta.get("cycle", -1)))
    global_step = int(selected_cfg.get("main_computer_global_step", selected_meta.get("global_step", -1)))
    state["cycle"] = cycle
    state["global_step"] = global_step
    if not selected_is_latest:
        for key in (
            "legacy_source_cursor", "mutation_source_cursor", "ast_source_cursor",
            "consensus_source_cursor", "triad_source_cursor", "consensus_label_cursor",
            "consensus_orbit_kind_cursor",
        ):
            state[key] = 0
        state["mix_credits"] = {task: 0.0 for task in ("legacy", "mutation", "ast", "consensus", "triad")}
        state["nonlatest_generation_cursor_policy"] = "reset_task_cursors_and_mix_credits_to_zero"
    metric_map = {
        "last_legacy_probability_separation": "legacy_dev_probability_separation",
        "last_legacy_mean_pair_logodds_gap": "legacy_dev_mean_pair_logodds_gap",
        "last_mutation_probability_separation": "mutation_dev_probability_separation",
        "last_mutation_mean_pair_logodds_gap": "mutation_dev_mean_pair_logodds_gap",
        "last_ast_probability_separation": "ast_dev_probability_separation",
        "last_ast_mean_pair_logodds_gap": "ast_dev_mean_pair_logodds_gap",
        "last_consensus_accuracy": "consensus_dev_accuracy",
        "last_consensus_mean_gold_margin": "consensus_dev_mean_gold_margin",
        "last_triad_relation_accuracy": "triad_dev_relation_accuracy",
        "last_triad_balanced_relation_accuracy": "triad_dev_balanced_relation_accuracy",
        "last_triad_topology_accuracy": "triad_dev_topology_accuracy",
        "last_triad_non_ambiguous_topology_accuracy": "triad_dev_non_ambiguous_topology_accuracy",
    }
    for state_key, meta_key in metric_map.items():
        if meta_key in selected_meta:
            state[state_key] = selected_meta[meta_key]
    return state


def branch(source: Path, target: Path, *, source_cycle: int | None, dry_run: bool) -> None:
    source = source.expanduser().resolve(strict=True)
    target = target.expanduser().resolve()
    if source == target:
        raise RuntimeError("source and target experiment directories must differ")
    snapshot = validate_source(source)
    checkpoint, cp_config, cp_meta = resolve_selected_checkpoint(source, snapshot, source_cycle)
    cycle = int(cp_config.get("main_computer_cycle", cp_meta.get("cycle", -1)))
    global_step = int(cp_config.get("main_computer_global_step", cp_meta.get("global_step", -1)))
    selected_is_latest = checkpoint == snapshot["latest"]
    decision_param_count, decision_keys = inspect_source_head(checkpoint / "head.safetensors")

    if target.exists() and any(target.iterdir()):
        marker = target / "s_first_r2_full_head_branch.json"
        if marker.is_file():
            existing = read_json(marker)
            emit(
                "full_head_branch_already_exists",
                target=str(target),
                source=existing.get("source_experiment"),
                fork_cycle=existing.get("fork_cycle"),
            )
            return
        raise RuntimeError(f"target branch directory is non-empty: {target}")

    emit(
        "full_head_branch_plan",
        source_experiment=str(source),
        source_lane="pre_r2_s_first",
        source_checkpoint=str(checkpoint),
        source_cycle_requested=source_cycle,
        source_latest_cycle=snapshot["latest_cycle"],
        fork_cycle=cycle,
        fork_global_step=global_step,
        selected_is_latest=selected_is_latest,
        target_experiment=str(target),
        dormant_r2_created=True,
        r2_gain=0.0,
        r2_gain_frozen_after_cutover=True,
        r2_third_pass_bypassed=True,
        qwen_frozen=True,
        nanojev_head_trainable_except_r2_gain=True,
        decision_head_parameter_tensors=decision_param_count,
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
        for filename in (
            "orbit_consistency_training.json", "orbit_supervision_training.json", "s_first_branch.json",
        ):
            src = source / filename
            if src.is_file():
                shutil.copy2(src, temp / filename)

        target_checkpoint = temp / "checkpoints" / "generations" / checkpoint.name
        shutil.copytree(checkpoint, target_checkpoint)
        source_head_sha = sha256_file(checkpoint / "head.safetensors")
        if sha256_file(target_checkpoint / "head.safetensors") != source_head_sha:
            raise RuntimeError("copied head hash differs from selected S-first generation")
        rng_sha = sha256_file(checkpoint / "rng_state.pt")
        if sha256_file(target_checkpoint / "rng_state.pt") != rng_sha:
            raise RuntimeError("copied RNG hash differs from selected S-first generation")

        expansion = expand_head_with_dormant_r2(target_checkpoint / "head.safetensors")
        target_head_sha = expansion["target_head_sha256"]
        opt_info = expand_optimizer_for_full_head_from_s_first(
            target_checkpoint / "optimizer.pt",
            head_lr=float(snapshot["config"]["head_lr"]),
            decision_param_count=decision_param_count,
        )

        parent_s_first_branch = snapshot["s_first_branch"]
        dormant_r2_lineage = {
            "schema_version": DORMANT_R2_BRANCH_SCHEMA,
            "source_experiment": str(source),
            "source_checkpoint": str(checkpoint),
            "target_experiment": str(target),
            "target_checkpoint": str(target / "checkpoints" / "generations" / checkpoint.name),
            "fork_cycle": cycle,
            "fork_global_step": global_step,
            "parent_s_first_fork_cycle": parent_s_first_branch.get("fork_cycle"),
            "source_head_sha256": source_head_sha,
            "target_head_sha256": target_head_sha,
            "control_space_size": EXPECTED_K,
            "register_rank": EXPECTED_RANK,
            "architecture_change": (
                "add dormant R2 router cloned exactly from R1; reuse shared K=1000 bank; "
                "create exact-zero g2; third pass remains bypassed while g2 is frozen"
            ),
            "r2_parameters": [
                "soft_feedback.r2_router_down.weight",
                "soft_feedback.r2_router_score.weight",
                "soft_feedback.r2_router_coeff.weight",
                "soft_feedback.r2_gain",
            ],
            "pass1": "Qwen([S,prompt]) -> C1 -> R1",
            "pass2": "Qwen([S+R1,prompt]) -> trainable head answer while g2 frozen",
            "pass3": "dormant until explicit --train-r2-gain release",
            "source_lane_modified": False,
        }

        lineage = {
            "schema_version": BRANCH_SCHEMA,
            "source_experiment": str(source),
            "source_lane": "pre_r2_s_first",
            "source_checkpoint": str(checkpoint),
            "source_cycle_requested": source_cycle,
            "source_latest_cycle_at_branch": snapshot["latest_cycle"],
            "selected_is_latest": selected_is_latest,
            "fork_cycle": cycle,
            "fork_global_step": global_step,
            "source_head_sha256": source_head_sha,
            "target_head_sha256": target_head_sha,
            "inherited_learned_tensors_changed": False,
            "dormant_r2_created_at_cutover": True,
            "r2_gain_cutover": expansion,
            "architecture_change": (
                "joint NanoJev-head training directly from pre-R2 S-first checkpoint; "
                "dormant R2 added with g2=0/frozen; Qwen remains frozen"
            ),
            "trainable_after_cutover": [
                "decision_head", "soft_feedback.base(S)", "soft_feedback.strength_logit",
                "shared_control_bank", "R1_router", "R2_router",
            ],
            "frozen_after_cutover": ["Qwen", "soft_feedback.r2_gain"],
            "r2_gain_optional_release_flag": "--train-r2-gain",
        }

        experiment = dict(snapshot["experiment"])
        target_consensus_probe = temp / "probes" / "consensus_dev.jsonl"
        if target_consensus_probe.is_file():
            experiment["consensus_dev_probe"] = str(target / "probes" / "consensus_dev.jsonl")
        experiment["parent_s_first_branch_lineage"] = experiment.get("branch_lineage")
        experiment["dormant_r2_branch_lineage"] = dormant_r2_lineage
        experiment["branch_lineage"] = lineage
        experiment["soft_feedback_generator"] = FULL_GENERATOR
        experiment["decision_head_frozen"] = False
        experiment["entire_nanojev_head_trainable"] = False
        experiment["nanojev_head_trainable_except_r2_gain"] = True
        experiment["r2_gain_trainable"] = False
        experiment["r2_gain_rezeroed_at_cutover"] = True
        experiment["soft_feedback_static_base_frozen"] = False
        experiment["soft_feedback_static_strength_frozen"] = False
        experiment["soft_feedback_first_pass"] = "Qwen([S,prompt]) -> R1 router"
        experiment["soft_feedback_second_pass"] = "Qwen([S+R1,prompt]) -> trainable decision head"
        experiment["soft_feedback_third_pass"] = "bypassed while frozen g2=0; available after explicit release"
        experiment["soft_feedback_backbone_reads_dynamic"] = 2
        experiment["recurrent_r2_enabled"] = True
        experiment["recurrent_r2_shared_control_bank"] = True
        experiment["recurrent_r2_router_initialization"] = "copy_R1_router"
        experiment["recurrent_r2_gain_initialization"] = 0.0
        experiment["soft_feedback_gradient_path"] = "final_loss_jointly_updates_nanojev_head_except_frozen_r2_gain_through_frozen_qwen"
        experiment["initialization"] = (
            f"pre-R2 S-first cycle-{cycle:06d}; learned S/R1/bank/head inherited unchanged; "
            "dormant R2 cloned from R1 with g2=0 frozen; all other NanoJev-head parameters trainable; Qwen frozen"
        )
        experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})

        config = dict(snapshot["config"])
        config["soft_feedback_generator"] = FULL_GENERATOR
        config["decision_head_frozen"] = False
        config["entire_nanojev_head_trainable"] = False
        config["nanojev_head_trainable_except_r2_gain"] = True
        config["r2_gain_trainable"] = False
        config["r2_gain_rezeroed_at_cutover"] = True
        config["r2_third_pass_bypassed_when_gain_frozen"] = True
        config["soft_feedback_static_base_frozen"] = False
        config["soft_feedback_static_strength_frozen"] = False

        target_cp_cfg = read_json(target_checkpoint / "config.json")
        target_cp_cfg["main_computer_experiment_sha256"] = experiment["experiment_sha256"]
        target_cp_cfg["decision_head_frozen"] = False
        target_cp_cfg["entire_nanojev_head_trainable"] = False
        target_cp_cfg["nanojev_head_trainable_except_r2_gain"] = True
        target_cp_cfg["r2_gain_trainable"] = False
        target_cp_cfg["r2_third_pass_bypassed_when_gain_frozen"] = True
        feedback = dict(target_cp_cfg.get("soft_feedback") or {})
        feedback.update({
            "generator": FULL_GENERATOR,
            "backbone_reads_dynamic": 2,
            "static_base_frozen": False,
            "static_strength_frozen": False,
            "entire_nanojev_head_trainable": False,
            "nanojev_head_trainable_except_r2_gain": True,
            "r2_gain_trainable": False,
            "r2_gain_rezeroed_at_cutover": True,
            "r2_third_pass_bypassed_when_gain_frozen": True,
            "recurrent_r2_enabled": True,
            "recurrent_r2_shared_control_bank": True,
            "recurrent_r2_router_initialization": "copy_R1_router",
            "recurrent_r2_gain": 0.0,
            "recurrent_r2_cutover_exact_noop": True,
        })
        target_cp_cfg["soft_feedback"] = feedback
        atomic_json(target_checkpoint / "config.json", target_cp_cfg)

        state = state_from_selected(snapshot["state"], cp_config, cp_meta, selected_is_latest=selected_is_latest)
        state["experiment_sha256"] = experiment["experiment_sha256"]
        state["latest_generation"] = str(target / "checkpoints" / "generations" / checkpoint.name)
        state["phase"] = FULL_PHASE
        state["training_objective"] = FULL_OBJECTIVE
        state["branch_lineage"] = lineage
        state["status"] = "branched_full_head_ready"
        state["latest_head_sha256"] = target_head_sha
        if isinstance(state.get("last_result"), dict):
            last_result = dict(state["last_result"])
            if last_result.get("checkpoint"):
                last_result["checkpoint"] = str(target / "checkpoints" / "generations" / checkpoint.name)
            state["last_result"] = last_result

        atomic_json(temp / "experiment.json", experiment)
        atomic_json(temp / "training_config.json", config)
        atomic_json(temp / "training_state.json", state)
        (temp / "history.jsonl").write_text(filtered_history(source / "history.jsonl", cycle), encoding="utf-8")
        atomic_json(temp / "s_first_r2_branch.json", dormant_r2_lineage)

        branch_record = {
            **lineage,
            "target_experiment": str(target),
            "target_checkpoint": str(target / "checkpoints" / "generations" / checkpoint.name),
            "control_space_size": EXPECTED_K,
            "register_rank": EXPECTED_RANK,
            "optimizer": opt_info,
            "rng_state_sha256": rng_sha,
            "rng_preserved": True,
            "decision_head_parameter_keys": decision_keys,
            "nonlatest_cursor_policy": None if selected_is_latest else "reset_task_cursors_and_mix_credits_to_zero",
            "source_lane_modified": False,
        }
        atomic_json(temp / "s_first_r2_full_head_branch.json", branch_record)

        if sha256_file(target_checkpoint / "head.safetensors") != target_head_sha:
            raise RuntimeError("cut-over head hash changed after dormant R2 creation")

        if selected_is_latest:
            latest_state = read_json(source / "training_state.json")
            if int(latest_state.get("cycle", -1)) != cycle or Path(str(latest_state.get("latest_generation"))).resolve() != checkpoint:
                raise RuntimeError("source S-first lane advanced while branching latest; rerun to fork the new latest checkpoint")

        if target.exists():
            if any(target.iterdir()):
                raise RuntimeError(f"target became non-empty while branching: {target}")
            target.rmdir()
        temp.rename(target)
        emit(
            "full_head_branch_committed",
            target_experiment=str(target),
            source_lane="pre_r2_s_first",
            fork_cycle=cycle,
            fork_global_step=global_step,
            selected_is_latest=selected_is_latest,
            checkpoint=str(target / "checkpoints" / "generations" / checkpoint.name),
            source_head_sha256=source_head_sha,
            head_sha256=target_head_sha,
            inherited_learned_tensors_changed=False,
            dormant_r2_created=True,
            r2_gain_parameter=0.0,
            r2_gain_frozen=True,
            r2_third_pass_bypassed=True,
            qwen_frozen=True,
            nanojev_head_trainable_except_r2_gain=True,
            optimizer_preserved_r1_slots=opt_info["preserved_r1_dynamic_parameter_slots"],
            optimizer_new_r2_slots=opt_info["new_r2_parameter_slots"],
            optimizer_new_static_slots=opt_info["new_static_parameter_slots"],
            optimizer_new_decision_head_slots=opt_info["new_decision_head_parameter_slots"],
        )
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment-dir", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--target-experiment-dir", type=Path, default=TARGET_DEFAULT)
    parser.add_argument(
        "--source-cycle", type=int, default=None,
        help="Retained committed S-first generation to fork; default is latest committed S-first generation",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    branch(
        args.source_experiment_dir,
        args.target_experiment_dir,
        source_cycle=args.source_cycle,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
