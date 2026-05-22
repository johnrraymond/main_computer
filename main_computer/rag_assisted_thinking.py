from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import traceback
from typing import Any, Sequence

from main_computer.docker_executor import default_docker_instance_pool
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider
from main_computer.rag_harness import run_rag_harness
from main_computer.thinking_models import RagHarnessResult


DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
DEFAULT_VERIFY_COMMAND = "echo > /dev/null"


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def default_run_id() -> str:
    return f"rag_assisted_thinking_{utc_stamp()}"


@dataclass(frozen=True)
class DockerCommandResult:
    """Result from a repo-mounted Docker verification command."""

    ok: bool
    label: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagAssistedThinkingPolicy:
    """Controls the RAG-assisted thinking backend request mode.

    The safe default is proposal-only: retrieve context, call the local thinking
    provider, and return replacement-file proposals without applying them or
    running Docker. Callers must explicitly enable Docker and/or auto-apply.
    """

    think: bool | str | None = None
    use_model_for_rag: bool = False
    docker_enabled: bool = False
    docker_image: str = DEFAULT_DOCKER_IMAGE
    docker_command: str = DEFAULT_VERIFY_COMMAND
    docker_allow_network: bool = False
    docker_timeout_s: float = 180.0
    verify_before: bool = True
    verify_after: bool = True
    require_docker_success: bool = True
    auto_apply: bool = False
    allowed_write_paths: tuple[str, ...] = ()
    max_context_chars: int = 30_000
    max_candidates: int = 24
    max_chunks: int = 12
    max_context_files: int = 16
    max_file_chars: int = 16_000
    max_repair_prompt_chars: int = 80_000

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagAssistedThinkingResult:
    ok: bool
    status: str
    run_id: str
    mode: str
    prompt: str
    repo_dir: str
    output_dir: str
    rag_result: dict[str, Any]
    repair_response: dict[str, Any]
    repair_payload: dict[str, Any]
    retrieved_paths: list[str] = field(default_factory=list)
    retrieved_context_paths: list[str] = field(default_factory=list)
    proposed_paths: list[str] = field(default_factory=list)
    written_paths: list[str] = field(default_factory=list)
    docker_before: DockerCommandResult | None = None
    docker_after: DockerCommandResult | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["docker_before"] = self.docker_before.as_dict() if self.docker_before else None
        data["docker_after"] = self.docker_after.as_dict() if self.docker_after else None
        return data


def parse_think(value: Any) -> bool | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return text


def normalize_ollama_base_url(base_url: str) -> str:
    """Normalize a user-supplied Ollama URL to the server root."""

    url = str(base_url or "").strip().rstrip("/")
    for suffix in ("/api/chat", "/api/generate", "/api/tags"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""

    raw = str(text or "").strip()
    if not raw:
        raise ValueError("AI response was empty.")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    start = raw.find("{")
    if start < 0:
        raise ValueError("AI response did not contain a JSON object.")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(raw[start : index + 1])
                if not isinstance(parsed, dict):
                    raise ValueError("AI JSON payload was not an object.")
                return parsed

    raise ValueError("AI response contained an unterminated JSON object.")


def _preview(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part for part in text.split())
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _record(
    activity_bus: Any | None,
    *,
    run_id: str,
    title: str,
    message: str = "",
    source: str = "rag-assisted-thinking",
    kind: str = "ai",
    status: str = "",
    severity: str = "info",
    tags: Sequence[str] = ("rag", "thinking", "local-ai"),
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if activity_bus is None:
        return {}
    payload = dict(data or {})
    payload.setdefault("run_id", run_id)
    return activity_bus.record(
        source=source,
        kind=kind,
        time_model="parallel",
        severity=severity,
        title=title,
        message=message,
        status=status,
        tags=list(tags),
        data=payload,
        fault=severity in {"warn", "error"},
    )


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
    return {
        "message_count": len(messages),
        "system_prompt_preview": _preview("\n\n".join(system_prompts), limit=900),
        "user_prompt_preview": _preview("\n\n".join(user_prompts[-2:]), limit=700),
        "input_messages_preview": " | ".join(previews[:6]),
        "system_prompt_chars": sum(len(item) for item in system_prompts),
        "user_prompt_chars": sum(len(item) for item in user_prompts),
    }


def _safe_relative_path(rel_path: str) -> str:
    raw = str(rel_path or "").replace("\\", "/").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        raise ValueError("Empty repository-relative path.")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise ValueError(f"Unsafe repository-relative path: {rel_path!r}")
    return str(pure)


def _safe_repo_target(repo_dir: Path, rel_path: str) -> Path:
    safe = _safe_relative_path(rel_path)
    target = (repo_dir / safe).resolve()
    repo_resolved = repo_dir.resolve()
    try:
        target.relative_to(repo_resolved)
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {rel_path!r}") from exc
    return target


def _normalize_allowed_paths(paths: Sequence[str] | None) -> set[str]:
    allowed: set[str] = set()
    for path in paths or []:
        allowed.add(_safe_relative_path(path))
    return allowed


def _provider_label(provider: LLMProvider | None) -> tuple[str, str]:
    if provider is None:
        return ("", "")
    return (str(getattr(provider, "name", provider.__class__.__name__)), str(getattr(provider, "model", "")))


def _apply_policy_think(provider: LLMProvider | None, think: bool | str | None) -> None:
    """Apply policy think settings to providers that expose a ``think`` attribute."""

    if provider is None or think is None or not hasattr(provider, "think"):
        return
    try:
        setattr(provider, "think", think)
    except Exception:
        return


def _safe_response_metadata(response: ChatResponse) -> dict[str, Any]:
    """Return response metadata without raw model thinking."""

    safe: dict[str, Any] = {}
    for key, value in (response.metadata or {}).items():
        if str(key).lower() in {"thinking", "raw_thinking", "chain_of_thought"}:
            safe["raw_thinking_omitted"] = True
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value
        else:
            safe[str(key)] = _preview(value)
    return safe


def retrieved_paths_from_result(result: RagHarnessResult) -> list[str]:
    paths: list[str] = []
    for chunk in result.retrieval.chunks:
        safe = str(chunk.path or "").replace("\\", "/")
        if safe and safe not in paths:
            paths.append(safe)
    for candidate in result.retrieval.candidates:
        safe = str(candidate.path or "").replace("\\", "/")
        if safe and safe not in paths:
            paths.append(safe)
    return paths


def read_retrieved_context(
    *,
    repo_dir: Path | str,
    paths: Sequence[str],
    max_files: int = 16,
    max_file_chars: int = 16_000,
    max_total_chars: int = 80_000,
) -> list[dict[str, Any]]:
    repo_path = Path(repo_dir).resolve()
    context: list[dict[str, Any]] = []
    used_chars = 0

    for raw_path in paths:
        if len(context) >= max_files or used_chars >= max_total_chars:
            break
        try:
            safe = _safe_relative_path(raw_path)
            path = _safe_repo_target(repo_path, safe)
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        truncated = False
        budget = max(0, min(max_file_chars, max_total_chars - used_chars))
        if len(content) > budget:
            content = content[:budget]
            truncated = True
        if not content:
            continue

        context.append({"path": safe, "content": content, "chars": len(content), "truncated": truncated})
        used_chars += len(content)

    return context


def proposed_paths_from_payload(payload: dict[str, Any]) -> list[str]:
    files = payload.get("files")
    if not isinstance(files, list):
        return []
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        try:
            safe = _safe_relative_path(str(item.get("path") or ""))
        except ValueError:
            continue
        if safe not in paths:
            paths.append(safe)
    return paths


def build_repair_messages(
    *,
    prompt: str,
    rag_result: RagHarnessResult,
    retrieved_context: list[dict[str, Any]],
    docker_before: DockerCommandResult | None = None,
    allowed_write_paths: Sequence[str] | None = None,
    max_prompt_chars: int = 80_000,
) -> list[ChatMessage]:
    """Build the grounded local-AI call for RAG-assisted thinking."""

    allowed = sorted(_normalize_allowed_paths(allowed_write_paths))
    rag_summary = {
        "run_id": rag_result.run_id,
        "ok": rag_result.ok,
        "task_decomposition": rag_result.task_decomposition,
        "context_brief": rag_result.context_brief,
        "final_plan": rag_result.final_plan,
        "retrieved_paths": [item.get("path") for item in retrieved_context],
    }
    payload: dict[str, Any] = {
        "task": "Use RAG-assisted thinking to answer the request and, when needed, propose complete replacement files.",
        "user_prompt": prompt,
        "rules": [
            "Return JSON only. Do not use Markdown fences.",
            "Use only the supplied RAG context and Docker observations.",
            "Do not claim commands were run unless a Docker observation is supplied.",
            "When proposing file changes, each file must be a complete replacement, not a diff.",
            "If no file change is required, return an empty files list and put the answer in the answer field.",
        ],
        "allowed_write_paths": allowed,
        "required_output_schema": {
            "ok": True,
            "summary": "brief explanation of the result",
            "answer": "final answer for the user",
            "files": [{"path": "repo-relative path from allowed_write_paths", "content": "complete replacement source text"}],
            "commands": [{"kind": "docker_verify", "command": DEFAULT_VERIFY_COMMAND}],
            "warnings": [],
        },
        "rag_summary": rag_summary,
        "rag_context": retrieved_context,
        "docker_before": docker_before.as_dict() if docker_before else None,
    }

    serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    if len(serialized) > max_prompt_chars:
        trimmed_context: list[dict[str, Any]] = []
        used = 0
        for item in retrieved_context:
            content = str(item.get("content") or "")
            remaining = max(0, max_prompt_chars - 20_000 - used)
            if remaining <= 0:
                break
            trimmed = dict(item)
            if len(content) > remaining:
                trimmed["content"] = content[:remaining]
                trimmed["truncated"] = True
            used += len(str(trimmed.get("content") or ""))
            trimmed_context.append(trimmed)
        payload["rag_context"] = trimmed_context

    system = (
        "You are Main Computer's RAG-assisted thinking backend. "
        "You receive retrieved repository context and optional Docker observations. "
        "Return a single JSON object only. Keep private reasoning out of the JSON."
    )

    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)),
    ]


def run_docker_verification(
    *,
    repo_dir: Path | str,
    run_id: str,
    activity_bus: Any | None = None,
    image: str = DEFAULT_DOCKER_IMAGE,
    command: str = DEFAULT_VERIFY_COMMAND,
    label: str = "verify",
    timeout_s: float = 180.0,
    allow_network: bool = False,
) -> DockerCommandResult:
    """Run a repo-mounted Docker verification command."""

    repo_path = Path(repo_dir).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError(f"repo_dir does not exist or is not a directory: {repo_path}")

    command_text = str(command or "").strip()
    if not command_text:
        raise ValueError("docker verification command is required")

    docker_command = ["docker", "run", "--rm"]
    if not allow_network:
        docker_command.extend(["--network", "none"])
    docker_command.extend(["-v", f"{str(repo_path)}:/workspace", "-w", "/workspace", str(image or DEFAULT_DOCKER_IMAGE), "sh", "-lc", command_text])

    pool = default_docker_instance_pool()
    try:
        lease = pool.request(
            run_id=run_id,
            image=str(image or DEFAULT_DOCKER_IMAGE),
            command_preview=command_text,
            label=label,
            activity_bus=activity_bus,
        )
    except Exception as exc:
        _record(
            activity_bus,
            run_id=run_id,
            title=f"Docker verification failed to acquire pool slot: {label}",
            message=_preview(exc),
            source="executor",
            kind="subprocess",
            status="failed",
            severity="warn",
            tags=["rag", "thinking", "docker", "executor", "subprocess", "pool"],
            data={
                "phase": label,
                "image": image,
                "command_preview": command_text,
                "network": bool(allow_network),
                "error": _preview(exc),
                "rag_type": "docker_executor",
            },
        )
        return DockerCommandResult(
            ok=False,
            label=label,
            command=docker_command,
            returncode=125,
            stdout="",
            stderr=str(exc),
            error=str(exc),
        )

    result: DockerCommandResult | None = None
    try:
        _record(
            activity_bus,
            run_id=run_id,
            title=f"Docker verification started: {label}",
            message=_preview(command_text),
            source="executor",
            kind="subprocess",
            status="running",
            tags=["rag", "thinking", "docker", "executor", "subprocess"],
            data={
                "phase": label,
                "image": image,
                "command_preview": command_text,
                "network": bool(allow_network),
                "docker_pool_lease": lease.as_dict(),
                "lease_id": lease.lease_id,
                "slot": lease.slot,
                "running_text": f"docker slot {lease.slot} running {label}: {command_text}",
                "rag_type": "docker_executor",
            },
        )

        try:
            completed = subprocess.run(
                docker_command,
                cwd=str(repo_path),
                text=True,
                capture_output=True,
                timeout=max(1.0, float(timeout_s)),
            )
            result = DockerCommandResult(
                ok=completed.returncode == 0,
                label=label,
                command=docker_command,
                returncode=int(completed.returncode),
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except FileNotFoundError as exc:
            result = DockerCommandResult(
                ok=False,
                label=label,
                command=docker_command,
                returncode=127,
                stdout="",
                stderr="Docker executable was not found on PATH.",
                error=str(exc),
            )
        except subprocess.TimeoutExpired as exc:
            result = DockerCommandResult(
                ok=False,
                label=label,
                command=docker_command,
                returncode=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"Docker verification timed out after {timeout_s}s.",
                timed_out=True,
                error="timeout",
            )

        _record(
            activity_bus,
            run_id=run_id,
            title=f"Docker verification {'completed' if result.ok else 'failed'}: {label}",
            message=f"returncode={result.returncode}",
            source="executor",
            kind="subprocess",
            status="completed" if result.ok else "failed",
            severity="info" if result.ok else "warn",
            tags=["rag", "thinking", "docker", "executor", "subprocess"],
            data={
                "phase": label,
                "returncode": result.returncode,
                "stdout_preview": result.stdout[-2000:],
                "stderr_preview": result.stderr[-2000:],
                "timed_out": result.timed_out,
                "docker_pool_lease": lease.as_dict(),
                "lease_id": lease.lease_id,
                "slot": lease.slot,
                "ran_text": f"docker slot {lease.slot} finished {label} with returncode={result.returncode}",
                "rag_type": "docker_executor",
            },
        )
        return result
    finally:
        pool.release(
            lease,
            activity_bus=activity_bus,
            status="completed" if result and result.ok else "failed",
            returncode=result.returncode if result else None,
            error=result.error if result else None,
        )


def apply_replacement_files(
    *,
    repo_dir: Path | str,
    payload: dict[str, Any],
    allowed_write_paths: Sequence[str],
    run_id: str = "",
    activity_bus: Any | None = None,
) -> list[str]:
    """Apply complete replacement files from an AI payload with path allow-listing."""

    repo_path = Path(repo_dir).resolve()
    allowed = _normalize_allowed_paths(allowed_write_paths)
    if not allowed:
        raise ValueError("allowed_write_paths is required before replacement files can be applied")

    files = payload.get("files")
    if not isinstance(files, list):
        return []

    written: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("replacement file entry must be an object")
        rel_path = _safe_relative_path(str(item.get("path") or ""))
        if rel_path not in allowed:
            raise ValueError(f"replacement path is not allowed: {rel_path}")
        content = item.get("content")
        if not isinstance(content, str) or not content:
            raise ValueError(f"replacement content is empty for {rel_path}")
        target = _safe_repo_target(repo_path, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel_path)

    if written:
        _record(
            activity_bus,
            run_id=run_id,
            title="RAG-assisted thinking replacement files applied",
            message=f"Wrote {len(written)} replacement file(s).",
            status="completed",
            tags=["rag", "thinking", "local-ai", "repair", "file-write"],
            data={"written_paths": written},
        )
    return written


def run_rag_assisted_thinking_request(
    *,
    prompt: str,
    repo_dir: Path | str = ".",
    provider: LLMProvider | None = None,
    rag_provider: LLMProvider | None = None,
    activity_bus: Any | None = None,
    queries: list[str] | str | None = None,
    run_id: str | None = None,
    output_root: Path | str | None = None,
    policy: RagAssistedThinkingPolicy | None = None,
) -> RagAssistedThinkingResult:
    """Run a backend RAG-assisted thinking request."""

    policy = policy or RagAssistedThinkingPolicy()
    repo_path = Path(repo_dir).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError(f"repo_dir does not exist or is not a directory: {repo_path}")

    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    if provider is None:
        raise ValueError("provider is required for RAG-assisted thinking")

    _apply_policy_think(provider, policy.think)
    _apply_policy_think(rag_provider, policy.think)

    run_id = run_id or default_run_id()
    base_output = Path(output_root).resolve() if output_root else repo_path / "diagnostics_output" / "rag_assisted_thinking_runs"
    output_dir = base_output / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    errors: list[str] = []
    warnings: list[str] = []
    docker_before: DockerCommandResult | None = None
    docker_after: DockerCommandResult | None = None
    written_paths: list[str] = []
    repair_payload: dict[str, Any] = {}
    repair_response_payload: dict[str, Any] = {}
    rag_result: RagHarnessResult | None = None
    retrieved_paths: list[str] = []
    retrieved_context: list[dict[str, Any]] = []
    status = "running"

    _record(
        activity_bus,
        run_id=run_id,
        title="RAG-assisted thinking request started",
        message=_preview(prompt),
        status="running",
        tags=["rag", "thinking", "local-ai", "run"],
        data={"repo_dir": str(repo_path), "policy": policy.as_dict(), "raw_thinking_exposed": False},
    )

    try:
        if policy.docker_enabled and policy.verify_before and policy.docker_command:
            docker_before = run_docker_verification(
                repo_dir=repo_path,
                run_id=run_id,
                activity_bus=activity_bus,
                image=policy.docker_image,
                command=policy.docker_command,
                label="before",
                timeout_s=policy.docker_timeout_s,
                allow_network=policy.docker_allow_network,
            )

        rag_result = run_rag_harness(
            prompt=prompt,
            repo_dir=repo_path,
            queries=queries,
            output_root=output_dir / "rag_runs",
            max_context_chars=policy.max_context_chars,
            max_candidates=policy.max_candidates,
            max_chunks=policy.max_chunks,
            use_model=bool(policy.use_model_for_rag),
            provider=rag_provider,
            run_id=run_id,
            activity_bus=activity_bus,
        )

        retrieved_paths = retrieved_paths_from_result(rag_result)
        retrieved_context = read_retrieved_context(
            repo_dir=repo_path,
            paths=retrieved_paths,
            max_files=policy.max_context_files,
            max_file_chars=policy.max_file_chars,
            max_total_chars=policy.max_repair_prompt_chars,
        )
        _write_json(output_dir / "retrieved_context.json", retrieved_context)

        _record(
            activity_bus,
            run_id=run_id,
            title="RAG-assisted thinking context selected",
            message=f"{len(retrieved_context)} files prepared for the thinking call.",
            status="completed",
            tags=["rag", "thinking", "retrieval", "context"],
            data={
                "retrieved_paths": retrieved_paths[:16],
                "context_paths": [item.get("path") for item in retrieved_context],
                "context_chars": sum(int(item.get("chars") or 0) for item in retrieved_context),
            },
        )

        messages = build_repair_messages(
            prompt=prompt,
            rag_result=rag_result,
            retrieved_context=retrieved_context,
            docker_before=docker_before,
            allowed_write_paths=policy.allowed_write_paths,
            max_prompt_chars=policy.max_repair_prompt_chars,
        )
        message_history = _message_history_payload(messages)
        provider_name, provider_model = _provider_label(provider)

        _record(
            activity_bus,
            run_id=run_id,
            title="Local AI RAG-assisted thinking input prepared",
            message=message_history.get("system_prompt_preview") or message_history.get("input_messages_preview") or "model input prepared",
            source="local-ai",
            kind="ai",
            status="running",
            tags=["rag", "thinking", "local-ai", "ollama", "model-call", "prompt"],
            data={
                "provider": provider_name,
                "model": provider_model,
                "prompt_chars": sum(len(message.content) for message in messages),
                "raw_thinking_exposed": False,
                "running_text": "RAG-assisted thinking model input prepared",
                "rag_type": "model_input",
                **message_history,
            },
        )

        _record(
            activity_bus,
            run_id=run_id,
            title="Local AI RAG-assisted thinking call started",
            message=f"{provider_name}/{provider_model}",
            source="local-ai",
            kind="ai",
            status="running",
            tags=["rag", "thinking", "local-ai", "ollama", "model-call"],
            data={
                "provider": provider_name,
                "model": provider_model,
                "think": policy.think,
                "prompt_chars": sum(len(message.content) for message in messages),
                "raw_thinking_exposed": False,
                "running_text": f"local AI model call running: {provider_name}/{provider_model}",
                "rag_type": "model_call",
                **message_history,
            },
        )

        repair_response = provider.chat(messages)
        repair_response_payload = {
            "provider": repair_response.provider,
            "model": repair_response.model,
            "content": repair_response.content,
            "metadata": _safe_response_metadata(repair_response),
        }
        _write_json(output_dir / "repair_response.json", repair_response_payload)

        repair_payload = extract_json_object(repair_response.content)
        _write_json(output_dir / "repair_payload.json", repair_payload)
        proposed_paths = proposed_paths_from_payload(repair_payload)

        _record(
            activity_bus,
            run_id=run_id,
            title="Local AI RAG-assisted thinking call completed",
            message=_preview(repair_payload.get("summary") or repair_payload.get("answer") or "AI response parsed."),
            source="local-ai",
            kind="ai",
            status="completed",
            tags=["rag", "thinking", "local-ai", "ollama", "model-call", "completed"],
            data={
                "provider": repair_response.provider,
                "model": repair_response.model,
                "response_chars": len(repair_response.content),
                "proposed_paths": proposed_paths,
                "raw_thinking_exposed": False,
                "ran_text": f"local AI model call completed: {repair_response.provider}/{repair_response.model}",
                "rag_type": "model_call",
            },
        )

        if repair_payload.get("ok") is False:
            errors.append(_preview(repair_payload.get("summary") or "AI payload reported ok=false"))

        if proposed_paths and not policy.auto_apply:
            warnings.append("AI proposed replacement files but auto_apply is disabled.")
            _record(
                activity_bus,
                run_id=run_id,
                title="RAG-assisted thinking replacement files proposed",
                message="Replacement files were not applied because auto_apply is disabled.",
                status="approval_required",
                severity="warn",
                tags=["rag", "thinking", "repair", "approval"],
                data={"proposed_paths": proposed_paths},
            )

        if proposed_paths and policy.auto_apply:
            written_paths = apply_replacement_files(
                repo_dir=repo_path,
                payload=repair_payload,
                allowed_write_paths=policy.allowed_write_paths,
                run_id=run_id,
                activity_bus=activity_bus,
            )

        if policy.docker_enabled and policy.verify_after and policy.docker_command and (policy.auto_apply or not proposed_paths):
            docker_after = run_docker_verification(
                repo_dir=repo_path,
                run_id=run_id,
                activity_bus=activity_bus,
                image=policy.docker_image,
                command=policy.docker_command,
                label="after",
                timeout_s=policy.docker_timeout_s,
                allow_network=policy.docker_allow_network,
            )
            if policy.require_docker_success and not docker_after.ok:
                errors.append("Docker verification failed after RAG-assisted thinking.")

        if policy.docker_enabled and policy.verify_after and proposed_paths and not policy.auto_apply:
            warnings.append("Docker after-verification skipped because proposed files were not applied.")

        status = "complete" if not errors else "failed"

    except Exception as exc:
        status = "failed"
        errors.append(str(exc))
        _write_json(output_dir / "error.json", {"error": str(exc), "traceback": traceback.format_exc()})
        _record(
            activity_bus,
            run_id=run_id,
            title="RAG-assisted thinking request failed",
            message=_preview(str(exc)),
            status="error",
            severity="error",
            tags=["rag", "thinking", "local-ai", "fault"],
            data={"error": _preview(str(exc), limit=800)},
        )

    rag_dict: dict[str, Any] = {} if rag_result is None else rag_result.as_dict()
    result = RagAssistedThinkingResult(
        ok=not errors,
        status=status,
        run_id=run_id,
        mode="rag_assisted_thinking",
        prompt=prompt,
        repo_dir=str(repo_path),
        output_dir=str(output_dir),
        rag_result=rag_dict,
        repair_response=repair_response_payload,
        repair_payload=repair_payload,
        retrieved_paths=retrieved_paths,
        retrieved_context_paths=[str(item.get("path") or "") for item in retrieved_context],
        proposed_paths=proposed_paths_from_payload(repair_payload),
        written_paths=written_paths,
        docker_before=docker_before,
        docker_after=docker_after,
        warnings=warnings,
        errors=errors,
    )
    _write_json(output_dir / "run.json", result.as_dict())

    _record(
        activity_bus,
        run_id=run_id,
        title=f"RAG-assisted thinking request {'completed' if result.ok else 'failed'}",
        message=_preview(repair_payload.get("summary") or repair_payload.get("answer") or status),
        status="completed" if result.ok else "error",
        severity="info" if result.ok else "error",
        tags=["rag", "thinking", "local-ai", "run", "completed" if result.ok else "failed"],
        data={
            "status": result.status,
            "ok": result.ok,
            "written_paths": result.written_paths,
            "proposed_paths": result.proposed_paths,
            "docker_before_ok": result.docker_before.ok if result.docker_before else None,
            "docker_after_ok": result.docker_after.ok if result.docker_after else None,
            "raw_thinking_exposed": False,
        },
    )

    return result


def docker_available() -> bool:
    return shutil.which("docker") is not None


__all__ = [
    "DEFAULT_DOCKER_IMAGE",
    "DEFAULT_VERIFY_COMMAND",
    "DockerCommandResult",
    "RagAssistedThinkingPolicy",
    "RagAssistedThinkingResult",
    "apply_replacement_files",
    "build_repair_messages",
    "default_run_id",
    "docker_available",
    "extract_json_object",
    "normalize_ollama_base_url",
    "parse_think",
    "read_retrieved_context",
    "retrieved_paths_from_result",
    "run_docker_verification",
    "run_rag_assisted_thinking_request",
]
