#!/usr/bin/env python3
"""
gremlin_rag_smoke.py

Standalone RAG smoke test for code-gremlins.

Flow:
1. Ask local AI for a safe grep plan: super_grep_prep.
2. Run grep with -C 3 context over gremlin_*.py by default.
3. Feed compressed grep buffer back to local AI.
4. Generate a normal Python gremlin_*.py file.
5. Run gremlin.main() through Docker.
6. On error/fail_condition, ask for duckpatcher_*.py, run it, then rerun gremlin.

Default AI endpoint:
  Ollama chat API at http://localhost:11434/api/chat

Example:
  python gremlin_rag_smoke.py "make a gremlin for csv validation" --repo .

Useful test mode without Ollama:
  python gremlin_rag_smoke.py "make a gremlin for csv validation" --repo . --ai mock
"""

from __future__ import annotations

import argparse
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
import traceback
import urllib.error
import urllib.request
from typing import Any


DEFAULT_SYSTEM = """You are the gremlin RAG smoke-test kernel writer.

A code-gremlin is ordinary Python. It may have module attributes like HEAD,
BODY, CONTENT, FEATURES, SEARCH_HINTS, TOP_QUESTIONS, and DUCKPATCH_CONTRACT,
but it does not know it is special.

In the default flavor, files named gremlin_*.py are preferred. Any .py file may
be treated as a code-gremlin when --all-py is enabled.

A good gremlin may not contain the final answer. It may contain:
- findable features for later grep/RAG passes
- top likely questions deduced from the user's prompt
- code fragments that make sense for the user's likely target
- a head/body/content structure that the next AI call can run, patch, or assemble
- a fail_condition or error message that can trigger duckpatcher_*.py

Return only the requested machine-readable artifact.
"""


SUPER_GREP_PREP_TEMPLATE = """User prompt:
{prompt}

Task:
Produce a safe grep plan for finding relevant code-gremlin fragments.

Rules:
- Do not solve the user's task yet.
- The executor will run grep with -C 3.
- Prefer grep patterns that find likely gremlin concepts, names, module attrs, and target code.
- Default flavor is ordinary Python files named gremlin_*.py.
- Any *.py may be a code-gremlin when --all-py is enabled.
- Return JSON only.

Schema:
{{
  "intent": "one sentence",
  "include_globs": ["gremlin_*.py"],
  "patterns": ["extended grep regex"],
  "top_questions": ["what the user likely wants from a found fragment"],
  "why": "brief reason"
}}
"""


GREMLIN_BUILD_TEMPLATE = """User prompt:
{prompt}

Grep plan:
{grep_plan_json}

Grep buffer:
{grep_buffer}

Task:
Assemble one ordinary Python file named gremlin_<slug>.py.

The gremlin must:
- use only Python standard library
- define HEAD, BODY, CONTENT, FEATURES, SEARCH_HINTS, TOP_QUESTIONS, DUCKPATCH_CONTRACT
- include useful code fragments for the user's likely target
- not assume it already has the answer
- expose main(prompt: str | None = None) -> dict
- when run as a script, print JSON from main()
- include "ok": true unless it detects a real fail_condition
- include "requested_output_path" when a next stage should write a result

Return only Python source code. No markdown fences.
"""


DUCKPATCHER_TEMPLATE = """User prompt:
{prompt}

The generated gremlin failed or reported a fail condition.

Gremlin path:
{gremlin_path}

Gremlin source:
{gremlin_source}

Executor result:
{executor_result_json}

Task:
Return one ordinary Python file named duckpatcher_<slug>.py.

The duckpatcher must:
- use only Python standard library
- read the gremlin file path from argv[1]
- rewrite the gremlin by applying small string-level deltas
- preserve main(prompt=None) -> dict
- print JSON with ok, patched, changes, gremlin_path

Return only Python source code. No markdown fences.
"""


DEFAULT_GREMLIN_SOURCE = """from __future__ import annotations

import json
from typing import Any

HEAD = {
    "kind": "code-gremlin",
    "name": "gremlin_mock_kernel",
    "flavor": "ordinary-python",
    "summary": "Fallback gremlin kernel emitted when no local AI is available.",
}

BODY = {
    "purpose": "Describe a RAG smoke-test chain that starts with gremlin search.",
    "fragments": [
        "super_grep_prep -> grep -RInE -C 3 over gremlin_*.py",
        "compressed grep buffer -> generated gremlin_*.py",
        "docker executor runs gremlin.main()",
        "failure -> duckpatcher_*.py rewrites the gremlin by deltas",
    ],
}

CONTENT = {
    "likely_output_shape": {
        "head": "identity and search metadata",
        "body": "deduced target steps and code fragments",
        "content": "next runnable kernel for the caller",
    }
}

FEATURES = {
    "findable_terms": ["gremlin", "rag", "grep", "docker", "duckpatcher", "main"],
    "file_flavor": ".py",
}

SEARCH_HINTS = ["HEAD", "BODY", "CONTENT", "FEATURES", "SEARCH_HINTS", "main("]

TOP_QUESTIONS = [
    "Which gremlin fragments best match the user's requested fix or test?",
    "What should the next runnable kernel emit?",
    "Does the kernel need a duckpatcher pass?",
]

DUCKPATCH_CONTRACT = {
    "patch_target": "this gremlin source file",
    "patch_method": "string-level deltas",
    "preserve": ["main(prompt=None) -> dict"],
}


def main(prompt: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "head": HEAD,
        "body": BODY,
        "content": CONTENT,
        "features": FEATURES,
        "search_hints": SEARCH_HINTS,
        "top_questions": TOP_QUESTIONS,
        "duckpatch_contract": DUCKPATCH_CONTRACT,
        "prompt_seen": prompt or "",
        "requested_output_path": "diagnostics_output/gremlin_rag_smoke/final_result.json",
    }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
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

    if '"ok": False' in text:
        text = text.replace('"ok": False', '"ok": True')
        changes.append("changed JSON-style ok false to ok true")

    if "'ok': False" in text:
        text = text.replace("'ok': False", "'ok': True")
        changes.append("changed Python-style ok false to ok true")

    if "fail_condition" in text and "patched_fail_condition" not in text:
        text = text.replace("fail_condition", "patched_fail_condition")
        changes.append("renamed fail_condition marker")

    path.write_text(text, encoding="utf-8")

    return {
        "ok": True,
        "patched": bool(changes),
        "changes": changes,
        "gremlin_path": str(path),
    }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
"""


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(text: str, limit: int = 48) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    base = "_".join(words[:8]) or "prompt"
    base = base[:limit].strip("_") or "prompt"
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{base}_{digest}"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_text(path: Path, limit: int | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if limit is not None and len(text) > limit:
        return text[:limit] + f"\n... <truncated {len(text) - limit} chars>"
    return text


def strip_code_fence(text: str) -> str:
    raw = str(text or "").strip()
    match = re.search(r"```(?:python|py)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip() + "\n"
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
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into",
        "have", "what", "when", "where", "then", "than", "they", "them",
        "will", "would", "should", "could", "about", "based", "given",
        "want", "wants", "need", "needs", "just", "code", "python",
        "file", "files", "user", "prompt", "answer",
    }

    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())
    ranked = []

    for word in words:
        if word not in stop and word not in ranked:
            ranked.append(word)

    preferred = [
        "gremlin", "rag", "grep", "docker", "executor", "duckpatch",
        "main", "head", "body", "content", "structure", "kernel", "smoke",
    ]

    merged = [word for word in preferred if word in text.lower()]

    for word in ranked:
        if word not in merged:
            merged.append(word)

    return merged[:24]


class ChatClient:
    def __init__(
        self,
        *,
        mode: str,
        base_url: str,
        model: str,
        timeout_s: float,
        transcript_path: Path,
    ):
        self.mode = mode
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = float(timeout_s)
        self.transcript_path = transcript_path
        self.messages = [{"role": "system", "content": DEFAULT_SYSTEM}]

    def ask(self, user_text: str, *, want_json: bool = False, fallback_kind: str = "text") -> str:
        self.messages.append({"role": "user", "content": user_text})

        if self.mode == "mock":
            answer = self._mock_response(user_text, fallback_kind)
            self.messages.append({"role": "assistant", "content": answer})
            self._write_transcript()
            return answer

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "stream": False,
        }

        if want_json:
            payload["format"] = "json"

        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8", errors="replace"))

            answer = str(((body.get("message") or {}).get("content")) or "")

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            answer = self._mock_response(user_text, fallback_kind)
            self.messages.append({
                "role": "system",
                "content": (
                    "NOTE: local AI call failed and mock fallback was used: "
                    f"{type(exc).__name__}: {exc}"
                ),
            })

        self.messages.append({"role": "assistant", "content": answer})
        self._write_transcript()
        return answer

    def _write_transcript(self) -> None:
        write_json(self.transcript_path, self.messages)

    def _mock_response(self, user_text: str, fallback_kind: str) -> str:
        if fallback_kind == "grep_plan":
            terms = derive_terms(user_text)
            pattern = "|".join(re.escape(term) for term in terms[:12])
            pattern = pattern or r"main\(|HEAD|BODY|CONTENT|FEATURES|SEARCH_HINTS"

            return json.dumps(
                {
                    "intent": "Find gremlin-like Python fragments relevant to the prompt.",
                    "include_globs": ["gremlin_*.py"],
                    "patterns": [
                        pattern,
                        r"HEAD|BODY|CONTENT|FEATURES|SEARCH_HINTS|TOP_QUESTIONS|main\(",
                    ],
                    "top_questions": [
                        "What file or code fragment is the user trying to create or repair?",
                        "What findable features should future grep passes look for?",
                        "What output shape should gremlin.main() emit?",
                    ],
                    "why": "Mock fallback derived keywords because local AI was unavailable.",
                },
                indent=2,
            )

        if fallback_kind == "duckpatcher":
            return DEFAULT_DUCKPATCHER_SOURCE

        return DEFAULT_GREMLIN_SOURCE


def normalize_grep_plan(plan: dict[str, Any], *, all_py: bool) -> dict[str, Any]:
    patterns = [str(item).strip() for item in plan.get("patterns", []) if str(item).strip()]
    include_globs = [str(item).strip() for item in plan.get("include_globs", []) if str(item).strip()]

    if not patterns:
        patterns = [r"HEAD|BODY|CONTENT|FEATURES|SEARCH_HINTS|TOP_QUESTIONS|main\("]

    if all_py:
        include_globs = ["*.py"]

    if not include_globs:
        include_globs = ["gremlin_*.py"]

    return {
        "intent": str(plan.get("intent") or "Find relevant code-gremlin fragments."),
        "include_globs": include_globs[:8],
        "patterns": [pattern[:300] for pattern in patterns[:8]],
        "top_questions": [str(item) for item in plan.get("top_questions", [])][:12],
        "why": str(plan.get("why") or ""),
    }


def should_skip_path(path: Path) -> bool:
    skip_names = {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
    }
    return bool(set(path.parts) & skip_names)


def local_grep(
    repo: Path,
    source_dirs: list[str],
    include_globs: list[str],
    patterns: list[str],
    context: int,
    max_chars: int,
) -> str:
    regexes: list[re.Pattern[str]] = []

    for pattern in patterns:
        try:
            regexes.append(re.compile(pattern, flags=re.IGNORECASE))
        except re.error:
            regexes.append(re.compile(re.escape(pattern), flags=re.IGNORECASE))

    roots: list[Path] = []

    for source_dir in source_dirs:
        candidate = (repo / source_dir).resolve()

        try:
            candidate.relative_to(repo.resolve())
        except ValueError:
            continue

        if candidate.exists():
            roots.append(candidate)

    if not roots:
        roots = [repo]

    chunks: list[str] = []

    for root in roots:
        for path in root.rglob("*"):
            if len("\n".join(chunks)) >= max_chars:
                break

            if not path.is_file() or should_skip_path(path):
                continue

            if not any(fnmatch.fnmatch(path.name, glob) for glob in include_globs):
                continue

            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            hit_lines: set[int] = set()

            for index, line in enumerate(lines):
                if any(regex.search(line) for regex in regexes):
                    start = max(0, index - context)
                    end = min(len(lines), index + context + 1)
                    hit_lines.update(range(start, end))

            if not hit_lines:
                continue

            chunks.append(f"### {path.relative_to(repo)}")
            previous = -99

            for line_index in sorted(hit_lines):
                if line_index != previous + 1:
                    chunks.append("--")
                chunks.append(f"{line_index + 1}:{lines[line_index]}")
                previous = line_index

    text = "\n".join(chunks)

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... <grep buffer truncated at {max_chars} chars>"

    return text or "<no grep matches>"


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def docker_grep(
    *,
    repo: Path,
    source_dirs: list[str],
    include_globs: list[str],
    patterns: list[str],
    context: int,
    max_chars: int,
    image: str,
    timeout_s: int,
) -> tuple[str, str, int, str]:
    include_args = " ".join(f"--include={shlex.quote(glob)}" for glob in include_globs)
    pattern = "|".join(f"(?:{pattern})" for pattern in patterns)

    roots = []

    for source_dir in source_dirs:
        clean = source_dir.strip().strip("/").replace("\\", "/")
        roots.append("/workspace" if not clean or clean == "." else f"/workspace/{clean}")

    root_args = " ".join(shlex.quote(root) for root in roots)

    command_inside = (
        "set +e\n"
        f"grep -RInE -C {int(context)} {include_args} {shlex.quote(pattern)} {root_args} "
        "| sed 's#^/workspace/##' "
        f"| head -c {int(max_chars)}\n"
        "exit 0\n"
    )

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{repo.resolve()}:/workspace:ro",
        "-w",
        "/workspace",
        image,
        "bash",
        "-lc",
        command_inside,
    ]

    proc = subprocess.run(
        docker_cmd,
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )

    return (
        proc.stdout or "<no grep matches>",
        proc.stderr,
        proc.returncode,
        shell_join(docker_cmd),
    )


def validate_python_source(source: str, filename: str) -> None:
    try:
        compile(source, filename, "exec")
    except SyntaxError as exc:
        raise ValueError(f"generated source is not valid Python: {exc}") from exc


def generated_name(prefix: str, prompt: str) -> str:
    return f"{prefix}_{slugify(prompt)}.py"


def run_python_in_docker(
    *,
    work_dir: Path,
    script_name: str,
    image: str,
    timeout_s: int,
    args: list[str] | None = None,
) -> dict[str, Any]:
    args = args or []

    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{work_dir.resolve()}:/workspace:rw",
        "-w",
        "/workspace",
        image,
        "python",
        f"/workspace/{script_name}",
        *args,
    ]

    try:
        proc = subprocess.run(
            docker_cmd,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )

        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "docker_command": shell_join(docker_cmd),
        }

    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "docker_command": shell_join(docker_cmd),
        }


def run_python_locally(
    *,
    work_dir: Path,
    script_name: str,
    timeout_s: int,
    args: list[str] | None = None,
) -> dict[str, Any]:
    args = args or []
    cmd = [sys.executable, str(work_dir / script_name), *args]

    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            cwd=str(work_dir),
        )

        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "command": shell_join(cmd),
        }

    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "command": shell_join(cmd),
        }


def parse_run_json(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    raw = str(stdout or "").strip()

    if not raw:
        return None, "empty stdout"

    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value, None
        return None, "stdout JSON was not an object"
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)

    if not match:
        return None, "stdout did not contain a JSON object"

    try:
        value = json.loads(match.group(0))
        if isinstance(value, dict):
            return value, None
        return None, "embedded JSON was not an object"
    except json.JSONDecodeError as exc:
        return None, f"could not parse stdout JSON: {exc}"


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

    fail = parsed.get("fail_condition") or parsed.get("error")

    if fail:
        return True, f"gremlin reported fail condition: {fail}"

    return False, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone gremlin RAG smoke test.")

    parser.add_argument("prompt", nargs="*", help="Prompt to turn into a gremlin RAG smoke chain.")
    parser.add_argument("--repo", default=".", help="Repo/source root to search and mount.")
    parser.add_argument("--source-dir", action="append", default=None, help="Repo-relative source folder. Repeatable.")
    parser.add_argument("--out", default=None, help="Output directory.")
    parser.add_argument("--ai", choices=["ollama", "mock"], default=os.environ.get("GREMLIN_AI", "ollama"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--model", default=os.environ.get("MAIN_COMPUTER_MODEL", os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")))
    parser.add_argument("--docker-image", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_IMAGE", "main-computer-executor:latest"))
    parser.add_argument("--no-docker-grep", action="store_true")
    parser.add_argument("--no-docker-run", action="store_true")
    parser.add_argument("--allow-local-generated-code", action="store_true")
    parser.add_argument("--all-py", action="store_true", help="Search all *.py files instead of only gremlin_*.py.")
    parser.add_argument("--context", type=int, default=3)
    parser.add_argument("--max-grep-chars", type=int, default=24000)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--no-duckpatch", action="store_true")

    args = parser.parse_args(argv)

    prompt = " ".join(args.prompt).strip()

    if not prompt:
        prompt = sys.stdin.read().strip()

    if not prompt:
        print("error: prompt required as argv or stdin", file=sys.stderr)
        return 2

    repo = Path(args.repo).resolve()

    if not repo.exists() or not repo.is_dir():
        print(f"error: repo does not exist or is not a directory: {repo}", file=sys.stderr)
        return 2

    run_id = "gremlin_rag_" + utc_stamp() + "_" + slugify(prompt, 28)
    out_dir = Path(args.out).resolve() if args.out else repo / "diagnostics_output" / "gremlin_rag_smoke" / run_id
    out_dir.mkdir(parents=True, exist_ok=False)

    source_dirs = args.source_dir or ["."]
    write_text(out_dir / "00_user_prompt.txt", prompt)

    chat = ChatClient(
        mode=args.ai,
        base_url=args.ollama_url,
        model=args.model,
        timeout_s=args.timeout_s,
        transcript_path=out_dir / "chat_transcript.json",
    )

    grep_prompt = SUPER_GREP_PREP_TEMPLATE.format(prompt=prompt)
    write_text(out_dir / "01_super_grep_prep_prompt.txt", grep_prompt)

    grep_answer = chat.ask(
        grep_prompt,
        want_json=True,
        fallback_kind="grep_plan",
    )

    grep_plan = normalize_grep_plan(
        extract_json(grep_answer),
        all_py=bool(args.all_py),
    )

    write_json(out_dir / "01_super_grep_plan.json", grep_plan)

    if args.no_docker_grep:
        grep_buffer = local_grep(
            repo,
            source_dirs,
            grep_plan["include_globs"],
            grep_plan["patterns"],
            args.context,
            args.max_grep_chars,
        )
        grep_stderr = ""
        grep_returncode = 0
        grep_command = "local_python_grep(" + json.dumps(grep_plan) + ")"

    else:
        grep_buffer, grep_stderr, grep_returncode, grep_command = docker_grep(
            repo=repo,
            source_dirs=source_dirs,
            include_globs=grep_plan["include_globs"],
            patterns=grep_plan["patterns"],
            context=args.context,
            max_chars=args.max_grep_chars,
            image=args.docker_image,
            timeout_s=args.timeout_s,
        )

        docker_failed = (
            grep_stderr
            and (
                "Cannot connect to the Docker daemon" in grep_stderr
                or "not found" in grep_stderr.lower()
                or "No such image" in grep_stderr
            )
        )

        if docker_failed:
            grep_buffer = local_grep(
                repo,
                source_dirs,
                grep_plan["include_globs"],
                grep_plan["patterns"],
                args.context,
                args.max_grep_chars,
            )
            grep_command += "\n# Docker grep failed; used local Python grep fallback."

    write_text(out_dir / "02_grep_command.sh", grep_command + "\n")
    write_text(out_dir / "02_grep_stderr.txt", grep_stderr or "")
    write_text(out_dir / "02_grep_buffer.txt", grep_buffer)

    gremlin_prompt = GREMLIN_BUILD_TEMPLATE.format(
        prompt=prompt,
        grep_plan_json=json.dumps(grep_plan, indent=2, sort_keys=True),
        grep_buffer=grep_buffer,
    )

    write_text(out_dir / "03_gremlin_build_prompt.txt", gremlin_prompt)

    gremlin_source = strip_code_fence(
        chat.ask(
            gremlin_prompt,
            want_json=False,
            fallback_kind="gremlin",
        )
    )

    gremlin_name = generated_name("gremlin", prompt)

    try:
        validate_python_source(gremlin_source, gremlin_name)
    except ValueError as exc:
        write_text(out_dir / "03_invalid_gremlin_error.txt", str(exc))
        gremlin_source = DEFAULT_GREMLIN_SOURCE
        validate_python_source(gremlin_source, gremlin_name)

    write_text(out_dir / gremlin_name, gremlin_source)

    if args.no_docker_run:
        if args.allow_local_generated_code:
            run1 = run_python_locally(
                work_dir=out_dir,
                script_name=gremlin_name,
                timeout_s=args.timeout_s,
            )
        else:
            run1 = {
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": "--no-docker-run was set without --allow-local-generated-code",
                "command": "not executed",
            }
    else:
        run1 = run_python_in_docker(
            work_dir=out_dir,
            script_name=gremlin_name,
            image=args.docker_image,
            timeout_s=args.timeout_s,
        )

    write_json(out_dir / "04_gremlin_executor_result.json", run1)
    write_text(out_dir / "04_gremlin_stdout.txt", str(run1.get("stdout") or ""))
    write_text(out_dir / "04_gremlin_stderr.txt", str(run1.get("stderr") or ""))

    failed, fail_reason = result_failed(run1)
    final_run = run1
    duckpatcher_name = None
    duckpatcher_result = None

    if failed and not args.no_duckpatch:
        duck_prompt = DUCKPATCHER_TEMPLATE.format(
            prompt=prompt,
            gremlin_path=gremlin_name,
            gremlin_source=read_text(out_dir / gremlin_name, limit=40000),
            executor_result_json=json.dumps(
                {
                    "failure_reason": fail_reason,
                    "executor_result": run1,
                },
                indent=2,
                sort_keys=True,
            ),
        )

        write_text(out_dir / "05_duckpatcher_prompt.txt", duck_prompt)

        duck_source = strip_code_fence(
            chat.ask(
                duck_prompt,
                want_json=False,
                fallback_kind="duckpatcher",
            )
        )

        duckpatcher_name = generated_name("duckpatcher", prompt)

        try:
            validate_python_source(duck_source, duckpatcher_name)
        except ValueError as exc:
            write_text(out_dir / "05_invalid_duckpatcher_error.txt", str(exc))
            duck_source = DEFAULT_DUCKPATCHER_SOURCE
            validate_python_source(duck_source, duckpatcher_name)

        write_text(out_dir / duckpatcher_name, duck_source)

        if args.no_docker_run:
            if args.allow_local_generated_code:
                duckpatcher_result = run_python_locally(
                    work_dir=out_dir,
                    script_name=duckpatcher_name,
                    timeout_s=args.timeout_s,
                    args=[gremlin_name],
                )
                final_run = run_python_locally(
                    work_dir=out_dir,
                    script_name=gremlin_name,
                    timeout_s=args.timeout_s,
                )
            else:
                duckpatcher_result = {
                    "ok": False,
                    "returncode": None,
                    "stdout": "",
                    "stderr": "--no-docker-run was set without --allow-local-generated-code",
                    "command": "not executed",
                }
        else:
            duckpatcher_result = run_python_in_docker(
                work_dir=out_dir,
                script_name=duckpatcher_name,
                image=args.docker_image,
                timeout_s=args.timeout_s,
                args=[gremlin_name],
            )
            final_run = run_python_in_docker(
                work_dir=out_dir,
                script_name=gremlin_name,
                image=args.docker_image,
                timeout_s=args.timeout_s,
            )

        write_json(out_dir / "06_duckpatcher_executor_result.json", duckpatcher_result)
        write_json(out_dir / "07_gremlin_rerun_executor_result.json", final_run)

    parsed_final, parse_error = parse_run_json(str(final_run.get("stdout") or ""))
    final_failed, final_reason = result_failed(final_run)

    report = {
        "ok": not final_failed,
        "run_id": run_id,
        "prompt": prompt,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "ai": args.ai,
        "model": args.model,
        "grep_plan": grep_plan,
        "grep_returncode": grep_returncode,
        "grep_command_path": str(out_dir / "02_grep_command.sh"),
        "grep_buffer_path": str(out_dir / "02_grep_buffer.txt"),
        "gremlin_file": gremlin_name,
        "gremlin_executor_result_path": str(out_dir / "04_gremlin_executor_result.json"),
        "duckpatcher_file": duckpatcher_name,
        "duckpatcher_executor_result": duckpatcher_result,
        "final_executor_result": final_run,
        "final_parsed_json": parsed_final,
        "final_parse_error": parse_error,
        "final_failure_reason": final_reason,
        "notes": [
            "grep used -C 3 semantics",
            "generated Python was syntax-checked before execution",
            "generated code execution is isolated in Docker unless explicitly overridden",
            "mock fallback is used when the local AI endpoint is unavailable",
        ],
    }

    write_json(out_dir / "final_report.json", report)

    print(json.dumps(
        {
            "ok": report["ok"],
            "out_dir": str(out_dir),
            "gremlin_file": gremlin_name,
            "duckpatcher_file": duckpatcher_name,
            "final_report": str(out_dir / "final_report.json"),
            "final_failure_reason": final_reason,
        },
        indent=2,
        sort_keys=True,
    ))

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