#!/usr/bin/env python3
"""Resume a branched K=1000 sparse-register experiment with S on pass 1.

A/B difference from the established K=1000 trainer:

  control lane:  pass1 Qwen(prompt)       -> controller -> pass2 Qwen([S+R,prompt])
  S-first lane:  pass1 Qwen([S,prompt])   -> controller -> pass2 Qwen([S+R,prompt])

No parameters are added or removed.  The inherited head, S, control bank, router,
optimizer state, and RNG state come from the exact fork checkpoint.  Only the
first-pass sensor's operating point changes.  Task identity remains absent from
routing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

TARGET_DEFAULT = Path(r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_v1")
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-branch-v1"
S_FIRST_GENERATOR = "frozen_static_token_prefixed_sensor_plus_self_routing_sparse_overcomplete_shared_control_dictionary_v1"
S_FIRST_PHASE = "frozen_qwen_frozen_head_frozen_static_token_s_conditioned_first_pass_self_organizing_sparse_registers_plus_balanced_pairwise"
S_FIRST_OBJECTIVE = "s_conditioned_first_pass_self_routing_sparse_registers_over_frozen_static_token_three_binary_rehearsals_plus_direct_consensus_plus_relation_balanced_pairwise"
EXPECTED_K = 1000
EXPECTED_RANK = 32
COMPACT_LOG_NAME = "s_first_trainer_compact.log"

DROP_KEYS = {
    "aggregate_active_slots",
    "active_slots",
    "occupied_slots",
    "dormant_slots",
    "aggregate_mean_abs_coefficients",
    "mean_abs_coefficients",
    "slot_task_affinities",
    "slot_owners",
    "shared_slots",
    "single_task_slots",
    "multiply_claimed_slots",
    "exclusively_claimed_slots",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("nanojev_sparse_register_base_for_s_first", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import base trainer: {path}")
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
    exp = exp.expanduser().resolve(strict=True)
    branch = read_json(exp / "s_first_branch.json")
    experiment = read_json(exp / "experiment.json")
    config = read_json(exp / "training_config.json")
    state = read_json(exp / "training_state.json")
    if branch.get("schema_version") != BRANCH_SCHEMA:
        raise RuntimeError(f"not an S-first branch: {exp}")
    if int(config.get("control_space_size", -1)) != EXPECTED_K or int(config.get("register_rank", -1)) != EXPECTED_RANK:
        raise RuntimeError(f"S-first lane must remain K={EXPECTED_K}, rank={EXPECTED_RANK}")
    if config.get("soft_feedback_generator") != S_FIRST_GENERATOR:
        raise RuntimeError("S-first training_config generator contract mismatch")
    if experiment.get("soft_feedback_generator") != S_FIRST_GENERATOR:
        raise RuntimeError("S-first experiment generator contract mismatch")
    latest = state.get("latest_generation")
    if not latest:
        raise RuntimeError("S-first lane has no committed checkpoint")
    cp = Path(str(latest)).expanduser().resolve(strict=True)
    for required in ("head.safetensors", "optimizer.pt", "rng_state.pt", "config.json", "meta.json"):
        if not (cp / required).is_file():
            raise RuntimeError(f"latest S-first checkpoint missing {required}: {cp}")
    cp_cfg = read_json(cp / "config.json")
    if int(cp_cfg.get("main_computer_cycle", -1)) != int(state.get("cycle", -2)):
        raise RuntimeError("S-first latest checkpoint cycle disagrees with training_state.json")
    return branch, experiment, config, state


def build_s_first_factory(base):
    original_factory = base.build_soft_feedback_decision_model_class

    def factory(BaseDecisionModel, *, control_space_size: int, register_rank: int, strength_init: float, active_epsilon: float):
        Parent = original_factory(
            BaseDecisionModel,
            control_space_size=control_space_size,
            register_rank=register_rank,
            strength_init=strength_init,
            active_epsilon=active_epsilon,
        )

        class SFirstDecisionModel(Parent):
            def _static_sensor_helper(self, question_count: int):
                import torch

                raw = self.soft_feedback.base.view(1, -1).expand(question_count, -1)
                direction = torch.tanh(raw)
                direction_rms = torch.sqrt(
                    direction.float().square().mean(dim=-1, keepdim=True) + 1e-8
                ).to(direction.dtype)
                normalized = direction / direction_rms
                strength = torch.sigmoid(self.soft_feedback.strength_logit).to(direction.dtype)
                helper = normalized * self.soft_feedback.embedding_rms.to(direction.dtype) * strength
                zero_rows = raw.detach().abs().amax(dim=-1, keepdim=True) == 0
                return torch.where(zero_rows, torch.zeros_like(helper), helper)

            def forward(self, examples, pad_token):
                import torch

                if self.soft_feedback.mode != "dynamic":
                    return super().forward(examples, pad_token)

                paths, lengths, tokens, attention, path_owner = self._assemble_paths(examples, pad_token)

                # Pass 1 observes the frozen model at the learned static operating point S.
                with torch.no_grad():
                    token_embeds = self.backbone.get_input_embeddings()(tokens)
                    static_by_question = self._static_sensor_helper(len(examples))
                    static_by_path = static_by_question.index_select(0, path_owner).unsqueeze(1)
                    static_by_path = static_by_path.to(dtype=token_embeds.dtype)
                    first_inputs = torch.cat([static_by_path, token_embeds], dim=1)
                    first_attention = torch.cat(
                        [
                            torch.ones(
                                (attention.shape[0], 1),
                                dtype=attention.dtype,
                                device=attention.device,
                            ),
                            attention,
                        ],
                        dim=1,
                    )
                    max_positions = int(
                        getattr(self.backbone.config, "max_position_embeddings", first_inputs.shape[1])
                    )
                    if first_inputs.shape[1] > max_positions:
                        raise RuntimeError(
                            f"S-first prefix exceeds backbone max positions: {first_inputs.shape[1]} > {max_positions}"
                        )
                    first_hidden = self.backbone(
                        inputs_embeds=first_inputs,
                        attention_mask=first_attention,
                        use_cache=False,
                    ).last_hidden_state
                    # One prepended S token shifts each original terminal token by +1.
                    first_leaves = first_hidden[
                        torch.arange(len(paths), device=tokens.device), lengths
                    ]
                    first_logits, _first_valid, first_h, first_path_valid = self._score_leaves(
                        first_leaves, examples
                    )
                    summary, score_stats = self._question_summary(
                        first_h, first_path_valid, first_logits, examples
                    )
                self.last_first_pass_logits = first_logits

                # Existing controller now computes R1 from the S-conditioned observation.
                helper_by_question, dynamic_raw, strength, coefficients = self.soft_feedback.helper(
                    summary, score_stats
                )
                helper_by_path = helper_by_question.index_select(0, path_owner).unsqueeze(1)
                with torch.no_grad():
                    token_embeds = self.backbone.get_input_embeddings()(tokens)
                helper_by_path = helper_by_path.to(dtype=token_embeds.dtype)
                second_inputs = torch.cat([helper_by_path, token_embeds], dim=1)
                second_attention = torch.cat(
                    [
                        torch.ones(
                            (attention.shape[0], 1),
                            dtype=attention.dtype,
                            device=attention.device,
                        ),
                        attention,
                    ],
                    dim=1,
                )
                max_positions = int(
                    getattr(self.backbone.config, "max_position_embeddings", second_inputs.shape[1])
                )
                if second_inputs.shape[1] > max_positions:
                    raise RuntimeError(
                        f"soft-feedback prefix exceeds backbone max positions: {second_inputs.shape[1]} > {max_positions}"
                    )
                second_hidden = self.backbone(
                    inputs_embeds=second_inputs,
                    attention_mask=second_attention,
                    use_cache=False,
                ).last_hidden_state
                second_leaves = second_hidden[
                    torch.arange(len(paths), device=tokens.device), lengths
                ]
                logits, valid, _second_h, _second_path_valid = self._score_leaves(
                    second_leaves, examples
                )

                with torch.no_grad():
                    helper_rms = torch.sqrt(
                        helper_by_question.detach().float().square().mean() + 1e-30
                    )
                    base_rms = torch.sqrt(
                        self.soft_feedback.base.detach().float().square().mean() + 1e-30
                    )
                    dynamic_rms = torch.sqrt(
                        dynamic_raw.detach().float().square().mean() + 1e-30
                    )
                    coeff_abs = coefficients.detach().float().abs()
                    aggregate_mean_abs = (
                        coeff_abs.mean(dim=0)
                        if coeff_abs.numel()
                        else coeff_abs.new_zeros(self.soft_feedback.control_space_size)
                    )
                    aggregate_active = aggregate_mean_abs >= self.soft_feedback.active_epsilon
                    displacement = second_leaves.detach().float() - first_leaves.detach().float()
                    first_rms = torch.sqrt(first_leaves.detach().float().square().mean() + 1e-30)
                    displacement_rms = torch.sqrt(displacement.square().mean() + 1e-30)
                    self.soft_feedback.last_stats = {
                        "mode": "dynamic",
                        "backbone_reads": 2,
                        "first_pass_prefix": "static_S",
                        "first_pass_sensor": "Qwen([S,prompt])",
                        "second_pass_prefix": "static_S_plus_R1",
                        "helper_tokens_per_question": 1,
                        "questions": len(examples),
                        "candidate_paths": len(paths),
                        "embedding_rms": float(self.soft_feedback.embedding_rms.detach().float().item()),
                        "helper_rms": float(helper_rms.item()),
                        "base_raw_rms": float(base_rms.item()),
                        "dynamic_raw_rms": float(dynamic_rms.item()),
                        "strength": float(strength.detach().float().item()),
                        "control_space_size": self.soft_feedback.control_space_size,
                        "register_rank": self.soft_feedback.register_rank,
                        "aggregate_active_slot_count": int(aggregate_active.sum().item()),
                        "orthogonality_penalty": float(
                            self.soft_feedback.last_regularization["orthogonality"].detach().item()
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
                return logits, valid

        SFirstDecisionModel.__name__ = "SFirstSparseRegisterDecisionModel"
        return SFirstDecisionModel

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
                "event": "s_first_heartbeat",
                "cycle": fields.get("cycle", state["cycle"]),
                "elapsed_training_seconds": elapsed,
                "requested_training_seconds": state["requested"],
                "cycle_step": fields.get("cycle_step"),
                "global_step": fields.get("global_step"),
                "active_above_threshold": soft.get("aggregate_active_slot_count"),
                "control_space_size": soft.get("control_space_size", EXPECTED_K),
                "mean_unit_nll": fields.get("mean_unit_nll"),
                "dynamic_raw_rms": soft.get("dynamic_raw_rms"),
                "first_pass_sensor": soft.get("first_pass_sensor"),
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
                    "topology_accuracy", "non_ambiguous_topology_accuracy", "elapsed_seconds"
                )
                if key in fields
            }
            write({"event": event, **keep})
            return
        if event in {
            "sparse_register_usage",
            "cycle_result",
            "checkpoint_committed",
            "checkpoint_generation_deleted",
            "checkpoint_write_start",
            "checkpoint_write_done",
            "current_sparse_register_checkpoint_resolved",
            "resume_optimizer_rng_loaded",
            "trainer_load_done",
            "head_load_start",
        }:
            write({"event": event, **fields})
            return
        # Startup and contract events are sparse and useful; keep them.
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
    base_path = Path(__file__).resolve().parent / "nanojev_code_sparse_register_train.py"
    base = load_module(base_path)

    # Patch only the experiment identity and model factory.  The training loop,
    # sampling, losses, evaluation, checkpointing, and optimizer semantics remain
    # the established sparse-register implementation.
    base.DEFAULT_EXPERIMENT = str(exp)
    base.SOFT_FEEDBACK_GENERATOR = S_FIRST_GENERATOR
    base.PHASE = S_FIRST_PHASE
    base.OBJECTIVE = S_FIRST_OBJECTIVE
    base.build_soft_feedback_decision_model_class = build_s_first_factory(base)

    argv = trainer_argv(exp, experiment, config, args)
    latest = Path(str(state["latest_generation"]))
    summary = {
        "event": "s_first_lane_resume",
        "experiment_dir": str(exp),
        "fork_cycle": branch.get("fork_cycle"),
        "latest_cycle": state.get("cycle"),
        "latest_checkpoint": str(latest),
        "global_step": state.get("global_step"),
        "control_space_size": config.get("control_space_size"),
        "register_rank": config.get("register_rank"),
        "cycle_seconds": config.get("cycle_seconds"),
        "cycles_this_run": args.cycles_this_run,
        "first_pass": "Qwen([S,prompt])",
        "second_pass": "Qwen([S+R1(x),prompt])",
        "optimizer_state": "preserved_from_fork",
        "rng_state": "preserved_from_fork",
        "heartbeat_seconds": args.heartbeat_seconds,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False), flush=True)
    if args.dry_run:
        print(json.dumps({"event": "s_first_trainer_argv", "argv": argv}, ensure_ascii=False), flush=True)
        return

    log_path = install_compact_emit(base, exp, args.heartbeat_seconds, args.verbose_output)
    print(json.dumps({"event": "s_first_console_mode", "compact_log": str(log_path)}, ensure_ascii=False), flush=True)
    old_argv = sys.argv
    try:
        sys.argv = argv
        base.main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    main()
