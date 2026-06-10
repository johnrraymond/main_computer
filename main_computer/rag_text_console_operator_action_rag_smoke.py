#!/usr/bin/env python3
"""
Text-console operator action-RAG smoke.

Purpose
-------
Prove the generalized text-console operator pathway before another UI patch:

    runtime user prompt
    -> TextConsoleConfig rooted at the repo/current directory
    -> action-spec preflight chooses relevant capability docs
    -> final model call receives only those retrieved specs
    -> assistant may emit normal prose, ```computer mounts, and/or ```repo-edit handoffs
    -> validators check every emitted artifact without executing or applying anything

This smoke intentionally does not special-case a single prompt.  It runs a small
fixture matrix covering answer-only, mount-only, edit-only, and combined
mount+edit requests.  The action knowledge comes from files in
main_computer/action_specs/*.md, not from one hard-coded final prompt.

Run from the repository root:

    python -S main_computer/rag_text_console_operator_action_rag_smoke.py

The live smoke touches Ollama by default.  Use --offline-contract-only only for
parser/validator development; it does not prove model behavior.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma4:26b"
DEFAULT_TIMEOUT = 120.0
DEFAULT_REPORT_PATH = "diagnostics_output/rag_text_console_operator_action_rag_smoke_report.json"
ACTION_SPEC_DIR = Path("main_computer") / "action_specs"

REPO_EDIT_FENCE_RE = re.compile(
    r"```[ \t]*(?:repo-edit|repo_edit)[^\n]*\n(?P<body>.*?)\n?[ \t]*```",
    re.IGNORECASE | re.DOTALL,
)

ACTION_PREFLIGHT_PROMPT = """\
You are the Main Computer text-console action-context preflight.

Your job is to decide which available action specs are relevant to the user's
request. You do not execute commands. You do not create edits. You do not emit
/act lines. You only choose context for a later assistant call.

Return JSON only, with this exact shape:

{
  "needs_mount": false,
  "needs_edit": false,
  "needs_answer_only": true,
  "selected_spec_ids": [],
  "reason": "<brief reason>"
}

Rules:
- Select only spec ids that appear in the available action spec catalog.
- Select terminal when the user asks for Terminal, shell commands, Git, tests,
  file listings, directory listings, command execution, interruption, or active
  terminal reuse.
- Select repo_edit when the user asks to edit, modify, update, create, delete,
  refactor, patch, or otherwise change repository files.
- Select both terminal and repo_edit when the user asks to inspect/run/test and
  prepare an edit in the same request.
- Select no specs for greetings, ordinary explanations, and workspace questions
  that do not ask for a local action or edit.
- Do not infer hidden intent. If the user asks you to explain a command without
  using it, do not select a mount spec.
"""

FINAL_OPERATOR_PROMPT = """\
You are the Main Computer text-console operator.

The previous action-context preflight selected the capability specs provided
below. Treat those specs as the only executable/edit affordance context for this
answer.

Rules:
- It is valid to answer with normal assistant prose only when no action or edit
  is requested.
- If a Terminal preview is requested, use a fenced block tagged exactly
  computer and put only exact /act lines inside it.
- If a repo edit is requested, use a fenced block tagged exactly repo-edit and
  put exactly one JSON object inside it.
- If the user asks for both a mount and an edit, include both blocks in one
  assistant message.
- Preview mounts are not execution. Repo-edit handoffs are not applied edits.
- Do not claim commands ran, files changed, tests passed, or commits happened.
- Do not invent capabilities that were not selected by the preflight.
"""


@dataclass(frozen=True)
class ActionSpec:
    spec_id: str
    app_id: str
    title: str
    keywords: tuple[str, ...]
    output_kinds: tuple[str, ...]
    path: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def runtime_context_text(self) -> str:
        return extract_markdown_section(self.text, "Runtime prompt") or compact_action_spec_text(self.text)

    @property
    def runtime_sha256(self) -> str:
        return hashlib.sha256(self.runtime_context_text.encode("utf-8")).hexdigest()

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "app_id": self.app_id,
            "title": self.title,
            "keywords": list(self.keywords),
            "output_kinds": list(self.output_kinds),
            "path": self.path,
            "sha256": self.sha256,
            "runtime_sha256": self.runtime_sha256,
        }


@dataclass(frozen=True)
class OperatorFixture:
    label: str
    prompt: str
    expected_spec_ids: tuple[str, ...]
    expected_mount_commands: tuple[str, ...] = ()
    expect_repo_edit: bool = False
    required_editor_terms: tuple[str, ...] = ()


def add_repo_to_path(root: Path) -> None:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def repo_root() -> Path:
    return Path.cwd().resolve()


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def one_line(text: str, *, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact if len(compact) <= limit else compact[: max(0, limit - 1)] + "…"


def parse_boolish(value: str | bool | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    if text in {"omit", "none", "null", ""}:
        return None
    raise argparse.ArgumentTypeError(f"Expected true/false/omit for think, got {value!r}")


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    raw = str(text or "")
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end < 0:
        return {}, raw
    header = raw[4:end].strip()
    body = raw[end + len("\n---") :].lstrip("\r\n")
    meta: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, body


def split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def extract_markdown_section(text: str, heading: str) -> str:
    """Return one markdown section body by heading name.

    Action specs may be long human-readable docs. The final model call should
    receive the concise runtime section for selected specs, not every doc line.
    """

    wanted = str(heading or "").strip().lower()
    current: list[str] = []
    in_section = False
    heading_re = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
    for line in str(text or "").splitlines():
        match = heading_re.match(line)
        if match:
            title = match.group(2).strip().lower()
            if in_section:
                break
            in_section = title == wanted
            continue
        if in_section:
            current.append(line)
    return "\n".join(current).strip()


def compact_action_spec_text(text: str, *, limit: int = 1200) -> str:
    """Fallback runtime prompt for specs that have not grown a Runtime prompt.

    This keeps the smoke generalized while protecting the model call from full
    docs or accidental giant specs.
    """

    _meta, body = parse_front_matter(text)
    body = re.sub(r"```[^`]*```", lambda m: m.group(0)[:400], body, flags=re.DOTALL)
    compact = re.sub(r"\n{3,}", "\n\n", body).strip()
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "\n..."


def load_action_specs(root: Path) -> dict[str, ActionSpec]:
    spec_dir = root / ACTION_SPEC_DIR
    if not spec_dir.exists():
        raise RuntimeError(f"Action spec directory does not exist: {spec_dir}")

    specs: dict[str, ActionSpec] = {}
    for path in sorted(spec_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, _body = parse_front_matter(text)
        spec_id = str(meta.get("spec_id") or path.stem).strip()
        app_id = str(meta.get("app_id") or spec_id).strip()
        title = str(meta.get("title") or spec_id).strip()
        spec = ActionSpec(
            spec_id=spec_id,
            app_id=app_id,
            title=title,
            keywords=split_csv(meta.get("keywords", "")),
            output_kinds=split_csv(meta.get("output_kinds", "")),
            path=path.relative_to(root).as_posix(),
            text=text,
        )
        if spec.spec_id in specs:
            raise RuntimeError(f"Duplicate action spec id {spec.spec_id!r}: {path}")
        specs[spec.spec_id] = spec

    if not specs:
        raise RuntimeError(f"No action specs found in {spec_dir}")
    return specs


def action_spec_catalog_prompt(specs: dict[str, ActionSpec]) -> str:
    return (
        "Available text-console action spec catalog:\n\n"
        + json.dumps([spec.catalog_entry() for spec in specs.values()], indent=2, ensure_ascii=False, sort_keys=True)
    )


def selected_action_specs_prompt(specs: dict[str, ActionSpec], selected_spec_ids: list[str]) -> str:
    chunks: list[str] = []
    for spec_id in selected_spec_ids:
        spec = specs[spec_id]
        chunks.append(
            f"Selected text-console action spec: {spec.spec_id}\n"
            f"title: {spec.title}\n"
            f"path: {spec.path}\n"
            f"spec_sha256: {spec.sha256}\n"
            f"runtime_sha256: {spec.runtime_sha256}\n\n"
            f"{spec.runtime_context_text.strip()}"
        )
    if not chunks:
        return "No action specs were selected. Answer normally without computer or repo-edit blocks unless the user corrects the request."
    return "\n\n---\n\n".join(chunks)


def strip_json_code_fence(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def parse_jsonish(text: str) -> dict[str, Any]:
    raw = strip_json_code_fence(text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    return payload


def build_base_model_input(*, root: Path, request_text: str, base_url: str, model: str, timeout: float, think: bool | str | None):
    add_repo_to_path(root)
    from main_computer.text_console import TextConsoleConfig, build_text_console_model_input

    config = TextConsoleConfig.from_repo_root(
        base_url=base_url,
        model=model,
        timeout=timeout,
        think=think,
    )
    failures = config.validate_repo_root()
    model_input = build_text_console_model_input(
        text_console_config=config,
        source=request_text,
    )
    return config, model_input, failures


def compact_context_pack_text(text: str, *, max_chars: int = 2200, max_manifest_lines: int = 80) -> str:
    """Compact repo context for operator action generation.

    The operator smoke is testing action-spec retrieval and validated output
    forms. Full file excerpts and giant manifests are useful for answering some
    workspace questions, but they are the wrong payload for deciding whether to
    emit terminal/repo-edit handoffs. Keep root/project grounding and a bounded
    manifest preview so the model stays below context limits.
    """

    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw

    out: list[str] = []
    manifest_lines = 0
    in_manifest = False
    dropped_sections = {
        "Matched file excerpts:",
        "Pinned guidance excerpts:",
    }

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped in dropped_sections:
            out.append(f"{stripped} [omitted from operator action-RAG compact context]")
            break

        if stripped == "Main computer file manifest:":
            in_manifest = True
            out.append(line)
            continue

        if stripped.endswith(":") and stripped != "Main computer file manifest:":
            in_manifest = False

        if in_manifest and line.startswith("  - "):
            manifest_lines += 1
            if manifest_lines > max_manifest_lines:
                out.append(f"  - ... [{manifest_lines - 1}+ manifest entries truncated]")
                break

        out.append(line)
        if len("\n".join(out)) >= max_chars:
            out.append("... [context truncated for operator action-RAG smoke]")
            break

    compact = "\n".join(out).strip()
    return compact if compact else raw[:max_chars].rstrip() + "\n... [context truncated for operator action-RAG smoke]"


def compact_model_messages_for_operator(model_input: Any) -> list[Any]:
    from main_computer.models import ChatMessage

    messages = list(getattr(model_input, "messages", []) or [])
    context_text = str(getattr(getattr(model_input, "context_pack", None), "text", "") or "")
    compact_context = compact_context_pack_text(context_text)
    compacted: list[Any] = []
    replaced_context = False
    for index, message in enumerate(messages):
        role = str(getattr(message, "role", "") or "")
        if not replaced_context and index == 1 and role == "system":
            compacted.append(ChatMessage(role="system", content=compact_context))
            replaced_context = True
        else:
            compacted.append(message)
    return compacted


def build_preflight_messages(*, model_input: Any, specs: dict[str, ActionSpec], request_text: str) -> list[Any]:
    from main_computer.models import ChatMessage

    config = getattr(model_input, "text_console_config", None)
    root_hint = ""
    if config is not None:
        root_hint = (
            "Text-console runtime root hint:\n"
            f"- current_directory: {getattr(config, 'current_directory', '')}\n"
            f"- context_root: {getattr(config, 'context_root', '')}\n"
            f"- working_directory: {getattr(config, 'working_directory', '')}"
        )

    messages = [
        ChatMessage(role="system", content=ACTION_PREFLIGHT_PROMPT),
        ChatMessage(role="system", content=action_spec_catalog_prompt(specs)),
    ]
    if root_hint:
        messages.append(ChatMessage(role="system", content=root_hint))
    messages.append(ChatMessage(role="user", content=request_text))
    return messages


def build_final_messages(*, model_input: Any, specs: dict[str, ActionSpec], selected_spec_ids: list[str]) -> list[Any]:
    from main_computer.models import ChatMessage

    base_messages = compact_model_messages_for_operator(model_input)
    action_context = selected_action_specs_prompt(specs, selected_spec_ids)
    inserted = [
        ChatMessage(role="system", content=FINAL_OPERATOR_PROMPT),
        ChatMessage(role="system", content=action_context),
    ]
    if base_messages and str(getattr(base_messages[-1], "role", "")) == "user":
        return [*base_messages[:-1], *inserted, base_messages[-1]]
    return [*base_messages, *inserted]


def call_provider_chat(model_input: Any, messages: list[Any]) -> dict[str, Any]:
    started = time.monotonic()
    provider = getattr(model_input.computer, "provider", None)
    if provider is None or not hasattr(provider, "chat"):
        raise RuntimeError("Text-console model input does not expose a chat provider.")
    response = provider.chat(messages)
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "content": str(getattr(response, "content", "") or ""),
        "provider": str(getattr(response, "provider", getattr(provider, "name", "")) or ""),
        "model": str(getattr(response, "model", getattr(provider, "model", "")) or ""),
        "metadata": dict(getattr(response, "metadata", {}) or {}),
        "duration_ms": duration_ms,
    }


def validate_preflight_payload(
    payload: dict[str, Any],
    *,
    available_spec_ids: set[str],
    expected_spec_ids: tuple[str, ...],
) -> dict[str, Any]:
    failures: list[str] = []
    selected = payload.get("selected_spec_ids")
    if not isinstance(selected, list):
        selected_ids: list[str] = []
        failures.append("preflight selected_spec_ids is not a list")
    else:
        selected_ids = [str(item) for item in selected]

    unknown = sorted(set(selected_ids) - available_spec_ids)
    if unknown:
        failures.append(f"preflight selected unknown specs: {unknown}")

    expected = list(expected_spec_ids)
    expected_set = set(expected)
    selected_set = set(selected_ids)
    if selected_set != expected_set:
        failures.append(f"expected selected_spec_ids set {sorted(expected_set)!r}, got {sorted(selected_set)!r}")

    payload_needs_mount = bool(payload.get("needs_mount"))
    payload_needs_edit = bool(payload.get("needs_edit"))
    payload_needs_answer_only = bool(payload.get("needs_answer_only"))

    derived_needs_mount = "terminal" in selected_set
    derived_needs_edit = "repo_edit" in selected_set
    derived_needs_answer_only = not derived_needs_mount and not derived_needs_edit

    boolean_notes: list[str] = []
    if payload_needs_mount != derived_needs_mount:
        boolean_notes.append(
            f"payload needs_mount={payload_needs_mount} disagrees with selected specs; "
            f"using derived value {derived_needs_mount}"
        )
    if payload_needs_edit != derived_needs_edit:
        boolean_notes.append(
            f"payload needs_edit={payload_needs_edit} disagrees with selected specs; "
            f"using derived value {derived_needs_edit}"
        )
    if payload_needs_answer_only != derived_needs_answer_only:
        boolean_notes.append(
            f"payload needs_answer_only={payload_needs_answer_only} disagrees with selected specs; "
            f"using derived value {derived_needs_answer_only}"
        )

    return {
        "ok": not failures,
        "selected_spec_ids": selected_ids,
        "expected_spec_ids": expected,
        "failures": failures,
        "boolean_notes": boolean_notes,
        "derived": {
            "needs_mount": derived_needs_mount,
            "needs_edit": derived_needs_edit,
            "needs_answer_only": derived_needs_answer_only,
        },
        "payload_needs": {
            "needs_mount": payload_needs_mount,
            "needs_edit": payload_needs_edit,
            "needs_answer_only": payload_needs_answer_only,
        },
        "payload": payload,
    }


def extract_repo_edit_blocks(message: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, match in enumerate(REPO_EDIT_FENCE_RE.finditer(str(message or "")), start=1):
        body = match.group("body").strip()
        parsed: dict[str, Any] | None = None
        parse_error = ""
        try:
            parsed = parse_jsonish(body)
        except Exception as exc:
            parse_error = repr(exc)
        blocks.append(
            {
                "index": index,
                "body": body,
                "parsed": parsed,
                "parse_error": parse_error,
                "sourceRange": {"start": match.start(), "end": match.end()},
                "leadingText": message[: match.start()],
                "trailingText": message[match.end() :],
            }
        )
    return blocks


def substantive_terms(text: str) -> set[str]:
    stop = {
        "that", "this", "with", "from", "into", "about", "request", "prepare", "please",
        "then", "using", "current", "repo", "root", "files", "file", "make", "edit",
        "update", "change", "create", "delete", "modify", "text", "console",
    }
    return {
        term.lower()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", str(text or ""))
        if term.lower() not in stop
    }


def validate_repo_edit_blocks(
    blocks: list[dict[str, Any]],
    *,
    expect_repo_edit: bool,
    user_prompt: str,
    required_terms: tuple[str, ...],
) -> dict[str, Any]:
    failures: list[str] = []
    if expect_repo_edit and not blocks:
        failures.append("expected a repo-edit handoff block but found none")
    if not expect_repo_edit and blocks:
        failures.append("did not expect a repo-edit handoff block")

    reports: list[dict[str, Any]] = []
    for block in blocks:
        report: dict[str, Any] = {
            "index": block.get("index"),
            "ok": False,
            "failures": [],
            "parse_error": block.get("parse_error") or "",
            "parsed": block.get("parsed"),
        }
        payload = block.get("parsed")
        raw_body = str(block.get("body") or "")
        if re.search(r"[A-Za-z]:\\", raw_body) or "\\Users\\" in raw_body:
            report["failures"].append("repo-edit block contains an absolute Windows path")
        if block.get("parse_error"):
            report["failures"].append(f"repo-edit JSON parse failed: {block.get('parse_error')}")
        if not isinstance(payload, dict):
            report["failures"].append("repo-edit block did not parse to an object")
        else:
            if payload.get("mode") != "repo_root_edit_request":
                report["failures"].append("repo-edit mode is not repo_root_edit_request")
            if payload.get("target_root") != "repo-root":
                report["failures"].append(f"repo-edit target_root must be repo-root, got {payload.get('target_root')!r}")
            if payload.get("requires_confirmation") is not True:
                report["failures"].append("repo-edit requires_confirmation must be true")
            blocked = payload.get("blocked_reasons")
            if blocked != []:
                report["failures"].append(f"repo-edit blocked_reasons must be [], got {blocked!r}")
            request = str(payload.get("request_for_editor") or "").strip()
            if not request:
                report["failures"].append("repo-edit request_for_editor is empty")
            if re.search(r"[A-Za-z]:\\", request) or "\\Users\\" in request:
                report["failures"].append("repo-edit request_for_editor contains an absolute Windows path")
            prompt_terms = substantive_terms(user_prompt)
            request_terms = substantive_terms(request)
            if prompt_terms and not (prompt_terms & request_terms):
                report["failures"].append("repo-edit request_for_editor shares no substantive terms with user prompt")
            missing_terms = [term for term in required_terms if term.lower() not in request.lower()]
            if missing_terms:
                report["failures"].append(f"repo-edit request_for_editor missing required terms: {missing_terms}")

        report["ok"] = not report["failures"]
        if report["failures"]:
            failures.append(f"repo-edit block {block.get('index')}: " + "; ".join(report["failures"]))
        reports.append(report)

    return {
        "ok": not failures,
        "count": len(blocks),
        "failures": failures,
        "reports": reports,
    }


def parse_and_validate_mounts(
    message: str,
    *,
    expected_mount_commands: tuple[str, ...],
) -> dict[str, Any]:
    from main_computer.rag_text_console_control_surface_smoke import (
        parse_assistant_control_message,
        validate_text_console_mount_commands,
    )

    parse_error = ""
    parsed_payload: dict[str, Any] | None = None
    try:
        parsed_payload = parse_assistant_control_message(message)
    except Exception as exc:
        parse_error = str(exc)

    if expected_mount_commands:
        if parsed_payload is None:
            return {
                "ok": False,
                "parse_error": parse_error,
                "validation": {
                    "ok": False,
                    "commands": [],
                    "expected_commands": list(expected_mount_commands),
                    "failures": [parse_error or "expected a computer mount but none parsed"],
                },
            }
        validation = validate_text_console_mount_commands(
            parsed_payload,
            expected_commands=list(expected_mount_commands),
        )
        return {
            "ok": bool(validation.get("ok")),
            "parse_error": parse_error,
            "parsed_payload": parsed_payload,
            "validation": validation,
        }

    if parsed_payload is not None:
        validation = validate_text_console_mount_commands(parsed_payload, expected_commands=[])
        return {
            "ok": False,
            "parse_error": "",
            "parsed_payload": parsed_payload,
            "validation": {
                **validation,
                "ok": False,
                "failures": [*list(validation.get("failures") or []), "did not expect a computer mount"],
            },
        }

    return {
        "ok": True,
        "parse_error": parse_error,
        "validation": {
            "ok": True,
            "commands": [],
            "expected_commands": [],
            "failures": [],
        },
    }


def default_fixtures() -> list[OperatorFixture]:
    return [
        OperatorFixture(
            label="answer only greeting",
            prompt="hi",
            expected_spec_ids=(),
        ),
        OperatorFixture(
            label="answer only explain act",
            prompt="Explain what /act terminal run does, but do not request a mount.",
            expected_spec_ids=(),
        ),
        OperatorFixture(
            label="terminal list files",
            prompt="Use Terminal to list the files in main_computer.",
            expected_spec_ids=("terminal",),
            expected_mount_commands=('/act terminal run "dir main_computer" --cwd repo-root',),
        ),
        OperatorFixture(
            label="repo edit only",
            prompt="Prepare a repo edit request to update README.md with a note that the text console supports preview-only computer mount requests.",
            expected_spec_ids=("repo_edit",),
            expect_repo_edit=True,
            required_editor_terms=("README", "preview-only"),
        ),
        OperatorFixture(
            label="terminal plus repo edit",
            prompt=(
                "List the files in main_computer, then prepare a repo edit request to update README.md "
                "with a note about text-console action RAG."
            ),
            expected_spec_ids=("terminal", "repo_edit"),
            expected_mount_commands=('/act terminal run "dir main_computer" --cwd repo-root',),
            expect_repo_edit=True,
            required_editor_terms=("README", "action RAG"),
        ),
        OperatorFixture(
            label="repo edit plus tests",
            prompt=(
                "Prepare a repo edit request to make the smoke summary concise, then request Terminal "
                "to run the Python tests."
            ),
            expected_spec_ids=("terminal", "repo_edit"),
            expected_mount_commands=('/act terminal run "python -m pytest" --cwd repo-root',),
            expect_repo_edit=True,
            required_editor_terms=("smoke", "summary"),
        ),
    ]


def fixture_names(fixtures: list[OperatorFixture]) -> list[str]:
    return [fixture.label for fixture in fixtures]


def deterministic_preflight_for_fixture(fixture: OperatorFixture) -> dict[str, Any]:
    selected = list(fixture.expected_spec_ids)
    return {
        "needs_mount": "terminal" in selected,
        "needs_edit": "repo_edit" in selected,
        "needs_answer_only": not selected,
        "selected_spec_ids": selected,
        "reason": "offline-contract fixture payload",
    }


def deterministic_response_for_fixture(fixture: OperatorFixture) -> str:
    parts: list[str] = []
    if fixture.expected_mount_commands:
        parts.append("I will request the preview-only terminal action.")
        parts.append("```computer\n" + "\n".join(fixture.expected_mount_commands) + "\n```")
    if fixture.expect_repo_edit:
        request = fixture.prompt
        parts.append("I will prepare a bounded repo-root edit handoff.")
        parts.append(
            "```repo-edit\n"
            + json.dumps(
                {
                    "mode": "repo_root_edit_request",
                    "target_root": "repo-root",
                    "request_for_editor": request,
                    "requires_confirmation": True,
                    "blocked_reasons": [],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n```"
        )
    if not parts:
        return "Hello. I can help with the current repository."
    return "\n\n".join(parts)


def run_fixture(
    *,
    root: Path,
    fixture: OperatorFixture,
    specs: dict[str, ActionSpec],
    base_url: str,
    model: str,
    timeout: float,
    think: bool | str | None,
    offline_contract_only: bool,
) -> dict[str, Any]:
    config, model_input, config_failures = build_base_model_input(
        root=root,
        request_text=fixture.prompt,
        base_url=base_url,
        model=model,
        timeout=timeout,
        think=think,
    )

    preflight_messages = build_preflight_messages(
        model_input=model_input,
        specs=specs,
        request_text=fixture.prompt,
    )

    preflight_raw_response: dict[str, Any]
    if offline_contract_only:
        preflight_payload = deterministic_preflight_for_fixture(fixture)
        preflight_raw_response = {
            "content": json.dumps(preflight_payload, ensure_ascii=False),
            "provider": "offline-contract",
            "model": model,
            "metadata": {},
            "duration_ms": 0,
        }
    else:
        preflight_raw_response = call_provider_chat(model_input, preflight_messages)
        preflight_payload = parse_jsonish(preflight_raw_response["content"])

    preflight_validation = validate_preflight_payload(
        preflight_payload,
        available_spec_ids=set(specs),
        expected_spec_ids=fixture.expected_spec_ids,
    )

    selected_spec_ids = list(preflight_validation.get("selected_spec_ids") or [])
    safe_selected_spec_ids = [spec_id for spec_id in selected_spec_ids if spec_id in specs]
    final_messages = build_final_messages(
        model_input=model_input,
        specs=specs,
        selected_spec_ids=safe_selected_spec_ids,
    )

    final_raw_response: dict[str, Any]
    if offline_contract_only:
        final_content = deterministic_response_for_fixture(fixture)
        final_raw_response = {
            "content": final_content,
            "provider": "offline-contract",
            "model": model,
            "metadata": {},
            "duration_ms": 0,
        }
    else:
        final_raw_response = call_provider_chat(model_input, final_messages)

    final_content = final_raw_response["content"]
    mount_validation = parse_and_validate_mounts(
        final_content,
        expected_mount_commands=fixture.expected_mount_commands,
    )
    repo_edit_blocks = extract_repo_edit_blocks(final_content)
    repo_edit_validation = validate_repo_edit_blocks(
        repo_edit_blocks,
        expect_repo_edit=fixture.expect_repo_edit,
        user_prompt=fixture.prompt,
        required_terms=fixture.required_editor_terms,
    )

    failures = [
        *config_failures,
        *list(preflight_validation.get("failures") or []),
    ]
    if not mount_validation.get("ok"):
        failures.extend(list((mount_validation.get("validation") or {}).get("failures") or []))
    if not repo_edit_validation.get("ok"):
        failures.extend(list(repo_edit_validation.get("failures") or []))

    from main_computer.text_console import text_console_request_bytes, text_console_request_sha256

    final_request_sha = text_console_request_sha256(
        final_messages,
        model=config.model,
        think=config.ollama_think,
    )

    preflight_request_sha = text_console_request_sha256(
        preflight_messages,
        model=config.model,
        think=config.ollama_think,
    )

    return {
        "label": fixture.label,
        "prompt": fixture.prompt,
        "ok": not failures,
        "failures": failures,
        "expected_spec_ids": list(fixture.expected_spec_ids),
        "expected_mount_commands": list(fixture.expected_mount_commands),
        "expect_repo_edit": fixture.expect_repo_edit,
        "text_console_config": config.to_payload(),
        "context_pack_chars": len(str(getattr(model_input.context_pack, "text", "") or "")),
        "compact_context_pack_chars": len(compact_context_pack_text(str(getattr(model_input.context_pack, "text", "") or ""))),
        "message_counts": {
            "preflight": len(preflight_messages),
            "final": len(final_messages),
        },
        "input_chars": {
            "preflight": sum(len(str(getattr(message, "content", "") or "")) for message in preflight_messages),
            "final": sum(len(str(getattr(message, "content", "") or "")) for message in final_messages),
        },
        "request_bytes": {
            "preflight": text_console_request_bytes(preflight_messages, model=config.model, think=config.ollama_think),
            "final": text_console_request_bytes(final_messages, model=config.model, think=config.ollama_think),
        },
        "request_sha256": {
            "preflight": preflight_request_sha,
            "final": final_request_sha,
        },
        "preflight": {
            "raw_response": preflight_raw_response,
            "payload": preflight_payload,
            "validation": preflight_validation,
        },
        "selected_spec_ids": safe_selected_spec_ids,
        "selected_spec_hashes": {spec_id: specs[spec_id].sha256 for spec_id in safe_selected_spec_ids},
        "selected_spec_runtime_hashes": {spec_id: specs[spec_id].runtime_sha256 for spec_id in safe_selected_spec_ids},
        "final_response": {
            "raw_response": final_raw_response,
            "content": final_content,
            "preview": one_line(final_content, limit=360),
        },
        "mount_validation": mount_validation,
        "repo_edit_blocks": repo_edit_blocks,
        "repo_edit_validation": repo_edit_validation,
    }


def print_summary(report: dict[str, Any]) -> None:
    status = "PASS" if report.get("ok") else "FAIL"
    print(f"Text-console operator action-RAG smoke: {status}")
    print(
        f"used_ollama={report.get('used_ollama')} "
        f"base_url={report.get('base_url')!r} model={report.get('model')!r} "
        f"offline_contract_only={report.get('offline_contract_only')}"
    )
    print(f"full_report={report.get('full_report_path')}")
    print()

    warnings = list(report.get("warnings") or [])
    failures = list(report.get("failures") or [])
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
        print()
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"- {failure}")
        print()

    specs = report.get("action_specs") or []
    print("Action specs:")
    for spec in specs:
        print(
            f"- {spec.get('spec_id')}: {spec.get('title')} "
            f"sha256={str(spec.get('sha256') or '')[:12]} path={spec.get('path')}"
        )
    print()

    print("Fixtures:")
    for item in report.get("fixtures", []):
        print(f"- {item.get('label')}: {'PASS' if item.get('ok') else 'FAIL'}")
        print(f"  prompt: {item.get('prompt')}")
        print(
            f"  context_root: {(item.get('text_console_config') or {}).get('context_root')} "
            f"context_pack_chars={item.get('context_pack_chars')}"
        )
        print(
            f"  selected_specs: expected={item.get('expected_spec_ids')} "
            f"actual={item.get('selected_spec_ids')}"
        )
        print(
            f"  request_sha256: preflight={((item.get('request_sha256') or {}).get('preflight') or '')[:16]} "
            f"final={((item.get('request_sha256') or {}).get('final') or '')[:16]}"
        )

        mount_validation = item.get("mount_validation") or {}
        mount_report = mount_validation.get("validation") or {}
        if item.get("expected_mount_commands") or mount_report.get("commands"):
            print(
                f"  mount_validation: {'PASS' if mount_validation.get('ok') else 'FAIL'} "
                f"expected={mount_report.get('expected_commands')} actual={mount_report.get('commands')}"
            )

        if item.get("expect_repo_edit") or item.get("repo_edit_blocks"):
            repo_validation = item.get("repo_edit_validation") or {}
            print(
                f"  repo_edit_validation: {'PASS' if repo_validation.get('ok') else 'FAIL'} "
                f"blocks={repo_validation.get('count')}"
            )

        response = (item.get("final_response") or {}).get("content") or ""
        if item.get("expected_mount_commands") or item.get("expect_repo_edit"):
            print("  response:")
            for line in response.strip().splitlines():
                print(f"    {line}")
        else:
            print(f"  response_preview: {(item.get('final_response') or {}).get('preview')}")
        failures = list(item.get("failures") or [])
        if failures:
            print("  failures:")
            for failure in failures:
                print(f"    - {failure}")
    print()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Text-console operator action-RAG smoke.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--think",
        default="false",
        help="Ollama think setting: true, false, or omit. Defaults to false for stable smoke output.",
    )
    parser.add_argument(
        "--offline-contract-only",
        action="store_true",
        help="Do not call Ollama; use deterministic fixture payloads to exercise parsing/validation only.",
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=[],
        help="Run only fixtures whose label contains this text. Can be supplied multiple times.",
    )
    parser.add_argument("--full-report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--print-full-report", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = repo_root()
    add_repo_to_path(root)
    think = parse_boolish(args.think)

    warnings: list[str] = []
    failures: list[str] = []

    specs = load_action_specs(root)
    required = {"terminal", "repo_edit"}
    missing = sorted(required - set(specs))
    if missing:
        failures.append(f"missing required action specs: {missing}")

    fixtures = default_fixtures()
    if args.fixture:
        needles = [needle.lower() for needle in args.fixture]
        fixtures = [fixture for fixture in fixtures if any(needle in fixture.label.lower() for needle in needles)]
        if not fixtures:
            failures.append(f"--fixture matched no fixtures: {args.fixture!r}")

    fixture_reports: list[dict[str, Any]] = []
    for fixture in fixtures:
        try:
            fixture_report = run_fixture(
                root=root,
                fixture=fixture,
                specs=specs,
                base_url=args.base_url,
                model=args.model,
                timeout=args.timeout,
                think=think,
                offline_contract_only=args.offline_contract_only,
            )
        except Exception as exc:
            fixture_report = {
                "label": fixture.label,
                "prompt": fixture.prompt,
                "ok": False,
                "failures": [repr(exc)],
                "expected_spec_ids": list(fixture.expected_spec_ids),
                "expected_mount_commands": list(fixture.expected_mount_commands),
                "expect_repo_edit": fixture.expect_repo_edit,
            }
        if not fixture_report.get("ok"):
            for failure in list(fixture_report.get("failures") or []):
                failures.append(f"{fixture.label}: {failure}")
        fixture_reports.append(fixture_report)

    if args.offline_contract_only:
        warnings.append("offline_contract_only was used; Ollama/model behavior was not tested")

    report = {
        "ok": not failures,
        "used_ollama": not args.offline_contract_only,
        "offline_contract_only": bool(args.offline_contract_only),
        "base_url": args.base_url,
        "model": args.model,
        "think": think,
        "root": str(root),
        "full_report_path": args.full_report,
        "warnings": warnings,
        "failures": failures,
        "action_specs": [spec.catalog_entry() for spec in specs.values()],
        "fixtures": fixture_reports,
    }

    full_report_path = root / args.full_report
    full_report_path.parent.mkdir(parents=True, exist_ok=True)
    full_report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print_summary(report)
    if args.print_full_report:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
