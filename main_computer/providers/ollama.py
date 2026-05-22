from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys
import threading
import time
import traceback
from urllib.error import URLError
from urllib.request import Request, urlopen

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.base import LLMProvider

_DIAGNOSTIC_RECORD_MAX_BYTES = 4096
_DIAGNOSTIC_ELLIPSIS = "..."
_PARTIAL_RESPONSE_PREVIEW_CHARS = 1200


class OllamaStreamTerminalError(RuntimeError):
    """Terminal provider stream failure with partial generation counters."""

    def __init__(
        self,
        message: str,
        *,
        terminal_fault_type: str,
        partial_content: str = "",
        partial_thinking: str = "",
    ) -> None:
        super().__init__(message)
        self.terminal_fault_type = terminal_fault_type
        self.terminal_fault_message = message
        self.partial_content = partial_content
        self.partial_thinking = partial_thinking
        self.partial_content_chars = len(partial_content)
        self.partial_thinking_chars = len(partial_thinking)
        self.partial_response_preview = partial_content[:_PARTIAL_RESPONSE_PREVIEW_CHARS]


def describe_thinking_setting(value: Any) -> dict[str, Any]:
    """Return safe, user-visible metadata for the Ollama think setting."""

    raw = value
    if isinstance(raw, bool):
        return {
            "think": raw,
            "thinking_enabled": raw,
            "thinking_state": "on" if raw else "off",
            "thinking_status": "Thinking: on." if raw else "Thinking: off.",
        }

    if raw is None:
        return {
            "think": None,
            "thinking_enabled": None,
            "thinking_state": "unspecified",
            "thinking_status": "Thinking: unspecified by client.",
        }

    text = str(raw).strip()
    lowered = text.lower()
    if not text or lowered in {"none", "null", "default"}:
        return {
            "think": raw,
            "thinking_enabled": None,
            "thinking_state": "unspecified",
            "thinking_status": "Thinking: unspecified by client.",
        }
    if lowered in {"0", "false", "no", "off"}:
        return {
            "think": raw,
            "thinking_enabled": False,
            "thinking_state": "off",
            "thinking_status": "Thinking: off.",
        }
    if lowered in {"1", "true", "yes", "on"}:
        return {
            "think": raw,
            "thinking_enabled": True,
            "thinking_state": "on",
            "thinking_status": "Thinking: on.",
        }

    return {
        "think": raw,
        "thinking_enabled": True,
        "thinking_state": text,
        "thinking_status": f"Thinking: on (mode: {text}).",
    }


def append_thinking_status(status_preview: str, thinking_metadata: dict[str, Any]) -> str:
    """Attach the think state to provider status text."""

    base = str(status_preview or "").strip()
    thinking_status = str(thinking_metadata.get("thinking_status") or "").strip()
    if not thinking_status:
        return base
    if thinking_status.lower() in base.lower():
        return base
    return f"{base} {thinking_status}".strip()


def parse_ollama_think_choice(value: Any) -> bool | str | None:
    """Normalize CLI/config values for Ollama's top-level ``think`` field.

    ``None`` and ``"default"`` mean "not explicitly enabled by the caller"; callers
    that require visible final text can then apply their own safe default.
    """

    if isinstance(value, bool) or value is None:
        return value

    text = str(value).strip()
    lowered = text.lower()
    if not text or lowered in {"default", "none", "null", "auto", "unspecified"}:
        return None
    if lowered in {"0", "false", "no", "off"}:
        return False
    if lowered in {"1", "true", "yes", "on"}:
        return True
    return text


def resolve_ollama_think_choice(
    value: Any,
    *,
    default_think: bool | str = False,
) -> tuple[bool | str, dict[str, Any]]:
    """Resolve an Ollama think setting for provider calls that need visible text.

    The provider default is intentionally non-thinking.  Thinking remains fully
    supported when the caller explicitly passes ``True`` or a supported thinking
    level, but omitted/default thinking is sent as top-level ``think: false`` so
    Ollama cannot silently choose a hidden-thinking-only mode.
    """

    parsed = parse_ollama_think_choice(value)
    default_applied = parsed is None
    resolved: bool | str = default_think if default_applied else parsed
    metadata = describe_thinking_setting(resolved)
    metadata.update(
        {
            "think_source": "default_non_thinking" if default_applied else "explicit",
            "think_default_applied": default_applied,
            "think_policy": "ollama_provider_defaults_to_non_thinking_unless_enabled",
        }
    )
    return resolved, metadata


def prepare_ollama_generate_payload(
    payload: dict[str, Any],
    *,
    think: bool | str | None = None,
    default_think: bool | str = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a /api/generate payload with an explicit top-level think policy.

    Thinking-capable Ollama models can default into hidden/thinking-only output
    when ``think`` is omitted.  Generate callers that need visible final text
    should not leave that mode ambiguous: omitted thinking defaults to off, while
    an explicit caller/payload value is preserved.
    """

    prepared = dict(payload)
    explicit_think = parse_ollama_think_choice(think)
    payload_has_think = "think" in prepared and prepared.get("think") is not None

    if explicit_think is not None:
        prepared["think"] = explicit_think
        think_source = "explicit_argument"
        default_applied = False
    elif payload_has_think:
        prepared["think"] = parse_ollama_think_choice(prepared.get("think"))
        think_source = "payload"
        default_applied = False
    else:
        prepared["think"] = default_think
        think_source = "default_non_thinking"
        default_applied = True

    metadata = describe_thinking_setting(prepared.get("think"))
    metadata.update(
        {
            "think_source": think_source,
            "think_default_applied": default_applied,
            "think_policy": "ollama_generate_defaults_to_non_thinking_unless_enabled",
        }
    )
    return prepared, metadata


@dataclass
class OllamaProvider(LLMProvider):
    model: str = "gemma4:26b"
    base_url: str = "http://localhost:11434"
    timeout_s: float = 600.0
    options: dict[str, Any] | None = None
    think: bool | str | None = None
    fallback: bool = False
    stream_callback: Callable[[dict[str, Any]], None] | None = None
    diagnostic_log_file: str | None = None
    diagnostic_run_id: str = ""
    diagnostic_label: str = ""
    stream_heartbeat_interval_s: float = 5.0
    thinking_only_watchdog_s: float = 0.0
    content_stall_watchdog_s: float = 120.0

    name: str = "ollama"

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self._diagnostic_log(
            "ollama chat called",
            run_id=self.diagnostic_run_id,
            diagnostic_label=self.diagnostic_label,
            model=self.model,
            message_count=len(messages),
            callback_present=self.stream_callback is not None,
        )
        ollama_messages: list[dict[str, Any]] = []
        for msg in messages:
            payload_message: dict[str, Any] = {"role": msg.role, "content": msg.content}
            images = [
                attachment.data_base64.split(",", 1)[-1]
                for attachment in msg.attachments
                if attachment.mime_type.startswith("image/") and attachment.data_base64
            ]
            if images:
                payload_message["images"] = images
            ollama_messages.append(payload_message)
        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": True,
        }
        if self.options:
            payload["options"] = self.options
        resolved_think, think_policy_metadata = resolve_ollama_think_choice(self.think)
        payload["think"] = resolved_think
        url = f"{self.base_url.rstrip('/')}/api/chat"
        self._diagnostic_log(
            "ollama payload prepared",
            run_id=self.diagnostic_run_id,
            diagnostic_label=self.diagnostic_label,
            model=self.model,
            stream=payload.get("stream"),
            think=payload.get("think"),
            think_source=think_policy_metadata.get("think_source"),
            think_default_applied=think_policy_metadata.get("think_default_applied"),
            options=payload.get("options"),
            message_count=len(ollama_messages),
        )
        return self._chat_streaming(url, payload, think_policy_metadata=think_policy_metadata)

    def _chat_streaming(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        think_policy_metadata: dict[str, Any] | None = None,
    ) -> ChatResponse:
        self._fallback_log(
            f"request stream=true model={self.model} messages={len(payload.get('messages') or [])} url={url}"
        )
        self._diagnostic_log(
            "ollama request prepared",
            run_id=self.diagnostic_run_id,
            diagnostic_label=self.diagnostic_label,
            url=url,
            model=self.model,
            timeout_s=self.timeout_s,
            callback_present=self.stream_callback is not None,
        )
        request_body = json.dumps(payload)
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        thinking_metadata = describe_thinking_setting(payload.get("think") if "think" in payload else self.think)
        if think_policy_metadata:
            thinking_metadata.update(think_policy_metadata)
        stream_identity = {
            "provider": self.name,
            "provider_class": self.__class__.__name__,
            "model": self.model,
            "diagnostic_label": self.diagnostic_label,
            "diagnostic_run_id": self.diagnostic_run_id,
            "stream": bool(payload.get("stream")),
            "transport": "http_stream",
            "message_count": len(messages),
            "request_bytes": len(request_body.encode("utf-8")),
            "timeout_s": self.timeout_s,
            "base_url": self.base_url,
            "url": url,
            "options_keys": sorted(str(key) for key in (payload.get("options") or {}).keys()) if isinstance(payload.get("options"), dict) else [],
            **thinking_metadata,
        }
        self._diagnostic_model_io_log(
            "ollama model input payload",
            {
                "url": url,
                "request_body": request_body,
                "payload": payload,
                "stream_identity": stream_identity,
            },
            model=self.model,
            stream=payload.get("stream"),
            think=payload.get("think"),
            thinking_enabled=thinking_metadata.get("thinking_enabled"),
            thinking_state=thinking_metadata.get("thinking_state"),
            options=payload.get("options"),
            message_count=len(messages),
        )
        request = Request(
            url,
            data=request_body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        first_model_stream_at: float | None = None
        last_content_at: float | None = None
        first_ms: int | None = None
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        events: list[dict[str, Any]] = []
        terminal_event: dict[str, Any] | None = None
        line_count = 0
        saw_done = False
        nonstream_response = False
        stream_state_lock = threading.Lock()
        stream_state: dict[str, Any] = {
            "phase": "request",
            "last_activity_monotonic": started,
            "content_chars": 0,
            "thinking_chars": 0,
        }
        heartbeat_stop = threading.Event()
        heartbeat_thread: threading.Thread | None = None

        def mark_stream_activity(phase: str | None = None) -> None:
            with stream_state_lock:
                if phase:
                    stream_state["phase"] = phase
                stream_state["last_activity_monotonic"] = time.monotonic()
                stream_state["content_chars"] = sum(len(part) for part in content_parts)
                stream_state["thinking_chars"] = sum(len(part) for part in thinking_parts)

        def raise_terminal_stream_error(fault_type: str, message: str) -> None:
            partial_content = "".join(content_parts)
            partial_thinking = "".join(thinking_parts)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._emit_stream_event(
                {
                    "type": "stream_error",
                    **stream_identity,
                    "error": message,
                    "terminal_fault_type": fault_type,
                    "partial_content_chars": len(partial_content),
                    "partial_thinking_chars": len(partial_thinking),
                    "partial_response_preview": partial_content[:_PARTIAL_RESPONSE_PREVIEW_CHARS],
                    "content_chars": len(partial_content),
                    "thinking_chars": len(partial_thinking),
                    "elapsed_ms": elapsed_ms,
                    "stream_phase": "error",
                    "stream_event_type": "stream_error",
                    "stream_heartbeat": False,
                }
            )
            self._diagnostic_model_io_log(
                "ollama model stream terminal error",
                {
                    "terminal_fault_type": fault_type,
                    "error": message,
                    "partial_content": partial_content,
                    "partial_thinking": partial_thinking,
                },
                elapsed_ms=elapsed_ms,
                content_chars=len(partial_content),
                thinking_chars=len(partial_thinking),
            )
            raise OllamaStreamTerminalError(
                message,
                terminal_fault_type=fault_type,
                partial_content=partial_content,
                partial_thinking=partial_thinking,
            )

        def check_stream_watchdogs() -> None:
            if first_model_stream_at is None:
                return
            now = time.monotonic()
            content_chars = sum(len(part) for part in content_parts)
            thinking_chars = sum(len(part) for part in thinking_parts)
            thinking_only_s = max(0.0, float(self.thinking_only_watchdog_s or 0.0))
            if (
                thinking_only_s > 0
                and content_chars == 0
                and thinking_chars >= 1000
                and now - first_model_stream_at > thinking_only_s
            ):
                raise_terminal_stream_error(
                    "thinking_only_watchdog",
                    f"model emitted thinking only for {int(thinking_only_s)}s and produced 0 final content chars",
                )
            stall_s = max(0.0, float(self.content_stall_watchdog_s or 0.0))
            if stall_s > 0 and content_chars > 0 and last_content_at is not None and now - last_content_at > stall_s:
                raise_terminal_stream_error(
                    "content_stall_watchdog",
                    f"model final content stalled for {int(stall_s)}s without content growth",
                )

        def start_stream_heartbeat() -> None:
            nonlocal heartbeat_thread
            interval_s = max(0.0, float(self.stream_heartbeat_interval_s or 0.0))
            if interval_s <= 0.0 or self.stream_callback is None:
                return

            def run() -> None:
                while not heartbeat_stop.wait(interval_s):
                    now = time.monotonic()
                    with stream_state_lock:
                        phase = str(stream_state.get("phase") or "request")
                        last_activity = float(stream_state.get("last_activity_monotonic") or started)
                        content_chars = int(stream_state.get("content_chars") or 0)
                        thinking_chars = int(stream_state.get("thinking_chars") or 0)
                    if now - last_activity < interval_s:
                        continue
                    elapsed_ms = int((now - started) * 1000)
                    if phase == "response":
                        event_type = "response_waiting"
                        waiting_reason = "waiting_for_next_streamed_token"
                        status_preview = append_thinking_status(
                            "Ollama response is open; still waiting for the next streamed model token.",
                            thinking_metadata,
                        )
                    else:
                        event_type = "request_waiting"
                        waiting_reason = "waiting_for_http_response_open"
                        status_preview = append_thinking_status(
                            "Ollama request is still waiting for the HTTP response to open; "
                            "the model may still be loading or pre-filling.",
                            thinking_metadata,
                        )
                    self._emit_stream_event(
                        {
                            "type": event_type,
                            **stream_identity,
                            "status_preview": status_preview,
                            "elapsed_ms": elapsed_ms,
                            "content_chars": content_chars,
                            "thinking_chars": thinking_chars,
                            "stream_phase": phase,
                            "stream_event_type": event_type,
                            "stream_heartbeat": True,
                            "waiting_reason": waiting_reason,
                        }
                    )
                    mark_stream_activity(phase)

            heartbeat_thread = threading.Thread(
                target=run,
                name=f"ollama-stream-heartbeat-{self.diagnostic_run_id or self.model}",
                daemon=True,
            )
            heartbeat_thread.start()
            self._diagnostic_log(
                "ollama stream heartbeat started",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
                interval_s=interval_s,
            )

        def stop_stream_heartbeat() -> None:
            heartbeat_stop.set()
            if heartbeat_thread is None:
                return
            heartbeat_thread.join(timeout=1.0)
            self._diagnostic_log(
                "ollama stream heartbeat stopped",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
            )

        try:
            self._diagnostic_log(
                "ollama urlopen starting",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
                url=url,
                timeout_s=self.timeout_s,
            )
            self._emit_stream_event(
                {
                    "type": "request_submitted",
                    **stream_identity,
                    "status_preview": append_thinking_status(
                        "Ollama request submitted; waiting for the HTTP response to open.",
                        thinking_metadata,
                    ),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "stream_phase": "request",
                    "stream_event_type": "request_submitted",
                    "stream_heartbeat": False,
                    "waiting_reason": "request_submitted",
                }
            )
            mark_stream_activity("request")
            start_stream_heartbeat()
            with urlopen(request, timeout=self.timeout_s) as response:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._diagnostic_log(
                    "ollama response opened",
                    run_id=self.diagnostic_run_id,
                    diagnostic_label=self.diagnostic_label,
                    elapsed_ms=elapsed_ms,
                )
                self._emit_stream_event(
                    {
                        "type": "response_opened",
                        **stream_identity,
                        "status_preview": append_thinking_status(
                            "Ollama response opened; waiting for streamed model tokens.",
                            thinking_metadata,
                        ),
                        "elapsed_ms": elapsed_ms,
                        "stream_phase": "response",
                        "stream_event_type": "response_opened",
                        "stream_heartbeat": False,
                        "waiting_reason": "response_opened",
                    }
                )
                mark_stream_activity("response")
                if hasattr(response, "__iter__"):
                    line_iter = response
                else:
                    nonstream_response = True
                    raw_body = response.read()
                    body_text = raw_body.decode("utf-8", errors="replace")
                    self._diagnostic_model_io_log(
                        "ollama model nonstream response body",
                        body_text,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        body_bytes=len(raw_body),
                    )
                    line_iter = [raw_body]
                for raw_line in line_iter:
                    if not raw_line:
                        continue
                    line_count += 1
                    text = raw_line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    raw_line_byte_count = (
                        len(raw_line)
                        if isinstance(raw_line, (bytes, bytearray))
                        else len(str(raw_line).encode("utf-8", errors="replace"))
                    )
                    self._diagnostic_model_io_log(
                        "ollama model raw stream line",
                        text,
                        line_index=line_count,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        raw_bytes=raw_line_byte_count,
                    )
                    self._diagnostic_log(
                        "ollama raw stream line",
                        run_id=self.diagnostic_run_id,
                        diagnostic_label=self.diagnostic_label,
                        line_index=line_count,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        raw_line=text,
                    )
                    try:
                        data: dict[str, Any] = json.loads(text)
                    except json.JSONDecodeError as exc:
                        self._fallback_log(f"non-json stream line: {text[:400]}")
                        self._diagnostic_log(
                            "ollama stream json decode failed",
                            run_id=self.diagnostic_run_id,
                            diagnostic_label=self.diagnostic_label,
                            line_index=line_count,
                            raw_text=text,
                            error=repr(exc),
                        )
                        continue
                    self._diagnostic_model_io_log(
                        "ollama model parsed stream object",
                        data,
                        line_index=line_count,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                    if len(events) < 50:
                        events.append(data)
                    stream_error = data.get("error")
                    if stream_error:
                        raise_terminal_stream_error(
                            "provider_stream_error",
                            f"Ollama stream error after {sum(len(part) for part in content_parts)} content chars "
                            f"and {sum(len(part) for part in thinking_parts)} thinking chars: {stream_error}",
                        )
                    message = data.get("message", {})
                    thinking = str(message.get("thinking", "") or "")
                    content = str(message.get("content", "") or "")
                    if (thinking or content) and first_model_stream_at is None:
                        first_model_stream_at = time.monotonic()
                    if thinking:
                        self._diagnostic_model_io_log(
                            "ollama model output thinking delta",
                            thinking,
                            line_index=line_count,
                            elapsed_ms=int((time.monotonic() - started) * 1000),
                            thinking_chars=len(thinking),
                        )
                    if content:
                        self._diagnostic_model_io_log(
                            "ollama model output content delta",
                            content,
                            line_index=line_count,
                            elapsed_ms=int((time.monotonic() - started) * 1000),
                            content_chars=len(content),
                        )
                    self._diagnostic_log(
                        "ollama parsed stream line",
                        run_id=self.diagnostic_run_id,
                        diagnostic_label=self.diagnostic_label,
                        line_index=line_count,
                        data=data,
                        has_content=bool(content),
                        has_thinking=bool(thinking),
                        done=bool(data.get("done")),
                        content_chars=len(content),
                        thinking_chars=len(thinking),
                    )
                    if thinking:
                        thinking_parts.append(thinking)
                        mark_stream_activity("response")
                    if content:
                        if first_ms is None:
                            first_ms = int((time.monotonic() - started) * 1000)
                            self._fallback_log(f"first model content after {first_ms} ms")
                        content_parts.append(content)
                        last_content_at = time.monotonic()
                        mark_stream_activity("response")
                        self._emit_stream_event(
                            {
                                "type": "content_delta",
                                **stream_identity,
                                "delta": content,
                                "content_preview": "".join(content_parts)[-800:],
                                "content_chars": sum(len(part) for part in content_parts),
                                "thinking_chars": sum(len(part) for part in thinking_parts),
                                "first_output_ms": first_ms,
                                "elapsed_ms": int((time.monotonic() - started) * 1000),
                                "stream_phase": "response",
                                "stream_event_type": "content_delta",
                                "stream_heartbeat": False,
                            }
                        )
                        if self.fallback:
                            sys.stdout.write(content)
                            sys.stdout.flush()
                    elif thinking:
                        self._emit_stream_event(
                            {
                                "type": "thinking_delta",
                                **stream_identity,
                                "delta": thinking,
                                "content_preview": "",
                                "thinking_preview": "".join(thinking_parts)[-800:],
                                "content_chars": sum(len(part) for part in content_parts),
                                "thinking_chars": sum(len(part) for part in thinking_parts),
                                "elapsed_ms": int((time.monotonic() - started) * 1000),
                                "stream_phase": "response",
                                "stream_event_type": "thinking_delta",
                                "stream_heartbeat": False,
                                "raw_thinking_exposed": True,
                            }
                        )
                    check_stream_watchdogs()
                    if data.get("done"):
                        saw_done = True
                        terminal_event = data
                        self._diagnostic_log(
                            "ollama stream done",
                            run_id=self.diagnostic_run_id,
                            diagnostic_label=self.diagnostic_label,
                            line_index=line_count,
                            elapsed_ms=int((time.monotonic() - started) * 1000),
                            content_chars=sum(len(part) for part in content_parts),
                            thinking_chars=sum(len(part) for part in thinking_parts),
                        )
                        break
        except URLError as exc:
            self._diagnostic_log(
                "ollama url error",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=repr(exc),
            )
            raise RuntimeError(
                f"Could not reach Ollama at {self.base_url}. Start Ollama and pull {self.model}."
            ) from exc
        except Exception as exc:
            self._diagnostic_log(
                "ollama stream exception",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=repr(exc),
                traceback=traceback.format_exc(),
            )
            raise
        finally:
            stop_stream_heartbeat()

        if self.fallback and (content_parts or thinking_parts):
            sys.stdout.write("\n")
            sys.stdout.flush()
        duration_ms = int((time.monotonic() - started) * 1000)
        final_content = "".join(content_parts)
        final_thinking = "".join(thinking_parts)
        if nonstream_response and final_content and not saw_done:
            saw_done = True
        if not saw_done:
            elapsed_since_first_stream = (
                time.monotonic() - first_model_stream_at
                if first_model_stream_at is not None
                else 0.0
            )
            thinking_only_s = max(0.0, float(self.thinking_only_watchdog_s or 0.0))
            if (
                thinking_only_s > 0
                and not final_content
                and len(final_thinking) >= 1000
                and elapsed_since_first_stream > thinking_only_s
            ):
                raise OllamaStreamTerminalError(
                    f"model emitted thinking only for {int(thinking_only_s)}s and produced 0 final content chars",
                    terminal_fault_type="thinking_only_watchdog",
                    partial_content=final_content,
                    partial_thinking=final_thinking,
                )
            raise OllamaStreamTerminalError(
                f"Ollama stream ended without done=true; partial content chars={len(final_content)}, "
                f"partial thinking chars={len(final_thinking)}",
                terminal_fault_type="provider_stream_incomplete",
                partial_content=final_content,
                partial_thinking=final_thinking,
            )

        def terminal_int(key: str) -> int:
            if not isinstance(terminal_event, dict):
                return 0
            try:
                return int(terminal_event.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        terminal_eval_count = terminal_int("eval_count")
        terminal_done_reason = (
            str(terminal_event.get("done_reason") or "")
            if isinstance(terminal_event, dict)
            else ""
        )
        if not final_content and (final_thinking or terminal_eval_count > 0):
            if final_thinking:
                fault_type = "thinking_only_no_visible_final_response"
                evidence = f"{len(final_thinking)} thinking char(s)"
            else:
                fault_type = "generated_tokens_no_visible_response"
                evidence = f"{terminal_eval_count} generated token(s)"
            raise_terminal_stream_error(
                fault_type,
                "Ollama provider produced no visible final content "
                f"after {evidence}; think={thinking_metadata.get('thinking_state')!r}; "
                f"done_reason={terminal_done_reason!r}",
            )
        self._diagnostic_model_io_log(
            "ollama model output final content",
            final_content,
            duration_ms=duration_ms,
            line_count=line_count,
            content_chars=len(final_content),
            first_output_ms=first_ms,
        )
        self._diagnostic_model_io_log(
            "ollama model output final thinking",
            final_thinking,
            duration_ms=duration_ms,
            line_count=line_count,
            thinking_chars=len(final_thinking),
        )
        self._diagnostic_log(
            "ollama stream completed",
            run_id=self.diagnostic_run_id,
            diagnostic_label=self.diagnostic_label,
            duration_ms=duration_ms,
            line_count=line_count,
            content_chars=len(final_content),
            thinking_chars=len(final_thinking),
            first_output_ms=first_ms,
        )
        self._fallback_log(f"stream complete duration_ms={duration_ms} content_chars={len(final_content)}")
        return ChatResponse(
            content=final_content,
            provider=self.name,
            model=self.model,
            metadata={
                "raw_stream_events": events,
                "thinking": final_thinking,
                "first_output_ms": first_ms,
                "duration_ms": duration_ms,
                **thinking_metadata,
            },
        )

    def _emit_stream_event(self, event: dict[str, Any]) -> None:
        self._diagnostic_log(
            "ollama stream callback attempt",
            run_id=self.diagnostic_run_id,
            diagnostic_label=self.diagnostic_label,
            event_type=event.get("type"),
            callback_present=self.stream_callback is not None,
            event=event,
        )
        if self.stream_callback is None:
            self._diagnostic_log(
                "ollama stream callback missing",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
                event_type=event.get("type"),
                event=event,
            )
            return
        try:
            self.stream_callback(event)
            self._diagnostic_log(
                "ollama stream callback ok",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
                event_type=event.get("type"),
                event=event,
            )
        except Exception as exc:
            self._diagnostic_log(
                "ollama stream callback failed",
                run_id=self.diagnostic_run_id,
                diagnostic_label=self.diagnostic_label,
                event_type=event.get("type"),
                error=repr(exc),
                traceback=traceback.format_exc(),
                event=event,
            )
            return

    def _fallback_log(self, message: str) -> None:
        if self.fallback:
            print(f"[fallback][ollama] {message}", file=sys.stderr, flush=True)

    def _diagnostic_model_io_log(self, label: str, payload: Any, **metadata: Any) -> None:
        if not self.diagnostic_log_file:
            return
        try:
            path = Path(self.diagnostic_log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            record = self._format_limited_diagnostic_record(label, payload, **metadata)
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(record)
        except Exception:
            return

    def _format_limited_diagnostic_record(
        self,
        label: str,
        payload: Any,
        *,
        max_bytes: int = _DIAGNOSTIC_RECORD_MAX_BYTES,
        **metadata: Any,
    ) -> str:
        fields: dict[str, Any] = {
            "run_id": self.diagnostic_run_id,
            "diagnostic_label": self.diagnostic_label,
        }
        fields.update(metadata)
        fields["payload"] = payload
        rendered = (
            f"[{datetime.now(tz=timezone.utc).isoformat()}] {label} "
            f"{json.dumps(fields, ensure_ascii=False, default=str, separators=(',', ':'))}\n"
        )
        return self._utf8_middle_ellipsize(rendered, max_bytes)

    def _diagnostic_log(self, label: str, **fields: Any) -> None:
        if not self.diagnostic_log_file:
            return
        try:
            path = Path(self.diagnostic_log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            record = self._format_limited_human_diagnostic_record(label, fields)
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(record)
        except Exception:
            return

    def _format_limited_human_diagnostic_record(
        self,
        label: str,
        fields: dict[str, Any],
        *,
        max_bytes: int = _DIAGNOSTIC_RECORD_MAX_BYTES,
    ) -> str:
        lines = [f"[{datetime.now(tz=timezone.utc).isoformat()}] {label}\n"]
        for key, value in fields.items():
            if isinstance(value, (dict, list, tuple)):
                lines.append(f"  {key}:\n")
                for line in json.dumps(value, indent=2, ensure_ascii=False, default=str).splitlines():
                    lines.append(f"    {line}\n")
            else:
                lines.append(f"  {key}: {value}\n")
        lines.append("\n")
        return self._utf8_middle_ellipsize("".join(lines), max_bytes)

    def _utf8_middle_ellipsize(self, text: str, max_bytes: int) -> str:
        if max_bytes <= 0:
            return ""
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text

        ellipsis = _DIAGNOSTIC_ELLIPSIS.encode("utf-8")
        if max_bytes <= len(ellipsis):
            return ellipsis[:max_bytes].decode("utf-8", errors="ignore")

        remaining = max_bytes - len(ellipsis)
        prefix_bytes = remaining // 2
        suffix_bytes = remaining - prefix_bytes

        prefix = encoded[:prefix_bytes].decode("utf-8", errors="ignore")
        suffix = encoded[-suffix_bytes:].decode("utf-8", errors="ignore")
        result = f"{prefix}{_DIAGNOSTIC_ELLIPSIS}{suffix}"

        while len(result.encode("utf-8")) > max_bytes:
            suffix = suffix[1:]
            result = f"{prefix}{_DIAGNOSTIC_ELLIPSIS}{suffix}"
        return result
