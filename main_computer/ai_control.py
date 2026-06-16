from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ast
import inspect
import json
from pathlib import Path
import threading
import time
import traceback
import uuid
from typing import Any

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.base import LLMProvider


AI_CONTROL_RUNTIME_DIR = Path("runtime") / "ai_control"
AI_CONTROL_CALLS_FILENAME = "calls.json"
AI_CONTROL_PROMPT_OVERRIDES_FILENAME = "prompt_overrides.json"
_MAX_RECORDED_CALLS = 80
_MAX_MESSAGE_CHARS = 12_000
_MAX_RESPONSE_CHARS = 8_000
_MAX_ERROR_CHARS = 4_000

_ai_call_surface: ContextVar[str | None] = ContextVar("main_computer_ai_call_surface", default=None)
_ai_control_lock = threading.RLock()


@dataclass(frozen=True)
class AiControlPromptSource:
    id: str
    title: str
    source_file: str
    source_symbol: str
    role: str
    description: str
    surfaces: tuple[str, ...]
    editable: bool = True


@dataclass(frozen=True)
class AiControlMessageSlot:
    role: str
    kind: str
    label: str
    prompt_id: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class AiControlMessageStructure:
    id: str
    title: str
    source_file: str
    function: str
    provider_call: str
    description: str
    slots: tuple[AiControlMessageSlot, ...]


AI_CONTROL_PROMPT_SOURCES: tuple[AiControlPromptSource, ...] = (
    AiControlPromptSource(
        id="router.system",
        title="Main Computer router system prompt",
        source_file="main_computer/router.py",
        source_symbol="SYSTEM_PROMPT",
        role="system",
        description="Base system prompt for the main chat router and several chat-console flows.",
        surfaces=("router.chat", "router.chat_console_ai", "text_console.chat", "chat_ai_subprocess.chat_console_ai"),
    ),
    AiControlPromptSource(
        id="router.terminal_suggestion.system",
        title="Terminal suggestion system prompt",
        source_file="main_computer/router.py",
        source_symbol="TERMINAL_SUGGESTION_SYSTEM_PROMPT",
        role="system",
        description="JSON-only instruction used when the model proposes one PowerShell command.",
        surfaces=("router.suggest_terminal_command",),
    ),
    AiControlPromptSource(
        id="chat_console.notebook_ai.system",
        title="Notebook AI system prompt",
        source_file="main_computer/chat_console.py",
        source_symbol="NOTEBOOK_AI_SYSTEM_PROMPT",
        role="system",
        description="Typed notebook-console behavior prompt shared by chat-console AI paths.",
        surfaces=("router.chat_console_ai", "chat_ai_subprocess.chat_console_ai"),
    ),
    AiControlPromptSource(
        id="text_console.chat.system",
        title="Text console chat system prompt",
        source_file="main_computer/text_console.py",
        source_symbol="TEXT_CONSOLE_AI_SYSTEM_PROMPT",
        role="system",
        description="Text console answer-shaping prompt for the normal text-console chat path.",
        surfaces=("text_console.chat",),
    ),
    AiControlPromptSource(
        id="text_console.thread_terminal_result_context.system",
        title="Thread terminal-result context prompt",
        source_file="main_computer/text_console.py",
        source_symbol="THREAD_TERMINAL_RESULT_CONTEXT_PROMPT",
        role="system",
        description="Marker prompt inserted before prior threaded terminal context in operator preflight.",
        surfaces=("text_console.operator.preflight",),
    ),
    AiControlPromptSource(
        id="text_console.clob_grounded_answer.system",
        title="CLOB grounded answer system prompt",
        source_file="main_computer/text_console.py",
        source_symbol="CLOB_GROUNDED_ANSWER_PROMPT",
        role="system",
        description="Evidence-only prompt for compact clob lookup follow-up answers.",
        surfaces=("text_console.clob_grounded_answer",),
    ),
    AiControlPromptSource(
        id="text_console.action_preflight.system",
        title="Text console action preflight prompt",
        source_file="main_computer/text_console.py",
        source_symbol="ACTION_PREFLIGHT_PROMPT",
        role="system",
        description="JSON preflight prompt that classifies whether a text-console request needs mount/edit/answer behavior.",
        surfaces=("text_console.operator.preflight",),
    ),
    AiControlPromptSource(
        id="text_console.final_operator.system",
        title="Text console final operator prompt",
        source_file="main_computer/text_console.py",
        source_symbol="FINAL_OPERATOR_PROMPT",
        role="system",
        description="Final answer prompt used after operator preflight selects action specs.",
        surfaces=("text_console.operator.final",),
    ),
    AiControlPromptSource(
        id="executor_tool_loop.system",
        title="Executor tool-loop system prompt",
        source_file="main_computer/executor_tool_loop.py",
        source_symbol="EXECUTOR_TOOL_LOOP_SYSTEM_PROMPT",
        role="system",
        description="Planner prompt for the Linux execution/tool loop.",
        surfaces=("executor_tool_loop.plan_next_step",),
    ),
    AiControlPromptSource(
        id="rag_trust_contract.system",
        title="RAG trust-contract drafting prompt",
        source_file="main_computer/rag_trust_contract_chat.py",
        source_symbol="TRUST_CONTRACT_SYSTEM_PROMPT",
        role="system",
        description="Prompt used to draft grounded answers from trust-contract evidence.",
        surfaces=("rag_trust_contract.draft",),
    ),
)

AI_CONTROL_PROMPT_SOURCE_BY_ID = {item.id: item for item in AI_CONTROL_PROMPT_SOURCES}

AI_CONTROL_MESSAGE_STRUCTURES: tuple[AiControlMessageStructure, ...] = (
    AiControlMessageStructure(
        id="router.chat",
        title="Main chat",
        source_file="main_computer/router.py",
        function="MainComputer.chat",
        provider_call="self.provider.chat(messages)",
        description="Normal /api/chat router call.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Main router system prompt", prompt_id="router.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Workspace context pack"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Web search context", optional=True),
            AiControlMessageSlot(role="user", kind="request", label="User prompt"),
        ),
    ),
    AiControlMessageStructure(
        id="router.suggest_terminal_command",
        title="Terminal command suggestion",
        source_file="main_computer/router.py",
        function="MainComputer.suggest_terminal_command",
        provider_call="self.provider.chat(messages)",
        description="AI helper that returns one JSON command suggestion for the terminal UI.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Terminal suggestion system prompt", prompt_id="router.terminal_suggestion.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Workspace context pack"),
            AiControlMessageSlot(role="user", kind="structured_request", label="JSON schema instructions, current cwd, and user request"),
        ),
    ),
    AiControlMessageStructure(
        id="router.chat_console_ai",
        title="Chat Console AI through router",
        source_file="main_computer/router.py",
        function="MainComputer.chat_console_ai",
        provider_call="self.provider.chat(messages)",
        description="Chat-console AI path assembled inside the main router.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Main router system prompt", prompt_id="router.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Workspace context pack"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Web search context", optional=True),
            AiControlMessageSlot(role="system", kind="static_prompt", label="Notebook AI system prompt", prompt_id="chat_console.notebook_ai.system"),
            AiControlMessageSlot(role="user", kind="request_with_attachments", label="Notebook source and attachments"),
        ),
    ),
    AiControlMessageStructure(
        id="chat_ai_subprocess.chat_console_ai",
        title="Chat Console AI subprocess",
        source_file="main_computer/chat_ai_subprocess.py",
        function="_run_chat_console_ai_child",
        provider_call="provider.chat(messages)",
        description="Subprocess model call for chat-console AI, with either mounted editor scope or normal workspace context.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Main router system prompt", prompt_id="router.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Mounted editor scope or workspace context pack"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Web search context", optional=True),
            AiControlMessageSlot(role="system", kind="static_prompt", label="Notebook AI system prompt", prompt_id="chat_console.notebook_ai.system"),
            AiControlMessageSlot(role="user", kind="request_with_attachments", label="Notebook source and attachments"),
        ),
    ),
    AiControlMessageStructure(
        id="text_console.chat",
        title="Text Console chat",
        source_file="main_computer/text_console.py",
        function="build_text_console_model_input",
        provider_call="model_input.computer.provider.chat(model_input.messages)",
        description="Normal text-console AI answer path.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Main router system prompt", prompt_id="router.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Workspace context pack"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Web search context", optional=True),
            AiControlMessageSlot(role="system", kind="static_prompt", label="Text console chat system prompt", prompt_id="text_console.chat.system"),
            AiControlMessageSlot(role="user", kind="request", label="Text console source"),
        ),
    ),
    AiControlMessageStructure(
        id="text_console.operator.preflight",
        title="Text Console operator preflight",
        source_file="main_computer/text_console.py",
        function="build_action_preflight_messages",
        provider_call="base_model_input.computer.provider.chat(preflight_messages)",
        description="JSON-only preflight that decides whether the text-console request needs mount/edit/answer handling.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Action preflight prompt", prompt_id="text_console.action_preflight.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Action spec catalog"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Target profile catalog"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Runtime root hint"),
            AiControlMessageSlot(role="system", kind="static_prompt", label="Thread terminal-result context marker", prompt_id="text_console.thread_terminal_result_context.system", optional=True),
            AiControlMessageSlot(role="assistant/user", kind="conversation_context", label="Prior threaded messages", optional=True),
            AiControlMessageSlot(role="user", kind="request", label="Operator request text"),
        ),
    ),
    AiControlMessageStructure(
        id="text_console.operator.final",
        title="Text Console operator final answer",
        source_file="main_computer/text_console.py",
        function="build_operator_final_messages",
        provider_call="base_model_input.computer.provider.chat(final_messages)",
        description="Final operator answer after preflight selects action specs.",
        slots=(
            AiControlMessageSlot(role="system/user", kind="compacted_context", label="Compacted base model messages"),
            AiControlMessageSlot(role="assistant/user", kind="conversation_context", label="Prior threaded messages", optional=True),
            AiControlMessageSlot(role="system", kind="static_prompt", label="Final operator prompt", prompt_id="text_console.final_operator.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Selected action specs"),
            AiControlMessageSlot(role="user", kind="request", label="Original user request"),
        ),
    ),
    AiControlMessageStructure(
        id="text_console.clob_grounded_answer",
        title="Text Console clob-grounded answer",
        source_file="main_computer/text_console.py",
        function="build_text_console_clob_grounded_answer_messages",
        provider_call="computer.provider.chat(messages)",
        description="Compact evidence-only path for CLOB lookup follow-up answers.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="CLOB grounded answer prompt", prompt_id="text_console.clob_grounded_answer.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Text-console root hint"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="CLOB lookup evidence"),
            AiControlMessageSlot(role="user", kind="request", label="User prompt"),
        ),
    ),
    AiControlMessageStructure(
        id="executor_tool_loop.plan_next_step",
        title="Executor tool-loop planner",
        source_file="main_computer/executor_tool_loop.py",
        function="run_executor_tool_loop",
        provider_call="provider.chat(messages)",
        description="Planner loop that proposes the next executor tool action.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Executor tool-loop system prompt", prompt_id="executor_tool_loop.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Executor context text"),
            AiControlMessageSlot(role="user", kind="request", label="Initial request / task state"),
            AiControlMessageSlot(role="assistant/user", kind="loop_history", label="Prior model/tool loop turns", optional=True),
        ),
    ),
    AiControlMessageStructure(
        id="rag_trust_contract.draft",
        title="RAG trust-contract drafting",
        source_file="main_computer/rag_trust_contract_chat.py",
        function="TrustContractDraftModel.draft",
        provider_call="provider.chat(model_messages)",
        description="Grounded answer draft over selected trust evidence.",
        slots=(
            AiControlMessageSlot(role="system", kind="static_prompt", label="Trust-contract system prompt", prompt_id="rag_trust_contract.system"),
            AiControlMessageSlot(role="system", kind="dynamic_context", label="Formatted evidence"),
            AiControlMessageSlot(role="user", kind="request", label="Normalized prompt"),
        ),
    ),
)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _clip_text(value: object, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[truncated after {limit} characters]"


def _calls_path(runtime_root: Path | str) -> Path:
    return Path(runtime_root) / AI_CONTROL_RUNTIME_DIR / AI_CONTROL_CALLS_FILENAME


def _prompt_overrides_path(runtime_root: Path | str) -> Path:
    return Path(runtime_root) / AI_CONTROL_RUNTIME_DIR / AI_CONTROL_PROMPT_OVERRIDES_FILENAME


def _read_calls(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict) and isinstance(raw.get("calls"), list):
        return [item for item in raw["calls"] if isinstance(item, dict)]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _write_calls(path: Path, calls: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": True,
        "schema": "main_computer.ai_control.calls.v1",
        "updated_at": _utc_now(),
        "calls": calls[-_MAX_RECORDED_CALLS:],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_prompt_overrides(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    source = raw.get("overrides") if isinstance(raw, dict) else raw
    if not isinstance(source, dict):
        return {}
    out: dict[str, str] = {}
    for prompt_id, content in source.items():
        prompt_key = str(prompt_id or "").strip()
        if prompt_key in AI_CONTROL_PROMPT_SOURCE_BY_ID and isinstance(content, str):
            out[prompt_key] = content
    return out


def _write_prompt_overrides(path: Path, overrides: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": True,
        "schema": "main_computer.ai_control.prompt_overrides.v1",
        "updated_at": _utc_now(),
        "overrides": {key: overrides[key] for key in sorted(overrides)},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _repo_root_from_runtime_root(runtime_root: Path | str) -> Path:
    root = Path(runtime_root).resolve()
    if (root / "main_computer").is_dir():
        return root
    for parent in root.parents:
        if (parent / "main_computer").is_dir():
            return parent
    return root


def _extract_source_string(repo_root: Path, source: AiControlPromptSource) -> tuple[str, str | None]:
    path = repo_root / source.source_file
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError as exc:
        return "", f"{type(exc).__name__}: {exc}"
    except SyntaxError as exc:
        return "", f"SyntaxError: {exc}"

    for node in module.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == source.source_symbol:
                try:
                    result = ast.literal_eval(value)
                except Exception as exc:
                    return "", f"{type(exc).__name__}: {exc}"
                if isinstance(result, str):
                    return result, None
                return "", f"{source.source_symbol} is not a string literal"
    return "", f"{source.source_symbol} was not found in {source.source_file}"


def _prompt_source_to_dict(
    source: AiControlPromptSource,
    *,
    repo_root: Path,
    overrides: dict[str, str],
) -> dict[str, Any]:
    default_content, error = _extract_source_string(repo_root, source)
    override_content = overrides.get(source.id)
    has_override = override_content is not None
    effective_content = override_content if override_content is not None else default_content
    return {
        **asdict(source),
        "surfaces": list(source.surfaces),
        "default_content": default_content,
        "override_content": override_content,
        "effective_content": effective_content,
        "has_override": has_override,
        "content_changed": has_override and override_content != default_content,
        "default_chars": len(default_content),
        "effective_chars": len(effective_content),
        "source_error": error,
    }


def _structure_to_dict(structure: AiControlMessageStructure) -> dict[str, Any]:
    return {
        **asdict(structure),
        "slots": [asdict(slot) for slot in structure.slots],
    }


def ai_control_prompt_catalog(runtime_root: Path | str, *, repo_root: Path | str | None = None) -> dict[str, Any]:
    """Return the static prompt catalog and message structures used by AI Control.

    This is intentionally about prompt preparation, not individual live calls.
    Defaults are read from source files, while editable runtime overrides live in
    runtime/ai_control/prompt_overrides.json.
    """

    runtime = Path(runtime_root).resolve()
    source_root = Path(repo_root).resolve() if repo_root is not None else _repo_root_from_runtime_root(runtime)
    overrides_path = _prompt_overrides_path(runtime)
    overrides = _read_prompt_overrides(overrides_path)
    prompts = [
        _prompt_source_to_dict(source, repo_root=source_root, overrides=overrides)
        for source in AI_CONTROL_PROMPT_SOURCES
    ]
    surfaces = sorted({surface for prompt in AI_CONTROL_PROMPT_SOURCES for surface in prompt.surfaces})
    return {
        "ok": True,
        "schema": "main_computer.ai_control.prompt_catalog.v1",
        "updated_at": _utc_now(),
        "runtime_root": str(runtime),
        "repo_root": str(source_root),
        "override_path": str(overrides_path),
        "prompt_count": len(prompts),
        "override_count": len(overrides),
        "surface_count": len(surfaces),
        "surfaces": surfaces,
        "prompts": prompts,
        "message_structures": [_structure_to_dict(item) for item in AI_CONTROL_MESSAGE_STRUCTURES],
    }


def ai_control_prompt_text(prompt_id: str, default_content: str, *, runtime_root: Path | str | None = None) -> str:
    """Return the effective prompt text for a named static prompt.

    If no override is saved, this returns default_content exactly. Call sites can
    opt into editable prompts with a one-line wrapper without changing default
    behavior.
    """

    prompt_key = str(prompt_id or "").strip()
    if prompt_key not in AI_CONTROL_PROMPT_SOURCE_BY_ID:
        return str(default_content or "")
    runtime = Path(runtime_root).resolve() if runtime_root is not None else Path.cwd().resolve()
    overrides = _read_prompt_overrides(_prompt_overrides_path(runtime))
    override = overrides.get(prompt_key)
    if override is None:
        return str(default_content or "")
    return override


def ai_control_save_prompt_override(
    runtime_root: Path | str,
    *,
    prompt_id: str,
    content: str | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Save or reset a runtime prompt override, then return the fresh catalog."""

    prompt_key = str(prompt_id or "").strip()
    if prompt_key not in AI_CONTROL_PROMPT_SOURCE_BY_ID:
        return {
            "ok": False,
            "error": f"Unknown AI prompt id: {prompt_id!r}",
            "known_prompt_ids": sorted(AI_CONTROL_PROMPT_SOURCE_BY_ID),
        }
    path = _prompt_overrides_path(runtime_root)
    with _ai_control_lock:
        overrides = _read_prompt_overrides(path)
        if reset:
            overrides.pop(prompt_key, None)
        else:
            if content is None:
                return {"ok": False, "error": "content is required unless reset is true"}
            overrides[prompt_key] = str(content)
        _write_prompt_overrides(path, overrides)
    return ai_control_prompt_catalog(runtime_root)


def _message_to_dict(message: ChatMessage, index: int) -> dict[str, Any]:
    content = getattr(message, "content", "")
    return {
        "index": index,
        "role": str(getattr(message, "role", "unknown") or "unknown"),
        "chars": len(str(content or "")),
        "content": _clip_text(content, _MAX_MESSAGE_CHARS),
    }


def _message_counts(messages: Sequence[ChatMessage]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        role = str(getattr(message, "role", "unknown") or "unknown")
        counts[role] = counts.get(role, 0) + 1
    return counts


def _infer_surface_from_stack() -> str:
    try:
        for frame in inspect.stack(context=0)[3:16]:
            path = Path(frame.filename)
            parts = path.parts
            if "main_computer" not in parts:
                continue
            rel_parts = parts[parts.index("main_computer") :]
            rel = "/".join(rel_parts)
            if rel.endswith("ai_control.py") or "/providers/" in rel:
                continue
            return f"{rel}:{frame.function}:{frame.lineno}"
    except Exception:
        pass
    return "unregistered"


@contextmanager
def ai_call_surface(surface: str):
    """Temporarily label provider calls made inside this block for AI Control."""

    token = _ai_call_surface.set(str(surface or "").strip() or None)
    try:
        yield
    finally:
        _ai_call_surface.reset(token)


@dataclass
class AiControlRecorder:
    runtime_root: Path

    @property
    def path(self) -> Path:
        return _calls_path(self.runtime_root)

    def begin_call(
        self,
        *,
        provider: str,
        model: str,
        messages: Sequence[ChatMessage],
        surface: str | None = None,
    ) -> str:
        call_id = uuid.uuid4().hex
        now = time.time()
        active_surface = surface or _ai_call_surface.get() or _infer_surface_from_stack()
        record = {
            "id": call_id,
            "status": "pending",
            "surface": active_surface,
            "provider": provider,
            "model": model,
            "started_at": _utc_now(),
            "started_monotonic_s": now,
            "duration_ms": None,
            "message_count": len(messages),
            "message_roles": _message_counts(messages),
            "system_message_count": sum(1 for item in messages if str(getattr(item, "role", "")) == "system"),
            "messages": [_message_to_dict(item, index) for index, item in enumerate(messages)],
            "response": None,
            "error": None,
        }
        self._upsert(record)
        return call_id

    def finish_call(self, call_id: str, response: ChatResponse, *, started_monotonic_s: float | None = None) -> None:
        duration_ms = None
        if started_monotonic_s is not None:
            duration_ms = int(max(0.0, time.monotonic() - started_monotonic_s) * 1000)
        self._update(
            call_id,
            {
                "status": "ok",
                "finished_at": _utc_now(),
                "duration_ms": duration_ms,
                "response": {
                    "provider": response.provider,
                    "model": response.model,
                    "chars": len(str(response.content or "")),
                    "content": _clip_text(response.content, _MAX_RESPONSE_CHARS),
                    "metadata_keys": sorted(str(key) for key in (response.metadata or {}).keys()),
                },
            },
        )

    def fail_call(self, call_id: str, exc: BaseException, *, started_monotonic_s: float | None = None) -> None:
        duration_ms = None
        if started_monotonic_s is not None:
            duration_ms = int(max(0.0, time.monotonic() - started_monotonic_s) * 1000)
        self._update(
            call_id,
            {
                "status": "error",
                "finished_at": _utc_now(),
                "duration_ms": duration_ms,
                "error": {
                    "type": type(exc).__name__,
                    "message": _clip_text(str(exc), _MAX_ERROR_CHARS),
                    "traceback": _clip_text("".join(traceback.format_exception_only(type(exc), exc)), _MAX_ERROR_CHARS),
                },
            },
        )

    def _upsert(self, record: dict[str, Any]) -> None:
        with _ai_control_lock:
            calls = _read_calls(self.path)
            calls = [item for item in calls if item.get("id") != record.get("id")]
            calls.append(record)
            _write_calls(self.path, calls)

    def _update(self, call_id: str, fields: dict[str, Any]) -> None:
        with _ai_control_lock:
            calls = _read_calls(self.path)
            for item in calls:
                if item.get("id") == call_id:
                    item.update(fields)
                    break
            _write_calls(self.path, calls)


class ObservedLLMProvider(LLMProvider):
    """Transparent LLM provider wrapper that records prompt/response snapshots.

    This remains available for manual diagnostics, but AI Control's default app
    now focuses on static prompt/message preparation rather than live call logs.
    """

    def __init__(self, delegate: LLMProvider, recorder: AiControlRecorder):
        self._delegate = delegate
        self._recorder = recorder
        self.name = delegate.name
        self.model = delegate.model

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        started_monotonic_s = time.monotonic()
        call_id = self._recorder.begin_call(
            provider=self.name,
            model=self.model,
            messages=messages,
        )
        try:
            response = self._delegate.chat(messages)
        except BaseException as exc:
            self._recorder.fail_call(call_id, exc, started_monotonic_s=started_monotonic_s)
            raise
        self._recorder.finish_call(call_id, response, started_monotonic_s=started_monotonic_s)
        return response


def observe_provider(delegate: LLMProvider, *, runtime_root: Path | str) -> LLMProvider:
    """Return a transparent provider wrapper for opt-in AI call observability."""

    if isinstance(delegate, ObservedLLMProvider):
        return delegate
    return ObservedLLMProvider(delegate, AiControlRecorder(Path(runtime_root)))


def ai_control_calls_snapshot(runtime_root: Path | str) -> dict[str, Any]:
    """Read the current AI Control call log for the legacy diagnostics API."""

    path = _calls_path(runtime_root)
    calls = _read_calls(path)
    surfaces: dict[str, int] = {}
    providers: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for call in calls:
        surface = str(call.get("surface") or "unregistered")
        provider = str(call.get("provider") or "unknown")
        status = str(call.get("status") or "unknown")
        surfaces[surface] = surfaces.get(surface, 0) + 1
        providers[provider] = providers.get(provider, 0) + 1
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "ok": True,
        "schema": "main_computer.ai_control.snapshot.v1",
        "path": str(path),
        "call_count": len(calls),
        "surfaces": surfaces,
        "providers": providers,
        "statuses": statuses,
        "calls": list(reversed(calls)),
    }
