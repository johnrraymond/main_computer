#!/usr/bin/env python3
"""Fork the mature one-token S1 full-head lane into dictionary training.

This is a curriculum cut-over only.  It adds no model parameter.  The selected source checkpoint is copied exactly at the learned
state level:

    pass1: Qwen([S1, prompt]) -> R1
    pass2: Qwen([S1 + R1, prompt]) -> answer

K remains 1000, Qwen remains frozen, g2 remains frozen at exact zero, and the
third Qwen pass remains bypassed.  The target trainer performs the curriculum
migration on first run from 5/10/34/1/50 to 5/5/10/15/25/40, with the final
40 percent assigned to the held-out-aware WordNet dictionary task.

The default source checkpoint is the healthy mature S1 cycle 120.
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
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_dictionary_v1"
)
SOURCE_CYCLE_DEFAULT = 120
SOURCE_BRANCH_FILE = "s_first_r2_full_head_branch.json"
SOURCE_BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-branch-v1"
BRANCH_FILE = "s_first_r2_full_head_dictionary_branch.json"
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-dictionary-branch-v1"
GENERATOR = "s_first_recurrent_r2_full_nanojev_head_joint_train_v1"
EXPECTED_K = 1000
EXPECTED_RANK = 32
SOURCE_CURRICULUM = {
    "legacy": 5.0,
    "mutation": 10.0,
    "ast": 34.0,
    "consensus": 1.0,
    "triad": 50.0,
}
TARGET_CURRICULUM = {
    "legacy": 20.0,
    "mutation": 5.0,
    "ast": 5.0,
    "consensus": 15.0,
    "triad": 20.0,
    "dictionary": 35.0,
}


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


def source_curriculum(config: dict[str, Any]) -> dict[str, float]:
    return {
        "legacy": float(config.get("legacy_training_percent", 0.0)),
        "mutation": float(config.get("mutation_training_percent", 0.0)),
        "ast": float(config.get("ast_training_percent", 0.0)),
        "consensus": float(config.get("consensus_training_percent", 0.0)),
        "triad": float(config.get("triad_training_percent", 0.0)),
    }


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
    if cfg.get("r2_gain_trainable") is True or feedback.get("r2_gain_trainable") is True:
        raise RuntimeError("dictionary cut-over requires frozen R2 gain")
    if not bool(cfg.get("r2_third_pass_bypassed_when_gain_frozen", True)):
        raise RuntimeError("dictionary cut-over requires the frozen-g2 third-pass bypass")
    return cfg, meta


def inspect_single_s_head(head_path: Path) -> dict[str, Any]:
    try:
        from safetensors import safe_open
    except ImportError as exc:
        raise RuntimeError("cut-over requires safetensors; run with the NanoJev venv") from exc
    with safe_open(str(head_path), framework="pt", device="cpu") as handle:
        keys = list(handle.keys())
    required = {
        "soft_feedback.base",
        "soft_feedback.strength_logit",
        "soft_feedback.control_bank",
        "soft_feedback.router_down.weight",
        "soft_feedback.router_score.weight",
        "soft_feedback.router_coeff.weight",
        "soft_feedback.r2_gain",
    }
    missing = sorted(required - set(keys))
    if missing:
        raise RuntimeError(f"selected S1 full-head checkpoint lacks tensors: {missing}")
    forbidden = sorted(key for key in keys if key == "soft_feedback.s2_base" or key.startswith("soft_feedback.s2_"))
    if forbidden:
        raise RuntimeError(f"selected checkpoint is not the one-token S1 architecture: {forbidden}")
    return {
        "tensor_count": len(keys),
        "head_sha256": sha256_file(head_path),
    }


def validate_source(source: Path) -> dict[str, Any]:
    for name in ("experiment.json", "training_config.json", "training_state.json", SOURCE_BRANCH_FILE):
        if not (source / name).is_file():
            raise RuntimeError(f"source S1 full-head lane missing {name}: {source}")
    experiment = read_json(source / "experiment.json")
    config = read_json(source / "training_config.json")
    state = read_json(source / "training_state.json")
    branch = read_json(source / SOURCE_BRANCH_FILE)
    if branch.get("schema_version") != SOURCE_BRANCH_SCHEMA:
        raise RuntimeError("source is not the expected S1 full-head branch")
    if experiment.get("soft_feedback_generator") != GENERATOR:
        raise RuntimeError("source experiment generator mismatch")
    if config.get("soft_feedback_generator") != GENERATOR:
        raise RuntimeError("source training_config generator mismatch")
    if int(config.get("soft_feedback_token_count", 1)) != 1:
        raise RuntimeError("source is not the one-token S1 architecture")
    if config.get("decision_head_frozen") is not False:
        raise RuntimeError("source decision head is not trainable")
    if bool(config.get("r2_gain_trainable", False)):
        raise RuntimeError("source has released R2 gain")
    actual_curriculum = source_curriculum(config)
    if actual_curriculum != SOURCE_CURRICULUM:
        raise RuntimeError(
            f"source curriculum is not the expected 5/10/34/1/50 baseline: {actual_curriculum}"
        )
    latest_value = state.get("latest_generation")
    if not latest_value:
        raise RuntimeError("source has no committed latest_generation")
    latest = Path(str(latest_value)).expanduser().resolve(strict=True)
    latest_cfg, latest_meta = require_checkpoint(latest)
    latest_cycle = int(state.get("cycle", -1))
    resolved_cycle = int(latest_cfg.get("main_computer_cycle", latest_meta.get("cycle", -2)))
    if resolved_cycle != latest_cycle:
        raise RuntimeError("source latest checkpoint cycle disagrees with training_state.json")
    return {
        "experiment": experiment,
        "config": config,
        "state": state,
        "branch": branch,
        "latest": latest,
        "latest_cycle": latest_cycle,
    }


def resolve_checkpoint(source: Path, snapshot: dict[str, Any], source_cycle: int | None):
    if source_cycle is None:
        checkpoint = snapshot["latest"]
    else:
        if source_cycle < 0:
            raise RuntimeError("--source-cycle must be >= 0")
        checkpoint = (source / "checkpoints" / "generations" / f"cycle-{source_cycle:06d}").resolve()
        if not checkpoint.is_dir():
            raise RuntimeError(f"requested retained S1 full-head generation does not exist: {checkpoint}")
    cfg, meta = require_checkpoint(checkpoint)
    cycle = int(cfg.get("main_computer_cycle", meta.get("cycle", -1)))
    if source_cycle is not None and cycle != source_cycle:
        raise RuntimeError(f"requested cycle {source_cycle} resolved to checkpoint cycle {cycle}")
    return checkpoint, cfg, meta


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


def state_from_selected(
    source_state: dict[str, Any], cfg: dict[str, Any], meta: dict[str, Any], *, selected_is_latest: bool
) -> dict[str, Any]:
    state = dict(source_state)
    state["cycle"] = int(cfg.get("main_computer_cycle", meta.get("cycle", -1)))
    state["global_step"] = int(cfg.get("main_computer_global_step", meta.get("global_step", -1)))
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
    checkpoint, cp_cfg, cp_meta = resolve_checkpoint(source, snapshot, source_cycle)
    cycle = int(cp_cfg.get("main_computer_cycle", cp_meta.get("cycle", -1)))
    global_step = int(cp_cfg.get("main_computer_global_step", cp_meta.get("global_step", -1)))
    selected_is_latest = checkpoint == snapshot["latest"]
    head_info = inspect_single_s_head(checkpoint / "head.safetensors")

    if target.exists() and any(target.iterdir()):
        marker = target / BRANCH_FILE
        if marker.is_file():
            existing = read_json(marker)
            emit(
                "s1_dictionary_branch_already_exists",
                target=str(target),
                source=existing.get("source_experiment"),
                fork_cycle=existing.get("fork_cycle"),
            )
            return
        raise RuntimeError(f"target branch directory is non-empty: {target}")

    emit(
        "s1_dictionary_branch_plan",
        source_experiment=str(source),
        source_checkpoint=str(checkpoint),
        source_cycle_requested=source_cycle,
        source_latest_cycle=snapshot["latest_cycle"],
        fork_cycle=cycle,
        fork_global_step=global_step,
        selected_is_latest=selected_is_latest,
        target_experiment=str(target),
        architecture="one persistent S1 token; pass1 [S1,prompt] -> R1; pass2 [S1+R1,prompt] -> answer",
        architecture_changed=False,
        model_parameters_changed=False,
        optimizer_changed=False,
        rng_changed=False,
        control_space_size=EXPECTED_K,
        r2_gain_frozen_zero=True,
        third_pass_bypassed=True,
        source_curriculum=SOURCE_CURRICULUM,
        target_curriculum=TARGET_CURRICULUM,
        curriculum_changes_on_first_training_run=True,
        dry_run=dry_run,
    )
    if dry_run:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent)))
    try:
        for rel in (
            "probes", "shards/legacy", "shards/mutation", "shards/ast",
            "shards/consensus", "shards/triad", "shards/dictionary", "checkpoints/generations",
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
        source_hashes = {
            name: sha256_file(checkpoint / name)
            for name in ("head.safetensors", "optimizer.pt", "rng_state.pt")
        }
        for name, source_sha in source_hashes.items():
            target_sha = sha256_file(target_cp / name)
            if target_sha != source_sha:
                raise RuntimeError(f"copied {name} differs from source")

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
            "architecture_changed": False,
            "persistent_tokens": 1,
            "prefix_pass1": "[S1,prompt]",
            "prefix_pass2": "[S1+R1,prompt]",
            "control_space_size": EXPECTED_K,
            "register_rank": EXPECTED_RANK,
            "qwen_frozen": True,
            "r2_gain_frozen_zero": True,
            "third_qwen_pass_bypassed": True,
            "head_sha256": source_hashes["head.safetensors"],
            "optimizer_sha256": source_hashes["optimizer.pt"],
            "rng_state_sha256": source_hashes["rng_state.pt"],
            "learned_tensors_changed": False,
            "optimizer_changed": False,
            "rng_preserved": True,
            "source_curriculum": SOURCE_CURRICULUM,
            "target_curriculum": TARGET_CURRICULUM,
            "curriculum_cutover": "performed by trainer on first run",
            "dictionary_holdout": "default nanojev_dictionary_smoke.py holdout reserved from training",
            "source_lane_modified": False,
            "nonlatest_cursor_policy": None if selected_is_latest else "reset_task_cursors_and_mix_credits_to_zero",
        }

        experiment = dict(snapshot["experiment"])
        experiment["parent_full_head_experiment_sha256"] = snapshot["experiment"].get("experiment_sha256")
        experiment["parent_full_head_branch_lineage"] = experiment.get("branch_lineage")
        experiment["branch_lineage"] = lineage
        experiment["dictionary_training_branch"] = lineage
        experiment["soft_feedback_generator"] = GENERATOR
        experiment["soft_feedback_token_count"] = 1
        experiment["initialization"] = (
            f"exact learned S1 full-head state from cycle-{cycle:06d}; no model/optimizer/RNG changes; "
            "dictionary curriculum begins only when the dictionary trainer is run"
        )
        experiment["experiment_sha256"] = sha256_json(
            {key: value for key, value in experiment.items() if key != "experiment_sha256"}
        )

        # Keep the source training_config unchanged at the branch point.  The
        # dictionary trainer performs the explicit 5/10/34/1/50 ->
        # 5/5/10/15/25/40 migration and records it in training_state.json.
        config = dict(snapshot["config"])

        target_cp_cfg = read_json(target_cp / "config.json")
        target_cp_cfg["main_computer_experiment_sha256"] = experiment["experiment_sha256"]
        atomic_json(target_cp / "config.json", target_cp_cfg)

        state = state_from_selected(snapshot["state"], cp_cfg, cp_meta, selected_is_latest=selected_is_latest)
        state["experiment_sha256"] = experiment["experiment_sha256"]
        state["latest_generation"] = str(target / "checkpoints" / "generations" / checkpoint.name)
        state["branch_lineage"] = lineage
        state["status"] = "branched_s1_dictionary_ready"
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
            latest_path = Path(str(latest_state.get("latest_generation"))).expanduser().resolve()
            if int(latest_state.get("cycle", -1)) != cycle or latest_path != checkpoint:
                raise RuntimeError("source S1 lane advanced while branching latest; rerun")

        if target.exists():
            if any(target.iterdir()):
                raise RuntimeError(f"target became non-empty while branching: {target}")
            target.rmdir()
        temp.rename(target)
        emit(
            "s1_dictionary_branch_committed",
            target_experiment=str(target),
            fork_cycle=cycle,
            fork_global_step=global_step,
            selected_is_latest=selected_is_latest,
            checkpoint=str(target / "checkpoints" / "generations" / checkpoint.name),
            head_sha256=source_hashes["head.safetensors"],
            optimizer_sha256=source_hashes["optimizer.pt"],
            rng_state_sha256=source_hashes["rng_state.pt"],
            architecture_changed=False,
            persistent_tokens=1,
            control_space_size=EXPECTED_K,
            curriculum_pending=True,
        )
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-experiment-dir", type=Path, default=SOURCE_DEFAULT)
    p.add_argument("--target-experiment-dir", type=Path, default=TARGET_DEFAULT)
    p.add_argument("--source-cycle", type=int, default=SOURCE_CYCLE_DEFAULT)
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
