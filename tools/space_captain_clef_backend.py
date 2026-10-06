#!/usr/bin/env python3
"""Serve the current trained TinyStories+CLEF checkpoint for captain smoke calls.

This backend intentionally uses the same three-backbone evidence extractor and CLEF
head class as the live TinyStories training path.  Qwen and Pythia remain frozen
context providers; the checkpoint supplies the evolving TinyStories and CLEF-head
weights.  The HTTP surface is deliberately tiny so the browser/game can later call
exactly the same boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEFAULT_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_structured_supervision_train_v1"
)


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def composite_checkpoint_sha(checkpoint: Path) -> str:
    payload = {
        "head.safetensors": sha256_file(checkpoint / "head.safetensors"),
        "tinystories.safetensors": sha256_file(checkpoint / "tinystories.safetensors"),
        "meta.json": sha256_file(checkpoint / "meta.json"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def resolve_run_dir(requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve(strict=True)
    if DEFAULT_RUN.is_dir():
        return DEFAULT_RUN.resolve(strict=True)
    runs = Path.home() / "NanoJev" / "runs"
    candidates = []
    if runs.is_dir():
        for candidate in runs.glob("three_backbone_clef_tinystories_structured_supervision_*train_v1"):
            if (candidate / "training_state.json").is_file() and (candidate / "experiment.json").is_file():
                candidates.append(candidate)
    if not candidates:
        raise RuntimeError(
            "could not locate the live TinyStories+CLEF training run; pass --run-dir"
        )
    return max(candidates, key=lambda path: (path / "training_state.json").stat().st_mtime).resolve()


class NullLogger:
    def emit(self, *_args, **_kwargs) -> None:
        return None


class CaptainClefModel:
    def __init__(self, run_dir: Path, checkpoint: Path | None = None) -> None:
        import torch
        from safetensors.torch import load_file

        if not torch.cuda.is_available():
            raise RuntimeError("live captain CLEF backend requires CUDA")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("live captain CLEF backend requires bf16-capable CUDA")

        self.torch = torch
        self.run_dir = Path(run_dir).resolve(strict=True)
        experiment = read_json(self.run_dir / "experiment.json")
        state = read_json(self.run_dir / "training_state.json")
        if checkpoint is None:
            checkpoint = Path(str(state["latest_checkpoint"]))
        self.checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
        for name in ("head.safetensors", "tinystories.safetensors", "meta.json"):
            if not (self.checkpoint / name).is_file():
                raise RuntimeError(f"checkpoint missing {name}: {self.checkpoint}")

        self.checkpoint_id = self.checkpoint.name
        self.checkpoint_sha256 = composite_checkpoint_sha(self.checkpoint)
        self.checkpoint_meta = read_json(self.checkpoint / "meta.json")

        cutover_dir = Path(str(experiment["cutover_dir"])).expanduser().resolve(strict=True)
        cutover = read_json(cutover_dir / "cutover.json")
        source_experiment = Path(
            str(cutover["question_source_experiment"])
        ).expanduser().resolve(strict=True)
        source_manifest = read_json(source_experiment / "experiment.json")
        source = dict(source_manifest.get("source") or {})
        required = ("model", "revision")
        missing = [name for name in required if not source.get(name)]
        if missing:
            raise RuntimeError(f"question-source lineage missing fields: {missing}")

        self.train = load_local_module(
            "space_captain_live_clef_train",
            TOOLS / "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
        )
        self.smoke = self.train.smoke
        self.trainer_script = "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py"
        self.structured_supervision_schema = str(
            (experiment.get("contract") or {}).get("structured_supervision_schema")
            or getattr(self.train, "STRUCTURED_SUPERVISION_SCHEMA", "")
        )
        self.objective_api = load_local_module(
            "space_captain_live_objective_api", TOOLS / "nanojev_objective_api.py"
        )

        torch.backends.cuda.matmul.allow_tf32 = False
        torch.manual_seed(int(experiment.get("seed", 20261003)))
        torch.cuda.manual_seed_all(int(experiment.get("seed", 20261003)))
        self.bundles, _ = self.smoke.load_backbones(
            source=source,
            local_files_only=True,
            logger=NullLogger(),
        )
        hidden_sizes = {label: bundle.hidden_size for label, bundle in self.bundles.items()}
        expected_hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
        if hidden_sizes != expected_hidden:
            raise RuntimeError(
                f"unexpected backbone hidden sizes: expected={expected_hidden} observed={hidden_sizes}"
            )

        self.bundles[self.train.TRAINABLE_LABEL].lm.load_state_dict(
            load_file(str(self.checkpoint / "tinystories.safetensors"), device="cpu"),
            strict=True,
        )
        for bundle in self.bundles.values():
            bundle.lm.eval()

        # Always build the trainer's current residual-tap head.  The trainer owns
        # checkpoint migration from legacy/v1/v2 layouts into the immutable final-layer
        # anchor plus zero-initialized L1/L2 residual adapters.  Reusing that loader keeps
        # captain inference aligned with training instead of duplicating migration logic.
        head, head_hidden_sizes = self.train.build_layer_tap_head(
            torch=torch,
            hidden_sizes=hidden_sizes,
        )
        self.head_load_mode = self.train.load_head_state_with_layer_taps(
            torch=torch,
            head=head,
            state=load_file(str(self.checkpoint / "head.safetensors"), device="cpu"),
        )
        self.head_hidden_sizes = dict(head_hidden_sizes)
        self.head = head.to(device="cuda", dtype=torch.bfloat16).eval()

        hparams = dict(experiment.get("hyperparameters") or {})
        self.evidence_args = SimpleNamespace(
            max_prompt_tokens=int(hparams.get("max_prompt_tokens", self.smoke.DEFAULT_MAX_PROMPT_TOKENS)),
            max_answer_tokens=int(hparams.get("max_answer_tokens", self.smoke.DEFAULT_MAX_ANSWER_TOKENS)),
            prompt_evidence_tokens=int(hparams.get("prompt_evidence_tokens", self.smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)),
            answer_evidence_tokens=int(hparams.get("answer_evidence_tokens", self.smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)),
            path_batch=max(3, int(hparams.get("path_batch", self.smoke.DEFAULT_PATH_BATCH))),
            tinystories_path_batch=int(hparams.get("tinystories_path_batch", 1)),
        )
        self.loaded_unix = time.time()
        self.parameter_counts = {
            "head": sum(int(p.numel()) for p in self.head.parameters()),
            "tinystories": sum(
                int(p.numel()) for p in self.bundles[self.train.TRAINABLE_LABEL].lm.parameters()
            ),
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "game.captainClefHealth.v1",
            "provider": "live-tinystories-clef",
            "trainerScript": self.trainer_script,
            "structuredSupervisionSchema": self.structured_supervision_schema,
            "runDir": str(self.run_dir),
            "checkpointPath": str(self.checkpoint),
            "checkpointId": self.checkpoint_id,
            "checkpointSha256": self.checkpoint_sha256,
            "cycle": self.checkpoint_meta.get("cycle"),
            "reuseEpoch": self.checkpoint_meta.get("reuse_epoch"),
            "parameterCounts": self.parameter_counts,
            "headLoadMode": self.head_load_mode,
            "headHiddenSizes": self.head_hidden_sizes,
            "tinystoriesEvidenceMode": "final-layer-4-plus-residual-layers-1-2",
            "tinystoriesTappedLayers": list(self.train.TINYSTORIES_TAPPED_LAYERS),
            "tinystoriesResidualLayers": list(self.train.TINYSTORIES_RESIDUAL_LAYERS),
            "tinystoriesAnchorLayer": int(self.train.TINYSTORIES_FINAL_LAYER),
            "tinystoriesResidualSourceHiddenSize": int(
                self.train.TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE
            ),
            "loadedUnix": self.loaded_unix,
        }

    def _question(self, row: dict[str, Any], shared_context: str):
        question_id = str(row.get("id") or row.get("questionId") or "")
        prompt = str(row.get("text") or "").strip()
        option_a = str(row.get("optionA") or "").strip().lower()
        option_b = str(row.get("optionB") or "").strip().lower()
        option_a_text = str(row.get("optionAText") or "").strip()
        option_b_text = str(row.get("optionBText") or "").strip()
        semantic_mode = str(row.get("semanticMode") or "").strip()
        accepted_semantic_modes = {
            "grounded-compact-pairwise-v2",
            "machine-grounded-tactical-control-v1",
            "machine-grounded-impact-policy-v1",
        }
        if (
            not question_id
            or not prompt
            or not option_a
            or not option_b
            or option_a == option_b
            or not option_a_text
            or not option_b_text
            or option_a_text == option_b_text
            or semantic_mode not in accepted_semantic_modes
        ):
            raise ValueError(f"invalid captain pairwise question: {row}")
        for option in (option_a, option_b):
            if len(option) > 64 or not option[0].isalpha() or any(
                not (ch.isalnum() or ch == "-") for ch in option
            ):
                raise ValueError(f"captain question contains invalid option id: {row}")
        if len(option_a_text) > 1200 or len(option_b_text) > 1200:
            raise ValueError("captain natural-language option text exceeds live-smoke bound")
        if not shared_context or len(shared_context) > 520:
            raise ValueError("captain shared semantic context missing or exceeds compact bound")
        if len(prompt) > 140:
            raise ValueError("captain compact question text exceeds live-smoke bound")
        # Symbolic planner IDs remain internal bookkeeping only.  The model sees one compact
        # shared natural-language context followed by a short independent A/B comparison.
        # Keeping the shared prefix byte-identical across the 20 judgments enables
        # KV-prefix reuse without changing the decision contract.
        choice_prompt = (
            shared_context.rstrip()
            + "\n"
            + prompt.rstrip()
            + f"\nA: {option_a_text}"
            + f"\nB: {option_b_text}"
            + "\nAnswer A or B:"
        )
        candidates = (
            self.objective_api.ObjectCandidate(
                option_a,
                (self.objective_api.ObjectPath(choice_prompt, " A"),),
            ),
            self.objective_api.ObjectCandidate(
                option_b,
                (self.objective_api.ObjectPath(choice_prompt, " B"),),
            ),
        )
        return self.objective_api.ObjectQuestion(
            question_id=question_id,
            task="captain_pairwise",
            candidates=candidates,
            gold_index=0,  # unused during inference
            stratum="captain",
        )

    @staticmethod
    def _common_prefix_length(sequences: list[list[int]]) -> int:
        if not sequences:
            return 0
        limit = min(len(sequence) for sequence in sequences)
        for index in range(limit):
            token = sequences[0][index]
            if any(sequence[index] != token for sequence in sequences[1:]):
                return index
        return limit

    def _repeat_legacy_cache(self, cache, repeats: int):
        """Repeat a one-row HF causal-LM cache across a suffix batch.

        Recent transformers may return Cache objects while older GPT-Neo/GPT-NeoX
        builds return legacy tuples.  Convert when possible; otherwise signal the
        caller to fall back to the ordinary full-sequence batch path.
        """
        torch = self.torch
        if cache is None:
            raise RuntimeError("backbone did not return a reusable prefix cache")
        if hasattr(cache, "to_legacy_cache"):
            cache = cache.to_legacy_cache()
        if not isinstance(cache, (tuple, list)):
            raise RuntimeError(f"unsupported causal-LM cache type: {type(cache)!r}")

        def repeat(value):
            if torch.is_tensor(value):
                if value.shape[0] != 1:
                    raise RuntimeError(
                        f"prefix cache tensor has unexpected batch dimension {value.shape[0]}"
                    )
                return value.repeat_interleave(repeats, dim=0)
            if isinstance(value, (tuple, list)):
                return tuple(repeat(item) for item in value)
            raise RuntimeError(f"unsupported value in causal-LM cache: {type(value)!r}")

        return tuple(repeat(layer) for layer in cache)

    def _repeat_cache(self, cache, repeats: int):
        """Repeat a one-row HF cache without discarding its native cache type.

        Modern Transformers models commonly return Cache objects whose native batch
        operations carry model-specific bookkeeping that is lost by eager conversion
        to a legacy tuple.  Prefer the native batch repeater when available, then fall
        back to the legacy tensor recursion for older GPT-Neo/GPT-NeoX builds.
        """
        if cache is None:
            raise RuntimeError("backbone did not return a reusable prefix cache")
        native_repeat = getattr(cache, "batch_repeat_interleave", None)
        if callable(native_repeat):
            repeated = native_repeat(repeats)
            return cache if repeated is None else repeated
        return self._repeat_legacy_cache(cache, repeats)

    @staticmethod
    def _cached_suffix_position_kwargs(backbone, *, prefix_len: int, suffix_width: int, batch_size: int, device):
        """Provide explicit cached positions only to backbones that accept them."""
        try:
            parameters = inspect.signature(backbone.forward).parameters
        except (TypeError, ValueError):
            return {}
        import torch
        positions = torch.arange(prefix_len, prefix_len + suffix_width, device=device, dtype=torch.long)
        kwargs = {}
        if "position_ids" in parameters:
            kwargs["position_ids"] = positions.unsqueeze(0).expand(batch_size, -1)
        if "cache_position" in parameters:
            kwargs["cache_position"] = positions
        return kwargs

    def _residual_hidden_states_from_output(self, bundle, output):
        """Return TinyStories residual-source layers plus the final-layer anchor.

        Training keeps the proven final TinyStories layer as the 768-wide base
        evidence and exposes only transformer layers 1 and 2 as a separate
        1536-wide residual source.  Non-TinyStories backbones have no residual
        source and use only their final hidden state.
        """
        final_hidden = output.last_hidden_state
        if bundle.label != self.train.TRAINABLE_LABEL:
            return (), final_hidden

        hidden_states = tuple(output.hidden_states or ())
        transformer_layers = len(hidden_states) - 1
        residual_layers = tuple(int(layer) for layer in self.train.TINYSTORIES_RESIDUAL_LAYERS)
        final_layer = int(self.train.TINYSTORIES_FINAL_LAYER)
        if transformer_layers < max((*residual_layers, final_layer)):
            raise RuntimeError(
                "TinyStories residual-tap contract exceeds available transformer depth: "
                f"residual_layers={residual_layers} final_layer={final_layer} "
                f"available={transformer_layers}"
            )
        residual = tuple(hidden_states[layer] for layer in residual_layers)
        expected = int(self.train.TINYSTORIES_BASE_HIDDEN_SIZE)
        if int(final_hidden.shape[-1]) != expected or any(
            int(hidden.shape[-1]) != expected for hidden in residual
        ):
            raise RuntimeError(
                "TinyStories residual-tap hidden width drifted: "
                f"expected={expected} final={int(final_hidden.shape[-1])} "
                f"residual={[int(hidden.shape[-1]) for hidden in residual]}"
            )
        return residual, final_hidden

    def _extract_bundle_batch(self, bundle, questions, evidence_execution_mode: str = "auto"):
        """Extract batched evidence using the exact current training contract.

        TinyStories keeps final-layer-4 evidence 768-wide and separately carries
        layers 1+2 as 1536-wide residual sources.  Prefix-cache and full-batch
        execution therefore differ only in how backbone states are obtained; both
        paths emit byte-for-byte equivalent evidence fields for the residual head.
        """
        if evidence_execution_mode not in {"auto", "prefix-cache", "full-batch"}:
            raise ValueError(f"unsupported captain evidence execution mode: {evidence_execution_mode}")

        torch = self.torch
        question_rows = []
        occurrence_counts = []
        flat_rows = []
        for question_index, question in enumerate(questions):
            rows, occurrence_count = self.smoke._model_sequences(
                bundle,
                question,
                max_prompt_tokens=int(self.evidence_args.max_prompt_tokens),
                max_answer_tokens=int(self.evidence_args.max_answer_tokens),
            )
            if len(question.candidates) != 2:
                raise RuntimeError(
                    f"captain batched inference requires binary questions: {question.question_id}"
                )
            question_rows.append(rows)
            occurrence_counts.append(int(occurrence_count))
            flat_rows.extend((question_index, row) for row in rows)
        if not flat_rows:
            raise RuntimeError("captain batched inference produced no model paths")

        question_count = len(questions)

        def new_accumulators():
            return (
                [[] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
                [[[] for _ in range(2)] for _ in range(question_count)],
            )

        (
            memory_parts,
            option_answer,
            option_predictor,
            option_terminal,
            option_prompt,
            option_lexical,
            option_logp,
            residual_memory_parts,
            residual_option_answer,
            residual_option_predictor,
            residual_option_terminal,
            residual_option_prompt,
        ) = new_accumulators()
        device = bundle.output_weight.device
        pad = int(bundle.tokenizer.pad_token_id)
        path_batch = max(int(self.evidence_args.path_batch), min(64, len(flat_rows)))
        forward_batches = 0
        prefix_cache_used = False
        shared_prefix_tokens = 0
        prefix_cache_candidate_tokens = 0
        prefix_cache_failure_reason = None

        residual_tap = bundle.label == self.train.TRAINABLE_LABEL

        def consume_row(question_index, row, residual_hidden_rows, final_hidden_row, token_row):
            plen = len(row["prompt_ids"])
            alen = len(row["answer_ids"])
            prompt_idx = self.smoke.balanced_indices(
                0, plen, int(self.evidence_args.prompt_evidence_tokens)
            )
            answer_idx = self.smoke.balanced_indices(
                plen, plen + alen, int(self.evidence_args.answer_evidence_tokens)
            )
            evidence_idx = prompt_idx + answer_idx

            path_logp, predictor_mean = self.smoke._continuation_mean_logp(
                torch=torch,
                hidden=final_hidden_row,
                tokens=token_row,
                prompt_length=plen,
                answer_length=alen,
                output_weight=bundle.output_weight,
            )
            memory_parts[question_index].append(
                final_hidden_row[evidence_idx].detach().clone()
            )
            prompt_mean = final_hidden_row[:plen].mean(dim=0).detach().clone()
            answer_mean = final_hidden_row[plen:plen + alen].mean(dim=0).detach().clone()
            terminal = final_hidden_row[plen + alen - 1].detach().clone()
            answer_token_ids = token_row[plen:plen + alen]
            lexical = bundle.output_weight[answer_token_ids].mean(dim=0).detach().clone()
            predictor_mean = predictor_mean.detach().clone()
            path_logp = path_logp.detach().clone()

            residual_prompt_mean = None
            residual_answer_mean = None
            residual_predictor_mean = None
            residual_terminal = None
            if residual_tap:
                if len(residual_hidden_rows) != len(self.train.TINYSTORIES_RESIDUAL_LAYERS):
                    raise RuntimeError(
                        "TinyStories residual source layer count drifted: "
                        f"expected={len(self.train.TINYSTORIES_RESIDUAL_LAYERS)} "
                        f"observed={len(residual_hidden_rows)}"
                    )
                residual_hidden = torch.cat(residual_hidden_rows, dim=-1)
                expected_residual = int(self.train.TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE)
                if int(residual_hidden.shape[-1]) != expected_residual:
                    raise RuntimeError(
                        "TinyStories residual source width drifted: "
                        f"expected={expected_residual} observed={int(residual_hidden.shape[-1])}"
                    )
                residual_memory_parts[question_index].append(
                    residual_hidden[evidence_idx].detach().clone()
                )
                residual_prompt_mean = residual_hidden[:plen].mean(dim=0).detach().clone()
                residual_answer_mean = residual_hidden[plen:plen + alen].mean(dim=0).detach().clone()
                residual_terminal = residual_hidden[plen + alen - 1].detach().clone()
                residual_predictor_mean = torch.cat([
                    self.train._continuation_predictor_mean(
                        hidden, prompt_length=plen, answer_length=alen
                    )
                    for hidden in residual_hidden_rows
                ], dim=-1).detach().clone()

            for candidate in row["candidates"]:
                option_prompt[question_index][candidate].append(prompt_mean)
                option_answer[question_index][candidate].append(answer_mean)
                option_predictor[question_index][candidate].append(predictor_mean)
                option_terminal[question_index][candidate].append(terminal)
                option_lexical[question_index][candidate].append(lexical)
                option_logp[question_index][candidate].append(path_logp)
                if residual_tap:
                    residual_option_prompt[question_index][candidate].append(residual_prompt_mean)
                    residual_option_answer[question_index][candidate].append(residual_answer_mean)
                    residual_option_predictor[question_index][candidate].append(residual_predictor_mean)
                    residual_option_terminal[question_index][candidate].append(residual_terminal)

        prompt_sequences = [list(row["prompt_ids"]) for _, row in flat_rows]
        prefix_len = self._common_prefix_length(prompt_sequences)
        prefix_cache_candidate_tokens = int(prefix_len)
        prefix_cache_eligible = len(flat_rows) <= 64 and prefix_len >= 8
        if evidence_execution_mode != "full-batch" and prefix_cache_eligible:
            try:
                with torch.no_grad():
                    prefix_tokens = torch.tensor(
                        [prompt_sequences[0][:prefix_len]], dtype=torch.long, device=device
                    )
                    prefix_attention = torch.ones_like(prefix_tokens, dtype=torch.long)
                    prefix_output = bundle.backbone(
                        input_ids=prefix_tokens,
                        attention_mask=prefix_attention,
                        use_cache=True,
                        output_hidden_states=residual_tap,
                        return_dict=True,
                    )
                    cache = self._repeat_cache(prefix_output.past_key_values, len(flat_rows))
                    suffixes = [
                        row["prompt_ids"][prefix_len:] + row["answer_ids"]
                        for _, row in flat_rows
                    ]
                    suffix_width = max(len(sequence) for sequence in suffixes)
                    suffix_tokens = torch.full(
                        (len(flat_rows), suffix_width), pad, dtype=torch.long, device=device
                    )
                    full_attention = torch.zeros(
                        (len(flat_rows), prefix_len + suffix_width),
                        dtype=torch.long,
                        device=device,
                    )
                    full_attention[:, :prefix_len] = 1
                    for batch_index, sequence in enumerate(suffixes):
                        suffix_tokens[batch_index, :len(sequence)] = torch.tensor(
                            sequence, dtype=torch.long, device=device
                        )
                        full_attention[batch_index, prefix_len:prefix_len + len(sequence)] = 1
                    cached_position_kwargs = self._cached_suffix_position_kwargs(
                        bundle.backbone,
                        prefix_len=prefix_len,
                        suffix_width=suffix_width,
                        batch_size=len(flat_rows),
                        device=device,
                    )
                    suffix_output = bundle.backbone(
                        input_ids=suffix_tokens,
                        attention_mask=full_attention,
                        past_key_values=cache,
                        use_cache=False,
                        output_hidden_states=residual_tap,
                        return_dict=True,
                        **cached_position_kwargs,
                    )
                    prefix_residual, prefix_final = self._residual_hidden_states_from_output(
                        bundle, prefix_output
                    )
                    suffix_residual, suffix_final = self._residual_hidden_states_from_output(
                        bundle, suffix_output
                    )
                    staged = []
                    for batch_index, (question_index, row) in enumerate(flat_rows):
                        suffix_len = len(suffixes[batch_index])
                        full_residual = tuple(
                            torch.cat(
                                [prefix_hidden[0].detach(), suffix_hidden[batch_index, :suffix_len]],
                                dim=0,
                            )
                            for prefix_hidden, suffix_hidden in zip(prefix_residual, suffix_residual)
                        )
                        full_final = torch.cat(
                            [prefix_final[0].detach(), suffix_final[batch_index, :suffix_len]],
                            dim=0,
                        )
                        full_tokens = torch.tensor(
                            row["prompt_ids"] + row["answer_ids"],
                            dtype=torch.long,
                            device=device,
                        )
                        staged.append(
                            (question_index, row, full_residual, full_final, full_tokens)
                        )
                    for staged_row in staged:
                        consume_row(*staged_row)
                    forward_batches = 2
                    prefix_cache_used = True
                    shared_prefix_tokens = int(prefix_len)
                    del (
                        prefix_output,
                        suffix_output,
                        prefix_residual,
                        suffix_residual,
                        prefix_final,
                        suffix_final,
                        prefix_tokens,
                        prefix_attention,
                        suffix_tokens,
                        full_attention,
                        cache,
                        staged,
                        cached_position_kwargs,
                    )
            except Exception as exc:
                if evidence_execution_mode == "prefix-cache":
                    raise RuntimeError(
                        f"forced prefix-cache execution failed for {bundle.label}: {type(exc).__name__}: {exc}"
                    ) from exc
                (
                    memory_parts,
                    option_answer,
                    option_predictor,
                    option_terminal,
                    option_prompt,
                    option_lexical,
                    option_logp,
                    residual_memory_parts,
                    residual_option_answer,
                    residual_option_predictor,
                    residual_option_terminal,
                    residual_option_prompt,
                ) = new_accumulators()
                forward_batches = 0
                prefix_cache_used = False
                shared_prefix_tokens = 0
                prefix_cache_failure_reason = f"{type(exc).__name__}: {exc}"[:320]

        if evidence_execution_mode == "prefix-cache" and not prefix_cache_used:
            raise RuntimeError(
                f"forced prefix-cache execution unavailable for {bundle.label}: "
                f"paths={len(flat_rows)} common_prefix_tokens={prefix_len}"
            )

        if not prefix_cache_used:
            with torch.no_grad():
                for offset in range(0, len(flat_rows), path_batch):
                    chunk = flat_rows[offset: offset + path_batch]
                    lengths = [
                        len(row["prompt_ids"]) + len(row["answer_ids"])
                        for _, row in chunk
                    ]
                    width = max(lengths)
                    tokens = torch.full(
                        (len(chunk), width), pad, dtype=torch.long, device=device
                    )
                    attention = torch.zeros(
                        (len(chunk), width), dtype=torch.long, device=device
                    )
                    for batch_index, (_question_index, row) in enumerate(chunk):
                        sequence = row["prompt_ids"] + row["answer_ids"]
                        tokens[batch_index, :len(sequence)] = torch.tensor(
                            sequence, dtype=torch.long, device=device
                        )
                        attention[batch_index, :len(sequence)] = 1
                    output = bundle.backbone(
                        input_ids=tokens,
                        attention_mask=attention,
                        use_cache=False,
                        output_hidden_states=residual_tap,
                        return_dict=True,
                    )
                    forward_batches += 1
                    residual_hidden, final_hidden = self._residual_hidden_states_from_output(
                        bundle, output
                    )
                    for batch_index, (question_index, row) in enumerate(chunk):
                        sequence_len = len(row["prompt_ids"]) + len(row["answer_ids"])
                        consume_row(
                            question_index,
                            row,
                            tuple(
                                hidden[batch_index, :sequence_len]
                                for hidden in residual_hidden
                            ),
                            final_hidden[batch_index, :sequence_len],
                            tokens[batch_index, :sequence_len],
                        )
                    del output, residual_hidden, final_hidden, tokens, attention

        def stack_mean(groups, label: str, question_id: str):
            values = []
            for candidate, items in enumerate(groups):
                if not items:
                    raise RuntimeError(
                        f"{bundle.label} missing {label} evidence for {question_id} candidate {candidate}"
                    )
                values.append(torch.stack(items, dim=0).mean(dim=0))
            return torch.stack(values, dim=0)

        evidence_rows = []
        stats_rows = []
        for question_index, question in enumerate(questions):
            memory = torch.cat(memory_parts[question_index], dim=0)
            row = {
                "memory": memory,
                "option_context": stack_mean(
                    option_answer[question_index], "answer", question.question_id
                ),
                "option_predictor": stack_mean(
                    option_predictor[question_index], "predictor", question.question_id
                ),
                "option_terminal": stack_mean(
                    option_terminal[question_index], "terminal", question.question_id
                ),
                "option_question": stack_mean(
                    option_prompt[question_index], "prompt", question.question_id
                ),
                "option_lexical": stack_mean(
                    option_lexical[question_index], "lexical", question.question_id
                ),
                "option_logp": stack_mean(
                    option_logp[question_index], "logp", question.question_id
                ),
                "global": memory.mean(dim=0),
            }
            if residual_tap:
                residual_memory = torch.cat(residual_memory_parts[question_index], dim=0)
                if int(residual_memory.shape[0]) != int(memory.shape[0]):
                    raise RuntimeError("TinyStories base/residual memory token counts diverged")
                row.update({
                    "residual_source_memory": residual_memory,
                    "residual_source_option_context": stack_mean(
                        residual_option_answer[question_index], "residual answer", question.question_id
                    ),
                    "residual_source_option_predictor": stack_mean(
                        residual_option_predictor[question_index], "residual predictor", question.question_id
                    ),
                    "residual_source_option_terminal": stack_mean(
                        residual_option_terminal[question_index], "residual terminal", question.question_id
                    ),
                    "residual_source_option_question": stack_mean(
                        residual_option_prompt[question_index], "residual prompt", question.question_id
                    ),
                    "residual_source_global": residual_memory.mean(dim=0),
                })
            evidence_rows.append(row)
            stats_rows.append({
                "path_count": occurrence_counts[question_index],
                "unique_path_count": len(question_rows[question_index]),
                "memory_tokens": int(memory.shape[0]),
                "shared_prefix_tokens": shared_prefix_tokens,
                "shared_prefix_cache_used": prefix_cache_used,
            })
        return (
            evidence_rows,
            stats_rows,
            forward_batches,
            prefix_cache_used,
            shared_prefix_tokens,
            prefix_cache_candidate_tokens,
            prefix_cache_failure_reason,
        )

    def _pack_evidence_batch(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        torch = self.torch
        if not rows:
            raise RuntimeError("cannot pack empty captain evidence batch")
        candidate_counts = {int(row["option_context"].shape[0]) for row in rows}
        if candidate_counts != {2}:
            raise RuntimeError(f"captain evidence candidate mismatch: {candidate_counts}")
        hidden = int(rows[0]["memory"].shape[-1])
        max_memory = max(int(row["memory"].shape[0]) for row in rows)
        device = rows[0]["memory"].device
        dtype = rows[0]["memory"].dtype
        memory = torch.zeros(
            (len(rows), max_memory, hidden), device=device, dtype=dtype
        )
        memory_valid = torch.zeros(
            (len(rows), max_memory), device=device, dtype=torch.bool
        )
        for index, row in enumerate(rows):
            count = int(row["memory"].shape[0])
            memory[index, :count] = row["memory"]
            memory_valid[index, :count] = True
        packed = {
            "memory": memory,
            "memory_valid": memory_valid,
            "option_context": torch.stack([row["option_context"] for row in rows], dim=0),
            "option_predictor": torch.stack([row["option_predictor"] for row in rows], dim=0),
            "option_terminal": torch.stack([row["option_terminal"] for row in rows], dim=0),
            "option_question": torch.stack([row["option_question"] for row in rows], dim=0),
            "option_lexical": torch.stack([row["option_lexical"] for row in rows], dim=0),
            "option_logp": torch.stack([row["option_logp"] for row in rows], dim=0),
            "global": torch.stack([row["global"] for row in rows], dim=0),
        }
        residual_keys = [
            "residual_source_memory",
            "residual_source_option_context",
            "residual_source_option_predictor",
            "residual_source_option_terminal",
            "residual_source_option_question",
            "residual_source_global",
        ]
        has_residual = [key in rows[0] for key in residual_keys]
        if any(has_residual):
            if not all(has_residual) or any(
                any((key in row) != has_residual[index] for index, key in enumerate(residual_keys))
                for row in rows
            ):
                raise RuntimeError("captain residual evidence fields are incomplete")
            residual_hidden = int(rows[0]["residual_source_memory"].shape[-1])
            residual_memory = torch.zeros(
                (len(rows), max_memory, residual_hidden), device=device, dtype=dtype
            )
            for index, row in enumerate(rows):
                count = int(row["residual_source_memory"].shape[0])
                if count != int(row["memory"].shape[0]):
                    raise RuntimeError("captain base/residual memory lengths disagree")
                residual_memory[index, :count] = row["residual_source_memory"]
            packed.update({
                "residual_source_memory": residual_memory,
                "residual_source_option_context": torch.stack(
                    [row["residual_source_option_context"] for row in rows], dim=0
                ),
                "residual_source_option_predictor": torch.stack(
                    [row["residual_source_option_predictor"] for row in rows], dim=0
                ),
                "residual_source_option_terminal": torch.stack(
                    [row["residual_source_option_terminal"] for row in rows], dim=0
                ),
                "residual_source_option_question": torch.stack(
                    [row["residual_source_option_question"] for row in rows], dim=0
                ),
                "residual_source_global": torch.stack(
                    [row["residual_source_global"] for row in rows], dim=0
                ),
            })
        return packed

    def _batched_head_forward(self, evidence: dict[str, dict[str, Any]]):
        """Vectorized equivalent of the trained CLEF head's single-question forward."""
        torch = self.torch
        import torch.nn.functional as F

        head = self.head
        if set(evidence) != set(head.labels):
            raise RuntimeError(
                f"head evidence labels mismatch: expected={head.labels} observed={tuple(evidence)}"
            )
        option_count = None
        memory_parts = []
        memory_masks = []
        option_query_parts = []
        field_parts = []
        global_parts = []
        lexical_parts = []
        logp_prior_parts = []
        for label in head.labels:
            row = evidence[label]
            module = head.backbone_modules[label]

            def residual_corrected(field: str):
                base_value = row[field]
                if label != self.train.TRAINABLE_LABEL or not hasattr(head, "tinystories_residual"):
                    return base_value
                source_key = f"residual_source_{field}"
                if source_key not in row:
                    raise RuntimeError(f"TinyStories residual evidence missing: {source_key}")
                source = head._normalize_residual_source(row[source_key])
                correction = head.tinystories_residual[field](source).to(
                    device=base_value.device, dtype=base_value.dtype
                )
                return base_value + correction

            memory = module.hidden_norm(residual_corrected("memory"))
            option_context = module.hidden_norm(residual_corrected("option_context"))
            option_predictor = module.hidden_norm(residual_corrected("option_predictor"))
            option_terminal = module.hidden_norm(residual_corrected("option_terminal"))
            option_question = module.hidden_norm(residual_corrected("option_question"))
            lexical = module.hidden_norm(row["option_lexical"])
            global_vector = module.hidden_norm(residual_corrected("global"))
            option_logp = row["option_logp"].to(
                device=option_context.device, dtype=option_context.dtype
            ).reshape(option_context.shape[0], option_context.shape[1], 1)
            centered_logp = option_logp - option_logp.mean(dim=1, keepdim=True)
            if option_count is None:
                option_count = int(option_context.shape[1])
            elif option_count != int(option_context.shape[1]):
                raise RuntimeError("backbone evidence disagrees on candidate count")
            embed = head.model_embeddings[label]
            memory_parts.append(module.memory_projection(memory) + embed)
            memory_masks.append(row["memory_valid"])
            option_query_parts.append(
                module.option_context_projection(
                    (option_context + option_predictor + option_terminal) / math.sqrt(3.0)
                )
                + module.option_lexical_projection(lexical)
                + module.option_question_projection(option_question)
                + module.option_logp_projection(centered_logp)
            )
            field_parts.append(module.question_projection(option_question.mean(dim=1)))
            global_parts.append(module.global_projection(global_vector))
            lexical_parts.append(module.option_lexical_projection(lexical))
            logp_prior_parts.append(module.option_logp_scalar(centered_logp).squeeze(-1))

        scale = 1.0 / math.sqrt(float(len(head.labels)))
        memory = torch.cat(memory_parts, dim=1)
        memory_valid = torch.cat(memory_masks, dim=1)
        options = torch.stack(option_query_parts, dim=0).sum(dim=0) * scale
        lexical = torch.stack(lexical_parts, dim=0).sum(dim=0) * scale
        logp_prior = torch.stack(logp_prior_parts, dim=0).sum(dim=0) * scale
        base_field = torch.stack(field_parts, dim=0).sum(dim=0) * scale
        global_vector = torch.stack(global_parts, dim=0).sum(dim=0) * scale

        routed = options
        for layer in head.evidence_layers:
            q = layer.query_norm(routed)
            m = layer.memory_norm(memory)
            routed_delta, _ = layer.attention(
                q,
                m,
                m,
                key_padding_mask=~memory_valid,
                need_weights=False,
            )
            queries = routed + routed_delta
            routed = queries + layer.ff(layer.ff_norm(queries))

        routing_weights = torch.softmax(
            torch.sum(routed * base_field.unsqueeze(1), dim=-1)
            / math.sqrt(float(head.width)),
            dim=1,
        )
        option_summary = torch.sum(routing_weights.unsqueeze(-1) * routed, dim=1)
        field = base_field + head.option_summary_norm(option_summary) + global_vector
        field = field + head.type_embedding.weight[1]
        field = field + head.fusion_ff(head.fusion_norm(field))
        target = field.unsqueeze(1)
        for layer in head.layers:
            target = layer(
                target,
                memory,
                memory_key_padding_mask=~memory_valid,
            )
        field = head.field_norm(target[:, 0])

        normalized_lexical = F.normalize(lexical, dim=-1)
        normalized_field = F.normalize(field, dim=-1).unsqueeze(1).expand_as(lexical)
        lexical_prior = F.cosine_similarity(
            normalized_lexical,
            normalized_field,
            dim=-1,
        )
        prior_scale = head.prior_logit_scale.clamp(max=math.log(100.0)).exp()
        option_values = head.option_norm(routed)
        repeated_field = field.unsqueeze(1).expand_as(option_values)
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
        residual = head.residual_scorer(features).squeeze(-1)
        joint_scale = head.joint_logit_scale.clamp(max=math.log(100.0)).exp()
        joint = joint_scale * cosine + residual
        return (
            prior_scale * lexical_prior
            + logp_prior
            + torch.sigmoid(head.residual_gate) * joint
        )

    def evaluate_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        checkpoint = dict(payload.get("checkpoint") or {})
        requested_id = str(checkpoint.get("checkpointId") or "")
        requested_sha = str(checkpoint.get("sha256") or "")
        if requested_id and requested_id != self.checkpoint_id:
            raise ValueError(
                f"request checkpoint id mismatch: loaded={self.checkpoint_id} requested={requested_id}"
            )
        if requested_sha and requested_sha != self.checkpoint_sha256:
            raise ValueError(
                "request checkpoint sha mismatch: "
                f"loaded={self.checkpoint_sha256} requested={requested_sha}"
            )
        execution = dict(payload.get("execution") or {})
        evidence_execution_mode = str(execution.get("evidenceMode") or "auto").strip().lower()
        if evidence_execution_mode not in {"auto", "prefix-cache", "full-batch"}:
            raise ValueError(
                "captain execution.evidenceMode must be auto, prefix-cache, or full-batch"
            )
        semantic_context = dict(payload.get("semanticContext") or {})
        if str(semantic_context.get("mode") or "") != "compact-shared-context-v2":
            raise ValueError("captain request missing compact shared semantic context")
        shared_context = str(semantic_context.get("text") or "").strip()
        question_rows = list(payload.get("questions") or [])
        if not question_rows:
            raise ValueError("captain request contains no questions")
        if len(question_rows) > 64:
            raise ValueError("captain request exceeds 64-question live smoke bound")
        questions = [self._question(row, shared_context) for row in question_rows]

        torch = self.torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        started = time.perf_counter()
        by_label = {}
        evidence_stats = {}
        prefix_cache_by_label = {}
        shared_prefix_tokens_by_label = {}
        prefix_cache_candidate_tokens_by_label = {}
        prefix_cache_failure_by_label = {}
        backbone_forward_batches = 0
        with torch.inference_mode():
            for label, bundle in self.bundles.items():
                (
                    rows,
                    stats,
                    forward_batches,
                    prefix_cache_used,
                    shared_prefix_tokens,
                    prefix_cache_candidate_tokens,
                    prefix_cache_failure_reason,
                ) = self._extract_bundle_batch(
                    bundle, questions, evidence_execution_mode=evidence_execution_mode
                )
                by_label[label] = self._pack_evidence_batch(rows)
                evidence_stats[label] = stats
                prefix_cache_by_label[label] = bool(prefix_cache_used)
                shared_prefix_tokens_by_label[label] = int(shared_prefix_tokens)
                prefix_cache_candidate_tokens_by_label[label] = int(prefix_cache_candidate_tokens)
                prefix_cache_failure_by_label[label] = prefix_cache_failure_reason
                backbone_forward_batches += int(forward_batches)
            logits = self._batched_head_forward(by_label)
            probabilities = logits.detach().float().softmax(dim=-1).cpu()
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        total_ms = (time.perf_counter() - started) * 1000.0

        answers = []
        for index, question in enumerate(questions):
            candidate_ids = [str(candidate.candidate_id) for candidate in question.candidates]
            row_probabilities = probabilities[index].tolist()
            predicted_index = max(
                range(len(row_probabilities)), key=lambda candidate: row_probabilities[candidate]
            )
            ordered = sorted(row_probabilities, reverse=True)
            margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
            answers.append({
                "questionId": question.question_id,
                "choice": candidate_ids[predicted_index],
                "candidateIds": candidate_ids,
                "probabilities": [float(value) for value in row_probabilities],
                "margin": float(margin),
            })

        if prefix_cache_by_label and all(prefix_cache_by_label.values()):
            evidence_execution_mode_actual = "prefix-cache"
        elif prefix_cache_by_label and not any(prefix_cache_by_label.values()):
            evidence_execution_mode_actual = "full-batch"
        else:
            evidence_execution_mode_actual = "mixed"

        del by_label, logits
        return {
            "schema": "game.captainDecisionResponse.v6",
            "checkpointId": self.checkpoint_id,
            "checkpointSha256": self.checkpoint_sha256,
            "provider": "live-tinystories-clef",
            "modelLatencyMs": float(total_ms),
            "amortizedQuestionLatencyMs": float(total_ms / len(answers)),
            "captainModelCallCount": 1,
            "clefHeadForwardCount": 1,
            "independentJudgmentCount": len(answers),
            "backboneForwardBatchCount": int(backbone_forward_batches),
            "batchingMode": "independent-pairwise-questions",
            "evidenceExecutionModeRequested": evidence_execution_mode,
            "evidenceExecutionModeActual": evidence_execution_mode_actual,
            "semanticChoicePromptMode": "compact-shared-context-a-b-v2",
            "semanticContextMode": "compact-shared-context-v2",
            "sharedContextChars": len(shared_context),
            "meanQuestionTextChars": float(sum(len(str(row.get("text") or "")) for row in question_rows) / len(question_rows)),
            "sharedPrefixCacheUsed": bool(prefix_cache_by_label) and all(prefix_cache_by_label.values()),
            "sharedPrefixCacheByBackbone": prefix_cache_by_label,
            "sharedPrefixTokensByBackbone": shared_prefix_tokens_by_label,
            "sharedPrefixCandidateTokensByBackbone": prefix_cache_candidate_tokens_by_label,
            "sharedPrefixCacheFailureByBackbone": prefix_cache_failure_by_label,
            "answers": answers,
            "evidenceStats": evidence_stats,
        }


class CaptainServer(HTTPServer):
    model: CaptainClefModel


class Handler(BaseHTTPRequestHandler):
    server_version = "MainComputerCaptainCLEF/1"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        print(format % args, file=sys.stderr, flush=True)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, self.server.model.health())
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/captain/evaluate":
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if length <= 0 or length > 2_000_000:
                raise ValueError("invalid captain request content length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            response = self.server.model.evaluate_request(payload)
            self._json(200, response)
        except Exception as exc:
            self._json(400, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the live TinyStories+CLEF captain checkpoint")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--checkpoint", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    run_dir = resolve_run_dir(args.run_dir or None)
    checkpoint = Path(args.checkpoint).expanduser().resolve(strict=True) if args.checkpoint else None
    model = CaptainClefModel(run_dir, checkpoint)
    server = CaptainServer((args.host, int(args.port)), Handler)
    server.model = model
    print(json.dumps({
        "event": "space_captain_clef_backend_ready",
        "host": args.host,
        "port": server.server_address[1],
        **model.health(),
    }), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
