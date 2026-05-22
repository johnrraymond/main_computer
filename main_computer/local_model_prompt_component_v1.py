#!/usr/bin/env python3
"""Single-boundary local model prompt component.

This component is intentionally narrow: it accepts a prompt, sends exactly one
chat request to the configured local Ollama provider, and emits the model
response plus call evidence. It performs no retrieval, indexing, RAG planning,
executor work, or downstream validation.

Default live smoke run:

    python -m main_computer.local_model_prompt_component_v1 --prompt "Reply with OK."

Assembly/state-file run:

    python -m main_computer.local_model_prompt_component_v1 --state prompt_state.json --output-dir diagnostics_output/local_model_prompt_component_v1
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Sequence

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider, OllamaProvider
from main_computer.rag_smoke_subscripts_v1.contract import (
    StateProvision,
    StateRequirement,
    StepContract,
    StepResult,
    json_stdout,
    read_state,
    write_json,
)


COMPONENT_VERSION = "local_model_prompt_component_v1"

CONTRACT = StepContract(
    step_id="local_model_prompt_call",
    version=COMPONENT_VERSION,
    description=(
        "Accept one prompt, make one local model chat call, and emit the raw "
        "assistant response with provider/model evidence. This boundary does not "
        "perform retrieval, RAG assembly, executor work, or response grading."
    ),
    requires=(
        StateRequirement("prompt_text", "Prompt text to send as the user message for the local model call."),
    ),
    provides=(
        StateProvision("local_model_call_ok", "True only when the local model call completed and returned non-empty text."),
        StateProvision("local_model_provider", "Provider identity returned by the model call."),
        StateProvision("local_model_model", "Model identity returned by the model call."),
        StateProvision("local_model_response_text", "Raw response text emitted by the local model."),
        StateProvision("local_model_response_chars", "Character count of the raw response text."),
        StateProvision("local_model_elapsed_ms", "Elapsed wall-clock time for the provider.chat call."),
        StateProvision("local_model_trace_path", "Path to the JSON trace written by this component."),
        StateProvision("local_model_prompt_sha256", "SHA-256 digest of the prompt supplied to this component."),
        StateProvision("local_model_message_count", "Number of ChatMessage objects sent to the provider."),
    ),
    evidence_required=("provider_identity", "model_response", "trace_json"),
)


@dataclass(frozen=True)
class LocalModelPromptCall:
    ok: bool
    schema_version: int
    component_version: str
    step_id: str
    run_id: str
    provider: str
    model: str
    prompt_sha256: str
    prompt_preview: str
    response_text: str
    response_chars: int
    elapsed_ms: int
    message_count: int
    trace_path: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _preview(text: str, limit: int = 160) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def make_local_ollama_provider(*, model: str | None = None) -> OllamaProvider:
    """Build the live local provider for this component.

    This component is specifically a local-model boundary, so it refuses to use
    non-Ollama provider settings from the environment.
    """

    config = MainComputerConfig.from_env()
    if config.provider != "ollama":
        raise ValueError(
            "local_model_prompt_component_v1 requires MAIN_COMPUTER_PROVIDER=ollama "
            f"or an unset provider; got {config.provider!r}"
        )
    return OllamaProvider(
        model=model or config.model,
        base_url=config.ollama_base_url,
        timeout_s=config.ollama_timeout_s,
        think=config.ollama_think,
        fallback=config.fallback,
        diagnostic_run_id=COMPONENT_VERSION,
        diagnostic_label="local-model-prompt-component",
    )


def run_local_model_prompt_call(
    *,
    prompt_text: str,
    output_dir: Path,
    provider: LLMProvider | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    run_id: str | None = None,
) -> StepResult:
    """Send one prompt to one provider and return contract-shaped evidence."""

    prompt = str(prompt_text or "")
    if not prompt.strip():
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"error": "prompt_text must be non-empty"},
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    call_run_id = run_id or _utc_run_id()
    trace_path = output_dir / "local_model_prompt_call.json"
    active_provider = provider or make_local_ollama_provider(model=model)

    messages: list[ChatMessage] = []
    if system_prompt and system_prompt.strip():
        messages.append(ChatMessage(role="system", content=system_prompt.strip()))
    messages.append(ChatMessage(role="user", content=prompt))

    started = time.monotonic()
    try:
        response: ChatResponse = active_provider.chat(messages)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        failure_trace = LocalModelPromptCall(
            ok=False,
            schema_version=1,
            component_version=COMPONENT_VERSION,
            step_id=CONTRACT.step_id,
            run_id=call_run_id,
            provider=str(getattr(active_provider, "name", "unknown")),
            model=str(getattr(active_provider, "model", model or "unknown")),
            prompt_sha256=_sha256_text(prompt),
            prompt_preview=_preview(prompt),
            response_text="",
            response_chars=0,
            elapsed_ms=elapsed_ms,
            message_count=len(messages),
            trace_path=str(trace_path),
            error=f"{type(exc).__name__}: {exc}",
        )
        write_json(trace_path, failure_trace.as_dict())
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            evidence={"trace_json": str(trace_path)},
            details={"error": failure_trace.error, "elapsed_ms": elapsed_ms},
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    response_text = str(response.content or "")
    ok = bool(response_text.strip())
    trace = LocalModelPromptCall(
        ok=ok,
        schema_version=1,
        component_version=COMPONENT_VERSION,
        step_id=CONTRACT.step_id,
        run_id=call_run_id,
        provider=str(response.provider or getattr(active_provider, "name", "unknown")),
        model=str(response.model or getattr(active_provider, "model", model or "unknown")),
        prompt_sha256=_sha256_text(prompt),
        prompt_preview=_preview(prompt),
        response_text=response_text,
        response_chars=len(response_text),
        elapsed_ms=elapsed_ms,
        message_count=len(messages),
        trace_path=str(trace_path),
        error=None if ok else "model returned an empty response",
    )
    write_json(trace_path, trace.as_dict())

    if not ok:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            evidence={
                "provider_identity": {"provider": trace.provider, "model": trace.model},
                "trace_json": str(trace_path),
            },
            details={"error": trace.error, "elapsed_ms": elapsed_ms},
        )

    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "local_model_call_ok": True,
            "local_model_provider": trace.provider,
            "local_model_model": trace.model,
            "local_model_response_text": trace.response_text,
            "local_model_response_chars": trace.response_chars,
            "local_model_elapsed_ms": trace.elapsed_ms,
            "local_model_trace_path": trace.trace_path,
            "local_model_prompt_sha256": trace.prompt_sha256,
            "local_model_message_count": trace.message_count,
        },
        evidence={
            "provider_identity": {"provider": trace.provider, "model": trace.model},
            "model_response": {
                "response_chars": trace.response_chars,
                "response_preview": _preview(trace.response_text),
            },
            "trace_json": str(trace_path),
        },
        details={
            "run_id": trace.run_id,
            "single_model_call_boundary": True,
            "retrieval_performed": False,
            "rag_performed": False,
            "executor_performed": False,
        },
    )


def _prompt_from_args(args: argparse.Namespace, state: dict[str, Any]) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt is not None:
        return str(args.prompt)
    value = state.get("prompt_text")
    if value is None:
        raise ValueError("provide --prompt, --prompt-file, or state.prompt_text")
    return str(value)


def run_from_state(
    *,
    output_dir: Path,
    state: dict[str, Any],
    prompt_text: str | None = None,
    system_prompt: str | None = None,
    provider: LLMProvider | None = None,
    model: str | None = None,
    run_id: str | None = None,
) -> StepResult:
    prompt = prompt_text if prompt_text is not None else str(state.get("prompt_text", ""))
    return run_local_model_prompt_call(
        prompt_text=prompt,
        output_dir=output_dir,
        provider=provider,
        system_prompt=system_prompt if system_prompt is not None else state.get("system_prompt"),
        model=model,
        run_id=run_id or state.get("assembly_run_id") or state.get("run_id"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CONTRACT.description)
    parser.add_argument("--contract", action="store_true", help="Print this component contract and exit.")
    parser.add_argument("--prompt", default=None, help="Prompt text to send to the local model.")
    parser.add_argument("--prompt-file", default=None, help="UTF-8 file containing prompt text.")
    parser.add_argument("--system-prompt", default=None, help="Optional system message for the one chat call.")
    parser.add_argument("--state", default=None, help="Input JSON state. Uses state.prompt_text when no prompt flag is provided.")
    parser.add_argument("--output-dir", default=None, help="Directory where the component writes local_model_prompt_call.json.")
    parser.add_argument("--model", default=None, help="Optional Ollama model override.")
    parser.add_argument("--run-id", default=None, help="Optional stable run id for diagnostics.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.contract:
        json_stdout(CONTRACT.as_dict())
        return 0

    state = read_state(args.state)
    output_dir = Path(args.output_dir or Path("diagnostics_output") / COMPONENT_VERSION).resolve()
    try:
        prompt = _prompt_from_args(args, state)
        result = run_from_state(
            output_dir=output_dir,
            state=state,
            prompt_text=prompt,
            system_prompt=args.system_prompt,
            model=args.model,
            run_id=args.run_id,
        )
    except Exception as exc:  # pragma: no cover - converted into machine-readable CLI output
        result = StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"error": f"{type(exc).__name__}: {exc}"},
        )

    json_stdout(result.as_dict())
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
