#!/usr/bin/env python3
"""Read-only NanoJev dictionary baseline smoke.

Purpose
-------
Measure the current frozen-Qwen / frozen-head / frozen-S / current-R1 system
on an unrelated first-order semantic task *before any dictionary training*.

The script:
  1. downloads a pinned Open English WordNet WNDB archive if absent,
  2. deterministically builds TRUE/FALSE definition pairs,
  3. loads the latest committed soft-feedback checkpoint and reconstructs its exact controller architecture,
  4. evaluates the exact same examples under none/static/dynamic control modes,
  5. prints a compact score table plus one machine-readable JSON result.

It does not train, create an optimizer, or mutate the NanoJev experiment.
Only the dictionary cache file is written persistently.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import re
import sys
import tempfile
import time
import urllib.request
import zipfile


DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_v1"
DEFAULT_DICTIONARY_CACHE = r"C:\Users\subsi\NanoJev\cache\english-wordnet-2025.zip"
DEFAULT_DICTIONARY_URL = "https://en-word.net/static/english-wordnet-2025.zip"
DEFAULT_PAIRS = 32
DEFAULT_SEED = 20260927
DEFAULT_MODES = ("none", "static", "dynamic")
DATA_BASENAMES = ("data.noun", "data.verb", "data.adj", "data.adv")

POS_NAMES = {
    "n": "noun",
    "v": "verb",
    "a": "adjective",
    "s": "adjective",
    "r": "adverb",
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "for",
    "from", "has", "have", "having", "in", "into", "is", "it", "its", "of",
    "on", "or", "that", "the", "their", "this", "to", "used", "using", "was",
    "were", "which", "with", "without", "one", "someone", "something",
}

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download_dictionary(url: str, target: Path) -> Path:
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        try:
            with zipfile.ZipFile(target) as zf:
                names = {Path(n.replace("\\", "/")).name for n in zf.namelist()}
                if any(name in names for name in DATA_BASENAMES):
                    return target
        except zipfile.BadZipFile:
            pass
        target.unlink(missing_ok=True)

    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    print(f"dictionary_download: {url}", flush=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "NanoJev-dictionary-smoke/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as out:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
        with zipfile.ZipFile(partial) as zf:
            names = {Path(n.replace("\\", "/")).name for n in zf.namelist()}
            if not any(name in names for name in DATA_BASENAMES):
                raise RuntimeError("downloaded archive does not contain WordNet data.* files")
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return target


def clean_gloss(gloss: str) -> str:
    # WNDB glosses append examples after semicolon + quote.  Keep the definition
    # itself so the task is definition matching, not example recognition.
    gloss = gloss.strip()
    match = re.search(r';\s*["“]', gloss)
    if match:
        gloss = gloss[:match.start()].rstrip(" ;")
    return re.sub(r"\s+", " ", gloss).strip()


def normalize_lemma(raw: str) -> str:
    return raw.replace("_", " ").strip()


def acceptable_lemma(text: str) -> bool:
    if not 2 <= len(text) <= 48:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z' -]*", text))


def gloss_tokens(text: str) -> frozenset[str]:
    return frozenset(
        token.lower()
        for token in WORD_RE.findall(text)
        if len(token) >= 3 and token.lower() not in STOPWORDS
    )


def parse_wordnet_archive(path: Path) -> list[dict]:
    synsets: list[dict] = []
    with zipfile.ZipFile(path) as zf:
        member_by_base = {}
        for name in zf.namelist():
            base = Path(name.replace("\\", "/")).name
            if base in DATA_BASENAMES:
                member_by_base[base] = name

        missing = [name for name in DATA_BASENAMES if name not in member_by_base]
        if missing:
            raise RuntimeError(f"WordNet archive is missing: {missing}")

        for basename in DATA_BASENAMES:
            member = member_by_base[basename]
            fallback_pos = {
                "data.noun": "n",
                "data.verb": "v",
                "data.adj": "a",
                "data.adv": "r",
            }[basename]
            text = zf.read(member).decode("utf-8", errors="replace")
            for line in text.splitlines():
                if not line or not line[0].isdigit() or "|" not in line:
                    continue
                left, gloss = line.split("|", 1)
                fields = left.split()
                if len(fields) < 4:
                    continue
                try:
                    offset = fields[0]
                    pos = fields[2] if fields[2] in POS_NAMES else fallback_pos
                    word_count = int(fields[3], 16)
                except (ValueError, IndexError):
                    continue
                cursor = 4
                lemmas = []
                try:
                    for _ in range(word_count):
                        lemma = normalize_lemma(fields[cursor])
                        cursor += 2  # word + lex_id
                        if acceptable_lemma(lemma):
                            lemmas.append(lemma)
                except IndexError:
                    continue
                definition = clean_gloss(gloss)
                if not lemmas or not 16 <= len(definition) <= 320:
                    continue
                tokens = gloss_tokens(definition)
                if len(tokens) < 2:
                    continue
                synsets.append({
                    "id": f"{pos}:{offset}",
                    "pos": pos,
                    "lemmas": tuple(dict.fromkeys(lemmas)),
                    "definition": definition,
                    "tokens": tokens,
                })
    if len(synsets) < 1000:
        raise RuntimeError(f"unexpectedly small WordNet parse: {len(synsets)} synsets")
    return synsets


def pick_headword(synset: dict, seen_words: set[str], rng: random.Random) -> str | None:
    choices = list(synset["lemmas"])
    rng.shuffle(choices)
    for word in choices:
        key = word.lower()
        if key not in seen_words:
            seen_words.add(key)
            return word
    return None


def hard_negative_for(
    positive: dict,
    headword: str,
    *,
    synsets: list[dict],
    by_pos: dict[str, list[int]],
    inverted: dict[tuple[str, str], list[int]],
    rng: random.Random,
) -> dict:
    candidates: set[int] = set()
    useful_tokens = sorted(positive["tokens"], key=lambda t: (len(inverted[(positive["pos"], t)]), -len(t)))
    for token in useful_tokens[:8]:
        bucket = inverted.get((positive["pos"], token), ())
        if len(bucket) <= 2000:
            candidates.update(bucket)
        if len(candidates) >= 256:
            break

    headword_l = headword.lower()
    valid = []
    for index in candidates:
        candidate = synsets[index]
        if candidate["id"] == positive["id"]:
            continue
        if any(lemma.lower() == headword_l for lemma in candidate["lemmas"]):
            continue
        if re.search(rf"\b{re.escape(headword_l)}\b", candidate["definition"].lower()):
            continue
        overlap = len(positive["tokens"] & candidate["tokens"])
        union = len(positive["tokens"] | candidate["tokens"])
        jaccard = overlap / union if union else 0.0
        length_penalty = abs(len(candidate["definition"]) - len(positive["definition"])) / 500.0
        valid.append((jaccard - length_penalty, rng.random(), candidate))

    if valid:
        valid.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return valid[0][2]

    pool = by_pos[positive["pos"]]
    for _ in range(500):
        candidate = synsets[rng.choice(pool)]
        if candidate["id"] == positive["id"]:
            continue
        if any(lemma.lower() == headword_l for lemma in candidate["lemmas"]):
            continue
        return candidate
    raise RuntimeError(f"could not find negative definition for {headword!r}")


def build_pairs(synsets: list[dict], count: int, seed: int) -> list[tuple[str, dict, dict]]:
    rng = random.Random(seed)
    by_pos: dict[str, list[int]] = defaultdict(list)
    inverted: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, synset in enumerate(synsets):
        by_pos[synset["pos"]].append(i)
        for token in synset["tokens"]:
            inverted[(synset["pos"], token)].append(i)

    indices = list(range(len(synsets)))
    rng.shuffle(indices)
    seen_words: set[str] = set()
    pairs = []
    for index in indices:
        positive = synsets[index]
        headword = pick_headword(positive, seen_words, rng)
        if headword is None:
            continue
        negative = hard_negative_for(
            positive,
            headword,
            synsets=synsets,
            by_pos=by_pos,
            inverted=inverted,
            rng=rng,
        )
        pairs.append((headword, positive, negative))
        if len(pairs) >= count:
            break
    if len(pairs) != count:
        raise RuntimeError(f"requested {count} dictionary pairs but built {len(pairs)}")
    return pairs


def make_record(
    *,
    pair_index: int,
    headword: str,
    positive: dict,
    candidate: dict,
    truth: bool,
) -> dict:
    pair_id = f"dictionary-{pair_index:05d}"
    side = "true" if truth else "false"
    record_id = f"{pair_id}-{side}"
    state = (
        f"Dictionary headword: {json.dumps(headword, ensure_ascii=False)}\n"
        f"Part of speech: {POS_NAMES.get(positive['pos'], positive['pos'])}\n"
        "Candidate definition:\n"
        f"{json.dumps(candidate['definition'], ensure_ascii=False)}"
    )
    return {
        "id": record_id,
        "state_id": record_id,
        "family_id": pair_id,
        "split": "dev",
        "state": state,
        "questions": {
            "definition_matches": {
                "type": "boolean",
                "instructions": (
                    "Does the candidate definition correctly define the dictionary headword "
                    "in the stated part of speech? Answer only the semantic match being asked."
                ),
                "criteria": {
                    "false": "No. The candidate definition does not define this headword.",
                    "true": "Yes. The candidate definition does define this headword.",
                },
            }
        },
        "gold": {"definition_matches": truth},
        "gold_label_kind": {"definition_matches": "deterministic_truth"},
        "metadata": {
            "source": "Open English WordNet 2025",
            "headword": headword,
            "part_of_speech": POS_NAMES.get(positive["pos"], positive["pos"]),
            "positive_synset_id": positive["id"],
            "candidate_synset_id": candidate["id"],
            "negative_strategy": None if truth else "same_pos_lexical_overlap",
        },
    }


def write_probe(records: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in records:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def resolve_checkpoint(experiment_dir: Path, override: str | None) -> Path:
    if override:
        checkpoint = Path(override).expanduser().resolve(strict=True)
    else:
        state = read_json(experiment_dir / "training_state.json")
        latest = state.get("latest_generation")
        if not latest:
            raise RuntimeError(f"training state has no latest_generation: {experiment_dir}")
        checkpoint = Path(latest).expanduser().resolve(strict=True)
    for required in ("head.safetensors", "config.json", "meta.json"):
        if not (checkpoint / required).is_file():
            raise RuntimeError(f"checkpoint missing {required}: {checkpoint}")
    return checkpoint


def detect_feedback_architecture(feedback_config: dict) -> str:
    generator = str(feedback_config.get("generator", "")).lower()
    keys = set(feedback_config)
    if (
        feedback_config.get("sparse_registers_trainable") is True
        or "sparse" in generator
        or {"control_space_size", "register_rank"} <= keys
    ):
        return "sparse_register"
    if "rank" in feedback_config:
        return "residual"
    raise RuntimeError(
        "cannot identify soft-feedback architecture from checkpoint config; "
        f"generator={feedback_config.get('generator')!r} keys={sorted(keys)}"
    )


def load_current_model(args, experiment_dir: Path, checkpoint: Path):
    tools_dir = Path(__file__).resolve().parent
    legacy = load_local_module(
        "nanojev_code_train_for_dictionary_smoke",
        tools_dir / "nanojev_code_train.py",
    )

    experiment = read_json(experiment_dir / "experiment.json")
    checkpoint_config = read_json(checkpoint / "config.json")
    feedback_config = checkpoint_config.get("soft_feedback")
    if not isinstance(feedback_config, dict) or feedback_config.get("enabled") is not True:
        raise RuntimeError("selected checkpoint is not a soft-feedback checkpoint")

    architecture = detect_feedback_architecture(feedback_config)

    legacy_exp = Path(experiment["legacy_experiment"]).expanduser().resolve(strict=True)
    legacy_experiment = read_json(legacy_exp / "experiment.json")
    nanojev_root = Path(experiment["nanojev_root"]).expanduser().resolve(strict=True)
    pipeline, BaseDecisionModel = legacy.import_nanojev(nanojev_root)

    import torch
    from transformers import AutoModel, AutoTokenizer

    if args.disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this smoke test")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported by this CUDA device")
    torch.backends.cuda.matmul.allow_tf32 = False

    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_dir),
        local_files_only=True,
        trust_remote_code=False,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(
        f"model_load: {legacy_experiment['model']} @ "
        f"{legacy_experiment['resolved_model_revision']}",
        flush=True,
    )
    print(
        "controller_load: "
        f"{architecture} / {feedback_config.get('generator', 'unknown-generator')}",
        flush=True,
    )

    backbone = AutoModel.from_pretrained(
        legacy_experiment["model"],
        revision=legacy_experiment["resolved_model_revision"],
        dtype=torch.float32,
        attn_implementation="sdpa",
        trust_remote_code=False,
        local_files_only=not args.allow_model_download,
    )

    latent_walk_hook = None
    if architecture == "sparse_register":
        controller_module = load_local_module(
            "nanojev_code_sparse_register_for_dictionary_smoke",
            tools_dir / "nanojev_code_sparse_register_train.py",
        )
        required = ("control_space_size", "register_rank", "register_active_epsilon")
        missing = [key for key in required if key not in feedback_config]
        if missing:
            raise RuntimeError(
                f"sparse-register checkpoint is missing controller config keys: {missing}"
            )
        DecisionModel = controller_module.build_soft_feedback_decision_model_class(
            BaseDecisionModel,
            control_space_size=int(feedback_config["control_space_size"]),
            register_rank=int(feedback_config["register_rank"]),
            strength_init=float(feedback_config.get("strength_init", 0.25)),
            active_epsilon=float(feedback_config["register_active_epsilon"]),
        )
    else:
        controller_module = load_local_module(
            "nanojev_code_soft_feedback_residual_for_dictionary_smoke",
            tools_dir / "nanojev_code_soft_feedback_residual_train.py",
        )
        if "rank" not in feedback_config:
            raise RuntimeError("residual checkpoint is missing soft-feedback rank")
        DecisionModel = controller_module.build_soft_feedback_decision_model_class(
            BaseDecisionModel,
            rank=int(feedback_config["rank"]),
            strength_init=float(feedback_config.get("strength_init", 0.25)),
        )

    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(int(experiment["seed"]))
        model = DecisionModel(backbone, legacy_experiment["set_head"])

    # Sparse-register runs may optionally include the latent walker.  Reconstruct
    # it before loading head.safetensors so the checkpoint is consumed exactly.
    latent_config = checkpoint_config.get("latent_walk")
    if architecture == "sparse_register" and isinstance(latent_config, dict) and latent_config.get("enabled") is True:
        if "steps" not in latent_config or "rank" not in latent_config:
            raise RuntimeError("latent-walk checkpoint config is incomplete")
        latent_walk = controller_module.build_latent_walk_module(
            hidden_size=int(model.backbone.config.hidden_size),
            rank=int(latent_config["rank"]),
            steps=int(latent_config["steps"]),
        )
        model.add_module("latent_walk", latent_walk)
        latent_walk_hook = controller_module.install_latent_walk_hook(model, latent_walk)

    controller_module.load_head_into_model(model, checkpoint / "head.safetensors")

    # The smoke is strictly inference-only even though the checkpoint may have
    # trainable controller parameters in its originating experiment.
    for parameter in model.parameters():
        parameter.requires_grad_(False)

    model.cuda()
    model.backbone.eval()
    model.eval()

    soft_feedback = model.soft_feedback
    static_params = (
        sum(parameter.numel() for parameter in soft_feedback.static_parameters())
        if hasattr(soft_feedback, "static_parameters")
        else None
    )
    dynamic_params = (
        sum(parameter.numel() for parameter in soft_feedback.dynamic_parameters())
        if hasattr(soft_feedback, "dynamic_parameters")
        else None
    )
    controller_info = {
        "architecture": architecture,
        "generator": feedback_config.get("generator"),
        "static_parameter_count": static_params,
        "dynamic_parameter_count": dynamic_params,
        "control_space_size": getattr(soft_feedback, "control_space_size", None),
        "register_rank": getattr(soft_feedback, "register_rank", feedback_config.get("rank")),
        "latent_walk_enabled": bool(
            isinstance(latent_config, dict) and latent_config.get("enabled") is True
        ),
    }

    return {
        "torch": torch,
        "controller_module": controller_module,
        "legacy": legacy,
        "pipeline": pipeline,
        "model": model,
        "tokenizer": tokenizer,
        "legacy_experiment": legacy_experiment,
        "checkpoint_config": checkpoint_config,
        "controller_info": controller_info,
        # Keep the hook handle alive for the lifetime of the loaded model.
        "latent_walk_hook": latent_walk_hook,
    }

def format_pct(value):
    return "n/a" if value is None else f"{100.0 * value:6.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--checkpoint", help="Optional checkpoint generation override")
    parser.add_argument("--dictionary-cache", default=DEFAULT_DICTIONARY_CACHE)
    parser.add_argument("--dictionary-url", default=DEFAULT_DICTIONARY_URL)
    parser.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=DEFAULT_MODES,
        default=list(DEFAULT_MODES),
        help="Control modes to score; default compares none static dynamic",
    )
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--microbatch-questions", type=int, default=1)
    parser.add_argument("--max-microbatch-tokens", type=int, default=8192)
    parser.add_argument("--pair-margin", type=float, default=0.10)
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run dictionary parser/record checks only; does not load NanoJev",
    )
    args = parser.parse_args()

    if args.pairs <= 0:
        parser.error("--pairs must be positive")
    if args.microbatch_questions <= 0 or args.max_microbatch_tokens <= 0:
        parser.error("microbatch limits must be positive")

    dictionary_path = download_dictionary(args.dictionary_url, Path(args.dictionary_cache))
    dictionary_sha = file_sha256(dictionary_path)
    synsets = parse_wordnet_archive(dictionary_path)
    pairs = build_pairs(synsets, args.pairs, args.seed)

    records = []
    for i, (headword, positive, negative) in enumerate(pairs):
        records.append(
            make_record(
                pair_index=i,
                headword=headword,
                positive=positive,
                candidate=negative,
                truth=False,
            )
        )
        records.append(
            make_record(
                pair_index=i,
                headword=headword,
                positive=positive,
                candidate=positive,
                truth=True,
            )
        )

    if args.self_test:
        family_counts = defaultdict(lambda: {False: 0, True: 0})
        for row in records:
            truth = bool(row["gold"]["definition_matches"])
            family_counts[row["family_id"]][truth] += 1
        if any(sides != {False: 1, True: 1} for sides in family_counts.values()):
            raise RuntimeError("dictionary pair construction self-test failed")
        print(json.dumps({
            "event": "dictionary_smoke_self_test_ok",
            "dictionary": str(dictionary_path),
            "dictionary_sha256": dictionary_sha,
            "synsets": len(synsets),
            "pairs": len(pairs),
            "records": len(records),
        }, sort_keys=True))
        return

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    checkpoint = resolve_checkpoint(experiment_dir, args.checkpoint)
    loaded = load_current_model(args, experiment_dir, checkpoint)
    model = loaded["model"]
    tokenizer = loaded["tokenizer"]
    pipeline = loaded["pipeline"]
    legacy = loaded["legacy"]
    legacy_experiment = loaded["legacy_experiment"]
    checkpoint_config = loaded["checkpoint_config"]
    controller_info = loaded["controller_info"]

    for row in records:
        pipeline.validate_training_row(row)

    with tempfile.TemporaryDirectory(prefix="nanojev_dictionary_smoke_") as temp_dir:
        probe_path = Path(temp_dir) / "dictionary_probe.jsonl"
        write_probe(records, probe_path)
        examples, _ = pipeline.load_training_examples(
            probe_path,
            tokenizer,
            legacy_experiment["max_length"],
        )
        pipeline.pack_complete_questions(
            examples,
            args.microbatch_questions,
            args.max_microbatch_tokens,
        )

        metrics_by_mode = {}
        control_stats_by_mode = {}
        started = time.perf_counter()
        for mode in args.modes:
            model.set_soft_feedback_mode(mode)
            metrics = legacy.evaluate(
                model,
                examples,
                tokenizer.pad_token_id,
                pipeline,
                precision=args.precision,
                microbatch_questions=args.microbatch_questions,
                max_microbatch_tokens=args.max_microbatch_tokens,
                label=f"dictionary_smoke_{mode}",
                pair_margin=args.pair_margin,
            )
            metrics_by_mode[mode] = metrics
            control_stats_by_mode[mode] = dict(getattr(model.soft_feedback, "last_stats", {}))

    print()
    print("NanoJev dictionary baseline smoke")
    print(f"checkpoint : {checkpoint}")
    print(f"controller : {controller_info['architecture']}")
    if controller_info.get("control_space_size") is not None:
        print(
            "registers  : "
            f"K={controller_info['control_space_size']} "
            f"rank={controller_info['register_rank']} "
            f"dynamic_params={controller_info['dynamic_parameter_count']}"
        )
    print(f"dictionary : {dictionary_path}")
    print(f"pairs      : {len(pairs)} ({len(records)} Boolean questions)")
    print()
    print("mode       dict_score   +accuracy   -accuracy   AUC        pair-win   separation")
    print("---------  -----------  ----------  ----------  ---------  ---------  ----------")
    for mode in args.modes:
        m = metrics_by_mode[mode]
        print(
            f"{mode:<9}  "
            f"{format_pct(m['balanced_accuracy']):>11}  "
            f"{format_pct(m['positive_accuracy']):>10}  "
            f"{format_pct(m['negative_accuracy']):>10}  "
            f"{format_pct(m['auc']):>9}  "
            f"{format_pct(m['pair_win_rate']):>9}  "
            f"{m['probability_separation']:+.4f}"
        )

    deltas = {}
    if "static" in metrics_by_mode and "dynamic" in metrics_by_mode:
        deltas["dynamic_minus_static_balanced_accuracy"] = (
            metrics_by_mode["dynamic"]["balanced_accuracy"]
            - metrics_by_mode["static"]["balanced_accuracy"]
        )
        deltas["dynamic_minus_static_pair_win_rate"] = (
            metrics_by_mode["dynamic"]["pair_win_rate"]
            - metrics_by_mode["static"]["pair_win_rate"]
        )
    if "none" in metrics_by_mode and "dynamic" in metrics_by_mode:
        deltas["dynamic_minus_none_balanced_accuracy"] = (
            metrics_by_mode["dynamic"]["balanced_accuracy"]
            - metrics_by_mode["none"]["balanced_accuracy"]
        )
        deltas["dynamic_minus_none_pair_win_rate"] = (
            metrics_by_mode["dynamic"]["pair_win_rate"]
            - metrics_by_mode["none"]["pair_win_rate"]
        )

    result = {
        "event": "dictionary_smoke_result",
        "score_definition": "balanced_accuracy over exact FALSE/TRUE definition pairs",
        "checkpoint": str(checkpoint),
        "checkpoint_cycle": checkpoint_config.get("main_computer_cycle"),
        "controller": controller_info,
        "control_stats_by_mode": control_stats_by_mode,
        "dictionary": {
            "name": "Open English WordNet 2025",
            "url": args.dictionary_url,
            "cache": str(dictionary_path),
            "sha256": dictionary_sha,
            "parsed_synsets": len(synsets),
        },
        "probe": {
            "seed": args.seed,
            "pairs": len(pairs),
            "questions": len(records),
            "negative_strategy": "same-part-of-speech lexical-overlap hard negative",
        },
        "modes": metrics_by_mode,
        "deltas": deltas,
        "elapsed_seconds": time.perf_counter() - started,
        "training_performed": False,
    }
    print()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
