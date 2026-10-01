#!/usr/bin/env python3
"""Continual latent-router training over three frozen causal backbones.

This experiment keeps all three causal language models frozen and also freezes
the entire inherited three-backbone NanoJev head.  Only a task-agnostic router
and its residual expert bank are optimized.  The router receives candidate
features only; task/objective names are never model inputs.

The inherited consensus-heavy curriculum and evaluation surfaces are retained
so this experiment answers one narrow question: can a learned modular head
accumulate useful structure without rewriting the mature head underneath it?
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
from typing import Any, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore
from nanojev_objective_api import ObjectiveRegistry, ObjectQuestion, validate_questions

TOOLS = Path(__file__).resolve().parent
SCHEMA = "main-computer-nanojev-three-backbone-latent-router-curriculum-v1"
DEFAULT_EXPERIMENT = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_router_curriculum_v1"
)
DEFAULT_CUTOVER = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_router_cutover_v1"
)


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
        "kind": "latent_router_cutover",
        "checkpoint": str(cutover_dir),
        "rng": str((cutover_dir / "rng_state.pt").resolve(strict=True)),
        "source_experiment": str(Path(str(cutover["source_experiment"])).expanduser().resolve(strict=True)),
        "source_checkpoint": str(Path(str(cutover["source_checkpoint"])).expanduser().resolve(strict=True)),
        "source_cycle": int(cutover["source_cycle"]),
        "source_global_step": int(cutover["source_global_step"]),
        "source_database": str(Path(str(database)).expanduser().resolve(strict=True)),
        "num_experts": int(cutover["num_experts"]),
        "expert_rank": int(cutover["expert_rank"]),
        "selection": {
            "selection": "latent_router_cutover_source_checkpoint",
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

    # Hard freeze first, then opt in only the latent modular parameters.
    for _name, parameter in model.named_parameters():
        parameter.requires_grad_(False)
    modular_named = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if name.startswith(direct.MODULAR_PREFIXES)
    ]
    if not modular_named:
        raise RuntimeError("latent-router model exposes no modular parameters")
    for _name, parameter in modular_named:
        parameter.requires_grad_(True)

    unexpected_trainable = [
        name for name, parameter in model.named_parameters()
        if parameter.requires_grad and not name.startswith(direct.MODULAR_PREFIXES)
    ]
    if unexpected_trainable:
        raise RuntimeError(f"unexpected trainable parameters: {unexpected_trainable}")

    # Explicitly prove all three language-model parameter sets are frozen.
    qwen_frozen = all(not p.requires_grad for p in model.backbone.parameters())
    pythia_frozen = all(not p.requires_grad for p in model.pythia_backbone.parameters())
    tinystories_frozen = all(not p.requires_grad for p in model.tinystories_backbone.parameters())
    if not (qwen_frozen and pythia_frozen and tinystories_frozen):
        raise RuntimeError("one or more of the three backbone models is trainable")

    model.cuda()
    model.eval()
    modular_params = [parameter for _name, parameter in modular_named]
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
        "legacy_meta": legacy_meta,
        "backbone_frozen": {
            "qwen": qwen_frozen,
            "pythia": pythia_frozen,
            "tinystories": tinystories_frozen,
        },
    }


def train_modular_cached_questions(*, direct, model, optimizer, modular_params,
                                   questions: Sequence, steps: int,
                                   batch_questions: int, rng: random.Random,
                                   balance_weight: float, entropy_weight: float,
                                   grad_clip: float) -> dict[str, Any]:
    import torch
    import torch.nn.functional as F

    if batch_questions <= 0:
        raise RuntimeError("--batch-questions must be positive")
    if len(questions) < batch_questions:
        raise RuntimeError("cached training population is smaller than one batch")
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
    entropies = []
    grad_norms = []
    usage_accum = None

    # Frozen inherited modules remain in eval mode.  Only router/experts are put
    # in training mode; no task identity is exposed to either module.
    model.eval()
    model.latent_router.train()
    model.latent_experts.train()

    for _ in range(steps):
        batch = draw_batch()
        optimizer.zero_grad(set_to_none=True)
        logits, _ = model.score_cached_questions(batch)
        targets = torch.tensor(
            [int(q.gold_index) for q in batch],
            dtype=torch.long,
            device=logits.device,
        )
        ce = F.cross_entropy(logits, targets)
        balance, entropy, usage = model.router_regularization()
        loss = ce + float(balance_weight) * balance + float(entropy_weight) * entropy
        if not torch.isfinite(loss):
            raise RuntimeError("non-finite latent-router training loss")
        loss.backward()
        if float(grad_clip) > 0:
            grad_norm = torch.nn.utils.clip_grad_norm_(modular_params, max_norm=float(grad_clip))
            grad_norm_value = float(grad_norm.detach().item())
        else:
            grad_norm_value = float(direct.global_grad_norm(modular_params))
        if not math.isfinite(grad_norm_value):
            raise RuntimeError("non-finite latent-router gradient norm")
        optimizer.step()

        losses.append(float(loss.detach().item()))
        ce_losses.append(float(ce.detach().item()))
        balance_losses.append(float(balance.detach().item()))
        entropies.append(float(entropy.detach().item()))
        grad_norms.append(grad_norm_value)
        current_usage = usage.detach().float().cpu()
        usage_accum = current_usage if usage_accum is None else usage_accum + current_usage

    mean_usage = (usage_accum / steps).tolist()
    return {
        "steps": int(steps),
        "mean_loss": sum(losses) / len(losses),
        "last_loss": losses[-1],
        "mean_cross_entropy": sum(ce_losses) / len(ce_losses),
        "mean_router_balance_loss": sum(balance_losses) / len(balance_losses),
        "mean_router_entropy": sum(entropies) / len(entropies),
        "mean_expert_usage": mean_usage,
        "max_mean_expert_usage": max(mean_usage),
        "min_mean_expert_usage": min(mean_usage),
        "grad_norm_preclip": direct.summarize_grad_norms(grad_norms),
        "grad_clip": float(grad_clip),
        "balance_weight": float(balance_weight),
        "entropy_weight": float(entropy_weight),
    }


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
        TOOLS / "nanojev_three_backbone_latent_router_cutover.py",
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
    parser.add_argument("--cutover-dir", default=DEFAULT_CUTOVER)
    parser.add_argument("--train-questions-per-cycle", type=int, default=curriculum.DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--consensus-percent", type=float, default=curriculum.DEFAULT_CONSENSUS_PERCENT)
    parser.add_argument("--relative-correct-percent", type=float, default=curriculum.DEFAULT_RELATIVE_CORRECT_PERCENT)
    parser.add_argument("--relative-wrong-percent", type=float, default=curriculum.DEFAULT_RELATIVE_WRONG_PERCENT)
    parser.add_argument("--train-files-per-cycle", type=int, default=curriculum.DEFAULT_TRAIN_FILES_PER_CYCLE)
    parser.add_argument("--triad-max-code-tokens", type=int, default=curriculum.DEFAULT_TRIAD_MAX_CODE_TOKENS)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=curriculum.DEFAULT_CONSENSUS_MAX_CODE_TOKENS)
    parser.add_argument("--relative-max-prompt-tokens", type=int, default=curriculum.DEFAULT_RELATIVE_MAX_PROMPT_TOKENS)
    parser.add_argument("--steps-per-cycle", type=int, default=32)
    parser.add_argument("--batch-questions", type=int, default=10)
    parser.add_argument("--eval-batch-questions", type=int, default=32)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=4)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--router-lr", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--router-balance-weight", type=float, default=0.01)
    parser.add_argument("--router-entropy-weight", type=float, default=0.001)
    parser.add_argument("--grad-clip", type=float, default=100.0)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--old-probe-every", type=int, default=10)
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
        "consensus_max_code_tokens", "relative_max_prompt_tokens", "steps_per_cycle",
        "batch_questions", "eval_batch_questions", "cache_qwen_batch_questions",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.old_probe_every < 0 or args.max_cycles < 0:
        parser.error("--old-probe-every and --max-cycles must be nonnegative")
    if args.router_lr <= 0 or args.weight_decay < 0:
        parser.error("router LR must be positive and weight decay nonnegative")
    if args.router_balance_weight < 0 or args.router_entropy_weight < 0 or args.grad_clip < 0:
        parser.error("router regularization and gradient clipping values must be nonnegative")
    if args.conditioned_analysis_folds < 2:
        parser.error("--conditioned-analysis-folds must be at least 2")

    try:
        plan = curriculum.training_plan(
            args.train_questions_per_cycle,
            consensus_percent=args.consensus_percent,
            relative_correct_percent=args.relative_correct_percent,
            relative_wrong_percent=args.relative_wrong_percent,
        )
        pair_source_plan = curriculum.relative_source_plan(plan)
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
            raise RuntimeError(f"existing latent-router experiment has wrong schema: {manifest_path}")
        if dict(manifest.get("source") or {}) != source:
            raise RuntimeError("cannot resume latent-router experiment from a different cutover")
        if {k: int(v) for k, v in manifest["training_plan"].items()} != plan:
            raise RuntimeError("cannot resume with a different training mix")
        if not db_path.is_file():
            raise RuntimeError(f"latent-router lexical DB is missing: {db_path}")
    else:
        clone_sqlite(Path(source["source_database"]), db_path)
        manifest = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "source": source,
            "database": str(db_path.resolve()),
            "training_plan": plan,
            "relative_source_plan": pair_source_plan,
            "relative_gap_thresholds": list(gap_thresholds),
            "candidate_feature_width": direct.TOTAL_FEATURE_WIDTH,
            "frozen_backbones": list(direct.THREE_FROZEN_MODELS),
            "frozen_inherited_head": True,
            "router_receives_task_identity": False,
            "num_experts": int(source["num_experts"]),
            "expert_rank": int(source["expert_rank"]),
            "trainable_prefixes": list(direct.MODULAR_PREFIXES),
            "training_contract": (
                "existing consensus-heavy population is cached from all three frozen backbones; "
                "only latent_router + latent_experts receive optimizer updates"
            ),
        }
        atomic_json(manifest_path, manifest)
        emit("latent_router_curriculum_source_selected", **source["selection"], checkpoint=source["checkpoint"])

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
            optimizer.load_state_dict(
                torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False)
            )
            for group in optimizer.param_groups:
                group["lr"] = float(args.router_lr)
                group["weight_decay"] = float(args.weight_decay)
            direct.move_optimizer_state_to_cuda(optimizer)
            direct.load_rng(checkpoint / "rng_state.pt")
        cycle = int(state.get("cycle", source["source_cycle"]))
        global_step = int(state.get("global_step", source["source_global_step"]))
    else:
        cycle = int(source["source_cycle"])
        global_step = int(source["source_global_step"])
        atomic_json(state_path, {
            "cycle": cycle,
            "global_step": global_step,
            "latest_checkpoint": None,
            "cutover_checkpoint": source["checkpoint"],
            "inherited_head_sha256": inherited_sha256,
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
        registry = ObjectiveRegistry([
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

        args.skip_old_probes = False
        old_cached = smoke.old_probe_cache(
            direct=direct, model=model, tokenizer=tokenizer,
            source=source, tools_dir=TOOLS, args=args,
        )

        cutover_eval_path = experiment_dir / "eval_cutover.json"
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
            cutover_eval = {
                "preservation": preservation,
                curriculum.TRIAD_TASK: triad_eval,
                curriculum.CONSENSUS_TASK: consensus_eval,
                "relative_candidate": relative_eval,
            }
            atomic_json(cutover_eval_path, cutover_eval)
            emit(
                "latent_router_curriculum_eval_cutover",
                metrics=cutover_eval,
                preservation_cache=preservation_cache_stats,
                triad_cache=triad_cache_stats,
                consensus_cache=consensus_cache_stats,
                relative_eval_cache=relative_eval_cache_stats,
            )

        emit(
            "latent_router_curriculum_ready",
            source_checkpoint=source["source_checkpoint"],
            source_cycle=source["source_cycle"],
            cycle=cycle,
            global_step=global_step,
            models_frozen=list(direct.THREE_FROZEN_MODELS),
            backbone_frozen=loaded["backbone_frozen"],
            inherited_head_frozen=True,
            inherited_head_sha256=inherited_sha256,
            trainable_modular_params=sum(p.numel() for p in modular_params),
            trainable_prefixes=list(direct.MODULAR_PREFIXES),
            router_receives_task_identity=False,
            num_experts=source["num_experts"],
            expert_rank=source["expert_rank"],
            registered_objectives=registry.names(),
            training_plan=plan,
            relative_source_plan=pair_source_plan,
            continuous=args.max_cycles == 0,
        )

        completed = 0
        while args.max_cycles == 0 or completed < args.max_cycles:
            cycle += 1
            train_rng = random.Random(curriculum.stable_seed(args.seed, cycle, "latent-router-train"))
            primary_plan = curriculum.primary_training_plan(plan)
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
                raise RuntimeError("inherited head changed before optimizer step")
            train_metrics = train_modular_cached_questions(
                direct=direct,
                model=model,
                optimizer=optimizer,
                modular_params=modular_params,
                questions=training_cached,
                steps=args.steps_per_cycle,
                batch_questions=args.batch_questions,
                rng=train_rng,
                balance_weight=args.router_balance_weight,
                entropy_weight=args.router_entropy_weight,
                grad_clip=args.grad_clip,
            )
            after_sha = direct.inherited_head_sha256(model)
            if after_sha != inherited_sha256:
                raise RuntimeError(
                    "FATAL: inherited NanoJev head changed during latent-router training"
                )
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
                    "inherited_head_unchanged": True,
                    "models_frozen": list(direct.THREE_FROZEN_MODELS),
                    "router_receives_task_identity": False,
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
                source=source,
                plan=plan,
                inherited_sha256=inherited_sha256,
            )
            state = {
                "cycle": cycle,
                "global_step": global_step,
                "latest_checkpoint": str(checkpoint.resolve()),
                "cutover_checkpoint": source["checkpoint"],
                "inherited_head_sha256": inherited_sha256,
                "training_plan": plan,
                "relative_source_plan": pair_source_plan,
                "last_eval": metrics["eval"],
                "last_router": train_metrics,
                "database": store.stats(),
                "reserve": store.reserve_counts(),
            }
            atomic_json(state_path, state)
            emit(
                "latent_router_curriculum_cycle",
                cycle=cycle,
                checkpoint=str(checkpoint),
                metrics=metrics,
            )
            completed += 1


if __name__ == "__main__":
    main()
