#!/usr/bin/env python3
"""End-to-end NanoJev bolt-on objective smoke: dictionary prose vs repo code.

Only one new objective supplies gradients:

    dictionary definition + "Is this English?" -> Yes
    dictionary definition + "Is this code?"    -> No
    repository code       + "Is this English?" -> No
    repository code       + "Is this code?"    -> Yes

The smoke exercises the persistent SQLite API, incrementally grows lexical
coverage, uses the existing frozen-Qwen 1024-hidden + continuation-logP path,
and trains the existing NanoJev head.  Old tasks are optional thermometers only;
they never contribute gradients here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from typing import Any, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore
from nanojev_objective_api import (
    ObjectCandidate,
    ObjectPath,
    ObjectQuestion,
    ObjectiveRegistry,
    binary_yes_no_question,
    validate_questions,
)


SCHEMA = "main-computer-nanojev-dictionary-code-objective-smoke-v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_dictionary_code_objective_smoke_v1"
DEFAULT_SOURCE_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_logp_v1"
DEFAULT_CUTOVER_DIR = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_logp_cutover_v1"
DEFAULT_DICTIONARY_CACHE = r"C:\Users\subsi\NanoJev\cache\english-wordnet-2025.zip"
DEFAULT_DICTIONARY_URL = "https://en-word.net/static/english-wordnet-2025.zip"
STRATA = ("definition_english", "definition_code", "code_english", "code_code")


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def stable_seed(*parts: object) -> int:
    import hashlib
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def question_prompt(kind: str, text: str) -> str:
    if kind == "english":
        question = "Is this English?"
    elif kind == "code":
        question = "Is this code?"
    else:
        raise ValueError(kind)
    return f"{question}\n\n{text.strip()}\n\nAnswer:"


class EnglishCodeObjective:
    """First client of the generic Objective boundary."""

    name = "english_code"

    def __init__(self, store: DictionaryCodeStore, repo_root: Path):
        self.store = store
        self.repo_root = Path(repo_root)

    def _questions(self, *, definitions: Sequence[dict], code: Sequence[dict], cycle: int,
                   split: str) -> list[ObjectQuestion]:
        questions: list[ObjectQuestion] = []
        for row in definitions:
            object_id = str(row["definition_id"])
            text = str(row["definition_text"])
            for kind, yes_is_gold, stratum in (
                ("english", True, "definition_english"),
                ("code", False, "definition_code"),
            ):
                qid = f"{split}:definition:{object_id}:{kind}:cycle-{cycle:06d}"
                questions.append(binary_yes_no_question(
                    question_id=qid,
                    task=self.name,
                    stratum=stratum,
                    prompt=question_prompt(kind, text),
                    yes_is_gold=yes_is_gold,
                    shuffle_seed=stable_seed(qid, "candidate-order"),
                ))
        for row in code:
            object_id = str(row["snippet_id"])
            text = str(row["snippet_text"])
            for kind, yes_is_gold, stratum in (
                ("english", False, "code_english"),
                ("code", True, "code_code"),
            ):
                qid = f"{split}:code:{object_id}:{kind}:cycle-{cycle:06d}"
                questions.append(binary_yes_no_question(
                    question_id=qid,
                    task=self.name,
                    stratum=stratum,
                    prompt=question_prompt(kind, text),
                    yes_is_gold=yes_is_gold,
                    shuffle_seed=stable_seed(qid, "candidate-order"),
                ))
        validate_questions(questions)
        return questions

    def generate_train(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        if count <= 0 or count % 4:
            raise ValueError("English/code train question count must be a positive multiple of 4")
        objects_per_class = count // 4
        definitions = self.store.ensure_fresh_coverage_definitions(objects_per_class)
        self.store.ensure_code_snippets(self.repo_root, max(objects_per_class * 8, 32))
        code = self.store.training_code_snippets(
            objects_per_class, rng,
            target_lengths=[len(str(row["definition_text"])) for row in definitions],
        )
        return self._questions(definitions=definitions, code=code, cycle=cycle, split="train")

    def generate_eval(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        if count <= 0 or count % 4:
            raise ValueError("English/code eval question count must be a positive multiple of 4")
        objects_per_class = count // 4
        definitions = self.store.fresh_eval_definitions(objects_per_class, rng)
        # Evaluation objects become a permanent holdout: training excludes anything
        # with eval_count>0.  Older DBs may already have consumed most of the original
        # code pool, so replenish only the shortfall in never-trained snippets.
        available_fresh_code = self.store.fresh_untrained_code_snippet_count()
        if available_fresh_code < objects_per_class:
            current_code = self.store.stats()["code_snippets"]
            self.store.ensure_code_snippets(
                self.repo_root,
                current_code + (objects_per_class - available_fresh_code),
            )
        code = self.store.fresh_eval_code_snippets(
            objects_per_class, rng,
            target_lengths=[len(str(row["definition_text"])) for row in definitions],
        )
        return self._questions(definitions=definitions, code=code, cycle=cycle, split="eval")


def resolve_source(*, source_experiment: Path, cutover_dir: Path) -> dict[str, Any]:
    cutover = read_json(cutover_dir / "cutover.json")
    if cutover.get("schema_version") != "main-computer-nanojev-direct-qwen-logp-cutover-v1":
        raise RuntimeError(f"unsupported cutover schema: {cutover.get('schema_version')}")
    source_experiment = Path(source_experiment).expanduser()
    if (source_experiment / "training_state.json").is_file():
        state = read_json(source_experiment / "training_state.json")
        latest = state.get("latest_generation")
        if latest:
            checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
            experiment = read_json(source_experiment / "experiment.json")
            return {
                "kind": "logp_experiment",
                "checkpoint": str(checkpoint),
                "optimizer": str(checkpoint / "optimizer.pt"),
                "rng": str(checkpoint / "rng_state.pt"),
                "source_experiment": str(source_experiment.resolve()),
                "source_cycle": int(state.get("cycle", 0)),
                "source_global_step": int(state.get("global_step", 0)),
                "model": experiment.get("model") or cutover.get("model"),
                "revision": experiment.get("resolved_model_revision") or cutover.get("resolved_model_revision"),
                "probe_experiment": experiment.get("probe_experiment") or cutover.get("probe_experiment"),
                "legacy_experiment": experiment.get("legacy_experiment") or cutover.get("legacy_experiment"),
                "repo_root": experiment.get("repo_root") or cutover.get("repo_root"),
            }
    return {
        "kind": "cutover",
        "checkpoint": str(cutover_dir.resolve()),
        "optimizer": str(cutover_dir / "optimizer.pt"),
        "rng": str(cutover_dir / "rng_state.pt"),
        "source_experiment": str(Path(cutover["source_experiment"]).resolve()),
        "source_cycle": int(cutover.get("source_cycle", 0)),
        "source_global_step": int(cutover.get("source_global_step", 0)),
        "model": cutover.get("model"),
        "revision": cutover.get("resolved_model_revision"),
        "probe_experiment": cutover.get("probe_experiment"),
        "legacy_experiment": cutover.get("legacy_experiment"),
        "repo_root": cutover.get("repo_root"),
    }


def load_model(*, direct, source: dict[str, Any], cutover_dir: Path, tools_dir: Path,
               max_answer_tokens: int, head_lr: float, weight_decay: float,
               local_files_only: bool, precision: str, disable_native_triton: bool):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    if precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported")
    torch.backends.cuda.matmul.allow_tf32 = False

    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    if tokenizer.pad_token_id is None:
        raise RuntimeError("tokenizer has no pad/eos token")

    model_name = str(source["model"])
    revision = str(source["revision"])
    emit("smoke_causal_lm_load_start", model=model_name, revision=revision)
    causal_lm = AutoModelForCausalLM.from_pretrained(
        model_name,
        revision=revision,
        dtype=torch.float32,
        attn_implementation="sdpa",
        trust_remote_code=False,
        local_files_only=local_files_only,
    )
    input_embeddings = causal_lm.get_input_embeddings()
    output_embeddings = causal_lm.get_output_embeddings()
    if output_embeddings is None or not hasattr(output_embeddings, "weight"):
        raise RuntimeError("Qwen causal LM does not expose native output embeddings")
    if input_embeddings.weight.data_ptr() != output_embeddings.weight.data_ptr():
        raise RuntimeError("smoke requires tied Qwen input/output embeddings for native logP")
    backbone = getattr(causal_lm, "model", None) or causal_lm.base_model
    del causal_lm

    legacy = direct.load_local_module(
        "nanojev_legacy_for_dictionary_code_smoke",
        tools_dir / "nanojev_code_train.py",
    )
    _pipeline, BaseDecisionModel = legacy.import_nanojev(Path(legacy_meta["nanojev_root"]).resolve(strict=True))
    Model = direct.build_direct_model_class(BaseDecisionModel, max_answer_tokens=max_answer_tokens)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(int(legacy_meta["seed"]) + 99173)
        model = Model(backbone, "attention")
    model.backbone.config.use_cache = False
    for parameter in model.backbone.parameters():
        parameter.requires_grad_(False)

    if source["kind"] == "logp_experiment":
        direct.load_own_checkpoint(model, Path(str(source["checkpoint"])))
    else:
        direct.load_cutover_full_head(model, cutover_dir)

    head_params = [
        parameter for name, parameter in model.named_parameters()
        if name.startswith(direct.HEAD_PREFIXES)
    ]
    if not head_params or any(not parameter.requires_grad for parameter in head_params):
        raise RuntimeError("smoke expected the complete NanoJev head to be trainable")
    non_head_trainable = [
        name for name, parameter in model.named_parameters()
        if parameter.requires_grad and not name.startswith(direct.HEAD_PREFIXES)
    ]
    if non_head_trainable:
        raise RuntimeError(f"unexpected trainable non-head parameters: {non_head_trainable}")

    model.cuda()
    model.backbone.eval()
    optimizer = torch.optim.AdamW([{"params": head_params, "lr": head_lr}], weight_decay=weight_decay)
    optimizer.load_state_dict(torch.load(Path(str(source["optimizer"])), map_location="cpu", weights_only=False))
    # Keep requested smoke LR/WD even when inheriting mature optimizer moments.
    for group in optimizer.param_groups:
        group["lr"] = float(head_lr)
        group["weight_decay"] = float(weight_decay)
    direct.move_optimizer_state_to_cuda(optimizer)
    direct.load_rng(Path(source["rng"]))
    return {
        "torch": torch,
        "model": model,
        "tokenizer": tokenizer,
        "optimizer": optimizer,
        "head_params": head_params,
        "legacy_meta": legacy_meta,
    }


def cache_questions(*, direct, model, tokenizer, questions: Sequence[ObjectQuestion], args):
    filtered, stats = direct.filter_bounded_questions(
        questions,
        tokenizer,
        max_prompt_tokens=args.max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
    )
    if len(filtered) != len(questions):
        raise RuntimeError(f"English/code smoke unexpectedly filtered questions: {stats}")
    return direct.materialize_cached_questions(
        model=model,
        tokenizer=tokenizer,
        questions=filtered,
        max_prompt_tokens=args.max_prompt_tokens,
        pad_token_id=int(tokenizer.pad_token_id),
        precision=args.precision,
        qwen_batch_questions=args.cache_qwen_batch_questions,
    )


def stratified_cached(questions, source_questions: Sequence[ObjectQuestion]) -> dict[str, list]:
    stratum_by_id = {q.question_id: q.stratum for q in source_questions}
    out: dict[str, list] = defaultdict(list)
    for question in questions:
        stratum = stratum_by_id.get(question.question_id)
        if stratum not in STRATA:
            raise RuntimeError(f"cached question lost stratum: {question.question_id}")
        out[stratum].append(question)
    missing = [name for name in STRATA if not out[name]]
    if missing:
        raise RuntimeError(f"missing English/code strata: {missing}")
    return dict(out)


def score_question_margins(model, questions: Sequence) -> list[dict[str, Any]]:
    """Score each yes/no question without turning the result into a decision yet.

    The per-question head margin is the canonical evidence value.  The yes
    probability is emitted as an equivalent human-readable view of that evidence.
    """
    import torch

    scored: list[dict[str, Any]] = []
    model.eval()
    with torch.no_grad():
        for question in questions:
            logits, _ = model.score_cached_questions([question])
            ids = list(question.candidate_ids)
            yi = ids.index("yes")
            ni = ids.index("no")
            scores = logits[0, :len(ids)].float()
            probabilities = scores.softmax(-1)
            vectors = question.candidate_vectors.float()
            scored.append({
                "question_id": question.question_id,
                "head_yes_minus_no": float((scores[yi] - scores[ni]).item()),
                "head_yes_probability": float(probabilities[yi].item()),
                "native_logp_yes_minus_no": float((vectors[yi, -1] - vectors[ni, -1]).item()),
            })
    return scored


def summarize_question_margins(scored: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    return {
        "head_yes_minus_no": (
            sum(float(row["head_yes_minus_no"]) for row in scored) / len(scored)
            if scored else None
        ),
        "native_logp_yes_minus_no": (
            sum(float(row["native_logp_yes_minus_no"]) for row in scored) / len(scored)
            if scored else None
        ),
    }


def paired_object_identity(question_id: str) -> tuple[str, str, str]:
    """Return (pair key, gold class, formulation) from an English/code qid."""
    try:
        prefix, formulation, cycle = question_id.rsplit(":", 2)
        split, object_type, object_id = prefix.split(":", 2)
    except ValueError as exc:
        raise RuntimeError(f"cannot pair English/code question id: {question_id}") from exc
    if formulation not in {"english", "code"}:
        raise RuntimeError(f"unexpected English/code formulation in question id: {question_id}")
    if object_type == "definition":
        gold_class = "english"
    elif object_type == "code":
        gold_class = "code"
    else:
        raise RuntimeError(f"unexpected English/code object type in question id: {question_id}")
    return f"{split}:{object_type}:{object_id}:{cycle}", gold_class, formulation


def paired_object_metrics(scored_by_stratum: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Combine both question formulations for each object, reporting only.

    Training remains ordinary per-question cross entropy.  Production-style
    deterministic evaluation compares the head's evidence for "Is this English?"
    against its evidence for "Is this code?" on the same object.
    """
    pairs: dict[str, dict[str, Any]] = {}
    for stratum in STRATA:
        for row in scored_by_stratum[stratum]:
            pair_key, gold_class, formulation = paired_object_identity(str(row["question_id"]))
            pair = pairs.setdefault(pair_key, {"gold_class": gold_class})
            if pair["gold_class"] != gold_class:
                raise RuntimeError(f"paired object changed gold class: {pair_key}")
            if formulation in pair:
                raise RuntimeError(f"duplicate paired formulation: {pair_key}/{formulation}")
            pair[formulation] = row

    total = 0
    correct = 0
    ties = 0
    label_n: dict[str, int] = defaultdict(int)
    label_correct: dict[str, int] = defaultdict(int)
    gold_logit_margins: list[float] = []
    gold_probability_margins: list[float] = []

    for pair_key, pair in sorted(pairs.items()):
        missing = {"english", "code"}.difference(pair)
        if missing:
            raise RuntimeError(f"paired object missing formulations {sorted(missing)}: {pair_key}")
        english = pair["english"]
        code = pair["code"]
        paired_logit_margin = (
            float(english["head_yes_minus_no"]) - float(code["head_yes_minus_no"])
        )
        paired_probability_margin = (
            float(english["head_yes_probability"]) - float(code["head_yes_probability"])
        )
        if paired_logit_margin == 0.0:
            ties += 1
        predicted = "english" if paired_logit_margin >= 0.0 else "code"
        gold = str(pair["gold_class"])
        is_correct = predicted == gold
        signed_logit_margin = paired_logit_margin if gold == "english" else -paired_logit_margin
        signed_probability_margin = (
            paired_probability_margin if gold == "english" else -paired_probability_margin
        )
        total += 1
        correct += int(is_correct)
        label_n[gold] += 1
        label_correct[gold] += int(is_correct)
        gold_logit_margins.append(signed_logit_margin)
        gold_probability_margins.append(signed_probability_margin)

    return {
        "reporting_only": True,
        "decision_rule": (
            "compare yes evidence for Is this English? vs Is this code? on the same object; "
            "english if english head yes-minus-no margin >= code head yes-minus-no margin, else code"
        ),
        "objects": total,
        "accuracy": correct / total if total else None,
        "label_accuracy": {
            label: label_correct[label] / count
            for label, count in sorted(label_n.items()) if count
        },
        "ties": ties,
        "mean_gold_logit_margin": (
            sum(gold_logit_margins) / len(gold_logit_margins) if gold_logit_margins else None
        ),
        "min_gold_logit_margin": min(gold_logit_margins) if gold_logit_margins else None,
        "mean_gold_probability_margin": (
            sum(gold_probability_margins) / len(gold_probability_margins)
            if gold_probability_margins else None
        ),
        "min_gold_probability_margin": (
            min(gold_probability_margins) if gold_probability_margins else None
        ),
    }


def evaluate_english_code(*, direct, model, cached_by_stratum: dict[str, list], batch_questions: int) -> dict:
    by_stratum = {}
    scored_by_stratum: dict[str, list[dict[str, Any]]] = {}
    for stratum in STRATA:
        metrics = direct.evaluate_cached(model=model, questions=cached_by_stratum[stratum], batch_questions=batch_questions)
        scored = score_question_margins(model, cached_by_stratum[stratum])
        metrics["margins"] = summarize_question_margins(scored)
        by_stratum[stratum] = metrics
        scored_by_stratum[stratum] = scored
    overall = direct.aggregate_metrics(by_stratum)
    complementary = {
        "definition_expected_signs": (
            by_stratum["definition_english"]["margins"]["head_yes_minus_no"] > 0
            and by_stratum["definition_code"]["margins"]["head_yes_minus_no"] < 0
        ),
        "code_expected_signs": (
            by_stratum["code_english"]["margins"]["head_yes_minus_no"] < 0
            and by_stratum["code_code"]["margins"]["head_yes_minus_no"] > 0
        ),
    }
    paired = paired_object_metrics(scored_by_stratum)
    return {
        "overall": overall,
        "by_stratum": by_stratum,
        "complementary": complementary,
        "paired": paired,
    }


def train_cached_questions(*, direct, model, optimizer, head_params, questions: Sequence,
                           steps: int, batch_questions: int, rng: random.Random) -> dict:
    """Objective-agnostic cached-head training loop.

    Objectives are responsible for constructing a balanced population.  This loop
    knows only cached questions, so future objectives do not require trainer branches.
    """
    import torch
    import torch.nn.functional as F

    if batch_questions <= 0:
        raise RuntimeError("--batch-questions must be positive")
    if len(questions) < batch_questions:
        raise RuntimeError(
            f"cached training population smaller than one batch: {len(questions)} < {batch_questions}"
        )
    order = list(range(len(questions)))
    rng.shuffle(order)
    cursor = 0

    def draw_batch():
        nonlocal cursor
        batch = []
        while len(batch) < batch_questions:
            if cursor >= len(order):
                rng.shuffle(order)
                cursor = 0
            take = min(batch_questions - len(batch), len(order) - cursor)
            batch.extend(questions[order[i]] for i in range(cursor, cursor + take))
            cursor += take
        return batch

    losses = []
    grad_norms = []
    model.train()
    model.backbone.eval()
    for _ in range(steps):
        batch = draw_batch()
        optimizer.zero_grad(set_to_none=True)
        logits, _ = model.score_cached_questions(batch)
        targets = torch.tensor([int(q.gold_index) for q in batch], dtype=torch.long, device=logits.device)
        loss = F.cross_entropy(logits, targets)
        if not torch.isfinite(loss):
            raise RuntimeError("non-finite objective smoke loss")
        loss.backward()
        grad_norm = direct.global_grad_norm(head_params)
        if not math.isfinite(grad_norm):
            raise RuntimeError("non-finite objective smoke gradient norm")
        optimizer.step()
        losses.append(float(loss.detach().item()))
        grad_norms.append(float(grad_norm))
    return {
        "steps": steps,
        "mean_loss": sum(losses) / len(losses),
        "last_loss": losses[-1],
        "grad_norm": direct.summarize_grad_norms(grad_norms),
    }


def checkpoint_head_state(model, head_prefixes: Sequence[str]) -> dict[str, Any]:
    return {
        key: value.detach().cpu().contiguous()
        for key, value in model.state_dict().items()
        if key.startswith(tuple(head_prefixes))
    }


def save_smoke_checkpoint(*, direct, experiment_dir: Path, model, optimizer, cycle: int,
                          global_step: int, metrics: dict, source: dict) -> Path:
    import torch
    from safetensors.torch import save_file

    root = experiment_dir / "checkpoints"
    final = root / f"cycle-{cycle:06d}"
    temp = root / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"smoke checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    save_file(checkpoint_head_state(model, direct.HEAD_PREFIXES), str(temp / "head.safetensors"))
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    direct.save_rng(temp / "rng_state.pt")
    atomic_json(temp / "meta.json", {
        "schema_version": SCHEMA,
        "cycle": cycle,
        "global_step": global_step,
        "source": source,
        "only_training_objective": "english_code",
        "metrics": metrics,
    })
    os.replace(temp, final)
    return final


def old_probe_cache(*, direct, model, tokenizer, source: dict, tools_dir: Path, args) -> dict[str, list]:
    if args.skip_old_probes:
        return {}
    probe_exp = Path(str(source["probe_experiment"])).expanduser().resolve(strict=True)
    probe_meta = read_json(probe_exp / "experiment.json")
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)
    ordered_api = load_local_module(
        "nanojev_ordered_for_dictionary_code_smoke",
        tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )
    cached = {}
    for task in direct.TASK_ORDER:
        raw = direct.read_jsonl(probe_exp / "probes" / direct.PROBE_FILES[task])
        questions = direct.objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
        questions, filter_stats = direct.filter_bounded_questions(
            questions, tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        if not questions:
            raise RuntimeError(f"all old probe questions filtered for {task}: {filter_stats}")
        cached[task], _stats = direct.materialize_cached_questions(
            model=model, tokenizer=tokenizer, questions=questions,
            max_prompt_tokens=args.max_prompt_tokens,
            pad_token_id=int(tokenizer.pad_token_id), precision=args.precision,
            qwen_batch_questions=args.cache_qwen_batch_questions,
        )
    return cached


def evaluate_old_probes(*, direct, model, cached: dict[str, list], batch_questions: int) -> dict:
    return {
        task: direct.evaluate_cached(model=model, questions=questions, batch_questions=batch_questions)
        for task, questions in cached.items()
    }


def relation_questions(store: DictionaryCodeStore, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
    rows = store.fresh_covered_relation_reserve(count, rng, consume=True)
    if not rows:
        return []
    questions = []
    for row in rows:
        headword = str(row["word"])
        wrong = store.wrong_definition(
            correct_definition_id=str(row["definition_id"]), pos=str(row["pos"]),
            headword=headword, rng=rng
        )
        correct = str(row["definition_text"])
        wrong_text = str(wrong["definition_text"])
        pos = str(row["pos"])
        forward = (
            "Dictionary entry\n"
            f"Headword: {json.dumps(headword, ensure_ascii=False)}\n"
            f"Part of speech: {pos}\nDefinition:"
        )
        correct_reverse = f"Dictionary entry\nPart of speech: {pos}\nDefinition: {correct}\nHeadword:"
        wrong_reverse = f"Dictionary entry\nPart of speech: {pos}\nDefinition: {wrong_text}\nHeadword:"
        qid = f"relation-smoke:{row['relation_id']}:cycle-{cycle:06d}"
        candidates = [
            ObjectCandidate("correct", (ObjectPath(forward, " " + correct), ObjectPath(correct_reverse, " " + headword))),
            ObjectCandidate("wrong", (ObjectPath(forward, " " + wrong_text), ObjectPath(wrong_reverse, " " + headword))),
        ]
        order_rng = random.Random(stable_seed(qid, "candidate-order"))
        order_rng.shuffle(candidates)
        questions.append(ObjectQuestion(
            question_id=qid,
            task="dictionary_relation_smoke",
            candidates=tuple(candidates),
            gold_index=next(i for i, c in enumerate(candidates) if c.candidate_id == "correct"),
            stratum="relation",
        ))
    return questions


def self_test() -> None:
    synthetic = [
        {"id": "n:1", "pos": "n", "lemmas": ("engine",), "definition": "a machine using power and motion"},
        {"id": "n:2", "pos": "n", "lemmas": ("machine",), "definition": "a device using power"},
        {"id": "n:3", "pos": "n", "lemmas": ("power",), "definition": "capacity of a machine to cause motion"},
        {"id": "n:4", "pos": "n", "lemmas": ("motion",), "definition": "movement caused by power"},
        {"id": "n:5", "pos": "n", "lemmas": ("device",), "definition": "a machine made for a purpose"},
        {"id": "n:6", "pos": "n", "lemmas": ("movement",), "definition": "motion from one place to another"},
    ]
    with tempfile.TemporaryDirectory(prefix="nanojev_dict_code_smoke_selftest_") as td:
        root = Path(td)
        repo = root / "repo"
        repo.mkdir()
        code_lines = [
            "def helper_function(value):",
            "    return value + 1",
            "",
        ]
        code_lines.extend(
            f"result_{i:02d} = helper_function({i})"
            for i in range(40)
        )
        (repo / "sample.py").write_text("\n".join(code_lines) + "\n", encoding="utf-8")
        with DictionaryCodeStore(root / "lexical.db") as store:
            stats = store.ingest_synsets(synthetic)
            assert stats["coverable_words"] >= 4
            first = store.select_next_coverage_definition()
            assert first and first["new_gain"] >= 1
            store.ensure_code_snippets(repo, 2)
            objective = EnglishCodeObjective(store, repo)
            registry = ObjectiveRegistry([objective])
            train = registry.generate_train(
                {"english_code": 4}, cycle=1, rng=random.Random(1)
            )
            eval_questions = registry.generate_eval(
                {"english_code": 4}, cycle=1, rng=random.Random(2)
            )
            assert len(train) == 4 and len(eval_questions) == 4
            assert {q.stratum for q in train} == set(STRATA)
            assert sorted(q.candidates[q.gold_index].candidate_id for q in train) == ["no", "no", "yes", "yes"]
            paired_test = paired_object_metrics({
                "definition_english": [{
                    "question_id": "eval:definition:d1:english:cycle-000001",
                    "head_yes_minus_no": 0.2,
                    "head_yes_probability": 0.55,
                    "native_logp_yes_minus_no": 0.0,
                }],
                "definition_code": [{
                    "question_id": "eval:definition:d1:code:cycle-000001",
                    "head_yes_minus_no": -0.4,
                    "head_yes_probability": 0.40,
                    "native_logp_yes_minus_no": 0.0,
                }],
                "code_english": [{
                    "question_id": "eval:code:c1:english:cycle-000001",
                    "head_yes_minus_no": -0.1,
                    "head_yes_probability": 0.475,
                    "native_logp_yes_minus_no": 0.0,
                }],
                "code_code": [{
                    "question_id": "eval:code:c1:code:cycle-000001",
                    "head_yes_minus_no": 0.3,
                    "head_yes_probability": 0.575,
                    "native_logp_yes_minus_no": 0.0,
                }],
            })
            assert paired_test["objects"] == 2
            assert paired_test["accuracy"] == 1.0
            assert paired_test["label_accuracy"] == {"code": 1.0, "english": 1.0}
            while not store.coverage_complete():
                assert store.select_next_coverage_definition() is not None
            complete = store.stats()
            assert complete["uncovered_words"] == 0
            relation_rows = store.fresh_covered_relation_reserve(2, random.Random(3), consume=True)
            assert len(relation_rows) == 2
            reserve = store.reserve_counts()
            assert reserve.get("smoke_eval", 0) == 2
            roundtrip_stats = dict(complete)
        with DictionaryCodeStore(root / "lexical.db") as reopened:
            assert reopened.stats()["uncovered_words"] == 0
            assert reopened.reserve_counts().get("smoke_eval", 0) == 2
            emit(
                "dictionary_code_objective_smoke_self_test_ok",
                stats=reopened.stats(), reserve=reopened.reserve_counts(),
                pre_reopen_stats=roundtrip_stats,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--source-experiment", default=DEFAULT_SOURCE_EXPERIMENT)
    parser.add_argument("--cutover-dir", default=DEFAULT_CUTOVER_DIR)
    parser.add_argument("--dictionary-cache", default=DEFAULT_DICTIONARY_CACHE)
    parser.add_argument("--dictionary-url", default=DEFAULT_DICTIONARY_URL)
    parser.add_argument(
        "--cycles", type=int, default=None,
        help="deprecated compatibility argument; accepted but ignored because training runs continuously until interrupted",
    )
    parser.add_argument("--train-questions-per-cycle", type=int, default=96)
    parser.add_argument("--eval-questions-per-cycle", type=int, default=128)
    parser.add_argument("--steps-per-cycle", type=int, default=32)
    parser.add_argument("--batch-questions", type=int, default=12)
    parser.add_argument("--eval-batch-questions", type=int, default=32)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=4)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--head-lr", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--target-accuracy", type=float, default=0.90)
    parser.add_argument("--target-streak", type=int, default=2)
    parser.add_argument("--relation-probe-pairs", type=int, default=32)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument("--skip-old-probes", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    for name in ("train_questions_per_cycle", "eval_questions_per_cycle", "steps_per_cycle",
                 "batch_questions", "eval_batch_questions", "cache_qwen_batch_questions"):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.train_questions_per_cycle % 4 or args.eval_questions_per_cycle % 4:
        parser.error("train/eval question counts must be multiples of four")
    if not 0.5 <= args.target_accuracy <= 1.0:
        parser.error("--target-accuracy must be between 0.5 and 1.0")
    if args.target_streak <= 0:
        parser.error("--target-streak must be positive")

    if args.self_test:
        self_test()
        return

    tools_dir = Path(__file__).resolve().parent
    direct_path = tools_dir / "nanojev_code_direct_qwen_logp_train.py"
    dictionary_path = tools_dir / "nanojev_dictionary_smoke.py"
    if not direct_path.is_file():
        raise RuntimeError(f"current logP trainer is required: {direct_path}")
    if not dictionary_path.is_file():
        raise RuntimeError(f"dictionary parser is required: {dictionary_path}")
    direct = load_local_module("nanojev_direct_logp_for_dictionary_code_smoke", direct_path)
    dictionary = load_local_module("nanojev_dictionary_for_dictionary_code_smoke", dictionary_path)

    experiment_dir = Path(args.experiment_dir).expanduser()
    cutover_dir = Path(args.cutover_dir).expanduser().resolve(strict=True)
    source = resolve_source(
        source_experiment=Path(args.source_experiment),
        cutover_dir=cutover_dir,
    )
    experiment_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment_dir / "experiment.json"
    previous_manifest = read_json(manifest_path) if manifest_path.is_file() else None
    if previous_manifest is not None:
        if previous_manifest.get("schema_version") != SCHEMA:
            raise RuntimeError(f"existing smoke experiment has wrong schema: {manifest_path}")
        source = dict(previous_manifest["source"])
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)
    db_path = experiment_dir / "lexical.db"

    dictionary_archive = dictionary.download_dictionary(args.dictionary_url, Path(args.dictionary_cache))
    dictionary_sha = dictionary.file_sha256(dictionary_archive)
    with DictionaryCodeStore(db_path) as store:
        if store.is_empty():
            synsets = dictionary.parse_wordnet_archive(dictionary_archive)
            stats = store.ingest_synsets(synsets)
            store.set_meta("dictionary_sha256", dictionary_sha)
            store.set_meta("dictionary_path", str(dictionary_archive.resolve()))
            emit("lexical_db_ingested", stats=stats, dictionary_sha256=dictionary_sha)
        else:
            previous_sha = store.get_meta("dictionary_sha256")
            if previous_sha != dictionary_sha:
                raise RuntimeError(
                    f"lexical DB dictionary SHA mismatch: db={previous_sha} current={dictionary_sha}"
                )
            emit("lexical_db_reused", stats=store.stats(), reserve=store.reserve_counts())

        source_manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source": source,
            "repo_root": str(repo_root),
            "dictionary_sha256": dictionary_sha,
            "database": str(db_path.resolve()),
            "only_training_objective": "english_code",
            "objective_contract": {
                "definition_english": "yes",
                "definition_code": "no",
                "code_english": "no",
                "code_code": "yes",
                "balanced_objects": True,
                "balanced_prompts": True,
                "balanced_labels": True,
            },
            "generic_boundary": "Question->Candidate->ObjectPath(prompt,answer)",
            "candidate_feature": "terminal_hidden_1024_plus_mean_continuation_logp_1",
        }
        if previous_manifest is not None:
            if previous_manifest.get("dictionary_sha256") != dictionary_sha:
                raise RuntimeError("cannot resume smoke against a different dictionary")
        else:
            atomic_json(manifest_path, source_manifest)

        loaded = load_model(
            direct=direct, source=source, cutover_dir=cutover_dir, tools_dir=tools_dir,
            max_answer_tokens=args.max_answer_tokens, head_lr=args.head_lr,
            weight_decay=args.weight_decay, local_files_only=args.local_files_only,
            precision=args.precision, disable_native_triton=args.disable_native_triton,
        )
        torch = loaded["torch"]
        model = loaded["model"]
        tokenizer = loaded["tokenizer"]
        optimizer = loaded["optimizer"]
        head_params = loaded["head_params"]

        state_path = experiment_dir / "state.json"
        if state_path.is_file():
            state = read_json(state_path)
            latest = state.get("latest_checkpoint")
            if latest:
                checkpoint = Path(str(latest)).resolve(strict=True)
                direct.load_own_checkpoint(model, checkpoint)
                optimizer.load_state_dict(torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False))
                for group in optimizer.param_groups:
                    group["lr"] = float(args.head_lr)
                    group["weight_decay"] = float(args.weight_decay)
                direct.move_optimizer_state_to_cuda(optimizer)
                direct.load_rng(checkpoint / "rng_state.pt")
            cycle_start = int(state.get("cycle", 0))
            global_step = int(state.get("global_step", 0))
        else:
            state = {"cycle": 0, "global_step": int(source["source_global_step"]), "latest_checkpoint": None}
            cycle_start = 0
            global_step = int(source["source_global_step"])
            atomic_json(state_path, state)

        best_paired_accuracy = state.get("best_paired_accuracy")
        best_paired_checkpoint = state.get("best_paired_checkpoint")

        old_cached = old_probe_cache(
            direct=direct, model=model, tokenizer=tokenizer, source=source,
            tools_dir=tools_dir, args=args,
        )
        old_baseline_path = experiment_dir / "old_probe_baseline.json"
        if old_cached and not old_baseline_path.is_file():
            old_baseline = evaluate_old_probes(
                direct=direct, model=model, cached=old_cached,
                batch_questions=args.eval_batch_questions,
            )
            atomic_json(old_baseline_path, old_baseline)
            emit("old_probe_baseline", metrics=old_baseline, overall=direct.aggregate_metrics(old_baseline))

        objective = EnglishCodeObjective(store, repo_root)
        registry = ObjectiveRegistry([objective])
        emit(
            "dictionary_code_smoke_ready",
            registered_objectives=registry.names(),
            only_training_objective="english_code",
            source_kind=source["kind"],
            source_checkpoint=str(source["checkpoint"]),
            trainable_head_params=sum(p.numel() for p in head_params),
            backbone_frozen=all(not p.requires_grad for p in model.backbone.parameters()),
            database=store.stats(),
            reserve=store.reserve_counts(),
            continuous_training=True,
            cycles_argument_ignored=args.cycles,
        )

        # One rotating fresh baseline.  Evaluation objects are permanently excluded
        # from this smoke's training once selected.
        if cycle_start == 0:
            rng0 = random.Random(stable_seed(args.seed, "baseline"))
            baseline_questions = registry.generate_eval(
                {"english_code": args.eval_questions_per_cycle}, cycle=0, rng=rng0
            )
            baseline_cached, cache_stats = cache_questions(
                direct=direct, model=model, tokenizer=tokenizer, questions=baseline_questions, args=args
            )
            baseline = evaluate_english_code(
                direct=direct, model=model,
                cached_by_stratum=stratified_cached(baseline_cached, baseline_questions),
                batch_questions=args.eval_batch_questions,
            )
            atomic_json(experiment_dir / "english_code_baseline.json", baseline)
            emit("english_code_baseline", metrics=baseline, cache=cache_stats)

        streak = int(state.get("success_streak", 0))
        learned_observed = bool(state.get("learned_observed", False))
        learned_cycle = state.get("learned_cycle")
        cycle = cycle_start
        while True:
            cycle += 1
            train_rng = random.Random(stable_seed(args.seed, cycle, "train"))
            eval_rng = random.Random(stable_seed(args.seed, cycle, "eval"))
            train_questions = registry.generate_train(
                {"english_code": args.train_questions_per_cycle}, cycle=cycle, rng=train_rng
            )
            train_cached, train_cache_stats = cache_questions(
                direct=direct, model=model, tokenizer=tokenizer, questions=train_questions, args=args
            )
            train_metrics = train_cached_questions(
                direct=direct, model=model, optimizer=optimizer, head_params=head_params,
                questions=train_cached, steps=args.steps_per_cycle,
                batch_questions=args.batch_questions, rng=train_rng,
            )
            global_step += int(args.steps_per_cycle)

            eval_questions = registry.generate_eval(
                {"english_code": args.eval_questions_per_cycle}, cycle=cycle, rng=eval_rng
            )
            eval_cached, eval_cache_stats = cache_questions(
                direct=direct, model=model, tokenizer=tokenizer, questions=eval_questions, args=args
            )
            eval_metrics = evaluate_english_code(
                direct=direct, model=model,
                cached_by_stratum=stratified_cached(eval_cached, eval_questions),
                batch_questions=args.eval_batch_questions,
            )

            relation_metrics = None
            if args.relation_probe_pairs > 0:
                relation_rng = random.Random(stable_seed(args.seed, cycle, "relation"))
                relation_qs = relation_questions(
                    store, count=args.relation_probe_pairs, cycle=cycle, rng=relation_rng
                )
                if relation_qs:
                    relation_cached, _ = cache_questions(
                        direct=direct, model=model, tokenizer=tokenizer, questions=relation_qs, args=args
                    )
                    relation_metrics = direct.evaluate_cached(
                        model=model, questions=relation_cached, batch_questions=args.eval_batch_questions
                    )

            metrics = {
                "train": train_metrics,
                "eval": eval_metrics,
                "relation_probe": relation_metrics,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
                "train_cache": train_cache_stats,
                "eval_cache": eval_cache_stats,
            }
            checkpoint = save_smoke_checkpoint(
                direct=direct, experiment_dir=experiment_dir, model=model, optimizer=optimizer,
                cycle=cycle, global_step=global_step, metrics=metrics, source=source,
            )
            paired_accuracy = eval_metrics["paired"]["accuracy"]
            paired_best = (
                paired_accuracy is not None
                and (best_paired_accuracy is None or float(paired_accuracy) > float(best_paired_accuracy))
            )
            if paired_best:
                best_paired_accuracy = float(paired_accuracy)
                best_paired_checkpoint = str(checkpoint.resolve())
                best_paired = {
                    "reporting_only": True,
                    "cycle": cycle,
                    "checkpoint": best_paired_checkpoint,
                    "paired": eval_metrics["paired"],
                    "individual_overall": eval_metrics["overall"],
                }
                atomic_json(experiment_dir / "best_paired.json", best_paired)
                emit(
                    "dictionary_code_paired_best",
                    cycle=cycle,
                    checkpoint=best_paired_checkpoint,
                    paired=eval_metrics["paired"],
                )
            success = (
                float(eval_metrics["overall"]["accuracy"] or 0.0) >= args.target_accuracy
                and all(bool(v) for v in eval_metrics["complementary"].values())
            )
            streak = streak + 1 if success else 0
            learned_now = streak >= args.target_streak and not learned_observed
            if learned_now:
                learned_observed = True
                learned_cycle = cycle

            state = {
                "cycle": cycle,
                "global_step": global_step,
                "latest_checkpoint": str(checkpoint.resolve()),
                "last_eval_accuracy": eval_metrics["overall"]["accuracy"],
                "last_paired_accuracy": paired_accuracy,
                "best_paired_accuracy": best_paired_accuracy,
                "best_paired_checkpoint": best_paired_checkpoint,
                "success_streak": streak,
                "learned_observed": learned_observed,
                "learned_cycle": learned_cycle,
                "coverage": store.stats(),
            }
            atomic_json(state_path, state)
            emit("dictionary_code_smoke_cycle", cycle=cycle, metrics=metrics, checkpoint=str(checkpoint))

            if learned_now:
                emit(
                    "dictionary_code_smoke_learned",
                    cycle=cycle,
                    target_accuracy=args.target_accuracy,
                    streak=streak,
                    paired_accuracy=eval_metrics["paired"]["accuracy"],
                    coverage=store.stats(),
                    reporting_only=True,
                    training_continues=True,
                )



if __name__ == "__main__":
    main()
