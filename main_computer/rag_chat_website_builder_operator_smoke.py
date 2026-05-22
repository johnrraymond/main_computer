#!/usr/bin/env python3
"""
Operator-friendly chat-app handoff smoke for the latest Website Builder debug site.

This script simulates the future flow:

    runtime user prompt
    -> chat app prompt construction normalizes the request
    -> backend verifies the selected site is a debug-golden-path-* Website Builder site
    -> blessed generated-editor path proposes and packages the edit
    -> new_patch.py dry-run/apply targets that exact site root
    -> Git verifies and commits inside that exact debug site repo

It is intentionally debug-site-only. It must refuse hub/platform/non-debug sites
before any model, patch, apply, or Git commit work can happen.

There is intentionally no default edit request. The operator must pass a prompt
with --prompt, --prompt-file, or a positional prompt so the smoke cannot pass by
using a local hard-coded website edit.

Run from PowerShell in the repo root with normal Python:

    python .\rag_chat_website_builder_operator_smoke.py --prompt "Change the debug homepage copy ..."

Do not wrap the whole script in wsl.exe. The script calls WSL internally only for
Git/new_patch operations that must run through the configured WSL executor.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEBUG_PREFIX = "debug-golden-path-"
AI_BRANCH = "ai/debug-website-chat-builder-handoff"
COMMIT_MESSAGE = "Apply chat-requested debug website edit"


CHAT_WEBSITE_BUILDER_PREFUNK = """\
You are the Website Builder chat handoff layer.

You are not allowed to edit files directly. Your job is to normalize the user's
chat request into a safe website-editor request for exactly one selected Website
Builder site.

Safety rules:
- AI website editing is currently debug-site-only.
- The only allowed site ids match: debug-golden-path-*
- Refuse hub-site, hub-local, platform services, Directus services, install hub,
  parent builder repos, and regular non-debug runtime websites.
- The selected site path must be inside runtime/websites/<site_id>.
- Do not choose a different site.
- Do not invent files or hidden state.
- Preserve metadata and non-homepage assets unless the user explicitly asks.
- Return JSON only.
"""

CHAT_SELECTED_SITE_PREFUNK_TEMPLATE = """\
Selected Website Builder site:

site_id: {site_id}
site_path: {site_path}
repo_relative_path: {repo_relative_path}
manifest_summary:
{manifest_summary}

Bounded source evidence from the selected site:
{bounded_source_evidence}
"""

CHAT_OUTPUT_CONTRACT_PREFUNK = """\
Return exactly one JSON object with this shape:

{
  "mode": "website_builder_debug_site_edit_request",
  "ok": true,
  "site_id": "<same selected site id>",
  "request_for_editor": "<natural-language edit request to send to the RAG editor>",
  "target_policy": "debug_sites_only",
  "blocked_reasons": [],
  "prefunk_prompts_required": [
    "website_builder_debug_site_safety",
    "selected_site_context",
    "json_handoff_contract"
  ]
}

If the selected site is not allowed, return ok:false and explain blocked_reasons.
Do not include Markdown fences.
"""


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "new_patch.py").is_file() and (candidate / "main_computer").is_dir():
            return candidate
    return Path.cwd().resolve()


def add_repo_to_path(root: Path) -> None:
    for item in (root, root / "main_computer"):
        text = str(item)
        if text not in sys.path:
            sys.path.insert(0, text)


def json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def read_prompt_from_args(args: argparse.Namespace) -> str:
    """Return the runtime user prompt.

    The smoke deliberately has no default edit request.  This prevents it from
    proving only one locally hard-coded change.  The prompt must come from the
    operator via --prompt, --prompt-file, or positional prompt text.
    """
    provided: list[tuple[str, str]] = []

    if getattr(args, "prompt", None):
        provided.append(("prompt", str(args.prompt)))

    prompt_file = getattr(args, "prompt_file", None)
    if prompt_file:
        path = Path(prompt_file)
        try:
            provided.append(("prompt_file", path.read_text(encoding="utf-8")))
        except OSError as exc:
            raise RuntimeError(f"Unable to read --prompt-file {path}: {exc}") from exc

    prompt_parts = getattr(args, "prompt_parts", None) or []
    if prompt_parts:
        provided.append(("positional_prompt", " ".join(str(part) for part in prompt_parts)))

    nonempty = [(source, text.strip()) for source, text in provided if text and text.strip()]
    if not nonempty:
        raise RuntimeError(
            "Missing runtime user prompt. Pass --prompt '...' or --prompt-file path.txt. "
            "This smoke intentionally has no default edit request."
        )

    if len(nonempty) > 1:
        sources = ", ".join(source for source, _ in nonempty)
        raise RuntimeError(f"Provide the user prompt in exactly one place, not multiple places: {sources}")

    return nonempty[0][1]


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def load_debug_website_tool(root: Path):
    path = root / "tools" / "local-platform" / "debug-website.py"
    spec = importlib.util.spec_from_file_location("debug_website_tool_for_chat_handoff", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import debug website tool from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_debug_golden_site_id(site_id: str) -> bool:
    return bool(re.fullmatch(r"debug-golden-path-[a-z0-9][a-z0-9-]{0,96}", str(site_id or "").strip().lower()))


def websites_root(builder_root: Path) -> Path:
    return builder_root / "runtime" / "websites"


def newest_existing_debug_site(builder_root: Path) -> str | None:
    root = websites_root(builder_root)
    candidates: list[Path] = []
    if root.is_dir():
        for path in root.iterdir():
            if path.is_dir() and valid_debug_golden_site_id(path.name):
                candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    return candidates[0].name


def ensure_debug_site(builder_root: Path, site_id: str, *, create_if_missing: bool) -> dict[str, Any]:
    tool = load_debug_website_tool(builder_root)
    site_dir = websites_root(builder_root) / site_id
    if not create_if_missing and not site_dir.exists():
        raise RuntimeError(f"Selected debug site does not exist: {site_id}")
    args = argparse.Namespace(
        command="ensure",
        repo_root=str(builder_root),
        site=site_id,
        purpose="golden website path",
        unique=False,
        name=None,
        bootstrap=False,
        overwrite=False,
        no_compose=False,
    )
    result = tool.ensure_debug_website(args)
    if not result.get("ok"):
        raise RuntimeError(f"debug-website ensure failed: {result}")
    return result


def select_or_create_debug_site(builder_root: Path, explicit_site: str | None, *, create_if_missing: bool) -> tuple[str, dict[str, Any]]:
    if explicit_site:
        site_id = explicit_site.strip().lower()
        source = "explicit_site"
    else:
        site_id = newest_existing_debug_site(builder_root) or ""
        source = "most_recent_debug_golden_path_site"
        if not site_id:
            if not create_if_missing:
                raise RuntimeError("No existing debug-golden-path-* site found and create_if_missing is disabled.")
            site_id = f"{DEBUG_PREFIX}{int(time.time())}"
            source = "created_no_existing_debug_golden_path_site"

    if not valid_debug_golden_site_id(site_id):
        raise RuntimeError(
            f"Refusing site {site_id!r}. AI Website Builder editing is currently limited to {DEBUG_PREFIX}* debug sites."
        )
    ensure_payload = ensure_debug_site(builder_root, site_id, create_if_missing=create_if_missing)
    return site_id, {"source": source, "ensure_payload": ensure_payload}


def assert_debug_site_target(builder_root: Path, site_id: str, site_path: Path) -> dict[str, Any]:
    builder_root = builder_root.resolve()
    site_path = site_path.resolve()
    web_root = websites_root(builder_root).resolve()
    if not valid_debug_golden_site_id(site_id):
        raise RuntimeError(f"Refusing non-debug-golden-path site id: {site_id}")
    try:
        rel = site_path.relative_to(web_root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing site outside runtime/websites: {site_path}") from exc
    if rel.as_posix() != site_id:
        raise RuntimeError(f"Refusing mismatched site path. site_id={site_id!r}, relative_path={rel.as_posix()!r}")
    forbidden = {"hub-site", "hub-local", "install", "hub", "directus"}
    if site_id in forbidden or any(part in forbidden for part in site_path.parts):
        raise RuntimeError(f"Refusing platform/hub target: {site_id}")
    required = ["site.json", "index.html", "style.css", "script.js"]
    missing = [name for name in required if not (site_path / name).is_file()]
    if missing:
        raise RuntimeError(f"Selected debug site is missing required files: {missing}")
    return {
        "ok": True,
        "site_id": site_id,
        "site_path": str(site_path),
        "repo_relative_path": f"runtime/websites/{site_id}",
        "websites_root": str(web_root),
    }


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def bounded_site_evidence(site_path: Path, limit_per_file: int = 2200) -> str:
    chunks = []
    for name in ["site.json", "index.html", "style.css", "script.js"]:
        path = site_path / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > limit_per_file:
            text = text[:limit_per_file].rstrip() + "\n...<truncated>"
        chunks.append(f"--- {name} ---\n{text}")
    return "\n\n".join(chunks)


def parse_jsonish(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(raw[start : end + 1])
        else:
            raise
    if not isinstance(payload, dict):
        raise ValueError("Model handoff response was not a JSON object.")
    return payload


def build_chat_handoff_messages(*, root: Path, site_id: str, site_path: Path, user_request: str):
    add_repo_to_path(root)
    from main_computer.models import ChatMessage
    from main_computer.chat_console import build_notebook_ai_messages

    manifest = read_json_file(site_path / "site.json")
    manifest_summary = json.dumps(
        {
            "id": manifest.get("id") or site_id,
            "name": manifest.get("name"),
            "kind": manifest.get("kind"),
            "lanes": manifest.get("lanes"),
        },
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    selected_site_prefunk = CHAT_SELECTED_SITE_PREFUNK_TEMPLATE.format(
        site_id=site_id,
        site_path=str(site_path),
        repo_relative_path=f"runtime/websites/{site_id}",
        manifest_summary=manifest_summary,
        bounded_source_evidence=bounded_site_evidence(site_path),
    )
    chat_source = (
        "A user asked the Website Builder chat app for this site modification:\n\n"
        f"{user_request}\n\n"
        "Normalize this into the safe editor request if and only if the selected site is allowed."
    )
    return [
        ChatMessage(role="system", content=CHAT_WEBSITE_BUILDER_PREFUNK),
        ChatMessage(role="system", content=selected_site_prefunk),
        ChatMessage(role="system", content=CHAT_OUTPUT_CONTRACT_PREFUNK),
        *build_notebook_ai_messages(chat_source, attachments=[]),
    ]


def run_chat_handoff(*, root: Path, site_id: str, site_path: Path, user_request: str, model: str | None = None) -> dict[str, Any]:
    add_repo_to_path(root)
    from main_computer.config import MainComputerConfig
    from main_computer.router import MainComputer

    config = MainComputerConfig()
    computer = MainComputer.build(config)
    provider = getattr(computer, "provider", None)
    if model and provider is not None and hasattr(provider, "model"):
        try:
            provider.model = model
        except Exception:
            pass
    messages = build_chat_handoff_messages(root=root, site_id=site_id, site_path=site_path, user_request=user_request)
    response = provider.chat(messages) if provider is not None and hasattr(provider, "chat") else computer.chat(user_request)
    payload = parse_jsonish(getattr(response, "content", ""))
    return {
        "ok": bool(payload.get("ok")),
        "payload": payload,
        "raw_response": getattr(response, "content", ""),
        "provider": getattr(response, "provider", getattr(provider, "name", "")),
        "model": getattr(response, "model", getattr(provider, "model", "")),
        "prefunk_prompts": {
            "website_builder_debug_site_safety": CHAT_WEBSITE_BUILDER_PREFUNK,
            "selected_site_context_template": CHAT_SELECTED_SITE_PREFUNK_TEMPLATE,
            "json_handoff_contract": CHAT_OUTPUT_CONTRACT_PREFUNK,
        },
    }


def validate_chat_handoff_payload(payload: dict[str, Any], *, site_id: str, user_request: str) -> list[str]:
    issues: list[str] = []
    if payload.get("mode") != "website_builder_debug_site_edit_request":
        issues.append("handoff mode is not website_builder_debug_site_edit_request")
    if payload.get("ok") is not True:
        issues.append("handoff ok is not true")
    if str(payload.get("site_id") or "") != site_id:
        issues.append("handoff site_id does not match selected site")
    editor_request = str(payload.get("request_for_editor") or "").strip()
    if not editor_request:
        issues.append("handoff did not produce request_for_editor")
    if "debug-golden-path-" not in str(payload.get("site_id") or ""):
        issues.append("handoff did not keep a debug-golden-path site id")
    required = {
        "website_builder_debug_site_safety",
        "selected_site_context",
        "json_handoff_contract",
    }
    got = set(payload.get("prefunk_prompts_required") or [])
    missing = sorted(required - got)
    if missing:
        issues.append(f"handoff omitted required prefunk prompt labels: {missing}")
    # This is not an edit-choice check. It only prevents a disconnected/local
    # stub from returning an unrelated canned request while claiming success.
    prompt_terms = {term.lower() for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", user_request)}
    editor_terms = {term.lower() for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", editor_request)}
    if prompt_terms and not (prompt_terms & editor_terms):
        issues.append("request_for_editor does not share any substantive terms with the runtime user prompt")
    return issues


def shell_quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\"'\"'") + "'"


def run_command(command: list[str], *, timeout: float = 240.0) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {
            "command": command,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": 124,
            "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
            "stderr": exc.stderr if isinstance(exc.stderr, str) else f"timed out after {timeout}s",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }


def wsl_shell(*, wsl_command: str, distribution: str, cwd_wsl: str, script: str, timeout: float) -> dict[str, Any]:
    command = [wsl_command]
    if distribution:
        command += ["--distribution", distribution]
    command += ["--cd", cwd_wsl, "--exec", "sh", "-lc", script]
    return run_command(command, timeout=timeout)


def ensure_site_git_boundary(*, site_wsl: str, websites_root_wsl: str, wsl_command: str, distribution: str, timeout: float) -> dict[str, Any]:
    script = f"""
set -eu
export GIT_CEILING_DIRECTORIES={shell_quote(websites_root_wsl)}
cd {shell_quote(site_wsl)}
if [ ! -d .git ]; then
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Git escaped selected site before initialization" >&2
    exit 31
  fi
  git init >/dev/null
  git checkout -B main >/dev/null
  printf '%s\n' '/tools/patching/reports/new_patch_runs/' > .gitignore
  git add .gitignore site.json index.html style.css script.js builder.json
  git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' commit -m 'seed builder debug website workbench' >/dev/null
fi
top="$(git rev-parse --show-toplevel)"
pwdp="$(pwd -P)"
if [ "$top" != "$pwdp" ]; then
  echo "Git top-level escaped selected site: $top != $pwdp" >&2
  exit 32
fi
status="$(git status --porcelain=v1)"
if [ -n "$status" ]; then
  printf '%s\n' "$status" >&2
  echo "Selected debug site has uncommitted changes before chat handoff smoke" >&2
  exit 33
fi
git checkout -B {shell_quote(AI_BRANCH)} >/dev/null
git rev-parse --show-toplevel
git status --porcelain=v1
""".strip()
    return wsl_shell(wsl_command=wsl_command, distribution=distribution, cwd_wsl=site_wsl, script=script, timeout=timeout)


def git_boundary_status(*, site_wsl: str, websites_root_wsl: str, wsl_command: str, distribution: str, timeout: float) -> dict[str, Any]:
    script = f"""
set -eu
export GIT_CEILING_DIRECTORIES={shell_quote(websites_root_wsl)}
cd {shell_quote(site_wsl)}
printf 'top='
git rev-parse --show-toplevel
printf 'branch='
git rev-parse --abbrev-ref HEAD
printf 'status<<EOF\n'
git status --porcelain=v1
printf 'EOF\n'
""".strip()
    return wsl_shell(wsl_command=wsl_command, distribution=distribution, cwd_wsl=site_wsl, script=script, timeout=timeout)


def parse_changed_files(stdout: str) -> int | None:
    match = re.search(r"changed_files:\s*(\d+)", stdout or "")
    return int(match.group(1)) if match else None


def run_new_patch(*, root: Path, patch_zip: Path, site_wsl: str, wsl_command: str, distribution: str, timeout: float, dry_run: bool) -> dict[str, Any]:
    from main_computer.rag_debug_website_golden_path_smoke import host_path_to_wsl
    argv = [
        wsl_command,
    ]
    if distribution:
        argv += ["--distribution", distribution]
    argv += [
        "--cd",
        site_wsl,
        "--exec",
        "python3",
        host_path_to_wsl(root / "new_patch.py"),
        host_path_to_wsl(patch_zip),
        "--target-root",
        site_wsl,
    ]
    if dry_run:
        argv.append("--dry-run")
    result = run_command(argv, timeout=timeout)
    result["changed_files"] = parse_changed_files(result.get("stdout", ""))
    return result


def commit_site_change(*, site_wsl: str, websites_root_wsl: str, wsl_command: str, distribution: str, timeout: float) -> dict[str, Any]:
    script = f"""
set -eu
export GIT_CEILING_DIRECTORIES={shell_quote(websites_root_wsl)}
cd {shell_quote(site_wsl)}
top="$(git rev-parse --show-toplevel)"
pwdp="$(pwd -P)"
if [ "$top" != "$pwdp" ]; then
  echo "Git top-level escaped selected site during commit: $top != $pwdp" >&2
  exit 41
fi
git add index.html
git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' commit -m {shell_quote(COMMIT_MESSAGE)} >/dev/null
git status --porcelain=v1
git log --oneline -1
""".strip()
    return wsl_shell(wsl_command=wsl_command, distribution=distribution, cwd_wsl=site_wsl, script=script, timeout=timeout)


def make_editor_args(args: argparse.Namespace):
    from main_computer.rag_debug_website_golden_path_smoke import ProgressReporter
    return argparse.Namespace(
        progress=ProgressReporter(enabled=not args.quiet, interval_seconds=args.progress_interval_seconds),
        ai_timeout_seconds=args.ai_timeout_seconds,
        model=args.model,
        ollama_url=args.ollama_url,
        num_predict=args.num_predict,
        format_mode=args.format_mode,
        think_mode=args.think_mode,
        max_index_files=args.max_index_files,
        max_excerpts_per_file=args.max_excerpts_per_file,
        excerpt_window_lines=args.excerpt_window_lines,
        max_excerpt_chars=args.max_excerpt_chars,
        max_file_read_chars=args.max_file_read_chars,
        max_evidence_chars=args.max_evidence_chars,
        discovery_repair_attempts=args.discovery_repair_attempts,
        discovery_repair_source_chars=args.discovery_repair_source_chars,
        discovery_anchor_option_repair_attempts=args.discovery_anchor_option_repair_attempts,
        discovery_anchor_option_count=args.discovery_anchor_option_count,
        grounding_repair_attempts=args.grounding_repair_attempts,
        patch_proposal_repair_attempts=args.patch_proposal_repair_attempts,
    )


def run_blessed_editor(*, root: Path, site_path: Path, request: str, args: argparse.Namespace) -> dict[str, Any]:
    from main_computer.rag_debug_website_golden_path_smoke import run_blessed_generated_editor_patch_artifact
    output_root = Path(tempfile.gettempdir()) / "mc_chat_website_builder_handoff" / time.strftime("%Y%m%d_%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    editor_args = make_editor_args(args)
    report = run_blessed_generated_editor_patch_artifact(
        root=root,
        source_site_dir=site_path,
        request=request,
        output_root=output_root,
        args=editor_args,
    )
    report["output_root"] = str(output_root)
    return report


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.builder_root).resolve() if args.builder_root else repo_root()
    add_repo_to_path(root)
    user_request = read_prompt_from_args(args)

    from main_computer.rag_debug_website_golden_path_smoke import host_path_to_wsl

    site_id, selection = select_or_create_debug_site(root, args.site, create_if_missing=bool(args.create_if_missing))
    ensure_payload = selection["ensure_payload"]
    site_path = Path(str(ensure_payload.get("site_path") or (websites_root(root) / site_id))).resolve()
    target = assert_debug_site_target(root, site_id, site_path)

    site_wsl = host_path_to_wsl(site_path)
    websites_root_wsl = host_path_to_wsl(websites_root(root).resolve())

    # The Git boundary is checked before any model call so the system cannot
    # accidentally target hub-site, the parent builder repo, or the install hub.
    setup_git = ensure_site_git_boundary(
        site_wsl=site_wsl,
        websites_root_wsl=websites_root_wsl,
        wsl_command=args.wsl_command,
        distribution=args.distribution,
        timeout=args.timeout_seconds,
    )
    if not setup_git.get("ok"):
        return {
            "ok": False,
            "failed_stage": "site_git_boundary",
            "site_id": site_id,
            "target": target,
            "site_selection": {k: v for k, v in selection.items() if k != "ensure_payload"},
            "setup_git": setup_git,
        }

    chat = run_chat_handoff(root=root, site_id=site_id, site_path=site_path, user_request=user_request, model=args.model)
    chat_payload = chat.get("payload") if isinstance(chat.get("payload"), dict) else {}
    chat_issues = validate_chat_handoff_payload(chat_payload, site_id=site_id, user_request=user_request)
    if not chat.get("ok") or chat_issues:
        return {
            "ok": False,
            "failed_stage": "chat_handoff",
            "site_id": site_id,
            "target": target,
            "user_prompt_sha256": sha256_text(user_request),
            "user_prompt_preview": user_request[:500],
            "chat_issues": chat_issues,
            "chat": chat,
        }

    editor_request = str(chat_payload.get("request_for_editor") or "").strip()

    blessed = run_blessed_editor(root=root, site_path=site_path, request=editor_request, args=args)
    artifact_report = blessed.get("artifact_packaging") if isinstance(blessed.get("artifact_packaging"), dict) else {}
    patch_zip_text = str(artifact_report.get("artifact_path") or "")
    patch_zip = Path(patch_zip_text) if patch_zip_text else Path()
    if blessed.get("ok") is not True or not patch_zip.is_file():
        return {
            "ok": False,
            "failed_stage": "blessed_editor_artifact",
            "site_id": site_id,
            "target": target,
            "chat": chat,
            "editor_request": editor_request,
            "blessed": blessed,
        }

    dry_run = run_new_patch(
        root=root,
        patch_zip=patch_zip,
        site_wsl=site_wsl,
        wsl_command=args.wsl_command,
        distribution=args.distribution,
        timeout=args.timeout_seconds,
        dry_run=True,
    )
    if not dry_run.get("ok"):
        return {
            "ok": False,
            "failed_stage": "new_patch_dry_run",
            "site_id": site_id,
            "target": target,
            "chat": chat,
            "editor_request": editor_request,
            "blessed": blessed,
            "patch_zip": str(patch_zip),
            "dry_run": dry_run,
        }

    apply_result: dict[str, Any] | None = None
    commit_result: dict[str, Any] | None = None
    post_status: dict[str, Any] | None = None
    if not args.dry_run_only:
        apply_result = run_new_patch(
            root=root,
            patch_zip=patch_zip,
            site_wsl=site_wsl,
            wsl_command=args.wsl_command,
            distribution=args.distribution,
            timeout=args.timeout_seconds,
            dry_run=False,
        )
        if not apply_result.get("ok"):
            return {
                "ok": False,
                "failed_stage": "new_patch_apply",
                "site_id": site_id,
                "target": target,
                "chat": chat,
                "editor_request": editor_request,
                "blessed": blessed,
                "patch_zip": str(patch_zip),
                "dry_run": dry_run,
                "apply": apply_result,
            }
        commit_result = commit_site_change(
            site_wsl=site_wsl,
            websites_root_wsl=websites_root_wsl,
            wsl_command=args.wsl_command,
            distribution=args.distribution,
            timeout=args.timeout_seconds,
        )
        if not commit_result.get("ok"):
            return {
                "ok": False,
                "failed_stage": "site_git_commit",
                "site_id": site_id,
                "target": target,
                "chat": chat,
                "editor_request": editor_request,
                "blessed": blessed,
                "patch_zip": str(patch_zip),
                "dry_run": dry_run,
                "apply": apply_result,
                "commit": commit_result,
            }
        post_status = git_boundary_status(
            site_wsl=site_wsl,
            websites_root_wsl=websites_root_wsl,
            wsl_command=args.wsl_command,
            distribution=args.distribution,
            timeout=args.timeout_seconds,
        )

    return {
        "ok": True,
        "mode": "chat_app_website_builder_latest_debug_site_operator_smoke",
        "site_id": site_id,
        "target": target,
        "site_selection": {k: v for k, v in selection.items() if k != "ensure_payload"},
        "user_prompt_sha256": sha256_text(user_request),
        "user_prompt_preview": user_request[:500],
        "chat": {
            "ok": chat.get("ok"),
            "provider": chat.get("provider"),
            "model": chat.get("model"),
            "payload": chat.get("payload"),
            "prefunk_prompts": chat.get("prefunk_prompts"),
        },
        "editor_request": editor_request,
        "blessed_artifact_ok": blessed.get("ok") is True,
        "selected_target_file": blessed.get("selected_target_file"),
        "patch_zip": str(patch_zip),
        "dry_run": dry_run,
        "apply": apply_result,
        "commit": commit_result,
        "post_status": post_status,
        "dry_run_only": bool(args.dry_run_only),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PowerShell-friendly smoke: simulate Website Builder chat app handoff against the latest debug-golden-path-* site.")
    parser.add_argument("prompt_parts", nargs="*", help="Runtime user prompt text. Alternative to --prompt or --prompt-file.")
    parser.add_argument("--site", default=None, help="Optional debug-golden-path-* site id override. Defaults to the most recently modified existing debug site.")
    parser.add_argument("--builder-root", default=None, help="Website Builder repo root. Defaults to the repo containing this script.")
    parser.add_argument("--prompt", default=None, help="Runtime user prompt to simulate from the Website Builder chat app.")
    parser.add_argument("--prompt-file", default=None, help="Read the runtime user prompt from a UTF-8 text file.")
    parser.add_argument("--create-if-missing", action="store_true", help="Create a new debug-golden-path-* site only if no existing debug site is found.")
    parser.add_argument("--dry-run-only", action="store_true", help="Stop after new_patch.py dry-run.")
    parser.add_argument("--wsl-command", default="wsl.exe")
    parser.add_argument("--distribution", default=os.environ.get("MAIN_COMPUTER_WSL_DISTRIBUTION", "MainComputerExecutorTest"))
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--ai-timeout-seconds", type=int, default=600)
    parser.add_argument("--model", default=None)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--num-predict", type=int, default=900)
    parser.add_argument("--format-mode", choices=["json", "none"], default="none")
    parser.add_argument("--think-mode", choices=["omit", "false", "true", "low", "medium", "high"], default="false")
    parser.add_argument("--max-index-files", type=int, default=8)
    parser.add_argument("--max-excerpts-per-file", type=int, default=3)
    parser.add_argument("--excerpt-window-lines", type=int, default=3)
    parser.add_argument("--max-excerpt-chars", type=int, default=1200)
    parser.add_argument("--max-file-read-chars", type=int, default=200000)
    parser.add_argument("--max-evidence-chars", type=int, default=16000)
    parser.add_argument("--discovery-repair-attempts", type=int, default=1)
    parser.add_argument("--discovery-repair-source-chars", type=int, default=12000)
    parser.add_argument("--discovery-anchor-option-repair-attempts", type=int, default=1)
    parser.add_argument("--discovery-anchor-option-count", type=int, default=48)
    parser.add_argument("--grounding-repair-attempts", type=int, default=2)
    parser.add_argument("--patch-proposal-repair-attempts", type=int, default=2)
    parser.add_argument("--progress-interval-seconds", type=float, default=15.0)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = evaluate(args)
    except Exception as exc:
        report = {
            "ok": False,
            "mode": "chat_app_website_builder_latest_debug_site_operator_smoke",
            "failed_stage": "unhandled_exception",
            "error": str(exc),
        }
    json_print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
