#!/usr/bin/env python3
"""Fork the frozen-g2 full-head lane into a two-persistent-token S2->S1 lane.

The source architecture is the mature two-pass full-head lane:

    pass1: Qwen([S1, prompt]) -> R1 sensor
    pass2: Qwen([S1 + R1, prompt]) -> answer

This cut-over adds one persistent trainable token *before* the existing S1:

    pass1: Qwen([S2, S1, prompt]) -> R1 sensor
    pass2: Qwen([S2, S1 + R1, prompt]) -> answer

S2 is initialized from the frozen Qwen embedding for token id 220, which is
verified at cut-over to encode exactly one literal space (" ").  Prepending S2
preserves every S1-to-prompt relative offset.  This is intentionally NOT an
exact behavioral no-op: the new attention position is live immediately.

All inherited learned tensors, AdamW state, and RNG state are preserved.  The
only new learned tensor is soft_feedback.s2_base, appended to the existing
soft-feedback optimizer group so every inherited parameter keeps its old id.
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

SOURCE_DEFAULT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_v1"
)
TARGET_DEFAULT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_s2_v1"
)
SOURCE_BRANCH_FILE = "s_first_r2_full_head_branch.json"
SOURCE_BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-branch-v1"
BRANCH_FILE = "s_first_r2_full_head_s2_branch.json"
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-s2-branch-v1"
SOURCE_GENERATOR = "s_first_recurrent_r2_full_nanojev_head_joint_train_v1"
TARGET_GENERATOR = "s2_s1_r1_full_nanojev_head_joint_train_v1"
TARGET_PHASE = "frozen_qwen_full_nanojev_head_trainable_s2_s1_r1_shared_sparse_registers_plus_balanced_pairwise"
TARGET_OBJECTIVE = "joint_full_nanojev_head_training_s2_s1_r1_shared_sparse_register_bank_three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
S2_TOKEN_ID = 220
S2_TOKEN_TEXT = " "
S2_TENSOR_KEY = "soft_feedback.s2_base"
CUTOVER_REFERENCE_FILE = "s2_cutover_reference.safetensors"
EXPECTED_K = 1000
EXPECTED_RANK = 32
EXPECTED_SOURCE_SOFT_GROUP_PARAMS = 10


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


def sha256_tensor(tensor) -> str:
    value = tensor.detach().cpu().contiguous()
    digest = hashlib.sha256()
    digest.update(str(value.dtype).encode("ascii"))
    digest.update(str(tuple(value.shape)).encode("ascii"))
    digest.update(value.numpy().tobytes())
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
    if feedback.get("recurrent_r2_enabled") is not True:
        raise RuntimeError("S2 cut-over requires the full-head checkpoint to retain dormant R2 machinery")
    if cfg.get("r2_gain_trainable") is True or feedback.get("r2_gain_trainable") is True:
        raise RuntimeError("S2 cut-over requires the frozen-g2 full-head lane")
    return cfg, meta


def validate_source(source: Path) -> dict[str, Any]:
    for name in ("experiment.json", "training_config.json", "training_state.json", SOURCE_BRANCH_FILE):
        if not (source / name).is_file():
            raise RuntimeError(f"source full-head lane missing {name}: {source}")
    experiment = read_json(source / "experiment.json")
    config = read_json(source / "training_config.json")
    state = read_json(source / "training_state.json")
    full_head_branch = read_json(source / SOURCE_BRANCH_FILE)
    if full_head_branch.get("schema_version") != SOURCE_BRANCH_SCHEMA:
        raise RuntimeError("source is not the expected full-head branch")
    if experiment.get("soft_feedback_generator") != SOURCE_GENERATOR:
        raise RuntimeError("source experiment generator is not the expected full-head generator")
    if config.get("soft_feedback_generator") != SOURCE_GENERATOR:
        raise RuntimeError("source training_config generator is not the expected full-head generator")
    if config.get("decision_head_frozen") is not False:
        raise RuntimeError("source decision head is not trainable")
    if bool(config.get("r2_gain_trainable", False)):
        raise RuntimeError("source has released R2 gain; S2 lane starts only from frozen-g2 full-head")
    if not bool(config.get("r2_third_pass_bypassed_when_gain_frozen", False)):
        raise RuntimeError("source does not declare the third pass bypassed")
    latest_value = state.get("latest_generation")
    if not latest_value:
        raise RuntimeError("source full-head lane has no committed latest_generation")
    latest = Path(str(latest_value)).expanduser().resolve(strict=True)
    latest_cfg, latest_meta = require_checkpoint(latest)
    latest_cycle = int(state.get("cycle", -1))
    if int(latest_cfg.get("main_computer_cycle", latest_meta.get("cycle", -2))) != latest_cycle:
        raise RuntimeError("source latest checkpoint cycle disagrees with training_state.json")
    return {
        "experiment": experiment,
        "config": config,
        "state": state,
        "full_head_branch": full_head_branch,
        "latest": latest,
        "latest_cycle": latest_cycle,
    }


def resolve_selected_checkpoint(source: Path, snapshot: dict[str, Any], source_cycle: int | None):
    if source_cycle is None:
        checkpoint = snapshot["latest"]
    else:
        if source_cycle < 0:
            raise RuntimeError("--source-cycle must be >= 0")
        checkpoint = (source / "checkpoints" / "generations" / f"cycle-{source_cycle:06d}").resolve()
        if not checkpoint.is_dir():
            raise RuntimeError(f"requested retained full-head generation does not exist: {checkpoint}")
    cfg, meta = require_checkpoint(checkpoint)
    cycle = int(cfg.get("main_computer_cycle", meta.get("cycle", -1)))
    if source_cycle is not None and cycle != source_cycle:
        raise RuntimeError(f"requested cycle {source_cycle} resolved to checkpoint cycle {cycle}")
    return checkpoint, cfg, meta


def load_native_s2_seed(experiment: dict[str, Any]):
    """Load exactly Qwen's native token-220 embedding from the pinned local revision."""
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("S2 cut-over requires torch+transformers in the NanoJev venv") from exc

    model_name = str(experiment.get("model") or "")
    revision = str(experiment.get("resolved_model_revision") or experiment.get("requested_revision") or "")
    if not model_name or not revision:
        raise RuntimeError("source experiment does not identify the pinned Qwen model/revision")

    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        revision=revision,
        local_files_only=True,
        trust_remote_code=False,
    )
    encoded = tokenizer.encode(S2_TOKEN_TEXT, add_special_tokens=False)
    if encoded != [S2_TOKEN_ID]:
        raise RuntimeError(
            f"pinned tokenizer no longer maps {S2_TOKEN_TEXT!r} to [{S2_TOKEN_ID}]: got {encoded}"
        )
    decoded = tokenizer.decode(
        [S2_TOKEN_ID],
        skip_special_tokens=False,
        clean_up_tokenization_spaces=False,
    )
    if decoded != S2_TOKEN_TEXT:
        raise RuntimeError(
            f"pinned tokenizer token {S2_TOKEN_ID} decodes to {decoded!r}, expected {S2_TOKEN_TEXT!r}"
        )

    backbone = AutoModel.from_pretrained(
        model_name,
        revision=revision,
        local_files_only=True,
        trust_remote_code=False,
    )
    try:
        table = backbone.get_input_embeddings().weight
        if S2_TOKEN_ID >= int(table.shape[0]):
            raise RuntimeError(f"token id {S2_TOKEN_ID} exceeds embedding vocabulary {table.shape[0]}")
        seed = table[S2_TOKEN_ID].detach().cpu().float().contiguous().clone()
    finally:
        del backbone
    if seed.ndim != 1:
        raise RuntimeError(f"Qwen token embedding must be a vector, got {tuple(seed.shape)}")
    if not torch.isfinite(seed).all():
        raise RuntimeError("Qwen token-220 embedding contains non-finite values")
    return seed, {
        "model": model_name,
        "revision": revision,
        "token_id": S2_TOKEN_ID,
        "token_text": S2_TOKEN_TEXT,
        "seed_sha256": sha256_tensor(seed),
        "hidden_size": int(seed.numel()),
        "seed_rms": float(torch.sqrt(seed.square().mean() + 1e-30).item()),
    }


def effective_s1_from_weights(weights):
    import torch

    raw = weights["soft_feedback.base"].float().reshape(1, -1)
    direction = torch.tanh(raw)
    rms = torch.sqrt(direction.square().mean(dim=-1, keepdim=True) + 1e-8)
    normalized = direction / rms
    embedding_rms = weights["soft_feedback.embedding_rms"].float().reshape(1, 1)
    strength = torch.sigmoid(weights["soft_feedback.strength_logit"].float().reshape(1, 1))
    return (normalized * embedding_rms * strength).reshape(-1).contiguous()


def inspect_and_expand_head(head_path: Path, s2_seed) -> dict[str, Any]:
    try:
        import torch
        from safetensors import safe_open
        from safetensors.torch import load_file, save_file
    except ImportError as exc:
        raise RuntimeError("S2 cut-over requires torch+safetensors; run with the NanoJev venv") from exc

    weights = load_file(str(head_path), device="cpu")
    required = {
        "soft_feedback.base",
        "soft_feedback.embedding_rms",
        "soft_feedback.strength_logit",
        "soft_feedback.control_bank",
        "soft_feedback.router_down.weight",
        "soft_feedback.router_score.weight",
        "soft_feedback.router_coeff.weight",
        "soft_feedback.r2_router_down.weight",
        "soft_feedback.r2_router_score.weight",
        "soft_feedback.r2_router_coeff.weight",
        "soft_feedback.r2_gain",
    }
    missing = sorted(required - set(weights))
    if missing:
        raise RuntimeError(f"selected full-head checkpoint lacks tensors: {missing}")
    if S2_TENSOR_KEY in weights:
        raise RuntimeError("selected checkpoint already contains S2")
    if int(torch.count_nonzero(weights["soft_feedback.r2_gain"]).item()) != 0:
        raise RuntimeError("selected source generation does not have exact-zero frozen g2")
    hidden = int(weights["soft_feedback.base"].numel())
    if int(s2_seed.numel()) != hidden:
        raise RuntimeError(f"S2 seed width {s2_seed.numel()} does not match NanoJev hidden width {hidden}")
    down = weights["soft_feedback.router_down.weight"]
    if tuple(down.shape) != (EXPECTED_RANK, hidden):
        raise RuntimeError(f"unexpected R1 router-down shape: {tuple(down.shape)}")

    source_sha = sha256_file(head_path)
    with safe_open(str(head_path), framework="pt", device="cpu") as handle:
        metadata = handle.metadata()
    expanded = {key: value.detach().cpu().contiguous().clone() for key, value in weights.items()}
    expected_s2 = s2_seed.to(dtype=weights["soft_feedback.base"].dtype).contiguous().clone()
    expanded[S2_TENSOR_KEY] = expected_s2
    tmp = head_path.with_name(head_path.name + ".tmp")
    save_file(expanded, str(tmp), metadata=metadata)
    os.replace(tmp, head_path)

    check = load_file(str(head_path), device="cpu")
    for key, value in weights.items():
        if not torch.equal(value, check[key]):
            raise RuntimeError(f"S2 cut-over changed inherited tensor: {key}")
    if not torch.equal(check[S2_TENSOR_KEY], expected_s2):
        raise RuntimeError("new S2 tensor does not equal the dtype-cast Qwen token-220 embedding")
    decision_keys = sorted(key for key in weights if not key.startswith("soft_feedback."))
    return {
        "source_head_sha256": source_sha,
        "target_head_sha256": sha256_file(head_path),
        "decision_head_parameter_tensors": len(decision_keys),
        "s2_shape": [hidden],
        "s2_seed_sha256": sha256_tensor(check[S2_TENSOR_KEY].float()),
        "inherited_learned_tensors_changed": False,
        "s1_raw_at_cutover": weights["soft_feedback.base"].float().contiguous().clone(),
        "s1_effective_at_cutover": effective_s1_from_weights(weights),
        "s2_at_cutover": check[S2_TENSOR_KEY].float().contiguous().clone(),
    }


def write_cutover_reference(path: Path, head_info: dict[str, Any]) -> str:
    from safetensors.torch import save_file

    tensors = {
        "s1_raw_at_cutover": head_info["s1_raw_at_cutover"],
        "s1_effective_at_cutover": head_info["s1_effective_at_cutover"],
        "s2_seed": head_info["s2_at_cutover"],
    }
    save_file(tensors, str(path))
    return sha256_file(path)


def expand_optimizer(optimizer_path: Path) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("S2 cut-over requires torch; run with the NanoJev venv") from exc

    source_sha = sha256_file(optimizer_path)
    payload = torch.load(optimizer_path, map_location="cpu", weights_only=False)
    groups = payload.get("param_groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list) or len(groups) != 2:
        raise RuntimeError("expected the full-head optimizer with two parameter groups")
    soft = dict(groups[0])
    head = dict(groups[1])
    soft_ids = list(soft.get("params", []))
    if len(soft_ids) != EXPECTED_SOURCE_SOFT_GROUP_PARAMS:
        raise RuntimeError(
            f"expected {EXPECTED_SOURCE_SOFT_GROUP_PARAMS} source soft-feedback parameters, found {len(soft_ids)}"
        )
    all_ids = [int(v) for group in groups for v in group.get("params", [])]
    all_ids += [int(v) for v in payload.get("state", {}).keys()]
    new_id = max(all_ids, default=-1) + 1
    # Source order is dynamic R1/R2 (8), S1/base (1), strength (1).
    # S2 trainer appends s2_base to static_parameters(), so preserve all ten ids
    # and append exactly one new optimizer slot.
    soft["params"] = soft_ids + [new_id]
    payload["param_groups"] = [soft, head]
    tmp = optimizer_path.with_name(optimizer_path.name + ".tmp")
    torch.save(payload, tmp)
    os.replace(tmp, optimizer_path)
    return {
        "source_sha256": source_sha,
        "target_sha256": sha256_file(optimizer_path),
        "source_soft_parameter_slots": len(soft_ids),
        "target_soft_parameter_slots": len(soft["params"]),
        "new_s2_optimizer_id": int(new_id),
        "new_s2_optimizer_state": "absent_until_first_update",
        "decision_head_parameter_slots": len(head.get("params", [])),
        "inherited_parameter_ids_preserved": soft["params"][:-1] == soft_ids,
    }


def filtered_history(path: Path, fork_cycle: int) -> str:
    if not path.is_file():
        return ""
    kept: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid history JSONL at {path}:{lineno}: {exc}") from exc
            if not isinstance(row, dict) or row.get("cycle") is None:
                raise RuntimeError(f"history row has no cycle at {path}:{lineno}")
            if int(row["cycle"]) <= fork_cycle:
                kept.append(json.dumps(row, ensure_ascii=False, allow_nan=False))
    return "".join(line + "\n" for line in kept)


def state_from_selected(source_state: dict[str, Any], cfg: dict[str, Any], meta: dict[str, Any], *, selected_is_latest: bool):
    state = dict(source_state)
    cycle = int(cfg.get("main_computer_cycle", meta.get("cycle", -1)))
    global_step = int(cfg.get("main_computer_global_step", meta.get("global_step", -1)))
    state["cycle"] = cycle
    state["global_step"] = global_step
    if not selected_is_latest:
        for key in (
            "legacy_source_cursor", "mutation_source_cursor", "ast_source_cursor",
            "consensus_source_cursor", "triad_source_cursor", "consensus_label_cursor",
            "consensus_orbit_kind_cursor",
        ):
            if key in state:
                state[key] = 0
        if isinstance(state.get("mix_credits"), dict):
            state["mix_credits"] = {key: 0.0 for key in state["mix_credits"]}
    return state


def branch(source: Path, target: Path, *, source_cycle: int | None, dry_run: bool) -> None:
    source = source.expanduser().resolve(strict=True)
    target = target.expanduser().resolve()
    if source == target:
        raise RuntimeError("source and target experiment directories must differ")
    snapshot = validate_source(source)
    checkpoint, cp_cfg, cp_meta = resolve_selected_checkpoint(source, snapshot, source_cycle)
    cycle = int(cp_cfg.get("main_computer_cycle", cp_meta.get("cycle", -1)))
    global_step = int(cp_cfg.get("main_computer_global_step", cp_meta.get("global_step", -1)))
    selected_is_latest = checkpoint == snapshot["latest"]

    if target.exists() and any(target.iterdir()):
        marker = target / BRANCH_FILE
        if marker.is_file():
            existing = read_json(marker)
            emit(
                "s2_branch_already_exists",
                target=str(target),
                source=existing.get("source_experiment"),
                fork_cycle=existing.get("fork_cycle"),
            )
            return
        raise RuntimeError(f"target branch directory is non-empty: {target}")

    emit(
        "s2_branch_plan",
        source_experiment=str(source),
        source_checkpoint=str(checkpoint),
        source_cycle_requested=source_cycle,
        source_latest_cycle=snapshot["latest_cycle"],
        fork_cycle=cycle,
        fork_global_step=global_step,
        selected_is_latest=selected_is_latest,
        target_experiment=str(target),
        architecture_change="prepend live persistent S2 before existing S1 on both Qwen passes",
        prefix_pass1="[S2,S1,prompt]",
        prefix_pass2="[S2,S1+R1,prompt]",
        s2_seed_token_id=S2_TOKEN_ID,
        s2_seed_token_text=S2_TOKEN_TEXT,
        s1_prompt_relative_positions_preserved=True,
        behavioral_noop_at_cutover=False,
        qwen_frozen=True,
        r2_gain_frozen_zero=True,
        third_pass_bypassed=True,
        dry_run=dry_run,
    )
    if dry_run:
        return

    s2_seed, seed_info = load_native_s2_seed(snapshot["experiment"])

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
            "orbit_consistency_training.json", "orbit_supervision_training.json",
            "s_first_branch.json", "s_first_r2_branch.json", SOURCE_BRANCH_FILE,
        ):
            src = source / filename
            if src.is_file():
                shutil.copy2(src, temp / filename)

        target_cp = temp / "checkpoints" / "generations" / checkpoint.name
        shutil.copytree(checkpoint, target_cp)
        source_rng_sha = sha256_file(checkpoint / "rng_state.pt")
        if sha256_file(target_cp / "rng_state.pt") != source_rng_sha:
            raise RuntimeError("copied RNG state differs from source")

        head_info = inspect_and_expand_head(target_cp / "head.safetensors", s2_seed)
        reference_sha = write_cutover_reference(temp / CUTOVER_REFERENCE_FILE, head_info)
        optimizer_info = expand_optimizer(target_cp / "optimizer.pt")

        lineage = {
            "schema_version": BRANCH_SCHEMA,
            "source_experiment": str(source),
            "source_checkpoint": str(checkpoint),
            "target_experiment": str(target),
            "target_checkpoint": str(target / "checkpoints" / "generations" / checkpoint.name),
            "source_cycle_requested": source_cycle,
            "source_latest_cycle_at_branch": snapshot["latest_cycle"],
            "selected_is_latest": selected_is_latest,
            "fork_cycle": cycle,
            "fork_global_step": global_step,
            "source_head_sha256": head_info["source_head_sha256"],
            "target_head_sha256": head_info["target_head_sha256"],
            "inherited_learned_tensors_changed": False,
            "new_parameter": S2_TENSOR_KEY,
            "new_parameter_shape": head_info["s2_shape"],
            "new_parameter_initialization": "exact frozen-Qwen token embedding",
            "s2_seed": seed_info,
            "s2_seed_sha256_after_dtype_cast": head_info["s2_seed_sha256"],
            "cutover_reference_file": str(target / CUTOVER_REFERENCE_FILE),
            "cutover_reference_sha256": reference_sha,
            "prefix_order_pass1": ["S2", "S1", "prompt"],
            "prefix_order_pass2": ["S2", "S1_plus_R1", "prompt"],
            "s1_prompt_relative_positions_preserved": True,
            "behavioral_noop_at_cutover": False,
            "behavioral_change_reason": "live extra causal attention position precedes S1",
            "qwen_frozen": True,
            "r2_gain_frozen_zero": True,
            "third_qwen_pass_bypassed": True,
            "r2_release_supported_by_this_lane": False,
            "optimizer": optimizer_info,
            "rng_state_sha256": source_rng_sha,
            "rng_preserved": True,
            "source_lane_modified": False,
            "nonlatest_cursor_policy": None if selected_is_latest else "reset_task_cursors_and_mix_credits_to_zero",
        }

        experiment = dict(snapshot["experiment"])
        experiment["parent_full_head_branch_lineage"] = experiment.get("branch_lineage")
        experiment["branch_lineage"] = lineage
        experiment["soft_feedback_generator"] = TARGET_GENERATOR
        experiment["soft_feedback_token_count"] = 2
        experiment["s2_prefix"] = {
            "enabled": True,
            "tensor": S2_TENSOR_KEY,
            "seed_token_id": S2_TOKEN_ID,
            "seed_token_text": S2_TOKEN_TEXT,
            "seed_source": "frozen_Qwen_input_embedding",
            "prefix_order_pass1": "[S2,S1,prompt]",
            "prefix_order_pass2": "[S2,S1+R1,prompt]",
            "s1_prompt_relative_positions_preserved": True,
            "trainable": True,
        }
        experiment["soft_feedback_first_pass"] = "Qwen([S2,S1,prompt]) -> R1 router"
        experiment["soft_feedback_second_pass"] = "Qwen([S2,S1+R1,prompt]) -> trainable decision head"
        experiment["soft_feedback_backbone_reads_dynamic"] = 2
        experiment["initialization"] = (
            f"full-head cycle-{cycle:06d}; inherited NanoJev tensors/optimizer/RNG preserved; "
            f"new trainable S2 initialized from frozen Qwen token {S2_TOKEN_ID} ({S2_TOKEN_TEXT!r}); "
            "S2 prepended before S1 so old S1-to-prompt relative offsets are preserved"
        )
        experiment["experiment_sha256"] = sha256_json(
            {k: v for k, v in experiment.items() if k != "experiment_sha256"}
        )

        config = dict(snapshot["config"])
        config["soft_feedback_generator"] = TARGET_GENERATOR
        config["soft_feedback_token_count"] = 2
        config["r2_gain_trainable"] = False
        config["r2_third_pass_bypassed_when_gain_frozen"] = True

        target_cp_cfg = read_json(target_cp / "config.json")
        target_cp_cfg["main_computer_experiment_sha256"] = experiment["experiment_sha256"]
        target_cp_cfg["training_objective"] = TARGET_OBJECTIVE
        feedback = dict(target_cp_cfg.get("soft_feedback") or {})
        feedback.update({
            "generator": TARGET_GENERATOR,
            "token_count": 2,
            "s2_enabled": True,
            "s2_tensor": S2_TENSOR_KEY,
            "s2_seed_token_id": S2_TOKEN_ID,
            "s2_seed_token_text": S2_TOKEN_TEXT,
            "prefix_order_pass1": "[S2,S1,prompt]",
            "prefix_order_pass2": "[S2,S1+R1,prompt]",
            "s1_prompt_relative_positions_preserved": True,
            "behavioral_noop_at_cutover": False,
            "r2_gain_trainable": False,
            "r2_third_pass_bypassed": True,
        })
        target_cp_cfg["soft_feedback"] = feedback
        atomic_json(target_cp / "config.json", target_cp_cfg)

        state = state_from_selected(snapshot["state"], cp_cfg, cp_meta, selected_is_latest=selected_is_latest)
        state["experiment_sha256"] = experiment["experiment_sha256"]
        state["latest_generation"] = str(target / "checkpoints" / "generations" / checkpoint.name)
        state["phase"] = TARGET_PHASE
        state["training_objective"] = TARGET_OBJECTIVE
        state["branch_lineage"] = lineage
        state["status"] = "branched_s2_ready"
        state["latest_head_sha256"] = head_info["target_head_sha256"]
        if isinstance(state.get("last_result"), dict):
            last_result = dict(state["last_result"])
            if last_result.get("checkpoint"):
                last_result["checkpoint"] = str(target / "checkpoints" / "generations" / checkpoint.name)
            state["last_result"] = last_result

        atomic_json(temp / "experiment.json", experiment)
        atomic_json(temp / "training_config.json", config)
        atomic_json(temp / "training_state.json", state)
        atomic_json(temp / BRANCH_FILE, lineage)
        (temp / "history.jsonl").write_text(filtered_history(source / "history.jsonl", cycle), encoding="utf-8")

        if selected_is_latest:
            latest_state = read_json(source / "training_state.json")
            if int(latest_state.get("cycle", -1)) != cycle or Path(str(latest_state.get("latest_generation"))).resolve() != checkpoint:
                raise RuntimeError("source full-head lane advanced while branching latest; rerun")

        if target.exists():
            if any(target.iterdir()):
                raise RuntimeError(f"target became non-empty while branching: {target}")
            target.rmdir()
        temp.rename(target)
        emit(
            "s2_branch_committed",
            target_experiment=str(target),
            fork_cycle=cycle,
            fork_global_step=global_step,
            selected_is_latest=selected_is_latest,
            checkpoint=str(target / "checkpoints" / "generations" / checkpoint.name),
            head_sha256=head_info["target_head_sha256"],
            inherited_learned_tensors_changed=False,
            s2_seed_token_id=S2_TOKEN_ID,
            s2_seed_token_text=S2_TOKEN_TEXT,
            s2_seed_sha256=head_info["s2_seed_sha256"],
            cutover_reference_sha256=reference_sha,
            optimizer_new_parameter_slots=1,
            behavioral_noop_at_cutover=False,
            s1_prompt_relative_positions_preserved=True,
            qwen_frozen=True,
            r2_gain_frozen_zero=True,
            third_pass_bypassed=True,
        )
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-experiment-dir", type=Path, default=SOURCE_DEFAULT)
    p.add_argument("--target-experiment-dir", type=Path, default=TARGET_DEFAULT)
    p.add_argument("--source-cycle", type=int, default=None, help="retained source cycle; default is latest committed")
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> None:
    args = parser().parse_args()
    branch(
        args.source_experiment_dir,
        args.target_experiment_dir,
        source_cycle=args.source_cycle,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
