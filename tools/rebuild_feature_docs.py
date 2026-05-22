from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import html.parser
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FRAGMENT_PASSES = [
    ("overview", "Explain what the feature is and where it appears."),
    ("component_hierarchy", "Explain component ids, owners, feature ids, and generated items."),
    ("frontend_flow", "Explain HTML, CSS, JavaScript flow, and browser state."),
    ("backend_flow", "Explain backend routes, request/response shapes, config, and errors."),
    ("state_and_modes", "Explain state transitions, modes, snap behavior, and failure states."),
    ("extension_notes", "Explain safe future extension points."),
]

MICRO_FRAGMENT_PASSES = [
    ("overview", "What this control is and where it lives."),
    ("implementation_flow", "How the control is read, which payload/state it affects, and related tests."),
]

COMPONENT_FRAGMENT_PASSES = [
    ("overview", "Explain what the component is and where it appears."),
    ("component_hierarchy", "Explain component ids, owners, feature ids, and generated items."),
    ("frontend_flow", "Explain HTML, CSS, JavaScript flow, and browser state."),
    ("extension_notes", "Explain safe future extension points."),
]

APP_FRAGMENT_PASSES = [
    ("overview", "Explain what the app is and where it appears."),
    ("component_hierarchy", "Explain component ids, owners, feature ids, and generated items."),
    ("frontend_flow", "Explain HTML, CSS, JavaScript flow, and browser state."),
    ("backend_flow", "Explain backend routes, request/response shapes, config, and errors."),
    ("state_and_modes", "Explain state transitions, modes, snap behavior, and failure states."),
    ("public_api", "Explain public scripting and API surfaces."),
    ("extension_notes", "Explain safe future extension points."),
]

EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "patching",
    ".venv",
    "venv",
}
TEXT_SUFFIXES = {
    ".html",
    ".css",
    ".js",
    ".py",
    ".md",
    ".json",
    ".toml",
    ".txt",
    ".ps1",
}

FRAGMENT_FILE_HINTS = {
    "overview": {".html", ".md", ".json", ".py", ".js", ".css"},
    "component_hierarchy": {".html", ".js"},
    "frontend_flow": {".html", ".css", ".js"},
    "backend_flow": {".py"},
    "state_and_modes": {".html", ".js", ".py", ".json"},
    "implementation_flow": {".html", ".js", ".py"},
    "public_api": {".html", ".js", ".py", ".json"},
    "extension_notes": {".html", ".css", ".js", ".py", ".md", ".json"},
}

STATE_SCHEMA_VERSION = 1
HTML_OUTPUT_RULES_VERSION = "html-fragments-v1"
PROMPT_TEMPLATE_VERSION = "bounded-evidence-v2"
SAFE_FRAGMENT_STATUSES = {"generated", "validated"}
TEMPLATE_FRAGMENT_STATUSES = {"template"}
TAINTED_FRAGMENT_STATUSES = {"generated_tainted", "needs_review"}
DOCGEN_SOURCE_PATHS = {
    "tools/rebuild_feature_docs.py",
    "tests/test_rebuild_feature_docs.py",
}
GENERATED_DOCS_PREFIXES = (
    "generated_component_docs/work/",
    "generated_component_docs/archive/",
    "generated_component_docs/nodes/",
)
GENERATED_DOCS_FILES = {
    "generated_component_docs/manifest.json",
    "generated_component_docs/graph.json",
}
DOC_BUILD_ARTIFACT_PREFIXES = (
    "tools/documentation/",
    "generated_component_docs/work/",
    "generated_component_docs/archive/",
    "generated_component_docs/nodes/",
    "generated_component_docs/features/",
)
DOC_BUILD_ARTIFACT_FILES = {
    "tools/crawl_component_docs.py",
    "generated_component_docs/doc-build.json",
    "generated_component_docs/doc-health.json",
    "generated_component_docs/manifest.json",
    "generated_component_docs/graph.json",
}


def fallback_enabled(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "fallback", False))


def verbose(args: argparse.Namespace, message: str) -> None:
    if getattr(args, "verbose", False) or fallback_enabled(args):
        prefix = "[rebuild-feature-docs][fallback]" if fallback_enabled(args) else "[rebuild-feature-docs]"
        print(f"{prefix} {message}", file=sys.stderr, flush=True)


def shell_command(command: list[str]) -> str:
    try:
        return shlex.join(command)
    except AttributeError:
        return " ".join(shlex.quote(part) for part in command)


def fallback_trace(args: argparse.Namespace, message: str) -> None:
    if fallback_enabled(args):
        print(f"[rebuild-feature-docs][fallback] {message}", file=sys.stderr, flush=True)


def stream_subprocess_to_console_and_log(
    *,
    args: argparse.Namespace,
    command: list[str],
    cwd: Path,
    log_file: Path,
    timeout: float,
    label: str,
) -> int:
    """Run a subprocess while mirroring output to stderr and the log file.

    Fallback mode optimizes for the earliest visible feedback from model-backed
    tools. It reads one byte at a time so partial tokens/chunks are visible even
    before a newline is emitted. The process wait happens on the main thread so
    a silent/hung model process still honors the timeout.
    """

    started = time.monotonic()
    first_output_ms: float | None = None
    output_lock = threading.Lock()

    with log_file.open("w", encoding="utf-8", errors="replace") as log:
        header = (
            f"[fallback] starting {label}\n"
            f"[fallback] cwd: {cwd}\n"
            f"[fallback] timeout_s: {timeout}\n"
            f"[fallback] command: {shell_command(command)}\n"
            "[fallback] capture: byte-immediate stdout/stderr tee\n"
        )
        log.write(header)
        log.flush()
        print(header, file=sys.stderr, end="", flush=True)

        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        assert process.stdout is not None

        def emit(text: str) -> None:
            log.write(text)
            log.flush()
            print(text, file=sys.stderr, end="", flush=True)

        def reader() -> None:
            nonlocal first_output_ms
            try:
                while True:
                    chunk = process.stdout.read(1)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    with output_lock:
                        if first_output_ms is None:
                            first_output_ms = (time.monotonic() - started) * 1000
                            emit(f"\n[fallback] first {label} output after {first_output_ms:.0f}ms\n")
                        emit(text)
            except ValueError:
                # The pipe may close while the timeout path kills the process.
                return

        reader_thread = threading.Thread(target=reader, name=f"{label}-fallback-reader", daemon=True)
        reader_thread.start()

        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
            with output_lock:
                emit(f"\n[fallback] {label} timed out after {timeout}s; killed with code {return_code}\n")
            reader_thread.join(timeout=1)
            raise

        reader_thread.join(timeout=1)
        with output_lock:
            if first_output_ms is None:
                emit(f"\n[fallback] {label} exited without emitting output\n")
            emit(f"\n[fallback] {label} exited with code {return_code}\n")
        return return_code


def ollama_stream_delta(data: dict[str, Any]) -> str:
    message = data.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(data.get("response") or "")


def read_ollama_streaming_content(
    *,
    args: argparse.Namespace,
    response: Any,
    trace_path: Path,
    label: str,
    started: float,
) -> tuple[str, list[str], float | None]:
    parts: list[str] = []
    response_keys: set[str] = set()
    first_output_ms: float | None = None
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8", errors="replace") as trace:
        header = f"[fallback] starting Ollama stream for {label}\n[fallback] trace: {trace_path}\n"
        trace.write(header)
        trace.flush()
        print(header, file=sys.stderr, end="", flush=True)
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
            if not line.strip():
                continue
            trace.write(line if line.endswith("\n") else f"{line}\n")
            trace.flush()
            data = json.loads(line)
            response_keys.update(data.keys())
            delta = ollama_stream_delta(data)
            if delta:
                if first_output_ms is None:
                    first_output_ms = (time.monotonic() - started) * 1000
                    marker = f"\n[fallback] first Ollama output for {label} after {first_output_ms:.0f}ms\n"
                    trace.write(marker)
                    trace.flush()
                    print(marker, file=sys.stderr, end="", flush=True)
                parts.append(delta)
                trace.write(f"[model-delta] {delta}\n")
                trace.flush()
                print(delta, file=sys.stderr, end="", flush=True)
        if first_output_ms is None:
            marker = f"\n[fallback] Ollama stream for {label} completed without content deltas\n"
            trace.write(marker)
            trace.flush()
            print(marker, file=sys.stderr, end="", flush=True)
    return "".join(parts).strip(), sorted(response_keys), first_output_ms

@dataclass
class Match:
    path: str
    reason: str
    score: int
    snippets: list[str]


class TaintedAiderRun(RuntimeError):
    pass


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    parent: "HtmlNode | None" = None
    children: list["HtmlNode"] | None = None
    path: str = ""

    def __post_init__(self) -> None:
        if self.children is None:
            self.children = []


class ComponentTreeParser(html.parser.HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("root", {}, None, [], path)
        self.stack = [self.root]
        self.nodes: list[HtmlNode] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag, {name: value or "" for name, value in attrs}, self.stack[-1], [], self.root.path)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag.lower() not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag.lower() == tag.lower():
                del self.stack[index:]
                return


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_timestamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y%m%d%H%M%S%f")


def utc_iso_timestamp() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_slug(value: str, fallback: str = "feature", max_chars: int = 120) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    slug = slug or fallback
    if max_chars and len(slug) > max_chars:
        truncated = slug[:max_chars].rstrip("-._")
        if "-" in truncated:
            word_truncated = truncated.rsplit("-", 1)[0].rstrip("-._")
            if len(word_truncated) >= max(12, max_chars // 2):
                truncated = word_truncated
        slug = truncated or fallback
    return slug


def short_stable_hash(value: str, length: int = 10) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:length]


def work_dir_basename(feature: str, target_id: str, timestamp: str) -> str:
    """Return a short, readable, collision-resistant work directory basename.

    Windows path limits make it unsafe to include the full --feature text in the
    directory name. The full feature text is still preserved in the JSON context,
    run state, prompts, and manifest metadata.
    """

    digest = short_stable_hash(f"{feature}\0{target_id}", 10)
    feature_slug = safe_slug(feature, "feature", max_chars=32)
    target_slug = safe_slug(target_id, "target", max_chars=48)
    basename = f"{feature_slug}__{target_slug}__{digest}__{timestamp}"
    if len(basename) > 120:
        overhead = len(feature_slug) + len(digest) + len(timestamp) + 6
        target_max = max(12, 120 - overhead)
        target_slug = safe_slug(target_id, "target", max_chars=target_max)
        basename = f"{feature_slug}__{target_slug}__{digest}__{timestamp}"
    return basename[:120].rstrip("-._") or f"feature__target__{digest}__{timestamp}"


def relpath(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def display_path(path: Path, root: Path) -> str:
    try:
        return relpath(path, root)
    except ValueError:
        return path.resolve().as_posix()


def normalize_manifest_doc_path(value: str | Path) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        raise ValueError("Manifest doc_path is required.")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        raise ValueError("Manifest doc_path must be relative to generated_component_docs.")
    parts = [part for part in raw.split("/") if part and part != "."]
    if parts and parts[0] == "generated_component_docs":
        parts = parts[1:]
    if not parts:
        raise ValueError("Manifest doc_path is required.")
    if any(part == ".." for part in parts):
        raise ValueError("Manifest doc_path may not contain traversal.")
    normalized = "/".join(parts)
    if not normalized.lower().endswith(".html"):
        raise ValueError("Manifest doc_path must point to an HTML document.")
    return normalized


def docs_root_relative_path(path: Path | str, output_root: Path | str) -> str:
    docs_root = Path(output_root).resolve()
    final_path = Path(path).resolve()
    try:
        relative = final_path.relative_to(docs_root)
    except ValueError as exc:
        raise ValueError("Generated documentation path must stay under generated_component_docs.") from exc
    return normalize_manifest_doc_path(relative.as_posix())


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-._") or "json"
    temp_path = path.with_name(f".{stem[:32]}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)
    except OSError as exc:
        raise OSError(
            f"Failed to write JSON to {path} "
            f"(path length={len(str(path))}, temp length={len(str(temp_path))})"
        ) from exc


def read_json_file(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return fallback


def hash_json(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def is_candidate_file(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if rel.parts and rel.parts[0] in {"diagnostics_output", "harness_output", "debug_assets", "debug_asset_revisions"}:
        return False
    if path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bin", ".pyc"}:
        return False
    return path.suffix.lower() in TEXT_SUFFIXES and path.is_file()


def is_generated_docs_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    return normalized in GENERATED_DOCS_FILES or any(normalized.startswith(prefix) for prefix in GENERATED_DOCS_PREFIXES)


def is_doc_build_artifact_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in DOC_BUILD_ARTIFACT_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in DOC_BUILD_ARTIFACT_PREFIXES):
        return True
    return bool(re.fullmatch(r"tools/crawl_component_docs(?:-\d+)?\.py", normalized))


def is_docgen_source_path(relative: str) -> bool:
    return relative.replace("\\", "/") in DOCGEN_SOURCE_PATHS


def targets_docgen_source(feature: str, target_id: str) -> bool:
    haystack = f"{feature} {target_id}".lower()
    return any(term in haystack for term in ["rebuild_feature_docs", "documentation generator", "feature docs script", "tools/rebuild_feature_docs.py"])


def iter_discovery_files(root: Path) -> list[Path]:
    files: list[Path] = []
    search_roots = [
        root / "main_computer" / "web" / "applications",
        root / "main_computer",
        root / "tests",
        root / "tools",
        root,
    ]
    root_level_names = {"README.md", "ENVIRONMENT.md", "TODO.md", "pyproject.toml"}
    seen: set[Path] = set()
    for search_root in search_roots:
        if not search_root.exists():
            continue
        if search_root == root:
            candidates = [root / name for name in root_level_names]
        elif search_root.name == "main_computer":
            candidates = [
                *search_root.glob("viewport*.py"),
                *search_root.glob("*aider*.py"),
                *search_root.glob("*editor*.py"),
            ]
        else:
            candidates = search_root.rglob("*")
        for path in candidates:
            if not path.is_file() or path in seen:
                continue
            if is_candidate_file(path, root):
                seen.add(path)
                files.append(path)
    return files


def feature_terms(feature: str) -> list[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "flow",
        "feature",
        "component",
        "embedded",
        "renders",
    }
    terms = []
    for term in re.findall(r"[A-Za-z0-9._/-]+", feature.lower()):
        if len(term) >= 3 and term not in stop:
            terms.append(term)
    return sorted(set(terms))


def snippets_for(text: str, needles: list[str], limit: int = 4) -> list[str]:
    snippets: list[str] = []
    lines = text.splitlines()
    lowered_needles = [needle.lower() for needle in needles if needle]
    seen_ranges: set[tuple[int, int]] = set()
    for needle in lowered_needles:
        for index, line in enumerate(lines):
            if needle not in line.lower():
                continue
            start = max(0, index - 2)
            end = min(len(lines), index + 3)
            key = (start, end)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            snippets.append("\n".join(lines[start:end]))
            if len(snippets) >= limit:
                return snippets
    return snippets


def compact_snippets_for(text: str, needles: list[str], limit: int, max_chars: int) -> list[str]:
    snippets = snippets_for(text, needles, limit=limit)
    compacted = []
    for snippet in snippets:
        value = snippet.strip()
        if len(value) > max_chars:
            value = value[:max_chars].rstrip() + "\n..."
        compacted.append(value)
    return compacted


def extract_attrs(tag_text: str) -> dict[str, str]:
    return {name: value for name, value in re.findall(r'([A-Za-z0-9_-]+)="([^"]*)"', tag_text)}


def iter_html_nodes(root: Path) -> list[HtmlNode]:
    nodes: list[HtmlNode] = []
    for path in [
        root / "main_computer" / "web" / "applications.html",
        *(root / "main_computer" / "web" / "applications" / "apps").glob("*.html"),
    ]:
        if not path.is_file():
            continue
        relative = relpath(path, root)
        parser = ComponentTreeParser(relative)
        parser.feed(read_text(path))
        nodes.extend(parser.nodes)
    return nodes


def node_attr_ids(node: HtmlNode) -> list[str]:
    return [
        value
        for value in [
            node.attrs.get("id", ""),
            node.attrs.get("data-mc-doc-id", ""),
            node.attrs.get("data-mc-component-id", ""),
            node.attrs.get("data-mc-widget-id", ""),
        ]
        if value
    ]


def node_subtree_ids(node: HtmlNode) -> list[str]:
    values = node_attr_ids(node)
    for child in node.children or []:
        values.extend(node_subtree_ids(child))
    return values


def closest_component_node(node: HtmlNode) -> HtmlNode | None:
    current: HtmlNode | None = node
    while current is not None:
        if current.attrs.get("data-mc-component-id"):
            return current
        current = current.parent
    return None


def resolve_doc_target_identity(target_id: str, root: Path) -> dict[str, Any]:
    target = target_id.strip()
    nodes = iter_html_nodes(root)
    matched = next((node for node in nodes if target in node_attr_ids(node)), None)
    component = closest_component_node(matched) if matched else None
    canonical_id = component.attrs["data-mc-component-id"] if component else target
    alias_values: list[str] = []
    if component:
        alias_values.extend(node_subtree_ids(component))
    if matched:
        alias_values.extend(node_attr_ids(matched))
    alias_values.append(target)
    aliases: list[str] = []
    for value in alias_values:
        if value and value != canonical_id and value not in aliases:
            aliases.append(value)
    title = (
        component.attrs.get("data-mc-component-label", "")
        if component
        else (
            matched.attrs.get("data-mc-component-label", "")
            or matched.attrs.get("data-mc-widget-label", "")
            or matched.attrs.get("data-widget-label", "")
            or matched.attrs.get("aria-label", "")
        )
        if matched
        else ""
    )
    return {
        "input_id": target,
        "id": canonical_id,
        "aliases": aliases,
        "title": title or canonical_id,
        "matched_node_path": (matched.path if matched else ""),
        "canonical_component": component.attrs if component else {},
    }


def extract_components(path: str, text: str) -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    for tag in re.findall(r"<[^>]*data-mc-component-id=\"[^\"]+\"[^>]*>", text):
        attrs = extract_attrs(tag)
        components.append(
            {
                "path": path,
                "id": attrs.get("data-mc-component-id", ""),
                "kind": attrs.get("data-mc-component-kind", ""),
                "label": attrs.get("data-mc-component-label", ""),
                "owner": attrs.get("data-mc-component-owner", ""),
                "feature": attrs.get("data-mc-feature-id", ""),
                "source": attrs.get("data-mc-source", ""),
            }
        )
    return components


def extract_routes(text: str) -> list[str]:
    routes = set(re.findall(r"/api/[A-Za-z0-9_./{}:-]+", text))
    routes.update(re.findall(r"/applications/[A-Za-z0-9_./{}:-]+", text))
    return sorted(routes)


def source_refs_from_components(components: list[dict[str, str]]) -> set[str]:
    refs: set[str] = set()
    for component in components:
        for item in component.get("source", "").split(";"):
            item = item.strip()
            if item and not item.startswith("/api/"):
                refs.add(item)
    return refs


def decoded_components(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for item in discovery.get("matched_components", []):
        try:
            component = json.loads(item) if isinstance(item, str) else item
        except json.JSONDecodeError:
            continue
        if isinstance(component, dict):
            components.append(component)
    return components


def auto_scope_for(discovery: dict[str, Any]) -> tuple[str, str]:
    target = str(discovery.get("target_id") or "")
    components = [component for component in decoded_components(discovery) if component.get("id") == target or component.get("feature") == target]
    exact = components[0] if components else {}
    kind = str(exact.get("kind") or "").lower()
    owner = str(exact.get("owner") or "")
    if kind in {"input", "action", "status"} and owner:
        return "micro", f"target appears to be an input/action/status ({kind})"
    snippets = "\n".join(snippet for match in discovery.get("matches", []) for snippet in match.get("snippets", []))
    if re.search(rf"(?is)<(input|button|select|textarea)\b[^>]*(id|data-mc-component-id)=[\"']{re.escape(target)}[\"']", snippets) or (
        f'id="{target}"' in snippets and any(word in snippets.lower() for word in ["checkbox", "button", "input", "select", "status"])
    ):
        return "micro", "target appears to be an input/action/status control"
    if target.startswith("code-editor.feature.") or ".feature." in target:
        return "feature", "target id is a feature id"
    if kind in {"app", "workspace"}:
        return "app", f"target kind is {kind}"
    return "component", "default component scope"


def fragment_plan_for_scope(scope: str, discovery: dict[str, Any], max_fragments: int = 0) -> list[tuple[str, str]]:
    if scope == "micro":
        plan = list(MICRO_FRAGMENT_PASSES)
    elif scope == "component":
        plan = list(COMPONENT_FRAGMENT_PASSES)
    elif scope == "app":
        plan = list(APP_FRAGMENT_PASSES)
    else:
        plan = list(FRAGMENT_PASSES)
    return plan[:max_fragments] if max_fragments and max_fragments > 0 else plan


def discover(feature: str, target_id: str, root: Path, include_docgen_source: bool = False) -> dict[str, Any]:
    target = target_id.strip()
    target_lower = target.lower()
    terms = feature_terms(feature)
    matches: dict[str, Match] = {}
    components: list[dict[str, str]] = []
    route_hits: set[str] = set()
    source_refs: set[str] = set()
    excluded_docgen_sources: list[str] = []
    excluded_doc_build_artifacts: list[str] = []
    allow_docgen = include_docgen_source or targets_docgen_source(feature, target_id)

    candidates = iter_discovery_files(root)
    for path in candidates:
        relative = relpath(path, root)
        if is_doc_build_artifact_path(relative):
            excluded_doc_build_artifacts.append(relative)
            continue
        if is_generated_docs_path(relative):
            continue
        if is_docgen_source_path(relative) and not allow_docgen:
            excluded_docgen_sources.append(relative)
            continue
        text = read_text(path)
        lowered = text.lower()
        path_lower = relative.lower()
        file_components = extract_components(relative, text)
        components.extend(file_components)

        score = 0
        reasons: list[str] = []
        if f'data-mc-component-id="{target}"' in text:
            score += 1000
            reasons.append("exact data-mc-component-id")
        if f'data-mc-feature-id="{target}"' in text:
            score += 900
            reasons.append("exact data-mc-feature-id")
        if f'data-mc-widget-id="{target}"' in text:
            score += 800
            reasons.append("exact data-mc-widget-id")
        if f'id="{target}"' in text or f"#{target}" in text:
            score += 700
            reasons.append("exact DOM id or selector")
        if target.startswith("/") and target in text:
            score += 650
            reasons.append("exact route string")
        if target_lower in path_lower:
            score += 500
            reasons.append("filename match")
        if target_lower in lowered:
            score += 250
            reasons.append("exact text match")

        term_hits = [term for term in terms if term in lowered or term in path_lower]
        if term_hits:
            score += min(120, 20 * len(term_hits))
            reasons.append(f"feature text match: {', '.join(term_hits[:6])}")

        routes = extract_routes(text)
        if routes and (score or any(term in lowered for term in terms)):
            route_hits.update(routes)

        if file_components:
            for component in file_components:
                if component.get("id") == target or component.get("feature") == target:
                    source_refs.update(source_refs_from_components([component]))

        if score:
            matches[relative] = Match(
                path=relative,
                reason="; ".join(reasons),
                score=score,
                snippets=snippets_for(text, [target, *terms]),
            )

    matched_components = [
        component
        for component in components
        if component.get("id") == target
        or component.get("feature") == target
        or component.get("owner") == target
        or target in component.get("source", "")
    ]
    matched_feature_ids = {component.get("feature") for component in matched_components if component.get("feature")}
    matched_component_ids = {component.get("id") for component in matched_components if component.get("id")}

    for component in components:
        if component.get("feature") in matched_feature_ids or component.get("owner") in matched_component_ids:
            matched_components.append(component)
            if component.get("path") not in matches:
                matches[component["path"]] = Match(
                    path=component["path"],
                    reason="component hierarchy relation",
                    score=350,
                    snippets=[],
                )
            source_refs.update(source_refs_from_components([component]))

    for source_ref in source_refs:
        source_path = root / source_ref
        if source_path.is_file():
            relative = relpath(source_path, root)
            if is_doc_build_artifact_path(relative):
                if relative not in excluded_doc_build_artifacts:
                    excluded_doc_build_artifacts.append(relative)
                continue
            if is_generated_docs_path(relative):
                continue
            if is_docgen_source_path(relative) and not allow_docgen:
                if relative not in excluded_docgen_sources:
                    excluded_docgen_sources.append(relative)
                continue
            if relative not in matches:
                text = read_text(source_path)
                matches[relative] = Match(
                    path=relative,
                    reason="data-mc-source relation",
                    score=450,
                    snippets=snippets_for(text, [target, *terms]),
                )

    ordered = sorted(matches.values(), key=lambda item: (-item.score, item.path))
    bounded = ordered[:24]
    return {
        "feature_description": feature,
        "target_id": target,
        "source_files": [match.path for match in bounded],
        "matches": [
            {"path": match.path, "reason": match.reason, "score": match.score, "snippets": match.snippets}
            for match in bounded
        ],
        "matched_components": sorted(
            {json.dumps(component, sort_keys=True) for component in matched_components}
        ),
        "backend_routes": sorted(route_hits),
        "terms": terms,
        "excluded_docgen_sources": sorted(set(excluded_docgen_sources)),
        "excluded_doc_build_artifacts": sorted(set(excluded_doc_build_artifacts)),
    }


def build_context_pack(discovery: dict[str, Any], root: Path) -> dict[str, Any]:
    files = []
    for relative in discovery["source_files"]:
        path = root / relative
        if not path.is_file():
            continue
        text = read_text(path)
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
                "size": len(text),
                "routes": extract_routes(text),
                "components": extract_components(relative, text),
                "snippets": snippets_for(text, [discovery["target_id"], *discovery.get("terms", [])], limit=8),
            }
        )
    return {
        "feature_description": discovery["feature_description"],
        "target_id": discovery["target_id"],
        "files": files,
        "backend_routes": discovery["backend_routes"],
    }


def source_fingerprint(discovery: dict[str, Any], context_pack: dict[str, Any]) -> str:
    files = [
        {
            "path": item.get("path", ""),
            "size": item.get("size", 0),
            "sha256": item.get("sha256", ""),
        }
        for item in context_pack.get("files", [])
    ]
    return hash_json(
        {
            "target_id": discovery.get("target_id", ""),
            "feature_description": discovery.get("feature_description", ""),
            "source_files": discovery.get("source_files", []),
            "files": files,
            "matched_components": discovery.get("matched_components", []),
            "backend_routes": discovery.get("backend_routes", []),
        }
    )


def effective_engine(args: argparse.Namespace) -> str:
    if getattr(args, "no_aider", False):
        return "template"
    return args.engine


def resolved_ollama_model(args: argparse.Namespace) -> str:
    if args.ollama_model:
        return args.ollama_model
    model = str(args.model or "")
    return model.split("/", 1)[1] if model.startswith("ollama_chat/") else model


def model_for_fragment(args: argparse.Namespace, scope: str, fragment_name: str) -> str:
    if args.fast_model and scope == "micro":
        return args.fast_model
    if args.deep_model and scope in {"feature", "app"} and fragment_name in {"frontend_flow", "backend_flow"}:
        return args.deep_model
    return resolved_ollama_model(args) if effective_engine(args) == "ollama" else args.model


def effective_timeout(args: argparse.Namespace, engine: str) -> float:
    if engine == "ollama":
        return args.ollama_timeout if args.ollama_timeout is not None else args.timeout
    return args.process_timeout if args.process_timeout is not None else args.timeout


def effective_evidence_limits(args: argparse.Namespace, scope: str) -> tuple[int, int, int, int]:
    if scope == "micro":
        return (
            min(args.max_files_per_pass, 4),
            max(args.max_snippets_per_file, 4),
            min(args.max_snippet_chars, 1600),
            min(args.max_evidence_chars, 12000),
        )
    return args.max_files_per_pass, args.max_snippets_per_file, args.max_snippet_chars, args.max_evidence_chars


def plan_fingerprint(args: argparse.Namespace, target_id: str, feature: str, fragment_passes: list[tuple[str, str]] | None = None, scope: str = "feature") -> str:
    return hash_json(
        {
            "fragment_passes": fragment_passes or FRAGMENT_PASSES,
            "target_id": target_id,
            "feature_description": feature,
            "engine": effective_engine(args),
            "scope": scope,
            "model": args.model,
            "ollama_model": resolved_ollama_model(args),
            "aider_source_mode": args.aider_source_mode,
            "max_files_per_pass": args.max_files_per_pass,
            "max_snippets_per_file": args.max_snippets_per_file,
            "max_snippet_chars": args.max_snippet_chars,
            "max_evidence_chars": args.max_evidence_chars,
            "html_output_rules_version": HTML_OUTPUT_RULES_VERSION,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        }
    )


def fragment_fingerprint(name: str, prompt_text_value: str, evidence_pack: dict[str, Any], args: argparse.Namespace, model: str | None = None) -> str:
    return hash_json(
        {
            "fragment_name": name,
            "engine": effective_engine(args),
            "model": model or args.model,
            "aider_source_mode": args.aider_source_mode,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "prompt": prompt_text_value,
            "evidence": evidence_pack,
        }
    )


def strip_markdown_fences(value: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"(?is)```(?:html)?\s*(.*?)\s*```", text)
    return match.group(1).strip() if match else text


def safe_fragment_html(value: str) -> str:
    value = strip_markdown_fences(value)
    value = re.sub(r"(?is)<script\b.*?</script>", "", value)
    value = re.sub(r"(?is)<(iframe|object|embed)\b.*?</\1>", "", value)
    value = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", value)
    value = re.sub(r"(?is)</?(html|head|body)\b[^>]*>", "", value)
    return value.strip()


def fragment_title(name: str) -> str:
    return name.replace("_", " ").title()


def normalize_fragment_html(name: str, focus: str, raw_html: str) -> str:
    content = safe_fragment_html(raw_html)
    if not content:
        return content
    if re.search(r"(?is)<(article|section)\b", content):
        return content
    return f"""<section class="mc-doc-fragment" data-fragment="{html.escape(name)}">
  <header>
    <h2>{html.escape(fragment_title(name))}</h2>
  </header>
  <div class="mc-doc-fragment-body">
    {content}
  </div>
</section>"""


def validate_fragment_html_text(content: str) -> dict[str, Any]:
    content = content.strip()
    lower = content.lower()
    if not content:
        return {"ok": False, "reason": "empty"}
    checks = [
        ("<script", "script tag"),
        ("<iframe", "iframe tag"),
        ("<object", "object tag"),
        ("<embed", "embed tag"),
    ]
    for needle, reason in checks:
        if needle in lower:
            return {"ok": False, "reason": reason}
    if re.search(r"(?i)\s+on[a-z]+\s*=", content):
        return {"ok": False, "reason": "inline event handler"}
    if re.search(r"(?is)<script\b[^>]*\bsrc\s*=", content):
        return {"ok": False, "reason": "external script reference"}
    if re.search(r"(?is)<link\b[^>]*rel=[\"']?stylesheet", content) or re.search(r"(?is)<style\b[^>]*@import", content):
        return {"ok": False, "reason": "external style reference"}
    if not re.search(r"(?is)<(article|section)\b", content):
        return {"ok": False, "reason": "missing article or section"}
    return {"ok": True, "reason": "valid", "sha256": hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()}


def validate_fragment_html(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "reason": "missing"}
    return validate_fragment_html_text(read_text(path))


def run_ollama_fragment(
    args: argparse.Namespace,
    prompt_path: Path,
    fragment_path: Path,
    evidence_path: Path,
    log_path: Path,
    model: str,
) -> dict[str, Any]:
    prompt = read_text(prompt_path)
    evidence = read_text(evidence_path)
    system_prompt = (
        "You generate static developer documentation as safe HTML fragments from source evidence. "
        "Do not output Markdown. Do not include scripts, inline handlers, iframes, external resources, "
        "or full html/head/body wrappers. Do not invent behavior. If evidence is missing, say implementation evidence was not found."
    )
    user_prompt = f"{prompt}\n\nEvidence JSON:\n```json\n{evidence}\n```"
    started = time.monotonic()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fragment_path.parent.mkdir(parents=True, exist_ok=True)
    stream = fallback_enabled(args)
    chat_payload = {
        "model": model,
        "stream": stream,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    timeout = effective_timeout(args, "ollama")
    urls = [args.ollama_url.rstrip("/") + "/api/chat", args.ollama_url.rstrip("/") + "/api/generate"]
    errors: list[str] = []
    for index, url in enumerate(urls):
        payload = chat_payload
        if index == 1:
            payload = {"model": model, "stream": stream, "system": system_prompt, "prompt": user_prompt}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        verbose(args, f"starting Ollama fragment pass: {fragment_path.stem}")
        verbose(args, f"Ollama URL: {url}")
        if stream:
            fallback_trace(args, f"Ollama fallback mode: streaming {url} for {fragment_path.stem}")
        try:
            first_output_ms: float | None = None
            stream_log = ""
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if stream:
                    stream_log_path = log_path.with_name(f"{log_path.stem}.stream.log")
                    content, response_keys, first_output_ms = read_ollama_streaming_content(
                        args=args,
                        response=response,
                        trace_path=stream_log_path,
                        label=fragment_path.stem,
                        started=started,
                    )
                    stream_log = str(stream_log_path)
                else:
                    raw = response.read().decode("utf-8", errors="replace")
                    data = json.loads(raw)
                    response_keys = sorted(data.keys())
                    content = str(data.get("message", {}).get("content") or data.get("response") or "").strip()
            if not content:
                raise RuntimeError("Ollama returned empty content")
            fragment_path.write_text(content, encoding="utf-8")
            elapsed = time.monotonic() - started
            log_payload = {
                "engine": "ollama",
                "url": url,
                "model": model,
                "elapsed_seconds": round(elapsed, 3),
                "response_keys": response_keys,
                "stream": stream,
            }
            if stream:
                log_payload["stream_log"] = stream_log
                log_payload["first_output_ms"] = None if first_output_ms is None else round(first_output_ms, 3)
            write_json(log_path, log_payload)
            verbose(args, f"Ollama pass {fragment_path.stem} complete in {elapsed:.1f}s")
            return {"elapsed_seconds": elapsed, "model": model, "url": url, "first_output_ms": first_output_ms}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append(f"{url}: {exc}")
            if index == 0:
                continue
    write_json(log_path, {"engine": "ollama", "model": model, "errors": errors})
    raise RuntimeError(
        "Ollama generation failed. Check that `ollama serve` is running or use --engine template. "
        + " | ".join(errors)
    )


def deterministic_fragment(name: str, focus: str, context_pack: dict[str, Any]) -> str:
    target = html.escape(context_pack["target_id"])
    feature = html.escape(context_pack["feature_description"])
    files = context_pack.get("files", [])
    routes = context_pack.get("backend_routes", [])
    file_items = "\n".join(f"<li><code>{html.escape(item['path'])}</code></li>" for item in files[:12]) or "<li>No source files discovered.</li>"
    route_items = "\n".join(f"<li><code>{html.escape(route)}</code></li>" for route in routes[:12]) or "<li>No backend routes found in matched files.</li>"
    components = []
    for item in files:
        components.extend(item.get("components", []))
    component_items = "\n".join(
        f"<li><code>{html.escape(component.get('id', ''))}</code> "
        f"owned by <code>{html.escape(component.get('owner') or 'none')}</code> "
        f"for <code>{html.escape(component.get('feature') or 'none')}</code></li>"
        for component in components[:16]
    ) or "<li>No component metadata found in matched files.</li>"
    return f"""<section class="mc-doc-fragment" data-fragment="{html.escape(name)}">
  <header>
    <h2>{html.escape(name.replace("_", " ").title())}</h2>
    <p>{html.escape(focus)}</p>
  </header>
  <p><strong>Target:</strong> <code>{target}</code></p>
  <p><strong>Feature:</strong> {feature}</p>
  <h3>Source Evidence</h3>
  <ul>
    {file_items}
  </ul>
  <h3>Component Evidence</h3>
  <ul>
    {component_items}
  </ul>
  <h3>Backend Route Evidence</h3>
  <ul>
    {route_items}
  </ul>
  <p>This fallback fragment is generated deterministically from local source evidence. No model behavior is inferred beyond matched filenames, component metadata, routes, and nearby text snippets.</p>
</section>"""


def select_fragment_source_files(name: str, discovery: dict[str, Any], max_files: int) -> list[str]:
    hints = FRAGMENT_FILE_HINTS.get(name, set())
    scored: list[tuple[int, str]] = []
    for match in discovery.get("matches", []):
        path = str(match.get("path") or "")
        suffix = Path(path).suffix.lower()
        score = int(match.get("score") or 0)
        if suffix in hints:
            score += 125
        if name == "component_hierarchy" and match.get("snippets"):
            score += 25
        if name == "backend_flow" and any(route in "\n".join(match.get("snippets", [])) for route in discovery.get("backend_routes", [])):
            score += 50
        scored.append((score, path))
    ordered = [path for _score, path in sorted(scored, key=lambda item: (-item[0], item[1])) if path]
    return ordered[:max_files]


def build_fragment_evidence_pack(
    name: str,
    focus: str,
    discovery: dict[str, Any],
    root: Path,
    max_files: int,
    max_snippets_per_file: int,
    max_snippet_chars: int,
    max_total_chars: int,
) -> dict[str, Any]:
    selected_files = select_fragment_source_files(name, discovery, max_files)
    remaining_chars = max_total_chars
    files: list[dict[str, Any]] = []
    needles = [discovery["target_id"], *discovery.get("terms", [])]

    def compact_component(component: dict[str, str]) -> dict[str, str]:
        return {
            "id": component.get("id", ""),
            "kind": component.get("kind", ""),
            "owner": component.get("owner", ""),
            "feature": component.get("feature", ""),
        }

    for relative in selected_files:
        path = root / relative
        if not path.is_file() or remaining_chars <= 0:
            continue
        text = read_text(path)
        snippets = compact_snippets_for(text, needles, max_snippets_per_file, max_snippet_chars)
        components = extract_components(relative, text)
        routes = extract_routes(text)
        item = {
            "path": relative,
            "size": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
            "snippets": snippets,
            "components": [compact_component(component) for component in components[:6]],
            "routes": routes[:8],
        }
        encoded = json.dumps(item, ensure_ascii=False)
        if len(encoded) > remaining_chars:
            item["snippets"] = compact_snippets_for(text, needles, 1, max(400, min(max_snippet_chars, remaining_chars // 2)))
            encoded = json.dumps(item, ensure_ascii=False)
        if len(encoded) <= remaining_chars:
            files.append(item)
            remaining_chars -= len(encoded)
    matched_components = [json.loads(item) for item in discovery.get("matched_components", [])]
    compact_components = [compact_component(component) for component in matched_components[:4]]
    return {
        "feature_description": discovery["feature_description"],
        "target_id": discovery["target_id"],
        "fragment_name": name,
        "focus": focus,
        "source_files": selected_files,
        "files": files,
        "matched_components": compact_components,
        "backend_routes": discovery.get("backend_routes", [])[:8],
        "evidence_limits": {
            "max_files": max_files,
            "max_snippets_per_file": max_snippets_per_file,
            "max_snippet_chars": max_snippet_chars,
            "max_total_chars": max_total_chars,
        },
    }


def prompt_text(name: str, focus: str, output_path: Path, discovery: dict[str, Any], evidence_path: Path | None = None) -> str:
    files = "\n".join(f"- {path}" for path in discovery["source_files"])
    evidence = f"\nEvidence pack: {evidence_path.as_posix()}\nUse the evidence pack as the bounded source context for this fragment.\n" if evidence_path else ""
    return f"""Generate static developer documentation as safe HTML from source evidence.
Do not invent behavior. If the feature description mentions behavior not visible in the provided files, say implementation evidence was not found.
Output HTML only. Do not output Markdown. Do not include scripts, inline handlers, iframes, external resources, or full document wrappers.
Do not modify source code. Write only the requested HTML fragment inside the current work directory.
Keep this fragment focused. Prefer 3-6 short paragraphs/lists over exhaustive documentation.

Feature description: {discovery["feature_description"]}
Target id: {discovery["target_id"]}
Fragment name: {name}
Focus: {focus}
Required output path: {output_path.as_posix()}
{evidence}

Matched source files:
{files}

Cite source filenames in prose.
"""


def run_aider_fragment(args: argparse.Namespace, prompt_file: Path, output_file: Path, files: list[str], root: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if not output_file.exists():
        output_file.write_text("", encoding="utf-8")
    command = [
        args.aider_command,
        "--timeout",
        str(args.timeout),
        "--model",
        args.model,
        "--yes",
        "--message-file",
        str(prompt_file),
    ]
    if not args.aider_git:
        command.append("--no-git")
        verbose(args, "Aider fragment mode: repo map disabled")
    if not args.aider_auto_commits:
        command.append("--no-auto-commits")
        verbose(args, "Aider fragment mode: auto commits disabled")
    if not args.aider_dirty_commits:
        command.append("--no-dirty-commits")
        verbose(args, "Aider fragment mode: dirty commits disabled")
    if args.aider_map_tokens is not None:
        command.extend(["--map-tokens", str(args.aider_map_tokens)])
    extra_args = list(args.aider_extra_arg or [])
    if fallback_enabled(args):
        extra_args = [arg for arg in extra_args if arg != "--no-stream"]
        for flag in ("--stream", "--verbose"):
            if flag not in command and flag not in extra_args:
                command.append(flag)
        fallback_trace(args, "Aider fallback mode: forcing fastest visible feedback with --stream --verbose")
    command.extend(extra_args)
    command.extend([
        str(output_file),
        *files,
    ])
    log_file = output_file.parents[1] / "logs" / f"{output_file.stem}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    verbose(args, f"running Aider pass {output_file.stem}")
    verbose(args, f"prompt: {display_path(prompt_file, root)}")
    verbose(args, f"output: {display_path(output_file, root)}")
    verbose(args, f"log: {display_path(log_file, root)}")
    verbose(args, "command: " + shell_command(command))
    if not files:
        verbose(args, "warning: no relevant source files were discovered for this Aider pass")
    timeout_s = effective_timeout(args, "aider")
    try:
        if fallback_enabled(args):
            returncode = stream_subprocess_to_console_and_log(
                args=args,
                command=command,
                cwd=root,
                log_file=log_file,
                timeout=timeout_s,
                label=f"Aider fragment {output_file.stem}",
            )
        else:
            with log_file.open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=root, text=True, stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s)
            returncode = result.returncode
    except subprocess.TimeoutExpired as exc:
        log_tail = ""
        if log_file.exists():
            lines = read_text(log_file).splitlines()
            log_tail = "\n".join(lines[-40:])
        raise RuntimeError(
            f"Aider fragment pass {output_file.stem} timed out after {timeout_s}s; see {log_file}"
            + (f"\nLast log lines:\n{log_tail}" if log_tail else "")
        ) from exc
    if returncode != 0:
        log_tail = ""
        if log_file.exists():
            lines = read_text(log_file).splitlines()
            log_tail = "\n".join(lines[-40:])
        raise RuntimeError(
            f"Aider fragment pass {output_file.stem} failed with exit code {returncode}; see {log_file}"
            + (f"\nLast log lines:\n{log_tail}" if log_tail else "")
        )
    verbose(args, f"Aider pass {output_file.stem} complete")


def assemble_doc(target_id: str, feature: str, fragments: list[Path], context_pack: dict[str, Any]) -> str:
    safe_target = html.escape(target_id)
    safe_feature = html.escape(feature)
    parts = [
        f'<article class="mc-component-doc" data-mc-doc-target="{safe_target}">',
        "<header>",
        f"<h1>{safe_feature}</h1>",
        f"<p><strong>Anchor:</strong> <code>{safe_target}</code></p>",
        "</header>",
    ]
    section_titles = [
        "Summary",
        "Component hierarchy",
        "User-facing behavior",
        "Frontend flow",
        "Backend flow",
        "State, modes, and configuration",
        "Generated items",
        "Public scripting/API surface",
        "Tests and verification",
        "Failure modes",
        "Extension notes",
        "Source files",
    ]
    for index, fragment in enumerate(fragments):
        title = section_titles[index] if index < len(section_titles) else fragment.stem.replace("_", " ").title()
        body = safe_fragment_html(read_text(fragment)) if fragment.exists() else ""
        parts.append(f'<section class="mc-doc-section" data-section="{html.escape(fragment.stem)}">')
        parts.append(f"<h2>{html.escape(title)}</h2>")
        parts.append(body or "<p>No fragment content was generated.</p>")
        parts.append("</section>")
    parts.append('<section class="mc-doc-section" data-section="source-files">')
    parts.append("<h2>Source files</h2><ul>")
    for item in context_pack.get("files", []):
        parts.append(f"<li><code>{html.escape(item['path'])}</code></li>")
    parts.append("</ul></section>")
    parts.append("</article>")
    return "\n".join(parts)


def update_manifest(manifest_path: Path, entry: dict[str, Any], dry_run: bool) -> None:
    manifest = {"schema_version": 1, "entries": []}
    if manifest_path.exists():
        try:
            manifest = json.loads(read_text(manifest_path))
        except json.JSONDecodeError:
            manifest = {"schema_version": 1, "entries": []}
    if not isinstance(manifest, dict):
        manifest = {"schema_version": 1, "entries": []}
    manifest["schema_version"] = 1
    entry_aliases = {str(alias) for alias in entry.get("aliases", []) if alias}
    entry_aliases.discard(str(entry.get("id") or ""))
    entries = []
    merged_aliases = set(entry_aliases)
    for item in manifest.get("entries", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        item_aliases = {str(alias) for alias in item.get("aliases", []) if alias}
        is_same_entry = item_id == entry["id"] or item_id in entry_aliases or bool(item_aliases & ({entry["id"]} | entry_aliases))
        if is_same_entry:
            if item_id and item_id != entry["id"]:
                merged_aliases.add(item_id)
            merged_aliases.update(alias for alias in item_aliases if alias != entry["id"])
            continue
        if item.get("doc_path"):
            try:
                item["doc_path"] = normalize_manifest_doc_path(item["doc_path"])
            except ValueError:
                pass
        item.setdefault("aliases", [])
        entries.append(item)
    entry = {**entry, "aliases": sorted(merged_aliases), "doc_path": normalize_manifest_doc_path(entry.get("doc_path", ""))}
    entries.append(entry)
    manifest["entries"] = sorted(entries, key=lambda item: item.get("id", ""))
    if not dry_run:
        write_json(manifest_path, manifest)


def archive_existing(final_path: Path, archive_root: Path, safe_id: str, timestamp: str, dry_run: bool) -> str | None:
    if not final_path.exists():
        return None
    archive_path = archive_root / safe_id / f"{timestamp}.html"
    if not dry_run:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final_path), str(archive_path))
    return archive_path.as_posix()


def default_run_state(
    args: argparse.Namespace,
    target_id: str,
    feature: str,
    safe_id: str,
    safe_feature: str,
    timestamp: str,
    source_fp: str,
    plan_fp: str,
    source_files: list[str],
) -> dict[str, Any]:
    now = utc_iso_timestamp()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "target_id": target_id,
        "feature_description": feature,
        "safe_target_id": safe_id,
        "safe_feature_slug": safe_feature,
        "created_at": now,
        "updated_at": now,
        "source_fingerprint": source_fp,
        "plan_fingerprint": plan_fp,
        "source_files": source_files,
        "options": state_options(args),
        "stages": {
            "discovery": "missing",
            "context": "missing",
            "assembly": "missing",
            "final_doc": "missing",
            "manifest": "missing",
        },
        "fragments": {
            name: {"status": "missing", "path": f"fragments/{name}.html"}
            for name, _focus in FRAGMENT_PASSES
        },
        "final_doc": "",
        "manifest_status": "missing",
        "dry_run": bool(args.dry_run),
        "status": "planned",
    }


def state_options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "model": args.model,
        "engine": effective_engine(args),
        "resolved_model": resolved_ollama_model(args) if effective_engine(args) == "ollama" else args.model,
        "ollama_url": args.ollama_url if effective_engine(args) == "ollama" else "",
        "scope": args.scope,
        "aider_source_mode": args.aider_source_mode,
        "include_docgen_source": bool(args.include_docgen_source),
        "max_files_per_pass": args.max_files_per_pass,
        "max_snippets_per_file": args.max_snippets_per_file,
        "max_snippet_chars": args.max_snippet_chars,
        "max_evidence_chars": args.max_evidence_chars,
        "no_aider": bool(args.no_aider),
        "accept_template_fragments": bool(args.accept_template_fragments),
        "accept_tainted_fragments": bool(args.accept_tainted_fragments),
    }


def save_run_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_iso_timestamp()
    write_json(path, state)


def load_work_index(work_root: Path) -> dict[str, Any]:
    index = read_json_file(work_root / "index.json", {"entries": []})
    if not isinstance(index, dict):
        return {"entries": []}
    if not isinstance(index.get("entries"), list):
        index["entries"] = []
    return index


def update_work_index(work_root: Path, entry: dict[str, Any]) -> None:
    index_path = work_root / "index.json"
    index = load_work_index(work_root)
    entries = [item for item in index.get("entries", []) if item.get("work_dir") != entry.get("work_dir")]
    entries.append(entry)
    index["entries"] = sorted(entries, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))
    write_json(index_path, index)


def remove_work_index_entries(work_root: Path, deleted_work_dirs: set[str]) -> None:
    if not deleted_work_dirs:
        return
    index_path = work_root / "index.json"
    index = load_work_index(work_root)
    index["entries"] = [entry for entry in index.get("entries", []) if str(entry.get("work_dir")) not in deleted_work_dirs]
    write_json(index_path, index)


def rebuild_work_index(work_root: Path, root: Path) -> None:
    entries = scan_work_states(work_root, root)
    write_json(work_root / "index.json", {"entries": entries})


def index_entry_from_state(state: dict[str, Any], work_dir: Path, root: Path) -> dict[str, Any]:
    return {
        "target_id": state.get("target_id", ""),
        "feature_description": state.get("feature_description", ""),
        "safe_target_id": state.get("safe_target_id", safe_slug(str(state.get("target_id") or ""), "target")),
        "safe_feature_slug": state.get("safe_feature_slug", safe_slug(str(state.get("feature_description") or ""), "feature")),
        "work_dir": display_path(work_dir, root),
        "created_at": state.get("created_at", ""),
        "updated_at": state.get("updated_at", ""),
        "source_fingerprint": state.get("source_fingerprint", ""),
        "plan_fingerprint": state.get("plan_fingerprint", ""),
        "model": state.get("options", {}).get("model", ""),
        "status": state.get("status", ""),
        "dry_run": bool(state.get("dry_run")),
        "fragments": state.get("fragments", {}),
    }


def scan_work_states(work_root: Path, root: Path) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for state_path in work_root.glob("*/run_state.json"):
        state = read_json_file(state_path, {})
        if isinstance(state, dict) and state.get("target_id"):
            states.append(index_entry_from_state(state, state_path.parent, root))
    return states


def legacy_work_entries(work_root: Path, root: Path, target_id: str, feature: str, safe_id: str, safe_feature: str) -> list[dict[str, Any]]:
    prefix = f"{safe_feature}__{safe_id}__"
    entries: list[dict[str, Any]] = []
    if not work_root.is_dir():
        return entries
    for child in work_root.iterdir():
        if not child.is_dir() or not child.name.startswith(prefix) or (child / "run_state.json").exists():
            continue
        if not is_path_under(child, work_root):
            continue
        artifacts = [child / name for name in ["prompts", "evidence", "fragments", "logs", "discovery.json", "context_pack.json"]]
        if not any(path.exists() for path in artifacts):
            continue
        assembled_dir = child / "assembled"
        uncertain = assembled_dir.exists() and any(assembled_dir.glob("*.html"))
        stat = child.stat()
        timestamp = _dt.datetime.fromtimestamp(stat.st_mtime, _dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        entries.append(
            {
                "target_id": target_id,
                "feature_description": feature,
                "safe_target_id": safe_id,
                "safe_feature_slug": safe_feature,
                "work_dir": display_path(child, root),
                "created_at": timestamp,
                "updated_at": timestamp,
                "source_fingerprint": "",
                "plan_fingerprint": "",
                "model": "",
                "status": "legacy-uncertain" if uncertain else "legacy-planning",
                "dry_run": not uncertain,
                "legacy": True,
                "uncertain": uncertain,
                "fragments": {},
            }
        )
    return entries


def candidate_work_entries(work_root: Path, root: Path) -> list[dict[str, Any]]:
    index = load_work_index(work_root)
    entries = [entry for entry in index.get("entries", []) if isinstance(entry, dict)]
    if entries:
        return entries
    entries = scan_work_states(work_root, root)
    if entries:
        write_json(work_root / "index.json", {"entries": entries})
    return entries


def resolve_work_dir(entry: dict[str, Any], root: Path, work_root: Path) -> Path:
    raw = str(entry.get("work_dir") or "")
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve() if raw else work_root


def find_resume_work_dir(
    args: argparse.Namespace,
    root: Path,
    work_root: Path,
    target_id: str,
    feature: str,
    source_fp: str,
    plan_fp: str,
) -> Path | None:
    if not args.resume or args.force_full_rebuild:
        if args.force_full_rebuild:
            verbose(args, "force full rebuild requested")
        return None
    matches: list[tuple[str, Path, dict[str, Any]]] = []
    for entry in candidate_work_entries(work_root, root):
        if entry.get("target_id") != target_id or entry.get("feature_description") != feature:
            continue
        work_dir = resolve_work_dir(entry, root, work_root)
        verbose(args, f"resume candidate found: {display_path(work_dir, root)}")
        if bool(entry.get("dry_run")) and not args.reuse_dry_run:
            verbose(args, "resume rejected: dry-run reuse disabled")
            continue
        if entry.get("source_fingerprint") != source_fp:
            verbose(args, "resume rejected: source fingerprint mismatch")
            continue
        if entry.get("plan_fingerprint") != plan_fp:
            verbose(args, "resume rejected: plan fingerprint mismatch")
            continue
        state_path = work_dir / "run_state.json"
        if not state_path.is_file():
            verbose(args, "resume rejected: missing run_state.json")
            continue
        timestamp = str(entry.get("updated_at") or entry.get("created_at") or "")
        matches.append((timestamp, work_dir, entry))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    work_dir = matches[-1][1]
    verbose(args, "resume accepted: source and plan fingerprints match")
    return work_dir


def is_path_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def cleanup_old_dry_runs(
    args: argparse.Namespace,
    root: Path,
    work_root: Path,
    target_id: str,
    feature: str,
    safe_id: str,
    safe_feature: str,
) -> None:
    if not args.force_full_rebuild or args.preserve_dry_runs:
        return
    entries = [
        entry
        for entry in [*candidate_work_entries(work_root, root), *legacy_work_entries(work_root, root, target_id, feature, safe_id, safe_feature)]
        if entry.get("target_id") == target_id
        and entry.get("feature_description") == feature
        and entry.get("safe_target_id") == safe_id
        and entry.get("safe_feature_slug") == safe_feature
    ]
    for entry in entries:
        if entry.get("legacy"):
            verbose(args, f"legacy dry-run retention: found matching legacy work dir {entry.get('work_dir')}")
    dry_runs = [entry for entry in entries if bool(entry.get("dry_run")) and not bool(entry.get("uncertain"))]
    if not dry_runs:
        verbose(args, "dry-run retention: no matching dry runs found")
        return
    dry_runs.sort(key=lambda entry: str(entry.get("updated_at") or entry.get("created_at") or ""))
    latest = dry_runs[-1]
    latest_path = resolve_work_dir(latest, root, work_root)
    verbose(args, f"dry-run retention: preserving latest dry run {display_path(latest_path, root)}")
    if latest.get("legacy"):
        verbose(args, f"legacy dry-run retention: preserving latest legacy work dir {display_path(latest_path, root)}")
    deleted: set[str] = set()
    for entry in dry_runs[:-1]:
        work_dir = resolve_work_dir(entry, root, work_root)
        if not is_path_under(work_dir, work_root) or work_dir == work_root or ".." in work_dir.parts:
            verbose(args, f"dry-run retention: skipped unsafe path {work_dir}")
            continue
        try:
            verbose(args, f"dry-run retention: deleting older dry run {display_path(work_dir, root)}")
            if entry.get("legacy"):
                verbose(args, f"legacy dry-run retention: deleting older legacy work dir {display_path(work_dir, root)}")
            shutil.rmtree(work_dir)
            deleted.add(str(entry.get("work_dir") or display_path(work_dir, root)))
        except OSError as exc:
            verbose(args, f"dry-run retention: warning could not delete {display_path(work_dir, root)}: {exc}")
    for entry in entries:
        if entry.get("uncertain"):
            verbose(args, f"legacy dry-run retention: preserving uncertain work dir {entry.get('work_dir')}")
        elif not bool(entry.get("dry_run")):
            verbose(args, f"dry-run retention: skipped non-dry-run work dir {entry.get('work_dir')}")
    remove_work_index_entries(work_root, deleted)
    rebuild_work_index(work_root, root)


def hash_files(root: Path, paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in sorted(set(paths)):
        path = root / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def restore_changed_files(root: Path, before_contents: dict[str, str], before_hashes: dict[str, str]) -> list[str]:
    changed: list[str] = []
    for relative, before_hash in before_hashes.items():
        path = root / relative
        current_hash = hashlib.sha256(read_text(path).encode("utf-8", errors="replace")).hexdigest() if path.is_file() else ""
        if current_hash != before_hash:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(before_contents[relative], encoding="utf-8")
            changed.append(relative)
    return changed


def docgen_source_files(root: Path) -> list[str]:
    return sorted(relative for relative in DOCGEN_SOURCE_PATHS if (root / relative).is_file())


def fragment_is_complete(
    fragment_path: Path,
    fragment_state: dict[str, Any],
    fingerprint: str,
    accept_template_fragments: bool,
    accept_tainted_fragments: bool = False,
    name: str = "",
    focus: str = "",
) -> tuple[bool, str, dict[str, Any]]:
    validation = validate_fragment_html(fragment_path)
    if not validation.get("ok") and validation.get("reason") == "missing article or section":
        repaired = normalize_fragment_html(name or fragment_path.stem, focus, read_text(fragment_path))
        repaired_validation = validate_fragment_html_text(repaired)
        if repaired_validation.get("ok"):
            fragment_path.write_text(repaired, encoding="utf-8")
            validation = {**repaired_validation, "repaired": "wrapped missing article or section"}
        else:
            return False, str(repaired_validation.get("reason") or "invalid html"), repaired_validation
    if not validation.get("ok"):
        return False, str(validation.get("reason") or "invalid html"), validation
    if fragment_state.get("fingerprint") != fingerprint:
        return False, "fingerprint mismatch", validation
    status = str(fragment_state.get("status") or "missing")
    if status in SAFE_FRAGMENT_STATUSES:
        return True, "complete", validation
    if status in TEMPLATE_FRAGMENT_STATUSES:
        if accept_template_fragments:
            return True, "accepted template", validation
        return False, "previous output was template", validation
    if status in TAINTED_FRAGMENT_STATUSES:
        if accept_tainted_fragments:
            return True, "accepted tainted fragment", validation
        return False, "tainted fragment needs review", validation
    if status == "failed":
        return False, "previous status failed", validation
    return False, f"status {status}", validation


def build_docs(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root()
    requested_id = args.id.strip()
    doc_identity = resolve_doc_target_identity(requested_id, root)
    target_id = doc_identity["id"]
    safe_id = safe_slug(target_id, "target")
    safe_feature = safe_slug(args.feature, "feature")
    timestamp = utc_timestamp()
    output_root = (root / args.output_root).resolve()
    work_root = (root / args.work_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)

    discovery = discover(args.feature, target_id, root, include_docgen_source=args.include_docgen_source)
    if requested_id != target_id:
        legacy_discovery = discover(args.feature, requested_id, root, include_docgen_source=args.include_docgen_source)
        for key in ["source_files", "backend_routes", "excluded_docgen_sources"]:
            discovery[key] = sorted(set(discovery.get(key, [])) | set(legacy_discovery.get(key, [])))
        discovery["matches"] = [*discovery.get("matches", []), *legacy_discovery.get("matches", [])]
        discovery["matched_components"] = sorted(set(discovery.get("matched_components", [])) | set(legacy_discovery.get("matched_components", [])))
        discovery["aliases"] = doc_identity["aliases"]
        discovery["requested_id"] = requested_id
    verbose(args, f"discovered {len(discovery['source_files'])} source file(s) for {target_id}")
    for path in discovery["source_files"]:
        verbose(args, f"  source: {path}")
    for path in discovery.get("excluded_docgen_sources", []):
        verbose(args, f"excluded docgen source from feature fingerprint: {path}")
    context_pack = build_context_pack(discovery, root)
    source_fp = source_fingerprint(discovery, context_pack)
    scope = args.scope
    if scope == "auto":
        scope, scope_reason = auto_scope_for(discovery)
        verbose(args, f"auto scope selected: {scope} because {scope_reason}")
    fragment_passes = fragment_plan_for_scope(scope, discovery, args.max_fragments)
    plan_fp = plan_fingerprint(args, target_id, args.feature, fragment_passes, scope)
    verbose(args, f"engine selected: {effective_engine(args)}")
    if effective_engine(args) == "ollama":
        verbose(args, f"resolved Ollama model: {resolved_ollama_model(args)}")
        verbose(args, f"Ollama URL: {args.ollama_url}")
    elif effective_engine(args) == "aider":
        verbose(args, "engine selected: aider")
        verbose(args, "Aider mode is optional and source-protected")

    cleanup_old_dry_runs(args, root, work_root, target_id, args.feature, safe_id, safe_feature)
    resumed = False
    work_dir = find_resume_work_dir(args, root, work_root, target_id, args.feature, source_fp, plan_fp)
    if work_dir is None:
        work_dir = work_root / work_dir_basename(args.feature, target_id, timestamp)
    else:
        resumed = True
    prompts_dir = work_dir / "prompts"
    fragments_dir = work_dir / "fragments"
    assembled_dir = work_dir / "assembled"
    logs_dir = work_dir / "logs"
    evidence_dir = work_dir / "evidence"
    for directory in [prompts_dir, fragments_dir, assembled_dir, logs_dir, evidence_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    verbose(args, f"work directory: {display_path(work_dir, root)}")

    run_state_path = work_dir / "run_state.json"
    run_state = read_json_file(run_state_path, {})
    if not isinstance(run_state, dict) or not resumed:
        run_state = default_run_state(args, target_id, args.feature, safe_id, safe_feature, timestamp, source_fp, plan_fp, discovery["source_files"])
    else:
        run_state.setdefault("created_at", utc_iso_timestamp())
        run_state["target_id"] = target_id
        run_state["feature_description"] = args.feature
        run_state["safe_target_id"] = safe_id
        run_state["safe_feature_slug"] = safe_feature
        run_state["source_fingerprint"] = source_fp
        run_state["plan_fingerprint"] = plan_fp
        run_state["source_files"] = discovery["source_files"]
        run_state["options"] = state_options(args)
        run_state["dry_run"] = bool(args.dry_run)
        run_state.setdefault("stages", {})
        run_state.setdefault("fragments", {})

    discovery_path = work_dir / "discovery.json"
    context_path = work_dir / "context_pack.json"
    if resumed and discovery_path.exists() and context_path.exists():
        verbose(args, "reusing discovery/context")
    else:
        write_json(discovery_path, discovery)
        write_json(context_path, context_pack)
        verbose(args, "wrote discovery.json and context_pack.json")
    run_state["stages"]["discovery"] = "complete"
    run_state["stages"]["context"] = "complete"
    save_run_state(run_state_path, run_state)

    fragment_paths: list[Path] = []
    fragments_reused: list[str] = []
    fragments_generated: list[str] = []
    fragments_failed: list[str] = []
    forced_fragments = set(args.force_fragments or [])
    max_files, max_snippets, max_snippet_chars, max_evidence_chars = effective_evidence_limits(args, scope)
    current_stage_path = work_dir / "current_stage.json"
    for name, focus in fragment_passes:
        verbose(args, f"starting fragment pass: {name}")
        fragment_path = fragments_dir / f"{name}.html"
        prompt_path = prompts_dir / f"{name}.txt"
        log_path = logs_dir / f"{name}.log"
        fragment_model = model_for_fragment(args, scope, name)
        evidence_pack = build_fragment_evidence_pack(
            name,
            focus,
            discovery,
            root,
            max_files,
            max_snippets,
            max_snippet_chars,
            max_evidence_chars,
        )
        evidence_path = evidence_dir / f"{name}.json"
        next_prompt_text = prompt_text(name, focus, fragment_path, discovery, evidence_path)
        next_fragment_fp = fragment_fingerprint(name, next_prompt_text, evidence_pack, args, fragment_model)
        fragment_state = run_state.setdefault("fragments", {}).get(name, {})
        can_reuse, reuse_reason, validation = fragment_is_complete(
            fragment_path,
            fragment_state,
            next_fragment_fp,
            args.accept_template_fragments,
            args.accept_tainted_fragments,
            name,
            focus,
        )
        if validation.get("repaired"):
            verbose(args, f"repaired fragment wrapper: {name}")
        if name in forced_fragments:
            can_reuse = False
            reuse_reason = "forced by --force-fragments"
        evidence_changed = not evidence_path.exists() or hash_json(read_json_file(evidence_path, {})) != hash_json(evidence_pack)
        prompt_changed = not prompt_path.exists() or read_text(prompt_path) != next_prompt_text
        if not can_reuse or evidence_changed:
            write_json(evidence_path, evidence_pack)
        if not can_reuse or prompt_changed:
            prompt_path.write_text(next_prompt_text, encoding="utf-8")
        model_files = [display_path(evidence_path, root)]
        if effective_engine(args) == "aider" and args.aider_source_mode == "files":
            aider_files = evidence_pack["source_files"]
        else:
            aider_files = model_files
        verbose(
            args,
            f"fragment pass {name} using {len(aider_files)} Aider file(s); evidence file covers {len(evidence_pack['files'])} source file(s)",
        )
        if can_reuse:
            verbose(args, f"reusing fragment: {name}")
            run_state["fragments"][name] = {
                **fragment_state,
                "status": "validated" if fragment_state.get("status") == "generated" else fragment_state.get("status", "validated"),
                "path": f"fragments/{name}.html",
                "prompt": f"prompts/{name}.txt",
                "evidence": f"evidence/{name}.json",
                "log": f"logs/{name}.log",
                "fingerprint": next_fragment_fp,
                "validation": validation,
            }
            fragments_reused.append(name)
        else:
            verbose(args, f"regenerating fragment: {name} because {reuse_reason}")
            write_json(
                current_stage_path,
                {
                    "stage": "fragment",
                    "fragment": name,
                    "engine": effective_engine(args),
                    "model": fragment_model,
                    "prompt": display_path(prompt_path, root),
                    "evidence": display_path(evidence_path, root),
                    "output": display_path(fragment_path, root),
                    "log": display_path(log_path, root),
                    "started_at": utc_iso_timestamp(),
                },
            )
            started = time.monotonic()
            try:
                engine = effective_engine(args)
                source_check = {"checked": True, "changed": []}
                if engine == "template" or args.dry_run:
                    normalized = normalize_fragment_html(name, focus, deterministic_fragment(name, focus, context_pack))
                    fragment_path.write_text(normalized, encoding="utf-8")
                    status = "template"
                    verbose(args, f"wrote deterministic fragment: {display_path(fragment_path, root)}")
                elif engine == "ollama":
                    protected_files = sorted(path for path in discovery["source_files"] if not is_docgen_source_path(path))
                    protected_docgen_files = docgen_source_files(root)
                    before_contents = {path: read_text(root / path) for path in [*protected_files, *protected_docgen_files] if (root / path).is_file()}
                    before_hashes = {path: hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest() for path, content in before_contents.items()}
                    run_meta = run_ollama_fragment(args, prompt_path, fragment_path, evidence_path, log_path, fragment_model)
                    changed = restore_changed_files(root, before_contents, before_hashes)
                    source_check = {"checked": True, "changed": changed}
                    if changed:
                        raise RuntimeError(f"Ollama documentation generation unexpectedly changed source files: {changed}")
                    normalized = normalize_fragment_html(name, focus, read_text(fragment_path))
                    fragment_path.write_text(normalized, encoding="utf-8")
                    status = "generated"
                    verbose(args, f"normalized fragment: {display_path(fragment_path, root)}")
                else:
                    protected_files = sorted(path for path in discovery["source_files"] if not is_docgen_source_path(path))
                    protected_docgen_files = docgen_source_files(root)
                    before_contents = {path: read_text(root / path) for path in [*protected_files, *protected_docgen_files] if (root / path).is_file()}
                    before_hashes = {path: hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest() for path, content in before_contents.items()}
                    run_aider_fragment(args, prompt_path, fragment_path, aider_files, root)
                    changed = restore_changed_files(root, before_contents, before_hashes)
                    changed_docgen = [path for path in changed if is_docgen_source_path(path)]
                    changed_target = [path for path in changed if not is_docgen_source_path(path)]
                    if changed:
                        for path in changed:
                            verbose(args, f"restored forbidden source file: {path}")
                        normalized = normalize_fragment_html(name, focus, read_text(fragment_path)) if fragment_path.exists() else ""
                        fragment_path.write_text(normalized, encoding="utf-8")
                        validation = validate_fragment_html(fragment_path)
                        if validation.get("ok"):
                            message = (
                                "Aider attempted to modify documentation generator files"
                                if changed_docgen
                                else f"Aider modified source files during documentation generation: {changed_target}"
                            )
                            verbose(args, f"fragment was written but run was tainted: {name}")
                            run_state["fragments"][name] = {
                                "status": "generated_tainted",
                                "path": f"fragments/{name}.html",
                                "prompt": f"prompts/{name}.txt",
                                "evidence": f"evidence/{name}.json",
                                "log": f"logs/{name}.log",
                                "fingerprint": next_fragment_fp,
                                "generated_by": args.model,
                                "validation": validation,
                                "tainted_files": changed,
                                "last_error": message,
                            }
                            save_run_state(run_state_path, run_state)
                            update_work_index(work_root, index_entry_from_state(run_state, work_dir, root))
                            if not args.accept_tainted_fragments:
                                raise TaintedAiderRun(message)
                        else:
                            raise RuntimeError(f"Aider touched forbidden files and fragment was not valid: {changed}")
                    normalized = normalize_fragment_html(name, focus, read_text(fragment_path))
                    fragment_path.write_text(normalized, encoding="utf-8")
                    status = "generated_tainted" if changed else "generated"
                    verbose(args, f"normalized fragment: {name}")
                    verbose(args, f"sanitized fragment: {display_path(fragment_path, root)}")
                validation = validate_fragment_html(fragment_path)
                if not validation.get("ok"):
                    raise RuntimeError(f"Fragment {name} failed HTML validation: {validation.get('reason')}")
                elapsed = time.monotonic() - started
                verbose(args, f"validated fragment: {display_path(fragment_path, root)}")
                run_state["fragments"][name] = {
                    "status": status,
                    "path": f"fragments/{name}.html",
                    "prompt": f"prompts/{name}.txt",
                    "evidence": f"evidence/{name}.json",
                    "log": f"logs/{name}.log",
                    "fingerprint": next_fragment_fp,
                    "generated_by": None if engine == "template" else fragment_model,
                    "engine": engine,
                    "resolved_model": fragment_model,
                    "elapsed_seconds": round(elapsed, 3),
                    "source_mutation_check": source_check,
                    "validation": validation,
                    "last_error": "",
                }
                fragments_generated.append(name)
            except Exception as exc:
                if isinstance(exc, TaintedAiderRun):
                    run_state["status"] = "needs_review"
                    save_run_state(run_state_path, run_state)
                    update_work_index(work_root, index_entry_from_state(run_state, work_dir, root))
                    raise
                run_state["fragments"][name] = {
                    "status": "failed",
                    "path": f"fragments/{name}.html",
                    "prompt": f"prompts/{name}.txt",
                    "evidence": f"evidence/{name}.json",
                    "log": f"logs/{name}.log",
                    "fingerprint": next_fragment_fp,
                    "generated_by": None if args.no_aider else args.model,
                    "validation": validate_fragment_html(fragment_path),
                    "last_error": str(exc),
                }
                run_state["status"] = "failed"
                save_run_state(run_state_path, run_state)
                update_work_index(work_root, index_entry_from_state(run_state, work_dir, root))
                fragments_failed.append(name)
                raise
        save_run_state(run_state_path, run_state)
        fragment_paths.append(fragment_path)

    verbose(args, "assembling from existing fragments")
    final_html = assemble_doc(target_id, args.feature, fragment_paths, context_pack)
    assembled_path = assembled_dir / f"{safe_id}.html"
    assembled_fp = hashlib.sha256(final_html.encode("utf-8", errors="replace")).hexdigest()
    if assembled_path.exists() and hashlib.sha256(read_text(assembled_path).encode("utf-8", errors="replace")).hexdigest() == assembled_fp:
        verbose(args, "reusing assembled document")
    else:
        assembled_path.write_text(final_html, encoding="utf-8")
    run_state["stages"]["assembly"] = "complete"
    final_path = output_root / "nodes" / f"{safe_id}.html"
    final_matches = final_path.exists() and hashlib.sha256(read_text(final_path).encode("utf-8", errors="replace")).hexdigest() == assembled_fp
    archived = None if final_matches else archive_existing(final_path, output_root / "archive", safe_id, timestamp, args.dry_run)
    if archived:
        verbose(args, f"archived existing doc to {archived}")
    if not args.dry_run:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists() and hashlib.sha256(read_text(final_path).encode("utf-8", errors="replace")).hexdigest() == assembled_fp:
            verbose(args, f"reusing final doc: {display_path(final_path, root)}")
        else:
            verbose(args, "promoting assembled document")
            final_path.write_text(final_html, encoding="utf-8")
            verbose(args, f"wrote final doc: {display_path(final_path, root)}")
        run_state["stages"]["final_doc"] = "complete"
    else:
        verbose(args, f"dry run: final doc would be {display_path(final_path, root)}")
        run_state["stages"]["final_doc"] = "skipped"

    manifest_doc_path = docs_root_relative_path(final_path, output_root)
    manifest_entry = {
        "id": target_id,
        "aliases": doc_identity["aliases"],
        "title": doc_identity["title"],
        "feature_description": args.feature,
        "doc_path": manifest_doc_path,
        "content_type": "text/html",
        "status": "dry-run" if args.dry_run else "generated",
        "generated_at": timestamp,
        "source_files": discovery["source_files"],
        "work_dir": display_path(work_dir, root),
        "fragments": [display_path(path, root) for path in fragment_paths],
        "model": None if args.no_aider else args.model,
        "engine": effective_engine(args),
        "resolved_model": resolved_ollama_model(args) if effective_engine(args) == "ollama" else args.model,
        "scope": scope,
        "matched_components": [json.loads(item) for item in discovery["matched_components"]],
        "backend_routes": discovery["backend_routes"],
        "tests": [path for path in discovery["source_files"] if path.startswith("tests/")],
        "source_fingerprint": source_fp,
        "plan_fingerprint": plan_fp,
        "assembled_fingerprint": assembled_fp,
    }
    update_manifest(output_root / "manifest.json", manifest_entry, args.dry_run)
    run_state["stages"]["manifest"] = "skipped" if args.dry_run else "complete"
    run_state["manifest_status"] = run_state["stages"]["manifest"]
    run_state["final_doc"] = display_path(final_path, root)
    run_state["manifest_doc_path"] = manifest_doc_path
    run_state["status"] = "dry-run" if args.dry_run else "generated"
    run_state["engine"] = effective_engine(args)
    run_state["resolved_model"] = resolved_ollama_model(args) if effective_engine(args) == "ollama" else args.model
    run_state["ollama_url"] = args.ollama_url if effective_engine(args) == "ollama" else ""
    run_state["scope"] = scope
    run_state["fragment_plan"] = [name for name, _focus in fragment_passes]
    save_run_state(run_state_path, run_state)
    update_work_index(work_root, index_entry_from_state(run_state, work_dir, root))
    verbose(args, f"{'would update' if args.dry_run else 'updated'} manifest: {display_path(output_root / 'manifest.json', root)}")

    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "target_id": target_id,
        "engine": effective_engine(args),
        "scope": scope,
        "work_dir": display_path(work_dir, root),
        "resumed": resumed,
        "force_full_rebuild": bool(args.force_full_rebuild),
        "fragments_reused": fragments_reused,
        "fragments_generated": fragments_generated,
        "fragments_failed": fragments_failed,
        "final_doc": display_path(final_path, root),
        "manifest_doc_path": manifest_doc_path,
        "archived": archived,
        "source_files": discovery["source_files"],
        "manifest": display_path(output_root / "manifest.json", root),
    }
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild safe HTML documentation for a feature/component anchor.")
    parser.add_argument("--feature", required=True, help="Human description of the feature to document.")
    parser.add_argument("--id", required=True, help="Anchor id, route, DOM id, component id, widget id, feature id, function name, or filename.")
    parser.add_argument("--model", default="ollama_chat/gemma4:26b")
    parser.add_argument("--engine", choices=["ollama", "aider", "template"], default="ollama")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model", default="")
    parser.add_argument("--aider-command", default="aider")
    parser.add_argument("--output-root", default="generated_component_docs")
    parser.add_argument("--work-root", default="generated_component_docs/work")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=60000)
    parser.add_argument("--ollama-timeout", type=float, default=None)
    parser.add_argument("--process-timeout", type=float, default=7200)
    parser.add_argument("--no-aider", action="store_true")
    parser.add_argument("--keep-work", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--verbose", action="store_true", help="Print discovery, prompt, command, output, and log paths while rebuilding.")
    parser.add_argument(
        "--fallback",
        action="store_true",
        help=(
            "Enable hyper-verbose prompt-test fallback mode: stream model/tool output to stderr "
            "and logs immediately, include fastest-feedback Aider flags, and timestamp first output."
        ),
    )
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True, help="Reuse compatible previous work when available.")
    parser.add_argument("--force-full-rebuild", action="store_true", help="Ignore prior work and create a fresh work directory.")
    parser.add_argument("--force-fragments", nargs="*", default=[], help="Regenerate only the named fragment passes even if complete.")
    parser.add_argument("--reuse-dry-run", action=argparse.BooleanOptionalAction, default=True, help="Allow reuse of setup artifacts from previous dry runs.")
    parser.add_argument("--accept-template-fragments", action="store_true", help="Treat deterministic template fragments as complete.")
    parser.add_argument("--accept-tainted-fragments", action="store_true", help="Treat safe fragments from tainted Aider runs as reusable.")
    parser.add_argument("--preserve-dry-runs", action="store_true", help="Keep all matching dry-run work dirs during force-full-rebuild.")
    parser.add_argument("--include-docgen-source", action="store_true", help="Include the documentation generator script and tests in discovery.")
    parser.add_argument("--aider-auto-commits", action="store_true", help="Allow Aider to auto-commit documentation changes.")
    parser.add_argument("--aider-dirty-commits", action="store_true", help="Allow Aider dirty commits.")
    parser.add_argument("--aider-git", action="store_true", help="Allow Aider git integration and repo-map behavior.")
    parser.add_argument("--aider-map-tokens", type=int, default=0, help="Repo-map token budget for Aider fragment mode.")
    parser.add_argument("--aider-extra-arg", nargs="*", default=[], help="Additional passthrough flags for local Aider compatibility.")
    parser.add_argument("--scope", choices=["auto", "micro", "component", "feature", "app"], default="auto")
    parser.add_argument("--fast-model", default="")
    parser.add_argument("--deep-model", default="")
    parser.add_argument("--max-fragments", type=int, default=0)
    parser.add_argument(
        "--aider-source-mode",
        choices=["evidence", "files"],
        default="evidence",
        help="Use compact per-fragment evidence packs by default; pass live source files only when explicitly requested.",
    )
    parser.add_argument("--max-files-per-pass", type=int, default=4, help="Maximum source files summarized into each fragment evidence pack.")
    parser.add_argument("--max-snippets-per-file", type=int, default=3, help="Maximum matched snippets per source file in each evidence pack.")
    parser.add_argument("--max-snippet-chars", type=int, default=1200, help="Maximum characters per snippet in each evidence pack.")
    parser.add_argument("--max-evidence-chars", type=int, default=40000, help="Approximate maximum JSON evidence characters per Aider fragment pass.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_docs(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# temporary fingerprint comment
