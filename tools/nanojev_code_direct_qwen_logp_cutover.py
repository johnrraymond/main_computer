#!/usr/bin/env python3
"""Cut a mature direct-Qwen NanoJev head over to hidden-state + native-logP input.

Logical candidate feature:
    [terminal_hidden_1024, mean_continuation_logP_1]

The existing NanoJev head is preserved byte-for-byte.  Instead of physically
widening its old matrices (which can alter floating-point accumulation order),
the cutover adds two zero-initialized residual weights:

    logp_scalar.weight   [1, 1]    adds to the existing scalar candidate logit
    logp_project.weight  [128, 1]  adds to the existing set-attention projection

At cutover both are exactly zero, so arbitrary logP values have exactly zero
influence while all existing head weights and AdamW moments remain unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


DEFAULT_SOURCE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_cached_batch_no_clip_reuse_limit_v1"
DEFAULT_OUTPUT_DIR = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_logp_cutover_v1"
SCHEMA = "main-computer-nanojev-direct-qwen-logp-cutover-v1"

OLD_HEAD_PARAM_ORDER = (
    "norm.weight",
    "norm.bias",
    "scalar.weight",
    "scalar.bias",
    "set_project.weight",
    "set_project.bias",
    "set_attention.in_proj_weight",
    "set_attention.in_proj_bias",
    "set_attention.out_proj.weight",
    "set_attention.out_proj.bias",
    "set_output.weight",
    "set_output.bias",
)
NEW_LOGP_PARAM_ORDER = ("logp_scalar.weight", "logp_project.weight")


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def resolve_source_checkpoint(source_experiment: Path, explicit: str | None) -> Path:
    if explicit:
        checkpoint = Path(explicit).expanduser().resolve(strict=True)
    else:
        state_path = source_experiment / "training_state.json"
        if not state_path.is_file():
            raise RuntimeError(f"source training_state.json missing: {state_path}")
        latest = read_json(state_path).get("latest_generation")
        if not latest:
            raise RuntimeError("source experiment has no latest_generation")
        checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    for name in ("head.safetensors", "optimizer.pt", "rng_state.pt", "config.json"):
        if not (checkpoint / name).is_file():
            raise RuntimeError(f"source checkpoint missing {name}: {checkpoint}")
    return checkpoint


def validate_source_head(state: dict[str, Any]) -> int:
    required = set(OLD_HEAD_PARAM_ORDER)
    if set(state) != required:
        raise RuntimeError(
            f"unexpected source head keys: missing={sorted(required - set(state))} "
            f"extra={sorted(set(state) - required)}"
        )
    hidden = int(state["norm.weight"].numel())
    checks = {
        "norm.bias": (hidden,),
        "scalar.weight": (1, hidden),
        "scalar.bias": (1,),
        "set_project.weight": (128, hidden + 1),
        "set_project.bias": (128,),
        "set_attention.in_proj_weight": (384, 128),
        "set_attention.in_proj_bias": (384,),
        "set_attention.out_proj.weight": (128, 128),
        "set_attention.out_proj.bias": (128,),
        "set_output.weight": (1, 128),
        "set_output.bias": (1,),
    }
    for name, expected in checks.items():
        if tuple(state[name].shape) != expected:
            raise RuntimeError(f"source {name} shape {tuple(state[name].shape)} != {expected}")
    return hidden


def cutover_head_state(source: dict[str, Any]) -> tuple[dict[str, Any], int]:
    import torch

    hidden = validate_source_head(source)
    out = {key: value.detach().cpu().contiguous().clone() for key, value in source.items()}
    out["logp_scalar.weight"] = torch.zeros((1, 1), dtype=source["scalar.weight"].dtype)
    out["logp_project.weight"] = torch.zeros((128, 1), dtype=source["set_project.weight"].dtype)
    return out, hidden


def cutover_optimizer_state(optimizer_state: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(optimizer_state)
    groups = out.get("param_groups")
    states = out.get("state")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(states, dict):
        raise RuntimeError("expected one-group AdamW optimizer state")
    params = list(groups[0].get("params") or [])
    if len(params) != len(OLD_HEAD_PARAM_ORDER):
        raise RuntimeError(
            f"source optimizer parameter count {len(params)} != {len(OLD_HEAD_PARAM_ORDER)}"
        )
    numeric = [int(x) for x in params] + [int(x) for x in states.keys()]
    next_id = (max(numeric) + 1) if numeric else 0
    groups[0]["params"] = params + [next_id, next_id + 1]
    # Deliberately leave the new parameters absent from optimizer['state'].
    # AdamW will create zero step/exp_avg/exp_avg_sq on their first gradient.
    return out


def verify_cutover(source: dict[str, Any], cutover: dict[str, Any]) -> None:
    import torch

    for name in OLD_HEAD_PARAM_ORDER:
        if not torch.equal(source[name].cpu(), cutover[name].cpu()):
            raise RuntimeError(f"cutover changed existing head tensor {name}")
    for name in NEW_LOGP_PARAM_ORDER:
        if torch.count_nonzero(cutover[name]).item() != 0:
            raise RuntimeError(f"new logP tensor is not zero: {name}")


def build_manifest(source_experiment: Path, checkpoint: Path, hidden: int,
                   source_head_sha: str, cutover_head_sha: str) -> dict[str, Any]:
    experiment = read_json(source_experiment / "experiment.json")
    config = read_json(source_experiment / "training_config.json")
    state = read_json(source_experiment / "training_state.json")
    meta = read_json(checkpoint / "meta.json") if (checkpoint / "meta.json").is_file() else {}
    return {
        "schema_version": SCHEMA,
        "source_experiment": str(source_experiment),
        "source_checkpoint": str(checkpoint),
        "source_head_sha256": source_head_sha,
        "cutover_head_sha256": cutover_head_sha,
        "source_cycle": int(state.get("cycle", meta.get("cycle", 0))),
        "source_global_step": int(state.get("global_step", meta.get("global_step", 0))),
        "source_training_state": state,
        "model": experiment.get("model"),
        "resolved_model_revision": experiment.get("resolved_model_revision"),
        "probe_experiment": experiment.get("probe_experiment"),
        "legacy_experiment": experiment.get("legacy_experiment"),
        "repo_root": experiment.get("repo_root"),
        "source_training_config": config,
        "hidden_width": hidden,
        "logp_width": 1,
        "candidate_feature_width": hidden + 1,
        "logp_semantics": "mean_teacher_forced_native_log_probability_per_continuation_token",
        "path_aggregation": "mean_each_path_logp_then_mean_paths_per_semantic_candidate",
        "cutover_contract": {
            "logical_candidate_feature": f"hidden_{hidden}_plus_logp_1",
            "old_head_tensors_byte_preserved": True,
            "old_optimizer_state_preserved": True,
            "new_logp_scalar_weight_zero": True,
            "new_logp_project_weight_zero": True,
            "new_optimizer_state_created_on_first_gradient": True,
            "rng_state_preserved": True,
            "old_head_function_exact_at_cutover": True,
        },
    }


def materialize(output_dir: Path, checkpoint: Path, source_experiment: Path) -> dict[str, Any]:
    import torch
    from safetensors.torch import load_file, save_file

    if output_dir.exists():
        raise RuntimeError(f"cutover output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = output_dir.parent / f".{output_dir.name}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)

    source_head = load_file(str(checkpoint / "head.safetensors"), device="cpu")
    new_head, hidden = cutover_head_state(source_head)
    verify_cutover(source_head, new_head)
    save_file(new_head, str(temp / "head.safetensors"))

    source_optimizer = torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False)
    torch.save(cutover_optimizer_state(source_optimizer), temp / "optimizer.pt")
    shutil.copy2(checkpoint / "rng_state.pt", temp / "rng_state.pt")

    source_sha = sha256_file(checkpoint / "head.safetensors")
    new_sha = sha256_file(temp / "head.safetensors")
    manifest = build_manifest(source_experiment, checkpoint, hidden, source_sha, new_sha)
    atomic_json(temp / "cutover.json", manifest)
    os.replace(temp, output_dir)
    return manifest


def self_test() -> None:
    import torch

    hidden = 8
    source = {
        "norm.weight": torch.randn(hidden),
        "norm.bias": torch.randn(hidden),
        "scalar.weight": torch.randn(1, hidden),
        "scalar.bias": torch.randn(1),
        "set_project.weight": torch.randn(128, hidden + 1),
        "set_project.bias": torch.randn(128),
        "set_attention.in_proj_weight": torch.randn(384, 128),
        "set_attention.in_proj_bias": torch.randn(384),
        "set_attention.out_proj.weight": torch.randn(128, 128),
        "set_attention.out_proj.bias": torch.randn(128),
        "set_output.weight": torch.randn(1, 128),
        "set_output.bias": torch.randn(1),
    }
    new_head, got_hidden = cutover_head_state(source)
    assert got_hidden == hidden
    verify_cutover(source, new_head)
    assert set(new_head) == set(OLD_HEAD_PARAM_ORDER) | set(NEW_LOGP_PARAM_ORDER)
    assert sum(v.numel() for v in new_head.values()) == sum(v.numel() for v in source.values()) + 129

    opt = {
        "state": {i: {"step": torch.tensor(3.0)} for i in range(len(OLD_HEAD_PARAM_ORDER))},
        "param_groups": [{"params": list(range(len(OLD_HEAD_PARAM_ORDER))), "lr": 1e-4}],
    }
    new_opt = cutover_optimizer_state(opt)
    assert new_opt["param_groups"][0]["params"][-2:] == [12, 13]
    assert 12 not in new_opt["state"] and 13 not in new_opt["state"]
    emit("self_test_ok", hidden_width=hidden, logp_width=1, added_trainable_params=129)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment-dir", default=DEFAULT_SOURCE_EXPERIMENT)
    parser.add_argument("--source-checkpoint")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    source_experiment = Path(args.source_experiment_dir).expanduser().resolve(strict=True)
    checkpoint = resolve_source_checkpoint(source_experiment, args.source_checkpoint)
    output_dir = Path(args.output_dir).expanduser().resolve()
    emit(
        "logp_cutover_resolved",
        source_experiment=str(source_experiment),
        source_checkpoint=str(checkpoint),
        output_dir=str(output_dir),
        source_head_sha256=sha256_file(checkpoint / "head.safetensors"),
        dry_run=bool(args.dry_run),
    )
    if args.dry_run:
        return
    manifest = materialize(output_dir, checkpoint, source_experiment)
    emit(
        "logp_cutover_done",
        output_dir=str(output_dir),
        source_cycle=manifest["source_cycle"],
        hidden_width=manifest["hidden_width"],
        logp_width=1,
        candidate_feature_width=manifest["candidate_feature_width"],
        added_trainable_params=129,
        old_head_function_exact_at_cutover=True,
    )


if __name__ == "__main__":
    main()
