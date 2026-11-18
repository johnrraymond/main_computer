#!/usr/bin/env python3
"""Frozen-Qwen hidden-layer/pooling polarity separation, with lexical final holdout.

Compare Qwen input embeddings, early/intermediate/final hidden states and mean,
last-non-padding-token, and last-four-non-padding-token pooling. Frozen 256-entry
vocabulary projections are evaluated for every extractor. No fitted decoder,
weight mutation, vocabulary recalibration, parameter tuning on FINAL, or seal.

Selection uses the historical authored phrase pair and geometries. The new
fixture is used only for final verification. The decision metric excludes
omission: merely detecting clause count cannot produce a polarity win.

Run from the NanoJev root:
  & $py -m tools.nanojev_sealed_overlap_polarity_layers_frob --device cuda \
    --output runtime/reports/sealed-overlap-polarity-layers-0001.json
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import sys
from pathlib import Path
from typing import Callable

import numpy as np

from tools import nanojev_sealed_overlap_clause_geometry_frob as clause
from tools import nanojev_sealed_overlap_geometry_frob as geometry
from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_training_information_bakeoff as training

SCHEMA = "nanojev.sealed-overlap.polarity-layer-extraction.v1"
HERE = Path(__file__).resolve().parent
FINAL_PHRASES = HERE / "fixtures" / "nanojev_overlap_polarity_layer_holdout_v1.json"
LAYER_NAMES = ("embedding", "first", "quarter", "half", "three_quarters", "final")
POOL_NAMES = ("mean", "last", "last4")
BASELINE = "final:mean"


def layer_positions(n: int) -> dict[str, int]:
    """hidden_states[0] = input embeddings; hidden_states[n] = final block."""
    if n < 8:
        raise ValueError("POLARITY_LAYERS_REQUIRES_AT_LEAST_EIGHT_TRANSFORMER_BLOCKS")
    positions = {"embedding": 0, "first": 1,
                 "quarter": max(2, int(round(n / 4))),
                 "half": int(round(n / 2)),
                 "three_quarters": int(round(3 * n / 4)), "final": n}
    if len(set(positions.values())) != len(LAYER_NAMES):
        raise ValueError("POLARITY_LAYERS_NOT_DISTINCT")
    return positions


def keys() -> tuple[str, ...]:
    return tuple(f"{layer}:{pool}" for layer in LAYER_NAMES for pool in POOL_NAMES)


def pooled_states(hidden_states: tuple, attention_mask, positions: dict[str, int]) -> dict[str, np.ndarray]:
    """All pooling ignores right-padding, including the last-N token modes.

    This function uses torch tensors but never constructs labels or calls a
    decoder. CPU float32 conversion follows the established encoder contract.
    """
    import torch
    mask = attention_mask.bool()
    if mask.ndim != 2 or not bool(mask.any(dim=1).all()):
        raise ValueError("POLARITY_LAYERS_EMPTY_OR_INVALID_MASK")
    valid_length = mask.long().sum(dim=1)
    last_idx = valid_length - 1
    batch = torch.arange(mask.shape[0], device=mask.device)
    output = {}
    for name, index in positions.items():
        tensor = hidden_states[index].float()
        if tensor.ndim != 3 or tensor.shape[:2] != mask.shape:
            raise ValueError("POLARITY_LAYERS_HIDDEN_SHAPE_MISMATCH")
        output[f"{name}:mean"] = ((tensor * mask[..., None]).sum(dim=1)
                                  / valid_length[:, None].clamp(min=1))
        output[f"{name}:last"] = tensor[batch, last_idx]
        # Mean of last <= 4 actual tokens, independently of padding. The
        # first 1-3 tokens of shorter clauses are included as available.
        last_four = mask & ((torch.arange(mask.shape[1], device=mask.device)[None, :]
                              >= (valid_length - 4).clamp(min=0)[:, None]))
        output[f"{name}:last4"] = ((tensor * last_four[..., None]).sum(dim=1)
                                   / last_four.sum(dim=1)[:, None].clamp(min=1))
    return {name: value.cpu().numpy().astype(np.float64) for name, value in output.items()}


class FrozenLayerEncoder:
    """One frozen forward pass per tokenized batch for all 18 feature views."""

    def __init__(self, model_id: str, device: str, *, allow_download: bool = False,
                 batch_size: int = 4, max_length: int = 192):
        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM
        except ImportError as exc:
            raise RuntimeError("QWEN_REQUIRES_TORCH_AND_TRANSFORMERS") from exc
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("QWEN_CUDA_UNAVAILABLE_EXPLICITLY_SELECT_CPU_IF_DESIRED")
        if batch_size < 1 or max_length < 8:
            raise ValueError("POLARITY_LAYERS_INVALID_BATCH_OR_SEQUENCE_LENGTH")
        self.torch = torch
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=False, local_files_only=not allow_download)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "right"
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=False, local_files_only=not allow_download,
            dtype=dtype).to(device)
        self.model.eval()
        for param in self.model.parameters():
            param.requires_grad_(False)
        self.positions = layer_positions(int(self.model.config.num_hidden_layers))
        self.stats = {"encoded_texts": 0, "texts_truncated": 0,
                      "max_untruncated_tokens": 0}

    def __call__(self, texts: list[str]) -> dict[str, np.ndarray]:
        if not texts:
            raise ValueError("POLARITY_LAYERS_EMPTY_TEXT_LIST")
        buffers = {name: [] for name in keys()}
        with self.torch.no_grad():
            for start in range(0, len(texts), self.batch_size):
                chunk = texts[start:start + self.batch_size]
                # Measure truncation before applying the historical max_length.
                # Full length is used ONLY for instrumentation, not embedding.
                lengths = self.tokenizer(chunk, truncation=False,
                                         add_special_tokens=True)["input_ids"]
                sizes = [len(ids) for ids in lengths]
                self.stats["encoded_texts"] += len(chunk)
                self.stats["texts_truncated"] += sum(s > self.max_length for s in sizes)
                self.stats["max_untruncated_tokens"] = max(
                    self.stats["max_untruncated_tokens"], max(sizes))
                batch = self.tokenizer(chunk, padding=True, truncation=True,
                                       max_length=self.max_length, return_tensors="pt")
                batch = {name: value.to(self.device) for name, value in batch.items()}
                states = self.model(**batch, output_hidden_states=True,
                                    use_cache=False).hidden_states
                if len(states) != max(self.positions.values()) + 1:
                    raise ValueError("POLARITY_LAYERS_MODEL_STATE_COUNT_MISMATCH")
                pool = pooled_states(states, batch["attention_mask"], self.positions)
                for name, features in pool.items():
                    buffers[name].append(features)
                if ((start // self.batch_size + 1) % max(1, 128 // self.batch_size) == 0
                        or start + self.batch_size >= len(texts)):
                    print(f"[POLARITY_LAYERS] encoded={min(start + self.batch_size, len(texts))}/{len(texts)} "
                          f"cumulative={self.stats['encoded_texts']} truncated={self.stats['texts_truncated']}",
                          file=sys.stderr, flush=True)
        result = {name: np.concatenate(parts, axis=0) for name, parts in buffers.items()}
        if any(not np.isfinite(x).all() or x.shape[0] != len(texts) for x in result.values()):
            raise ValueError("POLARITY_LAYERS_NONFINITE_OR_WRONG_SHAPE")
        return result


def read_final_phrases(path: Path, space: dict, selection: dict) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    axes = [d["id"] for d in space["dimensions"]]
    phrases = obj.get("axes")
    if (obj.get("schema") != "nanojev.sealed-overlap.polarity-layer-holdout.v1"
            or not isinstance(phrases, dict) or set(phrases) != set(axes)):
        raise ValueError("POLARITY_LAYERS_HOLDOUT_SCHEMA_OR_AXES_INVALID")
    # Lexical holdout is strict for all 32 final phrases.
    seen = {s.strip().casefold() for pair in selection.values()
            for polarity in ("positive", "negative") for s in pair[polarity]}
    seen.update(s.strip().casefold() for axis in space["dimensions"]
                for s in axis["phrases"])
    for phase in geometry.ternary.read_phrases(geometry.ternary.DEFAULT_PROBES, space).values():
        for axis in phase.values():
            for values in axis.values():
                seen.update(s.strip().casefold() for s in values)
    for axis in axes:
        pair = phrases[axis]
        if not isinstance(pair, dict) or set(pair) != {"positive", "negative"}:
            raise ValueError("POLARITY_LAYERS_HOLDOUT_BAD_POLARITY")
        for polarity in ("positive", "negative"):
            values = pair[polarity]
            if not isinstance(values, list) or len(values) != 2:
                raise ValueError("POLARITY_LAYERS_HOLDOUT_REQUIRES_TWO_PER_STATE")
            for phrase in values:
                if (not isinstance(phrase, str) or not 20 <= len(phrase) <= 240
                        or phrase != phrase.strip() or "." in phrase or "\n" in phrase):
                    raise ValueError("POLARITY_LAYERS_HOLDOUT_INVALID_PHRASE")
                value = phrase.casefold()
                if value in seen:
                    raise ValueError("POLARITY_LAYERS_HOLDOUT_NOT_DISJOINT")
                seen.add(value)
    return phrases


def polarity_only_rows(axes: list[str], phrases: dict, *, contexts: int, seed: int) -> list[dict]:
    """Use EXACT historical geometry generation, dropping omitted cases only.

    Both language pools use fixed equally shaped groups: two phrasings by
    affirmed/denied, and no clause-count difference inside a polarity group.
    """
    rows = geometry.make_cases(axes, phrases, contexts=contexts, seed=seed)
    groups = []
    for start in range(0, len(rows), 6):
        group = [rows[start+i] for i in (0, 1, 3, 4)]
        if ([r["state"] for r in group] != ["affirmed", "denied", "affirmed", "denied"]
                or len({r["group_id"] for r in group}) != 1):
            raise ValueError("POLARITY_LAYERS_GROUP_ORDER_MISMATCH")
        groups.extend(group)
    return groups


def metric_group(features: np.ndarray) -> dict:
    """Four same-versus-opposite tests; no omission or trained mapping."""
    x = smoke.unit_vectors(np.asarray(features, dtype=np.float64))
    if x.ndim != 2 or x.shape[0] != 4 or not np.isfinite(x).all():
        raise ValueError("POLARITY_LAYERS_EXPECTS_FOUR_VECTORS")
    p0, n0, p1, n1 = x
    margins = np.array([p0@p1 - p0@n1, n0@n1 - n0@p1,
                        p1@p0 - p1@n0, n1@n0 - n1@p0], dtype=float)
    return {"correct": int(np.count_nonzero(margins > 1e-10)),
            "count": 4, "mean_margin": float(margins.mean()),
            "near_ties": int(np.count_nonzero(abs(margins) <= 1e-10))}


def score_groups(features: np.ndarray, rows: list[dict], axes: list[str]) -> dict:
    if features.shape[0] != len(rows) or len(rows) % 4:
        raise ValueError("POLARITY_LAYERS_WRONG_CASE_COUNT")
    by_group = [metric_group(features[i:i+4]) for i in range(0, len(rows), 4)]
    by_depth = {}
    by_axis = {}
    for depth in geometry.DEPTHS:
        chosen = [m for i, m in enumerate(by_group) if rows[i*4]["depth"] == depth]
        by_depth[str(depth)] = float(sum(m["correct"] for m in chosen)/(4*len(chosen)))
    for axis in axes:
        chosen = [m for i, m in enumerate(by_group) if rows[i*4]["axis"] == axis]
        by_axis[axis] = float(sum(m["correct"] for m in chosen)/(4*len(chosen)))
    n = 4 * len(by_group)
    return {"polarity_accuracy": float(sum(m["correct"] for m in by_group)/n),
            "mean_margin": float(np.mean([m["mean_margin"] for m in by_group])),
            "near_tie_fraction": float(sum(m["near_ties"] for m in by_group)/n),
            "by_depth": by_depth, "by_axis": by_axis,
            "groups": len(by_group), "comparison_count": n,
            "group_details": by_group}


def isolated_score(features: np.ndarray, texts: list[str], phrases: dict, axes: list[str]) -> dict:
    return clause.isolated_polarity(features, texts, phrases, axes)


def unique_texts(rows: list[dict], phrases: dict, axes: list[str]) -> tuple[list[str], list[int], list[int]]:
    # The first 4*axis probes are positionally prespecified, and *not* target
    # markers given to the encoder. Duplicate whole prompts share an embedding.
    isolated = [s for axis in axes for state in ("positive", "negative")
                for s in phrases[axis][state]]
    text = list(dict.fromkeys(isolated + [r["prompt"] for r in rows]))
    loc = {s: i for i, s in enumerate(text)}
    return text, [loc[s] for s in isolated], [loc[r["prompt"]] for r in rows]


def extract_scores(features: dict[str, np.ndarray], rows: list[dict], phrases: dict,
                   axes: list[str], vocab_features: dict[str, np.ndarray],
                   *, stage: str) -> dict:
    text, isolated_idx, prompt_idx = unique_texts(rows, phrases, axes)
    out = {}
    for key in keys():
        q = smoke.unit_vectors(features[key])
        if stage == "vocabulary_similarity":
            # Same extractor applied to frozen descriptions. No trained or
            # hand-tuned weights, thresholds, or labels at projection time.
            v = smoke.unit_vectors(vocab_features[key])
            q = smoke.unit_vectors(q @ v.T)
        isolated = isolated_score(q[isolated_idx],
                                  [text[i] for i in isolated_idx], phrases, axes)
        retrieval = score_groups(q[prompt_idx], rows, axes)
        del retrieval["group_details"]
        out[key] = {"isolated_polarity": isolated["macro_polarity_retrieval_accuracy"],
                    "isolated_by_axis": {a: v["polarity_retrieval_accuracy"]
                                         for a, v in isolated["by_axis"].items()},
                    **retrieval}
    return out


def select_extractor(results: dict) -> str:
    """Only selection data; primary criterion = composed depth>=4 polarity."""
    candidate = sorted(keys(), key=lambda key: (
        -(results[key]["by_depth"]["4"] + results[key]["by_depth"]["8"])/2,
        -results[key]["isolated_polarity"],
        -results[key]["polarity_accuracy"],
        0 if key == BASELINE else 1,
        key))
    return candidate[0]


def paired_axis_bootstrap(chosen: dict, reference: dict, axes: list[str], *,
                          seed: int = 1912, trials: int = 999) -> dict:
    if trials < 10:
        raise ValueError("POLARITY_LAYERS_BOOTSTRAP_TRIALS_TOO_SMALL")
    diffs = np.asarray([chosen["by_axis"][a] - reference["by_axis"][a] for a in axes])
    rng = np.random.default_rng(seed)
    draws = np.array([diffs[rng.integers(0, len(diffs), len(diffs))].mean()
                      for _ in range(trials)])
    return {"delta_mean_accuracy": float(diffs.mean()),
            "axis_cluster_bootstrap_95pct": np.quantile(draws, [.025, .975]).tolist(),
            "limitation": "Only eight authored axes; intervals are descriptive and do not establish population generalization."}


def validate_clause_report(path: Path, *, exact: Path, space: dict,
                           vocabulary: list[dict], phrases: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    sources = obj.get("sources") or {}
    experiment = obj.get("experiment") or {}
    if (obj.get("schema") != clause.SCHEMA
            or obj.get("status") != "COMPLETE_CLAUSE_GEOMETRY_DIAGNOSTIC_NO_SEAL_NO_PROMOTION"
            or obj.get("acceptance", {}).get("never_updates_model_weights") is not True
            or sources.get("vocabulary_sha256") != smoke.seal(vocabulary)
            or sources.get("human_space_sha256") != smoke.seal(space)
            or sources.get("phrases_sha256") != sha256(phrases.read_bytes()).hexdigest()
            or sources.get("exact_report") != str(exact)
            or experiment.get("unique_clause_text_count_including_neutral") != 33
            or experiment.get("contexts_per_axis_depth") != 4
            or not isinstance(obj.get("isolated_clause_polarity"), dict)):
        raise ValueError("POLARITY_LAYERS_PRIOR_CLAUSE_REPORT_MISMATCH")
    return obj


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_TO_OVERWRITE")
    if (args.contexts < 1 or args.contexts > 4 or args.bootstrap_trials < 10
            or args.batch_size < 1 or args.max_length < 8):
        raise ValueError("POLARITY_LAYERS_INVALID_CONFIG")
    space = smoke.load_space(args.space)
    baseline, exact_report, vocabulary, _ = training.read_sources(
        args.exact_report, args.growth_report, args.baseline_report, space)
    axes = baseline["human_dimensions"]
    if len(axes) != 8 or len(vocabulary) != 256:
        raise ValueError("POLARITY_LAYERS_REQUIRES_ORIGINAL_256_AND_EIGHT_AXES")
    selection_phrases = geometry.read_phrases(args.selection_phrases, space)
    final_phrases = read_final_phrases(args.final_phrases, space, selection_phrases)
    historical = validate_clause_report(args.clause_report, exact=args.exact_report,
                                       space=space, vocabulary=vocabulary,
                                       phrases=args.selection_phrases)
    selection_rows = polarity_only_rows(axes, selection_phrases,
                                        contexts=args.contexts, seed=args.selection_seed)
    final_rows = polarity_only_rows(axes, final_phrases,
                                    contexts=args.contexts, seed=args.final_seed)
    if len(selection_rows) != len(final_rows) or len(selection_rows) != 8*4*args.contexts*4:
        raise ValueError("POLARITY_LAYERS_CASE_COUNT_INVALID")
    selection_texts, selection_isolated, selection_prompts = unique_texts(
        selection_rows, selection_phrases, axes)
    final_texts, final_isolated, final_prompts = unique_texts(
        final_rows, final_phrases, axes)
    if len(set(selection_texts) & set(final_texts)):
        raise ValueError("POLARITY_LAYERS_SELECTION_FINAL_TEXT_LEAK")
    if encoder is None:
        encoder = FrozenLayerEncoder(smoke.MODEL_ID, args.device,
                                    allow_download=args.allow_model_download,
                                    batch_size=args.batch_size, max_length=args.max_length)
    # All forward passes use frozen model. FINAL metrics are not calculated
    # until after model extraction, selection scoring, and model selection.
    vocabulary_vectors = encoder([v["description"] for v in vocabulary])
    select_vectors = encoder(selection_texts)
    select_scores = {stage: extract_scores(select_vectors, selection_rows,
                     selection_phrases, axes, vocabulary_vectors, stage=stage)
                     for stage in ("direct", "vocabulary_similarity")}
    selected = select_extractor(select_scores["direct"])
    # Baseline cross-check: same isolated phrases and Qwen final-layer mean
    # must reproduce the earlier frozen historical polarity metric.
    previous = historical["isolated_clause_polarity"]["direct"]["macro_polarity_retrieval_accuracy"]
    current = select_scores["direct"][BASELINE]["isolated_polarity"]
    # The old and new batching orders can change fp16 reductions slightly.
    # One or two of the 32 comparisons may flip near a cosine tie; larger
    # disagreement is an unsafe baseline and must stop this experiment.
    if abs(current-previous) > 2 / 32 + 1e-10:
        raise ValueError(f"POLARITY_LAYERS_BASELINE_NOT_REPRODUCED:{current}:{previous}")
    final_vectors = encoder(final_texts)
    final_scores = {stage: extract_scores(final_vectors, final_rows,
                    final_phrases, axes, vocabulary_vectors, stage=stage)
                    for stage in ("direct", "vocabulary_similarity")}
    deltas = {stage: paired_axis_bootstrap(final_scores[stage][selected],
                    final_scores[stage][BASELINE], axes, trials=args.bootstrap_trials,
                    seed=2027 + i)
              for i, stage in enumerate(("direct", "vocabulary_similarity"))}
    report = {
        "schema": SCHEMA,
        "status": "COMPLETE_POLARITY_LAYER_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
        "acceptance": {"status": "DIAGNOSTIC_ONLY_NOT_SEALABLE",
                       "never_authorizes_contract_seal": True,
                       "never_updates_model_weights": True,
                       "vocabulary_unchanged": True,
                       "no_decoder_or_vocabulary_calibration": True,
                       "ollama_called": False},
        "sources": {"clause_report": str(args.clause_report),
                    "clause_report_sha256": sha256(args.clause_report.read_bytes()).hexdigest(),
                    "exact_report_sha256": sha256(args.exact_report.read_bytes()).hexdigest(),
                    "vocabulary_sha256": smoke.seal(vocabulary),
                    "space_sha256": smoke.seal(space),
                    "selection_phrases_sha256": sha256(args.selection_phrases.read_bytes()).hexdigest(),
                    "final_phrases_sha256": sha256(args.final_phrases.read_bytes()).hexdigest()},
        "experiment": {"model": smoke.MODEL_ID, "frozen": True,
                       "contexts_per_axis_depth": args.contexts,
                       "selection_prompt_count": len(selection_rows),
                       "final_prompt_count": len(final_rows),
                       "selection_unique_text_count": len(selection_texts),
                       "final_unique_text_count": len(final_texts),
                       "layer_positions": getattr(encoder, "positions", None),
                       "pooling_methods": list(POOL_NAMES),
                       "candidates": list(keys()), "baseline": BASELINE,
                       "primary_metric": "affirmation/denial 4-way same-vs-opposite cross-paraphrase retrieval; omissions excluded",
                       "selection_criterion": "maximum mean depth4/depth8 direct polarity accuracy; then isolated polarity, overall polarity; deterministic baseline tie-break",
                       "selection_vs_final_lexical_disjoint": True,
                       "baseline_isolated_polarity_audit": {"historical": previous, "recomputed": current,
                                                           "absolute_difference": abs(current - previous),
                                                           "allow_at_most_two_comparison_flips_from_numerical_batching": True},
                       "all_candidates_final_scored_only_after_selection_locked": True,
                       "original_backbone_encoder_parity": "final:mean, right-padding, mean non-padding hidden states, max_length=192",
                       "tokenization": {"max_length": args.max_length,
                                        "truncation_stats": getattr(encoder, "stats", None)},
                       "limitations": "Fixed authored phrasing with repeated syntactic wrappers, 8 non-independent axes and 4 contextual families; not general understanding, polarity logic or a production contract seal."},
        "selected": {"extraction": selected, "selection_direct": select_scores["direct"][selected],
                     "final_labels_used_in_selection": False},
        "selection": select_scores,
        "final": final_scores,
        "selected_vs_baseline_final": deltas,
        "selected_direct_vs_projection_final": paired_axis_bootstrap(
            final_scores["direct"][selected], final_scores["vocabulary_similarity"][selected],
            axes, trials=args.bootstrap_trials, seed=2031),
        "interpretation": [
            "Prefer a candidate over the previous final-layer mean only if its locked final improvement persists and the matched polarity comparisons are positive beyond sampling uncertainty.",
            "If direct Qwen improves but the same extractor's vocabulary projection does not, raw description-similarity projection obscures that polarity information.",
            "Do not confuse lexical negation detection with robust contractual semantic polarity; labels, context and grammar are synthetic authored probes."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clause-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-clause-geometry-0001.json"))
    p.add_argument("--exact-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-exact-growth-0001.json"))
    p.add_argument("--growth-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-vocabulary-bakeoff-0001.json"))
    p.add_argument("--baseline-report", type=Path,
                   default=Path("runtime/reports/sealed-overlap-smoke-0002.json"))
    p.add_argument("--space", type=Path, default=smoke.SPACE_DEFAULT)
    p.add_argument("--selection-phrases", type=Path, default=geometry.DEFAULT_PROBES)
    p.add_argument("--final-phrases", type=Path, default=FINAL_PHRASES)
    p.add_argument("--selection-seed", type=int, default=1729)
    p.add_argument("--final-seed", type=int, default=2307)
    p.add_argument("--contexts", type=int, default=4)
    p.add_argument("--device", default="cuda")
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--max-length", type=int, default=192)
    p.add_argument("--bootstrap-trials", type=int, default=999)
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    report = run(args)
    selected = report["selected"]["extraction"]
    print(smoke.canonical({
        "status": report["status"], "acceptance": report["acceptance"]["status"],
        "selected_extraction": selected,
        "selection_direct_polarity": round(report["selection"]["direct"][selected]["polarity_accuracy"], 6),
        "final_direct_polarity": round(report["final"]["direct"][selected]["polarity_accuracy"], 6),
        "final_baseline_polarity": round(report["final"]["direct"][BASELINE]["polarity_accuracy"], 6),
        "final_projected_polarity": round(report["final"]["vocabulary_similarity"][selected]["polarity_accuracy"], 6),
        "final_isolated_polarity": round(report["final"]["direct"][selected]["isolated_polarity"], 6),
        "final_depth8_direct": round(report["final"]["direct"][selected]["by_depth"]["8"], 6),
        "final_depth8_baseline": round(report["final"]["direct"][BASELINE]["by_depth"]["8"], 6),
        "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
