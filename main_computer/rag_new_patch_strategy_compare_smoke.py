from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = Path("debug_assets") / "rag_new_patch_strategy_compare"
DEFAULT_DOCKER_IMAGE = "main-computer-executor:latest"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma4:26b"

NEW_PATCH_SPEC = r"""
Write a robust, self-contained Python implementation or replacement for a patch-application script named new_patch.py.

You do not have the current source code. Do not assume any existing internals. Solve from this interface specification only:

- It runs from a repository root as: python new_patch.py <artifact.zip> [--dry-run].
- <artifact.zip> is a changed-files snapshot containing full replacement files.
- Zip entries may have Windows separators, CRLF content, wrapper folders, or repo/repo nesting.
- Normalize zip paths to safe repo-relative POSIX paths.
- Reject absolute paths, drive-rooted paths, and traversal paths.
- Dry-run must compare incoming replacement files against local files and print the actual unified diff that would result, without modifying local files.
- Apply mode must create undo data before writing any replacement file, then write replacements.
- The undo data must be usable after the run, and the script must print an undo command on exit.
- If a reference.patch file exists in or next to the artifact, compare it with the actual local diff and report mismatch/fuzz. If no reference.patch exists, do not mention fuzz.
- Snapshot omission does not mean deletion. Do not delete files unless deletion is explicitly represented.
- Preserve line endings when possible and avoid whitespace churn.
- State touched files, assumptions, verification performed, warnings, and the dry-run command.

Return code that is complete enough to run.
"""


@dataclass
class GenerationResult:
    ok: bool
    strategy: str
    output_dir: Path
    candidate_path: Path | None
    raw_response_path: Path | None
    timings: dict[str, Any]
    errors: list[str]
    warnings: list[str]


class PartialGenerationError(RuntimeError):
    """Raised when generation fails after producing inspectable partial output."""

    def __init__(
        self,
        message: str,
        *,
        partial_text: str,
        partial_thinking: str,
        timings: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.partial_text = partial_text
        self.partial_thinking = partial_thinking
        self.timings = timings


def log(message: str = "") -> None:
    print(message, flush=True)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "run"


def repo_root_from_cwd() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "new_patch.py").exists() and (cwd / "debug_assets").exists():
        return cwd
    raise SystemExit("Run from repo root. Expected to find new_patch.py and debug_assets/.")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def repo_rel(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def bounded(text: str, max_chars: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw
    half = max(1, max_chars // 2)
    return raw[:half] + "\n\n... [middle omitted] ...\n\n" + raw[-half:]


def strip_markdown_code_fence(text: str) -> str:
    raw = str(text or "").strip()
    match = re.search(r"```(?:python|py)?\s*(.*?)```", raw, flags=re.S | re.I)
    if match:
        return match.group(1).strip() + "\n"
    return raw + ("\n" if raw and not raw.endswith("\n") else "")


def extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(raw[start : end + 1])
        if isinstance(value, dict):
            return value

    raise ValueError("No parseable JSON object found.")


def build_ollama_options(*, temperature: float, ollama_num_ctx: int) -> dict[str, Any]:
    options: dict[str, Any] = {"temperature": temperature}
    if int(ollama_num_ctx or 0) > 0:
        options["num_ctx"] = int(ollama_num_ctx)
    return options


def build_ollama_generate_payload(
    *,
    prompt: str,
    model: str,
    temperature: float,
    ollama_num_ctx: int,
    ollama_keep_alive: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": build_ollama_options(temperature=temperature, ollama_num_ctx=ollama_num_ctx),
    }
    if str(ollama_keep_alive or "").strip():
        payload["keep_alive"] = str(ollama_keep_alive).strip()
    return payload


def build_ollama_chat_payload(
    *,
    prompt: str,
    model: str,
    temperature: float,
    ollama_num_ctx: int,
    ollama_keep_alive: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": True,
        "options": build_ollama_options(temperature=temperature, ollama_num_ctx=ollama_num_ctx),
    }
    if str(ollama_keep_alive or "").strip():
        payload["keep_alive"] = str(ollama_keep_alive).strip()
    return payload


def build_ollama_payload(
    *,
    prompt: str,
    model: str,
    temperature: float,
    ollama_num_ctx: int,
    ollama_keep_alive: str,
    ollama_api: str,
) -> tuple[str, dict[str, Any]]:
    api = str(ollama_api or "generate").strip().lower()
    if api == "generate":
        return "/api/generate", build_ollama_generate_payload(
            prompt=prompt,
            model=model,
            temperature=temperature,
            ollama_num_ctx=ollama_num_ctx,
            ollama_keep_alive=ollama_keep_alive,
        )
    if api == "chat":
        return "/api/chat", build_ollama_chat_payload(
            prompt=prompt,
            model=model,
            temperature=temperature,
            ollama_num_ctx=ollama_num_ctx,
            ollama_keep_alive=ollama_keep_alive,
        )
    raise ValueError(f"unsupported ollama_api={ollama_api!r}; expected generate or chat")


def extract_ollama_event_text(event: dict[str, Any], *, ollama_api: str) -> tuple[str, str]:
    """Return (final_text_delta, thinking_delta) from Ollama generate or chat events."""

    api = str(ollama_api or "generate").strip().lower()
    if api == "chat":
        message = event.get("message")
        if not isinstance(message, dict):
            message = {}
        chunk = str(message.get("content") or "")
        thinking = str(
            message.get("thinking")
            or message.get("reasoning")
            or event.get("thinking")
            or event.get("reasoning")
            or ""
        )
        return chunk, thinking

    chunk = str(event.get("response") or "")
    thinking = str(event.get("thinking") or event.get("reasoning") or "")
    return chunk, thinking


def is_ollama_strategy(strategy: str) -> bool:
    return str(strategy or "").strip() in {
        "direct_code",
        "direct_base64",
        "direct_repair_loop",
    }


def skipped_generation_result(
    *,
    suite_dir: Path,
    strategy: str,
    reason: str,
    preflight: dict[str, Any] | None = None,
) -> GenerationResult:
    out_dir = suite_dir / strategy
    out_dir.mkdir(parents=True, exist_ok=True)
    timings = {
        "status": "skipped",
        "skip_reason": reason,
        "preflight": preflight or {},
    }
    write_json(out_dir / "timings.json", timings)
    return GenerationResult(
        ok=False,
        strategy=strategy,
        output_dir=out_dir,
        candidate_path=None,
        raw_response_path=None,
        timings=timings,
        errors=[reason],
        warnings=[],
    )



def classify_generation_timeout(
    *,
    first_content_s: float | None,
    content_chars: int,
    thinking_chars: int,
    source: str,
) -> str:
    if source == "idle":
        if content_chars > 0:
            return "content_stall_timeout"
        if thinking_chars > 0:
            return "thinking_only_timeout"
        return "no_first_content_timeout"
    if source == "thinking_only":
        return "thinking_only_timeout"
    if source == "content_stall":
        return "content_stall_timeout"
    if content_chars > 0 or thinking_chars > 0:
        return "total_timeout_after_partial_output"
    return "no_first_content_timeout"


def build_generation_timings(
    *,
    started: float,
    first_content_s: float | None,
    content: str,
    thinking: str,
    event_count: int,
    final_event: dict[str, Any],
    status: str,
    timeout_subtype: str = "",
    error: str = "",
    first_event_s: float | None = None,
    last_event_s: float | None = None,
    heartbeat_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total_s = time.monotonic() - started
    timings: dict[str, Any] = {
        "status": status,
        "total_wall_s": round(total_s, 3),
        "first_event_s": round(first_event_s, 3) if first_event_s is not None else None,
        "first_content_s": round(first_content_s, 3) if first_content_s is not None else None,
        "last_event_s": round(last_event_s, 3) if last_event_s is not None else None,
        "content_chars": len(content),
        "thinking_chars": len(thinking),
        "chars_per_sec": round(len(content) / total_s, 3) if total_s > 0 else 0,
        "event_count": event_count,
        "final_event": final_event,
    }
    if timeout_subtype:
        timings["timeout_subtype"] = timeout_subtype
    if error:
        timings["error"] = error
    if heartbeat_snapshots:
        timings["heartbeat_snapshots"] = heartbeat_snapshots
    return timings


def ollama_ps_snapshot() -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["ollama", "ps"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "elapsed_s": round(time.monotonic() - started, 3),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "one_line": " | ".join(line.strip() for line in proc.stdout.splitlines() if line.strip()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def append_text(path: Path, text: str) -> None:
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass



def ollama_generate_stream(
    *,
    prompt: str,
    model: str,
    ollama_url: str,
    timeout_s: float,
    temperature: float,
    output_dir: Path,
    first_event_timeout_s: float = 600.0,
    thinking_only_timeout_s: float = 300.0,
    thinking_only_min_chars: int = 1000,
    content_stall_timeout_s: float = 180.0,
    heartbeat_s: float = 10.0,
    ollama_ps_on_wait: bool = False,
    ollama_num_ctx: int = 8192,
    ollama_keep_alive: str = "5m",
    ollama_api: str = "generate",
) -> tuple[str, dict[str, Any]]:
    """Generate via Ollama and persist provider stream events immediately.

    Raw stream lines are written at the exact point the provider reader receives
    them. If raw_line_count remains zero, the Python HTTP client never received
    a stream line from Ollama.
    """

    api_path, payload = build_ollama_payload(
        prompt=prompt,
        model=model,
        temperature=temperature,
        ollama_num_ctx=ollama_num_ctx,
        ollama_keep_alive=ollama_keep_alive,
        ollama_api=ollama_api,
    )
    url = ollama_url.rstrip("/") + api_path
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    provider_raw_path = output_dir / "provider_stream_raw.jsonl"
    provider_events_path = output_dir / "provider_stream_events.jsonl"
    response_stream_path = output_dir / "raw_response.stream.txt"
    thinking_stream_path = output_dir / "thinking.stream.txt"
    timings_partial_path = output_dir / "timings.partial.json"

    for path in (
        provider_raw_path,
        provider_events_path,
        response_stream_path,
        thinking_stream_path,
        timings_partial_path,
    ):
        if path.exists():
            path.unlink()

    started = time.monotonic()
    state: dict[str, Any] = {
        "status": "running",
        "first_byte_s": None,
        "first_event_s": None,
        "first_content_s": None,
        "last_event_s": None,
        "last_content_s": None,
        "last_thinking_s": None,
        "raw_line_count": 0,
        "event_count": 0,
        "content_chars": 0,
        "thinking_chars": 0,
        "final_event": {},
        "error": "",
        "done": False,
        "ollama_api": str(ollama_api or "generate").strip().lower(),
    }
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    heartbeat_snapshots: list[dict[str, Any]] = []
    lock = threading.Lock()

    def snapshot_state() -> tuple[str, str, dict[str, Any]]:
        with lock:
            elapsed = time.monotonic() - started
            content = "".join(content_parts)
            thinking = "".join(thinking_parts)
            snapshot = dict(state)
            snapshot.update(
                {
                    "total_wall_s": round(elapsed, 3),
                    "content_chars": len(content),
                    "thinking_chars": len(thinking),
                    "chars_per_sec": round(len(content) / elapsed, 3) if elapsed > 0 else 0,
                    "heartbeat_snapshots": list(heartbeat_snapshots),
                    "provider_raw_path": str(provider_raw_path),
                    "provider_events_path": str(provider_events_path),
                    "response_stream_path": str(response_stream_path),
                    "thinking_stream_path": str(thinking_stream_path),
                }
            )
            return content, thinking, snapshot

    def write_partial_state() -> None:
        _content, _thinking, snapshot = snapshot_state()
        write_json(timings_partial_path, snapshot)

    def worker() -> None:
        socket_timeout_s = float(timeout_s) if not first_event_timeout_s or first_event_timeout_s <= 0 else max(1.0, min(float(timeout_s), float(first_event_timeout_s)))
        try:
            with urllib.request.urlopen(request, timeout=socket_timeout_s) as response:
                for raw_line in response:
                    elapsed = time.monotonic() - started
                    with lock:
                        if state.get("done"):
                            return
                    if elapsed > timeout_s:
                        with lock:
                            state["status"] = "timeout"
                            state["error"] = f"Ollama generation exceeded timeout_s={timeout_s}"
                            state["done"] = True
                        return

                    decoded = raw_line.decode("utf-8", errors="replace")
                    stripped = decoded.strip()
                    with lock:
                        if state["first_byte_s"] is None:
                            state["first_byte_s"] = elapsed
                        state["raw_line_count"] = int(state["raw_line_count"]) + 1
                        raw_line_index = int(state["raw_line_count"])

                    append_jsonl(
                        provider_raw_path,
                        {
                            "elapsed_s": round(elapsed, 3),
                            "raw_line_index": raw_line_index,
                            "raw_line": stripped,
                        },
                    )

                    if not stripped:
                        write_partial_state()
                        continue

                    try:
                        event = json.loads(stripped)
                    except Exception as exc:
                        append_jsonl(
                            provider_events_path,
                            {
                                "elapsed_s": round(elapsed, 3),
                                "raw_line_index": raw_line_index,
                                "parse_error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        write_partial_state()
                        continue

                    chunk, thinking = extract_ollama_event_text(event, ollama_api=ollama_api)

                    with lock:
                        if state["first_event_s"] is None:
                            state["first_event_s"] = elapsed
                        state["last_event_s"] = elapsed
                        state["event_count"] = int(state["event_count"]) + 1
                        event_index = int(state["event_count"])

                        if chunk:
                            if state["first_content_s"] is None:
                                state["first_content_s"] = elapsed
                            state["last_content_s"] = elapsed
                            content_parts.append(chunk)
                            state["content_chars"] = int(state["content_chars"]) + len(chunk)

                        if thinking:
                            state["last_thinking_s"] = elapsed
                            thinking_parts.append(thinking)
                            state["thinking_chars"] = int(state["thinking_chars"]) + len(thinking)

                        if event.get("done"):
                            state["final_event"] = event
                            state["status"] = "completed"
                            state["done"] = True

                    if chunk:
                        append_text(response_stream_path, chunk)
                    if thinking:
                        append_text(thinking_stream_path, thinking)

                    append_jsonl(
                        provider_events_path,
                        {
                            "elapsed_s": round(elapsed, 3),
                            "event_index": event_index,
                            "raw_line_index": raw_line_index,
                            "done": bool(event.get("done")),
                            "response_chars_delta": len(chunk),
                            "thinking_chars_delta": len(thinking),
                            "content_chars": int(state["content_chars"]),
                            "thinking_chars": int(state["thinking_chars"]),
                            "keys": sorted(str(key) for key in event.keys()),
                            "ollama_api": str(ollama_api or "generate").strip().lower(),
                        },
                    )
                    write_partial_state()

                    if event.get("done"):
                        return

                with lock:
                    state["status"] = "stream_closed"
                    state["done"] = True
        except BaseException as exc:
            with lock:
                state["status"] = "error"
                state["error"] = f"{type(exc).__name__}: {exc}"
                state["done"] = True
            write_partial_state()

    log(f"POST {url}")
    log(
        f"model={model} api={ollama_api} prompt_chars={len(prompt)} timeout_s={timeout_s} "
        f"first_event_timeout_s={first_event_timeout_s} "
        f"thinking_only_timeout_s={thinking_only_timeout_s} "
        f"content_stall_timeout_s={content_stall_timeout_s} "
        f"num_ctx={ollama_num_ctx} keep_alive={ollama_keep_alive}"
    )
    log(f"provider_raw_stream={provider_raw_path}")
    log(f"provider_event_stream={provider_events_path}")
    log(f"response_stream={response_stream_path}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    next_heartbeat = time.monotonic() + max(1.0, heartbeat_s)

    while thread.is_alive():
        elapsed = time.monotonic() - started
        content, thinking, snapshot = snapshot_state()
        first_event_s = snapshot.get("first_event_s")

        if first_event_timeout_s and first_event_timeout_s > 0 and first_event_s is None and elapsed >= first_event_timeout_s:
            with lock:
                state["status"] = "timeout"
                state["error"] = f"no Ollama stream event within first_event_timeout_s={first_event_timeout_s}"
                state["done"] = True
            content, thinking, snapshot = snapshot_state()
            snapshot["timeout_subtype"] = "no_first_event_timeout"
            write_json(timings_partial_path, snapshot)
            raise PartialGenerationError(
                f"no_first_event_timeout after {elapsed:.1f}s; "
                f"raw_line_count={snapshot.get('raw_line_count')} event_count={snapshot.get('event_count')}",
                partial_text=content,
                partial_thinking=thinking,
                timings=snapshot,
            )

        if (
            thinking_only_timeout_s
            and thinking_only_timeout_s > 0
            and first_event_s is not None
            and snapshot.get("first_content_s") is None
            and len(thinking) >= max(0, int(thinking_only_min_chars))
            and elapsed - float(first_event_s) >= float(thinking_only_timeout_s)
        ):
            with lock:
                state["status"] = "timeout"
                state["error"] = (
                    f"thinking-only stream exceeded thinking_only_timeout_s={thinking_only_timeout_s} "
                    f"without any final content"
                )
                state["done"] = True
            content, thinking, snapshot = snapshot_state()
            snapshot["status"] = "timeout"
            snapshot["timeout_subtype"] = "thinking_only_timeout"
            snapshot["error"] = (
                f"thinking-only stream exceeded thinking_only_timeout_s={thinking_only_timeout_s} "
                f"without any final content"
            )
            write_json(timings_partial_path, snapshot)
            raise PartialGenerationError(
                f"thinking_only_timeout after {elapsed:.1f}s; "
                f"raw_line_count={snapshot.get('raw_line_count')} event_count={snapshot.get('event_count')} "
                f"content_chars={len(content)} thinking_chars={len(thinking)}",
                partial_text=content,
                partial_thinking=thinking,
                timings=snapshot,
            )

        if (
            content_stall_timeout_s
            and content_stall_timeout_s > 0
            and snapshot.get("last_content_s") is not None
            and elapsed - float(snapshot.get("last_content_s")) >= float(content_stall_timeout_s)
        ):
            with lock:
                state["status"] = "timeout"
                state["error"] = (
                    f"final content stalled for content_stall_timeout_s={content_stall_timeout_s}"
                )
                state["done"] = True
            content, thinking, snapshot = snapshot_state()
            snapshot["status"] = "timeout"
            snapshot["timeout_subtype"] = "content_stall_timeout"
            snapshot["error"] = f"final content stalled for content_stall_timeout_s={content_stall_timeout_s}"
            write_json(timings_partial_path, snapshot)
            raise PartialGenerationError(
                f"content_stall_timeout after {elapsed:.1f}s; "
                f"raw_line_count={snapshot.get('raw_line_count')} event_count={snapshot.get('event_count')} "
                f"content_chars={len(content)} thinking_chars={len(thinking)}",
                partial_text=content,
                partial_thinking=thinking,
                timings=snapshot,
            )

        if elapsed >= timeout_s:
            content, thinking, snapshot = snapshot_state()
            if snapshot.get("first_event_s") is None:
                subtype = "no_first_event_total_timeout"
            elif len(content) == 0 and len(thinking) > 0:
                subtype = "thinking_only_total_timeout"
            else:
                subtype = "total_timeout_after_partial_output"
            with lock:
                state["status"] = "timeout"
                state["error"] = f"Ollama generation exceeded timeout_s={timeout_s}"
                state["done"] = True
            snapshot["status"] = "timeout"
            snapshot["timeout_subtype"] = subtype
            snapshot["error"] = f"Ollama generation exceeded timeout_s={timeout_s}"
            write_json(timings_partial_path, snapshot)
            raise PartialGenerationError(
                f"{subtype} after {elapsed:.1f}s; "
                f"raw_line_count={snapshot.get('raw_line_count')} event_count={snapshot.get('event_count')} "
                f"content_chars={len(content)} thinking_chars={len(thinking)}",
                partial_text=content,
                partial_thinking=thinking,
                timings=snapshot,
            )

        if time.monotonic() >= next_heartbeat:
            phase = "waiting_for_first_stream_event" if first_event_s is None else "streaming"
            log(
                f"ollama request {phase} elapsed_s={elapsed:.1f} "
                f"raw_lines={snapshot.get('raw_line_count')} events={snapshot.get('event_count')} "
                f"content_chars={len(content)} thinking_chars={len(thinking)}"
            )
            if ollama_ps_on_wait:
                ps = ollama_ps_snapshot()
                ps["elapsed_since_request_s"] = round(elapsed, 3)
                ps["phase"] = phase
                heartbeat_snapshots.append(ps)
                if ps.get("ok"):
                    log("ollama ps: " + str(ps.get("one_line") or "").strip())
                elif ps.get("error"):
                    log(f"ollama ps error: {ps.get('error')}")
                else:
                    log(f"ollama ps error: {ps.get('stderr') or 'unknown'}")
            write_partial_state()
            next_heartbeat = time.monotonic() + max(1.0, heartbeat_s)

        thread.join(timeout=0.25)

    content, thinking, snapshot = snapshot_state()
    if snapshot.get("error"):
        subtype = classify_generation_timeout(
            first_content_s=snapshot.get("first_content_s"),
            content_chars=len(content),
            thinking_chars=len(thinking),
            source="idle",
        )
        if snapshot.get("first_event_s") is None:
            subtype = "no_first_event_timeout"
        snapshot["timeout_subtype"] = subtype if "timeout" in str(snapshot.get("error")).lower() or snapshot.get("first_event_s") is None else ""
        write_json(timings_partial_path, snapshot)
        raise PartialGenerationError(
            str(snapshot.get("error")),
            partial_text=content,
            partial_thinking=thinking,
            timings=snapshot,
        )

    if not snapshot.get("timeout_subtype"):
        if int(snapshot.get("event_count") or 0) == 0:
            snapshot["timeout_subtype"] = "no_events_completed"
        elif len(content) == 0 and len(thinking) > 0:
            snapshot["timeout_subtype"] = "thinking_only_completed"
        else:
            snapshot["timeout_subtype"] = ""

    write_json(timings_partial_path, snapshot)
    return content, snapshot


def run_docker(
    *,
    repo_root: Path,
    workdir: Path,
    command: str,
    docker_image: str,
    timeout_s: float,
) -> dict[str, Any]:
    work_rel = repo_rel(repo_root, workdir)
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{str(repo_root)}:/workspace",
        "-w",
        f"/workspace/{work_rel}",
        docker_image,
        "sh",
        "-lc",
        command,
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "elapsed_s": round(time.monotonic() - started, 3),
            "command": cmd,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": f"timeout after {timeout_s}s",
            "command": cmd,
        }


def docker_py_compile(repo_root: Path, candidate: Path, docker_image: str, timeout_s: float) -> dict[str, Any]:
    rel = repo_rel(repo_root, candidate)
    py_literal = json.dumps(rel)
    command = (
        "python - <<'PY'\n"
        "import py_compile\n"
        f"py_compile.compile({py_literal}, doraise=True)\n"
        f"print('PY_COMPILE_OK {rel}')\n"
        "PY"
    )
    return run_docker(
        repo_root=repo_root,
        workdir=repo_root,
        command=command,
        docker_image=docker_image,
        timeout_s=timeout_s,
    )


def make_zip(path: Path, entries: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)


def run_fixture_suite(
    *,
    repo_root: Path,
    candidate_path: Path,
    strategy_dir: Path,
    docker_image: str,
    docker_timeout_s: float,
) -> dict[str, Any]:
    fixture_root = strategy_dir / "fixture_workspace"
    if fixture_root.exists():
        shutil.rmtree(fixture_root)
    fixture_root.mkdir(parents=True)

    candidate_code = candidate_path.read_text(encoding="utf-8", errors="replace")
    fixture_candidate = fixture_root / "new_patch.py"
    fixture_candidate.write_text(candidate_code, encoding="utf-8")

    results: dict[str, Any] = {
        "checks": {},
        "commands": {},
    }

    compile_result = docker_py_compile(repo_root, fixture_candidate, docker_image, docker_timeout_s)
    results["commands"]["py_compile"] = compile_result
    results["checks"]["py_compile"] = bool(compile_result.get("ok"))

    help_result = run_docker(
        repo_root=repo_root,
        workdir=fixture_root,
        command="python new_patch.py --help",
        docker_image=docker_image,
        timeout_s=docker_timeout_s,
    )
    help_text = str(help_result.get("stdout") or "") + str(help_result.get("stderr") or "")
    results["commands"]["help"] = help_result
    results["checks"]["help_works"] = bool(help_result.get("ok")) and "--dry-run" in help_text

    dry_repo = fixture_root / "dry_run_repo"
    dry_repo.mkdir()
    (dry_repo / "new_patch.py").write_text(candidate_code, encoding="utf-8")
    (dry_repo / "hello.txt").write_text("old\n", encoding="utf-8")
    make_zip(dry_repo / "artifact.zip", {"hello.txt": b"new\n"})

    dry_result = run_docker(
        repo_root=repo_root,
        workdir=dry_repo,
        command="python new_patch.py artifact.zip --dry-run",
        docker_image=docker_image,
        timeout_s=docker_timeout_s,
    )
    dry_stdout = str(dry_result.get("stdout") or "")
    dry_stderr = str(dry_result.get("stderr") or "")
    dry_text = dry_stdout + dry_stderr
    results["commands"]["dry_run"] = dry_result
    results["checks"]["dry_run_non_mutating"] = (dry_repo / "hello.txt").read_text(encoding="utf-8") == "old\n"
    results["checks"]["dry_run_unified_diff"] = (
        bool(dry_result.get("ok"))
        and "---" in dry_text
        and "+++" in dry_text
        and "-old" in dry_text
        and "+new" in dry_text
    )

    apply_repo = fixture_root / "apply_repo"
    apply_repo.mkdir()
    (apply_repo / "new_patch.py").write_text(candidate_code, encoding="utf-8")
    (apply_repo / "hello.txt").write_text("old\n", encoding="utf-8")
    (apply_repo / "keep.txt").write_text("keep\n", encoding="utf-8")
    make_zip(apply_repo / "artifact.zip", {"hello.txt": b"new\n"})

    apply_result = run_docker(
        repo_root=repo_root,
        workdir=apply_repo,
        command="python new_patch.py artifact.zip",
        docker_image=docker_image,
        timeout_s=docker_timeout_s,
    )
    apply_text = str(apply_result.get("stdout") or "") + str(apply_result.get("stderr") or "")
    results["commands"]["apply"] = apply_result
    results["checks"]["apply_writes_replacement"] = (
        bool(apply_result.get("ok"))
        and (apply_repo / "hello.txt").exists()
        and (apply_repo / "hello.txt").read_text(encoding="utf-8", errors="replace") == "new\n"
    )
    results["checks"]["emits_undo_or_backup"] = any(
        token in apply_text.lower()
        for token in ("undo", "backup", "restore")
    )
    results["checks"]["omission_does_not_delete"] = (apply_repo / "keep.txt").exists()

    unsafe_repo = fixture_root / "unsafe_repo"
    unsafe_repo.mkdir()
    (unsafe_repo / "new_patch.py").write_text(candidate_code, encoding="utf-8")
    make_zip(
        unsafe_repo / "unsafe.zip",
        {
            "../evil.txt": b"evil\n",
            "C:/evil.txt": b"evil\n",
        },
    )
    unsafe_result = run_docker(
        repo_root=repo_root,
        workdir=unsafe_repo,
        command="python new_patch.py unsafe.zip",
        docker_image=docker_image,
        timeout_s=docker_timeout_s,
    )
    results["commands"]["unsafe_paths"] = unsafe_result
    escaped_candidates = [
        repo_root / "evil.txt",
        unsafe_repo.parent / "evil.txt",
        unsafe_repo / "C:" / "evil.txt",
    ]
    results["checks"]["rejects_unsafe_paths"] = (
        not bool(unsafe_result.get("ok"))
        and not any(path.exists() for path in escaped_candidates)
    )

    checks = results["checks"]
    score = 0
    score += 25 if checks.get("py_compile") else 0
    score += 10 if checks.get("help_works") else 0
    score += 10 if checks.get("dry_run_non_mutating") else 0
    score += 10 if checks.get("dry_run_unified_diff") else 0
    score += 15 if checks.get("apply_writes_replacement") else 0
    score += 10 if checks.get("emits_undo_or_backup") else 0
    score += 10 if checks.get("rejects_unsafe_paths") else 0
    score += 10 if checks.get("omission_does_not_delete") else 0

    if not checks.get("py_compile"):
        score = min(score, 20)

    results["quality_score"] = score
    results["passed_all"] = score == 100
    return results


def evaluate_candidate(
    *,
    repo_root: Path,
    strategy_dir: Path,
    candidate_path: Path,
    docker_image: str,
    docker_timeout_s: float,
) -> dict[str, Any]:
    results = run_fixture_suite(
        repo_root=repo_root,
        candidate_path=candidate_path,
        strategy_dir=strategy_dir,
        docker_image=docker_image,
        docker_timeout_s=docker_timeout_s,
    )
    write_json(strategy_dir / "fixture_results.json", results)
    write_json(
        strategy_dir / "quality.json",
        {
            "quality_score": results["quality_score"],
            "passed_all": results["passed_all"],
            "checks": results["checks"],
        },
    )
    if results["passed_all"]:
        (strategy_dir / "strategy_passed.txt").write_text("ok\n", encoding="utf-8")
    return results


def direct_code_prompt() -> str:
    return (
        "Return only Python source code. No markdown fences. No JSON. "
        "The code must be a complete runnable new_patch.py implementation.\n\n"
        + NEW_PATCH_SPEC
    )


def direct_base64_prompt() -> str:
    return (
        "Return exactly one valid JSON object and no markdown fences.\n"
        "Use this schema:\n"
        '{ "ok": true, "path": "new_patch.py", "content_base64": "<base64 UTF-8 Python source>" }\n\n'
        "Encode the complete runnable new_patch.py Python source as base64.\n\n"
        + NEW_PATCH_SPEC
    )


def repair_prompt(previous_source: str, compile_stderr: str) -> str:
    return (
        "Repair this generated new_patch.py candidate.\n"
        "Return only complete Python source code. No markdown fences. No JSON.\n"
        "Fix the compile/runtime issue first, then preserve intended behavior.\n\n"
        "Compile stderr:\n"
        + bounded(compile_stderr, 10_000)
        + "\n\nPrevious source:\n"
        + bounded(previous_source, 80_000)
    )


def run_ollama_preflight(
    *,
    suite_dir: Path,
    model: str,
    ollama_url: str,
    temperature: float,
    timeout_s: float,
    first_event_timeout_s: float,
    thinking_only_timeout_s: float,
    thinking_only_min_chars: int,
    content_stall_timeout_s: float,
    stream_heartbeat_s: float,
    ollama_ps_on_wait: bool,
    ollama_num_ctx: int,
    ollama_keep_alive: str,
    ollama_api: str,
) -> dict[str, Any]:
    """Probe Ollama before expensive strategies so model/server stalls are explicit."""

    out_dir = suite_dir / "ollama_preflight"
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = "Return exactly: OK"
    (out_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")

    result: dict[str, Any] = {
        "ok": False,
        "status": "not_run",
        "output_dir": str(out_dir),
        "prompt": prompt,
        "model": model,
        "ollama_url": ollama_url,
        "ollama_num_ctx": ollama_num_ctx,
        "ollama_keep_alive": ollama_keep_alive,
        "ollama_api": str(ollama_api or "generate").strip().lower(),
    }

    try:
        raw, timings = ollama_generate_stream(
            prompt=prompt,
            model=model,
            ollama_url=ollama_url,
            timeout_s=timeout_s,
            temperature=temperature,
            output_dir=out_dir,
            first_event_timeout_s=first_event_timeout_s,
            thinking_only_timeout_s=thinking_only_timeout_s,
            thinking_only_min_chars=thinking_only_min_chars,
            content_stall_timeout_s=content_stall_timeout_s,
            heartbeat_s=stream_heartbeat_s,
            ollama_ps_on_wait=ollama_ps_on_wait,
            ollama_num_ctx=ollama_num_ctx,
            ollama_keep_alive=ollama_keep_alive,
            ollama_api=ollama_api,
        )
        (out_dir / "raw_response.txt").write_text(raw, encoding="utf-8")
        result.update(
            {
                "ok": bool(timings.get("event_count")) and bool(raw.strip()),
                "status": "completed",
                "raw_response": raw,
                "timings": timings,
            }
        )
        if not result["ok"]:
            result["error"] = "preflight completed without stream events or final text"
    except PartialGenerationError as exc:
        partial_path, timings = preserve_partial_generation(out_dir=out_dir, exc=exc, label="raw_response")
        result.update(
            {
                "ok": False,
                "status": "failed",
                "error": str(exc),
                "partial_path": str(partial_path) if partial_path else "",
                "timings": timings,
            }
        )
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "timings": {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
            }
        )

    write_json(out_dir / "preflight_result.json", result)
    return result



def preserve_partial_generation(
    *,
    out_dir: Path,
    exc: PartialGenerationError,
    label: str = "raw_response",
) -> tuple[Path | None, dict[str, Any]]:
    partial_path: Path | None = None
    if exc.partial_text:
        partial_path = out_dir / f"{label}.partial.txt"
        partial_path.write_text(exc.partial_text, encoding="utf-8")
    if exc.partial_thinking:
        (out_dir / "thinking.partial.txt").write_text(exc.partial_thinking, encoding="utf-8")
    write_json(out_dir / "timings.partial.json", exc.timings)
    return partial_path, dict(exc.timings)



def run_direct_code_strategy(
    *,
    suite_dir: Path,
    model: str,
    ollama_url: str,
    timeout_s: float,
    temperature: float,
    first_event_timeout_s: float,
    thinking_only_timeout_s: float,
    thinking_only_min_chars: int,
    content_stall_timeout_s: float,
    stream_heartbeat_s: float,
    ollama_ps_on_wait: bool,
    ollama_num_ctx: int,
    ollama_keep_alive: str,
    ollama_api: str,
) -> GenerationResult:
    strategy = "direct_code"
    out_dir = suite_dir / strategy
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = direct_code_prompt()
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    errors: list[str] = []
    warnings: list[str] = []
    candidate_path: Path | None = None
    raw_response_path = out_dir / "raw_response.txt"
    timings: dict[str, Any] = {}

    try:
        raw, timings = ollama_generate_stream(
            prompt=prompt,
            model=model,
            ollama_url=ollama_url,
            timeout_s=timeout_s,
            temperature=temperature,
            output_dir=out_dir,
            first_event_timeout_s=first_event_timeout_s,
            thinking_only_timeout_s=thinking_only_timeout_s,
            thinking_only_min_chars=thinking_only_min_chars,
            content_stall_timeout_s=content_stall_timeout_s,
            heartbeat_s=stream_heartbeat_s,
            ollama_ps_on_wait=ollama_ps_on_wait,
            ollama_num_ctx=ollama_num_ctx,
            ollama_keep_alive=ollama_keep_alive,
            ollama_api=ollama_api,
        )
        raw_response_path.write_text(raw, encoding="utf-8")
        code = strip_markdown_code_fence(raw)
        candidate_path = out_dir / "candidate.py"
        candidate_path.write_text(code, encoding="utf-8")
    except PartialGenerationError as exc:
        partial_path, timings = preserve_partial_generation(out_dir=out_dir, exc=exc)
        if partial_path:
            raw_response_path = partial_path
        errors.append(f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        timings = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        errors.append(f"{type(exc).__name__}: {exc}")

    write_json(out_dir / "timings.json", timings)
    return GenerationResult(
        ok=bool(candidate_path and candidate_path.exists()),
        strategy=strategy,
        output_dir=out_dir,
        candidate_path=candidate_path,
        raw_response_path=raw_response_path if raw_response_path.exists() else None,
        timings=timings,
        errors=errors,
        warnings=warnings,
    )


def run_direct_base64_strategy(
    *,
    suite_dir: Path,
    model: str,
    ollama_url: str,
    timeout_s: float,
    temperature: float,
    first_event_timeout_s: float,
    thinking_only_timeout_s: float,
    thinking_only_min_chars: int,
    content_stall_timeout_s: float,
    stream_heartbeat_s: float,
    ollama_ps_on_wait: bool,
    ollama_num_ctx: int,
    ollama_keep_alive: str,
    ollama_api: str,
) -> GenerationResult:
    strategy = "direct_base64"
    out_dir = suite_dir / strategy
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = direct_base64_prompt()
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    errors: list[str] = []
    warnings: list[str] = []
    candidate_path: Path | None = None
    raw_response_path = out_dir / "raw_response.txt"
    timings: dict[str, Any] = {}

    try:
        raw, timings = ollama_generate_stream(
            prompt=prompt,
            model=model,
            ollama_url=ollama_url,
            timeout_s=timeout_s,
            temperature=temperature,
            output_dir=out_dir,
            first_event_timeout_s=first_event_timeout_s,
            thinking_only_timeout_s=thinking_only_timeout_s,
            thinking_only_min_chars=thinking_only_min_chars,
            content_stall_timeout_s=content_stall_timeout_s,
            heartbeat_s=stream_heartbeat_s,
            ollama_ps_on_wait=ollama_ps_on_wait,
            ollama_num_ctx=ollama_num_ctx,
            ollama_keep_alive=ollama_keep_alive,
            ollama_api=ollama_api,
        )
        raw_response_path.write_text(raw, encoding="utf-8")
        payload = extract_json_object(raw)
        write_json(out_dir / "parse_result.json", payload)
        encoded = str(payload.get("content_base64") or "")
        code = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
        candidate_path = out_dir / "candidate.py"
        candidate_path.write_text(code, encoding="utf-8")
    except PartialGenerationError as exc:
        partial_path, timings = preserve_partial_generation(out_dir=out_dir, exc=exc)
        if partial_path:
            raw_response_path = partial_path
        errors.append(f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        timings = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        errors.append(f"{type(exc).__name__}: {exc}")

    write_json(out_dir / "timings.json", timings)
    return GenerationResult(
        ok=bool(candidate_path and candidate_path.exists()),
        strategy=strategy,
        output_dir=out_dir,
        candidate_path=candidate_path,
        raw_response_path=raw_response_path if raw_response_path.exists() else None,
        timings=timings,
        errors=errors,
        warnings=warnings,
    )


def run_direct_repair_loop_strategy(
    *,
    repo_root: Path,
    suite_dir: Path,
    model: str,
    ollama_url: str,
    timeout_s: float,
    temperature: float,
    first_event_timeout_s: float,
    thinking_only_timeout_s: float,
    thinking_only_min_chars: int,
    content_stall_timeout_s: float,
    stream_heartbeat_s: float,
    ollama_ps_on_wait: bool,
    ollama_num_ctx: int,
    ollama_keep_alive: str,
    ollama_api: str,
    docker_image: str,
    docker_timeout_s: float,
    max_attempts: int,
) -> GenerationResult:
    strategy = "direct_repair_loop"
    out_dir = suite_dir / strategy
    out_dir.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    warnings: list[str] = []
    timings_total: dict[str, Any] = {
        "total_wall_s": 0.0,
        "attempts": [],
    }

    candidate_path: Path | None = None
    raw_response_path: Path | None = None

    prompt = direct_code_prompt()
    previous_source = ""
    compile_stderr = ""

    for attempt in range(1, max(1, max_attempts) + 1):
        attempt_dir = out_dir / f"attempt_{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        try:
            raw, timings = ollama_generate_stream(
                prompt=prompt,
                model=model,
                ollama_url=ollama_url,
                timeout_s=timeout_s,
                temperature=temperature,
                output_dir=attempt_dir,
                first_event_timeout_s=first_event_timeout_s,
                thinking_only_timeout_s=thinking_only_timeout_s,
                thinking_only_min_chars=thinking_only_min_chars,
                content_stall_timeout_s=content_stall_timeout_s,
                heartbeat_s=stream_heartbeat_s,
                ollama_ps_on_wait=ollama_ps_on_wait,
                ollama_num_ctx=ollama_num_ctx,
                ollama_keep_alive=ollama_keep_alive,
                ollama_api=ollama_api,
            )
            timings_total["total_wall_s"] += float(timings.get("total_wall_s") or 0)
            timings_total["attempts"].append({"attempt": attempt, **timings})

            raw_path = attempt_dir / "raw_response.txt"
            raw_path.write_text(raw, encoding="utf-8")
            raw_response_path = raw_path

            code = strip_markdown_code_fence(raw)
            candidate = attempt_dir / "candidate.py"
            candidate.write_text(code, encoding="utf-8")
            candidate_path = candidate
            previous_source = code

            compile_result = docker_py_compile(repo_root, candidate, docker_image, docker_timeout_s)
            write_json(attempt_dir / "compile.json", compile_result)
            if compile_result.get("ok"):
                shutil.copyfile(candidate, out_dir / "final_candidate.py")
                candidate_path = out_dir / "final_candidate.py"
                break

            compile_stderr = str(compile_result.get("stderr") or "")
            prompt = repair_prompt(previous_source, compile_stderr)
        except PartialGenerationError as exc:
            partial_path, partial_timings = preserve_partial_generation(out_dir=attempt_dir, exc=exc)
            if partial_path:
                raw_response_path = partial_path
            timings_total["total_wall_s"] += float(partial_timings.get("total_wall_s") or 0)
            timings_total["attempts"].append({"attempt": attempt, **partial_timings})
            errors.append(f"attempt_{attempt}: {type(exc).__name__}: {exc}")
            break
        except Exception as exc:
            errors.append(f"attempt_{attempt}: {type(exc).__name__}: {exc}")
            break

    write_json(out_dir / "timings.json", timings_total)
    write_json(out_dir / "repair_trace.json", timings_total)

    return GenerationResult(
        ok=bool(candidate_path and candidate_path.exists()),
        strategy=strategy,
        output_dir=out_dir,
        candidate_path=candidate_path,
        raw_response_path=raw_response_path,
        timings=timings_total,
        errors=errors,
        warnings=warnings,
    )


def load_baseline_from_output_dir(
    *,
    repo_root: Path,
    suite_dir: Path,
    baseline_output_dir: Path,
) -> GenerationResult:
    strategy = "baseline_rag_at"
    out_dir = suite_dir / strategy
    out_dir.mkdir(parents=True, exist_ok=True)

    source_dir = baseline_output_dir
    if not source_dir.is_absolute():
        source_dir = repo_root / source_dir
    source_dir = source_dir.resolve()

    errors: list[str] = []
    warnings: list[str] = []
    candidate_path: Path | None = None
    timings: dict[str, Any] = {}

    proposed = source_dir / "proposed_new_patch.py"
    master_results = source_dir / "master_results.json"

    if not proposed.exists():
        errors.append(f"baseline proposed_new_patch.py not found: {proposed}")
    else:
        candidate_path = out_dir / "candidate.py"
        shutil.copyfile(proposed, candidate_path)

    if master_results.exists():
        master = read_json(master_results)
        write_json(out_dir / "master_results.json", master)
        if isinstance(master, dict):
            timings["total_wall_s"] = master.get("elapsed_s")
            timings["content_chars"] = master.get("primary_content_chars")
            timings["thinking_chars"] = master.get("primary_thinking_chars")
            timings["source_output_dir"] = str(source_dir)

    write_json(out_dir / "timings.json", timings)
    return GenerationResult(
        ok=bool(candidate_path and candidate_path.exists()),
        strategy=strategy,
        output_dir=out_dir,
        candidate_path=candidate_path,
        raw_response_path=None,
        timings=timings,
        errors=errors,
        warnings=warnings,
    )


def generation_result_to_record(result: GenerationResult, quality: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "strategy": result.strategy,
        "ok": result.ok,
        "output_dir": str(result.output_dir),
        "candidate_path": str(result.candidate_path) if result.candidate_path else "",
        "raw_response_path": str(result.raw_response_path) if result.raw_response_path else "",
        "timings": result.timings,
        "quality_score": (quality or {}).get("quality_score", 0),
        "passed_all": bool((quality or {}).get("passed_all")),
        "checks": (quality or {}).get("checks", {}),
        "errors": result.errors,
        "warnings": result.warnings,
    }


def compare_to_baseline(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = next((item for item in records if item.get("strategy") == "baseline_rag_at"), None)
    if not baseline:
        for item in records:
            item["beats_baseline"] = None
            item["speedup_vs_baseline"] = None
        return records

    baseline_quality = float(baseline.get("quality_score") or 0)
    baseline_wall = baseline.get("timings", {}).get("total_wall_s")
    try:
        baseline_wall_f = float(baseline_wall)
    except Exception:
        baseline_wall_f = 0.0

    for item in records:
        if item.get("strategy") == "baseline_rag_at":
            item["beats_baseline"] = False
            item["speedup_vs_baseline"] = 1.0
            continue

        quality = float(item.get("quality_score") or 0)
        wall = item.get("timings", {}).get("total_wall_s")
        try:
            wall_f = float(wall)
        except Exception:
            wall_f = 0.0

        speedup = round(baseline_wall_f / wall_f, 3) if baseline_wall_f > 0 and wall_f > 0 else None
        item["speedup_vs_baseline"] = speedup

        beats = False
        if baseline_wall_f > 0 and wall_f > 0:
            beats = (
                quality >= baseline_quality and wall_f <= baseline_wall_f * 0.70
            ) or (
                quality >= baseline_quality + 30 and wall_f <= baseline_wall_f * 1.25
            )
        else:
            beats = quality >= baseline_quality + 30

        item["beats_baseline"] = beats

    return records


def sort_leaderboard(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda item: (
            -float(item.get("quality_score") or 0),
            not bool(item.get("passed_all")),
            float(item.get("timings", {}).get("total_wall_s") or 999999),
            str(item.get("strategy") or ""),
        ),
    )


def write_summary(suite_dir: Path, leaderboard: list[dict[str, Any]]) -> None:
    lines = ["rag_new_patch strategy compare smoke", ""]
    for index, item in enumerate(leaderboard, start=1):
        lines.append(
            f"{index}. {item.get('strategy')}: "
            f"quality={item.get('quality_score')} "
            f"passed_all={item.get('passed_all')} "
            f"wall_s={item.get('timings', {}).get('total_wall_s')} "
            f"speedup={item.get('speedup_vs_baseline')} "
            f"beats_baseline={item.get('beats_baseline')}"
        )
    lines.append("")
    winner = leaderboard[0] if leaderboard else {}
    if winner:
        lines.append(f"winner={winner.get('strategy')}")
    any_beats = any(item.get("beats_baseline") for item in leaderboard)
    lines.append(f"any_strategy_beats_baseline={any_beats}")
    (suite_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare direct new_patch.py generation strategies against current RAG output."
    )
    parser.add_argument("--baseline-output-dir", default="", help="Existing rag_new_patch_recreation_tester output dir to use as baseline.")
    parser.add_argument("--strategies", default="direct_code,direct_base64,direct_repair_loop")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument(
        "--ollama-api",
        choices=("generate", "chat"),
        default="generate",
        help="Ollama streaming endpoint to use for direct strategies and preflight.",
    )
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    parser.add_argument(
        "--first-event-timeout-s",
        type=float,
        default=600.0,
        help="Fail a generation attempt if Ollama sends no stream event within this many seconds.",
    )
    parser.add_argument(
        "--disable-first-event-timeout",
        action="store_true",
        help="Do not abort before the first Ollama stream event; rely only on --timeout-s.",
    )
    parser.add_argument(
        "--thinking-only-timeout-s",
        type=float,
        default=300.0,
        help=(
            "Fail a generation attempt if the model streams only thinking/reasoning "
            "and no final content for this many seconds after the first stream event."
        ),
    )
    parser.add_argument(
        "--thinking-only-min-chars",
        type=int,
        default=1000,
        help="Minimum thinking chars required before --thinking-only-timeout-s can fire.",
    )
    parser.add_argument(
        "--content-stall-timeout-s",
        type=float,
        default=180.0,
        help="Fail a generation attempt if final content starts and then stalls for this many seconds.",
    )
    parser.add_argument(
        "--stream-heartbeat-s",
        type=float,
        default=10.0,
        help="Print waiting/streaming progress at this interval during Ollama generation.",
    )
    parser.add_argument(
        "--ollama-ps-on-wait",
        action="store_true",
        help="Run `ollama ps` on first-event heartbeats and store the snapshots in timings.",
    )
    parser.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=8192,
        help="Set Ollama options.num_ctx for direct generation/preflight. Use 0 to omit.",
    )
    parser.add_argument(
        "--ollama-keep-alive",
        default="5m",
        help="Set Ollama keep_alive for direct generation/preflight. Empty string omits it.",
    )
    parser.add_argument(
        "--skip-ollama-preflight",
        action="store_true",
        help="Skip the small direct Ollama first-byte preflight before direct strategies.",
    )
    parser.add_argument(
        "--ollama-preflight-timeout-s",
        type=float,
        default=120.0,
        help="Total timeout for the small Ollama direct preflight.",
    )
    parser.add_argument(
        "--ollama-preflight-first-event-timeout-s",
        type=float,
        default=60.0,
        help="First-event timeout for the small Ollama direct preflight.",
    )
    parser.add_argument("--docker-timeout-s", type=float, default=120.0)
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-repair-attempts", type=int, default=2)
    args = parser.parse_args()

    repo_root = repo_root_from_cwd()
    run_id = slug(args.run_id or f"strategy_compare_{utc_stamp()}")
    suite_dir = (repo_root / args.output_root / run_id).resolve()
    suite_dir.mkdir(parents=True, exist_ok=True)

    log("--- rag_new_patch strategy compare smoke ---")
    log(f"repo_root={repo_root}")
    log(f"suite_dir={suite_dir}")
    log(f"model={args.model}")
    log(f"ollama_url={args.ollama_url}")
    log(f"ollama_api={args.ollama_api}")
    effective_first_event_timeout_s = 0.0 if args.disable_first_event_timeout else float(args.first_event_timeout_s)

    log(f"timeout_s={args.timeout_s}")
    log(f"first_event_timeout_s={effective_first_event_timeout_s}")
    log(f"disable_first_event_timeout={args.disable_first_event_timeout}")
    log(f"thinking_only_timeout_s={args.thinking_only_timeout_s}")
    log(f"thinking_only_min_chars={args.thinking_only_min_chars}")
    log(f"content_stall_timeout_s={args.content_stall_timeout_s}")
    log(f"stream_heartbeat_s={args.stream_heartbeat_s}")
    log(f"ollama_ps_on_wait={args.ollama_ps_on_wait}")
    log(f"ollama_num_ctx={args.ollama_num_ctx}")
    log(f"ollama_keep_alive={args.ollama_keep_alive}")
    log(f"skip_ollama_preflight={args.skip_ollama_preflight}")
    log(f"strategies={args.strategies}")

    records: list[dict[str, Any]] = []

    if args.baseline_output_dir:
        log("--- baseline_rag_at from existing output dir ---")
        baseline = load_baseline_from_output_dir(
            repo_root=repo_root,
            suite_dir=suite_dir,
            baseline_output_dir=Path(args.baseline_output_dir),
        )
        baseline_quality: dict[str, Any] | None = None
        if baseline.candidate_path:
            baseline_quality = evaluate_candidate(
                repo_root=repo_root,
                strategy_dir=baseline.output_dir,
                candidate_path=baseline.candidate_path,
                docker_image=args.docker_image,
                docker_timeout_s=args.docker_timeout_s,
            )
        records.append(generation_result_to_record(baseline, baseline_quality))
        log(f"baseline_candidate={baseline.candidate_path}")
    else:
        log("No --baseline-output-dir supplied; baseline comparison will be unavailable.")

    selected = [item.strip() for item in str(args.strategies or "").split(",") if item.strip()]

    preflight_result: dict[str, Any] | None = None
    needs_ollama = any(is_ollama_strategy(strategy) for strategy in selected)
    if needs_ollama and not args.skip_ollama_preflight:
        log("--- ollama direct preflight ---")
        preflight_first_event_timeout_s = 0.0 if args.disable_first_event_timeout else float(args.ollama_preflight_first_event_timeout_s)
        preflight_result = run_ollama_preflight(
            suite_dir=suite_dir,
            model=args.model,
            ollama_url=args.ollama_url,
            temperature=args.temperature,
            timeout_s=float(args.ollama_preflight_timeout_s),
            first_event_timeout_s=preflight_first_event_timeout_s,
            thinking_only_timeout_s=float(args.thinking_only_timeout_s),
            thinking_only_min_chars=int(args.thinking_only_min_chars),
            content_stall_timeout_s=float(args.content_stall_timeout_s),
            stream_heartbeat_s=float(args.stream_heartbeat_s),
            ollama_ps_on_wait=bool(args.ollama_ps_on_wait),
            ollama_num_ctx=int(args.ollama_num_ctx),
            ollama_keep_alive=str(args.ollama_keep_alive),
            ollama_api=str(args.ollama_api),
        )
        log(
            "ollama_preflight: "
            f"ok={preflight_result.get('ok')} "
            f"status={preflight_result.get('status')} "
            f"error={preflight_result.get('error', '')}"
        )
    elif needs_ollama:
        preflight_result = {"ok": True, "status": "skipped", "reason": "--skip-ollama-preflight was supplied"}
        write_json(suite_dir / "ollama_preflight_skipped.json", preflight_result)

    for strategy in selected:
        log(f"--- strategy {strategy} ---")
        if is_ollama_strategy(strategy) and preflight_result is not None and not preflight_result.get("ok"):
            reason = (
                "skipped because direct Ollama preflight failed before strategy generation: "
                + str(preflight_result.get("error") or preflight_result.get("status") or "unknown")
            )
            result = skipped_generation_result(
                suite_dir=suite_dir,
                strategy=strategy,
                reason=reason,
                preflight=preflight_result,
            )
        elif strategy == "direct_code":
            result = run_direct_code_strategy(
                suite_dir=suite_dir,
                model=args.model,
                ollama_url=args.ollama_url,
                timeout_s=args.timeout_s,
                temperature=args.temperature,
                first_event_timeout_s=effective_first_event_timeout_s,
                thinking_only_timeout_s=float(args.thinking_only_timeout_s),
                thinking_only_min_chars=int(args.thinking_only_min_chars),
                content_stall_timeout_s=float(args.content_stall_timeout_s),
                stream_heartbeat_s=args.stream_heartbeat_s,
                ollama_ps_on_wait=args.ollama_ps_on_wait,
                ollama_num_ctx=int(args.ollama_num_ctx),
                ollama_keep_alive=str(args.ollama_keep_alive),
                ollama_api=str(args.ollama_api),
            )
        elif strategy == "direct_base64":
            result = run_direct_base64_strategy(
                suite_dir=suite_dir,
                model=args.model,
                ollama_url=args.ollama_url,
                timeout_s=args.timeout_s,
                temperature=args.temperature,
                first_event_timeout_s=effective_first_event_timeout_s,
                thinking_only_timeout_s=float(args.thinking_only_timeout_s),
                thinking_only_min_chars=int(args.thinking_only_min_chars),
                content_stall_timeout_s=float(args.content_stall_timeout_s),
                stream_heartbeat_s=args.stream_heartbeat_s,
                ollama_ps_on_wait=args.ollama_ps_on_wait,
                ollama_num_ctx=int(args.ollama_num_ctx),
                ollama_keep_alive=str(args.ollama_keep_alive),
                ollama_api=str(args.ollama_api),
            )
        elif strategy == "direct_repair_loop":
            result = run_direct_repair_loop_strategy(
                repo_root=repo_root,
                suite_dir=suite_dir,
                model=args.model,
                ollama_url=args.ollama_url,
                timeout_s=args.timeout_s,
                temperature=args.temperature,
                first_event_timeout_s=effective_first_event_timeout_s,
                thinking_only_timeout_s=float(args.thinking_only_timeout_s),
                thinking_only_min_chars=int(args.thinking_only_min_chars),
                content_stall_timeout_s=float(args.content_stall_timeout_s),
                stream_heartbeat_s=args.stream_heartbeat_s,
                ollama_ps_on_wait=args.ollama_ps_on_wait,
                ollama_num_ctx=int(args.ollama_num_ctx),
                ollama_keep_alive=str(args.ollama_keep_alive),
                ollama_api=str(args.ollama_api),
                docker_image=args.docker_image,
                docker_timeout_s=args.docker_timeout_s,
                max_attempts=args.max_repair_attempts,
            )
        else:
            result = GenerationResult(
                ok=False,
                strategy=strategy,
                output_dir=suite_dir / strategy,
                candidate_path=None,
                raw_response_path=None,
                timings={},
                errors=[f"unknown strategy: {strategy}"],
                warnings=[],
            )
            result.output_dir.mkdir(parents=True, exist_ok=True)

        quality: dict[str, Any] | None = None
        if result.candidate_path:
            quality = evaluate_candidate(
                repo_root=repo_root,
                strategy_dir=result.output_dir,
                candidate_path=result.candidate_path,
                docker_image=args.docker_image,
                docker_timeout_s=args.docker_timeout_s,
            )

        record = generation_result_to_record(result, quality)
        write_json(result.output_dir / "strategy_result.json", record)
        records.append(record)
        log(
            f"{strategy}: ok={record['ok']} quality={record['quality_score']} "
            f"wall_s={record['timings'].get('total_wall_s')} candidate={record['candidate_path']}"
        )

    records = compare_to_baseline(records)
    leaderboard = sort_leaderboard(records)

    write_json(suite_dir / "suite_results.json", {"records": records})
    write_json(suite_dir / "leaderboard.json", leaderboard)
    write_summary(suite_dir, leaderboard)

    completed_nonbaseline = [item for item in records if item.get("strategy") != "baseline_rag_at"]
    comparison_complete = bool(completed_nonbaseline) and (suite_dir / "leaderboard.json").exists()
    if args.baseline_output_dir:
        comparison_complete = comparison_complete and any(item.get("strategy") == "baseline_rag_at" for item in records)

    pass_payload = {
        "ok": comparison_complete,
        "suite_dir": str(suite_dir),
        "leaderboard": str(suite_dir / "leaderboard.json"),
        "summary": str(suite_dir / "summary.txt"),
        "baseline_supplied": bool(args.baseline_output_dir),
        "strategies": selected,
    }

    if comparison_complete:
        (suite_dir / "test_passed.txt").write_text(json.dumps(pass_payload, indent=2) + "\n", encoding="utf-8")
        log("--- smoke result ---")
        log("PASSED: comparison completed.")
        log(f"suite_dir={suite_dir}")
        log(f"leaderboard={suite_dir / 'leaderboard.json'}")
        log(f"summary={suite_dir / 'summary.txt'}")
        return 0

    write_json(suite_dir / "test_failed.json", pass_payload)
    log("--- smoke result ---")
    log("FAILED: comparison did not complete.")
    log(f"suite_dir={suite_dir}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())