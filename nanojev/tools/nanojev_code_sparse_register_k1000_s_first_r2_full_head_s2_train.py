#!/usr/bin/env python3
"""Train the two-persistent-token NanoJev full-head lane over frozen Qwen.

This is a thin architecture layer over the established frozen-g2 full-head
trainer.  It changes only the persistent prefix presented to Qwen:

    pass1: Qwen([S2, S1, prompt]) -> R1 sensor
    pass2: Qwen([S2, S1 + R1, prompt]) -> answer

S2 is a trainable parameter initialized by the cut-over script from Qwen token
220 (literal space).  The existing S1/R1/controller/head machinery is inherited
unchanged.  S2 is prepended, so every old S1-to-prompt relative position is
preserved.  Qwen remains frozen.  R2 gain must remain frozen at exact zero in
this lane; the third pass is intentionally bypassed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import types

DEFAULT_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_s2_v1"
)
BASE_TRAINER = "nanojev_code_sparse_register_k1000_s_first_r2_full_head_train.py"
BRANCH_FILE = "s_first_r2_full_head_s2_branch.json"
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-s2-branch-v1"
GENERATOR = "s2_s1_r1_full_nanojev_head_joint_train_v1"
PHASE = "frozen_qwen_full_nanojev_head_trainable_s2_s1_r1_shared_sparse_registers_plus_balanced_pairwise"
OBJECTIVE = "joint_full_nanojev_head_training_s2_s1_r1_shared_sparse_register_bank_three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
COMPACT_LOG = "s_first_r2_full_head_s2_trainer_compact.log"
HEARTBEAT_EVENT = "s_first_r2_full_head_s2_heartbeat"
S2_TOKEN_ID = 220
S2_TOKEN_TEXT = " "
S2_TENSOR_KEY = "soft_feedback.s2_base"
SOFT_TOKEN_COUNT = 2


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def resolved_experiment_from_argv() -> Path:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    ns, _ = p.parse_known_args()
    return ns.experiment_dir.expanduser().resolve()


def validate_s2_lane(exp: Path, *, allow_missing_for_self_test: bool = False) -> None:
    if allow_missing_for_self_test and "--self-test" in sys.argv:
        return
    if not exp.is_dir():
        raise RuntimeError(f"S2 experiment does not exist; run cut-over first: {exp}")
    marker = exp / BRANCH_FILE
    if not marker.is_file():
        raise RuntimeError(f"S2 trainer only resumes a cut-over lane; missing {marker}")
    branch = read_json(marker)
    if branch.get("schema_version") != BRANCH_SCHEMA:
        raise RuntimeError(f"S2 branch schema mismatch: {branch.get('schema_version')!r}")
    config = read_json(exp / "training_config.json")
    if config.get("soft_feedback_generator") != GENERATOR:
        raise RuntimeError("S2 training_config generator mismatch")
    if int(config.get("soft_feedback_token_count", -1)) != SOFT_TOKEN_COUNT:
        raise RuntimeError("S2 training_config must declare exactly two persistent soft tokens")
    if bool(config.get("r2_gain_trainable", False)):
        raise RuntimeError("S2 lane requires R2 gain frozen at zero")


def _cosine(a, b) -> float:
    import torch.nn.functional as F

    return float(F.cosine_similarity(a.float().reshape(1, -1), b.float().reshape(1, -1), dim=-1).item())


def install_s2_architecture(base) -> None:
    """Patch the mature full-head trainer only in memory; current source lane stays untouched."""
    original_load_local_module = base.load_local_module

    def wrapped_load_local_module(name: str, path: Path):
        module = original_load_local_module(name, path)
        if name != "nanojev_s_first_arch_for_full_head":
            return module

        inherited_build_s_first_factory = module.build_s_first_factory

        def build_s_first_factory_with_s2(base_module):
            inherited_factory = inherited_build_s_first_factory(base_module)

            def factory(BaseDecisionModel, *, control_space_size, register_rank, strength_init, active_epsilon):
                Parent = inherited_factory(
                    BaseDecisionModel,
                    control_space_size=control_space_size,
                    register_rank=register_rank,
                    strength_init=strength_init,
                    active_epsilon=active_epsilon,
                )

                class S2SFirstDecisionModel(Parent):
                    def __init__(self, backbone, set_head):
                        import torch
                        from torch import nn

                        super().__init__(backbone, set_head)
                        sf = self.soft_feedback
                        table = self.backbone.get_input_embeddings().weight
                        if S2_TOKEN_ID >= int(table.shape[0]):
                            raise RuntimeError(
                                f"S2 token id {S2_TOKEN_ID} exceeds backbone vocabulary {table.shape[0]}"
                            )
                        seed = table[S2_TOKEN_ID].detach().to(device=sf.base.device, dtype=sf.base.dtype).clone()
                        if int(seed.numel()) != int(sf.base.numel()):
                            raise RuntimeError(
                                f"S2 seed width {seed.numel()} does not match S1 width {sf.base.numel()}"
                            )
                        sf.s2_base = nn.Parameter(seed)

                        inherited_static_parameters = sf.static_parameters

                        def static_parameters(this):
                            # Preserve inherited ordering (S1, strength) and append S2.
                            return tuple(list(inherited_static_parameters()) + [this.s2_base])

                        sf.static_parameters = types.MethodType(static_parameters, sf)

                    def _qwen_with_s2_s1(self, tokens, attention, lengths, path_owner, s1_by_question):
                        import torch

                        with torch.no_grad():
                            token_embeds = self.backbone.get_input_embeddings()(tokens)
                        s1_by_path = s1_by_question.index_select(0, path_owner).unsqueeze(1).to(token_embeds.dtype)
                        s2_by_question = self.soft_feedback.s2_base.view(1, -1).expand(
                            s1_by_question.shape[0], -1
                        )
                        s2_by_path = s2_by_question.index_select(0, path_owner).unsqueeze(1).to(token_embeds.dtype)
                        inputs = torch.cat([s2_by_path, s1_by_path, token_embeds], dim=1)
                        prefix_attention = torch.cat(
                            [
                                torch.ones(
                                    (attention.shape[0], 2),
                                    dtype=attention.dtype,
                                    device=attention.device,
                                ),
                                attention,
                            ],
                            dim=1,
                        )
                        max_positions = int(
                            getattr(self.backbone.config, "max_position_embeddings", inputs.shape[1])
                        )
                        if inputs.shape[1] > max_positions:
                            raise RuntimeError(
                                f"S2/S1 prefix exceeds backbone max positions: {inputs.shape[1]} > {max_positions}"
                            )
                        hidden = self.backbone(
                            inputs_embeds=inputs,
                            attention_mask=prefix_attention,
                            use_cache=False,
                        ).last_hidden_state
                        # Original last token was length-1.  Two prepended tokens move
                        # it to length+1.  S1/prompt relative offsets remain unchanged.
                        leaves = hidden[
                            torch.arange(tokens.shape[0], device=tokens.device), lengths + 1
                        ]
                        return leaves

                    def _s2_common_stats(self, static_s1):
                        import torch

                        sf = self.soft_feedback
                        s2 = sf.s2_base
                        s1 = static_s1[0] if static_s1.ndim == 2 else static_s1
                        return {
                            "s2_raw_rms": float(torch.sqrt(s2.detach().float().square().mean() + 1e-30).item()),
                            "static_s1_helper_rms": float(
                                torch.sqrt(s1.detach().float().square().mean() + 1e-30).item()
                            ),
                            "s1_s2_cosine": _cosine(s1.detach(), s2.detach()),
                            "s2_token_id_at_initialization": S2_TOKEN_ID,
                            "persistent_prefix_tokens": 2,
                        }

                    def forward(self, examples, pad_token):
                        import torch

                        sf = self.soft_feedback
                        mode = sf.mode
                        if mode == "none":
                            # "none" remains the true no-soft-state ruler: no S1 and no S2.
                            return super().forward(examples, pad_token)

                        paths, lengths, tokens, attention, path_owner = self._assemble_paths(examples, pad_token)

                        if mode == "static":
                            hidden_size = int(self.backbone.config.hidden_size)
                            summary = sf.base.new_zeros((len(examples), hidden_size))
                            score_stats = sf.base.new_zeros((len(examples), 2))
                            helper_by_question, dynamic_raw, strength, coefficients = sf.helper(summary, score_stats)
                            leaves = self._qwen_with_s2_s1(
                                tokens, attention, lengths, path_owner, helper_by_question
                            )
                            logits, valid, _h, _path_valid = self._score_leaves(leaves, examples)
                            self.last_first_pass_logits = None
                            with torch.no_grad():
                                coeff_abs = coefficients.detach().float().abs()
                                aggregate_mean_abs = (
                                    coeff_abs.mean(dim=0)
                                    if coeff_abs.numel()
                                    else coeff_abs.new_zeros(sf.control_space_size)
                                )
                                aggregate_active = aggregate_mean_abs >= sf.active_epsilon
                                stats = {
                                    "mode": "static",
                                    "backbone_reads": 1,
                                    "first_pass_prefix": None,
                                    "second_pass_prefix": "S2_then_static_S1",
                                    "second_pass_answer": "Qwen([S2,S1,prompt])",
                                    "helper_tokens_per_question": 2,
                                    "questions": len(examples),
                                    "candidate_paths": len(paths),
                                    "embedding_rms": float(sf.embedding_rms.detach().float().item()),
                                    "helper_rms": float(
                                        torch.sqrt(helper_by_question.detach().float().square().mean() + 1e-30).item()
                                    ),
                                    "base_raw_rms": float(
                                        torch.sqrt(sf.base.detach().float().square().mean() + 1e-30).item()
                                    ),
                                    "dynamic_raw_rms": float(
                                        torch.sqrt(dynamic_raw.detach().float().square().mean() + 1e-30).item()
                                    ),
                                    "strength": float(strength.detach().float().item()),
                                    "control_space_size": sf.control_space_size,
                                    "register_rank": sf.register_rank,
                                    "aggregate_active_slot_count": int(aggregate_active.sum().item()),
                                    "self_routed_usage": sf.last_usage,
                                    "orthogonality_penalty": float(
                                        sf.last_regularization["orthogonality"].detach().item()
                                    ),
                                }
                                stats.update(self._s2_common_stats(helper_by_question))
                                sf.last_stats = stats
                            return logits, valid

                        if mode != "dynamic":
                            raise RuntimeError(f"unsupported S2 soft-feedback mode: {mode!r}")

                        # Pass 1 sees the two persistent states.  The sensor read is
                        # detached exactly as before; S1/S2 learn from the final pass.
                        with torch.no_grad():
                            static_by_question = self._static_sensor_helper(len(examples))
                            first_leaves = self._qwen_with_s2_s1(
                                tokens, attention, lengths, path_owner, static_by_question
                            )
                            first_logits, _first_valid, first_h, first_path_valid = self._score_leaves(
                                first_leaves, examples
                            )
                            summary, score_stats = self._question_summary(
                                first_h, first_path_valid, first_logits, examples
                            )
                        self.last_first_pass_logits = first_logits

                        # Existing controller still writes exactly one dynamic token:
                        # S1 + R1.  S2 is an independent persistent upstream token.
                        helper_by_question, dynamic_raw, strength, coefficients = sf.helper(
                            summary, score_stats
                        )
                        second_leaves = self._qwen_with_s2_s1(
                            tokens, attention, lengths, path_owner, helper_by_question
                        )
                        logits, valid, _second_h, _second_path_valid = self._score_leaves(
                            second_leaves, examples
                        )

                        with torch.no_grad():
                            helper_rms = torch.sqrt(
                                helper_by_question.detach().float().square().mean() + 1e-30
                            )
                            base_rms = torch.sqrt(sf.base.detach().float().square().mean() + 1e-30)
                            dynamic_rms = torch.sqrt(dynamic_raw.detach().float().square().mean() + 1e-30)
                            coeff_abs = coefficients.detach().float().abs()
                            aggregate_mean_abs = (
                                coeff_abs.mean(dim=0)
                                if coeff_abs.numel()
                                else coeff_abs.new_zeros(sf.control_space_size)
                            )
                            aggregate_active = aggregate_mean_abs >= sf.active_epsilon
                            displacement = second_leaves.detach().float() - first_leaves.detach().float()
                            first_rms = torch.sqrt(first_leaves.detach().float().square().mean() + 1e-30)
                            displacement_rms = torch.sqrt(displacement.square().mean() + 1e-30)
                            stats = {
                                "mode": "dynamic",
                                "backbone_reads": 2,
                                "first_pass_prefix": "S2_then_static_S1",
                                "first_pass_sensor": "Qwen([S2,S1,prompt])",
                                "second_pass_prefix": "S2_then_S1_plus_R1",
                                "second_pass_answer": "Qwen([S2,S1+R1,prompt])",
                                "helper_tokens_per_question": 2,
                                "questions": len(examples),
                                "candidate_paths": len(paths),
                                "embedding_rms": float(sf.embedding_rms.detach().float().item()),
                                "helper_rms": float(helper_rms.item()),
                                "base_raw_rms": float(base_rms.item()),
                                "dynamic_raw_rms": float(dynamic_rms.item()),
                                "strength": float(strength.detach().float().item()),
                                "control_space_size": sf.control_space_size,
                                "register_rank": sf.register_rank,
                                "aggregate_active_slot_count": int(aggregate_active.sum().item()),
                                "self_routed_usage": sf.last_usage,
                                "orthogonality_penalty": float(
                                    sf.last_regularization["orthogonality"].detach().item()
                                ),
                                "first_leaf_rms": float(first_rms.item()),
                                "first_to_second_leaf_displacement_rms": float(displacement_rms.item()),
                                "relative_leaf_displacement_rms": float(
                                    (displacement_rms / (first_rms + 1e-30)).item()
                                ),
                                "first_pass_score_stat_rms": float(
                                    torch.sqrt(score_stats.detach().float().square().mean() + 1e-30).item()
                                ),
                            }
                            stats.update(self._s2_common_stats(static_by_question))
                            sf.last_stats = stats
                        return logits, valid

                S2SFirstDecisionModel.__name__ = "S2SFirstSparseRegisterDecisionModel"
                return S2SFirstDecisionModel

            return factory

        module.build_s_first_factory = build_s_first_factory_with_s2
        return module

    base.load_local_module = wrapped_load_local_module

    # Make the mature trainer operate on the new lane/contract without touching
    # the source trainer or source experiment.
    base.DEFAULT_EXPERIMENT = str(DEFAULT_EXPERIMENT)
    base.SOFT_FEEDBACK_GENERATOR = GENERATOR
    base.PHASE = PHASE
    base.OBJECTIVE = OBJECTIVE
    base.SOFT_FEEDBACK_TOKEN_COUNT = SOFT_TOKEN_COUNT
    base.FULL_HEAD_COMPACT_LOG_NAME = COMPACT_LOG
    base.FULL_HEAD_HEARTBEAT_EVENT = HEARTBEAT_EVENT

    # Correct the static-parameter accounting used by diagnostics/self-test.
    base.soft_feedback_static_parameter_count = lambda hidden_size: 2 * int(hidden_size) + 1

    # Preserve S2 provenance in every subsequently committed checkpoint config.
    original_save_generation = base.save_generation

    def save_generation_with_s2_contract(*args, **kwargs):
        path = original_save_generation(*args, **kwargs)
        cfg_path = path / "config.json"
        cfg = base.read_json(cfg_path)
        feedback = dict(cfg.get("soft_feedback") or {})
        feedback.update({
            "generator": GENERATOR,
            "token_count": SOFT_TOKEN_COUNT,
            "s2_enabled": True,
            "s2_tensor": S2_TENSOR_KEY,
            "s2_seed_token_id": S2_TOKEN_ID,
            "s2_seed_token_text": S2_TOKEN_TEXT,
            "prefix_order_pass1": "[S2,S1,prompt]",
            "prefix_order_pass2": "[S2,S1+R1,prompt]",
            "s1_prompt_relative_positions_preserved": True,
            "r2_gain_trainable": False,
            "r2_third_pass_bypassed": True,
        })
        cfg["soft_feedback"] = feedback
        base.atomic_json(cfg_path, cfg)
        return path

    base.save_generation = save_generation_with_s2_contract


def main() -> None:
    if "--train-r2-gain" in sys.argv:
        raise SystemExit(
            "S2 lane intentionally keeps g2 frozen at zero; do not use --train-r2-gain in this experiment"
        )
    tools_dir = Path(__file__).resolve().parent
    base = load_module("nanojev_full_head_base_for_s2", tools_dir / BASE_TRAINER)
    install_s2_architecture(base)
    exp = resolved_experiment_from_argv()
    validate_s2_lane(exp, allow_missing_for_self_test=True)
    base.main()


if __name__ == "__main__":
    main()
