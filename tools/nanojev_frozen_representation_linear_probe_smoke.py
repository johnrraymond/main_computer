#!/usr/bin/env python3
"""Linear-probe the exact frozen-Qwen leaf representation used by NanoJev.

Read-only diagnostic with respect to NanoJev/Qwen.  It does NOT load a NanoJev
checkpoint and does NOT update Qwen, S1, the register bank, R1, or any NanoJev
head parameter.  The only learned objects are tiny throw-away CPU linear probes.

The smoke answers one narrow question:

    Is the ordered-continuation TRUE/FALSE relation already linearly readable
    from the final frozen-Qwen leaf vector that NanoJev's decision head receives?

It uses actual ordered-continuation TRAIN families from written legacy shards and
the experiment's protected ordered-continuation DEV probe.  Families are kept
intact; train/validation splitting is by family, and DEV is never used to select
probe hyperparameters.

Two feature surfaces are tested:

  RAW_LEAF
      Qwen's final-layer hidden vector at the terminal token of NanoJev's exact
      boolean path.

  LAYERNORM_LEAF
      Parameter-free per-example LayerNorm of RAW_LEAF.  This mirrors the
      normalization geometry immediately before NanoJev's scalar readout; any
      learned LayerNorm affine can be absorbed by a following linear probe.

For each surface the smoke fits:

  PAIR probe
      A zero-bias linear ranking direction trained on TRUE-FALSE feature
      differences.  HELDOUT_PAIR_WIN_RATE is the cleanest result for OPAIR.

  ROW probe
      A conventional linear TRUE/FALSE classifier with bias.  It reports both
      held-out balanced accuracy and held-out matched-pair win rate, exposing a
      calibration-vs-ranking split.

If frozen Qwen's native LM likelihood is strong (as established by the companion
ordered-signal smoke) and this probe is also strong on held-out DEV, the signal
is present at NanoJev's readout location and the multi-task/head training path is
the prime suspect.  If the probe remains near chance, the generative signal is
not linearly exposed at this terminal hidden-state readout.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
from typing import Iterable, Sequence


DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_dictionary_v1"
DEFAULT_TRAIN_PAIRS = 192
DEFAULT_MAX_TRAIN_SHARDS = 32
DEFAULT_SEED = 20260928
DEFAULT_BATCH_SIZE = 8
ORDERED_TASK = "ordered_lexeme_continuation_v1"
DEFAULT_L2_GRID = (1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0)


@dataclass(frozen=True)
class OrderedPair:
    family_id: str
    false_row: dict
    true_row: dict
    boundary_key: tuple
    source_cycle: int | None


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise RuntimeError(f"non-object JSON row at {path}:{line_no}")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _truth(row: dict) -> bool | None:
    gold = row.get("gold")
    if not isinstance(gold, dict) or "suffix_matches" not in gold:
        return None
    return bool(gold["suffix_matches"])


def _boundary_key(row: dict) -> tuple:
    metadata = row.get("metadata") or {}
    source = str(metadata.get("source_path") or metadata.get("source_group_id") or "")
    boundary = metadata.get("source_boundary")
    actual = tuple(str(x) for x in (metadata.get("actual_lexemes") or []))
    length = int(metadata.get("continuation_length") or len(actual) or 0)
    if source and isinstance(boundary, int):
        return (source, boundary, actual, length)
    # Fail-safe identity for older rows without reconstruction metadata.  Such
    # rows can still be probed, but cannot collide silently with held-out rows.
    family = str(row.get("family_id") or row.get("id") or "")
    return ("family", family)


def group_ordered_pairs(rows: Iterable[dict], *, source_cycle: int | None = None) -> list[OrderedPair]:
    grouped: dict[str, dict[bool, dict]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        if metadata.get("legacy_task") != ORDERED_TASK:
            continue
        truth = _truth(row)
        if truth is None:
            continue
        family = str(row.get("family_id") or "")
        if not family:
            raise RuntimeError(f"ordered row lacks family_id: {row.get('id')!r}")
        sides = grouped.setdefault(family, {})
        if truth in sides:
            raise RuntimeError(f"duplicate truth={truth} side for ordered family {family}")
        sides[truth] = row

    out: list[OrderedPair] = []
    for family, sides in grouped.items():
        if set(sides) != {False, True}:
            continue
        false_row = sides[False]
        true_row = sides[True]
        false_key = _boundary_key(false_row)
        true_key = _boundary_key(true_row)
        if false_key != true_key:
            raise RuntimeError(f"ordered family {family} TRUE/FALSE boundary metadata disagree")
        out.append(OrderedPair(
            family_id=family,
            false_row=false_row,
            true_row=true_row,
            boundary_key=true_key,
            source_cycle=source_cycle,
        ))
    return out


def cycle_from_path(path: Path) -> int | None:
    stem = path.stem
    if stem.startswith("cycle-"):
        try:
            return int(stem.split("-", 1)[1])
        except ValueError:
            return None
    return None


def collect_training_pairs(
    experiment: Path,
    *,
    heldout_keys: set[tuple],
    requested_pairs: int,
    max_shards: int,
    seed: int,
) -> tuple[list[OrderedPair], list[int], int]:
    shard_dir = experiment / "shards" / "legacy"
    if not shard_dir.is_dir():
        raise RuntimeError(f"legacy shard directory not found: {shard_dir}")
    shard_paths = sorted(shard_dir.glob("cycle-*.jsonl"), reverse=True)[:max_shards]
    if not shard_paths:
        raise RuntimeError(f"no legacy shards found: {shard_dir}")

    by_boundary: dict[tuple, OrderedPair] = {}
    cycles_with_ordered: list[int] = []
    collisions = 0
    for path in shard_paths:
        cycle = cycle_from_path(path)
        pairs = group_ordered_pairs(read_jsonl(path), source_cycle=cycle)
        if pairs and cycle is not None:
            cycles_with_ordered.append(cycle)
        for pair in pairs:
            if pair.boundary_key in heldout_keys:
                collisions += 1
                continue
            # Newest shard wins if the same source boundary was revisited.
            by_boundary.setdefault(pair.boundary_key, pair)

    values = list(by_boundary.values())
    rng = random.Random(seed)
    rng.shuffle(values)
    if requested_pairs > 0:
        values = values[:requested_pairs]
    cycles = sorted(set(c for c in cycles_with_ordered if c is not None))
    return values, cycles, collisions


def flatten_pair_rows(pairs: Sequence[OrderedPair]) -> list[dict]:
    rows: list[dict] = []
    for pair in pairs:
        # Fixed FALSE, TRUE order makes downstream pair construction explicit.
        rows.extend((pair.false_row, pair.true_row))
    return rows


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_exact_examples(pipeline, tokenizer, max_length: int, rows: Sequence[dict], work_dir: Path, name: str):
    path = work_dir / f"{name}.jsonl"
    write_jsonl(path, rows)
    examples, _stats = pipeline.load_training_examples(path, tokenizer, max_length)
    if len(examples) != len(rows):
        raise RuntimeError(
            f"NanoJev pipeline changed row count for {name}: rows={len(rows)} examples={len(examples)}"
        )
    for index, (row, ex) in enumerate(zip(rows, examples)):
        candidate_ids = list(ex.get("candidate_ids") or [])
        if candidate_ids != ["false", "true"]:
            raise RuntimeError(f"{name}[{index}] candidate order is {candidate_ids!r}, expected ['false','true']")
        expected = 1 if _truth(row) else 0
        if int(ex.get("gold_index")) != expected:
            raise RuntimeError(
                f"{name}[{index}] pipeline/order mismatch: gold_index={ex.get('gold_index')} expected={expected}"
            )
        leaves = ex.get("leaf_tokens") or []
        if len(leaves) != 1 or not leaves[0]:
            raise RuntimeError(
                f"{name}[{index}] ordered boolean example expected exactly one non-empty leaf path, got {len(leaves)}"
            )
    return examples


def extract_frozen_leaf_features(model, examples, *, pad_token_id: int, batch_size: int, device: str):
    import torch

    sequences = [list(ex["leaf_tokens"][0]) for ex in examples]
    lengths_all = [len(ids) for ids in sequences]
    if not sequences:
        raise RuntimeError("no examples to extract")
    max_positions = int(getattr(model.config, "max_position_embeddings", max(lengths_all)))
    if max(lengths_all) > max_positions:
        raise RuntimeError(f"NanoJev path exceeds Qwen max positions: {max(lengths_all)} > {max_positions}")

    chunks = []
    for start in range(0, len(sequences), batch_size):
        batch = sequences[start:start + batch_size]
        lengths = torch.tensor([len(ids) for ids in batch], dtype=torch.long, device=device)
        width = int(lengths.max().item())
        tokens = torch.full((len(batch), width), pad_token_id, dtype=torch.long, device=device)
        for i, ids in enumerate(batch):
            tokens[i, :len(ids)] = torch.tensor(ids, dtype=torch.long, device=device)
        attention = torch.arange(width, device=device)[None, :] < lengths[:, None]
        with torch.inference_mode():
            out = model(input_ids=tokens, attention_mask=attention, use_cache=False)
            leaves = out.last_hidden_state[
                torch.arange(len(batch), device=device), lengths - 1
            ]
        chunks.append(leaves.detach().float().cpu())
    return torch.cat(chunks, dim=0), lengths_all


def parameter_free_layernorm(x):
    import torch
    mean = x.mean(dim=-1, keepdim=True)
    var = (x - mean).square().mean(dim=-1, keepdim=True)
    return (x - mean) * torch.rsqrt(var + 1e-5)


def pair_differences(features, pair_count: int):
    if features.shape[0] != pair_count * 2:
        raise RuntimeError(f"feature row count {features.shape[0]} does not equal 2*pair_count={pair_count * 2}")
    false = features[0::2]
    true = features[1::2]
    return true - false


def balanced_accuracy(scores, labels) -> tuple[float, float, float, float]:
    import torch
    pred = scores > 0
    labels_b = labels > 0
    pos = labels_b
    neg = ~labels_b
    pos_acc = float((pred[pos] == labels_b[pos]).float().mean().item()) if bool(pos.any()) else float("nan")
    neg_acc = float((pred[neg] == labels_b[neg]).float().mean().item()) if bool(neg.any()) else float("nan")
    acc = float((pred == labels_b).float().mean().item())
    bal = (pos_acc + neg_acc) / 2.0
    return acc, bal, pos_acc, neg_acc


def _fit_logistic(X, y, *, l2: float, bias: bool, steps: int = 300):
    """Deterministic CPU logistic regression with zero initialization."""
    import torch
    import torch.nn.functional as F

    X = X.detach().float().cpu()
    y = y.detach().float().cpu()
    w = torch.zeros(X.shape[1], dtype=torch.float32, requires_grad=True)
    b = torch.zeros((), dtype=torch.float32, requires_grad=True) if bias else None
    params = [w] + ([b] if b is not None else [])
    optimizer = torch.optim.LBFGS(
        params,
        lr=1.0,
        max_iter=steps,
        tolerance_grad=1e-8,
        tolerance_change=1e-10,
        line_search_fn="strong_wolfe",
    )

    def closure():
        optimizer.zero_grad(set_to_none=True)
        logits = X @ w + (b if b is not None else 0.0)
        loss = F.binary_cross_entropy_with_logits(logits, y)
        loss = loss + 0.5 * float(l2) * w.square().mean()
        loss.backward()
        return loss

    optimizer.step(closure)
    return w.detach(), (float(b.detach().item()) if b is not None else 0.0)


def _pair_probe_fit(train_diff, *, l2: float):
    import torch
    X = torch.cat([train_diff, -train_diff], dim=0)
    y = torch.cat([
        torch.ones(train_diff.shape[0]),
        torch.zeros(train_diff.shape[0]),
    ], dim=0)
    return _fit_logistic(X, y, l2=l2, bias=False)


def _row_probe_fit(train_x, train_labels, *, l2: float):
    return _fit_logistic(train_x, train_labels, l2=l2, bias=True)


def split_pair_indices(pair_count: int, *, seed: int, validation_fraction: float = 0.20):
    if pair_count < 10:
        raise RuntimeError("need at least 10 training pairs for a train/validation linear-probe split")
    indices = list(range(pair_count))
    rng = random.Random(seed)
    rng.shuffle(indices)
    val_n = max(4, int(round(pair_count * validation_fraction)))
    val_n = min(val_n, pair_count - 4)
    return indices[val_n:], indices[:val_n]


def rows_for_pair_indices(features, pair_indices: Sequence[int]):
    import torch
    rows: list[int] = []
    for i in pair_indices:
        rows.extend((2 * i, 2 * i + 1))
    return features.index_select(0, torch.tensor(rows, dtype=torch.long))


def labels_for_pair_count(pair_count: int):
    import torch
    y = torch.empty(pair_count * 2, dtype=torch.float32)
    y[0::2] = 0.0
    y[1::2] = 1.0
    return y


def select_pair_l2(diff, fit_idx: Sequence[int], val_idx: Sequence[int], grid: Sequence[float]):
    import torch
    fit = diff.index_select(0, torch.tensor(list(fit_idx), dtype=torch.long))
    val = diff.index_select(0, torch.tensor(list(val_idx), dtype=torch.long))
    trials = []
    for l2 in grid:
        w, _ = _pair_probe_fit(fit, l2=l2)
        margins = val @ w
        acc = float((margins > 0).float().mean().item())
        mean_margin = float(margins.mean().item())
        trials.append((acc, mean_margin, -float(l2), float(l2)))
    return max(trials)[3], trials


def select_row_l2(features, fit_idx: Sequence[int], val_idx: Sequence[int], grid: Sequence[float]):
    import torch
    fit_x = rows_for_pair_indices(features, fit_idx)
    val_x = rows_for_pair_indices(features, val_idx)
    fit_y = labels_for_pair_count(len(fit_idx))
    val_y = labels_for_pair_count(len(val_idx))
    trials = []
    for l2 in grid:
        w, b = _row_probe_fit(fit_x, fit_y, l2=l2)
        scores = val_x @ w + b
        _acc, bal, _pos, _neg = balanced_accuracy(scores, val_y)
        pair_scores = scores[1::2] - scores[0::2]
        pair_win = float((pair_scores > 0).float().mean().item())
        trials.append((bal, pair_win, -float(l2), float(l2)))
    return max(trials)[3], trials


def evaluate_surface(name: str, train_features, dev_features, *, seed: int, l2_grid: Sequence[float]):
    import torch

    train_pair_count = train_features.shape[0] // 2
    dev_pair_count = dev_features.shape[0] // 2
    fit_idx, val_idx = split_pair_indices(train_pair_count, seed=seed)
    train_diff = pair_differences(train_features, train_pair_count)
    dev_diff = pair_differences(dev_features, dev_pair_count)

    pair_l2, pair_trials = select_pair_l2(train_diff, fit_idx, val_idx, l2_grid)
    pair_w, _ = _pair_probe_fit(train_diff, l2=pair_l2)
    train_pair_margin = train_diff @ pair_w
    dev_pair_margin = dev_diff @ pair_w

    row_l2, row_trials = select_row_l2(train_features, fit_idx, val_idx, l2_grid)
    train_y = labels_for_pair_count(train_pair_count)
    dev_y = labels_for_pair_count(dev_pair_count)
    row_w, row_b = _row_probe_fit(train_features, train_y, l2=row_l2)
    train_scores = train_features @ row_w + row_b
    dev_scores = dev_features @ row_w + row_b
    train_acc, train_bal, train_pos, train_neg = balanced_accuracy(train_scores, train_y)
    dev_acc, dev_bal, dev_pos, dev_neg = balanced_accuracy(dev_scores, dev_y)
    train_row_pair_margin = train_scores[1::2] - train_scores[0::2]
    dev_row_pair_margin = dev_scores[1::2] - dev_scores[0::2]

    result = {
        "surface": name,
        "fit_pairs": len(fit_idx),
        "validation_pairs": len(val_idx),
        "pair_l2": pair_l2,
        "pair_validation_grid": [
            {"l2": t[3], "pair_win": t[0], "mean_margin": t[1]} for t in pair_trials
        ],
        "pair_train_win": float((train_pair_margin > 0).float().mean().item()),
        "pair_train_mean_margin": float(train_pair_margin.mean().item()),
        "pair_heldout_win": float((dev_pair_margin > 0).float().mean().item()),
        "pair_heldout_mean_margin": float(dev_pair_margin.mean().item()),
        "pair_heldout_median_margin": float(dev_pair_margin.median().item()),
        "row_l2": row_l2,
        "row_validation_grid": [
            {"l2": t[3], "balanced_accuracy": t[0], "pair_win": t[1]} for t in row_trials
        ],
        "row_train_accuracy": train_acc,
        "row_train_balanced_accuracy": train_bal,
        "row_train_positive_accuracy": train_pos,
        "row_train_negative_accuracy": train_neg,
        "row_train_pair_win": float((train_row_pair_margin > 0).float().mean().item()),
        "row_heldout_accuracy": dev_acc,
        "row_heldout_balanced_accuracy": dev_bal,
        "row_heldout_positive_accuracy": dev_pos,
        "row_heldout_negative_accuracy": dev_neg,
        "row_heldout_pair_win": float((dev_row_pair_margin > 0).float().mean().item()),
        "row_heldout_mean_pair_margin": float(dev_row_pair_margin.mean().item()),
    }
    return result


def fmt(value: float, places: int = 4) -> str:
    return f"{value:.{places}f}" if math.isfinite(value) else "-"


def print_surface(result: dict) -> None:
    print(f"=== {result['surface']} ===")
    print(
        "PAIR_PROBE: "
        f"l2={result['pair_l2']:g} "
        f"train_pair_win={result['pair_train_win']:.4f} "
        f"HELDOUT_PAIR_WIN_RATE={result['pair_heldout_win']:.4f} "
        f"heldout_mean_margin={result['pair_heldout_mean_margin']:+.6f} "
        f"heldout_median_margin={result['pair_heldout_median_margin']:+.6f}"
    )
    print(
        "ROW_PROBE : "
        f"l2={result['row_l2']:g} "
        f"train_bal={result['row_train_balanced_accuracy']:.4f} "
        f"HELDOUT_BALANCED_ACCURACY={result['row_heldout_balanced_accuracy']:.4f} "
        f"D+={result['row_heldout_positive_accuracy']:.4f} "
        f"D-={result['row_heldout_negative_accuracy']:.4f} "
        f"HELDOUT_PAIR_WIN_RATE={result['row_heldout_pair_win']:.4f}"
    )
    print("PAIR_L2_VALIDATION: " + " ".join(
        f"{x['l2']:g}:{x['pair_win']:.3f}" for x in result["pair_validation_grid"]
    ))
    print("ROW_L2_VALIDATION : " + " ".join(
        f"{x['l2']:g}:{x['balanced_accuracy']:.3f}/{x['pair_win']:.3f}"
        for x in result["row_validation_grid"]
    ))
    print()


def run_self_test() -> None:
    import torch

    def row(family: str, truth: bool, boundary: int):
        proposed = ["a", "+", "b"] if truth else ["+", "b", "a"]
        return {
            "id": f"{family}-{'t' if truth else 'f'}",
            "family_id": family,
            "gold": {"suffix_matches": truth},
            "metadata": {
                "legacy_task": ORDERED_TASK,
                "source_path": "x.py",
                "source_boundary": boundary,
                "actual_lexemes": ["a", "+", "b"],
                "proposed_lexemes": proposed,
                "continuation_length": 3,
            },
        }

    rows = [row("p1", False, 10), row("p1", True, 10), row("p2", True, 20), row("p2", False, 20)]
    pairs = group_ordered_pairs(rows, source_cycle=7)
    assert len(pairs) == 2
    assert flatten_pair_rows(pairs)[0]["gold"]["suffix_matches"] is False
    assert flatten_pair_rows(pairs)[1]["gold"]["suffix_matches"] is True

    # Synthetic linearly readable relation with nuisance dimensions.
    gen = torch.Generator().manual_seed(123)
    train_pairs = 48
    dev_pairs = 24
    d = 16
    direction = torch.randn(d, generator=gen)
    direction = direction / direction.norm()

    def make(pair_n: int):
        base = torch.randn(pair_n, d, generator=gen)
        false = base - 2.5 * direction
        true = base + 2.5 * direction
        out = torch.empty(pair_n * 2, d)
        out[0::2] = false
        out[1::2] = true
        return out

    train = make(train_pairs)
    dev = make(dev_pairs)
    result = evaluate_surface("SELFTEST", train, dev, seed=7, l2_grid=(0.01, 0.1, 1.0))
    assert result["pair_heldout_win"] > 0.95
    assert result["row_heldout_balanced_accuracy"] > 0.90
    print(json.dumps({
        "event": "nanojev_frozen_representation_linear_probe_self_test_ok",
        "pairs": len(pairs),
        "pair_heldout_win": result["pair_heldout_win"],
        "row_heldout_balanced_accuracy": result["row_heldout_balanced_accuracy"],
    }, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit throw-away linear probes to exact frozen-Qwen NanoJev ordered-task leaf representations."
    )
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--train-pairs", type=int, default=DEFAULT_TRAIN_PAIRS,
                        help="Maximum unique ordered training families to use (default: 192).")
    parser.add_argument("--max-train-shards", type=int, default=DEFAULT_MAX_TRAIN_SHARDS,
                        help="How many newest legacy shard files to scan for ordered training families.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--precision", choices=("bf16", "fp16", "fp32"), default="bf16")
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return
    if args.train_pairs <= 0:
        parser.error("--train-pairs must be positive")
    if args.max_train_shards <= 0:
        parser.error("--max-train-shards must be positive")
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")

    experiment_dir = Path(args.experiment).expanduser().resolve(strict=True)
    experiment = read_json(experiment_dir / "experiment.json")
    legacy_exp = Path(experiment["legacy_experiment"]).expanduser().resolve(strict=True)
    legacy_experiment = read_json(legacy_exp / "experiment.json")
    nanojev_root = Path(legacy_experiment["nanojev_root"]).expanduser().resolve(strict=True)
    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    if not tokenizer_dir.is_dir():
        raise RuntimeError(f"legacy tokenizer artifact not found: {tokenizer_dir}")

    dev_path = experiment_dir / "probes" / "legacy_ordered_continuation_dev.jsonl"
    if not dev_path.is_file():
        raise RuntimeError(f"ordered held-out dev probe not found: {dev_path}")
    dev_pairs = group_ordered_pairs(read_jsonl(dev_path), source_cycle=None)
    if len(dev_pairs) < 16:
        raise RuntimeError(f"ordered held-out dev probe is unexpectedly small: {len(dev_pairs)} pairs")
    heldout_keys = {pair.boundary_key for pair in dev_pairs}
    if len(heldout_keys) != len(dev_pairs):
        raise RuntimeError("held-out ordered probe contains duplicate source boundaries")

    train_pairs, train_cycles, collisions = collect_training_pairs(
        experiment_dir,
        heldout_keys=heldout_keys,
        requested_pairs=args.train_pairs,
        max_shards=args.max_train_shards,
        seed=args.seed,
    )
    if len(train_pairs) < 32:
        raise RuntimeError(
            f"only {len(train_pairs)} unique non-heldout ordered training pairs found; need at least 32"
        )

    tools_dir = Path(__file__).resolve().parent
    legacy = load_local_module("nanojev_code_train_for_linear_probe", tools_dir / "nanojev_code_train.py")
    pipeline, _BaseDecisionModel = legacy.import_nanojev(nanojev_root)

    import torch
    from transformers import AutoModel, AutoTokenizer

    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")
    if args.precision == "bf16":
        if args.device.startswith("cuda") and not torch.cuda.is_bf16_supported():
            raise RuntimeError("bf16 requested but CUDA device does not support bf16")
        dtype = torch.bfloat16
    elif args.precision == "fp16":
        dtype = torch.float16
    else:
        dtype = torch.float32

    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_dir), local_files_only=True, trust_remote_code=False
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    max_length = int(legacy_experiment["max_length"])

    train_rows = flatten_pair_rows(train_pairs)
    dev_rows = flatten_pair_rows(dev_pairs)
    with tempfile.TemporaryDirectory(prefix="nanojev-linear-probe-") as tmp:
        work_dir = Path(tmp)
        train_examples = load_exact_examples(
            pipeline, tokenizer, max_length, train_rows, work_dir, "ordered_train"
        )
        dev_examples = load_exact_examples(
            pipeline, tokenizer, max_length, dev_rows, work_dir, "ordered_dev"
        )

    model_name = str(experiment.get("model") or legacy_experiment["model"])
    revision = str(experiment.get("resolved_model_revision") or legacy_experiment.get("resolved_model_revision") or "")
    if not revision:
        raise RuntimeError("no resolved model revision recorded in experiment metadata")

    print("NanoJev frozen-representation linear-probe smoke")
    print(f"experiment: {experiment_dir}")
    print(f"ordered_dev: {dev_path}")
    print(f"model: {model_name}")
    print(f"revision: {revision}")
    print(f"tokenizer: {tokenizer_dir}")
    print(f"train_pairs: {len(train_pairs)} ({len(train_rows)} TRUE/FALSE rows)")
    print(f"heldout_dev_pairs: {len(dev_pairs)} ({len(dev_rows)} TRUE/FALSE rows)")
    print(f"train_cycles_scanned_with_ordered: {train_cycles}")
    print(f"train/dev_boundary_collisions_excluded: {collisions}")
    print(f"max_length: {max_length}")
    print("NanoJev checkpoint loaded: NO")
    print("Qwen gradients: NO")
    print("S1/register/R1 training: NO")
    print("learned objects: throw-away CPU linear probes only")
    print()

    print("loading frozen Qwen base model (same AutoModel surface used by NanoJev trainer)...")
    model = AutoModel.from_pretrained(
        model_name,
        revision=revision,
        dtype=dtype,
        attn_implementation="sdpa",
        trust_remote_code=False,
        local_files_only=not args.allow_download,
    ).to(args.device)
    model.eval()
    model.requires_grad_(False)
    if hasattr(model.config, "use_cache"):
        model.config.use_cache = False
    print("frozen Qwen loaded")

    train_raw, train_lengths = extract_frozen_leaf_features(
        model, train_examples, pad_token_id=tokenizer.pad_token_id,
        batch_size=args.batch_size, device=args.device,
    )
    dev_raw, dev_lengths = extract_frozen_leaf_features(
        model, dev_examples, pad_token_id=tokenizer.pad_token_id,
        batch_size=args.batch_size, device=args.device,
    )
    del model
    if args.device.startswith("cuda"):
        torch.cuda.empty_cache()

    hidden_size = int(train_raw.shape[1])
    print(
        "feature_extraction: "
        f"hidden_size={hidden_size} "
        f"train_path_tokens=min{min(train_lengths)}/mean{sum(train_lengths)/len(train_lengths):.1f}/max{max(train_lengths)} "
        f"dev_path_tokens=min{min(dev_lengths)}/mean{sum(dev_lengths)/len(dev_lengths):.1f}/max{max(dev_lengths)}"
    )
    print()

    # No train-set centering is needed for pair differences, but row probes can
    # benefit numerically from a single affine scaling.  Use scalar RMS only so
    # we do not accidentally introduce a high-capacity per-dimension transform.
    train_rms = torch.sqrt(train_raw.square().mean() + 1e-12)
    raw_train = train_raw / train_rms
    raw_dev = dev_raw / train_rms
    ln_train = parameter_free_layernorm(train_raw)
    ln_dev = parameter_free_layernorm(dev_raw)

    raw_result = evaluate_surface(
        "RAW_LEAF", raw_train, raw_dev, seed=args.seed, l2_grid=DEFAULT_L2_GRID
    )
    ln_result = evaluate_surface(
        "LAYERNORM_LEAF", ln_train, ln_dev, seed=args.seed, l2_grid=DEFAULT_L2_GRID
    )
    print_surface(raw_result)
    print_surface(ln_result)

    best_pair = max(raw_result["pair_heldout_win"], ln_result["pair_heldout_win"])
    best_row = max(raw_result["row_heldout_balanced_accuracy"], ln_result["row_heldout_balanced_accuracy"])
    print("=== SUMMARY ===")
    print(f"BEST_HELDOUT_PAIR_WIN_RATE: {best_pair:.4f}")
    print(f"BEST_HELDOUT_ROW_BALANCED_ACCURACY: {best_row:.4f}")
    print()
    print("INTERPRETATION:")
    print("  pair probe strong (roughly >=0.80) -> the TRUE>garble relation is linearly readable at NanoJev's")
    print("                                    frozen-Qwen terminal leaf; head/multitask training is the suspect.")
    print("  row weak but pair strong           -> ordering is readable but absolute TRUE/FALSE calibration is the suspect.")
    print("  both near chance                   -> native LM knowledge is not linearly exposed at this terminal leaf;")
    print("                                    expose a sequence-likelihood/predictive signal or change the readout surface.")
    print("  RAW weak but LAYERNORM strong      -> normalization is essential; the existing normalized readout should in")
    print("                                    principle have access, shifting suspicion back toward training dynamics.")


if __name__ == "__main__":
    main()
