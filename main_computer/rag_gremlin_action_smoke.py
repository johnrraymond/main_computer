#!/usr/bin/env python3
"""
rag_gremlin_action_smoke.py

Non-cheating RAG gremlin action smoke test.

This leaves the existing smoke tests alone and adds the next stage after the
anti-cheat boundary:

  prompt
    -> AI call 1 returns a bare grep-word pyramid
    -> global case-insensitive grep once per pyramid word
    -> backward atom discovery from word hits
    -> selected atom evidence buffer
    -> symbol context
    -> AI call 2 generates an ordinary Python gremlin-generator
    -> generator.main() returns Python source for the final gremlin
    -> final gremlin.main() directly modifies repository files
    -> runner detects changed files from disk diffs
    -> runner builds a new_patch snapshot zip
    -> runner optionally runs new_patch.py --dry-run

Important contract:
  The runner does not infer the edit. The generated gremlin does.
  If the generated gremlin cannot safely act, it may return needs_more_evidence.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
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
from main_computer import rag_gremlin_pyramid_atom_smoke as base  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path("debug_assets") / "rga"
DEFAULT_MODEL = os.environ.get("MAIN_COMPUTER_GREMLIN_MODEL", base.DEFAULT_MODEL)
DEFAULT_OLLAMA_URL = base.DEFAULT_OLLAMA_URL
DEFAULT_DOCKER_IMAGE = base.DEFAULT_DOCKER_IMAGE

DEFAULT_SECOND_PROMPT_CHAR_LIMIT = 24000
DEFAULT_SELECTED_ATOM_JSON_CHAR_LIMIT = 12000
DEFAULT_SYMBOL_CONTEXT_CHAR_LIMIT = 5000


def int_env_default(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


DEFAULT_AI_TIMEOUT_S = int_env_default("MAIN_COMPUTER_RGA_AI_TIMEOUT_S", 0)
DEFAULT_GREMLIN_TIMEOUT_S = int_env_default("MAIN_COMPUTER_RGA_GREMLIN_TIMEOUT_S", 0)
DEFAULT_SECOND_PROMPT_CHAR_LIMIT = int_env_default("MAIN_COMPUTER_RGA_SECOND_PROMPT_CHARS", 12000)
DEFAULT_SELECTED_ATOM_JSON_CHAR_LIMIT = int_env_default("MAIN_COMPUTER_RGA_SELECTED_ATOM_JSON_CHARS", 5000)
DEFAULT_SYMBOL_CONTEXT_CHAR_LIMIT = int_env_default("MAIN_COMPUTER_RGA_SYMBOL_CONTEXT_CHARS", 3000)

ACTION_DEFAULT_SOURCE_DIRS = ["main_computer"]
ACTION_RUNNER_NAMES = {
    "rag_gremlin_action_smoke.py",
    "rag_gremlin_pyramid_atom_smoke.py",
}


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def safe_name(text: str, limit: int = 72) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", text.lower())
    stem = "_".join(parts)[:limit].strip("_") or "item"
    return f"{stem}_{short_hash(text)}"


def compact_word_summary(word_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "term": item.get("term"),
            "hit_count": item.get("hit_count"),
            "file_count": item.get("file_count"),
        }
        for item in word_summary
    ]


def parse_ai_pyramid_or_fail(ai_pyramid: str, out_dir: Path, log: base.Logger) -> dict[str, Any]:
    parsed_tree = base.parse_pyramid(ai_pyramid)
    if parsed_tree["unique_terms"]:
        return parsed_tree

    parse_error = {
        "ok": False,
        "reason": "AI pyramid produced no grep words",
    }
    base.write_json(out_dir / "02_parse_error.json", parse_error)
    base.print_block(log, "AI PYRAMID EMPTY", parse_error)
    raise RuntimeError("AI pyramid produced no grep words")


def action_should_skip_source(path: Path, repo: Path, include_runner: bool = False) -> bool:
    try:
        rel = path.resolve().relative_to(repo.resolve())
    except ValueError:
        rel = path
    rel_text = rel.as_posix()
    if any(part in {".git", "__pycache__", "debug_assets", "tests"} for part in rel.parts):
        return True
    if rel_text.startswith("tools/patching/reports/"):
        return True
    if path.name.startswith("gremlin_action_") or path.name in {"11_gremlin_source.py", "11_gremlin_invalid_source.py"}:
        return True
    if not include_runner and path.name in ACTION_RUNNER_NAMES:
        return True
    return False


def iter_action_source_files(repo: Path, source_dirs: list[str], include_runner: bool = False) -> list[Path]:
    source_files = base.iter_source_files(repo, source_dirs, include_runner=include_runner)
    return [
        path
        for path in source_files
        if not action_should_skip_source(path, repo, include_runner=include_runner)
    ]


def candidate_files_from_atoms(selected_atoms: list[dict[str, Any]], limit: int = 8) -> list[str]:
    files: list[str] = []
    for atom in selected_atoms:
        path = str(atom.get("path") or "").replace("\\", "/")
        if not path or path.startswith("symbol definition:"):
            continue
        if path not in files:
            files.append(path)
        if len(files) >= limit:
            break
    return files


def top_atoms_for_prompt(selected_atoms: list[dict[str, Any]], limit: int = 3, text_limit: int = DEFAULT_SELECTED_ATOM_JSON_CHAR_LIMIT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used = 0
    seen_text: set[str] = set()
    seen_spans: set[tuple[str, int | None, int | None]] = set()
    per_file_same_line: dict[str, int] = {}
    per_file_context: dict[str, int] = {}
    for atom in selected_atoms:
        if len(out) >= limit:
            break
        path = str(atom.get("path") or "")
        category = str(atom.get("category") or "")
        text = str(atom.get("text") or "")
        span = (path, atom.get("line_start"), atom.get("line_end"))
        if not path or not text or text in seen_text or span in seen_spans:
            continue
        if category == "same_line":
            if per_file_same_line.get(path, 0) >= 1:
                continue
        elif category in {"nearby_window", "block_window"}:
            if per_file_context.get(path, 0) >= 1:
                continue
        item = {
            "path": path,
            "line_start": atom.get("line_start"),
            "line_end": atom.get("line_end"),
            "category": category,
            "connected_words": atom.get("connected_words"),
            "connected_word_count": atom.get("connected_word_count"),
            "layer_score_sum": atom.get("layer_score_sum"),
            "score": atom.get("score"),
            "text": text,
        }
        encoded = json.dumps(item, sort_keys=True)
        if used + len(encoded) > text_limit:
            continue
        out.append(item)
        seen_text.add(text)
        seen_spans.add(span)
        if category == "same_line":
            per_file_same_line[path] = per_file_same_line.get(path, 0) + 1
        elif category in {"nearby_window", "block_window"}:
            per_file_context[path] = per_file_context.get(path, 0) + 1
        used += len(encoded)
    return out


def build_gremlin_generator_prompt(
    *,
    user_prompt: str,
    repo_name: str,
    selected_atoms: list[dict[str, Any]],
    symbol_context: str,
    candidate_files: list[str],
    max_prompt_chars: int = DEFAULT_SECOND_PROMPT_CHAR_LIMIT,
    max_atom_json_chars: int = DEFAULT_SELECTED_ATOM_JSON_CHAR_LIMIT,
    max_symbol_context_chars: int = DEFAULT_SYMBOL_CONTEXT_CHAR_LIMIT,
) -> tuple[str, dict[str, Any]]:
    selected_for_ai = top_atoms_for_prompt(selected_atoms, limit=3, text_limit=max_atom_json_chars)
    capped_symbol_context = symbol_context[:max_symbol_context_chars]
    prompt_text = user_prompt

    def render() -> str:
        selected_atom_json = json.dumps(selected_for_ai, indent=2, sort_keys=True)
        return f"""Return only raw Python source code. Do not return JSON. No markdown, no backticks, no prose.

You are writing a Python generator program.
Use paths exactly as shown in Candidate files or Evidence. Do not prepend the repo root name.
For replacement edits, the old text must appear verbatim in Evidence or Additional context.

The output program must:
- define main()
- take no arguments
- not print
- not write files
- not execute generated code
- not include an if __name__ == "__main__" block
- not contain comments or docstrings
- does build a string containing a second Python program
- does return that string from main()

The generator must build the returned source from short blocks, not from one large opaque string.
Use escape-safe construction, such as:
- small helper functions that return source-line lists
- small named line groups
- "\\n".join(lines)

Each source block should have one job:
- imports
- path helpers
- search or anchor helpers
- replacement helpers
- apply/orchestration main

The string returned by main() must be Python source code.

The returned program must:
- define main()
- take no arguments
- compile with compile(source, "<generated_editor>", "exec")
- use pathlib.Path
- directly edit repo-relative files only when an exact safe edit is grounded
- not print
- not include an if __name__ == "__main__" block
- not contain comments or docstrings
- not have return statements at module top level
- only use facts present in the request and evidence below
- make the smallest safe edit that satisfies the request
- be built from small task-shaped helper functions, not one large inline main()

Returned-program helper functions:
- should be short
- should each do one clear operation
- should have compact mechanical names
- should avoid prose-style names
- should avoid fixed template names unless they naturally fit the edit

Replacement edit contract for the returned program:
- the old string must be an exact literal substring copied from Evidence or Additional context
- the new string must be an exact end-state string justified by the request and the surrounding evidence
- never use the same value for old and new
- every replace call must be replace(old, new, 1)
- guard every replacement with: if old not in text: return "needs_more_evidence"
- write only after the guarded replacement produces changed text
- if a requested color, class, attribute, selector, or call shape is not present in the evidence, do not invent one
- never emit a direct editor that sets old and new to the same literal, even with an old == new guard

If the exact edit cannot be determined from the evidence, return a valid no-op returned program instead of guessing.
If your only possible replacement has old == new, or if either exact literal is missing, you must return the no-op program.
A valid no-op returned program imports pathlib.Path, defines main() with no arguments, performs no path I/O, and returns "needs_more_evidence".

Use this exact returned-program body for fail-closed no-op cases:
from pathlib import Path

def main():
    return "needs_more_evidence"

Original request:
<<<
{prompt_text}
>>>

Repo root:
<<<
{repo_name}
>>>

Candidate files:
<<<
{candidate_files}
>>>

Evidence:
<<<
{selected_atom_json}
>>>

Additional context:
<<<
{capped_symbol_context}
>>>
"""
    prompt = render()
    if len(prompt) > max_prompt_chars:
        while len(prompt) > max_prompt_chars and selected_for_ai:
            selected_for_ai.pop()
            prompt = render()
        if len(prompt) > max_prompt_chars:
            overflow = len(prompt) - max_prompt_chars
            capped_symbol_context = capped_symbol_context[:-overflow] if overflow < len(capped_symbol_context) else ""
            prompt = render()
        if len(prompt) > max_prompt_chars:
            overflow = len(prompt) - max_prompt_chars
            prompt_text = prompt_text[:max(0, len(prompt_text) - overflow - 32)] + "\n[truncated]"
            prompt = render()
        if len(prompt) > max_prompt_chars:
            prompt = prompt[:max_prompt_chars]
    stats = {
        "second_prompt_chars": len(prompt),
        "selected_atom_count_for_ai": len(selected_for_ai),
        "skipped_atom_count_for_budget": max(0, len(selected_atoms) - len(selected_for_ai)),
        "symbol_context_chars": len(capped_symbol_context),
        "candidate_file_count": len(candidate_files),
    }
    return prompt, stats


def gremlin_generator_prompt(
    *,
    user_prompt: str,
    repo_name: str,
    ai_pyramid: str,
    word_summary: list[dict[str, Any]],
    selected_atoms: list[dict[str, Any]],
    symbol_context: str,
    candidate_files: list[str],
) -> str:
    prompt, _stats = build_gremlin_generator_prompt(
        user_prompt=user_prompt,
        repo_name=repo_name,
        selected_atoms=selected_atoms,
        symbol_context=symbol_context,
        candidate_files=candidate_files,
    )
    return prompt


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:python|py)?\s*(.*?)\s*```$", stripped, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip() + "\n"
    return stripped + ("\n" if not stripped.endswith("\n") else "")


def summarize_action_gremlin_stream(events: list[Any], source_text: str, elapsed_s: float) -> dict[str, Any]:
    dict_events = [event for event in events if isinstance(event, dict)]
    final_event = dict_events[-1] if dict_events else {}
    non_empty_response_events = sum(
        1
        for event in dict_events
        if isinstance(event.get("response"), str) and bool(str(event.get("response")).strip())
    )
    key_set: set[str] = set()
    for event in dict_events:
        key_set.update(str(key) for key in event.keys())
    summary: dict[str, Any] = {
        "ok": True,
        "stream": True,
        "elapsed_s": round(elapsed_s, 3),
        "stream_event_count": len(events),
        "non_empty_response_event_count": non_empty_response_events,
        "accumulated_response_chars": len(source_text),
        "response_chars": len(source_text),
        "response_empty": not bool(source_text.strip()),
        "response_preview": source_text[:400],
        "response_repr": repr(source_text[:400]),
        "final_event_metadata": {
            key: value
            for key, value in final_event.items()
            if key != "response"
        },
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


def direct_ollama_generate_stream(
    *,
    payload: dict[str, Any],
    url: str,
    timeout_s: int,
    log: base.Logger,
    raw_path: Path,
    stream_label: str,
) -> tuple[str, dict[str, Any]]:
    """Directly call Ollama /api/generate and read JSONL bytes as they arrive.

    This intentionally duplicates the tiny diagnostic probe instead of routing
    through the broader application helper.  The smoke test's contract is to
    observe exactly what the model server emits, persist every raw byte, and
    append each visible ``response`` fragment before the final empty stats event.
    """
    stream_payload, think_metadata = prepare_ollama_generate_payload(payload)
    stream_payload = {**stream_payload, "stream": True}
    data = json.dumps(stream_payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    if timeout_s:
        log(f"[{stream_label}] direct Ollama stream; ignoring configured HTTP timeout {timeout_s}s")
    else:
        log(f"[{stream_label}] direct Ollama stream with no HTTP read timeout")

    started = time.perf_counter()
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    events: list[dict[str, Any]] = []
    source_parts: list[str] = []
    pending = bytearray()

    def handle_line(raw_line: bytes) -> None:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            return
        try:
            event: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            event = {"error": f"JSONDecodeError: {exc}", "raw": line}
        if not isinstance(event, dict):
            event = {"parsed": event, "parsed_type": type(event).__name__}
        events.append(event)
        fragment = event.get("response", "")
        if fragment is None:
            fragment = ""
        fragment = str(fragment)
        if fragment:
            source_parts.append(fragment)
            log(f"[{stream_label} token] {fragment}")

    with urllib.request.urlopen(req, timeout=None) as response, raw_path.open("wb") as raw_handle:
        while True:
            chunk = response.read(1)
            if not chunk:
                break
            raw_handle.write(chunk)
            raw_handle.flush()
            pending.extend(chunk)
            while b"\n" in pending:
                raw_line, _, rest = pending.partition(b"\n")
                handle_line(bytes(raw_line))
                pending = bytearray(rest)

        if pending:
            handle_line(bytes(pending))
            if not bytes(pending).endswith(b"\n"):
                raw_handle.write(b"\n")
                raw_handle.flush()

    source_text = "".join(source_parts)
    summary = summarize_action_gremlin_stream(events, source_text, time.perf_counter() - started)
    summary.update(think_metadata)
    return source_text, summary


def call_ollama_pyramid_direct(
    prompt: str,
    model: str,
    url: str,
    timeout_s: int,
    log: base.Logger,
    out_dir: Path,
    *,
    think: bool | str | None = None,
) -> tuple[str, dict[str, Any]]:
    request_text = base.ai_pyramid_prompt(prompt)
    payload, think_metadata = prepare_ollama_generate_payload(
        {
            "model": model,
            "prompt": request_text,
            "stream": True,
            "options": {"temperature": 0, "num_predict": 160},
        },
        think=think,
    )
    base.write_text(out_dir / "01_ai_pyramid_request.txt", request_text)
    base.write_json(out_dir / "01_ai_pyramid_post_payload.json", payload)
    base.print_block(log, "AI PYRAMID REQUEST", request_text)

    raw_path = out_dir / "01_ai_pyramid_raw_response.jsonl"
    text, summary = direct_ollama_generate_stream(
        payload=payload,
        url=url,
        timeout_s=timeout_s,
        log=log,
        raw_path=raw_path,
        stream_label="ai-pyramid",
    )
    base.write_json(out_dir / "01_ai_pyramid_response_summary.json", summary)
    base.print_block(log, "AI PYRAMID RESPONSE SUMMARY", summary)
    base.write_text(out_dir / "01_ai_pyramid_text.txt", text)
    base.print_block(log, "AI PYRAMID RAW TEXT", text if text else "<empty>")
    info = {
        "ok": True,
        "model": model,
        "url": url,
        "mode": "direct_ollama_generate_stream",
        "elapsed_s": summary.get("elapsed_s"),
        "raw_response_path": str(raw_path),
        "response_summary_path": str(out_dir / "01_ai_pyramid_response_summary.json"),
        "response_chars": summary.get("response_chars"),
        "response_empty": summary.get("response_empty"),
        "response_preview": summary.get("response_preview"),
        "stream": summary.get("stream"),
        "stream_event_count": summary.get("stream_event_count"),
        "non_empty_response_event_count": summary.get("non_empty_response_event_count"),
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
    return text, info


def call_ollama_generate_raw_python_stream(
    *,
    payload: dict[str, Any],
    url: str,
    timeout_s: int,
    log: base.Logger,
    raw_path: Path,
    stream_label: str,
) -> tuple[str, dict[str, Any]]:
    return direct_ollama_generate_stream(
        payload=payload,
        url=url,
        timeout_s=timeout_s,
        log=log,
        raw_path=raw_path,
        stream_label=stream_label,
    )


def call_ollama_gremlin_source(
    prompt_text: str,
    model: str,
    url: str,
    timeout_s: int,
    log: base.Logger,
    out_dir: Path,
    *,
    think: bool | str | None = None,
) -> tuple[str, dict[str, Any]]:
    payload, think_metadata = prepare_ollama_generate_payload(
        {
            "model": model,
            "prompt": prompt_text,
            "stream": True,
            "options": {
                "temperature": 0,
                "num_predict": 2200,
            },
        },
        think=think,
    )
    base.write_text(out_dir / "11_gremlin_generator_request.txt", prompt_text)
    base.write_json(out_dir / "11_gremlin_generator_post_payload.json", payload)

    log.banner("AI ACTION-GREMLIN GENERATOR REQUEST")
    log(prompt_text)

    raw_response_path = out_dir / "11_gremlin_generator_raw_response.jsonl"
    response_text, summary = call_ollama_generate_raw_python_stream(
        payload=payload,
        url=url,
        timeout_s=timeout_s,
        log=log,
        raw_path=raw_response_path,
        stream_label="ai-gremlin",
    )
    source = strip_code_fence(response_text)
    if not source.strip():
        error_info = {
            "ok": False,
            "error": "model produced no visible response text",
            "raw_response_path": str(raw_response_path),
            "summary": summary,
        }
        base.write_json(out_dir / "11_gremlin_generator_error.json", error_info)
        raise RuntimeError("model produced no visible response text")

    info = {
        "ok": True,
        "mode": "ollama",
        "model": model,
        "url": url,
        "elapsed_s": summary.get("elapsed_s"),
        "request_path": str(out_dir / "11_gremlin_generator_request.txt"),
        "raw_response_path": str(raw_response_path),
        "source_path": str(out_dir / "11_gremlin_source.py"),
        "response_chars": summary.get("response_chars"),
        "accumulated_response_chars": summary.get("accumulated_response_chars"),
        "response_empty": summary.get("response_empty"),
        "stream": summary.get("stream"),
        "stream_event_count": summary.get("stream_event_count"),
        "non_empty_response_event_count": summary.get("non_empty_response_event_count"),
        "final_event_metadata": summary.get("final_event_metadata"),
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
    base.write_json(out_dir / "11_gremlin_generator_info.json", info)
    log.banner("AI ACTION-GREMLIN RESPONSE SUMMARY")
    log(json.dumps(summary, indent=2, sort_keys=True))
    log.banner("AI ACTION-GREMLIN SOURCE")
    log(source)
    return source, info


def _parse_source_for_validation(source: str, filename: str) -> tuple[ast.Module | None, list[str]]:
    try:
        tree = ast.parse(source, filename=filename)
        compile(source, filename, "exec")
    except SyntaxError as exc:
        return None, [f"syntax error: {exc}"]
    return tree, []


def _main_function_issues(tree: ast.Module) -> list[str]:
    mains = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
    if not mains:
        return ["missing top-level def main()"]
    args = mains[0].args
    arg_count = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
    if arg_count or args.vararg or args.kwarg:
        return ["top-level main() must take no arguments"]
    return []


def _source_safety_issues(
    tree: ast.Module,
    *,
    allowed_import_roots: set[str],
    blocked_names: set[str],
    blocked_imports: set[str],
    reject_action_contract: bool,
) -> list[str]:
    issues: list[str] = []
    for node in ast.walk(tree):
        if reject_action_contract and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "action":
            issues.append("disallowed action() contract")
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in blocked_imports or root not in allowed_import_roots:
                    issues.append(f"disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in blocked_imports or root not in allowed_import_roots:
                issues.append(f"disallowed import-from: {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in blocked_names:
                issues.append(f"disallowed call: {func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in {"system", "popen", "remove", "unlink", "rmdir", "mkdir", "rename", "replace"}:
                issues.append(f"disallowed attribute call: {func.attr}()")
    return issues


def validate_gremlin_generator_source(source: str, filename: str) -> tuple[bool, list[str]]:
    tree, issues = _parse_source_for_validation(source, filename)
    if tree is None:
        return False, issues
    issues.extend(_main_function_issues(tree))
    issues.extend(_source_safety_issues(
        tree,
        allowed_import_roots={"__future__", "re", "json", "typing"},
        blocked_names={"open", "eval", "exec", "compile", "__import__", "input", "globals", "locals", "vars", "dir", "setattr", "delattr", "getattr"},
        blocked_imports={"os", "pathlib", "subprocess", "socket", "requests", "shutil", "tempfile", "urllib", "http", "ftplib", "glob", "importlib"},
        reject_action_contract=True,
    ))
    return not issues, sorted(set(issues))


def validate_generated_gremlin_source(source: str, filename: str) -> tuple[bool, list[str]]:
    tree, issues = _parse_source_for_validation(source, filename)
    if tree is None:
        return False, issues
    issues.extend(_main_function_issues(tree))
    issues.extend(_source_safety_issues(
        tree,
        allowed_import_roots={"__future__", "re", "json", "typing", "pathlib"},
        blocked_names={"eval", "exec", "compile", "__import__", "input", "globals", "locals", "vars", "dir", "setattr", "delattr", "getattr"},
        blocked_imports={"os", "subprocess", "socket", "requests", "shutil", "tempfile", "urllib", "http", "ftplib", "glob", "importlib"},
        reject_action_contract=True,
    ))
    return not issues, sorted(set(issues))


def validate_action_gremlin_source(source: str, filename: str) -> tuple[bool, list[str]]:
    return validate_generated_gremlin_source(source, filename)


def build_gremlin_generator_driver(generator_filename: str) -> str:
    template = """from __future__ import annotations
import contextlib, importlib.util, io, json, pathlib, traceback
workspace = pathlib.Path(__file__).resolve().parent
generator_path = workspace / __GENERATOR_FILENAME__
generated_source_path = workspace / "11_gremlin_source.py"
output_path = workspace / "11_gremlin_generator_output.json"
stdout_path = workspace / "11_gremlin_generator_stdout.txt"
stderr_path = workspace / "11_gremlin_generator_stderr.txt"
stdout_buffer = io.StringIO()
stderr_buffer = io.StringIO()
try:
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        spec = importlib.util.spec_from_file_location("ai_gremlin_generator", generator_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        generated_source = module.main()
        if not isinstance(generated_source, str) or not generated_source.strip():
            raise TypeError("generator main() did not return a non-empty source string")
        generated_source_path.write_text(generated_source, encoding="utf-8")
    output = {"ok": True, "generated_source_path": str(generated_source_path), "generated_source_chars": len(generated_source)}
except Exception as exc:
    output = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
finally:
    output["generator_stdout"] = stdout_buffer.getvalue()
    output["generator_stderr"] = stderr_buffer.getvalue()
    output["generator_stdout_path"] = str(stdout_path)
    output["generator_stderr_path"] = str(stderr_path)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    stdout_path.write_text(output["generator_stdout"], encoding="utf-8")
    stderr_path.write_text(output["generator_stderr"], encoding="utf-8")
summary = {"ok": output.get("ok"), "generated_source_chars": output.get("generated_source_chars"), "output_json": str(output_path)}
if output.get("error"):
    summary["error"] = output.get("error")
print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
"""
    return template.replace("__GENERATOR_FILENAME__", repr(generator_filename))


def build_gremlin_driver(gremlin_filename: str, repo_path: Path) -> str:
    template = """from __future__ import annotations
import contextlib, importlib.util, io, json, os, pathlib, traceback
workspace = pathlib.Path(__file__).resolve().parent
repo = pathlib.Path(__REPO_PATH__)
result_path = workspace / "12_action_result.json"
output_path = workspace / "12_gremlin_output.json"
stdout_path = workspace / "12_gremlin_stdout.txt"
stderr_path = workspace / "12_gremlin_stderr.txt"
gremlin_path = workspace / __GREMLIN_FILENAME__
stdout_buffer = io.StringIO()
stderr_buffer = io.StringIO()
try:
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        os.chdir(repo)
        spec = importlib.util.spec_from_file_location("generated_direct_gremlin", gremlin_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        #result = module.main()
        result = "MADE IT!"
    output = {"ok": True, "result": result}
except Exception as exc:
    output = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
finally:
    output["gremlin_stdout"] = stdout_buffer.getvalue()
    output["gremlin_stderr"] = stderr_buffer.getvalue()
    output["gremlin_stdout_path"] = str(stdout_path)
    output["gremlin_stderr_path"] = str(stderr_path)
    payload = json.dumps(output, indent=2, sort_keys=True) + "\\n"
    result_path.write_text(payload, encoding="utf-8")
    output_path.write_text(payload, encoding="utf-8")
    stdout_path.write_text(output["gremlin_stdout"], encoding="utf-8")
    stderr_path.write_text(output["gremlin_stderr"], encoding="utf-8")
summary = {"ok": output.get("ok"), "output_json": str(output_path)}
if output.get("error"):
    summary["error"] = output.get("error")
print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
"""
    return template.replace("__GREMLIN_FILENAME__", repr(gremlin_filename)).replace("__REPO_PATH__", repr(str(repo_path)))


def summarize_saved_gremlin_output(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"ok": False, "reason": "saved gremlin output was not a JSON object"}
    return {
        "ok": value.get("ok"),
        "result_type": type(value.get("result")).__name__ if "result" in value else None,
        "gremlin_stdout_chars": len(str(value.get("gremlin_stdout") or "")),
        "gremlin_stderr_chars": len(str(value.get("gremlin_stderr") or "")),
    }


def attach_saved_gremlin_output_paths(run_result: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    output_path = out_dir / "12_gremlin_output.json"
    stdout_path = out_dir / "12_gremlin_stdout.txt"
    stderr_path = out_dir / "12_gremlin_stderr.txt"
    run_result["gremlin_output_files"] = {"output_json": str(output_path), "stdout": str(stdout_path), "stderr": str(stderr_path)}
    run_result["gremlin_output_saved"] = output_path.exists()
    run_result["gremlin_stdout_saved"] = stdout_path.exists()
    run_result["gremlin_stderr_saved"] = stderr_path.exists()
    if output_path.exists():
        try:
            run_result["gremlin_output_summary"] = summarize_saved_gremlin_output(json.loads(base.read_text(output_path)))
        except Exception as exc:
            run_result["gremlin_output_summary"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return run_result


def saved_file_entry(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists() and path.is_file():
        entry["size_bytes"] = path.stat().st_size
    return entry


def write_gremlin_generation_files(
    out_dir: Path,
    gremlin_info: dict[str, Any] | None,
    validation_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Write an index of the action-gremlin generation artifacts.

    The generated gremlin can fail generation or validation. This index keeps
    the original model request/response, extracted source, active source, and
    validation payload together so the run can be audited without guessing which
    11_* file matters.
    """
    paths = {
        "request": out_dir / "11_gremlin_generator_request.txt",
        "post_payload": out_dir / "11_gremlin_generator_post_payload.json",
        "raw_response": out_dir / "11_gremlin_generator_raw_response.jsonl",
        "generator_info": out_dir / "11_gremlin_generator_info.json",
        "generator_source": out_dir / "11_gremlin_generator_source.py",
        "generator_output": out_dir / "11_gremlin_generator_output.json",
        "generator_stdout": out_dir / "11_gremlin_generator_stdout.txt",
        "generator_stderr": out_dir / "11_gremlin_generator_stderr.txt",
        "generator_error": out_dir / "11_gremlin_generator_error.json",
        "active_source": out_dir / "11_gremlin_source.py",
        "invalid_source": out_dir / "11_gremlin_invalid_source.py",
        "validation": out_dir / "11_gremlin_validation.json",
    }
    payload = {
        "files": {name: saved_file_entry(path) for name, path in paths.items()},
        "gremlin_info": gremlin_info or {},
        "validation": validation_payload or {},
    }
    base.write_json(out_dir / "11_gremlin_generation_files.json", payload)
    return payload


def run_gremlin_generator(generator_path: Path, out_dir: Path, args: argparse.Namespace, log: base.Logger) -> dict[str, Any]:
    driver_source = build_gremlin_generator_driver(generator_path.name)
    driver_path = out_dir / "11_gremlin_generator_driver.py"
    base.write_text(driver_path, driver_source)

    if args.no_docker_run:
        cmd = [sys.executable, str(driver_path)]
        log.banner("LOCAL GREMLIN-GENERATOR DRIVER COMMAND")
        log(" ".join(cmd))
        result = base.run_command(cmd, cwd=out_dir, timeout_s=args.timeout_s)
    else:
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{out_dir.resolve()}:/workspace:rw",
            "-w", "/workspace",
            args.docker_image,
            "python", "/workspace/11_gremlin_generator_driver.py",
        ]
        log.banner("DOCKER GREMLIN-GENERATOR DRIVER COMMAND")
        log(" ".join(cmd))
        result = base.run_command(cmd, timeout_s=args.timeout_s)
    output_path = out_dir / "11_gremlin_generator_output.json"
    result["generator_output_saved"] = output_path.exists()
    if output_path.exists():
        try:
            result["generator_output"] = json.loads(base.read_text(output_path))
        except Exception as exc:
            result["generator_output"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return result


def run_action_gremlin(gremlin_path: Path, repo: Path, out_dir: Path, args: argparse.Namespace, log: base.Logger) -> dict[str, Any]:
    driver_source = build_gremlin_driver(gremlin_path.name, repo)
    driver_path = out_dir / "12_action_driver.py"
    base.write_text(driver_path, driver_source)

    if args.no_docker_run:
        cmd = [sys.executable, str(driver_path)]
        log.banner("LOCAL DIRECT GREMLIN DRIVER COMMAND")
        log(" ".join(cmd))
        result = base.run_command(cmd, cwd=repo, timeout_s=args.timeout_s)
        result["local_execution_note"] = "Generated gremlin executed by local driver because --no-docker-run was supplied."
        return attach_saved_gremlin_output_paths(result, out_dir)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{repo.resolve()}:/repo:rw",
        "-v", f"{out_dir.resolve()}:/workspace:rw",
        "-w", "/repo",
        args.docker_image,
        "python", "/workspace/12_action_driver.py",
    ]
    log.banner("DOCKER DIRECT GREMLIN DRIVER COMMAND")
    log(" ".join(cmd))
    result = base.run_command(cmd, timeout_s=args.timeout_s)
    return attach_saved_gremlin_output_paths(result, out_dir)


def parse_driver_result(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    parsed, error = base.parse_json_object(stdout)
    if error:
        return None, error
    if not isinstance(parsed, dict):
        return None, "driver output was not an object"
    if not parsed.get("ok"):
        return parsed, parsed.get("error") or "driver reported failure"
    return parsed, None


def parse_saved_driver_result(out_dir: Path, stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    """Prefer the driver's saved JSON output over stdout transport.

    Generated gremlins are allowed to build ordinary Python action code. If that
    action prints diagnostic text, stdout is no longer a reliable machine channel.
    The driver writes 12_gremlin_output.json after execution; use that as the
    source of truth and keep stdout/stderr as captured logs.
    """
    saved_path = out_dir / "12_gremlin_output.json"
    if not saved_path.exists():
        return parse_driver_result(stdout)
    try:
        parsed = json.loads(base.read_text(saved_path))
    except Exception as exc:
        return None, f"saved gremlin output parse failed: {type(exc).__name__}: {exc}"
    if not isinstance(parsed, dict):
        return None, "saved gremlin output was not an object"
    if not parsed.get("ok"):
        return parsed, parsed.get("error") or "driver reported failure"
    return parsed, None


def snapshot_candidate_files(repo: Path, candidate_files: list[str]) -> dict[str, str]:
    snapshots: dict[str, str] = {}
    for raw in candidate_files:
        try:
            rel = base.normalize_rel_path(raw)
        except ValueError:
            continue
        path = repo / rel
        if path.exists() and path.is_file():
            snapshots[rel] = base.read_text(path)
    return snapshots


def verify_disk_changed_files(repo: Path, before: dict[str, str], candidate_files: list[str]) -> dict[str, Any]:
    changed_files: list[dict[str, Any]] = []
    for rel, old_text in before.items():
        path = repo / rel
        if not path.exists() or not path.is_file():
            continue
        new_text = base.read_text(path)
        if new_text == old_text:
            continue
        changed_files.append({
            "path": rel,
            "old_text": old_text,
            "new_text": new_text,
            "old_sha256": hashlib.sha256(old_text.encode("utf-8")).hexdigest(),
            "new_sha256": hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
            "reason": "detected by disk diff after generated gremlin main()",
            "evidence": {"source": "disk_diff"},
        })
    return {
        "ok": bool(changed_files),
        "status": "changed_files_built" if changed_files else "no_changes",
        "changed_file_count": len(changed_files),
        "changed_files": changed_files,
        "candidate_file_count": len(candidate_files),
        "snapshotted_file_count": len(before),
        "no_change_reason": None if changed_files else "gremlin_no_changes",
    }


def verify_changed_files(repo: Path, action_result: dict[str, Any], candidate_files: list[str]) -> dict[str, Any]:
    return verify_disk_changed_files(repo, {}, candidate_files)


def build_snapshot_zip_from_changed_files(repo: Path, verification: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    changed_files = verification.get("changed_files") or []
    if not changed_files:
        raise ValueError("no changed files to package")

    zip_path = out_dir / "action_gremlin_patch_snapshot.zip"
    diffs: list[str] = []
    replacements: list[dict[str, str]] = []

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in changed_files:
            rel = base.normalize_rel_path(str(item["path"]))
            new_text = str(item["new_text"])
            old_text = str(item["old_text"])
            entry = f"{repo.name}/{rel}".replace("\\", "/")
            archive.writestr(entry, new_text)

            flat_name = rel.replace("/", "__")
            replacement_path = out_dir / "replacement_files" / flat_name
            base.write_text(replacement_path, new_text)
            replacements.append({
                "path": rel,
                "snapshot_entry": entry,
                "replacement_path": str(replacement_path),
            })

            diffs.append("".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="\n",
            )))

    reference_diff = "\n".join(diffs)
    diff_path = out_dir / "reference_diff.patch"
    base.write_text(diff_path, reference_diff)

    return {
        "zip_path": str(zip_path),
        "changed_files": replacements,
        "reference_diff_path": str(diff_path),
        "reference_diff": reference_diff,
    }


def artifact_exists(out_dir: Path, name: str) -> bool:
    return (out_dir / name).exists()


def artifact_path_or_none(out_dir: Path, name: str) -> str | None:
    path = out_dir / name
    return str(path) if path.exists() else None


def append_artifact_line(lines: list[str], out_dir: Path, label: str, name: str, *, include_missing: bool = False) -> None:
    if artifact_exists(out_dir, name):
        lines.append(f"- {label}: `{name}`")
    elif include_missing:
        lines.append(f"- {label}: `{name}` (not created)")


def existing_artifact_names(out_dir: Path, names: list[str]) -> list[str]:
    return [name for name in names if artifact_exists(out_dir, name)]


def make_start_here(
    *,
    out_dir: Path,
    prompt: str,
    ai_pyramid: str,
    word_summary: list[dict[str, Any]],
    selected_atoms: list[dict[str, Any]],
    symbols_found: list[dict[str, str]],
    candidate_files: list[str],
    gremlin_info: dict[str, Any] | None,
    verification: dict[str, Any] | None,
    patch_zip: dict[str, Any] | None,
    new_patch_result: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    lines += ["# START HERE", "", "## Prompt", "", prompt, "", "## AI grep pyramid", "", "```", ai_pyramid.strip(), "```", ""]
    lines += ["## Global word greps", ""]
    for item in word_summary:
        lines.append(f"- `{item['term']}`: {item['hit_count']} hits across {item['file_count']} files")
    lines += ["", "## Candidate files given to the gremlin", ""]
    for path in candidate_files:
        lines.append(f"- `{path}`")
    lines += ["", "## Top selected atoms", ""]
    for idx, atom in enumerate(selected_atoms[:8], start=1):
        lines.append(f"{idx}. `{atom['path']}:{atom['line_start']}-{atom['line_end']}` words={atom['connected_words']} score={atom['score']}")
    lines += ["", "## Symbols discovered from atoms", ""]
    if symbols_found:
        for item in symbols_found:
            lines.append(f"- `{item['symbol']}` in `{item['path']}`")
    else:
        lines.append("- none")
    lines += ["", "## Action gremlin", ""]
    if gremlin_info:
        lines.append(f"- active source: `{gremlin_info.get('source_path')}`")
        lines.append(f"- model: `{gremlin_info.get('model')}`")
        if gremlin_info.get("mode"):
            lines.append(f"- mode: `{gremlin_info.get('mode')}`")
        if gremlin_info.get("ok") is not None:
            lines.append(f"- generation_ok: `{gremlin_info.get('ok')}`")
        if gremlin_info.get("error"):
            lines.append(f"- error: `{gremlin_info.get('error')}`")
        if gremlin_info.get("validation_issues"):
            lines.append(f"- validation_issues: `{gremlin_info.get('validation_issues')}`")
    else:
        lines.append("- not generated")
    append_artifact_line(lines, out_dir, "generator request", "11_gremlin_generator_request.txt", include_missing=True)
    append_artifact_line(lines, out_dir, "generator request stats", "11_gremlin_generator_request_stats.json")
    append_artifact_line(lines, out_dir, "generator post payload", "11_gremlin_generator_post_payload.json")
    append_artifact_line(lines, out_dir, "generator error", "11_gremlin_generator_error.json")
    append_artifact_line(lines, out_dir, "generator raw response", "11_gremlin_generator_raw_response.jsonl")
    append_artifact_line(lines, out_dir, "generator info", "11_gremlin_generator_info.json")
    append_artifact_line(lines, out_dir, "generation file index", "11_gremlin_generation_files.json", include_missing=True)
    append_artifact_line(lines, out_dir, "validation result", "11_gremlin_validation.json", include_missing=True)
    append_artifact_line(lines, out_dir, "invalid generated source", "11_gremlin_invalid_source.py")
    append_artifact_line(lines, out_dir, "driver result", "12_action_driver_result.json", include_missing=True)
    append_artifact_line(lines, out_dir, "saved gremlin output", "12_gremlin_output.json", include_missing=True)
    append_artifact_line(lines, out_dir, "captured gremlin stdout", "12_gremlin_stdout.txt", include_missing=True)
    append_artifact_line(lines, out_dir, "captured gremlin stderr", "12_gremlin_stderr.txt", include_missing=True)
    append_artifact_line(lines, out_dir, "parsed action result", "12_action_result.json", include_missing=True)
    lines += ["", "## Verification", ""]
    if verification:
        lines.append(f"- changed_file_count: `{verification.get('changed_file_count')}`")
        lines.append(f"- ok: `{verification.get('ok')}`")
        if verification.get("no_change_reason"):
            lines.append(f"- no_change_reason: `{verification.get('no_change_reason')}`")
    if patch_zip:
        lines.append(f"- patch_zip: `{patch_zip.get('zip_path')}`")
        lines.append(f"- reference_diff: `{patch_zip.get('reference_diff_path')}`")
    if new_patch_result:
        lines.append(f"- new_patch_dry_run_ok: `{new_patch_result.get('ok')}`")
    lines += ["", "## Read next", ""]
    read_next_names = existing_artifact_names(out_dir, [
        "01_ai_pyramid_text.txt",
        "05_word_hit_summary.json",
        "07_top_atoms.json",
        "08_selected_atom_buffer.txt",
        "10_symbol_context.txt",
        "11_gremlin_generator_request.txt",
        "11_gremlin_generator_request_stats.json",
        "11_gremlin_generator_post_payload.json",
        "11_gremlin_generator_error.json",
        "11_gremlin_generator_raw_response.jsonl",
        "11_gremlin_generator_info.json",
        "11_gremlin_generation_files.json",
        "11_gremlin_validation.json",
        "11_gremlin_invalid_source.py",
        "11_gremlin_source.py",
        "12_action_inputs.json",
        "12_action_driver.py",
        "12_action_driver_result.json",
        "12_gremlin_output.json",
        "12_gremlin_stdout.txt",
        "12_gremlin_stderr.txt",
        "12_gremlin_output_files.json",
        "12_action_result.json",
        "13_changed_files_verification.json",
        "14_patch_zip.json",
        "14_reference_diff.patch",
        "15_new_patch_dry_run.json",
    ])
    if read_next_names:
        for name in read_next_names:
            lines.append(f"- `{name}`")
    else:
        lines.append("- no artifact files have been written yet")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG atom evidence -> AI generated action-gremlin smoke test.")
    parser.add_argument("prompt", nargs="*", help="Patch/code-change prompt.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--source-dir", action="append", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--ai", choices=["ollama"], default="ollama", help="AI source for the grep pyramid.")
    parser.add_argument("--gremlin-ai", choices=["ollama"], default="ollama", help="Source for the gremlin-generator. ollama asks the model.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--gremlin-model", default=None)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument(
        "--ollama-think",
        choices=["default", "off", "false", "on", "true"],
        default="default",
        help="Ollama thinking mode for generate calls. Default means provider policy: non-thinking unless explicitly enabled.",
    )
    parser.add_argument(
        "--ai-timeout-s",
        type=int,
        default=DEFAULT_AI_TIMEOUT_S,
        help=(
            "Compatibility option for the pyramid call. "
            "Ollama streaming uses no HTTP read timeout. "
            f"Default: {DEFAULT_AI_TIMEOUT_S}."
        ),
    )
    parser.add_argument(
        "--gremlin-timeout-s",
        type=int,
        default=DEFAULT_GREMLIN_TIMEOUT_S,
        help=(
            "Compatibility option for action-gremlin generation. "
            "Ollama streaming uses no HTTP read timeout. "
            f"Default: {DEFAULT_GREMLIN_TIMEOUT_S}."
        ),
    )
    parser.add_argument("--include-runner", action="store_true")
    parser.add_argument("--max-evidence-chars", type=int, default=7000)
    parser.add_argument("--symbol-context-chars", type=int, default=DEFAULT_SYMBOL_CONTEXT_CHAR_LIMIT)
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

    run_id = f"rga_{utc_stamp()}_{short_hash(prompt)}"
    out_dir = Path(args.out).resolve() if args.out else repo / DEFAULT_OUTPUT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=False)

    log = base.Logger(out_dir / "verbose.log", quiet=args.quiet)
    timings: dict[str, float] = {}
    started = time.perf_counter()

    source_dirs = args.source_dir or [d for d in ACTION_DEFAULT_SOURCE_DIRS if (repo / d).exists()] or ["."]
    gremlin_model = args.gremlin_model or args.model

    phases = {
        "server_probe_ok": False,
        "ai_pyramid_ok": False,
        "global_word_greps_ok": False,
        "atoms_computed": False,
        "evidence_selected": False,
        "symbol_context_ok": False,
        "action_gremlin_generated": False,
        "action_gremlin_validated": False,
        "action_gremlin_ran": False,
        "changed_files_verified": False,
        "patch_zip_built": False,
        "new_patch_dry_run_passed": False,
        "needs_more_evidence": False,
    }

    log.banner("RAG ATOMS -> AI ACTION-GREMLIN SMOKE")
    config = {
        "prompt": prompt,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "source_dirs": source_dirs,
        "ai": args.ai,
        "gremlin_ai": args.gremlin_ai,
        "model": args.model,
        "gremlin_model": gremlin_model,
        "ollama_url": args.ollama_url,
        "ollama_think": args.ollama_think,
        "ollama_effective_think": parse_ollama_think_choice(args.ollama_think),
        "ai_timeout_s": args.ai_timeout_s,
        "gremlin_timeout_s": args.gremlin_timeout_s,
        "no_docker_run": args.no_docker_run,
        "skip_new_patch_dry_run": args.skip_new_patch_dry_run,
    }
    base.print_block(log, "CONFIG", config)
    base.write_json(out_dir / "00_config.json", config)
    base.write_text(out_dir / "00_prompt.txt", prompt)

    with base.Timer("0. probe server RAG pathway", log, timings):
        server_probe = base.probe_server_rag_pathway(repo)
        phases["server_probe_ok"] = bool(
            server_probe["viewport_routes_rag_assisted_thinking.py"]["has_evaluate_handler"]
            and server_probe["rag_assisted_thinking_v4.py"]["has_request_runner"]
        )
        base.write_json(out_dir / "00_server_pathway_probe.json", server_probe)

    with base.Timer("1. AI grep pyramid", log, timings):
        if args.ai_timeout_s:
            log(f"[ai-pyramid] streaming with no HTTP read timeout; ignoring configured HTTP timeout {args.ai_timeout_s}s")
        else:
            log("[ai-pyramid] streaming with no HTTP read timeout")
        try:
            ai_pyramid, ai_info = call_ollama_pyramid_direct(
                prompt=prompt,
                model=args.model,
                url=args.ollama_url,
                timeout_s=args.ai_timeout_s,
                log=log,
                out_dir=out_dir,
                think=parse_ollama_think_choice(args.ollama_think),
            )
        except Exception as exc:
            info = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            base.write_json(out_dir / "01_ai_pyramid_error.json", info)
            base.print_block(log, "AI PYRAMID ERROR", info)
            raise RuntimeError(f"AI pyramid generation failed: {info['error']}") from exc
        base.write_json(out_dir / "01_ai_pyramid_info.json", ai_info)
        phases["ai_pyramid_ok"] = bool(ai_pyramid.strip())

    with base.Timer("2. parse pyramid", log, timings):
        parsed_tree = parse_ai_pyramid_or_fail(ai_pyramid, out_dir, log)
        base.write_json(out_dir / "03_parsed_tree.json", parsed_tree)
        base.write_text(out_dir / "03_unique_words.txt", "\n".join(parsed_tree["unique_terms"]) + "\n")
        base.print_block(log, "PARSED PYRAMID TREE", parsed_tree)

    with base.Timer("3. global grep every pyramid word", log, timings):
        source_files = iter_action_source_files(repo, source_dirs, include_runner=args.include_runner)
        base.write_json(out_dir / "04_source_files.json", [p.relative_to(repo).as_posix() for p in source_files])

        word_summary: list[dict[str, Any]] = []
        for term in parsed_tree["unique_terms"]:
            out_path = out_dir / "04_word_greps" / f"{base.safe_name(term)}.jsonl"
            summary = base.grep_term_global(repo, source_files, term, out_path)
            word_summary.append(summary)
            log(f"[grep-word] {term!r}: {summary['hit_count']} hits across {summary['file_count']} files -> {out_path}")
        base.write_json(out_dir / "05_word_hit_summary.json", word_summary)
        phases["global_word_greps_ok"] = any(item["hit_count"] for item in word_summary)
        base.print_block(log, "GLOBAL WORD HIT SUMMARY", word_summary)

    with base.Timer("4. compute atoms backward from word hits", log, timings):
        word_hits = base.load_word_hits(out_dir, parsed_tree["unique_terms"])
        atoms = base.compute_atoms(repo, word_hits, parsed_tree["term_scores"], window=3)
        base.write_json(out_dir / "06_atoms_all.json", atoms)
        base.write_json(out_dir / "07_top_atoms.json", atoms[:50])
        phases["atoms_computed"] = bool(atoms)
        base.print_block(log, "TOP ATOMS", atoms[:15])

    with base.Timer("5. select atom evidence buffer", log, timings):
        evidence_buffer, selected_atoms, used_by_category = base.fill_evidence_buffer(
            atoms=atoms,
            max_chars=args.max_evidence_chars,
            category_limits={
                "same_line": 1800,
                "nearby_window": 4200,
                "block_window": 3500,
            },
        )
        base.write_text(out_dir / "08_selected_atom_buffer.txt", evidence_buffer)
        base.write_json(out_dir / "08_selected_atoms.json", selected_atoms)
        base.write_json(out_dir / "08_category_usage.json", used_by_category)
        phases["evidence_selected"] = bool(selected_atoms)
        base.print_block(log, "SELECTED ATOM BUFFER", evidence_buffer or "<no atom evidence selected>")

    with base.Timer("6. symbol context from atom evidence", log, timings):
        symbol_context, symbols_found = base.supplemental_symbol_context(
            repo=repo,
            evidence=evidence_buffer,
            selected_atoms=selected_atoms,
            source_files=source_files,
            max_chars=args.symbol_context_chars,
            context=args.symbol_context_lines,
            log=log,
        )
        base.write_text(out_dir / "10_symbol_context.txt", symbol_context)
        base.write_json(out_dir / "10_symbols_found.json", symbols_found)
        phases["symbol_context_ok"] = bool(symbols_found)
        base.print_block(log, "SYMBOL CONTEXT", symbol_context or "<none>")

    candidate_files = candidate_files_from_atoms(selected_atoms)
    base.write_json(out_dir / "10_candidate_files.json", candidate_files)

    gremlin_info: dict[str, Any] | None = None
    with base.Timer("7. build gremlin-generator and generated gremlin", log, timings):
        generator_prompt, second_prompt_stats = build_gremlin_generator_prompt(
            user_prompt=prompt,
            repo_name=repo.name,
            selected_atoms=selected_atoms,
            symbol_context=symbol_context,
            candidate_files=candidate_files,
        )
        base.write_json(out_dir / "11_gremlin_generator_request_stats.json", second_prompt_stats)
        try:
            generator_source, gremlin_info = call_ollama_gremlin_source(
                prompt_text=generator_prompt,
                model=gremlin_model,
                url=args.ollama_url,
                timeout_s=args.gremlin_timeout_s,
                log=log,
                out_dir=out_dir,
                think=parse_ollama_think_choice(args.ollama_think),
            )
        except Exception as exc:
            error_info = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            existing_error_path = out_dir / "11_gremlin_generator_error.json"
            if existing_error_path.exists():
                try:
                    existing_error = json.loads(base.read_text(existing_error_path))
                    if isinstance(existing_error, dict):
                        error_info = {**existing_error, "raised_error": error_info["error"]}
                except Exception:
                    pass
            gremlin_info = {
                "ok": False,
                "mode": "ollama_failed",
                "model": gremlin_model,
                "error": error_info["error"],
            }
            base.write_json(out_dir / "11_gremlin_generator_error.json", error_info)
            base.write_json(out_dir / "11_gremlin_validation.json", {"ok": False, "issues": [error_info["error"]]})
            gremlin_generation_files = write_gremlin_generation_files(
                out_dir,
                gremlin_info,
                {"ok": False, "issues": [error_info["error"]]},
            )
            base.print_block(log, "AI ACTION-GREMLIN ERROR", error_info)
            base.print_block(log, "ACTION-GREMLIN GENERATION FILES", gremlin_generation_files)
            raise RuntimeError(f"AI action-gremlin generation failed: {error_info['error']}") from exc
        phases["action_gremlin_generated"] = True

        generator_name = "gremlin_generator_" + safe_name(prompt, 48) + ".py"
        generator_path = out_dir / generator_name
        gremlin_name = "gremlin_action_" + safe_name(prompt, 48) + ".py"
        gremlin_path = out_dir / gremlin_name
        base.write_text(generator_path, generator_source)
        base.write_text(out_dir / "11_gremlin_generator_source.py", generator_source)

        ok_generator, generator_issues = validate_gremlin_generator_source(generator_source, generator_name)
        if not ok_generator:
            base.write_text(out_dir / "11_gremlin_invalid_source.py", generator_source)
            gremlin_info = {
                **(gremlin_info or {}),
                "ok": False,
                "mode": "gremlin_generator_invalid",
                "validation_issues": generator_issues,
                "invalid_source_path": str(out_dir / "11_gremlin_invalid_source.py"),
                "source_path": str(out_dir / "11_gremlin_source.py"),
            }
            validation_payload = {
                "ok": False,
                "stage": "generator_source",
                "generator_issues": generator_issues,
                "generated_issues": [],
            }
            base.write_json(out_dir / "11_gremlin_validation.json", validation_payload)
            gremlin_generation_files = write_gremlin_generation_files(out_dir, gremlin_info, validation_payload)
            base.print_block(log, "ACTION-GREMLIN GENERATION FILES", gremlin_generation_files)
            raise RuntimeError(f"gremlin-generator failed validation: {generator_issues}")

        generator_run = run_gremlin_generator(generator_path, out_dir, args, log)

        error = generator_run.get("error")
        if error:
            raise RuntimeError(f"gremlin-generator execution failed: {error}")

        generator_run = run_gremlin_generator(generator_path, out_dir, args, log)

        generator_output = generator_run.get("generator_output") if isinstance(generator_run, dict) else None
        if not isinstance(generator_output, dict):
            raise RuntimeError("gremlin-generator did not produce output json")

        if not generator_output.get("ok"):
            raise RuntimeError(f"gremlin-generator execution failed: {generator_output.get('error') or 'unknown error'}")

        generated_source_path = out_dir / "11_gremlin_source.py"
        if not generated_source_path.exists():
            raise RuntimeError("gremlin-generator did not write 11_gremlin_source.py")

        generated_source = base.read_text(generated_source_path)
        if not isinstance(generated_source, str) or not generated_source.strip():
            raise RuntimeError("gremlin-generator wrote an empty source string")

        base.print_block(log, "GENERATED GREMLIN SOURCE", generated_source)

        if not isinstance(generated_source, str) or not generated_source.strip():
            raise RuntimeError("gremlin-generator did not return a non-empty source string")

        base.write_text(out_dir / "11_gremlin_source.py", generated_source)
        base.print_block(log, "GENERATED GREMLIN SOURCE", generated_source)

        return 0


        base.write_json(out_dir / "11_gremlin_generator_driver_result.json", generator_run)
        generated_output = generator_run.get("generator_output") if isinstance(generator_run, dict) else None
        if not generator_run.get("ok") or not isinstance(generated_output, dict) or not generated_output.get("ok"):
            error = (generated_output or {}).get("error") if isinstance(generated_output, dict) else generator_run.get("stderr")
            validation_payload = {
                "ok": False,
                "stage": "generator_execution",
                "generator_issues": [],
                "generated_issues": [str(error or "generator execution failed")],
            }
            base.write_json(out_dir / "11_gremlin_validation.json", validation_payload)
            gremlin_generation_files = write_gremlin_generation_files(out_dir, gremlin_info, validation_payload)
            base.print_block(log, "ACTION-GREMLIN GENERATION FILES", gremlin_generation_files)
            raise RuntimeError(f"gremlin-generator execution failed: {error}")

        generated_source_path = out_dir / "11_gremlin_source.py"
        generated_source = base.read_text(generated_source_path)
        ok_source, source_issues = validate_generated_gremlin_source(generated_source, gremlin_name)
        validation_payload = {
            "ok": ok_source,
            "stage": "generated_gremlin_source",
            "generator_issues": [],
            "generated_issues": source_issues,
        }
        if not ok_source:
            base.write_text(out_dir / "11_gremlin_invalid_source.py", generated_source)
            gremlin_info = {
                **(gremlin_info or {}),
                "ok": False,
                "mode": "generated_gremlin_invalid",
                "validation_issues": source_issues,
                "invalid_source_path": str(out_dir / "11_gremlin_invalid_source.py"),
                "source_path": str(generated_source_path),
            }
        else:
            base.write_text(gremlin_path, generated_source)

        base.write_json(out_dir / "11_gremlin_validation.json", validation_payload)
        gremlin_generation_files = write_gremlin_generation_files(out_dir, gremlin_info, validation_payload)
        base.print_block(log, "ACTION-GREMLIN GENERATION FILES", gremlin_generation_files)
        if not ok_source:
            phases["action_gremlin_validated"] = False
            base.print_block(
                log,
                "ACTION-GREMLIN VALIDATION FAILED",
                {
                    "ok": False,
                    "issues": source_issues,
                    "invalid_source_path": str(out_dir / "11_gremlin_invalid_source.py"),
                },
            )
            raise RuntimeError(f"generated action gremlin failed validation: {source_issues}")
        phases["action_gremlin_validated"] = True

    with base.Timer("8. execute generated gremlin.main()", log, timings):
        before_files = snapshot_candidate_files(repo, candidate_files)
        base.write_json(out_dir / "12_before_file_snapshot.json", {
            path: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for path, text in before_files.items()
        })
        run_result = run_action_gremlin(gremlin_path, repo, out_dir, args, log)
        base.write_json(out_dir / "12_action_driver_result.json", run_result)
        parsed_driver, driver_error = parse_saved_driver_result(out_dir, str(run_result.get("stdout") or ""))
        if parsed_driver is not None:
            base.write_json(out_dir / "12_action_result.json", parsed_driver)
        else:
            base.write_json(out_dir / "12_action_result.json", {"ok": False, "error": driver_error})
        saved_output_files = {
            "output_json": str(out_dir / "12_gremlin_output.json"),
            "stdout": str(out_dir / "12_gremlin_stdout.txt"),
            "stderr": str(out_dir / "12_gremlin_stderr.txt"),
            "output_saved": (out_dir / "12_gremlin_output.json").exists(),
            "stdout_saved": (out_dir / "12_gremlin_stdout.txt").exists(),
            "stderr_saved": (out_dir / "12_gremlin_stderr.txt").exists(),
            "summary": run_result.get("gremlin_output_summary"),
        }
        base.write_json(out_dir / "12_gremlin_output_files.json", saved_output_files)
        phases["action_gremlin_ran"] = bool(run_result.get("ok")) and parsed_driver is not None and driver_error is None
        base.print_block(log, "ACTION-GREMLIN DRIVER RESULT", run_result)
        base.print_block(log, "ACTION-GREMLIN SAVED OUTPUT FILES", saved_output_files)
        base.print_block(log, "ACTION-GREMLIN PARSED RESULT", {"parsed": parsed_driver, "error": driver_error})

    verification: dict[str, Any]
    patch_zip: dict[str, Any] | None = None
    new_patch_result: dict[str, Any] | None = None

    with base.Timer("9. verify disk changed files", log, timings):
        verification = verify_disk_changed_files(repo, before_files, candidate_files)
        phases["changed_files_verified"] = bool(verification.get("ok"))
        phases["needs_more_evidence"] = not phases["changed_files_verified"]
        compact_verification = {
            k: v for k, v in verification.items() if k != "changed_files"
        } | {
            "changed_files": [
                {kk: vv for kk, vv in item.items() if kk not in {"old_text", "new_text"}}
                for item in verification.get("changed_files", [])
            ]
        }
        base.write_json(out_dir / "13_changed_files_verification.json", compact_verification)
        base.print_block(log, "CHANGED FILES VERIFICATION", compact_verification)

    with base.Timer("10. build changed-file snapshot zip", log, timings):
        if verification.get("ok"):
            patch_zip = build_snapshot_zip_from_changed_files(repo, verification, out_dir)
            phases["patch_zip_built"] = True
            base.write_json(out_dir / "14_patch_zip.json", {k: v for k, v in patch_zip.items() if k != "reference_diff"})
            base.write_text(out_dir / "14_reference_diff.patch", patch_zip["reference_diff"])
            base.print_block(log, "PATCH ZIP", {k: v for k, v in patch_zip.items() if k != "reference_diff"})
            base.print_block(log, "REFERENCE DIFF", patch_zip["reference_diff"])
        else:
            base.write_json(out_dir / "14_patch_zip.json", {"skipped": True, "reason": verification.get("no_change_reason") or verification.get("error")})
            log("[patch] skipped because gremlin did not produce verified changed files")

    with base.Timer("11. new_patch dry-run", log, timings):
        if args.skip_new_patch_dry_run:
            new_patch_result = {"ok": None, "skipped": True, "reason": "--skip-new-patch-dry-run"}
        elif patch_zip:
            new_patch_result = base.run_new_patch_dry_run(repo, Path(patch_zip["zip_path"]), args.timeout_s, log)
            phases["new_patch_dry_run_passed"] = bool(new_patch_result.get("ok"))
        else:
            new_patch_result = {"ok": None, "skipped": True, "reason": "no verified patch zip built"}
        base.write_json(out_dir / "15_new_patch_dry_run.json", new_patch_result)
        base.print_block(log, "NEW_PATCH DRY-RUN RESULT", new_patch_result)

    start_here = make_start_here(
        out_dir=out_dir,
        prompt=prompt,
        ai_pyramid=ai_pyramid,
        word_summary=word_summary,
        selected_atoms=selected_atoms,
        symbols_found=symbols_found,
        candidate_files=candidate_files,
        gremlin_info=gremlin_info,
        verification=verification,
        patch_zip=patch_zip,
        new_patch_result=new_patch_result,
    )
    base.write_text(out_dir / "START_HERE.md", start_here)

    status = "patch_verified" if phases["new_patch_dry_run_passed"] else (
        "changed_files_verified_no_dry_run" if phases["patch_zip_built"] and args.skip_new_patch_dry_run else (
            "gremlin_needs_more_evidence" if phases["needs_more_evidence"] else "gremlin_no_verified_patch"
        )
    )

    ok = bool(
        phases["ai_pyramid_ok"]
        and phases["global_word_greps_ok"]
        and phases["atoms_computed"]
        and phases["evidence_selected"]
        and phases["action_gremlin_generated"]
        and phases["action_gremlin_validated"]
        and phases["action_gremlin_ran"]
        and (
            phases["new_patch_dry_run_passed"]
            or (args.skip_new_patch_dry_run and phases["patch_zip_built"])
        )
    )

    final_report = {
        "ok": ok,
        "status": status,
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
        "candidate_files": candidate_files,
        "gremlin_generator_request": str(out_dir / "11_gremlin_generator_request.txt"),
        "gremlin_generator_request_stats": str(out_dir / "11_gremlin_generator_request_stats.json"),
        "gremlin_generator_post_payload": artifact_path_or_none(out_dir, "11_gremlin_generator_post_payload.json"),
        "gremlin_generator_error": artifact_path_or_none(out_dir, "11_gremlin_generator_error.json"),
        "gremlin_generator_raw_response": artifact_path_or_none(out_dir, "11_gremlin_generator_raw_response.jsonl"),
        "gremlin_generator_info": artifact_path_or_none(out_dir, "11_gremlin_generator_info.json"),
        "gremlin_generation_files": str(out_dir / "11_gremlin_generation_files.json"),
        "gremlin_info": gremlin_info or {},
        "gremlin_validation": str(out_dir / "11_gremlin_validation.json"),
        "gremlin_file": str(gremlin_path),
        "gremlin_source_copy": str(out_dir / "11_gremlin_source.py"),
        "action_driver_result": str(out_dir / "12_action_driver_result.json"),
        "gremlin_output": str(out_dir / "12_gremlin_output.json"),
        "gremlin_stdout": str(out_dir / "12_gremlin_stdout.txt"),
        "gremlin_stderr": str(out_dir / "12_gremlin_stderr.txt"),
        "gremlin_output_files": str(out_dir / "12_gremlin_output_files.json"),
        "action_result": str(out_dir / "12_action_result.json"),
        "changed_files_verification": {k: v for k, v in verification.items() if k != "changed_files"},
        "patch_zip": {k: v for k, v in (patch_zip or {}).items() if k != "reference_diff"},
        "new_patch_dry_run": new_patch_result,
    }
    base.write_json(out_dir / "final_report.json", final_report)

    base.print_block(log, "START HERE", start_here)
    base.print_block(log, "FINAL REPORT", final_report)

    summary = {
        "ok": ok,
        "status": status,
        "elapsed_s": final_report["elapsed_s"],
        "out_dir": str(out_dir),
        "start_here": str(out_dir / "START_HERE.md"),
        "final_report": str(out_dir / "final_report.json"),
        "gremlin_file": str(gremlin_path),
        "gremlin_generation_files": str(out_dir / "11_gremlin_generation_files.json"),
        "gremlin_mode": (gremlin_info or {}).get("mode"),
        "gremlin_error": (gremlin_info or {}).get("error"),
        "gremlin_generator_error": artifact_path_or_none(out_dir, "11_gremlin_generator_error.json"),
        "gremlin_validation": str(out_dir / "11_gremlin_validation.json"),
        "action_result": str(out_dir / "12_action_result.json"),
        "gremlin_output": str(out_dir / "12_gremlin_output.json"),
        "gremlin_stdout": str(out_dir / "12_gremlin_stdout.txt"),
        "gremlin_stderr": str(out_dir / "12_gremlin_stderr.txt"),
        "patch_zip": (patch_zip or {}).get("zip_path"),
        "new_patch_dry_run_ok": None if new_patch_result is None else new_patch_result.get("ok"),
    }
    base.print_block(log, "FINAL SUMMARY JSON", summary)

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
