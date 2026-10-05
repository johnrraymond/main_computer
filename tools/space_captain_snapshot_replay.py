from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

DEFAULT_WEIGHTS = (0.30, 1.0 / 3.0, 0.35, 3.0 / 8.0, 0.40)
DEFAULT_WEIGHT = 3.0 / 8.0


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return json.loads(raw.decode(encoding))
        except Exception as exc:  # pragma: no cover - diagnostic path
            errors.append(f"{encoding}: {exc}")
    raise ValueError(f"could not decode/parse JSON file {path}: {'; '.join(errors)}")


def _calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("callSnapshots")
    if rows is None:
        rows = payload.get("calls")
    if not isinstance(rows, list) or not rows:
        raise ValueError("snapshot payload must contain non-empty callSnapshots or calls")
    return [dict(row) for row in rows]


def _rank(scores: dict[str, float]) -> str:
    if not scores:
        raise ValueError("cannot rank empty scores")
    return sorted(scores, key=lambda option: (-float(scores[option]), option))[0]


def _scores_from_choices(
    call: dict[str, Any],
    field: str,
    fallback_field: str,
    question_count: int | None,
) -> dict[str, int]:
    choices = call.get(field)
    if choices is not None:
        values = [str(value) for value in choices]
        if question_count is not None:
            if question_count <= 0:
                raise ValueError("question_count must be positive")
            if question_count > len(values):
                raise ValueError(
                    f"question_count={question_count} exceeds stored choices={len(values)} "
                    f"for {call.get('jacketId')} {call.get('decisionLayer')}"
                )
            values = values[:question_count]
        scores: dict[str, int] = {}
        for value in values:
            scores[value] = scores.get(value, 0) + 1
        return scores
    if question_count is not None and question_count != int(call.get("questionCount") or 0):
        raise ValueError(
            "question-count replay requires cleanAnswerChoices/noisyAnswerChoices; "
            "this older snapshot only contains aggregate vote totals"
        )
    raw = call.get(fallback_field)
    if not isinstance(raw, dict):
        raise ValueError(f"call missing {fallback_field}")
    return {str(key): int(value) for key, value in raw.items()}


def synthesize_vote_scores(
    scores: dict[str, int],
    coherence_target: str | None,
    coherence_weight: float,
) -> tuple[str, dict[str, float]]:
    if not (0.0 <= coherence_weight < 1.0):
        raise ValueError("coherence_weight must be in [0, 1)")
    total = max(1, sum(int(value) for value in scores.values()))
    adjusted = {
        option: (1.0 - coherence_weight) * (int(votes) / total)
        + (coherence_weight if option == coherence_target else 0.0)
        for option, votes in scores.items()
    }
    return _rank(adjusted), adjusted


def _system_choice(
    call: dict[str, Any],
    *,
    noisy: bool,
    coherence_weight: float,
    question_count: int | None,
) -> str:
    if str(call.get("decisionLayer")) == "assessment":
        authoritative = call.get("assessmentAuthoritativeChoice")
        if authoritative:
            return str(authoritative)
        # Older/reference fixtures may not expose a separate authoritative field.
        return str(call.get("actualChoice" if noisy else "cleanChoice"))
    scores = _scores_from_choices(
        call,
        "noisyAnswerChoices" if noisy else "cleanAnswerChoices",
        "noisyChoiceScores" if noisy else "cleanChoiceScores",
        question_count,
    )
    choice, _ = synthesize_vote_scores(
        scores,
        str(call.get("planCoherenceTarget")) if call.get("planCoherenceTarget") else None,
        coherence_weight,
    )
    return choice


def _raw_choice(
    call: dict[str, Any],
    *,
    noisy: bool,
    question_count: int | None,
) -> str | None:
    if str(call.get("decisionLayer")) == "assessment":
        return None
    scores = _scores_from_choices(
        call,
        "noisyAnswerChoices" if noisy else "cleanAnswerChoices",
        "noisyChoiceScores" if noisy else "cleanChoiceScores",
        question_count,
    )
    return _rank(scores)


def replay_calls(
    calls: Iterable[dict[str, Any]],
    *,
    coherence_weight: float,
    question_count: int | None = None,
) -> dict[str, Any]:
    rows = list(calls)
    clean_expected = 0
    noisy_expected = 0
    stable = 0
    raw_evaluated = 0
    raw_clean_expected = 0
    raw_noisy_expected = 0
    raw_stable = 0
    failures: list[dict[str, Any]] = []

    for call in rows:
        expected = str(call.get("expectedChoice"))
        clean = _system_choice(
            call,
            noisy=False,
            coherence_weight=coherence_weight,
            question_count=question_count,
        )
        noisy = _system_choice(
            call,
            noisy=True,
            coherence_weight=coherence_weight,
            question_count=question_count,
        )
        clean_ok = clean == expected
        noisy_ok = noisy == expected
        stable_ok = clean == noisy
        clean_expected += int(clean_ok)
        noisy_expected += int(noisy_ok)
        stable += int(stable_ok)
        if not (clean_ok and noisy_ok and stable_ok):
            failures.append(
                {
                    "stepIndex": call.get("stepIndex"),
                    "jacketId": call.get("jacketId"),
                    "decisionLayer": call.get("decisionLayer"),
                    "expectedChoice": expected,
                    "cleanChoice": clean,
                    "noisyChoice": noisy,
                }
            )

        raw_clean = _raw_choice(call, noisy=False, question_count=question_count)
        raw_noisy = _raw_choice(call, noisy=True, question_count=question_count)
        if raw_clean is not None and raw_noisy is not None:
            raw_evaluated += 1
            raw_clean_expected += int(raw_clean == expected)
            raw_noisy_expected += int(raw_noisy == expected)
            raw_stable += int(raw_clean == raw_noisy)

    total = len(rows)
    return {
        "coherenceWeight": coherence_weight,
        "questionCount": question_count,
        "calls": total,
        "cleanExpected": clean_expected,
        "noisyExpected": noisy_expected,
        "stable": stable,
        "systemOk": clean_expected == total and noisy_expected == total and stable == total,
        "rawModelDiagnostics": {
            "callsEvaluated": raw_evaluated,
            "cleanExpected": raw_clean_expected,
            "noisyExpected": raw_noisy_expected,
            "stable": raw_stable,
        },
        "failures": failures,
    }


def _parse_weights(raw: str) -> list[float]:
    values = [float(part.strip()) for part in raw.split(",") if part.strip()]
    if not values:
        raise argparse.ArgumentTypeError("weights list cannot be empty")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay captain synthesis from saved live-call snapshots without loading models/CUDA."
    )
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--weights",
        type=_parse_weights,
        default=list(DEFAULT_WEIGHTS),
        help="comma-separated coherence weights (default: 0.30,1/3-ish,0.35,0.375,0.40)",
    )
    parser.add_argument(
        "--question-count",
        type=int,
        default=None,
        help="replay only the first N stored per-question choices; requires new-format choice telemetry",
    )
    parser.add_argument(
        "--assert-weight",
        type=float,
        default=None,
        help="exit nonzero unless this weight yields clean/noisy/stable success for every call",
    )
    args = parser.parse_args()

    payload = _read_json(args.snapshot)
    calls = _calls(payload)
    rows = [
        replay_calls(
            calls,
            coherence_weight=float(weight),
            question_count=args.question_count,
        )
        for weight in args.weights
    ]
    result = {
        "schema": "space.captainSnapshotReplay.v1",
        "source": str(args.snapshot),
        "callCount": len(calls),
        "rows": rows,
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.assert_weight is not None:
        asserted = replay_calls(
            calls,
            coherence_weight=float(args.assert_weight),
            question_count=args.question_count,
        )
        return 0 if asserted["systemOk"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
