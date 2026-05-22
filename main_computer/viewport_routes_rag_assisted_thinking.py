from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
import os
import re
from typing import Any

from main_computer.chat_ai_subprocess import (
    ChatAISubprocessBusy,
    ChatAISubprocessCancelled,
    ChatAISubprocessError,
    append_text_log,
    config_to_payload,
    policy_to_payload,
)
from main_computer.chat_console import build_output_cell, output_part
from main_computer.rag_assisted_thinking import (
    DEFAULT_VERIFY_COMMAND,
    parse_think,
)
from main_computer.rag_assisted_thinking_v4 import (
    RagAssistedThinkingV4Policy,
    default_run_id,
    run_rag_assisted_thinking_v4_request,
)


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_str_list(value: Any, *, max_items: int = 32) -> list[str]:
    raw_items = value if isinstance(value, (list, tuple, set)) else ([value] if value is not None else [])
    items: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _truncate(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _object_view(value: Any) -> Any:
    if isinstance(value, dict):
        return _AttrDict({str(key): _object_view(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_object_view(item) for item in value]
    return value


class _AttrDict(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            return None


def _should_inline_test_provider(provider: Any) -> bool:
    module = str(getattr(getattr(provider, "__class__", None), "__module__", "") or "")
    if not provider or module.startswith("main_computer.providers"):
        return False
    return os.environ.get("MAIN_COMPUTER_DISABLE_INLINE_TEST_PROVIDER", "").strip().lower() not in {"1", "true", "yes", "on"}


class ViewportRagAssistedThinkingRoutesMixin:
    def _rag_assisted_thinking_repo_root(self) -> Path:
        return Path(getattr(self.server, "debug_root", Path.cwd())).resolve()

    def _rag_assisted_thinking_thread_id(self, body: dict[str, Any], cell: dict[str, Any] | None = None) -> str:
        cell = cell if isinstance(cell, dict) else {}
        for value in (
            body.get("thread_id"),
            body.get("chat_thread_id"),
            body.get("notebook_id"),
            body.get("notebook", {}).get("id") if isinstance(body.get("notebook"), dict) else "",
            cell.get("thread_id"),
            cell.get("chat_thread_id"),
            cell.get("notebook_id"),
        ):
            text = str(value or "").strip()
            if text:
                return text
        return "default-chat-thread"

    def _rag_assisted_thinking_subprocess_log_path(self, run_id: str) -> Path:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(run_id or "").strip()).strip("-_.") or "rag-assisted-thinking"
        root = (self.server.debug_root / "diagnostics_output" / "chat_console_ai_sessions" / clean).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root / "session.log"

    def _rag_assisted_thinking_policy(self, body: dict[str, Any]) -> RagAssistedThinkingV4Policy:
        think = parse_think(body.get("think", "low"))
        auto_apply = _coerce_bool(body.get("auto_apply"), default=False)
        allowed_write_paths = tuple(_coerce_str_list(body.get("allowed_write_paths"), max_items=64))
        config = getattr(self.server, "config", None)
        require_docker = _coerce_bool(body.get("require_docker"), default=False)
        docker_enabled = bool(
            getattr(config, "rag_docker_enabled", True)
            and (getattr(config, "executor_enabled", False) or require_docker)
        )
        docker_image = str(getattr(config, "executor_image", "") or "") if docker_enabled else ""
        docker_timeout_s = float(getattr(config, "executor_timeout_s", 180.0) or 180.0)

        # Chat-console RAG-AT exposes one user-facing switch. Docker follows the
        # global executor setting while RAG-AT is active; stale per-cell Docker
        # flags are ignored. MAIN_COMPUTER_RAG_DOCKER_ENABLED can force this
        # path off later without adding a separate notebook toggle.
        return RagAssistedThinkingV4Policy(
            think=think,
            use_model_for_rag=_coerce_bool(body.get("use_model_for_rag"), default=False),
            docker_enabled=docker_enabled,
            docker_image=docker_image,
            docker_command=DEFAULT_VERIFY_COMMAND if docker_enabled else "",
            docker_allow_network=False,
            docker_timeout_s=docker_timeout_s if docker_enabled else 1.0,
            verify_before=docker_enabled,
            verify_after=docker_enabled,
            require_docker_success=docker_enabled,
            auto_apply=auto_apply,
            allowed_write_paths=allowed_write_paths,
            self_contained_benchmark_mode=_coerce_bool(body.get("self_contained_benchmark_mode"), default=False),
            max_context_chars=max(4_000, min(120_000, int(body.get("max_context_chars", 18_000) or 18_000))),
            max_candidates=max(1, min(64, int(body.get("max_candidates", 12) or 12))),
            max_chunks=max(1, min(32, int(body.get("max_chunks", 8) or 8))),
        )

    def _rag_assisted_thinking_output_parts(self, result: Any) -> list[dict[str, Any]]:
        payload = result.repair_payload if isinstance(result.repair_payload, dict) else {}
        answer = _first_text(payload.get("answer"), payload.get("summary"), "RAG-assisted thinking completed.")
        summary_lines = [
            answer,
            "",
            f"Run id: `{result.run_id}`",
            f"Status: `{result.status}`",
        ]
        if result.proposed_paths:
            summary_lines.append(f"Proposed paths: {', '.join(result.proposed_paths)}")
        if result.written_paths:
            summary_lines.append(f"Written paths: {', '.join(result.written_paths)}")
        if result.docker_before is not None:
            summary_lines.append(f"Docker before: returncode `{result.docker_before.returncode}`")
        if result.docker_after is not None:
            summary_lines.append(f"Docker after: returncode `{result.docker_after.returncode}`")
        summary_lines.append("")
        summary_lines.append("Activity Monitor: open the **AI** filter to watch this run.")

        parts = [
            output_part(
                "markdown",
                "AI response",
                answer,
                metadata={
                    "mode": result.mode,
                    "run_id": result.run_id,
                    "status": result.status,
                    "retrieved_context_paths": result.retrieved_context_paths,
                    "proposed_paths": result.proposed_paths,
                    "written_paths": result.written_paths,
                    "activity_filter": "ai",
                },
            )
        ]

        if result.proposed_paths and not result.written_paths:
            parts.append(
                output_part(
                    "action",
                    "Replacement files proposed",
                    "RAG-AT produced replacement-file proposals. They were not applied because auto-apply is off.",
                    metadata={
                        "run_id": result.run_id,
                        "proposed_paths": result.proposed_paths,
                        "output_dir": result.output_dir,
                    },
                )
            )

        if result.warnings:
            parts.append(output_part("warning", "RAG-AT warnings", "\n".join(str(item) for item in result.warnings), metadata={"run_id": result.run_id}))
        if result.errors:
            parts.append(output_part("error", "RAG-AT errors", "\n".join(str(item) for item in result.errors), metadata={"run_id": result.run_id}))

        return parts

    def _handle_chat_console_rag_assisted_thinking_evaluate(self) -> None:
        try:
            body = self._read_json()
            cell = body.get("cell") if isinstance(body.get("cell"), dict) else {}
            prompt = _first_text(body.get("prompt"), cell.get("source") if isinstance(cell, dict) else "")
            if not prompt:
                raise ValueError("RAG-AT prompt is required.")

            provider = getattr(getattr(self.server, "computer", None), "provider", None)
            if provider is None or not hasattr(provider, "chat"):
                raise ValueError("No chat provider is available for RAG-assisted thinking.")

            policy = self._rag_assisted_thinking_policy(body)
            repo_root = self._rag_assisted_thinking_repo_root()
            queries = body.get("queries")
            if not queries:
                queries = [prompt]
            run_id = _first_text(body.get("run_id")) or default_run_id()
            thread_id = self._rag_assisted_thinking_thread_id(body, cell)
            log_path = self._rag_assisted_thinking_subprocess_log_path(run_id)
            output_root = repo_root / "diagnostics_output" / "rag_assisted_thinking_v4_routes"

            self.server.signal(
                "api-chat-console-rag-assisted-thinking",
                prompt_chars=len(prompt),
                think=policy.think,
                docker_enabled=policy.docker_enabled,
                auto_apply=policy.auto_apply,
                thread_id=thread_id,
                run_id=run_id,
            )
            append_text_log(
                log_path,
                "route accepted RAG-AT subprocess request",
                run_id=run_id,
                thread_id=thread_id,
                prompt_chars=len(prompt),
                prompt=prompt,
                cell=cell,
                queries=queries,
                policy=policy_to_payload(policy),
                repo_root=str(repo_root),
                output_root=str(output_root),
                provider=getattr(provider, "name", ""),
                model=getattr(provider, "model", ""),
            )
            self.server.activity.record(
                source="chat-console",
                kind="ai",
                time_model="parallel",
                severity="info",
                title="AI RAG request queued",
                message=prompt[:500],
                status="running",
                tags=["ai", "rag", "thinking", "local-ai", "chat-console", "subprocess"],
                data={
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "mode": "rag_assisted_thinking_v4",
                    "activity_filter": "ai",
                    "provider": getattr(provider, "name", ""),
                    "model": getattr(provider, "model", ""),
                    "log_file": str(log_path),
                    "output_dir": str(output_root / run_id),
                    "docker_enabled": bool(policy.docker_enabled),
                    "raw_thinking_exposed": False,
                    "running_text": "RAG-AT subprocess queued",
                    "rag_type": "chat_console_rag_at",
                    "rag_types_seen": ["chat_console_rag_at"],
                },
            )

            if _should_inline_test_provider(provider):
                append_text_log(
                    log_path,
                    "using inline non-production provider instead of subprocess",
                    provider_class=f"{provider.__class__.__module__}.{provider.__class__.__name__}",
                    reason="test provider objects cannot be reconstructed safely in a child process",
                )
                inline_result = run_rag_assisted_thinking_v4_request(
                    prompt=prompt,
                    repo_dir=repo_root,
                    provider=provider,
                    activity_bus=self.server.activity,
                    queries=queries,
                    run_id=run_id,
                    output_root=output_root,
                    policy=policy,
                )
                result_payload = inline_result.as_dict()
            else:
                payload = self.server.chat_ai_processes.run(
                    command={
                        "mode": "rag_assisted_thinking_v4",
                        "run_id": run_id,
                        "prompt": prompt,
                        "repo_dir": str(repo_root),
                        "queries": queries,
                        "output_root": str(output_root),
                        "policy": policy_to_payload(policy),
                        "config": config_to_payload(self.server.config),
                    },
                    thread_id=thread_id,
                    log_file=log_path,
                    activity_bus=self.server.activity,
                    cwd=repo_root,
                )
                result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            result = _object_view(result_payload)

            repair_response = result.repair_response if isinstance(result.repair_response, dict) else {}
            provider_name = str(repair_response.get("provider") or getattr(provider, "name", ""))
            model_name = str(repair_response.get("model") or getattr(provider, "model", ""))
            output_cell = build_output_cell(
                cell if isinstance(cell, dict) else {"id": "rag-at", "type": "ai", "source": prompt},
                self._rag_assisted_thinking_output_parts(result),
                status="ok" if result.ok else "error",
                provider=provider_name,
                model=model_name,
            )
            output_cell.setdefault("metadata", {})
            output_cell["metadata"] = {
                **(output_cell.get("metadata") if isinstance(output_cell.get("metadata"), dict) else {}),
                "mode": "rag_assisted_thinking_v4",
                "run_id": result.run_id,
                "thread_id": thread_id,
                "activity_filter": "ai",
                "log_file": str(log_path),
                "subprocess": True,
            }

            append_text_log(
                log_path,
                "route completed RAG-AT subprocess request",
                run_id=result.run_id,
                thread_id=thread_id,
                ok=result.ok,
                status=result.status,
                proposed_paths=result.proposed_paths,
                written_paths=result.written_paths,
            )
            repair_payload = result.repair_payload if isinstance(result.repair_payload, dict) else {}
            response_json = {
                "ok": True,
                "mode": "rag_assisted_thinking_v4",
                "run_id": result.run_id,
                "thread_id": thread_id,
                "status": result.status,
                "answer": _first_text(repair_payload.get("answer"), repair_payload.get("summary")),
                "activity_filter": "ai",
                "log_file": str(log_path),
                "proposed_paths": result.proposed_paths,
                "written_paths": result.written_paths,
                "warnings": result.warnings,
                "errors": result.errors,
                "output_cell": output_cell,
            }
            self.server.chat_ai_processes.remember_route_result(run_id=result.run_id, payload=response_json)
            self._send_json(response_json)
        except ChatAISubprocessBusy as exc:
            self.server.signal("api-chat-console-rag-assisted-thinking-busy", error=exc)
            self._send_json({"ok": False, "error": str(exc), "busy": True}, status=HTTPStatus.CONFLICT)
        except ChatAISubprocessCancelled as exc:
            self.server.signal("api-chat-console-rag-assisted-thinking-cancelled", error=exc)
            self._send_json({"ok": False, "error": str(exc), "cancelled": True}, status=HTTPStatus.BAD_REQUEST)
        except ChatAISubprocessError as exc:
            self.server.signal("api-chat-console-rag-assisted-thinking-subprocess-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-chat-console-rag-assisted-thinking-error", error=exc)
            self._send_json({"ok": False, "error": _truncate(exc)}, status=HTTPStatus.BAD_REQUEST)
