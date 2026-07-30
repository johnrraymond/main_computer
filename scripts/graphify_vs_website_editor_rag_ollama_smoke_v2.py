#!/usr/bin/env python3
"""A/B smoke for the mounted Website Builder RAG path.

This benchmark compares two retrieval/index lanes while keeping the rest of the
real generated-editor pipeline identical:

Baseline
    build_selected_site_index (the mounted route's prompt-agnostic structural index)

Graphify
    Graphify extract/query over the staged selected site, then a site_index with
    the same schema containing only graph-selected candidate files

Both lanes then run the repository's exact:
    terminal decision -> verified anchors -> grounding -> patch proposal ->
    deterministic validation -> full-file promotion -> snapshot ZIP ->
    new_patch.py --dry-run

Model calls in both lanes are transported through the repository's
main_computer.providers.ollama.OllamaProvider. This is a benchmark-only transport
adapter: prompts, validators, promotion, packaging, and dry-run remain the real
Website Builder generated-editor code.

The scenario fixture is created from tools/local-platform/debug-website.py, the
same fresh debug-site code used by the open-ended Website Builder operator smoke.
The postcondition oracle is never passed to either model lane.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
import zipfile


class BenchmarkFailure(RuntimeError):
    """A deterministic benchmark setup or execution failure."""


@dataclass(frozen=True)
class Scenario:
    id: str
    endstate: str
    prompt_template: str
    expected_target: str | None
    expected_evidence: tuple[str, ...]
    description: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="homepage_copy_edit",
        endstate="edit",
        prompt_template=(
            "Update this Website Builder site's visible homepage status sentence "
            "so it says exactly: \"Operator readiness confirmed: {token}\". "
            "Preserve the existing heading, page metadata, stylesheet link, script "
            "link, debug details, and all non-homepage assets. Do not tell me which "
            "file you chose; inspect the selected site and produce the normal "
            "proposal-only generated-editor artifact."
        ),
        expected_target="index.html",
        expected_evidence=("index.html",),
        description="Open-ended visible-copy edit without a target path or source anchor.",
    ),
    Scenario(
        id="client_status_control_edit",
        endstate="edit",
        prompt_template=(
            "Add a small client-side operator status control to this selected site. "
            "It must appear on the page without reloading, start at Ready, and toggle "
            "between Ready and Paused when activated. Preserve the existing HTML, "
            "stylesheet, manifest, builder metadata, and original debug-ready log. "
            "Choose the correct file and return the normal proposal-only "
            "generated-editor artifact."
        ),
        expected_target="script.js",
        expected_evidence=("script.js",),
        description="Open-ended behavior edit constrained to the existing client script.",
    ),
    Scenario(
        id="site_surface_info",
        endstate="info",
        prompt_template=(
            "Without changing this selected Website Builder site, explain which files "
            "control the visible homepage content, the visual styling, and the browser "
            "behavior. Cite exact evidence from the selected site and do not produce "
            "a patch or replacement payload."
        ),
        expected_target=None,
        expected_evidence=("index.html", "style.css", "script.js"),
        description="Open-ended grounded information request over the same site code.",
    ),
)


GRAPHIFY_SOURCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[src=(?P<path>.+?)\s+loc=", re.IGNORECASE),
    re.compile(r"^\s*Source:\s*(?P<path>.+?)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"(?P<path>(?:[A-Za-z]:[\\/])?[^ \t\r\n<>'\"]+?\.(?:html|css|js|mjs|json|md|ts|tsx|py))",
        re.IGNORECASE,
    ),
)

STOPWORDS = {
    "about", "after", "again", "against", "also", "and", "before", "between",
    "builder", "change", "changing", "client", "code", "correct", "do", "exact",
    "file", "files", "for", "from", "generated", "homepage", "into", "must",
    "normal", "not", "only", "page", "preserve", "produce", "proposal", "selected",
    "site", "the", "their", "this", "through", "to", "use", "website", "which",
    "with", "without", "you",
}

REQUIRED_FILES: tuple[str, ...] = (
    "new_patch.py",
    "main_computer/website_builder_generated_editor_pipeline.py",
    "main_computer/viewport_routes_applications.py",
    "main_computer/rag_website_builder_real_edit_smoke.py",
    "main_computer/rag_chat_website_builder_operator_smoke_v5.py",
    "main_computer/rag_debug_website_golden_path_smoke.py",
    "main_computer/providers/ollama.py",
    "main_computer/models.py",
    "tools/local-platform/debug-website.py",
    "tests/test_debug_website_golden_path_no_deterministic_cheats.py",
    "tests/test_website_builder_app.py",
)


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("website_editor_graphify_%Y%m%dT%H%M%SZ")


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(json_safe(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("'\"")
    text = re.sub(r"/+", "/", text)
    while text.startswith("./"):
        text = text[2:]
    return text


def safe_zip_members(path: Path) -> tuple[bool, list[str]]:
    members: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            normalized = normalize_path(name)
            parts = PurePosixPath(normalized).parts
            if not normalized or normalized.startswith("/") or ".." in parts:
                return False, members
            members.append(normalized)
    return True, members


def resolve_repo(value: str) -> Path:
    repo = Path(value).resolve()
    if not repo.is_dir():
        raise BenchmarkFailure(f"Repository does not exist: {repo}")
    missing = [relative for relative in REQUIRED_FILES if not (repo / relative).is_file()]
    if missing:
        raise BenchmarkFailure(
            "Repository is missing required Website Builder benchmark files:\n"
            + "\n".join(f"  - {item}" for item in missing)
        )
    return repo


def source_contract_report(repo: Path) -> dict[str, Any]:
    pipeline_source = (repo / "main_computer/website_builder_generated_editor_pipeline.py").read_text(
        encoding="utf-8", errors="replace"
    )
    route_source = (repo / "main_computer/viewport_routes_applications.py").read_text(
        encoding="utf-8", errors="replace"
    )
    real_smoke_source = (repo / "main_computer/rag_website_builder_real_edit_smoke.py").read_text(
        encoding="utf-8", errors="replace"
    )
    operator_source = (repo / "main_computer/rag_chat_website_builder_operator_smoke_v5.py").read_text(
        encoding="utf-8", errors="replace"
    )

    assertions = {
        "mounted_route_imports_real_pipeline": (
            "run_generated_editor_pipeline as run_website_builder_generated_editor_pipeline"
            in route_source
        ),
        "mounted_route_calls_real_pipeline": (
            "pipeline_report = run_website_builder_generated_editor_pipeline(" in route_source
        ),
        "real_smoke_calls_same_pipeline": "pipeline_report = run_generated_editor_pipeline(" in real_smoke_source,
        "baseline_index_is_prompt_agnostic": (
            '"prompt_used_for_indexing": False' in pipeline_source
            and "The prompt is intentionally not an input." in pipeline_source
        ),
        "pipeline_runs_artifact_dry_run": (
            "package_full_file_replacement_snapshot_artifact(" in pipeline_source
            and "run_new_patch_dry_run(" in pipeline_source
        ),
        "operator_smoke_has_no_default_edit": "There is intentionally no default edit request." in operator_source,
        "operator_smoke_creates_fresh_site": "fresh_debug_golden_site_id()" in operator_source,
        "operator_smoke_calls_generated_editor": "run_blessed_generated_editor_patch_artifact(" in operator_source,
    }
    return {
        "ok": all(assertions.values()),
        "assertions": assertions,
        "pipeline_sha256": sha256_file(repo / "main_computer/website_builder_generated_editor_pipeline.py"),
        "route_sha256": sha256_file(repo / "main_computer/viewport_routes_applications.py"),
        "real_smoke_sha256": sha256_file(repo / "main_computer/rag_website_builder_real_edit_smoke.py"),
        "operator_smoke_sha256": sha256_file(repo / "main_computer/rag_chat_website_builder_operator_smoke_v5.py"),
    }


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "argv": list(argv),
            "returncode": completed.returncode,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(argv),
            "returncode": 124,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": str(exc.stdout or ""),
            "stderr": f"Timed out after {timeout} seconds.",
        }
    except OSError as exc:
        return {
            "argv": list(argv),
            "returncode": 127,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def run_contract_tests(
    repo: Path,
    timeout: float,
    *,
    strict_route_contracts: bool = False,
) -> dict[str, Any]:
    """Run benchmark-critical contracts separately from mounted-route policy checks.

    The A/B benchmark exercises the proposal-only generated-editor pipeline directly.
    Therefore anti-cheat guarantees and site-scope locking are blocking.  The mounted
    route's default apply/propose policy is still useful drift detection, but it is
    advisory unless --strict-route-contract-tests is requested.
    """

    blocking_tests = [
        "tests/test_debug_website_golden_path_no_deterministic_cheats.py",
        "tests/test_website_builder_app.py::test_website_builder_chat_edit_route_is_locked_to_site_scope",
    ]
    advisory_tests = [
        "tests/test_website_builder_app.py::test_website_builder_chat_edit_route_applies_generated_editor_payloads_by_default",
    ]

    blocking = run_process(
        [sys.executable, "-m", "pytest", "-q", *blocking_tests],
        cwd=repo,
        timeout=timeout,
    )
    advisory = run_process(
        [sys.executable, "-m", "pytest", "-q", *advisory_tests],
        cwd=repo,
        timeout=timeout,
    )

    blocking_ok = blocking["returncode"] == 0
    advisory_ok = advisory["returncode"] == 0
    overall_ok = blocking_ok and (advisory_ok or not strict_route_contracts)

    return {
        "ok": overall_ok,
        "blocking_ok": blocking_ok,
        "advisory_ok": advisory_ok,
        "strict_route_contracts": strict_route_contracts,
        "blocking": {**blocking, "tests": blocking_tests},
        "advisory": {**advisory, "tests": advisory_tests},
    }


def git_tracked_hashes(repo: Path) -> dict[str, str]:
    result = run_process(["git", "ls-files", "-z"], cwd=repo, timeout=60)
    if result["returncode"] != 0:
        raise BenchmarkFailure(
            "git ls-files failed; source non-mutation cannot be verified exactly:\n"
            + (result["stderr"] or result["stdout"])
        )
    hashes: dict[str, str] = {}
    for item in result["stdout"].split("\0"):
        relative = normalize_path(item)
        if not relative:
            continue
        path = repo / relative
        if path.is_file():
            hashes[relative] = sha256_file(path)
    return hashes


def changed_tracked_files(repo: Path, before: Mapping[str, str]) -> list[str]:
    changed: list[str] = []
    for relative, digest in before.items():
        path = repo / relative
        if not path.is_file() or sha256_file(path) != digest:
            changed.append(relative)
    return changed


def load_debug_website_tool(repo: Path) -> Any:
    path = repo / "tools/local-platform/debug-website.py"
    spec = importlib.util.spec_from_file_location("graphify_website_editor_debug_tool", path)
    if spec is None or spec.loader is None:
        raise BenchmarkFailure(f"Could not load debug website tool: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_fresh_debug_site_fixture(
    *,
    repo: Path,
    target: Path,
    site_id: str,
    purpose: str,
) -> dict[str, Any]:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    tool = load_debug_website_tool(repo)

    tool.write_debug_homepage(
        target,
        site_id=site_id,
        purpose=purpose,
        bootstrap=False,
        overwrite=True,
    )
    tool.write_debug_styles(target, overwrite=True)
    tool.write_debug_script(target, site_id=site_id, overwrite=True)
    tool.write_debug_builder_state(
        target,
        site_id=site_id,
        purpose=purpose,
        bootstrap=False,
        overwrite=True,
    )
    manifest = tool.debug_manifest(
        site_id,
        purpose=purpose,
        bootstrap=False,
        name=f"Graphify Website Editor Smoke {site_id}",
    )
    write_json(target / "site.json", manifest)

    files: dict[str, dict[str, Any]] = {}
    for path in sorted(target.rglob("*")):
        if path.is_file():
            relative = path.relative_to(target).as_posix()
            files[relative] = {
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    return {
        "site_id": site_id,
        "purpose": purpose,
        "file_count": len(files),
        "files": files,
        "source": "tools/local-platform/debug-website.py",
    }


def clone_fixture(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def parse_command(value: str) -> list[str]:
    parts = shlex.split(value, posix=(os.name != "nt"))
    cleaned = [part.strip('"') for part in parts if part.strip('"')]
    if not cleaned:
        raise BenchmarkFailure("--graphify-cmd must not be empty.")
    return cleaned


def graphify_candidates(explicit: str | None) -> list[list[str]]:
    if explicit:
        return [parse_command(explicit)]
    candidates: list[list[str]] = []
    executable = shutil.which("graphify")
    if executable:
        candidates.append([executable])
    candidates.append([sys.executable, "-m", "graphify"])
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def resolve_graphify_command(
    *,
    explicit: str | None,
    repo: Path,
    timeout: float,
) -> tuple[list[str], str, list[dict[str, Any]]]:
    probes: list[dict[str, Any]] = []
    for candidate in graphify_candidates(explicit):
        result = run_process([*candidate, "--version"], cwd=repo, timeout=min(timeout, 30))
        probes.append(result)
        if result["returncode"] == 0:
            version = (result["stdout"] or result["stderr"]).strip()
            return candidate, version, probes
    details = "\n".join(
        f"{' '.join(item['argv'])} -> {item['returncode']}: "
        f"{(item['stderr'] or item['stdout']).strip()}"
        for item in probes
    )
    raise BenchmarkFailure(f"No usable Graphify command found.\n{details}")


def resolve_ollama_base_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if text.endswith("/api/generate"):
        text = text[: -len("/api/generate")]
    if text.endswith("/api/chat"):
        text = text[: -len("/api/chat")]
    return text or "http://127.0.0.1:11434"


def urllib_json(url: str, timeout: float) -> Any:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_preflight(base_url: str, model: str, timeout: float) -> dict[str, Any]:
    version = urllib_json(f"{base_url.rstrip('/')}/api/version", min(timeout, 30))
    tags = urllib_json(f"{base_url.rstrip('/')}/api/tags", min(timeout, 30))
    names = [
        str(item.get("name") or item.get("model") or "")
        for item in (tags.get("models") if isinstance(tags, dict) else [])
        if isinstance(item, dict)
    ]
    listed = model in names or any(name.split(":", 1)[0] == model.split(":", 1)[0] for name in names)
    return {
        "ok": bool(listed),
        "base_url": base_url,
        "version": version,
        "requested_model": model,
        "requested_model_listed": listed,
        "installed_models": names,
    }


def parse_think_mode(value: str) -> bool | str | None:
    text = str(value or "").strip().lower()
    if text in {"", "omit", "none"}:
        return None
    if text in {"false", "0", "off", "no"}:
        return False
    if text in {"true", "1", "on", "yes"}:
        return True
    if text in {"low", "medium", "high"}:
        return text
    raise BenchmarkFailure(f"Unsupported think mode: {value}")


def provider_warmup(
    *,
    repo: Path,
    base_url: str,
    model: str,
    timeout: float,
    think_mode: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    ensure_repo_imports(repo)
    from main_computer.models import ChatMessage
    from main_computer.providers.ollama import OllamaProvider

    events: list[dict[str, Any]] = []
    provider = OllamaProvider(
        model=model,
        base_url=base_url,
        timeout_s=timeout,
        options={"temperature": 0, "num_predict": 64},
        think=parse_think_mode(think_mode),
        stream_callback=lambda event: events.append(json_safe(event)),
        diagnostic_log_file=str(artifact_dir / "ollama-provider-warmup.jsonl"),
        diagnostic_run_id=artifact_dir.name,
        diagnostic_label="website_editor_benchmark_warmup",
    )
    started = time.perf_counter()
    response = provider.chat(
        [
            ChatMessage(
                role="user",
                content='Return exactly this JSON object and nothing else: {"ok":true,"transport":"OllamaProvider"}',
            )
        ]
    )
    elapsed = round(time.perf_counter() - started, 3)
    write_json(artifact_dir / "ollama-provider-warmup-events.json", events)
    write_text(artifact_dir / "ollama-provider-warmup-response.txt", response.content)
    try:
        parsed = json.loads(response.content)
    except json.JSONDecodeError:
        parsed = None
    counts = collections.Counter(str(item.get("type") or item.get("stream_event_type") or "unknown") for item in events)
    return {
        "ok": isinstance(parsed, dict) and parsed.get("ok") is True,
        "elapsed_seconds": elapsed,
        "response": response.content,
        "metadata": json_safe(response.metadata),
        "event_counts": dict(sorted(counts.items())),
        "parsed": parsed,
    }


def ensure_repo_imports(repo: Path) -> None:
    for path in (repo, repo / "main_computer"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def import_pipeline(repo: Path) -> Any:
    ensure_repo_imports(repo)
    return importlib.import_module("main_computer.website_builder_generated_editor_pipeline")


def provider_call_model_json_factory(
    *,
    repo: Path,
    base_url: str,
    artifact_dir: Path,
    temperature: float,
) -> Callable[..., tuple[dict[str, Any] | None, dict[str, Any], str]]:
    ensure_repo_imports(repo)
    from main_computer.models import ChatMessage
    from main_computer.providers.ollama import OllamaProvider

    pipeline = import_pipeline(repo)

    def call_model_json(
        *,
        stage_name: str,
        prompt: str,
        output_dir: Path,
        model: str,
        ollama_url: str,
        timeout_seconds: float,
        num_predict: int,
        format_mode: str,
        think_mode: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
        del ollama_url
        output_dir.mkdir(parents=True, exist_ok=True)
        pipeline.write_text(output_dir / f"{stage_name}_prompt.txt", prompt)
        raw_path = output_dir / f"{stage_name}_raw.txt"
        events: list[dict[str, Any]] = []
        stage_started = time.perf_counter()

        provider = OllamaProvider(
            model=model,
            base_url=base_url,
            timeout_s=timeout_seconds,
            options={
                "temperature": temperature,
                "num_predict": num_predict,
            },
            think=parse_think_mode(think_mode),
            stream_callback=lambda event: events.append(json_safe(event)),
            diagnostic_log_file=str(artifact_dir / "ollama-provider-model-io.jsonl"),
            diagnostic_run_id=artifact_dir.name,
            diagnostic_label=stage_name,
        )

        try:
            response = provider.chat([ChatMessage(role="user", content=prompt)])
        except Exception as exc:
            pipeline.write_text(raw_path, "")
            diagnostics = {
                "transport": "main_computer.providers.ollama.OllamaProvider",
                "request": {
                    "model": model,
                    "base_url": base_url,
                    "format_mode_requested": format_mode,
                    "num_predict": num_predict,
                    "think_mode": think_mode,
                    "prompt_length": len(prompt),
                    "prompt_sha256": pipeline.sha256_text(prompt),
                },
                "elapsed_seconds": round(time.perf_counter() - stage_started, 3),
                "stream_events": events,
                "error": f"{type(exc).__name__}: {exc}",
            }
            pipeline.write_json(output_dir / f"{stage_name}_model_call.json", diagnostics)
            return None, {
                "stage": stage_name,
                "ok": False,
                "raw_path": str(raw_path),
                "raw": pipeline.raw_summary(""),
                "model_call": diagnostics,
                "parse_error": None,
                "call_error": diagnostics["error"],
            }, ""

        raw = response.content
        pipeline.write_text(raw_path, raw)
        diagnostics = {
            "transport": "main_computer.providers.ollama.OllamaProvider",
            "request": {
                "model": model,
                "base_url": base_url,
                "format_mode_requested": format_mode,
                "num_predict": num_predict,
                "think_mode": think_mode,
                "prompt_length": len(prompt),
                "prompt_sha256": pipeline.sha256_text(prompt),
            },
            "elapsed_seconds": round(time.perf_counter() - stage_started, 3),
            "response": {
                "provider": response.provider,
                "model": response.model,
                "metadata": json_safe(response.metadata),
                "content_chars": len(raw),
            },
            "stream_events": events,
        }
        pipeline.write_json(output_dir / f"{stage_name}_model_call.json", diagnostics)

        report: dict[str, Any] = {
            "stage": stage_name,
            "ok": False,
            "raw_path": str(raw_path),
            "raw": pipeline.raw_summary(raw),
            "model_call": diagnostics,
            "parse_error": None,
        }
        try:
            parsed = pipeline.extract_json_object(raw)
        except Exception as exc:
            report["parse_error"] = f"{type(exc).__name__}: {exc}"
            return None, report, raw

        report["ok"] = True
        report["parsed_keys"] = sorted(str(key) for key in parsed.keys())
        pipeline.write_json(output_dir / f"{stage_name}_parsed.json", parsed)
        return parsed, report, raw

    return call_model_json


@contextlib.contextmanager
def patched_pipeline(
    pipeline: Any,
    *,
    call_model_json: Callable[..., Any],
    build_index: Callable[..., Any] | None = None,
) -> Iterator[None]:
    original_call = pipeline.call_model_json
    original_index = pipeline.build_selected_site_index
    pipeline.call_model_json = call_model_json
    if build_index is not None:
        pipeline.build_selected_site_index = build_index
    try:
        yield
    finally:
        pipeline.call_model_json = original_call
        pipeline.build_selected_site_index = original_index


def graph_edge_collection(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if isinstance(payload.get("links"), list):
        return [item for item in payload["links"] if isinstance(item, dict)], "links"
    if isinstance(payload.get("edges"), list):
        return [item for item in payload["edges"] if isinstance(item, dict)], "edges"
    return [], "missing"


def graph_node_id(node: Mapping[str, Any]) -> str:
    return str(node.get("id") or node.get("key") or "").strip()


def graph_node_label(node: Mapping[str, Any]) -> str:
    return str(node.get("label") or node.get("name") or node.get("title") or graph_node_id(node)).strip()


def graph_node_source(node: Mapping[str, Any]) -> str:
    return normalize_path(node.get("source_file") or node.get("source") or node.get("path") or "")


def graph_edge_endpoint(edge: Mapping[str, Any], key: str) -> str:
    value = edge.get(key)
    if isinstance(value, Mapping):
        value = value.get("id") or value.get("key") or value.get("label")
    return str(value or "").strip()


def prompt_tokens(prompt: str) -> list[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", prompt)
    return sorted(
        {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", expanded)
            if len(token) >= 3 and token.lower() not in STOPWORDS
        }
    )


def suffix_candidate(path: str, candidates: Sequence[str]) -> str | None:
    normalized = normalize_path(path).lower().strip("/")
    for candidate in candidates:
        candidate_norm = normalize_path(candidate).lower().strip("/")
        if normalized == candidate_norm or normalized.endswith("/" + candidate_norm):
            return candidate
    return None


def query_source_paths(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for pattern in GRAPHIFY_SOURCE_PATTERNS:
        for match in pattern.finditer(text):
            value = normalize_path(match.group("path")).strip(" ,;:()[]{}")
            if value and value not in seen:
                seen.add(value)
                paths.append(value)
    return paths


def locate_graph_json(output_root: Path) -> Path:
    preferred = output_root / "graphify-out" / "graph.json"
    if preferred.is_file():
        return preferred
    matches = sorted(output_root.rglob("graph.json"))
    if not matches:
        raise BenchmarkFailure(f"Graphify did not create graph.json under {output_root}")
    return matches[0]


def graphify_index_builder_factory(
    *,
    original_builder: Callable[..., dict[str, Any]],
    command: Sequence[str],
    prompt: str,
    output_dir: Path,
    repo: Path,
    timeout: float,
    budget: int,
    max_selected_files: int,
) -> Callable[..., dict[str, Any]]:
    def build_selected_site_index(
        *,
        workspace: Path,
        max_files: int,
        max_file_chars: int,
    ) -> dict[str, Any]:
        baseline = original_builder(
            workspace=workspace,
            max_files=max_files,
            max_file_chars=max_file_chars,
        )
        candidates = [
            item
            for item in baseline.get("candidate_files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        candidate_paths = [normalize_path(item["path"]) for item in candidates]
        if not candidates:
            raise BenchmarkFailure("The selected-site structural index contained no candidate files.")

        graphify_root = output_dir / "graphify_retrieval"
        extract_root = graphify_root / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        extract = run_process(
            [
                *command,
                "extract",
                str(workspace),
                "--code-only",
                "--no-cluster",
                "--out",
                str(extract_root),
                "--max-workers",
                "1",
                "--timing",
            ],
            cwd=repo,
            timeout=timeout,
        )
        write_json(graphify_root / "extract_command.json", extract)
        if extract["returncode"] != 0:
            raise BenchmarkFailure(
                "Graphify selected-site extraction failed:\n"
                + (extract["stderr"] or extract["stdout"])
            )

        graph_path = locate_graph_json(extract_root)
        payload = json.loads(graph_path.read_text(encoding="utf-8"))
        nodes = [item for item in payload.get("nodes", []) if isinstance(item, dict)]
        edges, edge_key = graph_edge_collection(payload)
        if not nodes:
            raise BenchmarkFailure("Graphify selected-site graph contains no nodes.")

        query = run_process(
            [
                *command,
                "query",
                prompt,
                "--budget",
                str(budget),
                "--graph",
                str(graph_path),
            ],
            cwd=repo,
            timeout=timeout,
        )
        write_json(graphify_root / "query_command.json", query)
        write_text(graphify_root / "query_output.txt", query["stdout"] + ("\n" + query["stderr"] if query["stderr"] else ""))
        if query["returncode"] != 0:
            raise BenchmarkFailure(
                "Graphify selected-site query failed:\n"
                + (query["stderr"] or query["stdout"])
            )

        direct_paths = query_source_paths(query["stdout"] + "\n" + query["stderr"])
        direct_candidates = [
            matched
            for path in direct_paths
            if (matched := suffix_candidate(path, candidate_paths)) is not None
        ]

        tokens = prompt_tokens(prompt)
        by_id = {graph_node_id(node): node for node in nodes if graph_node_id(node)}
        node_scores: dict[str, float] = {}
        file_scores: dict[str, float] = collections.defaultdict(float)

        for node in nodes:
            identifier = graph_node_id(node)
            if not identifier:
                continue
            label = graph_node_label(node)
            source = graph_node_source(node)
            matched_candidate = suffix_candidate(source, candidate_paths)
            haystack = f"{identifier} {label} {source}".lower()
            score = 0.0
            for token in tokens:
                if token in label.lower():
                    score += 6.0
                elif token in identifier.lower():
                    score += 4.0
                elif token in source.lower():
                    score += 2.0
            if matched_candidate in direct_candidates:
                score += 100.0
            if score:
                node_scores[identifier] = score
                if matched_candidate:
                    file_scores[matched_candidate] += score

        adjacency: dict[str, list[str]] = collections.defaultdict(list)
        for edge in edges:
            left = graph_edge_endpoint(edge, "source")
            right = graph_edge_endpoint(edge, "target")
            if left in by_id and right in by_id:
                adjacency[left].append(right)
                adjacency[right].append(left)

        for identifier, score in list(node_scores.items()):
            for neighbor in adjacency.get(identifier, []):
                neighbor_node = by_id.get(neighbor)
                if neighbor_node is None:
                    continue
                candidate = suffix_candidate(graph_node_source(neighbor_node), candidate_paths)
                if candidate:
                    file_scores[candidate] += min(8.0, score * 0.12)

        for rank, candidate in enumerate(direct_candidates):
            file_scores[candidate] += max(1.0, 80.0 - rank)

        if not file_scores:
            # This is a graph-only fallback: choose files represented by the
            # highest-degree graph nodes. It does not scan source content.
            degrees = collections.Counter()
            for edge in edges:
                degrees[graph_edge_endpoint(edge, "source")] += 1
                degrees[graph_edge_endpoint(edge, "target")] += 1
            for identifier, degree in degrees.most_common():
                node = by_id.get(identifier)
                if node is None:
                    continue
                candidate = suffix_candidate(graph_node_source(node), candidate_paths)
                if candidate:
                    file_scores[candidate] += math.log2(degree + 2)

        ordered_paths = [
            path
            for path, _score in sorted(
                file_scores.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        selected_paths = ordered_paths[: max(1, min(max_selected_files, max_files))]
        if not selected_paths:
            raise BenchmarkFailure(
                "Graphify did not select any staged site files. No structural-index fallback was used."
            )

        by_path = {normalize_path(item["path"]): item for item in candidates}
        selected_files = [by_path[path] for path in selected_paths if path in by_path]
        top_node_ids = [
            identifier
            for identifier, _score in sorted(node_scores.items(), key=lambda item: (-item[1], item[0]))
        ][:3]

        explain_results: list[dict[str, Any]] = []
        for identifier in top_node_ids:
            result = run_process(
                [*command, "explain", identifier, "--graph", str(graph_path)],
                cwd=repo,
                timeout=timeout,
            )
            explain_results.append(result)
        write_json(graphify_root / "explain_commands.json", explain_results)

        path_probe: dict[str, Any] | None = None
        if len(top_node_ids) >= 2:
            path_probe = run_process(
                [
                    *command,
                    "path",
                    top_node_ids[0],
                    top_node_ids[1],
                    "--graph",
                    str(graph_path),
                ],
                cwd=repo,
                timeout=timeout,
            )
            write_json(graphify_root / "path_command.json", path_probe)

        report = {
            "mode": "selected_site_graphify_index",
            "workspace": str(workspace),
            "prompt_used_for_indexing": True,
            "candidate_count": len(selected_files),
            "candidate_files": selected_files,
            "skipped_files": baseline.get("skipped_files", []),
            "bounds": {
                "max_files": max_files,
                "max_file_chars": max_file_chars,
                "graphify_max_selected_files": max_selected_files,
                "graphify_budget": budget,
            },
            "graphify": {
                "graph_path": str(graph_path),
                "node_count": len(nodes),
                "edge_count": len(edges),
                "edge_collection_key": edge_key,
                "query_source_paths": direct_paths,
                "direct_candidate_paths": direct_candidates,
                "selected_paths": selected_paths,
                "file_scores": {
                    path: round(score, 3)
                    for path, score in sorted(file_scores.items(), key=lambda item: (-item[1], item[0]))
                },
                "top_node_ids": top_node_ids,
                "path_probe": path_probe,
                "structural_candidate_count_before_graphify": len(candidates),
                "structural_candidate_paths_before_graphify": candidate_paths,
                "no_source_content_grep_fallback": True,
            },
        }
        write_json(graphify_root / "retrieval_report.json", report["graphify"])
        return report

    return build_selected_site_index


def read_model_stage_metrics(output_dir: Path) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    totals = {
        "prompt_eval_count": 0,
        "eval_count": 0,
        "duration_ms": 0,
        "first_output_ms": 0,
        "model_call_count": 0,
    }
    for path in sorted(output_dir.glob("*_model_call.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        stage = path.name[: -len("_model_call.json")]
        stages[stage] = payload
        totals["model_call_count"] += 1
        metadata = (
            payload.get("response", {}).get("metadata", {})
            if isinstance(payload.get("response"), dict)
            else {}
        )
        if not isinstance(metadata, dict):
            metadata = {}
        for key in ("prompt_eval_count", "eval_count", "duration_ms", "first_output_ms"):
            try:
                totals[key] += int(metadata.get(key) or 0)
            except (TypeError, ValueError):
                pass
    return {"stages": stages, "totals": totals}


def read_site_index_metrics(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "site_index.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidate_files") if isinstance(payload.get("candidate_files"), list) else []
    return {
        "mode": payload.get("mode"),
        "prompt_used_for_indexing": payload.get("prompt_used_for_indexing"),
        "candidate_count": len(candidates),
        "candidate_paths": [
            item.get("path")
            for item in candidates
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ],
        "content_chars": sum(
            len(str(item.get("content") or ""))
            for item in candidates
            if isinstance(item, dict)
        ),
        "graphify": payload.get("graphify"),
    }


def artifact_replacement(
    pipeline_report: Mapping[str, Any],
) -> tuple[str | None, bytes | None, dict[str, Any]]:
    artifact = pipeline_report.get("artifact") if isinstance(pipeline_report.get("artifact"), dict) else {}
    path_value = artifact.get("path")
    if not isinstance(path_value, str) or not path_value:
        return None, None, {"ok": False, "reason": "artifact path missing"}
    path = Path(path_value)
    if not path.is_file():
        return None, None, {"ok": False, "reason": f"artifact missing: {path}"}
    safe, members = safe_zip_members(path)
    if not safe or len(members) != 1:
        return None, None, {"ok": False, "reason": "artifact member safety/count failed", "members": members}
    with zipfile.ZipFile(path) as archive:
        data = archive.read(archive.namelist()[0])
    logical = normalize_path(members[0]).split("/", 1)[-1]
    return logical, data, {
        "ok": True,
        "path": str(path),
        "sha256": sha256_file(path),
        "members": members,
        "logical_path": logical,
        "size_bytes": len(data),
    }


def semantic_postcondition(
    *,
    scenario: Scenario,
    token: str,
    base_site: Path,
    pipeline_report: Mapping[str, Any],
) -> dict[str, Any]:
    observed = str(pipeline_report.get("observed_terminal_class") or "")
    state = str(pipeline_report.get("terminal_state") or "")
    checks: dict[str, bool] = {}

    if scenario.endstate == "info":
        answer = str(pipeline_report.get("answer") or "")
        evidence_files = [
            normalize_path(item)
            for item in (pipeline_report.get("evidence_files") or [])
            if isinstance(item, str)
        ]
        checks = {
            "pipeline_ok": pipeline_report.get("ok") is True,
            "terminal_class_info": observed == "info",
            "grounded_info_state": state == "grounded_info_answer",
            "no_artifact": not pipeline_report.get("artifact"),
            "no_replacement_payloads": not pipeline_report.get("replacement_payloads"),
            "answer_mentions_index": "index.html" in answer.lower(),
            "answer_mentions_style": "style.css" in answer.lower(),
            "answer_mentions_script": "script.js" in answer.lower(),
            "evidence_index": any(path.endswith("index.html") for path in evidence_files),
            "evidence_style": any(path.endswith("style.css") for path in evidence_files),
            "evidence_script": any(path.endswith("script.js") for path in evidence_files),
        }
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "answer": answer,
            "evidence_files": evidence_files,
        }

    logical_path, replacement_bytes, artifact_report = artifact_replacement(pipeline_report)
    replacement_text = ""
    if replacement_bytes is not None:
        replacement_text = replacement_bytes.decode("utf-8", errors="replace")
    target = scenario.expected_target or ""

    checks.update(
        {
            "pipeline_ok": pipeline_report.get("ok") is True,
            "terminal_class_edit": observed == "edit",
            "promotable_artifact_state": state == "promotable_edit_artifact",
            "dry_run_ok": isinstance(pipeline_report.get("dry_run"), dict)
            and pipeline_report["dry_run"].get("ok") is True,
            "artifact_safe": artifact_report.get("ok") is True,
            "expected_target": logical_path == target,
            "replacement_differs_from_source": (
                bool(logical_path)
                and (base_site / logical_path).is_file()
                and replacement_bytes is not None
                and replacement_bytes != (base_site / logical_path).read_bytes()
            ),
        }
    )

    if scenario.id == "homepage_copy_edit":
        checks.update(
            {
                "requested_token_present": f"Operator readiness confirmed: {token}" in replacement_text,
                "heading_preserved": f"<h1>{base_site.name}</h1>" in replacement_text,
                "stylesheet_link_preserved": 'href="/style.css"' in replacement_text,
                "script_link_preserved": 'src="/script.js"' in replacement_text,
                "managed_by_preserved": "tools/local-platform/debug-website.py" in replacement_text,
            }
        )
    elif scenario.id == "client_status_control_edit":
        lowered = replacement_text.lower()
        checks.update(
            {
                "ready_state_present": "ready" in lowered,
                "paused_state_present": "paused" in lowered,
                "event_handler_present": (
                    "addeventlistener" in lowered
                    or ".onclick" in lowered
                    or "onclick" in lowered
                ),
                "dynamic_dom_present": (
                    "createelement" in lowered
                    or "insertadjacenthtml" in lowered
                    or "innerhtml" in lowered
                ),
                "original_debug_log_preserved": "Debug website ready:" in replacement_text,
            }
        )

    return {
        "ok": all(checks.values()),
        "checks": checks,
        "artifact": artifact_report,
        "replacement_preview": replacement_text[:2000],
    }


def lane_quality(
    *,
    scenario: Scenario,
    pipeline_report: Mapping[str, Any],
    postcondition: Mapping[str, Any],
    site_index: Mapping[str, Any],
) -> float:
    if scenario.endstate == "edit":
        components = [
            1.0 if pipeline_report.get("ok") is True else 0.0,
            1.0 if pipeline_report.get("terminal_state") == "promotable_edit_artifact" else 0.0,
            1.0 if isinstance(pipeline_report.get("dry_run"), dict) and pipeline_report["dry_run"].get("ok") is True else 0.0,
            1.0 if postcondition.get("ok") is True else 0.0,
        ]
    else:
        components = [
            1.0 if pipeline_report.get("ok") is True else 0.0,
            1.0 if pipeline_report.get("terminal_state") == "grounded_info_answer" else 0.0,
            1.0 if not pipeline_report.get("artifact") else 0.0,
            1.0 if postcondition.get("ok") is True else 0.0,
        ]
    # Small bounded-context bonus; never rescues a failed semantic result.
    context_chars = int(site_index.get("content_chars") or 0)
    context_bonus = 0.05 if context_chars and context_chars <= 36000 else 0.0
    return round(min(1.0, sum(components) / len(components) + context_bonus), 4)


def run_lane(
    *,
    lane: str,
    scenario: Scenario,
    token: str,
    repo: Path,
    source_fixture: Path,
    lane_site_root: Path,
    output_dir: Path,
    pipeline: Any,
    provider_call: Callable[..., Any],
    graphify_command: Sequence[str] | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    clone_fixture(source_fixture, lane_site_root)
    before_hashes = {
        path.relative_to(lane_site_root).as_posix(): sha256_file(path)
        for path in lane_site_root.rglob("*")
        if path.is_file()
    }

    prompt = scenario.prompt_template.format(token=token)
    original_builder = pipeline.build_selected_site_index
    graph_builder = None
    if lane == "graphify":
        if graphify_command is None:
            raise BenchmarkFailure("Graphify lane requested without a Graphify command.")
        graph_builder = graphify_index_builder_factory(
            original_builder=original_builder,
            command=graphify_command,
            prompt=prompt,
            output_dir=output_dir,
            repo=repo,
            timeout=args.graphify_timeout,
            budget=args.graphify_budget,
            max_selected_files=args.graphify_max_files,
        )

    started = time.perf_counter()
    with patched_pipeline(
        pipeline,
        call_model_json=provider_call,
        build_index=graph_builder,
    ):
        pipeline_report = pipeline.run_generated_editor_pipeline(
            repo=repo,
            site_id=lane_site_root.name,
            site_root=lane_site_root,
            user_prompt=prompt,
            output_dir=output_dir,
            model=args.model,
            ollama_url=f"{args.ollama_base_url.rstrip('/')}/api/generate",
            timeout_seconds=args.ai_timeout,
            terminal_num_predict=args.terminal_num_predict,
            grounding_num_predict=args.grounding_num_predict,
            patch_num_predict=args.patch_num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
            max_index_files=args.max_index_files,
            max_index_file_chars=args.max_index_file_chars,
            excerpt_context_lines=args.excerpt_context_lines,
            max_evidence_chars=args.max_evidence_chars,
        )
    elapsed = round(time.perf_counter() - started, 3)

    postcondition, post_exit = pipeline.postcondition_result(
        declared_endstate=scenario.endstate,
        site_id=lane_site_root.name,
        pipeline_report=pipeline_report,
        output_dir=output_dir,
    )
    semantic = semantic_postcondition(
        scenario=scenario,
        token=token,
        base_site=lane_site_root,
        pipeline_report=pipeline_report,
    )
    after_hashes = {
        path.relative_to(lane_site_root).as_posix(): sha256_file(path)
        for path in lane_site_root.rglob("*")
        if path.is_file() and path.name != "new_patch.py"
    }
    source_modified = any(
        after_hashes.get(relative) != digest
        for relative, digest in before_hashes.items()
    )

    site_index = read_site_index_metrics(output_dir)
    model_metrics = read_model_stage_metrics(output_dir)
    quality = lane_quality(
        scenario=scenario,
        pipeline_report=pipeline_report,
        postcondition=semantic,
        site_index=site_index,
    )
    report = {
        "lane": lane,
        "description": (
            "exact mounted structural index"
            if lane == "baseline"
            else "Graphify-selected site index"
        ),
        "model_transport": "main_computer.providers.ollama.OllamaProvider",
        "same_pipeline_after_index": True,
        "prompt": prompt,
        "endstate_oracle_passed_to_model": False,
        "elapsed_seconds": elapsed,
        "pipeline_ok": pipeline_report.get("ok") is True,
        "pipeline_report": json_safe(pipeline_report),
        "postcondition_result": postcondition,
        "postcondition_exit_code": post_exit,
        "semantic_postcondition": semantic,
        "site_index": site_index,
        "model_metrics": model_metrics,
        "source_fixture_modified": source_modified,
        "quality_score": quality,
        "passed": (
            post_exit == 0
            and semantic.get("ok") is True
            and not source_modified
        ),
    }
    write_json(output_dir / "lane_summary.json", report)
    return report


def case_winner(baseline: Mapping[str, Any], graphify: Mapping[str, Any]) -> str:
    left = float(baseline.get("quality_score") or 0.0)
    right = float(graphify.get("quality_score") or 0.0)
    if right > left:
        return "graphify"
    if left > right:
        return "baseline"
    left_context = int(baseline.get("site_index", {}).get("content_chars") or 0)
    right_context = int(graphify.get("site_index", {}).get("content_chars") or 0)
    if right_context < left_context:
        return "graphify_on_smaller_context"
    if left_context < right_context:
        return "baseline_on_smaller_context"
    left_time = float(baseline.get("elapsed_seconds") or 0.0)
    right_time = float(graphify.get("elapsed_seconds") or 0.0)
    if right_time < left_time:
        return "graphify_on_time"
    if left_time < right_time:
        return "baseline_on_time"
    return "tie"


def aggregate(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(cases)
    winners = collections.Counter(str(item.get("winner") or "unknown") for item in cases)

    def lane_summary(lane: str) -> dict[str, Any]:
        items = [item[lane] for item in cases]
        return {
            "passed_count": sum(1 for item in items if item.get("passed")),
            "average_quality_score": round(
                sum(float(item.get("quality_score") or 0.0) for item in items) / count,
                4,
            ),
            "total_elapsed_seconds": round(
                sum(float(item.get("elapsed_seconds") or 0.0) for item in items),
                3,
            ),
            "total_index_content_chars": sum(
                int(item.get("site_index", {}).get("content_chars") or 0)
                for item in items
            ),
            "total_model_calls": sum(
                int(item.get("model_metrics", {}).get("totals", {}).get("model_call_count") or 0)
                for item in items
            ),
            "total_prompt_eval_count": sum(
                int(item.get("model_metrics", {}).get("totals", {}).get("prompt_eval_count") or 0)
                for item in items
            ),
            "total_eval_count": sum(
                int(item.get("model_metrics", {}).get("totals", {}).get("eval_count") or 0)
                for item in items
            ),
        }

    return {
        "case_count": count,
        "baseline": lane_summary("baseline"),
        "graphify": lane_summary("graphify"),
        "winners": dict(sorted(winners.items())),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Graphify vs mounted Website Builder RAG",
        "",
        f"- Status: **{report.get('status')}**",
        f"- Run: `{report.get('run_id')}`",
        f"- Repository: `{report.get('repo')}`",
        f"- Ollama model: `{report.get('ollama', {}).get('requested_model')}`",
        f"- Graphify: `{report.get('graphify_version')}`",
        "- Model transport: `main_computer.providers.ollama.OllamaProvider`",
        "- Downstream path: real Website Builder terminal decision, grounding, patch validation, promotion, ZIP packaging, and `new_patch.py --dry-run`",
        "",
        "## Cases",
        "",
        "| Case | Baseline pass | Graphify pass | Baseline score | Graphify score | Baseline index chars | Graphify index chars | Winner |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for case in report.get("cases", []):
        baseline = case["baseline"]
        graphify = case["graphify"]
        lines.append(
            "| {id} | {bp} | {gp} | {bs:.4f} | {gs:.4f} | {bc:,} | {gc:,} | {winner} |".format(
                id=case["id"],
                bp="yes" if baseline.get("passed") else "no",
                gp="yes" if graphify.get("passed") else "no",
                bs=float(baseline.get("quality_score") or 0.0),
                gs=float(graphify.get("quality_score") or 0.0),
                bc=int(baseline.get("site_index", {}).get("content_chars") or 0),
                gc=int(graphify.get("site_index", {}).get("content_chars") or 0),
                winner=case.get("winner"),
            )
        )

    aggregate_report = report.get("aggregate") or {}
    if aggregate_report:
        lines.extend(
            [
                "",
                "## Aggregate",
                "",
                f"- Baseline passed: **{aggregate_report['baseline']['passed_count']}/{aggregate_report['case_count']}**",
                f"- Graphify passed: **{aggregate_report['graphify']['passed_count']}/{aggregate_report['case_count']}**",
                f"- Baseline average quality: **{aggregate_report['baseline']['average_quality_score']:.4f}**",
                f"- Graphify average quality: **{aggregate_report['graphify']['average_quality_score']:.4f}**",
                f"- Baseline index context: **{aggregate_report['baseline']['total_index_content_chars']:,} chars**",
                f"- Graphify index context: **{aggregate_report['graphify']['total_index_content_chars']:,} chars**",
                f"- Winners: `{json.dumps(aggregate_report['winners'], sort_keys=True)}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This benchmark changes only the selected-site retrieval/index stage. Both lanes use the same open-ended prompt, fresh debug-site code, OllamaProvider transport, terminal classifier, anchor verifier, grounding validator, patch validator, full-file promotion, replacement ZIP contract, and new_patch.py dry-run.",
            "",
            "The expected endstate, target file, and semantic postcondition are held outside the model prompts. They are used only after each lane returns.",
            "",
        ]
    )
    if report.get("warnings"):
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Graphify retrieval with the exact mounted Website Builder "
            "generated-editor RAG path using real OllamaProvider model calls."
        )
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--model", default="gemma4:26b")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--case",
        action="append",
        choices=[scenario.id for scenario in SCENARIOS],
        help="Run one or more named cases. Omit to run all cases.",
    )
    parser.add_argument("--graphify-cmd", help='Quoted command, for example "python -m graphify".')
    parser.add_argument("--graphify-budget", type=int, default=1800)
    parser.add_argument("--graphify-max-files", type=int, default=3)
    parser.add_argument("--graphify-timeout", type=float, default=180.0)
    parser.add_argument("--ai-timeout", type=float, default=600.0)
    parser.add_argument("--terminal-num-predict", type=int, default=3000)
    parser.add_argument("--grounding-num-predict", type=int, default=1600)
    parser.add_argument("--patch-num-predict", type=int, default=9000)
    parser.add_argument("--format-mode", choices=["none", "json"], default="none")
    parser.add_argument(
        "--think-mode",
        choices=["omit", "false", "true", "low", "medium", "high"],
        default="false",
    )
    # Mounted-route defaults, not the looser standalone smoke defaults.
    parser.add_argument("--max-index-files", type=int, default=80)
    parser.add_argument("--max-index-file-chars", type=int, default=12000)
    parser.add_argument("--excerpt-context-lines", type=int, default=8)
    parser.add_argument("--max-evidence-chars", type=int, default=12000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--contract-test-timeout", type=float, default=240.0)
    parser.add_argument("--skip-contract-tests", action="store_true")
    parser.add_argument(
        "--strict-route-contract-tests",
        action="store_true",
        help=(
            "Treat mounted-route apply/propose policy drift as blocking. By default "
            "that route-policy check is advisory because this benchmark exercises "
            "the proposal-only generated-editor pipeline directly."
        ),
    )
    parser.add_argument("--skip-provider-warmup", action="store_true")
    parser.add_argument("--require-graphify-wins", action="store_true")
    parser.add_argument("--json-out", default="graphify-vs-website-editor-rag-report.json")
    parser.add_argument("--markdown-out", default="graphify-vs-website-editor-rag-report.md")
    parser.add_argument(
        "--artifacts-dir",
        default="graphify-vs-website-editor-rag-artifacts",
    )
    parser.add_argument("--keep-work-dir", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    positive = (
        "graphify_budget",
        "graphify_max_files",
        "graphify_timeout",
        "ai_timeout",
        "terminal_num_predict",
        "grounding_num_predict",
        "patch_num_predict",
        "max_index_files",
        "max_index_file_chars",
        "excerpt_context_lines",
        "max_evidence_chars",
        "contract_test_timeout",
    )
    for name in positive:
        if float(getattr(args, name)) <= 0:
            raise BenchmarkFailure(f"--{name.replace('_', '-')} must be greater than zero.")


def selected_scenarios(args: argparse.Namespace) -> list[Scenario]:
    if not args.case:
        return list(SCENARIOS)
    wanted = set(args.case)
    return [scenario for scenario in SCENARIOS if scenario.id in wanted]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_args(args)
    repo = resolve_repo(args.repo)
    args.ollama_base_url = resolve_ollama_base_url(args.ollama_base_url)
    run_id = utc_run_id()

    json_out = (repo / args.json_out).resolve() if not Path(args.json_out).is_absolute() else Path(args.json_out).resolve()
    markdown_out = (repo / args.markdown_out).resolve() if not Path(args.markdown_out).is_absolute() else Path(args.markdown_out).resolve()
    artifacts_parent = (repo / args.artifacts_dir).resolve() if not Path(args.artifacts_dir).is_absolute() else Path(args.artifacts_dir).resolve()
    artifact_dir = artifacts_parent / run_id
    artifact_dir.mkdir(parents=True, exist_ok=False)
    work_dir = Path(tempfile.mkdtemp(prefix="graphify-website-editor-rag-")).resolve()

    report: dict[str, Any] = {
        "status": "running",
        "run_id": run_id,
        "repo": str(repo),
        "work_dir": str(work_dir),
        "artifact_dir": str(artifact_dir),
        "benchmark_contract": {
            "baseline_retrieval": "build_selected_site_index",
            "graphify_retrieval": "selected-site Graphify extract/query/explain",
            "model_transport": "main_computer.providers.ollama.OllamaProvider",
            "same_pipeline_after_index": True,
            "endstate_oracle_passed_to_model": False,
            "fresh_fixture_source": "tools/local-platform/debug-website.py",
            "live_apply": False,
        },
        "warnings": [],
        "cases": [],
    }
    exit_code = 2
    before_hashes: dict[str, str] = {}

    try:
        source_contract = source_contract_report(repo)
        report["source_contract"] = source_contract
        if not source_contract["ok"]:
            raise BenchmarkFailure("Website Builder source-path contract checks failed.")

        before_hashes = git_tracked_hashes(repo)
        report["tracked_source_file_count"] = len(before_hashes)

        if args.skip_contract_tests:
            report["contract_tests"] = {"ok": None, "skipped": True}
        else:
            report["contract_tests"] = run_contract_tests(
                repo,
                args.contract_test_timeout,
                strict_route_contracts=args.strict_route_contract_tests,
            )
            contract_tests = report["contract_tests"]
            if not contract_tests["blocking_ok"]:
                blocking = contract_tests["blocking"]
                raise BenchmarkFailure(
                    "Benchmark-critical Website Builder/anti-cheat contract tests failed:\n"
                    + (blocking["stderr"] or blocking["stdout"])
                )
            if not contract_tests["advisory_ok"]:
                advisory = contract_tests["advisory"]
                advisory_output = (advisory["stderr"] or advisory["stdout"]).strip()
                report["warnings"].append(
                    "Mounted Website Builder route-policy test is currently failing "
                    "(apply_edit vs propose_edit drift). This is advisory for the "
                    "proposal-only generated-editor A/B benchmark. "
                    + advisory_output[-1200:]
                )
            if not contract_tests["ok"]:
                advisory = contract_tests["advisory"]
                raise BenchmarkFailure(
                    "Strict mounted-route contract test failed:\n"
                    + (advisory["stderr"] or advisory["stdout"])
                )

        graphify_command, graphify_version, probes = resolve_graphify_command(
            explicit=args.graphify_cmd,
            repo=repo,
            timeout=args.graphify_timeout,
        )
        report["graphify_command"] = graphify_command
        report["graphify_version"] = graphify_version
        report["graphify_probes"] = probes

        report["ollama"] = ollama_preflight(
            args.ollama_base_url,
            args.model,
            args.ai_timeout,
        )
        if not report["ollama"]["ok"]:
            raise BenchmarkFailure(
                f"Ollama model is not listed: {args.model}. Installed: "
                + ", ".join(report["ollama"].get("installed_models", []))
            )

        if args.skip_provider_warmup:
            report["ollama_provider_warmup"] = {"ok": None, "skipped": True}
        else:
            report["ollama_provider_warmup"] = provider_warmup(
                repo=repo,
                base_url=args.ollama_base_url,
                model=args.model,
                timeout=args.ai_timeout,
                think_mode=args.think_mode,
                artifact_dir=artifact_dir,
            )
            if not report["ollama_provider_warmup"]["ok"]:
                raise BenchmarkFailure("OllamaProvider warm-up failed its JSON/transport contract.")

        pipeline = import_pipeline(repo)
        provider_call = provider_call_model_json_factory(
            repo=repo,
            base_url=args.ollama_base_url,
            artifact_dir=artifact_dir,
            temperature=args.temperature,
        )

        cases: list[dict[str, Any]] = []
        for case_index, scenario in enumerate(selected_scenarios(args), start=1):
            token = hashlib.sha256(f"{run_id}:{scenario.id}".encode("utf-8")).hexdigest()[:10].upper()
            site_id = f"debug-golden-path-graphify-{case_index}-{token.lower()}"
            case_dir = artifact_dir / scenario.id
            case_dir.mkdir(parents=True, exist_ok=True)
            fixture = work_dir / "fixtures" / scenario.id / site_id
            fixture_report = create_fresh_debug_site_fixture(
                repo=repo,
                target=fixture,
                site_id=site_id,
                purpose=f"Graphify vs mounted Website Builder RAG: {scenario.id}",
            )
            write_json(case_dir / "fixture.json", fixture_report)

            # Alternate lane order to reduce model-cache/order bias.
            lane_order = ["baseline", "graphify"] if case_index % 2 else ["graphify", "baseline"]
            lane_results: dict[str, dict[str, Any]] = {}
            for lane in lane_order:
                lane_site = work_dir / "lane-sites" / scenario.id / lane / site_id
                lane_output = case_dir / lane
                lane_output.mkdir(parents=True, exist_ok=True)
                lane_results[lane] = run_lane(
                    lane=lane,
                    scenario=scenario,
                    token=token,
                    repo=repo,
                    source_fixture=fixture,
                    lane_site_root=lane_site,
                    output_dir=lane_output,
                    pipeline=pipeline,
                    provider_call=provider_call,
                    graphify_command=graphify_command if lane == "graphify" else None,
                    args=args,
                )

            case_report = {
                "id": scenario.id,
                "description": scenario.description,
                "endstate": scenario.endstate,
                "expected_target": scenario.expected_target,
                "expected_evidence": list(scenario.expected_evidence),
                "postcondition_token": token,
                "lane_order": lane_order,
                "baseline": lane_results["baseline"],
                "graphify": lane_results["graphify"],
                "winner": case_winner(lane_results["baseline"], lane_results["graphify"]),
            }
            cases.append(case_report)
            write_json(case_dir / "case_summary.json", case_report)

        report["cases"] = cases
        report["aggregate"] = aggregate(cases)
        modified = changed_tracked_files(repo, before_hashes)
        report["source_repository_modified"] = bool(modified)
        report["modified_tracked_files"] = modified

        baseline_all = report["aggregate"]["baseline"]["passed_count"] == report["aggregate"]["case_count"]
        graphify_all = report["aggregate"]["graphify"]["passed_count"] == report["aggregate"]["case_count"]
        baseline_score = report["aggregate"]["baseline"]["average_quality_score"]
        graphify_score = report["aggregate"]["graphify"]["average_quality_score"]

        if modified:
            report["status"] = "failed_source_modified"
            report["warnings"].append("Tracked repository files changed during the benchmark.")
            exit_code = 2
        elif not graphify_all:
            report["status"] = "failed_graphify_website_editor_path"
            exit_code = 2
        elif graphify_score > baseline_score:
            report["status"] = "passed_graphify_advantage"
            exit_code = 0
        elif graphify_score == baseline_score:
            report["status"] = "passed_tie"
            exit_code = 0
        else:
            report["status"] = "passed_baseline_advantage"
            exit_code = 0

        if not baseline_all:
            report["warnings"].append(
                "At least one exact mounted structural-index baseline case failed. "
                "This does not invalidate a passing Graphify lane, but it is a live "
                "Website Builder regression signal."
            )

        if args.require_graphify_wins and graphify_score <= baseline_score:
            report["status"] = "failed_graphify_did_not_win"
            report["warnings"].append(
                "--require-graphify-wins was set, but Graphify did not achieve a "
                "strictly greater aggregate quality score."
            )
            exit_code = 3

    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        if before_hashes:
            modified = changed_tracked_files(repo, before_hashes)
            report["source_repository_modified"] = bool(modified)
            report["modified_tracked_files"] = modified
        exit_code = 2
    finally:
        report["output_artifacts"] = {
            "json_report": str(json_out),
            "markdown_report": str(markdown_out),
            "artifact_dir": str(artifact_dir),
        }
        write_json(json_out, report)
        write_text(markdown_out, render_markdown(report))
        if args.keep_work_dir:
            print(f"Work directory kept at: {work_dir}", file=sys.stderr)
        else:
            shutil.rmtree(work_dir, ignore_errors=True)

    summary = {
        "status": report.get("status"),
        "run_id": report.get("run_id"),
        "graphify_version": report.get("graphify_version"),
        "ollama": report.get("ollama"),
        "ollama_provider_warmup": report.get("ollama_provider_warmup"),
        "contract_tests": {
            "ok": report.get("contract_tests", {}).get("ok"),
            "blocking_ok": report.get("contract_tests", {}).get("blocking_ok"),
            "advisory_ok": report.get("contract_tests", {}).get("advisory_ok"),
            "strict_route_contracts": report.get("contract_tests", {}).get("strict_route_contracts"),
            "skipped": report.get("contract_tests", {}).get("skipped", False),
        },
        "aggregate": report.get("aggregate"),
        "source_repository_modified": report.get("source_repository_modified"),
        "output_artifacts": report.get("output_artifacts"),
        "warnings": report.get("warnings"),
        "error": report.get("error"),
    }
    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
