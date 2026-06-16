from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Sequence

from main_computer.executor_backend import ExecutorBackend
from main_computer.executor_models import ExecutorRequest, ExecutorResult
from main_computer.ai_control import ai_control_prompt_text
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider


EXECUTOR_TOOL_LOOP_SYSTEM_PROMPT = """You are Main Computer's Linux execution planner.

You may ask the backend for exactly one Linux/Python execution step at a time by returning one JSON object.
Do not claim that a command was executed until the backend returns command_output.

When you need execution, return JSON only:
{
  "action": "execute_shell",
  "description": "short reason for the operator log",
  "command": "python - <<'PY'\nprint('hello')\nPY",
  "cwd": "/workspace",
  "timeout_s": 30,
  "network": false,
  "input_ids": []
}

Rules:
- Use /inputs/<upload_id>/payload.bin for uploaded files.
- Write downloadable outputs under /outputs.
- Use network=false unless the operator explicitly asked for network access.
- Keep commands focused and inspect before transforming.
- After command_output is provided, either ask for the next execute_shell step or return a final answer.

When finished, return JSON only:
{
  "action": "final",
  "content": "final answer for the user, including artifact download URLs when relevant"
}
"""


TOOL_RESULT_OUTPUT_LIMIT = 12_000
VALID_TOOL_ACTIONS = {"execute_shell", "docker_exec", "final"}


@dataclass(frozen=True)
class ExecutorToolLoopConfig:
    max_steps: int = 4
    max_timeout_s: float = 120.0
    auto_run: bool = False
    allow_network: bool = False


@dataclass(frozen=True)
class ExecutorToolLoopStep:
    index: int
    kind: str
    content: str = ""
    tool_request: dict[str, Any] | None = None
    executor_result: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutorToolLoopResult:
    ok: bool
    status: str
    provider: str
    model: str
    final_content: str
    steps: list[ExecutorToolLoopStep] = field(default_factory=list)
    needs_approval: bool = False
    tool_request: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "final_content": self.final_content,
            "steps": [step.as_dict() for step in self.steps],
            "needs_approval": self.needs_approval,
            "tool_request": self.tool_request,
            "error": self.error,
        }


def run_executor_tool_loop(
    *,
    provider: LLMProvider,
    prompt: str,
    context_text: str,
    executor_backend: ExecutorBackend | None = None,
    docker_executor: ExecutorBackend | None = None,
    config: ExecutorToolLoopConfig,
    upload_ids: Sequence[str] | None = None,
) -> ExecutorToolLoopResult:
    """Run a bounded model/tool loop against the configured executor backend.

    This is intentionally explicit and conservative. If ``config.auto_run`` is
    false, the first execute_shell request is returned for operator approval
    instead of being executed.

    ``docker_executor`` remains as a backward-compatible keyword while callers
    migrate to the neutral ``executor_backend`` name.
    """

    active_executor = executor_backend or docker_executor
    if active_executor is None:
        raise ValueError("executor_backend is required.")

    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        raise ValueError("Prompt is required.")

    uploads = [str(item).strip() for item in (upload_ids or []) if str(item).strip()]
    upload_context = _upload_context_text(uploads)

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=ai_control_prompt_text("executor_tool_loop.system", EXECUTOR_TOOL_LOOP_SYSTEM_PROMPT)),
        ChatMessage(role="system", content=context_text),
        ChatMessage(
            role="user",
            content="\n\n".join(
                part
                for part in [
                    f"User request:\n{prompt_text}",
                    upload_context,
                    "Return either an execute_shell JSON object or a final JSON object.",
                ]
                if part
            ),
        ),
    ]
    steps: list[ExecutorToolLoopStep] = []
    provider_name = getattr(provider, "name", "unknown")
    model_name = getattr(provider, "model", "unknown")

    max_steps = max(1, int(config.max_steps))
    for index in range(1, max_steps + 1):
        response = provider.chat(messages)
        provider_name = response.provider
        model_name = response.model
        model_content = response.content
        steps.append(ExecutorToolLoopStep(index=index, kind="model", content=model_content))

        try:
            tool_request = extract_executor_tool_request(model_content)
        except ValueError as exc:
            return ExecutorToolLoopResult(
                ok=False,
                status="invalid_tool_request",
                provider=provider_name,
                model=model_name,
                final_content=model_content,
                steps=[*steps, ExecutorToolLoopStep(index=index, kind="error", error=str(exc))],
                error=str(exc),
            )

        if tool_request is None:
            return ExecutorToolLoopResult(
                ok=True,
                status="complete",
                provider=provider_name,
                model=model_name,
                final_content=model_content,
                steps=steps,
            )

        action = str(tool_request.get("action") or "").strip()
        if action == "final":
            content = str(tool_request.get("content") or tool_request.get("final") or "").strip()
            return ExecutorToolLoopResult(
                ok=True,
                status="complete",
                provider=provider_name,
                model=model_name,
                final_content=content or model_content,
                steps=steps,
            )

        request_mapping = _executor_request_mapping(tool_request, uploads)
        try:
            executor_request = ExecutorRequest.from_mapping(
                request_mapping,
                max_timeout_s=config.max_timeout_s,
            )
        except ValueError as exc:
            return ExecutorToolLoopResult(
                ok=False,
                status="invalid_executor_request",
                provider=provider_name,
                model=model_name,
                final_content="",
                steps=[*steps, ExecutorToolLoopStep(index=index, kind="error", tool_request=tool_request, error=str(exc))],
                tool_request=tool_request,
                error=str(exc),
            )

        if executor_request.network and not config.allow_network:
            return ExecutorToolLoopResult(
                ok=False,
                status="blocked",
                provider=provider_name,
                model=model_name,
                final_content="",
                steps=[*steps, ExecutorToolLoopStep(index=index, kind="blocked", tool_request=executor_request.as_dict(), error="Network execution is disabled.")],
                needs_approval=True,
                tool_request=executor_request.as_dict(),
                error="Network execution is disabled for executor AI tool loops.",
            )

        if not config.auto_run:
            return ExecutorToolLoopResult(
                ok=True,
                status="tool_requested",
                provider=provider_name,
                model=model_name,
                final_content="",
                steps=[*steps, ExecutorToolLoopStep(index=index, kind="tool_request", tool_request=executor_request.as_dict())],
                needs_approval=True,
                tool_request=executor_request.as_dict(),
            )

        executor_result = active_executor.run(executor_request)
        result_payload = _executor_result_for_model(executor_result)
        steps.append(
            ExecutorToolLoopStep(
                index=index,
                kind="command_output",
                tool_request=executor_request.as_dict(),
                executor_result=result_payload,
                error=executor_result.error,
            )
        )
        messages.append(ChatMessage(role="assistant", content=model_content))
        messages.append(
            ChatMessage(
                role="user",
                content="command_output:\n" + json.dumps(result_payload, indent=2, ensure_ascii=False),
            )
        )

    return ExecutorToolLoopResult(
        ok=False,
        status="max_steps",
        provider=provider_name,
        model=model_name,
        final_content="",
        steps=steps,
        error=f"Executor tool loop stopped after {max_steps} steps.",
    )


def extract_executor_tool_request(content: str) -> dict[str, Any] | None:
    """Extract one executor tool request from model text.

    Supports raw JSON and fenced JSON blocks. Returns None when the model sent
    ordinary final text with no structured tool request.
    """

    text = str(content or "").strip()
    if not text:
        return None

    candidates = _json_candidates(text)
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        action = str(data.get("action") or data.get("tool") or "").strip()
        if action == "docker_exec":
            data = dict(data)
            data["action"] = "docker_exec"
        if action in VALID_TOOL_ACTIONS:
            return data
    if _looks_like_tool_json(text):
        raise ValueError("Model returned malformed executor tool JSON.")
    return None


def _json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL):
        candidates.append(match.group(1).strip())

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            candidates.append(text[index : index + end])
    return candidates


def _looks_like_tool_json(text: str) -> bool:
    lowered = text.lower()
    return "execute_shell" in lowered or "docker_exec" in lowered or '"action"' in lowered


def _executor_request_mapping(tool_request: dict[str, Any], upload_ids: list[str]) -> dict[str, Any]:
    mapping = dict(tool_request)
    mapping.pop("action", None)
    mapping.pop("tool", None)
    if "input_ids" not in mapping and "inputs" not in mapping and upload_ids:
        mapping["input_ids"] = upload_ids
    return mapping


def _executor_result_for_model(result: ExecutorResult) -> dict[str, Any]:
    payload = result.as_dict()
    payload["stdout"] = _truncate(payload.get("stdout", ""), TOOL_RESULT_OUTPUT_LIMIT)
    payload["stderr"] = _truncate(payload.get("stderr", ""), TOOL_RESULT_OUTPUT_LIMIT)
    if payload.get("artifacts"):
        payload["artifact_download_urls"] = [
            artifact.get("download_url")
            for artifact in payload["artifacts"]
            if isinstance(artifact, dict) and artifact.get("download_url")
        ]
    return payload


def _upload_context_text(upload_ids: list[str]) -> str:
    if not upload_ids:
        return ""
    lines = ["Uploaded files available to executor:"]
    for upload_id in upload_ids:
        lines.append(f"- {upload_id}: /inputs/{upload_id}/payload.bin")
    return "\n".join(lines)


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[truncated after {limit} characters]"
