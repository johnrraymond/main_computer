#!/usr/bin/env python3
"""Train-smoke Cloudflare's released Clef-Flash JointSchemaHead.

This is deliberately *not* a reproduction of Cloudflare's private post-training
pipeline.  It exercises the public JointSchemaHead implementation and released
head weights without downloading the 19.1 GB Qwen backbone.

The smoke proves that the published head can:
  * load at the pinned public Clef-Flash revision,
  * accept differentiable hidden-state / lexical-prior inputs,
  * produce finite supervised decision loss,
  * backpropagate non-zero gradients,
  * update parameters, and
  * reduce loss on a tiny deterministic synthetic task.

All progress is emitted as JSON.  If anything fails, the tool writes whatever
progress/results already exist plus a full traceback to ``error.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import time
import traceback
from typing import Any

import torch
import torch.nn.functional as F


DEFAULT_REPO_ID = "Cloudflare/clef-flash"
DEFAULT_REVISION = "17f0b0ad64efb65d273590632833508766b2aae6"
DEFAULT_HEAD_SHA256 = "19cdcec8c81dc9212be320fff47462ab342fbc1278be4368fb3da71241cf5ba0"
EXPECTED_CONFIG = {
    "hidden_size": 4096,
    "width": 1024,
    "routing_layers": 2,
    "layers": 4,
    "heads": 16,
    "feedforward": 4096,
}


def emit(event: str, **payload: Any) -> None:
    row = {"event": event, **payload}
    print(json.dumps(row, sort_keys=True, default=str), flush=True)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        handle.flush()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_device(value: str) -> torch.device:
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(value)


def import_module_from_path(path: Path):
    spec = importlib.util.spec_from_file_location("cloudflare_clef_joint_schema_model", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import Cloudflare module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def download_public_head_files(
    *, repo_id: str, revision: str, cache_dir: Path | None, include_weights: bool
) -> dict[str, Path]:
    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("huggingface_hub is required: pip install huggingface_hub") from exc

    filenames = ["joint_schema_model.py", "joint_head_config.json"]
    if include_weights:
        filenames.append("joint_head.safetensors")
    paths: dict[str, Path] = {}
    for filename in filenames:
        emit("clef_smoke_download_start", repo_id=repo_id, revision=revision, filename=filename)
        path = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                cache_dir=str(cache_dir) if cache_dir is not None else None,
            )
        ).resolve()
        paths[filename] = path
        emit(
            "clef_smoke_download_complete",
            filename=filename,
            path=str(path),
            bytes=path.stat().st_size,
            sha256=sha256_file(path),
        )
    return paths


def load_public_head(
    *, module: Any, config_path: Path, weights_path: Path | None, device: torch.device
) -> tuple[torch.nn.Module, dict[str, Any]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    normalized = {key: int(config[key]) for key in EXPECTED_CONFIG}
    if normalized != EXPECTED_CONFIG:
        raise RuntimeError(
            f"unexpected Clef-Flash joint head config: {normalized}; expected {EXPECTED_CONFIG}"
        )
    head = module.JointSchemaHead(**config)
    if weights_path is not None:
        try:
            from safetensors.torch import load_file
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("safetensors is required: pip install safetensors") from exc
        actual_sha = sha256_file(weights_path)
        if actual_sha != DEFAULT_HEAD_SHA256:
            raise RuntimeError(
                f"released head SHA256 mismatch: {actual_sha} != {DEFAULT_HEAD_SHA256}"
            )
        state = load_file(str(weights_path), device="cpu")
        head.load_state_dict(state, strict=True)
    head = head.to(device=device, dtype=torch.float32)
    head.train()
    return head, normalized


def make_synthetic_batch(
    *, module: Any, hidden_size: int, count: int, sequence_length: int, seed: int, device: torch.device
) -> dict[str, Any]:
    if count < 4 or count % 2:
        raise ValueError("synthetic count must be even and >= 4")
    if sequence_length < 12:
        raise ValueError("sequence_length must be >= 12")
    generator = torch.Generator(device="cpu").manual_seed(seed)
    hidden = torch.randn((count, sequence_length, hidden_size), generator=generator, dtype=torch.float32) * 0.03
    input_ids = torch.zeros((count, sequence_length), dtype=torch.long)
    attention_mask = torch.ones((count, sequence_length), dtype=torch.long)
    labels = torch.tensor([index % 2 for index in range(count)], dtype=torch.long)

    # Small fake lexical table.  The public head only indexes token IDs present in
    # option spans, so a full 248k-token Qwen output matrix is unnecessary for this
    # optimizer smoke.
    lexical = torch.randn((16, hidden_size), generator=generator, dtype=torch.float32) * 0.03
    lexical[1].zero_()
    lexical[2].zero_()
    lexical[1, 3] = 2.0
    lexical[2, 3] = -2.0

    records = []
    for index, label in enumerate(labels.tolist()):
        sign = 1.0 if label == 1 else -1.0
        # Put a strong, learnable class signal in state, question, and global
        # positions while keeping the two option spans fixed across examples.
        hidden[index, 1:3, 0] += 4.0 * sign
        hidden[index, 4, 1] += 3.0 * sign
        hidden[index, -1, 2] += 2.0 * sign
        hidden[index, 6, 3] += 1.0
        hidden[index, 8, 3] -= 1.0
        input_ids[index, 6] = 1
        input_ids[index, 8] = 2
        question = module.EncodedQuestion(
            question_id="decision",
            question_type=1,
            question_span=(4, 5),
            option_spans=((6, 7), (8, 9)),
            option_ids=("left", "right"),
        )
        records.append(
            module.EncodedRecord(
                input_ids=tuple(int(value) for value in input_ids[index].tolist()),
                questions=(question,),
                record_id=f"synthetic-{index:02d}",
            )
        )

    return {
        "hidden_states": hidden.to(device),
        "input_ids": input_ids.to(device),
        "attention_mask": attention_mask.to(device),
        "records": records,
        "output_embedding_weight": lexical.to(device),
        "labels": labels.to(device),
    }


def stack_logits(head: torch.nn.Module, batch: dict[str, Any]) -> torch.Tensor:
    outputs = head(
        batch["hidden_states"],
        batch["input_ids"],
        batch["attention_mask"],
        batch["records"],
        batch["output_embedding_weight"],
    )
    if len(outputs) != len(batch["records"]):
        raise RuntimeError(f"head returned {len(outputs)} records for {len(batch['records'])} inputs")
    logits = []
    for index, record_outputs in enumerate(outputs):
        if len(record_outputs) != 1:
            raise RuntimeError(f"synthetic record {index} returned {len(record_outputs)} questions; expected 1")
        if tuple(record_outputs[0].shape) != (2,):
            raise RuntimeError(f"synthetic record {index} logits shape {tuple(record_outputs[0].shape)} != (2,)")
        logits.append(record_outputs[0])
    return torch.stack(logits, dim=0)


def decision_loss(
    logits: torch.Tensor, labels: torch.Tensor, *, label_smoothing: float, brier_weight: float
) -> tuple[torch.Tensor, dict[str, float]]:
    ce = F.cross_entropy(logits, labels, label_smoothing=label_smoothing)
    probabilities = logits.softmax(dim=-1)
    target = F.one_hot(labels, num_classes=logits.shape[-1]).to(probabilities.dtype)
    brier = torch.square(probabilities - target).sum(dim=-1).mean()
    total = ce + brier_weight * brier
    metrics = {
        "cross_entropy": float(ce.detach().cpu()),
        "brier": float(brier.detach().cpu()),
        "loss": float(total.detach().cpu()),
        "accuracy": float((logits.argmax(dim=-1) == labels).float().mean().detach().cpu()),
    }
    return total, metrics


def finite_metrics(metrics: dict[str, float]) -> bool:
    return all(math.isfinite(float(value)) for value in metrics.values())


def first_nonzero_gradient_parameter(head: torch.nn.Module) -> tuple[str, torch.Tensor]:
    for name, parameter in head.named_parameters():
        if parameter.requires_grad and parameter.grad is not None:
            grad = parameter.grad.detach()
            if torch.isfinite(grad).all() and float(grad.float().abs().sum().cpu()) > 0.0:
                return name, parameter.detach().float().cpu().clone()
    raise RuntimeError("backward pass produced no parameter with a non-zero finite gradient")


def tracked_parameter_delta(head: torch.nn.Module, name: str, before: torch.Tensor) -> float:
    parameter = dict(head.named_parameters()).get(name)
    if parameter is None:
        raise RuntimeError(f"tracked parameter disappeared after training: {name}")
    return float(torch.linalg.vector_norm(parameter.detach().float().cpu() - before))

def train_head(
    *, head: torch.nn.Module, batch: dict[str, Any], steps: int, learning_rate: float,
    label_smoothing: float, brier_weight: float, events_path: Path | None = None,
) -> dict[str, Any]:
    if steps <= 0:
        raise ValueError("steps must be > 0")
    optimizer = torch.optim.AdamW(head.parameters(), lr=learning_rate, weight_decay=0.0)
    tracked_name: str | None = None
    tracked_before: torch.Tensor | None = None

    head.eval()
    with torch.no_grad():
        initial_logits = stack_logits(head, batch)
        _, initial = decision_loss(
            initial_logits,
            batch["labels"],
            label_smoothing=label_smoothing,
            brier_weight=brier_weight,
        )
    head.train()
    if not finite_metrics(initial):
        raise RuntimeError(f"non-finite initial metrics: {initial}")
    emit("clef_smoke_initial", **initial)
    if events_path is not None:
        append_jsonl(events_path, {"event": "clef_smoke_initial", **initial})

    maximum_grad_norm = 0.0
    history = []
    for step in range(1, steps + 1):
        optimizer.zero_grad(set_to_none=True)
        logits = stack_logits(head, batch)
        loss, metrics = decision_loss(
            logits,
            batch["labels"],
            label_smoothing=label_smoothing,
            brier_weight=brier_weight,
        )
        if not torch.isfinite(loss):
            raise RuntimeError(f"non-finite loss at step {step}: {float(loss.detach().cpu())}")
        loss.backward()
        grad_norm = float(torch.nn.utils.clip_grad_norm_(head.parameters(), max_norm=10.0).detach().cpu())
        if not math.isfinite(grad_norm):
            raise RuntimeError(f"non-finite gradient norm at step {step}: {grad_norm}")
        maximum_grad_norm = max(maximum_grad_norm, grad_norm)
        if tracked_name is None:
            tracked_name, tracked_before = first_nonzero_gradient_parameter(head)
            emit("clef_smoke_parameter_tracked", parameter=tracked_name)
            if events_path is not None:
                append_jsonl(events_path, {"event": "clef_smoke_parameter_tracked", "parameter": tracked_name})
        optimizer.step()
        row = {"event": "clef_smoke_train_step", "step": step, "grad_norm": grad_norm, **metrics}
        emit(**row)
        if events_path is not None:
            append_jsonl(events_path, row)
        history.append(row)

    head.eval()
    with torch.no_grad():
        final_logits = stack_logits(head, batch)
        _, final = decision_loss(
            final_logits,
            batch["labels"],
            label_smoothing=label_smoothing,
            brier_weight=brier_weight,
        )
    if tracked_name is None or tracked_before is None:
        raise RuntimeError("training completed without selecting a gradient-bearing parameter")
    tracked_delta = tracked_parameter_delta(head, tracked_name, tracked_before)
    result = {
        "initial": initial,
        "final": final,
        "maximum_grad_norm": maximum_grad_norm,
        "tracked_parameter": tracked_name,
        "tracked_parameter_delta_l2": tracked_delta,
        "steps": steps,
        "learning_rate": learning_rate,
        "label_smoothing": label_smoothing,
        "brier_weight": brier_weight,
        "history": history,
    }
    if not finite_metrics(final):
        raise RuntimeError(f"non-finite final metrics: {final}")
    if maximum_grad_norm <= 0.0:
        raise RuntimeError("training produced no non-zero gradient")
    if tracked_delta <= 0.0:
        raise RuntimeError(f"training did not change tracked parameter {tracked_name}: delta={tracked_delta}")
    if final["loss"] >= initial["loss"]:
        raise RuntimeError(
            f"smoke did not reduce loss: initial={initial['loss']:.6f} final={final['loss']:.6f}"
        )
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = resolve_device(args.device)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "events.jsonl"
    if events_path.exists() and not args.resume_output:
        raise RuntimeError(f"output already contains events.jsonl; use a new directory: {output_dir}")

    progress = {
        "status": "starting",
        "repo_id": args.repo_id,
        "revision": args.revision,
        "device": str(device),
        "seed": args.seed,
        "steps": args.steps,
        "output_dir": str(output_dir),
    }
    atomic_json(output_dir / "progress.json", progress)
    emit("clef_smoke_start", **progress)
    append_jsonl(events_path, {"event": "clef_smoke_start", **progress})

    paths = download_public_head_files(
        repo_id=args.repo_id,
        revision=args.revision,
        cache_dir=Path(args.cache_dir).expanduser().resolve() if args.cache_dir else None,
        include_weights=not args.random_head,
    )
    module = import_module_from_path(paths["joint_schema_model.py"])
    weights = None if args.random_head else paths["joint_head.safetensors"]
    head, config = load_public_head(
        module=module,
        config_path=paths["joint_head_config.json"],
        weights_path=weights,
        device=device,
    )
    parameter_count = sum(parameter.numel() for parameter in head.parameters())
    trainable_count = sum(parameter.numel() for parameter in head.parameters() if parameter.requires_grad)
    emit(
        "clef_smoke_head_loaded",
        config=config,
        parameter_count=parameter_count,
        trainable_parameter_count=trainable_count,
        released_weights=not args.random_head,
        device=str(device),
    )
    progress.update({
        "status": "head_loaded",
        "head_config": config,
        "parameter_count": parameter_count,
        "trainable_parameter_count": trainable_count,
        "released_weights": not args.random_head,
    })
    atomic_json(output_dir / "progress.json", progress)

    batch = make_synthetic_batch(
        module=module,
        hidden_size=config["hidden_size"],
        count=args.examples,
        sequence_length=args.sequence_length,
        seed=args.seed,
        device=device,
    )
    emit(
        "clef_smoke_batch_ready",
        examples=args.examples,
        sequence_length=args.sequence_length,
        hidden_size=config["hidden_size"],
        labels=batch["labels"].detach().cpu().tolist(),
    )
    progress["status"] = "training"
    atomic_json(output_dir / "progress.json", progress)

    result = train_head(
        head=head,
        batch=batch,
        steps=args.steps,
        learning_rate=args.learning_rate,
        label_smoothing=args.label_smoothing,
        brier_weight=args.brier_weight,
        events_path=events_path,
    )
    result.update({
        "status": "pass",
        "repo_id": args.repo_id,
        "revision": args.revision,
        "device": str(device),
        "head_config": config,
        "parameter_count": parameter_count,
        "trainable_parameter_count": trainable_count,
        "released_weights": not args.random_head,
        "synthetic_examples": args.examples,
        "sequence_length": args.sequence_length,
        "note": (
            "Public-head optimizer smoke only. Cloudflare's exact synthetic corpus, LoRA trainer, "
            "loss weights, and RLCD implementation are not published in the release."
        ),
    })
    atomic_json(output_dir / "result.json", result)

    # Save the changed head so the smoke proves that a trainable artifact was actually produced.
    checkpoint_path = output_dir / "joint_head_smoke_after.safetensors"
    try:
        from safetensors.torch import save_file
        cpu_state = {name: tensor.detach().cpu().contiguous() for name, tensor in head.state_dict().items()}
        save_file(cpu_state, str(checkpoint_path))
        result["checkpoint"] = {
            "path": str(checkpoint_path),
            "bytes": checkpoint_path.stat().st_size,
            "sha256": sha256_file(checkpoint_path),
        }
        atomic_json(output_dir / "result.json", result)
    except Exception as exc:
        emit("clef_smoke_checkpoint_warning", exception_type=type(exc).__name__, exception=str(exc))
        result["checkpoint_warning"] = f"{type(exc).__name__}: {exc}"
        atomic_json(output_dir / "result.json", result)

    progress.update({"status": "complete", "result": str(output_dir / "result.json")})
    atomic_json(output_dir / "progress.json", progress)
    emit(
        "clef_smoke_pass",
        initial_loss=result["initial"]["loss"],
        final_loss=result["final"]["loss"],
        initial_accuracy=result["initial"]["accuracy"],
        final_accuracy=result["final"]["accuracy"],
        maximum_grad_norm=result["maximum_grad_norm"],
        result=str(output_dir / "result.json"),
    )
    return result


def self_test() -> None:
    labels = torch.tensor([0, 1])
    logits = torch.tensor([[2.0, -1.0], [-1.0, 2.0]], requires_grad=True)
    loss, metrics = decision_loss(logits, labels, label_smoothing=0.05, brier_weight=0.25)
    assert finite_metrics(metrics)
    assert metrics["accuracy"] == 1.0
    loss.backward()
    assert logits.grad is not None
    assert float(logits.grad.abs().sum()) > 0.0
    assert resolve_device("cpu").type == "cpu"
    emit("clef_smoke_self_test_passed", metrics=metrics)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--output-dir", default="clef_flash_joint_head_train_smoke")
    parser.add_argument("--cache-dir")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, cuda:0, ...")
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--examples", type=int, default=8)
    parser.add_argument("--sequence-length", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--label-smoothing", type=float, default=0.05)
    parser.add_argument("--brier-weight", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument(
        "--random-head",
        action="store_true",
        help="Instantiate the public architecture without downloading/loading the 244 MB released head weights.",
    )
    parser.add_argument("--resume-output", action="store_true", help="Allow appending to an existing smoke output directory.")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        self_test()
        return 0
    output_dir = Path(args.output_dir).expanduser().resolve()
    try:
        run(args)
        return 0
    except Exception as exc:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        report = {
            "event": "clef_smoke_failed",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
            "output_dir": str(output_dir),
            "artifacts": {},
        }
        if output_dir.exists():
            for name in ("progress.json", "events.jsonl", "result.json", "joint_head_smoke_after.safetensors"):
                path = output_dir / name
                if path.is_file():
                    report["artifacts"][name] = {
                        "path": str(path),
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
            try:
                atomic_json(output_dir / "error.json", report)
            except Exception:
                pass
        emit(**report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
