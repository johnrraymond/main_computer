from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ast
import inspect
import json
import re
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
AI_CONTROL_PROFILES_FILENAME = "profiles.json"
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


@dataclass(frozen=True)
class AiControlComposable:
    id: str
    label: str
    kind: str
    description: str
    prompt_text: str
    source: str = "factory"


@dataclass(frozen=True)
class AiControlProfile:
    id: str
    name: str
    description: str
    enabled_composable_ids: tuple[str, ...]
    source: str = "factory"


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


AI_CONTROL_FACTORY_COMPOSABLES: tuple[AiControlComposable, ...] = (
    AiControlComposable(
        id="builtin.operator_real_workspace",
        label="Operator in a real workspace",
        kind="user_treatment",
        description="Treat the user as a capable operator working in a real local Windows workspace.",
        prompt_text=(
            "Treat the user as a capable operator working in a real local Windows workspace. "
            "They need grounded, executable help, not abstract reassurance."
        ),
    ),
    AiControlComposable(
        id="builtin.provider_honesty",
        label="Be honest about provider and tool limits",
        kind="limits",
        description="Do not imply file, command, runtime, or hardware access unless that access actually exists.",
        prompt_text=(
            "Be explicit about provider and tool limits. Do not imply file, command, runtime, "
            "patch, or hardware access unless that access was actually available and used."
        ),
    ),
    AiControlComposable(
        id="builtin.next_command",
        label="Needs the next command",
        kind="output_shape",
        description="Prefer the most obvious next step and include the exact command when useful.",
        prompt_text=(
            "Prefer the most obvious next step over broad theory. When a command is appropriate, "
            "include the exact next command."
        ),
    ),
    AiControlComposable(
        id="builtin.verified_vs_unverified",
        label="Verified vs unverified split",
        kind="verification",
        description="Clearly separate what was actually checked from assumptions and remaining risk.",
        prompt_text=(
            "Separate verified facts from unverified assumptions. Do not claim tests, command success, "
            "patch success, or runtime behavior unless it was actually verified."
        ),
    ),
    AiControlComposable(
        id="builtin.dry_run_before_mutation",
        label="Dry-run before mutation",
        kind="safety",
        description="Prefer status checks, previews, and dry-runs before actions that mutate state.",
        prompt_text=(
            "Prefer status checks, previews, and dry-runs before mutation. Keep changes narrow, "
            "reversible, and low-cleanup when possible."
        ),
    ),
    AiControlComposable(
        id="builtin.hardware_gate",
        label="Real hardware is the judge",
        kind="hardware",
        description="Do not imply hardware success unless the result was tested on real hardware.",
        prompt_text=(
            "Real hardware is the final judge. Do not claim hardware success unless real hardware "
            "was actually tested; mark hardware behavior as unverified otherwise."
        ),
    ),
    AiControlComposable(
        id="builtin.unforgiving_reviewer",
        label="Unforgiving reviewer",
        kind="user_treatment",
        description="Treat the user as someone who may reject vague, overbroad, or costly work.",
        prompt_text=(
            "Treat the user as an unforgiving reviewer with limited patience for preventable ambiguity. "
            "Optimize for work that is clear, narrow, reviewable, and worth keeping."
        ),
    ),
)

AI_CONTROL_FACTORY_COMPOSABLE_BY_ID = {item.id: item for item in AI_CONTROL_FACTORY_COMPOSABLES}

AI_CONTROL_FACTORY_PROFILES: tuple[AiControlProfile, ...] = (
    AiControlProfile(
        id="factory.operator_safe",
        name="Operator Safe",
        description="Grounded, executable help for a real workspace operator.",
        enabled_composable_ids=(
            "builtin.operator_real_workspace",
            "builtin.provider_honesty",
            "builtin.verified_vs_unverified",
            "builtin.next_command",
        ),
    ),
    AiControlProfile(
        id="factory.patch_builder",
        name="Patch Builder",
        description="Prepare narrow, reviewable code changes with dry-run-first habits.",
        enabled_composable_ids=(
            "builtin.operator_real_workspace",
            "builtin.provider_honesty",
            "builtin.verified_vs_unverified",
            "builtin.dry_run_before_mutation",
            "builtin.next_command",
            "builtin.unforgiving_reviewer",
        ),
    ),
    AiControlProfile(
        id="factory.hardware_reviewer",
        name="Hardware Reviewer",
        description="Treat outputs as destined for real hardware and unforgiving validation.",
        enabled_composable_ids=(
            "builtin.operator_real_workspace",
            "builtin.provider_honesty",
            "builtin.verified_vs_unverified",
            "builtin.next_command",
            "builtin.hardware_gate",
            "builtin.unforgiving_reviewer",
        ),
    ),
)

AI_CONTROL_FACTORY_PROFILE_BY_ID = {item.id: item for item in AI_CONTROL_FACTORY_PROFILES}
AI_CONTROL_DEFAULT_PROFILE_ID = "factory.operator_safe"
AI_CONTROL_PROFILE_SCHEMA = "main_computer.ai_control.profiles.v1"


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


def _profiles_path(runtime_root: Path | str) -> Path:
    return Path(runtime_root) / AI_CONTROL_RUNTIME_DIR / AI_CONTROL_PROFILES_FILENAME


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


def _safe_id_fragment(value: str, *, fallback: str = "item") -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip().lower()).strip("._-")
    return text or fallback


def _unique_id(existing: set[str], prefix: str, label: str) -> str:
    base = f"{prefix}.{_safe_id_fragment(label, fallback='custom')}"
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate


def _read_profiles_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_profiles_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": True,
        "schema": AI_CONTROL_PROFILE_SCHEMA,
        "updated_at": _utc_now(),
        "active_profile_id": str(state.get("active_profile_id") or AI_CONTROL_DEFAULT_PROFILE_ID),
        "profile_overrides": state.get("profile_overrides") if isinstance(state.get("profile_overrides"), dict) else {},
        "user_profiles": state.get("user_profiles") if isinstance(state.get("user_profiles"), dict) else {},
        "composable_overrides": state.get("composable_overrides") if isinstance(state.get("composable_overrides"), dict) else {},
        "user_composables": state.get("user_composables") if isinstance(state.get("user_composables"), dict) else {},
        "profile_composable_overrides": (
            state.get("profile_composable_overrides")
            if isinstance(state.get("profile_composable_overrides"), dict)
            else {}
        ),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _normalize_id_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    out: list[str] = []
    for item in value:
        item_id = str(item or "").strip()
        if item_id and item_id not in out:
            out.append(item_id)
    return out


def _composable_to_dict(composable: AiControlComposable, *, has_override: bool = False) -> dict[str, Any]:
    return {
        "id": composable.id,
        "label": composable.label,
        "kind": composable.kind,
        "description": composable.description,
        "prompt_text": composable.prompt_text,
        "source": composable.source,
        "is_factory": composable.id in AI_CONTROL_FACTORY_COMPOSABLE_BY_ID,
        "has_override": has_override,
        "can_reset": has_override,
        "can_delete": composable.source == "user",
    }


def _profile_to_dict(profile: AiControlProfile, *, has_override: bool = False) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "enabled_composable_ids": list(profile.enabled_composable_ids),
        "source": profile.source,
        "is_factory": profile.id in AI_CONTROL_FACTORY_PROFILE_BY_ID,
        "has_override": has_override,
        "can_reset": has_override,
        "can_delete": profile.source == "user",
    }


def _merged_composable_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {
        item.id: _composable_to_dict(item)
        for item in AI_CONTROL_FACTORY_COMPOSABLES
    }
    overrides = state.get("composable_overrides") if isinstance(state.get("composable_overrides"), dict) else {}
    for composable_id, override in overrides.items():
        key = str(composable_id or "").strip()
        if key not in merged or not isinstance(override, dict):
            continue
        current = dict(merged[key])
        for field in ("label", "kind", "description", "prompt_text"):
            if isinstance(override.get(field), str):
                current[field] = override[field]
        current["has_override"] = True
        current["can_reset"] = True
        merged[key] = current
    user_composables = state.get("user_composables") if isinstance(state.get("user_composables"), dict) else {}
    for composable_id, item in user_composables.items():
        key = str(composable_id or "").strip()
        if not key or not isinstance(item, dict):
            continue
        merged[key] = {
            "id": key,
            "label": str(item.get("label") or key),
            "kind": str(item.get("kind") or "user_defined"),
            "description": str(item.get("description") or ""),
            "prompt_text": str(item.get("prompt_text") or ""),
            "source": "user",
            "is_factory": False,
            "has_override": False,
            "can_reset": False,
            "can_delete": True,
        }
    return merged


def _merged_profile_map(state: dict[str, Any], composable_ids: set[str]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {
        item.id: _profile_to_dict(item)
        for item in AI_CONTROL_FACTORY_PROFILES
    }
    overrides = state.get("profile_overrides") if isinstance(state.get("profile_overrides"), dict) else {}
    for profile_id, override in overrides.items():
        key = str(profile_id or "").strip()
        if key not in merged or not isinstance(override, dict):
            continue
        current = dict(merged[key])
        if isinstance(override.get("name"), str):
            current["name"] = override["name"]
        if isinstance(override.get("description"), str):
            current["description"] = override["description"]
        if isinstance(override.get("enabled_composable_ids"), list):
            current["enabled_composable_ids"] = [
                item for item in _normalize_id_list(override["enabled_composable_ids"])
                if item in composable_ids
            ]
        current["has_override"] = True
        current["can_reset"] = True
        merged[key] = current
    user_profiles = state.get("user_profiles") if isinstance(state.get("user_profiles"), dict) else {}
    for profile_id, item in user_profiles.items():
        key = str(profile_id or "").strip()
        if not key or not isinstance(item, dict):
            continue
        merged[key] = {
            "id": key,
            "name": str(item.get("name") or key),
            "description": str(item.get("description") or ""),
            "enabled_composable_ids": [
                item_id for item_id in _normalize_id_list(item.get("enabled_composable_ids"))
                if item_id in composable_ids
            ],
            "source": "user",
            "is_factory": False,
            "has_override": False,
            "can_reset": False,
            "can_delete": True,
        }
    return merged


def _profile_composable_override_map(state: dict[str, Any], profile_id: str) -> dict[str, dict[str, str]]:
    all_overrides = (
        state.get("profile_composable_overrides")
        if isinstance(state.get("profile_composable_overrides"), dict)
        else {}
    )
    raw = all_overrides.get(str(profile_id or "").strip())
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for composable_id, item in raw.items():
        key = str(composable_id or "").strip()
        if not key or not isinstance(item, dict):
            continue
        payload: dict[str, str] = {}
        for field in ("label", "kind", "description", "prompt_text"):
            if isinstance(item.get(field), str):
                payload[field] = item[field]
        if payload:
            out[key] = payload
    return out


def _apply_profile_composable_override(
    composable: dict[str, Any],
    override: dict[str, str] | None,
) -> dict[str, Any]:
    item = dict(composable)
    item["base_label"] = str(composable.get("label") or composable.get("id") or "")
    item["base_kind"] = str(composable.get("kind") or "")
    item["base_description"] = str(composable.get("description") or "")
    item["base_prompt_text"] = str(composable.get("prompt_text") or "")
    item["profile_override"] = {}
    item["profile_has_override"] = False
    item["can_reset_profile_choice"] = False
    if override:
        clean: dict[str, str] = {}
        for field in ("label", "kind", "description", "prompt_text"):
            if isinstance(override.get(field), str):
                clean[field] = override[field]
                item[field] = override[field]
        if clean:
            item["profile_override"] = clean
            item["profile_has_override"] = True
            item["can_reset_profile_choice"] = True
    return item


def _profile_choice_list(
    profile: dict[str, Any],
    composables: dict[str, dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    enabled_ids = set(_normalize_id_list(profile.get("enabled_composable_ids")))
    overrides = _profile_composable_override_map(state, str(profile.get("id") or ""))
    choices: list[dict[str, Any]] = []
    for composable in composables.values():
        item = _apply_profile_composable_override(composable, overrides.get(str(composable.get("id") or "")))
        item["enabled"] = str(item.get("id") or "") in enabled_ids
        choices.append(item)
    choices.sort(
        key=lambda item: (
            0 if item.get("enabled") else 1,
            0 if item.get("is_factory") else 1,
            str(item.get("kind") or ""),
            str(item.get("label") or item.get("id")),
        )
    )
    return choices


def _sanitize_profile_composable_overrides(
    value: object,
    composables: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for composable_id, item in value.items():
        key = str(composable_id or "").strip()
        if key not in composables or not isinstance(item, dict):
            continue
        base = composables[key]
        payload: dict[str, str] = {}
        for field in ("label", "kind", "description", "prompt_text"):
            raw = item.get(field)
            if not isinstance(raw, str):
                continue
            text = raw
            if text != str(base.get(field) or ""):
                payload[field] = text
        if payload:
            out[key] = payload
    return out


def _compile_profile_preview(
    profile: dict[str, Any] | None,
    composables: dict[str, dict[str, Any]],
    state: dict[str, Any] | None = None,
) -> str:
    if not profile:
        return ""
    state = state or {}
    lines = [
        f"User treatment profile: {profile.get('name') or profile.get('id')}",
    ]
    description = str(profile.get("description") or "").strip()
    if description:
        lines.extend(["", description])
    enabled_ids = _normalize_id_list(profile.get("enabled_composable_ids"))
    overrides = _profile_composable_override_map(state, str(profile.get("id") or ""))
    if enabled_ids:
        lines.append("")
        lines.append("Enabled profile choices:")
        for composable_id in enabled_ids:
            composable = composables.get(composable_id)
            if not composable:
                continue
            effective = _apply_profile_composable_override(composable, overrides.get(composable_id))
            prompt_text = str(effective.get("prompt_text") or "").strip()
            if prompt_text:
                lines.append(f"- {prompt_text}")
    else:
        lines.extend(["", "No profile choices are enabled yet."])
    return "\n".join(lines).strip()


def ai_control_profile_catalog(runtime_root: Path | str) -> dict[str, Any]:
    """Return user-treatment profile UI state.

    This is UI/state only. It does not inject composables into model calls yet.
    """

    runtime = Path(runtime_root).resolve()
    path = _profiles_path(runtime)
    state = _read_profiles_state(path)
    composable_map = _merged_composable_map(state)
    profile_map = _merged_profile_map(state, set(composable_map))
    active_profile_id = str(state.get("active_profile_id") or AI_CONTROL_DEFAULT_PROFILE_ID)
    if active_profile_id not in profile_map:
        active_profile_id = AI_CONTROL_DEFAULT_PROFILE_ID if AI_CONTROL_DEFAULT_PROFILE_ID in profile_map else next(iter(profile_map), "")
    profiles = list(profile_map.values())
    profiles.sort(key=lambda item: (0 if item.get("is_factory") else 1, str(item.get("name") or item.get("id"))))
    composables = list(composable_map.values())
    composables.sort(key=lambda item: (0 if item.get("is_factory") else 1, str(item.get("kind") or ""), str(item.get("label") or item.get("id"))))
    for profile in profiles:
        profile["choices"] = _profile_choice_list(profile, composable_map, state)
        profile["compiled_preview"] = _compile_profile_preview(profile, composable_map, state)
        profile["is_active"] = profile["id"] == active_profile_id
        profile["has_profile_choice_overrides"] = bool(_profile_composable_override_map(state, str(profile.get("id") or "")))
    active_profile = profile_map.get(active_profile_id)
    return {
        "ok": True,
        "schema": AI_CONTROL_PROFILE_SCHEMA,
        "updated_at": _utc_now(),
        "runtime_root": str(runtime),
        "path": str(path),
        "active_profile_id": active_profile_id,
        "active_profile": {
            **active_profile,
            "choices": _profile_choice_list(active_profile, composable_map, state),
            "compiled_preview": _compile_profile_preview(active_profile, composable_map, state),
            "is_active": True,
            "has_profile_choice_overrides": bool(_profile_composable_override_map(state, str(active_profile.get("id") or ""))),
        } if active_profile else None,
        "profile_count": len(profiles),
        "composable_count": len(composables),
        "profiles": profiles,
        "composables": composables,
        "note": "Profiles are not injected into AI calls yet; this is the UI/state control layer.",
    }


def ai_control_save_profile(
    runtime_root: Path | str,
    *,
    profile_id: str | None = None,
    name: str,
    description: str = "",
    enabled_composable_ids: Sequence[str] | None = None,
    composable_overrides: object | None = None,
    set_active: bool = False,
) -> dict[str, Any]:
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        composable_map = _merged_composable_map(state)
        composable_ids = set(composable_map)
        enabled_ids = [item for item in _normalize_id_list(list(enabled_composable_ids or [])) if item in composable_ids]
        existing_ids = set(_merged_profile_map(state, composable_ids))
        key = str(profile_id or "").strip()
        if not key:
            key = _unique_id(existing_ids, "user.profile", name)
        if key in AI_CONTROL_FACTORY_PROFILE_BY_ID:
            overrides = state.setdefault("profile_overrides", {})
            overrides[key] = {
                "name": str(name or key),
                "description": str(description or ""),
                "enabled_composable_ids": enabled_ids,
            }
        else:
            profiles = state.setdefault("user_profiles", {})
            profiles[key] = {
                "name": str(name or key),
                "description": str(description or ""),
                "enabled_composable_ids": enabled_ids,
            }
        profile_choice_overrides = state.setdefault("profile_composable_overrides", {})
        clean_choice_overrides = _sanitize_profile_composable_overrides(composable_overrides, composable_map)
        if clean_choice_overrides:
            profile_choice_overrides[key] = clean_choice_overrides
        else:
            profile_choice_overrides.pop(key, None)
        if set_active:
            state["active_profile_id"] = key
        elif not state.get("active_profile_id"):
            state["active_profile_id"] = AI_CONTROL_DEFAULT_PROFILE_ID
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_duplicate_profile(runtime_root: Path | str, *, profile_id: str) -> dict[str, Any]:
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        composables = _merged_composable_map(state)
        profiles = _merged_profile_map(state, set(composables))
        source = profiles.get(str(profile_id or "").strip())
        if not source:
            return {"ok": False, "error": f"Unknown AI profile id: {profile_id!r}"}
        existing_ids = set(profiles)
        key = _unique_id(existing_ids, "user.profile", f"{source.get('name') or source.get('id')} copy")
        user_profiles = state.setdefault("user_profiles", {})
        user_profiles[key] = {
            "name": f"{source.get('name') or source.get('id')} Copy",
            "description": str(source.get("description") or ""),
            "enabled_composable_ids": _normalize_id_list(source.get("enabled_composable_ids")),
        }
        profile_choice_overrides = state.setdefault("profile_composable_overrides", {})
        source_choice_overrides = _profile_composable_override_map(state, str(source.get("id") or ""))
        if source_choice_overrides:
            profile_choice_overrides[key] = source_choice_overrides
        state["active_profile_id"] = key
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_delete_profile(runtime_root: Path | str, *, profile_id: str) -> dict[str, Any]:
    key = str(profile_id or "").strip()
    if key in AI_CONTROL_FACTORY_PROFILE_BY_ID:
        return {"ok": False, "error": "Factory profiles cannot be deleted. Use reset instead."}
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        user_profiles = state.get("user_profiles") if isinstance(state.get("user_profiles"), dict) else {}
        if key not in user_profiles:
            return {"ok": False, "error": f"Unknown user profile id: {profile_id!r}"}
        user_profiles.pop(key, None)
        state["user_profiles"] = user_profiles
        profile_choice_overrides = state.get("profile_composable_overrides") if isinstance(state.get("profile_composable_overrides"), dict) else {}
        profile_choice_overrides.pop(key, None)
        state["profile_composable_overrides"] = profile_choice_overrides
        if state.get("active_profile_id") == key:
            state["active_profile_id"] = AI_CONTROL_DEFAULT_PROFILE_ID
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_reset_profile(runtime_root: Path | str, *, profile_id: str) -> dict[str, Any]:
    key = str(profile_id or "").strip()
    if key not in AI_CONTROL_FACTORY_PROFILE_BY_ID:
        return {"ok": False, "error": "Only factory profiles can be reset to factory settings."}
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        overrides = state.get("profile_overrides") if isinstance(state.get("profile_overrides"), dict) else {}
        overrides.pop(key, None)
        state["profile_overrides"] = overrides
        profile_choice_overrides = state.get("profile_composable_overrides") if isinstance(state.get("profile_composable_overrides"), dict) else {}
        profile_choice_overrides.pop(key, None)
        state["profile_composable_overrides"] = profile_choice_overrides
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_set_active_profile(runtime_root: Path | str, *, profile_id: str) -> dict[str, Any]:
    key = str(profile_id or "").strip()
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        composables = _merged_composable_map(state)
        profiles = _merged_profile_map(state, set(composables))
        if key not in profiles:
            return {"ok": False, "error": f"Unknown AI profile id: {profile_id!r}"}
        state["active_profile_id"] = key
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_save_composable(
    runtime_root: Path | str,
    *,
    composable_id: str | None = None,
    label: str,
    kind: str = "user_defined",
    description: str = "",
    prompt_text: str = "",
) -> dict[str, Any]:
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        existing = set(_merged_composable_map(state))
        key = str(composable_id or "").strip()
        if not key:
            key = _unique_id(existing, "user.composable", label)
        payload = {
            "label": str(label or key),
            "kind": str(kind or "user_defined"),
            "description": str(description or ""),
            "prompt_text": str(prompt_text or ""),
        }
        if key in AI_CONTROL_FACTORY_COMPOSABLE_BY_ID:
            overrides = state.setdefault("composable_overrides", {})
            overrides[key] = payload
        else:
            user_composables = state.setdefault("user_composables", {})
            user_composables[key] = payload
        if not state.get("active_profile_id"):
            state["active_profile_id"] = AI_CONTROL_DEFAULT_PROFILE_ID
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_delete_composable(runtime_root: Path | str, *, composable_id: str) -> dict[str, Any]:
    key = str(composable_id or "").strip()
    if key in AI_CONTROL_FACTORY_COMPOSABLE_BY_ID:
        return {"ok": False, "error": "Factory composables cannot be deleted. Use reset instead."}
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        user_composables = state.get("user_composables") if isinstance(state.get("user_composables"), dict) else {}
        if key not in user_composables:
            return {"ok": False, "error": f"Unknown user composable id: {composable_id!r}"}
        user_composables.pop(key, None)
        state["user_composables"] = user_composables
        user_profiles = state.get("user_profiles") if isinstance(state.get("user_profiles"), dict) else {}
        for profile in user_profiles.values():
            if isinstance(profile, dict):
                profile["enabled_composable_ids"] = [item for item in _normalize_id_list(profile.get("enabled_composable_ids")) if item != key]
        overrides = state.get("profile_overrides") if isinstance(state.get("profile_overrides"), dict) else {}
        for profile in overrides.values():
            if isinstance(profile, dict):
                profile["enabled_composable_ids"] = [item for item in _normalize_id_list(profile.get("enabled_composable_ids")) if item != key]
        profile_choice_overrides = state.get("profile_composable_overrides") if isinstance(state.get("profile_composable_overrides"), dict) else {}
        for profile_id, choice_overrides in list(profile_choice_overrides.items()):
            if isinstance(choice_overrides, dict):
                choice_overrides.pop(key, None)
                if not choice_overrides:
                    profile_choice_overrides.pop(profile_id, None)
        state["profile_composable_overrides"] = profile_choice_overrides
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_reset_composable(runtime_root: Path | str, *, composable_id: str) -> dict[str, Any]:
    key = str(composable_id or "").strip()
    if key not in AI_CONTROL_FACTORY_COMPOSABLE_BY_ID:
        return {"ok": False, "error": "Only factory composables can be reset to factory settings."}
    path = _profiles_path(runtime_root)
    with _ai_control_lock:
        state = _read_profiles_state(path)
        overrides = state.get("composable_overrides") if isinstance(state.get("composable_overrides"), dict) else {}
        overrides.pop(key, None)
        state["composable_overrides"] = overrides
        _write_profiles_state(path, state)
    return ai_control_profile_catalog(runtime_root)


def ai_control_handle_profile_action(runtime_root: Path | str, body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "").strip()
    if action == "save_profile":
        return ai_control_save_profile(
            runtime_root,
            profile_id=body.get("profile_id") or body.get("id"),
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
            enabled_composable_ids=body.get("enabled_composable_ids") or [],
            composable_overrides=body.get("composable_overrides") or {},
            set_active=bool(body.get("set_active")),
        )
    if action == "duplicate_profile":
        return ai_control_duplicate_profile(runtime_root, profile_id=str(body.get("profile_id") or body.get("id") or ""))
    if action == "delete_profile":
        return ai_control_delete_profile(runtime_root, profile_id=str(body.get("profile_id") or body.get("id") or ""))
    if action == "reset_profile":
        return ai_control_reset_profile(runtime_root, profile_id=str(body.get("profile_id") or body.get("id") or ""))
    if action == "set_active_profile":
        return ai_control_set_active_profile(runtime_root, profile_id=str(body.get("profile_id") or body.get("id") or ""))
    if action == "save_composable":
        return ai_control_save_composable(
            runtime_root,
            composable_id=body.get("composable_id") or body.get("id"),
            label=str(body.get("label") or ""),
            kind=str(body.get("kind") or "user_defined"),
            description=str(body.get("description") or ""),
            prompt_text=str(body.get("prompt_text") or ""),
        )
    if action == "delete_composable":
        return ai_control_delete_composable(runtime_root, composable_id=str(body.get("composable_id") or body.get("id") or ""))
    if action == "reset_composable":
        return ai_control_reset_composable(runtime_root, composable_id=str(body.get("composable_id") or body.get("id") or ""))
    return {"ok": False, "error": f"Unknown AI Control profile action: {action!r}"}


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
