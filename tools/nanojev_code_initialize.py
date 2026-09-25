#!/usr/bin/env python3
"""Initialize a clean frozen-backbone NanoJev next-lexeme-verification experiment."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sys

SCHEMA = "main-computer-nanojev-code-lexeme-experiment-v1"
STATE_SCHEMA = "main-computer-nanojev-code-lexeme-training-state-v1"


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


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


def sha256_json(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
    if not scripts.is_dir():
        raise RuntimeError(f"NanoJev scripts directory missing: {scripts}")
    sys.path.insert(0, str(scripts))
    import train_pipeline_decisions as pipeline  # type: ignore
    from train_toy_decisions import DecisionModel  # type: ignore
    return pipeline, DecisionModel


def head_state(model):
    return {
        k: v.detach().cpu().contiguous().clone()
        for k, v in model.state_dict().items()
        if not k.startswith("backbone.")
    }


def tensor_digest(tensor) -> str:
    return hashlib.sha256(tensor.detach().cpu().contiguous().numpy().tobytes()).hexdigest()


def choose_probe(model, *, backbone: bool) -> tuple[str, str]:
    for name, param in model.named_parameters():
        is_backbone = name.startswith("backbone.")
        if is_backbone == backbone and param.numel() >= 16:
            return name, tensor_digest(param)
    raise RuntimeError("unable to select parameter probe")


def self_test() -> None:
    assert sha256_json({"b": 2, "a": 1}) == sha256_json({"a": 1, "b": 2})
    print(json.dumps({"ok": True, "self_test": "passed"}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=r"C:\Users\subsi\main_computer")
    p.add_argument("--nanojev-root", default=r"C:\Users\subsi\NanoJev")
    p.add_argument("--experiment-dir", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--revision", default="main")
    p.add_argument("--set-head", choices=["none", "attention"], default="attention")
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--max-length", type=int, default=384)
    p.add_argument("--max-prefix-tokens", type=int, default=192)
    p.add_argument("--max-lexeme-tokens", type=int, default=48)
    p.add_argument("--max-file-bytes", type=int, default=1_000_000)
    p.add_argument("--suffix-pool-files", type=int, default=256)
    p.add_argument("--dev-files", type=int, default=40)
    p.add_argument("--dev-pairs", type=int, default=64, help="Produces twice this many balanced Boolean records")
    p.add_argument("--test-files", type=int, default=60)
    p.add_argument("--test-pairs", type=int, default=128)
    p.add_argument("--local-files-only", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test(); return
    if min(args.max_length, args.max_prefix_tokens, args.max_lexeme_tokens, args.max_file_bytes, args.dev_files,
           args.suffix_pool_files, args.dev_pairs, args.test_files, args.test_pairs) <= 0:
        p.error("limits must be positive")

    repo_root = Path(args.repo_root).expanduser().resolve(strict=True)
    nanojev_root = Path(args.nanojev_root).expanduser().resolve(strict=True)
    exp = Path(args.experiment_dir).expanduser().resolve()
    if exp.exists() and any(exp.iterdir()):
        raise RuntimeError(f"experiment directory must be new/empty: {exp}")
    exp.mkdir(parents=True, exist_ok=True)
    for rel in ("artifacts", "manifests", "probes", "shards", "checkpoints/generations", "checkpoints/best"):
        (exp / rel).mkdir(parents=True, exist_ok=True)

    tools_dir = Path(__file__).resolve().parent
    data = load_local_module("nanojev_code_lexeme_data_for_init", tools_dir / "nanojev_code_lexeme_data.py")

    emit("source_manifest_scan_start", repo_root=str(repo_root))
    manifests = {"train": [], "dev": [], "test": []}
    for path, language in data.iter_source_paths(repo_root, args.max_file_bytes):
        relative = path.relative_to(repo_root).as_posix()
        split = data.split_for_file(args.seed, relative)
        manifests[split].append({"relative": relative, "language": language})
    for split, rows in manifests.items():
        rows.sort(key=lambda r: r["relative"])
        random.Random(args.seed + {"train": 11, "dev": 22, "test": 33}[split]).shuffle(rows)
        atomic_json(exp / "manifests" / f"{split}.json", rows)
    if any(not manifests[s] for s in manifests):
        raise RuntimeError("source split missing train/dev/test files")
    emit("source_manifest_scan_done", total=sum(len(v) for v in manifests.values()),
         train_files=len(manifests["train"]), dev_files=len(manifests["dev"]), test_files=len(manifests["test"]))

    import torch
    from safetensors.torch import save_file
    from transformers import AutoModel, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    emit("base_load_start", model=args.model, revision=args.revision)
    tokenizer = AutoTokenizer.from_pretrained(
        args.model, revision=args.revision, trust_remote_code=False,
        local_files_only=args.local_files_only,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    backbone = AutoModel.from_pretrained(
        args.model, revision=args.revision, dtype=torch.float32,
        attn_implementation="sdpa", trust_remote_code=False,
        local_files_only=args.local_files_only,
    )
    resolved_revision = getattr(backbone.config, "_commit_hash", None) or args.revision
    emit("base_load_done", resolved_revision=resolved_revision,
         hidden_size=getattr(backbone.config, "hidden_size", None),
         parameter_count=sum(p.numel() for p in backbone.parameters()))

    pipeline, DecisionModel = import_nanojev(nanojev_root)
    model = DecisionModel(backbone, args.set_head)
    backbone_probe_name, backbone_probe_sha = choose_probe(model, backbone=True)
    head_probe_name, head_probe_sha = choose_probe(model, backbone=False)

    tokenizer.save_pretrained(exp / "artifacts" / "tokenizer")
    model.backbone.config.save_pretrained(exp / "artifacts" / "backbone_config")
    initial_head_path = exp / "artifacts" / "initial_head.safetensors"
    save_file(head_state(model), initial_head_path)

    emit("super_suffix_build_start", files=min(args.suffix_pool_files, len(manifests["train"])))
    pool_docs = data.load_docs(manifests["train"][:args.suffix_pool_files], repo_root)
    super_suffix = data.build_super_suffix_pool(pool_docs)
    super_suffix = data.filter_super_suffix_pool(super_suffix, tokenizer, args.max_lexeme_tokens)
    if not super_suffix:
        raise RuntimeError("train-derived super suffix pool is empty")
    suffix_path = exp / "artifacts" / "super_suffix.json"
    atomic_json(suffix_path, super_suffix)
    emit("super_suffix_build_done", kinds=sorted(super_suffix), entries=sum(len(v) for v in super_suffix.values()))

    emit("lexeme_probe_build_start")
    dev_docs = data.load_docs(manifests["dev"][:args.dev_files], repo_root)
    test_docs = data.load_docs(manifests["test"][:args.test_files], repo_root)
    dev_records = data.sample_paired_records(
        docs=dev_docs, tokenizer=tokenizer, split="dev", pair_count=args.dev_pairs,
        max_prefix_tokens=args.max_prefix_tokens, seed=args.seed + 2000, pools=super_suffix,
        max_lexeme_tokens=args.max_lexeme_tokens,
    )
    test_records = data.sample_paired_records(
        docs=test_docs, tokenizer=tokenizer, split="test", pair_count=args.test_pairs,
        max_prefix_tokens=args.max_prefix_tokens, seed=args.seed + 3000, pools=super_suffix,
        max_lexeme_tokens=args.max_lexeme_tokens,
    )
    dev_path = exp / "probes" / "dev.jsonl"
    test_path = exp / "probes" / "test.jsonl"
    data.write_jsonl(dev_path, dev_records)
    data.write_jsonl(test_path, test_records)
    pipeline.read_training_records(dev_path)
    pipeline.read_training_records(test_path)
    # Also ensure NanoJev can prepare every fixed question within max_length now.
    pipeline.load_training_examples(dev_path, tokenizer, args.max_length)
    pipeline.load_training_examples(test_path, tokenizer, args.max_length)
    emit("lexeme_probe_build_done", dev_records=len(dev_records), test_records=len(test_records))

    experiment = {
        "schema_version": SCHEMA,
        "task": "binary_next_lexeme_verification",
        "repo_root": str(repo_root),
        "nanojev_root": str(nanojev_root),
        "model": args.model,
        "requested_revision": args.revision,
        "resolved_model_revision": resolved_revision,
        "set_head": args.set_head,
        "seed": args.seed,
        "backbone_frozen": True,
        "max_length": args.max_length,
        "max_prefix_tokens": args.max_prefix_tokens,
        "max_lexeme_tokens": args.max_lexeme_tokens,
        "super_suffix": str(suffix_path),
        "super_suffix_sha256": sha256_file(suffix_path),
        "manifests": {s: str(exp / "manifests" / f"{s}.json") for s in manifests},
        "manifest_sha256": {s: sha256_file(exp / "manifests" / f"{s}.json") for s in manifests},
        "dev_probe": str(dev_path),
        "dev_probe_sha256": sha256_file(dev_path),
        "test_probe": str(test_path),
        "test_probe_sha256": sha256_file(test_path),
        "initial_head": str(initial_head_path),
        "initial_head_sha256": sha256_file(initial_head_path),
        "parameter_probes": {
            "backbone": {"name": backbone_probe_name, "sha256": backbone_probe_sha},
            "head": {"name": head_probe_name, "sha256": head_probe_sha},
        },
        "initialization": "clean pretrained frozen backbone with fresh NanoJev decision head; binary next-lexeme task",
    }
    experiment["experiment_sha256"] = sha256_json({k: v for k, v in experiment.items() if k != "experiment_sha256"})
    atomic_json(exp / "experiment.json", experiment)
    state = {
        "schema_version": STATE_SCHEMA,
        "experiment_sha256": experiment["experiment_sha256"],
        "status": "initialized",
        "cycle": 0,
        "global_step": 0,
        "phase": "frozen_head_lexeme_binary",
        "source_cursor": 0,
        "latest_generation": None,
        "best_selection_policy": "dev_auc_then_probability_separation_then_nll",
        "best_cycle": None,
        "best_dev_auc": None,
        "best_dev_probability_separation": None,
        "best_dev_nll": None,
        "last_dev_auc": None,
        "last_dev_probability_separation": None,
        "last_dev_nll": None,
    }
    atomic_json(exp / "training_state.json", state)
    (exp / "history.jsonl").write_text("", encoding="utf-8")
    emit("initialization_complete", ok=True, experiment_dir=str(exp),
         experiment_sha256=experiment["experiment_sha256"], task=experiment["task"],
         model=args.model, resolved_model_revision=resolved_revision,
         initial_head_sha256=experiment["initial_head_sha256"],
         train_files=len(manifests["train"]), dev_records=len(dev_records), test_records=len(test_records))


if __name__ == "__main__":
    main()
