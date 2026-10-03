#!/usr/bin/env python3
"""Live three-backbone + Clef-sized decision-head training smoke.

This is deliberately a *training* smoke rather than a benchmark.  It asks one
representative question from each current primary NanoJev objective to flow
through the real frozen Qwen/Pythia/TinyStories backbones on every optimizer
step.  Their live token-level evidence feeds a ~121M-parameter Clef-inspired
joint decision head.  No hidden-state cache is written or reused.

The smoke proves four things:
  1. all three language-model backbones remain frozen;
  2. the Clef-sized head receives gradients and changes;
  3. the same real NanoJev questions are re-encoded live every training epoch;
  4. mean fit loss on those questions decreases.

It is not a generalization benchmark.  The purpose is to answer whether this
architecture can actually be trained on the target laptop before committing to
a longer experiment.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
from typing import Any, Iterable, Sequence

TOOLS = Path(__file__).resolve().parent
DEFAULT_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_broad_curriculum_v1"
)
DEFAULT_OUTPUT = Path(r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_sized_live_smoke")
DEFAULT_SEED = 20261001
DEFAULT_CYCLE = 991001
DEFAULT_EPOCHS = 2
DEFAULT_HEAD_LR = 1e-4
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_GRAD_CLIP = 1.0
DEFAULT_MAX_PROMPT_TOKENS = 768
DEFAULT_MAX_ANSWER_TOKENS = 128
DEFAULT_PROMPT_EVIDENCE_TOKENS = 8
DEFAULT_ANSWER_EVIDENCE_TOKENS = 8
DEFAULT_PATH_BATCH = 4

# Clef-Flash release used only for compatible shared-head initialization.
CLEF_REPO = "Cloudflare/clef-flash"
CLEF_REVISION = "17f0b0ad64efb65d273590632833508766b2aae6"
CLEF_HEAD_SHA256 = "19cdcec8c81dc9212be320fff47462ab342fbc1278be4368fb3da71241cf5ba0"
CLEF_RELEASED_HEAD_PARAMS = 121_762_820

TASKS = (
    "legacy",
    "mutation",
    "ast",
    "consensus",
    "triad",
    "dictionary_definition",
    "english_code",
)
# Minimum generator counts that preserve each objective's native balance contract.
GENERATION_PLAN = {
    "legacy": 1,
    "mutation": 2,
    "ast": 2,
    "consensus": 4,
    "triad": 2,
    "dictionary_definition": 1,
    "english_code": 4,
}


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def artifact_inventory(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {}
    out: dict[str, Any] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root)).replace("\\", "/")
        size = int(path.stat().st_size)
        row: dict[str, Any] = {"bytes": size}
        if size <= 32 * 1024 * 1024:
            try:
                row["sha256"] = sha256_file(path)
            except OSError as exc:
                row["sha256_error"] = str(exc)
        out[rel] = row
    return out


class EventLog:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "events.jsonl"
        self.progress_path = self.output_dir / "progress.json"
        self.stage = "starting"
        self.last: dict[str, Any] = {}

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        print(json.dumps(row, sort_keys=True), flush=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
        self.last = row

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        atomic_json(self.progress_path, payload)
        self.emit("clef_sized_smoke_stage", **payload)


def prepare_output(path: Path) -> Path:
    path = Path(path).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    # A prior early failure must not poison the next run.  Only our own diagnostic
    # files are safe to clear.  Anything else indicates useful/unknown state.
    own_early = {"events.jsonl", "progress.json", "error.json"}
    existing = {p.name for p in path.iterdir()}
    unknown = existing - own_early
    if unknown:
        raise RuntimeError(
            f"smoke output directory contains prior artifacts; use a new directory: {path}; "
            f"found={sorted(unknown)}"
        )
    for name in own_early:
        (path / name).unlink(missing_ok=True)
    return path.resolve()


def write_error_report(output_dir: Path, progress: EventLog | None, exc: BaseException) -> None:
    tb = traceback.format_exc()
    payload = {
        "event": "clef_sized_smoke_failed",
        "stage": None if progress is None else progress.stage,
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "traceback": tb,
        "artifacts": artifact_inventory(output_dir),
    }
    try:
        atomic_json(Path(output_dir) / "error.json", payload)
    except Exception:
        pass
    print(json.dumps(payload, sort_keys=True), flush=True)


def cuda_memory(torch, stage: str) -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {"stage": stage, "cuda": False}
    torch.cuda.synchronize()
    return {
        "stage": stage,
        "cuda": True,
        "allocated_mb": round(torch.cuda.memory_allocated() / 1024**2, 2),
        "reserved_mb": round(torch.cuda.memory_reserved() / 1024**2, 2),
        "peak_allocated_mb": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
        "peak_reserved_mb": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
    }


def balanced_indices(start: int, end: int, limit: int) -> list[int]:
    count = max(0, end - start)
    if count <= 0 or limit <= 0:
        return []
    if count <= limit:
        return list(range(start, end))
    left = (limit + 1) // 2
    right = limit - left
    return list(range(start, start + left)) + list(range(end - right, end))


def balanced_truncate(ids: Sequence[int], limit: int) -> list[int]:
    ids = list(ids)
    if len(ids) <= limit:
        return ids
    idx = balanced_indices(0, len(ids), limit)
    return [ids[i] for i in idx]


def canonical_candidate_paths(question, candidate) -> tuple:
    # Composition-v2 keeps every semantic path.  In particular, consensus needs
    # both orientations of each pair so an outlier hypothesis is not evaluated
    # through an arbitrary A->B/A->C/B->C directional projection.  Exact
    # duplicate (prompt, answer) work is interned later by _model_sequences().
    return tuple(candidate.paths)


RELATIONAL_TASKS = {"mutation", "ast", "consensus", "triad"}
TASK_COMPOSITION_VERSION = "direct-relational-labels-bidirectional-v2"
EVIDENCE_CONTRACT_VERSION = "three-backbone-native-logp-predictor-terminal-v2"


def _program_pair_prompt(left: str, right: str, *, task: str) -> str:
    if task in {"ast", "triad", "consensus"}:
        instruction = (
            "Determine whether Program A and Program B have exactly the same normalized "
            "Python AST. Ignore formatting-only differences. "
            "Answer with exactly SAME or DIFFERENT."
        )
    elif task == "mutation":
        instruction = (
            "Determine whether Program B is a behavior-preserving rewrite of Program A. "
            "Answer with exactly PRESERVING or CHANGING."
        )
    else:
        raise ValueError(f"unsupported relational task: {task}")
    return (
        "Language: Python\n"
        "Program A:\n```python\n" + left.rstrip() + "\n```\n"
        "Program B:\n```python\n" + right.rstrip() + "\n```\n"
        "Task: " + instruction + "\n"
        "Answer:"
    )


def _new_path(template, prompt: str, answer: str):
    return template.__class__(prompt, answer)


def _new_candidate(template, candidate_id: str, paths: Sequence[Any]):
    return template.__class__(candidate_id, tuple(paths))


def _new_question_like(question, candidates: Sequence[Any]):
    kwargs = {
        "question_id": question.question_id,
        "task": question.task,
        "candidates": tuple(candidates),
        "gold_index": int(question.gold_index),
    }
    if hasattr(question, "stratum"):
        kwargs["stratum"] = str(getattr(question, "stratum") or "")
    try:
        return question.__class__(**kwargs)
    except TypeError:
        kwargs.pop("stratum", None)
        return question.__class__(**kwargs)


def _pair_codes_from_relation_candidate(candidate) -> tuple[str, str]:
    paths = tuple(candidate.paths)
    if len(paths) < 2:
        raise RuntimeError(f"relational candidate has fewer than two paths: {candidate.candidate_id}")
    # Legacy relation_candidate(left,right,...) stores left->right then right->left.
    return str(paths[1].answer), str(paths[0].answer)


def compose_relational_question_v2(question):
    """Turn hypothesis-conditioned code continuations into direct relation labels."""
    task = str(question.task)
    if task not in RELATIONAL_TASKS:
        return question
    if not question.candidates:
        raise RuntimeError(f"relational question has no candidates: {question.question_id}")
    template_candidate = question.candidates[0]
    template_path = template_candidate.paths[0]

    if task in {"ast", "mutation", "triad"}:
        left, right = _pair_codes_from_relation_candidate(template_candidate)
        answer_by_id = {
            "ast": {"positive": " SAME", "negative": " DIFFERENT"},
            "mutation": {"positive": " PRESERVING", "negative": " CHANGING"},
            "triad": {"same": " SAME", "different": " DIFFERENT"},
        }[task]
        candidates = []
        for candidate in question.candidates:
            if candidate.candidate_id not in answer_by_id:
                raise RuntimeError(f"unexpected {task} candidate id: {candidate.candidate_id}")
            answer = answer_by_id[candidate.candidate_id]
            paths = (
                _new_path(template_path, _program_pair_prompt(left, right, task=task), answer),
                _new_path(template_path, _program_pair_prompt(right, left, task=task), answer),
            )
            candidates.append(_new_candidate(candidate, candidate.candidate_id, paths))
        return _new_question_like(question, candidates)

    paths = tuple(template_candidate.paths)
    if len(paths) < 6:
        raise RuntimeError(
            f"consensus candidate lost bidirectional pair geometry: {question.question_id}"
        )
    a = str(paths[1].answer)
    b = str(paths[0].answer)
    c = str(paths[2].answer)
    pair_codes = {"ab": (a, b), "ac": (a, c), "bc": (b, c)}
    expected_by_id = {
        "none": {"ab": "same", "ac": "same", "bc": "same"},
        "a": {"ab": "different", "ac": "different", "bc": "same"},
        "b": {"ab": "different", "ac": "same", "bc": "different"},
        "c": {"ab": "same", "ac": "different", "bc": "different"},
    }
    candidates = []
    for candidate in question.candidates:
        expected = expected_by_id.get(candidate.candidate_id)
        if expected is None:
            raise RuntimeError(f"unexpected consensus candidate id: {candidate.candidate_id}")
        new_paths = []
        for pair_name in ("ab", "ac", "bc"):
            left, right = pair_codes[pair_name]
            answer = " SAME" if expected[pair_name] == "same" else " DIFFERENT"
            new_paths.extend((
                _new_path(template_path, _program_pair_prompt(left, right, task="consensus"), answer),
                _new_path(template_path, _program_pair_prompt(right, left, task="consensus"), answer),
            ))
        candidates.append(_new_candidate(candidate, candidate.candidate_id, new_paths))
    return _new_question_like(question, candidates)


@dataclass
class BackboneBundle:
    label: str
    model_name: str
    lm: Any
    backbone: Any
    tokenizer: Any
    output_weight: Any
    hidden_size: int
    max_positions: int


def _max_positions(config: Any) -> int:
    for name in ("max_position_embeddings", "n_positions", "max_sequence_length"):
        value = getattr(config, name, None)
        if isinstance(value, int) and value > 0:
            return int(value)
    return 2048


def freeze_module(module: Any) -> int:
    count = 0
    for parameter in module.parameters():
        parameter.requires_grad_(False)
        count += int(parameter.numel())
    module.eval()
    return count


def load_backbones(*, source: dict[str, Any], local_files_only: bool, logger: EventLog):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the live three-backbone smoke")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("the live smoke requires bf16-capable CUDA")

    specs = (
        ("qwen", str(source["model"]), str(source["revision"]), "sdpa"),
        ("pythia", "EleutherAI/pythia-70m", None, "sdpa"),
        ("tinystories", "roneneldan/TinyStories-33M", None, "eager"),
    )
    bundles: dict[str, BackboneBundle] = {}
    frozen_total = 0
    for label, model_name, revision, attn_impl in specs:
        logger.emit("clef_sized_backbone_load_start", label=label, model=model_name, revision=revision)
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            revision=revision,
            local_files_only=local_files_only,
            trust_remote_code=False,
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        if tokenizer.pad_token_id is None:
            raise RuntimeError(f"{label} tokenizer has no pad/eos token")
        lm = AutoModelForCausalLM.from_pretrained(
            model_name,
            revision=revision,
            dtype=torch.bfloat16,
            attn_implementation=attn_impl,
            local_files_only=local_files_only,
            trust_remote_code=False,
        ).to("cuda")
        frozen_total += freeze_module(lm)
        output = lm.get_output_embeddings()
        if output is None or not hasattr(output, "weight"):
            raise RuntimeError(f"{label} model has no output embedding weight")
        if label == "qwen":
            backbone = getattr(lm, "model", None) or lm.base_model
        elif label == "pythia":
            backbone = getattr(lm, "gpt_neox", None) or lm.base_model
        else:
            backbone = getattr(lm, "transformer", None) or lm.base_model
        hidden = int(getattr(backbone.config, "hidden_size"))
        bundles[label] = BackboneBundle(
            label=label,
            model_name=model_name,
            lm=lm,
            backbone=backbone,
            tokenizer=tokenizer,
            output_weight=output.weight,
            hidden_size=hidden,
            max_positions=_max_positions(backbone.config),
        )
        logger.emit(
            "clef_sized_backbone_load_complete",
            label=label,
            model=model_name,
            hidden_size=hidden,
            parameters=sum(int(p.numel()) for p in lm.parameters()),
            max_positions=bundles[label].max_positions,
            memory=cuda_memory(torch, f"after_{label}_load"),
        )
    return bundles, frozen_total


class ModelProjection:
    """Namespace helper; real implementation is returned by build_head_class."""


def build_head_class():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class EvidenceRoutingLayer(nn.Module):
        def __init__(self, width: int, heads: int, feedforward: int):
            super().__init__()
            self.query_norm = nn.LayerNorm(width)
            self.memory_norm = nn.LayerNorm(width)
            self.attention = nn.MultiheadAttention(width, heads, dropout=0.0, batch_first=True)
            self.ff_norm = nn.LayerNorm(width)
            self.ff = nn.Sequential(
                nn.Linear(width, feedforward), nn.GELU(), nn.Linear(feedforward, width)
            )

        def forward(self, queries, memory):
            q = self.query_norm(queries)
            m = self.memory_norm(memory)
            routed, _ = self.attention(q, m, m, need_weights=False)
            queries = queries + routed
            return queries + self.ff(self.ff_norm(queries))

    class PerBackboneProjection(nn.Module):
        def __init__(self, hidden: int, width: int):
            super().__init__()
            self.hidden_norm = nn.LayerNorm(hidden)
            self.memory_projection = nn.Linear(hidden, width, bias=False)
            self.question_projection = nn.Linear(hidden, width, bias=False)
            self.option_question_projection = nn.Linear(hidden, width, bias=False)
            self.global_projection = nn.Linear(hidden, width, bias=False)
            self.option_context_projection = nn.Linear(hidden, width, bias=False)
            self.option_lexical_projection = nn.Linear(hidden, width, bias=False)
            self.option_logp_projection = nn.Linear(1, width, bias=False)
            self.option_logp_scalar = nn.Linear(1, 1, bias=False)

    class ThreeBackboneClefHead(nn.Module):
        def __init__(
            self,
            hidden_sizes: dict[str, int],
            width: int = 1024,
            routing_layers: int = 2,
            layers: int = 4,
            heads: int = 16,
            feedforward: int = 4096,
            fusion_feedforward: int = 5120,
        ):
            super().__init__()
            self.width = int(width)
            self.labels = tuple(hidden_sizes)
            self.backbone_modules = nn.ModuleDict({
                label: PerBackboneProjection(int(hidden), width)
                for label, hidden in hidden_sizes.items()
            })
            self.model_embeddings = nn.ParameterDict({
                label: nn.Parameter(torch.zeros(width)) for label in hidden_sizes
            })
            for parameter in self.model_embeddings.values():
                nn.init.normal_(parameter, mean=0.0, std=0.02)
            self.evidence_layers = nn.ModuleList([
                EvidenceRoutingLayer(width, heads, feedforward)
                for _ in range(routing_layers)
            ])
            self.option_summary_norm = nn.LayerNorm(width)
            self.type_embedding = nn.Embedding(3, width)
            self.layers = nn.ModuleList([
                nn.TransformerDecoderLayer(
                    d_model=width,
                    nhead=heads,
                    dim_feedforward=feedforward,
                    dropout=0.0,
                    activation="gelu",
                    batch_first=True,
                    norm_first=True,
                )
                for _ in range(layers)
            ])
            self.fusion_norm = nn.LayerNorm(width)
            self.fusion_ff = nn.Sequential(
                nn.Linear(width, fusion_feedforward),
                nn.GELU(),
                nn.Linear(fusion_feedforward, width),
            )
            self.field_norm = nn.LayerNorm(width)
            self.option_norm = nn.LayerNorm(width)
            self.residual_scorer = nn.Sequential(
                nn.Linear(width * 4, width), nn.GELU(), nn.Linear(width, 1)
            )
            self.prior_logit_scale = nn.Parameter(torch.zeros(()))
            self.joint_logit_scale = nn.Parameter(torch.zeros(()))
            self.residual_gate = nn.Parameter(torch.zeros(()))

        def forward(self, evidence: dict[str, dict[str, Any]]):
            if set(evidence) != set(self.labels):
                raise RuntimeError(
                    f"head evidence labels mismatch: expected={self.labels} observed={tuple(evidence)}"
                )
            option_count = None
            memory_parts = []
            option_query_parts = []
            field_parts = []
            global_parts = []
            lexical_parts = []
            logp_prior_parts = []
            for label in self.labels:
                row = evidence[label]
                module = self.backbone_modules[label]
                memory = module.hidden_norm(row["memory"])
                option_context = module.hidden_norm(row["option_context"])
                option_predictor = module.hidden_norm(row["option_predictor"])
                option_terminal = module.hidden_norm(row["option_terminal"])
                option_question = module.hidden_norm(row["option_question"])
                lexical = module.hidden_norm(row["option_lexical"])
                global_vector = module.hidden_norm(row["global"])
                option_logp = row["option_logp"].to(
                    device=option_context.device, dtype=option_context.dtype
                ).reshape(-1, 1)
                # Absolute LM calibration differs by backbone and question length.
                # Candidate selection needs the within-question continuation
                # evidence, so center each model's logP before projecting it.
                centered_logp = option_logp - option_logp.mean(dim=0, keepdim=True)
                if option_count is None:
                    option_count = int(option_context.shape[0])
                elif option_count != int(option_context.shape[0]):
                    raise RuntimeError("backbone evidence disagrees on candidate count")
                embed = self.model_embeddings[label]
                memory_parts.append(module.memory_projection(memory) + embed)
                option_query_parts.append(
                    module.option_context_projection(
                        (option_context + option_predictor + option_terminal)
                        / math.sqrt(3.0)
                    )
                    + module.option_lexical_projection(lexical)
                    + module.option_question_projection(option_question)
                    + module.option_logp_projection(centered_logp)
                )
                field_parts.append(module.question_projection(option_question.mean(dim=0)))
                global_parts.append(module.global_projection(global_vector))
                lexical_parts.append(module.option_lexical_projection(lexical))
                logp_prior_parts.append(module.option_logp_scalar(centered_logp).squeeze(-1))

            scale = 1.0 / math.sqrt(float(len(self.labels)))
            memory = torch.cat(memory_parts, dim=0).unsqueeze(0)
            options = torch.stack(option_query_parts, dim=0).sum(dim=0) * scale
            lexical = torch.stack(lexical_parts, dim=0).sum(dim=0) * scale
            logp_prior = torch.stack(logp_prior_parts, dim=0).sum(dim=0) * scale
            base_field = torch.stack(field_parts, dim=0).sum(dim=0) * scale
            global_vector = torch.stack(global_parts, dim=0).sum(dim=0) * scale

            routed = options.unsqueeze(0)
            for layer in self.evidence_layers:
                routed = layer(routed, memory)
            routed = routed[0]
            routing_weights = torch.softmax(
                torch.matmul(routed, base_field) / math.sqrt(float(self.width)), dim=0
            )
            option_summary = torch.sum(routing_weights.unsqueeze(-1) * routed, dim=0)
            field = base_field + self.option_summary_norm(option_summary) + global_vector
            # All NanoJev object questions are ordinary choices; keep Clef's type
            # channel but do not expose the NanoJev task identity.
            field = field + self.type_embedding.weight[1]
            field = field + self.fusion_ff(self.fusion_norm(field))
            target = field.view(1, 1, -1)
            for layer in self.layers:
                target = layer(target, memory)
            field = self.field_norm(target[0, 0])

            lexical_prior = F.cosine_similarity(
                F.normalize(lexical, dim=-1),
                F.normalize(field.unsqueeze(0).expand_as(lexical), dim=-1),
                dim=-1,
            )
            prior_scale = self.prior_logit_scale.clamp(max=math.log(100.0)).exp()
            option_values = self.option_norm(routed)
            repeated_field = field.unsqueeze(0).expand_as(option_values)
            cosine = F.cosine_similarity(repeated_field, option_values, dim=-1)
            features = torch.cat(
                [
                    repeated_field,
                    option_values,
                    repeated_field * option_values,
                    torch.abs(repeated_field - option_values),
                ],
                dim=-1,
            )
            residual = self.residual_scorer(features).squeeze(-1)
            joint_scale = self.joint_logit_scale.clamp(max=math.log(100.0)).exp()
            joint = joint_scale * cosine + residual
            return (
                prior_scale * lexical_prior
                + logp_prior
                + torch.sigmoid(self.residual_gate) * joint
            )

    return ThreeBackboneClefHead


def count_parameters(module) -> int:
    return sum(int(parameter.numel()) for parameter in module.parameters())


def production_head_parameter_count() -> int:
    import torch
    Head = build_head_class()
    # Meta construction verifies the real module topology without allocating 0.5 GB
    # of CPU float32 weights merely to answer --self-test/--architecture-only.
    with torch.device("meta"):
        head = Head({"qwen": 1024, "pythia": 512, "tinystories": 768})
    return count_parameters(head)


def load_shared_clef_initialization(head, *, local_files_only: bool, logger: EventLog) -> dict[str, Any]:
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file

    logger.emit(
        "clef_sized_shared_init_download_start",
        repo_id=CLEF_REPO,
        revision=CLEF_REVISION,
        filename="joint_head.safetensors",
    )
    path = Path(hf_hub_download(
        repo_id=CLEF_REPO,
        filename="joint_head.safetensors",
        revision=CLEF_REVISION,
        local_files_only=local_files_only,
    ))
    observed = sha256_file(path)
    if observed != CLEF_HEAD_SHA256:
        raise RuntimeError(
            f"Clef-Flash head SHA mismatch: expected={CLEF_HEAD_SHA256} observed={observed}"
        )
    released = load_file(str(path), device="cpu")
    own = head.state_dict()
    compatible_prefixes = (
        "evidence_layers.",
        "option_summary_norm.",
        "type_embedding.",
        "layers.",
        "field_norm.",
        "option_norm.",
        "residual_scorer.",
        "prior_logit_scale",
        "joint_logit_scale",
        "residual_gate",
    )
    copied = 0
    copied_tensors = 0
    with __import__("torch").no_grad():
        for name, tensor in released.items():
            if not name.startswith(compatible_prefixes):
                continue
            target = own.get(name)
            if target is None or tuple(target.shape) != tuple(tensor.shape):
                continue
            target.copy_(tensor.to(dtype=target.dtype))
            copied += int(target.numel())
            copied_tensors += 1
    logger.emit(
        "clef_sized_shared_init_complete",
        path=str(path),
        sha256=observed,
        copied_parameters=copied,
        copied_tensors=copied_tensors,
        head_parameters=count_parameters(head),
        copied_fraction=copied / max(1, count_parameters(head)),
    )
    return {
        "path": str(path),
        "sha256": observed,
        "copied_parameters": copied,
        "copied_tensors": copied_tensors,
    }


def clone_sqlite(source: Path, target: Path) -> None:
    import sqlite3
    source = Path(source).expanduser().resolve(strict=True)
    target = Path(target)
    target.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def build_smoke_questions(*, experiment_dir: Path, output_dir: Path, seed: int, cycle: int, logger: EventLog):
    from transformers import AutoTokenizer

    broad = load_local_module("clef_smoke_broad", TOOLS / "nanojev_three_backbone_latent_top2_broad_curriculum_train.py")
    curriculum = load_local_module("clef_smoke_consensus", TOOLS / "nanojev_three_backbone_consensus_train.py")
    smoke = load_local_module("clef_smoke_objective", TOOLS / "nanojev_three_backbone_objective_smoke.py")
    direct = load_local_module("clef_smoke_direct", TOOLS / "nanojev_three_backbone_latent_top2_cutover.py")
    dictionary_curriculum = load_local_module(
        "clef_smoke_dictionary", TOOLS / "nanojev_dictionary_definition_curriculum_train.py"
    )
    triad_curriculum = load_local_module("clef_smoke_triad", TOOLS / "nanojev_triad_curriculum_train.py")
    data = load_local_module("clef_smoke_data", TOOLS / "nanojev_code_lexeme_data.py")
    mutation = load_local_module("clef_smoke_mutation", TOOLS / "nanojev_code_mutation_train.py")
    source_sampler = load_local_module(
        "clef_smoke_sampler", TOOLS / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py"
    )
    ordered_api = load_local_module(
        "clef_smoke_ordered", TOOLS / "nanojev_frozen_qwen_ordered_signal_smoke.py"
    )
    objective_api = load_local_module("clef_smoke_objective_api", TOOLS / "nanojev_objective_api.py")
    store_module = load_local_module("clef_smoke_store", TOOLS / "nanojev_dictionary_code_store.py")

    manifest = read_json(experiment_dir / "experiment.json")
    source = dict(manifest.get("source") or {})
    required = ("model", "revision", "legacy_experiment", "repo_root")
    missing = [key for key in required if not source.get(key)]
    if missing:
        raise RuntimeError(f"broad experiment source lineage is missing: {missing}")
    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    qwen_tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_dir), local_files_only=True, trust_remote_code=False
    )
    if qwen_tokenizer.pad_token_id is None:
        qwen_tokenizer.pad_token = qwen_tokenizer.eos_token
    train_manifest = read_json(Path(str(legacy_meta["manifests"]["train"])).expanduser().resolve(strict=True))
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)
    live_db = experiment_dir / "lexical.db"
    if not live_db.is_file():
        source_db = source.get("source_database")
        if not source_db:
            raise RuntimeError(f"broad experiment has no lexical.db and source has no source_database: {experiment_dir}")
        live_db = Path(str(source_db)).expanduser().resolve(strict=True)
    scratch_db = output_dir / "scratch_lexical.db"
    clone_sqlite(live_db, scratch_db)
    logger.emit(
        "clef_sized_question_source_ready",
        experiment_dir=str(experiment_dir),
        source_model=source["model"],
        source_revision=source["revision"],
        live_db=str(live_db),
        scratch_db=str(scratch_db),
        plan=GENERATION_PLAN,
    )

    with store_module.DictionaryCodeStore(scratch_db) as store:
        objectives = [
            broad.ReintroducedCodeObjective(
                name="legacy", direct=direct, source_sampler=source_sampler, data=data,
                mutation=mutation, ordered_api=ordered_api, tokenizer=qwen_tokenizer,
                repo_root=repo_root, train_manifest=train_manifest, legacy_meta=legacy_meta,
                train_files_per_cycle=40, max_code_tokens=96,
                max_prompt_tokens=768, max_answer_tokens=128, seed=seed,
            ),
            broad.ReintroducedCodeObjective(
                name="mutation", direct=direct, source_sampler=source_sampler, data=data,
                mutation=mutation, ordered_api=ordered_api, tokenizer=qwen_tokenizer,
                repo_root=repo_root, train_manifest=train_manifest, legacy_meta=legacy_meta,
                train_files_per_cycle=40, max_code_tokens=96,
                max_prompt_tokens=768, max_answer_tokens=128, seed=seed,
            ),
            broad.ReintroducedCodeObjective(
                name="ast", direct=direct, source_sampler=source_sampler, data=data,
                mutation=mutation, ordered_api=ordered_api, tokenizer=qwen_tokenizer,
                repo_root=repo_root, train_manifest=train_manifest, legacy_meta=legacy_meta,
                train_files_per_cycle=40, max_code_tokens=96,
                max_prompt_tokens=768, max_answer_tokens=128, seed=seed,
            ),
            curriculum.ConsensusObjective(
                direct=direct, source_sampler=source_sampler, data=data, mutation=mutation,
                ordered_api=ordered_api, tokenizer=qwen_tokenizer, repo_root=repo_root,
                train_manifest=train_manifest, max_length=int(legacy_meta["max_length"]),
                max_prompt_tokens=768, max_answer_tokens=128,
                train_files_per_cycle=40, max_code_tokens=128, seed=seed,
            ),
            triad_curriculum.TriadObjective(
                direct=direct, source_sampler=source_sampler, data=data, mutation=mutation,
                ordered_api=ordered_api, tokenizer=qwen_tokenizer, repo_root=repo_root,
                train_manifest=train_manifest, max_length=int(legacy_meta["max_length"]),
                max_prompt_tokens=768, max_answer_tokens=128,
                train_files_per_cycle=40, max_code_tokens=128, seed=seed,
            ),
            dictionary_curriculum.DictionaryDefinitionObjective(store),
            smoke.EnglishCodeObjective(store, repo_root),
        ]
        registry = objective_api.ObjectiveRegistry(objectives)
        generated = [
            compose_relational_question_v2(question)
            for question in registry.generate_train(
                dict(GENERATION_PLAN),
                cycle=int(cycle),
                rng=random.Random(int(seed)),
            )
        ]
        by_task: dict[str, list[Any]] = defaultdict(list)
        for question in generated:
            by_task[str(question.task)].append(question)
        missing_tasks = [task for task in TASKS if not by_task.get(task)]
        if missing_tasks:
            raise RuntimeError(f"smoke generation lost tasks: {missing_tasks}")
        # One representative per task keeps the live training smoke bounded while
        # still exercising every current primary objective.
        selected = []
        for task in TASKS:
            pool = list(by_task[task])
            pick_rng = random.Random(int.from_bytes(hashlib.sha256(f"{seed}:{cycle}:{task}".encode()).digest()[:8], "big"))
            pick_rng.shuffle(pool)
            selected.append(pool[0])

        def qdict(q):
            return {
                "question_id": q.question_id,
                "task": q.task,
                "stratum": getattr(q, "stratum", ""),
                "gold_index": int(q.gold_index),
                "candidate_ids": [c.candidate_id for c in q.candidates],
                "path_counts": [len(canonical_candidate_paths(q, c)) for c in q.candidates],
            }

        atomic_json(output_dir / "questions.json", {
            "generation_plan": GENERATION_PLAN,
            "generated_counts": {task: len(by_task[task]) for task in TASKS},
            "selected": [qdict(q) for q in selected],
        })
        logger.emit(
            "clef_sized_questions_selected",
            generated_counts={task: len(by_task[task]) for task in TASKS},
            selected=[qdict(q) for q in selected],
        )
    return source, selected


def _model_sequences(bundle: BackboneBundle, question, *, max_prompt_tokens: int, max_answer_tokens: int):
    """Encode and intern exact object paths while preserving semantic occurrences.

    Composition-v2 preserves the complete answer in every backbone's native token
    space.  Qwen's source objective already enforces the requested answer budget;
    auxiliary tokenizers may expand the same text beyond that count, so truncating
    them would silently change the candidate.  Prompt context is suffix-oriented,
    matching the mature three-backbone logP path.
    """
    rows: list[dict[str, Any]] = []
    row_by_key: dict[tuple[tuple[int, ...], tuple[int, ...]], int] = {}
    occurrence_count = 0
    for candidate_index, candidate in enumerate(question.candidates):
        for path_index, path in enumerate(canonical_candidate_paths(question, candidate)):
            prompt_ids = list(bundle.tokenizer.encode(path.prompt, add_special_tokens=False))
            answer_ids = list(bundle.tokenizer.encode(path.answer, add_special_tokens=False))
            if not answer_ids:
                raise RuntimeError(
                    f"{bundle.label} produced empty answer tokens: "
                    f"{question.question_id}/{candidate_index}/{path_index}"
                )
            if bundle.label == "qwen" and len(answer_ids) > int(max_answer_tokens):
                raise RuntimeError(
                    f"qwen answer exceeds max_answer_tokens={max_answer_tokens}: "
                    f"{question.question_id}/{candidate_index}/{path_index} tokens={len(answer_ids)}"
                )
            if len(answer_ids) >= bundle.max_positions:
                raise RuntimeError(
                    f"{bundle.label} answer cannot fit model context: "
                    f"{question.question_id} answer={len(answer_ids)} max={bundle.max_positions}"
                )
            prompt_budget = min(
                int(max_prompt_tokens),
                int(bundle.max_positions) - len(answer_ids),
            )
            if prompt_budget <= 0:
                raise RuntimeError(
                    f"{bundle.label} has no prompt room after preserving the answer: "
                    f"{question.question_id} answer={len(answer_ids)}"
                )
            prompt_ids = prompt_ids[-prompt_budget:]
            if not prompt_ids:
                raise RuntimeError(
                    f"{bundle.label} produced empty bounded prompt: "
                    f"{question.question_id}/{candidate_index}/{path_index}"
                )
            key = (tuple(prompt_ids), tuple(answer_ids))
            row_index = row_by_key.get(key)
            if row_index is None:
                row_index = len(rows)
                row_by_key[key] = row_index
                rows.append({
                    "candidates": [candidate_index],
                    "prompt_ids": prompt_ids,
                    "answer_ids": answer_ids,
                })
            else:
                rows[row_index]["candidates"].append(candidate_index)
            occurrence_count += 1
    if not rows:
        raise RuntimeError(f"question has no encoded paths: {question.question_id}")
    return rows, occurrence_count


def _continuation_mean_logp(*, torch, hidden, tokens, prompt_length: int,
                            answer_length: int, output_weight):
    import torch.nn.functional as F

    start = int(prompt_length)
    count = int(answer_length)
    if start <= 0 or count <= 0:
        raise RuntimeError("continuation logP requires nonempty prompt and answer")
    predictors = hidden[start - 1 : start + count - 1]
    targets = tokens[start : start + count]
    if predictors.shape[0] != count or targets.shape[0] != count:
        raise RuntimeError("continuation logP span accounting mismatch")
    logits = F.linear(predictors, output_weight).float()
    selected = F.log_softmax(logits, dim=-1).gather(
        1, targets.long().unsqueeze(1)
    ).squeeze(1)
    return selected.mean(), predictors.mean(dim=0)


def extract_bundle_evidence(
    *, torch, bundle: BackboneBundle, question, path_batch: int,
    max_prompt_tokens: int, max_answer_tokens: int,
    prompt_evidence_tokens: int, answer_evidence_tokens: int,
    track_grad: bool = False,
):
    rows, occurrence_count = _model_sequences(
        bundle, question,
        max_prompt_tokens=max_prompt_tokens,
        max_answer_tokens=max_answer_tokens,
    )
    candidate_count = len(question.candidates)
    memory_parts = []
    option_answer: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_predictor: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_terminal: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_prompt: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_lexical: list[list[Any]] = [[] for _ in range(candidate_count)]
    option_logp: list[list[Any]] = [[] for _ in range(candidate_count)]
    device = bundle.output_weight.device
    pad = int(bundle.tokenizer.pad_token_id)

    for offset in range(0, len(rows), path_batch):
        chunk = rows[offset: offset + path_batch]
        lengths = [len(row["prompt_ids"]) + len(row["answer_ids"]) for row in chunk]
        width = max(lengths)
        tokens = torch.full((len(chunk), width), pad, dtype=torch.long, device=device)
        attention = torch.zeros((len(chunk), width), dtype=torch.long, device=device)
        for index, row in enumerate(chunk):
            seq = row["prompt_ids"] + row["answer_ids"]
            tokens[index, :len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
            attention[index, :len(seq)] = 1
        from contextlib import nullcontext
        grad_context = nullcontext() if track_grad else torch.no_grad()
        with grad_context:
            output = bundle.backbone(
                input_ids=tokens,
                attention_mask=attention,
                use_cache=False,
                return_dict=True,
            )
            hidden = output.last_hidden_state

            def preserve(value):
                return value if track_grad else value.detach()

            for index, row in enumerate(chunk):
                plen = len(row["prompt_ids"])
                alen = len(row["answer_ids"])
                prompt_idx = balanced_indices(0, plen, prompt_evidence_tokens)
                answer_idx = balanced_indices(plen, plen + alen, answer_evidence_tokens)
                evidence_idx = prompt_idx + answer_idx
                memory_parts.append(preserve(hidden[index, evidence_idx]))
                path_logp, predictor_mean = _continuation_mean_logp(
                    torch=torch,
                    hidden=hidden[index],
                    tokens=tokens[index],
                    prompt_length=plen,
                    answer_length=alen,
                    output_weight=bundle.output_weight,
                )
                prompt_mean = preserve(hidden[index, :plen].mean(dim=0))
                answer_mean = preserve(hidden[index, plen:plen + alen].mean(dim=0))
                terminal = preserve(hidden[index, plen + alen - 1])
                answer_token_ids = tokens[index, plen:plen + alen]
                lexical = preserve(bundle.output_weight[answer_token_ids].mean(dim=0))
                for candidate in row["candidates"]:
                    option_prompt[candidate].append(prompt_mean)
                    option_answer[candidate].append(answer_mean)
                    option_predictor[candidate].append(preserve(predictor_mean))
                    option_terminal[candidate].append(terminal)
                    option_lexical[candidate].append(lexical)
                    option_logp[candidate].append(preserve(path_logp))
        del output, hidden, tokens, attention

    def stack_mean(groups, label: str):
        values = []
        for candidate, items in enumerate(groups):
            if not items:
                raise RuntimeError(f"{bundle.label} missing {label} evidence for candidate {candidate}")
            values.append(torch.stack(items, dim=0).mean(dim=0))
        return torch.stack(values, dim=0)

    memory = torch.cat(memory_parts, dim=0)
    return {
        "memory": memory,
        "option_context": stack_mean(option_answer, "answer"),
        "option_predictor": stack_mean(option_predictor, "predictor"),
        "option_terminal": stack_mean(option_terminal, "terminal"),
        "option_question": stack_mean(option_prompt, "prompt"),
        "option_lexical": stack_mean(option_lexical, "lexical"),
        "option_logp": stack_mean(option_logp, "logp"),
        "global": memory.mean(dim=0),
        "path_count": occurrence_count,
        "unique_path_count": len(rows),
        "memory_tokens": int(memory.shape[0]),
    }


def extract_live_evidence(*, torch, bundles: dict[str, BackboneBundle], question, args, logger: EventLog):
    evidence: dict[str, dict[str, Any]] = {}
    stats = {}
    for label in ("qwen", "pythia", "tinystories"):
        row = extract_bundle_evidence(
            torch=torch,
            bundle=bundles[label],
            question=question,
            path_batch=args.path_batch,
            max_prompt_tokens=args.max_prompt_tokens,
            max_answer_tokens=args.max_answer_tokens,
            prompt_evidence_tokens=args.prompt_evidence_tokens,
            answer_evidence_tokens=args.answer_evidence_tokens,
        )
        stats[label] = {
            "path_count": row.pop("path_count"),
            "unique_path_count": row.pop("unique_path_count"),
            "memory_tokens": row.pop("memory_tokens"),
        }
        evidence[label] = row
    logger.emit(
        "clef_sized_live_evidence",
        question_id=question.question_id,
        task=question.task,
        candidates=len(question.candidates),
        backbone_stats=stats,
        memory=cuda_memory(torch, "after_live_backbones"),
    )
    return evidence


def loss_parts(torch, logits, gold_index: int):
    import torch.nn.functional as F
    target = torch.tensor([int(gold_index)], dtype=torch.long, device=logits.device)
    ce = F.cross_entropy(logits.unsqueeze(0).float(), target, label_smoothing=0.05)
    probs = logits.float().softmax(dim=-1)
    one_hot = F.one_hot(target, num_classes=logits.numel()).float()[0]
    brier = torch.mean((probs - one_hot).square())
    return ce + 0.25 * brier, ce, brier


def evaluate_questions(*, torch, head, bundles, questions, args, logger: EventLog, label: str):
    head.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        evidence = extract_live_evidence(
            torch=torch, bundles=bundles, question=question, args=args, logger=logger
        )
        with torch.no_grad():
            logits = head(evidence)
            loss, ce, brier = loss_parts(torch, logits, question.gold_index)
            pred = int(logits.argmax().item())
        row = {
            "task": str(question.task),
            "question_id": str(question.question_id),
            "loss": float(loss.item()),
            "cross_entropy": float(ce.item()),
            "brier": float(brier.item()),
            "correct": bool(pred == int(question.gold_index)),
            "predicted_index": pred,
            "gold_index": int(question.gold_index),
            "probabilities": logits.float().softmax(-1).cpu().tolist(),
        }
        rows.append(row)
        logger.emit("clef_sized_eval_question", phase=label, index=index, total=len(questions), **row)
    mean_loss = sum(row["loss"] for row in rows) / len(rows)
    accuracy = sum(int(row["correct"]) for row in rows) / len(rows)
    return {"mean_loss": mean_loss, "accuracy": accuracy, "rows": rows}


def deterministic_sample_indices(numel: int, count: int) -> list[int]:
    numel = int(numel)
    count = int(count)
    if numel <= 0:
        raise ValueError("numel must be positive")
    if count <= 0:
        raise ValueError("count must be positive")
    if count >= numel:
        return list(range(numel))
    if count == 1:
        return [0]
    # Pure Python integer arithmetic is intentional here.  torch.linspace()
    # defaults to float32, which cannot represent every integer above 2**24;
    # on large model tensors its endpoint can round from numel-1 up to numel
    # and trigger a CUDA device-side out-of-bounds assertion.
    last = numel - 1
    denominator = count - 1
    return [(index * last) // denominator for index in range(count)]


def sampled_parameter_signature(parameter, count: int = 4096):
    import torch
    flat = parameter.detach().float().reshape(-1)
    if flat.numel() <= count:
        sample = flat.cpu().clone()
    else:
        indices = deterministic_sample_indices(flat.numel(), count)
        idx = torch.tensor(indices, dtype=torch.long, device=flat.device)
        sample = flat.index_select(0, idx).cpu().clone()
    return sample


def frozen_signatures(bundles: dict[str, BackboneBundle]):
    signatures = {}
    for label, bundle in bundles.items():
        name, param = next(iter(bundle.lm.named_parameters()))
        signatures[label] = {"name": name, "sample": sampled_parameter_signature(param, 2048)}
    return signatures


def verify_frozen_unchanged(bundles, before) -> dict[str, Any]:
    import torch
    result = {}
    for label, bundle in bundles.items():
        name, param = next(iter(bundle.lm.named_parameters()))
        after = sampled_parameter_signature(param, len(before[label]["sample"]))
        result[label] = {
            "parameter": name,
            "unchanged": bool(torch.equal(before[label]["sample"], after)),
            "requires_grad_any": any(bool(p.requires_grad) for p in bundle.lm.parameters()),
            "grad_present_any": any(p.grad is not None for p in bundle.lm.parameters()),
        }
    return result


def run(args, logger: EventLog) -> None:
    import torch

    logger.set_stage("question_generation")
    source, questions = build_smoke_questions(
        experiment_dir=Path(args.experiment_dir).expanduser().resolve(strict=True),
        output_dir=logger.output_dir,
        seed=args.seed,
        cycle=args.cycle,
        logger=logger,
    )

    logger.set_stage("backbone_load")
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    torch.cuda.reset_peak_memory_stats()
    bundles, frozen_total = load_backbones(
        source=source,
        local_files_only=args.local_files_only,
        logger=logger,
    )
    hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
    expected = {"qwen": 1024, "pythia": 512, "tinystories": 768}
    if hidden_sizes != expected:
        raise RuntimeError(f"unexpected backbone hidden sizes: expected={expected} observed={hidden_sizes}")

    logger.set_stage("frozen_signature")
    logger.emit(
        "clef_sized_frozen_signature_start",
        models=list(bundles),
        samples_per_model=2048,
        memory=cuda_memory(torch, "before_frozen_signature"),
    )
    frozen_before = frozen_signatures(bundles)
    logger.emit(
        "clef_sized_frozen_signature_complete",
        signatures={label: {"parameter": row["name"], "samples": len(row["sample"])}
                    for label, row in frozen_before.items()},
        memory=cuda_memory(torch, "after_frozen_signature"),
    )

    logger.set_stage("head_build")
    Head = build_head_class()
    torch.manual_seed(args.seed + 17)
    head = Head(hidden_sizes)
    head_params = count_parameters(head)
    if not 118_000_000 <= head_params <= 124_000_000:
        raise RuntimeError(f"Clef-sized head parameter count escaped target band: {head_params}")
    init = None
    if not args.no_clef_shared_init:
        init = load_shared_clef_initialization(
            head,
            local_files_only=args.local_files_only,
            logger=logger,
        )
    head = head.to(device="cuda", dtype=torch.bfloat16)
    head.train()
    logger.emit(
        "clef_sized_head_ready",
        parameters=head_params,
        clef_flash_released_head_parameters=CLEF_RELEASED_HEAD_PARAMS,
        ratio_to_clef_flash=head_params / CLEF_RELEASED_HEAD_PARAMS,
        hidden_sizes=hidden_sizes,
        shared_initialization=init,
        memory=cuda_memory(torch, "after_head_load"),
    )

    optimizer = torch.optim.AdamW(
        head.parameters(),
        lr=float(args.head_lr),
        weight_decay=float(args.weight_decay),
        foreach=False,
    )
    tracked = head.backbone_modules["qwen"].memory_projection.weight
    tracked_before = sampled_parameter_signature(tracked)

    logger.set_stage("baseline_eval")
    baseline = evaluate_questions(
        torch=torch, head=head, bundles=bundles, questions=questions,
        args=args, logger=logger, label="baseline",
    )
    atomic_json(logger.output_dir / "result.partial.json", {
        "status": "baseline_complete",
        "head_parameters": head_params,
        "frozen_parameters": frozen_total,
        "baseline": baseline,
    })

    logger.set_stage("training")
    train_order = []
    for epoch in range(args.epochs):
        order = list(range(len(questions)))
        random.Random(args.seed + epoch * 1009).shuffle(order)
        train_order.extend((epoch, index) for index in order)

    step_rows = []
    max_grad_norm = 0.0
    for step, (epoch, question_index) in enumerate(train_order, 1):
        question = questions[question_index]
        torch.cuda.reset_peak_memory_stats()
        logger.emit(
            "clef_sized_train_step_start",
            step=step,
            total_steps=len(train_order),
            epoch=epoch + 1,
            task=question.task,
            question_id=question.question_id,
        )
        evidence = extract_live_evidence(
            torch=torch, bundles=bundles, question=question, args=args, logger=logger
        )
        optimizer.zero_grad(set_to_none=True)
        head.train()
        logits = head(evidence)
        loss, ce, brier = loss_parts(torch, logits, question.gold_index)
        if not bool(torch.isfinite(loss).item()):
            raise RuntimeError(f"non-finite loss at step {step}: {float(loss.item())}")
        loss.backward()
        grad_norm = float(torch.nn.utils.clip_grad_norm_(head.parameters(), float(args.grad_clip)).item())
        if not math.isfinite(grad_norm):
            raise RuntimeError(f"non-finite gradient norm at step {step}: {grad_norm}")
        max_grad_norm = max(max_grad_norm, grad_norm)
        optimizer.step()
        pred = int(logits.detach().argmax().item())
        row = {
            "step": step,
            "total_steps": len(train_order),
            "epoch": epoch + 1,
            "task": str(question.task),
            "question_id": str(question.question_id),
            "loss": float(loss.item()),
            "cross_entropy": float(ce.item()),
            "brier": float(brier.item()),
            "grad_norm_preclip": grad_norm,
            "correct": bool(pred == int(question.gold_index)),
            "memory": cuda_memory(torch, f"after_step_{step}"),
        }
        step_rows.append(row)
        logger.emit("clef_sized_train_step_complete", **row)
        atomic_json(logger.output_dir / "result.partial.json", {
            "status": "training",
            "head_parameters": head_params,
            "frozen_parameters": frozen_total,
            "baseline": baseline,
            "completed_steps": step,
            "total_steps": len(train_order),
            "last_step": row,
        })

    logger.set_stage("final_eval")
    final = evaluate_questions(
        torch=torch, head=head, bundles=bundles, questions=questions,
        args=args, logger=logger, label="final",
    )
    tracked_after = sampled_parameter_signature(tracked)
    tracked_delta = float((tracked_after - tracked_before).abs().max().item())
    frozen = verify_frozen_unchanged(bundles, frozen_before)
    frozen_ok = all(
        row["unchanged"] and not row["requires_grad_any"] and not row["grad_present_any"]
        for row in frozen.values()
    )
    loss_improved = bool(final["mean_loss"] < baseline["mean_loss"])
    head_changed = bool(tracked_delta > 0.0)
    passed = bool(loss_improved and head_changed and frozen_ok and max_grad_norm > 0.0)

    result = {
        "schema_version": "main-computer-three-backbone-clef-sized-live-train-smoke-v1",
        "status": "pass" if passed else "fail",
        "contract": {
            "backbone_evidence": "recomputed live on every evaluation/training question; no hidden-state cache",
            "frozen_backbones": [bundles[label].model_name for label in ("qwen", "pythia", "tinystories")],
            "primary_tasks": list(TASKS),
            "head_style": "Clef-inspired multi-backbone joint choice head",
            "generalization_claim": False,
        },
        "head": {
            "parameters": head_params,
            "clef_flash_released_head_parameters": CLEF_RELEASED_HEAD_PARAMS,
            "ratio": head_params / CLEF_RELEASED_HEAD_PARAMS,
            "tracked_parameter": "backbone_modules.qwen.memory_projection.weight",
            "tracked_max_abs_delta": tracked_delta,
            "shared_initialization": init,
        },
        "frozen_parameter_count": frozen_total,
        "frozen_verification": frozen,
        "baseline": baseline,
        "training_steps": step_rows,
        "final": final,
        "maximum_grad_norm": max_grad_norm,
        "checks": {
            "loss_improved": loss_improved,
            "head_changed": head_changed,
            "frozen_backbones_unchanged": frozen_ok,
            "nonzero_gradient": max_grad_norm > 0.0,
        },
        "memory": cuda_memory(torch, "complete"),
    }
    atomic_json(logger.output_dir / "result.json", result)
    (logger.output_dir / "result.partial.json").unlink(missing_ok=True)
    logger.set_stage("complete", status=result["status"])
    logger.emit(
        "clef_sized_smoke_pass" if passed else "clef_sized_smoke_fail",
        initial_loss=baseline["mean_loss"],
        final_loss=final["mean_loss"],
        initial_accuracy=baseline["accuracy"],
        final_accuracy=final["accuracy"],
        head_parameters=head_params,
        tracked_max_abs_delta=tracked_delta,
        frozen_ok=frozen_ok,
        maximum_grad_norm=max_grad_norm,
        result=str(logger.output_dir / "result.json"),
    )
    if not passed:
        raise RuntimeError(f"live Clef-sized training smoke failed checks: {result['checks']}")


def self_test() -> dict[str, Any]:
    import torch
    Head = build_head_class()
    count = production_head_parameter_count()
    if not 118_000_000 <= count <= 124_000_000:
        raise AssertionError(f"production head count outside target band: {count}")

    torch.manual_seed(7)
    tiny = Head(
        {"qwen": 16, "pythia": 12, "tinystories": 8},
        width=16,
        routing_layers=1,
        layers=1,
        heads=4,
        feedforward=32,
        fusion_feedforward=24,
    )
    evidence = {}
    for label, hidden in (("qwen", 16), ("pythia", 12), ("tinystories", 8)):
        evidence[label] = {
            "memory": torch.randn(11, hidden),
            "option_context": torch.randn(2, hidden),
            "option_predictor": torch.randn(2, hidden),
            "option_terminal": torch.randn(2, hidden),
            "option_question": torch.randn(2, hidden),
            "option_lexical": torch.randn(2, hidden),
            "option_logp": torch.randn(2),
            "global": torch.randn(hidden),
        }
    optimizer = torch.optim.AdamW(tiny.parameters(), lr=1e-2)
    before = sampled_parameter_signature(tiny.backbone_modules["qwen"].memory_projection.weight, 64)
    initial = None
    final = None
    for _ in range(8):
        optimizer.zero_grad(set_to_none=True)
        logits = tiny(evidence)
        loss, _ce, _brier = loss_parts(torch, logits, 0)
        if initial is None:
            initial = float(loss.item())
        loss.backward()
        optimizer.step()
        final = float(loss.item())
    after = sampled_parameter_signature(tiny.backbone_modules["qwen"].memory_projection.weight, 64)
    if not float(final) < float(initial):
        raise AssertionError(f"tiny head did not learn: initial={initial} final={final}")
    if torch.equal(before, after):
        raise AssertionError("tiny head parameter did not change")
    if balanced_indices(0, 10, 4) != [0, 1, 8, 9]:
        raise AssertionError("balanced evidence selector contract changed")
    return {
        "event": "clef_sized_smoke_self_test_passed",
        "production_head_parameters": count,
        "clef_flash_released_head_parameters": CLEF_RELEASED_HEAD_PARAMS,
        "ratio": count / CLEF_RELEASED_HEAD_PARAMS,
        "tiny_initial_loss": initial,
        "tiny_final_loss": final,
    }


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=str(DEFAULT_EXPERIMENT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--cycle", type=int, default=DEFAULT_CYCLE)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--head-lr", type=float, default=DEFAULT_HEAD_LR)
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--grad-clip", type=float, default=DEFAULT_GRAD_CLIP)
    parser.add_argument("--max-prompt-tokens", type=int, default=DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--path-batch", type=int, default=DEFAULT_PATH_BATCH)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--no-clef-shared-init", action="store_true")
    parser.add_argument("--architecture-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.epochs <= 0:
        parser.error("--epochs must be positive")
    for name in ("max_prompt_tokens", "max_answer_tokens", "prompt_evidence_tokens", "answer_evidence_tokens", "path_batch"):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if not math.isfinite(args.head_lr) or args.head_lr <= 0:
        parser.error("--head-lr must be finite and positive")
    if not math.isfinite(args.grad_clip) or args.grad_clip <= 0:
        parser.error("--grad-clip must be finite and positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True), flush=True)
        return 0
    if args.architecture_only:
        payload = {
            "event": "clef_sized_architecture",
            "parameters": production_head_parameter_count(),
            "clef_flash_released_head_parameters": CLEF_RELEASED_HEAD_PARAMS,
        }
        payload["ratio"] = payload["parameters"] / payload["clef_flash_released_head_parameters"]
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 0

    output_dir = Path(args.output_dir).expanduser()
    progress: EventLog | None = None
    try:
        output_dir = prepare_output(output_dir)
        progress = EventLog(output_dir)
        progress.emit(
            "clef_sized_smoke_start",
            experiment_dir=str(Path(args.experiment_dir).expanduser()),
            output_dir=str(output_dir),
            seed=args.seed,
            cycle=args.cycle,
            epochs=args.epochs,
            tasks=list(TASKS),
            no_hidden_state_cache=True,
        )
        # Exercise the lightweight contracts before spending GPU time.
        st = self_test()
        progress.emit(**st)
        run(args, progress)
        return 0
    except KeyboardInterrupt as exc:
        write_error_report(output_dir, progress, exc)
        return 130
    except Exception as exc:
        write_error_report(output_dir, progress, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
