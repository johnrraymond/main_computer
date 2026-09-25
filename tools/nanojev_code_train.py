#!/usr/bin/env python3
"""Restartable frozen-backbone NanoJev trainer for binary next-lexeme verification."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time

EXPERIMENT_SCHEMA = "main-computer-nanojev-code-lexeme-experiment-v1"
STATE_SCHEMA = "main-computer-nanojev-code-lexeme-training-state-v1"
TRAINING_SCHEMA = "main-computer-nanojev-code-lexeme-training-config-v1"
RANKING_SCHEMA = "main-computer-nanojev-code-lexeme-ranking-config-v1"
RANKING_OBJECTIVE = "binary_next_lexeme_verification_plus_pairwise_margin"
PHASE = "frozen_head_lexeme_binary"
ROLLING_DEV_MODE = "canonical_64_with_random_half_replaced_v1"
ROLLING_DEV_SOURCE_FILES = 40
ROLLING_DEV_SEED_STRIDE = 104729


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def import_nanojev(nanojev_root: Path):
    scripts = nanojev_root / "scripts"
    sys.path.insert(0, str(scripts))
    import train_pipeline_decisions as pipeline  # type: ignore
    from train_toy_decisions import DecisionModel  # type: ignore
    for name in ("load_training_examples", "pack_complete_questions", "grouped_target_loss"):
        if not hasattr(pipeline, name):
            raise RuntimeError(f"NanoJev pipeline lacks required API: {name}")
    return pipeline, DecisionModel


def tensor_digest(tensor) -> str:
    return hashlib.sha256(tensor.detach().cpu().contiguous().numpy().tobytes()).hexdigest()


def named_probe_digest(model, name: str) -> str:
    for current, param in model.named_parameters():
        if current == name:
            return tensor_digest(param)
    raise RuntimeError(f"parameter probe disappeared: {name}")


def head_state(model):
    return {
        k: v.detach().cpu().contiguous().clone()
        for k, v in model.state_dict().items()
        if not k.startswith("backbone.")
    }


def full_state_cpu(model):
    return {k: v.detach().cpu().contiguous().clone() for k, v in model.state_dict().items()}


def cyclic_slice(values, start: int, count: int):
    if not values:
        return []
    return [values[(start + i) % len(values)] for i in range(min(count, len(values)))]


def grad_norm(parameters) -> float:
    total = 0.0
    for p in parameters:
        if p.grad is None:
            continue
        g = p.grad.detach().float()
        total += float(g.pow(2).sum().item())
    return math.sqrt(total)


def binary_auc(scores: list[float], labels: list[bool]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ordered = sorted(zip(scores, labels), key=lambda x: x[0])
    rank_sum_positive = 0.0
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        average_rank = ((i + 1) + j) / 2.0
        rank_sum_positive += average_rank * sum(1 for _, label in ordered[i:j] if label)
        i = j
    u = rank_sum_positive - positives * (positives + 1) / 2.0
    return u / (positives * negatives)


def paired_ranking_metrics(pair_scores: dict[str, dict[int, float]], margin: float) -> dict:
    """Measure ranking only within matched FALSE/TRUE lexeme families."""
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError("pair ranking margin must be finite and positive")
    if not pair_scores:
        return {
            "pair_count": 0,
            "pair_win_rate": None,
            "mean_pair_logodds_gap": None,
            "pair_margin_satisfied_rate": None,
        }
    gaps = []
    for family, sides in pair_scores.items():
        if set(sides) != {0, 1}:
            raise RuntimeError(f"lexeme evaluation family is not an exact FALSE/TRUE pair: {family}")
        gap = float(sides[1]) - float(sides[0])
        if not math.isfinite(gap):
            raise RuntimeError(f"nonfinite paired lexeme score gap: {family}")
        gaps.append(gap)
    return {
        "pair_count": len(gaps),
        "pair_win_rate": sum(gap > 0.0 for gap in gaps) / len(gaps),
        "mean_pair_logodds_gap": sum(gaps) / len(gaps),
        "pair_margin_satisfied_rate": sum(gap >= margin for gap in gaps) / len(gaps),
    }


SELECTION_POLICY = "dev_auc_then_probability_separation_then_nll"


def discriminator_selection(*, cycle: int, auc, probability_separation, mean_nll) -> dict:
    return {
        "cycle": int(cycle),
        "dev_auc": None if auc is None else float(auc),
        "dev_probability_separation": None if probability_separation is None else float(probability_separation),
        "dev_mean_nll": None if mean_nll is None else float(mean_nll),
    }


def selection_key(selection: dict | None) -> tuple[float, float, float]:
    if not selection:
        return (float("-inf"), float("-inf"), float("-inf"))
    auc = selection.get("dev_auc")
    separation = selection.get("dev_probability_separation")
    nll = selection.get("dev_mean_nll")
    return (
        float("-inf") if auc is None else float(auc),
        float("-inf") if separation is None else float(separation),
        float("-inf") if nll is None else -float(nll),
    )


def is_better_selection(candidate: dict, incumbent: dict | None) -> bool:
    return selection_key(candidate) > selection_key(incumbent)


def state_best_selection(state: dict) -> dict | None:
    cycle = state.get("best_cycle")
    if cycle is None:
        return None
    return discriminator_selection(
        cycle=int(cycle),
        auc=state.get("best_dev_auc"),
        probability_separation=state.get("best_dev_probability_separation"),
        mean_nll=state.get("best_dev_nll"),
    )


def apply_best_selection_to_state(state: dict, selection: dict) -> None:
    state["best_selection_policy"] = SELECTION_POLICY
    state["best_cycle"] = int(selection["cycle"])
    state["best_dev_auc"] = selection["dev_auc"]
    state["best_dev_probability_separation"] = selection["dev_probability_separation"]
    state["best_dev_nll"] = selection["dev_mean_nll"]


def best_selection_from_history(exp: Path) -> dict | None:
    best = None
    baseline_path = exp / "baseline_dev.json"
    if baseline_path.exists():
        baseline = read_json(baseline_path)
        candidate = discriminator_selection(
            cycle=0, auc=baseline.get("auc"),
            probability_separation=baseline.get("probability_separation"),
            mean_nll=baseline.get("mean_nll"),
        )
        if is_better_selection(candidate, best):
            best = candidate
    history_path = exp / "history.jsonl"
    if history_path.exists():
        for raw in history_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            if "cycle" not in row:
                continue
            candidate = discriminator_selection(
                cycle=int(row["cycle"]), auc=row.get("dev_auc"),
                probability_separation=row.get("dev_probability_separation"),
                mean_nll=row.get("dev_mean_nll"),
            )
            if is_better_selection(candidate, best):
                best = candidate
    return best


def generation_for_cycle(exp: Path, cycle: int) -> Path | None:
    if cycle <= 0:
        return None
    path = exp / "checkpoints" / "generations" / f"cycle-{cycle:06d}"
    return path if path.is_dir() else None


def evaluate(model, examples, pad_token_id, pipeline, *, precision: str,
             microbatch_questions: int, max_microbatch_tokens: int, label: str,
             pair_margin: float):
    import torch
    model.eval()
    groups = pipeline.pack_complete_questions(examples, microbatch_questions, max_microbatch_tokens)
    nll_sum = 0.0
    correct = 0
    pos_n = neg_n = pos_correct = neg_correct = 0
    pos_prob_sum = neg_prob_sum = 0.0
    scores: list[float] = []
    labels: list[bool] = []
    pair_scores: dict[str, dict[int, float]] = {}
    q = 0
    started = time.perf_counter()
    with torch.inference_mode():
        for group in groups:
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                logits, _ = model(group, pad_token_id)
            for ex, z in zip(group, logits):
                if ex["candidate_ids"] != ["false", "true"]:
                    raise RuntimeError(f"non-Boolean example entered lexeme evaluation: {ex['id']}")
                probs = z[:2].float().softmax(-1)
                gold = int(ex["gold_index"])
                p_true = float(probs[1].item())
                pred = int(p_true >= 0.5)
                nll_sum += -math.log(max(float(probs[gold].item()), 1e-30))
                correct += int(pred == gold)
                truth = bool(gold)
                scores.append(p_true)
                labels.append(truth)
                family = ex.get("family_id")
                if not isinstance(family, str) or not family:
                    raise RuntimeError(f"lexeme evaluation example lacks family_id: {ex.get('id')}")
                sides = pair_scores.setdefault(family, {})
                if gold in sides:
                    raise RuntimeError(f"duplicate paired lexeme evaluation side: {family}")
                # Use the same offset-invariant Boolean log-odds score used by the
                # phase-2 ranking loss, so dev measures exactly what training targets.
                sides[gold] = float((z[1].float() - z[0].float()).item())
                if truth:
                    pos_n += 1
                    pos_correct += int(pred == 1)
                    pos_prob_sum += p_true
                else:
                    neg_n += 1
                    neg_correct += int(pred == 0)
                    neg_prob_sum += p_true
                q += 1
    if q == 0:
        raise RuntimeError("evaluation set is empty")
    pos_acc = pos_correct / pos_n if pos_n else None
    neg_acc = neg_correct / neg_n if neg_n else None
    paired = paired_ranking_metrics(pair_scores, pair_margin)
    metrics = {
        "questions": q,
        "accuracy": correct / q,
        "top1_error": 1.0 - (correct / q),
        "mean_nll": nll_sum / q,
        "positive_accuracy": pos_acc,
        "negative_accuracy": neg_acc,
        "balanced_accuracy": (pos_acc + neg_acc) / 2 if pos_acc is not None and neg_acc is not None else None,
        "mean_p_true_on_true_suffix": pos_prob_sum / pos_n if pos_n else None,
        "mean_p_true_on_false_suffix": neg_prob_sum / neg_n if neg_n else None,
        "probability_separation": (pos_prob_sum / pos_n - neg_prob_sum / neg_n) if pos_n and neg_n else None,
        "auc": binary_auc(scores, labels),
        "pair_count": paired["pair_count"],
        "pair_win_rate": paired["pair_win_rate"],
        "mean_pair_logodds_gap": paired["mean_pair_logodds_gap"],
        "pair_margin_satisfied_rate": paired["pair_margin_satisfied_rate"],
        "pair_margin": pair_margin,
        "elapsed_seconds": time.perf_counter() - started,
    }
    emit("evaluation_done", label=label, **metrics)
    return metrics




def select_canonical_half(canonical_pairs, *, seed: int):
    """Select exactly half of the immutable canonical dev pairs without mutation."""
    if len(canonical_pairs) < 2 or len(canonical_pairs) % 2:
        raise RuntimeError("canonical dev pair count must be positive and even for half replacement")
    return random.Random(seed).sample(canonical_pairs, len(canonical_pairs) // 2)


def flatten_pairs(pairs):
    return [ex for false_ex, true_ex in pairs for ex in (false_ex, true_ex)]


def select_fresh_dev_rows(dev_manifest, canonical_source_paths: set[str], *, count: int, seed: int):
    """Choose dev-only source files outside the canonical probe's source-file set."""
    candidates = [row for row in dev_manifest if row.get("relative") not in canonical_source_paths]
    if not candidates:
        raise RuntimeError("no non-canonical dev source files are available for rolling evaluation")
    return random.Random(seed).sample(candidates, min(count, len(candidates)))


def build_training_pairs(examples):
    """Return exact FALSE/TRUE example pairs keyed by family_id."""
    families = {}
    for ex in examples:
        if ex.get("candidate_ids") != ["false", "true"]:
            raise RuntimeError(f"non-Boolean example entered lexeme pair builder: {ex.get('id')}")
        family = ex.get("family_id")
        if not isinstance(family, str) or not family:
            raise RuntimeError(f"lexeme example lacks family_id: {ex.get('id')}")
        gold = int(ex.get("gold_index"))
        if gold not in (0, 1):
            raise RuntimeError(f"lexeme example lacks deterministic Boolean gold: {ex.get('id')}")
        sides = families.setdefault(family, {})
        if gold in sides:
            raise RuntimeError(f"duplicate {'TRUE' if gold else 'FALSE'} member for lexeme family {family}")
        sides[gold] = ex
    pairs = []
    for family, sides in families.items():
        if set(sides) != {0, 1}:
            raise RuntimeError(f"lexeme family is not an exact FALSE/TRUE pair: {family}")
        pairs.append((sides[0], sides[1]))
    if not pairs:
        raise RuntimeError("no complete lexeme training pairs")
    return pairs


def best_available_generation_selection(exp: Path, preferred: dict | None) -> tuple[dict, Path]:
    """Resolve a retained discriminator generation for phase-2 branching."""
    if preferred is not None:
        generation = generation_for_cycle(exp, int(preferred["cycle"]))
        if generation is not None:
            return preferred, generation
    best = None
    history_path = exp / "history.jsonl"
    if history_path.exists():
        for raw in history_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            cycle = int(row.get("cycle", 0))
            generation = generation_for_cycle(exp, cycle)
            if generation is None:
                continue
            candidate = discriminator_selection(
                cycle=cycle, auc=row.get("dev_auc"),
                probability_separation=row.get("dev_probability_separation"),
                mean_nll=row.get("dev_mean_nll"),
            )
            if is_better_selection(candidate, best):
                best = candidate
    if best is None:
        raise RuntimeError("no retained trained discriminator generation is available for ranking phase")
    generation = generation_for_cycle(exp, int(best["cycle"]))
    assert generation is not None
    return best, generation


def move_optimizer_state_to_cuda(optimizer) -> None:
    import torch
    for state in optimizer.state.values():
        for key, value in list(state.items()):
            if torch.is_tensor(value):
                state[key] = value.cuda()


def save_rng(path: Path) -> None:
    import torch
    torch.save({
        "python": random.getstate(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": torch.cuda.get_rng_state_all(),
    }, path)


def load_rng(path: Path) -> None:
    import torch
    state = torch.load(path, map_location="cpu", weights_only=False)
    random.setstate(state["python"])
    torch.set_rng_state(state["torch_cpu"])
    torch.cuda.set_rng_state_all(state["torch_cuda"])


def checkpoint_config(experiment: dict, training_config: dict, ranking_config: dict, cycle: int, global_step: int) -> dict:
    return {
        "schema_version": "openjev-decision-pipeline-v1",
        "model": experiment["model"],
        "revision": experiment["requested_revision"],
        "resolved_model_revision": experiment["resolved_model_revision"],
        "set_head": experiment["set_head"],
        "max_length": experiment["max_length"],
        "initialization": experiment["initialization"],
        "task": experiment["task"],
        "backbone_frozen": True,
        "head_lr": training_config["head_lr"],
        "training_objective": RANKING_OBJECTIVE,
        "ranking_weight": ranking_config["ranking_weight"],
        "ranking_margin": ranking_config["ranking_margin"],
        "main_computer_experiment_sha256": experiment["experiment_sha256"],
        "main_computer_cycle": cycle,
        "main_computer_global_step": global_step,
    }


def save_generation(*, exp: Path, model, optimizer, experiment: dict, training_config: dict, ranking_config: dict,
                    cycle: int, global_step: int, meta: dict) -> Path:
    import torch
    from safetensors.torch import save_file
    generations = exp / "checkpoints" / "generations"
    final = generations / f"cycle-{cycle:06d}"
    temp = generations / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint generation already exists: {final}")
    temp.mkdir(parents=True)
    emit("checkpoint_write_start", cycle=cycle, directory=str(final))
    save_file(head_state(model), temp / "head.safetensors")
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    save_rng(temp / "rng_state.pt")
    atomic_json(temp / "config.json", checkpoint_config(experiment, training_config, ranking_config, cycle, global_step))
    atomic_json(temp / "meta.json", meta)
    os.replace(temp, final)
    emit("checkpoint_write_done", cycle=cycle, directory=str(final), head_sha256=sha256_file(final / "head.safetensors"))
    return final


def ensure_best_package(exp: Path, model, generation: Path, selection: dict) -> None:
    from safetensors.torch import load_file, save_file
    best = exp / "checkpoints" / "best"
    best.mkdir(parents=True, exist_ok=True)
    full_state = full_state_cpu(model)
    selected_head = load_file(str(generation / "head.safetensors"), device="cpu")
    expected_head_keys = {k for k in full_state if not k.startswith("backbone.")}
    if set(selected_head) != expected_head_keys:
        raise RuntimeError("best-checkpoint head keys do not match current NanoJev head")
    for key, value in selected_head.items():
        full_state[key] = value.detach().cpu().contiguous().clone()
    tmp = best / "best.safetensors.tmp"
    if tmp.exists():
        tmp.unlink()
    save_file(full_state, tmp)
    os.replace(tmp, best / "best.safetensors")
    shutil.copy2(generation / "config.json", best / "config.json")
    for dirname in ("tokenizer", "backbone_config"):
        src = exp / "artifacts" / dirname
        dst = best / dirname
        if not dst.exists():
            shutil.copytree(src, dst)
    meta = read_json(generation / "meta.json")
    meta["selection_policy"] = SELECTION_POLICY
    meta["selected_cycle"] = int(selection["cycle"])
    meta["selected_dev_auc"] = selection["dev_auc"]
    meta["selected_dev_probability_separation"] = selection["dev_probability_separation"]
    meta["selected_dev_nll"] = selection["dev_mean_nll"]
    atomic_json(best / "selection.json", meta)


def migrate_best_selection(exp: Path, state: dict, model) -> bool:
    historical = best_selection_from_history(exp)
    if historical is None:
        return False
    current = state_best_selection(state)
    policy_current = state.get("best_selection_policy") == SELECTION_POLICY
    same = policy_current and current is not None and selection_key(current) == selection_key(historical) and int(current["cycle"]) == int(historical["cycle"])
    if same:
        return False

    selected = historical
    generation = generation_for_cycle(exp, int(selected["cycle"]))
    if int(selected["cycle"]) > 0 and generation is None:
        available = None
        history_path = exp / "history.jsonl"
        if history_path.exists():
            for raw in history_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                row = json.loads(raw)
                cycle = int(row.get("cycle", 0))
                if generation_for_cycle(exp, cycle) is None:
                    continue
                candidate = discriminator_selection(
                    cycle=cycle, auc=row.get("dev_auc"),
                    probability_separation=row.get("dev_probability_separation"),
                    mean_nll=row.get("dev_mean_nll"),
                )
                if is_better_selection(candidate, available):
                    available = candidate
        if available is None:
            emit("best_selection_migration_deferred", reason="historical_best_generation_missing", historical_best=selected)
            return False
        emit("best_selection_history_unavailable", historical_best=selected, selected_available=available)
        selected = available
        generation = generation_for_cycle(exp, int(selected["cycle"]))

    old_cycle = state.get("best_cycle")
    apply_best_selection_to_state(state, selected)
    if generation is not None:
        ensure_best_package(exp, model, generation, selected)
    emit(
        "best_selection_migrated",
        policy=SELECTION_POLICY, old_best_cycle=old_cycle, best_cycle=selected["cycle"],
        best_dev_auc=selected["dev_auc"],
        best_dev_probability_separation=selected["dev_probability_separation"],
        best_dev_nll=selected["dev_mean_nll"],
    )
    return True


def garbage_collect(exp: Path, keep: int, *, protected_cycles=()) -> None:
    dirs = sorted(p for p in (exp / "checkpoints" / "generations").glob("cycle-*") if p.is_dir())
    newest = set(dirs[-keep:])
    protected = {
        exp / "checkpoints" / "generations" / f"cycle-{int(cycle):06d}"
        for cycle in protected_cycles if cycle is not None and int(cycle) > 0
    }
    keep_paths = newest | protected
    for old in dirs:
        if old in keep_paths:
            continue
        shutil.rmtree(old)
        emit("checkpoint_generation_deleted", directory=str(old))


def load_model_and_optimizer(*, exp: Path, experiment: dict, state: dict,
                             training_config: dict, DecisionModel, local_files_only: bool,
                             resume_generation_override: Path | None = None):
    import torch
    from safetensors.torch import load_file
    from transformers import AutoModel

    emit("frozen_backbone_load_start", model=experiment["model"], revision=experiment["resolved_model_revision"])
    backbone = AutoModel.from_pretrained(
        experiment["model"], revision=experiment["resolved_model_revision"], dtype=torch.float32,
        attn_implementation="sdpa", trust_remote_code=False, local_files_only=local_files_only,
    )
    model = DecisionModel(backbone, experiment["set_head"])
    latest = state.get("latest_generation")
    if resume_generation_override is not None:
        generation = resume_generation_override.resolve(strict=True)
        head_path = generation / "head.safetensors"
        emit("ranking_phase_best_head_load_start", generation=str(generation))
    elif latest:
        generation = Path(latest).resolve(strict=True)
        head_path = generation / "head.safetensors"
        emit("resume_head_load_start", generation=str(generation))
    else:
        generation = None
        head_path = exp / "artifacts" / "initial_head.safetensors"
        emit("initial_head_load_start", path=str(head_path))
    weights = load_file(str(head_path), device="cpu")
    incompatible = model.load_state_dict(weights, strict=False)
    if incompatible.unexpected_keys or any(not k.startswith("backbone.") for k in incompatible.missing_keys):
        raise RuntimeError(f"decision-head load mismatch: {incompatible}")

    model.backbone.config.use_cache = False
    body = list(model.backbone.parameters())
    for p in body:
        p.requires_grad_(False)
    head = [p for name, p in model.named_parameters() if not name.startswith("backbone.")]
    if not head:
        raise RuntimeError("NanoJev decision head has no trainable parameters")
    for p in head:
        p.requires_grad_(True)
    model.cuda()
    model.backbone.eval()
    optimizer = torch.optim.AdamW(head, lr=training_config["head_lr"], weight_decay=training_config["weight_decay"])
    if generation is not None:
        optimizer.load_state_dict(torch.load(generation / "optimizer.pt", map_location="cpu", weights_only=False))
        move_optimizer_state_to_cuda(optimizer)
        load_rng(generation / "rng_state.pt")
        emit("resume_optimizer_rng_loaded", cycle=state["cycle"], global_step=state["global_step"])
    else:
        seed = int(experiment["seed"])
        random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    emit("frozen_backbone_load_done")
    return model, optimizer, body, head


def self_test() -> None:
    assert cyclic_slice([1, 2, 3], 2, 3) == [3, 1, 2]
    assert abs(binary_auc([0.1, 0.2, 0.8, 0.9], [False, False, True, True]) - 1.0) < 1e-12
    assert abs(binary_auc([0.9, 0.8, 0.2, 0.1], [False, False, True, True]) - 0.0) < 1e-12
    a = discriminator_selection(cycle=1, auc=0.60, probability_separation=0.01, mean_nll=0.69)
    b = discriminator_selection(cycle=2, auc=0.61, probability_separation=0.001, mean_nll=0.90)
    c = discriminator_selection(cycle=3, auc=0.61, probability_separation=0.002, mean_nll=1.20)
    d = discriminator_selection(cycle=4, auc=0.61, probability_separation=0.002, mean_nll=0.70)
    assert is_better_selection(b, a)
    assert is_better_selection(c, b)
    assert is_better_selection(d, c)
    fake = [
        {"id": "p-false", "family_id": "p", "candidate_ids": ["false", "true"], "gold_index": 0},
        {"id": "p-true", "family_id": "p", "candidate_ids": ["false", "true"], "gold_index": 1},
    ]
    pairs = build_training_pairs(fake)
    assert len(pairs) == 1 and pairs[0][0]["gold_index"] == 0 and pairs[0][1]["gold_index"] == 1
    pair_metrics = paired_ranking_metrics({"p": {0: -0.05, 1: 0.15}}, 0.10)
    assert pair_metrics["pair_win_rate"] == 1.0
    assert abs(pair_metrics["mean_pair_logodds_gap"] - 0.20) < 1e-12
    assert pair_metrics["pair_margin_satisfied_rate"] == 1.0
    print(json.dumps({"ok": True, "self_test": "passed", "selection_policy": SELECTION_POLICY, "objective": RANKING_OBJECTIVE}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-dir")
    p.add_argument("--cycles-this-run", type=int, default=100)
    p.add_argument("--cycle-seconds", type=float, default=75.0)
    p.add_argument("--train-files-per-cycle", type=int, default=40)
    p.add_argument("--train-pairs-per-cycle", type=int, default=128,
                   help="Each pair yields one TRUE and one FALSE question")
    p.add_argument("--batch-questions", type=int, default=8)
    p.add_argument("--microbatch-questions", type=int, default=4)
    p.add_argument("--max-microbatch-tokens", type=int, default=8192)
    p.add_argument("--head-lr", type=float, default=2e-4)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--ranking-weight", type=float, default=0.25,
                   help="Auxiliary pairwise margin-loss weight")
    p.add_argument("--ranking-margin", type=float, default=0.10,
                   help="Required TRUE-logit compatibility gap between real and false suffixes")
    p.add_argument("--precision", choices=["bf16", "fp32"], default="bf16")
    p.add_argument("--keep-generations", type=int, default=2)
    p.add_argument("--local-files-only", action="store_true")
    p.add_argument("--disable-native-triton", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test(); return
    if not args.experiment_dir:
        p.error("--experiment-dir is required")
    if min(args.cycles_this_run, args.cycle_seconds, args.train_files_per_cycle,
           args.train_pairs_per_cycle, args.batch_questions, args.microbatch_questions) <= 0:
        p.error("cycle/data/batch settings must be positive")
    if args.max_microbatch_tokens < 0 or args.keep_generations < 1:
        p.error("invalid token/checkpoint limits")
    if not math.isfinite(args.head_lr) or args.head_lr <= 0:
        p.error("--head-lr must be finite and positive")
    if args.batch_questions % 2:
        p.error("--batch-questions must be even so every optimizer step contains complete lexeme pairs")
    if not math.isfinite(args.ranking_weight) or args.ranking_weight <= 0:
        p.error("--ranking-weight must be finite and positive")
    if not math.isfinite(args.ranking_margin) or args.ranking_margin <= 0:
        p.error("--ranking-margin must be finite and positive")

    exp = Path(args.experiment_dir).expanduser().resolve(strict=True)
    experiment = read_json(exp / "experiment.json")
    state_path = exp / "training_state.json"
    state = read_json(state_path)
    if experiment.get("schema_version") != EXPERIMENT_SCHEMA or state.get("schema_version") != STATE_SCHEMA:
        raise RuntimeError("this trainer requires a fresh next-lexeme experiment")
    if experiment.get("task") != "binary_next_lexeme_verification":
        raise RuntimeError("wrong training objective; initialize a new lexeme experiment")
    if experiment.get("backbone_frozen") is not True or state.get("phase") != PHASE:
        raise RuntimeError("frozen-backbone lexeme invariant missing")
    if state.get("experiment_sha256") != experiment.get("experiment_sha256"):
        raise RuntimeError("training state does not belong to experiment")

    training_config = {
        "schema_version": TRAINING_SCHEMA,
        "cycle_seconds": args.cycle_seconds,
        "train_files_per_cycle": args.train_files_per_cycle,
        "train_pairs_per_cycle": args.train_pairs_per_cycle,
        "batch_questions": args.batch_questions,
        "microbatch_questions": args.microbatch_questions,
        "max_microbatch_tokens": args.max_microbatch_tokens,
        "head_lr": args.head_lr,
        "weight_decay": args.weight_decay,
        "precision": args.precision,
        "backbone_frozen": True,
        "task": experiment["task"],
        "disable_native_triton": args.disable_native_triton,
    }
    config_path = exp / "training_config.json"
    if config_path.exists():
        if read_json(config_path) != training_config:
            raise RuntimeError("training configuration differs from established run; initialize a new experiment")
    else:
        atomic_json(config_path, training_config)

    ranking_config = {
        "schema_version": RANKING_SCHEMA,
        "training_objective": RANKING_OBJECTIVE,
        "ranking_weight": args.ranking_weight,
        "ranking_margin": args.ranking_margin,
        "branch_from": "best_dev_auc_checkpoint",
        "selection_policy": SELECTION_POLICY,
    }
    ranking_config_path = exp / "ranking_training_config.json"
    if ranking_config_path.exists():
        if read_json(ranking_config_path) != ranking_config:
            raise RuntimeError("ranking configuration differs from established phase-2 run")
    else:
        atomic_json(ranking_config_path, ranking_config)

    repo_root = Path(experiment["repo_root"]).resolve(strict=True)
    nanojev_root = Path(experiment["nanojev_root"]).resolve(strict=True)
    data = load_local_module("nanojev_code_lexeme_data_for_train", Path(__file__).with_name("nanojev_code_lexeme_data.py"))
    pipeline, DecisionModel = import_nanojev(nanojev_root)

    import torch
    from transformers import AutoTokenizer
    if args.disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported by CUDA device")
    torch.backends.cuda.matmul.allow_tf32 = False

    tokenizer = AutoTokenizer.from_pretrained(str(exp / "artifacts" / "tokenizer"), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    dev_examples, _ = pipeline.load_training_examples(experiment["dev_probe"], tokenizer, experiment["max_length"])
    pipeline.pack_complete_questions(dev_examples, args.microbatch_questions, args.max_microbatch_tokens)
    canonical_dev_pairs = build_training_pairs(dev_examples)
    if len(canonical_dev_pairs) != 64:
        raise RuntimeError(f"rolling evaluator requires the established 64-pair canonical dev probe; found {len(canonical_dev_pairs)}")
    canonical_probe_records = read_jsonl(Path(experiment["dev_probe"]))
    canonical_source_paths = {
        row.get("metadata", {}).get("source_path")
        for row in canonical_probe_records
        if row.get("metadata", {}).get("source_path")
    }
    train_manifest = read_json(Path(experiment["manifests"]["train"]))
    dev_manifest = read_json(Path(experiment["manifests"]["dev"]))
    fresh_dev_manifest = [row for row in dev_manifest if row.get("relative") not in canonical_source_paths]
    if not fresh_dev_manifest:
        raise RuntimeError("dev manifest contains no files outside the canonical dev probe")
    super_suffix = read_json(Path(experiment["super_suffix"]))

    ranking_origin_selection = None
    ranking_origin_generation = None
    if state.get("training_objective") != RANKING_OBJECTIVE:
        preferred = state_best_selection(state) or best_selection_from_history(exp)
        ranking_origin_selection, ranking_origin_generation = best_available_generation_selection(exp, preferred)
        emit(
            "ranking_phase_prepare", objective=RANKING_OBJECTIVE,
            origin_cycle=ranking_origin_selection["cycle"],
            origin_dev_auc=ranking_origin_selection["dev_auc"],
            origin_dev_probability_separation=ranking_origin_selection["dev_probability_separation"],
            generation=str(ranking_origin_generation),
        )

    emit("trainer_load_start", cycle=state["cycle"], global_step=state["global_step"], phase=state["phase"])
    model, optimizer, body, head = load_model_and_optimizer(
        exp=exp, experiment=experiment, state=state, training_config=training_config,
        DecisionModel=DecisionModel, local_files_only=args.local_files_only,
        resume_generation_override=ranking_origin_generation,
    )
    emit("trainer_load_done", gpu=torch.cuda.get_device_name(0),
         body_params=sum(p.numel() for p in body), head_params=sum(p.numel() for p in head))

    if migrate_best_selection(exp, state, model):
        atomic_json(state_path, state)

    if ranking_origin_generation is not None and ranking_origin_selection is not None:
        state["training_objective"] = RANKING_OBJECTIVE
        state["ranking_origin_cycle"] = int(ranking_origin_selection["cycle"])
        state["ranking_origin_dev_auc"] = ranking_origin_selection["dev_auc"]
        state["ranking_origin_dev_probability_separation"] = ranking_origin_selection["dev_probability_separation"]
        state["ranking_origin_generation"] = str(ranking_origin_generation)
        origin_meta = read_json(ranking_origin_generation / "meta.json")
        state["ranking_origin_global_step"] = origin_meta.get("global_step")
        # First phase-2 deltas must be measured from the branch checkpoint, not from
        # a later but weaker phase-1 head that happened to run before the patch.
        state["last_dev_nll"] = ranking_origin_selection["dev_mean_nll"]
        state["last_dev_auc"] = ranking_origin_selection["dev_auc"]
        state["last_dev_probability_separation"] = ranking_origin_selection["dev_probability_separation"]
        # Until the first ranked cycle commits, a restart must return to the same branch point.
        state["latest_generation"] = str(ranking_origin_generation)
        atomic_json(state_path, state)
        emit(
            "ranking_phase_started", objective=RANKING_OBJECTIVE,
            origin_cycle=state["ranking_origin_cycle"],
            ranking_weight=args.ranking_weight, ranking_margin=args.ranking_margin,
        )

    if state["cycle"] == 0 and state["last_dev_nll"] is None:
        baseline = evaluate(model, dev_examples, tokenizer.pad_token_id, pipeline,
                            precision=args.precision, microbatch_questions=args.microbatch_questions,
                            max_microbatch_tokens=args.max_microbatch_tokens, label="dev_baseline",
                            pair_margin=args.ranking_margin)
        state["last_dev_nll"] = baseline["mean_nll"]
        state["last_dev_auc"] = baseline["auc"]
        state["last_dev_probability_separation"] = baseline["probability_separation"]
        state["last_dev_pair_win_rate"] = baseline["pair_win_rate"]
        state["last_dev_mean_pair_logodds_gap"] = baseline["mean_pair_logodds_gap"]
        state["last_dev_pair_margin_satisfied_rate"] = baseline["pair_margin_satisfied_rate"]
        apply_best_selection_to_state(state, discriminator_selection(
            cycle=0, auc=baseline["auc"],
            probability_separation=baseline["probability_separation"],
            mean_nll=baseline["mean_nll"],
        ))
        atomic_json(exp / "baseline_dev.json", baseline)
        atomic_json(state_path, state)

    history_path = exp / "history.jsonl"
    backbone_probe_name = experiment["parameter_probes"]["backbone"]["name"]
    head_probe_name = experiment["parameter_probes"]["head"]["name"]

    for _ in range(args.cycles_this_run):
        cycle = int(state["cycle"]) + 1
        cycle_started = time.perf_counter()
        source_start = int(state["source_cursor"]) % len(train_manifest)
        rows = cyclic_slice(train_manifest, source_start, args.train_files_per_cycle)
        shard_seed = int(experiment["seed"]) + cycle * 10007
        emit("cycle_start", cycle=cycle, global_step=state["global_step"], source_start=source_start,
             selected_files=len(rows), requested_training_seconds=args.cycle_seconds,
             objective=RANKING_OBJECTIVE)

        docs = data.load_docs(rows, repo_root)
        records = data.sample_paired_records(
            docs=docs, tokenizer=tokenizer, split="train", pair_count=args.train_pairs_per_cycle,
            max_prefix_tokens=experiment["max_prefix_tokens"], seed=shard_seed,
            pools=super_suffix, max_lexeme_tokens=experiment["max_lexeme_tokens"],
        )
        shard_path = exp / "shards" / f"cycle-{cycle:06d}.jsonl"
        data.write_jsonl(shard_path, records)
        train_examples, _ = pipeline.load_training_examples(shard_path, tokenizer, experiment["max_length"])
        if len(train_examples) < args.batch_questions:
            raise RuntimeError("fewer lexeme questions than one batch")
        pipeline.pack_complete_questions(train_examples, args.microbatch_questions, args.max_microbatch_tokens)
        train_pairs = build_training_pairs(train_examples)
        if len(train_pairs) < args.batch_questions // 2:
            raise RuntimeError("fewer complete lexeme pairs than one effective batch")
        positives = sum(bool(ex["gold_index"]) for ex in train_examples)
        emit("cycle_shard_ready", cycle=cycle, documents=len(docs), questions=len(train_examples),
             positive_questions=positives, negative_questions=len(train_examples)-positives,
             shard=str(shard_path), sha256=sha256_file(shard_path))

        backbone_before = named_probe_digest(model, backbone_probe_name)
        head_before = named_probe_digest(model, head_probe_name)
        training_started = time.perf_counter()
        cycle_steps = train_questions = train_correct = 0
        train_nll_sum = 0.0
        train_rank_loss_sum = 0.0
        train_pair_gap_sum = 0.0
        train_pair_n = train_pair_margin_satisfied = 0
        train_pos_n = train_neg_n = train_pos_correct = train_neg_correct = 0
        last_head_grad_norm = 0.0
        rng = random.Random(int(experiment["seed"]) + cycle * 7919)

        while True:
            if cycle_steps > 0 and time.perf_counter() - training_started >= args.cycle_seconds:
                break
            sampled_pairs = rng.sample(train_pairs, args.batch_questions // 2)
            batch = [ex for false_ex, true_ex in sampled_pairs for ex in (false_ex, true_ex)]
            groups = pipeline.pack_complete_questions(batch, args.microbatch_questions, args.max_microbatch_tokens)
            model.train()
            model.backbone.eval()
            optimizer.zero_grad(set_to_none=True)
            step_nll = 0.0
            step_correct = 0
            step_q = 0
            bce_losses = []
            pair_scores = {}
            for group in groups:
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                    logits, _ = model(group, tokenizer.pad_token_id)
                    losses = pipeline.grouped_target_loss(logits, group, "gold_distribution")
                bce_losses.append(losses)
                train_nll_sum += float(losses.detach().float().sum().item())
                step_nll += float(losses.detach().float().sum().item())
                for ex, z in zip(group, logits):
                    if ex["candidate_ids"] != ["false", "true"]:
                        raise RuntimeError("non-Boolean example entered lexeme training")
                    gold = int(ex["gold_index"])
                    pred = int(torch.argmax(z[:2]).item())
                    step_correct += int(pred == gold)
                    train_correct += int(pred == gold)
                    step_q += 1
                    train_questions += 1
                    if gold:
                        train_pos_n += 1; train_pos_correct += int(pred == 1)
                    else:
                        train_neg_n += 1; train_neg_correct += int(pred == 0)
                    family = ex["family_id"]
                    sides = pair_scores.setdefault(family, {})
                    if gold in sides:
                        raise RuntimeError(f"duplicate pair side reached optimizer step: {family}")
                    # Boolean log-odds is invariant to a shared logit offset and is the
                    # natural scalar compatibility score for pairwise ranking.
                    sides[gold] = z[1].float() - z[0].float()

            if len(pair_scores) != len(sampled_pairs) or any(set(sides) != {0, 1} for sides in pair_scores.values()):
                raise RuntimeError("optimizer step lost a TRUE/FALSE member of a sampled lexeme pair")
            bce_loss = torch.cat(bce_losses).sum() / len(batch)
            gaps = torch.stack([sides[1] - sides[0] for sides in pair_scores.values()])
            rank_losses = torch.relu(args.ranking_margin - gaps)
            rank_loss = rank_losses.mean()
            loss = bce_loss + args.ranking_weight * rank_loss
            if not torch.isfinite(loss):
                raise RuntimeError(f"cycle {cycle}: nonfinite BCE+ranking training loss")
            loss.backward()

            step_rank_loss = float(rank_loss.detach().item())
            step_pair_gap = float(gaps.detach().mean().item())
            step_margin_satisfied = float((gaps.detach() >= args.ranking_margin).float().mean().item())
            train_rank_loss_sum += step_rank_loss * len(sampled_pairs)
            train_pair_gap_sum += step_pair_gap * len(sampled_pairs)
            train_pair_margin_satisfied += int((gaps.detach() >= args.ranking_margin).sum().item())
            train_pair_n += len(sampled_pairs)

            body_norm = grad_norm(body)
            last_head_grad_norm = grad_norm(head)
            if body_norm != 0.0:
                raise RuntimeError("frozen backbone produced gradients")
            torch.nn.utils.clip_grad_norm_(head, 1.0, error_if_nonfinite=True)
            optimizer.step()
            cycle_steps += 1
            state["global_step"] = int(state["global_step"]) + 1
            emit("cycle_train_step", cycle=cycle, cycle_step=cycle_steps,
                 global_step=state["global_step"], phase=PHASE, training_objective=RANKING_OBJECTIVE,
                 mean_nll=step_nll/max(step_q,1), top1_error=1.0-step_correct/max(step_q,1),
                 pair_rank_loss=step_rank_loss, mean_pair_logodds_gap=step_pair_gap,
                 pair_margin_satisfied_rate=step_margin_satisfied,
                 body_grad_norm=body_norm, head_grad_norm=last_head_grad_norm,
                 elapsed_training_seconds=time.perf_counter()-training_started)

        # Keep the original 64-pair probe immutable. For the stochastic ruler,
        # return to that canonical set every cycle, retain a random 32 pairs, and
        # temporarily replace the other 32 with fresh pairs from non-canonical dev files.
        rotation_seed = int(experiment["seed"]) + cycle * ROLLING_DEV_SEED_STRIDE
        retained_pairs = select_canonical_half(canonical_dev_pairs, seed=rotation_seed)
        fresh_pair_count = len(canonical_dev_pairs) - len(retained_pairs)
        fresh_rows = select_fresh_dev_rows(
            dev_manifest, canonical_source_paths, count=ROLLING_DEV_SOURCE_FILES, seed=rotation_seed + 1
        )
        fresh_docs = data.load_docs(fresh_rows, repo_root)
        fresh_records = data.sample_paired_records(
            docs=fresh_docs, tokenizer=tokenizer, split="dev", pair_count=fresh_pair_count,
            max_prefix_tokens=experiment["max_prefix_tokens"], seed=rotation_seed + 2,
            pools=super_suffix, max_lexeme_tokens=experiment["max_lexeme_tokens"],
        )
        rolling_probe_path = exp / "probes" / "rolling" / f"cycle-{cycle:06d}-fresh.jsonl"
        data.write_jsonl(rolling_probe_path, fresh_records)
        fresh_examples, _ = pipeline.load_training_examples(
            rolling_probe_path, tokenizer, experiment["max_length"]
        )
        fresh_pairs = build_training_pairs(fresh_examples)
        if len(fresh_pairs) != fresh_pair_count:
            raise RuntimeError("fresh rolling dev probe did not produce the requested complete pair count")
        rolling_examples = flatten_pairs(retained_pairs) + flatten_pairs(fresh_pairs)
        random.Random(rotation_seed + 3).shuffle(rolling_examples)
        emit(
            "rolling_dev_ready", cycle=cycle, mode=ROLLING_DEV_MODE,
            canonical_total_pairs=len(canonical_dev_pairs), canonical_retained_pairs=len(retained_pairs),
            fresh_pairs=len(fresh_pairs), fresh_source_files=len(fresh_docs),
            rotation_seed=rotation_seed, fresh_probe=str(rolling_probe_path),
            fresh_probe_sha256=sha256_file(rolling_probe_path),
        )

        # Preserve the historical fixed ruler and checkpoint-selection semantics.
        dev = evaluate(model, dev_examples, tokenizer.pad_token_id, pipeline,
                       precision=args.precision, microbatch_questions=args.microbatch_questions,
                       max_microbatch_tokens=args.max_microbatch_tokens, label=f"dev_cycle_{cycle}",
                       pair_margin=args.ranking_margin)
        rolling_dev = evaluate(model, rolling_examples, tokenizer.pad_token_id, pipeline,
                               precision=args.precision, microbatch_questions=args.microbatch_questions,
                               max_microbatch_tokens=args.max_microbatch_tokens,
                               label=f"rolling_dev_cycle_{cycle}", pair_margin=args.ranking_margin)
        previous_dev = state.get("last_dev_nll")
        previous_auc = state.get("last_dev_auc")
        previous_separation = state.get("last_dev_probability_separation")
        previous_pair_win_rate = state.get("last_dev_pair_win_rate")
        previous_pair_gap = state.get("last_dev_mean_pair_logodds_gap")
        previous_pair_margin_rate = state.get("last_dev_pair_margin_satisfied_rate")
        same_rolling_mode = state.get("rolling_dev_mode") == ROLLING_DEV_MODE
        previous_rolling_auc = state.get("last_rolling_dev_auc") if same_rolling_mode else None
        previous_rolling_pair_win = state.get("last_rolling_dev_pair_win_rate") if same_rolling_mode else None
        previous_rolling_pair_gap = state.get("last_rolling_dev_mean_pair_logodds_gap") if same_rolling_mode else None
        previous_rolling_margin = state.get("last_rolling_dev_pair_margin_satisfied_rate") if same_rolling_mode else None
        previous_rolling_separation = state.get("last_rolling_dev_probability_separation") if same_rolling_mode else None
        previous_rolling_nll = state.get("last_rolling_dev_nll") if same_rolling_mode else None
        candidate_selection = discriminator_selection(
            cycle=cycle, auc=dev["auc"],
            probability_separation=dev["probability_separation"],
            mean_nll=dev["mean_nll"],
        )
        improved_best = is_better_selection(candidate_selection, state_best_selection(state))
        backbone_after = named_probe_digest(model, backbone_probe_name)
        head_after = named_probe_digest(model, head_probe_name)
        backbone_changed = backbone_after != backbone_before
        head_changed = head_after != head_before
        if backbone_changed:
            raise RuntimeError("frozen backbone changed during training cycle")
        if not head_changed:
            raise RuntimeError("decision head did not change during training cycle")

        state["cycle"] = cycle
        state["source_cursor"] = (source_start + len(rows)) % len(train_manifest)
        state["phase"] = PHASE
        state["training_objective"] = RANKING_OBJECTIVE
        state["last_dev_nll"] = dev["mean_nll"]
        state["last_dev_auc"] = dev["auc"]
        state["last_dev_probability_separation"] = dev["probability_separation"]
        state["last_dev_pair_win_rate"] = dev["pair_win_rate"]
        state["last_dev_mean_pair_logodds_gap"] = dev["mean_pair_logodds_gap"]
        state["last_dev_pair_margin_satisfied_rate"] = dev["pair_margin_satisfied_rate"]
        state["rolling_dev_mode"] = ROLLING_DEV_MODE
        state["last_rolling_dev_auc"] = rolling_dev["auc"]
        state["last_rolling_dev_pair_win_rate"] = rolling_dev["pair_win_rate"]
        state["last_rolling_dev_mean_pair_logodds_gap"] = rolling_dev["mean_pair_logodds_gap"]
        state["last_rolling_dev_pair_margin_satisfied_rate"] = rolling_dev["pair_margin_satisfied_rate"]
        state["last_rolling_dev_probability_separation"] = rolling_dev["probability_separation"]
        state["last_rolling_dev_nll"] = rolling_dev["mean_nll"]
        state["status"] = "training"
        result = {
            "cycle": cycle,
            "global_step": state["global_step"],
            "phase_after_cycle": PHASE,
            "steps": cycle_steps,
            "source_files": len(docs),
            "train_questions_available": len(train_examples),
            "train_questions_seen": train_questions,
            "train_mean_nll": train_nll_sum/max(train_questions,1),
            "train_pair_rank_loss": train_rank_loss_sum/max(train_pair_n,1),
            "train_mean_pair_logodds_gap": train_pair_gap_sum/max(train_pair_n,1),
            "train_pair_margin_satisfied_rate": train_pair_margin_satisfied/max(train_pair_n,1),
            "ranking_weight": args.ranking_weight,
            "ranking_margin": args.ranking_margin,
            "train_top1_error": 1.0-train_correct/max(train_questions,1),
            "train_positive_accuracy": train_pos_correct/train_pos_n if train_pos_n else None,
            "train_negative_accuracy": train_neg_correct/train_neg_n if train_neg_n else None,
            "dev_mean_nll": dev["mean_nll"],
            "dev_top1_error": dev["top1_error"],
            "dev_accuracy": dev["accuracy"],
            "dev_balanced_accuracy": dev["balanced_accuracy"],
            "dev_positive_accuracy": dev["positive_accuracy"],
            "dev_negative_accuracy": dev["negative_accuracy"],
            "dev_auc": dev["auc"],
            "dev_probability_separation": dev["probability_separation"],
            "dev_mean_p_true_on_true_suffix": dev["mean_p_true_on_true_suffix"],
            "dev_mean_p_true_on_false_suffix": dev["mean_p_true_on_false_suffix"],
            "dev_pair_count": dev["pair_count"],
            "dev_pair_win_rate": dev["pair_win_rate"],
            "dev_mean_pair_logodds_gap": dev["mean_pair_logodds_gap"],
            "dev_pair_margin_satisfied_rate": dev["pair_margin_satisfied_rate"],
            "rolling_dev_mode": ROLLING_DEV_MODE,
            "rolling_dev_canonical_total_pairs": len(canonical_dev_pairs),
            "rolling_dev_canonical_retained_pairs": len(retained_pairs),
            "rolling_dev_fresh_pairs": len(fresh_pairs),
            "rolling_dev_fresh_source_files": len(fresh_docs),
            "rolling_dev_rotation_seed": rotation_seed,
            "rolling_dev_fresh_probe": str(rolling_probe_path),
            "rolling_dev_fresh_probe_sha256": sha256_file(rolling_probe_path),
            "rolling_dev_mean_nll": rolling_dev["mean_nll"],
            "rolling_dev_top1_error": rolling_dev["top1_error"],
            "rolling_dev_accuracy": rolling_dev["accuracy"],
            "rolling_dev_balanced_accuracy": rolling_dev["balanced_accuracy"],
            "rolling_dev_positive_accuracy": rolling_dev["positive_accuracy"],
            "rolling_dev_negative_accuracy": rolling_dev["negative_accuracy"],
            "rolling_dev_auc": rolling_dev["auc"],
            "rolling_dev_probability_separation": rolling_dev["probability_separation"],
            "rolling_dev_mean_p_true_on_true_suffix": rolling_dev["mean_p_true_on_true_suffix"],
            "rolling_dev_mean_p_true_on_false_suffix": rolling_dev["mean_p_true_on_false_suffix"],
            "rolling_dev_pair_count": rolling_dev["pair_count"],
            "rolling_dev_pair_win_rate": rolling_dev["pair_win_rate"],
            "rolling_dev_mean_pair_logodds_gap": rolling_dev["mean_pair_logodds_gap"],
            "rolling_dev_pair_margin_satisfied_rate": rolling_dev["pair_margin_satisfied_rate"],
            "delta_rolling_dev_auc": None if previous_rolling_auc is None or rolling_dev["auc"] is None else rolling_dev["auc"]-float(previous_rolling_auc),
            "delta_rolling_dev_pair_win_rate": None if previous_rolling_pair_win is None or rolling_dev["pair_win_rate"] is None else rolling_dev["pair_win_rate"]-float(previous_rolling_pair_win),
            "delta_rolling_dev_mean_pair_logodds_gap": None if previous_rolling_pair_gap is None or rolling_dev["mean_pair_logodds_gap"] is None else rolling_dev["mean_pair_logodds_gap"]-float(previous_rolling_pair_gap),
            "delta_rolling_dev_pair_margin_satisfied_rate": None if previous_rolling_margin is None or rolling_dev["pair_margin_satisfied_rate"] is None else rolling_dev["pair_margin_satisfied_rate"]-float(previous_rolling_margin),
            "delta_rolling_dev_probability_separation": None if previous_rolling_separation is None or rolling_dev["probability_separation"] is None else rolling_dev["probability_separation"]-float(previous_rolling_separation),
            "delta_rolling_dev_nll": None if previous_rolling_nll is None else rolling_dev["mean_nll"]-float(previous_rolling_nll),
            "delta_dev_pair_win_rate": None if previous_pair_win_rate is None or dev["pair_win_rate"] is None else dev["pair_win_rate"]-float(previous_pair_win_rate),
            "delta_dev_mean_pair_logodds_gap": None if previous_pair_gap is None or dev["mean_pair_logodds_gap"] is None else dev["mean_pair_logodds_gap"]-float(previous_pair_gap),
            "delta_dev_pair_margin_satisfied_rate": None if previous_pair_margin_rate is None or dev["pair_margin_satisfied_rate"] is None else dev["pair_margin_satisfied_rate"]-float(previous_pair_margin_rate),
            "delta_dev_nll": None if previous_dev is None else dev["mean_nll"]-float(previous_dev),
            "delta_dev_auc": None if previous_auc is None or dev["auc"] is None else dev["auc"]-float(previous_auc),
            "delta_dev_probability_separation": None if previous_separation is None or dev["probability_separation"] is None else dev["probability_separation"]-float(previous_separation),
            "body_grad_norm_last_step": 0.0,
            "head_grad_norm_last_step": last_head_grad_norm,
            "backbone_probe_changed": backbone_changed,
            "head_probe_changed": head_changed,
            "training_seconds": time.perf_counter()-training_started,
            "shard": str(shard_path),
            "shard_sha256": sha256_file(shard_path),
        }
        generation = save_generation(exp=exp, model=model, optimizer=optimizer,
                                     experiment=experiment, training_config=training_config, ranking_config=ranking_config,
                                     cycle=cycle, global_step=state["global_step"], meta=result)
        state["latest_generation"] = str(generation)
        if improved_best:
            apply_best_selection_to_state(state, candidate_selection)
            ensure_best_package(exp, model, generation, candidate_selection)
        result.update(
            improved_best=improved_best, best_selection_policy=SELECTION_POLICY,
            best_dev_auc=state.get("best_dev_auc"),
            best_dev_probability_separation=state.get("best_dev_probability_separation"),
            best_dev_nll=state.get("best_dev_nll"), best_cycle=state.get("best_cycle"),
            checkpoint=str(generation), cycle_total_seconds=time.perf_counter()-cycle_started,
        )
        atomic_json(state_path, state)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
        emit("cycle_result", **result)
        emit("checkpoint_committed", cycle=cycle, checkpoint=str(generation), state=str(state_path))
        garbage_collect(exp, args.keep_generations, protected_cycles={state.get("best_cycle")})


if __name__ == "__main__":
    main()
