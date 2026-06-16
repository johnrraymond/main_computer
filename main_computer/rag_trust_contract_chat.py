#!/usr/bin/env python3
from __future__ import annotations

"""Trust-contract RAG chat mode with JSONL pipe control frames.

Callable from chat subprocess code or as a console ``--stdio`` program.  The
chat answer is returned as the final result; contract/race/status frames are
emitted separately as JSON objects with ``channel: "oob"`` so the parent can
observe control flow without trusting ordinary model text.
"""

import argparse
from dataclasses import asdict, dataclass, field
import json
import re
import sys
import time
from typing import Any, Callable, Iterable, Sequence

from main_computer.ai_control import ai_control_prompt_text
from main_computer.models import ChatMessage, ChatResponse


FrameEmitter = Callable[[dict[str, Any]], None]

STOPWORDS = {
    "a", "an", "and", "are", "as", "be", "by", "for", "from", "has", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "with",
}

TRUST_CONTRACT_SYSTEM_PROMPT = """\
You are running inside Main Computer's trust-contract RAG chat mode.

Return one JSON object only, with this schema:
{
  "mode": "answer" | "abstain",
  "answer": "final user-visible answer",
  "claims": [
    {"text": "single factual claim", "evidence_ids": ["evidence-id"]}
  ]
}

Rules:
- Every factual claim must cite at least one supplied evidence_id.
- Do not invent evidence IDs.
- If the evidence is missing, stale, or insufficient, return mode "abstain".
- The verifier may reject your output. Contract compliance matters more than
  sounding confident.
"""


@dataclass(frozen=True)
class TrustEvidence:
    evidence_id: str
    source: str
    text: str
    produced_at_ms: int = 0
    expires_at_ms: int = 60_000
    trust: str = "local_verified"

    def is_live_at(self, elapsed_ms: int) -> bool:
        return self.produced_at_ms <= elapsed_ms <= self.expires_at_ms

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrustClaim:
    text: str
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class TrustCandidate:
    mode: str
    answer: str
    claims: tuple[TrustClaim, ...] = ()
    provider: str = "deterministic"
    model: str = "trust-contract-rag"
    raw_response: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "answer": self.answer,
            "claims": [claim.as_dict() for claim in self.claims],
            "provider": self.provider,
            "model": self.model,
            "raw_response": self.raw_response,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TrustContractEvent:
    stage: str
    status: str
    elapsed_ms: int
    contract: str
    detail: str
    depends_on: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "contract": self.contract,
            "detail": self.detail,
            "depends_on": list(self.depends_on),
        }


@dataclass
class TrustContractChatResult:
    run_id: str
    ok: bool
    status: str
    final_mode: str
    answer: str
    provider: str
    model: str
    elapsed_ms: int
    deadline_ms: int
    evidence: list[TrustEvidence] = field(default_factory=list)
    events: list[TrustContractEvent] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    candidate: TrustCandidate | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "status": self.status,
            "final_mode": self.final_mode,
            "answer": self.answer,
            "provider": self.provider,
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "deadline_ms": self.deadline_ms,
            "evidence": [item.as_dict() for item in self.evidence],
            "events": [event.as_dict() for event in self.events],
            "failures": list(self.failures),
            "candidate": self.candidate.as_dict() if self.candidate else None,
        }


class TrustContractChatRunner:
    def __init__(self, *, run_id: str, deadline_ms: int, emit: FrameEmitter | None = None) -> None:
        self.run_id = str(run_id or f"trust_contract_{int(time.time() * 1000)}")
        self.deadline_ms = max(1, int(deadline_ms or 30_000))
        self.emit = emit
        self.started_at = time.monotonic()
        self.events: list[TrustContractEvent] = []
        self.failures: list[str] = []

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def frame(self, frame_type: str, **payload: Any) -> None:
        if self.emit is None:
            return
        frame = {
            "type": frame_type,
            "channel": payload.pop("channel", "oob"),
            "run_id": self.run_id,
            "elapsed_ms": self.elapsed_ms(),
            **payload,
        }
        self.emit(frame)

    def record(self, *, stage: str, status: str, contract: str, detail: str, depends_on: Iterable[str] = ()) -> TrustContractEvent:
        event = TrustContractEvent(
            stage=stage,
            status=status,
            elapsed_ms=self.elapsed_ms(),
            contract=contract,
            detail=detail,
            depends_on=tuple(str(item) for item in depends_on if str(item)),
        )
        self.events.append(event)
        self.frame(
            "control",
            event="contract_event",
            stage=event.stage,
            status=event.status,
            contract=event.contract,
            detail=event.detail,
            depends_on=list(event.depends_on),
        )
        self.frame(
            "activity",
            event={
                "source": "rag-trust-contract-chat",
                "kind": "ai",
                "time_model": "parallel",
                "severity": "info" if status not in {"failed", "rejected", "timeout"} else "warn",
                "title": f"Trust contract {stage}",
                "message": detail[:1000],
                "status": "running" if status not in {"failed", "rejected", "timeout", "accepted", "abstain"} else status,
                "tags": ["ai", "rag", "trust-contract", "chat-console", "pipe-control"],
                "data": {
                    "run_id": self.run_id,
                    "activity_filter": "ai",
                    "rag_type": "rag_trust_contract_chat",
                    "stage": stage,
                    "contract_status": status,
                    "contract": contract,
                    "depends_on": list(event.depends_on),
                    "elapsed_ms": event.elapsed_ms,
                    "running_text": detail[:500],
                    "raw_thinking_exposed": False,
                },
            },
        )
        return event

    def expired(self) -> bool:
        return self.elapsed_ms() > self.deadline_ms

    def normalize_prompt(self, prompt: str, messages: Sequence[ChatMessage]) -> str:
        text = " ".join(str(prompt or "").split())
        if not text:
            for message in reversed(list(messages or [])):
                if getattr(message, "role", "") == "user" and str(getattr(message, "content", "")).strip():
                    text = " ".join(str(message.content).split())
                    break
        if not text:
            self.failures.append("empty chat prompt")
            self.record(
                stage="intake",
                status="failed",
                contract="must produce a non-empty normalized prompt from chat history",
                detail="empty prompt",
            )
            return ""
        self.record(
            stage="intake",
            status="ok",
            contract="must produce a non-empty normalized prompt from chat history",
            detail=text[:700],
        )
        return text

    def retrieve(self, prompt: str, evidence: Sequence[TrustEvidence]) -> list[TrustEvidence]:
        prompt_tokens = _tokens(prompt)
        matches: list[TrustEvidence] = []
        for item in evidence:
            if prompt_tokens & _tokens(f"{item.source} {item.text}"):
                matches.append(item)
        matches.sort(key=lambda item: len(prompt_tokens & _tokens(f"{item.source} {item.text}")), reverse=True)
        live = [item for item in matches if item.is_live_at(self.elapsed_ms())]
        if not live:
            self.failures.append("retriever found no live evidence")
        self.record(
            stage="retriever",
            status="ok" if live else "no_evidence",
            contract="must return only evidence that is live and lexically related to the chat turn",
            detail=f"{len(live)} live evidence item(s)",
            depends_on=[item.evidence_id for item in live],
        )
        return live

    def draft(self, *, prompt: str, messages: Sequence[ChatMessage], evidence: Sequence[TrustEvidence], provider: Any | None) -> TrustCandidate:
        if self.expired():
            self.record(
                stage="answer_drafter",
                status="timeout",
                contract="must not start a model call after the deadline has already expired",
                detail="deadline expired before candidate drafting",
            )
            return TrustCandidate(mode="timeout", answer="Time expired before the system could draft a safe answer.", provider="none", model="none", reason="deadline_expired_before_draft")

        if provider is None:
            if not evidence:
                candidate = TrustCandidate(mode="abstain", answer="I cannot verify the answer because no live evidence was retrieved.", reason="no_live_evidence")
            else:
                best = evidence[0]
                candidate = TrustCandidate(mode="answer", answer=best.text, claims=(TrustClaim(text=best.text, evidence_ids=(best.evidence_id,)),), reason="deterministic_evidence_answer")
            self.record(
                stage="answer_drafter",
                status=candidate.mode,
                contract="deterministic drafter must cite evidence or abstain",
                detail=candidate.answer[:1000],
                depends_on=[eid for claim in candidate.claims for eid in claim.evidence_ids],
            )
            return candidate

        provider_name = str(getattr(provider, "name", provider.__class__.__name__))
        model_name = str(getattr(provider, "model", ""))
        model_messages = [
            ChatMessage(role="system", content=ai_control_prompt_text("rag_trust_contract.system", TRUST_CONTRACT_SYSTEM_PROMPT)),
            ChatMessage(role="system", content=_format_evidence_for_model(evidence)),
            *list(messages or []),
            ChatMessage(role="user", content=prompt),
        ]
        self.record(
            stage="answer_drafter",
            status="model_call",
            contract="model must return JSON claims tied to supplied evidence IDs",
            detail=f"calling {provider_name}/{model_name}",
            depends_on=[item.evidence_id for item in evidence],
        )
        try:
            response = provider.chat(model_messages)
        except Exception as exc:
            self.failures.append(f"provider call failed: {exc}")
            self.record(stage="answer_drafter", status="failed", contract="provider call must return a candidate or fail closed", detail=repr(exc))
            return TrustCandidate(mode="reject", answer="Rejected: provider call failed.", provider=provider_name, model=model_name, reason="provider_exception")

        raw = str(getattr(response, "content", "") or "")
        candidate = _candidate_from_provider_text(raw, provider=str(getattr(response, "provider", provider_name)), model=str(getattr(response, "model", model_name)))
        self.record(
            stage="answer_drafter",
            status="candidate" if candidate.mode in {"answer", "abstain"} else "invalid_candidate",
            contract="model output must parse as trust-contract candidate JSON",
            detail=raw[:1000],
            depends_on=[eid for claim in candidate.claims for eid in claim.evidence_ids],
        )
        return candidate

    def verify(self, candidate: TrustCandidate, evidence: Sequence[TrustEvidence]) -> TrustCandidate:
        if self.expired():
            self.failures.append("deadline expired before verified answer")
            self.record(stage="verifier", status="timeout", contract="must reject answers that finish after deadline", detail="deadline expired before verification")
            return TrustCandidate(mode="timeout", answer="Time expired before the system could verify a safe answer.", provider=candidate.provider, model=candidate.model, reason="deadline_expired")

        if candidate.mode == "abstain":
            self.record(stage="verifier", status="abstain", contract="abstention is valid when evidence is missing or insufficient", detail=candidate.answer[:1000])
            return candidate

        if candidate.mode != "answer":
            self.failures.append(f"candidate mode is not answer or abstain: {candidate.mode}")
            self.record(stage="verifier", status="rejected", contract="must reject unknown or invalid candidate modes", detail=candidate.answer[:1000])
            return TrustCandidate(mode="reject", answer="Rejected: candidate mode is not contract-valid.", provider=candidate.provider, model=candidate.model, reason="invalid_candidate_mode")

        if not candidate.claims:
            self.failures.append("answer candidate has no claims")
        evidence_by_id = {item.evidence_id: item for item in evidence}
        for claim in candidate.claims:
            if not claim.evidence_ids:
                self.failures.append(f"claim has no evidence IDs: {claim.text}")
                continue
            support_chunks: list[str] = []
            for evidence_id in claim.evidence_ids:
                item = evidence_by_id.get(evidence_id)
                if item is None or not item.is_live_at(self.elapsed_ms()):
                    self.failures.append(f"claim cites missing/stale evidence {evidence_id}: {claim.text}")
                    continue
                support_chunks.append(item.text)
            if not support_chunks:
                continue
            missing = sorted(_tokens(claim.text) - _tokens(" ".join(support_chunks)))
            if missing:
                self.failures.append(f"claim is not anchored to cited evidence: {claim.text}; missing={missing[:8]}")

        accepted = not self.failures
        self.record(
            stage="verifier",
            status="accepted" if accepted else "rejected",
            contract="must reject unsupported, stale, uncited, late, or unanchored claims",
            detail="all claims anchored" if accepted else "; ".join(self.failures)[:1000],
            depends_on=[eid for claim in candidate.claims for eid in claim.evidence_ids],
        )
        if accepted:
            return candidate
        return TrustCandidate(mode="reject", answer="Rejected: contract verification failed.", provider=candidate.provider, model=candidate.model, raw_response=candidate.raw_response, reason="verification_failed")


def _tokens(text: str) -> set[str]:
    normalized: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./:-]+", str(text or "")):
        clean = token.strip(".,;:!?()[]{}<>\\\"'")
        lowered = clean.lower()
        if len(lowered) > 1 and lowered not in STOPWORDS:
            normalized.add(lowered)
    return normalized


def _coerce_message(item: Any) -> ChatMessage:
    if isinstance(item, ChatMessage):
        return item
    data = item if isinstance(item, dict) else {}
    role = str(data.get("role") or "user").strip().lower()
    if role not in {"system", "user", "assistant"}:
        role = "user"
    return ChatMessage(role=role, content=str(data.get("content") or ""))


def _coerce_evidence(item: Any, index: int) -> TrustEvidence:
    if isinstance(item, TrustEvidence):
        return item
    data = item if isinstance(item, dict) else {}
    evidence_id = str(data.get("evidence_id") or data.get("id") or f"evidence_{index + 1}").strip()
    return TrustEvidence(
        evidence_id=evidence_id or f"evidence_{index + 1}",
        source=str(data.get("source") or data.get("path") or "provided"),
        text=str(data.get("text") or data.get("content") or ""),
        produced_at_ms=int(data.get("produced_at_ms") or 0),
        expires_at_ms=int(data.get("expires_at_ms") or 60_000),
        trust=str(data.get("trust") or "local_verified"),
    )


def coerce_messages(messages: Sequence[Any] | None) -> list[ChatMessage]:
    return [_coerce_message(item) for item in list(messages or [])]


def coerce_evidence(evidence: Sequence[Any] | None) -> list[TrustEvidence]:
    items: list[TrustEvidence] = []
    for index, item in enumerate(list(evidence or [])):
        if isinstance(item, TrustEvidence):
            text = item.text
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("content") or "")
        else:
            text = str(getattr(item, "text", "") or "")
        if text.strip():
            items.append(_coerce_evidence(item, index))
    return items


def _format_evidence_for_model(evidence: Sequence[TrustEvidence]) -> str:
    if not evidence:
        return "EVIDENCE: none"
    rows = ["EVIDENCE:"]
    for item in evidence:
        rows.append(json.dumps({"evidence_id": item.evidence_id, "source": item.source, "text": item.text, "trust": item.trust}, ensure_ascii=False))
    return "\n".join(rows)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _candidate_from_provider_text(text: str, *, provider: str, model: str) -> TrustCandidate:
    data = _extract_json_object(text)
    if data is None:
        return TrustCandidate(mode="reject", answer="Rejected: provider did not return trust-contract JSON.", provider=provider, model=model, raw_response=text, reason="invalid_json")
    mode = str(data.get("mode") or "").strip().lower()
    answer = str(data.get("answer") or data.get("text") or "").strip()
    claims: list[TrustClaim] = []
    raw_claims = data.get("claims") if isinstance(data.get("claims"), list) else []
    for item in raw_claims:
        if not isinstance(item, dict):
            continue
        evidence_ids = item.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            evidence_ids = [item.get("evidence_id")] if item.get("evidence_id") else []
        claims.append(TrustClaim(text=str(item.get("text") or "").strip(), evidence_ids=tuple(str(eid).strip() for eid in evidence_ids if str(eid).strip())))
    if mode not in {"answer", "abstain"}:
        mode = "reject"
    if not answer:
        answer = "Rejected: provider returned an empty answer." if mode != "abstain" else "I cannot verify the answer from the available evidence."
    return TrustCandidate(mode=mode, answer=answer, claims=tuple(claims), provider=provider, model=model, raw_response=text)


def run_trust_contract_chat_request(*, prompt: str = "", messages: Sequence[Any] | None = None, evidence: Sequence[Any] | None = None, provider: Any | None = None, deadline_ms: int = 30_000, run_id: str = "", emit: FrameEmitter | None = None) -> TrustContractChatResult:
    chat_messages = coerce_messages(messages)
    trust_evidence = coerce_evidence(evidence)
    runner = TrustContractChatRunner(run_id=run_id, deadline_ms=deadline_ms, emit=emit)
    runner.frame("control", event="started", status="running", mode="rag_trust_contract_chat")
    normalized = runner.normalize_prompt(prompt, chat_messages)
    live_evidence = runner.retrieve(normalized, trust_evidence) if normalized else []
    candidate = runner.draft(prompt=normalized, messages=chat_messages, evidence=live_evidence, provider=provider)
    final = runner.verify(candidate, live_evidence)
    ok = final.mode in {"answer", "abstain"}
    status = "completed" if final.mode == "answer" else "abstained" if final.mode == "abstain" else final.mode
    result = TrustContractChatResult(
        run_id=runner.run_id,
        ok=ok,
        status=status,
        final_mode=final.mode,
        answer=final.answer,
        provider=final.provider,
        model=final.model,
        elapsed_ms=runner.elapsed_ms(),
        deadline_ms=runner.deadline_ms,
        evidence=live_evidence,
        events=list(runner.events),
        failures=list(runner.failures),
        candidate=candidate,
    )
    if ok:
        runner.frame("content", channel="chat", event="final_answer", delta=final.answer, text=final.answer)
    runner.frame("result", status=status, ok=ok, payload=result.as_dict())
    return result


def chat_response_from_trust_result(result: TrustContractChatResult) -> ChatResponse:
    return ChatResponse(
        content=result.answer,
        provider=result.provider,
        model=result.model,
        metadata={
            "mode": "rag_trust_contract_chat",
            "run_id": result.run_id,
            "ok": result.ok,
            "status": result.status,
            "final_mode": result.final_mode,
            "elapsed_ms": result.elapsed_ms,
            "deadline_ms": result.deadline_ms,
            "failures": result.failures,
            "events": [event.as_dict() for event in result.events],
            "evidence": [item.as_dict() for item in result.evidence],
        },
    )


def run_stdio(stdin: Any = None, stdout: Any = None, *, provider: Any | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    exit_code = 0

    def emit(frame: dict[str, Any]) -> None:
        stdout.write(json.dumps(frame, ensure_ascii=False, default=str) + "\n")
        stdout.flush()

    for raw_line in stdin:
        line = str(raw_line).strip()
        if not line:
            continue
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError("stdio request must be a JSON object")
            mode = str(command.get("mode") or "rag_trust_contract_chat")
            if mode != "rag_trust_contract_chat":
                raise ValueError(f"unsupported mode: {mode}")
            result = run_trust_contract_chat_request(
                prompt=str(command.get("prompt") or command.get("source") or ""),
                messages=command.get("messages") if isinstance(command.get("messages"), list) else [],
                evidence=command.get("evidence") if isinstance(command.get("evidence"), list) else [],
                provider=provider,
                deadline_ms=int(command.get("deadline_ms") or 30_000),
                run_id=str(command.get("run_id") or ""),
                emit=emit,
            )
            if not result.ok:
                exit_code = 1
        except Exception as exc:
            exit_code = 1
            emit({"type": "result", "channel": "oob", "ok": False, "status": "failed", "error": str(exc)})
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trust-contract RAG chat console.")
    parser.add_argument("--stdio", action="store_true", help="Read one JSON request per line and emit JSONL pipe frames.")
    parser.add_argument("--prompt", default="", help="Prompt for a single deterministic request.")
    parser.add_argument("--evidence-json", default="[]", help="JSON list of evidence objects for --prompt mode.")
    parser.add_argument("--deadline-ms", type=int, default=30_000)
    args = parser.parse_args(argv)
    if args.stdio:
        return run_stdio()
    evidence = json.loads(args.evidence_json or "[]")
    frames: list[dict[str, Any]] = []
    result = run_trust_contract_chat_request(prompt=args.prompt, evidence=evidence if isinstance(evidence, list) else [], deadline_ms=args.deadline_ms, emit=frames.append)
    for frame in frames:
        print(json.dumps(frame, ensure_ascii=False, default=str))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
