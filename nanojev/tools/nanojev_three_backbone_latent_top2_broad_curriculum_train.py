#!/usr/bin/env python3
"""Broad-curriculum continuation of the mature top-2 modular NanoJev head.

This experiment branches from the latest committed checkpoint of the existing
load-balanced top-2 run and changes only the training population.  Qwen, Pythia,
and TinyStories remain frozen.  The inherited NanoJev head, task-agnostic router,
and residual expert bank are trainable; task names are diagnostic only.

The curriculum restores the earlier code skills that previously trained well:
legacy ordered continuation, behavior-preserving mutation, and AST equivalence,
while retaining consensus, triad, dictionary-definition, English/code, and the
balanced relative-candidate verifier.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore
from nanojev_objective_api import ObjectiveRegistry, ObjectQuestion, validate_questions

TOOLS = Path(__file__).resolve().parent
SCHEMA = "main-computer-nanojev-three-backbone-latent-top2-broad-curriculum-v1"
PARENT_SCHEMA = "main-computer-nanojev-three-backbone-latent-top2-load-balanced-curriculum-v1"
DEFAULT_EXPERIMENT = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_broad_curriculum_v1"
)
DEFAULT_PARENT_EXPERIMENT = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_load_balanced_curriculum_v1"
)
DEFAULT_CUTOVER = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_load_balanced_cutover_v1"
)

LEGACY_TASK = "legacy"
MUTATION_TASK = "mutation"
AST_TASK = "ast"
DEFAULT_TRAIN_QUESTIONS = 160
DEFAULT_BROAD_PERCENTAGES = {
    LEGACY_TASK: 5.0,
    MUTATION_TASK: 10.0,
    AST_TASK: 20.0,
    "consensus": 15.0,
    "triad": 10.0,
    "dictionary_definition": 12.5,
    "english_code": 12.5,
    "relative_correct": 7.5,
    "relative_wrong": 7.5,
}



def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def read_json(path: Path) -> Any:
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


def clone_sqlite(source: Path, target: Path) -> None:
    source = Path(source).expanduser().resolve(strict=True)
    target = Path(target).expanduser()
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(temp)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    os.replace(temp, target)


def broad_training_plan(total_questions: int, percentages: dict[str, float]) -> dict[str, int]:
    if total_questions <= 0:
        raise ValueError("training population must be positive")
    if set(percentages) != set(DEFAULT_BROAD_PERCENTAGES):
        raise ValueError(f"broad curriculum keys changed: {sorted(percentages)}")
    if any(not math.isfinite(float(v)) or float(v) < 0.0 for v in percentages.values()):
        raise ValueError("broad curriculum percentages must be finite and nonnegative")
    if abs(sum(float(v) for v in percentages.values()) - 100.0) > 1e-9:
        raise ValueError("broad curriculum percentages must sum to exactly 100")
    plan: dict[str, int] = {}
    for task, percent in percentages.items():
        exact = total_questions * float(percent) / 100.0
        count = int(round(exact))
        if abs(exact - count) > 1e-9:
            raise ValueError(
                f"training mix is not integral: total={total_questions} task={task} "
                f"percent={percent} gives {exact}"
            )
        plan[task] = count
    if plan["relative_correct"] != plan["relative_wrong"]:
        raise ValueError("relative correct/wrong populations must be equal")
    if plan["consensus"] % 4:
        raise ValueError("consensus count must be a multiple of four")
    if plan["triad"] % 2:
        raise ValueError("triad count must be even")
    if plan["english_code"] % 4:
        raise ValueError("English/code count must be a multiple of four")
    if plan[MUTATION_TASK] % 2 or plan[AST_TASK] % 2:
        raise ValueError("mutation and AST counts must be even for balanced true/false questions")
    if sum(plan.values()) != total_questions:
        raise ValueError(f"broad plan does not sum to total: {plan}")
    return plan


def current_relative_plan(plan: dict[str, int]) -> dict[str, int]:
    return {
        "consensus": int(plan["consensus"]),
        "triad": int(plan["triad"]),
        "dictionary_definition": int(plan["dictionary_definition"]),
        "english_code": int(plan["english_code"]),
        "relative_correct": int(plan["relative_correct"]),
        "relative_wrong": int(plan["relative_wrong"]),
    }


def broad_primary_plan(plan: dict[str, int]) -> dict[str, int]:
    return {
        LEGACY_TASK: int(plan[LEGACY_TASK]),
        MUTATION_TASK: int(plan[MUTATION_TASK]),
        AST_TASK: int(plan[AST_TASK]),
        "consensus": int(plan["consensus"]),
        "triad": int(plan["triad"]),
        "dictionary_definition": int(plan["dictionary_definition"]),
        "english_code": int(plan["english_code"]),
    }


def resolve_parent_experiment(parent_dir: Path) -> dict[str, Any]:
    parent_dir = Path(parent_dir).expanduser().resolve(strict=True)
    manifest = read_json(parent_dir / "experiment.json")
    if manifest.get("schema_version") != PARENT_SCHEMA:
        raise RuntimeError(
            f"broad curriculum parent has wrong schema: {manifest.get('schema_version')}"
        )
    state = read_json(parent_dir / "state.json")
    latest = state.get("latest_checkpoint")
    if not latest:
        raise RuntimeError("broad curriculum parent has no committed latest checkpoint")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    meta = read_json(checkpoint / "meta.json")
    if int(meta.get("cycle", -1)) != int(state.get("cycle", -2)):
        raise RuntimeError("parent checkpoint cycle does not match parent training state")
    if int(meta.get("global_step", -1)) != int(state.get("global_step", -2)):
        raise RuntimeError("parent checkpoint global step does not match parent training state")
    database = manifest.get("database")
    if not database:
        raise RuntimeError("parent experiment does not identify its lexical DB")
    return {
        "experiment": str(parent_dir),
        "checkpoint": str(checkpoint),
        "cycle": int(meta["cycle"]),
        "global_step": int(meta["global_step"]),
        "database": str(Path(str(database)).expanduser().resolve(strict=True)),
        "inherited_head_sha256": str(meta["inherited_head_sha256"]),
    }


class ReintroducedCodeObjective:
    """Task-agnostic adapter for legacy continuation, mutation, and AST data."""

    def __init__(self, *, name: str, direct, source_sampler, data, mutation, ordered_api,
                 tokenizer, repo_root: Path, train_manifest: Sequence[dict], legacy_meta: dict,
                 train_files_per_cycle: int, max_code_tokens: int, max_prompt_tokens: int,
                 max_answer_tokens: int, seed: int):
        if name not in {LEGACY_TASK, MUTATION_TASK, AST_TASK}:
            raise ValueError(name)
        self.name = name
        self.direct = direct
        self.source_sampler = source_sampler
        self.data = data
        self.mutation = mutation
        self.ordered_api = ordered_api
        self.tokenizer = tokenizer
        self.repo_root = Path(repo_root)
        self.train_manifest = list(train_manifest)
        self.python_manifest = [row for row in train_manifest if row.get("language") == "python"]
        if name != LEGACY_TASK and not self.python_manifest:
            raise RuntimeError(f"{name} objective found no Python rows in training manifest")
        self.legacy_meta = dict(legacy_meta)
        self.train_files_per_cycle = int(train_files_per_cycle)
        self.max_code_tokens = int(max_code_tokens)
        self.max_prompt_tokens = int(max_prompt_tokens)
        self.max_answer_tokens = int(max_answer_tokens)
        self.seed = int(seed)

    def _rows(self, *, cycle: int, split: str, attempt: int) -> list[dict]:
        rows = list(self.train_manifest if self.name == LEGACY_TASK else self.python_manifest)
        rng = random.Random(self.direct.stable_seed(
            self.seed, cycle, split, attempt, self.name, "source-files"
        ))
        rng.shuffle(rows)
        return rows[:min(self.train_files_per_cycle, len(rows))]

    def _raw(self, *, cycle: int, split: str, attempt: int, pair_count: int) -> list[dict]:
        rows = self._rows(cycle=cycle, split=split, attempt=attempt)
        docs = self.data.load_docs(rows, self.repo_root)
        seed = self.direct.stable_seed(self.seed, cycle, split, attempt, self.name, "records")
        if self.name == LEGACY_TASK:
            return self.data.sample_ordered_continuation_paired_records(
                docs=docs, tokenizer=self.tokenizer, split=split, pair_count=pair_count,
                max_prefix_tokens=int(self.legacy_meta["max_prefix_tokens"]), seed=seed,
                max_lexeme_tokens=int(self.legacy_meta["max_lexeme_tokens"]),
                min_symbols=3, max_symbols=5,
            )
        if self.name == MUTATION_TASK:
            return self.mutation.sample_mutation_records(
                docs=docs, data=self.data, tokenizer=self.tokenizer, split=split,
                pair_count=pair_count, max_code_tokens=self.max_code_tokens,
                max_length=int(self.legacy_meta["max_length"]), seed=seed,
            )
        return self.source_sampler.sample_ast_records(
            docs=docs, mutation=self.mutation, data=self.data, tokenizer=self.tokenizer,
            split=split, pair_count=pair_count, max_code_tokens=self.max_code_tokens,
            max_length=int(self.legacy_meta["max_length"]), seed=seed,
        )

    def _questions(self, *, count: int, cycle: int, split: str) -> list[ObjectQuestion]:
        if count <= 0:
            return []
        if self.name in {MUTATION_TASK, AST_TASK} and count % 2:
            raise RuntimeError(f"{self.name} count must be even: {count}")
        by_gold: dict[str, list[ObjectQuestion]] = defaultdict(list)
        seen: set[str] = set()
        target_pairs = count if self.name == LEGACY_TASK else max(count // 2, 1)
        for attempt in range(8):
            raw = self._raw(
                cycle=cycle, split=f"{split}-c{cycle:06d}-a{attempt}", attempt=attempt,
                pair_count=max(target_pairs * 2, 16),
            )
            questions = self.direct.objectize(
                self.name, raw, repo_root=self.repo_root, ordered_api=self.ordered_api
            )
            questions, _stats = self.direct.filter_bounded_questions(
                questions, self.tokenizer, max_prompt_tokens=self.max_prompt_tokens,
                max_answer_tokens=self.max_answer_tokens,
            )
            for question in questions:
                fp = self.direct.question_fingerprint(question)
                if fp in seen:
                    continue
                seen.add(fp)
                gold_id = question.candidates[question.gold_index].candidate_id
                order_rng = random.Random(self.direct.stable_seed(
                    self.seed, cycle, split, self.name, fp, "candidate-order"
                ))
                by_gold[gold_id].append(self.direct.shuffle_candidates(question, order_rng))
            if self.name == LEGACY_TASK and len(by_gold.get("correct", ())) >= count:
                break
            if self.name in {MUTATION_TASK, AST_TASK}:
                need = count // 2
                if len(by_gold.get("positive", ())) >= need and len(by_gold.get("negative", ())) >= need:
                    break
        select_rng = random.Random(self.direct.stable_seed(
            self.seed, cycle, split, self.name, "select"
        ))
        if self.name == LEGACY_TASK:
            pool = list(by_gold.get("correct", ()))
            if len(pool) < count:
                raise RuntimeError(f"legacy objective built only {len(pool)} bounded questions; need {count}")
            select_rng.shuffle(pool)
            selected = pool[:count]
        else:
            need = count // 2
            selected = []
            for gold_id in ("positive", "negative"):
                pool = list(by_gold.get(gold_id, ()))
                if len(pool) < need:
                    raise RuntimeError(
                        f"{self.name} objective built only {len(pool)} {gold_id} questions; need {need}"
                    )
                select_rng.shuffle(pool)
                selected.extend(pool[:need])
            select_rng.shuffle(selected)
        validate_questions(selected)
        if len(selected) != count:
            raise RuntimeError(f"{self.name} objective returned {len(selected)} != {count}")
        return selected

    def generate_train(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        return self._questions(count=count, cycle=cycle, split="train")

    def generate_eval(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        return self._questions(count=count, cycle=cycle, split="eval")


def resolve_cutover(cutover_dir: Path, direct) -> dict[str, Any]:
    cutover_dir = Path(cutover_dir).expanduser().resolve(strict=True)
    cutover = read_json(cutover_dir / "cutover.json")
    if cutover.get("schema_version") != direct.SCHEMA:
        raise RuntimeError(
            f"unsupported latent-router cutover schema: {cutover.get('schema_version')}"
        )
    if tuple(cutover.get("models") or ()) != direct.THREE_FROZEN_MODELS:
        raise RuntimeError("latent-router cutover does not preserve the exact three frozen models")
    if int(cutover.get("candidate_feature_width", 0)) != direct.TOTAL_FEATURE_WIDTH:
        raise RuntimeError("latent-router cutover has the wrong candidate feature width")
    source_manifest = dict(cutover.get("source_manifest") or {})
    inherited = dict(source_manifest.get("source") or {})
    required = ("model", "revision", "probe_experiment", "legacy_experiment", "repo_root")
    missing = [name for name in required if not inherited.get(name)]
    if missing:
        raise RuntimeError(f"latent-router source lineage missing fields: {missing}")
    database = source_manifest.get("database")
    if not database:
        raise RuntimeError("latent-router source experiment does not identify its lexical DB")
    inherited.update({
        "kind": "latent_top2_cutover",
        "checkpoint": str(cutover_dir),
        "rng": str((cutover_dir / "rng_state.pt").resolve(strict=True)),
        "source_experiment": str(Path(str(cutover["source_experiment"])).expanduser().resolve(strict=True)),
        "source_checkpoint": str(Path(str(cutover["source_checkpoint"])).expanduser().resolve(strict=True)),
        "source_cycle": int(cutover["source_cycle"]),
        "source_global_step": int(cutover["source_global_step"]),
        "source_database": str(Path(str(database)).expanduser().resolve(strict=True)),
        "num_experts": int(cutover["num_experts"]),
        "expert_rank": int(cutover["expert_rank"]),
        "top_k": int(cutover["top_k"]),
        "selection": {
            "selection": "latent_top2_cutover_source_checkpoint",
            "cycle": int(cutover["source_cycle"]),
            "global_step": int(cutover["source_global_step"]),
        },
    })
    return inherited


def load_model(*, direct, smoke, source: dict[str, Any], tools_dir: Path,
               max_answer_tokens: int, router_lr: float, weight_decay: float,
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
    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_dir), local_files_only=True, trust_remote_code=False
    )
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
        raise RuntimeError("latent-router training requires tied Qwen input/output embeddings")
    backbone = getattr(causal_lm, "model", None) or causal_lm.base_model
    del causal_lm

    legacy = direct.load_local_module(
        "nanojev_legacy_for_latent_router_train",
        tools_dir / "nanojev_code_train.py",
    )
    _pipeline, BaseDecisionModel = legacy.import_nanojev(
        Path(legacy_meta["nanojev_root"]).resolve(strict=True)
    )
    Model = direct.build_direct_model_class(
        BaseDecisionModel,
        max_answer_tokens=max_answer_tokens,
        num_experts=int(source["num_experts"]),
        expert_rank=int(source["expert_rank"]),
        top_k=int(source["top_k"]),
    )
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(int(legacy_meta["seed"]) + 99173)
        model = Model(backbone, "attention")

    aux_specs = (
        (direct.PYTHIA_MODEL, "pythia", "sdpa"),
        (direct.TINYSTORIES_MODEL, "tinystories", "eager"),
    )
    aux = {}
    for aux_name, label, attn_impl in aux_specs:
        emit("smoke_aux_causal_lm_load_start", model=aux_name, label=label)
        tok = AutoTokenizer.from_pretrained(
            aux_name, local_files_only=local_files_only, trust_remote_code=False
        )
        if tok.pad_token_id is None:
            tok.pad_token = tok.eos_token
        lm = AutoModelForCausalLM.from_pretrained(
            aux_name,
            dtype=torch.float32,
            attn_implementation=attn_impl,
            trust_remote_code=False,
            local_files_only=local_files_only,
        )
        aux[label] = (lm, tok)
    model.attach_auxiliary_backbones(
        pythia_lm=aux["pythia"][0],
        pythia_tokenizer=aux["pythia"][1],
        tinystories_lm=aux["tinystories"][0],
        tinystories_tokenizer=aux["tinystories"][1],
    )
    model.backbone.config.use_cache = False

    direct.load_own_checkpoint(model, Path(str(source["checkpoint"])))

    # Hard freeze first.  Keep all three language-model backbones frozen, then opt
    # the latent modular bank and the inherited NanoJev decision head back into training.
    for _name, parameter in model.named_parameters():
        parameter.requires_grad_(False)

    backbone_parameter_ids = {
        id(parameter)
        for module in (model.backbone, model.pythia_backbone, model.tinystories_backbone)
        for parameter in module.parameters()
    }
    modular_named = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if name.startswith(direct.MODULAR_PREFIXES)
    ]
    if not modular_named:
        raise RuntimeError("latent-router model exposes no modular parameters")
    head_named = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if id(parameter) not in backbone_parameter_ids
        and not name.startswith(direct.MODULAR_PREFIXES)
    ]
    if not head_named:
        raise RuntimeError("latent-router model exposes no inherited head parameters")

    for _name, parameter in modular_named + head_named:
        parameter.requires_grad_(True)

    # Explicitly prove all three language-model parameter sets remain frozen.
    qwen_frozen = all(not p.requires_grad for p in model.backbone.parameters())
    pythia_frozen = all(not p.requires_grad for p in model.pythia_backbone.parameters())
    tinystories_frozen = all(not p.requires_grad for p in model.tinystories_backbone.parameters())
    if not (qwen_frozen and pythia_frozen and tinystories_frozen):
        raise RuntimeError("one or more of the three backbone models is trainable")

    unexpected_frozen = [
        name for name, parameter in model.named_parameters()
        if id(parameter) not in backbone_parameter_ids and not parameter.requires_grad
    ]
    if unexpected_frozen:
        raise RuntimeError(f"unexpected frozen non-backbone parameters: {unexpected_frozen}")

    model.cuda()
    model.eval()
    modular_params = [parameter for _name, parameter in modular_named]
    head_params = [parameter for _name, parameter in head_named]
    trainable_names = [name for name, _parameter in modular_named + head_named]
    trainable_params = modular_params + head_params
    # Start with the historical one-group shape.  Resume compatibility adds the
    # inherited-head group after loading legacy optimizer state, or before loading
    # checkpoints written by this trainable-head continuation.
    optimizer = torch.optim.AdamW(
        [{"params": modular_params, "lr": float(router_lr)}],
        weight_decay=float(weight_decay),
    )
    direct.load_rng(Path(str(source["rng"])))
    return {
        "torch": torch,
        "model": model,
        "tokenizer": tokenizer,
        "optimizer": optimizer,
        "modular_params": modular_params,
        "modular_names": [name for name, _parameter in modular_named],
        "head_params": head_params,
        "head_names": [name for name, _parameter in head_named],
        "trainable_params": trainable_params,
        "trainable_names": trainable_names,
        "legacy_meta": legacy_meta,
        "backbone_frozen": {
            "qwen": qwen_frozen,
            "pythia": pythia_frozen,
            "tinystories": tinystories_frozen,
        },
    }


def _restore_optimizer_with_trainable_head(*, torch, optimizer, checkpoint: Path,
                                           modular_params: Sequence, head_params: Sequence,
                                           router_lr: float, head_lr: float,
                                           weight_decay: float) -> None:
    """Restore legacy/new optimizer state while preserving mature modular Adam moments."""
    saved = torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False)
    saved_groups = list(saved.get("param_groups") or [])
    if len(saved_groups) == 1:
        if len(saved_groups[0].get("params") or []) != len(modular_params):
            raise RuntimeError("legacy optimizer modular parameter count mismatch")
        optimizer.load_state_dict(saved)
        optimizer.add_param_group({
            "params": list(head_params),
            "lr": float(head_lr),
            "weight_decay": float(weight_decay),
        })
    elif len(saved_groups) == 2:
        if len(saved_groups[0].get("params") or []) != len(modular_params):
            raise RuntimeError("resume optimizer modular parameter count mismatch")
        if len(saved_groups[1].get("params") or []) != len(head_params):
            raise RuntimeError("resume optimizer inherited-head parameter count mismatch")
        optimizer.add_param_group({
            "params": list(head_params),
            "lr": float(head_lr),
            "weight_decay": float(weight_decay),
        })
        optimizer.load_state_dict(saved)
    else:
        raise RuntimeError(
            f"unsupported optimizer parameter-group count for trainable-head resume: {len(saved_groups)}"
        )

    if len(optimizer.param_groups) != 2:
        raise RuntimeError("trainable-head optimizer must expose modular and inherited-head groups")
    optimizer.param_groups[0]["lr"] = float(router_lr)
    optimizer.param_groups[1]["lr"] = float(head_lr)
    for group in optimizer.param_groups:
        group["weight_decay"] = float(weight_decay)


def _training_objective_label(question) -> str:
    """Diagnostic label for a cached training question; never exposed to the router."""
    stratum = str(getattr(question, "stratum", "") or "")
    if stratum == "correct_candidate":
        return "relative_correct"
    if stratum == "wrong_candidate":
        return "relative_wrong"
    return str(getattr(question, "task", "unknown"))


def _gradient_group_norms(*, model, trainable_names: Sequence[str], gradients: Sequence) -> dict[str, float]:
    """L2 norms for all trainables plus the modular/router/expert/head partitions."""
    import torch

    if len(trainable_names) != len(gradients):
        raise RuntimeError("gradient telemetry name/value length mismatch")

    def norm_for(predicate) -> float:
        squared = 0.0
        for name, gradient in zip(trainable_names, gradients):
            if gradient is None or not predicate(name):
                continue
            value = gradient.detach().float()
            squared += float(torch.sum(value * value).item())
        return math.sqrt(squared)

    is_modular = lambda name: name.startswith(("latent_router.", "latent_experts."))
    result = {
        "trainable_all": norm_for(lambda _name: True),
        "modular_all": norm_for(is_modular),
        "head_all": norm_for(lambda name: not is_modular(name)),
        "router": norm_for(lambda name: name.startswith("latent_router.")),
        "experts_all": norm_for(lambda name: name.startswith("latent_experts.")),
    }
    for expert_index in range(len(model.latent_experts)):
        prefix = f"latent_experts.{expert_index}."
        result[f"expert_{expert_index}"] = norm_for(lambda name, prefix=prefix: name.startswith(prefix))
    return result


def _gradient_cosine(left: Sequence, right: Sequence) -> float | None:
    """Cosine between two gradient tuples without materializing a flattened vector."""
    import torch

    dot = 0.0
    left_sq = 0.0
    right_sq = 0.0
    for left_gradient, right_gradient in zip(left, right):
        if left_gradient is None or right_gradient is None:
            continue
        left_value = left_gradient.detach().float()
        right_value = right_gradient.detach().float()
        dot += float(torch.sum(left_value * right_value).item())
        left_sq += float(torch.sum(left_value * left_value).item())
        right_sq += float(torch.sum(right_value * right_value).item())
    if left_sq <= 0.0 or right_sq <= 0.0:
        return None
    return float(dot / math.sqrt(left_sq * right_sq))


def _router_step_telemetry(model) -> dict[str, Any]:
    """Snapshot sparse expert selection from the just-scored training batch."""
    import torch

    probabilities = getattr(model, "_last_router_probabilities", None)
    if probabilities is None or probabilities.numel() == 0:
        return {
            "routed_candidates": 0,
            "expert_selection_rate": [],
            "top_expert_pairs": [],
        }
    p = probabilities.detach().float().cpu()
    selected = p > 0
    if not torch.all(selected.sum(dim=-1) == int(model.latent_top_k)):
        raise RuntimeError("gradient telemetry observed invalid top-k router selection")
    pair_counts: Counter[tuple[int, ...]] = Counter()
    for row in selected:
        pair = tuple(int(i) for i in torch.nonzero(row, as_tuple=False).flatten().tolist())
        pair_counts[pair] += 1
    routed_candidates = int(p.shape[0])
    selection_rate = selected.float().mean(dim=0).tolist()
    return {
        "routed_candidates": routed_candidates,
        "expert_selection_rate": selection_rate,
        "top_expert_pairs": [
            {
                "experts": list(pair),
                "count": int(count),
                "rate": float(count / routed_candidates),
            }
            for pair, count in pair_counts.most_common(8)
        ],
    }


def _spike_objective_gradient_decomposition(*, model, trainable_params, trainable_names,
                                            batch: Sequence, balance_weight: float,
                                            entropy_weight: float,
                                            forward_rng_state: dict[str, Any]) -> dict[str, Any]:
    """Read-only CE/regularization gradient decomposition for one already-detected spike.

    The diagnostic performs a second forward pass at the same pre-update parameters and uses
    torch.autograd.grad, which does not write parameter .grad fields.  RNG state is forked and
    restored so instrumentation cannot advance the training RNG stream.
    """
    import torch
    import torch.nn.functional as F

    devices: list[int] = []
    first_parameter = next(iter(trainable_params), None)
    if first_parameter is not None and first_parameter.device.type == "cuda":
        devices = [
            int(first_parameter.device.index)
            if first_parameter.device.index is not None
            else int(torch.cuda.current_device())
        ]

    labels = [_training_objective_label(question) for question in batch]
    with torch.random.fork_rng(devices=devices, enabled=True):
        torch.set_rng_state(forward_rng_state["cpu"])
        if devices and forward_rng_state.get("cuda") is not None:
            torch.cuda.set_rng_state(forward_rng_state["cuda"], device=devices[0])
        logits, _ = model.score_cached_questions(batch)
        targets = torch.tensor(
            [int(q.gold_index) for q in batch],
            dtype=torch.long,
            device=logits.device,
        )
        per_question_ce = F.cross_entropy(logits, targets, reduction="none")
        balance, entropy, _usage, _router_stats = model.router_regularization()
        regularization = float(balance_weight) * balance + float(entropy_weight) * entropy
        diagnostic_total_loss = per_question_ce.mean() + regularization
        total_gradients = torch.autograd.grad(
            diagnostic_total_loss,
            trainable_params,
            retain_graph=True,
            allow_unused=True,
        )

        objective_rows: dict[str, Any] = {}
        for label in sorted(set(labels)):
            indices = [index for index, value in enumerate(labels) if value == label]
            index_tensor = torch.tensor(indices, dtype=torch.long, device=logits.device)
            # Overall CE is a mean over the entire mixed batch.  Scaling each objective's
            # summed CE by batch size makes these terms add exactly to that CE objective.
            contribution = per_question_ce.index_select(0, index_tensor).sum() / len(batch)
            gradients = torch.autograd.grad(
                contribution,
                trainable_params,
                retain_graph=True,
                allow_unused=True,
            )
            objective_rows[label] = {
                "questions": len(indices),
                "ce_loss_contribution": float(contribution.detach().item()),
                "gradient_norms": _gradient_group_norms(
                    model=model,
                    trainable_names=trainable_names,
                    gradients=gradients,
                ),
                "cosine_to_total_training_gradient": _gradient_cosine(
                    gradients, total_gradients
                ),
            }

        regularization_gradients = torch.autograd.grad(
            regularization,
            trainable_params,
            retain_graph=False,
            allow_unused=True,
        )
        regularization_row = {
            "loss_contribution": float(regularization.detach().item()),
            "balance_loss": float(balance.detach().item()),
            "entropy": float(entropy.detach().item()),
            "gradient_norms": _gradient_group_norms(
                model=model,
                trainable_names=trainable_names,
                gradients=regularization_gradients,
            ),
            "cosine_to_total_training_gradient": _gradient_cosine(
                regularization_gradients, total_gradients
            ),
        }

    return {
        "diagnostic_total_gradient_norms": _gradient_group_norms(
            model=model, trainable_names=trainable_names, gradients=total_gradients
        ),
        "ce_by_objective": objective_rows,
        "regularization": regularization_row,
        "contract": {
            "diagnostic_only": True,
            "ce_scaling": "each objective is its summed per-question CE divided by full mixed-batch size",
            "gradient_write": False,
            "optimizer_step": False,
            "rng_advanced": False,
            "forward_rng_replayed": True,
        },
    }


def train_modular_cached_questions(*, direct, model, optimizer, trainable_params,
                                   trainable_names: Sequence[str],
                                   modular_params: Sequence, head_params: Sequence,
                                   questions: Sequence, steps: int, batch_questions: int,
                                   rng: random.Random, balance_weight: float,
                                   entropy_weight: float, grad_clip: float,
                                   head_grad_clip: float | None,
                                   cycle: int, global_step_start: int,
                                   gradient_telemetry_spike_threshold: float) -> dict[str, Any]:
    import torch
    import torch.nn.functional as F

    if batch_questions <= 0:
        raise RuntimeError("--batch-questions must be positive")
    if len(questions) < batch_questions:
        raise RuntimeError("cached training population is smaller than one batch")
    if len(trainable_names) != len(trainable_params):
        raise RuntimeError("trainable parameter names do not match trainable parameter list")
    if len(modular_params) + len(head_params) != len(trainable_params):
        raise RuntimeError("modular/head parameter partitions do not cover all trainables")
    separate_gradient_clipping = head_grad_clip is not None
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
    ce_losses = []
    balance_losses = []
    sparse_load_balance_losses = []
    dense_importance_balance_losses = []
    sparse_load_cv2_values = []
    dense_importance_cv2_values = []
    entropies = []
    grad_norms = []
    gradient_steps: list[dict[str, Any]] = []
    usage_accum = None
    selection_rate_accum = None
    dense_importance_accum = None

    # Keep the inherited head in deterministic eval mode while allowing gradients through it.
    # Preserve the existing router/expert train-mode behavior; task identity is never exposed.
    model.eval()
    model.latent_router.train()
    model.latent_experts.train()

    for step_index in range(steps):
        batch = draw_batch()
        objective_counts = Counter(_training_objective_label(question) for question in batch)
        optimizer.zero_grad(set_to_none=True)
        forward_rng_state: dict[str, Any] = {"cpu": torch.get_rng_state(), "cuda": None}
        first_trainable_parameter = trainable_params[0]
        if first_trainable_parameter.device.type == "cuda":
            cuda_device = (
                int(first_trainable_parameter.device.index)
                if first_trainable_parameter.device.index is not None
                else int(torch.cuda.current_device())
            )
            forward_rng_state["cuda"] = torch.cuda.get_rng_state(cuda_device)
        logits, _ = model.score_cached_questions(batch)
        targets = torch.tensor(
            [int(q.gold_index) for q in batch],
            dtype=torch.long,
            device=logits.device,
        )
        ce = F.cross_entropy(logits, targets)
        balance, entropy, usage, router_stats = model.router_regularization()
        loss = ce + float(balance_weight) * balance + float(entropy_weight) * entropy
        if not torch.isfinite(loss):
            raise RuntimeError("non-finite latent-router training loss")
        loss.backward()

        gradient_norms_preclip = _gradient_group_norms(
            model=model,
            trainable_names=trainable_names,
            gradients=[parameter.grad for parameter in trainable_params],
        )
        routing_step = _router_step_telemetry(model)

        grad_norm_value = float(gradient_norms_preclip["trainable_all"])
        if not math.isfinite(grad_norm_value):
            raise RuntimeError("non-finite trainable gradient norm")

        if separate_gradient_clipping:
            if float(grad_clip) > 0:
                torch.nn.utils.clip_grad_norm_(modular_params, max_norm=float(grad_clip))
            if float(head_grad_clip) > 0:
                torch.nn.utils.clip_grad_norm_(head_params, max_norm=float(head_grad_clip))
        elif float(grad_clip) > 0:
            # Backward-compatible legacy mode: one global norm couples the modular
            # path and inherited head exactly as it did before --head-grad-clip existed.
            torch.nn.utils.clip_grad_norm_(trainable_params, max_norm=float(grad_clip))

        objective_decomposition = None
        if (
            float(gradient_telemetry_spike_threshold) >= 0
            and grad_norm_value >= float(gradient_telemetry_spike_threshold)
        ):
            objective_decomposition = _spike_objective_gradient_decomposition(
                model=model,
                trainable_params=trainable_params,
                trainable_names=trainable_names,
                batch=batch,
                balance_weight=balance_weight,
                entropy_weight=entropy_weight,
                forward_rng_state=forward_rng_state,
            )

        optimizer.step()

        step_row = {
            "step_in_cycle": int(step_index + 1),
            "global_step": int(global_step_start + step_index + 1),
            "batch_questions": len(batch),
            "objective_counts": dict(sorted(objective_counts.items())),
            "loss": float(loss.detach().item()),
            "cross_entropy": float(ce.detach().item()),
            "router_balance_loss": float(balance.detach().item()),
            "router_entropy": float(entropy.detach().item()),
            "gradient_norm_preclip": grad_norm_value,
            "gradient_norms_preclip": gradient_norms_preclip,
            "routing": routing_step,
            "spike_threshold": float(gradient_telemetry_spike_threshold),
            "is_spike": bool(objective_decomposition is not None),
            "objective_gradient_decomposition": objective_decomposition,
        }
        gradient_steps.append(step_row)
        emit(
            "latent_top2_broad_curriculum_gradient_step",
            cycle=int(cycle),
            **step_row,
        )

        losses.append(float(loss.detach().item()))
        ce_losses.append(float(ce.detach().item()))
        balance_losses.append(float(balance.detach().item()))
        sparse_load_balance_losses.append(
            float(router_stats["sparse_load_balance"].detach().item())
        )
        dense_importance_balance_losses.append(
            float(router_stats["dense_importance_balance"].detach().item())
        )
        sparse_load_cv2_values.append(float(router_stats["sparse_load_cv2"].detach().item()))
        dense_importance_cv2_values.append(
            float(router_stats["dense_importance_cv2"].detach().item())
        )
        entropies.append(float(entropy.detach().item()))
        grad_norms.append(grad_norm_value)
        current_usage = usage.detach().float().cpu()
        current_selection_rate = router_stats["selection_rate"].detach().float().cpu()
        current_dense_importance = router_stats["dense_importance"].detach().float().cpu()
        usage_accum = current_usage if usage_accum is None else usage_accum + current_usage
        selection_rate_accum = (
            current_selection_rate
            if selection_rate_accum is None
            else selection_rate_accum + current_selection_rate
        )
        dense_importance_accum = (
            current_dense_importance
            if dense_importance_accum is None
            else dense_importance_accum + current_dense_importance
        )

    mean_usage = (usage_accum / steps).tolist()
    mean_selection_rate = (selection_rate_accum / steps).tolist()
    mean_dense_importance = (dense_importance_accum / steps).tolist()
    return {
        "steps": int(steps),
        "mean_loss": sum(losses) / len(losses),
        "last_loss": losses[-1],
        "mean_cross_entropy": sum(ce_losses) / len(ce_losses),
        "mean_router_balance_loss": sum(balance_losses) / len(balance_losses),
        "mean_sparse_load_balance_loss": (
            sum(sparse_load_balance_losses) / len(sparse_load_balance_losses)
        ),
        "mean_dense_importance_balance_loss": (
            sum(dense_importance_balance_losses) / len(dense_importance_balance_losses)
        ),
        "mean_sparse_load_cv2": sum(sparse_load_cv2_values) / len(sparse_load_cv2_values),
        "mean_dense_importance_cv2": (
            sum(dense_importance_cv2_values) / len(dense_importance_cv2_values)
        ),
        "mean_router_entropy": sum(entropies) / len(entropies),
        "mean_expert_usage": mean_usage,
        "mean_sparse_selection_rate": mean_selection_rate,
        "mean_dense_importance": mean_dense_importance,
        "max_mean_expert_usage": max(mean_usage),
        "min_mean_expert_usage": min(mean_usage),
        "grad_norm_preclip": direct.summarize_grad_norms(grad_norms),
        "gradient_telemetry": {
            "spike_threshold": float(gradient_telemetry_spike_threshold),
            "spike_count": sum(1 for row in gradient_steps if row["is_spike"]),
            "steps": gradient_steps,
            "contract": {
                "router_receives_objective_identity": False,
                "batch_objective_labels_reporting_only": True,
                "per_objective_gradient_decomposition_only_on_spikes": True,
                "optimizer_update_unchanged": True,
            },
        },
        "grad_clip": float(grad_clip),
        "head_grad_clip": (
            None if head_grad_clip is None else float(head_grad_clip)
        ),
        "gradient_clip_mode": (
            "separate_modular_and_head" if separate_gradient_clipping else "legacy_combined"
        ),
        "balance_weight": float(balance_weight),
        "entropy_weight": float(entropy_weight),
        "router_top_k": int(model.latent_top_k),
    }

def router_grouped_telemetry(*, model, groups: dict[str, Sequence], batch_questions: int) -> dict[str, Any]:
    """Report routing by known eval group without exposing labels to the router."""
    import torch

    if batch_questions <= 0:
        raise RuntimeError("router telemetry batch size must be positive")
    result: dict[str, Any] = {
        "analysis_only": True,
        "router_receives_group_identity": False,
        "top_k": int(model.latent_top_k),
        "groups": {},
    }
    model.eval()
    with torch.no_grad():
        for label, questions in groups.items():
            questions = list(questions)
            if not questions:
                continue
            usage_sum = None
            selection_sum = None
            entropy_sum = 0.0
            routed_candidates = 0
            pair_counts: Counter[tuple[int, ...]] = Counter()
            for start in range(0, len(questions), batch_questions):
                batch = questions[start:start + batch_questions]
                model.score_cached_questions(batch)
                p = model._last_router_probabilities
                if p is None or p.numel() == 0:
                    raise RuntimeError(f"router telemetry produced no probabilities for {label}")
                p = p.detach().float().cpu()
                selected = p > 0
                selected_per_row = selected.sum(dim=-1)
                if not torch.all(selected_per_row == int(model.latent_top_k)):
                    raise RuntimeError(
                        f"router telemetry expected exactly {model.latent_top_k} experts per candidate"
                    )
                usage = p.sum(dim=0)
                selections = selected.float().sum(dim=0)
                usage_sum = usage if usage_sum is None else usage_sum + usage
                selection_sum = selections if selection_sum is None else selection_sum + selections
                entropy_sum += float(
                    (-(p.clamp_min(1e-9) * p.clamp_min(1e-9).log()).sum(dim=-1)).sum().item()
                )
                routed_candidates += int(p.shape[0])
                for row in selected:
                    experts = tuple(int(i) for i in torch.nonzero(row, as_tuple=False).flatten().tolist())
                    pair_counts[experts] += 1
            mean_usage = (usage_sum / routed_candidates).tolist()
            selection_rate = (selection_sum / routed_candidates).tolist()
            top_pairs = [
                {
                    "experts": list(pair),
                    "count": int(count),
                    "rate": float(count / routed_candidates),
                }
                for pair, count in pair_counts.most_common(8)
            ]
            result["groups"][label] = {
                "questions": len(questions),
                "routed_candidates": routed_candidates,
                "mean_expert_usage": mean_usage,
                "expert_selection_rate": selection_rate,
                "mean_router_entropy": float(entropy_sum / routed_candidates),
                "top_expert_pairs": top_pairs,
            }
    return result


def save_checkpoint(*, direct, experiment_dir: Path, model, optimizer,
                    cycle: int, global_step: int, metrics: dict[str, Any],
                    source: dict[str, Any], plan: dict[str, int],
                    inherited_sha256: str) -> Path:
    import torch
    from safetensors.torch import save_file

    root = experiment_dir / "checkpoints"
    final = root / f"cycle-{cycle:06d}"
    temp = root / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"latent-router checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    save_file(direct.checkpoint_head_state(model), str(temp / "head.safetensors"))
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    direct.save_rng(temp / "rng_state.pt")
    atomic_json(temp / "meta.json", {
        "schema_version": SCHEMA,
        "cycle": int(cycle),
        "global_step": int(global_step),
        "source": source,
        "training_plan": plan,
        "inherited_head_sha256": inherited_sha256,
        "inherited_head_trainable": True,
        "models_frozen": list(direct.THREE_FROZEN_MODELS),
        "trainable_prefixes": list(direct.MODULAR_PREFIXES),
        "metrics": metrics,
    })
    os.replace(temp, final)
    return final


def main() -> None:
    curriculum = load_local_module(
        "nanojev_three_backbone_consensus_for_latent_router",
        TOOLS / "nanojev_three_backbone_consensus_train.py",
    )
    smoke = load_local_module(
        "nanojev_three_backbone_smoke_for_latent_router",
        TOOLS / "nanojev_three_backbone_objective_smoke.py",
    )
    direct = load_local_module(
        "nanojev_three_backbone_latent_router_direct",
        TOOLS / "nanojev_three_backbone_latent_top2_cutover.py",
    )
    dictionary_curriculum = load_local_module(
        "nanojev_dictionary_curriculum_for_latent_router",
        TOOLS / "nanojev_dictionary_definition_curriculum_train.py",
    )
    triad_curriculum = load_local_module(
        "nanojev_triad_curriculum_for_latent_router",
        TOOLS / "nanojev_triad_curriculum_train.py",
    )
    data = load_local_module(
        "nanojev_code_lexeme_data_for_latent_router",
        TOOLS / "nanojev_code_lexeme_data.py",
    )
    mutation = load_local_module(
        "nanojev_code_mutation_for_latent_router",
        TOOLS / "nanojev_code_mutation_train.py",
    )
    source_sampler = load_local_module(
        "nanojev_pairwise_source_for_latent_router",
        TOOLS / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    ordered_api = load_local_module(
        "nanojev_ordered_for_latent_router",
        TOOLS / "nanojev_frozen_qwen_ordered_signal_smoke.py",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--parent-experiment-dir", default=DEFAULT_PARENT_EXPERIMENT)
    parser.add_argument("--cutover-dir", default=DEFAULT_CUTOVER)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--legacy-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES[LEGACY_TASK])
    parser.add_argument("--mutation-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES[MUTATION_TASK])
    parser.add_argument("--ast-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES[AST_TASK])
    parser.add_argument("--consensus-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES["consensus"])
    parser.add_argument("--triad-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES["triad"])
    parser.add_argument("--dictionary-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES["dictionary_definition"])
    parser.add_argument("--english-code-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES["english_code"])
    parser.add_argument("--relative-correct-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES["relative_correct"])
    parser.add_argument("--relative-wrong-percent", type=float, default=DEFAULT_BROAD_PERCENTAGES["relative_wrong"])
    parser.add_argument("--train-files-per-cycle", type=int, default=curriculum.DEFAULT_TRAIN_FILES_PER_CYCLE)
    parser.add_argument("--triad-max-code-tokens", type=int, default=curriculum.DEFAULT_TRIAD_MAX_CODE_TOKENS)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=curriculum.DEFAULT_CONSENSUS_MAX_CODE_TOKENS)
    parser.add_argument("--reintroduced-max-code-tokens", type=int, default=96)
    parser.add_argument("--relative-max-prompt-tokens", type=int, default=curriculum.DEFAULT_RELATIVE_MAX_PROMPT_TOKENS)
    parser.add_argument("--steps-per-cycle", type=int, default=32)
    parser.add_argument("--batch-questions", type=int, default=10)
    parser.add_argument("--eval-batch-questions", type=int, default=32)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=4)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument(
        "--router-lr", type=float, default=2e-5,
        help="learning rate for latent_router + latent_experts",
    )
    parser.add_argument(
        "--head-lr", type=float, default=None,
        help="learning rate for the inherited NanoJev head; defaults to --router-lr",
    )
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--router-balance-weight", type=float, default=0.01)
    parser.add_argument("--router-entropy-weight", type=float, default=0.001)
    parser.add_argument(
        "--grad-clip", type=float, default=100.0,
        help=(
            "gradient clip for the modular router/expert group when --head-grad-clip is set; "
            "otherwise preserves legacy combined clipping across all trainables"
        ),
    )
    parser.add_argument(
        "--head-grad-clip", type=float, default=None,
        help=(
            "independent gradient clip for the inherited NanoJev head; specifying this "
            "enables separate modular/head clipping; 0 disables head clipping"
        ),
    )
    parser.add_argument(
        "--gradient-telemetry-spike-threshold", type=float, default=20.0,
        help="run per-objective CE-gradient decomposition when total pre-clip trainable grad norm reaches this value; negative disables",
    )
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--old-probe-every", type=int, default=1)
    parser.add_argument("--relative-gap-thresholds", default=curriculum.DEFAULT_GAP_THRESHOLDS)
    parser.add_argument("--conditioned-analysis-folds", type=int, default=curriculum.DEFAULT_CONDITIONED_ANALYSIS_FOLDS)
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means continuous")
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--disable-native-triton", action="store_true")
    args = parser.parse_args()

    for name in (
        "train_questions_per_cycle", "train_files_per_cycle", "triad_max_code_tokens",
        "consensus_max_code_tokens", "reintroduced_max_code_tokens", "relative_max_prompt_tokens", "steps_per_cycle",
        "batch_questions", "eval_batch_questions", "cache_qwen_batch_questions",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.old_probe_every < 0 or args.max_cycles < 0:
        parser.error("--old-probe-every and --max-cycles must be nonnegative")
    if args.head_lr is None:
        args.head_lr = float(args.router_lr)
    if args.router_lr <= 0 or args.head_lr <= 0 or args.weight_decay < 0:
        parser.error("router/head LR must be positive and weight decay nonnegative")
    if (
        args.router_balance_weight < 0
        or args.router_entropy_weight < 0
        or args.grad_clip < 0
        or (args.head_grad_clip is not None and args.head_grad_clip < 0)
    ):
        parser.error("router regularization and gradient clipping values must be nonnegative")
    if not math.isfinite(args.gradient_telemetry_spike_threshold):
        parser.error("--gradient-telemetry-spike-threshold must be finite")
    if args.conditioned_analysis_folds < 2:
        parser.error("--conditioned-analysis-folds must be at least 2")

    try:
        percentages = {
            LEGACY_TASK: args.legacy_percent,
            MUTATION_TASK: args.mutation_percent,
            AST_TASK: args.ast_percent,
            curriculum.CONSENSUS_TASK: args.consensus_percent,
            curriculum.TRIAD_TASK: args.triad_percent,
            curriculum.DICTIONARY_TASK: args.dictionary_percent,
            curriculum.ENGLISH_CODE_TASK: args.english_code_percent,
            curriculum.RELATIVE_CORRECT: args.relative_correct_percent,
            curriculum.RELATIVE_WRONG: args.relative_wrong_percent,
        }
        plan = broad_training_plan(args.train_questions_per_cycle, percentages)
        pair_source_plan = curriculum.relative_source_plan(current_relative_plan(plan))
        gap_thresholds = curriculum.parse_gap_thresholds(args.relative_gap_thresholds)
    except ValueError as exc:
        parser.error(str(exc))

    experiment_dir = Path(args.experiment_dir).expanduser()
    experiment_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment_dir / "experiment.json"
    state_path = experiment_dir / "state.json"
    db_path = experiment_dir / "lexical.db"
    cutover_dir = Path(args.cutover_dir).expanduser().resolve(strict=True)
    source = resolve_cutover(cutover_dir, direct)

    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if manifest.get("schema_version") != SCHEMA:
            raise RuntimeError(f"existing broad-curriculum experiment has wrong schema: {manifest_path}")
        if dict(manifest.get("source") or {}) != source:
            raise RuntimeError("cannot resume broad-curriculum experiment from a different cutover")
        branch_parent = dict(manifest.get("branch_parent") or {})
        if not branch_parent.get("checkpoint"):
            raise RuntimeError("broad-curriculum manifest is missing its pinned parent checkpoint")
        Path(str(branch_parent["checkpoint"])).expanduser().resolve(strict=True)
        if {k: int(v) for k, v in manifest["training_plan"].items()} != plan:
            raise RuntimeError("cannot resume with a different training mix")
        if not db_path.is_file():
            raise RuntimeError(f"broad-curriculum lexical DB is missing: {db_path}")
        manifest["frozen_inherited_head"] = False
        manifest["trainable_inherited_head"] = True
        manifest["optimizer_hyperparameters"] = {
            "router_lr": float(args.router_lr),
            "head_lr": float(args.head_lr),
            "grad_clip": float(args.grad_clip),
            "head_grad_clip": (
                None if args.head_grad_clip is None else float(args.head_grad_clip)
            ),
            "gradient_clip_mode": (
                "separate_modular_and_head"
                if args.head_grad_clip is not None else "legacy_combined"
            ),
        }
        manifest["training_contract"] = (
            "continue the mature broad curriculum with all three language-model backbones frozen; "
            "latent_router + latent_experts + the inherited NanoJev head receive optimizer updates"
        )
        atomic_json(manifest_path, manifest)
    else:
        branch_parent = resolve_parent_experiment(Path(args.parent_experiment_dir))
        clone_sqlite(Path(branch_parent["database"]), db_path)
        manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source": source,
            "branch_parent": branch_parent,
            "database": str(db_path.resolve()),
            "training_plan": plan,
            "training_percentages": percentages,
            "relative_source_plan": pair_source_plan,
            "relative_gap_thresholds": list(gap_thresholds),
            "candidate_feature_width": direct.TOTAL_FEATURE_WIDTH,
            "frozen_backbones": list(direct.THREE_FROZEN_MODELS),
            "frozen_inherited_head": False,
            "trainable_inherited_head": True,
            "optimizer_hyperparameters": {
                "router_lr": float(args.router_lr),
                "head_lr": float(args.head_lr),
                "grad_clip": float(args.grad_clip),
                "head_grad_clip": (
                    None if args.head_grad_clip is None else float(args.head_grad_clip)
                ),
                "gradient_clip_mode": (
                    "separate_modular_and_head"
                    if args.head_grad_clip is not None else "legacy_combined"
                ),
            },
            "router_receives_task_identity": False,
            "num_experts": int(source["num_experts"]),
            "expert_rank": int(source["expert_rank"]),
            "top_k": int(source["top_k"]),
            "trainable_prefixes": list(direct.MODULAR_PREFIXES),
            "training_contract": (
                "branch from the mature load-balanced top-2 checkpoint; restore legacy continuation, "
                "mutation, and AST rehearsal while retaining current objectives; keep all three language-"
                "model backbones frozen while latent_router + latent_experts + the inherited NanoJev head "
                "receive optimizer updates"
            ),
        }
        atomic_json(manifest_path, manifest)
        emit(
            "latent_top2_broad_curriculum_source_selected",
            parent_checkpoint=branch_parent["checkpoint"],
            parent_cycle=branch_parent["cycle"],
            parent_global_step=branch_parent["global_step"],
            training_plan=plan,
            training_percentages=percentages,
        )

    loaded = load_model(
        direct=direct,
        smoke=smoke,
        source=source,
        tools_dir=TOOLS,
        max_answer_tokens=args.max_answer_tokens,
        router_lr=args.router_lr,
        weight_decay=args.weight_decay,
        local_files_only=args.local_files_only,
        precision=args.precision,
        disable_native_triton=args.disable_native_triton,
    )
    torch = loaded["torch"]
    model = loaded["model"]
    tokenizer = loaded["tokenizer"]
    optimizer = loaded["optimizer"]
    modular_params = loaded["modular_params"]
    head_params = loaded["head_params"]
    trainable_params = loaded["trainable_params"]

    inherited_sha256 = direct.inherited_head_sha256(model)

    if state_path.is_file():
        state = read_json(state_path)
        latest = state.get("latest_checkpoint")
        if latest:
            checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
            direct.load_own_checkpoint(model, checkpoint)
            observed = direct.inherited_head_sha256(model)
            expected = str(read_json(checkpoint / "meta.json")["inherited_head_sha256"])
            if observed != expected:
                raise RuntimeError("resume checkpoint inherited-head checksum mismatch")
            inherited_sha256 = expected
            _restore_optimizer_with_trainable_head(
                torch=torch,
                optimizer=optimizer,
                checkpoint=checkpoint,
                modular_params=modular_params,
                head_params=head_params,
                router_lr=args.router_lr,
                head_lr=args.head_lr,
                weight_decay=args.weight_decay,
            )
            direct.move_optimizer_state_to_cuda(optimizer)
            direct.load_rng(checkpoint / "rng_state.pt")
        cycle = int(state.get("cycle", branch_parent["cycle"]))
        global_step = int(state.get("global_step", branch_parent["global_step"]))
    else:
        parent_checkpoint = Path(branch_parent["checkpoint"]).expanduser().resolve(strict=True)
        direct.load_own_checkpoint(model, parent_checkpoint)
        observed = direct.inherited_head_sha256(model)
        if observed != branch_parent["inherited_head_sha256"]:
            raise RuntimeError("branch parent inherited-head checksum mismatch")
        inherited_sha256 = observed
        _restore_optimizer_with_trainable_head(
            torch=torch,
            optimizer=optimizer,
            checkpoint=parent_checkpoint,
            modular_params=modular_params,
            head_params=head_params,
            router_lr=args.router_lr,
            head_lr=args.head_lr,
            weight_decay=args.weight_decay,
        )
        direct.move_optimizer_state_to_cuda(optimizer)
        direct.load_rng(parent_checkpoint / "rng_state.pt")
        cycle = int(branch_parent["cycle"])
        global_step = int(branch_parent["global_step"])
        atomic_json(state_path, {
            "cycle": cycle,
            "global_step": global_step,
            "latest_checkpoint": None,
            "cutover_checkpoint": source["checkpoint"],
            "branch_parent_checkpoint": branch_parent["checkpoint"],
            "inherited_head_sha256": inherited_sha256,
            "training_plan": plan,
            "optimizer_hyperparameters": {
                "router_lr": float(args.router_lr),
                "head_lr": float(args.head_lr),
                "grad_clip": float(args.grad_clip),
                "head_grad_clip": (
                    None if args.head_grad_clip is None else float(args.head_grad_clip)
                ),
                "gradient_clip_mode": (
                    "separate_modular_and_head"
                    if args.head_grad_clip is not None else "legacy_combined"
                ),
            },
        })

    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    train_manifest = read_json(
        Path(str(legacy_meta["manifests"]["train"])).expanduser().resolve(strict=True)
    )
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)

    with DictionaryCodeStore(db_path) as store:
        english_code_objective = smoke.EnglishCodeObjective(store, repo_root)
        dictionary_objective = dictionary_curriculum.DictionaryDefinitionObjective(store)
        triad_objective = triad_curriculum.TriadObjective(
            direct=direct,
            source_sampler=source_sampler,
            data=data,
            mutation=mutation,
            ordered_api=ordered_api,
            tokenizer=tokenizer,
            repo_root=repo_root,
            train_manifest=train_manifest,
            max_length=int(legacy_meta["max_length"]),
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
            train_files_per_cycle=args.train_files_per_cycle,
            max_code_tokens=args.triad_max_code_tokens,
            seed=args.seed,
        )
        consensus_objective = curriculum.ConsensusObjective(
            direct=direct,
            source_sampler=source_sampler,
            data=data,
            mutation=mutation,
            ordered_api=ordered_api,
            tokenizer=tokenizer,
            repo_root=repo_root,
            train_manifest=train_manifest,
            max_length=int(legacy_meta["max_length"]),
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
            train_files_per_cycle=args.train_files_per_cycle,
            max_code_tokens=args.consensus_max_code_tokens,
            seed=args.seed,
        )
        legacy_objective = ReintroducedCodeObjective(
            name=LEGACY_TASK, direct=direct, source_sampler=source_sampler, data=data, mutation=mutation,
            ordered_api=ordered_api, tokenizer=tokenizer, repo_root=repo_root, train_manifest=train_manifest,
            legacy_meta=legacy_meta, train_files_per_cycle=args.train_files_per_cycle,
            max_code_tokens=args.reintroduced_max_code_tokens, max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens, seed=args.seed,
        )
        mutation_objective = ReintroducedCodeObjective(
            name=MUTATION_TASK, direct=direct, source_sampler=source_sampler, data=data, mutation=mutation,
            ordered_api=ordered_api, tokenizer=tokenizer, repo_root=repo_root, train_manifest=train_manifest,
            legacy_meta=legacy_meta, train_files_per_cycle=args.train_files_per_cycle,
            max_code_tokens=args.reintroduced_max_code_tokens, max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens, seed=args.seed,
        )
        ast_objective = ReintroducedCodeObjective(
            name=AST_TASK, direct=direct, source_sampler=source_sampler, data=data, mutation=mutation,
            ordered_api=ordered_api, tokenizer=tokenizer, repo_root=repo_root, train_manifest=train_manifest,
            legacy_meta=legacy_meta, train_files_per_cycle=args.train_files_per_cycle,
            max_code_tokens=args.reintroduced_max_code_tokens, max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens, seed=args.seed,
        )
        registry = ObjectiveRegistry([
            legacy_objective,
            mutation_objective,
            ast_objective,
            consensus_objective,
            triad_objective,
            dictionary_objective,
            english_code_objective,
        ])

        source_experiment = Path(str(source["source_experiment"])).expanduser().resolve(strict=True)
        preservation_questions = curriculum.load_preservation_holdout(
            source_experiment=source_experiment,
            target=experiment_dir / "preservation_holdout.json",
            dictionary_curriculum=dictionary_curriculum,
        )
        preservation_cached, preservation_cache_stats = smoke.cache_questions(
            direct=direct, model=model, tokenizer=tokenizer,
            questions=preservation_questions, args=args,
        )

        triad_source_questions = curriculum.load_old_probe_source_questions(
            direct=direct, source=source, tools_dir=TOOLS, task=curriculum.TRIAD_TASK,
        )
        triad_source_questions, triad_filter_stats = direct.filter_bounded_questions(
            triad_source_questions, tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        if not triad_source_questions:
            raise RuntimeError(f"all fixed triad questions filtered: {triad_filter_stats}")
        triad_cached, triad_cache_stats = direct.materialize_cached_questions(
            model=model, tokenizer=tokenizer, questions=triad_source_questions,
            max_prompt_tokens=args.max_prompt_tokens,
            pad_token_id=int(tokenizer.pad_token_id), precision=args.precision,
            qwen_batch_questions=args.cache_qwen_batch_questions,
        )

        consensus_source_questions = curriculum.load_old_probe_source_questions(
            direct=direct, source=source, tools_dir=TOOLS, task=curriculum.CONSENSUS_TASK,
        )
        consensus_source_questions, consensus_filter_stats = direct.filter_bounded_questions(
            consensus_source_questions, tokenizer,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
        )
        if not consensus_source_questions:
            raise RuntimeError(f"all fixed consensus questions filtered: {consensus_filter_stats}")
        consensus_cached, consensus_cache_stats = direct.materialize_cached_questions(
            model=model, tokenizer=tokenizer, questions=consensus_source_questions,
            max_prompt_tokens=args.max_prompt_tokens,
            pad_token_id=int(tokenizer.pad_token_id), precision=args.precision,
            qwen_batch_questions=args.cache_qwen_batch_questions,
        )

        relative_eval_sources = (
            list(preservation_questions) + list(triad_source_questions) + list(consensus_source_questions)
        )
        validate_questions(relative_eval_sources)
        relative_eval_primary_cached = list(preservation_cached) + list(triad_cached) + list(consensus_cached)
        relative_eval_cached_by_key, relative_eval_cache_stats = curriculum.cache_relative_eval_variants(
            smoke=smoke, direct=direct, model=model, tokenizer=tokenizer,
            source_questions=relative_eval_sources, args=args,
        )

        preservation_cached_by_task: dict[str, list] = defaultdict(list)
        for cached_question in preservation_cached:
            preservation_cached_by_task[str(cached_question.task)].append(cached_question)
        router_eval_groups = {
            curriculum.CONSENSUS_TASK: list(consensus_cached),
            curriculum.TRIAD_TASK: list(triad_cached),
            curriculum.DICTIONARY_TASK: list(
                preservation_cached_by_task.get(curriculum.DICTIONARY_TASK, ())
            ),
            curriculum.ENGLISH_CODE_TASK: list(
                preservation_cached_by_task.get(curriculum.ENGLISH_CODE_TASK, ())
            ),
            curriculum.META_TASK: list(relative_eval_cached_by_key.values()),
        }
        missing_router_groups = [name for name, rows in router_eval_groups.items() if not rows]
        if missing_router_groups:
            raise RuntimeError(f"router telemetry missing fixed eval groups: {missing_router_groups}")

        args.skip_old_probes = False
        old_cached = smoke.old_probe_cache(
            direct=direct, model=model, tokenizer=tokenizer,
            source=source, tools_dir=TOOLS, args=args,
        )

        cutover_eval_path = experiment_dir / "eval_parent.json"
        if not cutover_eval_path.is_file():
            preservation = curriculum.evaluate_preservation(
                dictionary_curriculum=dictionary_curriculum,
                smoke=smoke, direct=direct, model=model,
                cached=preservation_cached, source_questions=preservation_questions,
                batch_questions=args.eval_batch_questions,
            )
            triad_eval = direct.evaluate_cached(
                model=model, questions=triad_cached, batch_questions=args.eval_batch_questions,
            )
            consensus_eval = direct.evaluate_cached(
                model=model, questions=consensus_cached, batch_questions=args.eval_batch_questions,
            )
            relative_eval = curriculum.evaluate_relative_candidate(
                model=model,
                primary_cached=relative_eval_primary_cached,
                source_questions=relative_eval_sources,
                relative_cached_by_key=relative_eval_cached_by_key,
                batch_questions=args.eval_batch_questions,
                gap_thresholds=gap_thresholds,
                conditioned_analysis_folds=args.conditioned_analysis_folds,
            )
            router_eval = router_grouped_telemetry(
                model=model, groups=router_eval_groups,
                batch_questions=args.eval_batch_questions,
            )
            cutover_eval = {
                "preservation": preservation,
                curriculum.TRIAD_TASK: triad_eval,
                curriculum.CONSENSUS_TASK: consensus_eval,
                "relative_candidate": relative_eval,
                "router_by_eval_group": router_eval,
            }
            atomic_json(cutover_eval_path, cutover_eval)
            emit(
                "latent_top2_broad_curriculum_eval_parent",
                metrics=cutover_eval,
                preservation_cache=preservation_cache_stats,
                triad_cache=triad_cache_stats,
                consensus_cache=consensus_cache_stats,
                relative_eval_cache=relative_eval_cache_stats,
            )

        emit(
            "latent_top2_broad_curriculum_ready",
            source_checkpoint=source["source_checkpoint"],
            source_cycle=source["source_cycle"],
            branch_parent_checkpoint=branch_parent["checkpoint"],
            branch_parent_cycle=branch_parent["cycle"],
            branch_parent_global_step=branch_parent["global_step"],
            cycle=cycle,
            global_step=global_step,
            models_frozen=list(direct.THREE_FROZEN_MODELS),
            backbone_frozen=loaded["backbone_frozen"],
            inherited_head_frozen=False,
            inherited_head_sha256=inherited_sha256,
            trainable_modular_params=sum(p.numel() for p in modular_params),
            trainable_inherited_head_params=sum(p.numel() for p in head_params),
            trainable_total_params=sum(p.numel() for p in trainable_params),
            trainable_prefixes=list(direct.MODULAR_PREFIXES),
            router_receives_task_identity=False,
            num_experts=source["num_experts"],
            expert_rank=source["expert_rank"],
            top_k=source["top_k"],
            registered_objectives=registry.names(),
            training_plan=plan,
            training_percentages=percentages,
            relative_source_plan=pair_source_plan,
            router_lr=float(args.router_lr),
            head_lr=float(args.head_lr),
            grad_clip=float(args.grad_clip),
            head_grad_clip=(
                None if args.head_grad_clip is None else float(args.head_grad_clip)
            ),
            gradient_clip_mode=(
                "separate_modular_and_head"
                if args.head_grad_clip is not None else "legacy_combined"
            ),
            gradient_telemetry_spike_threshold=args.gradient_telemetry_spike_threshold,
            continuous=args.max_cycles == 0,
        )

        completed = 0
        while args.max_cycles == 0 or completed < args.max_cycles:
            cycle += 1
            train_rng = random.Random(curriculum.stable_seed(args.seed, cycle, "latent-router-train"))
            primary_plan = broad_primary_plan(plan)
            primary_questions = registry.generate_train(primary_plan, cycle=cycle, rng=train_rng)
            primary_cached, primary_cache_stats = smoke.cache_questions(
                direct=direct, model=model, tokenizer=tokenizer,
                questions=primary_questions, args=args,
            )

            relative_sources = curriculum.select_relative_source_questions(
                questions=primary_questions,
                source_plan=pair_source_plan,
                cycle=cycle,
                seed=args.seed,
            )
            relative_questions: list[ObjectQuestion] = []
            for source_index, source_question in enumerate(relative_sources):
                relative_questions.extend(curriculum.relative_questions_for_source(
                    question=source_question,
                    cycle=cycle,
                    source_index=source_index,
                    seed=args.seed,
                ))
            validate_questions(relative_questions)
            relative_correct_count = sum(
                question.stratum == "correct_candidate" for question in relative_questions
            )
            relative_wrong_count = sum(
                question.stratum == "wrong_candidate" for question in relative_questions
            )
            if relative_correct_count != plan[curriculum.RELATIVE_CORRECT] or relative_wrong_count != plan[curriculum.RELATIVE_WRONG]:
                raise RuntimeError("relative pair construction broke balance")
            relative_cached, relative_cache_stats = smoke.cache_questions(
                direct=direct, model=model, tokenizer=tokenizer,
                questions=relative_questions, args=args,
            )

            training_cached = list(primary_cached) + list(relative_cached)
            if len(training_cached) != args.train_questions_per_cycle:
                raise RuntimeError("final latent-router training population has wrong size")
            shuffle_rng = random.Random(
                curriculum.stable_seed(args.seed, cycle, "latent-router-final-shuffle")
            )
            shuffle_rng.shuffle(training_cached)

            before_sha = direct.inherited_head_sha256(model)
            if before_sha != inherited_sha256:
                raise RuntimeError("inherited head checksum changed outside optimizer training")
            train_metrics = train_modular_cached_questions(
                direct=direct,
                model=model,
                optimizer=optimizer,
                trainable_params=trainable_params,
                trainable_names=loaded["trainable_names"],
                modular_params=modular_params,
                head_params=head_params,
                questions=training_cached,
                steps=args.steps_per_cycle,
                batch_questions=args.batch_questions,
                rng=train_rng,
                balance_weight=args.router_balance_weight,
                entropy_weight=args.router_entropy_weight,
                grad_clip=args.grad_clip,
                head_grad_clip=args.head_grad_clip,
                cycle=cycle,
                global_step_start=global_step,
                gradient_telemetry_spike_threshold=args.gradient_telemetry_spike_threshold,
            )
            after_sha = direct.inherited_head_sha256(model)
            inherited_head_changed = after_sha != before_sha
            inherited_sha256 = after_sha
            global_step += int(args.steps_per_cycle)

            preservation = curriculum.evaluate_preservation(
                dictionary_curriculum=dictionary_curriculum,
                smoke=smoke, direct=direct, model=model,
                cached=preservation_cached, source_questions=preservation_questions,
                batch_questions=args.eval_batch_questions,
            )
            triad_eval = direct.evaluate_cached(
                model=model, questions=triad_cached, batch_questions=args.eval_batch_questions,
            )
            consensus_eval = direct.evaluate_cached(
                model=model, questions=consensus_cached, batch_questions=args.eval_batch_questions,
            )
            relative_eval = curriculum.evaluate_relative_candidate(
                model=model,
                primary_cached=relative_eval_primary_cached,
                source_questions=relative_eval_sources,
                relative_cached_by_key=relative_eval_cached_by_key,
                batch_questions=args.eval_batch_questions,
                gap_thresholds=gap_thresholds,
                conditioned_analysis_folds=args.conditioned_analysis_folds,
            )
            router_eval = router_grouped_telemetry(
                model=model, groups=router_eval_groups,
                batch_questions=args.eval_batch_questions,
            )
            old_metrics = None
            if args.old_probe_every and cycle % args.old_probe_every == 0:
                old_metrics = smoke.evaluate_old_probes(
                    direct=direct, model=model, cached=old_cached,
                    batch_questions=args.eval_batch_questions,
                )

            metrics = {
                "training_plan": plan,
                "train": train_metrics,
                "eval": {
                    "preservation": preservation,
                    curriculum.TRIAD_TASK: triad_eval,
                    curriculum.CONSENSUS_TASK: consensus_eval,
                    "relative_candidate": relative_eval,
                },
                "router_by_eval_group": router_eval,
                "old_probe": old_metrics,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
                "primary_train_cache": primary_cache_stats,
                "relative_train_cache": relative_cache_stats,
                "relative_training": {
                    "source_questions": len(relative_sources),
                    "candidate_questions": len(relative_questions),
                    "correct_candidate": relative_correct_count,
                    "wrong_candidate": relative_wrong_count,
                    "source_by_task": pair_source_plan,
                },
                "invariants": {
                    "inherited_head_sha256": inherited_sha256,
                    "inherited_head_trainable": True,
                    "inherited_head_changed_this_cycle": inherited_head_changed,
                    "models_frozen": list(direct.THREE_FROZEN_MODELS),
                    "router_receives_task_identity": False,
                    "router_top_k": int(source["top_k"]),
                },
            }
            checkpoint = save_checkpoint(
                direct=direct,
                experiment_dir=experiment_dir,
                model=model,
                optimizer=optimizer,
                cycle=cycle,
                global_step=global_step,
                metrics=metrics,
                source={**source, "branch_parent": branch_parent},
                plan=plan,
                inherited_sha256=inherited_sha256,
            )
            state = {
                "cycle": cycle,
                "global_step": global_step,
                "latest_checkpoint": str(checkpoint.resolve()),
                "cutover_checkpoint": source["checkpoint"],
                "branch_parent_checkpoint": branch_parent["checkpoint"],
                "inherited_head_sha256": inherited_sha256,
                "training_plan": plan,
                "training_percentages": percentages,
                "relative_source_plan": pair_source_plan,
                "optimizer_hyperparameters": {
                    "router_lr": float(args.router_lr),
                    "head_lr": float(args.head_lr),
                    "grad_clip": float(args.grad_clip),
                    "head_grad_clip": (
                        None if args.head_grad_clip is None else float(args.head_grad_clip)
                    ),
                    "gradient_clip_mode": (
                        "separate_modular_and_head"
                        if args.head_grad_clip is not None else "legacy_combined"
                    ),
                },
                "last_eval": metrics["eval"],
                "last_router": train_metrics,
                "last_router_by_eval_group": router_eval,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
            }
            atomic_json(state_path, state)
            emit(
                "latent_top2_broad_curriculum_cycle",
                cycle=cycle,
                checkpoint=str(checkpoint),
                metrics=metrics,
            )
            completed += 1


if __name__ == "__main__":
    main()
