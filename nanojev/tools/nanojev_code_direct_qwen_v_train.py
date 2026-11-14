#!/usr/bin/env python3
"""Train one prepended virtual vector V against a frozen Qwen + frozen NanoJev head.

Architecture: (V, x) -> frozen Qwen -> frozen mature NanoJev attention head.
V starts exactly from the literal-space token embedding.  No S, R, recurrence,
second Qwen pass, evidence adapter, head training, or gradient clipping is used.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE_SCRIPT = "nanojev_code_direct_qwen_cached_batch_no_clip_reuse_limit_train.py"
DEFAULT_CUTOVER_DIR = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_v_cutover_v1"
DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_direct_qwen_v_only_v1"
SCHEMA = "nanojev-direct-qwen-v-only-experiment-v1"
CONFIG_SCHEMA = "nanojev-direct-qwen-v-only-config-v1"
STATE_SCHEMA = "nanojev-direct-qwen-v-only-state-v1"
CHECKPOINT_SCHEMA = "nanojev-direct-qwen-v-only-checkpoint-v1"


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def load_base():
    import importlib.util
    path = Path(__file__).resolve().parent / BASE_SCRIPT
    if not path.is_file():
        raise RuntimeError(f"required base trainer missing: {path}")
    spec = importlib.util.spec_from_file_location("nanojev_v_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def build_v_model_class(DirectModel):
    import torch
    import torch.nn as nn

    class VPrefixDecisionModel(DirectModel):
        def __init__(self, backbone, set_head):
            super().__init__(backbone, set_head)
            hidden = int(getattr(backbone.config, "hidden_size"))
            self.v = nn.Parameter(torch.zeros(hidden, dtype=torch.float32))
            self.v_space_token_id: int | None = None

        def initialize_v_from_literal_space(self, tokenizer) -> int:
            ids = list(tokenizer.encode(" ", add_special_tokens=False))
            if len(ids) != 1:
                raise RuntimeError(f"literal space must tokenize to exactly one token, got {ids}")
            token_id = int(ids[0])
            with torch.no_grad():
                source = self.backbone.get_input_embeddings().weight[token_id].detach().float()
                if source.shape != self.v.shape:
                    raise RuntimeError(f"space embedding shape mismatch: {tuple(source.shape)} != {tuple(self.v.shape)}")
                self.v.copy_(source)
            self.v_space_token_id = token_id
            return token_id

        def _qwen_pass(self, encoded):
            width = int(encoded["tokens"].shape[1]) + 1
            max_positions = int(getattr(self.backbone.config, "max_position_embeddings", width))
            if width > max_positions:
                raise RuntimeError(f"V-prefixed object-stream path exceeds Qwen max positions: {width} > {max_positions}")
            # Qwen parameters are frozen.  Detach ordinary token embeddings, but keep
            # autograd through the prepended V and through the frozen transformer ops.
            with torch.no_grad():
                token_embeds = self.backbone.get_input_embeddings()(encoded["tokens"]).detach()
            v = self.v.to(dtype=token_embeds.dtype).view(1, 1, -1).expand(token_embeds.shape[0], 1, -1)
            inputs = torch.cat([v, token_embeds], dim=1)
            prefix_attention = torch.ones(
                (encoded["attention"].shape[0], 1), dtype=encoded["attention"].dtype,
                device=encoded["attention"].device,
            )
            attention = torch.cat([prefix_attention, encoded["attention"]], dim=1)
            hidden = self.backbone(inputs_embeds=inputs, attention_mask=attention, use_cache=False).last_hidden_state
            # Remove V's own output position so the inherited terminal-answer indexing
            # remains exactly the direct trainer's indexing over x.
            return hidden[:, 1:, :]

    VPrefixDecisionModel.__name__ = "DirectQwenVPrefixFrozenHeadDecisionModel"
    return VPrefixDecisionModel


def v_state(model) -> dict[str, Any]:
    return {"v": model.v.detach().cpu().contiguous()}


def save_generation(*, exp: Path, model, optimizer, cycle: int, global_step: int,
                    experiment: dict[str, Any], config: dict[str, Any], metrics: dict[str, Any]) -> Path:
    import torch
    from safetensors.torch import save_file
    root = exp / "checkpoints" / "generations"
    final = root / f"cycle-{cycle:06d}"
    temp = root / f".cycle-{cycle:06d}.tmp"
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint generation already exists: {final}")
    temp.mkdir(parents=True)
    save_file(v_state(model), temp / "v.safetensors")
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    atomic_json(temp / "config.json", {
        "schema_version": CHECKPOINT_SCHEMA,
        "cycle": cycle,
        "global_step": global_step,
        "architecture": experiment["architecture"],
        "cutover_dir": experiment["cutover_dir"],
        "cutover_head_sha256": experiment["cutover_head_sha256"],
        "v_initialization": "literal_space_embedding",
        "v_position": "prefix",
        "qwen_frozen": True,
        "head_frozen": True,
        "only_v_trainable": True,
        "gradient_clipping": False,
        "max_prompt_tokens": config["max_prompt_tokens"],
        "max_answer_tokens": config["max_answer_tokens"],
    })
    atomic_json(temp / "meta.json", {"cycle": cycle, "global_step": global_step, "metrics": metrics})
    os.replace(temp, final)
    return final


def load_generation(model, checkpoint: Path) -> None:
    from safetensors.torch import load_file
    state = load_file(str(checkpoint / "v.safetensors"), device="cpu")
    if set(state) != {"v"} or tuple(state["v"].shape) != tuple(model.v.shape):
        raise RuntimeError(f"V checkpoint mismatch at {checkpoint}")
    with __import__("torch").no_grad():
        model.v.copy_(state["v"].to(dtype=model.v.dtype))


def self_test() -> None:
    import torch
    import torch.nn as nn
    from types import SimpleNamespace

    class TinyBackbone(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(8, 4)
            self.proj = nn.Linear(4, 4, bias=False)
            self.config = SimpleNamespace(hidden_size=4, max_position_embeddings=32)
        def get_input_embeddings(self):
            return self.emb
        def forward(self, *, inputs_embeds, attention_mask, use_cache=False):
            return SimpleNamespace(last_hidden_state=self.proj(inputs_embeds))

    b = TinyBackbone()
    for p in b.parameters():
        p.requires_grad_(False)
    v = nn.Parameter(torch.ones(4))
    tokens = torch.tensor([[1, 2]])
    attention = torch.ones_like(tokens, dtype=torch.bool)
    with torch.no_grad():
        e = b.get_input_embeddings()(tokens).detach()
    inputs = torch.cat([v.view(1, 1, -1), e], dim=1)
    out = b(inputs_embeds=inputs, attention_mask=torch.ones((1, 3), dtype=torch.bool)).last_hidden_state
    out.sum().backward()
    assert v.grad is not None and float(v.grad.abs().sum()) > 0
    assert all(p.grad is None for p in b.parameters())
    emit("self_test_ok")


def main() -> None:
    base = load_base()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutover-dir", default=DEFAULT_CUTOVER_DIR)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--probe-experiment-dir", default=base.DEFAULT_PROBE_EXPERIMENT)
    parser.add_argument("--dictionary-cache", default=base.DEFAULT_DICTIONARY_CACHE)
    parser.add_argument("--dictionary-url", default=base.DEFAULT_DICTIONARY_URL)
    parser.add_argument("--dictionary-holdout-pairs", type=int, default=32)
    parser.add_argument("--dictionary-holdout-seed", type=int, default=20260927)
    parser.add_argument("--cycles-this-run", type=int, default=100)
    parser.add_argument("--cycle-seconds", type=float, default=75.0)
    parser.add_argument("--train-files-per-cycle", type=int, default=40)
    parser.add_argument("--train-units-per-cycle", type=int, default=120)
    parser.add_argument("--train-questions-per-task", type=int, default=16)
    parser.add_argument("--batch-questions-per-task", type=int, default=2)
    parser.add_argument("--epochs-per-cache", type=int, default=1)
    parser.add_argument("--eval-batch-questions", type=int, default=8)
    parser.add_argument("--eval-every", type=int, default=5)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--max-answer-tokens", type=int, default=128)
    parser.add_argument("--mutation-max-code-tokens", type=int, default=128)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=128)
    parser.add_argument("--v-lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--keep-generations", type=int, default=2)
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--seed-offset", type=int, default=94000)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    positive = (
        args.cycles_this_run, args.train_files_per_cycle, args.train_units_per_cycle,
        args.train_questions_per_task, args.batch_questions_per_task, args.epochs_per_cache,
        args.eval_batch_questions, args.eval_every, args.max_prompt_tokens, args.max_answer_tokens,
        args.mutation_max_code_tokens, args.consensus_max_code_tokens, args.keep_generations,
    )
    if min(positive) <= 0 or args.cycle_seconds <= 0 or args.v_lr <= 0 or args.weight_decay < 0:
        parser.error("cycle/data/optimizer settings must be positive (weight decay may be zero)")
    if args.train_questions_per_task % args.batch_questions_per_task != 0:
        parser.error("--train-questions-per-task must be divisible by --batch-questions-per-task")
    if args.mutation_max_code_tokens > args.max_answer_tokens or args.consensus_max_code_tokens > args.max_answer_tokens:
        parser.error("code-token generator caps must be <= --max-answer-tokens")

    tools_dir = Path(__file__).resolve().parent
    legacy = base.load_local_module("nanojev_code_train_for_v", tools_dir / "nanojev_code_train.py")
    data = base.load_local_module("nanojev_code_lexeme_data_for_v", tools_dir / "nanojev_code_lexeme_data.py")
    mutation = base.load_local_module("nanojev_code_mutation_for_v", tools_dir / "nanojev_code_mutation_train.py")
    source = base.load_local_module(
        "nanojev_full_head_dictionary_source_for_v",
        tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    native = base.load_local_module("nanojev_native_helpers_for_v", tools_dir / "nanojev_code_s_r_native_lm_train.py")
    ordered_api = base.load_local_module("nanojev_ordered_signal_for_v", tools_dir / "nanojev_frozen_qwen_ordered_signal_smoke.py")
    dictionary = base.load_local_module("nanojev_dictionary_for_v", tools_dir / "nanojev_dictionary_smoke.py")

    cutover_dir = Path(args.cutover_dir).expanduser().resolve(strict=True)
    cutover = read_json(cutover_dir / "cutover.json")
    if cutover.get("schema_version") != "nanojev-direct-qwen-v-cutover-v1":
        raise RuntimeError(f"unsupported cutover manifest: {cutover_dir / 'cutover.json'}")
    if not (cutover_dir / "head.safetensors").is_file():
        raise RuntimeError(f"cutover head missing: {cutover_dir / 'head.safetensors'}")

    probe_exp = Path(args.probe_experiment_dir).expanduser().resolve(strict=True)
    probe_meta = base.read_json(probe_exp / "experiment.json")
    legacy_exp = Path(probe_meta["legacy_experiment"]).expanduser().resolve(strict=True)
    legacy_meta = base.read_json(legacy_exp / "experiment.json")
    repo_root = Path(legacy_meta["repo_root"]).expanduser().resolve(strict=True)
    train_manifest = base.read_json(Path(legacy_meta["manifests"]["train"]).expanduser().resolve(strict=True))
    model_name = str(probe_meta["model"])
    revision = str(probe_meta["resolved_model_revision"])
    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    if cutover.get("model") and str(cutover["model"]) != model_name:
        raise RuntimeError(f"cutover model mismatch: {cutover['model']} != {model_name}")
    if cutover.get("resolved_model_revision") and str(cutover["resolved_model_revision"]) != revision:
        raise RuntimeError("cutover Qwen revision mismatch")

    seed = int(legacy_meta["seed"]) + int(args.seed_offset)
    steps_per_epoch = args.train_questions_per_task // args.batch_questions_per_task
    effective_batch_questions = args.batch_questions_per_task * len(base.TASK_ORDER)
    steps_per_cycle = steps_per_epoch * args.epochs_per_cache
    weights = {task: base.EQUAL_TASK_WEIGHT for task in base.TASK_ORDER}

    exp = Path(args.experiment_dir).expanduser()
    existing = (exp / "experiment.json").is_file()
    if existing:
        experiment = read_json(exp / "experiment.json")
        config = read_json(exp / "training_config.json")
        state = read_json(exp / "training_state.json")
        if experiment.get("schema_version") != SCHEMA or config.get("schema_version") != CONFIG_SCHEMA:
            raise RuntimeError(f"existing directory is not a V-only experiment: {exp}")
        if Path(experiment["cutover_dir"]).resolve() != cutover_dir.resolve():
            raise RuntimeError("resume cutover mismatch")
        for key, requested in (
            ("train_questions_per_task", args.train_questions_per_task),
            ("batch_questions_per_task", args.batch_questions_per_task),
            ("max_prompt_tokens", args.max_prompt_tokens),
            ("max_answer_tokens", args.max_answer_tokens),
            ("epochs_per_cache", args.epochs_per_cache),
        ):
            if int(config[key]) != int(requested):
                raise RuntimeError(f"resume {key} mismatch: {config[key]} != {requested}")
    else:
        exp.mkdir(parents=True, exist_ok=True)
        for rel in ("shards", "checkpoints/generations"):
            (exp / rel).mkdir(parents=True, exist_ok=True)
        experiment = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "experiment_dir": str(exp.resolve()),
            "cutover_dir": str(cutover_dir),
            "cutover_source_checkpoint": cutover["source_checkpoint"],
            "cutover_source_cycle": cutover.get("source_cycle"),
            "cutover_source_reuse_epoch": cutover.get("source_reuse_epoch"),
            "cutover_head_sha256": cutover["source_head_sha256"],
            "probe_experiment": str(probe_exp),
            "legacy_experiment": str(legacy_exp),
            "repo_root": str(repo_root),
            "model": model_name,
            "resolved_model_revision": revision,
            "seed": seed,
            "architecture": "v_prefix_frozen_qwen_frozen_mature_nanojev_head",
        }
        config = {
            "schema_version": CONFIG_SCHEMA,
            "curriculum": weights,
            "steps_per_epoch": steps_per_epoch,
            "steps_per_cycle": steps_per_cycle,
            "epochs_per_cache": args.epochs_per_cache,
            "effective_batch_questions": effective_batch_questions,
            "train_questions_per_task": args.train_questions_per_task,
            "batch_questions_per_task": args.batch_questions_per_task,
            "max_prompt_tokens": args.max_prompt_tokens,
            "max_answer_tokens": args.max_answer_tokens,
            "path_pooling": "terminal_answer_hidden_state",
            "qwen_passes": 1,
            "v_position": "prefix",
            "v_initialization": "literal_space_embedding",
            "v_lr": args.v_lr,
            "weight_decay": args.weight_decay,
            "gradient_clipping": False,
            "precision": args.precision,
            "qwen_frozen": True,
            "head_frozen": True,
            "only_v_trainable": True,
            "s_present": False,
            "r_controller_present": False,
            "recurrence_present": False,
            "learned_evidence_interface_present": False,
        }
        state = {
            "schema_version": STATE_SCHEMA,
            "cycle": 0,
            "global_step": 0,
            "global_question_presentations": 0,
            "latest_generation": None,
            "source_cursors": {task: 0 for task in base.TASK_ORDER},
        }
        atomic_json(exp / "experiment.json", experiment)
        atomic_json(exp / "training_config.json", config)
        atomic_json(exp / "training_state.json", state)

    if args.dry_run:
        emit(
            "dry_run_ok",
            experiment=str(exp), cutover_dir=str(cutover_dir),
            source_checkpoint=cutover["source_checkpoint"],
            source_cycle=cutover.get("source_cycle"), source_reuse_epoch=cutover.get("source_reuse_epoch"),
            architecture="(V,x)->frozen_qwen->frozen_nanojev_head",
            only_v_trainable=True, v_initialization="literal_space_embedding",
            v_position="prefix", steps_per_cycle=steps_per_cycle,
            effective_batch_questions=effective_batch_questions,
        )
        return

    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer

    if args.disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported")
    torch.backends.cuda.matmul.allow_tf32 = False

    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    if tokenizer.pad_token_id is None:
        raise RuntimeError("tokenizer has no pad/eos token")

    probes: dict[str, list[Any]] = {}
    for task in base.TASK_ORDER:
        path = probe_exp / "probes" / base.PROBE_FILES[task]
        raw = base.read_jsonl(path)
        qs = base.objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
        qs, stats = base.filter_bounded_questions(
            qs, tokenizer, max_prompt_tokens=args.max_prompt_tokens, max_answer_tokens=args.max_answer_tokens
        )
        if not qs:
            raise RuntimeError(f"all {task} held-out questions filtered")
        probes[task] = qs
        emit("object_probe_ready", task=task, questions=len(qs), filter_stats=stats)

    emit("frozen_backbone_load_start", model=model_name, revision=revision)
    backbone = AutoModel.from_pretrained(
        model_name, revision=revision, dtype=torch.float32, attn_implementation="sdpa",
        trust_remote_code=False, local_files_only=args.local_files_only,
    )
    _pipeline, BaseDecisionModel = legacy.import_nanojev(Path(legacy_meta["nanojev_root"]).resolve(strict=True))
    DirectModel = base.build_direct_model_class(BaseDecisionModel, max_answer_tokens=args.max_answer_tokens)
    Model = build_v_model_class(DirectModel)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed + 11)
        model = Model(backbone, "attention")
    model.backbone.config.use_cache = False

    # Freeze every inherited parameter first, then enable only V.
    for p in model.parameters():
        p.requires_grad_(False)
    model.v.requires_grad_(True)
    head_load = base.load_inherited_full_head_only(model, cutover_dir)
    resume = Path(state["latest_generation"]).resolve(strict=True) if state.get("latest_generation") else None
    if resume:
        load_generation(model, resume)
    else:
        space_token_id = model.initialize_v_from_literal_space(tokenizer)

    trainable = [(name, p) for name, p in model.named_parameters() if p.requires_grad]
    if [name for name, _p in trainable] != ["v"]:
        raise RuntimeError(f"V-only contract violated; trainable parameters: {[name for name, _ in trainable]}")
    if any(p.requires_grad for name, p in model.named_parameters() if name != "v"):
        raise RuntimeError("non-V parameter unexpectedly trainable")

    model.cuda()
    optimizer = torch.optim.AdamW([{"params": [model.v], "lr": args.v_lr}], weight_decay=args.weight_decay)
    if resume:
        optimizer.load_state_dict(torch.load(resume / "optimizer.pt", map_location="cpu", weights_only=False))
        base.move_optimizer_state_to_cuda(optimizer)
    else:
        random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    model.eval()
    model.backbone.eval()

    with torch.no_grad():
        v_rms = float(model.v.detach().float().pow(2).mean().sqrt().item())
        space_ids = list(tokenizer.encode(" ", add_special_tokens=False))
        space_id = int(space_ids[0]) if len(space_ids) == 1 else None
        space = model.backbone.get_input_embeddings().weight[space_id].detach().float() if space_id is not None else None
        v_space_cosine = float(F.cosine_similarity(model.v.detach().float(), space, dim=0).item()) if space is not None else None
    emit(
        "trainer_ready",
        gpu=torch.cuda.get_device_name(0), cutover_dir=str(cutover_dir),
        source_checkpoint=cutover["source_checkpoint"], source_cycle=cutover.get("source_cycle"),
        source_reuse_epoch=cutover.get("source_reuse_epoch"), head_sha256=cutover["source_head_sha256"],
        inherited_head=head_load, qwen_frozen=True, head_frozen=True,
        only_v_trainable=True, v_trainable_params=model.v.numel(),
        v_position="prefix", v_space_token_id=space_id, v_rms=v_rms, v_space_cosine=v_space_cosine,
        qwen_passes=1, recurrence_present=False, s_present=False, r_controller_present=False,
        learned_evidence_interface_present=False, gradient_clipping=False,
        steps_per_epoch=steps_per_epoch, epochs_per_cache=args.epochs_per_cache,
        steps_per_cycle=steps_per_cycle, effective_batch_questions=effective_batch_questions,
    )

    # Baseline is the frozen cutover head with V exactly at E[' '] before any V update.
    baseline_path = exp / "baseline.json"
    if not baseline_path.is_file():
        baseline = {
            task: base.evaluate(
                model=model, tokenizer=tokenizer, questions=probes[task],
                max_prompt_tokens=args.max_prompt_tokens, pad_token_id=int(tokenizer.pad_token_id),
                precision=args.precision, batch_questions=args.eval_batch_questions,
            )
            for task in base.TASK_ORDER
        }
        atomic_json(baseline_path, baseline)
        emit("v_space_baseline", metrics=baseline, overall=base.aggregate_dev_metrics(baseline))

    _dictionary_dev_pairs, dictionary_training_synsets = native.build_dictionary_split(
        dictionary, Path(args.dictionary_cache), args.dictionary_url,
        args.dictionary_holdout_pairs, args.dictionary_holdout_seed,
    )
    unit_budgets = base.largest_remainder_budgets(args.train_units_per_cycle, weights)

    for _ in range(args.cycles_this_run):
        cycle = int(state["cycle"]) + 1
        shard_seed = seed + cycle * 10007
        cursors = dict(state.get("source_cursors") or {})

        def source_rows(task: str, *, python_only: bool) -> list[dict]:
            start = int(cursors.get(task, 0))
            if python_only:
                rows, next_cursor = mutation.cyclic_filtered_slice(
                    train_manifest, start, args.train_files_per_cycle,
                    lambda row: row.get("language") == "python",
                )
                cursors[task] = next_cursor
                return rows
            rows = legacy.cyclic_slice(train_manifest, start % len(train_manifest), args.train_files_per_cycle)
            cursors[task] = (start + len(rows)) % len(train_manifest)
            return rows

        raw_by_task: dict[str, list[dict]] = {}
        docs = data.load_docs(source_rows("legacy", python_only=False), repo_root)
        raw_by_task["legacy"] = data.sample_ordered_continuation_paired_records(
            docs=docs, tokenizer=tokenizer, split="train", pair_count=max(unit_budgets["legacy"], 1),
            max_prefix_tokens=int(legacy_meta["max_prefix_tokens"]), seed=shard_seed,
            max_lexeme_tokens=int(legacy_meta["max_lexeme_tokens"]), min_symbols=3, max_symbols=5,
        )
        docs = data.load_docs(source_rows("mutation", python_only=True), repo_root)
        raw_by_task["mutation"] = mutation.sample_mutation_records(
            docs=docs, data=data, tokenizer=tokenizer, split="train", pair_count=max(unit_budgets["mutation"], 1),
            max_code_tokens=args.mutation_max_code_tokens, max_length=int(legacy_meta["max_length"]), seed=shard_seed + 1,
        )
        docs = data.load_docs(source_rows("ast", python_only=True), repo_root)
        raw_by_task["ast"] = source.sample_ast_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
            pair_count=max(unit_budgets["ast"], 1), max_code_tokens=args.mutation_max_code_tokens,
            max_length=int(legacy_meta["max_length"]), seed=shard_seed + 2,
        )
        docs = data.load_docs(source_rows("consensus", python_only=True), repo_root)
        raw_by_task["consensus"] = source.sample_consensus_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
            record_count=max(unit_budgets["consensus"], 4), max_code_tokens=args.consensus_max_code_tokens,
            max_length=int(legacy_meta["max_length"]), seed=shard_seed + 3,
        )
        docs = data.load_docs(source_rows("triad", python_only=True), repo_root)
        raw_by_task["triad"] = source.sample_relation_balanced_pairwise_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
            unit_count=max(unit_budgets["triad"], 1), max_code_tokens=args.consensus_max_code_tokens,
            max_length=int(legacy_meta["max_length"]), seed=shard_seed + 4,
        )
        pairs = dictionary.build_pairs(dictionary_training_synsets, max(unit_budgets["dictionary"], 1), shard_seed + 5)
        raw_by_task["dictionary"] = native.dictionary_records(
            dictionary, pairs, split="train", prefix=f"v-only-c{cycle:06d}"
        )

        questions_by_task: dict[str, list[Any]] = {}
        filter_report: dict[str, Any] = {}
        for task in base.TASK_ORDER:
            raw = raw_by_task[task]
            base.write_jsonl(exp / "shards" / f"cycle-{cycle:06d}-{task}.jsonl", raw)
            qs = base.objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
            qs, stats = base.filter_bounded_questions(
                qs, tokenizer, max_prompt_tokens=args.max_prompt_tokens, max_answer_tokens=args.max_answer_tokens
            )
            if not qs:
                raise RuntimeError(f"task {task} produced no bounded questions at cycle {cycle}: {stats}")
            questions_by_task[task] = qs
            filter_report[task] = stats

        selection_rng = random.Random(shard_seed + 999)
        selected_by_task: dict[str, list[Any]] = {}
        for task in base.TASK_ORDER:
            pool = base.QuestionPool(questions_by_task[task], random.Random(shard_seed + 100 + base.TASK_ORDER.index(task)))
            selected_by_task[task] = [
                base.shuffle_candidates(pool.draw(), selection_rng)
                for _ in range(args.train_questions_per_task)
            ]

        emit(
            "cycle_start", cycle=cycle, global_step=state["global_step"],
            global_question_presentations=state["global_question_presentations"],
            curriculum=weights, unit_budgets=unit_budgets, bounded_filter=filter_report,
            selected_questions_per_task={task: len(selected_by_task[task]) for task in base.TASK_ORDER},
            selected_unique_questions={task: len({q.question_id for q in selected_by_task[task]}) for task in base.TASK_ORDER},
            optimizer_steps=steps_per_cycle, epochs_per_cache=args.epochs_per_cache,
            effective_batch_questions=effective_batch_questions,
        )

        started = time.perf_counter()
        steps = 0
        losses: list[float] = []
        grad_norms: list[float] = []
        correct_by_task = defaultdict(int)
        count_by_task = defaultdict(int)
        stop_for_time = False

        for epoch_index in range(args.epochs_per_cache):
            epoch_rng = random.Random(shard_seed + 1999 + (epoch_index + 1) * 1009)
            epoch_by_task = {task: list(selected_by_task[task]) for task in base.TASK_ORDER}
            for rows in epoch_by_task.values():
                epoch_rng.shuffle(rows)
            for step_index in range(steps_per_epoch):
                if steps > 0 and time.perf_counter() - started >= args.cycle_seconds:
                    stop_for_time = True
                    break
                batch = []
                lo = step_index * args.batch_questions_per_task
                hi = lo + args.batch_questions_per_task
                for task in base.TASK_ORDER:
                    batch.extend(epoch_by_task[task][lo:hi])
                epoch_rng.shuffle(batch)

                optimizer.zero_grad(set_to_none=True)
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                    logits, _ = model.forward_object_questions(
                        batch, tokenizer, args.max_prompt_tokens, int(tokenizer.pad_token_id)
                    )
                    per_question = []
                    for i, q in enumerate(batch):
                        scores = logits[i, :len(q.candidates)].float()
                        target = torch.tensor([q.gold_index], dtype=torch.long, device=scores.device)
                        per_question.append(F.cross_entropy(scores.unsqueeze(0), target))
                        pred = int(scores.argmax().item())
                        correct_by_task[q.task] += int(pred == q.gold_index)
                        count_by_task[q.task] += 1
                    loss = torch.stack(per_question).mean()
                loss.backward()
                grad_norm = base.global_grad_norm([model.v])
                if not math.isfinite(grad_norm) or not math.isfinite(float(loss.detach().item())):
                    raise RuntimeError(f"non-finite V training state at cycle={cycle} step={steps + 1}")
                optimizer.step()
                steps += 1
                state["global_step"] = int(state["global_step"]) + 1
                state["global_question_presentations"] = int(state["global_question_presentations"]) + len(batch)
                losses.append(float(loss.detach().item()))
                grad_norms.append(grad_norm)
            if stop_for_time:
                break

        with torch.no_grad():
            vf = model.v.detach().float()
            space = model.backbone.get_input_embeddings().weight[space_id].detach().float()
            v_rms = float(vf.pow(2).mean().sqrt().item())
            v_space_cosine = float(F.cosine_similarity(vf, space, dim=0).item())
            v_delta_rms = float((vf - space).pow(2).mean().sqrt().item())

        dev_metrics = None
        if cycle == 1 or cycle % args.eval_every == 0:
            dev_metrics = {
                task: base.evaluate(
                    model=model, tokenizer=tokenizer, questions=probes[task],
                    max_prompt_tokens=args.max_prompt_tokens, pad_token_id=int(tokenizer.pad_token_id),
                    precision=args.precision, batch_questions=args.eval_batch_questions,
                )
                for task in base.TASK_ORDER
            }
            emit(
                "dev_evaluation", cycle=cycle, metrics=dev_metrics,
                overall=base.aggregate_dev_metrics(dev_metrics),
                v_rms=v_rms, v_space_cosine=v_space_cosine, v_delta_rms=v_delta_rms,
            )

        cycle_metrics = {
            "steps": steps,
            "mean_loss": sum(losses) / len(losses) if losses else None,
            "task_accuracy": {
                task: correct_by_task[task] / count_by_task[task]
                for task in base.TASK_ORDER if count_by_task[task]
            },
            "task_questions": {task: count_by_task[task] for task in base.TASK_ORDER if count_by_task[task]},
            "question_presentations": sum(count_by_task.values()),
            "global_question_presentations": int(state["global_question_presentations"]),
            "raw_grad_norm": base.summarize_grad_norms(grad_norms),
            "v_rms": v_rms,
            "v_space_cosine": v_space_cosine,
            "v_delta_rms": v_delta_rms,
            "elapsed_seconds": time.perf_counter() - started,
        }
        generation = save_generation(
            exp=exp, model=model, optimizer=optimizer, cycle=cycle, global_step=int(state["global_step"]),
            experiment=experiment, config=config, metrics={"train": cycle_metrics, "dev": dev_metrics},
        )
        state.update({"cycle": cycle, "latest_generation": str(generation.resolve()), "source_cursors": cursors})
        atomic_json(exp / "training_state.json", state)
        with (exp / "history.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"cycle": cycle, "train": cycle_metrics, "dev": dev_metrics}, ensure_ascii=False) + "\n")
        dirs = sorted(p for p in (exp / "checkpoints" / "generations").glob("cycle-*") if p.is_dir())
        for old in dirs[:-args.keep_generations]:
            shutil.rmtree(old)
        emit("cycle_done", cycle=cycle, checkpoint=str(generation), **cycle_metrics)


if __name__ == "__main__":
    main()
