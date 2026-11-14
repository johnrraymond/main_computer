#!/usr/bin/env python3
"""Shared primitives for the TinyStories-only center-expansion CLEF experiment.

The transform is intentionally parameter-free. Text is tokenized once; token
embeddings are then copied directly so the prompt occupies an exact configured
vector budget. Copy density increases linearly from both edges toward the
middle. No text duplication or re-tokenization occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any, Sequence

TOOLS = Path(__file__).resolve().parent


def load_local_module(name: str, path: Path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mature = load_local_module(
    "nanojev_center_expand_structured_library",
    TOOLS / "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
)
smoke = mature.smoke
base = mature.base

TINYSTORIES_MODEL = "roneneldan/TinyStories-33M"
TINYSTORIES_LABEL = "tinystories"
TINYSTORIES_HIDDEN = mature.TINYSTORIES_BASE_HIDDEN_SIZE
RESIDUAL_LAYERS = mature.TINYSTORIES_RESIDUAL_LAYERS
FINAL_LAYER = mature.TINYSTORIES_FINAL_LAYER
RESIDUAL_SOURCE_HIDDEN = mature.TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE
EXPANSION_SCHEMA = "tinystories-center-triangular-vector-expansion-v1"
MICRO_HEAD_SCHEMA = "tinystories-only-clef-residual-v3-from-three-backbone-champion-v1"


@dataclass
class ExpansionStats:
    source_tokens: int
    expanded_tokens: int
    min_copies: int
    max_copies: int
    mean_copies: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def triangular_replication_counts(source_length: int, target_length: int) -> list[int]:
    """Return exact-budget, center-heavy integer replication counts.

    Every source position survives once. Remaining slots are apportioned by a
    symmetric tent weight that rises linearly from zero at either edge to its
    maximum at the center. Largest-remainder apportionment guarantees that the
    integer counts sum *exactly* to ``target_length`` for arbitrary lengths.
    """
    n = int(source_length)
    target = int(target_length)
    if n <= 0:
        raise ValueError("source_length must be positive")
    if target < n:
        raise ValueError(
            f"center expansion cannot shrink: source_length={n} target_length={target}"
        )
    if target == n:
        return [1] * n
    if n == 1:
        return [target]

    # Integer tent weights are numerically stable for any practical context
    # length and make the desired constant edge->center slope explicit.
    weights = [min(i, n - 1 - i) for i in range(n)]
    weight_sum = sum(weights)
    extra = target - n

    # n=2 has no unique interior. Treat both positions symmetrically.
    if weight_sum == 0:
        base_extra, remainder = divmod(extra, n)
        counts = [1 + base_extra] * n
        # Deterministic symmetric-as-possible apportionment.
        for i in range(remainder):
            counts[i if i % 2 == 0 else n - 1 - i // 2] += 1
        if sum(counts) != target:
            raise RuntimeError("uniform two-position apportionment drifted")
        return counts

    floors = [(extra * w) // weight_sum for w in weights]
    remainders = [(extra * w) % weight_sum for w in weights]
    counts = [1 + value for value in floors]
    remaining = target - sum(counts)
    center = (n - 1) / 2.0
    order = sorted(
        range(n),
        key=lambda i: (-remainders[i], -weights[i], abs(i - center), i),
    )
    for i in order[:remaining]:
        counts[i] += 1

    if sum(counts) != target:
        raise RuntimeError(
            f"exact-budget expansion invariant failed: {sum(counts)} != {target}"
        )
    if min(counts) < 1:
        raise RuntimeError("center expansion dropped a source vector")
    # Apart from unavoidable one-slot largest-remainder ties, copy density must
    # not decrease as we approach either center.
    left = counts[: (n + 1) // 2]
    right = list(reversed(counts[n // 2 :]))
    if any(b < a for a, b in zip(left, left[1:])):
        raise RuntimeError(f"left copy gradient is not center-monotone: {counts}")
    if any(b < a for a, b in zip(right, right[1:])):
        raise RuntimeError(f"right copy gradient is not center-monotone: {counts}")
    return counts


def expand_embeddings_exact(torch, embeddings, target_length: int):
    """Copy embedding rows so the result has exactly ``target_length`` rows."""
    if embeddings.ndim != 2:
        raise ValueError(f"expected [tokens, hidden] embeddings, got {tuple(embeddings.shape)}")
    counts = triangular_replication_counts(int(embeddings.shape[0]), int(target_length))
    repeats = torch.tensor(counts, dtype=torch.long, device=embeddings.device)
    expanded = torch.repeat_interleave(embeddings, repeats, dim=0)
    if int(expanded.shape[0]) != int(target_length):
        raise RuntimeError("repeat_interleave expansion length drifted")
    stats = ExpansionStats(
        source_tokens=int(embeddings.shape[0]),
        expanded_tokens=int(expanded.shape[0]),
        min_copies=min(counts),
        max_copies=max(counts),
        mean_copies=float(sum(counts)) / len(counts),
    )
    return expanded, counts, stats


def build_micro_head(*, torch):
    """Build the champion-compatible CLEF head with TinyStories as its only LM."""
    head, hidden_sizes = mature.build_layer_tap_head(
        torch=torch,
        hidden_sizes={TINYSTORIES_LABEL: TINYSTORIES_HIDDEN},
    )
    if hidden_sizes != {TINYSTORIES_LABEL: TINYSTORIES_HIDDEN}:
        raise RuntimeError(f"micro head hidden-size contract drifted: {hidden_sizes}")
    return head


def micro_head_state_spec(*, torch) -> dict[str, tuple[int, ...]]:
    with torch.device("meta"):
        head = build_micro_head(torch=torch)
    return {name: tuple(value.shape) for name, value in head.state_dict().items()}


def select_micro_state_from_champion(*, torch, source_state: dict[str, Any]):
    """Select every shape-compatible target tensor from a mature champion state."""
    target_spec = micro_head_state_spec(torch=torch)
    selected = {}
    missing = []
    mismatched = []
    for name, shape in target_spec.items():
        tensor = source_state.get(name)
        if tensor is None:
            missing.append(name)
            continue
        if tuple(tensor.shape) != tuple(shape):
            mismatched.append((name, tuple(tensor.shape), tuple(shape)))
            continue
        selected[name] = tensor.detach().cpu().contiguous()
    if missing or mismatched:
        raise RuntimeError(
            "champion cannot seed TinyStories-only CLEF exactly: "
            f"missing={missing[:8]} mismatched={mismatched[:8]}"
        )
    dropped = sorted(set(source_state) - set(selected))
    return selected, dropped


def load_tinystories_bundle(*, torch, local_files_only: bool, logger, exact_state: Path | None = None):
    """Load only TinyStories-33M, frozen, on CUDA."""
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from safetensors.torch import load_file

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the TinyStories center-expansion trainer")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("the TinyStories center-expansion trainer requires bf16-capable CUDA")

    logger.emit("tinystories_center_expand_backbone_load_start", model=TINYSTORIES_MODEL)
    tokenizer = AutoTokenizer.from_pretrained(
        TINYSTORIES_MODEL,
        local_files_only=bool(local_files_only),
        trust_remote_code=False,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    if tokenizer.pad_token_id is None:
        raise RuntimeError("TinyStories tokenizer has no pad/eos token")

    lm = AutoModelForCausalLM.from_pretrained(
        TINYSTORIES_MODEL,
        dtype=torch.bfloat16,
        attn_implementation="eager",
        local_files_only=bool(local_files_only),
        trust_remote_code=False,
    ).to("cuda")
    if exact_state is not None:
        lm.load_state_dict(load_file(str(Path(exact_state).resolve(strict=True)), device="cpu"), strict=True)
    for parameter in lm.parameters():
        parameter.requires_grad_(False)
    lm.eval()
    output = lm.get_output_embeddings()
    if output is None or not hasattr(output, "weight"):
        raise RuntimeError("TinyStories model has no output embedding weight")
    backbone = getattr(lm, "transformer", None) or lm.base_model
    hidden = int(getattr(backbone.config, "hidden_size"))
    if hidden != TINYSTORIES_HIDDEN:
        raise RuntimeError(f"TinyStories hidden width drifted: {hidden}")
    bundle = smoke.BackboneBundle(
        label=TINYSTORIES_LABEL,
        model_name=TINYSTORIES_MODEL,
        lm=lm,
        backbone=backbone,
        tokenizer=tokenizer,
        output_weight=output.weight,
        hidden_size=hidden,
        max_positions=smoke._max_positions(backbone.config),
    )
    logger.emit(
        "tinystories_center_expand_backbone_load_complete",
        model=TINYSTORIES_MODEL,
        hidden_size=hidden,
        max_positions=bundle.max_positions,
        parameters=sum(int(p.numel()) for p in lm.parameters()),
        frozen=True,
        memory=smoke.cuda_memory(torch, "after_tinystories_center_expand_load"),
    )
    return bundle


def _expanded_model_sequences(bundle, question, *, max_prompt_tokens: int, max_answer_tokens: int,
                              expanded_prompt_tokens: int):
    """Tokenize once, bound source prompt, and reserve an exact expansion budget."""
    rows, occurrence_count = smoke._model_sequences(
        bundle,
        question,
        max_prompt_tokens=min(int(max_prompt_tokens), int(expanded_prompt_tokens)),
        max_answer_tokens=int(max_answer_tokens),
    )
    for row in rows:
        source_len = len(row["prompt_ids"])
        answer_len = len(row["answer_ids"])
        if int(expanded_prompt_tokens) < source_len:
            raise RuntimeError(
                f"expanded prompt budget is smaller than bounded source prompt: "
                f"source={source_len} budget={expanded_prompt_tokens}"
            )
        if int(expanded_prompt_tokens) + answer_len > int(bundle.max_positions):
            raise RuntimeError(
                "expanded prompt plus preserved answer exceeds TinyStories context: "
                f"prompt_budget={expanded_prompt_tokens} answer={answer_len} max={bundle.max_positions}"
            )
    return rows, occurrence_count


def extract_center_expanded_evidence(
    *, torch, bundle, question, path_batch: int,
    max_prompt_tokens: int, max_answer_tokens: int,
    prompt_evidence_tokens: int, answer_evidence_tokens: int,
    expanded_prompt_tokens: int,
):
    """Extract residual-v3 evidence after embedding-space center expansion.

    Prompt IDs are embedded exactly once. Those embedding rows are copied into
    an exact-size center-weighted vector sequence. Answer embeddings are then
    appended unchanged so native continuation log-probability remains defined.
    """
    rows, occurrence_count = _expanded_model_sequences(
        bundle,
        question,
        max_prompt_tokens=max_prompt_tokens,
        max_answer_tokens=max_answer_tokens,
        expanded_prompt_tokens=expanded_prompt_tokens,
    )
    candidate_count = len(question.candidates)
    memory_parts = []
    residual_memory_parts = []
    option_answer = [[] for _ in range(candidate_count)]
    residual_option_answer = [[] for _ in range(candidate_count)]
    option_predictor = [[] for _ in range(candidate_count)]
    residual_option_predictor = [[] for _ in range(candidate_count)]
    option_terminal = [[] for _ in range(candidate_count)]
    residual_option_terminal = [[] for _ in range(candidate_count)]
    option_prompt = [[] for _ in range(candidate_count)]
    residual_option_prompt = [[] for _ in range(candidate_count)]
    option_lexical = [[] for _ in range(candidate_count)]
    option_logp = [[] for _ in range(candidate_count)]
    expansion_stats: list[ExpansionStats] = []
    device = bundle.output_weight.device
    input_embeddings = bundle.lm.get_input_embeddings()
    pad = int(bundle.tokenizer.pad_token_id)
    expanded_prompt_tokens = int(expanded_prompt_tokens)

    for offset in range(0, len(rows), int(path_batch)):
        chunk = rows[offset: offset + int(path_batch)]
        total_lengths = [expanded_prompt_tokens + len(row["answer_ids"]) for row in chunk]
        width = max(total_lengths)
        embeds = torch.zeros(
            (len(chunk), width, TINYSTORIES_HIDDEN),
            dtype=bundle.output_weight.dtype,
            device=device,
        )
        attention = torch.zeros((len(chunk), width), dtype=torch.long, device=device)
        answer_targets = torch.full((len(chunk), width), pad, dtype=torch.long, device=device)

        with torch.no_grad():
            for index, row in enumerate(chunk):
                prompt_ids = torch.tensor(row["prompt_ids"], dtype=torch.long, device=device)
                answer_ids = torch.tensor(row["answer_ids"], dtype=torch.long, device=device)
                prompt_vectors = input_embeddings(prompt_ids)
                expanded, _counts, stats = expand_embeddings_exact(
                    torch, prompt_vectors, expanded_prompt_tokens
                )
                if stats.source_tokens < stats.expanded_tokens and stats.max_copies <= 1:
                    raise RuntimeError(
                        "center-expansion invariant failed: source is shorter than target "
                        "but no source vector was replicated: "
                        f"source={stats.source_tokens} expanded={stats.expanded_tokens}"
                    )
                answer_vectors = input_embeddings(answer_ids)
                seq = torch.cat([expanded, answer_vectors], dim=0)
                embeds[index, : int(seq.shape[0])] = seq
                attention[index, : int(seq.shape[0])] = 1
                answer_targets[
                    index,
                    expanded_prompt_tokens : expanded_prompt_tokens + int(answer_ids.numel()),
                ] = answer_ids
                expansion_stats.append(stats)

            output = bundle.backbone(
                inputs_embeds=embeds,
                attention_mask=attention,
                use_cache=False,
                output_hidden_states=True,
                return_dict=True,
            )
            hidden_states = tuple(output.hidden_states or ())
            transformer_layers = len(hidden_states) - 1
            if transformer_layers < FINAL_LAYER:
                raise RuntimeError(
                    f"TinyStories layer-tap contract exceeds available depth: {transformer_layers}"
                )
            final_hidden = output.last_hidden_state
            residual_hidden = tuple(hidden_states[layer] for layer in RESIDUAL_LAYERS)

            for index, row in enumerate(chunk):
                plen = expanded_prompt_tokens
                alen = len(row["answer_ids"])
                prompt_idx = smoke.balanced_indices(0, plen, int(prompt_evidence_tokens))
                answer_idx = smoke.balanced_indices(plen, plen + alen, int(answer_evidence_tokens))
                evidence_idx = prompt_idx + answer_idx

                final_tokens = final_hidden[index, evidence_idx].detach()
                residual_tokens = torch.cat(
                    [hidden[index, evidence_idx] for hidden in residual_hidden], dim=-1
                ).detach()
                memory_parts.append(final_tokens)
                residual_memory_parts.append(residual_tokens)

                path_logp, _legacy_predictor = smoke._continuation_mean_logp(
                    torch=torch,
                    hidden=final_hidden[index],
                    tokens=answer_targets[index],
                    prompt_length=plen,
                    answer_length=alen,
                    output_weight=bundle.output_weight,
                )
                prompt_mean = final_hidden[index, :plen].mean(dim=0).detach()
                residual_prompt_mean = torch.cat(
                    [hidden[index, :plen].mean(dim=0) for hidden in residual_hidden], dim=-1
                ).detach()
                answer_mean = final_hidden[index, plen: plen + alen].mean(dim=0).detach()
                residual_answer_mean = torch.cat(
                    [hidden[index, plen: plen + alen].mean(dim=0) for hidden in residual_hidden], dim=-1
                ).detach()
                predictor_mean = mature._continuation_predictor_mean(
                    final_hidden[index], prompt_length=plen, answer_length=alen
                ).detach()
                residual_predictor_mean = torch.cat(
                    [
                        mature._continuation_predictor_mean(
                            hidden[index], prompt_length=plen, answer_length=alen
                        )
                        for hidden in residual_hidden
                    ],
                    dim=-1,
                ).detach()
                terminal = final_hidden[index, plen + alen - 1].detach()
                residual_terminal = torch.cat(
                    [hidden[index, plen + alen - 1] for hidden in residual_hidden], dim=-1
                ).detach()
                answer_token_ids = answer_targets[index, plen: plen + alen]
                lexical = bundle.output_weight[answer_token_ids].mean(dim=0).detach()
                for candidate in row["candidates"]:
                    option_prompt[candidate].append(prompt_mean)
                    residual_option_prompt[candidate].append(residual_prompt_mean)
                    option_answer[candidate].append(answer_mean)
                    residual_option_answer[candidate].append(residual_answer_mean)
                    option_predictor[candidate].append(predictor_mean)
                    residual_option_predictor[candidate].append(residual_predictor_mean)
                    option_terminal[candidate].append(terminal)
                    residual_option_terminal[candidate].append(residual_terminal)
                    option_lexical[candidate].append(lexical)
                    option_logp[candidate].append(path_logp.detach())

        del output, hidden_states, residual_hidden, final_hidden, embeds, attention, answer_targets

    def stack_mean(groups, label: str):
        values = []
        for candidate, items in enumerate(groups):
            if not items:
                raise RuntimeError(f"TinyStories missing {label} evidence for candidate {candidate}")
            values.append(torch.stack(items, dim=0).mean(dim=0))
        return torch.stack(values, dim=0)

    memory = torch.cat(memory_parts, dim=0)
    residual_memory = torch.cat(residual_memory_parts, dim=0)
    result = {
        "memory": memory,
        "residual_source_memory": residual_memory,
        "option_context": stack_mean(option_answer, "answer"),
        "residual_source_option_context": stack_mean(residual_option_answer, "residual answer"),
        "option_predictor": stack_mean(option_predictor, "predictor"),
        "residual_source_option_predictor": stack_mean(residual_option_predictor, "residual predictor"),
        "option_terminal": stack_mean(option_terminal, "terminal"),
        "residual_source_option_terminal": stack_mean(residual_option_terminal, "residual terminal"),
        "option_question": stack_mean(option_prompt, "prompt"),
        "residual_source_option_question": stack_mean(residual_option_prompt, "residual prompt"),
        "option_lexical": stack_mean(option_lexical, "lexical"),
        "option_logp": stack_mean(option_logp, "logp"),
        "global": memory.mean(dim=0),
        "residual_source_global": residual_memory.mean(dim=0),
        "path_count": occurrence_count,
        "unique_path_count": len(rows),
        "memory_tokens": int(memory.shape[0]),
        "source_prompt_tokens_min": min(s.source_tokens for s in expansion_stats),
        "source_prompt_tokens_max": max(s.source_tokens for s in expansion_stats),
        "expanded_prompt_tokens": expanded_prompt_tokens,
        "min_replication": min(s.min_copies for s in expansion_stats),
        "max_replication": max(s.max_copies for s in expansion_stats),
    }
    return result


def patch_mature_for_tinystories_only(*, expanded_prompt_tokens: int):
    """Route reusable mature train/eval logic through this one-backbone extractor."""
    mature.FROZEN_LABELS = (TINYSTORIES_LABEL,)

    def extract_one_bundle(*, torch, bundle, question, args, track_grad: bool):
        if track_grad:
            raise RuntimeError("TinyStories remains frozen in center-expansion training")
        row = extract_center_expanded_evidence(
            torch=torch,
            bundle=bundle,
            question=question,
            path_batch=int(args.path_batch),
            max_prompt_tokens=int(args.max_prompt_tokens),
            max_answer_tokens=int(args.max_answer_tokens),
            prompt_evidence_tokens=int(args.prompt_evidence_tokens),
            answer_evidence_tokens=int(args.answer_evidence_tokens),
            expanded_prompt_tokens=int(expanded_prompt_tokens),
        )
        stats = {
            key: int(row.pop(key))
            for key in (
                "path_count",
                "unique_path_count",
                "memory_tokens",
                "source_prompt_tokens_min",
                "source_prompt_tokens_max",
                "expanded_prompt_tokens",
                "min_replication",
                "max_replication",
            )
        }
        return row, stats

    def extract_live_evidence(*, torch, bundles, question, args, logger):
        row, stats = extract_one_bundle(
            torch=torch,
            bundle=bundles[TINYSTORIES_LABEL],
            question=question,
            args=args,
            track_grad=False,
        )
        logger.emit(
            "tinystories_center_expand_live_evidence",
            question_id=question.question_id,
            task=question.task,
            candidates=len(question.candidates),
            backbone_stats={TINYSTORIES_LABEL: stats},
            expansion_schema=EXPANSION_SCHEMA,
            memory=smoke.cuda_memory(torch, "after_tinystories_center_expand_evidence"),
        )
        return {TINYSTORIES_LABEL: row}

    mature.extract_one_bundle = extract_one_bundle
    mature.extract_live_evidence = extract_live_evidence
    return mature


def self_test() -> dict[str, Any]:
    cases = {
        (4, 6): [1, 2, 2, 1],
        (7, 16): [1, 2, 3, 4, 3, 2, 1],
        (1, 9): [9],
    }
    for (source, target), expected in cases.items():
        observed = triangular_replication_counts(source, target)
        if observed != expected:
            raise AssertionError((source, target, expected, observed))
    for source in range(1, 65):
        for target in range(source, source + 130):
            counts = triangular_replication_counts(source, target)
            if sum(counts) != target or min(counts) < 1:
                raise AssertionError((source, target, counts))
    return {
        "ok": True,
        "expansion_schema": EXPANSION_SCHEMA,
        "micro_head_schema": MICRO_HEAD_SCHEMA,
        "example_4_to_6": triangular_replication_counts(4, 6),
        "example_7_to_16": triangular_replication_counts(7, 16),
    }
