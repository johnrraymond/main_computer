#!/usr/bin/env python3
"""
Operator-friendly chat-app handoff smoke for a fresh Website Builder debug site.

This script simulates the future flow:

    runtime user prompt
    -> chat app prompt construction normalizes the request
    -> backend creates and verifies a fresh debug-golden-path-* Website Builder site
    -> blessed generated-editor path proposes and packages the edit
    -> new_patch.py dry-run/apply targets that exact site root
    -> Git verifies and commits inside that exact debug site repo

It is intentionally debug-site-only. It must refuse hub/platform/non-debug sites
before any model, patch, apply, or Git commit work can happen.

There is intentionally no default edit request. The operator must pass a prompt
with --prompt, --prompt-file, or a positional prompt so the smoke cannot pass by
using a local hard-coded website edit. By default it creates a new disposable
debug-golden-path-* site for every run so repeated smoke runs cannot pass or fail
because a previous run already mutated the selected website.

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
MC_OPERATOR_SMOKE_METADATA_BASELINE_V4 = True
MC_OPERATOR_SMOKE_GROUNDING_EXACT_COPY_OPTIONS_V4 = True
MC_OPERATOR_SMOKE_LOCAL_OLLAMA_DEFAULTS_V6 = True
MC_OPERATOR_SMOKE_WSL_DISTRIBUTION_DEFAULTS_V6 = True
MC_OPERATOR_SMOKE_FRESH_DEBUG_SITE_PER_RUN_V7 = True
MC_OPERATOR_SMOKE_SKIP_COMPOSE_FOR_DEBUG_SITE_ENSURE_V8 = True
SMOKE_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
SMOKE_DEFAULT_OLLAMA_MODEL = "gemma4:26b"
SMOKE_PREFERRED_WSL_DISTRIBUTIONS = (
    "MainComputerExecutorTest",
    "MainComputerExecutor",
    "Ubuntu-24.04",
    "Ubuntu-22.04",
    "Ubuntu",
)


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


def ollama_generate_url_from_base(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = SMOKE_DEFAULT_OLLAMA_BASE_URL
    if base.endswith("/api/generate"):
        return base
    return f"{base}/api/generate"


def install_operator_smoke_ollama_defaults(args: argparse.Namespace) -> dict[str, Any]:
    """Install smoke-local Ollama defaults before MainComputerConfig.from_env().

    The Website Builder app can keep using normal environment-driven config.
    This operator smoke fills only missing local values so a Windows localhost
    split cannot stop the golden-path harness before it reaches the editor.
    """

    applied: dict[str, str] = {}

    base_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
    if not base_url:
        base_url = SMOKE_DEFAULT_OLLAMA_BASE_URL
        os.environ["OLLAMA_BASE_URL"] = base_url
        applied["OLLAMA_BASE_URL"] = base_url

    model = (
        str(getattr(args, "model", None) or "").strip()
        or os.environ.get("MAIN_COMPUTER_MODEL", "").strip()
        or os.environ.get("OLLAMA_MODEL", "").strip()
        or SMOKE_DEFAULT_OLLAMA_MODEL
    )

    if not str(getattr(args, "model", None) or "").strip():
        args.model = model
        applied["--model"] = model

    if not os.environ.get("MAIN_COMPUTER_MODEL", "").strip():
        os.environ["MAIN_COMPUTER_MODEL"] = model
        applied["MAIN_COMPUTER_MODEL"] = model

    if not os.environ.get("OLLAMA_MODEL", "").strip():
        os.environ["OLLAMA_MODEL"] = model
        applied["OLLAMA_MODEL"] = model

    if not str(getattr(args, "ollama_url", None) or "").strip():
        args.ollama_url = ollama_generate_url_from_base(base_url)
        applied["--ollama-url"] = args.ollama_url

    return {
        "marker": "MC_OPERATOR_SMOKE_LOCAL_OLLAMA_DEFAULTS_V6",
        "policy": "smoke-local defaults only; explicit CLI/env values win",
        "applied": applied,
        "resolved": {
            "model": str(args.model),
            "ollama_base_url": base_url,
            "ollama_generate_url": str(args.ollama_url),
        },
    }


def decode_wsl_bytes(data: bytes | None) -> str:
    if not data:
        return ""
    if b"\x00" in data:
        try:
            return data.decode("utf-16le", errors="replace").replace("\ufeff", "")
        except Exception:
            pass
    return data.decode("utf-8", errors="replace").replace("\x00", "")


def probe_wsl_distributions(wsl_command: str, *, timeout: float = 12.0) -> dict[str, Any]:
    command = [str(wsl_command or "wsl.exe"), "--list", "--quiet"]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "distributions": [],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": 124,
            "stdout": decode_wsl_bytes(exc.stdout if isinstance(exc.stdout, bytes) else None),
            "stderr": decode_wsl_bytes(exc.stderr if isinstance(exc.stderr, bytes) else None) or f"timed out after {timeout}s",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "distributions": [],
        }

    stdout = decode_wsl_bytes(completed.stdout)
    stderr = decode_wsl_bytes(completed.stderr)
    distributions: list[str] = []
    seen: set[str] = set()
    for raw_line in stdout.splitlines():
        name = raw_line.strip().lstrip("*").strip()
        if not name or name.lower() == "windows subsystem for linux distributions:":
            continue
        key = name.lower()
        if key not in seen:
            distributions.append(name)
            seen.add(key)

    return {
        "command": command,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "distributions": distributions,
    }


def install_operator_smoke_wsl_defaults(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve the WSL distribution for this smoke without hard-coding a missing distro."""

    applied: dict[str, str] = {}
    requested = str(getattr(args, "distribution", None) or "").strip()
    env_distribution = (
        os.environ.get("MAIN_COMPUTER_WSL_DISTRIBUTION", "").strip()
        or os.environ.get("RAG_WSL_DISTRIBUTION", "").strip()
    )

    probe: dict[str, Any] | None = None
    source = "cli"
    selected = requested
    reason = "explicit --distribution"

    if not selected and env_distribution:
        selected = env_distribution
        source = "environment"
        reason = "MAIN_COMPUTER_WSL_DISTRIBUTION/RAG_WSL_DISTRIBUTION"
        applied["--distribution"] = selected

    if not selected:
        source = "auto"
        probe = probe_wsl_distributions(str(getattr(args, "wsl_command", None) or "wsl.exe"))
        available = list(probe.get("distributions") or [])
        lower_to_name = {name.lower(): name for name in available}
        for candidate in SMOKE_PREFERRED_WSL_DISTRIBUTIONS:
            if candidate.lower() in lower_to_name:
                selected = lower_to_name[candidate.lower()]
                reason = f"preferred distribution is installed: {candidate}"
                break
        if not selected and len(available) == 1:
            selected = available[0]
            reason = "single installed WSL distribution"
        if not selected:
            selected = ""
            reason = "no preferred WSL distribution found; omit --distribution and use the WSL default"
        applied["--distribution"] = selected or "(omitted; WSL default distribution)"

    args.distribution = selected

    return {
        "marker": "MC_OPERATOR_SMOKE_WSL_DISTRIBUTION_DEFAULTS_V6",
        "policy": "smoke-local WSL distribution resolution only; explicit CLI/env values win",
        "source": source,
        "reason": reason,
        "applied": applied,
        "resolved": {
            "wsl_command": str(getattr(args, "wsl_command", None) or "wsl.exe"),
            "distribution": selected,
            "distribution_argument_omitted": not bool(selected),
        },
        "probe": probe,
    }


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


def fresh_debug_golden_site_id() -> str:
    """Return a unique debug-golden-path-* site id for one smoke run.

    The operator smoke must not reuse the most recently edited debug site by
    default. Reuse makes prompt hardening and over-edit detection stateful: a
    prior successful apply can remove the original edit target or sibling
    content, then the next run no longer tests prompt -> fresh site -> edit.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    entropy = f"{time.time_ns() % 1_000_000_000:09d}"
    site_id = f"{DEBUG_PREFIX}{stamp}-{entropy}"
    if not valid_debug_golden_site_id(site_id):
        raise RuntimeError(f"Generated invalid debug site id: {site_id!r}")
    return site_id


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
        # This smoke only needs disposable website files plus registry metadata.
        # Regenerating the local-platform compose file can fail when another
        # checkout already owns Directus host ports, which is unrelated to the
        # prompt -> editor -> patch -> apply golden path.
        no_compose=True,
    )
    result = tool.ensure_debug_website(args)
    if not result.get("ok"):
        raise RuntimeError(f"debug-website ensure failed: {result}")
    return result


def select_or_create_debug_site(
    builder_root: Path,
    explicit_site: str | None,
    *,
    create_if_missing: bool,
    reuse_latest_debug_site: bool,
) -> tuple[str, dict[str, Any]]:
    if explicit_site:
        site_id = explicit_site.strip().lower()
        source = "explicit_site"
        should_create_if_missing = create_if_missing
    elif reuse_latest_debug_site:
        site_id = newest_existing_debug_site(builder_root) or ""
        source = "most_recent_debug_golden_path_site"
        should_create_if_missing = create_if_missing
        if not site_id:
            if not create_if_missing:
                raise RuntimeError("No existing debug-golden-path-* site found and create_if_missing is disabled.")
            site_id = fresh_debug_golden_site_id()
            source = "created_no_existing_debug_golden_path_site"
            should_create_if_missing = True
    else:
        root = websites_root(builder_root)
        for _attempt in range(10):
            site_id = fresh_debug_golden_site_id()
            if not (root / site_id).exists():
                break
        else:
            raise RuntimeError("Could not allocate a fresh debug-golden-path-* site id for this smoke run.")
        source = "fresh_debug_golden_path_site_per_run"
        should_create_if_missing = True

    if not valid_debug_golden_site_id(site_id):
        raise RuntimeError(
            f"Refusing site {site_id!r}. AI Website Builder editing is currently limited to {DEBUG_PREFIX}* debug sites."
        )
    ensure_payload = ensure_debug_site(builder_root, site_id, create_if_missing=should_create_if_missing)
    return site_id, {
        "source": source,
        "fresh_site_per_run": not bool(explicit_site) and not bool(reuse_latest_debug_site),
        "reuse_latest_debug_site": bool(reuse_latest_debug_site),
        "marker": "MC_OPERATOR_SMOKE_FRESH_DEBUG_SITE_PER_RUN_V7",
        "ensure_payload": ensure_payload,
    }


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

    from dataclasses import replace

    config = MainComputerConfig.from_env()
    # Match the normal app configuration path, but pin the workspace to the
    # parent workspace that contains this builder repo.  MainComputerConfig
    # requires a workspace in the current snapshot; constructing it directly
    # regresses with: "__init__() missing ... workspace".
    try:
        config = replace(config, workspace=root.parent)
    except TypeError:
        config = MainComputerConfig.from_env()
    if model:
        try:
            config = replace(config, model=model)
        except TypeError:
            pass
    computer = MainComputer.build(config)
    provider = getattr(computer, "provider", None)
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
  git add .gitignore site.json index.html style.css script.js builder.json 2>/dev/null || true
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
  # Existing debug sites may have builder metadata drift after debug-website.py
  # ensure rewrites site.json/builder.json.  Baseline only that metadata before
  # the chat handoff.  Dirty website content remains a hard stop.
  disallowed="$(
    printf '%s\n' "$status" | while IFS= read -r line; do
      path="${{line#???}}"
      case "$path" in
        .gitignore|site.json|builder.json) ;;
        *) printf '%s\n' "$line" ;;
      esac
    done
  )"
  if [ -n "$disallowed" ]; then
    printf '%s\n' "$status" >&2
    echo "Selected debug site has dirty content files before chat handoff smoke" >&2
    exit 33
  fi
  git add .gitignore site.json builder.json 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git -c user.name='RAG Smoke' -c user.email='rag-smoke@example.invalid' commit -m 'baseline builder debug website metadata' >/dev/null
  fi
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



def grounding_exact_copy_options(source: str, *, max_options: int = 80) -> list[dict[str, Any]]:
    """Build exact source snippets the grounding model may copy.

    This is a validation rail, not an edit rail: it does not choose the target,
    the replacement, or any patch content. It only gives the grounding repair
    stage mechanically exact substrings so it can stop retyping metadata with
    small typos such as malformed viewport attributes.
    """
    lines = str(source or "").splitlines()
    options: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, start_line: int, text: str) -> None:
        snippet = str(text)
        if not snippet.strip():
            return
        if len(snippet) > 420:
            return
        if snippet in seen:
            return
        seen.add(snippet)
        options.append({"id": f"O{len(options) + 1}", "kind": kind, "line": start_line, "text": snippet})

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) <= 260:
            add("line", index, stripped)

    block_terms = (
        "<!doctype",
        "<html",
        "<head",
        "<meta",
        "<title",
        "<link",
        "<body",
        "<main",
        "<dl",
        "<script",
    )
    for index, line in enumerate(lines):
        if any(term in line.lower() for term in block_terms):
            for width in (2, 3, 4):
                block = "\n".join(lines[index : min(len(lines), index + width)]).strip()
                add(f"{width}_line_block", index + 1, block)

    return options[:max_options]


def install_grounding_exact_copy_prompt_rails() -> dict[str, Any]:
    """Patch the blessed editor prompts in-process with exact-copy repair rails.

    The future app integration should move this into the shared chat/editor
    service. Keeping it here makes the operator smoke prove the needed prefunk
    without requiring a repo patch first.
    """
    import main_computer.rag_debug_website_golden_path_smoke as golden

    if getattr(golden, "_operator_grounding_exact_copy_rails_v4", False):
        return {"installed": False, "reason": "already_installed"}

    original_grounding_prompt = golden.make_grounding_prompt
    original_repair_prompt = golden.make_grounding_validation_repair_prompt

    def append_options(base_prompt: str, evidence: dict[str, Any]) -> str:
        target_file = evidence.get("target_file")
        files = evidence.get("files") if isinstance(evidence.get("files"), dict) else {}
        file_info = files.get(target_file) if isinstance(target_file, str) else {}
        source = file_info.get("content") if isinstance(file_info, dict) else ""
        options = grounding_exact_copy_options(str(source or ""))
        return (
            base_prompt
            + "\n\nEXACT_COPY_OPTIONS_FOR_GROUNDING_REPAIR:\n"
            + json.dumps(options, ensure_ascii=False, indent=2)
            + "\n\nAdditional grounding rules:\n"
            + "- For evidence_exact_text and preserve[].evidence_exact_text, copy exact text from SOURCE.\n"
            + "- Prefer copying one of EXACT_COPY_OPTIONS_FOR_GROUNDING_REPAIR verbatim for preservation evidence.\n"
            + "- Do not retype metadata from memory. Do not normalize whitespace inside copied evidence.\n"
            + "- If validation says an evidence string is not in SOURCE, replace it with an exact option above.\n"
        )

    def make_grounding_prompt_with_options(evidence: dict[str, Any]) -> str:
        return append_options(original_grounding_prompt(evidence), evidence)

    def make_grounding_validation_repair_prompt_with_options(
        *,
        evidence: dict[str, Any],
        previous_card: dict[str, Any] | None,
        validation_report: dict[str, Any],
    ) -> str:
        return append_options(
            original_repair_prompt(
                evidence=evidence,
                previous_card=previous_card,
                validation_report=validation_report,
            ),
            evidence,
        )

    golden.make_grounding_prompt = make_grounding_prompt_with_options
    golden.make_grounding_validation_repair_prompt = make_grounding_validation_repair_prompt_with_options
    golden._operator_grounding_exact_copy_rails_v4 = True
    return {"installed": True, "option_builder": "grounding_exact_copy_options", "version": "v4"}



def install_ai_workspace_line_ending_stability_rails() -> dict[str, Any]:
    """Normalize copied AI-workspace text files to LF before discovery.

    This smoke runs from Windows against Website Builder files, which may be
    CRLF on disk. The blessed editor discovery code stores a text SHA-256 after
    Python universal-newline decoding, while full-file promotion compares the
    target file's byte SHA-256. If the copied AI workspace keeps CRLF bytes,
    promotion can falsely report that the target file changed after discovery.

    The real selected site is not modified here. Only the temporary AI workspace
    copy is normalized, so discovery, grounding, patch proposal, and promotion
    all reason about the same bytes.
    """
    import main_computer.rag_debug_website_golden_path_smoke as golden

    if getattr(golden, "_operator_ai_workspace_line_ending_stability_v5", False):
        return {"installed": False, "reason": "already_installed", "version": "v5"}

    original_copy = golden.copy_debug_site_to_ai_workspace

    def copy_debug_site_to_ai_workspace_lf_normalized(*, source_site_dir: Path, ai_repo: Path, root: Path) -> None:
        original_copy(source_site_dir=source_site_dir, ai_repo=ai_repo, root=root)
        normalized: list[str] = []
        for name in ("site.json", "index.html", "style.css", "script.js"):
            path = ai_repo / name
            if not path.exists() or not path.is_file():
                continue
            data = path.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            lf_text = text.replace("\r\n", "\n").replace("\r", "\n")
            if lf_text != text:
                path.write_text(lf_text, encoding="utf-8", newline="\n")
                normalized.append(name)
        golden._operator_ai_workspace_line_ending_stability_last = {
            "version": "v5",
            "normalized_files": normalized,
            "ai_workspace": str(ai_repo),
        }

    golden.copy_debug_site_to_ai_workspace = copy_debug_site_to_ai_workspace_lf_normalized
    golden._operator_ai_workspace_line_ending_stability_v5 = True
    golden._operator_ai_workspace_line_ending_stability_original = original_copy
    return {
        "installed": True,
        "version": "v5",
        "marker": "MC_OPERATOR_SMOKE_AI_WORKSPACE_LF_HASH_STABILITY_V5",
        "scope": "temporary_ai_workspace_only",
    }



def compact_blessed_failure(blessed: dict[str, Any]) -> dict[str, Any]:
    """Return the high-signal blessed editor failure without flooding stdout."""
    result: dict[str, Any] = {
        "ok": blessed.get("ok"),
        "mode": blessed.get("mode"),
        "selected_target_file": blessed.get("selected_target_file"),
        "output_root": blessed.get("output_root"),
    }
    for key in ("discovery", "grounding", "patch_proposal", "full_file_promotion", "artifact_packaging", "terminal_result"):
        value = blessed.get(key)
        if isinstance(value, dict):
            result[key] = {
                "ok": value.get("ok"),
                "issues": (value.get("issues") or [])[:8],
                "blocking_reasons": (value.get("blocking_reasons") or [])[:8],
                "warnings": (value.get("warnings") or [])[:5],
                "repair_attempt_count": value.get("repair_attempt_count"),
                "failed_gate": value.get("failed_gate"),
            }
    return result



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
    import main_computer.rag_debug_website_golden_path_smoke as golden

    prompt_rails = install_grounding_exact_copy_prompt_rails()
    line_ending_rails = install_ai_workspace_line_ending_stability_rails()
    output_root = Path(tempfile.gettempdir()) / "mc_chat_website_builder_handoff" / time.strftime("%Y%m%d_%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    editor_args = make_editor_args(args)
    report = golden.run_blessed_generated_editor_patch_artifact(
        root=root,
        source_site_dir=site_path,
        request=request,
        output_root=output_root,
        args=editor_args,
    )
    report["output_root"] = str(output_root)
    report["operator_prompt_rails"] = prompt_rails
    report["operator_line_ending_rails"] = line_ending_rails
    try:
        import main_computer.rag_debug_website_golden_path_smoke as golden
        report["operator_ai_workspace_line_ending_stability"] = getattr(
            golden,
            "_operator_ai_workspace_line_ending_stability_last",
            {},
        )
    except Exception:
        report["operator_ai_workspace_line_ending_stability"] = {}
    return report


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.builder_root).resolve() if args.builder_root else repo_root()
    add_repo_to_path(root)
    user_request = read_prompt_from_args(args)

    from main_computer.rag_debug_website_golden_path_smoke import host_path_to_wsl

    site_id, selection = select_or_create_debug_site(
        root,
        args.site,
        create_if_missing=bool(args.create_if_missing),
        reuse_latest_debug_site=bool(args.reuse_latest_debug_site),
    )
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
            "blessed_summary": compact_blessed_failure(blessed),
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
    parser = argparse.ArgumentParser(description="PowerShell-friendly smoke: simulate Website Builder chat app handoff against a fresh debug-golden-path-* site.")
    parser.add_argument("prompt_parts", nargs="*", help="Runtime user prompt text. Alternative to --prompt or --prompt-file.")
    parser.add_argument("--site", default=None, help="Optional debug-golden-path-* site id override. Existing explicit sites are reused; add --create-if-missing to create a missing explicit site.")
    parser.add_argument("--builder-root", default=None, help="Website Builder repo root. Defaults to the repo containing this script.")
    parser.add_argument("--prompt", default=None, help="Runtime user prompt to simulate from the Website Builder chat app.")
    parser.add_argument("--prompt-file", default=None, help="Read the runtime user prompt from a UTF-8 text file.")
    parser.add_argument("--create-if-missing", action="store_true", help="Create a missing explicit --site, or create one if --reuse-latest-debug-site finds no existing debug site.")
    parser.add_argument("--reuse-latest-debug-site", action="store_true", help="Opt into the old stateful behavior: target the most recently modified existing debug-golden-path-* site instead of creating a fresh site per run.")
    parser.add_argument("--dry-run-only", action="store_true", help="Stop after new_patch.py dry-run.")
    parser.add_argument("--wsl-command", default="wsl.exe")
    parser.add_argument("--distribution", default=None, help="WSL distribution override. Defaults to MAIN_COMPUTER_WSL_DISTRIBUTION/RAG_WSL_DISTRIBUTION, then auto-detects or uses the WSL default.")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--ai-timeout-seconds", type=int, default=600)
    parser.add_argument("--model", default=None)
    parser.add_argument("--ollama-url", default=None)
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
    operator_smoke_ollama_defaults = install_operator_smoke_ollama_defaults(args)
    operator_smoke_wsl_defaults = install_operator_smoke_wsl_defaults(args)
    try:
        report = evaluate(args)
    except Exception as exc:
        report = {
            "ok": False,
            "mode": "chat_app_website_builder_latest_debug_site_operator_smoke",
            "failed_stage": "unhandled_exception",
            "error": str(exc),
        }
    report["operator_smoke_ollama_defaults"] = operator_smoke_ollama_defaults
    report["operator_smoke_wsl_defaults"] = operator_smoke_wsl_defaults
    json_print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
