#!/usr/bin/env python3
from __future__ import annotations

# python3 .\main_computer\rag_assisted_thinking_full_smoke.py --real-ollama --model qwen3:8b --think medium

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from typing import Any, Sequence


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from main_computer.activity import ActivityBus
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider
from main_computer.providers.ollama import OllamaProvider
from main_computer.rag_harness import run_rag_harness


DEFAULT_MODEL = "gemma4:26b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_DOCKER_IMAGE = "python:3.12-slim"

BROKEN_FILE = "broken_widget/widget_status.py"
ALLOWED_WRITE_PATHS = {BROKEN_FILE}

SMOKE_PROMPT = """Use RAG-assisted thinking to fix the broken fixture repository.

The repo contains a tiny Python package and tests. The implementation is intentionally wrong.
You must use retrieved repository context to identify the expected behavior, produce a complete
replacement for the broken implementation file, and verify the result in Docker.
"""


class SmokeFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class DockerRunResult:
    ok: bool
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class FakeHarnessThinkingProvider(LLMProvider):
    """Deterministic provider for the RAG harness planning calls.

    This keeps the smoke stable. The real or fake repair provider is used later
    for the actual fix-producing AI call.
    """

    name = "fake-harness-thinking"
    model = "fake-rag-planner"

    def __init__(self, *, think: bool | str = "medium") -> None:
        self.think = think
        self.calls = 0

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        if self.calls == 1:
            payload = {
                "task_type": "rag_assisted_repair",
                "goal": "Fix the failing fixture repo by reading README, implementation, and tests.",
                "needs": [
                    "expected behavior from README",
                    "current broken implementation",
                    "failing tests",
                    "complete replacement file",
                    "Docker verification before and after",
                ],
                "retrieval_queries": [
                    "status_label expected behavior",
                    "broken_widget widget_status",
                    "test_widget_status",
                    "invalid negative count",
                    "ok colon count",
                ],
                "candidate_paths": [
                    "README.md",
                    "broken_widget/widget_status.py",
                    "tests/test_widget_status.py",
                ],
                "executor_likely_needed": True,
                "risk": "may_need_writes",
            }
        else:
            payload = {
                "type": "plan",
                "summary": (
                    "Retrieved context shows status_label(count) should return 'invalid' for negative "
                    "counts and 'ok:<count>' for zero or positive counts. The implementation file "
                    "should be replaced and verified in Docker."
                ),
                "evidence": [
                    {
                        "path": "README.md",
                        "reason": "Documents the expected public behavior.",
                    },
                    {
                        "path": "broken_widget/widget_status.py",
                        "reason": "Contains the intentionally broken implementation.",
                    },
                    {
                        "path": "tests/test_widget_status.py",
                        "reason": "Provides executable expectations for the repair.",
                    },
                ],
                "next_step": {
                    "kind": "proposal",
                    "description": "Ask the local thinking model for a complete replacement file, apply it, and run Docker tests.",
                    "requires_executor": True,
                    "requires_approval": False,
                },
                "open_questions": [],
            }

        return ChatResponse(
            content=json.dumps(payload, sort_keys=True),
            provider=self.name,
            model=self.model,
            metadata={
                "think": self.think,
                "thinking": f"FAKE_HARNESS_PRIVATE_THINKING_{self.calls}",
            },
        )


class FakeRepairThinkingProvider(LLMProvider):
    """Deterministic repair provider.

    It only succeeds when the prompt contains the RAG evidence we expect. This
    makes the smoke prove that the repair path is fed by retrieved context.
    """

    name = "fake-repair-thinking"
    model = "fake-moe-repair-model"

    def __init__(self, *, think: bool | str = "medium") -> None:
        self.think = think
        self.calls = 0

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        prompt = "\n\n".join(message.content for message in messages)

        required_terms = [
            "README.md",
            "broken_widget/widget_status.py",
            "tests/test_widget_status.py",
            "status_label",
            "ok:<count>",
            "invalid",
        ]
        missing = [term for term in required_terms if term not in prompt]
        if missing:
            payload = {
                "ok": False,
                "summary": f"RAG context incomplete; missing: {', '.join(missing)}",
                "files": [],
                "commands": [],
            }
        else:
            payload = {
                "ok": True,
                "summary": (
                    "Replace status_label with the README/test-backed behavior: negative values are "
                    "invalid; zero and positive values return ok:<count>."
                ),
                "files": [
                    {
                        "path": BROKEN_FILE,
                        "content": (
                            '"""Status helpers for the broken_widget fixture."""\n'
                            "\n"
                            "\n"
                            "def status_label(count: int) -> str:\n"
                            '    """Return the public status label for a widget count.\n'
                            "\n"
                            "    Negative counts are invalid. Zero and positive counts are reported\n"
                            "    with the stable ``ok:<count>`` label documented by the fixture README.\n"
                            '    """\n'
                            "    if count < 0:\n"
                            '        return "invalid"\n'
                            '    return f"ok:{count}"\n'
                        ),
                    }
                ],
                "commands": [
                    {
                        "kind": "docker_verify",
                        "command": "python -m unittest discover -s tests -v",
                    }
                ],
            }

        return ChatResponse(
            content=json.dumps(payload, sort_keys=True),
            provider=self.name,
            model=self.model,
            metadata={
                "think": self.think,
                "thinking": f"FAKE_REPAIR_PRIVATE_THINKING_{self.calls}",
            },
        )


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def parse_think(value: str | None) -> bool | str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return text


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def write_fixture_repo(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    (path / "broken_widget").mkdir(parents=True)
    (path / "tests").mkdir(parents=True)

    (path / "README.md").write_text(
        "# Broken Widget Fixture\n\n"
        "This repository is intentionally broken for the RAG-assisted thinking full smoke.\n\n"
        "Expected behavior:\n\n"
        "- `status_label(count)` returns `\"invalid\"` when `count` is negative.\n"
        "- `status_label(count)` returns `\"ok:<count>\"` when `count` is zero or positive.\n"
        "- The implementation should live in `broken_widget/widget_status.py`.\n\n"
        "The repair must be made as a complete replacement of the implementation file, "
        "then verified with `python -m unittest discover -s tests -v`.\n",
        encoding="utf-8",
    )

    (path / "broken_widget" / "__init__.py").write_text(
        "from .widget_status import status_label\n\n"
        "__all__ = ['status_label']\n",
        encoding="utf-8",
    )

    (path / "broken_widget" / "widget_status.py").write_text(
        '"""Status helpers for the broken_widget fixture."""\n'
        "\n"
        "\n"
        "def status_label(count: int) -> str:\n"
        '    """Return the public status label for a widget count."""\n'
        "    # BUG: this implementation ignores the documented ok:<count> format\n"
        "    # and incorrectly treats zero as invalid.\n"
        "    if count <= 0:\n"
        '        return "invalid"\n'
        '    return "ok"\n',
        encoding="utf-8",
    )

    (path / "tests" / "test_widget_status.py").write_text(
        "import unittest\n"
        "\n"
        "from broken_widget.widget_status import status_label\n"
        "\n"
        "\n"
        "class WidgetStatusTests(unittest.TestCase):\n"
        "    def test_negative_count_is_invalid(self):\n"
        '        self.assertEqual(status_label(-1), "invalid")\n'
        "\n"
        "    def test_zero_count_is_ok_with_count(self):\n"
        '        self.assertEqual(status_label(0), "ok:0")\n'
        "\n"
        "    def test_positive_count_is_ok_with_count(self):\n"
        '        self.assertEqual(status_label(7), "ok:7")\n'
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )

    return path


def event(
    bus: ActivityBus,
    *,
    run_id: str,
    title: str,
    message: str = "",
    source: str = "rag-assisted-thinking-full-smoke",
    kind: str = "ai",
    status: str = "completed",
    severity: str = "info",
    tags: list[str] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(data or {})
    payload.setdefault("run_id", run_id)
    return bus.record(
        source=source,
        kind=kind,
        time_model="parallel",
        severity=severity,
        title=title,
        message=message,
        status=status,
        tags=tags or ["rag", "thinking", "local-ai"],
        data=payload,
    )


def run_docker_unittest(
    *,
    repo_dir: Path,
    run_id: str,
    bus: ActivityBus,
    image: str,
    label: str,
    timeout_s: int = 120,
) -> DockerRunResult:
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{str(repo_dir.resolve())}:/workspace",
        "-w",
        "/workspace",
        image,
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    ]

    event(
        bus,
        run_id=run_id,
        title=f"Docker verification started: {label}",
        message="Running fixture unittest suite in Docker.",
        source="executor",
        kind="subprocess",
        status="running",
        tags=["rag", "thinking", "docker", "executor", "subprocess"],
        data={
            "phase": label,
            "image": image,
            "command_preview": " ".join(command),
        },
    )

    try:
        completed = subprocess.run(
            command,
            cwd=str(repo_dir),
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        result = DockerRunResult(
            ok=False,
            command=command,
            returncode=127,
            stdout="",
            stderr="Docker executable was not found on PATH.",
        )
        event(
            bus,
            run_id=run_id,
            title=f"Docker verification failed: {label}",
            message=result.stderr,
            source="executor",
            kind="subprocess",
            status="failed",
            severity="error",
            tags=["rag", "thinking", "docker", "executor", "subprocess"],
            data={"phase": label, "returncode": result.returncode},
        )
        raise SmokeFailure(result.stderr) from exc
    except subprocess.TimeoutExpired as exc:
        result = DockerRunResult(
            ok=False,
            command=command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or f"Docker verification timed out after {timeout_s}s.",
        )
    else:
        result = DockerRunResult(
            ok=completed.returncode == 0,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    event(
        bus,
        run_id=run_id,
        title=(
            f"Docker verification completed: {label}"
            if result.ok
            else f"Docker verification failed: {label}"
        ),
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
        },
    )

    return result


def extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise SmokeFailure("AI response was empty.")

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
        raise SmokeFailure("AI response did not contain a JSON object.")

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
                candidate = raw[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise SmokeFailure(f"Could not parse AI JSON object: {exc}") from exc
                if not isinstance(parsed, dict):
                    raise SmokeFailure("AI JSON payload was not an object.")
                return parsed

    raise SmokeFailure("AI response contained an unterminated JSON object.")


def safe_repo_path(repo_dir: Path, rel_path: str) -> Path:
    raw = str(rel_path or "").replace("\\", "/").strip()
    if not raw:
        raise SmokeFailure("AI proposed an empty path.")

    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        raise SmokeFailure(f"AI proposed unsafe path: {rel_path!r}")

    if raw not in ALLOWED_WRITE_PATHS:
        raise SmokeFailure(
            f"AI proposed path {raw!r}, but this smoke only allows: {sorted(ALLOWED_WRITE_PATHS)}"
        )

    target = (repo_dir / raw).resolve()
    repo_resolved = repo_dir.resolve()
    try:
        target.relative_to(repo_resolved)
    except ValueError as exc:
        raise SmokeFailure(f"AI proposed path outside fixture repo: {rel_path!r}") from exc

    return target


def get_retrieved_paths(result: Any) -> list[str]:
    paths: list[str] = []
    retrieval = getattr(result, "retrieval", None)
    candidates = getattr(retrieval, "candidates", []) if retrieval is not None else []
    for candidate in candidates:
        path = str(getattr(candidate, "path", "") or "").replace("\\", "/")
        if path and path not in paths:
            paths.append(path)
    return paths


def read_context_files(repo_dir: Path, paths: list[str]) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    for rel_path in paths:
        safe = rel_path.replace("\\", "/").strip()
        if not safe or PurePosixPath(safe).is_absolute() or ".." in PurePosixPath(safe).parts:
            continue
        path = repo_dir / safe
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        context.append(
            {
                "path": safe,
                "content": content,
            }
        )
    return context


def build_repair_messages(
    *,
    prompt: str,
    rag_result: Any,
    retrieved_context: list[dict[str, Any]],
    failing_docker: DockerRunResult,
) -> list[ChatMessage]:
    rag_summary = {
        "run_id": getattr(rag_result, "run_id", ""),
        "ok": getattr(rag_result, "ok", None),
        "final_plan": getattr(rag_result, "final_plan", {}),
        "retrieved_paths": [item["path"] for item in retrieved_context],
    }

    payload = {
        "task": "Produce a complete replacement file that fixes the fixture repository.",
        "user_prompt": prompt,
        "rules": [
            "Return JSON only.",
            f"Only write this path: {BROKEN_FILE}",
            "Use the supplied RAG context and failing Docker output.",
            "Do not claim the final tests pass; the smoke runner will verify after applying the file.",
            "The file content must be a complete replacement, not a diff.",
        ],
        "required_output_schema": {
            "ok": True,
            "summary": "brief explanation",
            "files": [
                {
                    "path": BROKEN_FILE,
                    "content": "complete replacement source text",
                }
            ],
            "commands": [
                {
                    "kind": "docker_verify",
                    "command": "python -m unittest discover -s tests -v",
                }
            ],
        },
        "rag_summary": rag_summary,
        "rag_context": retrieved_context,
        "failing_docker_result": failing_docker.as_dict(),
        "expected_behavior_hint": "README says negative -> invalid, zero/positive -> ok:<count>.",
    }

    system = (
        "You are Main Computer's RAG-assisted thinking repair engine. "
        "Use repository context and command observations to produce safe complete-file replacements. "
        "Return JSON only. Do not use markdown fences."
    )

    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=json_dumps(payload)),
    ]


def make_repair_provider(args: argparse.Namespace) -> LLMProvider:
    think = parse_think(args.think)
    if args.real_ollama:
        return OllamaProvider(
            model=args.model,
            base_url=args.base_url,
            timeout_s=float(args.ollama_timeout_s),
            think=think,
            fallback=False,
        )
    return FakeRepairThinkingProvider(think=think if think is not None else "medium")


def apply_repair(
    *,
    repo_dir: Path,
    run_id: str,
    bus: ActivityBus,
    repair_payload: dict[str, Any],
) -> list[str]:
    if repair_payload.get("ok") is False:
        raise SmokeFailure(f"AI repair payload reported ok=false: {repair_payload.get('summary', '')}")

    files = repair_payload.get("files")
    if not isinstance(files, list) or not files:
        raise SmokeFailure("AI repair payload did not include any replacement files.")

    written: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            raise SmokeFailure("AI repair file entry was not an object.")
        rel_path = str(item.get("path") or "")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise SmokeFailure(f"AI repair for {rel_path!r} did not include source content.")

        target = safe_repo_path(repo_dir, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel_path.replace("\\", "/"))

    event(
        bus,
        run_id=run_id,
        title="AI repair applied",
        message=f"Wrote {len(written)} replacement file(s).",
        source="rag-assisted-thinking-full-smoke",
        kind="ai",
        status="completed",
        tags=["rag", "thinking", "local-ai", "repair"],
        data={
            "written_paths": written,
        },
    )

    return written


def raw_thinking_leaked(events: list[dict[str, Any]]) -> bool:
    serialized = json.dumps(events, sort_keys=True, default=str)
    private_markers = [
        "FAKE_HARNESS_PRIVATE_THINKING",
        "FAKE_REPAIR_PRIVATE_THINKING",
        "PRIVATE_THINKING",
        "message.thinking",
    ]
    return any(marker in serialized for marker in private_markers)


def activity_summary(bus: ActivityBus, *, limit: int = 300) -> dict[str, Any]:
    live = bus.events(filter_id="live", limit=limit)
    rag = bus.events(filter_id="rag", limit=limit)
    thinking = bus.events(filter_id="thinking", limit=limit)
    docker = bus.events(filter_id="docker", limit=limit)
    ai = bus.events(filter_id="ai", limit=limit)

    return {
        "live_count": len(live),
        "rag_count": len(rag),
        "thinking_count": len(thinking),
        "docker_count": len(docker),
        "ai_count": len(ai),
        "raw_thinking_leaked": raw_thinking_leaked(live),
        "events_preview": [
            {
                "source": event.get("source"),
                "title": event.get("title"),
                "status": event.get("status"),
                "severity": event.get("severity"),
                "tags": event.get("tags"),
                "data": {
                    key: value
                    for key, value in (event.get("data") or {}).items()
                    if key
                    in {
                        "run_id",
                        "step",
                        "stage",
                        "phase",
                        "provider",
                        "model",
                        "think",
                        "written_paths",
                        "returncode",
                    }
                },
            }
            for event in live[:32]
        ],
    }


def run_full_smoke(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or f"rag_assisted_thinking_full_smoke_{utc_stamp()}"
    project_root = Path(args.project_root).resolve() if args.project_root else repo_root_from_script()
    smoke_root = project_root / "diagnostics_output" / "rag_assisted_thinking_full_smoke" / run_id
    repo_dir = write_fixture_repo(smoke_root / "fixture_repo")
    output_root = smoke_root / "rag_runs"

    bus = ActivityBus(repo_dir)

    event(
        bus,
        run_id=run_id,
        title="RAG-assisted thinking full smoke started",
        message="Creating broken fixture repo, running RAG, asking AI for a fix, and verifying in Docker.",
        status="running",
        data={
            "repo_dir": str(repo_dir),
            "real_ollama": bool(args.real_ollama),
            "docker_image": args.docker_image,
        },
    )

    before = run_docker_unittest(
        repo_dir=repo_dir,
        run_id=run_id,
        bus=bus,
        image=args.docker_image,
        label="before_repair",
        timeout_s=int(args.docker_timeout_s),
    )

    if before.ok:
        raise SmokeFailure("Fixture unexpectedly passed before repair; smoke problem is not broken.")

    harness_provider = FakeHarnessThinkingProvider(think=parse_think(args.think) or "medium")

    rag_result = run_rag_harness(
        prompt=SMOKE_PROMPT,
        repo_dir=repo_dir,
        queries=[
            "status_label expected behavior",
            "broken_widget widget_status",
            "test_widget_status",
            "ok:<count>",
            "invalid negative count",
        ],
        output_root=output_root,
        max_context_chars=int(args.max_context_chars),
        max_candidates=24,
        max_chunks=12,
        use_model=True,
        provider=harness_provider,
        run_id=run_id,
        activity_bus=bus,
    )

    if not getattr(rag_result, "ok", False):
        raise SmokeFailure("RAG harness did not complete successfully.")

    retrieved_paths = get_retrieved_paths(rag_result)
    retrieved_context = read_context_files(repo_dir, retrieved_paths)

    required_context = {"README.md", "broken_widget/widget_status.py", "tests/test_widget_status.py"}
    retrieved_set = {item["path"] for item in retrieved_context}
    missing_context = sorted(required_context - retrieved_set)
    if missing_context:
        raise SmokeFailure(f"RAG did not retrieve required context files: {missing_context}")

    repair_provider = make_repair_provider(args)
    repair_messages = build_repair_messages(
        prompt=SMOKE_PROMPT,
        rag_result=rag_result,
        retrieved_context=retrieved_context,
        failing_docker=before,
    )

    event(
        bus,
        run_id=run_id,
        title="Local AI repair call started",
        message="Asking thinking provider for complete replacement file from RAG context.",
        source="local-ai",
        kind="ai",
        status="running",
        tags=["rag", "thinking", "local-ai", "ollama", "repair"],
        data={
            "provider": getattr(repair_provider, "name", repair_provider.__class__.__name__),
            "model": getattr(repair_provider, "model", ""),
            "think": parse_think(args.think),
            "prompt_chars": sum(len(message.content) for message in repair_messages),
            "raw_thinking_exposed": False,
        },
    )

    repair_response = repair_provider.chat(repair_messages)
    repair_payload = extract_json_object(repair_response.content)

    event(
        bus,
        run_id=run_id,
        title="Local AI repair call completed",
        message="Thinking provider returned a proposed replacement-file payload.",
        source="local-ai",
        kind="ai",
        status="completed",
        tags=["rag", "thinking", "local-ai", "ollama", "repair"],
        data={
            "provider": repair_response.provider,
            "model": repair_response.model,
            "think": parse_think(args.think),
            "response_chars": len(repair_response.content),
            "raw_thinking_exposed": False,
            "proposed_paths": [
                item.get("path")
                for item in repair_payload.get("files", [])
                if isinstance(item, dict)
            ],
        },
    )

    written_paths = apply_repair(
        repo_dir=repo_dir,
        run_id=run_id,
        bus=bus,
        repair_payload=repair_payload,
    )

    after = run_docker_unittest(
        repo_dir=repo_dir,
        run_id=run_id,
        bus=bus,
        image=args.docker_image,
        label="after_repair",
        timeout_s=int(args.docker_timeout_s),
    )

    if not after.ok:
        raise SmokeFailure("Docker verification still failed after applying the AI repair.")

    live_events = bus.events(filter_id="live", limit=300)
    if raw_thinking_leaked(live_events):
        raise SmokeFailure("Raw thinking leaked into Activity Monitor events.")

    event(
        bus,
        run_id=run_id,
        title="RAG-assisted thinking full smoke completed",
        message="The fixture failed before repair, AI produced a RAG-grounded replacement, and Docker passed after repair.",
        status="completed",
        data={
            "written_paths": written_paths,
            "before_returncode": before.returncode,
            "after_returncode": after.returncode,
        },
    )

    report = {
        "ok": True,
        "run_id": run_id,
        "mode": "rag_assisted_thinking_full_smoke",
        "real_ollama": bool(args.real_ollama),
        "repo_dir": str(repo_dir),
        "output_root": str(output_root),
        "docker_image": args.docker_image,
        "before": before.as_dict(),
        "after": after.as_dict(),
        "retrieved_paths": retrieved_paths,
        "retrieved_context_paths": [item["path"] for item in retrieved_context],
        "written_paths": written_paths,
        "repair_provider": {
            "provider": repair_response.provider,
            "model": repair_response.model,
            "think": parse_think(args.think),
        },
        "repair_payload_summary": {
            "ok": repair_payload.get("ok"),
            "summary": repair_payload.get("summary"),
            "paths": [
                item.get("path")
                for item in repair_payload.get("files", [])
                if isinstance(item, dict)
            ],
        },
        "activity": activity_summary(bus),
    }

    report_path = smoke_root / "rag_assisted_thinking_full_smoke_report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json_dumps(report) + "\n", encoding="utf-8")

    return report


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Full RAG-assisted thinking smoke: create a broken fixture repo, prove failure in Docker, "
            "use RAG context plus a thinking AI call to produce a fix, apply it, and prove success in Docker."
        )
    )
    parser.add_argument("--project-root", default="", help="Defaults to this repository root.")
    parser.add_argument("--run-id", default="", help="Defaults to rag_assisted_thinking_full_smoke_<timestamp>.")
    parser.add_argument("--real-ollama", action="store_true", default=env_flag("MAIN_COMPUTER_RAG_FULL_SMOKE_REAL_OLLAMA", False))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--think", default=os.environ.get("MAIN_COMPUTER_OLLAMA_THINK", "medium"))
    parser.add_argument("--ollama-timeout-s", type=float, default=float(os.environ.get("MAIN_COMPUTER_OLLAMA_TIMEOUT_S", "600")))
    parser.add_argument("--docker-image", default=os.environ.get("MAIN_COMPUTER_RAG_FULL_SMOKE_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE))
    parser.add_argument("--docker-timeout-s", type=int, default=int(os.environ.get("MAIN_COMPUTER_RAG_FULL_SMOKE_DOCKER_TIMEOUT_S", "180")))
    parser.add_argument("--max-context-chars", type=int, default=30_000)
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))

    try:
        report = run_full_smoke(args)
    except Exception as exc:
        print(f"RAG-assisted thinking full smoke: FAIL", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json_dumps(report))
    else:
        print("RAG-assisted thinking full smoke: PASS")
        print(f"Run id: {report['run_id']}")
        print(f"Mode: {report['mode']}")
        print(f"Repo: {report['repo_dir']}")
        print(f"Report: {report['report_path']}")
        print(f"AI: {report['repair_provider']['provider']} / {report['repair_provider']['model']} think={report['repair_provider']['think']}")
        print(f"Docker image: {report['docker_image']}")
        print(f"Before repair returncode: {report['before']['returncode']}")
        print(f"After repair returncode: {report['after']['returncode']}")
        print(f"Retrieved context: {', '.join(report['retrieved_context_paths'])}")
        print(f"Written paths: {', '.join(report['written_paths'])}")
        print(
            "Activity: "
            f"{report['activity']['rag_count']} RAG, "
            f"{report['activity']['thinking_count']} thinking, "
            f"{report['activity']['docker_count']} Docker, "
            f"{report['activity']['ai_count']} AI"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())