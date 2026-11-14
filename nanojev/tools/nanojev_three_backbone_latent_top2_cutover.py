#!/usr/bin/env python3
"""Function-preserving cutover to a top-2 latent-routed modular NanoJev head.

The three frozen causal language models remain unchanged feature producers:
  * Qwen/Qwen3-0.6B
  * EleutherAI/pythia-70m
  * roneneldan/TinyStories-33M

The mature three-backbone NanoJev head is copied byte-for-byte and is intended
for freezing during the first modular training phase.  A task-agnostic router
and a bank of low-rank residual experts are inserted on the 2307-wide candidate
feature representation.  The router selects exactly two experts per candidate.
Expert output projections are initialized to zero, so the cutover is exactly
function preserving even though sparse routing is live.

This file is both the cutover CLI and the model provider imported by
``nanojev_three_backbone_latent_top2_train.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "nanojev_three_backbone_base_for_latent_router",
    TOOLS / "nanojev_three_backbone_logp_train.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load three-backbone direct model provider")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)

# Re-export the mature helpers. Existing objective/cache code can consume this
# module anywhere it previously consumed nanojev_three_backbone_logp_train.py.
# Snapshot the exports before updating globals: the source module itself has an
# internal ``_base`` name, and copying that into this module would replace our
# three-backbone provider reference mid-loop with the underlying Qwen provider.
_base_exports = {
    _export_name: getattr(_base, _export_name)
    for _export_name in dir(_base)
    if not _export_name.startswith("__")
    and _export_name not in {"_base", "_spec", "_name"}
}
globals().update(_base_exports)

SCHEMA = "main-computer-nanojev-three-backbone-latent-top2-load-balanced-cutover-v1"
DEFAULT_SOURCE_CUTOVER = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_router_cutover_v1"
)
DEFAULT_OUTPUT_DIR = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_load_balanced_cutover_v1"
)
DEFAULT_NUM_EXPERTS = 8
DEFAULT_EXPERT_RANK = 32
DEFAULT_TOP_K = 2
DEFAULT_ROUTER_INIT_STD = 1e-3
DEFAULT_INIT_SEED = 20261001

INHERITED_HEAD_PREFIXES = tuple(_base.HEAD_PREFIXES)
MODULAR_PREFIXES = ("latent_router.", "latent_experts.")
HEAD_PREFIXES = INHERITED_HEAD_PREFIXES + MODULAR_PREFIXES
THREE_FROZEN_MODELS = (
    "Qwen/Qwen3-0.6B",
    _base.PYTHIA_MODEL,
    _base.TINYSTORIES_MODEL,
)


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tensor_state_sha256(state: dict[str, Any], prefixes: tuple[str, ...]) -> str:
    """Stable checksum of selected tensors including names, dtype, shape, bytes."""
    import torch
    h = hashlib.sha256()
    for name in sorted(k for k in state if k.startswith(prefixes)):
        tensor = state[name].detach().cpu().contiguous()
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(str(tensor.dtype).encode("ascii"))
        h.update(b"\0")
        h.update(json.dumps(list(tensor.shape), separators=(",", ":")).encode("ascii"))
        h.update(b"\0")
        h.update(tensor.view(-1).view(torch.uint8).numpy().tobytes())
        h.update(b"\0")
    return h.hexdigest()


def inherited_head_sha256(model) -> str:
    return tensor_state_sha256(model.state_dict(), INHERITED_HEAD_PREFIXES)


def modular_state_sha256(model) -> str:
    return tensor_state_sha256(model.state_dict(), MODULAR_PREFIXES)


def build_direct_model_class(
    BaseDecisionModel,
    *,
    max_answer_tokens: int,
    num_experts: int = DEFAULT_NUM_EXPERTS,
    expert_rank: int = DEFAULT_EXPERT_RANK,
    top_k: int = DEFAULT_TOP_K,
):
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    if num_experts < 2:
        raise ValueError("latent router requires at least two experts")
    if expert_rank <= 0:
        raise ValueError("expert rank must be positive")
    if top_k <= 0 or top_k > num_experts:
        raise ValueError("top_k must be between 1 and num_experts")

    Base = _base.build_direct_model_class(
        BaseDecisionModel,
        max_answer_tokens=max_answer_tokens,
    )

    class ResidualExpert(nn.Module):
        def __init__(self):
            super().__init__()
            self.down = nn.Linear(TOTAL_FEATURE_WIDTH, expert_rank, bias=True)
            self.up = nn.Linear(expert_rank, TOTAL_FEATURE_WIDTH, bias=True)

        def forward(self, x):
            return torch.tanh(self.up(F.gelu(self.down(x))))

    class LatentRouterDecisionModel(Base):
        def __init__(self, backbone, set_head):
            super().__init__(backbone, set_head)
            self.latent_router = nn.Linear(TOTAL_FEATURE_WIDTH, num_experts, bias=True)
            self.latent_experts = nn.ModuleList([ResidualExpert() for _ in range(num_experts)])
            self.latent_num_experts = int(num_experts)
            self.latent_expert_rank = int(expert_rank)
            self.latent_top_k = int(top_k)
            self._last_router_probabilities = None
            self._last_router_dense_probabilities = None
            self._last_router_topk_indices = None

            # Task-neutral and function preserving at cutover.  Expert down
            # projections may differ, but every expert emits exactly zero until
            # its up projection learns away from zero.
            nn.init.normal_(self.latent_router.weight, mean=0.0, std=DEFAULT_ROUTER_INIT_STD)
            nn.init.zeros_(self.latent_router.bias)
            for expert in self.latent_experts:
                nn.init.kaiming_uniform_(expert.down.weight, a=math.sqrt(5))
                if expert.down.bias is not None:
                    fan_in, _ = nn.init._calculate_fan_in_and_fan_out(expert.down.weight)
                    bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
                    nn.init.uniform_(expert.down.bias, -bound, bound)
                nn.init.zeros_(expert.up.weight)
                nn.init.zeros_(expert.up.bias)

        def _latent_route(self, candidate_features, valid):
            if candidate_features.shape[-1] != TOTAL_FEATURE_WIDTH:
                raise RuntimeError(
                    f"latent-router feature width {candidate_features.shape[-1]} != {TOTAL_FEATURE_WIDTH}"
                )
            # Non-affine normalization means the router receives no task label and
            # adds no hidden trainable path around the expert bank.
            x = F.layer_norm(candidate_features, (TOTAL_FEATURE_WIDTH,))
            logits = self.latent_router(x)
            dense_probabilities = torch.softmax(logits, dim=-1)
            top_logits, top_indices = torch.topk(logits, k=self.latent_top_k, dim=-1)
            top_probabilities = torch.softmax(top_logits, dim=-1)
            probabilities = torch.zeros_like(dense_probabilities).scatter(
                -1, top_indices, top_probabilities
            )
            expert_outputs = torch.stack(
                [expert(x) for expert in self.latent_experts],
                dim=-2,
            )
            residual = (probabilities.unsqueeze(-1) * expert_outputs).sum(dim=-2)
            self._last_router_probabilities = probabilities[valid]
            self._last_router_dense_probabilities = dense_probabilities[valid]
            self._last_router_topk_indices = top_indices[valid]
            return candidate_features + residual

        def _score_dense_candidate_vectors(self, candidate_features, valid):
            routed = self._latent_route(candidate_features, valid)
            return super()._score_dense_candidate_vectors(routed, valid)

        def router_regularization(self):
            p = self._last_router_probabilities
            dense = self._last_router_dense_probabilities
            if p is None or dense is None or p.numel() == 0:
                raise RuntimeError("router regularization requested before a scored batch")

            # Actual sparse post-top-k traffic.  ``selection_rate`` sums to top_k;
            # normalized_load sums to one and is detached because hard top-k
            # membership is not differentiable.  It still supplies the routing
            # load signal that tells the differentiable dense probabilities which
            # experts are over- or under-selected.
            selected = (p > 0).to(dtype=dense.dtype)
            selection_rate = selected.mean(dim=0)
            normalized_load = (selection_rate / float(self.latent_top_k)).detach()

            # Differentiable pre-top-k importance.  The sparse load-balancing term
            # is the Switch-style load x importance objective, normalized so a
            # perfectly uniform router has value 1.  Unlike the previous dense-only
            # penalty, this has a non-zero corrective gradient when hard top-k
            # traffic collapses even if dense probabilities remain nearly uniform.
            dense_importance = dense.mean(dim=0)
            sparse_load_balance = (
                self.latent_num_experts * (normalized_load * dense_importance).sum()
            )

            # Retain a small direct importance-uniformity term so unused logits do
            # not drift arbitrarily.  It is zero at uniform importance and cannot
            # by itself declare a collapsed top-k router healthy.
            dense_importance_balance = (
                self.latent_num_experts * dense_importance.square().sum() - 1.0
            )
            balance = sparse_load_balance + dense_importance_balance

            # Entropy is measured on the actual sparse top-k routing weights.
            entropy = -(p.clamp_min(1e-9) * p.clamp_min(1e-9).log()).sum(dim=-1).mean()
            mean_usage = p.mean(dim=0)

            uniform_load = 1.0 / float(self.latent_num_experts)
            sparse_load_cv2 = (
                (normalized_load - uniform_load).square().mean()
                / (uniform_load * uniform_load)
            )
            dense_importance_cv2 = (
                (dense_importance - uniform_load).square().mean()
                / (uniform_load * uniform_load)
            )
            stats = {
                "selection_rate": selection_rate,
                "normalized_load": normalized_load,
                "dense_importance": dense_importance,
                "sparse_load_balance": sparse_load_balance,
                "dense_importance_balance": dense_importance_balance,
                "sparse_load_cv2": sparse_load_cv2,
                "dense_importance_cv2": dense_importance_cv2,
            }
            return balance, entropy, mean_usage, stats

    LatentRouterDecisionModel.__name__ = "ThreeBackboneLatentRouterDecisionModel"
    return LatentRouterDecisionModel


def checkpoint_head_state(model):
    return {
        key: value.detach().cpu().contiguous()
        for key, value in model.state_dict().items()
        if key.startswith(HEAD_PREFIXES)
    }


def load_own_checkpoint(model, path: Path) -> None:
    from safetensors.torch import load_file

    path = Path(path)
    weights = load_file(str(path / "head.safetensors"), device="cpu")
    expected = {key for key in model.state_dict() if key.startswith(HEAD_PREFIXES)}
    if set(weights) != expected:
        raise RuntimeError(
            "latent-router head mismatch: "
            f"missing={sorted(expected-set(weights))} extra={sorted(set(weights)-expected)}"
        )
    incompatible = model.load_state_dict(weights, strict=False)
    bad_missing = [key for key in incompatible.missing_keys if key.startswith(HEAD_PREFIXES)]
    if bad_missing or incompatible.unexpected_keys:
        raise RuntimeError(
            f"latent-router checkpoint load mismatch: missing={bad_missing} "
            f"unexpected={incompatible.unexpected_keys}"
        )


def _resolve_latest_checkpoint(experiment: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    experiment = Path(experiment).expanduser().resolve(strict=True)
    manifest = read_json(experiment / "experiment.json")
    state = read_json(experiment / "state.json")
    latest = state.get("latest_checkpoint")
    if not latest:
        raise RuntimeError(f"source experiment has no committed checkpoint: {experiment}")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    meta = read_json(checkpoint / "meta.json")
    return checkpoint, manifest, meta


def _new_modular_tensors(*, num_experts: int, expert_rank: int, seed: int):
    import torch

    if num_experts < 2:
        raise ValueError("--num-experts must be at least 2")
    if expert_rank <= 0:
        raise ValueError("--expert-rank must be positive")
    g = torch.Generator(device="cpu")
    g.manual_seed(int(seed))
    router_g = torch.Generator(device="cpu")
    router_g.manual_seed(int(seed) ^ 0x5A17C0DE)
    state: dict[str, torch.Tensor] = {
        # Tiny deterministic asymmetry prevents a top-k tie from permanently
        # selecting the same experts for every candidate.  Use a separate RNG
        # so the expert down-projection initialization stays byte-identical to
        # the dense-router comparison cutover.  Expert outputs are still exactly
        # zero, so cutover behavior remains function preserving.
        "latent_router.weight": torch.empty(
            (num_experts, TOTAL_FEATURE_WIDTH), dtype=torch.float32
        ).normal_(mean=0.0, std=DEFAULT_ROUTER_INIT_STD, generator=router_g),
        "latent_router.bias": torch.zeros((num_experts,), dtype=torch.float32),
    }
    bound_down = 1.0 / math.sqrt(TOTAL_FEATURE_WIDTH)
    for i in range(num_experts):
        state[f"latent_experts.{i}.down.weight"] = torch.empty(
            (expert_rank, TOTAL_FEATURE_WIDTH), dtype=torch.float32
        ).uniform_(-bound_down, bound_down, generator=g)
        state[f"latent_experts.{i}.down.bias"] = torch.empty(
            (expert_rank,), dtype=torch.float32
        ).uniform_(-bound_down, bound_down, generator=g)
        state[f"latent_experts.{i}.up.weight"] = torch.zeros(
            (TOTAL_FEATURE_WIDTH, expert_rank), dtype=torch.float32
        )
        state[f"latent_experts.{i}.up.bias"] = torch.zeros(
            (TOTAL_FEATURE_WIDTH,), dtype=torch.float32
        )
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-cutover", default=DEFAULT_SOURCE_CUTOVER)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--num-experts", type=int, default=DEFAULT_NUM_EXPERTS)
    parser.add_argument("--expert-rank", type=int, default=DEFAULT_EXPERT_RANK)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--seed", type=int, default=DEFAULT_INIT_SEED)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.top_k != DEFAULT_TOP_K:
        parser.error(f"this experiment is fixed to --top-k {DEFAULT_TOP_K}")
    if args.top_k > args.num_experts:
        parser.error("--top-k cannot exceed --num-experts")

    source_cutover = Path(args.source_cutover).expanduser().resolve(strict=True)
    comparison = read_json(source_cutover / "cutover.json")
    if comparison.get("schema_version") != "main-computer-nanojev-three-backbone-latent-router-cutover-v1":
        raise RuntimeError("source cutover is not the dense latent-router comparison cutover")
    source_experiment = Path(str(comparison["source_experiment"])).expanduser().resolve(strict=True)
    checkpoint = Path(str(comparison["source_checkpoint"])).expanduser().resolve(strict=True)
    source_manifest = dict(comparison.get("source_manifest") or {})
    source_meta = {
        "cycle": int(comparison["source_cycle"]),
        "global_step": int(comparison["source_global_step"]),
    }
    output_dir = Path(args.output_dir).expanduser()

    frozen = tuple(source_manifest.get("frozen_backbones") or ())
    if frozen != THREE_FROZEN_MODELS:
        raise RuntimeError(
            "source experiment does not declare the exact three frozen models: "
            f"expected={THREE_FROZEN_MODELS} observed={frozen}"
        )
    if int(source_manifest.get("candidate_feature_width", 0)) != TOTAL_FEATURE_WIDTH:
        raise RuntimeError(
            f"source candidate feature width is not {TOTAL_FEATURE_WIDTH}: "
            f"{source_manifest.get('candidate_feature_width')}"
        )
    source_head = checkpoint / "head.safetensors"
    source_rng = source_cutover / "rng_state.pt"
    if not source_head.is_file() or not source_rng.is_file():
        raise RuntimeError(f"source checkpoint is incomplete: {checkpoint}")

    emit(
        "latent_top2_cutover_resolved",
        source_checkpoint=str(checkpoint),
        source_cycle=int(source_meta.get("cycle", 0)),
        source_global_step=int(source_meta.get("global_step", 0)),
        output_dir=str(output_dir),
        models=list(THREE_FROZEN_MODELS),
        num_experts=args.num_experts,
        expert_rank=args.expert_rank,
        top_k=args.top_k,
        comparison_source_cutover=str(source_cutover),
        dry_run=bool(args.dry_run),
    )
    if args.dry_run:
        return
    if output_dir.exists():
        raise RuntimeError(f"output already exists: {output_dir}")

    from safetensors.torch import load_file, save_file

    old = load_file(str(source_head), device="cpu")
    expected_old = set(old)
    if not expected_old or not all(key.startswith(INHERITED_HEAD_PREFIXES) for key in expected_old):
        raise RuntimeError("source checkpoint contains unexpected non-head tensors")
    modular = _new_modular_tensors(
        num_experts=args.num_experts,
        expert_rank=args.expert_rank,
        seed=args.seed,
    )
    collision = set(old).intersection(modular)
    if collision:
        raise RuntimeError(f"modular tensor names collide with inherited head: {sorted(collision)}")
    merged = {**old, **modular}

    tmp_parent = output_dir.parent
    tmp_parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=output_dir.name + ".tmp-", dir=str(tmp_parent)))
    try:
        save_file({k: v.contiguous() for k, v in merged.items()}, str(tmp / "head.safetensors"))
        shutil.copy2(source_rng, tmp / "rng_state.pt")
        manifest = {
            "schema_version": SCHEMA,
            "comparison_source_cutover": str(source_cutover),
            "source_experiment": str(source_experiment),
            "source_checkpoint": str(checkpoint),
            "source_cycle": int(source_meta.get("cycle", 0)),
            "source_global_step": int(source_meta.get("global_step", 0)),
            "source_head_sha256": sha256_file(source_head),
            "source_manifest": source_manifest,
            "models": list(THREE_FROZEN_MODELS),
            "candidate_feature_width": TOTAL_FEATURE_WIDTH,
            "num_experts": int(args.num_experts),
            "expert_rank": int(args.expert_rank),
            "top_k": int(args.top_k),
            "router_init_std": float(DEFAULT_ROUTER_INIT_STD),
            "init_seed": int(args.seed),
            "router_receives_task_identity": False,
            "inherited_head_frozen_by_training_contract": True,
            "backbones_frozen_by_training_contract": True,
            "function_preserving": True,
            "optimizer_inherited": False,
            "optimizer_contract": "fresh AdamW over latent_router + latent_experts only",
            "expert_routing": "top-2 sparse softmax over latent experts; task identity is never an input",
            "expert_residual": "2307-wide residual added before the frozen inherited NanoJev head",
            "regularization": "load balance uses actual sparse top-k selection frequency times differentiable dense importance, plus a dense-importance uniformity term; entropy uses actual sparse routing; none uses task labels",
        }
        (tmp / "cutover.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, output_dir)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    emit(
        "latent_top2_cutover_done",
        output_dir=str(output_dir.resolve()),
        source_checkpoint=str(checkpoint),
        inherited_tensors=len(old),
        modular_tensors=len(modular),
        candidate_feature_width=TOTAL_FEATURE_WIDTH,
        num_experts=args.num_experts,
        expert_rank=args.expert_rank,
        top_k=args.top_k,
        models=list(THREE_FROZEN_MODELS),
    )


if __name__ == "__main__":
    main()
