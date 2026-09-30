#!/usr/bin/env python3
"""Generic NanoJev object-question boundary used by bolt-on objectives.

An objective owns source-specific data generation.  The trainer owns only this
shape:

    Question -> Candidate -> one or more ObjectPath(prompt, answer)

The current direct-Qwen/logP model already consumes that shape by attribute, so
new objectives can be added without teaching the trainer about their domain.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import random
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ObjectPath:
    prompt: str
    answer: str


@dataclass(frozen=True)
class ObjectCandidate:
    candidate_id: str
    paths: tuple[ObjectPath, ...]


@dataclass(frozen=True)
class ObjectQuestion:
    question_id: str
    task: str
    candidates: tuple[ObjectCandidate, ...]
    gold_index: int
    # Optional objective-owned balancing key.  The generic Qwen/head path ignores it.
    stratum: str = ""


class Objective(Protocol):
    name: str

    def generate_train(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]: ...

    def generate_eval(self, *, count: int, cycle: int, rng: random.Random) -> list[ObjectQuestion]: ...


class ObjectiveRegistry:
    """Small registry intentionally ignorant of objective internals."""

    def __init__(self, objectives: Sequence[Objective] = ()) -> None:
        self._objectives: dict[str, Objective] = {}
        for objective in objectives:
            self.register(objective)

    def register(self, objective: Objective) -> None:
        name = str(objective.name)
        if not name:
            raise ValueError("objective name must be nonempty")
        if name in self._objectives:
            raise ValueError(f"duplicate objective: {name}")
        self._objectives[name] = objective

    def names(self) -> tuple[str, ...]:
        return tuple(self._objectives)

    def get(self, name: str) -> Objective:
        try:
            return self._objectives[name]
        except KeyError as exc:
            raise KeyError(f"objective not registered: {name}") from exc

    def generate_train(self, plan: dict[str, int], *, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        questions: list[ObjectQuestion] = []
        for name, count in plan.items():
            if count <= 0:
                continue
            questions.extend(self.get(name).generate_train(count=int(count), cycle=cycle, rng=rng))
        validate_questions(questions)
        return questions

    def generate_eval(self, plan: dict[str, int], *, cycle: int, rng: random.Random) -> list[ObjectQuestion]:
        questions: list[ObjectQuestion] = []
        for name, count in plan.items():
            if count <= 0:
                continue
            questions.extend(self.get(name).generate_eval(count=int(count), cycle=cycle, rng=rng))
        validate_questions(questions)
        return questions


def binary_yes_no_question(*, question_id: str, task: str, stratum: str,
                           prompt: str, yes_is_gold: bool, shuffle_seed: int) -> ObjectQuestion:
    """Build a deterministic binary object question with candidate-order shuffling."""
    candidates = [
        ObjectCandidate("yes", (ObjectPath(prompt, " Yes"),)),
        ObjectCandidate("no", (ObjectPath(prompt, " No"),)),
    ]
    gold_id = "yes" if yes_is_gold else "no"
    rng = random.Random(int(shuffle_seed))
    rng.shuffle(candidates)
    return ObjectQuestion(
        question_id=question_id,
        task=task,
        candidates=tuple(candidates),
        gold_index=next(i for i, candidate in enumerate(candidates) if candidate.candidate_id == gold_id),
        stratum=stratum,
    )


def question_fingerprint(question: ObjectQuestion) -> str:
    """Content identity independent of candidate order."""
    gold_id = question.candidates[question.gold_index].candidate_id
    payload = {
        "task": question.task,
        "gold": gold_id,
        "candidates": sorted(
            (
                candidate.candidate_id,
                tuple((path.prompt, path.answer) for path in candidate.paths),
            )
            for candidate in question.candidates
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_questions(questions: Sequence[ObjectQuestion]) -> None:
    seen: set[str] = set()
    for question in questions:
        if not question.question_id:
            raise RuntimeError("question id must be nonempty")
        if question.question_id in seen:
            raise RuntimeError(f"duplicate question id: {question.question_id}")
        seen.add(question.question_id)
        if len(question.candidates) < 2:
            raise RuntimeError(f"question has fewer than two candidates: {question.question_id}")
        if not 0 <= int(question.gold_index) < len(question.candidates):
            raise RuntimeError(f"gold index out of range: {question.question_id}")
        candidate_ids = [candidate.candidate_id for candidate in question.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise RuntimeError(f"duplicate candidate ids: {question.question_id}")
        for candidate in question.candidates:
            if not candidate.paths:
                raise RuntimeError(f"candidate has no paths: {question.question_id}/{candidate.candidate_id}")
            for path in candidate.paths:
                if not path.prompt or not path.answer:
                    raise RuntimeError(f"empty object path: {question.question_id}/{candidate.candidate_id}")
