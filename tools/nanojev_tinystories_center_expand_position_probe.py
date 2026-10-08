#!/usr/bin/env python3
"""Causal position probe for TinyStories-only CLEF center expansion.

The probe freezes one exact checkpoint, then holds semantic content fixed while
moving one decisive fact through 5/25/50/75/95 percent of a fixed-length source.
Each positional variant is evaluated under four vector-allocation modes:

    none, uniform, triangular, edge

The probe reports a mode x position matrix for both the trained CLEF head and
TinyStories' raw candidate continuation likelihood.  This separates positional
behavior in the frozen backbone from behavior learned by the CLEF head.

No probe text is expanded or retokenized. Source text is tokenized once; vector
rows are copied after the embedding lookup.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

TOOLS = Path(__file__).resolve().parent


def load_local_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mature = load_local_module(
    "nanojev_position_probe_structured",
    TOOLS / "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
)
smoke = mature.smoke

SCHEMA = "main-computer-tinystories-center-expand-position-probe-v2"
LOCK_SCHEMA = "main-computer-tinystories-position-probe-checkpoint-lock-v1"
DEFAULT_RUN_DIR = Path(
    r"C:\Users\subsi\NanoJev\runs\tinystories_center_expand_960_to_1920_v1"
)
DEFAULT_SOURCE_TOKENS = 960
DEFAULT_EXPANDED_TOKENS = 1920
DEFAULT_ITEMS = 256
DEFAULT_SEED = 20261007
DEFAULT_POSITIONS = (0.05, 0.25, 0.50, 0.75, 0.95)
DEFAULT_MODES = ("none", "uniform", "triangular", "edge")
TINYSTORIES_MODEL = "roneneldan/TinyStories-33M"
TINYSTORIES_HIDDEN = int(mature.TINYSTORIES_BASE_HIDDEN_SIZE)
POSITION_EPSILON = 0.015

WORD_POOL = (
    "blue", "green", "yellow", "orange", "purple", "silver", "golden", "black",
    "white", "brown", "apple", "lemon", "peach", "grape", "melon", "berry",
    "cat", "dog", "horse", "sheep", "mouse", "bird", "fish", "frog",
    "garden", "river", "forest", "ocean", "cloud", "mountain", "island", "bridge",
    "north", "south", "east", "west", "morning", "evening", "summer", "winter",
    "circle", "square", "stone", "paper", "music", "story", "light", "shadow",
)

FILLER_TEXT = (
    "The room was quiet and the table was clean. "
    "A small lamp stood beside the window. "
    "Someone walked slowly through the hall and then sat down. "
    "Nothing in this sentence changes the requested key word. "
    "The weather stayed mild and the ordinary notes continued. "
)
HEADER_TEXT = "Read the notes carefully and remember the stated key word. "
QUERY_TEXT = " Question: What is the stated key word? Answer:"


@dataclass(frozen=True)
class ProbeItem:
    item_id: str
    candidate_words: tuple[str, str]
    gold_index: int


@dataclass(frozen=True)
class PromptVariant:
    prompt_ids: tuple[int, ...]
    fact_start: int
    fact_end: int
    requested_position: float
    actual_position: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _apportion(weights: Sequence[int], *, source_length: int, target_length: int) -> list[int]:
    n = int(source_length)
    target = int(target_length)
    if n <= 0:
        raise ValueError("source_length must be positive")
    if target < n:
        raise ValueError(f"allocation cannot shrink: source={n} target={target}")
    if len(weights) != n:
        raise ValueError("weight length mismatch")
    if target == n:
        return [1] * n
    if any(int(w) < 0 for w in weights):
        raise ValueError("allocation weights must be nonnegative")
    weight_sum = sum(int(w) for w in weights)
    extra = target - n
    if weight_sum <= 0:
        base_extra, remainder = divmod(extra, n)
        counts = [1 + base_extra] * n
        for index in range(remainder):
            counts[index] += 1
        return counts
    floors = [(extra * int(w)) // weight_sum for w in weights]
    remainders = [(extra * int(w)) % weight_sum for w in weights]
    counts = [1 + value for value in floors]
    remaining = target - sum(counts)
    center = (n - 1) / 2.0
    order = sorted(
        range(n),
        key=lambda i: (-remainders[i], -int(weights[i]), abs(i - center), i),
    )
    for index in order[:remaining]:
        counts[index] += 1
    if sum(counts) != target or min(counts) < 1:
        raise RuntimeError("exact allocation invariant failed")
    return counts


def replication_counts(mode: str, source_length: int, target_length: int) -> list[int]:
    mode = str(mode).strip().lower()
    n = int(source_length)
    target = int(target_length)
    if mode == "none":
        if target != n:
            raise ValueError("none mode target must equal source length")
        return [1] * n
    if mode == "uniform":
        return _apportion([1] * n, source_length=n, target_length=target)
    if mode == "triangular":
        if n == 1:
            return [target]
        weights = [min(i, n - 1 - i) for i in range(n)]
        return _apportion(weights, source_length=n, target_length=target)
    if mode == "edge":
        if n == 1:
            return [target]
        center = n - 1
        # Integer V: highest at either edge, lowest at the middle.
        weights = [abs(2 * i - center) for i in range(n)]
        return _apportion(weights, source_length=n, target_length=target)
    raise ValueError(f"unsupported allocation mode: {mode}")


def expanded_span(counts: Sequence[int], start: int, end: int) -> tuple[int, int, float]:
    if not (0 <= start < end <= len(counts)):
        raise ValueError("invalid source span")
    expanded_start = sum(int(v) for v in counts[:start])
    expanded_end = expanded_start + sum(int(v) for v in counts[start:end])
    total = sum(int(v) for v in counts)
    center = ((expanded_start + expanded_end - 1) / 2.0) / max(1, total - 1)
    return expanded_start, expanded_end, center


def _repeat_tokens(pattern: Sequence[int], count: int) -> list[int]:
    if count < 0:
        raise ValueError("negative filler count")
    if not pattern and count:
        raise ValueError("empty filler token pattern")
    if count == 0:
        return []
    repeats = (count + len(pattern) - 1) // len(pattern)
    return (list(pattern) * repeats)[:count]


def build_prompt_variant(
    *,
    tokenizer,
    gold_word: str,
    position: float,
    source_tokens: int,
) -> PromptVariant:
    source_tokens = int(source_tokens)
    position = float(position)
    if not 0.0 < position < 1.0:
        raise ValueError("position must be strictly inside (0,1)")
    header_ids = list(tokenizer.encode(HEADER_TEXT, add_special_tokens=False))
    fact_ids = list(tokenizer.encode(f"The key word is {gold_word}. ", add_special_tokens=False))
    query_ids = list(tokenizer.encode(QUERY_TEXT, add_special_tokens=False))
    filler_pattern = list(tokenizer.encode(FILLER_TEXT, add_special_tokens=False))
    fixed = len(header_ids) + len(fact_ids) + len(query_ids)
    filler_total = source_tokens - fixed
    if filler_total < 0:
        raise RuntimeError(
            f"source budget too small for probe scaffold: source={source_tokens} fixed={fixed}"
        )
    filler = _repeat_tokens(filler_pattern, filler_total)
    desired_center = position * (source_tokens - 1)
    before = int(round(desired_center - len(header_ids) - (len(fact_ids) - 1) / 2.0))
    before = max(0, min(filler_total, before))
    prompt = header_ids + filler[:before] + fact_ids + filler[before:] + query_ids
    if len(prompt) != source_tokens:
        raise RuntimeError(f"probe prompt length drifted: {len(prompt)} != {source_tokens}")
    fact_start = len(header_ids) + before
    fact_end = fact_start + len(fact_ids)
    actual = ((fact_start + fact_end - 1) / 2.0) / max(1, source_tokens - 1)
    if abs(actual - position) > POSITION_EPSILON:
        raise RuntimeError(
            f"cannot place fact close enough to requested position: requested={position} actual={actual}"
        )
    return PromptVariant(
        prompt_ids=tuple(int(v) for v in prompt),
        fact_start=fact_start,
        fact_end=fact_end,
        requested_position=position,
        actual_position=actual,
    )


def build_items(*, tokenizer, count: int, seed: int) -> list[ProbeItem]:
    usable = []
    for word in WORD_POOL:
        ids = tokenizer.encode(" " + word, add_special_tokens=False)
        if len(ids) == 1:
            usable.append(word)
    if len(usable) < 16:
        raise RuntimeError(f"not enough single-token probe words: {len(usable)}")
    rng = random.Random(int(seed))
    items: list[ProbeItem] = []
    for index in range(int(count)):
        left, right = rng.sample(usable, 2)
        gold = index % 2
        items.append(
            ProbeItem(
                item_id=f"position-probe-{index:05d}",
                candidate_words=(left, right),
                gold_index=gold,
            )
        )
    return items


def _score_from_probabilities(probabilities: Sequence[float], gold_index: int) -> dict[str, Any]:
    probs = [float(v) for v in probabilities]
    gold = float(probs[int(gold_index)])
    other = max(v for i, v in enumerate(probs) if i != int(gold_index))
    pred = max(range(len(probs)), key=probs.__getitem__)
    # This is ordinary NLL for interpretability. CLEF's training loss is also
    # reported separately from smoke.loss_parts during live evaluation.
    return {
        "correct": bool(pred == int(gold_index)),
        "predicted_index": int(pred),
        "gold_probability": gold,
        "gold_margin": gold - other,
        "nll": -math.log(max(gold, 1e-12)),
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot summarize empty rows")
    n = len(rows)
    return {
        "questions": n,
        "accuracy": sum(int(bool(row["correct"])) for row in rows) / n,
        "mean_loss": sum(float(row["loss"]) for row in rows) / n,
        "mean_cross_entropy": sum(float(row["cross_entropy"]) for row in rows) / n,
        "mean_brier": sum(float(row["brier"]) for row in rows) / n,
        "mean_gold_probability": sum(float(row["gold_probability"]) for row in rows) / n,
        "mean_gold_margin": sum(float(row["gold_margin"]) for row in rows) / n,
    }


def summarize_raw(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot summarize empty rows")
    n = len(rows)
    return {
        "questions": n,
        "accuracy": sum(int(bool(row["correct"])) for row in rows) / n,
        "mean_nll": sum(float(row["nll"]) for row in rows) / n,
        "mean_gold_probability": sum(float(row["gold_probability"]) for row in rows) / n,
        "mean_gold_margin": sum(float(row["gold_margin"]) for row in rows) / n,
    }


def curve_diagnostics(matrix: Mapping[str, Mapping[str, Mapping[str, float]]], *, loss_key: str) -> dict[str, Any]:
    out = {}
    for mode, cells in matrix.items():
        position_keys = sorted(cells, key=lambda value: float(value))
        if len(position_keys) < 3:
            continue
        left = position_keys[0]
        right = position_keys[-1]
        middle = min(position_keys, key=lambda value: abs(float(value) - 0.5))
        edge_loss = (float(cells[left][loss_key]) + float(cells[right][loss_key])) / 2.0
        middle_loss = float(cells[middle][loss_key])
        edge_acc = (float(cells[left]["accuracy"]) + float(cells[right]["accuracy"])) / 2.0
        middle_acc = float(cells[middle]["accuracy"])
        edge_gold = (
            float(cells[left]["mean_gold_probability"])
            + float(cells[right]["mean_gold_probability"])
        ) / 2.0
        middle_gold = float(cells[middle]["mean_gold_probability"])
        out[mode] = {
            "left_position": float(left),
            "middle_position": float(middle),
            "right_position": float(right),
            "middle_minus_edge_loss": middle_loss - edge_loss,
            "middle_minus_edge_accuracy": middle_acc - edge_acc,
            "middle_minus_edge_gold_probability": middle_gold - edge_gold,
            "loss_range": max(float(cells[p][loss_key]) for p in position_keys)
            - min(float(cells[p][loss_key]) for p in position_keys),
        }
    return out


def paired_mode_delta(
    rows: Sequence[Mapping[str, Any]], *, candidate: str, baseline: str
) -> dict[str, Any]:
    by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (str(row["item_id"]), str(row["position_key"]), str(row["mode"]))
        by_key[key] = row
    deltas = {}
    position_keys = sorted({str(row["position_key"]) for row in rows})
    for position in position_keys:
        pairs = []
        for item_id in sorted({str(row["item_id"]) for row in rows}):
            c = by_key.get((item_id, position, candidate))
            b = by_key.get((item_id, position, baseline))
            if c is not None and b is not None:
                pairs.append((c, b))
        if not pairs:
            continue
        deltas[position] = {
            "shared_items": len(pairs),
            "accuracy_delta": sum(int(c["correct"]) - int(b["correct"]) for c, b in pairs) / len(pairs),
            "loss_delta": sum(float(c["loss"]) - float(b["loss"]) for c, b in pairs) / len(pairs),
            "gold_probability_delta": sum(
                float(c["gold_probability"]) - float(b["gold_probability"]) for c, b in pairs
            ) / len(pairs),
            "candidate_lower_loss": sum(float(c["loss"]) < float(b["loss"]) for c, b in pairs),
            "baseline_lower_loss": sum(float(c["loss"]) > float(b["loss"]) for c, b in pairs),
        }
    return deltas


def _checkpoint_score(meta: Mapping[str, Any]) -> tuple[float, float] | None:
    metrics = meta.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    predev = metrics.get("predev")
    if not isinstance(predev, Mapping):
        return None
    overall = predev.get("overall") if isinstance(predev.get("overall"), Mapping) else predev
    loss = overall.get("mean_loss")
    accuracy = overall.get("accuracy")
    if loss is None or accuracy is None:
        return None
    return float(loss), float(accuracy)


def resolve_best_finalized_checkpoint(run_dir: Path) -> tuple[Path, tuple[float, float] | None, str]:
    run_dir = Path(run_dir).expanduser().resolve(strict=True)
    candidates: list[tuple[tuple[float, float], Path]] = []
    checkpoint_root = run_dir / "checkpoints"
    for path in sorted(checkpoint_root.glob("cycle-*-reuse-*")):
        if not path.is_dir():
            continue
        required = [path / "meta.json", path / "head.safetensors", path / "tinystories.safetensors"]
        if not all(item.is_file() for item in required):
            continue
        meta = smoke.read_json(path / "meta.json")
        if meta.get("cycle_complete") is False:
            continue
        score = _checkpoint_score(meta)
        if score is not None:
            candidates.append((score, path.resolve()))
    if candidates:
        score, path = min(candidates, key=lambda item: (item[0][0], -item[0][1], str(item[1])))
        return path, score, "best-finalized-candidate"
    state = smoke.read_json(run_dir / "training_state.json")
    path = Path(str(state["best_checkpoint"])).expanduser().resolve(strict=True)
    return path, None, "training-state-best"


def freeze_checkpoint(
    *, run_dir: Path, explicit_checkpoint: Path | None, reset: bool
) -> tuple[Path, dict[str, Any]]:
    run_dir = Path(run_dir).expanduser().resolve(strict=True)
    probe_root = run_dir / "probes" / "position_probe_v2"
    frozen_dir = probe_root / "frozen_checkpoint"
    lock_path = probe_root / "checkpoint_lock.json"
    if reset:
        if frozen_dir.exists():
            shutil.rmtree(frozen_dir)
        if lock_path.exists():
            lock_path.unlink()
    if lock_path.is_file():
        lock = smoke.read_json(lock_path)
        if lock.get("schema_version") != LOCK_SCHEMA:
            raise RuntimeError(f"unsupported position-probe lock schema: {lock.get('schema_version')}")
        checkpoint = Path(str(lock["frozen_checkpoint"])).resolve(strict=True)
        for filename, field in (("head.safetensors", "head_sha256"), ("tinystories.safetensors", "tinystories_sha256")):
            if sha256_file(checkpoint / filename) != lock[field]:
                raise RuntimeError(f"frozen checkpoint hash mismatch: {filename}")
        return checkpoint, lock

    if explicit_checkpoint is not None:
        source = Path(explicit_checkpoint).expanduser().resolve(strict=True)
        score = _checkpoint_score(smoke.read_json(source / "meta.json"))
        source_kind = "explicit"
    else:
        source, score, source_kind = resolve_best_finalized_checkpoint(run_dir)
    required = [source / "head.safetensors", source / "tinystories.safetensors", source / "meta.json"]
    if not all(path.is_file() for path in required):
        raise RuntimeError(f"probe checkpoint is incomplete: {source}")
    probe_root.mkdir(parents=True, exist_ok=True)
    temp = frozen_dir.with_name("frozen_checkpoint.tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    for filename in ("head.safetensors", "tinystories.safetensors", "meta.json"):
        shutil.copy2(source / filename, temp / filename)
    if frozen_dir.exists():
        shutil.rmtree(frozen_dir)
    os.replace(temp, frozen_dir)
    frozen_meta = smoke.read_json(frozen_dir / "meta.json")
    lock = {
        "schema_version": LOCK_SCHEMA,
        "created_unix": time.time(),
        "source_checkpoint": str(source),
        "source_kind": source_kind,
        "source_cycle": frozen_meta.get("cycle"),
        "source_reuse_depth": frozen_meta.get("reuse_depth"),
        "source_predev_score": list(score) if score is not None else None,
        "frozen_checkpoint": str(frozen_dir.resolve()),
        "head_sha256": sha256_file(frozen_dir / "head.safetensors"),
        "tinystories_sha256": sha256_file(frozen_dir / "tinystories.safetensors"),
    }
    atomic_json(lock_path, lock)
    return frozen_dir.resolve(), lock


def load_models(*, torch, checkpoint: Path, local_files_only: bool):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from safetensors.torch import load_file

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the position probe")
    tokenizer = AutoTokenizer.from_pretrained(
        TINYSTORIES_MODEL,
        local_files_only=bool(local_files_only),
        trust_remote_code=False,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    lm = AutoModelForCausalLM.from_pretrained(
        TINYSTORIES_MODEL,
        dtype=torch.bfloat16,
        attn_implementation="eager",
        local_files_only=bool(local_files_only),
        trust_remote_code=False,
    ).to("cuda")
    lm.load_state_dict(load_file(str(checkpoint / "tinystories.safetensors"), device="cpu"), strict=True)
    for parameter in lm.parameters():
        parameter.requires_grad_(False)
    lm.eval()
    head, hidden_sizes = mature.build_layer_tap_head(
        torch=torch,
        hidden_sizes={"tinystories": TINYSTORIES_HIDDEN},
    )
    if hidden_sizes != {"tinystories": TINYSTORIES_HIDDEN}:
        raise RuntimeError(f"micro-head hidden size drifted: {hidden_sizes}")
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    head.to(device="cuda", dtype=torch.bfloat16).eval()
    output = lm.get_output_embeddings()
    backbone = getattr(lm, "transformer", None) or lm.base_model
    bundle = smoke.BackboneBundle(
        label="tinystories",
        model_name=TINYSTORIES_MODEL,
        lm=lm,
        backbone=backbone,
        tokenizer=tokenizer,
        output_weight=output.weight,
        hidden_size=TINYSTORIES_HIDDEN,
        max_positions=smoke._max_positions(backbone.config),
    )
    return tokenizer, bundle, head


def _continuation_predictor_mean(hidden, *, prompt_length: int, answer_length: int):
    start = int(prompt_length)
    count = int(answer_length)
    predictors = hidden[start - 1 : start + count - 1]
    if int(predictors.shape[0]) != count:
        raise RuntimeError("continuation predictor span mismatch")
    return predictors.mean(dim=0)


def evaluate_variant(
    *,
    torch,
    bundle,
    head,
    variant: PromptVariant,
    candidates: tuple[str, str],
    gold_index: int,
    mode: str,
    expanded_tokens: int,
    prompt_evidence_tokens: int,
    answer_evidence_tokens: int,
) -> dict[str, Any]:
    import torch.nn.functional as F

    device = bundle.output_weight.device
    input_embeddings = bundle.lm.get_input_embeddings()
    prompt_ids = torch.tensor(variant.prompt_ids, dtype=torch.long, device=device)
    prompt_vectors = input_embeddings(prompt_ids)
    if mode == "none":
        counts = replication_counts("none", len(variant.prompt_ids), len(variant.prompt_ids))
    else:
        counts = replication_counts(mode, len(variant.prompt_ids), int(expanded_tokens))
    repeats = torch.tensor(counts, dtype=torch.long, device=device)
    expanded_prompt = torch.repeat_interleave(prompt_vectors, repeats, dim=0)
    prompt_length = int(expanded_prompt.shape[0])
    answer_ids = [
        list(bundle.tokenizer.encode(" " + word, add_special_tokens=False)) for word in candidates
    ]
    if any(not ids for ids in answer_ids):
        raise RuntimeError("empty probe answer encoding")
    if len({len(ids) for ids in answer_ids}) != 1:
        raise RuntimeError(f"probe candidate token lengths diverged: {[len(ids) for ids in answer_ids]}")
    answer_length = len(answer_ids[0])
    width = prompt_length + answer_length
    if width > int(bundle.max_positions):
        raise RuntimeError(f"probe sequence exceeds model context: {width} > {bundle.max_positions}")
    batch = len(candidates)
    embeds = torch.zeros(
        (batch, width, TINYSTORIES_HIDDEN),
        dtype=bundle.output_weight.dtype,
        device=device,
    )
    attention = torch.ones((batch, width), dtype=torch.long, device=device)
    target_tokens = torch.empty((batch, answer_length), dtype=torch.long, device=device)
    for index, ids in enumerate(answer_ids):
        ids_tensor = torch.tensor(ids, dtype=torch.long, device=device)
        embeds[index, :prompt_length] = expanded_prompt
        embeds[index, prompt_length:] = input_embeddings(ids_tensor)
        target_tokens[index] = ids_tensor

    with torch.no_grad():
        output = bundle.backbone(
            inputs_embeds=embeds,
            attention_mask=attention,
            use_cache=False,
            output_hidden_states=True,
            return_dict=True,
        )
        final_hidden = output.last_hidden_state
        hidden_states = tuple(output.hidden_states or ())
        residual_hidden = tuple(
            hidden_states[layer] for layer in mature.TINYSTORIES_RESIDUAL_LAYERS
        )
        prompt_idx = smoke.balanced_indices(0, prompt_length, int(prompt_evidence_tokens))
        answer_idx = smoke.balanced_indices(
            prompt_length, prompt_length + answer_length, int(answer_evidence_tokens)
        )
        evidence_idx = prompt_idx + answer_idx
        memory_parts = []
        residual_memory_parts = []
        option_context = []
        residual_option_context = []
        option_predictor = []
        residual_option_predictor = []
        option_terminal = []
        residual_option_terminal = []
        option_question = []
        residual_option_question = []
        option_lexical = []
        option_logp = []
        for index in range(batch):
            memory_parts.append(final_hidden[index, evidence_idx].detach())
            residual_memory_parts.append(
                torch.cat([hidden[index, evidence_idx] for hidden in residual_hidden], dim=-1).detach()
            )
            prompt_mean = final_hidden[index, :prompt_length].mean(dim=0).detach()
            residual_prompt_mean = torch.cat(
                [hidden[index, :prompt_length].mean(dim=0) for hidden in residual_hidden], dim=-1
            ).detach()
            answer_mean = final_hidden[index, prompt_length:].mean(dim=0).detach()
            residual_answer_mean = torch.cat(
                [hidden[index, prompt_length:].mean(dim=0) for hidden in residual_hidden], dim=-1
            ).detach()
            predictor = _continuation_predictor_mean(
                final_hidden[index], prompt_length=prompt_length, answer_length=answer_length
            ).detach()
            residual_predictor = torch.cat(
                [
                    _continuation_predictor_mean(
                        hidden[index], prompt_length=prompt_length, answer_length=answer_length
                    )
                    for hidden in residual_hidden
                ],
                dim=-1,
            ).detach()
            terminal = final_hidden[index, -1].detach()
            residual_terminal = torch.cat([hidden[index, -1] for hidden in residual_hidden], dim=-1).detach()
            lexical = bundle.output_weight[target_tokens[index]].mean(dim=0).detach()
            predictors = final_hidden[index, prompt_length - 1 : prompt_length + answer_length - 1]
            logits = F.linear(predictors, bundle.output_weight).float()
            logp = F.log_softmax(logits, dim=-1).gather(
                1, target_tokens[index].unsqueeze(1)
            ).squeeze(1).mean().detach()
            option_question.append(prompt_mean)
            residual_option_question.append(residual_prompt_mean)
            option_context.append(answer_mean)
            residual_option_context.append(residual_answer_mean)
            option_predictor.append(predictor)
            residual_option_predictor.append(residual_predictor)
            option_terminal.append(terminal)
            residual_option_terminal.append(residual_terminal)
            option_lexical.append(lexical)
            option_logp.append(logp)

        memory = torch.cat(memory_parts, dim=0)
        residual_memory = torch.cat(residual_memory_parts, dim=0)
        evidence = {
            "memory": memory,
            "residual_source_memory": residual_memory,
            "option_context": torch.stack(option_context),
            "residual_source_option_context": torch.stack(residual_option_context),
            "option_predictor": torch.stack(option_predictor),
            "residual_source_option_predictor": torch.stack(residual_option_predictor),
            "option_terminal": torch.stack(option_terminal),
            "residual_source_option_terminal": torch.stack(residual_option_terminal),
            "option_question": torch.stack(option_question),
            "residual_source_option_question": torch.stack(residual_option_question),
            "option_lexical": torch.stack(option_lexical),
            "option_logp": torch.stack(option_logp),
            "global": memory.mean(dim=0),
            "residual_source_global": residual_memory.mean(dim=0),
        }
        clef_logits = head({"tinystories": evidence})
        loss, ce, brier = smoke.loss_parts(torch, clef_logits, int(gold_index))
        clef_probs = clef_logits.float().softmax(dim=-1).detach().cpu().tolist()
        raw_logits = torch.stack(option_logp).float()
        raw_probs = raw_logits.softmax(dim=-1).detach().cpu().tolist()

    clef_score = _score_from_probabilities(clef_probs, int(gold_index))
    raw_score = _score_from_probabilities(raw_probs, int(gold_index))
    expanded_start, expanded_end, expanded_center = expanded_span(
        counts, variant.fact_start, variant.fact_end
    )
    fact_counts = counts[variant.fact_start : variant.fact_end]
    return {
        "clef": {
            **clef_score,
            "loss": float(loss.detach().item()),
            "cross_entropy": float(ce.detach().item()),
            "brier": float(brier.detach().item()),
            "probabilities": clef_probs,
        },
        "backbone": {
            **raw_score,
            "candidate_mean_logp": [float(v) for v in raw_logits.detach().cpu().tolist()],
            "probabilities": raw_probs,
        },
        "geometry": {
            "source_tokens": len(variant.prompt_ids),
            "model_prompt_tokens": prompt_length,
            "replication_min": min(counts),
            "replication_max": max(counts),
            "fact_replication_min": min(fact_counts),
            "fact_replication_max": max(fact_counts),
            "fact_replication_mean": sum(fact_counts) / len(fact_counts),
            "source_fact_position": variant.actual_position,
            "expanded_fact_position": expanded_center,
            "expanded_fact_start": expanded_start,
            "expanded_fact_end": expanded_end,
        },
    }


def parse_positions(value: str) -> tuple[float, ...]:
    positions = tuple(float(piece.strip()) for piece in value.split(",") if piece.strip())
    if not positions or any(not 0.0 < value < 1.0 for value in positions):
        raise argparse.ArgumentTypeError("positions must be comma-separated fractions inside (0,1)")
    return positions


def parse_modes(value: str) -> tuple[str, ...]:
    modes = tuple(piece.strip().lower() for piece in value.split(",") if piece.strip())
    allowed = set(DEFAULT_MODES)
    if not modes or any(mode not in allowed for mode in modes):
        raise argparse.ArgumentTypeError(f"modes must be drawn from {sorted(allowed)}")
    return modes


def self_test() -> dict[str, Any]:
    assert replication_counts("uniform", 7, 14) == [2] * 7
    tri = replication_counts("triangular", 7, 16)
    assert tri == [1, 2, 3, 4, 3, 2, 1], tri
    edge = replication_counts("edge", 7, 16)
    assert sum(edge) == 16 and edge[0] > edge[3] and edge[-1] > edge[3]
    for n in range(1, 80):
        for target in range(n, n + 150):
            for mode in ("uniform", "triangular", "edge"):
                counts = replication_counts(mode, n, target)
                assert len(counts) == n
                assert sum(counts) == target
                assert min(counts) >= 1
    matrix = {
        mode: {
            position: {"accuracy": 0.5, "mean_loss": float(i), "mean_gold_probability": 0.5}
            for i, position in enumerate(("0.05", "0.25", "0.50", "0.75", "0.95"), 1)
        }
        for mode in DEFAULT_MODES
    }
    diag = curve_diagnostics(matrix, loss_key="mean_loss")
    assert set(diag) == set(DEFAULT_MODES)
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "triangular_7_to_16": tri,
        "edge_7_to_16": edge,
        "default_positions": list(DEFAULT_POSITIONS),
        "default_modes": list(DEFAULT_MODES),
        "default_items": DEFAULT_ITEMS,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--reset-frozen-checkpoint", action="store_true")
    parser.add_argument("--items", type=int, default=DEFAULT_ITEMS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--positions", type=parse_positions, default=DEFAULT_POSITIONS)
    parser.add_argument("--modes", type=parse_modes, default=DEFAULT_MODES)
    parser.add_argument("--source-tokens", type=int, default=DEFAULT_SOURCE_TOKENS)
    parser.add_argument("--expanded-tokens", type=int, default=DEFAULT_EXPANDED_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    if int(args.items) <= 0:
        raise SystemExit("--items must be positive")
    if int(args.source_tokens) <= 0 or int(args.expanded_tokens) <= int(args.source_tokens):
        raise SystemExit("position probe requires expanded_tokens > source_tokens > 0")

    import torch

    run_dir = Path(args.run_dir).expanduser().resolve(strict=True)
    checkpoint, lock = freeze_checkpoint(
        run_dir=run_dir,
        explicit_checkpoint=args.checkpoint,
        reset=bool(args.reset_frozen_checkpoint),
    )
    tokenizer, bundle, head = load_models(
        torch=torch,
        checkpoint=checkpoint,
        local_files_only=bool(args.local_files_only),
    )
    items = build_items(tokenizer=tokenizer, count=int(args.items), seed=int(args.seed))
    rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    geometry: dict[str, dict[str, list[dict[str, Any]]]] = {
        mode: {f"{position:.2f}": [] for position in args.positions} for mode in args.modes
    }
    started = time.perf_counter()
    total = len(items) * len(args.positions) * len(args.modes)
    completed = 0
    for item in items:
        gold_word = item.candidate_words[item.gold_index]
        variants = {
            float(position): build_prompt_variant(
                tokenizer=tokenizer,
                gold_word=gold_word,
                position=float(position),
                source_tokens=int(args.source_tokens),
            )
            for position in args.positions
        }
        # Strict semantic identity check: moving the fact only changes its insertion
        # point.  Candidate words and the exact token count remain fixed.
        if len({len(variant.prompt_ids) for variant in variants.values()}) != 1:
            raise RuntimeError("positional variants do not have identical source length")
        for position in args.positions:
            position_key = f"{float(position):.2f}"
            variant = variants[float(position)]
            for mode in args.modes:
                result = evaluate_variant(
                    torch=torch,
                    bundle=bundle,
                    head=head,
                    variant=variant,
                    candidates=item.candidate_words,
                    gold_index=item.gold_index,
                    mode=mode,
                    expanded_tokens=int(args.expanded_tokens),
                    prompt_evidence_tokens=int(args.prompt_evidence_tokens),
                    answer_evidence_tokens=int(args.answer_evidence_tokens),
                )
                base_fields = {
                    "item_id": item.item_id,
                    "mode": mode,
                    "position": float(position),
                    "position_key": position_key,
                    "gold_index": item.gold_index,
                    "candidate_words": list(item.candidate_words),
                }
                rows.append({**base_fields, **result["clef"]})
                raw_rows.append({**base_fields, **result["backbone"]})
                geometry[mode][position_key].append(result["geometry"])
                completed += 1
                if completed % 100 == 0 or completed == total:
                    elapsed = time.perf_counter() - started
                    print(json.dumps({
                        "event": "position_probe_progress",
                        "completed": completed,
                        "total": total,
                        "percent": 100.0 * completed / total,
                        "elapsed_seconds": elapsed,
                        "evaluations_per_second": completed / max(elapsed, 1e-9),
                    }), file=sys.stderr, flush=True)

    clef_matrix: dict[str, dict[str, Any]] = {}
    backbone_matrix: dict[str, dict[str, Any]] = {}
    geometry_summary: dict[str, dict[str, Any]] = {}
    for mode in args.modes:
        clef_matrix[mode] = {}
        backbone_matrix[mode] = {}
        geometry_summary[mode] = {}
        for position in args.positions:
            key = f"{float(position):.2f}"
            clef_cells = [row for row in rows if row["mode"] == mode and row["position_key"] == key]
            raw_cells = [row for row in raw_rows if row["mode"] == mode and row["position_key"] == key]
            clef_matrix[mode][key] = summarize(clef_cells)
            backbone_matrix[mode][key] = summarize_raw(raw_cells)
            cells = geometry[mode][key]
            geometry_summary[mode][key] = {
                "source_fact_position_mean": sum(float(cell["source_fact_position"]) for cell in cells) / len(cells),
                "expanded_fact_position_mean": sum(float(cell["expanded_fact_position"]) for cell in cells) / len(cells),
                "fact_replication_mean": sum(float(cell["fact_replication_mean"]) for cell in cells) / len(cells),
                "replication_min": min(int(cell["replication_min"]) for cell in cells),
                "replication_max": max(int(cell["replication_max"]) for cell in cells),
            }

    result = {
        "ok": True,
        "schema_version": SCHEMA,
        "run_dir": str(run_dir),
        "checkpoint_lock": lock,
        "checkpoint": str(checkpoint),
        "items": len(items),
        "evaluations": total,
        "positions": [float(v) for v in args.positions],
        "modes": list(args.modes),
        "source_tokens": int(args.source_tokens),
        "expanded_tokens": int(args.expanded_tokens),
        "clef_matrix": clef_matrix,
        "backbone_matrix": backbone_matrix,
        "geometry_matrix": geometry_summary,
        "clef_position_curve": curve_diagnostics(clef_matrix, loss_key="mean_loss"),
        "backbone_position_curve": curve_diagnostics(backbone_matrix, loss_key="mean_nll"),
        "clef_triangular_vs_uniform": paired_mode_delta(rows, candidate="triangular", baseline="uniform")
        if "triangular" in args.modes and "uniform" in args.modes else {},
        "elapsed_seconds": time.perf_counter() - started,
    }
    output = args.output
    if output is None:
        probe_root = run_dir / "probes" / "position_probe_v2"
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        output = probe_root / f"position-probe-{stamp}.json"
    output = Path(output).expanduser().resolve()
    full = dict(result)
    full["clef_rows"] = rows
    full["backbone_rows"] = raw_rows
    atomic_json(output, full)
    result["result_file"] = str(output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
