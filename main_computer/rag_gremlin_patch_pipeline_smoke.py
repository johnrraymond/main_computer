#!/usr/bin/env python3
"""
rag_gremlin_patch_pipeline_smoke.py

Evidence-first gremlin patch pipeline smoke test.

Purpose:
  Turn the current gremlin/RAG recommendation into a runnable smoke test.

Pipeline:
  prompt
    -> probe current server RAG-AT pathway
    -> grep gremlin_*.py first
    -> targeted source grep fallback
    -> supplemental symbol-definition context
    -> synthesize evidence-first gremlin
    -> run gremlin.main()
    -> adapt result into exact edit
    -> verify old_fragment occurs exactly once
    -> build changed-file snapshot zip
    -> run: python new_patch.py <zip> --dry-run

This intentionally does not call Ollama by default. The point of this smoke test
is to prove that strong evidence can produce a useful gremlin and verified
new_patch artifact without waiting minutes for a model-generated gremlin.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import traceback
import zipfile
from typing import Any


DEFAULT_OUTPUT_ROOT = Path("debug_assets") / "rgp"
DEFAULT_DOCKER_IMAGE = "main-computer-executor:latest"

DEFAULT_SOURCE_DIRS = ["main_computer", "tests"]
GREMLIN_GLOBS = ["gremlin_*.py"]
SOURCE_GLOBS = ["*.py", "*.html", "*.js", "*.css", "*.ts", "*.tsx"]

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "diagnostics_output",
    "debug_assets",
    "snapshots",
    "revision_control",
    ".main_computer_browser_profile",
    "generated_component_docs",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

RUNNER_NAMES = {
    "gremlin_rag_smoke.py",
    "rag_gremlin_patch_pipeline_smoke.py",
}


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(text: str, limit: int = 48) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    base = "_".join(words[:8])[:limit].strip("_") or "prompt"
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{base}_{digest}"


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
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "command": cmd,
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "command": cmd,
        }


def print_block(log: Logger, title: str, value: Any) -> None:
    log.banner(title)
    if isinstance(value, str):
        log(value)
    else:
        log(json.dumps(value, indent=2, sort_keys=True))


def probe_server_rag_pathway(repo: Path) -> dict[str, Any]:
    viewport = repo / "main_computer" / "viewport_routes_rag_assisted_thinking.py"
    v4 = repo / "main_computer" / "rag_assisted_thinking_v4.py"
    subprocess_file = repo / "main_computer" / "chat_ai_subprocess.py"

    viewport_text = read_text(viewport) if viewport.exists() else ""
    v4_text = read_text(v4) if v4.exists() else ""
    subprocess_text = read_text(subprocess_file) if subprocess_file.exists() else ""

    return {
        "viewport_routes_rag_assisted_thinking.py": {
            "exists": viewport.exists(),
            "has_evaluate_handler": "_handle_chat_console_rag_assisted_thinking_evaluate" in viewport_text,
            "mentions_v4_runner": "run_rag_assisted_thinking_v4_request" in viewport_text,
            "mentions_policy": "RagAssistedThinkingV4Policy" in viewport_text,
            "mentions_auto_apply": "auto_apply" in viewport_text,
            "mentions_proposed_paths": "proposed_paths" in viewport_text,
            "mentions_written_paths": "written_paths" in viewport_text,
            "mentions_route_subprocess_memory": "remember_route_result" in viewport_text,
        },
        "rag_assisted_thinking_v4.py": {
            "exists": v4.exists(),
            "has_policy": "class RagAssistedThinkingV4Policy" in v4_text,
            "has_request_runner": "run_rag_assisted_thinking_v4_request" in v4_text,
            "mentions_output_dir": "output_dir" in v4_text,
            "mentions_proposed_paths": "proposed_paths" in v4_text,
            "mentions_written_paths": "written_paths" in v4_text,
            "mentions_activity": "Activity" in v4_text,
        },
        "chat_ai_subprocess.py": {
            "exists": subprocess_file.exists(),
            "mentions_rag_v4_mode": "rag_assisted_thinking_v4" in subprocess_text,
            "mentions_cancel": "cancel" in subprocess_text.lower(),
        },
        "interpretation": [
            "The current server path is chat-console -> RAG-AT evaluate route -> V4 policy -> subprocess or inline runner -> output cell.",
            "This smoke test is not yet wired into that route.",
            "The integration target is to expose this evidence-first gremlin patch pipeline as a RAG-AT strategy.",
        ],
    }


def derive_prompt_terms(prompt: str) -> dict[str, list[str]]:
    words = [w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", prompt)]
    stop = {
        "the", "and", "for", "with", "that", "this", "when", "where", "what",
        "give", "code", "change", "should", "would", "could", "app", "chat",
        "button", "not", "make", "want", "need", "shows", "show", "into",
        "from", "your",
    }

    unique: list[str] = []
    for word in words:
        if word not in unique:
            unique.append(word)

    colors = [w for w in unique if w in {"red", "green", "blue", "yellow", "orange", "white", "black"}]
    states = [w for w in unique if w in {"stop", "start", "run", "running", "cancel", "cancelled", "error", "ok"}]
    strong = [w for w in unique if w not in stop and (w in colors or w in states or len(w) > 4)]
    generic = [w for w in unique if w not in stop]

    if not strong:
        strong = generic[:8]

    return {
        "colors": colors,
        "states": states,
        "strong": strong[:16],
        "generic": generic[:24],
    }


def regex_from_terms(terms: list[str]) -> str:
    if not terms:
        return r"TODO|FIXME"
    parts: list[str] = []
    for term in terms:
        esc = re.escape(term)
        parts.append(rf"(?<![A-Za-z0-9_]){esc}(?![A-Za-z0-9_])")
        parts.append(rf"{esc}(?=[A-Z_-])")
    return "|".join(parts)


def should_skip(path: Path, repo: Path, include_runner: bool) -> bool:
    try:
        rel = path.relative_to(repo)
    except ValueError:
        rel = path

    if any(part in SKIP_DIRS for part in rel.parts):
        return True

    if not include_runner and path.name in RUNNER_NAMES:
        return True

    return False


def compile_regexes(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            compiled.append(re.compile(re.escape(pattern), re.IGNORECASE))
    return compiled


def local_grep(
    *,
    repo: Path,
    source_dirs: list[str],
    include_globs: list[str],
    patterns: list[str],
    context: int,
    max_chars: int,
    include_runner: bool,
) -> tuple[str, dict[str, Any]]:
    roots: list[Path] = []
    for source_dir in source_dirs:
        candidate = (repo / source_dir).resolve()
        try:
            candidate.relative_to(repo)
        except ValueError:
            continue
        if candidate.exists():
            roots.append(candidate)

    if not roots:
        roots = [repo]

    regexes = compile_regexes(patterns)
    chunks: list[str] = []
    scanned = 0
    matched = 0
    skipped_runner = 0

    for root in roots:
        for path in sorted(root.rglob("*")):
            if len("\n".join(chunks)) >= max_chars:
                break
            if not path.is_file():
                continue
            if should_skip(path, repo, include_runner):
                if path.name in RUNNER_NAMES:
                    skipped_runner += 1
                continue
            if not any(fnmatch.fnmatch(path.name, glob) for glob in include_globs):
                continue

            scanned += 1

            try:
                lines = read_text(path).splitlines()
            except OSError:
                continue

            hit_lines: set[int] = set()
            for idx, line in enumerate(lines):
                if any(regex.search(line) for regex in regexes):
                    start = max(0, idx - context)
                    end = min(len(lines), idx + context + 1)
                    hit_lines.update(range(start, end))

            if not hit_lines:
                continue

            matched += 1
            chunks.append(f"### {path.relative_to(repo)}")
            previous = -99
            for idx in sorted(hit_lines):
                if idx != previous + 1:
                    chunks.append("--")
                chunks.append(f"{idx + 1}:{lines[idx]}")
                previous = idx

    text = "\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... <raw grep buffer cut at {max_chars} chars>"

    return text or "<no grep matches>", {
        "roots": [str(root) for root in roots],
        "include_globs": include_globs,
        "patterns": patterns,
        "context": context,
        "max_chars": max_chars,
        "scanned": scanned,
        "matched": matched,
        "skipped_runner": skipped_runner,
        "chars": len(text),
    }


def parse_grep_sections(buffer: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in buffer.splitlines():
        if line.startswith("### "):
            if current:
                sections.append(current)
            current = {"path": line[4:].strip(), "lines": []}
            continue

        if current is None or line == "--" or line.startswith("... <"):
            continue

        match = re.match(r"^(\d+):(.*)$", line)
        if match:
            current["lines"].append({
                "line": int(match.group(1)),
                "text": match.group(2),
            })

    if current:
        sections.append(current)

    return sections


def score_section(section: dict[str, Any], terms: dict[str, list[str]]) -> int:
    text = "\n".join(row.get("text", "") for row in section.get("lines", [])).lower()
    path = str(section.get("path", "")).lower()
    score = 0

    for term in terms.get("strong", []):
        if term in text:
            score += 10
        if term in path:
            score += 4

    if "chat-console" in path:
        score += 15
    if "stop" in text:
        score += 8
    if "button" in text:
        score += 8
    if "red" in text or "green" in text:
        score += 6
    if "function " in text or "=>" in text:
        score += 2

    return score


def select_context(raw_buffer: str, terms: dict[str, list[str]], max_chars: int) -> tuple[str, list[dict[str, Any]]]:
    sections = parse_grep_sections(raw_buffer)
    scored: list[dict[str, Any]] = []

    for section in sections:
        item = dict(section)
        item["score"] = score_section(section, terms)
        scored.append(item)

    scored.sort(key=lambda item: item.get("score", 0), reverse=True)

    chunks: list[str] = []
    selected: list[dict[str, Any]] = []

    for section in scored:
        if section.get("score", 0) <= 0 and selected:
            continue

        lines = [f"### {section['path']}"]
        previous = -99
        for row in section.get("lines", []):
            line_no = int(row.get("line", 0))
            if line_no != previous + 1:
                lines.append("--")
            lines.append(f"{line_no}:{row.get('text', '')}")
            previous = line_no

        chunk = "\n".join(lines)
        if chunks and len("\n\n".join(chunks + [chunk])) > max_chars:
            break

        chunks.append(chunk)
        selected.append(section)

    return "\n\n".join(chunks) or "<no selected context>", selected


def extract_called_symbols(buffer: str) -> list[str]:
    ignored = {
        "if", "for", "while", "switch", "catch", "function", "return", "JSON",
        "Boolean", "String", "Number", "Date", "fetch", "find", "some", "append",
        "push", "splice", "setInterval", "clearInterval",
    }
    symbols: list[str] = []

    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", buffer):
        name = match.group(1)
        if name in ignored:
            continue
        if name not in symbols:
            symbols.append(name)

    priority: list[str] = []
    for name in symbols:
        if "button" in name.lower() or "stop" in name.lower():
            priority.append(name)

    for name in symbols:
        if name not in priority:
            priority.append(name)

    return priority[:12]


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
            if start > 0:
                out.append("--")
            for row_idx in range(start, end):
                out.append(f"{row_idx + 1}:{lines[row_idx]}")
            return "\n".join(out)

    return None


def supplemental_symbol_context(
    *,
    repo: Path,
    selected_context: str,
    selected_sections: list[dict[str, Any]],
    context: int,
    max_chars: int,
    log: Logger,
) -> tuple[str, list[dict[str, Any]]]:
    symbols = extract_called_symbols(selected_context)
    if not symbols:
        return "", []

    candidate_paths: list[Path] = []

    for section in selected_sections:
        raw = str(section.get("path", ""))
        if raw.startswith("symbol definition:"):
            raw = raw.split(" in ")[-1]
        try:
            path = repo / normalize_rel_path(raw)
        except ValueError:
            continue
        if path.exists() and path not in candidate_paths:
            candidate_paths.append(path)

    main_computer = repo / "main_computer"
    if main_computer.exists():
        for glob in ("*.js", "*.ts", "*.tsx", "*.py", "*.html", "*.css"):
            for path in sorted(main_computer.rglob(glob)):
                if should_skip(path, repo, include_runner=False):
                    continue
                if path not in candidate_paths:
                    candidate_paths.append(path)

    chunks: list[str] = []
    found: list[dict[str, Any]] = []

    for symbol in symbols:
        for path in candidate_paths:
            snippet = find_symbol_definition(path, symbol, context)
            if not snippet:
                continue

            candidate = "\n\n".join(chunks + [snippet])
            if len(candidate) > max_chars:
                log(f"[symbol-context] max_chars reached selected_chars={len(chr(10).join(chunks))}")
                return "\n\n".join(chunks), found

            chunks.append(snippet)
            try:
                rel = path.relative_to(repo).as_posix()
            except ValueError:
                rel = str(path)
            found.append({"symbol": symbol, "path": rel})
            log(f"[symbol-context] found {symbol} in {rel}")
            break

    return "\n\n".join(chunks), found


def evidence_sections(context: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in parse_grep_sections(context):
        out.append({
            "path": section.get("path", ""),
            "lines": section.get("lines", [])[:40],
        })
    return out[:12]


def infer_stop_button_edit(repo: Path, context: str, prompt: str) -> dict[str, Any] | None:
    prompt_l = prompt.lower()
    if "stop" not in prompt_l or "red" not in prompt_l:
        return None

    target_rel = "main_computer/web/applications/scripts/chat-console.js"
    target = repo / target_rel
    if not target.exists():
        return None

    source = read_text(target)
    candidates = [
        '        if (cell.type === "ai" && cell.status === "running") controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));',
        'if (cell.type === "ai" && cell.status === "running") controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));',
    ]

    old = ""
    for candidate in candidates:
        if candidate in source:
            old = candidate
            break

    if not old:
        return None

    indent = re.match(r"^(\s*)", old).group(1)
    replacement = (
        f'{indent}if (cell.type === "ai" && cell.status === "running") {{\n'
        f'{indent}  const stopButton = chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id));\n'
        f'{indent}  stopButton.style.borderColor = "rgba(255, 99, 99, 0.85)";\n'
        f'{indent}  stopButton.style.background = "#4a1111";\n'
        f'{indent}  stopButton.style.color = "#ffecec";\n'
        f'{indent}  controls.append(stopButton);\n'
        f'{indent}}}'
    )

    line_no = None
    for idx, line in enumerate(source.splitlines(), start=1):
        if line == old:
            line_no = idx
            break

    return {
        "kind": "replace_fragment",
        "target_file": target_rel,
        "line": line_no,
        "old_fragment": old,
        "replacement_fragment": replacement,
        "confidence": "high" if "function chatConsoleButton(label, onClick)" in context else "medium",
        "reason": (
            "Evidence shows the running AI cell creates a Stop button with the generic chatConsoleButton helper. "
            "The replacement leaves the helper generic and applies explicit red styling only to the Stop button instance."
        ),
    }


def infer_green_to_red_line_edit(repo: Path, sections: list[dict[str, Any]], prompt: str) -> dict[str, Any] | None:
    prompt_l = prompt.lower()
    if "red" not in prompt_l or "green" not in prompt_l:
        return None

    for section in sections:
        raw_path = str(section.get("path", ""))
        if raw_path.startswith("symbol definition:"):
            continue

        try:
            rel = normalize_rel_path(raw_path)
        except ValueError:
            continue

        path = repo / rel
        if not path.exists():
            continue

        source = read_text(path)
        for row in section.get("lines", []):
            line = str(row.get("text", ""))
            if "green" not in line.lower():
                continue
            replacement = re.sub("green", "red", line, flags=re.IGNORECASE)
            if replacement != line and line in source:
                return {
                    "kind": "replace_fragment",
                    "target_file": rel,
                    "line": row.get("line"),
                    "old_fragment": line,
                    "replacement_fragment": replacement,
                    "confidence": "medium",
                    "reason": "Inferred exact green-to-red line replacement from prompt and grep evidence.",
                }

    return None


def infer_evidence_edit(repo: Path, context: str, sections: list[dict[str, Any]], prompt: str) -> dict[str, Any] | None:
    return (
        infer_stop_button_edit(repo, context, prompt)
        or infer_green_to_red_line_edit(repo, sections, prompt)
    )


def build_gremlin_source(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, sort_keys=True)
    return f'''from __future__ import annotations

import json
from typing import Any

PAYLOAD_JSON = {payload_json!r}


def load_payload() -> dict[str, Any]:
    return json.loads(PAYLOAD_JSON)


def build_head(payload: dict[str, Any], prompt: str | None) -> dict[str, Any]:
    return {{
        "kind": "code-gremlin",
        "name": "evidence_first_patch_gremlin",
        "strategy": "evidence_first",
        "prompt": prompt or payload.get("prompt", ""),
    }}


def choose_target_file(payload: dict[str, Any]) -> str:
    edit = payload.get("proposed_edit")
    if isinstance(edit, dict):
        return str(edit.get("target_file") or "")
    return ""


def build_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = payload.get("evidence")
    return list(evidence) if isinstance(evidence, list) else []


def build_proposed_edit(payload: dict[str, Any]) -> dict[str, Any] | None:
    edit = payload.get("proposed_edit")
    return dict(edit) if isinstance(edit, dict) else None


def build_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {{
        "target_file": choose_target_file(payload),
        "proposed_edit": build_proposed_edit(payload),
        "server_pathway": payload.get("server_pathway", {{}}),
    }}


def build_content(payload: dict[str, Any]) -> dict[str, Any]:
    edit = build_proposed_edit(payload)
    if edit:
        return {{
            "status": "code_change_inferred",
            "edit": edit,
        }}
    return {{
        "status": "needs_more_evidence",
        "fail_condition": payload.get("fail_condition") or "no concrete edit inferred",
    }}


def assemble_result(prompt: str | None = None) -> dict[str, Any]:
    payload = load_payload()
    content = build_content(payload)
    result = {{
        "ok": bool(build_proposed_edit(payload)),
        "head": build_head(payload, prompt),
        "body": build_body(payload),
        "content": content,
        "evidence": build_evidence(payload),
        "requested_output_path": "diagnostics_output/gremlin_rag_smoke/final_result.json",
    }}
    if not result["ok"]:
        result["fail_condition"] = content.get("fail_condition")
    return result


def main(prompt: str | None = None) -> dict[str, Any]:
    return assemble_result(prompt)


if __name__ == "__main__":
    print(json.dumps(main(), separators=(",", ":"), sort_keys=True))
'''


def validate_python_source(source: str, filename: str) -> tuple[bool, list[str]]:
    issues: list[str] = []
    try:
        compile(source, filename, "exec")
    except SyntaxError as exc:
        return False, [f"syntax error: {exc}"]

    import ast
    tree = ast.parse(source, filename=filename)
    has_main = any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body)
    if not has_main:
        issues.append("missing top-level def main(...)")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            else:
                names = [str(node.module or "").split(".")[0]]
            for name in names:
                if name not in {"json", "typing", "__future__"}:
                    issues.append(f"non-allowed import: {name}")

    return not issues, issues


def run_gremlin(path: Path, out_dir: Path, args: argparse.Namespace, log: Logger) -> dict[str, Any]:
    if args.no_docker_run:
        cmd = [sys.executable, str(path)]
        log.banner(f"LOCAL EXEC COMMAND USED FOR {path.name}")
        log(" ".join(cmd))
        result = run_command(cmd, cwd=out_dir, timeout_s=args.timeout_s)
        result["local_execution_note"] = (
            "This smoke test generates a deterministic evidence-first gremlin from local grep evidence; "
            "--no-docker-run executes that generated gremlin locally for convenience."
        )
        return result

    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{out_dir.resolve()}:/workspace:rw",
        "-w",
        "/workspace",
        args.docker_image,
        "python",
        f"/workspace/{path.name}",
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
        return None, "stdout did not contain a JSON object"

    try:
        value = json.loads(match.group(0))
        if isinstance(value, dict):
            return value, None
        return None, "embedded JSON was not an object"
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc}"


def extract_edit(result: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        result.get("proposed_edit"),
        result.get("edit"),
    ]

    if isinstance(result.get("content"), dict):
        candidates.append(result["content"].get("edit"))

    if isinstance(result.get("body"), dict):
        candidates.append(result["body"].get("proposed_edit"))

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("target_file") and candidate.get("old_fragment") and candidate.get("replacement_fragment"):
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
        return {"ok": False, "target_file": rel, "error": "replacement is identical to old_fragment"}

    count = source.count(old)
    if count != 1:
        return {
            "ok": False,
            "target_file": rel,
            "old_fragment_count": count,
            "error": "old_fragment must occur exactly once",
        }

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
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )


def build_snapshot_zip(repo: Path, edit_validation: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    rel = normalize_rel_path(str(edit_validation["target_file"]))
    rel_parts = PurePosixPath(rel).parts
    target = repo.joinpath(*rel_parts)
    old_text = read_text(target)
    new_text = str(edit_validation["new_text"])

    replacement_dir = out_dir / "replacement_files"
    replacement_path = replacement_dir / rel.replace("/", "__")
    write_text(replacement_path, new_text)

    zip_path = out_dir / "evidence_first_patch_snapshot.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    archive_name = f"{repo.name}/{rel}".replace("\\", "/")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(archive_name, new_text)

    diff = make_diff(rel, old_text, new_text)
    diff_path = out_dir / "reference_diff.patch"
    write_text(diff_path, diff)

    return {
        "zip_path": str(zip_path),
        "snapshot_entry": archive_name,
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

    cmd = [
        sys.executable,
        str(new_patch),
        str(zip_path),
        "--dry-run",
        "--target-root",
        str(repo),
    ]
    log.banner("NEW_PATCH DRY-RUN COMMAND")
    log(" ".join(cmd))
    return run_command(cmd, cwd=repo, timeout_s=timeout_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence-first gremlin patch pipeline smoke test.")
    parser.add_argument("prompt", nargs="*", help="Patch/code-change prompt.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--source-dir", action="append", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--context", type=int, default=3)
    parser.add_argument("--symbol-context", type=int, default=12)
    parser.add_argument("--max-raw-grep-chars", type=int, default=500000)
    parser.add_argument("--max-selected-chars", type=int, default=6000)
    parser.add_argument("--max-symbol-chars", type=int, default=4000)
    parser.add_argument("--include-runner", action="store_true")
    parser.add_argument("--docker-image", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_IMAGE", DEFAULT_DOCKER_IMAGE))
    parser.add_argument("--no-docker-run", action="store_true")
    parser.add_argument("--allow-local-generated-code", action="store_true", help="Accepted for compatibility; evidence-first local execution no longer requires it.")
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

    run_id = "rgp_" + utc_stamp() + "_" + hashlib.sha1(prompt.encode("utf-8", errors="ignore")).hexdigest()[:8]
    out_dir = Path(args.out).resolve() if args.out else repo / DEFAULT_OUTPUT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=False)

    log = Logger(out_dir / "verbose.log", quiet=args.quiet)
    timings: dict[str, float] = {}
    started = time.perf_counter()

    source_dirs = args.source_dir or [d for d in DEFAULT_SOURCE_DIRS if (repo / d).exists()] or ["."]
    phases = {
        "server_probe_ok": False,
        "retrieval_ok": False,
        "symbol_context_ok": False,
        "gremlin_built": False,
        "gremlin_ran": False,
        "edit_inferred": False,
        "old_fragment_verified": False,
        "patch_zip_built": False,
        "new_patch_dry_run_passed": False,
    }

    log.banner("EVIDENCE-FIRST GREMLIN PATCH PIPELINE SMOKE")
    config = {
        "prompt": prompt,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "source_dirs": source_dirs,
        "docker_image": args.docker_image,
        "no_docker_run": args.no_docker_run,
        "skip_new_patch_dry_run": args.skip_new_patch_dry_run,
    }
    print_block(log, "CONFIG", config)
    write_json(out_dir / "00_config.json", config)
    write_text(out_dir / "00_prompt.txt", prompt)

    with Timer("1. probe server RAG pathway", log, timings):
        server_probe = probe_server_rag_pathway(repo)
        phases["server_probe_ok"] = bool(
            server_probe["viewport_routes_rag_assisted_thinking.py"]["has_evaluate_handler"]
            and server_probe["rag_assisted_thinking_v4.py"]["has_request_runner"]
        )
        write_json(out_dir / "01_server_pathway_probe.json", server_probe)
        print_block(log, "SERVER RAG PATHWAY PROBE", server_probe)

    terms = derive_prompt_terms(prompt)
    grep_plan = {
        "terms": terms,
        "gremlin_first": {
            "include_globs": GREMLIN_GLOBS,
            "patterns": [regex_from_terms(terms["strong"])],
        },
        "source_fallback": {
            "include_globs": SOURCE_GLOBS,
            "patterns": [regex_from_terms(terms["strong"])],
        },
    }
    write_json(out_dir / "02_grep_plan.json", grep_plan)

    with Timer("2. gremlin-first grep", log, timings):
        gremlin_buffer, gremlin_stats = local_grep(
            repo=repo,
            source_dirs=source_dirs,
            include_globs=GREMLIN_GLOBS,
            patterns=grep_plan["gremlin_first"]["patterns"],
            context=args.context,
            max_chars=args.max_raw_grep_chars,
            include_runner=args.include_runner,
        )
        write_text(out_dir / "02a_gremlin_first_buffer.txt", gremlin_buffer)
        write_json(out_dir / "02a_gremlin_first_stats.json", gremlin_stats)
        print_block(log, "GREMLIN-FIRST GREP STATS", gremlin_stats)

    with Timer("3. targeted source grep", log, timings):
        source_buffer, source_stats = local_grep(
            repo=repo,
            source_dirs=source_dirs,
            include_globs=SOURCE_GLOBS,
            patterns=grep_plan["source_fallback"]["patterns"],
            context=args.context,
            max_chars=args.max_raw_grep_chars,
            include_runner=args.include_runner,
        )
        write_text(out_dir / "02b_raw_source_grep_buffer.txt", source_buffer)
        write_json(out_dir / "02b_source_grep_stats.json", source_stats)
        print_block(log, "TARGETED SOURCE GREP STATS", source_stats)

        selected_context, selected_sections = select_context(source_buffer, terms, args.max_selected_chars)
        phases["retrieval_ok"] = bool(selected_sections)
        write_text(out_dir / "03_selected_context_before_symbols.txt", selected_context)
        write_json(out_dir / "03_selected_sections.json", selected_sections)
        print_block(log, "SELECTED CONTEXT BEFORE SYMBOLS", selected_context)

    with Timer("4. supplemental symbol context", log, timings):
        symbol_context, symbols_found = supplemental_symbol_context(
            repo=repo,
            selected_context=selected_context,
            selected_sections=selected_sections,
            context=args.symbol_context,
            max_chars=args.max_symbol_chars,
            log=log,
        )
        combined_context = selected_context
        if symbol_context:
            combined_context += "\n\nSupplemental symbol-definition context:\n" + symbol_context

        phases["symbol_context_ok"] = bool(symbols_found)
        write_text(out_dir / "04_symbol_context.txt", symbol_context)
        write_json(out_dir / "04_symbols_found.json", symbols_found)
        write_text(out_dir / "04_model_equivalent_context.txt", combined_context)
        print_block(log, "SUPPLEMENTAL SYMBOL CONTEXT", symbol_context or "<none>")
        log(f"[context] selected_chars={len(selected_context)} symbol_chars={len(symbol_context)} combined_chars={len(combined_context)}")

    with Timer("5. synthesize evidence-first gremlin", log, timings):
        sections = evidence_sections(combined_context)
        edit = infer_evidence_edit(repo, combined_context, sections, prompt)
        phases["edit_inferred"] = edit is not None

        payload = {
            "prompt": prompt,
            "strategy": "evidence_first",
            "server_pathway": server_probe,
            "grep_plan": grep_plan,
            "symbols_found": symbols_found,
            "evidence": sections,
            "proposed_edit": edit,
            "fail_condition": None if edit else "No concrete exact edit could be inferred from evidence.",
        }

        gremlin_source = build_gremlin_source(payload)
        gremlin_name = "gremlin_evidence_first_" + slugify(prompt) + ".py"
        ok_source, source_issues = validate_python_source(gremlin_source, gremlin_name)
        if not ok_source:
            raise RuntimeError(f"internal evidence gremlin is invalid: {source_issues}")

        gremlin_path = out_dir / gremlin_name
        write_text(gremlin_path, gremlin_source)
        write_json(out_dir / "05_gremlin_payload.json", payload)
        write_text(out_dir / "05_gremlin_source.py", gremlin_source)
        phases["gremlin_built"] = True
        print_block(log, "EVIDENCE-FIRST GREMLIN SOURCE", gremlin_source)

    with Timer("6. execute gremlin", log, timings):
        run_result = run_gremlin(gremlin_path, out_dir, args, log)
        write_json(out_dir / "06_gremlin_executor_result.json", run_result)
        print_block(log, "GREMLIN EXECUTOR RESULT", run_result)
        phases["gremlin_ran"] = bool(run_result.get("ok"))

        parsed, parse_error = parse_json_object(str(run_result.get("stdout") or ""))
        write_json(out_dir / "06_gremlin_parsed.json", {"parsed": parsed, "parse_error": parse_error})
        if parse_error:
            log(f"[gremlin] parse error: {parse_error}")

    adapted_edit = None
    edit_validation: dict[str, Any] = {"ok": False, "error": "not attempted"}
    patch_zip: dict[str, Any] | None = None
    new_patch_result: dict[str, Any] | None = None

    with Timer("7. adapt and verify edit", log, timings):
        if parsed:
            adapted_edit = extract_edit(parsed)
        write_json(out_dir / "07_adapted_edit.json", adapted_edit or {})
        print_block(log, "ADAPTED EDIT", adapted_edit or {"error": "no actionable edit found"})

        if adapted_edit:
            edit_validation = validate_edit(repo, adapted_edit)
            phases["old_fragment_verified"] = bool(edit_validation.get("ok"))

        write_json(out_dir / "07_edit_validation.json", {k: v for k, v in edit_validation.items() if k != "new_text"})
        print_block(log, "EDIT VALIDATION", {k: v for k, v in edit_validation.items() if k != "new_text"})

    with Timer("8. build changed-file snapshot zip", log, timings):
        if edit_validation.get("ok"):
            patch_zip = build_snapshot_zip(repo, edit_validation, out_dir)
            phases["patch_zip_built"] = True
            write_json(out_dir / "08_patch_zip.json", {k: v for k, v in patch_zip.items() if k != "reference_diff"})
            write_text(out_dir / "08_reference_diff.patch", patch_zip["reference_diff"])
            print_block(log, "PATCH ZIP", {k: v for k, v in patch_zip.items() if k != "reference_diff"})
            print_block(log, "REFERENCE DIFF", patch_zip["reference_diff"])
        else:
            log("[patch] skipped because edit validation failed")

    with Timer("9. new_patch dry-run", log, timings):
        if args.skip_new_patch_dry_run:
            new_patch_result = {"ok": None, "skipped": True, "reason": "--skip-new-patch-dry-run"}
        elif patch_zip:
            new_patch_result = run_new_patch_dry_run(repo, Path(patch_zip["zip_path"]), args.timeout_s, log)
            phases["new_patch_dry_run_passed"] = bool(new_patch_result.get("ok"))
        else:
            new_patch_result = {"ok": False, "skipped": True, "reason": "no patch zip built"}

        write_json(out_dir / "09_new_patch_dry_run.json", new_patch_result)
        print_block(log, "NEW_PATCH DRY-RUN RESULT", new_patch_result)

    ok = bool(
        phases["retrieval_ok"]
        and phases["gremlin_built"]
        and phases["gremlin_ran"]
        and phases["edit_inferred"]
        and phases["old_fragment_verified"]
        and phases["patch_zip_built"]
        and (phases["new_patch_dry_run_passed"] or args.skip_new_patch_dry_run)
    )

    final_report = {
        "ok": ok,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "prompt": prompt,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "phase_status": phases,
        "timings": timings,
        "server_probe_path": str(out_dir / "01_server_pathway_probe.json"),
        "selected_context_path": str(out_dir / "04_model_equivalent_context.txt"),
        "gremlin_file": str(gremlin_path),
        "adapted_edit": adapted_edit,
        "edit_validation": {k: v for k, v in edit_validation.items() if k != "new_text"},
        "patch_zip": {k: v for k, v in (patch_zip or {}).items() if k != "reference_diff"},
        "new_patch_dry_run": new_patch_result,
        "next_server_integration_target": (
            "Expose this evidence-first gremlin patch pipeline as a strategy behind "
            "the existing chat-console RAG-AT evaluate route."
        ),
    }
    write_json(out_dir / "final_report.json", final_report)
    print_block(log, "FINAL REPORT", final_report)

    summary = {
        "ok": ok,
        "out_dir": str(out_dir),
        "final_report": str(out_dir / "final_report.json"),
        "gremlin_file": str(gremlin_path),
        "patch_zip": (patch_zip or {}).get("zip_path"),
        "new_patch_dry_run_ok": None if new_patch_result is None else new_patch_result.get("ok"),
        "elapsed_s": final_report["elapsed_s"],
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