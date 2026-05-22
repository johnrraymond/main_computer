from __future__ import annotations

"""RAG assisted thinking backend v3.

Version 3 keeps the v2 control plane and safety gates, but changes the activity
contract used by the chat app. RAG, thinking/model calls, and Docker verifier
subprocesses all emit into the AI activity view with facet tags that still let
the older RAG/Thinking/Docker filters work.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider
from main_computer.rag_assisted_thinking_v2 import (
    RAG_ASSISTED_THINKING_V2_VERSION,
    RagAssistedThinkingV2Policy,
    RagAssistedThinkingV2Result,
    WebSearchFn,
    run_rag_assisted_thinking_v2_request,
)


RAG_ASSISTED_THINKING_V3_VERSION = "3.0"
STREAM_ACTIVITY_BRIDGE_ATTR = "_main_computer_activity_stream_bridge"


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def default_run_id() -> str:
    return f"rag_assisted_thinking_v3_{utc_stamp()}"


def _preview(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part for part in text.split())
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "..."
    return text




def _rag_type_from_event(seed: str, tags: list[str], data: dict[str, Any]) -> str:
    for key in ("rag_type", "step", "stage", "phase"):
        value = str(data.get(key) or "").strip()
        if value:
            if key == "phase" and ("docker" in seed or "executor" in seed):
                return "docker_executor"
            return value.replace("-", "_")
    lowered = seed.lower()
    if "docker" in lowered or "executor" in lowered or "command_preview" in data:
        return "docker_executor"
    if "model-call" in tags or "model call" in lowered or "ollama" in lowered:
        return "model_call"
    if "context-inventory" in tags or "context_inventory" in lowered:
        return "context_inventory"
    if "context-brief" in tags or "context_brief" in lowered:
        return "context_brief"
    if "grounded-plan" in tags or "grounded_plan" in lowered:
        return "grounded_plan"
    if "retrieval" in tags or "retrieval" in lowered:
        return "retrieval"
    if "quality" in tags or "quality" in lowered:
        return "quality_gate"
    if "run" in tags:
        return "run"
    return "rag"


def _history_label(event: dict[str, Any], data: dict[str, Any]) -> str:
    labelled_keys = (
        ("system_prompt_preview", "system prompt"),
        ("input_messages_preview", "model input"),
        ("user_prompt_preview", "user prompt"),
        ("prompt_preview", "prompt"),
        ("latest_text", "model stream"),
        ("thinking_preview", "model thinking"),
        ("command_preview", "docker command"),
        ("script_preview", "script"),
        ("stdout_preview", "stdout"),
        ("stderr_preview", "stderr"),
        ("running_text", ""),
        ("ran_text", ""),
    )
    for key, label in labelled_keys:
        value = str(data.get(key) or "").strip()
        if value:
            preview = _preview(value, limit=500)
            return f"{label}: {preview}" if label else preview
    title = str(event.get("title") or "").strip()
    message = str(event.get("message") or "").strip()
    if title and message:
        return _preview(f"{title}: {message}", limit=500)
    return _preview(title or message, limit=500)


def _write_session_log(path_text: str, record: dict[str, Any]) -> None:
    if not path_text:
        return
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(record)
        payload.setdefault("ts", datetime.now(tz=timezone.utc).isoformat())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    except Exception:
        return

def _dedupe(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _message_history_payload(messages: Sequence[ChatMessage]) -> dict[str, Any]:
    system_prompts: list[str] = []
    user_prompts: list[str] = []
    previews: list[str] = []
    for index, message in enumerate(messages):
        role = str(getattr(message, "role", "") or "").strip() or "message"
        content = str(getattr(message, "content", "") or "")
        preview = _preview(content, limit=500)
        if preview:
            previews.append(f"{index + 1}:{role}: {preview}")
        if role == "system" and content:
            system_prompts.append(content)
        elif role == "user" and content:
            user_prompts.append(content)
    system_preview = _preview("\n\n".join(system_prompts), limit=900)
    user_preview = _preview("\n\n".join(user_prompts[-2:]), limit=700)
    return {
        "message_count": len(messages),
        "system_prompt_preview": system_preview,
        "user_prompt_preview": user_preview,
        "input_messages_preview": " | ".join(previews[:6]),
        "system_prompt_chars": sum(len(item) for item in system_prompts),
        "user_prompt_chars": sum(len(item) for item in user_prompts),
    }


@dataclass(frozen=True)
class RagAssistedThinkingV3Policy(RagAssistedThinkingV2Policy):
    """Runtime policy for v3.

    This intentionally matches v2's knobs so route callers can upgrade without
    inventing a second request shape.
    """

    activity_filter: str = "ai"


class UnifiedRagActivityBus:
    """Retag backend events so one AI pane shows RAG, thinking, and Docker work."""

    def __init__(
        self,
        bus: Any | None,
        *,
        run_id: str,
        log_file: str = "",
        activity_tag: str = "rag-assisted-thinking-v3",
    ) -> None:
        self.bus = bus
        self.run_id = run_id
        self.log_file = log_file
        self.activity_tag = str(activity_tag or "rag-assisted-thinking-v3").strip() or "rag-assisted-thinking-v3"
        self.rag_types_seen: list[str] = []

    def record(self, **event: Any) -> dict[str, Any]:
        if self.bus is None:
            return {}

        tags = list(event.get("tags") if isinstance(event.get("tags"), list) else [])
        title = str(event.get("title") or "")
        message = str(event.get("message") or "")
        source = str(event.get("source") or "")
        kind = str(event.get("kind") or "")
        seed = f"{source} {kind} {title} {message} {' '.join(tags)}".lower()

        unified_tags = ["ai", "rag", "thinking", "local-ai", self.activity_tag]
        if "docker" in seed or "executor" in seed or "subprocess" in seed:
            unified_tags.extend(["docker", "executor", "subprocess", "script", "command"])
        if "model" in seed or "ollama" in seed or "local-ai" in seed:
            unified_tags.extend(["model-call", "ollama"])
        if "retrieval" in seed or "context" in seed:
            unified_tags.extend(["retrieval", "context"])

        data = dict(event.get("data") if isinstance(event.get("data"), dict) else {})
        child_run_id = str(data.get("run_id") or "").strip()
        if child_run_id and child_run_id != self.run_id:
            data.setdefault("child_run_id", child_run_id)
        data["run_id"] = self.run_id
        data.setdefault("activity_filter", "ai")
        if self.log_file:
            data.setdefault("log_file", self.log_file)
        if "command_preview" in data:
            data.setdefault("script_preview", data.get("command_preview"))

        payload = dict(event)
        all_tags = _dedupe([*tags, *unified_tags])
        rag_type = _rag_type_from_event(seed, all_tags, data)
        data.setdefault("rag_type", rag_type)
        tool_plan = data.get("tool_plan") if isinstance(data.get("tool_plan"), dict) else {}
        extra_rag_types = [
            str(item or "").strip().replace("-", "_")
            for item in (tool_plan.get("allowed_tools") or [])
            if str(item or "").strip()
        ]
        for current_type in [rag_type, *extra_rag_types]:
            if current_type and current_type not in self.rag_types_seen:
                self.rag_types_seen.append(current_type)
        data["rag_types_seen"] = list(self.rag_types_seen)
        data.setdefault("history_label", _history_label(payload, data))
        if str(payload.get("status") or "") == "running":
            data.setdefault("running_text", data["history_label"])
        elif data.get("history_label"):
            data.setdefault("ran_text", data["history_label"])

        payload["tags"] = all_tags
        payload["data"] = data
        payload.setdefault("time_model", "parallel")
        recorded = self.bus.record(**payload)
        _write_session_log(
            self.log_file,
            {
                "event": "activity",
                "run_id": self.run_id,
                "title": recorded.get("title") or payload.get("title"),
                "source": recorded.get("source") or payload.get("source"),
                "kind": recorded.get("kind") or payload.get("kind"),
                "status": recorded.get("status") or payload.get("status"),
                "severity": recorded.get("severity") or payload.get("severity"),
                "message": recorded.get("message") or payload.get("message"),
                "tags": recorded.get("tags") or payload.get("tags"),
                "data": recorded.get("data") or payload.get("data"),
            },
        )
        return recorded


class ActivityAwareProvider:
    """Provider wrapper that emits safe model-call boundaries."""

    def __init__(self, provider: LLMProvider, activity: UnifiedRagActivityBus, *, run_id: str) -> None:
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_activity", activity)
        object.__setattr__(self, "_run_id", run_id)
        object.__setattr__(self, "name", str(getattr(provider, "name", provider.__class__.__name__)))
        object.__setattr__(self, "model", str(getattr(provider, "model", "")))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"think", "stream_callback"} and hasattr(self._provider, name):
            try:
                setattr(self._provider, name, value)
            except Exception:
                pass
        object.__setattr__(self, name, value)

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        input_chars = sum(len(str(message.content or "")) for message in messages)
        message_history = _message_history_payload(messages)
        self._activity.record(
            source="local-ai",
            kind="ai",
            time_model="parallel",
            severity="info",
            title="AI model input prepared",
            message=message_history.get("system_prompt_preview") or message_history.get("input_messages_preview") or f"{input_chars} input chars",
            status="running",
            tags=["ai", "rag", "thinking", "local-ai", "model-call", "prompt"],
            data={
                "run_id": self._run_id,
                "provider": self.name,
                "model": self.model,
                "input_chars": input_chars,
                "raw_thinking_exposed": False,
                "running_text": "model input prepared",
                "rag_type": "model_input",
                **message_history,
            },
        )
        self._activity.record(
            source="local-ai",
            kind="ai",
            time_model="parallel",
            severity="info",
            title="AI RAG thinking call started",
            message=f"{self.name}/{self.model}",
            status="running",
            tags=["ai", "rag", "thinking", "local-ai", "model-call"],
            data={
                "run_id": self._run_id,
                "provider": self.name,
                "model": self.model,
                "input_chars": input_chars,
                "raw_thinking_exposed": False,
                "running_text": f"local AI model call running: {self.name}/{self.model}",
                "rag_type": "model_call",
                **message_history,
            },
        )
        previous_callback = getattr(self._provider, "stream_callback", None)
        if hasattr(self._provider, "stream_callback"):
            try:
                def chained_stream_callback(event: dict[str, Any]) -> None:
                    bridge_handles_activity = bool(getattr(previous_callback, STREAM_ACTIVITY_BRIDGE_ATTR, False))
                    if not bridge_handles_activity:
                        self._stream_activity_event(event)
                    if callable(previous_callback):
                        try:
                            previous_callback(event)
                        except Exception:
                            pass

                setattr(self._provider, "stream_callback", chained_stream_callback)
            except Exception:
                pass
        try:
            response = self._provider.chat(messages)
        except Exception as exc:
            self._activity.record(
                source="local-ai",
                kind="ai",
                time_model="parallel",
                severity="error",
                title="AI RAG thinking call failed",
                message=_preview(exc),
                status="failed",
                tags=["ai", "rag", "thinking", "local-ai", "model-call", "fault"],
                data={
                    "run_id": self._run_id,
                    "error": _preview(exc),
                    "raw_thinking_exposed": False,
                    "ran_text": f"local AI model call failed: {_preview(exc)}",
                    "rag_type": "model_call",
                },
            )
            raise
        finally:
            if hasattr(self._provider, "stream_callback"):
                try:
                    setattr(self._provider, "stream_callback", previous_callback)
                except Exception:
                    pass

        self._activity.record(
            source="local-ai",
            kind="ai",
            time_model="parallel",
            severity="info",
            title="AI RAG thinking call completed",
            message=f"{getattr(response, 'provider', self.name)}/{getattr(response, 'model', self.model)}",
            status="completed",
            tags=["ai", "rag", "thinking", "local-ai", "model-call", "completed"],
            data={
                "run_id": self._run_id,
                "provider": getattr(response, "provider", self.name),
                "model": getattr(response, "model", self.model),
                "response_chars": len(str(getattr(response, "content", "") or "")),
                "raw_thinking_exposed": False,
                "ran_text": f"local AI model call completed: {getattr(response, 'provider', self.name)}/{getattr(response, 'model', self.model)}",
                "rag_type": "model_call",
            },
        )
        return response

    def _stream_activity_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "content_delta":
            message = _preview(event.get("content_preview") or event.get("delta"), limit=500)
            title = "Model text transmitted"
        elif event_type == "thinking_delta":
            message = _preview(event.get("thinking_preview") or event.get("delta"), limit=500)
            title = "Model thinking transmitted"
        elif event_type == "request_submitted":
            message = str(event.get("status_preview") or "Provider request submitted; waiting for first response.")
            title = "Model request submitted"
        elif event_type == "response_opened":
            message = str(event.get("status_preview") or "Provider response opened; waiting for streamed tokens.")
            title = "Model response opened"
        elif event_type == "request_waiting":
            message = str(event.get("status_preview") or "Provider request is still waiting for a response.")
            title = "Model request still waiting"
        elif event_type == "response_waiting":
            message = str(event.get("status_preview") or "Provider response is open; waiting for streamed tokens.")
            title = "Model response still waiting"
        elif event_type == "stream_error":
            message = str(event.get("error") or event.get("status_preview") or "Provider stream ended with an error.")
            title = "Model stream error"
        else:
            message = str(event.get("status_preview") or event_type or "Model stream status updated.")
            title = "Model stream status"
        self._activity.record(
            source="local-ai",
            kind="ai",
            time_model="parallel",
            severity="error" if event_type == "stream_error" else "info",
            title=title,
            message=message,
            status="failed" if event_type == "stream_error" else "running",
            tags=["ai", "rag", "thinking", "local-ai", "model-call", "stream"],
            data={
                "run_id": self._run_id,
                "provider": event.get("provider") or self.name,
                "model": event.get("model") or self.model,
                "latest_text": message if event_type in {"content_delta", "thinking_delta"} else "",
                "thinking_preview": message if event_type == "thinking_delta" else "",
                "status_preview": message if event_type not in {"content_delta", "thinking_delta"} else "",
                "content_chars": event.get("content_chars", 0),
                "thinking_chars": event.get("thinking_chars", 0),
                "elapsed_ms": event.get("elapsed_ms"),
                "think": event.get("think"),
                "thinking_enabled": event.get("thinking_enabled"),
                "thinking_state": event.get("thinking_state"),
                "thinking_status": event.get("thinking_status"),
                "stream_event_type": event.get("stream_event_type") or event_type,
                "error": event.get("error"),
                "terminal_fault_type": event.get("terminal_fault_type"),
                "partial_content_chars": event.get("partial_content_chars", event.get("content_chars", 0)),
                "partial_thinking_chars": event.get("partial_thinking_chars", event.get("thinking_chars", 0)),
                "partial_response_preview": event.get("partial_response_preview", ""),
                "stream_phase": event.get("stream_phase"),
                "stream_heartbeat": event.get("stream_heartbeat"),
                "waiting_reason": event.get("waiting_reason"),
                "transport": event.get("transport"),
                "provider_class": event.get("provider_class"),
                "base_url": event.get("base_url"),
                "stream": event.get("stream"),
                "options_keys": event.get("options_keys"),
                "message_count": event.get("message_count"),
                "request_bytes": event.get("request_bytes"),
                "timeout_s": event.get("timeout_s"),
                "diagnostic_label": event.get("diagnostic_label"),
                "diagnostic_run_id": event.get("diagnostic_run_id"),
                "raw_thinking_exposed": bool(event.get("raw_thinking_exposed")) if "raw_thinking_exposed" in event else False,
                "running_text": (
                    "model text streaming"
                    if event_type == "content_delta"
                    else message
                    if event_type == "thinking_delta"
                    else message
                ),
                "history_label": f"model stream: {message}" if event_type == "content_delta" else f"model thinking: {message}" if event_type == "thinking_delta" else message,
                "rag_type": "model_stream",
            },
        )


class RagAssistedThinkingV3Result:
    def __init__(self, delegate: RagAssistedThinkingV2Result) -> None:
        self._delegate = delegate
        self.version = RAG_ASSISTED_THINKING_V3_VERSION
        self.mode = "rag_assisted_thinking_v3"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def as_dict(self) -> dict[str, Any]:
        data = self._delegate.as_dict()
        data["version"] = self.version
        data["previous_version"] = RAG_ASSISTED_THINKING_V2_VERSION
        data["mode"] = self.mode
        data["activity_filter"] = "ai"
        return data


def run_rag_assisted_thinking_v3_request(
    *,
    prompt: str,
    repo_dir: Path | str = ".",
    provider: LLMProvider | None = None,
    rag_provider: LLMProvider | None = None,
    activity_bus: Any | None = None,
    queries: list[str] | str | None = None,
    upload_ids: list[str] | None = None,
    run_id: str | None = None,
    output_root: Path | str | None = None,
    policy: RagAssistedThinkingV3Policy | None = None,
    web_search_fn: WebSearchFn | None = None,
) -> RagAssistedThinkingV3Result:
    if provider is None:
        raise ValueError("provider is required for RAG-assisted thinking v3")

    run_id = run_id or default_run_id()
    policy = policy or RagAssistedThinkingV3Policy()
    repo_path = Path(repo_dir).resolve()
    base_output = Path(output_root).resolve() if output_root else repo_path / "diagnostics_output" / "rag_assisted_thinking_v2_runs"
    log_file = str(base_output / f"{run_id}.session.jsonl")
    _write_session_log(
        log_file,
        {
            "event": "prompt",
            "run_id": run_id,
            "mode": "rag_assisted_thinking_v3",
            "prompt": prompt,
            "queries": queries,
            "upload_ids": upload_ids,
            "repo_dir": str(repo_path),
            "provider": getattr(provider, "name", ""),
            "model": getattr(provider, "model", ""),
            "policy": policy.as_dict(),
        },
    )
    unified_activity = UnifiedRagActivityBus(activity_bus, run_id=run_id, log_file=log_file)
    wrapped_provider = ActivityAwareProvider(provider, unified_activity, run_id=run_id)
    wrapped_rag_provider = ActivityAwareProvider(rag_provider, unified_activity, run_id=run_id) if rag_provider is not None else None

    unified_activity.record(
        source="rag-assisted-thinking-v3",
        kind="ai",
        time_model="parallel",
        severity="info",
        title="AI RAG backend v3 started",
        message=_preview(prompt),
        status="running",
        tags=["ai", "rag", "thinking", "local-ai", "run", "v3"],
        data={
            "run_id": run_id,
            "mode": "rag_assisted_thinking_v3",
            "activity_filter": "ai",
            "log_file": log_file,
            "output_dir": str(base_output / run_id),
            "docker_enabled": bool(policy.docker_enabled),
            "raw_thinking_exposed": False,
            "running_text": "RAG-assisted thinking v3 backend running",
            "rag_type": "run",
        },
    )

    result = run_rag_assisted_thinking_v2_request(
        prompt=prompt,
        repo_dir=repo_dir,
        provider=wrapped_provider,
        rag_provider=wrapped_rag_provider,
        activity_bus=unified_activity,
        queries=queries,
        upload_ids=upload_ids,
        run_id=run_id,
        output_root=output_root,
        policy=policy,
        web_search_fn=web_search_fn,
    )

    unified_activity.record(
        source="rag-assisted-thinking-v3",
        kind="ai",
        time_model="parallel",
        severity="info" if result.ok else "error",
        title="AI RAG backend v3 finished",
        message=f"status={result.status}; proposed={len(result.proposed_paths)}; written={len(result.written_paths)}",
        status=result.status,
        tags=["ai", "rag", "thinking", "local-ai", "run", "v3", "completed" if result.ok else "failed"],
        data={
            "run_id": run_id,
            "mode": "rag_assisted_thinking_v3",
            "activity_filter": "ai",
            "ok": result.ok,
            "status": result.status,
            "output_dir": result.output_dir,
            "log_file": log_file,
            "proposed_paths": result.proposed_paths,
            "written_paths": result.written_paths,
            "docker_before_ok": result.docker_before.ok if result.docker_before else None,
            "docker_after_ok": result.docker_after.ok if result.docker_after else None,
            "raw_thinking_exposed": False,
            "ran_text": f"RAG-assisted thinking v3 finished with status={result.status}",
            "rag_type": "run",
        },
    )
    return RagAssistedThinkingV3Result(result)


run_rag_assisted_thinking_request_v3 = run_rag_assisted_thinking_v3_request


__all__ = [
    "RAG_ASSISTED_THINKING_V3_VERSION",
    "RagAssistedThinkingV3Policy",
    "RagAssistedThinkingV3Result",
    "default_run_id",
    "run_rag_assisted_thinking_request_v3",
    "run_rag_assisted_thinking_v3_request",
]
