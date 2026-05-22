from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

AIDER_SUBPROCESS_OUTPUT_ENCODING = "utf-8"
AIDER_SUBPROCESS_ENV_DEFAULTS: Mapping[str, str] = {
    "PYTHONIOENCODING": AIDER_SUBPROCESS_OUTPUT_ENCODING,
    "PYTHONUTF8": "1",
    "PYTHONUNBUFFERED": "1",
}

AiderOutputCallback = Callable[[str, str], None]


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class AiderAgentError(Exception):
    """Base exception for local Aider agent actions."""


class AiderValidationError(AiderAgentError):
    """Raised when an Aider action points outside the allowed workspace."""


@dataclass(frozen=True)
class AiderAgentConfig:
    workspace: Path
    aider_bin: str = os.getenv("MAIN_COMPUTER_AIDER_BIN", os.getenv("AIDER_BIN", "aider"))
    default_model: str = os.getenv(
        "MAIN_COMPUTER_AIDER_MODEL",
        os.getenv("AIDER_MODEL", "ollama_chat/llama3.1:8b"),
    )
    timeout_seconds: int = int(os.getenv("MAIN_COMPUTER_AIDER_TIMEOUT_SECONDS", "600"))
    allow_non_git_repos: bool = os.getenv("MAIN_COMPUTER_AIDER_ALLOW_NON_GIT_REPOS", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    extra_env: Mapping[str, str] = field(default_factory=dict)
    fallback: bool = _env_flag("MAIN_COMPUTER_FALLBACK")


@dataclass(frozen=True)
class AiderActionRequest:
    repo_dir: str
    instruction: str
    files: list[str] = field(default_factory=list)
    model: str | None = None
    dry_run: bool = True
    extra_args: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None
    chat_history_file: str | None = None
    input_history_file: str | None = None
    fallback: bool = False


@dataclass(frozen=True)
class AiderActionResult:
    ok: bool
    dry_run: bool
    repo_dir: str
    git_root: str | None
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timeout_seconds: int
    timed_out: bool = False
    error: str | None = None
    first_output_ms: int | None = None
    first_output_stream: str | None = None


def append_aider_log(log_path: Path, event: str, **fields: object) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(_json_line(record) + "\n")


def parse_file_list(value: object) -> list[str]:
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    else:
        raw_items = str(value or "").replace(",", "\n").splitlines()
    return [item.strip() for item in raw_items if item.strip()]


def _json_line(record: Mapping[str, object]) -> str:
    import json

    return json.dumps(record, ensure_ascii=False, default=str)


def prepare_aider_action(request: AiderActionRequest, config: AiderAgentConfig) -> AiderActionResult:
    repo, git_root = _resolve_repo_dir(request.repo_dir, config)
    safe_files = _validate_repo_relative_files(repo, request.files)
    command = _build_command(request, config, safe_files)
    timeout_seconds = _resolved_timeout_seconds(request, config)
    return AiderActionResult(
        ok=True,
        dry_run=request.dry_run,
        repo_dir=str(repo),
        git_root=str(git_root) if git_root else None,
        command=command,
        returncode=None,
        stdout="",
        stderr="",
        duration_ms=0,
        timeout_seconds=timeout_seconds,
    )


def run_aider_action(
    request: AiderActionRequest,
    config: AiderAgentConfig,
    *,
    output_callback: AiderOutputCallback | None = None,
    response_file: Path | None = None,
    stream_to_console: bool = True,
) -> AiderActionResult:
    prepared = prepare_aider_action(request, config)
    started = time.monotonic()
    fallback = bool(request.fallback or config.fallback)
    effective_stream_to_console = True if fallback else stream_to_console
    env = _build_subprocess_env(config)
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    write_lock = threading.Lock()

    response_handle: TextIO | None = None
    if response_file is not None:
        response_file.parent.mkdir(parents=True, exist_ok=True)
        response_handle = response_file.open("a", encoding=AIDER_SUBPROCESS_OUTPUT_ENCODING, errors="replace")

    first_output: dict[str, object] = {"ms": None, "stream": None}
    if fallback:
        _emit_subprocess_text(
            "fallback",
            _fallback_start_text(prepared),
            sys.stderr,
            write_lock,
            output_callback,
            response_handle,
            effective_stream_to_console,
            started=started,
            fallback=False,
            first_output=None,
        )

    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            prepared.command,
            cwd=prepared.repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=env,
            text=False,
            bufsize=0,
        )

        threads = [
            threading.Thread(
                target=_capture_subprocess_stream,
                args=(
                    "stdout",
                    process.stdout,
                    stdout_chunks,
                    sys.stdout,
                    write_lock,
                    output_callback,
                    response_handle,
                    effective_stream_to_console,
                    started,
                    fallback,
                    first_output,
                ),
                daemon=True,
                name="aider-stdout-reader",
            ),
            threading.Thread(
                target=_capture_subprocess_stream,
                args=(
                    "stderr",
                    process.stderr,
                    stderr_chunks,
                    sys.stderr,
                    write_lock,
                    output_callback,
                    response_handle,
                    effective_stream_to_console,
                    started,
                    fallback,
                    first_output,
                ),
                daemon=True,
                name="aider-stderr-reader",
            ),
        ]
        for thread in threads:
            thread.start()

        timed_out = False
        returncode: int | None
        error: str | None = None
        try:
            returncode = process.wait(timeout=prepared.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = None
            error = f"Aider action timed out after {prepared.timeout_seconds} seconds."
            timeout_text = f"\n{error}\n"
            stderr_chunks.append(timeout_text.encode(AIDER_SUBPROCESS_OUTPUT_ENCODING, errors="replace"))
            _emit_subprocess_text(
                "stderr",
                timeout_text,
                sys.stderr,
                write_lock,
                output_callback,
                response_handle,
                effective_stream_to_console,
                started=started,
                fallback=False,
                first_output=None,
            )
            process.kill()
            process.wait()

        for thread in threads:
            thread.join(timeout=5)

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = _decode_subprocess_output(b"".join(stdout_chunks))
        stderr = _decode_subprocess_output(b"".join(stderr_chunks))
        return AiderActionResult(
            ok=(returncode == 0 and not timed_out),
            dry_run=request.dry_run,
            repo_dir=prepared.repo_dir,
            git_root=prepared.git_root,
            command=prepared.command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timeout_seconds=prepared.timeout_seconds,
            timed_out=timed_out,
            error=error,
            first_output_ms=first_output["ms"] if isinstance(first_output.get("ms"), int) else None,
            first_output_stream=str(first_output["stream"]) if first_output.get("stream") else None,
        )
    finally:
        if response_handle is not None:
            response_handle.close()


def _capture_subprocess_stream(
    stream_name: str,
    pipe: object,
    chunks: list[bytes],
    console: TextIO,
    write_lock: threading.Lock,
    output_callback: AiderOutputCallback | None,
    response_handle: TextIO | None,
    stream_to_console: bool,
    started: float | None = None,
    fallback: bool = False,
    first_output: dict[str, object] | None = None,
) -> None:
    if pipe is None:
        return
    try:
        while True:
            chunk = pipe.read(1) if fallback else pipe.readline()
            if not chunk:
                break
            if isinstance(chunk, str):
                raw = chunk.encode(AIDER_SUBPROCESS_OUTPUT_ENCODING, errors="replace")
                text = chunk
            else:
                raw = bytes(chunk)
                text = _decode_subprocess_output(raw)
            chunks.append(raw)
            _emit_subprocess_text(
                stream_name,
                text,
                console,
                write_lock,
                output_callback,
                response_handle,
                stream_to_console,
                started=started,
                fallback=fallback,
                first_output=first_output,
            )
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _emit_subprocess_text(
    stream_name: str,
    text: str,
    console: TextIO,
    write_lock: threading.Lock,
    output_callback: AiderOutputCallback | None,
    response_handle: TextIO | None,
    stream_to_console: bool,
    *,
    started: float | None = None,
    fallback: bool = False,
    first_output: dict[str, object] | None = None,
) -> None:
    if not text:
        return
    with write_lock:
        if fallback and first_output is not None and first_output.get("ms") is None and stream_name in {"stdout", "stderr"}:
            elapsed_ms = int((time.monotonic() - started) * 1000) if started is not None else 0
            first_output["ms"] = elapsed_ms
            first_output["stream"] = stream_name
            marker = f"\n[fallback] first {stream_name} output after {elapsed_ms} ms\n"
            if stream_to_console:
                try:
                    console.write(marker)
                    console.flush()
                except Exception:
                    pass
            if response_handle is not None:
                response_handle.write(marker)
                response_handle.flush()
        if stream_to_console:
            try:
                console.write(text)
                console.flush()
            except Exception:
                pass
        if response_handle is not None:
            response_handle.write(text)
            response_handle.flush()
    if output_callback is not None:
        output_callback(stream_name, text)


def _fallback_start_text(prepared: AiderActionResult) -> str:
    return "\n".join(
        [
            "[fallback] Aider fallback mode enabled",
            f"[fallback] cwd={prepared.repo_dir}",
            f"[fallback] timeout_seconds={prepared.timeout_seconds}",
            f"[fallback] command={shlex.join(prepared.command)}",
            "[fallback] stream capture=byte-immediate; console streaming=forced",
            "",
        ]
    )


def _build_subprocess_env(config: AiderAgentConfig) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in AIDER_SUBPROCESS_ENV_DEFAULTS.items():
        env.setdefault(key, value)
    env.update(config.extra_env)
    return env


def _decode_subprocess_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode(AIDER_SUBPROCESS_OUTPUT_ENCODING, errors="replace")


def _resolved_timeout_seconds(request: AiderActionRequest, config: AiderAgentConfig) -> int:
    value = config.timeout_seconds if request.timeout_seconds is None else request.timeout_seconds
    try:
        timeout_seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise AiderValidationError("Aider timeout must be a whole number of seconds.") from exc
    if timeout_seconds < 1:
        raise AiderValidationError("Aider timeout must be at least 1 second.")
    return timeout_seconds


def _resolve_repo_dir(repo_dir: str, config: AiderAgentConfig) -> tuple[Path, Path | None]:
    workspace = config.workspace.expanduser().resolve()
    repo = (workspace / repo_dir).resolve() if not Path(repo_dir).is_absolute() else Path(repo_dir).expanduser().resolve()

    if not repo.exists():
        raise AiderValidationError(f"Repository path does not exist: {repo}")
    if not repo.is_dir():
        raise AiderValidationError(f"Repository path is not a directory: {repo}")
    try:
        repo.relative_to(workspace)
    except ValueError as exc:
        raise AiderValidationError("Repository path must stay inside the local workspace.") from exc

    git_root = _find_git_root(repo)
    if not git_root and not config.allow_non_git_repos:
        raise AiderValidationError(
            f"Repository path is not inside a git worktree: {repo}. "
            "Set MAIN_COMPUTER_AIDER_ALLOW_NON_GIT_REPOS=true to override."
        )
    return repo, git_root


def _find_git_root(repo: Path) -> Path | None:
    for candidate in [repo, *repo.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _validate_repo_relative_files(repo: Path, files: Sequence[str]) -> list[str]:
    safe_files: list[str] = []
    for raw in files:
        rel = raw.strip().replace("\\", "/")
        if not rel:
            raise AiderValidationError("File paths must not be empty.")
        candidate = (repo / rel).resolve()
        try:
            candidate.relative_to(repo)
        except ValueError as exc:
            raise AiderValidationError(f"File path escapes repository root: {raw}") from exc
        safe_files.append(rel)
    return safe_files


def _build_command(request: AiderActionRequest, config: AiderAgentConfig, safe_files: Sequence[str]) -> list[str]:
    instruction = request.instruction.strip()
    fallback = bool(request.fallback or config.fallback)
    if not instruction:
        raise AiderValidationError("Instruction is required.")
    command = [
        *shlex.split(config.aider_bin),
        "--yes-always",
        "--no-pretty",
        "--subtree-only",
        "--encoding",
        "utf-8",
        "--model",
        request.model or config.default_model,
        "--message",
        instruction,
    ]
    if fallback:
        command.extend(["--stream", "--verbose"])
    else:
        command.append("--no-show-model-warnings")
    if request.chat_history_file:
        command.extend(["--restore-chat-history", "--chat-history-file", request.chat_history_file])
    else:
        command.append("--no-restore-chat-history")
    if request.input_history_file:
        command.extend(["--input-history-file", request.input_history_file])
    if request.dry_run:
        command.append("--dry-run")
    if request.extra_args:
        command.extend(request.extra_args)
    command.extend(safe_files)
    return command
