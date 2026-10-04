#!/usr/bin/env python3
"""Read-only synthetic math-transfer smoke for the trained three-backbone CLEF head.

The smoke deliberately does not train anything. It resolves the current champion
checkpoint, freezes all three backbones and the CLEF head, generates a large
deterministic population of fresh integer-expression questions, and asks the
existing head to rank one exact answer against numerically plausible distractors.

The generator is intentionally independent of the training curriculum and can
produce an effectively unbounded stream from a seed. The default four-way test
therefore has a 25% random-choice baseline. Results are reported overall and by
expression stratum, with Wilson 95% intervals and per-question rows retained for
error inspection.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import sys
import time
from types import SimpleNamespace
from typing import Any, Sequence

TOOLS = Path(__file__).resolve().parent


def load_local_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trainer = load_local_module(
    "nanojev_three_backbone_clef_math_smoke_train_library",
    TOOLS / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py",
)
objective_api = load_local_module(
    "nanojev_three_backbone_clef_math_smoke_objective_api",
    TOOLS / "nanojev_objective_api.py",
)
smoke = trainer.smoke

DEFAULT_EXPERIMENT_DIR = Path(trainer.DEFAULT_OUTPUT)
DEFAULT_OUTPUT_ROOT = Path("diagnostics_output") / "nanojev_three_backbone_clef_math_expression_smoke"
DEFAULT_QUESTIONS = 1000
DEFAULT_CANDIDATES = 4
DEFAULT_SEED = 20261004
STRATA = (
    "add_sub",
    "multiply",
    "exact_division",
    "precedence",
    "parenthesized",
    "nested_product",
)


@dataclass(frozen=True)
class MathCase:
    expression: str
    value: int
    distractors: tuple[int, ...]
    stratum: str


@dataclass(frozen=True)
class GeneratedQuestion:
    question: Any
    expression: str
    correct_value: int
    candidate_values: tuple[int, ...]
    stratum: str


def read_json(path: Path) -> dict[str, Any]:
    return smoke.read_json(Path(path))


def _nonzero(rng: random.Random, low: int, high: int) -> int:
    while True:
        value = rng.randint(low, high)
        if value != 0:
            return value


def _unique_distractors(correct: int, values: Sequence[int], *, needed: int, rng: random.Random) -> tuple[int, ...]:
    pool: list[int] = []
    seen = {int(correct)}
    for value in values:
        value = int(value)
        if value in seen:
            continue
        seen.add(value)
        pool.append(value)
    # Guaranteed deterministic fallbacks. They remain close enough to be plausible
    # arithmetic slips while preventing a rare duplicate-heavy case from failing.
    radius = 1
    while len(pool) < needed:
        for value in (correct + radius, correct - radius):
            if value not in seen:
                seen.add(value)
                pool.append(value)
                if len(pool) >= needed:
                    break
        radius += 1
    rng.shuffle(pool)
    return tuple(pool[:needed])


def generate_case(stratum: str, rng: random.Random, *, distractor_count: int) -> MathCase:
    if stratum == "add_sub":
        a = rng.randint(-75, 75)
        b = _nonzero(rng, -40, 40)
        if rng.random() < 0.5:
            value = a + b
            expression = f"{a} + {b}"
            raw = (a - b, b - a, value + 1, value - 1, -value, value + 2, value - 2)
        else:
            value = a - b
            expression = f"{a} - ({b})" if b < 0 else f"{a} - {b}"
            raw = (a + b, b - a, value + 1, value - 1, -value, value + 2, value - 2)

    elif stratum == "multiply":
        a = _nonzero(rng, -18, 18)
        b = _nonzero(rng, -15, 15)
        value = a * b
        expression = f"{a} * {b}"
        raw = (
            (a + 1) * b,
            (a - 1) * b,
            a * (b + 1),
            a * (b - 1),
            a + b,
            value + 1,
            value - 1,
            -value,
        )

    elif stratum == "exact_division":
        divisor = rng.randint(2, 15)
        quotient = _nonzero(rng, -30, 30)
        numerator = divisor * quotient
        value = quotient
        expression = f"{numerator} / {divisor}"
        raw = (
            quotient + 1,
            quotient - 1,
            -quotient,
            numerator,
            divisor,
            quotient + divisor,
            quotient - divisor,
        )

    elif stratum == "precedence":
        a = rng.randint(-20, 20)
        b = _nonzero(rng, -12, 12)
        c = _nonzero(rng, -10, 10)
        variant = rng.randrange(3)
        if variant == 0:
            value = a + b * c
            expression = f"{a} + {b} * {c}"
            wrong_precedence = (a + b) * c
            raw = (wrong_precedence, a + b + c, a - b * c, value + c, value - c, value + 1, value - 1)
        elif variant == 1:
            value = a * b - c
            expression = f"{a} * {b} - ({c})" if c < 0 else f"{a} * {b} - {c}"
            wrong_precedence = a * (b - c)
            raw = (wrong_precedence, a * b + c, a + b - c, value + b, value - b, value + 1, value - 1)
        else:
            value = a - b * c
            expression = f"{a} - {b} * {c}"
            wrong_precedence = (a - b) * c
            raw = (wrong_precedence, a - b - c, a + b * c, value + c, value - c, value + 1, value - 1)

    elif stratum == "parenthesized":
        a = rng.randint(-15, 15)
        b = _nonzero(rng, -12, 12)
        c = _nonzero(rng, -9, 9)
        if rng.random() < 0.5:
            value = (a + b) * c
            expression = f"({a} + {b}) * {c}"
            unparenthesized = a + b * c
            raw = (unparenthesized, (a - b) * c, a + b + c, value + c, value - c, value + 1, value - 1)
        else:
            value = (a - b) * c
            expression = f"({a} - {b}) * {c}"
            unparenthesized = a - b * c
            raw = (unparenthesized, (a + b) * c, a - b - c, value + c, value - c, value + 1, value - 1)

    elif stratum == "nested_product":
        a = rng.randint(-10, 10)
        b = _nonzero(rng, -8, 8)
        c = rng.randint(-10, 10)
        d = _nonzero(rng, -8, 8)
        left = a + b
        right = c - d
        value = left * right
        expression = f"({a} + {b}) * ({c} - {d})"
        raw = (
            a + b * (c - d),
            (a + b) * c - d,
            (a - b) * (c - d),
            (a + b) * (c + d),
            value + left,
            value - right,
            value + 1,
            value - 1,
        )

    else:
        raise ValueError(f"unknown math stratum: {stratum}")

    distractors = _unique_distractors(value, raw, needed=distractor_count, rng=rng)
    return MathCase(expression=expression, value=int(value), distractors=distractors, stratum=stratum)


def build_question(case: MathCase, *, index: int, seed: int, candidate_count: int) -> GeneratedQuestion:
    if candidate_count < 2:
        raise ValueError("candidate_count must be at least 2")
    prompt = (
        "Evaluate the arithmetic expression exactly. Use ordinary integer arithmetic and "
        "standard order of operations. Return only the numerical value.\n"
        f"Expression: {case.expression}\n"
        "Answer:"
    )
    values = [case.value, *case.distractors[: candidate_count - 1]]
    candidates = [
        objective_api.ObjectCandidate(
            "correct" if value == case.value else f"distractor-{slot}",
            (objective_api.ObjectPath(prompt, f" {value}"),),
        )
        for slot, value in enumerate(values)
    ]
    order_rng = random.Random((int(seed) << 32) ^ int(index) ^ 0x4D415448)
    order_rng.shuffle(candidates)
    gold_index = next(i for i, candidate in enumerate(candidates) if candidate.candidate_id == "correct")
    candidate_values = tuple(int(candidate.paths[0].answer.strip()) for candidate in candidates)
    question = objective_api.ObjectQuestion(
        question_id=f"math-expression-smoke:{seed}:{index:07d}",
        task="math_expression_smoke",
        candidates=tuple(candidates),
        gold_index=gold_index,
        stratum=case.stratum,
    )
    return GeneratedQuestion(
        question=question,
        expression=case.expression,
        correct_value=case.value,
        candidate_values=candidate_values,
        stratum=case.stratum,
    )


def generate_questions(*, count: int, seed: int, candidate_count: int) -> list[GeneratedQuestion]:
    if count <= 0:
        raise ValueError("count must be positive")
    if candidate_count < 2 or candidate_count > 8:
        raise ValueError("candidate_count must be between 2 and 8")
    rng = random.Random(int(seed))
    rows: list[GeneratedQuestion] = []
    for index in range(int(count)):
        # Round-robin strata makes every prefix balanced to within one question.
        stratum = STRATA[index % len(STRATA)]
        case = generate_case(stratum, rng, distractor_count=candidate_count - 1)
        rows.append(build_question(case, index=index, seed=seed, candidate_count=candidate_count))
    objective_api.validate_questions([row.question for row in rows])
    return rows


def wilson_interval(correct: int, total: int, z: float = 1.959963984540054) -> dict[str, float]:
    if total <= 0:
        return {"low": math.nan, "high": math.nan}
    p = float(correct) / float(total)
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    radius = (z / denom) * math.sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total * total))
    return {"low": max(0.0, center - radius), "high": min(1.0, center + radius)}


def summarize(rows: Sequence[dict[str, Any]], *, candidate_count: int) -> dict[str, Any]:
    def aggregate(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
        total = len(items)
        correct = sum(int(bool(row["correct"])) for row in items)
        accuracy = correct / total if total else math.nan
        return {
            "questions": total,
            "correct": correct,
            "accuracy": accuracy,
            "accuracy_ci95": wilson_interval(correct, total),
            "mean_loss": sum(float(row["loss"]) for row in items) / total if total else math.nan,
            "mean_gold_probability": sum(float(row["gold_probability"]) for row in items) / total if total else math.nan,
            "mean_gold_margin": sum(float(row["gold_margin"]) for row in items) / total if total else math.nan,
        }

    by_stratum: dict[str, list[dict[str, Any]]] = {name: [] for name in STRATA}
    for row in rows:
        by_stratum[str(row["stratum"])].append(row)
    overall = aggregate(rows)
    chance = 1.0 / int(candidate_count)
    overall["chance_accuracy"] = chance
    overall["accuracy_minus_chance"] = float(overall["accuracy"]) - chance
    overall["ci95_above_chance"] = float(overall["accuracy_ci95"]["low"]) > chance
    return {
        "overall": overall,
        "by_stratum": {name: aggregate(by_stratum[name]) for name in STRATA},
    }


def resolve_checkpoint(experiment_dir: Path, checkpoint: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    experiment_dir = Path(experiment_dir).expanduser().resolve(strict=True)
    experiment = read_json(experiment_dir / "experiment.json")
    state = read_json(experiment_dir / "training_state.json")
    if checkpoint == "champion":
        path = Path(str(state["best_checkpoint"]))
    elif checkpoint == "latest":
        path = Path(str(state["latest_checkpoint"]))
    else:
        path = Path(str(checkpoint)).expanduser()
    path = path.resolve(strict=True)
    for filename in ("head.safetensors", "tinystories.safetensors", "meta.json"):
        if not (path / filename).is_file():
            raise RuntimeError(f"checkpoint missing {filename}: {path}")
    return path, experiment, state


def eval_namespace(experiment: dict[str, Any]) -> SimpleNamespace:
    hp = dict(experiment.get("hyperparameters") or {})
    return SimpleNamespace(
        path_batch=int(hp.get("path_batch", smoke.DEFAULT_PATH_BATCH)),
        max_prompt_tokens=int(hp.get("max_prompt_tokens", smoke.DEFAULT_MAX_PROMPT_TOKENS)),
        max_answer_tokens=int(hp.get("max_answer_tokens", smoke.DEFAULT_MAX_ANSWER_TOKENS)),
        prompt_evidence_tokens=int(hp.get("prompt_evidence_tokens", smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)),
        answer_evidence_tokens=int(hp.get("answer_evidence_tokens", smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)),
    )


def make_output_dir(root: Path, *, seed: int, count: int) -> Path:
    root = Path(root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = root / f"math-{count}-seed-{seed}-{stamp}"
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate.resolve()


def plan_payload(generated: Sequence[GeneratedQuestion], *, seed: int, candidate_count: int, sample_count: int) -> dict[str, Any]:
    counts = {name: 0 for name in STRATA}
    for row in generated:
        counts[row.stratum] += 1
    return {
        "event": "clef_math_expression_smoke_plan",
        "questions": len(generated),
        "candidate_count": int(candidate_count),
        "chance_accuracy": 1.0 / int(candidate_count),
        "seed": int(seed),
        "strata": counts,
        "samples": [
            {
                "question_id": row.question.question_id,
                "stratum": row.stratum,
                "expression": row.expression,
                "candidate_values": list(row.candidate_values),
                "gold_index": int(row.question.gold_index),
                "correct_value": int(row.correct_value),
            }
            for row in generated[: max(0, int(sample_count))]
        ],
    }


def run(args) -> dict[str, Any]:
    generated = generate_questions(
        count=int(args.questions), seed=int(args.seed), candidate_count=int(args.candidates)
    )
    plan = plan_payload(
        generated, seed=int(args.seed), candidate_count=int(args.candidates), sample_count=int(args.sample_count)
    )
    if args.plan_only:
        print(json.dumps(plan, sort_keys=True), flush=True)
        return plan

    import torch
    from safetensors.torch import load_file

    checkpoint, experiment, state = resolve_checkpoint(Path(args.experiment_dir), str(args.checkpoint))
    question_source = Path(str(experiment["question_source_experiment"])).expanduser().resolve(strict=True)
    source_manifest = read_json(question_source / "experiment.json")
    source = dict(source_manifest.get("source") or {})
    if not source.get("model") or not source.get("revision"):
        raise RuntimeError(f"question source has incomplete model lineage: {question_source}")

    output_dir = make_output_dir(Path(args.output_root), seed=int(args.seed), count=int(args.questions))
    logger = trainer.EventLog(output_dir, verbose_console=bool(args.verbose_events))
    smoke.atomic_json(output_dir / "plan.json", plan)
    logger.emit(
        "clef_math_expression_smoke_start",
        experiment_dir=str(Path(args.experiment_dir).expanduser().resolve()),
        checkpoint=str(checkpoint),
        checkpoint_selector=str(args.checkpoint),
        questions=int(args.questions),
        candidates=int(args.candidates),
        chance_accuracy=1.0 / int(args.candidates),
        seed=int(args.seed),
        optimizer_steps=0,
    )

    torch.manual_seed(int(args.seed))
    torch.cuda.manual_seed_all(int(args.seed))
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    logger.set_stage("backbone_load")
    bundles, _ = smoke.load_backbones(
        source=source,
        local_files_only=bool(args.local_files_only),
        logger=logger,
    )
    bundles[trainer.TRAINABLE_LABEL].lm.load_state_dict(
        load_file(str(checkpoint / "tinystories.safetensors"), device="cpu"), strict=True
    )
    for bundle in bundles.values():
        smoke.freeze_module(bundle.lm)
        bundle.lm.eval()

    logger.set_stage("head_load")
    hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
    Head = smoke.build_head_class()
    head = Head(hidden_sizes)
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    head = head.to(device="cuda", dtype=torch.bfloat16)
    for parameter in head.parameters():
        parameter.requires_grad_(False)
    head.eval()

    eval_args = eval_namespace(experiment)
    rows: list[dict[str, Any]] = []
    logger.set_stage("math_eval")
    for index, generated_row in enumerate(generated, 1):
        question = generated_row.question
        with torch.no_grad():
            evidence = smoke.extract_live_evidence(
                torch=torch, bundles=bundles, question=question, args=eval_args, logger=logger
            )
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
        row = trainer.metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
        predicted_value = int(generated_row.candidate_values[int(row["predicted_index"])])
        row.update(
            {
                "stratum": generated_row.stratum,
                "expression": generated_row.expression,
                "correct_value": int(generated_row.correct_value),
                "candidate_values": list(generated_row.candidate_values),
                "predicted_value": predicted_value,
            }
        )
        rows.append(row)
        if index % int(args.progress_every) == 0 or index == len(generated):
            partial = summarize(rows, candidate_count=int(args.candidates))["overall"]
            logger.emit(
                "clef_math_expression_smoke_progress",
                completed=index,
                total=len(generated),
                accuracy=partial["accuracy"],
                accuracy_ci95=partial["accuracy_ci95"],
                mean_gold_probability=partial["mean_gold_probability"],
            )
        del evidence, logits, loss, ce, brier

    summary = summarize(rows, candidate_count=int(args.candidates))
    wrong = [row for row in rows if not bool(row["correct"])]
    wrong.sort(key=lambda row: float(row["gold_probability"]))
    result = {
        "schema_version": "main-computer-three-backbone-clef-math-expression-smoke-v1",
        "experiment_dir": str(Path(args.experiment_dir).expanduser().resolve()),
        "checkpoint": str(checkpoint),
        "checkpoint_selector": str(args.checkpoint),
        "champion_checkpoint_at_start": str(state.get("best_checkpoint")),
        "checkpoint_head_sha256": trainer.sha256_file(checkpoint / "head.safetensors"),
        "checkpoint_tinystories_sha256": trainer.sha256_file(checkpoint / "tinystories.safetensors"),
        "questions": len(generated),
        "candidates": int(args.candidates),
        "seed": int(args.seed),
        "optimizer_steps": 0,
        "summary": summary,
        "hardest_errors": wrong[: min(20, len(wrong))],
        "output_dir": str(output_dir),
        "memory": smoke.cuda_memory(torch, "math_smoke_complete"),
    }
    smoke.atomic_json(output_dir / "rows.json", {"rows": rows})
    smoke.atomic_json(output_dir / "result.json", result)
    logger.set_stage("complete")
    logger.emit(
        "clef_math_expression_smoke_complete",
        output_dir=str(output_dir),
        checkpoint=str(checkpoint),
        questions=len(generated),
        accuracy=summary["overall"]["accuracy"],
        accuracy_ci95=summary["overall"]["accuracy_ci95"],
        chance_accuracy=summary["overall"]["chance_accuracy"],
        ci95_above_chance=summary["overall"]["ci95_above_chance"],
        by_stratum=summary["by_stratum"],
        optimizer_steps=0,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def self_test() -> dict[str, Any]:
    generated = generate_questions(count=120, seed=17, candidate_count=4)
    assert len(generated) == 120
    assert len({row.question.question_id for row in generated}) == 120
    counts = {name: 0 for name in STRATA}
    for row in generated:
        counts[row.stratum] += 1
        assert len(row.question.candidates) == 4
        assert row.candidate_values[row.question.gold_index] == row.correct_value
        assert len(set(row.candidate_values)) == 4
        assert row.stratum in STRATA
    assert set(counts.values()) == {20}

    again = generate_questions(count=120, seed=17, candidate_count=4)
    signature = [(r.expression, r.candidate_values, r.question.gold_index) for r in generated]
    signature_again = [(r.expression, r.candidate_values, r.question.gold_index) for r in again]
    assert signature == signature_again

    fake_rows = []
    for i, row in enumerate(generated):
        fake_rows.append(
            {
                "correct": i % 2 == 0,
                "loss": 0.5,
                "gold_probability": 0.5,
                "gold_margin": 0.1,
                "stratum": row.stratum,
            }
        )
    summary = summarize(fake_rows, candidate_count=4)
    assert summary["overall"]["questions"] == 120
    assert summary["overall"]["accuracy"] == 0.5
    assert summary["overall"]["chance_accuracy"] == 0.25
    assert all(v["questions"] == 20 for v in summary["by_stratum"].values())
    return {
        "ok": True,
        "questions": len(generated),
        "candidate_count": 4,
        "strata": counts,
        "deterministic": True,
        "optimizer_steps": 0,
    }


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=str(DEFAULT_EXPERIMENT_DIR))
    parser.add_argument(
        "--checkpoint",
        default="champion",
        help="champion (default), latest, or an explicit checkpoint directory",
    )
    parser.add_argument("--questions", type=int, default=DEFAULT_QUESTIONS)
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--sample-count", type=int, default=12)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--verbose-events", action="store_true")
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.questions <= 0:
        parser.error("--questions must be positive")
    if not 2 <= args.candidates <= 8:
        parser.error("--candidates must be between 2 and 8")
    if args.sample_count < 0:
        parser.error("--sample-count must be nonnegative")
    if args.progress_every <= 0:
        parser.error("--progress-every must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True), flush=True)
        return 0
    try:
        run(args)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "clef_math_expression_smoke_failed",
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
