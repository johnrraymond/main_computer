#!/usr/bin/env python3
"""Train only a fresh NanoJev decision head on Round-1 code continuations.

The Qwen3-0.6B backbone is loaded from the original pretrained revision and then
frozen for every optimizer update. Only NanoJev's norm/scalar/set-attention head
parameters are optimized. The resulting directory is compatible with NanoJev's
DecisionPredictor/serve_decisions checkpoint layout.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
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
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def import_nanojev(nanojev_root: Path):
    scripts = nanojev_root / "scripts"
    required = [scripts / name for name in ("train_pipeline_decisions.py", "train_toy_decisions.py")]
    for path in required:
        if not path.is_file():
            raise ValueError(f"NanoJev script missing: {path}")
    sys.path.insert(0, str(scripts))
    import train_pipeline_decisions as pipeline  # type: ignore
    from train_toy_decisions import DecisionModel  # type: ignore
    for name in ("read_training_records", "load_training_examples", "pack_complete_questions", "grouped_target_loss"):
        if not hasattr(pipeline, name):
            raise RuntimeError(f"local NanoJev train_pipeline_decisions.py lacks required API: {name}")
    return pipeline, DecisionModel


def ranking_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"questions": 0, "accuracy": None, "mean_nll": None, "mean_reciprocal_rank": None, "by_k": {}}
    correct = 0
    nll = 0.0
    rr = 0.0
    by_k = {}
    for row in rows:
        probs = row["student_probs"]
        gold = row["gold_index"]
        order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
        rank = order.index(gold) + 1
        hit = int(rank == 1)
        correct += hit
        nll += -math.log(max(probs[gold], 1e-30))
        rr += 1.0 / rank
        bucket = by_k.setdefault(str(len(probs)), {"n": 0, "correct": 0, "nll": 0.0, "rr": 0.0})
        bucket["n"] += 1; bucket["correct"] += hit; bucket["nll"] += -math.log(max(probs[gold], 1e-30)); bucket["rr"] += 1.0 / rank
    for k, bucket in by_k.items():
        n = bucket["n"]
        bucket.update(accuracy=bucket.pop("correct") / n, mean_nll=bucket.pop("nll") / n,
                      mean_reciprocal_rank=bucket.pop("rr") / n, chance_accuracy=1.0 / int(k))
    return {
        "questions": len(rows),
        "accuracy": correct / len(rows),
        "chance_accuracy": sum(1.0 / len(row["student_probs"]) for row in rows) / len(rows),
        "mean_nll": nll / len(rows),
        "mean_reciprocal_rank": rr / len(rows),
        "by_k": by_k,
    }


def evaluate(model, examples, pad_token_id, pipeline, args, predictions_path: Path | None = None, label: str = "eval") -> dict:
    import torch
    model.eval()
    rows = []
    groups = pipeline.pack_complete_questions(examples, args.microbatch_questions, args.max_microbatch_tokens)
    emit("evaluation_start", label=label, questions=len(examples), microbatches=len(groups))
    processed = 0
    last_reported = 0
    with torch.inference_mode():
        for group in groups:
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                logits, _ = model(group, pad_token_id)
            for ex, z in zip(group, logits):
                k = len(ex["candidate_ids"])
                probs = z[:k].float().softmax(-1).cpu().tolist()
                rows.append({
                    "id": ex["id"], "state_id": ex["state_id"], "split": ex["split"],
                    "family_id": ex["family_id"], "candidate_ids": ex["candidate_ids"],
                    "gold_index": ex["gold_index"], "student_probs": probs,
                    "source_path": ex["source"].get("metadata", {}).get("source_path"),
                    "target_token_display": ex["source"].get("metadata", {}).get("target_token_display"),
                    "candidate_count": k,
                })
            processed += len(group)
            if processed == len(examples) or processed - last_reported >= args.progress_every_questions:
                last_reported = processed
                emit("evaluation_progress", label=label, questions_done=processed, questions_total=len(examples))
    if predictions_path is not None:
        predictions_path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
    metrics = ranking_metrics(rows)
    emit("evaluation_done", label=label, accuracy=metrics.get("accuracy"), mean_nll=metrics.get("mean_nll"), mean_reciprocal_rank=metrics.get("mean_reciprocal_rank"))
    return metrics


def self_test() -> None:
    rows = [
        {"student_probs": [0.8, 0.2], "gold_index": 0},
        {"student_probs": [0.1, 0.2, 0.6, 0.1], "gold_index": 2},
    ]
    metrics = ranking_metrics(rows)
    assert metrics["accuracy"] == 1.0
    assert abs(metrics["chance_accuracy"] - 0.375) < 1e-12
    assert metrics["by_k"]["2"]["chance_accuracy"] == 0.5
    print(json.dumps({"ok": True, "self_test": "passed"}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nanojev-root", required=False, default=r"C:\Users\subsi\NanoJev")
    p.add_argument("--input", required=False, default=r"runtime\nanojev-training\round1\all.jsonl")
    p.add_argument("--output-dir", required=False, default=r"C:\Users\subsi\NanoJev\runs\main_computer_code_round1_seed17")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--revision", default=DEFAULT_REVISION)
    p.add_argument("--set-head", choices=["none", "attention"], default="attention")
    p.add_argument("--steps", type=int, default=400)
    p.add_argument("--batch-questions", type=int, default=8)
    p.add_argument("--microbatch-questions", type=int, default=1)
    p.add_argument("--max-microbatch-tokens", type=int, default=8192)
    p.add_argument("--max-length", type=int, default=384)
    p.add_argument("--eval-every", type=int, default=25)
    p.add_argument("--checkpoint-every", type=int, default=10,
                   help="overwrite latest_head.safetensors/progress.json every N optimizer steps")
    p.add_argument("--progress-every-questions", type=int, default=100,
                   help="emit evaluation progress after roughly this many questions")
    p.add_argument("--head-lr", type=float, default=1e-3)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--precision", choices=["bf16", "fp32"], default="bf16")
    p.add_argument("--disable-native-triton", action="store_true")
    p.add_argument("--local-files-only", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test(); return
    if min(args.steps, args.batch_questions, args.microbatch_questions, args.max_length, args.eval_every,
           args.checkpoint_every, args.progress_every_questions) <= 0:
        p.error("steps/batch/microbatch/max-length/eval/checkpoint/progress intervals must be positive")
    if args.max_microbatch_tokens < 0 or not math.isfinite(args.head_lr) or args.head_lr <= 0:
        p.error("invalid token budget or head learning rate")

    emit("trainer_start", output_dir=args.output_dir, steps=args.steps, batch_questions=args.batch_questions,
         microbatch_questions=args.microbatch_questions, eval_every=args.eval_every, checkpoint_every=args.checkpoint_every)
    nanojev_root = Path(args.nanojev_root).expanduser().resolve(strict=True)
    input_path = Path(args.input).expanduser().resolve(strict=True)
    out = Path(args.output_dir).expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"output directory must be new/empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    emit("trainer_import_nanojev_start", nanojev_root=str(nanojev_root))
    pipeline, DecisionModel = import_nanojev(nanojev_root)
    emit("trainer_import_nanojev_done")

    import torch
    from safetensors.torch import load_file, save_file
    from transformers import AutoModel, AutoTokenizer

    if args.disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for Round-1 training")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("requested bf16 but CUDA device does not report bf16 support")
    torch.backends.cuda.matmul.allow_tf32 = False
    random.seed(args.seed); torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)

    emit("trainer_tokenizer_load_start", model=args.model, revision=args.revision)
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision, trust_remote_code=False,
                                              local_files_only=args.local_files_only)
    emit("trainer_tokenizer_load_done", vocab_size=len(tokenizer))
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    emit("trainer_backbone_load_start", model=args.model, revision=args.revision)
    backbone = AutoModel.from_pretrained(args.model, revision=args.revision, dtype=torch.float32,
                                         attn_implementation="sdpa", trust_remote_code=False,
                                         local_files_only=args.local_files_only)
    emit("trainer_backbone_load_done")
    backbone.config.use_cache = False
    model = DecisionModel(backbone, args.set_head)
    for param in model.backbone.parameters():
        param.requires_grad_(False)
    head = [param for name, param in model.named_parameters() if not name.startswith("backbone.")]
    if not head or any(param.requires_grad for param in model.backbone.parameters()):
        raise RuntimeError("frozen-backbone invariant failed")

    emit("trainer_dataset_load_start", input=str(input_path))
    records, files = pipeline.read_training_records(input_path)
    emit("trainer_dataset_records_read", records=len(records), files=len(files))
    examples, audit = pipeline.load_training_examples(input_path, tokenizer, args.max_length)
    emit("trainer_dataset_examples_loaded", examples=len(examples))
    splits = {name: [ex for ex in examples if ex["split"] == name] for name in ("train", "dev", "calibration", "test", "ood")}
    for split in ("train", "dev", "test"):
        if not splits[split]:
            raise ValueError(f"required split is empty: {split}")
    train = splits["train"]
    if len(train) < args.batch_questions:
        raise ValueError("fewer training questions than one effective batch")
    for split_name, split_examples in splits.items():
        if split_examples:
            groups = pipeline.pack_complete_questions(split_examples, args.microbatch_questions, args.max_microbatch_tokens)
            emit("trainer_split_packed", split=split_name, questions=len(split_examples), microbatches=len(groups))

    emit("trainer_cuda_move_start")
    model.cuda()
    emit("trainer_cuda_move_done")
    resolved_revision = getattr(model.backbone.config, "_commit_hash", None) or args.revision
    config = {
        **vars(args),
        "schema_version": "main-computer-nanojev-code-round1-head-only-v1",
        "model": args.model,
        "resolved_model_revision": resolved_revision,
        "initialization": "original pretrained Qwen backbone with fresh NanoJev decision head",
        "training_scope": "head-only; backbone frozen for every optimizer update",
        "backbone_frozen": True,
        "objective": "gold_distribution",
        "loss": "complete-question cross entropy",
        "data_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "deps": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "safetensors")},
        "gpu": torch.cuda.get_device_name(0),
        "parameter_storage": "float32",
        "forward_autocast": "bfloat16" if args.precision == "bf16" else "disabled",
        "train_questions": len(train),
        "source_split_counts": dict(Counter(ex["split"] for ex in examples)),
        "parameter_count": sum(p.numel() for p in model.parameters()),
        "trainable_parameter_count": sum(p.numel() for p in head),
        "backbone_trainable_parameter_count": 0,
        "selection": "minimum dev deterministic-target NLL; test untouched until checkpoint selection",
    }
    dump(out / "config.json", config)
    dump(out / "target_audit.json", audit)
    tokenizer.save_pretrained(out / "tokenizer")
    model.backbone.config.save_pretrained(out / "backbone_config")

    emit("trainer_initial_dev_start")
    initial_dev = evaluate(model, splits["dev"], tokenizer.pad_token_id, pipeline, args, out / "predictions_initial_dev.jsonl", label="initial_dev")
    dump(out / "initial_dev_metrics.json", initial_dev)
    emit("trainer_initial_dev_done", accuracy=initial_dev.get("accuracy"), mean_nll=initial_dev.get("mean_nll"))

    optimizer = torch.optim.AdamW(head, lr=args.head_lr, weight_decay=0.01)
    best = float("inf"); best_step = None; logs = []
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    for step in range(1, args.steps + 1):
        batch = random.sample(train, args.batch_questions)
        groups = pipeline.pack_complete_questions(batch, args.microbatch_questions, args.max_microbatch_tokens)
        model.train(); model.backbone.eval()  # fixed feature extractor; train only the head
        optimizer.zero_grad(set_to_none=True)
        loss_sum = 0.0
        for group in groups:
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                logits, _ = model(group, tokenizer.pad_token_id)
                loss = pipeline.grouped_target_loss(logits, group, "gold_distribution").sum() / len(batch)
            if not torch.isfinite(loss):
                raise RuntimeError("nonfinite loss")
            loss.backward(); loss_sum += float(loss.detach())
        torch.nn.utils.clip_grad_norm_(head, 1.0, error_if_nonfinite=True)
        optimizer.step()
        item = {"step": step, "phase": "head-only", "loss": loss_sum,
                "questions": len(batch), "microbatches": len(groups),
                "elapsed_seconds": time.perf_counter() - started}
        if step % args.eval_every == 0 or step == args.steps:
            metrics = evaluate(model, splits["dev"], tokenizer.pad_token_id, pipeline, args, label=f"dev_step_{step}")
            item["dev"] = metrics
            score = metrics["mean_nll"]
            if score is not None and math.isfinite(score) and score < best:
                best = score; best_step = step
                # Save only the tiny trainable head during model selection. Writing the
                # frozen 0.6B backbone at every improving eval would create needless multi-GB I/O.
                save_file({k: v.detach().cpu().contiguous().clone() for k, v in model.state_dict().items()
                           if not k.startswith("backbone.")}, out / "best_head.safetensors")
        logs.append(item)
        if step == 1 or step % 10 == 0 or "dev" in item:
            emit("train_step", **item)
        if step == 1 or step % args.checkpoint_every == 0 or step == args.steps:
            save_file({k: v.detach().cpu().contiguous().clone() for k, v in model.state_dict().items()
                       if not k.startswith("backbone.")}, out / "latest_head.safetensors")
            progress = {
                "step": step, "steps_total": args.steps, "loss": loss_sum,
                "best_step": best_step, "best_dev_mean_nll": None if not math.isfinite(best) else best,
                "elapsed_seconds": time.perf_counter() - started,
            }
            dump(out / "progress.json", progress)
            dump(out / "train_log.partial.json", logs)
            emit("checkpoint_saved", path=str(out / "latest_head.safetensors"), **progress)

    if best_step is None:
        raise RuntimeError("no finite dev checkpoint selected")
    head_weights = load_file(out / "best_head.safetensors", device="cpu")
    incompatible = model.load_state_dict(head_weights, strict=False)
    if incompatible.unexpected_keys or any(not key.startswith("backbone.") for key in incompatible.missing_keys):
        raise RuntimeError(f"best-head restore mismatch: {incompatible}")
    model.cuda()
    # Materialize one standard NanoJev checkpoint only after dev selection.
    save_file({k: v.detach().cpu().contiguous().clone() for k, v in model.state_dict().items()}, out / "best.safetensors")
    final_dev = evaluate(model, splits["dev"], tokenizer.pad_token_id, pipeline, args, out / "predictions_dev.jsonl", label="final_dev")
    final_test = evaluate(model, splits["test"], tokenizer.pad_token_id, pipeline, args, out / "predictions_test.jsonl", label="final_test")
    summary = {
        "best_step": best_step,
        "best_dev_mean_nll": best,
        "selected_on": "dev deterministic-target NLL",
        "backbone_frozen": True,
        "initial_dev": initial_dev,
        "final_dev": final_dev,
        "final_test": final_test,
        "training_seconds": time.perf_counter() - started,
        "max_gpu_allocated_gb": torch.cuda.max_memory_allocated() / 1e9,
    }
    dump(out / "train_log.json", logs)
    dump(out / "summary.json", summary)
    emit("trainer_done", output_dir=str(out), **summary)


if __name__ == "__main__":
    main()
