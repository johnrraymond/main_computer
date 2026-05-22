from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class RagUploadContext:
    id: str
    filename: str = ""
    size: int = 0
    mime_type: str = ""
    container_path: str = ""
    host_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagCandidate:
    path: str
    score: float
    reason: str
    matches: list[int] = field(default_factory=list)
    size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagChunk:
    path: str
    start_line: int
    end_line: int
    chars: int
    score: float
    reason: str
    content: str
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagRetrievalResult:
    queries: list[str]
    scanned_files: int
    candidates: list[RagCandidate]
    chunks: list[RagChunk]
    context_budget_chars: int
    used_chars: int
    truncated_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": list(self.queries),
            "scanned_files": self.scanned_files,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "chunks": [chunk.as_dict() for chunk in self.chunks],
            "context_budget_chars": self.context_budget_chars,
            "used_chars": self.used_chars,
            "truncated_files": list(self.truncated_files),
        }


@dataclass(frozen=True)
class ThinkingStepRecord:
    index: int
    kind: str
    status: str
    started_at: str
    completed_at: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagHarnessResult:
    ok: bool
    run_id: str
    prompt: str
    repo_dir: str
    output_dir: str
    no_model: bool
    status: str
    task_decomposition: dict[str, Any]
    inventory: dict[str, Any]
    retrieval: RagRetrievalResult
    context_brief: dict[str, Any]
    final_plan: dict[str, Any]
    steps: list[ThinkingStepRecord] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "run_id": self.run_id,
            "prompt": self.prompt,
            "repo_dir": self.repo_dir,
            "output_dir": self.output_dir,
            "no_model": self.no_model,
            "status": self.status,
            "task_decomposition": self.task_decomposition,
            "inventory": self.inventory,
            "retrieval": self.retrieval.as_dict(),
            "context_brief": self.context_brief,
            "final_plan": self.final_plan,
            "steps": [step.as_dict() for step in self.steps],
            "error": self.error,
        }
