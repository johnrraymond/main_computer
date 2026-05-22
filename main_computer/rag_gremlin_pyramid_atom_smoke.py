#!/usr/bin/env python3
"""
rag_gremlin_pyramid_atom_smoke.py

AI-pyramid -> global word greps -> backward atom discovery -> gremlin.main()
-> anti-cheat stop before proposed-edit inference.

The AI call is part of the normal path. It returns only a bare indentation-based
grep pyramid. The runner greps the whole repo once per unique pyramid word,
computes atoms where words connect, and feeds the best atom evidence into a
generated code-gremlin.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import traceback
import urllib.request
import zipfile
from typing import Any


_THIS_FILE = Path(__file__).resolve()
_REPO_HINT = _THIS_FILE.parents[1]
if str(_REPO_HINT) not in sys.path:
    sys.path.insert(0, str(_REPO_HINT))

from main_computer.providers.ollama import parse_ollama_think_choice, prepare_ollama_generate_payload  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path("debug_assets") / "rgp"
DEFAULT_DOCKER_IMAGE = "main-computer-executor:latest"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_MODEL = os.environ.get("MAIN_COMPUTER_GREMLIN_MODEL", "gemma4:26b")
DEFAULT_SOURCE_DIRS = ["main_computer", "tests"]
SOURCE_GLOBS = ["*.py", "*.html", "*.js", "*.css", "*.ts", "*.tsx"]

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules",
    "dist", "build", "diagnostics_output", "debug_assets", "snapshots",
    "revision_control", ".main_computer_browser_profile",
    "generated_component_docs", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

RUNNER_NAMES = {
    "gremlin_rag_smoke.py",
    "rag_gremlin_patch_pipeline_smoke.py",
    "rag_gremlin_pyramid_atom_smoke.py",
}


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def safe_name(text: str, limit: int = 72) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", text.lower())
    base = "_".join(parts)[:limit].strip("_") or "item"
    return f"{base}_{short_hash(text)}"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_rel_path(value: str | Path) -> str:
    text = str(value).replace("\\", "/").strip()
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe repo-relative path: {value!r}")
    return path.as_posix()


class Logger:
    def __init__(self, path: Path, quiet: bool = False):
        self.path = path
        self.quiet = quiet
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def __call__(self, message: str = "") -> None:
        text = str(message)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
        if not self.quiet:
            print(text, flush=True)

    def banner(self, title: str) -> None:
        self("\n" + "=" * 78)
        self(title)
        self("=" * 78)


class Timer:
    def __init__(self, label: str, log: Logger, timings: dict[str, float]):
        self.label = label
        self.log = log
        self.timings = timings
        self.start = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        self.log(f"[start] {self.label}")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter() - self.start
        self.timings[self.label] = round(elapsed, 3)
        if exc:
            self.log(f"[fail]  {self.label} after {elapsed:.2f}s: {exc}")
        else:
            self.log(f"[done]  {self.label} in {elapsed:.2f}s")


def run_command(cmd: list[str], cwd: Path | None = None, timeout_s: int = 120) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "command": cmd}
    except Exception as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "command": cmd}


def print_block(log: Logger, title: str, value: Any) -> None:
    log.banner(title)
    log(value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True))


def should_skip(path: Path, repo: Path, include_runner: bool = False) -> bool:
    try:
        rel = path.relative_to(repo)
    except ValueError:
        rel = path
    if any(part in SKIP_DIRS for part in rel.parts):
        return True
    if not include_runner and path.name in RUNNER_NAMES:
        return True
    return False


def iter_source_files(repo: Path, source_dirs: list[str], include_runner: bool) -> list[Path]:
    roots: list[Path] = []
    for source_dir in source_dirs:
        candidate = (repo / source_dir).resolve()
        if not candidate.exists():
            continue
        try:
            candidate.relative_to(repo)
        except ValueError:
            continue
        roots.append(candidate)
    if not roots:
        roots = [repo]

    files: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if should_skip(path, repo, include_runner=include_runner):
                continue
            if not any(fnmatch.fnmatch(path.name, glob) for glob in SOURCE_GLOBS):
                continue
            files.append(path)
    return files


def probe_server_rag_pathway(repo: Path) -> dict[str, Any]:
    viewport = repo / "main_computer" / "viewport_routes_rag_assisted_thinking.py"
    v4 = repo / "main_computer" / "rag_assisted_thinking_v4.py"
    subproc = repo / "main_computer" / "chat_ai_subprocess.py"
    viewport_text = read_text(viewport) if viewport.exists() else ""
    v4_text = read_text(v4) if v4.exists() else ""
    subproc_text = read_text(subproc) if subproc.exists() else ""
    return {
        "viewport_routes_rag_assisted_thinking.py": {
            "exists": viewport.exists(),
            "has_evaluate_handler": "_handle_chat_console_rag_assisted_thinking_evaluate" in viewport_text,
            "mentions_v4_runner": "run_rag_assisted_thinking_v4_request" in viewport_text,
            "mentions_policy": "RagAssistedThinkingV4Policy" in viewport_text,
            "mentions_proposed_paths": "proposed_paths" in viewport_text,
            "mentions_written_paths": "written_paths" in viewport_text,
        },
        "rag_assisted_thinking_v4.py": {
            "exists": v4.exists(),
            "has_policy": "class RagAssistedThinkingV4Policy" in v4_text,
            "has_request_runner": "run_rag_assisted_thinking_v4_request" in v4_text,
            "mentions_output_dir": "output_dir" in v4_text,
            "mentions_proposed_paths": "proposed_paths" in v4_text,
            "mentions_written_paths": "written_paths" in v4_text,
        },
        "chat_ai_subprocess.py": {
            "exists": subproc.exists(),
            "mentions_rag_v4_mode": "rag_assisted_thinking_v4" in subproc_text,
            "mentions_cancel": "cancel" in subproc_text.lower(),
        },
        "integration_target": "Expose the pyramid/atom gremlin patch pipeline as a RAG-AT strategy behind the chat-console evaluate route.",
    }


def ai_pyramid_prompt(user_prompt: str) -> str:
    return f"""Return only a grep word pyramid for finding code evidence.

Rules:
- Plain text only.
- No JSON.
- No markdown.
- No explanations.
- One word or short phrase per line.
- Indent children by two spaces.
- The pyramid must go from broad/noisy trunk terms to targeted/obscure leaf terms.
- The trunk can contain broad area words that may produce many grep hits.
- Do not stop at broad area words like chat, button, file, page, app, code, update, change, render, or handler.
- Broad words are only the bare minimum context; they are not the goal.
- Children must drill into the problem area with more specific evidence words.
- Leaf nodes should be the most obscure but still relevant words you can infer from the user's request.
- Obscure means targeted at the problem area, not random or clever.
- Prefer leaf words that are likely to appear near the relevant code but unlikely to appear throughout the whole repo.
- Preserve concrete user literals as targeted leaves when they matter, including visible text, colors, labels, ids, error strings, route pieces, file extensions, and UI copy.
- If the user mentions exact words like colors, labels, statuses, commands, or quoted text, include those exact words as leaves.
- Avoid generic programming words as leaves.
- Avoid tiny ambiguous words as leaves unless the user explicitly used them and they are central to the issue.
- Children are not guessed repo symbols; they are ordinary words likely to connect evidence.
- Do not invent function names, class names, file names, or camelCase symbols.
- Search is case-insensitive, so do not duplicate capitalization.
- Put the broadest and most polluting terms nearer the trunk.
- Put the most targeted, least polluting, problem-specific terms at the leaves.
- Arrange words so traversing into children narrows from general area to precise evidence.

User prompt:
{user_prompt}
"""


def mock_pyramid_for_prompt(prompt: str) -> str:
    print("using mock is illegal behavior.")
    exit(1)
    p = prompt.lower()
    if "stop" in p and ("red" in p or "green" in p):
        return """chat
  button
    stop
      red
      green
    running
  console
    cell
    status
"""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]+", p)]
    stop = {
        "the", "and", "for", "with", "that", "this", "when", "where", "what",
        "give", "code", "change", "should", "would", "could", "not", "make",
        "want", "need", "it", "me", "a", "an", "to", "of", "in", "on",
    }
    useful: list[str] = []
    for w in words:
        if w not in stop and w not in useful:
            useful.append(w)
    useful = useful[:8] or ["code", "change"]
    lines = [useful[0]]
    for i, child in enumerate(useful[1:4]):
        lines.append(f"  {child}")
        for grand in useful[4 + i:5 + i]:
            lines.append(f"    {grand}")
    return "\n".join(lines) + "\n"


def extract_generate_text_fragment(parsed: Any) -> str:
    """Return generated text from common streaming response shapes.

    Ollama ``/api/generate`` streams text in ``response`` chunks. Some chat-like
    or compatible endpoints put text under ``message.content`` or
    ``choices[].delta.content`` instead. Accepting all of those keeps the smoke
    runner focused on produced text rather than on one exact provider envelope.
    """
    if not isinstance(parsed, dict):
        return ""

    pieces: list[str] = []
    response = parsed.get("response")
    if response is not None:
        pieces.append(str(response))

    message = parsed.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if content is not None:
            pieces.append(str(content))

    choices = parsed.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and delta.get("content") is not None:
                pieces.append(str(delta.get("content")))
            message_choice = choice.get("message")
            if isinstance(message_choice, dict) and message_choice.get("content") is not None:
                pieces.append(str(message_choice.get("content")))
            if choice.get("text") is not None:
                pieces.append(str(choice.get("text")))

    return "".join(pieces)


def summarize_ollama_generate_response(parsed: Any, elapsed_s: float) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "elapsed_s": round(elapsed_s, 3),
            "parsed_type": type(parsed).__name__,
            "reason": "Ollama response JSON was not an object",
        }

    response_text = extract_generate_text_fragment(parsed)
    summary: dict[str, Any] = {
        "ok": True,
        "elapsed_s": round(elapsed_s, 3),
        "response_chars": len(response_text),
        "response_preview": response_text[:400],
        "response_repr": repr(response_text[:400]),
        "response_empty": not bool(response_text.strip()),
        "keys": sorted(str(key) for key in parsed.keys()),
    }

    for key in (
        "model",
        "created_at",
        "done",
        "done_reason",
        "error",
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    ):
        if key in parsed:
            summary[key] = parsed.get(key)

    return summary


def summarize_ollama_streaming_response(events: list[Any], response_text: str, elapsed_s: float) -> dict[str, Any]:
    dict_events = [event for event in events if isinstance(event, dict)]
    final_event = dict_events[-1] if dict_events else {}
    key_set: set[str] = set()
    for event in dict_events:
        key_set.update(str(key) for key in event.keys())

    summary: dict[str, Any] = {
        "ok": True,
        "stream": True,
        "elapsed_s": round(elapsed_s, 3),
        "stream_event_count": len(events),
        "response_chars": len(response_text),
        "response_preview": response_text[:400],
        "response_repr": repr(response_text[:400]),
        "response_empty": not bool(response_text.strip()),
        "keys": sorted(key_set),
    }

    for key in (
        "model",
        "created_at",
        "done",
        "done_reason",
        "error",
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    ):
        if key in final_event:
            summary[key] = final_event.get(key)

    return summary


def iter_response_jsonl_from_bytes(response: Any, raw_path: Path) -> list[dict[str, Any]]:
    """Read an HTTP streaming response byte-by-byte and persist every raw byte.

    Some local Ollama/runtime combinations expose streamed JSONL chunks reliably
    via ``read(1)`` even when iterating over the response object only observes the
    final stats event.  This helper mirrors the smallest diagnostic probe: keep
    reading bytes as they arrive, flush them to disk immediately, and parse JSONL
    records once a newline is seen.
    """
    events: list[dict[str, Any]] = []
    pending = bytearray()
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    def consume_line(raw_line: bytes) -> None:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            event = {"error": f"JSONDecodeError: {exc}", "raw": line}
        if isinstance(event, dict):
            events.append(event)
        else:
            events.append({"parsed": event, "parsed_type": type(event).__name__})

    with raw_path.open("wb") as raw_handle:
        while True:
            chunk = response.read(1)
            if not chunk:
                break
            raw_handle.write(chunk)
            raw_handle.flush()
            pending.extend(chunk)
            while b"\n" in pending:
                raw_line, _, rest = pending.partition(b"\n")
                consume_line(bytes(raw_line))
                pending = bytearray(rest)

        if pending:
            consume_line(bytes(pending))
            if not bytes(pending).endswith(b"\n"):
                raw_handle.write(b"\n")
                raw_handle.flush()

    return events


def call_ollama_generate_streaming(
    *,
    payload: dict[str, Any],
    url: str,
    timeout_s: int,
    log: Logger,
    raw_path: Path,
    stream_label: str,
) -> tuple[str, dict[str, Any]]:
    """Call an Ollama-compatible generate endpoint in streaming mode.

    The HTTP read timeout is intentionally disabled here. The smoke test should
    stream model output as it arrives instead of failing just because a slow
    local or remote model took longer than an arbitrary socket timeout.

    Read the socket byte-by-byte instead of relying on HTTPResponse iteration.
    The latter can miss intermediate Ollama JSONL chunks on some Windows/local
    runtime combinations and leave callers with only the final empty stats event.
    """
    stream_payload, think_metadata = prepare_ollama_generate_payload(payload)
    stream_payload = {**stream_payload, "stream": True}
    data = json.dumps(stream_payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    if timeout_s:
        log(f"[{stream_label}] streaming with no HTTP read timeout; ignoring configured HTTP timeout {timeout_s}s")
    else:
        log(f"[{stream_label}] streaming with no HTTP read timeout")

    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=None) as response:
        events = iter_response_jsonl_from_bytes(response, raw_path)

    text_parts: list[str] = []
    for event in events:
        fragment = extract_generate_text_fragment(event)
        if fragment:
            text_parts.append(fragment)
            log(f"[{stream_label} chunk] {fragment}")

    elapsed = time.perf_counter() - started
    response_text = "".join(text_parts)
    summary = summarize_ollama_streaming_response(events, response_text, elapsed)
    summary.update(think_metadata)
    return response_text, summary


def call_ollama_pyramid(
    prompt: str,
    model: str,
    url: str,
    timeout_s: int,
    log: Logger,
    out_dir: Path,
    *,
    think: bool | str | None = None,
) -> tuple[str, dict[str, Any]]:
    request_text = ai_pyramid_prompt(prompt)
    payload, think_metadata = prepare_ollama_generate_payload(
        {
            "model": model,
            "prompt": request_text,
            "stream": True,
            "options": {"temperature": 0, "num_predict": 160},
        },
        think=think,
    )
    write_text(out_dir / "01_ai_pyramid_request.txt", request_text)
    write_json(out_dir / "01_ai_pyramid_post_payload.json", payload)
    print_block(log, "AI PYRAMID REQUEST", request_text)

    text, summary = call_ollama_generate_streaming(
        payload=payload,
        url=url,
        timeout_s=timeout_s,
        log=log,
        raw_path=out_dir / "01_ai_pyramid_raw_response.jsonl",
        stream_label="ai-pyramid",
    )
    write_json(out_dir / "01_ai_pyramid_response_summary.json", summary)
    print_block(log, "AI PYRAMID RESPONSE SUMMARY", summary)
    write_text(out_dir / "01_ai_pyramid_text.txt", text)
    print_block(log, "AI PYRAMID RAW TEXT", text if text else "<empty>")
    info = {
        "ok": True,
        "model": model,
        "url": url,
        "elapsed_s": summary.get("elapsed_s"),
        "raw_response_path": str(out_dir / "01_ai_pyramid_raw_response.jsonl"),
        "response_summary_path": str(out_dir / "01_ai_pyramid_response_summary.json"),
        "response_chars": summary.get("response_chars"),
        "response_empty": summary.get("response_empty"),
        "stream": summary.get("stream"),
        "stream_event_count": summary.get("stream_event_count"),
        "done": summary.get("done"),
        "done_reason": summary.get("done_reason"),
        "eval_count": summary.get("eval_count"),
        "prompt_eval_count": summary.get("prompt_eval_count"),
        "think": summary.get("think"),
        "thinking_state": summary.get("thinking_state"),
        "think_source": summary.get("think_source"),
        "think_default_applied": summary.get("think_default_applied"),
        "think_policy": summary.get("think_policy"),
    }
    if summary.get("error") is not None:
        info["error"] = summary.get("error")
    return text, info


def clean_pyramid_line(line: str) -> tuple[int, str] | None:
    raw = line.rstrip()
    if not raw.strip() or raw.strip().startswith("```"):
        return None
    leading = len(raw) - len(raw.lstrip(" "))
    text = raw.strip()
    text = re.sub(r"^[-*•]+\s*", "", text).strip()
    text = re.sub(r"^\d+[.)]\s*", "", text).strip()
    text = text.strip("\"`'").strip()
    if not text:
        return None
    lower = text.lower()
    if lower.startswith(("rules:", "user prompt:", "prompt:", "here", "json", "{", "[")):
        return None
    words = re.findall(r"[A-Za-z0-9_-]+", text)
    if not words:
        return None
    if len(words) > 4:
        words = words[:4]
    return leading, " ".join(words).lower()


def parse_pyramid(text: str) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for line in text.splitlines():
        cleaned = clean_pyramid_line(line)
        if not cleaned:
            continue
        leading, term = cleaned
        depth = max(0, leading // 2)
        if depth > len(stack):
            depth = len(stack)
        stack = stack[:depth]
        path = (stack[-1]["path"] + [term]) if stack else [term]
        node = {
            "id": len(nodes),
            "term": term,
            "depth": depth,
            "parent": stack[-1]["id"] if stack else None,
            "path": path,
        }
        nodes.append(node)
        stack.append(node)

    max_depth = max((int(n["depth"]) for n in nodes), default=0)
    children_by_parent: dict[int, list[int]] = {int(node["id"]): [] for node in nodes}
    for node in nodes:
        parent = node.get("parent")
        if parent is not None:
            children_by_parent.setdefault(int(parent), []).append(int(node["id"]))

    for node in nodes:
        depth = int(node["depth"])
        is_leaf = not children_by_parent.get(int(node["id"]))
        node["is_leaf"] = is_leaf
        node["trunk_score"] = max_depth + 1 - depth
        node["distal_score"] = depth + 1 + (1 if is_leaf else 0)
        # Keep the historical field name for downstream atom scoring, but make
        # it distal-first: leaves and deeper nodes are tried before broad trunks.
        node["layer_score"] = node["distal_score"]

    term_meta: dict[str, dict[str, int]] = {}
    for index, node in enumerate(nodes):
        term = str(node["term"])
        depth = int(node["depth"])
        distal_score = int(node["distal_score"])
        leaf_flag = 1 if node.get("is_leaf") else 0
        meta = term_meta.setdefault(
            term,
            {
                "first_index": index,
                "max_depth": depth,
                "max_distal_score": distal_score,
                "is_leaf": leaf_flag,
            },
        )
        meta["max_depth"] = max(meta["max_depth"], depth)
        meta["max_distal_score"] = max(meta["max_distal_score"], distal_score)
        meta["is_leaf"] = max(meta["is_leaf"], leaf_flag)

    unique_terms = sorted(
        term_meta,
        key=lambda term: (
            -int(term_meta[term]["max_distal_score"]),
            -int(term_meta[term]["max_depth"]),
            int(term_meta[term]["first_index"]),
            term,
        ),
    )

    term_scores: dict[str, int] = {
        term: int(meta["max_distal_score"])
        for term, meta in term_meta.items()
    }
    term_depths: dict[str, int] = {
        term: int(meta["max_depth"])
        for term, meta in term_meta.items()
    }
    term_is_leaf: dict[str, bool] = {
        term: bool(meta["is_leaf"])
        for term, meta in term_meta.items()
    }

    return {
        "nodes": nodes,
        "unique_terms": unique_terms,
        "term_scores": term_scores,
        "term_depths": term_depths,
        "term_is_leaf": term_is_leaf,
        "paths": [node["path"] for node in nodes],
        "max_depth": max_depth,
    }


def grep_term_global(repo: Path, files: list[Path], term: str, out_path: Path) -> dict[str, Any]:
    term_l = term.lower()
    hits: list[dict[str, Any]] = []
    files_with_hits: set[str] = set()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for path in files:
            rel = path.relative_to(repo).as_posix()
            if term_l in rel.lower():
                item = {"term": term, "path": rel, "line": 0, "text": rel, "kind": "path"}
                hits.append(item)
                files_with_hits.add(rel)
                handle.write(json.dumps(item, sort_keys=True) + "\n")
            try:
                lines = read_text(path).splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                if term_l in line.lower():
                    item = {"term": term, "path": rel, "line": idx, "text": line, "kind": "line"}
                    hits.append(item)
                    files_with_hits.add(rel)
                    handle.write(json.dumps(item, sort_keys=True) + "\n")
    return {"term": term, "hit_count": len(hits), "file_count": len(files_with_hits), "jsonl_path": str(out_path)}


def load_word_hits(out_dir: Path, terms: list[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for term in terms:
        path = out_dir / "04_word_greps" / f"{safe_name(term)}.jsonl"
        hits: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    hits.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        out[term] = hits
    return out


def get_file_lines(repo: Path, rel: str) -> list[str]:
    try:
        return read_text(repo / rel).splitlines()
    except OSError:
        return []


def compute_atoms(repo: Path, word_hits: dict[str, list[dict[str, Any]]], term_scores: dict[str, int], window: int = 3) -> list[dict[str, Any]]:
    file_term_lines: dict[str, dict[str, set[int]]] = {}
    file_path_terms: dict[str, set[str]] = {}

    for term, hits in word_hits.items():
        for hit in hits:
            rel = str(hit["path"])
            file_term_lines.setdefault(rel, {}).setdefault(term, set())
            if hit.get("kind") == "path":
                file_path_terms.setdefault(rel, set()).add(term)
            else:
                file_term_lines[rel][term].add(int(hit["line"]))

    # Process the most connected files first. Global word greps are fully logged,
    # but atom discovery should not spend time generating redundant atoms for
    # every broad-word hit file.
    file_scores: list[tuple[int, int, str]] = []
    for rel, term_lines in file_term_lines.items():
        present_terms = set(term_lines) | file_path_terms.get(rel, set())
        if len(present_terms) < 2:
            continue
        score = sum(term_scores.get(t, 0) for t in present_terms)
        file_scores.append((len(present_terms), score, rel))
    file_scores.sort(reverse=True)
    files_to_process = [rel for _, _, rel in file_scores[:80]]

    atoms_by_key: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    file_lines_cache: dict[str, list[str]] = {}

    def lines_for(path: str) -> list[str]:
        if path not in file_lines_cache:
            file_lines_cache[path] = get_file_lines(repo, path)
        return file_lines_cache[path]

    def words_in_window(term_lines: dict[str, set[int]], path_terms: set[str], start: int, end: int) -> set[str]:
        words = set(path_terms)
        for term, lines in term_lines.items():
            # Most line sets are small; range membership is cheaper than scanning
            # thousands of lines for broad terms.
            if any(n in lines for n in range(start, end + 1)):
                words.add(term)
        return words

    def add_atom(path: str, line_start: int, line_end: int, category: str, connected_words: set[str]) -> None:
        connected_words = set(connected_words)
        if len(connected_words) < 2:
            return
        lines = lines_for(path)
        if not lines:
            return
        start = max(1, line_start)
        end = min(len(lines), line_end)
        if end < start:
            return
        text_lines = [f"{idx}:{lines[idx - 1]}" for idx in range(start, end + 1)]
        span = max(1, end - start + 1)
        layer_score_sum = sum(term_scores.get(w, 0) for w in connected_words)
        rarity_score = 0.0
        for w in connected_words:
            count = max(1, len(word_hits.get(w, [])))
            rarity_score += 1.0 / math.log(count + 2)
        path_bonus = 5 if file_path_terms.get(path, set()) & connected_words else 0
        score = 100 * len(connected_words) + 10 * layer_score_sum + max(0, 20 - span) + path_bonus + rarity_score
        atom = {
            "path": path,
            "line_start": start,
            "line_end": end,
            "category": category,
            "connected_words": sorted(connected_words, key=lambda w: (-term_scores.get(w, 0), w)),
            "connected_word_count": len(connected_words),
            "layer_score_sum": layer_score_sum,
            "span": span,
            "rarity_score": round(rarity_score, 3),
            "path_bonus": path_bonus,
            "score": round(score, 3),
            "text": "\n".join(text_lines),
        }
        atoms_by_key[(path, start, end, category)] = atom

    for rel in files_to_process:
        term_lines = file_term_lines.get(rel, {})
        path_terms = file_path_terms.get(rel, set())

        line_to_terms: dict[int, set[str]] = {}
        for term, lines in term_lines.items():
            for line_no in lines:
                line_to_terms.setdefault(line_no, set()).add(term)

        # Same-line atoms are cheap and exact.
        for line_no, terms in line_to_terms.items():
            add_atom(rel, line_no, line_no, "same_line", terms | path_terms)

        # Pick only the best centers per file, based on how many words connect
        # around that center.
        scored_centers: list[tuple[int, int, int]] = []
        for center in line_to_terms:
            start = max(1, center - window)
            end = center + window
            words = words_in_window(term_lines, path_terms, start, end)
            if len(words) >= 2:
                scored_centers.append((len(words), sum(term_scores.get(w, 0) for w in words), center))
        scored_centers.sort(reverse=True)
        chosen_centers = [center for _, _, center in scored_centers[:40]]

        for center in chosen_centers:
            for category, radius in (("nearby_window", window), ("block_window", 12)):
                start = max(1, center - radius)
                end = center + radius
                words = words_in_window(term_lines, path_terms, start, end)
                add_atom(rel, start, end, category, words)

    atoms = list(atoms_by_key.values())
    atoms.sort(
        key=lambda a: (int(a["connected_word_count"]), int(a["layer_score_sum"]), float(a["score"]), -int(a["span"])),
        reverse=True,
    )
    return atoms

def fill_evidence_buffer(atoms: list[dict[str, Any]], max_chars: int, category_limits: dict[str, int]) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    used_by_category = {k: 0 for k in category_limits}
    chunks: list[str] = []
    selected: list[dict[str, Any]] = []
    total = 0
    for atom in atoms:
        category = str(atom["category"])
        limit = category_limits.get(category, max_chars)
        header = (
            f"### atom {category} score={atom['score']} "
            f"words={','.join(atom['connected_words'])} "
            f"count={atom['connected_word_count']} "
            f"layer_sum={atom['layer_score_sum']} "
            f"{atom['path']}:{atom['line_start']}-{atom['line_end']}"
        )
        chunk = header + "\n" + str(atom["text"])
        chunk_len = len(chunk) + 2
        if used_by_category.get(category, 0) + chunk_len > limit:
            continue
        if total + chunk_len > max_chars:
            continue
        chunks.append(chunk)
        selected.append(atom)
        used_by_category[category] = used_by_category.get(category, 0) + chunk_len
        total += chunk_len
    return "\n\n".join(chunks), selected, used_by_category


def extract_called_symbols(text: str) -> list[str]:
    ignored = {
        "if", "for", "while", "switch", "catch", "function", "return", "json",
        "boolean", "string", "number", "date", "fetch", "find", "some", "append",
        "push", "splice", "setinterval", "clearinterval",
    }
    symbols: list[str] = []
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        name = match.group(1)
        if name.lower() in ignored:
            continue
        if name not in symbols:
            symbols.append(name)
    return symbols[:20]


def find_symbol_definition(path: Path, symbol: str, context: int) -> str | None:
    try:
        lines = read_text(path).splitlines()
    except OSError:
        return None
    patterns = [
        re.compile(rf"\bfunction\s+{re.escape(symbol)}\s*\("),
        re.compile(rf"\bconst\s+{re.escape(symbol)}\s*=\s*(?:async\s*)?\("),
        re.compile(rf"\blet\s+{re.escape(symbol)}\s*=\s*(?:async\s*)?\("),
        re.compile(rf"\bvar\s+{re.escape(symbol)}\s*=\s*(?:async\s*)?\("),
        re.compile(rf"\bdef\s+{re.escape(symbol)}\s*\("),
        re.compile(rf"\bclass\s+{re.escape(symbol)}\b"),
    ]
    for idx, line in enumerate(lines):
        if any(pattern.search(line) for pattern in patterns):
            start = max(0, idx - context)
            end = min(len(lines), idx + context + 1)
            out = [f"### symbol definition: {symbol} in {path}"]
            for row_idx in range(start, end):
                out.append(f"{row_idx + 1}:{lines[row_idx]}")
            return "\n".join(out)
    return None


def supplemental_symbol_context(
    repo: Path,
    evidence: str,
    selected_atoms: list[dict[str, Any]],
    source_files: list[Path],
    max_chars: int,
    context: int,
    log: Logger,
) -> tuple[str, list[dict[str, str]]]:
    symbols = extract_called_symbols(evidence)
    if not symbols:
        return "", []
    # Prioritize real symbols that are likely to explain the strongest atom.
    # Broad render helpers can exist in the buffer, but stop/button symbols are
    # the ones most likely to explain this style-control edit.
    priority_terms = ("stop", "button", "cancel", "submit", "run")
    symbols = sorted(
        symbols,
        key=lambda name: (
            0 if any(term in name.lower() for term in priority_terms) else 1,
            len(name),
            name.lower(),
        ),
    )[:8]
    candidate_paths: list[Path] = []
    for atom in selected_atoms:
        p = repo / str(atom["path"])
        if p.exists() and p not in candidate_paths:
            candidate_paths.append(p)
    for p in source_files:
        if p.suffix.lower() in {".js", ".ts", ".tsx", ".py", ".html"} and p not in candidate_paths:
            candidate_paths.append(p)
    chunks: list[str] = []
    found: list[dict[str, str]] = []
    for symbol in symbols:
        for path in candidate_paths:
            snippet = find_symbol_definition(path, symbol, context=context)
            if not snippet:
                continue
            candidate = "\n\n".join(chunks + [snippet])
            if len(candidate) > max_chars:
                log(f"[symbol-context] max chars reached at {len(chr(10).join(chunks))}")
                return "\n\n".join(chunks), found
            rel = path.relative_to(repo).as_posix()
            chunks.append(snippet)
            found.append({"symbol": symbol, "path": rel})
            log(f"[symbol-context] found {symbol} in {rel}")
            break
    return "\n\n".join(chunks), found


def build_anti_cheat_boundary_report(
    *,
    prompt: str,
    ai_pyramid: str,
    parsed_tree: dict[str, Any],
    word_summary: list[dict[str, Any]],
    selected_atoms: list[dict[str, Any]],
    symbols_found: list[dict[str, str]],
    symbol_context: str,
) -> dict[str, Any]:
    """Return the intentional stop point before any hard-coded edit inference.

    The pyramid/atom smoke test is allowed to prove retrieval:
      prompt -> AI grep pyramid -> global greps -> atom evidence -> symbol context.

    It is not allowed to smuggle in a hand-coded patch kernel.  If no separate
    non-cheating gremlin-generation step exists, the smoke test must stop here.
    """

    top_atom = selected_atoms[0] if selected_atoms else None
    useful_word_summary = [
        {
            "term": item.get("term"),
            "hit_count": item.get("hit_count"),
            "file_count": item.get("file_count"),
        }
        for item in word_summary
    ]

    return {
        "ok": True,
        "status": "stopped_before_cheating",
        "reason": (
            "The smoke test reached the first boundary where producing a proposed edit "
            "would require either a real gremlin-generation step or a declared patch "
            "kernel. Hard-coded edit inference is intentionally disabled."
        ),
        "prompt": prompt,
        "ai_pyramid": ai_pyramid,
        "unique_terms": parsed_tree.get("unique_terms", []),
        "word_hit_summary": useful_word_summary,
        "top_atom": top_atom,
        "top_atom_count": len(selected_atoms),
        "symbols_found": symbols_found,
        "symbol_context_chars": len(symbol_context),
        "next_non_cheating_step": (
            "Use a second AI call or a separately declared reusable patch-kernel registry "
            "to generate an ordinary Python gremlin from the selected atom evidence. "
            "Then run gremlin.main(), adapt the edit, verify old_fragment_count == 1, "
            "and only then build a new_patch artifact."
        ),
        "files_to_read_next": [
            "01_ai_pyramid_text.txt",
            "05_word_hit_summary.json",
            "07_top_atoms.json",
            "08_selected_atom_buffer.txt",
            "10_symbol_context.txt",
            "11_anti_cheat_boundary.json",
        ],
    }


def build_gremlin_source(payload: dict[str, Any]) -> str:
    payload_json_literal = repr(json.dumps(payload, sort_keys=True))
    return """from __future__ import annotations

import json
from typing import Any

from main_computer.providers.ollama import parse_ollama_think_choice, prepare_ollama_generate_payload

PAYLOAD_JSON = """ + payload_json_literal + """


def load_payload() -> dict[str, Any]:
    return json.loads(PAYLOAD_JSON)


def build_head(payload: dict[str, Any], prompt: str | None) -> dict[str, Any]:
    return {
        "kind": "code-gremlin",
        "name": "pyramid_atom_patch_gremlin",
        "strategy": "ai_pyramid_global_grep_atoms",
        "prompt": prompt or payload.get("prompt", ""),
    }


def choose_target_atom(payload: dict[str, Any]) -> dict[str, Any] | None:
    atoms = payload.get("top_atoms") or []
    if not atoms:
        return None
    return max(
        atoms,
        key=lambda atom: (
            int(atom.get("connected_word_count") or 0),
            int(atom.get("layer_score_sum") or 0),
            float(atom.get("score") or 0),
        ),
    )


def build_atom_story(payload: dict[str, Any]) -> dict[str, Any]:
    atom = choose_target_atom(payload)
    if not atom:
        return {"status": "no atom selected"}
    return {
        "path": atom.get("path"),
        "line_start": atom.get("line_start"),
        "line_end": atom.get("line_end"),
        "connected_words": atom.get("connected_words"),
        "connected_word_count": atom.get("connected_word_count"),
        "layer_score_sum": atom.get("layer_score_sum"),
        "score": atom.get("score"),
    }


def build_proposed_edit(payload: dict[str, Any]) -> dict[str, Any] | None:
    edit = payload.get("proposed_edit")
    return dict(edit) if isinstance(edit, dict) else None


def build_content(payload: dict[str, Any]) -> dict[str, Any]:
    edit = build_proposed_edit(payload)
    if edit:
        return {"status": "code_change_inferred", "edit": edit}
    return {
        "status": "needs_more_evidence",
        "fail_condition": payload.get("fail_condition") or "no exact edit inferred from atoms",
    }


def build_body(payload: dict[str, Any]) -> dict[str, Any]:
    edit = build_proposed_edit(payload)
    return {
        "ai_pyramid": payload.get("ai_pyramid", ""),
        "word_hit_summary": payload.get("word_hit_summary", []),
        "target_atom": build_atom_story(payload),
        "target_file": edit.get("target_file") if isinstance(edit, dict) else "",
        "proposed_edit": edit,
    }


def main(prompt: str | None = None) -> dict[str, Any]:
    payload = load_payload()
    content = build_content(payload)
    result = {
        "ok": bool(build_proposed_edit(payload)),
        "head": build_head(payload, prompt),
        "body": build_body(payload),
        "content": content,
        "top_atoms": payload.get("top_atoms", []),
        "symbols_found": payload.get("symbols_found", []),
        "requested_output_path": "diagnostics_output/gremlin_rag_smoke/final_result.json",
    }
    if not result["ok"]:
        result["fail_condition"] = content.get("fail_condition")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), separators=(",", ":"), sort_keys=True))
"""


def validate_python_source(source: str, filename: str) -> tuple[bool, list[str]]:
    issues: list[str] = []
    try:
        compile(source, filename, "exec")
    except SyntaxError as exc:
        return False, [f"syntax error: {exc}"]
    import ast
    tree = ast.parse(source, filename=filename)
    if not any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
        issues.append("missing top-level def main(...)")
    return not issues, issues


def run_gremlin(path: Path, out_dir: Path, args: argparse.Namespace, log: Logger) -> dict[str, Any]:
    if args.no_docker_run:
        log.banner(f"LOCAL IN-PROCESS GREMLIN MAIN USED FOR {path.name}")
        log(f"exec(compile(read_text({path!s}), ...)); main()")
        try:
            namespace: dict[str, Any] = {"__name__": "__generated_gremlin__"}
            source = read_text(path)
            exec(compile(source, str(path), "exec"), namespace)
            main_fn = namespace.get("main")
            if not callable(main_fn):
                return {
                    "ok": False,
                    "returncode": None,
                    "stdout": "",
                    "stderr": "generated gremlin does not define callable main()",
                    "command": ["in-process-main", str(path)],
                }
            value = main_fn()
            stdout = json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n"
            return {
                "ok": True,
                "returncode": 0,
                "stdout": stdout,
                "stderr": "",
                "command": ["in-process-main", str(path)],
                "local_execution_note": "Generated deterministic gremlin main() executed in-process because --no-docker-run was supplied.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "command": ["in-process-main", str(path)],
                "local_execution_note": "Generated deterministic gremlin main() attempted in-process because --no-docker-run was supplied.",
            }

    cmd = [
        "docker", "run", "--rm", "-v", f"{out_dir.resolve()}:/workspace:rw",
        "-w", "/workspace", args.docker_image, "python", f"/workspace/{path.name}",
    ]
    log.banner(f"DOCKER EXEC COMMAND USED FOR {path.name}")
    log(" ".join(cmd))
    return run_command(cmd, timeout_s=args.timeout_s)


def parse_json_object(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    raw = str(stdout or "").strip()
    if not raw:
        return None, "empty stdout"
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value, None
        return None, "JSON was not an object"
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None, "stdout did not contain JSON object"
    try:
        value = json.loads(match.group(0))
        if isinstance(value, dict):
            return value, None
        return None, "embedded JSON was not an object"
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc}"


def extract_edit(result: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [result.get("proposed_edit"), result.get("edit")]
    if isinstance(result.get("content"), dict):
        candidates.append(result["content"].get("edit"))
    if isinstance(result.get("body"), dict):
        candidates.append(result["body"].get("proposed_edit"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("target_file") and candidate.get("old_fragment") and candidate.get("replacement_fragment"):
            return dict(candidate)
    return None


def validate_edit(repo: Path, edit: dict[str, Any]) -> dict[str, Any]:
    try:
        rel = normalize_rel_path(str(edit.get("target_file") or ""))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    target = repo / rel
    if not target.exists():
        return {"ok": False, "target_file": rel, "error": "target file does not exist"}
    old = str(edit.get("old_fragment") or "")
    new = str(edit.get("replacement_fragment") or "")
    source = read_text(target)
    if not old:
        return {"ok": False, "target_file": rel, "error": "old_fragment is empty"}
    if old == new:
        return {"ok": False, "target_file": rel, "error": "replacement equals old_fragment"}
    count = source.count(old)
    if count != 1:
        return {"ok": False, "target_file": rel, "old_fragment_count": count, "error": "old_fragment must occur exactly once"}
    new_text = source.replace(old, new, 1)
    return {
        "ok": True,
        "target_file": rel,
        "old_fragment_count": count,
        "old_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "new_sha256": hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
        "new_text": new_text,
    }


def make_diff(path: str, old_text: str, new_text: str) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="\n",
    ))


def build_snapshot_zip(repo: Path, edit_validation: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    rel = normalize_rel_path(str(edit_validation["target_file"]))
    old_text = read_text(repo / rel)
    new_text = str(edit_validation["new_text"])
    zip_path = out_dir / "evidence_first_patch_snapshot.zip"
    entry = f"{repo.name}/{rel}".replace("\\", "/")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(entry, new_text)
    flat_name = rel.replace("/", "__")
    replacement_path = out_dir / "replacement_files" / flat_name
    write_text(replacement_path, new_text)
    diff = make_diff(rel, old_text, new_text)
    diff_path = out_dir / "reference_diff.patch"
    write_text(diff_path, diff)
    return {
        "zip_path": str(zip_path),
        "snapshot_entry": entry,
        "replacement_path": str(replacement_path),
        "replacement_parent_exists": replacement_path.parent.exists(),
        "zip_entry_written_without_temp_nested_path": True,
        "reference_diff_path": str(diff_path),
        "reference_diff": diff,
    }


def run_new_patch_dry_run(repo: Path, zip_path: Path, timeout_s: int, log: Logger) -> dict[str, Any]:
    new_patch = repo / "new_patch.py"
    if not new_patch.exists():
        return {"ok": False, "error": f"new_patch.py not found: {new_patch}"}
    cmd = [sys.executable, str(new_patch), str(zip_path), "--dry-run", "--target-root", str(repo)]
    log.banner("NEW_PATCH DRY-RUN COMMAND")
    log(" ".join(cmd))
    return run_command(cmd, cwd=repo, timeout_s=timeout_s)


def make_start_here(
    prompt: str,
    ai_pyramid: str,
    word_summary: list[dict[str, Any]],
    selected_atoms: list[dict[str, Any]],
    symbols_found: list[dict[str, str]],
    adapted_edit: dict[str, Any] | None,
    patch_zip: dict[str, Any] | None,
    new_patch_result: dict[str, Any] | None,
    boundary_report: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    lines += ["# START HERE", "", "## Prompt", "", prompt, "", "## AI grep pyramid", "", "```", ai_pyramid.strip(), "```", ""]
    lines.append("## Global word greps")
    lines.append("")
    for item in word_summary:
        lines.append(f"- `{item['term']}`: {item['hit_count']} hits across {item['file_count']} files")
    lines += ["", "## Top selected atoms", ""]
    for idx, atom in enumerate(selected_atoms[:8], start=1):
        lines.append(f"{idx}. `{atom['path']}:{atom['line_start']}-{atom['line_end']}` words={atom['connected_words']} score={atom['score']}")
    lines += ["", "## Symbols discovered from atoms", ""]
    if symbols_found:
        for item in symbols_found:
            lines.append(f"- `{item['symbol']}` in `{item['path']}`")
    else:
        lines.append("- none")
    lines += ["", "## Anti-cheat boundary", ""]
    if boundary_report:
        lines.append(f"- status: `{boundary_report.get('status')}`")
        lines.append(f"- reason: {boundary_report.get('reason')}")
        lines.append(f"- next non-cheating step: {boundary_report.get('next_non_cheating_step')}")
    else:
        lines.append("- no boundary report was produced")
    lines += ["", "## Gremlin / patch result", ""]
    lines.append("- Gremlin source: not generated by this anti-cheat smoke test.")
    lines.append("- Adapted edit: not produced.")
    lines.append("- Patch zip: not produced.")
    if new_patch_result:
        lines.append(f"- new_patch_dry_run: `{new_patch_result.get('reason') or new_patch_result.get('ok')}`")
    lines += ["", "## Read next", ""]
    for name in [
        "01_ai_pyramid_text.txt", "03_parsed_tree.json", "05_word_hit_summary.json",
        "07_top_atoms.json", "08_selected_atom_buffer.txt", "10_symbol_context.txt",
        "11_anti_cheat_boundary.json", "11_GREMLIN_NOT_GENERATED.md",
    ]:
        lines.append(f"- `{name}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI-pyramid atom smoke test with anti-cheat stop before edit inference.")
    parser.add_argument("prompt", nargs="*", help="Patch/code-change prompt.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--source-dir", action="append", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--ai", choices=["ollama", "mock"], default="ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument(
        "--ollama-think",
        choices=["default", "off", "false", "on", "true"],
        default="default",
        help="Ollama thinking mode for generate calls. Default means provider policy: non-thinking unless explicitly enabled.",
    )
    parser.add_argument("--ai-timeout-s", type=int, default=0, help="Compatibility option; streaming uses no HTTP read timeout.")
    parser.add_argument("--include-runner", action="store_true")
    parser.add_argument("--max-evidence-chars", type=int, default=7000)
    parser.add_argument("--symbol-context-chars", type=int, default=4000)
    parser.add_argument("--symbol-context-lines", type=int, default=12)
    parser.add_argument("--docker-image", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_IMAGE", DEFAULT_DOCKER_IMAGE))
    parser.add_argument("--no-docker-run", action="store_true")
    parser.add_argument("--skip-new-patch-dry-run", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        parser.print_help()
        return 2

    repo = Path(args.repo).resolve()
    if not repo.exists():
        print(f"repo does not exist: {repo}", file=sys.stderr)
        return 2

    run_id = f"rgp_{utc_stamp()}_{short_hash(prompt)}"
    out_dir = Path(args.out).resolve() if args.out else repo / DEFAULT_OUTPUT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=False)

    log = Logger(out_dir / "verbose.log", quiet=args.quiet)
    timings: dict[str, float] = {}
    started = time.perf_counter()
    source_dirs = args.source_dir or [d for d in DEFAULT_SOURCE_DIRS if (repo / d).exists()] or ["."]

    phases = {
        "server_probe_ok": False,
        "ai_pyramid_ok": False,
        "global_word_greps_ok": False,
        "atoms_computed": False,
        "evidence_selected": False,
        "symbol_context_ok": False,
        "stopped_before_cheating": False,
        "gremlin_built": False,
        "gremlin_ran": False,
        "edit_inferred": False,
        "old_fragment_verified": False,
        "patch_zip_built": False,
        "new_patch_dry_run_passed": False,
    }

    log.banner("AI PYRAMID -> ATOMS -> GREMLIN PATCH SMOKE")
    config = {
        "prompt": prompt,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "source_dirs": source_dirs,
        "ai": args.ai,
        "model": args.model,
        "ollama_url": args.ollama_url,
        "ollama_think": args.ollama_think,
        "ollama_effective_think": parse_ollama_think_choice(args.ollama_think),
        "no_docker_run": args.no_docker_run,
        "skip_new_patch_dry_run": args.skip_new_patch_dry_run,
    }
    write_json(out_dir / "00_config.json", config)
    write_text(out_dir / "00_prompt.txt", prompt)
    print_block(log, "CONFIG", config)

    with Timer("0. probe server RAG pathway", log, timings):
        server_probe = probe_server_rag_pathway(repo)
        phases["server_probe_ok"] = bool(
            server_probe["viewport_routes_rag_assisted_thinking.py"]["has_evaluate_handler"]
            and server_probe["rag_assisted_thinking_v4.py"]["has_request_runner"]
        )
        write_json(out_dir / "00_server_pathway_probe.json", server_probe)
        print_block(log, "SERVER RAG PATHWAY PROBE", server_probe)

    with Timer("1. AI grep pyramid", log, timings):
        if args.ai == "mock":
            ai_pyramid = mock_pyramid_for_prompt(prompt)
            ai_info = {"ok": True, "mode": "mock"}
            write_text(out_dir / "01_ai_pyramid_request.txt", ai_pyramid_prompt(prompt))
            write_text(out_dir / "01_ai_pyramid_text.txt", ai_pyramid)
            write_json(out_dir / "01_ai_pyramid_info.json", ai_info)
            print_block(log, "MOCK AI PYRAMID TEXT", ai_pyramid)
        else:
            try:
                ai_pyramid, ai_info = call_ollama_pyramid(
                    prompt,
                    args.model,
                    args.ollama_url,
                    args.ai_timeout_s,
                    log,
                    out_dir,
                    think=parse_ollama_think_choice(args.ollama_think),
                )
            except Exception as exc:
                info = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "fallback": "mock pyramid used after AI error"}
                write_json(out_dir / "01_ai_pyramid_error.json", info)
                print_block(log, "AI PYRAMID ERROR; USING MOCK FALLBACK", info)
                ai_pyramid = mock_pyramid_for_prompt(prompt)
                ai_info = {"ok": False, "mode": "ollama_failed_mock_fallback", "error": info["error"]}
                write_text(out_dir / "01_ai_pyramid_text.txt", ai_pyramid)
            write_json(out_dir / "01_ai_pyramid_info.json", ai_info)
        phases["ai_pyramid_ok"] = bool(ai_pyramid.strip())

    with Timer("2. parse pyramid", log, timings):
        parsed_tree = parse_pyramid(ai_pyramid)
        if not parsed_tree["unique_terms"]:
            ai_pyramid = mock_pyramid_for_prompt(prompt)
            write_text(out_dir / "02_parse_fallback_pyramid.txt", ai_pyramid)
            parsed_tree = parse_pyramid(ai_pyramid)
        if not parsed_tree["unique_terms"]:
            raise RuntimeError("AI pyramid produced no grep words")
        write_json(out_dir / "03_parsed_tree.json", parsed_tree)
        write_text(out_dir / "03_unique_words.txt", "\n".join(parsed_tree["unique_terms"]) + "\n")
        print_block(log, "PARSED PYRAMID TREE", parsed_tree)

    with Timer("3. global grep every pyramid word", log, timings):
        source_files = iter_source_files(repo, source_dirs, include_runner=args.include_runner)
        write_json(out_dir / "04_source_files.json", [p.relative_to(repo).as_posix() for p in source_files])
        word_summary: list[dict[str, Any]] = []
        for term in parsed_tree["unique_terms"]:
            out_path = out_dir / "04_word_greps" / f"{safe_name(term)}.jsonl"
            summary = grep_term_global(repo, source_files, term, out_path)
            word_summary.append(summary)
            log(f"[grep-word] {term!r}: {summary['hit_count']} hits across {summary['file_count']} files -> {out_path}")
        write_json(out_dir / "05_word_hit_summary.json", word_summary)
        phases["global_word_greps_ok"] = any(item["hit_count"] for item in word_summary)
        print_block(log, "GLOBAL WORD HIT SUMMARY", word_summary)

    with Timer("4. compute atoms backward from word hits", log, timings):
        word_hits = load_word_hits(out_dir, parsed_tree["unique_terms"])
        atoms = compute_atoms(repo, word_hits, parsed_tree["term_scores"], window=3)
        write_json(out_dir / "06_atoms_all.json", atoms)
        write_json(out_dir / "07_top_atoms.json", atoms[:50])
        phases["atoms_computed"] = bool(atoms)
        print_block(log, "TOP ATOMS", atoms[:15])

    with Timer("5. select atom evidence buffer", log, timings):
        evidence_buffer, selected_atoms, used_by_category = fill_evidence_buffer(
            atoms=atoms,
            max_chars=args.max_evidence_chars,
            category_limits={"same_line": 1800, "nearby_window": 4200, "block_window": 3500},
        )
        write_text(out_dir / "08_selected_atom_buffer.txt", evidence_buffer)
        write_json(out_dir / "08_selected_atoms.json", selected_atoms)
        write_json(out_dir / "08_category_usage.json", used_by_category)
        phases["evidence_selected"] = bool(selected_atoms)
        print_block(log, "SELECTED ATOM BUFFER", evidence_buffer or "<no atom evidence selected>")

    with Timer("6. symbol context from atom evidence", log, timings):
        symbol_context, symbols_found = supplemental_symbol_context(
            repo=repo,
            evidence=evidence_buffer,
            selected_atoms=selected_atoms,
            source_files=source_files,
            max_chars=args.symbol_context_chars,
            context=args.symbol_context_lines,
            log=log,
        )
        write_text(out_dir / "10_symbol_context.txt", symbol_context)
        write_json(out_dir / "10_symbols_found.json", symbols_found)
        phases["symbol_context_ok"] = bool(symbols_found)
        print_block(log, "SYMBOL CONTEXT", symbol_context or "<none>")

    adapted_edit = None
    edit_validation: dict[str, Any] = {"ok": False, "skipped": True, "reason": "anti_cheat_boundary_reached"}
    patch_zip: dict[str, Any] | None = None
    new_patch_result: dict[str, Any] | None = {
        "ok": None,
        "skipped": True,
        "reason": "anti_cheat_boundary_reached_before_gremlin_or_patch",
    }
    gremlin_path: Path | None = None
    boundary_report: dict[str, Any] = {}

    with Timer("7. anti-cheat boundary before proposed edit", log, timings):
        boundary_report = build_anti_cheat_boundary_report(
            prompt=prompt,
            ai_pyramid=ai_pyramid,
            parsed_tree=parsed_tree,
            word_summary=word_summary,
            selected_atoms=selected_atoms,
            symbols_found=symbols_found,
            symbol_context=symbol_context,
        )
        phases["stopped_before_cheating"] = True
        write_json(out_dir / "11_anti_cheat_boundary.json", boundary_report)
        write_text(
            out_dir / "11_GREMLIN_NOT_GENERATED.md",
            "# Gremlin not generated\n\n"
            "This smoke test intentionally stopped before the first cheating boundary.\n\n"
            "The pyramid, global word greps, backward-computed atoms, selected evidence, "
            "and symbol context were produced. Generating a proposed edit from a "
            "hard-coded stop-button/red rule would cheat, so no gremlin, edit, patch zip, "
            "or new_patch dry-run was produced.\n\n"
            "Next non-cheating step: add a second AI call that receives the atom evidence "
            "and writes an ordinary Python gremlin whose `main()` assembles the proposed edit, "
            "or add a declared reusable patch-kernel registry and test that registry separately.\n",
        )
        write_json(out_dir / "12_gremlin_executor_result.json", {"ok": None, "skipped": True, "reason": "anti_cheat_boundary"})
        write_json(out_dir / "12_gremlin_parsed.json", {"parsed": None, "parse_error": "anti_cheat_boundary"})
        write_json(out_dir / "13_adapted_edit.json", {})
        write_json(out_dir / "13_edit_validation.json", edit_validation)
        write_json(out_dir / "14_patch_zip.json", {})
        write_text(out_dir / "14_reference_diff.patch", "")
        write_json(out_dir / "15_new_patch_dry_run.json", new_patch_result)
        print_block(log, "ANTI-CHEAT BOUNDARY", boundary_report)

    start_here = make_start_here(
        prompt,
        ai_pyramid,
        word_summary,
        selected_atoms,
        symbols_found,
        adapted_edit,
        patch_zip,
        new_patch_result,
        boundary_report,
    )
    write_text(out_dir / "START_HERE.md", start_here)

    ok = bool(
        phases["ai_pyramid_ok"]
        and phases["global_word_greps_ok"]
        and phases["atoms_computed"]
        and phases["evidence_selected"]
        and phases["stopped_before_cheating"]
    )

    final_report = {
        "ok": ok,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "prompt": prompt,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "phase_status": phases,
        "timings": timings,
        "start_here": str(out_dir / "START_HERE.md"),
        "ai_pyramid_path": str(out_dir / "01_ai_pyramid_text.txt"),
        "word_hit_summary_path": str(out_dir / "05_word_hit_summary.json"),
        "top_atoms_path": str(out_dir / "07_top_atoms.json"),
        "selected_atom_buffer_path": str(out_dir / "08_selected_atom_buffer.txt"),
        "symbol_context_path": str(out_dir / "10_symbol_context.txt"),
        "anti_cheat_boundary_path": str(out_dir / "11_anti_cheat_boundary.json"),
        "anti_cheat_boundary": boundary_report,
        "gremlin_file": None,
        "gremlin_source_copy": None,
        "gremlin_main_result": str(out_dir / "12_gremlin_parsed.json"),
        "adapted_edit": adapted_edit,
        "edit_validation": {k: v for k, v in edit_validation.items() if k != "new_text"},
        "patch_zip": {k: v for k, v in (patch_zip or {}).items() if k != "reference_diff"},
        "new_patch_dry_run": new_patch_result,
    }
    write_json(out_dir / "final_report.json", final_report)
    print_block(log, "START HERE", start_here)
    print_block(log, "FINAL REPORT", final_report)

    summary = {
        "ok": ok,
        "elapsed_s": final_report["elapsed_s"],
        "out_dir": str(out_dir),
        "start_here": str(out_dir / "START_HERE.md"),
        "final_report": str(out_dir / "final_report.json"),
        "status": boundary_report.get("status"),
        "anti_cheat_boundary": str(out_dir / "11_anti_cheat_boundary.json"),
        "gremlin_file": None,
        "gremlin_main_result": str(out_dir / "12_gremlin_parsed.json"),
        "adapted_edit": str(out_dir / "13_adapted_edit.json"),
        "patch_zip": None,
        "new_patch_dry_run_ok": None if new_patch_result is None else new_patch_result.get("ok"),
    }
    print_block(log, "FINAL SUMMARY JSON", summary)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
