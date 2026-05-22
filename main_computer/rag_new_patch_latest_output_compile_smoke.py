from __future__ import annotations

import argparse
import hashlib
import json
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DOCKER_IMAGE = "main-computer-executor:latest"
PASS_FILE_NAME = "test_passed.txt"
RAG_AT_ROUTE = "/api/applications/chat-console/rag-assisted-thinking/evaluate"
DEFAULT_REPAIRED_FILE_NAME = "ai_repaired_new_patch_candidate.py"


@dataclass(frozen=True)
class LatestRun:
    run_dir: Path
    proposed_file: Path
    master_results: Path | None


@dataclass(frozen=True)
class CompileResult:
    returncode: int
    stdout: str
    stderr: str


def log(message: str = "") -> None:
    print(message, flush=True)


def append_raw(path: Path, text: str) -> None:
    """Append diagnostic text without interpreting it as JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text)


def raw_header(title: str) -> str:
    return "\n" + ("=" * 20) + f" {title} " + ("=" * 20) + "\n"


def route_session_log_path(repo_root: Path, run_id: str) -> Path:
    return repo_root / "diagnostics_output" / "chat_console_ai_sessions" / run_id / "session.log"


def append_session_log_delta(
    *,
    repo_root: Path,
    run_id: str,
    provider_raw_path: Path,
    state: dict[str, int | bool],
) -> int:
    """Copy newly written route/provider session-log text into provider.raw.

    The repair-smoke process cannot see provider tokens directly because it waits
    on a synchronous POST. The route does, however, write raw stream callbacks and
    activity snapshots to the chat-console session log while the POST is running.
    This function tails that log into provider.raw so thinking/content/context
    channel evidence is visible before the POST returns.
    """

    session_log = route_session_log_path(repo_root, run_id)
    if not session_log.exists():
        return 0

    size = session_log.stat().st_size
    offset = int(state.get("offset", 0) or 0)
    if size < offset:
        offset = 0

    if not bool(state.get("header_written", False)):
        append_raw(
            provider_raw_path,
            raw_header("route session log raw stream")
            + f"session_log={session_log}\n",
        )
        state["header_written"] = True

    if size <= offset:
        state["offset"] = offset
        return 0

    with session_log.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read(size - offset)
    state["offset"] = size

    text = chunk.decode("utf-8", errors="replace")
    append_raw(provider_raw_path, text)
    return len(text)


def repo_root_from_cwd() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "new_patch.py").exists() and (cwd / "debug_assets").exists():
        return cwd
    raise SystemExit(
        "Run this script from the repository root. Expected to find new_patch.py and debug_assets/."
    )


def load_smoke_output_dir(repo_root: Path, output_dir: Path | str) -> LatestRun:
    """Load the first smoke-test output directory explicitly supplied by the user."""

    candidate = Path(output_dir)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    run_dir = candidate.resolve()

    if not run_dir.exists() or not run_dir.is_dir():
        raise SystemExit(f"Smoke-test output directory does not exist: {run_dir}")

    proposed_file = run_dir / "proposed_new_patch.py"
    if not proposed_file.exists():
        raise SystemExit(
            "The supplied smoke-test output directory does not contain proposed_new_patch.py: "
            + str(run_dir)
        )

    master_results = run_dir / "master_results.json"
    return LatestRun(
        run_dir=run_dir,
        proposed_file=proposed_file,
        master_results=master_results if master_results.exists() else None,
    )


def make_repair_run_dir(parent_run_dir: Path, run_id: str) -> Path:
    """Create an isolated repair-smoke run directory under the first smoke output."""

    base = parent_run_dir / "compile_repair_smoke_runs" / run_id
    candidate = base
    for index in range(1, 10_000):
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
        candidate = base.with_name(f"{base.name}_{index}")

    raise RuntimeError(f"Could not allocate repair run directory under: {parent_run_dir}")


def docker_relpath(repo_root: Path, target: Path) -> str:
    try:
        return target.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise SystemExit(f"Target is outside repo root: {target}") from exc


def docker_compile(repo_root: Path, proposed_file: Path, docker_image: str) -> CompileResult:
    rel = docker_relpath(repo_root, proposed_file)

    command = (
        "python - <<'PY'\n"
        "import py_compile\n"
        f"py_compile.compile({rel!r}, doraise=True)\n"
        f"print('PY_COMPILE_OK {rel}')\n"
        "PY"
    )

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{str(repo_root)}:/workspace",
        "-w",
        "/workspace",
        docker_image,
        "sh",
        "-lc",
        command,
    ]

    result = subprocess.run(
        docker_cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    return CompileResult(returncode=result.returncode, stdout=result.stdout or "", stderr=result.stderr or "")


def read_master_summary(master_results: Path | None) -> dict[str, Any]:
    if master_results is None or not master_results.exists():
        return {}
    try:
        return json.loads(master_results.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_output_path(run_dir: Path, filename: str) -> Path:
    base = run_dir / filename
    if not base.exists():
        return base

    stem = base.stem
    suffix = base.suffix
    for index in range(2, 10_000):
        candidate = run_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not allocate unique output path for: {base}")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def bounded_text(text: str, *, max_chars: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw
    head = raw[: max_chars // 2]
    tail = raw[-max_chars // 2 :]
    return head + "\n\n... [middle omitted by smoke test] ...\n\n" + tail


def safe_run_id_fragment(value: str) -> str:
    fragment = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    fragment = fragment.strip("._-")
    return fragment or "candidate"


def resolve_candidate_path(repo_root: Path, run_dir: Path, candidate: str | None) -> Path:
    """Resolve the candidate to compile/repair.

    By default this is ``proposed_new_patch.py`` in the first smoke output dir.
    A supplied candidate may be absolute, relative to the first smoke output dir,
    or relative to the repo root. It must stay inside the repo so Docker can see it.
    """

    if not candidate:
        path = run_dir / "proposed_new_patch.py"
    else:
        raw = Path(str(candidate))
        if raw.is_absolute():
            path = raw
        else:
            run_relative = run_dir / raw
            repo_relative = repo_root / raw
            path = run_relative if run_relative.exists() else repo_relative

    path = path.resolve()
    if not path.exists() or not path.is_file():
        raise SystemExit(f"Candidate file does not exist: {path}")

    try:
        path.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"Candidate file is outside repo root: {path}") from exc

    return path


def compile_error_line_number(stderr: str) -> int | None:
    raw = str(stderr or "")
    matches = re.findall(r'File\s+"[^"]+",\s+line\s+(\d+)', raw)
    if not matches:
        matches = re.findall(r"detected at line\s+(\d+)", raw)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def compile_error_summary(stderr: str) -> str:
    for line in reversed(str(stderr or "").splitlines()):
        stripped = line.strip()
        if stripped and ("Error:" in stripped or stripped.startswith("SyntaxError")):
            return stripped
    return ""


def source_excerpt_around_line(source: str, line_number: int | None, *, radius: int = 8) -> str:
    lines = str(source or "").splitlines()
    if not lines:
        return ""
    if line_number is None or line_number < 1:
        line_number = 1
    start = max(1, line_number - radius)
    end = min(len(lines), line_number + radius)
    width = len(str(end))
    rendered: list[str] = []
    for lineno in range(start, end + 1):
        marker = ">>" if lineno == line_number else "  "
        rendered.append(f"{marker} {lineno:>{width}}: {lines[lineno - 1]}")
    return "\n".join(rendered)


def compile_failure_context(
    *,
    candidate_path: Path,
    candidate_source: str,
    compile_result: CompileResult,
) -> dict[str, Any]:
    line_number = compile_error_line_number(compile_result.stderr)
    return {
        "candidate": str(candidate_path),
        "returncode": compile_result.returncode,
        "line_number": line_number,
        "error_summary": compile_error_summary(compile_result.stderr),
        "source_excerpt": source_excerpt_around_line(candidate_source, line_number),
        "stderr_preview": bounded_text(compile_result.stderr, max_chars=12_000),
    }


def build_repair_prompt(
    *,
    latest: LatestRun,
    candidate_path: Path,
    compile_result: CompileResult,
    candidate_source: str,
    max_source_chars: int,
) -> str:
    failure = compile_failure_context(
        candidate_path=candidate_path,
        candidate_source=candidate_source,
        compile_result=compile_result,
    )
    return (
        "You are repairing a generated candidate new_patch.py from the RAG new_patch recreation smoke test.\n"
        "\n"
        "Task:\n"
        "- Fix the candidate so it is syntactically valid Python.\n"
        "- Return a complete replacement implementation, not a patch and not a partial tail.\n"
        "- Preserve the intent and CLI behavior where possible.\n"
        "- Return exactly one replacement file proposal for path new_patch.py.\n"
        "- Do not overwrite or modify the selected candidate file. The smoke harness will write a separate repaired candidate.\n"
        "- Do not use markdown fences.\n"
        "- Prefer content_base64 for the file payload if available in this control plane.\n"
        "- The repaired file must pass python -m py_compile before any dry-run fixture can execute.\n"
        "\n"
        "Selected candidate path:\n"
        f"{candidate_path}\n"
        "\n"
        "Original first-smoke proposed_new_patch.py path:\n"
        f"{latest.proposed_file}\n"
        "\n"
        "Primary compile failure location:\n"
        f"line_number={failure.get('line_number')}\n"
        f"error_summary={failure.get('error_summary')}\n"
        "\n"
        "Source excerpt around failing line:\n"
        "----- BEGIN FAILING SOURCE EXCERPT -----\n"
        f"{failure.get('source_excerpt') or ''}\n"
        "----- END FAILING SOURCE EXCERPT -----\n"
        "\n"
        "Full Docker py_compile stderr:\n"
        f"returncode={compile_result.returncode}\n"
        "----- BEGIN STDERR -----\n"
        f"{failure.get('stderr_preview') or ''}\n"
        "----- END STDERR -----\n"
        "\n"
        "Complete selected candidate source:\n"
        "----- BEGIN CANDIDATE SOURCE -----\n"
        f"{bounded_text(candidate_source, max_chars=max_source_chars)}\n"
        "----- END CANDIDATE SOURCE -----\n"
    )


def repair_payload(
    *,
    prompt: str,
    run_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "run_id": run_id,
        "thread_id": f"compile-smoke-repair-{run_id}",
        "think": "low",
        "auto_apply": False,
        "allowed_write_paths": ["new_patch.py"],
        "self_contained_benchmark_mode": True,
        "max_context_chars": 8000,
        "max_candidates": 8,
        "max_chunks": 4,
        "timeout_s": timeout_s,
        "queries": ["repair generated proposed_new_patch.py compile error"],
        "cell": {
            "id": run_id,
            "type": "ai",
            "source": prompt,
        },
    }


def post_json_with_progress(
    *,
    base_url: str,
    payload: dict[str, Any],
    timeout_s: float,
    heartbeat_s: float,
    repo_root: Path,
    run_id: str,
    provider_raw_path: Path,
    render_raw_path: Path,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + RAG_AT_ROUTE
    body = json.dumps(payload).encode("utf-8")
    result_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
    session_tail_state: dict[str, int | bool] = {"offset": 0, "header_written": False}

    append_raw(
        provider_raw_path,
        raw_header("repair provider request")
        + f"url={url}\n"
        + f"run_id={run_id}\n"
        + f"payload_bytes={len(body)}\n"
        + body.decode("utf-8", errors="replace")
        + "\n",
    )
    append_raw(
        render_raw_path,
        raw_header("repair render start")
        + f"POST {url}\n"
        + f"run_id={run_id}\n"
        + f"payload_bytes={len(body)} timeout_s={timeout_s}\n",
    )

    def worker() -> None:
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                append_raw(
                    provider_raw_path,
                    raw_header("repair http response raw")
                    + f"http_status={int(response.status)}\n"
                    + response_body
                    + "\n",
                )
                parsed = json.loads(response_body) if response_body.strip() else {}
                result_q.put(
                    {
                        "ok": True,
                        "http_status": int(response.status),
                        "elapsed_s": round(time.monotonic() - started, 3),
                        "payload": parsed,
                        "raw_body_preview": response_body[:4000],
                    }
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            append_raw(
                provider_raw_path,
                raw_header("repair http error raw")
                + f"http_status={int(getattr(exc, 'code', 0) or 0)}\n"
                + raw
                + "\n",
            )
            result_q.put(
                {
                    "ok": False,
                    "http_status": int(getattr(exc, "code", 0) or 0),
                    "elapsed_s": round(time.monotonic() - started, 3),
                    "error": f"HTTPError: {exc}",
                    "raw_body_preview": raw[:4000],
                }
            )
        except BaseException as exc:
            append_raw(
                provider_raw_path,
                raw_header("repair provider exception")
                + f"{type(exc).__name__}: {exc}\n",
            )
            result_q.put(
                {
                    "ok": False,
                    "http_status": None,
                    "elapsed_s": round(time.monotonic() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    started = time.monotonic()
    next_heartbeat = started + max(1.0, heartbeat_s)
    log(f"POST {url}")
    log(f"payload_bytes={len(body)} timeout_s={timeout_s}")

    while thread.is_alive():
        now = time.monotonic()
        elapsed = now - started
        if elapsed >= timeout_s:
            copied = append_session_log_delta(
                repo_root=repo_root,
                run_id=run_id,
                provider_raw_path=provider_raw_path,
                state=session_tail_state,
            )
            append_raw(
                render_raw_path,
                f"AI repair timeout elapsed_s={elapsed:.1f} copied_provider_chars={copied}\n",
            )
            return {
                "ok": False,
                "http_status": None,
                "elapsed_s": round(elapsed, 3),
                "error": f"wall-clock timeout waiting for AI repair response after {timeout_s:.1f}s",
            }
        if now >= next_heartbeat:
            copied = append_session_log_delta(
                repo_root=repo_root,
                run_id=run_id,
                provider_raw_path=provider_raw_path,
                state=session_tail_state,
            )
            message = f"AI repair request still waiting elapsed_s={elapsed:.1f} provider_raw_delta_chars={copied}"
            log(message)
            append_raw(render_raw_path, message + "\n")
            next_heartbeat = now + max(1.0, heartbeat_s)
        thread.join(timeout=0.25)

    copied = append_session_log_delta(
        repo_root=repo_root,
        run_id=run_id,
        provider_raw_path=provider_raw_path,
        state=session_tail_state,
    )
    append_raw(render_raw_path, f"AI repair POST thread ended provider_raw_delta_chars={copied}\n")
    if result_q.empty():
        return {
            "ok": False,
            "http_status": None,
            "elapsed_s": round(time.monotonic() - started, 3),
            "error": "AI repair thread exited without returning a result",
        }
    return result_q.get()


def find_route_output_dir(repo_root: Path, run_id: str, response_payload: dict[str, Any]) -> Path | None:
    """Find diagnostics for exactly this repair run.

    Deliberately avoid broad glob fallback so a timed-out repair cannot harvest a
    stale candidate from an older repair_* run.
    """

    def matches_current_run(path: Path) -> bool:
        return path.exists() and path.is_dir() and path.name == run_id

    output_cell = response_payload.get("output_cell") if isinstance(response_payload, dict) else {}
    parts = output_cell.get("parts") if isinstance(output_cell, dict) else []
    if isinstance(parts, list):
        for part in parts:
            metadata = part.get("metadata") if isinstance(part, dict) else {}
            output_dir = metadata.get("output_dir") if isinstance(metadata, dict) else ""
            if output_dir:
                candidate = Path(str(output_dir))
                if matches_current_run(candidate):
                    return candidate

    direct = repo_root / "diagnostics_output" / "rag_assisted_thinking_v4_routes" / run_id
    if matches_current_run(direct):
        return direct

    return None


def extract_file_from_payload(payload: dict[str, Any], wanted_path: str = "new_patch.py") -> str | None:
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return None
    for item in files:
        if not isinstance(item, dict):
            continue
        if str(item.get("path") or "").replace("\\", "/") != wanted_path:
            continue
        content = item.get("content")
        if isinstance(content, str) and content:
            return content
    return None


def extract_repaired_code(repo_root: Path, run_id: str, response: dict[str, Any]) -> tuple[str | None, list[str]]:
    searched: list[str] = []

    response_payload = response.get("payload") if isinstance(response.get("payload"), dict) else {}
    route_payload = response_payload if isinstance(response_payload, dict) else {}

    route_output_dir = find_route_output_dir(repo_root, run_id, route_payload)
    if route_output_dir:
        for filename in ("repair_payload.json", "result.json", "error.json"):
            path = route_output_dir / filename
            searched.append(str(path))
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if filename == "result.json" and isinstance(data.get("repair_payload"), dict):
                code = extract_file_from_payload(data["repair_payload"])
                if code:
                    return code, searched
            code = extract_file_from_payload(data)
            if code:
                return code, searched

    searched.append("route response payload")
    code = extract_file_from_payload(route_payload)
    if code:
        return code, searched

    return None, searched


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def print_compile_result(result: CompileResult) -> None:
    log(f"returncode={result.returncode}")
    if result.stdout:
        log("--- stdout ---")
        log(result.stdout.rstrip())
    if result.stderr:
        log("--- stderr ---")
        log(result.stderr.rstrip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compile and optionally AI-repair a specific rag_new_patch recreation output directory. "
            "The first smoke-test output directory must be supplied explicitly."
        )
    )
    parser.add_argument(
        "output_dir",
        help=(
            "Output directory from the first rag_new_patch recreation smoke test, e.g. "
            "debug_assets/rag_new_patch_recreation_tester/rag_new_patch_018/20260506_223946"
        ),
    )
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument(
        "--candidate",
        default="",
        help=(
            "Candidate file to compile/repair. Defaults to proposed_new_patch.py in the supplied output dir. "
            "May be absolute, relative to the supplied output dir, or relative to repo root."
        ),
    )
    parser.add_argument("--write-pass-file", default=PASS_FILE_NAME)
    parser.add_argument("--base-url", default="", help="Main Computer server base URL for AI repair, e.g. http://127.0.0.1:8765")
    parser.add_argument("--no-ai-repair", action="store_true", help="Only compile the selected candidate; do not ask AI to repair failures.")
    parser.add_argument("--repair-timeout-s", type=float, default=1200.0)
    parser.add_argument("--repair-heartbeat-s", type=float, default=10.0)
    parser.add_argument("--max-source-chars", type=int, default=60_000)
    parser.add_argument("--repaired-filename", default=DEFAULT_REPAIRED_FILE_NAME)
    parser.add_argument(
        "--repair-run-prefix",
        default="repair",
        help="Prefix for the child repair-smoke run directory created under the supplied output directory.",
    )
    args = parser.parse_args()

    repo_root = repo_root_from_cwd()
    latest = load_smoke_output_dir(repo_root, args.output_dir)
    master = read_master_summary(latest.master_results)

    candidate_path = resolve_candidate_path(repo_root, latest.run_dir, str(args.candidate or "").strip() or None)
    candidate_label = safe_run_id_fragment(candidate_path.stem)
    run_id = f"{str(args.repair_run_prefix).strip() or 'repair'}_{latest.run_dir.parent.name}_{latest.run_dir.name}_{candidate_label}_{utc_stamp()}"
    repair_run_dir = make_repair_run_dir(latest.run_dir, run_id)
    pass_file = repair_run_dir / args.write_pass_file
    if pass_file.exists():
        pass_file.unlink()

    candidate_hash_before = sha256_file(candidate_path)

    log("--- rag_new_patch recreation output ---")
    log(f"repo_root={repo_root}")
    log(f"input_output_dir={latest.run_dir}")
    log(f"repair_run_dir={repair_run_dir}")
    log(f"proposed_new_patch={latest.proposed_file}")
    log(f"selected_candidate={candidate_path}")
    log(f"master_results={latest.master_results or ''}")
    log("auto_find_latest_run=false")
    log("selected candidate will not be overwritten")

    if master:
        log("--- master_results summary ---")
        log(f"ok={master.get('ok')}")
        log(f"route_status={master.get('route_status')}")
        log(f"final_score={master.get('final_score')}")
        log(f"raw_new_patch_score={master.get('raw_new_patch_score')}")
        log(f"proposed_new_patch_file={master.get('proposed_new_patch_file')}")

    log("--- docker py_compile selected candidate ---")
    original_compile = docker_compile(repo_root, candidate_path, args.docker_image)
    print_compile_result(original_compile)
    candidate_source = candidate_path.read_text(encoding="utf-8", errors="replace")
    failure_context = compile_failure_context(
        candidate_path=candidate_path,
        candidate_source=candidate_source,
        compile_result=original_compile,
    )
    write_json(
        repair_run_dir / "original_compile_result.json",
        {
            "candidate": str(candidate_path),
            "first_smoke_proposed_new_patch": str(latest.proposed_file),
            "candidate_sha256": candidate_hash_before,
            "docker_image": args.docker_image,
            "returncode": original_compile.returncode,
            "stdout": original_compile.stdout,
            "stderr": original_compile.stderr,
            "compile_failure_context": failure_context,
        },
    )
    write_json(repair_run_dir / "compile_failure_context.json", failure_context)

    if original_compile.returncode == 0:
        pass_payload = {
            "ok": True,
            "check": "docker_py_compile_original",
            "candidate": str(candidate_path),
            "first_smoke_proposed_new_patch": str(latest.proposed_file),
            "docker_image": args.docker_image,
            "candidate_sha256": candidate_hash_before,
            "repair_run_dir": str(repair_run_dir),
        }
        pass_file.write_text(json.dumps(pass_payload, indent=2) + "\n", encoding="utf-8")
        log("--- smoke result ---")
        log("PASSED: selected candidate compiles in Docker.")
        log(f"wrote={pass_file}")
        return 0

    log("--- smoke result ---")
    log("FAILED: selected candidate does not compile in Docker.")
    log("No test_passed.txt was written for the selected candidate.")

    if args.no_ai_repair:
        write_json(
            repair_run_dir / "repair_smoke_status.json",
            {
                "ok": False,
                "stage": "original_compile",
                "ai_repair_attempted": False,
                "reason": "--no-ai-repair was supplied",
                "repair_run_dir": str(repair_run_dir),
            },
        )
        return original_compile.returncode or 1
    if not str(args.base_url or "").strip():
        log("AI repair skipped: --base-url was not provided.")
        write_json(
            repair_run_dir / "repair_smoke_status.json",
            {
                "ok": False,
                "stage": "original_compile",
                "ai_repair_attempted": False,
                "reason": "--base-url was not provided",
                "repair_run_dir": str(repair_run_dir),
            },
        )
        return original_compile.returncode or 1

    prompt = build_repair_prompt(
        latest=latest,
        candidate_path=candidate_path,
        compile_result=original_compile,
        candidate_source=candidate_source,
        max_source_chars=max(1000, int(args.max_source_chars)),
    )
    payload = repair_payload(prompt=prompt, run_id=run_id, timeout_s=float(args.repair_timeout_s))

    prompt_path = repair_run_dir / "ai_repair_prompt.txt"
    payload_path = repair_run_dir / "ai_repair_request.json"
    response_path = repair_run_dir / "ai_repair_response.json"
    status_path = repair_run_dir / "ai_repair_status.json"
    provider_raw_path = repair_run_dir / "provider.raw"
    render_raw_path = repair_run_dir / "render.raw"
    prompt_path.write_text(prompt, encoding="utf-8")
    write_json(payload_path, payload)
    provider_raw_path.write_text("", encoding="utf-8")
    render_raw_path.write_text("", encoding="utf-8")
    append_raw(
        render_raw_path,
        raw_header("repair input")
        + f"input_output_dir={latest.run_dir}\n"
        + f"repair_run_dir={repair_run_dir}\n"
        + f"proposed_new_patch={latest.proposed_file}\n"
        + f"selected_candidate={candidate_path}\n"
        + f"selected_candidate_sha256={candidate_hash_before}\n"
        + f"compile_error_line={failure_context.get('line_number')}\n"
        + f"compile_error_summary={failure_context.get('error_summary')}\n"
        + f"original_compile_returncode={original_compile.returncode}\n",
    )

    log("--- AI repair request ---")
    log(f"run_id={run_id}")
    log(f"base_url={args.base_url}")
    log(f"prompt_chars={len(prompt)}")
    log(f"repair_run_dir={repair_run_dir}")
    log(f"request_payload={payload_path}")
    log(f"request_prompt={prompt_path}")
    log(f"provider_raw={provider_raw_path}")
    log(f"render_raw={render_raw_path}")
    log("selected candidate will not be overwritten")

    response = post_json_with_progress(
        base_url=str(args.base_url),
        payload=payload,
        timeout_s=float(args.repair_timeout_s),
        heartbeat_s=float(args.repair_heartbeat_s),
        repo_root=repo_root,
        run_id=run_id,
        provider_raw_path=provider_raw_path,
        render_raw_path=render_raw_path,
    )
    write_json(response_path, response)
    write_json(
        status_path,
        {
            "run_id": run_id,
            "response_path": str(response_path),
            "response": response,
            "repair_run_dir": str(repair_run_dir),
            "provider_raw": str(provider_raw_path),
            "render_raw": str(render_raw_path),
        },
    )
    log("--- AI repair response ---")
    log(f"response_path={response_path}")
    log(f"status_path={status_path}")
    response_summary = f"ok={response.get('ok')} http_status={response.get('http_status')} elapsed_s={response.get('elapsed_s')}"
    log(response_summary)
    append_raw(render_raw_path, raw_header("repair response summary") + response_summary + "\n")
    if response.get("error"):
        log(f"error={response.get('error')}")
        append_raw(render_raw_path, f"error={response.get('error')}\n")

    candidate_hash_after = sha256_file(candidate_path)
    if candidate_hash_after != candidate_hash_before:
        write_json(
            repair_run_dir / "repair_smoke_status.json",
            {
                "ok": False,
                "stage": "original_integrity",
                "error": "selected candidate changed during repair request",
                "candidate": str(candidate_path),
                "before_sha256": candidate_hash_before,
                "after_sha256": candidate_hash_after,
            },
        )
        log("--- smoke result ---")
        log("FAILED: selected candidate changed during repair request.")
        log(f"candidate={candidate_path}")
        log(f"before_sha256={candidate_hash_before}")
        log(f"after_sha256={candidate_hash_after}")
        return 2

    repaired_code, searched = extract_repaired_code(repo_root, run_id, response)
    log("--- AI repair extraction ---")
    for item in searched:
        log(f"searched={item}")

    if not repaired_code:
        no_candidate_path = repair_run_dir / "ai_repair_no_candidate.txt"
        no_candidate_path.write_text(
            "AI repair response did not contain an extractable new_patch.py proposal for this exact repair run.\n"
            f"run_id={run_id}\n"
            f"response_path={response_path}\n"
            + "\n".join(f"searched={item}" for item in searched)
            + "\n",
            encoding="utf-8",
        )
        write_json(
            repair_run_dir / "repair_smoke_status.json",
            {
                "ok": False,
                "stage": "ai_repair_extraction",
                "run_id": run_id,
                "response_path": str(response_path),
                "searched": searched,
                "no_candidate_path": str(no_candidate_path),
                "provider_raw": str(provider_raw_path),
                "render_raw": str(render_raw_path),
            },
        )
        log("--- smoke result ---")
        log("FAILED: AI repair response did not contain an extractable new_patch.py proposal.")
        log(f"wrote_no_candidate={no_candidate_path}")
        log("No repaired candidate file was written.")
        return 1

    repaired_path = unique_output_path(repair_run_dir, str(args.repaired_filename))
    repaired_path.write_text(repaired_code, encoding="utf-8")
    log(f"wrote_repaired_candidate={repaired_path}")
    log(f"repaired_candidate_chars={len(repaired_code)}")
    log("selected candidate remains unchanged")

    log("--- docker py_compile repaired candidate ---")
    repaired_compile = docker_compile(repo_root, repaired_path, args.docker_image)
    print_compile_result(repaired_compile)
    write_json(
        repair_run_dir / "ai_repair_compile_result.json",
        {
            "candidate": str(repaired_path),
            "selected_candidate": str(candidate_path),
            "first_smoke_proposed_new_patch": str(latest.proposed_file),
            "selected_candidate_sha256": candidate_hash_before,
            "docker_image": args.docker_image,
            "returncode": repaired_compile.returncode,
            "stdout": repaired_compile.stdout,
            "stderr": repaired_compile.stderr,
        },
    )

    if repaired_compile.returncode != 0:
        write_json(
            repair_run_dir / "repair_smoke_status.json",
            {
                "ok": False,
                "stage": "ai_repaired_compile",
                "repaired_candidate": str(repaired_path),
                "returncode": repaired_compile.returncode,
                "repair_run_dir": str(repair_run_dir),
            },
        )
        log("--- smoke result ---")
        log("FAILED: AI repaired candidate does not compile in Docker.")
        log(f"repaired_candidate={repaired_path}")
        return repaired_compile.returncode or 1

    pass_payload = {
        "ok": True,
        "check": "docker_py_compile_ai_repaired",
        "selected_candidate": str(candidate_path),
        "first_smoke_proposed_new_patch": str(latest.proposed_file),
        "repaired_candidate": str(repaired_path),
        "docker_image": args.docker_image,
        "selected_candidate_sha256": candidate_hash_before,
        "repair_run_dir": str(repair_run_dir),
    }
    pass_file.write_text(json.dumps(pass_payload, indent=2) + "\n", encoding="utf-8")
    write_json(
        repair_run_dir / "repair_smoke_status.json",
        {
            "ok": True,
            "stage": "ai_repaired_compile",
            "pass_file": str(pass_file),
            "repaired_candidate": str(repaired_path),
            "repair_run_dir": str(repair_run_dir),
        },
    )

    log("--- smoke result ---")
    log("PASSED: AI repaired candidate compiles in Docker.")
    log(f"wrote={pass_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
