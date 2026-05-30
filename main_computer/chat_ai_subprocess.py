from __future__ import annotations

"""Subprocess control plane for chat-console AI and RAG-AT requests.

The parent process owns HTTP, activity storage, and cancellation.  Each AI/RAG
request runs in a short-lived Python child process.  The child receives exactly
one JSON command on stdin, emits JSON status/activity/result messages on stdout,
logs noisy human-readable details to the supplied .log file, and exits.
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_trust_contract_chat import (
    chat_response_from_trust_result,
    run_trust_contract_chat_request,
)
from main_computer.router import MainComputer
from main_computer.chat_console import build_notebook_ai_messages
from main_computer.rag_assisted_thinking_v3 import (
    RagAssistedThinkingV3Policy,
    run_rag_assisted_thinking_v3_request,
)
from main_computer.rag_assisted_thinking_v4 import (
    RagAssistedThinkingV4Policy,
    run_rag_assisted_thinking_v4_request,
)


HEARTBEAT_INTERVAL_S = 2.0
HEARTBEAT_TIMEOUT_S = 30.0


class ChatAISubprocessError(RuntimeError):
    """Raised when the child process cannot complete a request."""


class ChatAISubprocessBusy(ChatAISubprocessError):
    """Raised when a chat thread already has a live child process."""


class ChatAISubprocessCancelled(ChatAISubprocessError):
    """Raised when a child process was killed by a stop request."""


_LOG_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _safe_text(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return repr(value)


def _selected_python_executable() -> str:
    """Return the interpreter child AI workers must use.

    Functional installed tests set MAIN_COMPUTER_FUNCTIONAL_PYTHON/MAIN_COMPUTER_PYTHON
    to the isolated temp interpreter.  Honor that interpreter explicitly so child
    model calls do not drift back into a user/debug/bootstrap Python.
    """

    for env_name in ("MAIN_COMPUTER_FUNCTIONAL_PYTHON", "MAIN_COMPUTER_PYTHON"):
        candidate = str(os.environ.get(env_name) or "").strip()
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path.resolve())
    return sys.executable


def _child_process_env(package_root: Path, python_executable: str) -> dict[str, str]:
    env = os.environ.copy()
    python_path = Path(python_executable).resolve()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PATH"] = str(python_path.parent) + os.pathsep + env.get("PATH", "")
    env["MAIN_COMPUTER_PYTHON"] = str(python_path)
    env["MAIN_COMPUTER_FUNCTIONAL_PYTHON"] = str(python_path)
    env["PYTHONPATH"] = str(package_root)
    env.pop("PYTHONHOME", None)
    env.pop("__PYVENV_LAUNCHER__", None)
    return env


def append_text_log(log_file: str | Path, entry_label: str, **fields_map: Any) -> None:
    """Append a noisy, regular text .log entry.

    The format is intentionally not JSONL.  Nested structures are pretty-printed
    as indented text so operators can search the file with normal log tools.
    """

    path = Path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(f"[{utc_now()}] {entry_label}\n")
                for key, value in fields_map.items():
                    if isinstance(value, (dict, list, tuple)):
                        pretty = json.dumps(value, indent=2, ensure_ascii=False, default=str)
                        handle.write(f"  {key}:\n")
                        for line in pretty.splitlines():
                            handle.write(f"    {line}\n")
                    else:
                        text = _safe_text(value)
                        if "\n" in text:
                            handle.write(f"  {key}:\n")
                            for line in text.splitlines():
                                handle.write(f"    {line}\n")
                        else:
                            handle.write(f"  {key}: {text}\n")
                handle.write("\n")
    except Exception:
        # Logging must never become the reason a child cannot report a failure.
        return


def config_to_payload(config: MainComputerConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields(MainComputerConfig):
        value = getattr(config, field.name)
        if isinstance(value, Path):
            payload[field.name] = str(value)
        else:
            payload[field.name] = value
    return payload


def config_from_payload(payload: dict[str, Any]) -> MainComputerConfig:
    data = dict(payload or {})
    for name in ("workspace", "executor_root"):
        if name in data and data[name] is not None:
            data[name] = Path(str(data[name]))
    return MainComputerConfig(**data)


def policy_to_payload(policy: Any) -> dict[str, Any]:
    payload = asdict(policy)
    if isinstance(payload.get("allowed_write_paths"), tuple):
        payload["allowed_write_paths"] = list(payload["allowed_write_paths"])
    return payload


def policy_from_payload(payload: dict[str, Any], *, mode: str = "rag_assisted_thinking_v4") -> RagAssistedThinkingV3Policy | RagAssistedThinkingV4Policy:
    data = dict(payload or {})
    if "allowed_write_paths" in data:
        data["allowed_write_paths"] = tuple(str(item) for item in (data.get("allowed_write_paths") or []))
    policy_cls = RagAssistedThinkingV4Policy if mode == "rag_assisted_thinking_v4" else RagAssistedThinkingV3Policy
    allowed = {field.name for field in fields(policy_cls)}
    return policy_cls(**{key: value for key, value in data.items() if key in allowed})


def _jsonable_response(response: ChatResponse) -> dict[str, Any]:
    return {
        "content": response.content,
        "provider": response.provider,
        "model": response.model,
        "metadata": response.metadata,
    }


def _preview(value: Any, *, limit: int = 700) -> str:
    text = " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split())
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _message_history_payload(messages: list[ChatMessage]) -> dict[str, Any]:
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
    return {
        "message_count": len(messages),
        "system_prompt_preview": _preview("\n\n".join(system_prompts), limit=900),
        "user_prompt_preview": _preview("\n\n".join(user_prompts[-2:]), limit=700),
        "input_messages_preview": " | ".join(previews[:6]),
        "system_prompt_chars": sum(len(item) for item in system_prompts),
        "user_prompt_chars": sum(len(item) for item in user_prompts),
    }


class WorkerStdout:
    def __init__(self, log_file: str | Path) -> None:
        self.log_file = str(log_file)
        self._write_lock = threading.Lock()

    def emit(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, default=str)
        # Heartbeats are control-plane liveness signals.  Keep them on stdout
        # for the parent monitor, but do not flood the human .log with normal
        # heartbeat traffic.  The parent logs only missed/resumed heartbeats.
        if str(message.get("type") or "") != "heartbeat":
            append_text_log(self.log_file, "worker stdout message", message=message)
        with self._write_lock:
            os.write(sys.stdout.fileno(), (line + "\n").encode("utf-8"))


class SubprocessActivityProxy:
    def __init__(self, stdout: WorkerStdout, *, log_file: str | Path, run_id: str = "") -> None:
        self.stdout = stdout
        self.log_file = str(log_file)
        self.run_id = run_id

    def record(self, **event: Any) -> dict[str, Any]:
        data = dict(event.get("data") if isinstance(event.get("data"), dict) else {})
        if self.run_id and not data.get("run_id"):
            data["run_id"] = self.run_id
        data.setdefault("activity_filter", "ai")
        # The subprocess session .log is the operator-facing source of truth.
        # Inner RAG components may still create JSONL diagnostics, but activity
        # cards should always point back to this human-readable log.
        data["log_file"] = self.log_file
        event["data"] = data
        append_text_log(self.log_file, "activity event", event=event)
        self.stdout.emit({"type": "activity", "event": event})
        return event

    def record_signal(self, name: str, fields_map: dict[str, Any] | None = None) -> dict[str, Any]:
        fields_map = dict(fields_map or {})
        return self.record(
            source="chat-ai-subprocess",
            kind="subprocess",
            time_model="parallel",
            severity="info",
            title=str(name),
            message=_preview(fields_map),
            status="running",
            tags=["ai", "chat-console", "subprocess", "signal"],
            data={"signal": name, **fields_map},
        )


STREAM_ACTIVITY_BRIDGE_ATTR = "_main_computer_activity_stream_bridge"


def _worker_stream_callback(
    stdout: WorkerStdout,
    *,
    log_file: str,
    run_id: str,
    provider: Any,
    source: str = "chat-console",
    tags: list[str] | None = None,
    rag_type: str = "model_stream",
):
    base_tags = list(tags or ["ai", "local-ai", "chat-console", "model-call", "stream", "thinking"])

    def on_stream(event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        provider_name = str(event.get("provider") or getattr(provider, "name", ""))
        model = str(event.get("model") or getattr(provider, "model", ""))
        if event_type == "content_delta":
            latest_text = " ".join(str(event.get("content_preview") or event.get("delta") or "").split())
            title = "Model text transmitted"
            message = latest_text[:500]
        elif event_type == "thinking_delta":
            latest_text = str(event.get("thinking_preview") or event.get("delta") or "")
            title = "Model thinking transmitted"
            message = latest_text[:500]
        elif event_type == "request_submitted":
            latest_text = ""
            title = "Model request submitted"
            message = str(event.get("status_preview") or "Provider request submitted; waiting for first response.")
        elif event_type == "response_opened":
            latest_text = ""
            title = "Model response opened"
            message = str(event.get("status_preview") or "Provider response opened; waiting for streamed tokens.")
        elif event_type == "request_waiting":
            latest_text = ""
            title = "Model request still waiting"
            message = str(event.get("status_preview") or "Provider request is still waiting for a response.")
        elif event_type == "response_waiting":
            latest_text = ""
            title = "Model response still waiting"
            message = str(event.get("status_preview") or "Provider response is open; waiting for streamed tokens.")
        else:
            latest_text = ""
            title = "Model stream status"
            message = str(event.get("status_preview") or event_type or "Model stream status updated.")
        activity = {
            "source": source,
            "kind": "ai",
            "time_model": "parallel",
            "severity": "info",
            "title": title,
            "message": message,
            "status": "running",
            "tags": base_tags,
            "data": {
                "run_id": run_id,
                "activity_filter": "ai",
                "provider": provider_name,
                "model": model,
                "log_file": log_file,
                "latest_text": latest_text[:500],
                "thinking_preview": latest_text[:500] if event_type == "thinking_delta" else "",
                "status_preview": message if event_type not in {"content_delta", "thinking_delta"} else "",
                "content_chars": event.get("content_chars", 0),
                "thinking_chars": event.get("thinking_chars", 0),
                "elapsed_ms": event.get("elapsed_ms"),
                "think": event.get("think"),
                "thinking_enabled": event.get("thinking_enabled"),
                "thinking_state": event.get("thinking_state"),
                "thinking_status": event.get("thinking_status"),
                "stream_event_type": event.get("stream_event_type") or event_type,
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
                    else latest_text[:500]
                    if event_type == "thinking_delta"
                    else message
                ),
                "history_label": f"model stream: {latest_text[:500]}" if event_type == "content_delta" else f"model thinking: {latest_text[:500]}" if event_type == "thinking_delta" else message,
                "rag_type": rag_type,
            },
        }
        append_text_log(log_file, "model stream callback", raw_stream_event=event, activity=activity)
        stdout.emit({"type": "activity", "event": activity})

    setattr(on_stream, STREAM_ACTIVITY_BRIDGE_ATTR, True)
    return on_stream


class ModelIOLoggingProvider:
    """Provider proxy that writes complete model input/output into the .log."""

    def __init__(self, provider: Any, *, log_file: str | Path, run_id: str, label: str) -> None:
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_log_file", str(log_file))
        object.__setattr__(self, "_run_id", str(run_id or ""))
        object.__setattr__(self, "_label", str(label or "model"))
        object.__setattr__(self, "name", str(getattr(provider, "name", provider.__class__.__name__)))
        object.__setattr__(self, "model", str(getattr(provider, "model", "")))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        try:
            setattr(self._provider, name, value)
        finally:
            object.__setattr__(self, name, value)

    @staticmethod
    def _message_payload(messages: list[ChatMessage] | tuple[ChatMessage, ...] | Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for index, message in enumerate(list(messages or [])):
            attachments = getattr(message, "attachments", None)
            payload.append(
                {
                    "index": index,
                    "role": getattr(message, "role", ""),
                    "content": getattr(message, "content", ""),
                    "content_chars": len(str(getattr(message, "content", "") or "")),
                    "attachments": attachments if isinstance(attachments, list) else [],
                    "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
                }
            )
        return payload

    def chat(self, messages: list[ChatMessage] | tuple[ChatMessage, ...] | Any) -> ChatResponse:
        message_payload = self._message_payload(messages)
        append_text_log(
            self._log_file,
            "model input to provider",
            run_id=self._run_id,
            label=self._label,
            provider=self.name,
            model=self.model,
            message_count=len(message_payload),
            input_chars=sum(int(item.get("content_chars") or 0) for item in message_payload),
            messages=message_payload,
        )
        started = time.monotonic()
        try:
            append_text_log(
                self._log_file,
                "model provider call starting",
                run_id=self._run_id,
                label=self._label,
                provider=self.name,
                model=self.model,
                provider_class=self._provider.__class__.__name__,
                callback_present=callable(getattr(self._provider, "stream_callback", None)),
                elapsed_s=0,
            )
            for attr, value in {
                "diagnostic_log_file": self._log_file,
                "diagnostic_run_id": self._run_id,
                "diagnostic_label": self._label,
            }.items():
                try:
                    setattr(self._provider, attr, value)
                except Exception:
                    pass
            response = self._provider.chat(messages)
        except BaseException as exc:
            append_text_log(
                self._log_file,
                "model provider exception",
                run_id=self._run_id,
                label=self._label,
                provider=self.name,
                model=self.model,
                elapsed_s=round(time.monotonic() - started, 3),
                error=repr(exc),
                traceback=traceback.format_exc(),
            )
            raise

        append_text_log(
            self._log_file,
            "model provider call returned",
            run_id=self._run_id,
            label=self._label,
            provider=getattr(response, "provider", self.name),
            model=getattr(response, "model", self.model),
            elapsed_s=round(time.monotonic() - started, 3),
        )
        append_text_log(
            self._log_file,
            "model output from provider",
            run_id=self._run_id,
            label=self._label,
            provider=getattr(response, "provider", self.name),
            model=getattr(response, "model", self.model),
            elapsed_s=round(time.monotonic() - started, 3),
            response_chars=len(str(getattr(response, "content", "") or "")),
            response=getattr(response, "content", ""),
            metadata=getattr(response, "metadata", {}),
        )
        return response



def _run_chat_console_ai_child(command: dict[str, Any], stdout: WorkerStdout, *, log_file: str) -> dict[str, Any]:
    run_id = str(command.get("run_id") or "").strip()
    source = str(command.get("source") or "")
    attachments = command.get("attachments") if isinstance(command.get("attachments"), list) else []
    scoped_context = command.get("scoped_context") if isinstance(command.get("scoped_context"), dict) else {}
    scoped_context_text = str(scoped_context.get("text") or "").strip()
    config = config_from_payload(command.get("config") if isinstance(command.get("config"), dict) else {})
    append_text_log(
        log_file,
        "chat console AI child starting",
        run_id=run_id,
        source_chars=len(source),
        source=source,
        attachments=attachments,
        scoped_context_enabled=bool(scoped_context_text),
        scoped_context_chars=len(scoped_context_text),
        scoped_context_label=str(scoped_context.get("label") or ""),
        config=config_to_payload(config) if config is not None else {},
    )

    computer = MainComputer.build(config)
    provider = getattr(computer, "provider", None)
    if provider is not None:
        provider = ModelIOLoggingProvider(provider, log_file=log_file, run_id=run_id, label="chat_console_ai")
    provider_name = getattr(provider, "name", "")
    model_name = getattr(provider, "model", "")
    stdout.emit(
        {
            "type": "activity",
            "event": {
                "source": "chat-console",
                "kind": "ai",
                "time_model": "parallel",
                "severity": "info",
                "title": "AI notebook subprocess started",
                "message": source[:500],
                "status": "running",
                "tags": ["ai", "local-ai", "chat-console", "model-call", "subprocess"],
                "data": {
                    "run_id": run_id,
                    "activity_filter": "ai",
                    "provider": provider_name,
                    "model": model_name,
                    "log_file": log_file,
                    "raw_thinking_exposed": False,
                    "running_text": "AI notebook subprocess running",
                    "rag_type": "chat_console_ai",
                },
            },
        }
    )

    if scoped_context_text:
        context_pack = None
        web_search_context, web_search_text = {"disabled": True, "reason": "mounted_editor_scope"}, ""
        messages = [
            ChatMessage(role="system", content=__import__("main_computer.router", fromlist=["SYSTEM_PROMPT"]).SYSTEM_PROMPT),
            ChatMessage(role="system", content=scoped_context_text),
            *build_notebook_ai_messages(source, attachments),
        ]
    else:
        context_pack = computer.context_pack(source)
        web_search_context, web_search_text = computer._web_search_context(source)
        messages = [
            ChatMessage(role="system", content=__import__("main_computer.router", fromlist=["SYSTEM_PROMPT"]).SYSTEM_PROMPT),
            ChatMessage(role="system", content=context_pack.text),
            *([ChatMessage(role="system", content=web_search_text)] if web_search_text else []),
            *build_notebook_ai_messages(source, attachments),
        ]
    message_history = _message_history_payload(messages)
    append_text_log(
        log_file,
        "chat console AI model input prepared",
        run_id=run_id,
        provider=provider_name,
        model=model_name,
        message_history=message_history,
        messages=[{"role": msg.role, "content": msg.content, "attachment_count": len(msg.attachments)} for msg in messages],
        web_search=web_search_context,
    )
    stdout.emit(
        {
            "type": "activity",
            "event": {
                "source": "chat-console",
                "kind": "ai",
                "time_model": "parallel",
                "severity": "info",
                "title": "AI notebook system prompt prepared",
                "message": message_history.get("system_prompt_preview") or message_history.get("input_messages_preview") or "model input prepared",
                "status": "running",
                "tags": ["ai", "local-ai", "chat-console", "model-call", "prompt", "thinking", "subprocess"],
                "data": {
                    "run_id": run_id,
                    "activity_filter": "ai",
                    "provider": provider_name,
                    "model": model_name,
                    "log_file": log_file,
                    "raw_thinking_exposed": False,
                    "running_text": "chat console model input prepared",
                    "rag_type": "model_input",
                    **message_history,
                },
            },
        }
    )

    previous_callback = getattr(provider, "stream_callback", None)
    if hasattr(provider, "stream_callback"):
        try:
            setattr(provider, "stream_callback", _worker_stream_callback(stdout, log_file=log_file, run_id=run_id, provider=provider))
            append_text_log(log_file, "installed worker stream callback", run_id=run_id)
        except Exception as exc:
            append_text_log(log_file, "failed to install stream callback", run_id=run_id, error=repr(exc))

    try:
        if provider is not None and hasattr(provider, "chat"):
            response = provider.chat(messages)
        else:
            response = computer.chat(source, context_pack=context_pack)
    finally:
        if hasattr(provider, "stream_callback"):
            try:
                setattr(provider, "stream_callback", previous_callback)
                append_text_log(log_file, "restored worker stream callback", run_id=run_id)
            except Exception as exc:
                append_text_log(log_file, "failed to restore stream callback", run_id=run_id, error=repr(exc))

    response_payload = _jsonable_response(response)
    append_text_log(
        log_file,
        "chat console AI response completed",
        run_id=run_id,
        provider=response.provider,
        model=response.model,
        response_chars=len(response.content),
        response=response.content,
        metadata=response.metadata,
    )
    stdout.emit(
        {
            "type": "activity",
            "event": {
                "source": "chat-console",
                "kind": "ai",
                "time_model": "parallel",
                "severity": "info",
                "title": "AI notebook subprocess completed",
                "message": f"{response.provider}/{response.model}",
                "status": "completed",
                "tags": ["ai", "local-ai", "chat-console", "model-call", "completed", "subprocess"],
                "data": {
                    "run_id": run_id,
                    "activity_filter": "ai",
                    "provider": response.provider,
                    "model": response.model,
                    "log_file": log_file,
                    "response_chars": len(response.content),
                    "raw_thinking_exposed": False,
                    "ran_text": f"AI notebook subprocess completed: {response.provider}/{response.model}",
                    "rag_type": "chat_console_ai",
                },
            },
        }
    )
    return {"response": response_payload}


def _run_rag_assisted_thinking_child(command: dict[str, Any], stdout: WorkerStdout, *, log_file: str) -> dict[str, Any]:
    mode = str(command.get("mode") or "rag_assisted_thinking_v4")
    is_v4 = mode == "rag_assisted_thinking_v4"
    mode_label = "rag_assisted_thinking_v4" if is_v4 else "rag_assisted_thinking_v3"
    run_id = str(command.get("run_id") or "").strip() or None
    prompt = str(command.get("prompt") or "")
    config = config_from_payload(command.get("config") if isinstance(command.get("config"), dict) else {})
    repo_dir = Path(str(command.get("repo_dir") or Path.cwd())).resolve()
    output_root = Path(str(command.get("output_root") or (repo_dir / "diagnostics_output" / f"{mode_label}_routes"))).resolve()
    queries = command.get("queries")
    policy = policy_from_payload(command.get("policy") if isinstance(command.get("policy"), dict) else {}, mode=mode_label)

    append_text_log(
        log_file,
        "RAG-AT child starting",
        run_id=run_id,
        prompt_chars=len(prompt),
        prompt=prompt,
        repo_dir=str(repo_dir),
        output_root=str(output_root),
        queries=queries,
        policy=policy_to_payload(policy),
        config=config_to_payload(config),
    )

    computer = MainComputer.build(config)
    provider = getattr(computer, "provider", None)
    if provider is not None:
        provider = ModelIOLoggingProvider(provider, log_file=log_file, run_id=run_id or "", label=mode_label)
    previous_callback = getattr(provider, "stream_callback", None)
    if provider is not None and hasattr(provider, "stream_callback"):
        try:
            subprocess_stream_callback = _worker_stream_callback(
                stdout,
                log_file=log_file,
                run_id=run_id or "",
                provider=provider,
                source="rag-assisted-thinking-v4" if is_v4 else "rag-assisted-thinking-v3",
                tags=["ai", "rag", "thinking", "local-ai", "model-call", "stream", "subprocess"],
                rag_type="model_stream",
            )
            if callable(previous_callback):
                def chained_subprocess_stream_callback(event: dict[str, Any]) -> None:
                    subprocess_stream_callback(event)
                    try:
                        previous_callback(event)
                    except Exception as exc:
                        append_text_log(log_file, "previous RAG stream callback failed", run_id=run_id, error=repr(exc))

                setattr(chained_subprocess_stream_callback, STREAM_ACTIVITY_BRIDGE_ATTR, True)
                setattr(provider, "stream_callback", chained_subprocess_stream_callback)
            else:
                setattr(provider, "stream_callback", subprocess_stream_callback)
            append_text_log(log_file, "installed RAG worker stream callback", run_id=run_id)
        except Exception as exc:
            append_text_log(log_file, "failed to install RAG worker stream callback", run_id=run_id, error=repr(exc))
    activity = SubprocessActivityProxy(stdout, log_file=log_file, run_id=run_id or "")
    try:
        runner = run_rag_assisted_thinking_v4_request if is_v4 else run_rag_assisted_thinking_v3_request
        result = runner(
            prompt=prompt,
            repo_dir=repo_dir,
            provider=provider,
            activity_bus=activity,
            queries=queries,
            run_id=run_id,
            output_root=output_root,
            policy=policy,
        )
    finally:
        if provider is not None and hasattr(provider, "stream_callback"):
            try:
                setattr(provider, "stream_callback", previous_callback)
                append_text_log(log_file, "restored RAG worker stream callback", run_id=run_id)
            except Exception as exc:
                append_text_log(log_file, "failed to restore RAG worker stream callback", run_id=run_id, error=repr(exc))
    result_payload = result.as_dict()
    append_text_log(
        log_file,
        "RAG-AT child completed",
        run_id=result.run_id,
        ok=result.ok,
        status=result.status,
        result=result_payload,
    )
    return {"result": result_payload}


def _workspace_context_evidence(context_pack: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    context_text = str(getattr(context_pack, "text", "") or "")
    if context_text.strip():
        evidence.append(
            {
                "evidence_id": "workspace_context",
                "source": "workspace_context_pack",
                "text": context_text,
                "produced_at_ms": 0,
                "expires_at_ms": 30_000,
                "trust": "workspace_context",
            }
        )
    for index, item in enumerate(getattr(context_pack, "evidence", ()) or ()):
        path = str(getattr(item, "path", "") or f"context_evidence_{index + 1}")
        reason = str(getattr(item, "reason", "") or "")
        if not path and not reason:
            continue
        evidence.append(
            {
                "evidence_id": f"workspace_file_{index + 1}",
                "source": path,
                "text": f"{path}\n{reason}".strip(),
                "produced_at_ms": 0,
                "expires_at_ms": 30_000,
                "trust": str(getattr(item, "kind", "") or "workspace_file"),
            }
        )
    return evidence


def _run_rag_trust_contract_chat_child(command: dict[str, Any], stdout: WorkerStdout, *, log_file: str) -> dict[str, Any]:
    run_id = str(command.get("run_id") or "").strip()
    prompt = str(command.get("prompt") or command.get("source") or "")
    messages = command.get("messages") if isinstance(command.get("messages"), list) else []
    explicit_evidence = command.get("evidence") if isinstance(command.get("evidence"), list) else []
    deadline_ms = int(command.get("deadline_ms") or 30_000)
    use_provider = bool(command.get("use_provider", True))
    include_workspace_context = bool(command.get("include_workspace_context", not explicit_evidence))
    config_payload = command.get("config") if isinstance(command.get("config"), dict) else {}
    config = config_from_payload(config_payload) if (use_provider or include_workspace_context) else None

    append_text_log(
        log_file,
        "trust-contract chat child starting",
        run_id=run_id,
        prompt_chars=len(prompt),
        prompt=prompt,
        messages=messages,
        explicit_evidence_count=len(explicit_evidence),
        deadline_ms=deadline_ms,
        use_provider=use_provider,
        include_workspace_context=include_workspace_context,
        config=config_to_payload(config) if config is not None else {},
    )

    provider: Any | None = None
    previous_callback: Any | None = None
    evidence = list(explicit_evidence)

    if use_provider or include_workspace_context:
        if config is None:
            raise ChatAISubprocessError("Trust-contract chat needs config when provider or workspace context is requested.")
        computer = MainComputer.build(config)
        if include_workspace_context:
            context_seed = prompt or "\n".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))
            context_pack = computer.context_pack(context_seed)
            evidence.extend(_workspace_context_evidence(context_pack))
        provider = getattr(computer, "provider", None) if use_provider else None
        if provider is not None:
            provider = ModelIOLoggingProvider(provider, log_file=log_file, run_id=run_id, label="rag_trust_contract_chat")
            previous_callback = getattr(provider, "stream_callback", None)
            if hasattr(provider, "stream_callback"):
                try:
                    setattr(
                        provider,
                        "stream_callback",
                        _worker_stream_callback(
                            stdout,
                            log_file=log_file,
                            run_id=run_id,
                            provider=provider,
                            source="rag-trust-contract-chat",
                            tags=["ai", "rag", "trust-contract", "local-ai", "model-call", "stream", "subprocess"],
                            rag_type="rag_trust_contract_chat_model_stream",
                        ),
                    )
                    append_text_log(log_file, "installed trust-contract worker stream callback", run_id=run_id)
                except Exception as exc:
                    append_text_log(log_file, "failed to install trust-contract worker stream callback", run_id=run_id, error=repr(exc))

    def emit_pipe_frame(frame: dict[str, Any]) -> None:
        append_text_log(log_file, "trust-contract pipe frame", run_id=run_id, frame=frame)
        stdout.emit(frame)

    try:
        result = run_trust_contract_chat_request(
            prompt=prompt,
            messages=messages,
            evidence=evidence,
            provider=provider,
            deadline_ms=deadline_ms,
            run_id=run_id,
            emit=emit_pipe_frame,
        )
    finally:
        if provider is not None and hasattr(provider, "stream_callback"):
            try:
                setattr(provider, "stream_callback", previous_callback)
                append_text_log(log_file, "restored trust-contract worker stream callback", run_id=run_id)
            except Exception as exc:
                append_text_log(log_file, "failed to restore trust-contract worker stream callback", run_id=run_id, error=repr(exc))

    result_payload = result.as_dict()
    response_payload = _jsonable_response(chat_response_from_trust_result(result))
    append_text_log(
        log_file,
        "trust-contract chat child completed",
        run_id=result.run_id,
        ok=result.ok,
        status=result.status,
        final_mode=result.final_mode,
        failures=result.failures,
        result=result_payload,
    )
    return {"result": result_payload, "response": response_payload}


def _heartbeat_loop(stdout: WorkerStdout, stop_event: threading.Event, *, run_id: str, log_file: str) -> None:
    count = 0
    append_text_log(
        log_file,
        "worker heartbeat monitor started",
        run_id=run_id,
        interval_s=HEARTBEAT_INTERVAL_S,
        miss_log_after_s=HEARTBEAT_INTERVAL_S * 2.0,
        pid=os.getpid(),
    )
    while not stop_event.wait(HEARTBEAT_INTERVAL_S):
        count += 1
        stdout.emit(
            {
                "type": "heartbeat",
                "run_id": run_id,
                "count": count,
                "pid": os.getpid(),
                "ts": utc_now(),
            }
        )


def run_worker(log_file: str | Path) -> int:
    log_file = str(log_file)
    stdout = WorkerStdout(log_file)
    stop_event = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    command: dict[str, Any] = {}
    run_id = ""
    try:
        append_text_log(log_file, "worker process boot", argv=sys.argv, pid=os.getpid(), cwd=os.getcwd())
        raw = sys.stdin.readline()
        append_text_log(log_file, "worker stdin received", raw=raw)
        if not raw:
            raise ChatAISubprocessError("No command was received on stdin.")
        command = json.loads(raw)
        run_id = str(command.get("run_id") or "")
        stdout.emit({"type": "started", "run_id": run_id, "pid": os.getpid(), "ts": utc_now()})
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(stdout, stop_event),
            kwargs={"run_id": run_id, "log_file": log_file},
            daemon=True,
        )
        heartbeat_thread.start()

        mode = str(command.get("mode") or "")
        append_text_log(log_file, "worker command dispatch", mode=mode, run_id=run_id, command=command)
        if mode == "chat_console_ai":
            payload = _run_chat_console_ai_child(command, stdout, log_file=log_file)
        elif mode in {"rag_assisted_thinking_v3", "rag_assisted_thinking_v4"}:
            payload = _run_rag_assisted_thinking_child(command, stdout, log_file=log_file)
        elif mode == "rag_trust_contract_chat":
            payload = _run_rag_trust_contract_chat_child(command, stdout, log_file=log_file)
        else:
            raise ChatAISubprocessError(f"Unsupported worker mode: {mode or '(empty)'}")
        stdout.emit({"type": "result", "ok": True, "run_id": run_id, "payload": payload, "ts": utc_now()})
        append_text_log(log_file, "worker result emitted", run_id=run_id, payload=payload)
        return 0
    except BaseException as exc:
        tb = traceback.format_exc()
        append_text_log(log_file, "worker exception", run_id=run_id, error=repr(exc), traceback=tb, command=command)
        try:
            stdout.emit({"type": "result", "ok": False, "run_id": run_id, "error": str(exc), "traceback": tb, "ts": utc_now()})
        except Exception:
            pass
        return 1
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
        append_text_log(log_file, "worker process exit", run_id=run_id, pid=os.getpid())


class ActiveChatAIProcess:
    def __init__(self, *, thread_id: str, run_id: str, process: subprocess.Popen[str], log_file: Path, started_at: float) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.process = process
        self.log_file = log_file
        self.started_at = started_at
        self.stop_requested = False


class ChatAISubprocessManager:
    def __init__(self, *, heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S) -> None:
        self.heartbeat_timeout_s = max(5.0, float(heartbeat_timeout_s))
        self._lock = threading.RLock()
        self._active_by_thread: dict[str, ActiveChatAIProcess] = {}
        self._results_by_run_id: dict[str, dict[str, Any]] = {}
        self._result_order: list[str] = []
        self._result_limit = 80

    def _remember_run_result(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id:
            return payload
        snapshot = {
            **(self._results_by_run_id.get(run_id) or {}),
            **payload,
            "run_id": run_id,
            "updated_at": utc_now(),
        }
        if run_id in self._result_order:
            self._result_order.remove(run_id)
        self._result_order.append(run_id)
        while len(self._result_order) > self._result_limit:
            old_run_id = self._result_order.pop(0)
            self._results_by_run_id.pop(old_run_id, None)
        self._results_by_run_id[run_id] = snapshot
        return snapshot

    def remember_route_result(self, *, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Store a completed route payload so the browser can reconnect by run_id.

        The HTTP request that started a model call can disconnect while the
        subprocess continues emitting out-of-band activity.  Route handlers call
        this before writing the final response, allowing a later polling request
        to recover the completed output_cell from the subprocess result.
        """
        with self._lock:
            return dict(self._remember_run_result(run_id, {
                **(payload if isinstance(payload, dict) else {}),
                "found": True,
                "status": str((payload or {}).get("status") or "completed"),
                "completed": True,
                "running": False,
            }))

    def run_result(self, *, run_id: str = "", thread_id: str = "") -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        thread_id = str(thread_id or "").strip()
        with self._lock:
            if run_id and run_id in self._results_by_run_id:
                return {"ok": True, "found": True, **dict(self._results_by_run_id[run_id])}
            active: ActiveChatAIProcess | None = None
            for candidate in self._active_by_thread.values():
                if run_id and candidate.run_id == run_id:
                    active = candidate
                    break
                if thread_id and candidate.thread_id == thread_id:
                    active = candidate
                    break
            if active is not None and active.process.poll() is None:
                return {
                    "ok": True,
                    "found": True,
                    "run_id": active.run_id,
                    "thread_id": active.thread_id,
                    "status": "running",
                    "running": True,
                    "completed": False,
                    "pid": active.process.pid,
                    "log_file": str(active.log_file),
                    "started_at": active.started_at,
                    "updated_at": utc_now(),
                }
            if run_id:
                return {"ok": True, "found": False, "run_id": run_id, "thread_id": thread_id, "status": "missing", "running": False, "completed": False}
            return {"ok": True, "found": False, "thread_id": thread_id, "status": "missing", "running": False, "completed": False}

    def _active_for(self, thread_id: str) -> ActiveChatAIProcess | None:
        with self._lock:
            active = self._active_by_thread.get(thread_id)
            if active is not None and active.process.poll() is None:
                return active
            if active is not None:
                self._active_by_thread.pop(thread_id, None)
            return None

    def is_active(self, thread_id: str) -> bool:
        return self._active_for(thread_id) is not None

    def _live_active_snapshots_locked(self) -> list[dict[str, Any]]:
        """Return live AI subprocess snapshots and prune finished entries.

        This is the first backend capacity primitive for remote-overflow routing:
        callers can tell whether local AI is already busy without trying to start
        another model run and catching a conflict.
        """

        now = time.monotonic()
        snapshots: list[dict[str, Any]] = []
        stale_thread_ids: list[str] = []
        for thread_id, active in list(self._active_by_thread.items()):
            if active.process.poll() is None:
                snapshots.append(
                    {
                        "thread_id": active.thread_id,
                        "run_id": active.run_id,
                        "pid": active.process.pid,
                        "log_file": str(active.log_file),
                        "started_at_monotonic": active.started_at,
                        "age_s": round(max(0.0, now - active.started_at), 3),
                        "stop_requested": bool(active.stop_requested),
                    }
                )
            else:
                stale_thread_ids.append(thread_id)
        for thread_id in stale_thread_ids:
            self._active_by_thread.pop(thread_id, None)
        snapshots.sort(key=lambda item: (str(item.get("thread_id") or ""), str(item.get("run_id") or "")))
        return snapshots

    def active_runs_snapshot(self) -> dict[str, Any]:
        """Return live local AI/RAG subprocesses in a JSON-safe shape."""

        with self._lock:
            active_runs = self._live_active_snapshots_locked()
            return {
                "ok": True,
                "scope": "local-ai",
                "active_run_count": len(active_runs),
                "active_thread_ids": [str(item.get("thread_id") or "") for item in active_runs],
                "active_runs": active_runs,
                "updated_at": utc_now(),
            }

    def local_ai_capacity_snapshot(
        self,
        *,
        thread_id: str = "",
        max_local_concurrency: int = 1,
    ) -> dict[str, Any]:
        """Describe whether local AI can accept another request now.

        The shape intentionally mirrors the future remote-overflow card pipeline:
        it returns a machine-readable reason code, user-visible message, and
        diagnostic cards. Later credit/hub checks can append their own cards
        without changing this local-capacity contract.
        """

        clean_thread_id = str(thread_id or "").strip()
        try:
            capacity_limit = int(max_local_concurrency)
        except (TypeError, ValueError):
            capacity_limit = 1
        capacity_limit = max(1, capacity_limit)

        with self._lock:
            active_runs = self._live_active_snapshots_locked()

        matching_thread_active = any(str(item.get("thread_id") or "") == clean_thread_id for item in active_runs) if clean_thread_id else False
        active_count = len(active_runs)
        if matching_thread_active:
            busy = True
            reason_code = "thread_busy"
            message = f"Local AI is already running for chat thread {clean_thread_id}."
        elif active_count >= capacity_limit:
            busy = True
            reason_code = "local_concurrency_exhausted"
            message = f"Local AI capacity is busy: {active_count} active run(s) for a limit of {capacity_limit}."
        else:
            busy = False
            reason_code = "local_ai_available"
            message = "Local AI capacity is available now."

        card_status = "blocked" if busy else "pass"
        return {
            "ok": True,
            "scope": "local-ai",
            "available_now": not busy,
            "busy": busy,
            "reason_code": reason_code,
            "user_message": message,
            "thread_id": clean_thread_id,
            "active_run_count": active_count,
            "max_local_concurrency": capacity_limit,
            "active_thread_ids": [str(item.get("thread_id") or "") for item in active_runs],
            "active_runs": active_runs,
            "cards": [
                {
                    "key": "local_capacity",
                    "title": "Local AI capacity",
                    "status": card_status,
                    "message": message,
                    "details": {
                        "thread_id": clean_thread_id,
                        "active_run_count": active_count,
                        "max_local_concurrency": capacity_limit,
                        "reason_code": reason_code,
                    },
                }
            ],
            "updated_at": utc_now(),
        }

    def stop(self, *, thread_id: str = "", run_id: str = "", reason: str = "ui-stop") -> dict[str, Any]:
        with self._lock:
            candidates = list(self._active_by_thread.items())
            active: ActiveChatAIProcess | None = None
            for candidate_thread_id, candidate in candidates:
                if thread_id and candidate_thread_id == thread_id:
                    active = candidate
                    break
                if run_id and candidate.run_id == run_id:
                    active = candidate
                    break
            if active is None or active.process.poll() is not None:
                return {"ok": False, "stopped": False, "reason": "not-running", "thread_id": thread_id, "run_id": run_id}
            active.stop_requested = True

        append_text_log(active.log_file, "parent stop requested", thread_id=active.thread_id, run_id=active.run_id, reason=reason, pid=active.process.pid)
        try:
            active.process.terminate()
        except Exception as exc:
            append_text_log(active.log_file, "parent terminate failed", error=repr(exc))
        try:
            active.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            append_text_log(active.log_file, "parent kill after terminate timeout", pid=active.process.pid)
            try:
                active.process.kill()
            except Exception as exc:
                append_text_log(active.log_file, "parent kill failed", error=repr(exc))
            try:
                active.process.wait(timeout=2.0)
            except Exception as exc:
                append_text_log(active.log_file, "parent wait after kill failed", error=repr(exc))
        return {"ok": True, "stopped": True, "thread_id": active.thread_id, "run_id": active.run_id, "pid": active.process.pid}

    def run(self, *, command: dict[str, Any], thread_id: str, log_file: Path, activity_bus: Any | None, cwd: Path | str) -> dict[str, Any]:
        thread_id = str(thread_id or "default-chat-thread").strip() or "default-chat-thread"
        run_id = str(command.get("run_id") or "").strip() or f"chat_ai_{int(time.time() * 1000)}"
        command = {**command, "run_id": run_id, "thread_id": thread_id}
        log_file = Path(log_file).resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)

        active = self._active_for(thread_id)
        if active is not None:
            raise ChatAISubprocessBusy(f"Chat thread {thread_id} already has active AI/RAG subprocess {active.run_id}.")

        package_root = Path(__file__).resolve().parents[1]
        python_executable = _selected_python_executable()
        env = _child_process_env(package_root, python_executable)
        process_cwd = Path(cwd).resolve()

        args = [python_executable, "-u", "-m", "main_computer.chat_ai_subprocess", "--worker", "--log-file", str(log_file)]
        append_text_log(
            log_file,
            "parent spawning worker",
            thread_id=thread_id,
            run_id=run_id,
            args=args,
            cwd=str(process_cwd),
            python_executable=python_executable,
            package_root=str(package_root),
            command=command,
        )
        process = subprocess.Popen(
            args,
            cwd=str(process_cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            env=env,
        )
        active_process = ActiveChatAIProcess(
            thread_id=thread_id,
            run_id=run_id,
            process=process,
            log_file=log_file,
            started_at=time.monotonic(),
        )
        with self._lock:
            self._active_by_thread[thread_id] = active_process
            self._remember_run_result(run_id, {
                "ok": True,
                "found": True,
                "status": "running",
                "running": True,
                "completed": False,
                "thread_id": thread_id,
                "pid": process.pid,
                "log_file": str(log_file),
                "started_at": active_process.started_at,
            })

        stdout_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        stderr_queue: queue.Queue[tuple[str, str]] = queue.Queue()

        def reader(pipe: Any, out_queue: queue.Queue[tuple[str, str]], name: str) -> None:
            try:
                for raw_line in iter(pipe.readline, b""):
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                    out_queue.put((name, line))
            except Exception as exc:
                out_queue.put((name, f"[reader-error] {exc!r}"))
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        stdout_thread = threading.Thread(target=reader, args=(process.stdout, stdout_queue, "stdout"), daemon=True)
        stderr_thread = threading.Thread(target=reader, args=(process.stderr, stderr_queue, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            assert process.stdin is not None
            line = json.dumps(command, ensure_ascii=False, default=str)
            append_text_log(log_file, "parent writing command to stdin", run_id=run_id, bytes=len(line.encode("utf-8")), command=command)
            process.stdin.write((line + "\n").encode("utf-8"))
            process.stdin.flush()
            process.stdin.close()
        except Exception as exc:
            append_text_log(log_file, "parent failed writing command", run_id=run_id, error=repr(exc))
            self.stop(thread_id=thread_id, run_id=run_id, reason="stdin-write-failed")
            raise ChatAISubprocessError(f"Failed to write AI subprocess command: {exc}") from exc

        result_message: dict[str, Any] | None = None
        last_message = time.monotonic()
        last_heartbeat_at = last_message
        last_heartbeat_count = 0
        heartbeat_miss_logged = False
        heartbeat_miss_after_s = HEARTBEAT_INTERVAL_S * 2.0

        try:
            while True:
                drained = False

                while True:
                    try:
                        _, line = stdout_queue.get_nowait()
                    except queue.Empty:
                        break
                    drained = True
                    last_message = time.monotonic()
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        append_text_log(log_file, "parent received non-json stdout", line=line)
                        continue

                    msg_type = str(message.get("type") or "")
                    if msg_type != "heartbeat":
                        append_text_log(log_file, "parent received stdout", line=line)
                    if msg_type == "activity":
                        event = message.get("event")
                        if isinstance(event, dict) and activity_bus is not None:
                            try:
                                activity_bus.record(**event)
                            except Exception as exc:
                                append_text_log(log_file, "parent activity record failed", error=repr(exc), event=event)
                    elif msg_type == "heartbeat":
                        now = time.monotonic()
                        previous_heartbeat_at = last_heartbeat_at
                        last_heartbeat_at = now
                        last_heartbeat_count = int(message.get("count") or last_heartbeat_count)
                        if heartbeat_miss_logged:
                            append_text_log(
                                log_file,
                                "parent heartbeat resumed",
                                run_id=run_id,
                                thread_id=thread_id,
                                heartbeat_count=last_heartbeat_count,
                                pid=process.pid,
                                gap_s=round(now - previous_heartbeat_at, 3),
                            )
                        heartbeat_miss_logged = False
                    elif msg_type == "result":
                        result_message = message
                    else:
                        append_text_log(log_file, "parent received control message", message=message)

                while True:
                    try:
                        _, line = stderr_queue.get_nowait()
                    except queue.Empty:
                        break
                    drained = True
                    last_message = time.monotonic()
                    append_text_log(log_file, "parent received stderr", line=line)
                    if activity_bus is not None and line.strip():
                        try:
                            activity_bus.record(
                                source="chat-ai-subprocess",
                                kind="subprocess",
                                time_model="parallel",
                                severity="warn",
                                title="AI subprocess stderr",
                                message=line[:1000],
                                status="running",
                                tags=["ai", "chat-console", "subprocess", "stderr"],
                                data={
                                    "run_id": run_id,
                                    "thread_id": thread_id,
                                    "activity_filter": "ai",
                                    "pid": process.pid,
                                    "stderr": line,
                                    "log_file": str(log_file),
                                    "rag_type": "subprocess_stderr",
                                },
                            )
                        except Exception as exc:
                            append_text_log(log_file, "parent stderr activity failed", error=repr(exc), stderr=line)

                if result_message is not None and process.poll() is not None:
                    break

                if process.poll() is not None and not drained:
                    # One last drain pass happens next iteration; if queues are empty now, break.
                    if stdout_queue.empty() and stderr_queue.empty():
                        break

                now = time.monotonic()
                heartbeat_gap_s = now - last_heartbeat_at
                if process.poll() is None and heartbeat_gap_s >= heartbeat_miss_after_s and not heartbeat_miss_logged:
                    heartbeat_miss_logged = True
                    append_text_log(
                        log_file,
                        "parent missed heartbeat deadline",
                        run_id=run_id,
                        thread_id=thread_id,
                        heartbeat_count=last_heartbeat_count,
                        pid=process.pid,
                        seconds_since_heartbeat=round(heartbeat_gap_s, 3),
                        expected_interval_s=HEARTBEAT_INTERVAL_S,
                        miss_log_after_s=heartbeat_miss_after_s,
                    )
                    if activity_bus is not None:
                        try:
                            activity_bus.record(
                                source="chat-ai-subprocess",
                                kind="heartbeat",
                                time_model="time_series",
                                severity="warn",
                                title="AI subprocess heartbeat missing",
                                message=f"run {run_id} has not produced a heartbeat for {heartbeat_gap_s:.1f}s",
                                status="running",
                                tags=["ai", "chat-console", "subprocess", "heartbeat", "warn"],
                                data={
                                    "run_id": run_id,
                                    "thread_id": thread_id,
                                    "activity_filter": "ai",
                                    "pid": process.pid,
                                    "heartbeat_count": last_heartbeat_count,
                                    "seconds_since_heartbeat": round(heartbeat_gap_s, 3),
                                    "expected_interval_s": HEARTBEAT_INTERVAL_S,
                                    "log_file": str(log_file),
                                    "running_text": "AI subprocess heartbeat missing",
                                    "rag_type": "subprocess_heartbeat_missing",
                                },
                            )
                        except Exception as exc:
                            append_text_log(log_file, "parent missed heartbeat activity failed", error=repr(exc))

                if process.poll() is None and heartbeat_gap_s > self.heartbeat_timeout_s:
                    append_text_log(
                        log_file,
                        "parent heartbeat timeout",
                        run_id=run_id,
                        thread_id=thread_id,
                        seconds_since_heartbeat=round(heartbeat_gap_s, 3),
                        timeout_s=self.heartbeat_timeout_s,
                        last_heartbeat_count=last_heartbeat_count,
                    )
                    self.stop(thread_id=thread_id, run_id=run_id, reason="heartbeat-timeout")
                    raise ChatAISubprocessError(f"AI subprocess {run_id} stopped responding to heartbeat status.")

                time.sleep(0.05)

            returncode = process.wait(timeout=2.0)
            append_text_log(log_file, "parent worker exited", run_id=run_id, returncode=returncode, result_message=result_message)
            if active_process.stop_requested:
                with self._lock:
                    self._remember_run_result(run_id, {
                        "ok": False,
                        "found": True,
                        "status": "cancelled",
                        "running": False,
                        "completed": False,
                        "thread_id": thread_id,
                        "log_file": str(log_file),
                        "cancelled": True,
                        "error": f"AI subprocess {run_id} was cancelled.",
                    })
                raise ChatAISubprocessCancelled(f"AI subprocess {run_id} was cancelled.")
            if result_message is None:
                with self._lock:
                    self._remember_run_result(run_id, {
                        "ok": False,
                        "found": True,
                        "status": "failed",
                        "running": False,
                        "completed": False,
                        "thread_id": thread_id,
                        "log_file": str(log_file),
                        "error": f"AI subprocess {run_id} exited without a result. See {log_file}.",
                    })
                raise ChatAISubprocessError(f"AI subprocess {run_id} exited without a result. See {log_file}.")
            if not result_message.get("ok"):
                error_text = str(result_message.get("error") or f"AI subprocess {run_id} failed.")
                with self._lock:
                    self._remember_run_result(run_id, {
                        "ok": False,
                        "found": True,
                        "status": "failed",
                        "running": False,
                        "completed": False,
                        "thread_id": thread_id,
                        "log_file": str(log_file),
                        "error": error_text,
                    })
                raise ChatAISubprocessError(error_text)
            payload = result_message.get("payload") if isinstance(result_message.get("payload"), dict) else {}
            with self._lock:
                self._remember_run_result(run_id, {
                    "ok": True,
                    "found": True,
                    "status": "subprocess-completed",
                    "running": False,
                    "completed": False,
                    "thread_id": thread_id,
                    "log_file": str(log_file),
                    "payload": payload,
                })
            return payload
        finally:
            with self._lock:
                current = self._active_by_thread.get(thread_id)
                if current is active_process:
                    self._active_by_thread.pop(thread_id, None)
            append_text_log(log_file, "parent run cleanup", run_id=run_id, thread_id=thread_id, returncode=process.poll())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chat AI/RAG subprocess worker.")
    parser.add_argument("--worker", action="store_true", help="Run a single stdin/stdout worker command.")
    parser.add_argument("--log-file", required=True, help="Human-readable .log file for all worker output.")
    args = parser.parse_args(argv)
    if not args.worker:
        parser.error("--worker is required")
    return run_worker(args.log_file)


if __name__ == "__main__":
    raise SystemExit(main())
