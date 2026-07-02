from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


DEFAULT_AGENT_RUN_ROOT = Path("runtime") / "agent_runs"
DEFAULT_MISSION = "codebase-digestion"
DEFAULT_FOCUS = "worker-registration"
DEFAULT_RING = "3"
DEFAULT_MAX_CREDITS = "2"
DEFAULT_HUB_URL = "http://127.0.0.1:8871"
DEFAULT_SESSION_TIMEOUT_SECONDS = 420.0
DEFAULT_ACCEPT_TIMEOUT_SECONDS = 10.0
DEFAULT_WORKER_LOCAL_AI_TIMEOUT_SECONDS = 300.0
DEFAULT_WORKER_TARGET_TOKENS = 192
AGENT_SHAPE_COMPLETION_SENTINEL = "AGENT_SHAPE_DIGEST_DONE"
AGENT_SHAPE_EARLY_RESULT_REQUIRED_HEADINGS: tuple[str, ...] = (
    "# Codebase Digest",
    "## Summary",
    "## Relevant files",
    "## State machine",
    "## Risks",
    "## Verification steps",
)


try:  # pragma: no cover - optional dependency probe only.
    import pydantic_ai as _pydantic_ai  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency probe only.
    _pydantic_ai = None

try:  # pragma: no cover - optional dependency probe only.
    import pydantic as _pydantic  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency probe only.
    _pydantic = None

try:  # pragma: no cover - optional dependency probe only.
    import temporalio as _temporalio  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency probe only.
    _temporalio = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(payload) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def clean_run_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-_.")
    return text[:96] or datetime.now(timezone.utc).strftime("agent-shape-%Y%m%d-%H%M%S")


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("agent-shape-%Y%m%d-%H%M%S")


def normalize_ring(value: str | int | None) -> str:
    text = str(value if value is not None else DEFAULT_RING).strip().lower()
    if text.startswith("ring-"):
        suffix = text.split("-", 1)[1]
    else:
        suffix = text
    if suffix not in {"0", "1", "2", "3"}:
        raise ValueError(f"ring must be one of 0, 1, 2, 3; got {value!r}")
    return f"ring-{suffix}"


def ring_number(value: str) -> str:
    return normalize_ring(value).split("-", 1)[1]


def framework_capabilities() -> dict[str, Any]:
    return {
        "temporalio_available": _temporalio is not None,
        "pydantic_available": _pydantic is not None,
        "pydantic_ai_available": _pydantic_ai is not None,
        "workflow_shape": "temporal-compatible",
        "schema_shape": "pydantic-compatible",
        "transport": "main-computer-hub-live-session",
    }


def _short_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 12:
        return "<present>"
    return text[:8] + "…" + text[-6:]


def _with_sse_query(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["format"] = ["sse"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _positive_float(value: Any, *, default: float, minimum: float = 1.0, maximum: float = 3600.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _positive_int(value: Any, *, default: int, minimum: int = 1, maximum: int = 128_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    return max(minimum, min(parsed, maximum))


SUCCESS_TERMINAL_STATUSES = {"succeeded", "completed", "complete", "success"}
FAILED_TERMINAL_STATUSES = {"failed", "cancelled", "canceled", "error", "errored", "timeout", "timed_out"}


def hub_terminal_status(payload: dict[str, Any]) -> str:
    """Return the most authoritative terminal status visible in a Hub payload."""

    if not isinstance(payload, dict):
        return ""

    candidates: list[Any] = [
        payload.get("status"),
        payload.get("state"),
        payload.get("lifecycle_status"),
    ]
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    candidates.extend(
        [
            request.get("status"),
            request.get("state"),
            request.get("lifecycle_status"),
        ]
    )
    for value in candidates:
        status = str(value or "").strip().lower()
        if status:
            return status
    return ""


def hub_payload_failed(payload: dict[str, Any]) -> bool:
    return hub_terminal_status(payload) in FAILED_TERMINAL_STATUSES


def hub_payload_succeeded(payload: dict[str, Any]) -> bool:
    return hub_terminal_status(payload) in SUCCESS_TERMINAL_STATUSES


def _nested_payload_value(payload: dict[str, Any], path: Sequence[str]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _payload_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def hub_payload_failure_reason(payload: dict[str, Any]) -> str:
    """Return a human-facing Hub/worker failure reason when one is present."""

    if not isinstance(payload, dict):
        return ""

    paths: tuple[tuple[str, ...], ...] = (
        ("error",),
        ("message",),
        ("reason",),
        ("detail",),
        ("failure_reason",),
        ("request", "error"),
        ("request", "message"),
        ("request", "reason"),
        ("request", "detail"),
        ("request", "failure_reason"),
        ("request", "response", "error"),
        ("request", "response", "message"),
        ("request", "response", "reason"),
        ("request", "response", "detail"),
        ("request", "response", "failure_reason"),
        ("response", "error"),
        ("response", "message"),
        ("response", "reason"),
        ("response", "detail"),
        ("response", "failure_reason"),
        ("stream", "error"),
        ("stream", "message"),
    )
    for path in paths:
        text = _payload_string(_nested_payload_value(payload, path))
        if text:
            return text

    return ""


def hub_payload_worker_text(payload: dict[str, Any]) -> str:
    """Extract worker text from common Hub response shapes for diagnostics."""

    if not isinstance(payload, dict):
        return ""

    paths: tuple[tuple[str, ...], ...] = (
        ("request", "response", "content"),
        ("request", "response", "text"),
        ("request", "response", "output"),
        ("request", "response", "result"),
        ("response", "content"),
        ("response", "text"),
        ("response", "output"),
        ("response", "result"),
        ("content",),
        ("content_so_far",),
        ("delta",),
        ("text",),
        ("output",),
        ("result",),
    )
    for path in paths:
        text = _payload_string(_nested_payload_value(payload, path))
        if text:
            return text

    stream = payload.get("stream") if isinstance(payload.get("stream"), dict) else {}
    events = stream.get("events") if isinstance(stream.get("events"), list) else []
    chunks: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        text = _payload_string(event.get("delta") or event.get("content") or event.get("content_so_far"))
        if text:
            chunks.append(text)
    return "".join(chunks).strip()


def worker_timeout_refresh_hint(reason: str, *, requested_timeout_seconds: float) -> str:
    """Describe a likely stale live-worker runtime when its timeout ignores the requested limit."""

    text = str(reason or "")
    match = re.search(r"timed out after\s+([0-9]+(?:\.[0-9]+)?)s", text, flags=re.IGNORECASE)
    if not match:
        return ""
    try:
        observed = float(match.group(1))
    except ValueError:
        return ""
    if requested_timeout_seconds > observed + 1.0:
        return (
            f"Live worker used a {observed:.1f}s local-AI timeout even though this smoke requested "
            f"{requested_timeout_seconds:.1f}s. Restart the local Hub/worker runtime so the updated "
            "offer-timeout contract is loaded before rerunning."
        )
    return ""


@dataclass(frozen=True)
class AgentRunSpec:
    mission: str = DEFAULT_MISSION
    focus: str = DEFAULT_FOCUS
    ring: str = DEFAULT_RING
    max_credits: str = DEFAULT_MAX_CREDITS
    hub_url: str = DEFAULT_HUB_URL
    run_id: str = ""
    repo_root: str = "."
    pause_after_current_job: bool = True
    user_instructions: tuple[str, ...] = ()
    session_timeout: float = DEFAULT_SESSION_TIMEOUT_SECONDS
    accept_timeout: float = DEFAULT_ACCEPT_TIMEOUT_SECONDS
    worker_local_ai_timeout_seconds: float = DEFAULT_WORKER_LOCAL_AI_TIMEOUT_SECONDS
    worker_target_tokens: int = DEFAULT_WORKER_TARGET_TOKENS

    def __post_init__(self) -> None:
        mission = str(self.mission or "").strip()
        focus = str(self.focus or "").strip()
        hub_url = str(self.hub_url or DEFAULT_HUB_URL).strip().rstrip("/")
        if not mission:
            raise ValueError("mission is required")
        if not focus:
            raise ValueError("focus is required")
        if not hub_url:
            raise ValueError("hub_url is required")
        object.__setattr__(self, "mission", mission)
        object.__setattr__(self, "focus", focus)
        object.__setattr__(self, "ring", normalize_ring(self.ring))
        object.__setattr__(self, "max_credits", str(self.max_credits or DEFAULT_MAX_CREDITS).strip())
        object.__setattr__(self, "hub_url", hub_url)
        object.__setattr__(self, "session_timeout", _positive_float(self.session_timeout, default=DEFAULT_SESSION_TIMEOUT_SECONDS))
        object.__setattr__(self, "accept_timeout", _positive_float(self.accept_timeout, default=DEFAULT_ACCEPT_TIMEOUT_SECONDS))
        object.__setattr__(
            self,
            "worker_local_ai_timeout_seconds",
            _positive_float(
                self.worker_local_ai_timeout_seconds,
                default=DEFAULT_WORKER_LOCAL_AI_TIMEOUT_SECONDS,
            ),
        )
        object.__setattr__(
            self,
            "worker_target_tokens",
            _positive_int(self.worker_target_tokens, default=DEFAULT_WORKER_TARGET_TOKENS),
        )


@dataclass(frozen=True)
class DigestJobContract:
    job_id: str
    mission: str
    focus: str
    ring: str
    max_credits: str
    required_questions: tuple[str, ...] = (
        "Which files, routes, and state stores appear involved?",
        "What is the setup or execution state machine?",
        "How should requester no-worker recovery work?",
        "How can the result be verified?",
        "What follow-up task should run next?",
    )
    required_files_hint: tuple[str, ...] = ()
    output_schema: str = "CodebaseDigestResultMarkdown"

    def to_prompt(self, extra_instructions: Sequence[str] = ()) -> str:
        lines = [
            "You are completing a paid codebase digestion task for Main Computer.",
            "This is part of an agent smoke test that proves paid worker dispatch, realtime streaming, artifact writing, evaluation, and pause-after-current-job control.",
            "",
            f"Mission: {self.mission}",
            f"Focus: {self.focus}",
            f"Ring: {self.ring}",
            f"Max credits: {self.max_credits}",
            "",
            "Required questions:",
        ]
        lines.extend(f"- {question}" for question in self.required_questions)
        if self.required_files_hint:
            lines.append("")
            lines.append("File hints to consider:")
            lines.extend(f"- {item}" for item in self.required_files_hint)
        if extra_instructions:
            lines.append("")
            lines.append("User steering instructions:")
            lines.extend(f"- {item}" for item in extra_instructions if str(item).strip())
        lines.extend(
            [
                "",
                "Return Markdown only.",
                "Use these exact section headings, in this exact order:",
                "# Codebase Digest",
                "## Summary",
                "## Relevant files",
                "## State machine",
                "## Risks",
                "## Verification steps",
                "## Follow-up tasks",
                "",
                "Use exactly one short bullet under each ## heading.",
                "Hard limit: 120 words total.",
                "If time is tight, complete through ## Verification steps first; follow-up tasks can be terse.",
                "Mention exact file paths when you can, but prefer completion over detail.",
                f"End the response with this exact final line when possible: {AGENT_SHAPE_COMPLETION_SENTINEL}",
                "Do not add narrative, analysis, or extra sections after that final line.",
                "Prioritize a complete bounded digest over exhaustive coverage.",
            ]
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class CodebaseDigestResult:
    summary: str
    relevant_files: tuple[str, ...]
    state_machine: tuple[str, ...]
    risks: tuple[str, ...]
    verification_steps: tuple[str, ...]
    follow_up_tasks: tuple[str, ...]
    artifact_markdown: str
    raw_worker_text: str = ""

    def validate_contract(self) -> None:
        if not self.summary.strip():
            raise ValueError("summary is required")
        if not self.relevant_files:
            raise ValueError("at least one relevant file is required")
        if not self.state_machine:
            raise ValueError("state_machine is required")
        if not self.verification_steps:
            raise ValueError("verification_steps is required")
        if not self.follow_up_tasks:
            raise ValueError("follow_up_tasks is required")
        if "# Codebase Digest" not in self.artifact_markdown:
            raise ValueError("artifact_markdown must be a Codebase Digest document")


@dataclass(frozen=True)
class AgentEvaluation:
    accepted: bool
    score: float
    reasons: tuple[str, ...]
    pay_decision: Literal["pay", "reject", "needs_review"]

    def validate_contract(self) -> None:
        if self.score < 0.0 or self.score > 1.0:
            raise ValueError("score must be between 0 and 1")
        if not self.reasons:
            raise ValueError("evaluation reasons are required")
        if self.accepted and self.pay_decision != "pay":
            raise ValueError("accepted results must have pay_decision='pay'")


@dataclass(frozen=True)
class NextTaskProposal:
    task_id: str
    title: str
    focus: str
    reason: str
    max_credits: str = DEFAULT_MAX_CREDITS


@dataclass(frozen=True)
class AgentControlCommand:
    type: Literal[
        "pause_after_current_job",
        "resume",
        "cancel",
        "add_instruction",
        "set_budget",
        "approve_next_task",
    ]
    text: str = ""
    max_credits: str = ""

    @staticmethod
    def from_mapping(payload: dict[str, Any]) -> "AgentControlCommand":
        command_type = str(payload.get("type") or "").strip()
        allowed = {
            "pause_after_current_job",
            "resume",
            "cancel",
            "add_instruction",
            "set_budget",
            "approve_next_task",
        }
        if command_type not in allowed:
            raise ValueError(f"unsupported control command type: {command_type!r}")
        return AgentControlCommand(
            type=command_type,  # type: ignore[arg-type]
            text=str(payload.get("text") or "").strip(),
            max_credits=str(payload.get("max_credits") or "").strip(),
        )


@dataclass(frozen=True)
class AgentSmokeArtifacts:
    run_dir: Path
    run_json: Path
    job_json: Path
    stream_jsonl: Path
    result_json: Path
    artifact_md: Path
    evaluation_json: Path
    next_tasks_json: Path
    commands_jsonl: Path

    @classmethod
    def for_run(cls, run_dir: Path) -> "AgentSmokeArtifacts":
        return cls(
            run_dir=run_dir,
            run_json=run_dir / "run.json",
            job_json=run_dir / "job.json",
            stream_jsonl=run_dir / "stream.jsonl",
            result_json=run_dir / "result.json",
            artifact_md=run_dir / "artifact.md",
            evaluation_json=run_dir / "evaluation.json",
            next_tasks_json=run_dir / "next_tasks.json",
            commands_jsonl=run_dir / "commands.jsonl",
        )


def dataclass_to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: dataclass_to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [dataclass_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [dataclass_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): dataclass_to_jsonable(item) for key, item in value.items()}
    return value


def make_digest_contract(spec: AgentRunSpec) -> DigestJobContract:
    file_hints: tuple[str, ...]
    if spec.focus == "worker-registration":
        file_hints = (
            "micro_agent_canvas.py",
            "main_computer/viewport_routes_energy.py",
            "main_computer/web/applications/scripts/worker.js",
            "tests/test_micro_agent_canvas.py",
            "tests/test_worker_idle_availability.py",
        )
    else:
        file_hints = (
            "main_computer/",
            "tests/",
            "runtime/agent_runs/",
        )
    return DigestJobContract(
        job_id="job-001",
        mission=spec.mission,
        focus=spec.focus,
        ring=spec.ring,
        max_credits=spec.max_credits,
        required_files_hint=file_hints,
    )


_SECTION_NAMES = {
    "summary": "summary",
    "relevant files": "relevant_files",
    "state machine": "state_machine",
    "risks": "risks",
    "verification steps": "verification_steps",
    "follow-up tasks": "follow_up_tasks",
    "follow up tasks": "follow_up_tasks",
}


def _section_lines(markdown: str) -> dict[str, list[str]]:
    current = ""
    sections: dict[str, list[str]] = {}
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            key = _SECTION_NAMES.get(line[3:].strip().lower())
            current = key or ""
            if current:
                sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(raw_line.rstrip())
    return sections


def _bullets(lines: Sequence[str]) -> tuple[str, ...]:
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            text = stripped[1:].strip()
            if text:
                items.append(text)
        elif re.match(r"^\d+[.)]\s+", stripped):
            text = re.sub(r"^\d+[.)]\s+", "", stripped).strip()
            if text:
                items.append(text)
    return tuple(items)


def _paragraph(lines: Sequence[str]) -> str:
    return " ".join(line.strip() for line in lines if line.strip()).strip()


def _fallback_artifact(text: str, contract: DigestJobContract) -> str:
    hints = "\n".join(f"- `{item}`" for item in contract.required_files_hint)
    raw = text.strip() or "(worker returned no visible text)"
    return f"""# Codebase Digest

## Summary
Worker returned a live Hub result for `{contract.focus}`. The raw output is preserved below.

## Relevant files
{hints}

## State machine
- requester authority checked
- worker availability checked
- paid Hub job dispatched
- live stream observed
- terminal result received
- artifact written
- result evaluated
- next task proposed

## Risks
- worker output did not fully use the requested digest section headings
- evaluator wrapped raw output into the required artifact shape

## Verification steps
- inspect `stream.jsonl` and confirm stream events precede terminal result
- inspect the final Hub payload in `result.json`
- rerun the smoke after changing worker availability or wallet state

## Follow-up tasks
- Tighten the worker prompt to return the exact digest sections.
- Add a UI view for this agent run artifact.
- Wire the same run contract into a Temporal workflow.

## Raw worker output
{raw}
"""


def digest_result_from_worker_text(text: str, contract: DigestJobContract) -> CodebaseDigestResult:
    raw = str(text or "").strip()
    artifact = raw if "# Codebase Digest" in raw else _fallback_artifact(raw, contract)
    sections = _section_lines(artifact)

    summary = _paragraph(sections.get("summary", [])) or f"Digested {contract.focus} through a live Hub worker request."
    relevant_files = _bullets(sections.get("relevant_files", [])) or tuple(f"`{item}`" for item in contract.required_files_hint[:3])
    state_machine = _bullets(sections.get("state_machine", [])) or (
        "requester_authority_checked",
        "worker_availability_checked",
        "hub_job_dispatched",
        "stream_observed",
        "terminal_result_received",
    )
    risks = _bullets(sections.get("risks", [])) or ("worker output may need human review",)
    verification_steps = _bullets(sections.get("verification_steps", [])) or (
        "inspect stream.jsonl for deltas before terminal_result",
        "inspect result.json for the final Hub payload",
    )
    follow_up_tasks = _bullets(sections.get("follow_up_tasks", [])) or (
        "Run the next codebase digestion focus area.",
        "Add Agent page controls for this run shape.",
    )

    result = CodebaseDigestResult(
        summary=summary,
        relevant_files=tuple(relevant_files),
        state_machine=tuple(state_machine),
        risks=tuple(risks),
        verification_steps=tuple(verification_steps),
        follow_up_tasks=tuple(follow_up_tasks),
        artifact_markdown=artifact,
        raw_worker_text=raw,
    )
    result.validate_contract()
    return result


def evaluate_digest_result(result: CodebaseDigestResult) -> AgentEvaluation:
    reasons: list[str] = []
    score = 0.0
    try:
        result.validate_contract()
    except ValueError as exc:
        return AgentEvaluation(
            accepted=False,
            score=0.0,
            reasons=(f"contract validation failed: {exc}",),
            pay_decision="reject",
        )

    score += 0.2
    reasons.append("result matched the CodebaseDigestResult artifact contract")
    if result.relevant_files:
        score += 0.2
        reasons.append("result listed relevant files")
    if result.state_machine:
        score += 0.2
        reasons.append("result described an agent state machine")
    if result.verification_steps:
        score += 0.2
        reasons.append("result included verification steps")
    if result.follow_up_tasks:
        score += 0.2
        reasons.append("result proposed follow-up tasks")

    score = min(1.0, score)
    accepted = score >= 0.8
    return AgentEvaluation(
        accepted=accepted,
        score=score,
        reasons=tuple(reasons),
        pay_decision="pay" if accepted else "needs_review",
    )


def make_next_task_proposals(result: CodebaseDigestResult, *, max_credits: str) -> tuple[NextTaskProposal, ...]:
    proposals: list[NextTaskProposal] = []
    for index, title in enumerate(result.follow_up_tasks[:3], start=1):
        proposals.append(
            NextTaskProposal(
                task_id=f"task-{index:03d}",
                title=title,
                focus=title.lower().replace(" ", "-").strip(".`"),
                reason="proposed by the accepted codebase digestion result",
                max_credits=max_credits,
            )
        )
    return tuple(proposals)


class AgentShapeSmokeRunner:
    def __init__(self, *, spec: AgentRunSpec, run_root: Path = DEFAULT_AGENT_RUN_ROOT, transport: Any | None = None) -> None:
        run_id = clean_run_id(spec.run_id or make_run_id())
        self.spec = AgentRunSpec(
            mission=spec.mission,
            focus=spec.focus,
            ring=spec.ring,
            max_credits=spec.max_credits,
            hub_url=spec.hub_url,
            run_id=run_id,
            repo_root=spec.repo_root,
            pause_after_current_job=spec.pause_after_current_job,
            user_instructions=spec.user_instructions,
            session_timeout=spec.session_timeout,
            accept_timeout=spec.accept_timeout,
            worker_local_ai_timeout_seconds=spec.worker_local_ai_timeout_seconds,
            worker_target_tokens=spec.worker_target_tokens,
        )
        root = run_root if run_root.is_absolute() else Path(self.spec.repo_root) / run_root
        self.artifacts = AgentSmokeArtifacts.for_run(root / run_id)
        self.extra_instructions: list[str] = [item for item in self.spec.user_instructions if str(item).strip()]
        self.cancel_requested = False
        self.pause_after_current_job = bool(self.spec.pause_after_current_job)
        self.transport = transport or self._load_micro_agent_transport()
        self._seen_hub_stream_events: set[str] = set()
        self._last_session_status = ""

    def _load_micro_agent_transport(self) -> Any:
        import importlib

        return importlib.import_module("micro_agent_canvas")

    def emit(self, event: str, **fields: Any) -> None:
        record = {
            "timestamp": utc_now(),
            "event": event,
            **fields,
        }
        append_jsonl(self.artifacts.stream_jsonl, record)
        print(f"[{event}] {fields.get('message') or fields.get('status') or ''}".rstrip())

    def read_control_commands(self) -> tuple[AgentControlCommand, ...]:
        commands: list[AgentControlCommand] = []
        for payload in read_jsonl(self.artifacts.commands_jsonl):
            try:
                commands.append(AgentControlCommand.from_mapping(payload))
            except ValueError as exc:
                self.emit("control_command_ignored", error=str(exc), payload=payload)
        return tuple(commands)

    def apply_control_commands(self) -> None:
        for command in self.read_control_commands():
            if command.type == "add_instruction" and command.text:
                if command.text not in self.extra_instructions:
                    self.extra_instructions.append(command.text)
                    self.emit("control_command_applied", command=dataclass_to_jsonable(command))
            elif command.type == "pause_after_current_job":
                self.pause_after_current_job = True
                self.emit("control_command_applied", command=dataclass_to_jsonable(command))
            elif command.type == "resume":
                self.pause_after_current_job = False
                self.emit("control_command_applied", command=dataclass_to_jsonable(command))
            elif command.type == "cancel":
                self.cancel_requested = True
                self.emit("control_command_applied", command=dataclass_to_jsonable(command))
            elif command.type == "set_budget" and command.max_credits:
                self.emit("control_command_observed", command=dataclass_to_jsonable(command))
            elif command.type == "approve_next_task":
                self.emit("control_command_observed", command=dataclass_to_jsonable(command))

    def _micro_agent_args(self, *, prompt: str) -> Any:
        return SimpleNamespace(
            prompt=prompt,
            hub=self.spec.hub_url,
            app=os.environ.get("MAIN_COMPUTER_APP_URL", ""),
            ring=ring_number(self.spec.ring),
            capability="chat.completions",
            client_node_id="agent-shape-smoke",
            model="micro-agent-local",
            max_credits=self.spec.max_credits,
            accept_timeout=float(self.spec.accept_timeout),
            session_timeout=float(self.spec.session_timeout),
            private_key=os.environ.get("MAIN_COMPUTER_AGENT_SMOKE_PRIVATE_KEY", ""),
            private_key_file="",
            wallet="",
            msk_lifetime_minutes=10,
            no_auto_worker=False,
            auto_worker_timeout=10.0,
            auto_worker_seconds=3600,
            worker_model=os.environ.get("MAIN_COMPUTER_MICRO_AGENT_WORKER_MODEL", "gemma4:26b"),
            worker_endpoint=os.environ.get("MAIN_COMPUTER_MICRO_AGENT_WORKER_ENDPOINT", "http://127.0.0.1:8771"),
            worker_credits_per_token="0.001",
            worker_target_tokens=int(self.spec.worker_target_tokens),
            worker_local_ai_timeout_seconds=float(self.spec.worker_local_ai_timeout_seconds),
            work_timeout_seconds=float(self.spec.worker_local_ai_timeout_seconds),
            worker_availability_mode="ai_idle",
            json=False,
        )

    def _emit_session_status(self, status: Any) -> None:
        status_text = str(status or "").strip()
        if status_text and status_text != self._last_session_status:
            self._last_session_status = status_text
            self.emit("session_status", status=status_text)

    def _hub_stream_event_signature(self, event_payload: dict[str, Any]) -> str:
        # Prefer Hub-assigned sequence ids when they are present. Those are stable
        # across session snapshots and realtime SSE replays.
        for key in ("seq", "worker_seq"):
            value = event_payload.get(key)
            if value is not None and str(value) != "":
                return "|".join(
                    [
                        str(event_payload.get("request_id") or ""),
                        str(event_payload.get("session_id") or ""),
                        str(event_payload.get("run_id") or ""),
                        str(key),
                        str(value),
                    ]
                )

        # Some accepted/status SSE frames are replayed without a sequence id. Keep
        # enough fields to suppress pure replay spam while still allowing a later
        # running/failed/result status to be recorded.
        return json_dumps(
            {
                "type": event_payload.get("type"),
                "status": event_payload.get("status"),
                "created_at": event_payload.get("created_at") or event_payload.get("hub_received_at"),
                "delta": event_payload.get("delta"),
                "content": event_payload.get("content"),
                "content_so_far": event_payload.get("content_so_far"),
                "request_id": event_payload.get("request_id"),
                "session_id": event_payload.get("session_id"),
                "run_id": event_payload.get("run_id"),
            }
        )

    def _record_snapshot_stream_events(self, snapshot: dict[str, Any]) -> None:
        stream = snapshot.get("stream") if isinstance(snapshot.get("stream"), dict) else {}
        events = stream.get("events") if isinstance(stream.get("events"), list) else []
        for event in events:
            if isinstance(event, dict):
                self._record_hub_stream_event(event)

    def _follow_session_with_artifacts(self, continuation_url: str) -> dict[str, Any]:
        self.emit("session_stream_opened", url=continuation_url)
        status, snapshot = self.transport.http_json("GET", continuation_url, timeout=15.0)
        if status >= 400:
            self.emit("session_snapshot_failed", status=f"HTTP {status}", payload=snapshot)
            return snapshot

        final_payload = snapshot
        self._emit_session_status(snapshot.get("status"))
        self._record_snapshot_stream_events(snapshot)

        stream = snapshot.get("stream") if isinstance(snapshot.get("stream"), dict) else {}
        realtime = stream.get("realtime") if isinstance(stream.get("realtime"), dict) else {}
        sse_url = str(realtime.get("url") or "").strip() or _with_sse_query(continuation_url)

        deadline = time.monotonic() + max(1.0, float(self.spec.session_timeout))
        try:
            terminal = self._read_sse_stream(sse_url, deadline=deadline)
            if terminal:
                terminal_status = str(terminal.get("status") or terminal.get("type") or "terminal")
                if terminal_status == "non_terminal_replay":
                    self.emit(
                        "session_realtime_replay_detected",
                        status=terminal_status,
                        message="Realtime stream replayed duplicate non-terminal events; polling session snapshots.",
                    )
                else:
                    self.emit("session_terminal_event", status=terminal_status)
                    # Return the authoritative accepted-session record, not the
                    # individual SSE frame, because it contains payment/request data.
                    status, snapshot = self.transport.http_json("GET", continuation_url, timeout=15.0)
                    if status < 400:
                        final_payload = snapshot
                        self._emit_session_status(snapshot.get("status"))
                        self._record_snapshot_stream_events(snapshot)
                        snapshot_status = hub_terminal_status(snapshot)
                        if snapshot_status in SUCCESS_TERMINAL_STATUSES or snapshot_status in FAILED_TERMINAL_STATUSES:
                            return final_payload
                        self.emit(
                            "session_terminal_snapshot_pending",
                            status=snapshot_status or str(snapshot.get("status") or "unknown"),
                            message="Realtime terminal event arrived before the authoritative session snapshot became terminal; polling session snapshots.",
                        )
        except Exception as exc:
            self.emit("session_realtime_fallback", error=str(exc), message="SSE unavailable; polling session snapshot")

        while time.monotonic() < deadline:
            status, snapshot = self.transport.http_json("GET", continuation_url, timeout=15.0)
            final_payload = snapshot
            self._emit_session_status(snapshot.get("status"))
            self._record_snapshot_stream_events(snapshot)
            snapshot_status = hub_terminal_status(snapshot)
            if snapshot_status in SUCCESS_TERMINAL_STATUSES or snapshot_status in FAILED_TERMINAL_STATUSES:
                return final_payload
            time.sleep(1.0)

        self.emit(
            "session_timeout",
            status=str(final_payload.get("status") or "unknown"),
            message="Timed out waiting for Hub session to reach a terminal result.",
        )
        raise TimeoutError(f"Hub session did not reach a terminal result before timeout: {continuation_url}")

    def _read_sse_stream(self, url: str, *, deadline: float) -> dict[str, Any] | None:
        request = Request(url, headers={"Accept": "text/event-stream"}, method="GET")
        last_event = "message"
        data_lines: list[str] = []
        terminal_event: dict[str, Any] | None = None
        duplicate_replay_count = 0
        timeout = max(1.0, min(3600.0, deadline - time.monotonic()))
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied local Hub URL.
            while time.monotonic() < deadline:
                raw_line = response.readline()
                if raw_line == b"":
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        data_text = "\n".join(data_lines)
                        data_lines = []
                        try:
                            event_payload = json.loads(data_text)
                        except json.JSONDecodeError:
                            last_event = "message"
                            continue
                        if isinstance(event_payload, dict):
                            event_payload.setdefault("type", last_event)
                            recorded = self._record_hub_stream_event(event_payload)
                            duplicate_replay_count = 0 if recorded else duplicate_replay_count + 1
                            event_type = str(event_payload.get("type") or last_event)
                            status = str(event_payload.get("status") or "").strip().lower()
                            event_type_normalized = event_type.strip().lower()
                            if (
                                event_type_normalized == "result"
                                or event_type_normalized in FAILED_TERMINAL_STATUSES
                                or status in SUCCESS_TERMINAL_STATUSES
                                or status in FAILED_TERMINAL_STATUSES
                            ):
                                terminal_event = event_payload
                                break
                            if duplicate_replay_count >= 25:
                                terminal_event = {"type": "agent.sse_replay", "status": "non_terminal_replay"}
                                break
                    last_event = "message"
                    continue
                if line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    last_event = value or "message"
                elif field == "data":
                    data_lines.append(value)
        return terminal_event

    def _record_hub_stream_event(self, event_payload: dict[str, Any]) -> bool:
        signature = self._hub_stream_event_signature(event_payload)
        if signature in self._seen_hub_stream_events:
            return False
        self._seen_hub_stream_events.add(signature)

        event_type = str(event_payload.get("type") or "message")
        status = str(event_payload.get("status") or "").strip()
        delta = str(event_payload.get("delta") or "").strip()
        content = str(event_payload.get("content") or event_payload.get("content_so_far") or "").strip()
        fields: dict[str, Any] = {
            "hub_event_type": event_type,
            "hub_status": status,
            "hub_event": event_payload,
        }
        if delta:
            fields["delta"] = delta
            print(f"[stream] {delta}", end="", flush=True)
        elif status:
            fields["status"] = status
        elif content:
            fields["content"] = content
        self.emit("hub_stream_delta" if delta else "hub_stream_event", **fields)
        return True

    def _apply_agent_work_limits(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach bounded worker execution hints to the Hub work payload.

        The session timeout only controls how long the requester waits.  The live
        worker also needs provider-level output controls; otherwise a local model
        can keep streaming useful text until the worker executor timeout expires.
        Duplicate the small output/timeout contract across legacy and current
        payload shapes so Hub offer construction and the worker subprocess can
        both enforce it.
        """

        worker_timeout = _positive_float(
            self.spec.worker_local_ai_timeout_seconds,
            default=DEFAULT_WORKER_LOCAL_AI_TIMEOUT_SECONDS,
        )
        worker_target_tokens = _positive_int(self.spec.worker_target_tokens, default=DEFAULT_WORKER_TARGET_TOKENS)

        def bounded_tokens(value: Any) -> int:
            return min(_positive_int(value, default=worker_target_tokens), worker_target_tokens)

        for key in (
            "timeout_seconds",
            "worker_timeout_seconds",
            "work_timeout_seconds",
            "max_runtime_seconds",
            "local_ai_timeout_seconds",
            "worker_local_ai_timeout_seconds",
        ):
            payload[key] = worker_timeout
        payload["target_tokens"] = bounded_tokens(payload.get("target_tokens"))
        payload["max_output_tokens"] = bounded_tokens(payload.get("max_output_tokens"))
        payload["think"] = False
        payload["ollama_think"] = False
        payload["completion_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
        payload["early_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
        payload["stream_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
        payload["required_headings"] = list(AGENT_SHAPE_EARLY_RESULT_REQUIRED_HEADINGS)

        execution_limits = payload.setdefault("execution_limits", {})
        if isinstance(execution_limits, dict):
            execution_limits["timeout_seconds"] = worker_timeout
            execution_limits["worker_timeout_seconds"] = worker_timeout
            execution_limits["work_timeout_seconds"] = worker_timeout
            execution_limits["max_runtime_seconds"] = worker_timeout
            execution_limits["local_ai_timeout_seconds"] = worker_timeout
            execution_limits["worker_local_ai_timeout_seconds"] = worker_timeout
            execution_limits["target_tokens"] = payload["target_tokens"]
            execution_limits["max_output_tokens"] = payload["max_output_tokens"]
            execution_limits["think"] = False
            execution_limits["ollama_think"] = False
            execution_limits["completion_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            execution_limits["early_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            execution_limits["stream_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            execution_limits["required_headings"] = list(AGENT_SHAPE_EARLY_RESULT_REQUIRED_HEADINGS)

        for options_key in ("provider_options", "ollama_options"):
            options = payload.setdefault(options_key, {})
            if isinstance(options, dict):
                options["num_predict"] = payload["max_output_tokens"]

        metadata = payload.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["agent_shape_smoke"] = True
            metadata["worker_local_ai_timeout_seconds"] = worker_timeout
            metadata["worker_timeout_seconds"] = worker_timeout
            metadata["worker_target_tokens"] = worker_target_tokens
            metadata["target_tokens"] = payload["target_tokens"]
            metadata["max_output_tokens"] = payload["max_output_tokens"]
            metadata["think"] = False
            metadata["ollama_think"] = False
            metadata["completion_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            metadata["early_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            metadata["stream_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            metadata["required_headings"] = list(AGENT_SHAPE_EARLY_RESULT_REQUIRED_HEADINGS)
            for options_key in ("provider_options", "ollama_options"):
                options = metadata.setdefault(options_key, {})
                if isinstance(options, dict):
                    options["num_predict"] = payload["max_output_tokens"]

        input_payload = payload.get("input")
        if isinstance(input_payload, dict):
            input_payload["target_tokens"] = bounded_tokens(input_payload.get("target_tokens"))
            input_payload["max_output_tokens"] = bounded_tokens(input_payload.get("max_output_tokens"))
            input_payload["think"] = False
            input_payload["ollama_think"] = False
            input_payload["completion_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            input_payload["early_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            input_payload["stream_result_sentinel"] = AGENT_SHAPE_COMPLETION_SENTINEL
            input_payload["required_headings"] = list(AGENT_SHAPE_EARLY_RESULT_REQUIRED_HEADINGS)
            for options_key in ("provider_options", "ollama_options"):
                options = input_payload.setdefault(options_key, {})
                if isinstance(options, dict):
                    options["num_predict"] = input_payload["max_output_tokens"]

        return payload

    def _dispatch_paid_job(self, *, contract: DigestJobContract, prompt: str) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        args = self._micro_agent_args(prompt=prompt)
        hub_url = str(self.spec.hub_url).rstrip("/")
        hub_status = self.transport._load_hub_identity(hub_url)
        summary = self.transport.extract_hub_status_summary(hub_status)
        self.emit(
            "hub_identity_loaded",
            hub_id=summary.get("hub_id"),
            network=summary.get("network_key"),
            chain_id=summary.get("chain_id"),
            backend=summary.get("backend"),
        )

        authorization = self.transport.request_fresh_multisession_authorization(
            args=args,
            hub_url=hub_url,
            hub_status=hub_status,
            settings={},
        )
        self.emit(
            "requester_authority_checked",
            wallet=_short_secret(str(authorization.get("wallet_address") or "")),
            key=_short_secret(str(authorization.get("multisession_key_id") or authorization.get("key_id") or "")),
            message="requester wallet/MSK authority ready",
        )

        payload = self.transport.build_work_payload(args, authorization=authorization, hub_status=hub_status)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Hub work payload builder returned non-object payload: {payload!r}")
        payload = self._apply_agent_work_limits(payload)
        self.emit(
            "job_dispatched",
            ring=payload.get("ring"),
            capabilities=payload.get("capabilities"),
            max_price=payload.get("max_price"),
            message="submitting paid Hub work request",
        )
        status, submitted = self.transport.http_json("POST", f"{hub_url}/api/hub/v1/work/requests", payload, timeout=30.0)
        self.emit("hub_submit_result", status=f"HTTP {status}", payload=submitted)

        worker_prepare_error = ""
        if status == 409 and submitted.get("error") == "no_live_worker_available":
            self.emit("worker_availability_checked", status="no_live_worker_available", message="auto-preparing local dev worker")
            try:
                self.transport.ensure_local_worker_available(args=args, hub_url=hub_url, hub_status=hub_status)
                self.emit("worker_availability_checked", status="accepting", message="local dev worker setup attempted; retrying Hub request")
            except Exception as exc:
                worker_prepare_error = str(exc)
                self.emit(
                    "worker_auto_prepare_failed",
                    error=worker_prepare_error,
                    message="local worker auto-prepare failed; retrying Hub request once before failing",
                )
                time.sleep(1.0)
            status, submitted = self.transport.http_json("POST", f"{hub_url}/api/hub/v1/work/requests", payload, timeout=30.0)
            self.emit("hub_submit_retry_result", status=f"HTTP {status}", payload=submitted)
            if status == 409 and submitted.get("error") == "no_live_worker_available" and worker_prepare_error:
                raise RuntimeError(
                    "Hub still reports no_live_worker_available after local worker auto-prepare failed: "
                    f"{worker_prepare_error}"
                )
        else:
            self.emit("worker_availability_checked", status="existing_or_not_required", message="Hub accepted initial request or returned non-worker error")

        if status >= 400 or submitted.get("ok") is False:
            raise RuntimeError(f"Hub work request failed HTTP {status}: {submitted}")

        continuation_url = str(submitted.get("continuation_url") or "").strip()
        final_payload = submitted
        if continuation_url:
            final_payload = self._follow_session_with_artifacts(continuation_url)

        terminal_status = hub_terminal_status(final_payload) or hub_terminal_status(submitted)
        if terminal_status in FAILED_TERMINAL_STATUSES:
            failure_reason = hub_payload_failure_reason(final_payload) or hub_payload_failure_reason(submitted)
            partial_worker_text = hub_payload_worker_text(final_payload) or hub_payload_worker_text(submitted)
            reason_suffix = f": {failure_reason}" if failure_reason else ""
            timeout_refresh_hint = worker_timeout_refresh_hint(
                failure_reason,
                requested_timeout_seconds=float(self.spec.worker_local_ai_timeout_seconds),
            )
            message = (
                f"Hub session finished with terminal status {terminal_status}{reason_suffix}; "
                "not evaluating or accepting partial worker output."
            )
            if timeout_refresh_hint:
                message = f"{message} {timeout_refresh_hint}"
                self.emit(
                    "worker_runtime_refresh_required",
                    status=terminal_status,
                    message=timeout_refresh_hint,
                    requested_timeout_seconds=float(self.spec.worker_local_ai_timeout_seconds),
                )
            atomic_write_json(
                self.artifacts.result_json,
                {
                    "created_at": utc_now(),
                    "error": message,
                    "terminal_status": terminal_status,
                    "failure_reason": failure_reason,
                    "timeout_refresh_hint": timeout_refresh_hint,
                    "partial_worker_text": partial_worker_text,
                    "partial_worker_text_present": bool(partial_worker_text),
                    "submitted_payload": submitted,
                    "final_payload": final_payload,
                    "hub_status": hub_status,
                },
            )
            self.emit("terminal_result_failed", status=terminal_status, message=message, reason=failure_reason)
            raise RuntimeError(message)

        if continuation_url and terminal_status and terminal_status not in SUCCESS_TERMINAL_STATUSES:
            failure_reason = hub_payload_failure_reason(final_payload) or hub_payload_failure_reason(submitted)
            partial_worker_text = hub_payload_worker_text(final_payload) or hub_payload_worker_text(submitted)
            reason_suffix = f": {failure_reason}" if failure_reason else ""
            timeout_refresh_hint = worker_timeout_refresh_hint(
                failure_reason,
                requested_timeout_seconds=float(self.spec.worker_local_ai_timeout_seconds),
            )
            message = f"Hub session ended with unexpected terminal status {terminal_status!r}{reason_suffix}: {final_payload}"
            if timeout_refresh_hint:
                message = f"{message} {timeout_refresh_hint}"
                self.emit(
                    "worker_runtime_refresh_required",
                    status=terminal_status,
                    message=timeout_refresh_hint,
                    requested_timeout_seconds=float(self.spec.worker_local_ai_timeout_seconds),
                )
            atomic_write_json(
                self.artifacts.result_json,
                {
                    "created_at": utc_now(),
                    "error": message,
                    "terminal_status": terminal_status,
                    "failure_reason": failure_reason,
                    "timeout_refresh_hint": timeout_refresh_hint,
                    "partial_worker_text": partial_worker_text,
                    "partial_worker_text_present": bool(partial_worker_text),
                    "submitted_payload": submitted,
                    "final_payload": final_payload,
                    "hub_status": hub_status,
                },
            )
            self.emit("terminal_result_unknown", status=terminal_status, message=message, reason=failure_reason)
            raise RuntimeError(message)

        text_result = self.transport.extract_simple_text_result(final_payload)
        if not text_result:
            raise RuntimeError(f"Hub worker returned no simple text result: {final_payload}")
        return text_result, final_payload, submitted, hub_status

    def run(self) -> dict[str, Any]:
        self.artifacts.run_dir.mkdir(parents=True, exist_ok=True)
        run_record = {
            "run_id": self.spec.run_id,
            "created_at": utc_now(),
            "status": "running",
            "mission": self.spec.mission,
            "focus": self.spec.focus,
            "ring": self.spec.ring,
            "max_credits": self.spec.max_credits,
            "hub_url": self.spec.hub_url,
            "transport": "hub-live-session",
            "frameworks": framework_capabilities(),
            "temporal": {
                "workflow_type": "AgentRunWorkflow",
                "activities": [
                    "ensure_requester_authority",
                    "ensure_worker_availability",
                    "dispatch_paid_job",
                    "follow_stream",
                    "write_artifact",
                    "evaluate_result",
                    "propose_next_tasks",
                ],
                "signals": [
                    "add_instruction",
                    "pause_after_current_job",
                    "cancel",
                    "set_budget",
                    "approve_next_task",
                ],
            },
            "control_plane": {
                "commands_jsonl": str(self.artifacts.commands_jsonl),
                "supported_commands": [
                    "add_instruction",
                    "pause_after_current_job",
                    "resume",
                    "cancel",
                    "set_budget",
                    "approve_next_task",
                ],
            },
        }
        atomic_write_json(self.artifacts.run_json, run_record)
        self.emit("workflow_started", status="running", mission=self.spec.mission, focus=self.spec.focus)

        self.apply_control_commands()
        if self.cancel_requested:
            run_record["status"] = "cancelled"
            atomic_write_json(self.artifacts.run_json, run_record)
            self.emit("workflow_cancelled", status="cancelled")
            return run_record

        contract = make_digest_contract(self.spec)
        prompt = contract.to_prompt(self.extra_instructions)
        atomic_write_json(
            self.artifacts.job_json,
            {
                "created_at": utc_now(),
                "contract": dataclass_to_jsonable(contract),
                "prompt": prompt,
                "transport": "hub-live-session",
                "execution_limits": {
                    "session_timeout": self.spec.session_timeout,
                    "accept_timeout": self.spec.accept_timeout,
                    "worker_local_ai_timeout_seconds": self.spec.worker_local_ai_timeout_seconds,
                    "worker_target_tokens": self.spec.worker_target_tokens,
                },
            },
        )
        self.emit("job_contract_created", job_id=contract.job_id, focus=contract.focus)

        try:
            text_result, final_payload, submitted_payload, hub_status = self._dispatch_paid_job(contract=contract, prompt=prompt)
        except Exception as exc:
            run_record["status"] = "failed"
            run_record["completed_at"] = utc_now()
            run_record["error"] = str(exc)
            run_record["artifacts"] = {
                "run_json": str(self.artifacts.run_json),
                "job_json": str(self.artifacts.job_json),
                "stream_jsonl": str(self.artifacts.stream_jsonl),
                "result_json": str(self.artifacts.result_json),
            }
            atomic_write_json(self.artifacts.run_json, run_record)
            self.emit("workflow_failed", status="failed", error=str(exc))
            raise

        result = digest_result_from_worker_text(text_result, contract)
        atomic_write_json(
            self.artifacts.result_json,
            {
                "created_at": utc_now(),
                "worker_text": text_result,
                "digest_result": dataclass_to_jsonable(result),
                "submitted_payload": submitted_payload,
                "final_payload": final_payload,
                "hub_status": hub_status,
            },
        )
        self.artifacts.artifact_md.write_text(result.artifact_markdown, encoding="utf-8")
        self.emit("terminal_result", status="succeeded", artifact=str(self.artifacts.artifact_md))

        evaluation = evaluate_digest_result(result)
        evaluation.validate_contract()
        atomic_write_json(self.artifacts.evaluation_json, dataclass_to_jsonable(evaluation))
        self.emit("result_evaluated", status="accepted" if evaluation.accepted else "needs_review", score=evaluation.score)

        proposals = make_next_task_proposals(result, max_credits=self.spec.max_credits)
        atomic_write_json(
            self.artifacts.next_tasks_json,
            {
                "created_at": utc_now(),
                "tasks": [dataclass_to_jsonable(item) for item in proposals],
            },
        )
        self.emit("next_tasks_proposed", count=len(proposals))

        final_status = "paused_after_current_job" if self.pause_after_current_job else "passed"
        run_record["status"] = final_status
        run_record["completed_at"] = utc_now()
        run_record["artifacts"] = {
            "run_json": str(self.artifacts.run_json),
            "job_json": str(self.artifacts.job_json),
            "stream_jsonl": str(self.artifacts.stream_jsonl),
            "result_json": str(self.artifacts.result_json),
            "artifact_md": str(self.artifacts.artifact_md),
            "evaluation_json": str(self.artifacts.evaluation_json),
            "next_tasks_json": str(self.artifacts.next_tasks_json),
        }
        atomic_write_json(self.artifacts.run_json, run_record)
        self.emit("workflow_paused" if self.pause_after_current_job else "workflow_passed", status=final_status)
        return run_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke the controllable agent-run shape against the live Hub/credits/worker path.")
    parser.add_argument("--mission", default=DEFAULT_MISSION)
    parser.add_argument("--focus", default=DEFAULT_FOCUS)
    parser.add_argument("--ring", default=DEFAULT_RING)
    parser.add_argument("--max-credits", default=DEFAULT_MAX_CREDITS)
    parser.add_argument("--hub", default=os.environ.get("MAIN_COMPUTER_HUB_URL", DEFAULT_HUB_URL))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-root", default=str(DEFAULT_AGENT_RUN_ROOT))
    parser.add_argument("--instruction", action="append", default=[])
    parser.add_argument("--session-timeout", type=float, default=DEFAULT_SESSION_TIMEOUT_SECONDS)
    parser.add_argument("--accept-timeout", type=float, default=DEFAULT_ACCEPT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--worker-local-ai-timeout",
        type=float,
        default=DEFAULT_WORKER_LOCAL_AI_TIMEOUT_SECONDS,
        help="Outer local-AI executor timeout to request from the live worker.",
    )
    parser.add_argument(
        "--worker-target-tokens",
        type=int,
        default=DEFAULT_WORKER_TARGET_TOKENS,
        help="Bounded output token target for the live worker digest.",
    )
    parser.add_argument("--pause-after-current-job", action="store_true", default=True)
    parser.add_argument(
        "--continue-after-current-job",
        action="store_false",
        dest="pause_after_current_job",
        help="Mark the run passed instead of paused after the first paid job.",
    )
    parser.add_argument("--write-command", action="append", default=[], help="Seed a control command JSON object before running.")
    return parser


def _parse_seed_command(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"--write-command must be JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("--write-command must be a JSON object")
    AgentControlCommand.from_mapping(payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = AgentRunSpec(
        mission=args.mission,
        focus=args.focus,
        ring=args.ring,
        max_credits=args.max_credits,
        hub_url=args.hub,
        run_id=args.run_id,
        repo_root=args.repo_root,
        pause_after_current_job=bool(args.pause_after_current_job),
        user_instructions=tuple(str(item) for item in args.instruction),
        session_timeout=float(args.session_timeout),
        accept_timeout=float(args.accept_timeout),
        worker_local_ai_timeout_seconds=float(args.worker_local_ai_timeout),
        worker_target_tokens=int(args.worker_target_tokens),
    )
    runner = AgentShapeSmokeRunner(spec=spec, run_root=Path(args.run_root))
    for command_text in args.write_command:
        command = _parse_seed_command(command_text)
        append_jsonl(runner.artifacts.commands_jsonl, command)
    try:
        run_record = runner.run()
    except Exception as exc:
        print(f"[agent] FAIL: {exc}", file=sys.stderr)
        return 1
    print("")
    print("[agent] run_dir", runner.artifacts.run_dir)
    print("[agent] status", run_record.get("status"))
    print("[agent] artifact", runner.artifacts.artifact_md)
    print("[agent] evaluation", runner.artifacts.evaluation_json)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
