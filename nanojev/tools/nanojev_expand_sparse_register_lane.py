#!/usr/bin/env python3
"""Branch the latest sparse-register checkpoint into a wider control-space lane.

The migration preserves the learned function of the current K-slot controller while
expanding its shared control dictionary to a larger K'.  Existing control-bank rows
and router coefficient rows are copied exactly into slots [0:K).  New bank rows are
initialized as orthonormal directions in the complement of the old bank's row span,
and their router coefficient rows are exact zero.  Therefore the new slots contribute
exactly zero at migration but have nonzero directions and can receive router gradients.

The source lane is never modified.  The target lane gets the current committed
checkpoint, state/config lineage, probes, and history needed to resume training.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
from pathlib import Path


DEFAULT_SOURCE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_v1"
DEFAULT_TARGET_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_v1"
DEFAULT_NEW_CONTROL_SPACE_SIZE = 1000
HEAD_BANK_KEY = "soft_feedback.control_bank"
HEAD_ROUTER_COEFF_KEY = "soft_feedback.router_coeff.weight"
HEAD_ROUTER_DOWN_KEY = "soft_feedback.router_down.weight"
HEAD_ROUTER_SCORE_KEY = "soft_feedback.router_score.weight"


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def sha256_json(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_safetensors_with_metadata(path: Path):
    from safetensors import safe_open

    tensors = {}
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        metadata = handle.metadata()
        for key in handle.keys():
            tensors[key] = handle.get_tensor(key)
    return tensors, metadata


def save_safetensors(path: Path, tensors: dict, metadata: dict | None) -> None:
    from safetensors.torch import save_file

    path.parent.mkdir(parents=True, exist_ok=True)
    contiguous = {key: value.detach().cpu().contiguous() for key, value in tensors.items()}
    save_file(contiguous, str(path), metadata=metadata)


def normalized_rows(tensor):
    import torch.nn.functional as F

    return F.normalize(tensor.float(), p=2.0, dim=1)


def expand_control_bank(old_bank, new_size: int):
    """Preserve old rows exactly and add unit directions orthogonal to their span."""
    import torch

    if old_bank.ndim != 2:
        raise RuntimeError(f"{HEAD_BANK_KEY} must be rank-2, got shape={tuple(old_bank.shape)}")
    old_size, hidden_size = map(int, old_bank.shape)
    if new_size <= old_size:
        raise RuntimeError(f"new control-space size must exceed old size: old={old_size} new={new_size}")
    if new_size > hidden_size:
        raise RuntimeError(
            f"new control-space size {new_size} exceeds hidden size {hidden_size}; "
            "the current trainer requires K <= H for its normalized/orthogonality geometry"
        )

    old_float = old_bank.detach().float().cpu()
    matrix_rank = int(torch.linalg.matrix_rank(old_float).item())
    if matrix_rank != old_size:
        raise RuntimeError(
            f"old control bank is rank deficient: rows={old_size} rank={matrix_rank}; "
            "cannot construct the requested complement while preserving every learned row"
        )

    # Complete QR of HxK gives a full H-dimensional orthonormal frame whose first K
    # columns span the learned old row space.  The next K'-K columns are therefore
    # orthogonal to every old row and to each other.
    q, _ = torch.linalg.qr(old_float.transpose(0, 1), mode="complete")
    new_rows = q[:, old_size:new_size].transpose(0, 1).contiguous()
    if int(new_rows.shape[0]) != new_size - old_size:
        raise RuntimeError("failed to construct the requested number of new control directions")

    expanded = torch.cat([old_float, new_rows], dim=0).to(dtype=old_bank.dtype)
    if not torch.equal(expanded[:old_size], old_bank.detach().cpu()):
        raise RuntimeError("control-bank migration failed exact preservation of the original rows")

    old_unit = normalized_rows(old_float)
    new_unit = normalized_rows(new_rows)
    cross_max = float((old_unit @ new_unit.transpose(0, 1)).abs().max().item()) if new_rows.numel() else 0.0
    new_gram = new_unit @ new_unit.transpose(0, 1)
    eye = torch.eye(new_rows.shape[0], dtype=new_gram.dtype)
    new_orth_error = float((new_gram - eye).abs().max().item()) if new_rows.numel() else 0.0
    if cross_max > 2e-4 or new_orth_error > 2e-4:
        raise RuntimeError(
            "expanded control-bank geometry check failed: "
            f"old_new_max_abs_dot={cross_max} new_rows_max_orth_error={new_orth_error}"
        )
    return expanded, cross_max, new_orth_error


def expand_router_coeff(old_weight, new_size: int):
    import torch

    if old_weight.ndim != 2:
        raise RuntimeError(f"{HEAD_ROUTER_COEFF_KEY} must be rank-2, got shape={tuple(old_weight.shape)}")
    old_size, register_rank = map(int, old_weight.shape)
    expanded = torch.zeros((new_size, register_rank), dtype=old_weight.dtype, device="cpu")
    expanded[:old_size].copy_(old_weight.detach().cpu())
    if not torch.equal(expanded[:old_size], old_weight.detach().cpu()):
        raise RuntimeError("router coefficient migration failed exact preservation of the original rows")
    if int(torch.count_nonzero(expanded[old_size:]).item()) != 0:
        raise RuntimeError("new router coefficient rows are not exact zero")
    return expanded


def verify_function_preservation(old_bank, old_coeff_weight, new_bank, new_coeff_weight) -> float:
    """Probe the residual map in router-code space; new slots must be a functional no-op."""
    import torch

    rank = int(old_coeff_weight.shape[1])
    generator = torch.Generator(device="cpu")
    generator.manual_seed(20260927)
    code = torch.randn((17, rank), generator=generator, dtype=torch.float32)

    old_coeff = torch.tanh(code @ old_coeff_weight.detach().float().cpu().transpose(0, 1))
    new_coeff = torch.tanh(code @ new_coeff_weight.detach().float().cpu().transpose(0, 1))
    old_residual = old_coeff @ normalized_rows(old_bank.detach().cpu())
    new_residual = new_coeff @ normalized_rows(new_bank.detach().cpu())
    max_abs = float((old_residual - new_residual).abs().max().item())
    if max_abs > 2e-5:
        raise RuntimeError(f"expanded controller changed the inherited residual map: max_abs_diff={max_abs}")
    return max_abs


def migrate_optimizer(source_path: Path, target_path: Path, *, old_size: int, hidden_size: int, register_rank: int) -> dict:
    """Preserve unchanged router moments; reset the two resized tensors' Adam state.

    Adam stores one scalar step per Parameter, not per row.  A widened Parameter cannot
    simultaneously keep mature step/moments for rows 0:K and give new rows a true step-0
    state.  Resetting state only for control_bank and router_coeff avoids invalid mixed-age
    Adam state while preserving learned weights and the unchanged router_down/router_score
    optimizer state.
    """
    import torch

    payload = torch.load(source_path, map_location="cpu", weights_only=False)
    groups = payload.get("param_groups")
    state = payload.get("state")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(state, dict):
        raise RuntimeError("expected one AdamW param group in sparse-register optimizer checkpoint")
    param_ids = list(groups[0].get("params", []))
    if len(param_ids) != 4:
        raise RuntimeError(
            "expected sparse-register dynamic parameter order "
            "[control_bank, router_down.weight, router_score.weight, router_coeff.weight]"
        )

    bank_id, down_id, score_id, coeff_id = param_ids

    def state_shape(param_id):
        entry = state.get(param_id, {})
        for key in ("exp_avg", "exp_avg_sq", "max_exp_avg_sq"):
            value = entry.get(key)
            if isinstance(value, torch.Tensor) and value.ndim > 0:
                return tuple(int(v) for v in value.shape)
        return None

    expected = {
        bank_id: (old_size, hidden_size),
        down_id: (register_rank, hidden_size),
        score_id: (register_rank, 2),
        coeff_id: (old_size, register_rank),
    }
    observed = {param_id: state_shape(param_id) for param_id in param_ids}
    for param_id, shape in expected.items():
        if observed[param_id] not in (None, shape):
            raise RuntimeError(
                f"optimizer parameter ordering/shape mismatch for id={param_id}: "
                f"expected={shape} observed={observed[param_id]}"
            )

    reset_ids = []
    for param_id in (bank_id, coeff_id):
        if param_id in state:
            state.pop(param_id)
            reset_ids.append(param_id)

    torch.save(payload, target_path)
    return {
        "optimizer_param_ids": [int(v) for v in param_ids],
        "reset_param_ids": [int(v) for v in reset_ids],
        "preserved_param_ids": [int(down_id), int(score_id)],
        "policy": "reset_state_for_resized_control_bank_and_router_coeff; preserve_router_down_and_router_score_state",
        "reason": "Adam step is scalar per Parameter, so mixed mature/fresh row ages cannot be represented safely after widening",
    }


def copied_root_support_files(source: Path, target: Path) -> list[str]:
    names = (
        "history.jsonl",
        "orbit_consistency_training.json",
        "orbit_supervision_training.json",
        "baseline_consensus_dev.json",
        "baseline_pairwise_triad_dev.json",
        "latest_consensus_orbit_dev.json",
    )
    copied = []
    for name in names:
        src = source / name
        if src.is_file():
            shutil.copy2(src, target / name)
            copied.append(name)
    if not (target / "history.jsonl").exists():
        (target / "history.jsonl").write_text("", encoding="utf-8")
        copied.append("history.jsonl")
    return copied


def powershell_resume_command(target: Path, config: dict) -> str:
    parts = [
        "& 'C:\\Users\\subsi\\NanoJev\\.venv\\Scripts\\python.exe' `",
        "  .\\tools\\nanojev_code_sparse_register_train.py `",
        f"  --experiment-dir '{target}' `",
        f"  --control-space-size {int(config['control_space_size'])} `",
        f"  --register-rank {int(config['register_rank'])} `",
        f"  --cycle-seconds {float(config['cycle_seconds']):.12g} `",
        f"  --register-sparsity-weight {float(config['register_sparsity_weight']):.17g} `",
        f"  --register-residual-weight {float(config['register_residual_weight']):.17g} `",
        f"  --register-orthogonality-weight {float(config['register_orthogonality_weight']):.17g} `",
        f"  --register-active-epsilon {float(config['register_active_epsilon']):.17g}",
    ]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment-dir", default=DEFAULT_SOURCE_EXPERIMENT)
    parser.add_argument("--target-experiment-dir", default=DEFAULT_TARGET_EXPERIMENT)
    parser.add_argument("--new-control-space-size", type=int, default=DEFAULT_NEW_CONTROL_SPACE_SIZE)
    parser.add_argument(
        "--regularization-mode",
        choices=("preserve-per-coordinate", "keep-weights"),
        default="preserve-per-coordinate",
        help=(
            "preserve-per-coordinate rescales sparsity and orthogonality weights so the inherited active "
            "coordinates/pairs receive the same regularization force after K expands; keep-weights copies "
            "the numeric weights unchanged"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and report the migration without writing the target lane")
    args = parser.parse_args()

    source = Path(args.source_experiment_dir).expanduser().resolve(strict=True)
    target = Path(args.target_experiment_dir).expanduser().resolve()
    new_size = int(args.new_control_space_size)

    if source == target:
        raise RuntimeError("source and target experiment directories must differ")
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(f"target experiment directory is not empty: {target}")
    if target.exists() and not target.is_dir():
        raise RuntimeError(f"target path exists and is not a directory: {target}")

    experiment = read_json(source / "experiment.json")
    training_config = read_json(source / "training_config.json")
    state = read_json(source / "training_state.json")
    latest_raw = state.get("latest_generation")
    if not latest_raw:
        raise RuntimeError("source sparse-register lane has no committed latest_generation")
    source_checkpoint = Path(latest_raw).expanduser().resolve(strict=True)
    source_head = source_checkpoint / "head.safetensors"
    source_optimizer = source_checkpoint / "optimizer.pt"
    source_rng = source_checkpoint / "rng_state.pt"
    source_checkpoint_config = read_json(source_checkpoint / "config.json")
    source_meta = read_json(source_checkpoint / "meta.json")
    for required in (source_head, source_optimizer, source_rng):
        if not required.is_file():
            raise RuntimeError(f"current checkpoint is incomplete: missing {required}")

    old_size = int(training_config.get("control_space_size", 0))
    register_rank = int(training_config.get("register_rank", 0))
    if old_size <= 0 or register_rank <= 0:
        raise RuntimeError("source training config has invalid sparse-register dimensions")
    if new_size <= old_size:
        raise RuntimeError(f"new control-space size must exceed source size: old={old_size} new={new_size}")

    weights, metadata = load_safetensors_with_metadata(source_head)
    for key in (HEAD_BANK_KEY, HEAD_ROUTER_COEFF_KEY, HEAD_ROUTER_DOWN_KEY, HEAD_ROUTER_SCORE_KEY):
        if key not in weights:
            raise RuntimeError(f"source head is missing required sparse-register tensor: {key}")

    old_bank = weights[HEAD_BANK_KEY]
    old_coeff = weights[HEAD_ROUTER_COEFF_KEY]
    router_down = weights[HEAD_ROUTER_DOWN_KEY]
    router_score = weights[HEAD_ROUTER_SCORE_KEY]
    if tuple(old_bank.shape[:1]) != (old_size,):
        raise RuntimeError(f"control bank disagrees with config K={old_size}: shape={tuple(old_bank.shape)}")
    hidden_size = int(old_bank.shape[1])
    if tuple(old_coeff.shape) != (old_size, register_rank):
        raise RuntimeError(
            f"router coeff disagrees with config K/rank: expected={(old_size, register_rank)} got={tuple(old_coeff.shape)}"
        )
    if tuple(router_down.shape) != (register_rank, hidden_size):
        raise RuntimeError(f"router_down shape mismatch: expected={(register_rank, hidden_size)} got={tuple(router_down.shape)}")
    if tuple(router_score.shape) != (register_rank, 2):
        raise RuntimeError(f"router_score shape mismatch: expected={(register_rank, 2)} got={tuple(router_score.shape)}")

    expanded_bank, old_new_cross_max, new_orth_error = expand_control_bank(old_bank, new_size)
    expanded_coeff = expand_router_coeff(old_coeff, new_size)
    residual_probe_max_abs = verify_function_preservation(
        old_bank, old_coeff, expanded_bank, expanded_coeff
    )

    migrated_training_config = copy.deepcopy(training_config)
    migrated_training_config["control_space_size"] = new_size
    old_sparsity_weight = float(training_config["register_sparsity_weight"])
    old_orth_weight = float(training_config["register_orthogonality_weight"])
    if args.regularization_mode == "preserve-per-coordinate":
        sparsity_scale = float(new_size) / float(old_size)
        old_pairs = old_size * (old_size - 1)
        new_pairs = new_size * (new_size - 1)
        orthogonality_scale = float(new_pairs) / float(old_pairs) if old_pairs else 1.0
        migrated_training_config["register_sparsity_weight"] = old_sparsity_weight * sparsity_scale
        migrated_training_config["register_orthogonality_weight"] = old_orth_weight * orthogonality_scale
    else:
        sparsity_scale = 1.0
        orthogonality_scale = 1.0

    source_head_sha = file_sha256(source_head)
    source_cycle = int(state.get("cycle", source_checkpoint_config.get("main_computer_cycle", -1)))
    source_global_step = int(state.get("global_step", source_checkpoint_config.get("main_computer_global_step", -1)))
    target_generation_rel = Path("checkpoints") / "generations" / f"cycle-{source_cycle:06d}"
    target_generation = target / target_generation_rel

    migration_record = {
        "schema_version": "main-computer-nanojev-sparse-register-lane-expansion-v1",
        "source_experiment": str(source),
        "source_checkpoint": str(source_checkpoint),
        "source_head_sha256": source_head_sha,
        "source_cycle": source_cycle,
        "source_global_step": source_global_step,
        "old_control_space_size": old_size,
        "new_control_space_size": new_size,
        "register_rank": register_rank,
        "hidden_size": hidden_size,
        "preserved_slots": [0, old_size - 1],
        "new_zero_output_slots": [old_size, new_size - 1],
        "new_bank_initialization": "orthonormal_complement_of_old_control_bank_row_span",
        "new_router_coeff_initialization": "exact_zero",
        "residual_probe_max_abs_diff": residual_probe_max_abs,
        "old_new_bank_max_abs_dot": old_new_cross_max,
        "new_bank_max_orthogonality_error": new_orth_error,
        "regularization_mode": args.regularization_mode,
        "sparsity_weight_scale": sparsity_scale,
        "orthogonality_weight_scale": orthogonality_scale,
        "old_register_sparsity_weight": old_sparsity_weight,
        "new_register_sparsity_weight": float(migrated_training_config["register_sparsity_weight"]),
        "old_register_orthogonality_weight": old_orth_weight,
        "new_register_orthogonality_weight": float(migrated_training_config["register_orthogonality_weight"]),
        "register_residual_weight": float(migrated_training_config["register_residual_weight"]),
        "function_preservation": "old 32 control-bank rows and router coefficient rows exact; added rows contribute zero at migration",
    }

    migrated_experiment = copy.deepcopy(experiment)
    migrated_experiment["source_soft_feedback_experiment"] = str(source)
    migrated_experiment["parent_checkpoint"] = str(source_checkpoint)
    migrated_experiment["parent_cycle"] = source_cycle
    migrated_experiment["parent_global_step"] = source_global_step
    migrated_experiment["parent_head_sha256"] = source_head_sha
    migrated_experiment["control_space_size"] = new_size
    migrated_experiment["register_sparsity_weight"] = float(migrated_training_config["register_sparsity_weight"])
    migrated_experiment["register_orthogonality_weight"] = float(migrated_training_config["register_orthogonality_weight"])
    migrated_experiment["initialization"] = (
        f"migrated from sparse-register cycle-{source_cycle:06d}; first {old_size} learned control rows and "
        f"router coefficient rows preserved exactly; {new_size - old_size} new complement directions with exact-zero "
        "router coefficient rows; Qwen/head/static soft token remain frozen"
    )
    migrated_experiment["diagnostic_parent_reason"] = (
        "capacity expansion branch from the current committed sparse-register checkpoint; preserve learned function "
        "while adding zero-contribution, immediately learnable control directions"
    )
    migrated_experiment["sparse_register_invariant"] = (
        f"slots 0:{old_size} preserve the source learned controller; slots {old_size}:{new_size} have zero coefficient "
        "output at migration and nonzero complement bank directions; task identity remains absent from routing"
    )
    migrated_experiment["lane_migration"] = migration_record

    # Keep the target lane self-contained for its fixed consensus probe.
    source_consensus_probe = migrated_experiment.get("consensus_dev_probe")
    if source_consensus_probe:
        source_probe_path = Path(source_consensus_probe)
        if source_probe_path.name == "consensus_dev.jsonl":
            migrated_experiment["consensus_dev_probe"] = str(target / "probes" / "consensus_dev.jsonl")

    migrated_experiment["experiment_sha256"] = sha256_json(
        {key: value for key, value in migrated_experiment.items() if key != "experiment_sha256"}
    )

    migrated_state = copy.deepcopy(state)
    migrated_state["experiment_sha256"] = migrated_experiment["experiment_sha256"]
    migrated_state["latest_generation"] = str(target_generation)
    migrated_state["parent_checkpoint"] = str(source_checkpoint)
    migrated_state["parent_cycle"] = source_cycle
    migrated_state["parent_global_step"] = source_global_step
    migrated_state["parent_head_sha256"] = source_head_sha
    lane_migrations = list(migrated_state.get("lane_migrations", []))
    lane_migrations.append(migration_record)
    migrated_state["lane_migrations"] = lane_migrations

    migrated_checkpoint_config = copy.deepcopy(source_checkpoint_config)
    migrated_checkpoint_config["main_computer_experiment_sha256"] = migrated_experiment["experiment_sha256"]
    migrated_checkpoint_config["parent_head_sha256"] = source_head_sha
    feedback_config = migrated_checkpoint_config.get("soft_feedback")
    if not isinstance(feedback_config, dict):
        raise RuntimeError("source checkpoint config has no soft_feedback section")
    feedback_config["control_space_size"] = new_size
    feedback_config["register_rank"] = register_rank
    feedback_config["register_sparsity_weight"] = float(migrated_training_config["register_sparsity_weight"])
    feedback_config["register_residual_weight"] = float(migrated_training_config["register_residual_weight"])
    feedback_config["register_orthogonality_weight"] = float(migrated_training_config["register_orthogonality_weight"])
    feedback_config["expanded_from_control_space_size"] = old_size
    feedback_config["expanded_new_slots_zero_output_initialized"] = True
    feedback_config["expanded_new_bank_rows"] = "orthonormal_complement_of_old_control_bank_row_span"
    migrated_checkpoint_config["lane_migration"] = migration_record

    migrated_meta = copy.deepcopy(source_meta)
    migrated_meta["post_cycle_lane_migration"] = migration_record

    emit(
        "migration_plan",
        source_experiment=str(source),
        source_checkpoint=str(source_checkpoint),
        target_experiment=str(target),
        cycle=source_cycle,
        global_step=source_global_step,
        old_control_space_size=old_size,
        new_control_space_size=new_size,
        register_rank=register_rank,
        hidden_size=hidden_size,
        residual_probe_max_abs_diff=residual_probe_max_abs,
        old_new_bank_max_abs_dot=old_new_cross_max,
        new_bank_max_orthogonality_error=new_orth_error,
        regularization_mode=args.regularization_mode,
        new_register_sparsity_weight=migrated_training_config["register_sparsity_weight"],
        new_register_orthogonality_weight=migrated_training_config["register_orthogonality_weight"],
        dry_run=bool(args.dry_run),
    )
    if args.dry_run:
        print(powershell_resume_command(target, migrated_training_config))
        return

    staging = target.with_name(target.name + ".migrating")
    if staging.exists():
        raise RuntimeError(f"stale migration staging directory exists: {staging}")
    if target.exists():
        # Empty target directories are allowed by validation above but cannot be atomically replaced.
        target.rmdir()

    staging.mkdir(parents=True)
    try:
        (staging / "checkpoints" / "generations" / f"cycle-{source_cycle:06d}").mkdir(parents=True)
        for task in ("legacy", "mutation", "ast", "consensus", "triad"):
            (staging / "shards" / task).mkdir(parents=True, exist_ok=True)

        source_probes = source / "probes"
        if not source_probes.is_dir():
            raise RuntimeError(f"source lane has no probes directory: {source_probes}")
        shutil.copytree(source_probes, staging / "probes")
        support_files = copied_root_support_files(source, staging)

        migrated_weights = dict(weights)
        migrated_weights[HEAD_BANK_KEY] = expanded_bank
        migrated_weights[HEAD_ROUTER_COEFF_KEY] = expanded_coeff
        staged_generation = staging / target_generation_rel
        save_safetensors(staged_generation / "head.safetensors", migrated_weights, metadata)
        optimizer_migration = migrate_optimizer(
            source_optimizer,
            staged_generation / "optimizer.pt",
            old_size=old_size,
            hidden_size=hidden_size,
            register_rank=register_rank,
        )
        shutil.copy2(source_rng, staged_generation / "rng_state.pt")
        atomic_json(staged_generation / "config.json", migrated_checkpoint_config)
        atomic_json(staged_generation / "meta.json", migrated_meta)

        migrated_head_sha = file_sha256(staged_generation / "head.safetensors")
        migrated_state["latest_head_sha256"] = migrated_head_sha
        migration_record["target_head_sha256"] = migrated_head_sha
        migration_record["optimizer_migration"] = optimizer_migration
        migrated_experiment["lane_migration"] = migration_record
        migrated_experiment["experiment_sha256"] = sha256_json(
            {key: value for key, value in migrated_experiment.items() if key != "experiment_sha256"}
        )
        migrated_state["experiment_sha256"] = migrated_experiment["experiment_sha256"]
        migrated_state["lane_migrations"][-1] = migration_record
        migrated_checkpoint_config["main_computer_experiment_sha256"] = migrated_experiment["experiment_sha256"]
        migrated_checkpoint_config["lane_migration"] = migration_record
        migrated_meta["post_cycle_lane_migration"] = migration_record

        # Rewrite dependent JSON after target head hash/optimizer policy become known.
        atomic_json(staged_generation / "config.json", migrated_checkpoint_config)
        atomic_json(staged_generation / "meta.json", migrated_meta)
        atomic_json(staging / "experiment.json", migrated_experiment)
        atomic_json(staging / "training_config.json", migrated_training_config)
        atomic_json(staging / "training_state.json", migrated_state)
        atomic_json(staging / "lane_migration.json", migration_record)

        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    final_head = target_generation / "head.safetensors"
    final_head_sha = file_sha256(final_head)
    if final_head_sha != migrated_state["latest_head_sha256"]:
        raise RuntimeError("post-commit target head hash differs from recorded migration state")

    emit(
        "migration_committed",
        source_checkpoint=str(source_checkpoint),
        target_experiment=str(target),
        target_checkpoint=str(target_generation),
        source_head_sha256=source_head_sha,
        target_head_sha256=final_head_sha,
        old_control_space_size=old_size,
        new_control_space_size=new_size,
        optimizer_policy=migration_record["optimizer_migration"]["policy"],
        support_files=support_files,
    )
    print(powershell_resume_command(target, migrated_training_config))


if __name__ == "__main__":
    main()
