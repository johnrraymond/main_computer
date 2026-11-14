#!/usr/bin/env python3
"""Train the K=1000 S-first branch with one additional recurrent Qwen pass.

Dynamic architecture:

    pass 1: Qwen([S, prompt])              -> C1 -> R1
    pass 2: Qwen([S + R1, prompt])         -> C2 -> R2
    pass 3: Qwen([S + R1 + R2, prompt])    -> answer

C2 has its own rank-32 router, initialized as an exact copy of the trained C1
router at branch time.  R2 reuses the *same* learned K=1000 control bank as R1.
A scalar tanh gate starts at exactly zero, so the branch is behaviorally equal
to its S-first parent at cut-over.  Existing R1/bank optimizer state is preserved;
new C2/gate Adam state starts empty and is created on the first update.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import types
from typing import Any

TARGET_DEFAULT = Path(r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_v1")
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-branch-v1"
R2_GENERATOR = "s_first_recurrent_r2_shared_sparse_control_dictionary_v1"
R2_PHASE = "frozen_qwen_frozen_head_frozen_static_token_s_first_recurrent_r2_shared_sparse_registers_plus_balanced_pairwise"
R2_OBJECTIVE = "s_first_r1_plus_zero_init_recurrent_r2_over_shared_sparse_register_bank_three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
EXPECTED_K = 1000
EXPECTED_RANK = 32
COMPACT_LOG_NAME = "s_first_r2_trainer_compact.log"
HEARTBEAT_EVENT = "s_first_r2_heartbeat"

DROP_KEYS = {
    "aggregate_active_slots", "active_slots", "occupied_slots", "dormant_slots",
    "aggregate_mean_abs_coefficients", "mean_abs_coefficients", "slot_task_affinities",
    "slot_owners", "shared_slots", "single_task_slots", "multiply_claimed_slots",
    "exclusively_claimed_slots",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k not in DROP_KEYS}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def validate_lane(exp: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    branch = read_json(exp / "s_first_r2_branch.json")
    experiment = read_json(exp / "experiment.json")
    config = read_json(exp / "training_config.json")
    state = read_json(exp / "training_state.json")
    if branch.get("schema_version") != BRANCH_SCHEMA:
        raise RuntimeError(f"not an S-first R2 branch: {exp}")
    if experiment.get("soft_feedback_generator") != R2_GENERATOR:
        raise RuntimeError("R2 experiment generator contract mismatch")
    if config.get("soft_feedback_generator") != R2_GENERATOR:
        raise RuntimeError("R2 training_config generator contract mismatch")
    if (int(config.get("control_space_size", -1)), int(config.get("register_rank", -1))) != (EXPECTED_K, EXPECTED_RANK):
        raise RuntimeError(f"R2 lane must remain K={EXPECTED_K}, rank={EXPECTED_RANK}")
    latest = state.get("latest_generation")
    if not latest:
        raise RuntimeError("R2 lane has no committed checkpoint")
    checkpoint = Path(str(latest)).expanduser().resolve(strict=True)
    for required in ("head.safetensors", "optimizer.pt", "rng_state.pt", "config.json", "meta.json"):
        if not (checkpoint / required).is_file():
            raise RuntimeError(f"latest R2 checkpoint missing {required}: {checkpoint}")
    cp_cfg = read_json(checkpoint / "config.json")
    if int(cp_cfg.get("main_computer_cycle", -1)) != int(state.get("cycle", -2)):
        raise RuntimeError("R2 latest checkpoint cycle disagrees with training_state.json")
    feedback = cp_cfg.get("soft_feedback")
    if not isinstance(feedback, dict) or feedback.get("recurrent_r2_enabled") is not True:
        raise RuntimeError("latest checkpoint lacks the recurrent-R2 contract")
    return branch, experiment, config, state


def build_r2_factory(base, s_first):
    s_first_factory = s_first.build_s_first_factory(base)

    def factory(BaseDecisionModel, *, control_space_size: int, register_rank: int, strength_init: float, active_epsilon: float):
        Parent = s_first_factory(
            BaseDecisionModel,
            control_space_size=control_space_size,
            register_rank=register_rank,
            strength_init=strength_init,
            active_epsilon=active_epsilon,
        )

        class SFirstRecurrentR2DecisionModel(Parent):
            def __init__(self, backbone, set_head):
                import torch
                from torch import nn

                super().__init__(backbone, set_head)
                sf = self.soft_feedback
                sf.r2_router_down = nn.Linear(sf.hidden_size, sf.register_rank, bias=False)
                sf.r2_router_score = nn.Linear(2, sf.register_rank, bias=False)
                sf.r2_router_coeff = nn.Linear(sf.register_rank, sf.control_space_size, bias=False)
                sf.r2_gain = nn.Parameter(torch.zeros(1, dtype=sf.control_bank.dtype))
                with torch.no_grad():
                    sf.r2_router_down.weight.copy_(sf.router_down.weight)
                    sf.r2_router_score.weight.copy_(sf.router_score.weight)
                    sf.r2_router_coeff.weight.copy_(sf.router_coeff.weight)
                    sf.r2_gain.zero_()

                inherited_dynamic_parameters = sf.dynamic_parameters

                def dynamic_parameters(this):
                    return tuple(
                        list(inherited_dynamic_parameters())
                        + list(this.r2_router_down.parameters())
                        + list(this.r2_router_score.parameters())
                        + list(this.r2_router_coeff.parameters())
                        + [this.r2_gain]
                    )

                sf.dynamic_parameters = types.MethodType(dynamic_parameters, sf)
                self.last_second_pass_logits = None

            @staticmethod
            def _row_cosine_mean(a, b) -> float:
                import torch.nn.functional as F

                if a.numel() == 0 or b.numel() == 0:
                    return 0.0
                return float(F.cosine_similarity(a.float(), b.float(), dim=-1, eps=1e-8).mean().detach().item())

            def _helper_from_raw(self, raw):
                import torch

                sf = self.soft_feedback
                direction = torch.tanh(raw)
                direction_rms = torch.sqrt(
                    direction.float().square().mean(dim=-1, keepdim=True) + 1e-8
                ).to(direction.dtype)
                normalized = direction / direction_rms
                strength = torch.sigmoid(sf.strength_logit).to(direction.dtype)
                helper = normalized * sf.embedding_rms.to(direction.dtype) * strength
                zero_rows = raw.detach().abs().amax(dim=-1, keepdim=True) == 0
                return torch.where(zero_rows, torch.zeros_like(helper), helper), strength

            def _qwen_with_helper(self, tokens, attention, lengths, path_owner, helper_by_question):
                import torch

                helper_by_path = helper_by_question.index_select(0, path_owner).unsqueeze(1)
                with torch.no_grad():
                    token_embeds = self.backbone.get_input_embeddings()(tokens)
                helper_by_path = helper_by_path.to(dtype=token_embeds.dtype)
                inputs = torch.cat([helper_by_path, token_embeds], dim=1)
                prefix_attention = torch.cat(
                    [
                        torch.ones((attention.shape[0], 1), dtype=attention.dtype, device=attention.device),
                        attention,
                    ],
                    dim=1,
                )
                max_positions = int(getattr(self.backbone.config, "max_position_embeddings", inputs.shape[1]))
                if inputs.shape[1] > max_positions:
                    raise RuntimeError(
                        f"recurrent soft-feedback prefix exceeds backbone max positions: {inputs.shape[1]} > {max_positions}"
                    )
                hidden = self.backbone(
                    inputs_embeds=inputs,
                    attention_mask=prefix_attention,
                    use_cache=False,
                ).last_hidden_state
                leaves = hidden[torch.arange(tokens.shape[0], device=tokens.device), lengths]
                return leaves

            def forward(self, examples, pad_token):
                import torch

                if self.soft_feedback.mode != "dynamic":
                    return super().forward(examples, pad_token)

                sf = self.soft_feedback
                paths, lengths, tokens, attention, path_owner = self._assemble_paths(examples, pad_token)

                # Pass 1: observe Qwen at the frozen S operating point.
                with torch.no_grad():
                    token_embeds = self.backbone.get_input_embeddings()(tokens)
                    static_by_question = self._static_sensor_helper(len(examples))
                    static_by_path = static_by_question.index_select(0, path_owner).unsqueeze(1).to(token_embeds.dtype)
                    first_inputs = torch.cat([static_by_path, token_embeds], dim=1)
                    first_attention = torch.cat(
                        [torch.ones((attention.shape[0], 1), dtype=attention.dtype, device=attention.device), attention],
                        dim=1,
                    )
                    max_positions = int(getattr(self.backbone.config, "max_position_embeddings", first_inputs.shape[1]))
                    if first_inputs.shape[1] > max_positions:
                        raise RuntimeError(
                            f"S-first prefix exceeds backbone max positions: {first_inputs.shape[1]} > {max_positions}"
                        )
                    first_hidden = self.backbone(
                        inputs_embeds=first_inputs,
                        attention_mask=first_attention,
                        use_cache=False,
                    ).last_hidden_state
                    first_leaves = first_hidden[torch.arange(len(paths), device=tokens.device), lengths]
                    first_logits, _first_valid, first_h, first_path_valid = self._score_leaves(first_leaves, examples)
                    summary1, score_stats1 = self._question_summary(first_h, first_path_valid, first_logits, examples)
                self.last_first_pass_logits = first_logits

                # R1 is the already-trained S-first controller over the shared bank.
                helper1, dynamic_raw1, strength, coefficients1 = sf.helper(summary1, score_stats1)

                # Pass 2: observe the actual consequence of R1.  This sensor read is detached;
                # final-loss gradients reach R1 directly through the cumulative pass-3 helper.
                with torch.no_grad():
                    second_leaves = self._qwen_with_helper(tokens, attention, lengths, path_owner, helper1)
                    second_logits, _second_valid, second_h, second_path_valid = self._score_leaves(second_leaves, examples)
                    summary2, score_stats2 = self._question_summary(second_h, second_path_valid, second_logits, examples)
                self.last_second_pass_logits = second_logits

                # C2 starts as a copy of C1 and speaks through the same learned 1000-vector bank.
                code2 = torch.tanh(
                    sf.r2_router_down(summary2)
                    + sf.r2_router_score(score_stats2.to(summary2.dtype))
                )
                coefficients2 = torch.tanh(sf.r2_router_coeff(code2))
                candidate_raw2 = coefficients2.to(sf.control_bank.dtype) @ sf.normalized_bank()
                r2_gain = torch.tanh(sf.r2_gain[0]).to(candidate_raw2.dtype)
                dynamic_raw2 = candidate_raw2 * r2_gain
                effective_coefficients2 = coefficients2 * r2_gain.to(coefficients2.dtype)

                # Pass 3 stays on the same helper-token scale: H(S + R1 + R2).
                cumulative_raw = sf.base.view(1, -1).expand(dynamic_raw1.shape[0], -1) + dynamic_raw1 + dynamic_raw2
                helper3, strength3 = self._helper_from_raw(cumulative_raw)
                third_leaves = self._qwen_with_helper(tokens, attention, lengths, path_owner, helper3)
                logits, valid, _third_h, _third_path_valid = self._score_leaves(third_leaves, examples)

                # Preserve the parent's regularization exactly when R2 gain == 0.
                orthogonality = sf.orthogonality_penalty()
                sparsity = coefficients1.float().abs().mean() + effective_coefficients2.float().abs().mean()
                residual_mse = dynamic_raw1.float().square().mean() + dynamic_raw2.float().square().mean()
                sf.last_regularization = {
                    "sparsity": sparsity,
                    "residual_mse": residual_mse,
                    "orthogonality": orthogonality,
                }

                cumulative_coefficients = coefficients1 + effective_coefficients2.to(coefficients1.dtype)
                detached_cumulative = cumulative_coefficients.detach().float()
                mean_abs = detached_cumulative.abs().mean(dim=0)
                cumulative_active = mean_abs >= sf.active_epsilon
                r1_mean_abs = coefficients1.detach().float().abs().mean(dim=0)
                r2_mean_abs = effective_coefficients2.detach().float().abs().mean(dim=0)
                r1_active = r1_mean_abs >= sf.active_epsilon
                r2_active = r2_mean_abs >= sf.active_epsilon
                sf.last_usage = {
                    "questions": int(summary1.shape[0]),
                    "active_slot_count": int(cumulative_active.sum().item()),
                }
                sf.last_coefficients = detached_cumulative

                with torch.no_grad():
                    def rms(value):
                        return float(torch.sqrt(value.detach().float().square().mean() + 1e-30).item())

                    first_rms = rms(first_leaves)
                    second_rms = rms(second_leaves)
                    third_rms = rms(third_leaves)
                    d12 = rms(second_leaves.detach().float() - first_leaves.detach().float())
                    d23 = rms(third_leaves.detach().float() - second_leaves.detach().float())
                    coeff2_candidate_active = int(
                        (coefficients2.detach().float().abs().mean(dim=0) >= sf.active_epsilon).sum().item()
                    )
                    sf.last_stats = {
                        "mode": "dynamic",
                        "backbone_reads": 3,
                        "first_pass_prefix": "static_S",
                        "first_pass_sensor": "Qwen([S,prompt])",
                        "second_pass_prefix": "static_S_plus_R1",
                        "second_pass_sensor": "Qwen([S+R1,prompt])",
                        "third_pass_prefix": "static_S_plus_R1_plus_R2",
                        "third_pass_answer": "Qwen([S+R1+R2,prompt])",
                        "helper_tokens_per_question": 1,
                        "questions": len(examples),
                        "candidate_paths": len(paths),
                        "embedding_rms": float(sf.embedding_rms.detach().float().item()),
                        "helper_rms": rms(helper3),
                        "r1_helper_rms": rms(helper1),
                        "base_raw_rms": rms(sf.base),
                        "dynamic_raw_rms": rms(dynamic_raw1 + dynamic_raw2),
                        "r1_dynamic_raw_rms": rms(dynamic_raw1),
                        "r2_candidate_raw_rms": rms(candidate_raw2),
                        "r2_dynamic_raw_rms": rms(dynamic_raw2),
                        "r2_gain_parameter": float(sf.r2_gain.detach().float().item()),
                        "r2_gain": float(r2_gain.detach().float().item()),
                        "strength": float(strength3.detach().float().item()),
                        "control_space_size": sf.control_space_size,
                        "register_rank": sf.register_rank,
                        "aggregate_active_slot_count": int(cumulative_active.sum().item()),
                        "r1_active_slot_count": int(r1_active.sum().item()),
                        "r2_candidate_active_slot_count": coeff2_candidate_active,
                        "r2_effective_active_slot_count": int(r2_active.sum().item()),
                        "r1_r2_candidate_coefficient_cosine": self._row_cosine_mean(
                            coefficients1.detach(), coefficients2.detach()
                        ),
                        "r1_r2_candidate_residual_cosine": self._row_cosine_mean(
                            dynamic_raw1.detach(), candidate_raw2.detach()
                        ),
                        "orthogonality_penalty": float(orthogonality.detach().item()),
                        "first_leaf_rms": first_rms,
                        "second_leaf_rms": second_rms,
                        "third_leaf_rms": third_rms,
                        "first_to_second_leaf_displacement_rms": d12,
                        "second_to_third_leaf_displacement_rms": d23,
                        "relative_first_to_second_leaf_displacement_rms": d12 / (first_rms + 1e-30),
                        "relative_second_to_third_leaf_displacement_rms": d23 / (second_rms + 1e-30),
                        "first_pass_score_stat_rms": rms(score_stats1),
                        "second_pass_score_stat_rms": rms(score_stats2),
                    }
                return logits, valid

        SFirstRecurrentR2DecisionModel.__name__ = "SFirstRecurrentR2SparseRegisterDecisionModel"
        return SFirstRecurrentR2DecisionModel

    return factory


def trainer_argv(exp: Path, experiment: dict[str, Any], config: dict[str, Any], args: argparse.Namespace) -> list[str]:
    def add(name: str, value: Any) -> list[str]:
        return [name, str(value)]

    argv = ["nanojev_code_sparse_register_train.py"]
    argv += add("--experiment-dir", exp)
    argv += add("--legacy-experiment-dir", experiment["legacy_experiment"])
    argv += add("--mutation-experiment-dir", experiment["mutation_experiment"])
    argv += add("--three-mode-experiment-dir", experiment["three_mode_experiment"])
    argv += add("--source-soft-feedback-experiment-dir", experiment["source_soft_feedback_experiment"])
    argv += add("--control-space-size", config["control_space_size"])
    argv += add("--register-rank", config["register_rank"])
    argv += add("--register-lr", config["soft_feedback_lr"])
    argv += add("--register-sparsity-weight", config["register_sparsity_weight"])
    argv += add("--register-residual-weight", config["register_residual_weight"])
    argv += add("--register-orthogonality-weight", config["register_orthogonality_weight"])
    argv += add("--register-active-epsilon", config["register_active_epsilon"])
    argv += add("--soft-feedback-strength-init", config["soft_feedback_strength_init"])
    argv += add("--soft-feedback-control-eval-every", config["soft_feedback_control_eval_every"])
    for task in ("legacy", "mutation", "ast", "consensus", "triad"):
        argv += add(f"--{task}-training-percent", config[f"{task}_training_percent"])
    argv += add("--cycles-this-run", args.cycles_this_run)
    argv += add("--cycle-seconds", config["cycle_seconds"])
    argv += add("--train-files-per-cycle", config["train_files_per_cycle"])
    argv += add("--train-units-per-cycle", config["train_units_per_cycle"])
    argv += add("--consensus-dev-files", config["consensus_dev_files"])
    argv += add("--consensus-dev-records", config["consensus_dev_records"])
    argv += add("--mutation-max-code-tokens", config["mutation_max_code_tokens"])
    argv += add("--consensus-max-code-tokens", config["consensus_max_code_tokens"])
    argv += add("--batch-units", config["batch_units"])
    argv += add("--microbatch-questions", config["microbatch_questions"])
    argv += add("--max-microbatch-tokens", config["max_microbatch_tokens"])
    argv += add("--head-lr", config["head_lr"])
    argv += add("--weight-decay", config["weight_decay"])
    argv += add("--ranking-weight", config["ranking_weight"])
    argv += add("--ranking-margin", config["ranking_margin"])
    argv += add("--precision", config["precision"])
    argv += add("--keep-generations", args.keep_generations)
    if bool(config.get("disable_native_triton")):
        argv.append("--disable-native-triton")
    if not bool(config.get("soft_feedback_gradient_checkpointing", True)):
        argv.append("--disable-soft-feedback-gradient-checkpointing")
    if args.local_files_only:
        argv.append("--local-files-only")
    return argv


def install_checkpoint_contract(base):
    original = base.save_generation

    def save_generation(*args, **kwargs):
        path = original(*args, **kwargs)
        cfg_path = Path(path) / "config.json"
        cfg = read_json(cfg_path)
        feedback = dict(cfg.get("soft_feedback") or {})
        feedback.update({
            "generator": R2_GENERATOR,
            "backbone_reads_dynamic": 3,
            "recurrent_r2_enabled": True,
            "recurrent_r2_shared_control_bank": True,
            "recurrent_r2_router_initialization": "copy_R1_router_at_branch",
            "recurrent_r2_gain_parameterization": "tanh_scalar_zero_initialized",
        })
        cfg["soft_feedback"] = feedback
        base.atomic_json(cfg_path, cfg)
        return path

    base.save_generation = save_generation


def install_compact_emit(base, exp: Path, heartbeat_seconds: float, verbose: bool):
    log_path = exp / COMPACT_LOG_NAME
    state = {"cycle": None, "next_heartbeat": heartbeat_seconds, "requested": None}

    def write(payload: dict[str, Any]) -> None:
        cleaned = sanitize(payload)
        encoded = json.dumps(cleaned, ensure_ascii=False, allow_nan=False)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        print(encoded, flush=True)

    def emit(event: str, **fields: Any) -> None:
        if verbose:
            write({"event": event, **fields})
            return
        if event == "cycle_start":
            state["cycle"] = fields.get("cycle")
            state["requested"] = fields.get("requested_training_seconds")
            state["next_heartbeat"] = heartbeat_seconds
            write({"event": event, **fields})
            return
        if event == "cycle_train_step":
            elapsed = float(fields.get("elapsed_training_seconds", 0.0) or 0.0)
            if elapsed < float(state["next_heartbeat"]):
                return
            soft = fields.get("soft_feedback") if isinstance(fields.get("soft_feedback"), dict) else {}
            write({
                "event": HEARTBEAT_EVENT,
                "cycle": fields.get("cycle", state["cycle"]),
                "elapsed_training_seconds": elapsed,
                "requested_training_seconds": state["requested"],
                "cycle_step": fields.get("cycle_step"),
                "global_step": fields.get("global_step"),
                "active_above_threshold": soft.get("aggregate_active_slot_count"),
                "r1_active_above_threshold": soft.get("r1_active_slot_count"),
                "r2_candidate_active_above_threshold": soft.get("r2_candidate_active_slot_count"),
                "r2_effective_active_above_threshold": soft.get("r2_effective_active_slot_count"),
                "control_space_size": soft.get("control_space_size", EXPECTED_K),
                "mean_unit_nll": fields.get("mean_unit_nll"),
                "dynamic_raw_rms": soft.get("dynamic_raw_rms"),
                "r1_dynamic_raw_rms": soft.get("r1_dynamic_raw_rms"),
                "r2_candidate_raw_rms": soft.get("r2_candidate_raw_rms"),
                "r2_dynamic_raw_rms": soft.get("r2_dynamic_raw_rms"),
                "r2_gain": soft.get("r2_gain"),
                "r1_r2_candidate_coefficient_cosine": soft.get("r1_r2_candidate_coefficient_cosine"),
                "r1_r2_candidate_residual_cosine": soft.get("r1_r2_candidate_residual_cosine"),
                "second_to_third_leaf_displacement_rms": soft.get("second_to_third_leaf_displacement_rms"),
            })
            while float(state["next_heartbeat"]) <= elapsed:
                state["next_heartbeat"] = float(state["next_heartbeat"]) + heartbeat_seconds
            return
        if event == "evaluation_done":
            keep = {
                key: fields.get(key)
                for key in (
                    "label", "questions", "accuracy", "balanced_accuracy", "pair_win_rate",
                    "balanced_relation_accuracy", "all_three_relations_correct_rate",
                    "topology_accuracy", "non_ambiguous_topology_accuracy", "elapsed_seconds",
                )
                if key in fields
            }
            write({"event": event, **keep})
            return
        if event in {
            "sparse_register_usage", "cycle_result", "checkpoint_committed",
            "checkpoint_generation_deleted", "checkpoint_write_start", "checkpoint_write_done",
            "current_sparse_register_checkpoint_resolved", "resume_optimizer_rng_loaded",
            "trainer_load_done", "head_load_start",
        }:
            write({"event": event, **fields})
            return
        if not event.startswith("cycle_"):
            write({"event": event, **fields})

    base.emit = emit
    return log_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", type=Path, default=TARGET_DEFAULT)
    parser.add_argument("--cycles-this-run", type=int, default=100)
    parser.add_argument("--keep-generations", type=int, default=2)
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--verbose-output", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.cycles_this_run <= 0 or args.keep_generations <= 0:
        parser.error("--cycles-this-run and --keep-generations must be positive")
    if not math.isfinite(args.heartbeat_seconds) or args.heartbeat_seconds <= 0:
        parser.error("--heartbeat-seconds must be finite and positive")

    exp = args.experiment_dir.expanduser().resolve(strict=True)
    branch, experiment, config, state = validate_lane(exp)
    tools_dir = Path(__file__).resolve().parent
    base = load_module("nanojev_sparse_register_base_for_s_first_r2", tools_dir / "nanojev_code_sparse_register_train.py")
    s_first = load_module("nanojev_s_first_factory_for_r2", tools_dir / "nanojev_code_sparse_register_k1000_s_first_train.py")

    base.DEFAULT_EXPERIMENT = str(exp)
    base.SOFT_FEEDBACK_GENERATOR = R2_GENERATOR
    base.PHASE = R2_PHASE
    base.OBJECTIVE = R2_OBJECTIVE
    base.build_soft_feedback_decision_model_class = build_r2_factory(base, s_first)
    install_checkpoint_contract(base)

    argv = trainer_argv(exp, experiment, config, args)
    latest = Path(str(state["latest_generation"]))
    summary = {
        "event": "s_first_r2_lane_resume",
        "experiment_dir": str(exp),
        "fork_cycle": branch.get("fork_cycle"),
        "latest_cycle": state.get("cycle"),
        "latest_checkpoint": str(latest),
        "global_step": state.get("global_step"),
        "control_space_size": config.get("control_space_size"),
        "register_rank": config.get("register_rank"),
        "cycle_seconds": config.get("cycle_seconds"),
        "cycles_this_run": args.cycles_this_run,
        "pass1": "Qwen([S,prompt]) -> C1 -> R1",
        "pass2": "Qwen([S+R1,prompt]) -> C2 -> R2",
        "pass3": "Qwen([S+R1+R2,prompt]) -> answer",
        "shared_control_bank": True,
        "r2_router": "independent rank-32 router cloned from R1 at cut-over",
        "r2_gate": "tanh scalar, zero at cut-over",
        "old_optimizer_state": "preserved",
        "new_r2_optimizer_state": "created lazily",
        "rng_state": "preserved",
        "heartbeat_seconds": args.heartbeat_seconds,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False), flush=True)
    if args.dry_run:
        print(json.dumps({"event": "s_first_r2_trainer_argv", "argv": argv}, ensure_ascii=False), flush=True)
        return

    log_path = install_compact_emit(base, exp, args.heartbeat_seconds, args.verbose_output)
    print(json.dumps({"event": "s_first_r2_console_mode", "compact_log": str(log_path)}, ensure_ascii=False), flush=True)
    old_argv = sys.argv
    try:
        sys.argv = argv
        base.main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    main()
