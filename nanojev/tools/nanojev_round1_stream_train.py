#!/usr/bin/env python3
"""Round-1 streaming head-only training for NanoJev on main_computer code.

This intentionally avoids a monolithic corpus-build phase.  Each cycle selects a
small deterministic shard of Python/JavaScript source files, generates a few
hundred self-supervised exact-next-token Choice questions, trains the fresh
NanoJev head for a bounded wall-clock interval, evaluates a fixed held-out dev
probe, reports train/dev error, and checkpoints before moving to the next shard.

The Qwen3-0.6B backbone remains frozen for every optimizer update.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import time

DEFAULT_MODEL = "Qwen/Qwen3-0.6B"
DEFAULT_REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def cyclic_slice(values, start: int, count: int):
    if not values:
        return []
    return [values[(start + i) % len(values)] for i in range(min(count, len(values)))]


def cycle_error(train_correct: int, train_questions: int) -> float | None:
    if train_questions <= 0:
        return None
    return 1.0 - (train_correct / train_questions)


def self_test() -> None:
    assert cyclic_slice([1, 2, 3], 2, 3) == [3, 1, 2]
    assert abs(cycle_error(7, 10) - 0.3) < 1e-12
    assert cycle_error(0, 0) is None
    print(json.dumps({"ok": True, "self_test": "passed"}))


def import_nanojev(nanojev_root: Path):
    scripts = nanojev_root / "scripts"
    sys.path.insert(0, str(scripts))
    import train_pipeline_decisions as pipeline  # type: ignore
    from train_toy_decisions import DecisionModel  # type: ignore
    for name in ("load_training_examples", "pack_complete_questions", "grouped_target_loss"):
        if not hasattr(pipeline, name):
            raise RuntimeError(f"NanoJev pipeline lacks required API: {name}")
    return pipeline, DecisionModel


def write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in records), encoding="utf-8")


def load_selected_docs(builder, sources, *, root: Path, split: str, tokenizer, seed: int,
                       max_file_tokens: int, min_file_tokens: int, char_window: int):
    docs = []
    rng = random.Random(seed)
    for path, language in sources:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # Sample a bounded character window before tokenization.  This keeps every
        # shard cheap even when a selected source file is very large, while moving
        # the sampled region between cycles exposes different portions of long files.
        if len(text) > char_window:
            start = rng.randrange(0, len(text) - char_window + 1)
            if start:
                nl = text.find("\n", start, min(len(text), start + 512))
                if nl >= 0:
                    start = nl + 1
            text = text[start:start + char_window]
        token_ids = tuple(tokenizer.encode(
            text, add_special_tokens=False, truncation=True,
            max_length=max_file_tokens, verbose=False,
        ))
        if len(token_ids) < min_file_tokens:
            continue
        relative = path.relative_to(root).as_posix()
        docs.append(builder.SourceDoc(path, relative, language, text, token_ids, split))
    return docs


def build_records(builder, *, docs, tokenizer, split: str, count: int, context_tokens: int,
                  candidate_counts, lookahead_tokens: int, seed: int):
    if not docs:
        raise RuntimeError(f"no usable {split} documents in selected shard")
    pools = builder.build_global_pools(docs, tokenizer)
    return builder.sample_split(
        docs=docs, tokenizer=tokenizer, split=split, n=count,
        context_tokens=context_tokens, candidate_counts=candidate_counts,
        lookahead=lookahead_tokens, global_pools=pools, seed=seed,
    )


def evaluate(model, examples, pad_token_id, pipeline, *, precision: str,
             microbatch_questions: int, max_microbatch_tokens: int, label: str):
    import torch
    model.eval()
    groups = pipeline.pack_complete_questions(examples, microbatch_questions, max_microbatch_tokens)
    correct = 0
    nll_sum = 0.0
    rr_sum = 0.0
    q = 0
    started = time.perf_counter()
    with torch.inference_mode():
        for group in groups:
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                logits, _ = model(group, pad_token_id)
            for ex, z in zip(group, logits):
                k = len(ex["candidate_ids"])
                probs = z[:k].float().softmax(-1)
                gold = int(ex["gold_index"])
                order = torch.argsort(probs, descending=True).tolist()
                rank = order.index(gold) + 1
                correct += int(rank == 1)
                nll_sum += -math.log(max(float(probs[gold].item()), 1e-30))
                rr_sum += 1.0 / rank
                q += 1
    metrics = {
        "questions": q,
        "accuracy": correct / q,
        "top1_error": 1.0 - (correct / q),
        "mean_nll": nll_sum / q,
        "mean_reciprocal_rank": rr_sum / q,
        "elapsed_seconds": time.perf_counter() - started,
    }
    emit("evaluation_done", label=label, **metrics)
    return metrics


def head_state(model):
    return {
        k: v.detach().cpu().contiguous().clone()
        for k, v in model.state_dict().items()
        if not k.startswith("backbone.")
    }


def move_optimizer_state_to_cuda(optimizer) -> None:
    import torch
    for state in optimizer.state.values():
        for key, value in list(state.items()):
            if torch.is_tensor(value):
                state[key] = value.cuda()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=r"C:\Users\subsi\main_computer")
    p.add_argument("--nanojev-root", default=r"C:\Users\subsi\NanoJev")
    p.add_argument("--output-dir", default=r"C:\Users\subsi\NanoJev\runs\main_computer_code_round1_stream_seed17")
    p.add_argument("--tokenizer-dir", default=r"C:\Users\subsi\NanoJev\checkpoints\NanoJev-unified\tokenizer")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--revision", default=DEFAULT_REVISION)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--cycles", type=int, default=100)
    p.add_argument("--cycle-seconds", type=float, default=75.0)
    p.add_argument("--max-steps-per-cycle", type=int, default=20)
    p.add_argument("--train-files-per-cycle", type=int, default=40)
    p.add_argument("--train-records-per-cycle", type=int, default=256)
    p.add_argument("--dev-files", type=int, default=24)
    p.add_argument("--dev-records", type=int, default=64)
    p.add_argument("--test-files", type=int, default=24)
    p.add_argument("--test-records", type=int, default=128)
    p.add_argument("--batch-questions", type=int, default=8)
    p.add_argument("--microbatch-questions", type=int, default=4)
    p.add_argument("--max-microbatch-tokens", type=int, default=8192)
    p.add_argument("--max-length", type=int, default=384)
    p.add_argument("--context-tokens", type=int, default=192)
    p.add_argument("--lookahead-tokens", type=int, default=128)
    p.add_argument("--candidate-counts", default="2,4,8,16")
    p.add_argument("--max-file-bytes", type=int, default=1_000_000)
    p.add_argument("--max-file-tokens", type=int, default=4096)
    p.add_argument("--min-file-tokens", type=int, default=64)
    p.add_argument("--char-window", type=int, default=24000)
    p.add_argument("--head-lr", type=float, default=1e-3)
    p.add_argument("--set-head", choices=["none", "attention"], default="attention")
    p.add_argument("--precision", choices=["bf16", "fp32"], default="bf16")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--local-files-only", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test(); return
    numeric_positive = [args.cycles, args.cycle_seconds, args.max_steps_per_cycle,
                        args.train_files_per_cycle, args.train_records_per_cycle,
                        args.dev_files, args.dev_records, args.test_files, args.test_records,
                        args.batch_questions, args.microbatch_questions, args.max_length,
                        args.context_tokens, args.lookahead_tokens, args.max_file_tokens,
                        args.min_file_tokens, args.char_window, args.head_lr]
    if any(v <= 0 for v in numeric_positive):
        p.error("cycle/data/training parameters must be positive")

    root = Path(args.repo_root).expanduser().resolve(strict=True)
    nanojev_root = Path(args.nanojev_root).expanduser().resolve(strict=True)
    out = Path(args.output_dir).expanduser().resolve()
    tools_dir = Path(__file__).resolve().parent
    builder = load_local_module("nanojev_round1_build_code_choices_local", tools_dir / "nanojev_round1_build_code_choices.py")
    candidate_counts = builder.candidate_counts_for_samples(
        [int(x.strip()) for x in args.candidate_counts.split(",") if x.strip()]
    )

    if out.exists() and any(out.iterdir()) and not args.resume:
        raise ValueError(f"output directory is nonempty; use --resume or choose a new path: {out}")
    out.mkdir(parents=True, exist_ok=True)
    shards = out / "shards"
    shards.mkdir(exist_ok=True)

    from transformers import AutoModel, AutoTokenizer
    import torch
    from safetensors.torch import load_file, save_file

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported by CUDA device")
    random.seed(args.seed); torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)

    emit("source_scan_start", repo_root=str(root))
    sources = list(builder.iter_source_paths(root, args.max_file_bytes))
    by_split = {"train": [], "dev": [], "test": []}
    for source in sources:
        rel = source[0].relative_to(root).as_posix()
        by_split[builder.split_for_file(args.seed, rel)].append(source)
    for split, values in by_split.items():
        random.Random(args.seed + {"train": 11, "dev": 22, "test": 33}[split]).shuffle(values)
    emit("source_scan_done", total=len(sources), **{f"{k}_files": len(v) for k, v in by_split.items()})
    if any(not by_split[s] for s in ("train", "dev", "test")):
        raise RuntimeError("file split missing train/dev/test sources")

    emit("tokenizer_load_start", source=args.tokenizer_dir)
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_dir, trust_remote_code=False, local_files_only=args.local_files_only)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    emit("tokenizer_load_done", vocab_size=len(tokenizer))

    # Fixed held-out dev probe: built once from a small deterministic file subset.
    dev_path = out / "dev_probe.jsonl"
    if not dev_path.exists():
        emit("dev_probe_build_start", files=args.dev_files, records=args.dev_records)
        dev_docs = load_selected_docs(
            builder, by_split["dev"][:args.dev_files], root=root, split="dev",
            tokenizer=tokenizer, seed=args.seed + 2000, max_file_tokens=args.max_file_tokens,
            min_file_tokens=args.min_file_tokens, char_window=args.char_window,
        )
        dev_records = build_records(
            builder, docs=dev_docs, tokenizer=tokenizer, split="dev", count=args.dev_records,
            context_tokens=args.context_tokens, candidate_counts=candidate_counts,
            lookahead_tokens=args.lookahead_tokens, seed=args.seed + 2100,
        )
        write_jsonl(dev_path, dev_records)
        emit("dev_probe_build_done", path=str(dev_path), sha256=sha256_file(dev_path))

    emit("nanojev_import_start")
    pipeline, DecisionModel = import_nanojev(nanojev_root)
    emit("nanojev_import_done")
    dev_examples, _ = pipeline.load_training_examples(dev_path, tokenizer, args.max_length)
    if not dev_examples:
        raise RuntimeError("fixed dev probe produced no examples")

    emit("backbone_load_start", model=args.model, revision=args.revision)
    backbone = AutoModel.from_pretrained(
        args.model, revision=args.revision, dtype=torch.float32,
        attn_implementation="sdpa", trust_remote_code=False,
        local_files_only=args.local_files_only,
    )
    backbone.config.use_cache = False
    model = DecisionModel(backbone, args.set_head)
    for param in model.backbone.parameters():
        param.requires_grad_(False)
    head_params = [p for name, p in model.named_parameters() if not name.startswith("backbone.")]
    if not head_params:
        raise RuntimeError("no trainable head parameters")
    model.cuda()
    emit("backbone_load_done", gpu=torch.cuda.get_device_name(0), trainable_head_params=sum(p.numel() for p in head_params))

    optimizer = torch.optim.AdamW(head_params, lr=args.head_lr, weight_decay=0.01)
    latest_head = out / "latest_head.safetensors"
    optimizer_path = out / "optimizer.pt"
    progress_path = out / "progress.json"
    history_path = out / "history.json"
    best_head = out / "best_head.safetensors"

    start_cycle = 1
    history = []
    best_dev_nll = float("inf")
    best_cycle = None
    previous_dev_nll = None
    if args.resume:
        if not (latest_head.exists() and optimizer_path.exists() and progress_path.exists()):
            raise RuntimeError("--resume requested but latest_head/optimizer/progress checkpoint is incomplete")
        head_weights = load_file(latest_head, device="cpu")
        incompatible = model.load_state_dict(head_weights, strict=False)
        if incompatible.unexpected_keys or any(not k.startswith("backbone.") for k in incompatible.missing_keys):
            raise RuntimeError(f"head resume mismatch: {incompatible}")
        model.cuda()
        state = torch.load(optimizer_path, map_location="cpu", weights_only=False)
        optimizer.load_state_dict(state)
        move_optimizer_state_to_cuda(optimizer)
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        start_cycle = int(progress["cycle"]) + 1
        best_dev_nll = float(progress.get("best_dev_nll", float("inf")))
        best_cycle = progress.get("best_cycle")
        previous_dev_nll = progress.get("dev_mean_nll")
        if history_path.exists():
            history = json.loads(history_path.read_text(encoding="utf-8"))
        emit("resume_loaded", completed_cycle=start_cycle - 1, next_cycle=start_cycle,
             best_cycle=best_cycle, best_dev_nll=best_dev_nll)
    else:
        baseline = evaluate(
            model, dev_examples, tokenizer.pad_token_id, pipeline, precision=args.precision,
            microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label="dev_baseline",
        )
        previous_dev_nll = baseline["mean_nll"]
        dump(out / "baseline_dev.json", baseline)

    config = {
        "schema_version": "main-computer-nanojev-code-round1-stream-v1",
        "objective": "finite-choice exact-next-Qwen-token discrimination",
        "training_scope": "fresh NanoJev head only; Qwen backbone frozen",
        "repo_root": str(root), "model": args.model, "revision": args.revision,
        "seed": args.seed, "candidate_counts": list(candidate_counts),
        "cycle_seconds": args.cycle_seconds, "max_steps_per_cycle": args.max_steps_per_cycle,
        "train_files_per_cycle": args.train_files_per_cycle,
        "train_records_per_cycle": args.train_records_per_cycle,
        "dev_records": args.dev_records, "head_lr": args.head_lr,
        "dev_probe_sha256": sha256_file(dev_path),
    }
    dump(out / "config.json", config)

    global_step = sum(int(h.get("steps", 0)) for h in history)
    run_started = time.perf_counter()
    for cycle in range(start_cycle, args.cycles + 1):
        cycle_started = time.perf_counter()
        source_start = ((cycle - 1) * args.train_files_per_cycle) % len(by_split["train"])
        selected = cyclic_slice(by_split["train"], source_start, args.train_files_per_cycle)
        emit("cycle_start", cycle=cycle, cycles_total=args.cycles,
             source_start=source_start, selected_files=len(selected))

        shard_seed = args.seed + cycle * 10007
        docs = load_selected_docs(
            builder, selected, root=root, split="train", tokenizer=tokenizer,
            seed=shard_seed, max_file_tokens=args.max_file_tokens,
            min_file_tokens=args.min_file_tokens, char_window=args.char_window,
        )
        records = build_records(
            builder, docs=docs, tokenizer=tokenizer, split="train",
            count=args.train_records_per_cycle, context_tokens=args.context_tokens,
            candidate_counts=candidate_counts, lookahead_tokens=args.lookahead_tokens,
            seed=shard_seed + 1,
        )
        shard_path = shards / f"cycle-{cycle:05d}.jsonl"
        write_jsonl(shard_path, records)
        train_examples, _ = pipeline.load_training_examples(shard_path, tokenizer, args.max_length)
        if len(train_examples) < args.batch_questions:
            raise RuntimeError(f"cycle {cycle}: too few train examples")
        emit("cycle_shard_ready", cycle=cycle, documents=len(docs), questions=len(train_examples),
             shard=str(shard_path), sha256=sha256_file(shard_path))

        cycle_rng = random.Random(args.seed + cycle * 7919)
        training_started = time.perf_counter()
        train_nll_sum = 0.0
        train_questions = 0
        train_correct = 0
        cycle_steps = 0
        first_step_loss = None
        last_step_loss = None

        while cycle_steps < args.max_steps_per_cycle:
            # Time bound is checked between complete optimizer steps.  Every cycle
            # performs at least one update and then checkpoints.
            if cycle_steps > 0 and time.perf_counter() - training_started >= args.cycle_seconds:
                break
            batch = cycle_rng.sample(train_examples, args.batch_questions)
            groups = pipeline.pack_complete_questions(batch, args.microbatch_questions, args.max_microbatch_tokens)
            model.train(); model.backbone.eval()
            optimizer.zero_grad(set_to_none=True)
            step_loss = 0.0
            step_correct = 0
            step_questions = 0
            for group in groups:
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                    logits, _ = model(group, tokenizer.pad_token_id)
                    per_question = pipeline.grouped_target_loss(logits, group, "gold_distribution")
                    loss = per_question.sum() / len(batch)
                if not torch.isfinite(loss):
                    raise RuntimeError(f"cycle {cycle}: nonfinite training loss")
                loss.backward()
                step_loss += float(per_question.detach().float().sum().item())
                train_nll_sum += float(per_question.detach().float().sum().item())
                for ex, z in zip(group, logits):
                    k = len(ex["candidate_ids"])
                    pred = int(torch.argmax(z[:k]).item())
                    gold = int(ex["gold_index"])
                    step_correct += int(pred == gold)
                    train_correct += int(pred == gold)
                    step_questions += 1
                    train_questions += 1
            torch.nn.utils.clip_grad_norm_(head_params, 1.0, error_if_nonfinite=True)
            optimizer.step()
            cycle_steps += 1
            global_step += 1
            mean_step_loss = step_loss / max(step_questions, 1)
            if first_step_loss is None:
                first_step_loss = mean_step_loss
            last_step_loss = mean_step_loss
            emit("cycle_train_step", cycle=cycle, cycle_step=cycle_steps, global_step=global_step,
                 mean_nll=mean_step_loss, top1_error=1.0 - (step_correct / max(step_questions, 1)),
                 elapsed_training_seconds=time.perf_counter() - training_started)

        train_mean_nll = train_nll_sum / max(train_questions, 1)
        train_top1_error = cycle_error(train_correct, train_questions)
        dev = evaluate(
            model, dev_examples, tokenizer.pad_token_id, pipeline, precision=args.precision,
            microbatch_questions=args.microbatch_questions,
            max_microbatch_tokens=args.max_microbatch_tokens, label=f"dev_cycle_{cycle}",
        )
        delta_dev_nll = None if previous_dev_nll is None else dev["mean_nll"] - float(previous_dev_nll)
        previous_dev_nll = dev["mean_nll"]

        improved = dev["mean_nll"] < best_dev_nll
        if improved:
            best_dev_nll = dev["mean_nll"]
            best_cycle = cycle
            save_file(head_state(model), best_head)

        # The checkpoint boundary IS the cycle boundary: head, optimizer, progress,
        # history, and visible error metrics all advance atomically together.
        save_file(head_state(model), latest_head)
        torch.save(optimizer.state_dict(), optimizer_path)
        result = {
            "cycle": cycle, "steps": cycle_steps, "global_step": global_step,
            "source_files": len(docs), "train_questions_available": len(train_examples),
            "train_questions_seen": train_questions,
            "train_mean_nll": train_mean_nll,
            "train_top1_error": train_top1_error,
            "first_step_mean_nll": first_step_loss,
            "last_step_mean_nll": last_step_loss,
            "dev_mean_nll": dev["mean_nll"], "dev_top1_error": dev["top1_error"],
            "dev_accuracy": dev["accuracy"], "delta_dev_nll": delta_dev_nll,
            "best_dev_nll": best_dev_nll, "best_cycle": best_cycle,
            "improved_best": improved,
            "training_seconds": time.perf_counter() - training_started,
            "cycle_total_seconds": time.perf_counter() - cycle_started,
            "checkpoint": str(latest_head),
        }
        history.append(result)
        dump(progress_path, result)
        dump(history_path, history)
        emit("cycle_result", **result)
        emit("checkpoint_saved", cycle=cycle, head=str(latest_head), optimizer=str(optimizer_path),
             progress=str(progress_path))

    if not best_head.exists():
        raise RuntimeError("no best head checkpoint was selected")
    best_weights = load_file(best_head, device="cpu")
    incompatible = model.load_state_dict(best_weights, strict=False)
    if incompatible.unexpected_keys or any(not k.startswith("backbone.") for k in incompatible.missing_keys):
        raise RuntimeError(f"best head restore mismatch: {incompatible}")
    model.cuda()

    emit("test_probe_build_start", files=args.test_files, records=args.test_records)
    test_docs = load_selected_docs(
        builder, by_split["test"][:args.test_files], root=root, split="test", tokenizer=tokenizer,
        seed=args.seed + 3000, max_file_tokens=args.max_file_tokens,
        min_file_tokens=args.min_file_tokens, char_window=args.char_window,
    )
    test_records = build_records(
        builder, docs=test_docs, tokenizer=tokenizer, split="test", count=args.test_records,
        context_tokens=args.context_tokens, candidate_counts=candidate_counts,
        lookahead_tokens=args.lookahead_tokens, seed=args.seed + 3100,
    )
    test_path = out / "test_probe.jsonl"
    write_jsonl(test_path, test_records)
    test_examples, _ = pipeline.load_training_examples(test_path, tokenizer, args.max_length)
    final_dev = evaluate(
        model, dev_examples, tokenizer.pad_token_id, pipeline, precision=args.precision,
        microbatch_questions=args.microbatch_questions,
        max_microbatch_tokens=args.max_microbatch_tokens, label="final_dev_best",
    )
    final_test = evaluate(
        model, test_examples, tokenizer.pad_token_id, pipeline, precision=args.precision,
        microbatch_questions=args.microbatch_questions,
        max_microbatch_tokens=args.max_microbatch_tokens, label="final_test_best",
    )

    # Standard NanoJev-compatible full checkpoint is written only once.
    save_file({k: v.detach().cpu().contiguous().clone() for k, v in model.state_dict().items()}, out / "best.safetensors")
    tokenizer.save_pretrained(out / "tokenizer")
    model.backbone.config.save_pretrained(out / "backbone_config")
    summary = {
        "best_cycle": best_cycle, "best_dev_nll": best_dev_nll,
        "final_dev": final_dev, "final_test": final_test,
        "cycles_completed": len(history), "global_step": global_step,
        "run_seconds": time.perf_counter() - run_started,
        "max_gpu_allocated_gb": torch.cuda.max_memory_allocated() / 1e9,
        "backbone_frozen": True,
    }
    dump(out / "summary.json", summary)
    emit("training_done", output_dir=str(out), **summary)


if __name__ == "__main__":
    main()
