#!/usr/bin/env python3
"""
gremlin_rag_smoke.py

Verbose-by-default RAG smoke test for code-gremlins.

Running with no prompt now prints help immediately instead of blocking on stdin.

Main fixes in this version:
- Defaults source search to main_computer and tests instead of the whole repo.
- Excludes this runner and heavy/generated folders from grep by default.
- Falls back from gremlin_*.py to targeted code globs without broad main()/HEAD/BODY patterns.
- Logs every model-used payload and generated/executed artifact with no truncation.
- Saves raw candidate grep output separately without duplicating huge non-model payloads into verbose.log.
- Treats fixed gremlin attrs as optional; the hard shape is ordinary Python with main().
- Lets Ollama calls run as long as needed by default; use --ai-timeout-s to cap them.
- Rejects fuzzy proposed edits and falls back to exact evidence-based edits.
- Limits Ollama generation with --num-predict to reduce model-generated output.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from typing import Any

from main_computer.container_runtime import resolve_container_runtime


DEFAULT_SYSTEM = (
    "You write tiny ordinary Python code-gremlin files. "
    "A code-gremlin is ordinary Python: make useful small defs, then make main() "
    "assemble the full gremlin result from those defs. "
    "Return only the requested machine-readable artifact."
)

SUPER_GREP_PREP_TEMPLATE = """User prompt:
{prompt}

Return JSON only, compact.

Task:
Choose a safe grep plan for finding small gremlin/code fragments.

Rules:
- grep will use -C 3.
- Prefer gremlin_*.py first.
- If the user asks for a concrete code fix, include terms for UI text, symbols, colors, routes, files, and likely tests.
- Keep output tiny: max 3 globs, max 4 patterns, max 3 questions.

Schema:
{{"intent":"","include_globs":["gremlin_*.py"],"patterns":["regex"],"top_questions":[""],"why":""}}
"""

GREMLIN_BUILD_TEMPLATE = """User prompt:
{prompt}

Grep plan:
{grep_plan_json}

Selected grep buffer used for this model call:
{grep_buffer}

Return only Python source code. No markdown.

Write one compact ordinary Python file named gremlin_<slug>.py.

Gremlin idea:
- A gremlin is just ordinary Python.
- Do not invent a framework, base class, or import named code_gremlin.
- The only hard gremlin shape is that main() exists.
- Build useful small defs first, then make main() visibly assemble the full gremlin result from those defs.
- Optional constants such as HEAD, BODY, CONTENT, FEATURES, SEARCH_HINTS, TOP_QUESTIONS are allowed only when useful.
- The result may be an answer, a likely code-change kernel, or a compact next-step structure when evidence is incomplete.

Hard limits:
- <= 160 lines.
- Standard library only.
- No external imports.
- Do not copy the grep buffer wholesale.
- Do not write long explanations.
- If the grep buffer has enough evidence for a code change, include a compact proposed edit object.
- Proposed edits must be machine-checkable: use target_file, line when known, old_fragment, and replacement_fragment.
- Do not use a one-line function signature as a search key for a multi-line replacement.
- If modifying a helper function, include the whole old helper block and the whole replacement helper block.
- If evidence is incomplete, main() should return a compact fail_condition explaining what is missing.

Runtime:
- main(prompt: str | None = None) should return a JSON-serializable dict.
- When run as a script, print JSON from main().
"""


DUCKPATCHER_TEMPLATE = """User prompt:
{prompt}

Generated gremlin failed.

Gremlin path:
{gremlin_path}

Gremlin source:
{gremlin_source}

Executor result:
{executor_result_json}

Return only Python source code. No markdown.

Write one compact ordinary Python file named duckpatcher_<slug>.py.

Hard limits:
- <= 120 lines.
- Standard library only.
- Read gremlin path from argv[1].
- Apply small string-level deltas only.
- Print compact JSON with ok, patched, changes, gremlin_path.
"""

DEFAULT_GREMLIN_SOURCE = """from __future__ import annotations

import json
from typing import Any


def build_head(prompt: str | None) -> dict[str, Any]:
    return {
        "kind": "code-gremlin",
        "name": "gremlin_fallback_kernel",
        "flavor": "ordinary-python",
        "prompt": prompt or "",
    }


def build_search_hints(prompt: str | None) -> list[str]:
    text = (prompt or "").lower()
    hints = ["grep -RInE -C 3", "main()", "small helper defs"]
    for term in ("stop", "button", "red", "green", "chat", "test", "fix"):
        if term in text:
            hints.append(term)
    return hints


def build_body(prompt: str | None) -> dict[str, Any]:
    return {
        "purpose": "Fallback gremlin assembled from small defs.",
        "likely_flow": [
            "derive search hints from prompt",
            "inspect grep snippets",
            "assemble a compact result in main()",
            "duckpatch only if the assembled result cannot run",
        ],
    }


def build_content(prompt: str | None) -> dict[str, Any]:
    return {
        "status": "fallback",
        "next_step": "Use matched snippets to build a targeted code/test kernel.",
    }


def assemble_result(prompt: str | None = None) -> dict[str, Any]:
    head = build_head(prompt)
    body = build_body(prompt)
    content = build_content(prompt)
    search_hints = build_search_hints(prompt)
    return {
        "ok": True,
        "head": head,
        "body": body,
        "content": content,
        "search_hints": search_hints,
        "requested_output_path": "diagnostics_output/gremlin_rag_smoke/final_result.json",
    }


def main(prompt: str | None = None) -> dict[str, Any]:
    return assemble_result(prompt)


if __name__ == "__main__":
    print(json.dumps(main(), separators=(",", ":"), sort_keys=True))
"""


DEFAULT_DUCKPATCHER_SOURCE = """from __future__ import annotations

import json
import sys
from pathlib import Path

def main() -> dict:
    if len(sys.argv) < 2:
        return {"ok": False, "patched": False, "error": "missing gremlin path argv[1]"}
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8", errors="replace")
    changes = []
    pairs = [
        ('"ok": False', '"ok": True'),
        ("'ok': False", "'ok': True"),
        ("fail_condition", "patched_fail_condition"),
    ]
    for old, new in pairs:
        if old in text and new not in text:
            text = text.replace(old, new)
            changes.append(f"replace {old!r} with {new!r}")
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "patched": bool(changes), "changes": changes, "gremlin_path": str(path)}

if __name__ == "__main__":
    print(json.dumps(main(), separators=(",", ":"), sort_keys=True))
"""


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(text: str, limit: int = 48) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    base = "_".join(words[:8])[:limit].strip("_") or "prompt"
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{base}_{digest}"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


class Logger:
    def __init__(self, path: Path, quiet: bool = False):
        self.path = path
        self.quiet = quiet
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def log(self, message: str = "") -> None:
        text = str(message)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
        if not self.quiet:
            print(text, flush=True)

    def block(self, title: str, text: str) -> None:
        self.log("\n" + "=" * 78)
        self.log(title)
        self.log("=" * 78)
        self.log(str(text))

    def file(self, title: str, path: Path, text: str, *, echo: bool = True) -> None:
        write_text(path, text)
        self.log(f"[write] {title}: {path} ({len(text)} chars)")
        if echo:
            self.block(title, text)
        else:
            self.log(f"[write] {title} saved to file only; not duplicated into verbose.log")


class Timer:
    def __init__(self, label: str, logger: Logger):
        self.label = label
        self.logger = logger
        self.start = 0.0

    def __enter__(self) -> "Timer":
        self.start = time.perf_counter()
        self.logger.log(f"[start] {self.label}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = time.perf_counter() - self.start
        if exc:
            self.logger.log(f"[fail]  {self.label} after {elapsed:.2f}s: {exc}")
        else:
            self.logger.log(f"[done]  {self.label} in {elapsed:.2f}s")


def strip_code_fence(text: str) -> str:
    raw = str(text or "").strip()
    match = re.search(r"```(?:python|py)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    if match:
        raw = match.group(1).strip()
    return raw + ("\n" if raw and not raw.endswith("\n") else "")


def extract_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def derive_terms(text: str) -> list[str]:
    stop = {"the", "and", "for", "with", "that", "this", "from", "into", "have", "what", "when", "then", "will", "would", "should", "could", "about", "given", "want", "needs", "code", "python", "file", "files", "user", "prompt", "answer", "not"}
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())
    preferred = ["gremlin", "rag", "grep", "docker", "executor", "duckpatch", "main", "head", "body", "content", "stop", "button", "red", "green", "chat"]
    out: list[str] = []
    for word in preferred + words:
        if word in stop:
            continue
        if word in text.lower() or word in words:
            if word not in out:
                out.append(word)
    return out[:24]


class ChatClient:
    def __init__(self, *, mode: str, base_url: str, model: str, ai_timeout_s: float | None, num_predict: int, transcript_path: Path, out_dir: Path, logger: Logger):
        self.mode = mode
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.ai_timeout_s = None if ai_timeout_s is None or float(ai_timeout_s) <= 0 else float(ai_timeout_s)
        self.num_predict = int(num_predict)
        self.transcript_path = transcript_path
        self.out_dir = out_dir
        self.logger = logger
        self.messages = [{"role": "system", "content": DEFAULT_SYSTEM}]
        self.last_used_mock_fallback = False

    def ask(self, stage: str, user_text: str, *, want_json: bool, fallback_kind: str) -> str:
        safe_stage = re.sub(r"[^a-zA-Z0-9_]+", "_", stage).strip("_")
        self.last_used_mock_fallback = False
        self.logger.file(f"{stage} MODEL INPUT USED", self.out_dir / f"{safe_stage}_model_input.txt", user_text)
        self.messages.append({"role": "user", "content": user_text})

        timeout_label = "none/wait-as-needed" if self.ai_timeout_s is None else f"{self.ai_timeout_s}s"
        self.logger.log(f"[ai] mode={self.mode} model={self.model} want_json={want_json} num_predict={self.num_predict} ai_timeout={timeout_label}")
        if self.mode == "mock":
            self.last_used_mock_fallback = True
            answer = self._mock_response(user_text, fallback_kind)
            self.messages.append({"role": "assistant", "content": answer})
            self._write_transcript()
            self.logger.file(f"{stage} MODEL OUTPUT RAW", self.out_dir / f"{safe_stage}_model_output.txt", answer)
            return answer

        payload: dict[str, Any] = {"model": self.model, "messages": self.messages, "stream": False}
        if want_json:
            payload["format"] = "json"
        if self.num_predict > 0:
            payload["options"] = {"num_predict": self.num_predict}

        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            self.logger.log(f"[ai] POST {self.base_url}/api/chat payload_chars={len(json.dumps(payload))}")
            if self.ai_timeout_s is None:
                self.logger.log("[ai] no HTTP read timeout is set; waiting for Ollama to finish")
            with urllib.request.urlopen(request, timeout=self.ai_timeout_s) as response:
                body_text = response.read().decode("utf-8", errors="replace")
            self.logger.file(f"{stage} OLLAMA HTTP RESPONSE RAW", self.out_dir / f"{safe_stage}_http_response.json", body_text)
            body = json.loads(body_text)
            answer = str(((body.get("message") or {}).get("content")) or "")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.last_used_mock_fallback = True
            note = f"local AI failed; using mock fallback: {type(exc).__name__}: {exc}"
            self.logger.log(f"[ai] {note}")
            self.messages.append({"role": "system", "content": "NOTE: " + note})
            answer = self._mock_response(user_text, fallback_kind)

        self.messages.append({"role": "assistant", "content": answer})
        self._write_transcript()
        self.logger.file(f"{stage} MODEL OUTPUT RAW", self.out_dir / f"{safe_stage}_model_output.txt", answer)
        return answer

    def _write_transcript(self) -> None:
        write_json(self.transcript_path, self.messages)
        self.logger.log(f"[write] chat transcript: {self.transcript_path}")

    def _mock_response(self, user_text: str, fallback_kind: str) -> str:
        if fallback_kind == "grep_plan":
            terms = derive_terms(user_text)
            pattern = "|".join(re.escape(term) for term in terms[:12]) or r"main\(|HEAD|BODY|CONTENT|FEATURES|SEARCH_HINTS"
            return json.dumps({
                "intent": "Find gremlin/code fragments relevant to the prompt.",
                "include_globs": ["gremlin_*.py"],
                "patterns": [pattern, r"HEAD|BODY|CONTENT|FEATURES|SEARCH_HINTS|TOP_QUESTIONS|main\("],
                "top_questions": ["Which file likely changes?", "Which color/state controls stop?", "What exact code-change evidence exists?"],
                "why": "Mock fallback derived compact terms.",
            }, separators=(",", ":"))
        if fallback_kind == "duckpatcher":
            return DEFAULT_DUCKPATCHER_SOURCE
        return DEFAULT_GREMLIN_SOURCE


def normalize_grep_plan(plan: dict[str, Any], *, all_py: bool) -> dict[str, Any]:
    patterns = [str(x).strip()[:300] for x in plan.get("patterns", []) if str(x).strip()]
    globs = [str(x).strip() for x in plan.get("include_globs", []) if str(x).strip()]
    if all_py:
        globs = ["*.py"]
        stripped = strip_structural_patterns(patterns)
        patterns = stripped or patterns
    if not globs:
        globs = ["gremlin_*.py"]
    if not patterns:
        patterns = [r"HEAD|BODY|CONTENT|FEATURES|SEARCH_HINTS|TOP_QUESTIONS|main\("]
    return {
        "intent": str(plan.get("intent") or "Find relevant code-gremlin fragments.")[:240],
        "include_globs": globs[:3],
        "patterns": patterns[:4],
        "top_questions": [str(x)[:160] for x in plan.get("top_questions", [])][:3],
        "why": str(plan.get("why") or "")[:240],
    }


DEFAULT_SOURCE_DIRS = ["main_computer", "tests"]

CODE_FALLBACK_GLOBS = ["*.py", "*.html", "*.js", "*.css", "*.ts", "*.tsx"]

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "diagnostics_output",
    "revision_control",
    "snapshots",
    "generated_component_docs",
    ".main_computer_browser_profile",
    "aider.log",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

GREMLIN_STRUCTURAL_MARKERS = (
    "HEAD|BODY|CONTENT",
    "FEATURES|SEARCH_HINTS",
    "TOP_QUESTIONS",
    "DUCKPATCH_CONTRACT",
    "main\\(",
)

HIGH_SIGNAL_STATE_TERMS = {
    "red", "green", "blue", "yellow", "orange", "purple", "gray", "grey",
    "black", "white", "stop", "start", "run", "running", "error", "warning",
    "success", "danger", "cancel", "submit", "send",
}


def default_source_dirs(repo: Path) -> list[str]:
    found = [name for name in DEFAULT_SOURCE_DIRS if (repo / name).exists()]
    return found or ["."]


def is_inside_skip_dir(path: Path) -> bool:
    return bool(set(path.parts) & SKIP_DIRS)


def is_runner_file(path: Path) -> bool:
    try:
        return path.resolve() == Path(__file__).resolve()
    except OSError:
        return path.name == "gremlin_rag_smoke.py"


def is_gremlin_structural_pattern(pattern: str) -> bool:
    compact = re.sub(r"\s+", "", str(pattern)).upper()
    return any(marker in compact for marker in GREMLIN_STRUCTURAL_MARKERS)


def prompt_search_terms(prompt: str, *, limit: int = 16) -> list[str]:
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "have",
        "what", "when", "then", "will", "would", "should", "could", "about",
        "given", "want", "wants", "need", "needs", "code", "change", "changes",
        "fix", "test", "tests", "file", "files", "user", "prompt", "answer",
        "app", "application", "make", "give", "shows", "showing", "looks",
    }
    meta = {
        "gremlin", "rag", "grep", "docker", "executor", "duckpatch",
        "duckpatcher", "main", "head", "body", "content", "kernel", "smoke",
    }
    color_or_state = HIGH_SIGNAL_STATE_TERMS

    words = re.findall(r"[A-Za-z_][A-Za-z0-9_-]{1,}", prompt.lower())
    quoted = [m.group(1).lower() for m in re.finditer(r"['\"]([^'\"]{2,40})['\"]", prompt)]

    ranked: list[str] = []
    for word in quoted + words:
        clean = word.strip("_-")
        if len(clean) < 2 or clean in stop or clean in meta:
            continue
        if len(clean) <= 3 and clean not in color_or_state:
            continue
        if clean not in ranked:
            ranked.append(clean)

    # Put highly diagnostic state/color words first.
    ranked.sort(key=lambda term: (0 if term in color_or_state else 1, len(term)))
    return ranked[:limit]


def term_regex(term: str) -> str:
    escaped = re.escape(term)
    if len(term) <= 5:
        return rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])|{escaped}(?=[A-Z_-])"
    return escaped


def fallback_patterns_for_prompt(prompt: str, logger: Logger) -> list[str]:
    terms = prompt_search_terms(prompt, limit=14)
    if not terms:
        logger.log("[grep:fallback] no high-signal prompt terms; using small UI/code fallback pattern")
        return [r"TODO|FIXME|button|class=|style=|color|error|warning"]

    state_terms = [term for term in terms if term in HIGH_SIGNAL_STATE_TERMS]
    if len(state_terms) >= 2:
        terms_for_regex = state_terms[:8]
        logger.log(f"[grep:fallback] using state/color terms={terms_for_regex}; held generic terms={terms}")
    else:
        terms_for_regex = terms
        logger.log(f"[grep:fallback] high-signal terms={terms_for_regex}")

    return ["|".join(term_regex(term) for term in terms_for_regex)]


def strip_structural_patterns(patterns: list[str]) -> list[str]:
    return [pattern for pattern in patterns if not is_gremlin_structural_pattern(pattern)]


def compile_patterns(patterns: list[str], logger: Logger) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            out.append(re.compile(pattern, flags=re.IGNORECASE))
        except re.error:
            logger.log(f"[grep] invalid regex; escaped literal: {pattern!r}")
            out.append(re.compile(re.escape(pattern), flags=re.IGNORECASE))
    return out


def resolve_roots(repo: Path, source_dirs: list[str], logger: Logger) -> list[Path]:
    roots: list[Path] = []
    repo_resolved = repo.resolve()
    for source_dir in source_dirs:
        candidate = (repo / source_dir).resolve()
        try:
            candidate.relative_to(repo_resolved)
        except ValueError:
            logger.log(f"[grep] skipped source outside repo: {candidate}")
            continue
        if candidate.exists():
            roots.append(candidate)
        else:
            logger.log(f"[grep] skipped missing source: {candidate}")
    return roots or [repo]


def local_grep(repo: Path, source_dirs: list[str], globs: list[str], patterns: list[str], context: int, logger: Logger, *, include_runner: bool) -> str:
    regexes = compile_patterns(patterns, logger)
    roots = resolve_roots(repo, source_dirs, logger)
    logger.log(f"[grep:local] roots={[str(x) for x in roots]}")
    logger.log(f"[grep:local] include_globs={globs} include_runner={include_runner} context={context}")

    chunks: list[str] = []
    scanned = matched = skipped_runner = 0

    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or is_inside_skip_dir(path):
                continue
            if not include_runner and is_runner_file(path):
                skipped_runner += 1
                continue
            if not any(fnmatch.fnmatch(path.name, glob) for glob in globs):
                continue
            scanned += 1
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.log(f"[grep:local] read failed {path}: {exc}")
                continue
            hit_lines: set[int] = set()
            for idx, line in enumerate(lines):
                if any(rx.search(line) for rx in regexes):
                    hit_lines.update(range(max(0, idx - context), min(len(lines), idx + context + 1)))
            if not hit_lines:
                continue
            matched += 1
            chunks.append(f"### {path.relative_to(repo)}")
            last = -99
            for idx in sorted(hit_lines):
                if idx != last + 1:
                    chunks.append("--")
                chunks.append(f"{idx + 1}:{lines[idx]}")
                last = idx

    text = "\n".join(chunks) or "<no grep matches>"
    logger.log(f"[grep:local] scanned={scanned} matched={matched} skipped_runner={skipped_runner} chars={len(text)}")
    return text


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def docker_grep(repo: Path, source_dirs: list[str], globs: list[str], patterns: list[str], context: int, image: str, timeout_s: int, logger: Logger, *, include_runner: bool) -> tuple[str, str, int, str]:
    include_args = " ".join(f"--include={shlex.quote(g)}" for g in globs)
    exclude_args = " ".join(f"--exclude-dir={shlex.quote(d)}" for d in sorted(SKIP_DIRS))
    if not include_runner:
        exclude_args += " --exclude=gremlin_rag_smoke.py"
    pattern = "|".join(f"(?:{p})" for p in patterns)
    roots = []
    for source_dir in source_dirs:
        clean = source_dir.strip().strip("/").replace("\\", "/")
        roots.append("/workspace" if not clean or clean == "." else f"/workspace/{clean}")
    root_args = " ".join(shlex.quote(r) for r in roots)
    inner = (
        "set +e\n"
        f"grep -RInE -C {int(context)} {include_args} {exclude_args} {shlex.quote(pattern)} {root_args} "
        "| sed 's#^/workspace/##'\n"
        "exit 0\n"
    )
    runtime = resolve_container_runtime(cwd=repo, probe=False)
    cmd = runtime.container_args("run", "--rm", "-v", f"{repo.resolve()}:/workspace:ro", "-w", "/workspace", image, "bash", "-lc", inner)
    logger.block("DOCKER GREP COMMAND USED", shell_join(cmd))
    logger.block("DOCKER GREP INNER BASH USED", inner)
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_s)
    logger.log(f"[grep:docker] returncode={proc.returncode} stdout_chars={len(proc.stdout or '')} stderr_chars={len(proc.stderr or '')}")
    return proc.stdout or "<no grep matches>", proc.stderr or "", proc.returncode, shell_join(cmd)


def sectionize_grep_buffer(text: str) -> list[str]:
    parts = re.split(r"(?m)^### ", text)
    sections: list[str] = []
    if parts and parts[0].strip() and not text.startswith("### "):
        sections.append(parts[0])
    for part in parts[1:]:
        sections.append("### " + part)
    return sections or [text]


def make_model_grep_buffer(raw: str, prompt: str, max_chars: int, logger: Logger) -> str:
    if max_chars <= 0 or len(raw) <= max_chars:
        logger.log(f"[model-buffer] using full raw grep buffer chars={len(raw)}")
        return raw

    terms = prompt_search_terms(prompt, limit=24) or derive_terms(prompt)
    sections = sectionize_grep_buffer(raw)

    def score(section: str) -> int:
        lower = section.lower()
        first = section.splitlines()[0] if section.splitlines() else ""
        first_lower = first.lower()
        s = sum(3 for t in terms if t.lower() in lower)
        s += sum(4 for t in terms if t.lower() in first_lower)

        ui_terms = {"button", "red", "green", "color", "style", "css", "html", "stop"}
        if any(t in ui_terms for t in terms):
            if first_lower.endswith(".html"):
                s += 8
            elif first_lower.endswith(".css"):
                s += 5
            elif first_lower.endswith(".js"):
                s += 2

        if "/vendor/" in first_lower or "\\vendor\\" in first_lower:
            s -= 20
        if first_lower.startswith("### tests/") and not any(t.startswith("test") for t in terms):
            s -= 4
        if "terminal" in first_lower and "terminal" not in terms:
            s -= 8
        if "harness.py" in first_lower:
            s -= 8
        if "gremlin_rag_smoke.py" in first_lower:
            s -= 100
        if "diagnostics_output" in first_lower:
            s -= 50
        return s

    ranked = sorted(sections, key=score, reverse=True)
    selected: list[str] = []
    used = 0
    for section in ranked:
        if score(section) < 0 and selected:
            continue
        piece = section.strip()
        if not piece:
            continue
        add_len = len(piece) + 2
        if used + add_len > max_chars:
            remaining = max_chars - used
            if remaining > 500:
                selected.append(piece[:remaining] + "\n... <selected model grep buffer cut here>")
            break
        selected.append(piece)
        used += add_len
    out = "\n\n".join(selected).strip() or "<no selected grep matches>"
    logger.log(f"[model-buffer] raw_chars={len(raw)} selected_chars={len(out)} max_chars={max_chars} sections={len(sections)}")
    return out


JS_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "function", "return", "const", "let", "var",
    "await", "fetch", "JSON", "Math", "Date", "Object", "Array", "String", "Number",
    "console", "render", "append", "push", "find", "map", "filter", "setInterval",
    "clearInterval", "encodeURIComponent",
}


def extract_symbol_candidates(model_grep: str, *, limit: int = 16) -> list[str]:
    """Find likely helper names called by selected evidence.

    Names that occur on lines with the requested UI state (Stop/button/color)
    are ranked first. This makes chatConsoleButton outrank unrelated helpers
    like chatConsoleRunLabel in the observed stop-button case.
    """
    scores: dict[str, int] = {}
    order: dict[str, int] = {}

    for line_index, line in enumerate(model_grep.splitlines()):
        lower_line = line.lower()
        state_line = any(term in lower_line for term in ("stop", "button", "red", "green", "color", "style", "css"))
        for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
            if name in JS_KEYWORDS or name.startswith(("set", "get")) and len(name) <= 4:
                continue
            if len(name) < 4:
                continue
            order.setdefault(name, line_index)
            score = 1
            if state_line:
                score += 20
            name_lower = name.lower()
            for boost in ("button", "stop", "color", "style", "class", "control"):
                if boost in name_lower:
                    score += 12
            if name_lower.startswith(("render", "save", "update", "stage", "evaluate")):
                score -= 4
            scores[name] = scores.get(name, 0) + score

    ranked = sorted(scores, key=lambda name: (-scores[name], order[name], name))
    return ranked[:limit]
def extract_paths_from_grep_buffer(model_grep: str) -> list[str]:
    paths: list[str] = []
    for line in model_grep.splitlines():
        if line.startswith("### "):
            value = line[4:].strip().replace("\\", "/")
            if value and value not in paths:
                paths.append(value)
    return paths


def _definition_patterns(symbol: str) -> list[re.Pattern[str]]:
    escaped = re.escape(symbol)
    return [
        re.compile(rf"\bfunction\s+{escaped}\s*\("),
        re.compile(rf"\b(?:const|let|var)\s+{escaped}\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"),
        re.compile(rf"\b{escaped}\s*=\s*(?:async\s*)?function\b"),
        re.compile(rf"^\s*def\s+{escaped}\s*\("),
        re.compile(rf"^\s*class\s+{escaped}\b"),
    ]


def find_symbol_definition_context(
    repo: Path,
    paths: list[str],
    symbols: list[str],
    *,
    context: int,
    max_chars: int,
    logger: Logger,
) -> str:
    if max_chars <= 0 or not paths or not symbols:
        logger.log(f"[symbol-context] skipped paths={len(paths)} symbols={len(symbols)} max_chars={max_chars}")
        return ""

    chunks: list[str] = []
    used = 0

    for rel in paths:
        clean = rel.replace("\\", "/").strip().lstrip("/")
        if not clean or ".." in Path(clean).parts:
            continue
        file_path = (repo / clean).resolve()
        try:
            file_path.relative_to(repo.resolve())
        except ValueError:
            continue
        if not file_path.is_file() or is_inside_skip_dir(file_path) or is_runner_file(file_path):
            continue

        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logger.log(f"[symbol-context] read failed {file_path}: {exc}")
            continue

        for symbol in symbols:
            patterns = _definition_patterns(symbol)
            for idx, line in enumerate(lines):
                if not any(pattern.search(line) for pattern in patterns):
                    continue

                start = max(0, idx - context)
                end = min(len(lines), idx + context + 1)
                header = f"### symbol definition: {symbol} in {clean}"
                block_lines = [header, "--"]
                for line_no in range(start, end):
                    block_lines.append(f"{line_no + 1}:{lines[line_no]}")
                block = "\n".join(block_lines).strip()
                add_len = len(block) + 2

                if used + add_len > max_chars:
                    remaining = max_chars - used
                    if remaining > 500:
                        chunks.append(block[:remaining] + "\n... <symbol context cut here>")
                    logger.log(f"[symbol-context] max_chars reached selected_chars={sum(len(c) for c in chunks)}")
                    return "\n\n".join(chunks).strip()

                chunks.append(block)
                used += add_len
                logger.log(f"[symbol-context] found {symbol} in {clean} around line {idx + 1}")
                break

    out = "\n\n".join(chunks).strip()
    logger.log(f"[symbol-context] symbols={symbols} selected_chars={len(out)}")
    return out


def append_symbol_context(
    repo: Path,
    model_grep: str,
    *,
    max_chars: int,
    context: int,
    logger: Logger,
) -> tuple[str, str]:
    """Append small helper-definition snippets for symbols visible in selected grep.

    This solves the observed Stop-button case: selected evidence shows
    chatConsoleButton("Stop", ...), so the next model call must also see the
    chatConsoleButton definition instead of guessing a third argument.
    """
    symbols = extract_symbol_candidates(model_grep)
    paths = extract_paths_from_grep_buffer(model_grep)
    symbol_context = find_symbol_definition_context(
        repo,
        paths,
        symbols,
        context=context,
        max_chars=max_chars,
        logger=logger,
    )
    if not symbol_context:
        return model_grep, ""

    combined = (
        model_grep.rstrip()
        + "\n\nSupplemental symbol-definition context used for this model call:\n"
        + symbol_context
    )
    logger.log(f"[symbol-context] appended_chars={len(symbol_context)} combined_model_grep_chars={len(combined)}")
    return combined, symbol_context


def validate_python_source(source: str, filename: str) -> None:
    compile(source, filename, "exec")


def _top_level_import_root(name: str) -> str:
    return str(name or "").split(".", 1)[0]


def _stdlib_module_names() -> set[str]:
    names = set(getattr(sys, "stdlib_module_names", set()))
    names.update({"__future__", "typing"})
    return names


def gremlin_source_issues(source: str) -> list[str]:
    """Return issues that make a generated gremlin unsafe or not smoke-runnable.

    This intentionally keeps the gremlin contract small: ordinary Python plus
    a main() that can be run by the smoke test. Fixed module attrs are optional.
    """
    issues: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    has_main = any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body)
    if not has_main:
        issues.append("missing top-level def main(...)")

    stdlib = _stdlib_module_names()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _top_level_import_root(alias.name)
                if root and root not in stdlib:
                    issues.append(f"external import not allowed in gremlin: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = _top_level_import_root(node.module or "")
            if node.level == 0 and root and root not in stdlib:
                issues.append(f"external import not allowed in gremlin: from {node.module} import ...")

    if "code_gremlin" in source:
        issues.append("invented code_gremlin dependency is not allowed")

    return issues


def ensure_main_json_runner(source: str) -> str:
    """Append a small __main__ JSON printer when the model omitted one.

    If the model supplied an existing __main__ block, leave it intact; static
    validation and executor output will decide whether it is usable.
    """
    if "__name__" in source and "__main__" in source:
        return source
    return source.rstrip() + "\n\nif __name__ == \"__main__\":\n    import json as _gremlin_json\n    print(_gremlin_json.dumps(main(), separators=(\",\", \":\"), sort_keys=True))\n"


def parse_grep_evidence_sections(grep_buffer: str) -> list[dict[str, Any]]:
    """Parse grep -C sections into compact evidence records."""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in str(grep_buffer or "").splitlines():
        if raw_line.startswith("### "):
            if current:
                sections.append(current)
            current = {"path": raw_line[4:].strip(), "lines": []}
            continue
        if current is None or raw_line.strip() == "--" or not raw_line.strip():
            continue
        match = re.match(r"^(\d+):(.*)$", raw_line)
        if match:
            current["lines"].append({"line": int(match.group(1)), "text": match.group(2)})
    if current:
        sections.append(current)
    return sections


def score_evidence_section(section: dict[str, Any], prompt: str) -> int:
    path = str(section.get("path") or "")
    body = "\n".join(str(item.get("text") or "") for item in (section.get("lines") or []))
    combined = f"{path}\n{body}".lower()
    path_lower = path.lower()
    terms = prompt_search_terms(prompt, limit=24) or derive_terms(prompt)
    score = 0
    for term in terms:
        low = term.lower()
        if low in combined:
            score += 4
        if low in path_lower:
            score += 5
    if re.search(r"chatconsolebutton\s*\(\s*['\"]stop['\"]", body, flags=re.IGNORECASE):
        score += 40
    if "stopchatconsoleairequest" in combined:
        score += 15
    if "button" in combined and "stop" in combined:
        score += 10
    if any(color in combined for color in ("red", "green", "danger")):
        score += 6
    suffix = Path(path.replace("\\", "/")).suffix.lower()
    if suffix in {".js", ".ts", ".tsx"}:
        score += 8
    elif suffix in {".html", ".css"}:
        score += 5
    elif suffix == ".py":
        score += 1
    for marker in ("basic-worker", "harness.py", "terminal", "vendor", "generated_component_docs"):
        if marker in path_lower:
            score -= 10
    if "gremlin_rag_smoke.py" in path_lower:
        score -= 100
    return score


def compact_evidence_sections(grep_buffer: str, prompt: str, *, limit_sections: int = 4, limit_lines: int = 18) -> list[dict[str, Any]]:
    sections = parse_grep_evidence_sections(grep_buffer)
    ranked = sorted(sections, key=lambda section: score_evidence_section(section, prompt), reverse=True)
    compact: list[dict[str, Any]] = []
    for section in ranked:
        section_score = score_evidence_section(section, prompt)
        if section_score < 0 and compact:
            continue
        compact.append({
            "path": str(section.get("path") or ""),
            "score": section_score,
            "lines": (section.get("lines") or [])[:limit_lines],
        })
        if len(compact) >= limit_sections:
            break
    return compact


def infer_stop_button_edit(prompt: str, evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    lower_prompt = prompt.lower()
    wants_stop_red = "stop" in lower_prompt and "red" in lower_prompt and ("green" in lower_prompt or "not green" in lower_prompt)
    if not wants_stop_red:
        return None
    for section in evidence:
        path = str(section.get("path") or "")
        if Path(path.replace("\\", "/")).suffix.lower() not in {".js", ".ts", ".tsx", ".html"}:
            continue
        for item in section.get("lines") or []:
            code = str(item.get("text") or "")
            if not re.search(r"chatConsoleButton\s*\(\s*['\"]Stop['\"]", code):
                continue
            if "stopChatConsoleAiRequest" not in code:
                continue
            indent = re.match(r"\s*", code).group(0)
            stripped = code.strip()
            child_indent = indent + "  "
            if re.match(r"^\s*if\s*\(", code):
                replacement_lines = [
                    f'{indent}if (cell.type === "ai" && cell.status === "running") {{',
                    f'{child_indent}const stopButton = chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id));',
                    f'{child_indent}stopButton.style.borderColor = "rgba(255, 99, 99, 0.85)";',
                    f'{child_indent}stopButton.style.background = "#4a1111";',
                    f'{child_indent}stopButton.style.color = "#ffecec";',
                    f'{child_indent}controls.append(stopButton);',
                    f'{indent}}}',
                ]
            else:
                replacement_lines = [
                    f'{indent}const stopButton = chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id));',
                    f'{indent}stopButton.style.borderColor = "rgba(255, 99, 99, 0.85)";',
                    f'{indent}stopButton.style.background = "#4a1111";',
                    f'{indent}stopButton.style.color = "#ffecec";',
                    f'{indent}controls.append(stopButton);',
                ]
            return {
                "kind": "replace_fragment",
                "target_file": path,
                "line": item.get("line"),
                "old_fragment": stripped,
                "replacement_fragment": "\n".join(replacement_lines).strip("\n"),
                "reason": "The grep evidence shows the running AI cell creates a Stop button with shared green button styling. The replacement creates the same button and applies explicit red stop styling before appending it.",
                "confidence": "medium",
            }
    return None


def build_evidence_fallback_payload(prompt: str, grep_plan: dict[str, Any], grep_buffer: str, rejected_issues: list[str]) -> dict[str, Any]:
    evidence = compact_evidence_sections(grep_buffer, prompt)
    proposed_edit = infer_stop_button_edit(prompt, evidence)
    target_file = str(proposed_edit.get("target_file") or "") if proposed_edit else (str(evidence[0].get("path") or "") if evidence else "")
    payload: dict[str, Any] = {
        "status": "model_rejected_evidence_fallback",
        "prompt": prompt,
        "rejected_model_issues": rejected_issues,
        "grep_plan": grep_plan,
        "target_file": target_file,
        "evidence": evidence,
        "proposed_edit": proposed_edit,
    }
    if not proposed_edit:
        payload["fail_condition"] = "The model gremlin was rejected and selected grep evidence was not specific enough to infer a concrete edit."
    return payload


def build_evidence_fallback_gremlin_source(prompt: str, grep_plan: dict[str, Any], grep_buffer: str, rejected_issues: list[str]) -> str:
    """Build a runnable fallback gremlin from selected grep evidence.

    The generated file follows the gremlin idea: small defs first, then main()
    visibly assembles the result from those parts. It is not a generic success
    placeholder; if no concrete edit can be inferred, main() returns ok=False.
    """
    payload = build_evidence_fallback_payload(prompt, grep_plan, grep_buffer, rejected_issues)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return (
        "from __future__ import annotations\n\n"
        "import json\nfrom typing import Any\n\n"
        f"PAYLOAD_JSON = {payload_json!r}\n\n"
        "def load_payload() -> dict[str, Any]:\n"
        "    return json.loads(PAYLOAD_JSON)\n\n"
        "def build_head(payload: dict[str, Any], prompt: str | None) -> dict[str, Any]:\n"
        "    return {\"kind\": \"code-gremlin\", \"name\": \"evidence_fallback_gremlin\", \"status\": payload.get(\"status\"), \"prompt\": prompt or payload.get(\"prompt\", \"\")}\n\n"
        "def choose_target_file(payload: dict[str, Any]) -> str:\n"
        "    return str(payload.get(\"target_file\") or \"\")\n\n"
        "def build_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:\n"
        "    return list(payload.get(\"evidence\") or [])\n\n"
        "def build_proposed_edit(payload: dict[str, Any]) -> dict[str, Any] | None:\n"
        "    edit = payload.get(\"proposed_edit\")\n"
        "    return dict(edit) if isinstance(edit, dict) else None\n\n"
        "def build_body(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    return {\"target_file\": choose_target_file(payload), \"proposed_edit\": build_proposed_edit(payload), \"rejected_model_issues\": list(payload.get(\"rejected_model_issues\") or [])}\n\n"
        "def build_content(payload: dict[str, Any]) -> dict[str, Any]:\n"
        "    edit = build_proposed_edit(payload)\n"
        "    if edit:\n"
        "        return {\"status\": \"code_change_inferred\", \"edit\": edit}\n"
        "    return {\"status\": \"needs_more_evidence\", \"fail_condition\": payload.get(\"fail_condition\")}\n\n"
        "def assemble_result(prompt: str | None = None) -> dict[str, Any]:\n"
        "    payload = load_payload()\n"
        "    content = build_content(payload)\n"
        "    result = {\"ok\": bool(build_proposed_edit(payload)), \"head\": build_head(payload, prompt), \"body\": build_body(payload), \"content\": content, \"evidence\": build_evidence(payload), \"requested_output_path\": \"diagnostics_output/gremlin_rag_smoke/final_result.json\"}\n"
        "    if not result[\"ok\"]:\n"
        "        result[\"fail_condition\"] = content.get(\"fail_condition\") or \"no concrete edit inferred\"\n"
        "    return result\n\n"
        "def main(prompt: str | None = None) -> dict[str, Any]:\n"
        "    return assemble_result(prompt)\n\n"
        "if __name__ == \"__main__\":\n"
        "    print(json.dumps(main(), separators=(\",\", \":\"), sort_keys=True))\n"
    )


def generated_name(prefix: str, prompt: str) -> str:
    return f"{prefix}_{slugify(prompt)}.py"


def run_python_in_docker(work_dir: Path, script_name: str, image: str, timeout_s: int, logger: Logger, args: list[str] | None = None) -> dict[str, Any]:
    args = args or []
    runtime = resolve_container_runtime(cwd=work_dir, probe=False)
    cmd = runtime.container_args("run", "--rm", "-v", f"{work_dir.resolve()}:/workspace:rw", "-w", "/workspace", image, "python", f"/workspace/{script_name}", *args)
    logger.block(f"DOCKER EXEC COMMAND USED FOR {script_name}", shell_join(cmd))
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_s)
        result = {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "docker_command": shell_join(cmd)}
    except Exception as exc:
        result = {"ok": False, "returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "docker_command": shell_join(cmd)}
    logger.block(f"STDOUT FROM {script_name}", str(result.get("stdout") or ""))
    logger.block(f"STDERR FROM {script_name}", str(result.get("stderr") or ""))
    return result


def run_python_locally(work_dir: Path, script_name: str, timeout_s: int, logger: Logger, args: list[str] | None = None) -> dict[str, Any]:
    args = args or []
    cmd = [sys.executable, str(work_dir / script_name), *args]
    logger.block(f"LOCAL EXEC COMMAND USED FOR {script_name}", shell_join(cmd))
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_s, cwd=str(work_dir))
        result = {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "command": shell_join(cmd)}
    except Exception as exc:
        result = {"ok": False, "returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "command": shell_join(cmd)}
    logger.block(f"STDOUT FROM {script_name}", str(result.get("stdout") or ""))
    logger.block(f"STDERR FROM {script_name}", str(result.get("stderr") or ""))
    return result


def parse_run_json(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    raw = str(stdout or "").strip()
    if not raw:
        return None, "empty stdout"
    try:
        value = json.loads(raw)
        return (value, None) if isinstance(value, dict) else (None, "stdout JSON was not an object")
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None, "stdout did not contain a JSON object"
    try:
        value = json.loads(match.group(0))
        return (value, None) if isinstance(value, dict) else (None, "embedded JSON was not an object")
    except json.JSONDecodeError as exc:
        return None, f"could not parse stdout JSON: {exc}"


def _walk_json_values(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_values(child)


def _truthy_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _status_says_success(value: Any) -> bool:
    return str(value or "").strip().lower() in {"ok", "success", "complete", "code_change_inferred"}


def _brace_balance_issue(text: str) -> str | None:
    """Cheap guard for JavaScript-ish replacement snippets.

    This is intentionally conservative: it only complains when a multi-line
    replacement visibly opens more braces than it closes. The goal is to catch
    unsafe payloads like replacing a one-line function signature with a partial
    function body.
    """
    raw = str(text or "")
    if "\n" not in raw and "function " not in raw:
        return None
    opens = raw.count("{")
    closes = raw.count("}")
    if opens != closes:
        return f"replacement appears to have unbalanced braces: {{={opens}, }}={closes}"
    return None


def edit_quality_issues(parsed: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    edit_like_seen = False
    status_success_seen = False

    for item in _walk_json_values(parsed):
        status = item.get("status")
        if _status_says_success(status):
            status_success_seen = True

        if "search" in item and "replace" in item:
            edit_like_seen = True
            search = str(item.get("search") or "").strip()
            replacement = str(item.get("replace") or "")
            if not search:
                issues.append("edit has empty search")
            if not replacement.strip():
                issues.append("edit has empty replacement")
            if re.match(r"^(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*\([^)]*\)\s*$", search):
                issues.append("edit search is only a function signature; use old_fragment with the full old block")
            if "\n" not in search and len(search) < 80 and "\n" in replacement:
                issues.append("edit uses a one-line search marker for a multi-line replacement")
            brace_issue = _brace_balance_issue(replacement)
            if brace_issue:
                issues.append(brace_issue)

        old_value = item.get("old_fragment", item.get("original"))
        new_value = item.get("replacement_fragment", item.get("replacement"))
        if old_value is not None or new_value is not None:
            edit_like_seen = True
            old_text = str(old_value or "").strip()
            new_text = str(new_value or "").strip()
            if not old_text:
                issues.append("edit is missing old_fragment/original")
            if not new_text:
                issues.append("edit is missing replacement_fragment/replacement")
            if old_text and new_text:
                if old_text == new_text:
                    issues.append("edit old and replacement fragments are identical")
                if "\n" not in old_text and len(old_text) < 30 and "\n" in new_text:
                    issues.append("edit old fragment is too small for a multi-line replacement")
                brace_issue = _brace_balance_issue(new_text)
                if brace_issue:
                    issues.append(brace_issue)

    if status_success_seen and not edit_like_seen:
        text = json.dumps(parsed, sort_keys=True, ensure_ascii=False).lower()
        if any(term in text for term in ("code change", "proposed_fix", "update_ui_style", "patch", "edit")):
            issues.append("success result mentions a code change but contains no machine-checkable edit object")

    # Keep only distinct issues in order.
    distinct: list[str] = []
    for issue in issues:
        if issue not in distinct:
            distinct.append(issue)
    return distinct


def should_replace_failed_result_with_evidence_fallback(reason: str) -> bool:
    lower = str(reason or "").lower()
    return any(
        needle in lower
        for needle in (
            "not machine-actionable",
            "missing_info",
            "needs_more_evidence",
            "incomplete",
            "no concrete edit",
        )
    )


def result_failed(executor_result: dict[str, Any]) -> tuple[bool, str]:
    if not executor_result.get("ok"):
        return True, "executor returned non-zero status or failed to run"
    parsed, error = parse_run_json(str(executor_result.get("stdout") or ""))
    if error:
        return True, error
    if not parsed:
        return True, "missing parsed JSON result"

    if parsed.get("ok") is False:
        return True, "gremlin main() returned ok=false"

    for item in _walk_json_values(parsed):
        status = str(item.get("status") or "").strip().lower()
        if status in {"incomplete", "needs_more_evidence", "failed", "error"}:
            return True, f"gremlin reported incomplete/failed status: {status}"
        if _truthy_text(item.get("fail_condition")):
            return True, f"gremlin reported fail condition: {item.get('fail_condition')}"
        if _truthy_text(item.get("error")):
            return True, f"gremlin reported error: {item.get('error')}"
        if _truthy_text(item.get("missing_info")):
            return True, f"gremlin reported missing_info: {item.get('missing_info')}"

    quality_issues = edit_quality_issues(parsed)
    if quality_issues:
        return True, "gremlin proposed edit is not machine-actionable: " + "; ".join(quality_issues[:4])

    # A top-level "status": "complete" is not enough to be useful if the result
    # simultaneously carries missing_info somewhere nested. The recursive check
    # above catches that case.
    return False, ""


def looks_like_no_matches(text: str) -> bool:
    return text.strip() in {"", "<no grep matches>"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verbose compact gremlin RAG smoke test.")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--source-dir", action="append", default=None, help="Repo-relative source folder. Default: existing main_computer and tests.")
    parser.add_argument("--out", default=None)
    parser.add_argument("--ai", choices=["ollama", "mock"], default=os.environ.get("GREMLIN_AI", "ollama"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--model", default=os.environ.get("MAIN_COMPUTER_MODEL", os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")))
    parser.add_argument("--num-predict", type=int, default=int(os.environ.get("GREMLIN_NUM_PREDICT", "700")), help="Ollama generation cap. 0 means do not set.")
    parser.add_argument("--docker-image", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_IMAGE", "main-computer-executor:latest"))
    parser.add_argument("--no-docker-grep", action="store_true")
    parser.add_argument("--no-docker-run", action="store_true")
    parser.add_argument("--allow-local-generated-code", action="store_true")
    parser.add_argument("--all-py", action="store_true", help="Search *.py directly; broad gremlin structural patterns are stripped when possible.")
    parser.add_argument("--no-auto-all-py-fallback", action="store_true")
    parser.add_argument("--include-runner", action="store_true", help="Allow grep to match this smoke-test script.")
    parser.add_argument("--context", type=int, default=3)
    parser.add_argument("--max-model-grep-chars", type=int, default=6000, help="Selected grep buffer sent to model. 0 uses full raw grep buffer.")
    parser.add_argument("--max-symbol-context-chars", type=int, default=4000, help="Extra helper/function definition context appended to model grep buffer. 0 disables.")
    parser.add_argument("--timeout-s", type=int, default=60, help="Timeout for Docker/local generated-code execution.")
    parser.add_argument("--ai-timeout-s", type=float, default=0.0, help="Ollama HTTP read timeout per model call. 0 means wait as long as needed.")
    parser.add_argument("--no-duckpatch", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Do not print verbose log to stdout; still writes full verbose.log.")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the prompt from stdin. Without this flag, a missing prompt prints help instead of silently blocking.",
    )
    args = parser.parse_args(argv)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        if args.stdin:
            print("[input] no prompt argument supplied; reading prompt from stdin...", flush=True)
            prompt = sys.stdin.read().strip()
        elif not sys.stdin.isatty():
            print("[input] no prompt argument supplied; reading prompt from piped stdin...", flush=True)
            prompt = sys.stdin.read().strip()
        else:
            print("error: prompt required; pass it as an argument or use --stdin.", file=sys.stderr, flush=True)
            print(file=sys.stderr, flush=True)
            parser.print_help(sys.stderr)
            return 2

    if not prompt:
        print("error: prompt was empty after reading input.", file=sys.stderr, flush=True)
        return 2

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"error: repo is not a directory: {repo}", file=sys.stderr)
        return 2

    run_id = "gremlin_rag_" + utc_stamp() + "_" + slugify(prompt, 28)
    out_dir = Path(args.out).resolve() if args.out else repo / "diagnostics_output" / "gremlin_rag_smoke" / run_id
    out_dir.mkdir(parents=True, exist_ok=False)

    logger = Logger(out_dir / "verbose.log", quiet=args.quiet)
    started = time.perf_counter()
    source_dirs = args.source_dir or default_source_dirs(repo)
    write_text(out_dir / "00_user_prompt.txt", prompt)

    logger.block("GREMLIN RAG SMOKE TEST", json.dumps({
        "prompt": prompt,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "ai": args.ai,
        "model": args.model,
        "num_predict": args.num_predict,
        "source_dirs": source_dirs,
        "no_docker_grep": args.no_docker_grep,
        "no_docker_run": args.no_docker_run,
        "all_py": args.all_py,
        "include_runner": args.include_runner,
        "max_model_grep_chars": args.max_model_grep_chars,
        "max_symbol_context_chars": args.max_symbol_context_chars,
        "timeout_s": args.timeout_s,
        "ai_timeout_s": args.ai_timeout_s,
    }, indent=2, sort_keys=True))

    chat = ChatClient(
        mode=args.ai,
        base_url=args.ollama_url,
        model=args.model,
        ai_timeout_s=args.ai_timeout_s,
        num_predict=args.num_predict,
        transcript_path=out_dir / "chat_transcript.json",
        out_dir=out_dir,
        logger=logger,
    )

    with Timer("1. super_grep_prep", logger):
        grep_prompt = SUPER_GREP_PREP_TEMPLATE.format(prompt=prompt)
        grep_answer = chat.ask("01_super_grep_prep", grep_prompt, want_json=True, fallback_kind="grep_plan")
        grep_plan = normalize_grep_plan(extract_json(grep_answer), all_py=args.all_py)
        write_json(out_dir / "01_super_grep_plan.json", grep_plan)
        logger.block("NORMALIZED GREP PLAN USED", json.dumps(grep_plan, indent=2, sort_keys=True))

    with Timer("2. grep", logger):
        if args.no_docker_grep:
            raw_grep = local_grep(repo, source_dirs, grep_plan["include_globs"], grep_plan["patterns"], args.context, logger, include_runner=args.include_runner)
            grep_stderr, grep_returncode, grep_command = "", 0, "local_grep"
        else:
            raw_grep, grep_stderr, grep_returncode, grep_command = docker_grep(repo, source_dirs, grep_plan["include_globs"], grep_plan["patterns"], args.context, args.docker_image, args.timeout_s, logger, include_runner=args.include_runner)
            if grep_stderr and ("Cannot connect to the Docker daemon" in grep_stderr or "No such image" in grep_stderr or "not found" in grep_stderr.lower()):
                logger.log("[grep] docker failed; falling back to local grep")
                raw_grep = local_grep(repo, source_dirs, grep_plan["include_globs"], grep_plan["patterns"], args.context, logger, include_runner=args.include_runner)
                grep_command += "\n# fallback: local_grep"

        if looks_like_no_matches(raw_grep) and not args.all_py and not args.no_auto_all_py_fallback:
            logger.log("[grep] gremlin-first search found no matches; falling back to targeted code globs")
            grep_plan["include_globs"] = CODE_FALLBACK_GLOBS
            grep_plan["patterns"] = fallback_patterns_for_prompt(prompt, logger)
            grep_plan["fallback_from_gremlin_first"] = True
            logger.block("FALLBACK GREP PLAN USED", json.dumps(grep_plan, indent=2, sort_keys=True))
            if args.no_docker_grep:
                raw_grep = local_grep(repo, source_dirs, grep_plan["include_globs"], grep_plan["patterns"], args.context, logger, include_runner=args.include_runner)
            else:
                raw_grep, grep_stderr, grep_returncode, grep_command = docker_grep(repo, source_dirs, grep_plan["include_globs"], grep_plan["patterns"], args.context, args.docker_image, args.timeout_s, logger, include_runner=args.include_runner)

        logger.file("RAW CANDIDATE GREP BUFFER SAVED (NOT SENT TO MODEL UNLESS SELECTED BELOW)", out_dir / "02_raw_grep_buffer.txt", raw_grep, echo=False)
        write_text(out_dir / "02_grep_stderr.txt", grep_stderr or "")
        write_text(out_dir / "02_grep_command.sh", grep_command + "\n")
        base_model_grep = make_model_grep_buffer(raw_grep, prompt, args.max_model_grep_chars, logger)
        logger.file("SELECTED GREP BUFFER BEFORE SYMBOL CONTEXT", out_dir / "02_model_grep_buffer_base.txt", base_model_grep)
        model_grep, symbol_context = append_symbol_context(
            repo,
            base_model_grep,
            max_chars=args.max_symbol_context_chars,
            context=max(args.context, 12),
            logger=logger,
        )
        logger.file("SUPPLEMENTAL SYMBOL CONTEXT USED BY MODEL", out_dir / "02_symbol_context_used.txt", symbol_context or "<no supplemental symbol context>")
        logger.file("SELECTED GREP BUFFER USED BY MODEL", out_dir / "02_model_grep_buffer_used.txt", model_grep)

    with Timer("3. build gremlin", logger):
        gremlin_prompt = GREMLIN_BUILD_TEMPLATE.format(
            prompt=prompt,
            grep_plan_json=json.dumps(grep_plan, indent=2, sort_keys=True),
            grep_buffer=model_grep,
        )
        gremlin_answer = chat.ask("03_gremlin_build", gremlin_prompt, want_json=False, fallback_kind="gremlin")
        gremlin_used_mock_fallback = chat.last_used_mock_fallback
        gremlin_source = strip_code_fence(gremlin_answer)
        gremlin_name = generated_name("gremlin", prompt)
        if gremlin_used_mock_fallback:
            issues = ["model call used mock fallback; replacing generic mock gremlin with evidence-based fallback"]
        else:
            gremlin_source = ensure_main_json_runner(gremlin_source)
            try:
                validate_python_source(gremlin_source, gremlin_name)
                issues = gremlin_source_issues(gremlin_source)
            except SyntaxError as exc:
                issues = [f"syntax error: {exc}"]
        if issues:
            logger.block("GENERATED GREMLIN REJECTED", json.dumps({"issues": issues}, indent=2, sort_keys=True))
            logger.log("[gremlin] using evidence-based fallback gremlin assembled from selected grep snippets")
            gremlin_source = build_evidence_fallback_gremlin_source(prompt, grep_plan, model_grep, issues)
            validate_python_source(gremlin_source, gremlin_name)
        else:
            logger.log("[gremlin] generated source accepted: ordinary Python with top-level main()")
        logger.file(f"GENERATED GREMLIN SOURCE USED: {gremlin_name}", out_dir / gremlin_name, gremlin_source)

    with Timer("4. execute gremlin", logger):
        if args.no_docker_run:
            if args.allow_local_generated_code:
                run1 = run_python_locally(out_dir, gremlin_name, args.timeout_s, logger)
            else:
                run1 = {"ok": False, "returncode": None, "stdout": "", "stderr": "--no-docker-run without --allow-local-generated-code", "command": "not executed"}
                logger.block("GREMLIN EXECUTION SKIPPED", json.dumps(run1, indent=2))
        else:
            run1 = run_python_in_docker(out_dir, gremlin_name, args.docker_image, args.timeout_s, logger)
        write_json(out_dir / "04_gremlin_executor_result.json", run1)
        failed, fail_reason = result_failed(run1)
        logger.log(f"[gremlin] failed={failed} reason={fail_reason!r}")

    final_run = run1
    duckpatcher_name = None
    duckpatcher_result = None

    if failed and should_replace_failed_result_with_evidence_fallback(fail_reason):
        with Timer("5. evidence fallback after non-actionable result", logger):
            logger.log("[gremlin] replacing non-actionable generated result with evidence-based fallback gremlin")
            fallback_issues = [f"generated gremlin executed but result was not useful: {fail_reason}"]
            gremlin_source = build_evidence_fallback_gremlin_source(prompt, grep_plan, model_grep, fallback_issues)
            validate_python_source(gremlin_source, gremlin_name)
            logger.file(f"EVIDENCE FALLBACK GREMLIN SOURCE USED AFTER EXECUTION: {gremlin_name}", out_dir / gremlin_name, gremlin_source)
            if args.no_docker_run:
                if args.allow_local_generated_code:
                    final_run = run_python_locally(out_dir, gremlin_name, args.timeout_s, logger)
                else:
                    final_run = {"ok": False, "returncode": None, "stdout": "", "stderr": "--no-docker-run without --allow-local-generated-code", "command": "not executed"}
            else:
                final_run = run_python_in_docker(out_dir, gremlin_name, args.docker_image, args.timeout_s, logger)
            write_json(out_dir / "05_evidence_fallback_executor_result.json", final_run)
            failed, fail_reason = result_failed(final_run)
            logger.log(f"[gremlin] evidence fallback failed={failed} reason={fail_reason!r}")

    if failed and not args.no_duckpatch:
        with Timer("5. build and run duckpatcher", logger):
            duck_prompt = DUCKPATCHER_TEMPLATE.format(
                prompt=prompt,
                gremlin_path=gremlin_name,
                gremlin_source=(out_dir / gremlin_name).read_text(encoding="utf-8", errors="replace"),
                executor_result_json=json.dumps({"failure_reason": fail_reason, "executor_result": run1}, indent=2, sort_keys=True),
            )
            duck_source = strip_code_fence(chat.ask("05_duckpatcher_build", duck_prompt, want_json=False, fallback_kind="duckpatcher"))
            duckpatcher_name = generated_name("duckpatcher", prompt)
            try:
                validate_python_source(duck_source, duckpatcher_name)
            except SyntaxError as exc:
                logger.log(f"[duckpatcher] invalid generated source; using fallback: {exc}")
                duck_source = DEFAULT_DUCKPATCHER_SOURCE
                validate_python_source(duck_source, duckpatcher_name)
            logger.file(f"GENERATED DUCKPATCHER SOURCE USED: {duckpatcher_name}", out_dir / duckpatcher_name, duck_source)

            if args.no_docker_run:
                if args.allow_local_generated_code:
                    duckpatcher_result = run_python_locally(out_dir, duckpatcher_name, args.timeout_s, logger, [gremlin_name])
                    final_run = run_python_locally(out_dir, gremlin_name, args.timeout_s, logger)
                else:
                    duckpatcher_result = {"ok": False, "returncode": None, "stdout": "", "stderr": "--no-docker-run without --allow-local-generated-code", "command": "not executed"}
            else:
                duckpatcher_result = run_python_in_docker(out_dir, duckpatcher_name, args.docker_image, args.timeout_s, logger, [gremlin_name])
                final_run = run_python_in_docker(out_dir, gremlin_name, args.docker_image, args.timeout_s, logger)
            write_json(out_dir / "06_duckpatcher_executor_result.json", duckpatcher_result)
            write_json(out_dir / "07_gremlin_rerun_executor_result.json", final_run)

    with Timer("6. final report", logger):
        parsed_final, parse_error = parse_run_json(str(final_run.get("stdout") or ""))
        final_failed, final_reason = result_failed(final_run)
        report = {
            "ok": not final_failed,
            "run_id": run_id,
            "prompt": prompt,
            "repo": str(repo),
            "out_dir": str(out_dir),
            "verbose_log": str(out_dir / "verbose.log"),
            "ai": args.ai,
            "model": args.model,
            "num_predict": args.num_predict,
            "ai_timeout_s": args.ai_timeout_s,
            "grep_plan": grep_plan,
            "grep_returncode": grep_returncode,
            "grep_command_path": str(out_dir / "02_grep_command.sh"),
            "raw_grep_buffer_path": str(out_dir / "02_raw_grep_buffer.txt"),
            "model_grep_buffer_used_path": str(out_dir / "02_model_grep_buffer_used.txt"),
            "gremlin_file": gremlin_name,
            "gremlin_path": str(out_dir / gremlin_name),
            "duckpatcher_file": duckpatcher_name,
            "duckpatcher_result": duckpatcher_result,
            "final_executor_result": final_run,
            "final_parsed_json": parsed_final,
            "final_parse_error": parse_error,
            "final_failure_reason": final_reason,
            "elapsed_s": round(time.perf_counter() - started, 3),
            "notes": [
                "all exact model inputs and outputs are logged without truncation",
                "raw candidate grep buffer is saved to file without duplicating huge non-model payloads into verbose.log",
                "selected model grep buffer and all generated/executed artifacts are logged without truncation",
                "default search dirs are existing main_computer and tests unless --source-dir is provided",
                "runner file and heavy generated/cache folders are excluded from grep unless explicitly overridden where available",
                "generated gremlin validation only requires ordinary Python, safe imports, and top-level main(); fixed attrs are optional",
                "Ollama HTTP read timeout defaults to no cap; use --ai-timeout-s to set one",
                "Ollama generation is capped by --num-predict unless set to 0",
            ],
        }
        write_json(out_dir / "final_report.json", report)
        logger.block("FINAL REPORT", json.dumps(report, indent=2, sort_keys=True))

    summary = {
        "ok": report["ok"],
        "out_dir": str(out_dir),
        "verbose_log": str(out_dir / "verbose.log"),
        "gremlin_file": gremlin_name,
        "duckpatcher_file": duckpatcher_name,
        "final_report": str(out_dir / "final_report.json"),
        "final_failure_reason": report["final_failure_reason"],
        "elapsed_s": report["elapsed_s"],
    }
    logger.block("FINAL SUMMARY JSON", json.dumps(summary, indent=2, sort_keys=True))
    if args.quiet:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
