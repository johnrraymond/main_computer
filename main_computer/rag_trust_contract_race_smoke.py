#!/usr/bin/env python3
from __future__ import annotations

"""
RAG trust-contract race smoke.

Model:
  Every chat turn is a race against time.

  The system may use many pieces:
    - prompt intake
    - retriever
    - evidence packer
    - planner
    - answer drafter
    - verifier

  But no piece is trusted just because it speaks. Each piece must honor a
  contract. The final answer is only accepted if it wins the race AND every
  upstream contract is still valid.

This smoke is deterministic and self-contained. It does not call a model,
network, database, shell, or git. It tests the contract shape the real system
should honor when "hunting down the right answer for every chat forward."
"""

from dataclasses import dataclass, field
import json
import re
import sys
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "with",
}


def tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_./:-]+", text or "")
        if len(token) > 1 and token.lower() not in STOPWORDS
    }


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source: str
    text: str
    produced_at_ms: int
    expires_at_ms: int
    trust: str = "local_verified"

    def is_live_at(self, now_ms: int) -> bool:
        return self.produced_at_ms <= now_ms <= self.expires_at_ms


@dataclass(frozen=True)
class ContractEvent:
    stage: str
    status: str
    started_ms: int
    finished_ms: int
    contract: str
    detail: str
    depends_on: tuple[str, ...] = ()

    @property
    def elapsed_ms(self) -> int:
        return self.finished_ms - self.started_ms


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateAnswer:
    mode: str
    text: str
    claims: tuple[Claim, ...] = ()
    reason: str = ""


@dataclass
class RaceResult:
    case_id: str
    accepted: bool
    final_mode: str
    final_text: str
    elapsed_ms: int
    deadline_ms: int
    events: list[ContractEvent] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "accepted": self.accepted,
            "final_mode": self.final_mode,
            "final_text": self.final_text,
            "elapsed_ms": self.elapsed_ms,
            "deadline_ms": self.deadline_ms,
            "events": [event.__dict__ for event in self.events],
            "failures": self.failures,
        }


@dataclass(frozen=True)
class Case:
    case_id: str
    prompt: str
    evidence: tuple[Evidence, ...]
    deadline_ms: int
    expected_mode: str
    expected_accepted: bool
    injected_delay_ms: int = 0
    cheating_answer: CandidateAnswer | None = None


class TrustContractRace:
    def __init__(self, *, case_id: str, deadline_ms: int) -> None:
        self.case_id = case_id
        self.deadline_ms = deadline_ms
        self.now_ms = 0
        self.events: list[ContractEvent] = []
        self.failures: list[str] = []

    def spend(self, ms: int) -> tuple[int, int]:
        started = self.now_ms
        self.now_ms += max(0, int(ms))
        return started, self.now_ms

    def record(
        self,
        *,
        stage: str,
        status: str,
        started_ms: int,
        finished_ms: int,
        contract: str,
        detail: str,
        depends_on: tuple[str, ...] = (),
    ) -> None:
        self.events.append(
            ContractEvent(
                stage=stage,
                status=status,
                started_ms=started_ms,
                finished_ms=finished_ms,
                contract=contract,
                detail=detail,
                depends_on=depends_on,
            )
        )

    def expired(self) -> bool:
        return self.now_ms > self.deadline_ms

    def intake(self, prompt: str) -> str:
        started, finished = self.spend(3)
        clean = " ".join(str(prompt or "").split())
        status = "ok" if clean else "failed"
        if not clean:
            self.failures.append("intake produced empty prompt")
        self.record(
            stage="intake",
            status=status,
            started_ms=started,
            finished_ms=finished,
            contract="must produce a non-empty normalized prompt",
            detail=clean,
        )
        return clean

    def retrieve(self, prompt: str, evidence_pool: tuple[Evidence, ...]) -> list[Evidence]:
        started, _ = self.spend(7)

        prompt_tokens = tokens(prompt)
        matches: list[Evidence] = []
        for item in evidence_pool:
            if prompt_tokens & tokens(item.text + " " + item.source):
                matches.append(item)

        # Deterministic ordering: strongest lexical overlap first.
        matches.sort(
            key=lambda item: len(prompt_tokens & tokens(item.text + " " + item.source)),
            reverse=True,
        )

        _, finished = self.spend(5)
        live_matches = [item for item in matches if item.is_live_at(self.now_ms)]

        if not live_matches:
            self.failures.append("retriever found no live evidence")

        self.record(
            stage="retriever",
            status="ok" if live_matches else "no_evidence",
            started_ms=started,
            finished_ms=finished,
            contract="must return only evidence that is live at verification time",
            detail=f"{len(live_matches)} live evidence item(s)",
            depends_on=tuple(item.evidence_id for item in live_matches),
        )
        return live_matches

    def pack_context(self, evidence: list[Evidence]) -> list[Evidence]:
        started, finished = self.spend(4)
        unique: dict[str, Evidence] = {}
        for item in evidence:
            unique[item.evidence_id] = item
        packed = list(unique.values())

        self.record(
            stage="context_packer",
            status="ok" if packed else "empty",
            started_ms=started,
            finished_ms=finished,
            contract="must preserve evidence IDs and not invent context",
            detail=f"packed {len(packed)} item(s)",
            depends_on=tuple(item.evidence_id for item in packed),
        )
        return packed

    def draft_answer(self, prompt: str, evidence: list[Evidence], cheating_answer: CandidateAnswer | None) -> CandidateAnswer:
        started, finished = self.spend(9)

        if cheating_answer is not None:
            self.record(
                stage="answer_drafter",
                status="untrusted_candidate",
                started_ms=started,
                finished_ms=finished,
                contract="may propose an answer, but verifier must reject unsupported claims",
                detail=cheating_answer.text,
                depends_on=tuple(
                    evidence_id
                    for claim in cheating_answer.claims
                    for evidence_id in claim.evidence_ids
                ),
            )
            return cheating_answer

        if not evidence:
            answer = CandidateAnswer(
                mode="abstain",
                text="I cannot verify the answer before the deadline because no live evidence was retrieved.",
                reason="no_live_evidence",
            )
            self.record(
                stage="answer_drafter",
                status="abstain",
                started_ms=started,
                finished_ms=finished,
                contract="must abstain when evidence is missing",
                detail=answer.text,
            )
            return answer

        best = evidence[0]
        answer = CandidateAnswer(
            mode="answer",
            text=f"Grounded answer: {best.text}",
            claims=(Claim(text=best.text, evidence_ids=(best.evidence_id,)),),
        )
        self.record(
            stage="answer_drafter",
            status="candidate",
            started_ms=started,
            finished_ms=finished,
            contract="must attach evidence IDs to every factual claim",
            detail=answer.text,
            depends_on=(best.evidence_id,),
        )
        return answer

    def verify(self, candidate: CandidateAnswer, evidence: list[Evidence]) -> CandidateAnswer:
        started, finished = self.spend(8)
        evidence_by_id = {item.evidence_id: item for item in evidence}

        if self.expired():
            self.failures.append("deadline expired before verified answer")
            verified = CandidateAnswer(
                mode="timeout",
                text="Time expired before the system could verify a safe answer.",
                reason="deadline_expired",
            )
            self.record(
                stage="verifier",
                status="timeout",
                started_ms=started,
                finished_ms=finished,
                contract="must reject answers that finish after deadline",
                detail=verified.text,
            )
            return verified

        if candidate.mode == "abstain":
            self.record(
                stage="verifier",
                status="accepted_abstention",
                started_ms=started,
                finished_ms=finished,
                contract="abstention is valid when upstream evidence contract failed",
                detail=candidate.text,
            )
            return candidate

        if candidate.mode != "answer":
            self.failures.append(f"unknown candidate mode: {candidate.mode}")
            return CandidateAnswer(mode="reject", text="Rejected: unknown answer mode.", reason="unknown_mode")

        if not candidate.claims:
            self.failures.append("answer contained no claims")
            return CandidateAnswer(mode="reject", text="Rejected: answer has no claims.", reason="no_claims")

        for claim in candidate.claims:
            if not claim.evidence_ids:
                self.failures.append(f"claim has no evidence IDs: {claim.text}")
                continue

            supporting_text = " ".join(
                evidence_by_id[evidence_id].text
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence_by_id and evidence_by_id[evidence_id].is_live_at(self.now_ms)
            )
            if not supporting_text:
                self.failures.append(f"claim cites missing/stale evidence: {claim.text}")
                continue

            claim_tokens = tokens(claim.text)
            support_tokens = tokens(supporting_text)
            missing = sorted(claim_tokens - support_tokens)
            if missing:
                self.failures.append(
                    f"claim is not anchored to cited evidence: {claim.text}; missing={missing[:8]}"
                )

        accepted = not self.failures
        self.record(
            stage="verifier",
            status="accepted" if accepted else "rejected",
            started_ms=started,
            finished_ms=finished,
            contract="must reject unsupported, stale, uncited, or late claims",
            detail="all claims anchored" if accepted else "; ".join(self.failures),
            depends_on=tuple(
                evidence_id
                for claim in candidate.claims
                for evidence_id in claim.evidence_ids
            ),
        )

        if accepted:
            return candidate
        return CandidateAnswer(mode="reject", text="Rejected: contract verification failed.", reason="verification_failed")

    def run(
        self,
        *,
        prompt: str,
        evidence_pool: tuple[Evidence, ...],
        injected_delay_ms: int = 0,
        cheating_answer: CandidateAnswer | None = None,
    ) -> RaceResult:
        normalized = self.intake(prompt)
        evidence = self.retrieve(normalized, evidence_pool)
        packed = self.pack_context(evidence)

        if injected_delay_ms:
            started, finished = self.spend(injected_delay_ms)
            self.record(
                stage="time_pressure",
                status="delay_injected",
                started_ms=started,
                finished_ms=finished,
                contract="race clock keeps moving while the system hunts",
                detail=f"spent {injected_delay_ms}ms",
            )

        candidate = self.draft_answer(normalized, packed, cheating_answer)
        final = self.verify(candidate, packed)

        accepted = final.mode in {"answer", "abstain"}
        return RaceResult(
            case_id=self.case_id,
            accepted=accepted,
            final_mode=final.mode,
            final_text=final.text,
            elapsed_ms=self.now_ms,
            deadline_ms=self.deadline_ms,
            events=self.events,
            failures=self.failures,
        )


def cases() -> list[Case]:
    return [
        Case(
            case_id="wins_race_with_grounded_answer",
            prompt="What should happen when the selected folder has no .git but a parent Git root exists?",
            evidence=(
                Evidence(
                    evidence_id="repo_boundary_contract",
                    source="main_computer/web/applications/scripts/task-manager.js",
                    text=(
                        "When the selected folder has no .git but a parent Git root exists, "
                        "the UI must ask whether to start Git in this folder or use the parent repository."
                    ),
                    produced_at_ms=0,
                    expires_at_ms=200,
                ),
            ),
            deadline_ms=80,
            expected_mode="answer",
            expected_accepted=True,
        ),
        Case(
            case_id="loses_race_and_times_out",
            prompt="What should happen when the selected folder has no .git but a parent Git root exists?",
            evidence=(
                Evidence(
                    evidence_id="repo_boundary_contract",
                    source="main_computer/web/applications/scripts/task-manager.js",
                    text=(
                        "When the selected folder has no .git but a parent Git root exists, "
                        "the UI must ask whether to start Git in this folder or use the parent repository."
                    ),
                    produced_at_ms=0,
                    expires_at_ms=200,
                ),
            ),
            deadline_ms=25,
            injected_delay_ms=40,
            expected_mode="timeout",
            expected_accepted=False,
        ),
        Case(
            case_id="cheater_claim_rejected",
            prompt="Did the commit runner create the first commit?",
            evidence=(
                Evidence(
                    evidence_id="commit_log",
                    source="terminal",
                    text="git log --oneline -1 returned: fatal: your current branch 'main' does not have any commits yet",
                    produced_at_ms=0,
                    expires_at_ms=200,
                ),
            ),
            deadline_ms=80,
            cheating_answer=CandidateAnswer(
                mode="answer",
                text="Grounded answer: the first commit definitely happened.",
                claims=(
                    Claim(
                        text="the first commit definitely happened",
                        evidence_ids=("commit_log",),
                    ),
                ),
            ),
            expected_mode="reject",
            expected_accepted=False,
        ),
        Case(
            case_id="missing_evidence_abstains",
            prompt="Which exact function updates QuantumNotebookIndex?",
            evidence=(),
            deadline_ms=80,
            expected_mode="abstain",
            expected_accepted=True,
        ),
    ]


def run_case(case: Case) -> RaceResult:
    race = TrustContractRace(case_id=case.case_id, deadline_ms=case.deadline_ms)
    return race.run(
        prompt=case.prompt,
        evidence_pool=case.evidence,
        injected_delay_ms=case.injected_delay_ms,
        cheating_answer=case.cheating_answer,
    )


def main() -> int:
    results = [run_case(case) for case in cases()]
    failures: list[str] = []

    case_by_id = {case.case_id: case for case in cases()}
    for result in results:
        expected = case_by_id[result.case_id]
        if result.final_mode != expected.expected_mode:
            failures.append(
                f"{result.case_id}: expected mode {expected.expected_mode!r}, got {result.final_mode!r}"
            )
        if result.accepted != expected.expected_accepted:
            failures.append(
                f"{result.case_id}: expected accepted={expected.expected_accepted}, got {result.accepted}"
            )

    report = {
        "smoke": "rag_trust_contract_race",
        "passed": not failures,
        "summary": {
            "cases": len(results),
            "failures": failures,
        },
        "results": [result.as_dict() for result in results],
    }
    print(json.dumps(report, indent=2, sort_keys=True))

    if failures:
        return 1
    print("PASS: trust-contract race accepted only answers that honored every upstream contract before deadline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())