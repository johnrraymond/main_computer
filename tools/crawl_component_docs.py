#!/usr/bin/env python3
"""Audit, repair, and plan rebuilds for generated component documentation.

This script is intentionally conservative:

* It does not generate prose documentation.
* It does not call the model unless --ask-model is supplied for planning advice.
* It does not execute rebuilds unless --execute-plan is supplied.
* It repairs only cheap structural issues that should not require regeneration:
  docs-root-relative doc_path values, missing content_type for HTML docs,
  aliases discovered from component markup, schema_version, and safe wrapper
  containers around otherwise safe HTML.

Typical usage from the repository root:

    python tools/crawl_component_docs.py --planner
    python tools/crawl_component_docs.py --verbose

Outputs:

    generated_component_docs/doc-health.json
    generated_component_docs/doc-build.json
    tools/documentation/plan-<unique-plan-id>.py

The generated plan script is the primary handoff artifact. It contains the
current rebuild queue and can be reviewed, edited, committed, or run later:

    python tools/documentation/plan-<unique-plan-id>.py --dry-run
    python tools/documentation/plan-<unique-plan-id>.py --max-rebuilds 1
    python tools/documentation/plan-<unique-plan-id>.py
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
import urllib.error
import urllib.request


SCHEMA_VERSION = 1
OUTPUT_ROOT_DEFAULT = "generated_component_docs"
SAFE_DOC_EXTENSIONS = {".html"}
SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "build",
    "dist",
}
UNSAFE_HTML_PATTERNS = [
    re.compile(r"<\s*script\b", re.IGNORECASE),
    re.compile(r"<\s*iframe\b", re.IGNORECASE),
    re.compile(r"<\s*object\b", re.IGNORECASE),
    re.compile(r"<\s*embed\b", re.IGNORECASE),
    re.compile(r"\son[a-zA-Z]+\s*=", re.IGNORECASE),
    re.compile(r"<\s*link\b[^>]*\brel\s*=\s*['\"]?stylesheet", re.IGNORECASE),
]
DOC_BUILD_ARTIFACT_FILES = {
    "tools/crawl_component_docs.py",
    "generated_component_docs/doc-build.json",
    "generated_component_docs/doc-health.json",
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
SYNTHETIC_WIDGET_TAGS = {
    "aside",
    "button",
    "canvas",
    "div",
    "form",
    "input",
    "label",
    "nav",
    "output",
    "pre",
    "section",
    "select",
    "span",
    "table",
    "textarea",
    "ul",
}
IGNORED_SYNTHETIC_WIDGET_TAGS = {"script", "style", "template", "option"}


def is_doc_build_artifact_source_path(relative: str) -> bool:
    """Return True for generated documentation planning/build outputs.

    Older generated manifest entries may have accidentally captured plan scripts
    or generated HTML as feature sources. Those artifacts should never make a
    component doc look stale merely because a planner or previous docs build ran.
    """
    normalized = normalize_slashes(relative).strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in DOC_BUILD_ARTIFACT_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in DOC_BUILD_ARTIFACT_PREFIXES):
        return True
    return bool(re.fullmatch(r"tools/crawl_component_docs(?:-\d+)?\.py", normalized))


def clean_doc_source_files(source_files: Any) -> Tuple[List[str], List[str]]:
    """Split manifest source_files into real feature sources and doc-build artifacts."""
    if not isinstance(source_files, list):
        return [], []
    cleaned: List[str] = []
    skipped: List[str] = []
    seen: Set[str] = set()
    for raw in source_files:
        rel = normalize_slashes(str(raw).strip())
        if not rel:
            continue
        if is_doc_build_artifact_source_path(rel):
            skipped.append(rel)
            continue
        if rel not in seen:
            cleaned.append(rel)
            seen.add(rel)
    return cleaned, skipped


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_write_text(path: Path, text: str, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def safe_write_json(path: Path, payload: Any, *, dry_run: bool = False) -> None:
    safe_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", dry_run=dry_run)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def posix_rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def humanize_id(value: str) -> str:
    text = re.sub(r"[_\-.]+", " ", value or "").strip()
    return re.sub(r"\s+", " ", text).title() or "Generated Component Documentation"


def safe_target_filename(value: str) -> str:
    text = (value or "target").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text)
    text = text.strip(".-") or "target"
    # Keep dots because component ids use them and the frontend expects them.
    return text[:160]


def normalize_slashes(value: str) -> str:
    return str(value).replace("\\", "/")


class UnsafeDocPath(ValueError):
    pass


def normalize_generated_doc_path(raw: str, output_root_name: str = OUTPUT_ROOT_DEFAULT) -> str:
    """Normalize a manifest doc_path to be relative to generated_component_docs.

    Raises UnsafeDocPath for absolute, traversal, or non-html paths.
    """
    if raw is None:
        raise UnsafeDocPath("empty doc_path")
    text = normalize_slashes(str(raw).strip())
    if not text:
        raise UnsafeDocPath("empty doc_path")

    # Strip URI-ish file prefix and leading "./"; reject Windows absolute paths before Path.
    if re.match(r"^[A-Za-z]:/", text) or text.startswith("/") or text.startswith("//"):
        raise UnsafeDocPath(f"absolute doc_path rejected: {raw!r}")

    while text.startswith("./"):
        text = text[2:]

    root_prefix = output_root_name.rstrip("/") + "/"
    if text == output_root_name.rstrip("/"):
        raise UnsafeDocPath("doc_path points at docs root, not a document")
    while text.startswith(root_prefix):
        text = text[len(root_prefix):]

    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafeDocPath("empty normalized doc_path")
    if any(p == ".." for p in parts):
        raise UnsafeDocPath(f"traversal doc_path rejected: {raw!r}")

    normalized = "/".join(parts)
    if Path(normalized).suffix.lower() not in SAFE_DOC_EXTENSIONS:
        raise UnsafeDocPath(f"unsupported generated doc extension: {normalized!r}")
    return normalized


def resolve_doc_path(output_root: Path, normalized_doc_path: str) -> Path:
    path = output_root / normalized_doc_path
    if not is_under(path, output_root):
        raise UnsafeDocPath(f"resolved doc_path escapes output root: {normalized_doc_path}")
    return path


def docs_root_relative_path(path: Path, output_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(output_root.resolve()).as_posix()
    except Exception as exc:
        raise UnsafeDocPath(f"path is outside generated docs root: {path}") from exc
    return normalize_generated_doc_path(rel, output_root.name)


def iter_files(root: Path, patterns: Sequence[str]) -> Iterable[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        base_path = Path(base)
        for name in files:
            p = base_path / name
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
                yield p


def looks_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except Exception:
        return True
    return b"\0" in chunk


def check_html_safety(text: str) -> Dict[str, Any]:
    problems: List[str] = []
    stripped = text.strip()
    if not stripped:
        problems.append("empty")
    for pattern in UNSAFE_HTML_PATTERNS:
        if pattern.search(text):
            problems.append(f"unsafe:{pattern.pattern}")
    has_container = bool(re.search(r"<\s*(article|section)\b", text, re.IGNORECASE))
    if not has_container:
        problems.append("missing_article_or_section")
    return {
        "ok": not problems,
        "problems": problems,
        "repairable": problems == ["missing_article_or_section"],
        "has_container": has_container,
    }


def wrap_safe_html_fragment(text: str, target_id: str, title: str) -> str:
    body = text.strip()
    safe_title = html.escape(title or humanize_id(target_id))
    safe_target = html.escape(target_id or "")
    return (
        f'<article class="mc-component-doc" data-mc-doc-target="{safe_target}">\n'
        f'  <section class="mc-doc-fragment" data-fragment="crawler-wrapper">\n'
        f"    <header><h1>{safe_title}</h1></header>\n"
        f'    <div class="mc-doc-fragment-body">\n'
        f"{body}\n"
        f"    </div>\n"
        f"  </section>\n"
        f"</article>\n"
    )


def infer_synthetic_widget_kind(tag: str, attr: Dict[str, str]) -> str:
    explicit = (attr.get("data-mc-widget-kind") or attr.get("role") or "").strip().lower()
    if explicit:
        return explicit
    tag = tag.lower()
    input_type = (attr.get("type") or "").strip().lower()
    class_text = " ".join(
        value for key, value in attr.items() if key in {"class", "data-mc-widget-class"}
    ).lower()
    if tag == "button" or "button" in class_text or input_type in {"button", "submit", "reset"}:
        return "action"
    if tag in {"input", "select", "textarea"}:
        return "input"
    if tag == "canvas":
        return "canvas"
    if tag in {"pre", "output"} or any(token in class_text for token in ["status", "output", "preview", "result"]):
        return "output"
    if any(token in class_text for token in ["toolbar", "actions"]):
        return "toolbar"
    if tag in {"ul", "ol"} or any(token in class_text for token in ["list", "table", "notebook"]):
        return "list"
    if tag in {"aside", "section", "nav", "form"} or any(token in class_text for token in ["panel", "library", "sidebar", "workspace", "shell"]):
        return "panel"
    return "widget"


def should_synthesize_widget_component(tag: str, attr: Dict[str, str], dom_id: str, widget_id: str, doc_id: str) -> bool:
    tag = tag.lower()
    if tag in IGNORED_SYNTHETIC_WIDGET_TAGS:
        return False
    if tag not in SYNTHETIC_WIDGET_TAGS and not widget_id and not doc_id:
        return False
    return bool(dom_id or widget_id or doc_id)



class ComponentScanner(HTMLParser):
    """Small HTML component/alias scanner.

    It is not a full browser DOM, but it tracks open component ancestors so child
    inputs/buttons with DOM ids can become aliases of a parent component.
    """

    def __init__(self, source_path: str):
        super().__init__(convert_charrefs=True)
        self.source_path = source_path
        self.stack: List[Dict[str, Any]] = []
        self.components: Dict[str, Dict[str, Any]] = {}
        self.alias_to_component: Dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attr = {k.lower(): (v if v is not None else "") for k, v in attrs}
        comp_id = attr.get("data-mc-component-id", "").strip()
        widget_id = attr.get("data-mc-widget-id", "").strip()
        doc_id = attr.get("data-mc-doc-id", "").strip()
        dom_id = attr.get("id", "").strip()
        feature_id = attr.get("data-mc-feature-id", "").strip()
        owner = attr.get("data-mc-component-owner", "").strip()
        kind = attr.get("data-mc-component-kind", "").strip()
        label = (
            attr.get("data-mc-component-label", "").strip()
            or attr.get("data-mc-widget-label", "").strip()
            or attr.get("data-widget-label", "").strip()
            or attr.get("aria-label", "").strip()
        )

        parent_component = next(
            (item.get("component_id") for item in reversed(self.stack) if item.get("component_id")),
            "",
        )

        if comp_id:
            entry = self.components.setdefault(
                comp_id,
                {
                    "id": comp_id,
                    "aliases": set(),
                    "label": label or humanize_id(comp_id),
                    "kind": kind,
                    "owner": owner or parent_component,
                    "feature_id": feature_id,
                    "source_files": set(),
                    "dom_ids": set(),
                    "widget_ids": set(),
                },
            )
            entry["source_files"].add(self.source_path)
            if label and not entry.get("label"):
                entry["label"] = label
            if kind and not entry.get("kind"):
                entry["kind"] = kind
            if feature_id and not entry.get("feature_id"):
                entry["feature_id"] = feature_id
            if owner and not entry.get("owner"):
                entry["owner"] = owner
            for alias in (dom_id, widget_id, doc_id):
                if alias and alias != comp_id:
                    entry["aliases"].add(alias)
                    self.alias_to_component.setdefault(alias, comp_id)
            if dom_id:
                entry["dom_ids"].add(dom_id)
            if widget_id:
                entry["widget_ids"].add(widget_id)

        elif parent_component:
            # Child controls under a component become aliases of their owner. This
            # handles input#aider-dry-run inside span[data-mc-component-id].
            parent = self.components.setdefault(
                parent_component,
                {
                    "id": parent_component,
                    "aliases": set(),
                    "label": humanize_id(parent_component),
                    "kind": "",
                    "owner": "",
                    "feature_id": "",
                    "source_files": set(),
                    "dom_ids": set(),
                    "widget_ids": set(),
                },
            )
            parent["source_files"].add(self.source_path)
            for alias in (dom_id, widget_id, doc_id):
                if alias and alias != parent_component:
                    parent["aliases"].add(alias)
                    self.alias_to_component.setdefault(alias, parent_component)
            if dom_id:
                parent["dom_ids"].add(dom_id)
            if widget_id:
                parent["widget_ids"].add(widget_id)

        elif should_synthesize_widget_component(tag, attr, dom_id, widget_id, doc_id):
            # Some application surfaces predate the data-mc-component-id schema but
            # still expose stable DOM ids. Treat those ids as documentable widget
            # targets so the documentation viewport and rebuild planner can see
            # controls in apps such as Document, Spreadsheet, Terminal, and Task Manager.
            synthetic_id = doc_id or widget_id or dom_id
            entry = self.components.setdefault(
                synthetic_id,
                {
                    "id": synthetic_id,
                    "aliases": set(),
                    "label": label or attr.get("data-widget-label", "").strip() or humanize_id(synthetic_id),
                    "kind": kind or infer_synthetic_widget_kind(tag, attr),
                    "owner": owner,
                    "feature_id": feature_id,
                    "source_files": set(),
                    "dom_ids": set(),
                    "widget_ids": set(),
                },
            )
            entry["source_files"].add(self.source_path)
            if label and not entry.get("label"):
                entry["label"] = label
            if feature_id and not entry.get("feature_id"):
                entry["feature_id"] = feature_id
            for alias in (dom_id, widget_id, doc_id):
                if alias and alias != synthetic_id:
                    entry["aliases"].add(alias)
                    self.alias_to_component.setdefault(alias, synthetic_id)
            if dom_id:
                entry["dom_ids"].add(dom_id)
            if widget_id:
                entry["widget_ids"].add(widget_id)

        self.stack.append({"tag": tag, "component_id": comp_id or parent_component})

    def handle_endtag(self, tag: str) -> None:
        # Pop up to the matching tag where possible.
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx].get("tag") == tag:
                del self.stack[idx:]
                return
        if self.stack:
            self.stack.pop()


def scan_components(repo_root: Path) -> Dict[str, Any]:
    components: Dict[str, Dict[str, Any]] = {}
    alias_to_component: Dict[str, str] = {}

    html_roots = [
        repo_root / "main_computer" / "web" / "applications" / "apps",
        repo_root / "main_computer" / "web",
    ]
    html_files: List[Path] = []
    for html_root in html_roots:
        if html_root.exists():
            for p in iter_files(html_root, ["*.html"]):
                if p not in html_files:
                    html_files.append(p)

    for path in html_files:
        if looks_binary(path):
            continue
        rel = path.relative_to(repo_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        scanner = ComponentScanner(rel)
        try:
            scanner.feed(text)
        except Exception:
            # Keep partial scanner results if any.
            pass

        for comp_id, entry in scanner.components.items():
            target = components.setdefault(
                comp_id,
                {
                    "id": comp_id,
                    "aliases": set(),
                    "label": entry.get("label") or humanize_id(comp_id),
                    "kind": entry.get("kind") or "",
                    "owner": entry.get("owner") or "",
                    "feature_id": entry.get("feature_id") or "",
                    "source_files": set(),
                    "dom_ids": set(),
                    "widget_ids": set(),
                },
            )
            for key in ("aliases", "source_files", "dom_ids", "widget_ids"):
                target[key].update(entry.get(key, set()))
            for key in ("label", "kind", "owner", "feature_id"):
                if entry.get(key) and not target.get(key):
                    target[key] = entry[key]

        for alias, comp_id in scanner.alias_to_component.items():
            alias_to_component.setdefault(alias, comp_id)

    # Convert sets to sorted lists for JSON.
    clean_components = {}
    for comp_id, entry in components.items():
        clean = dict(entry)
        for key in ("aliases", "source_files", "dom_ids", "widget_ids"):
            clean[key] = sorted(str(v) for v in entry.get(key, set()) if v)
        clean_components[comp_id] = clean

    return {
        "components": clean_components,
        "alias_to_component": alias_to_component,
    }


def load_manifest(output_root: Path) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    manifest_path = output_root / "manifest.json"
    raw = load_json(manifest_path, {"schema_version": SCHEMA_VERSION, "entries": []})
    if not isinstance(raw, dict):
        warnings.append("manifest was not an object; using empty manifest")
        raw = {"schema_version": SCHEMA_VERSION, "entries": []}
    entries = raw.get("entries")
    if isinstance(entries, dict):
        raw["entries"] = list(entries.values())
        warnings.append("manifest entries was a mapping; normalized in memory")
    elif not isinstance(entries, list):
        raw["entries"] = []
        warnings.append("manifest entries was missing or not a list")
    return raw, warnings


def entry_by_id(entries: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            out[str(entry["id"])] = entry
    return out


def scan_generated_doc_files(output_root: Path) -> Dict[str, Dict[str, Any]]:
    docs: Dict[str, Dict[str, Any]] = {}
    for sub in ("nodes", "features"):
        base = output_root / sub
        if not base.exists():
            continue
        for path in iter_files(base, ["*.html"]):
            try:
                rel = docs_root_relative_path(path, output_root)
                text = path.read_text(encoding="utf-8", errors="replace")
                st = path.stat()
                safety = check_html_safety(text)
                docs[rel] = {
                    "path": rel,
                    "physical_path": str(path),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "sha256": sha256_file(path),
                    "safety": safety,
                }
            except Exception as exc:
                docs[str(path)] = {"path": str(path), "error": str(exc)}
    return docs


def source_info(repo_root: Path, source_files: Sequence[str]) -> Dict[str, Any]:
    infos = []
    max_mtime = 0.0
    missing = []
    skipped_artifacts = []
    h = hashlib.sha256()
    for raw in source_files:
        if not raw:
            continue
        rel = normalize_slashes(str(raw)).strip()
        if is_doc_build_artifact_source_path(rel):
            skipped_artifacts.append(rel)
            continue
        if rel.startswith("/") or re.match(r"^[A-Za-z]:/", rel) or ".." in Path(rel).parts:
            missing.append({"path": rel, "reason": "unsafe_or_absolute"})
            continue
        path = repo_root / rel
        if not path.exists() or not path.is_file():
            missing.append({"path": rel, "reason": "missing"})
            continue
        try:
            digest = sha256_file(path)
            stat = path.stat()
            max_mtime = max(max_mtime, stat.st_mtime)
            infos.append({"path": rel, "size": stat.st_size, "mtime": stat.st_mtime, "sha256": digest})
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(digest.encode("ascii"))
            h.update(b"\0")
        except Exception as exc:
            missing.append({"path": rel, "reason": str(exc)})
    return {
        "files": infos,
        "missing": missing,
        "skipped_artifacts": sorted(set(skipped_artifacts)),
        "max_mtime": max_mtime,
        "fingerprint": h.hexdigest() if infos else "",
    }


def make_rebuild_command(
    *,
    target_id: str,
    feature_description: str,
    entry: Dict[str, Any],
    force_full_rebuild: bool = False,
) -> Dict[str, Any]:
    argv = [
        sys.executable or "python",
        "tools/rebuild_feature_docs.py",
        "--id",
        target_id,
        "--feature",
        feature_description,
        "--keep-work",
        "--verbose",
    ]

    model = entry.get("model") or ""
    if not model and entry.get("resolved_model"):
        model = f"ollama_chat/{entry['resolved_model']}"
    if model:
        argv.extend(["--model", str(model)])
    if entry.get("scope"):
        argv.extend(["--scope", str(entry["scope"])])
    if force_full_rebuild:
        argv.append("--force-full-rebuild")

    return {"argv": argv, "display": " ".join(quote_arg(a) for a in argv)}



def plan_id_from_queue(plan: Dict[str, Any], *, override: str = "") -> str:
    """Return a short unique id for a generated Python rebuild plan."""
    if override:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", override.strip()).strip(".-")
        if slug:
            return slug[:80]
    queue = plan.get("queue") or []
    payload = json.dumps(
        [
            {
                "id": item.get("id"),
                "reasons": item.get("reasons", []),
                "argv": item.get("argv", []),
            }
            for item in queue
        ],
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{digest}"


def normalize_plan_script_path(repo_root: Path, args: argparse.Namespace, plan: Dict[str, Any]) -> Path:
    """Resolve the path for tools/documentation/plan-<id>.py safely."""
    if getattr(args, "plan_script", ""):
        path = Path(args.plan_script)
        if not path.is_absolute():
            path = repo_root / path
    else:
        plan_dir = Path(getattr(args, "plan_dir", "") or "tools/documentation")
        if not plan_dir.is_absolute():
            plan_dir = repo_root / plan_dir
        plan_id = plan_id_from_queue(plan, override=getattr(args, "plan_id", "") or "")
        path = plan_dir / f"plan-{plan_id}.py"

    if not is_under(path, repo_root):
        raise UnsafeDocPath(f"plan script path escapes repo root: {path}")
    if path.suffix != ".py":
        raise UnsafeDocPath(f"plan script must be a .py file: {path}")
    return path


def command_args_for_plan_script(argv: Sequence[Any]) -> List[str]:
    """Drop the Python executable from a queued argv so the plan script uses its own interpreter."""
    values = [str(v) for v in argv if str(v)]
    if values and re.search(r"(^|[/\\])python(\.exe)?$", values[0], re.IGNORECASE):
        return values[1:]
    return values


def build_plan_script_text(plan: Dict[str, Any], *, repo_root: Path, plan_path: Path) -> str:
    """Create a standalone, resumable Python plan script.

    The generated plan is intentionally make-like:
    - it has its own state file beside the plan script;
    - a successful target is marked complete and skipped on later runs;
    - an interrupted target is not marked complete, so the next run retries it;
    - rebuild_feature_docs.py remains responsible for resuming the target's own work dir.
    """
    queue = []
    for item in plan.get("queue") or []:
        queue.append({
            "id": item.get("id"),
            "action": item.get("action", "rebuild"),
            "priority": item.get("priority", 100),
            "reasons": item.get("reasons", []),
            "args": command_args_for_plan_script(item.get("argv") or []),
            "display": item.get("command", ""),
        })

    generated_at = plan.get("generated_at") or utc_now()
    summary_json = json.dumps(plan.get("summary", {}), indent=2, sort_keys=True)
    queue_json = json.dumps(queue, indent=2, sort_keys=True)
    rel_plan = plan_path.resolve().relative_to(repo_root.resolve()).as_posix()

    lines = [
        "#!/usr/bin/env python3",
        "\"\"\"Generated component documentation rebuild plan.",
        "",
        f"Generated by tools/crawl_component_docs.py at {generated_at}.",
        "",
        "This is a make-like, resumable Python plan. It runs queued",
        "tools/rebuild_feature_docs.py commands and records successful targets in",
        "a state file next to this plan. Kill it and rerun it later; completed",
        "targets are skipped and the interrupted target is retried. The underlying",
        "rebuild_feature_docs.py script is still responsible for resuming its own",
        "fragment/work-directory state.",
        "",
        f"Plan file: {rel_plan}",
        "\"\"\"",
        "",
        "from __future__ import annotations",
        "",
        "import argparse",
        "import datetime as _dt",
        "import json",
        "from pathlib import Path",
        "import subprocess",
        "import sys",
        "import time",
        "",
        f"SUMMARY = {summary_json}",
        "",
        f"REBUILD_QUEUE = {queue_json}",
        "",
        "",
        "def utc_now() -> str:",
        "    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')",
        "",
        "",
        "def repo_root() -> Path:",
        "    here = Path(__file__).resolve()",
        "    for candidate in [here.parents[2], here.parents[1], here.parent, Path.cwd()]:",
        "        if (candidate / 'tools' / 'rebuild_feature_docs.py').exists():",
        "            return candidate",
        "    return Path.cwd()",
        "",
        "",
        "def default_state_path() -> Path:",
        "    return Path(__file__).resolve().with_suffix('.state.json')",
        "",
        "",
        "def load_state(path: Path) -> dict:",
        "    if not path.exists():",
        "        return {'schema_version': 1, 'created_at': utc_now(), 'completed': {}, 'running': None, 'history': []}",
        "    try:",
        "        data = json.loads(path.read_text(encoding='utf-8'))",
        "        if not isinstance(data, dict):",
        "            raise ValueError('state root is not an object')",
        "        data.setdefault('schema_version', 1)",
        "        data.setdefault('created_at', utc_now())",
        "        data.setdefault('completed', {})",
        "        data.setdefault('running', None)",
        "        data.setdefault('history', [])",
        "        return data",
        "    except Exception as exc:",
        "        backup = path.with_suffix(path.suffix + '.corrupt')",
        "        try:",
        "            path.replace(backup)",
        "        except Exception:",
        "            pass",
        "        return {'schema_version': 1, 'created_at': utc_now(), 'completed': {}, 'running': None, 'history': [{'event': 'state_corrupt', 'error': str(exc), 'at': utc_now()}]}",
        "",
        "",
        "def save_state(path: Path, state: dict) -> None:",
        "    path.parent.mkdir(parents=True, exist_ok=True)",
        "    state['updated_at'] = utc_now()",
        "    temp = path.with_name('.' + path.name + '.tmp')",
        "    temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')",
        "    temp.replace(path)",
        "",
        "",
        "def quote_arg(value: str) -> str:",
        "    if not value:",
        "        return '\"\"'",
        "    if any(ch.isspace() for ch in value) or any(ch in value for ch in ['\\\"', \"'\", '`']):",
        "        return '\"' + value.replace('\\\"', '\\\\\"') + '\"'",
        "    return value",
        "",
        "",
        "def command_for_item(item: dict, python_executable: str) -> list[str]:",
        "    args = [str(part) for part in item.get('args', [])]",
        "    if not args:",
        "        return []",
        "    first = args[0].replace('\\\\', '/')",
        "    if first.endswith('rebuild_feature_docs.py'):",
        "        return [python_executable] + args",
        "    if first.endswith('python') or first.endswith('python.exe'):",
        "        return [python_executable] + args[1:]",
        "    return [python_executable] + args",
        "",
        "",
        "def item_key(item: dict) -> str:",
        "    return str(item.get('id') or item.get('display') or item.get('args') or '')",
        "",
        "",
        "def main(argv: list[str] | None = None) -> int:",
        "    parser = argparse.ArgumentParser(description='Run a generated, resumable component documentation rebuild plan.')",
        "    parser.add_argument('--dry-run', action='store_true', help='Print commands without running them.')",
        "    parser.add_argument('--max-rebuilds', type=int, default=0, help='Limit commands run this invocation; 0 means no limit.')",
        "    parser.add_argument('--target', default='', help='Run only one target id or prefix.')",
        "    parser.add_argument('--continue-on-error', action='store_true', help='Continue after a rebuild command fails.')",
        "    parser.add_argument('--python-executable', default=sys.executable, help='Python executable to use.')",
        "    parser.add_argument('--state-path', default='', help='Override the resumable state file path.')",
        "    parser.add_argument('--force', action='store_true', help='Run targets even if marked complete in the plan state.')",
        "    parser.add_argument('--reset-state', action='store_true', help='Delete the plan state before running.')",
        "    parser.add_argument('--json', action='store_true', help='Print JSON summary at the end.')",
        "    args = parser.parse_args(argv)",
        "",
        "    root = repo_root()",
        "    state_path = Path(args.state_path) if args.state_path else default_state_path()",
        "    if not state_path.is_absolute():",
        "        state_path = root / state_path",
        "    if args.reset_state and state_path.exists() and not args.dry_run:",
        "        state_path.unlink()",
        "    state = load_state(state_path)",
        "",
        "    queue = list(REBUILD_QUEUE)",
        "    if args.target:",
        "        queue = [item for item in queue if str(item.get('id', '')).startswith(args.target)]",
        "",
        "    skipped_completed = []",
        "    if not args.force:",
        "        completed = state.get('completed') or {}",
        "        pending = []",
        "        for item in queue:",
        "            key = item_key(item)",
        "            if key in completed:",
        "                skipped_completed.append(key)",
        "            else:",
        "                pending.append(item)",
        "        queue = pending",
        "",
        "    if args.max_rebuilds and args.max_rebuilds > 0:",
        "        queue = queue[: args.max_rebuilds]",
        "",
        "    if skipped_completed:",
        "        print(f'[component-doc-plan] skipped completed targets: {len(skipped_completed)}')",
        "        for key in skipped_completed:",
        "            print(f'  - {key}')",
        "",
        "    results = []",
        "    if not queue:",
        "        print('[component-doc-plan] no rebuilds queued')",
        "        if args.json:",
        "            print(json.dumps({'ok': True, 'ran': 0, 'skipped_completed': skipped_completed, 'state_path': str(state_path)}, indent=2))",
        "        return 0",
        "",
        "    ok = True",
        "    for index, item in enumerate(queue, 1):",
        "        key = item_key(item)",
        "        command = command_for_item(item, args.python_executable)",
        "        display = ' '.join(quote_arg(part) for part in command)",
        "        print(f'[component-doc-plan] {index}/{len(queue)} {key}')",
        "        print(f'[component-doc-plan] command: {display}')",
        "        if args.dry_run:",
        "            results.append({'id': key, 'dry_run': True, 'command': command})",
        "            continue",
        "",
        "        state['running'] = {'id': key, 'started_at': utc_now(), 'command': command}",
        "        state.setdefault('history', []).append({'event': 'started', 'id': key, 'at': utc_now()})",
        "        save_state(state_path, state)",
        "        started = time.time()",
        "        result = subprocess.run(command, cwd=root)",
        "        elapsed = round(time.time() - started, 3)",
        "        record = {'id': key, 'returncode': result.returncode, 'elapsed_seconds': elapsed, 'command': command, 'finished_at': utc_now()}",
        "        results.append(record)",
        "        state['running'] = None",
        "        if result.returncode == 0:",
        "            state.setdefault('completed', {})[key] = record",
        "            state.setdefault('history', []).append({'event': 'completed', 'id': key, 'at': utc_now(), 'elapsed_seconds': elapsed})",
        "            save_state(state_path, state)",
        "            continue",
        "",
        "        ok = False",
        "        state.setdefault('history', []).append({'event': 'failed', 'id': key, 'at': utc_now(), 'returncode': result.returncode})",
        "        save_state(state_path, state)",
        "        print(f'[component-doc-plan] failed: {key} exit {result.returncode}')",
        "        if not args.continue_on_error:",
        "            break",
        "",
        "    summary = {'ok': ok, 'ran': len(results), 'results': results, 'state_path': str(state_path), 'skipped_completed': skipped_completed}",
        "    if args.json:",
        "        print(json.dumps(summary, indent=2, sort_keys=True))",
        "    return 0 if ok else 1",
        "",
        "",
        "if __name__ == '__main__':",
        "    raise SystemExit(main())",
        "",
    ]
    return "\n".join(lines)

def write_plan_script(plan: Dict[str, Any], *, repo_root: Path, args: argparse.Namespace, dry_run: bool) -> Optional[Path]:
    """Write tools/documentation/plan-<unique>.py and return the path."""
    if getattr(args, "no_plan_script", False):
        return None
    path = normalize_plan_script_path(repo_root, args, plan)
    text = build_plan_script_text(plan, repo_root=repo_root, plan_path=path)
    safe_write_text(path, text, dry_run=dry_run)
    if not dry_run:
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except Exception:
            pass
    return path


def quote_arg(arg: str) -> str:
    if re.search(r"\s", arg):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def infer_feature_description(entry: Dict[str, Any], component: Optional[Dict[str, Any]] = None) -> str:
    for key in ("feature_description", "title", "label"):
        if entry.get(key):
            return str(entry[key])
    if component:
        for key in ("label", "feature_id", "id"):
            if component.get(key):
                return humanize_id(str(component[key]))
    return humanize_id(str(entry.get("id") or ""))


def normalize_entry(
    entry: Dict[str, Any],
    *,
    output_root: Path,
    repo_root: Path,
    components: Dict[str, Dict[str, Any]],
    alias_to_component: Dict[str, str],
    doc_files: Dict[str, Dict[str, Any]],
    repair: bool,
    dry_run: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str], List[str]]:
    """Normalize one manifest entry in-place if repair is True.

    Returns (entry, repairs, warnings, errors).
    """
    repairs: List[Dict[str, Any]] = []
    warnings: List[str] = []
    errors: List[str] = []
    target_id = str(entry.get("id") or "").strip()
    if not target_id:
        errors.append("manifest entry missing id")
        return entry, repairs, warnings, errors

    # Build alias set from current entry and discovered code anchors.
    aliases: Set[str] = set()
    existing_aliases = entry.get("aliases", [])
    if isinstance(existing_aliases, str):
        existing_aliases = [existing_aliases]
    if isinstance(existing_aliases, list):
        aliases.update(str(a).strip() for a in existing_aliases if str(a).strip())

    component_id = target_id if target_id in components else alias_to_component.get(target_id, "")
    component = components.get(component_id) if component_id else None
    if component:
        if component_id != target_id:
            aliases.add(component_id)
        for a in component.get("aliases", []):
            if a and a != target_id:
                aliases.add(str(a))
        for a in component.get("dom_ids", []):
            if a and a != target_id:
                aliases.add(str(a))
        for a in component.get("widget_ids", []):
            if a and a != target_id:
                aliases.add(str(a))
        if not entry.get("title") and component.get("label"):
            before = entry.get("title")
            entry["title"] = component["label"]
            repairs.append({"target_id": target_id, "kind": "title_fill", "before": before, "after": entry["title"], "applied": repair and not dry_run})
        if not entry.get("feature_id") and component.get("feature_id"):
            entry["feature_id"] = component["feature_id"]

    aliases = {a for a in aliases if a and a != target_id}
    if sorted(aliases) != sorted(entry.get("aliases", []) if isinstance(entry.get("aliases"), list) else []):
        before = entry.get("aliases")
        after = sorted(aliases)
        repairs.append({"target_id": target_id, "kind": "aliases", "before": before, "after": after, "applied": repair and not dry_run})
        if repair and not dry_run:
            entry["aliases"] = after

    cleaned_sources, skipped_artifacts = clean_doc_source_files(entry.get("source_files", []))
    if skipped_artifacts:
        repairs.append({
            "target_id": target_id,
            "kind": "source_files_doc_build_artifacts",
            "before": entry.get("source_files", []),
            "after": cleaned_sources,
            "removed": skipped_artifacts,
            "applied": repair and not dry_run,
        })
        if repair and not dry_run:
            entry["source_files"] = cleaned_sources

    # content_type for HTML docs.
    doc_path_raw = str(entry.get("doc_path") or "")
    if (not entry.get("content_type")) and (doc_path_raw.endswith(".html") or not doc_path_raw):
        repairs.append({"target_id": target_id, "kind": "content_type", "before": entry.get("content_type"), "after": "text/html", "applied": repair and not dry_run})
        if repair and not dry_run:
            entry["content_type"] = "text/html"

    # doc_path normalization.
    normalized_doc_path = ""
    if doc_path_raw:
        try:
            normalized_doc_path = normalize_generated_doc_path(doc_path_raw, output_root.name)
            if normalized_doc_path != doc_path_raw.replace("\\", "/").lstrip("./"):
                repairs.append({"target_id": target_id, "kind": "manifest_doc_path", "before": doc_path_raw, "after": normalized_doc_path, "applied": repair and not dry_run})
                if repair and not dry_run:
                    entry["doc_path"] = normalized_doc_path
        except UnsafeDocPath as exc:
            errors.append(str(exc))
    else:
        candidate = f"nodes/{safe_target_filename(target_id)}.html"
        if candidate in doc_files:
            repairs.append({"target_id": target_id, "kind": "manifest_doc_path_fill", "before": "", "after": candidate, "applied": repair and not dry_run})
            normalized_doc_path = candidate
            if repair and not dry_run:
                entry["doc_path"] = candidate
        else:
            warnings.append("missing doc_path")

    if "content_type" not in entry and normalized_doc_path.endswith(".html"):
        if repair and not dry_run:
            entry["content_type"] = "text/html"

    return entry, repairs, warnings, errors


def audit_and_plan(args: argparse.Namespace) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output_root = (repo_root / args.output_root).resolve()
    if output_root.exists() or not args.dry_run:
        output_root.mkdir(parents=True, exist_ok=True)

    manifest_path = output_root / "manifest.json"
    plan_path = Path(args.plan) if args.plan else output_root / "doc-build.json"
    health_path = Path(args.health) if args.health else output_root / "doc-health.json"
    if not plan_path.is_absolute():
        plan_path = repo_root / plan_path
    if not health_path.is_absolute():
        health_path = repo_root / health_path

    manifest, load_warnings = load_manifest(output_root)
    original_manifest_text = json.dumps(manifest, indent=2, sort_keys=True)

    component_scan = scan_components(repo_root)
    components = component_scan["components"]
    alias_to_component = component_scan["alias_to_component"]
    doc_files = scan_generated_doc_files(output_root)

    target_filter = args.target.strip()

    health: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "warnings": list(load_warnings),
        "errors": [],
        "orphaned_docs": [],
        "unsafe_docs": [],
        "missing_docs": [],
        "stale_docs": [],
        "repaired_docs": [],
        "current_docs": [],
        "blocked_docs": [],
        "components_scanned": len(components),
    }
    plan: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": health["generated_at"],
        "repo_root": str(repo_root),
        "output_root": args.output_root,
        "mode": {
            "repair": bool(args.repair and not args.no_repair),
            "execute_plan": bool(args.execute_plan or getattr(args, "run_new_doc_plan", False)),
            "force_full_rebuild": bool(args.force_full_rebuild),
            "dry_run": bool(args.dry_run),
        },
        "summary": {},
        "repairs": [],
        "targets": [],
        "queue": [],
    }

    repair = bool(args.repair and not args.no_repair)
    entries = manifest.get("entries", [])
    if not isinstance(entries, list):
        entries = []
        manifest["entries"] = entries

    if manifest.get("schema_version") != SCHEMA_VERSION:
        plan["repairs"].append({
            "target_id": None,
            "kind": "schema_version",
            "before": manifest.get("schema_version"),
            "after": SCHEMA_VERSION,
            "applied": repair and not args.dry_run,
        })
        if repair and not args.dry_run:
            manifest["schema_version"] = SCHEMA_VERSION

    used_doc_paths: Set[str] = set()
    seen_ids: Set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        target_id = str(entry.get("id") or "").strip()
        if not target_id:
            continue
        if target_filter and not (target_id == target_filter or target_id.startswith(target_filter) or fnmatch.fnmatch(target_id, target_filter)):
            continue
        if target_id in seen_ids:
            health["warnings"].append(f"duplicate manifest id: {target_id}")
        seen_ids.add(target_id)

        entry, repairs, warnings, errors = normalize_entry(
            entry,
            output_root=output_root,
            repo_root=repo_root,
            components=components,
            alias_to_component=alias_to_component,
            doc_files=doc_files,
            repair=repair,
            dry_run=args.dry_run,
        )
        plan["repairs"].extend(repairs)
        for r in repairs:
            if r.get("applied"):
                health["repaired_docs"].append(r)
        for w in warnings:
            health["warnings"].append(f"{target_id}: {w}")
        for e in errors:
            health["errors"].append(f"{target_id}: {e}")

        status = "current"
        reasons: List[str] = []
        action = "none"
        blocked = False
        doc_path = str(entry.get("doc_path") or "")
        normalized_doc_path = ""
        doc_file_info: Optional[Dict[str, Any]] = None
        physical_doc: Optional[Path] = None

        try:
            normalized_doc_path = normalize_generated_doc_path(doc_path, output_root.name)
            used_doc_paths.add(normalized_doc_path)
            physical_doc = resolve_doc_path(output_root, normalized_doc_path)
        except Exception as exc:
            status = "blocked"
            blocked = True
            reasons.append(f"unsafe_doc_path:{exc}")

        if not blocked:
            if not physical_doc or not physical_doc.exists():
                status = "missing"
                action = "rebuild"
                reasons.append("doc_file_missing")
                health["missing_docs"].append(target_id)
            else:
                doc_file_info = doc_files.get(normalized_doc_path)
                if not doc_file_info:
                    # Not under nodes/features or scan failed.
                    status = "blocked"
                    blocked = True
                    reasons.append("doc_file_not_scanned")
                else:
                    safety = doc_file_info.get("safety", {})
                    if not safety.get("ok"):
                        problems = safety.get("problems", [])
                        if safety.get("repairable") and repair:
                            try:
                                raw = physical_doc.read_text(encoding="utf-8", errors="replace")
                                fixed = wrap_safe_html_fragment(raw, target_id, str(entry.get("title") or infer_feature_description(entry, components.get(target_id))))
                                fixed_safety = check_html_safety(fixed)
                                if fixed_safety.get("ok"):
                                    plan["repairs"].append({
                                        "target_id": target_id,
                                        "kind": "html_wrapper",
                                        "before": problems,
                                        "after": "wrapped in article/section",
                                        "applied": not args.dry_run,
                                    })
                                    if not args.dry_run:
                                        safe_write_text(physical_doc, fixed)
                                    health["repaired_docs"].append({"target_id": target_id, "kind": "html_wrapper"})
                                else:
                                    status = "blocked"
                                    blocked = True
                                    reasons.append("unsafe_html_after_wrapper")
                            except Exception as exc:
                                status = "blocked"
                                blocked = True
                                reasons.append(f"html_wrapper_failed:{exc}")
                        else:
                            status = "blocked"
                            blocked = True
                            reasons.append("unsafe_html:" + ",".join(problems))
                            health["unsafe_docs"].append({"id": target_id, "doc_path": normalized_doc_path, "problems": problems})

                    # Freshness check: make-like mtime and missing sources.
                    if not blocked:
                        src = source_info(repo_root, entry.get("source_files") or [])
                        source_max_mtime = src["max_mtime"]
                        doc_mtime = physical_doc.stat().st_mtime
                        if src["missing"]:
                            health["warnings"].append(f"{target_id}: missing source files: {src['missing']}")
                        if args.force_full_rebuild:
                            status = "stale"
                            action = "rebuild"
                            reasons.append("force_full_rebuild")
                        elif source_max_mtime and source_max_mtime > doc_mtime + 0.0001:
                            status = "stale"
                            action = "rebuild"
                            reasons.append("source_newer_than_doc")

        feature_description = infer_feature_description(entry, components.get(str(entry.get("id", ""))))
        if blocked:
            action = "blocked"
            health["blocked_docs"].append({"id": target_id, "reasons": reasons})
        elif action == "rebuild":
            command = make_rebuild_command(
                target_id=target_id,
                feature_description=feature_description,
                entry=entry,
                force_full_rebuild=args.force_full_rebuild,
            )
            plan["queue"].append({
                "id": target_id,
                "action": "rebuild",
                "priority": 100,
                "reasons": reasons,
                "command": command["display"],
                "argv": command["argv"],
            })
            if status == "stale":
                health["stale_docs"].append(target_id)
        else:
            health["current_docs"].append(target_id)

        plan["targets"].append({
            "id": target_id,
            "aliases": entry.get("aliases", []),
            "feature_description": feature_description,
            "doc_path": entry.get("doc_path"),
            "content_type": entry.get("content_type"),
            "status": status,
            "reasons": reasons,
            "source_files": entry.get("source_files") or [],
            "doc_mtime": physical_doc.stat().st_mtime if physical_doc and physical_doc.exists() else 0,
            "action": action,
            "rebuild_command": plan["queue"][-1]["command"] if action == "rebuild" and plan["queue"] else None,
        })

    # Orphan node/feature docs.
    for rel in sorted(doc_files):
        if rel not in used_doc_paths:
            health["orphaned_docs"].append(rel)

    # Codebase scan: queue discovered documentable components that do not have docs.
    if args.include_undocumented and not getattr(args, "skip_undocumented", False):
        manifest_ids = {str(e.get("id")) for e in entries if isinstance(e, dict) and e.get("id")}
        manifest_aliases = {
            str(alias)
            for e in entries if isinstance(e, dict)
            for alias in (e.get("aliases") or [])
            if alias
        }
        for comp_id, comp in sorted(components.items()):
            if target_filter and not (comp_id == target_filter or comp_id.startswith(target_filter) or fnmatch.fnmatch(comp_id, target_filter)):
                continue
            if comp_id in manifest_ids or comp_id in manifest_aliases:
                continue
            feature_description = comp.get("label") or humanize_id(comp_id)
            entry_stub = {"id": comp_id, "scope": "component"}
            command = make_rebuild_command(
                target_id=comp_id,
                feature_description=feature_description,
                entry=entry_stub,
                force_full_rebuild=args.force_full_rebuild,
            )
            health["missing_docs"].append(comp_id)
            plan["targets"].append({
                "id": comp_id,
                "aliases": comp.get("aliases", []),
                "feature_description": feature_description,
                "doc_path": f"nodes/{safe_target_filename(comp_id)}.html",
                "content_type": "text/html",
                "status": "missing",
                "reasons": ["undocumented_component"],
                "source_files": comp.get("source_files", []),
                "action": "rebuild",
                "rebuild_command": command["display"],
            })
            plan["queue"].append({
                "id": comp_id,
                "action": "rebuild",
                "priority": 200,
                "reasons": ["undocumented_component"],
                "command": command["display"],
                "argv": command["argv"],
            })

    # Summary.
    summary = {
        "total_manifest_entries": len(entries),
        "total_docs_scanned": len(doc_files),
        "current": len(health["current_docs"]),
        "repaired": len(health["repaired_docs"]),
        "stale": len(health["stale_docs"]),
        "missing": len(health["missing_docs"]),
        "blocked": len(health["blocked_docs"]),
        "orphaned": len(health["orphaned_docs"]),
        "queued_rebuilds": len(plan["queue"]),
        "warnings": len(health["warnings"]),
        "errors": len(health["errors"]),
    }
    plan["summary"] = summary
    health["summary"] = summary

    if args.ask_model:
        advice = ask_local_model_for_plan(args, health, plan)
        plan["model_advice"] = advice
        health["model_advice"] = advice

    # Write repaired manifest if changed.
    final_manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    manifest_changed = final_manifest_text.strip() != original_manifest_text.strip()
    if manifest_changed:
        if args.no_repair or not repair:
            health["warnings"].append("manifest changes available but repair disabled")
        elif args.dry_run:
            health["warnings"].append("manifest changes available but dry-run enabled")
        else:
            safe_write_text(manifest_path, final_manifest_text)

    plan_script_path: Optional[Path] = None
    try:
        plan_script_path = write_plan_script(plan, repo_root=repo_root, args=args, dry_run=args.dry_run)
        if plan_script_path:
            plan["plan_script"] = plan_script_path.resolve().relative_to(repo_root).as_posix()
            health["plan_script"] = plan["plan_script"]
    except Exception as exc:
        health["errors"].append(f"plan_script:{exc}")
        plan["plan_script_error"] = str(exc)

    summary["errors"] = len(health["errors"])
    plan["summary"] = summary
    health["summary"] = summary

    safe_write_json(health_path, health, dry_run=args.dry_run)
    safe_write_json(plan_path, plan, dry_run=args.dry_run)

    if (args.execute_plan or getattr(args, "run_new_doc_plan", False)) and not args.dry_run:
        if plan_script_path and not getattr(args, "no_plan_script", False):
            command = [sys.executable, str(plan_script_path)]
            if args.max_rebuilds and args.max_rebuilds > 0:
                command.extend(["--max-rebuilds", str(args.max_rebuilds)])
            if args.verbose:
                print(f"[crawl-component-docs] executing generated plan script: {' '.join(quote_arg(part) for part in command)}")
            result = subprocess.run(command, cwd=repo_root)
            if result.returncode != 0:
                raise SystemExit(f"plan script failed: exit {result.returncode}")
        else:
            execute_plan(plan, repo_root, args)

    return {
        "health": health,
        "plan": plan,
        "manifest_changed": manifest_changed,
        "health_path": str(health_path),
        "plan_path": str(plan_path),
        "plan_script_path": str(plan_script_path) if plan_script_path else "",
    }


def ask_local_model_for_plan(args: argparse.Namespace, health: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Ask Ollama for planning advice. This is advisory only."""
    prompt = {
        "task": "Review this generated component documentation health report and build plan. Suggest priorities and any risks. Do not invent source facts.",
        "health_summary": health.get("summary", {}),
        "warnings": health.get("warnings", [])[:20],
        "errors": health.get("errors", [])[:20],
        "queue": plan.get("queue", [])[:20],
    }
    model = args.ollama_model
    url = args.ollama_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": "You are a local documentation build planner. Be concise and only advise; do not generate docs."},
            {"role": "user", "content": json.dumps(prompt, indent=2)},
        ],
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=args.ollama_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return {
            "ok": True,
            "model": model,
            "content": data.get("message", {}).get("content") or data.get("response") or "",
        }
    except Exception as exc:
        return {"ok": False, "model": model, "error": str(exc)}




def build_planner_audit_args(args: argparse.Namespace) -> argparse.Namespace:
    """Return a side-effect-minimized argument set for planner inspection."""
    audit_args = argparse.Namespace(**vars(args))
    audit_args.dry_run = True
    audit_args.no_repair = True
    audit_args.repair = False
    audit_args.execute_plan = False
    audit_args.run_new_doc_plan = False
    audit_args.no_plan_script = True
    return audit_args


def build_planner_real_command(args: argparse.Namespace, plan: Dict[str, Any]) -> List[str]:
    """Build the recommended real crawler invocation for the inspected state."""
    command = ["python", "tools/crawl_component_docs.py", "--verbose"]

    if args.repo_root != ".":
        command.extend(["--repo-root", str(args.repo_root)])
    if args.output_root != OUTPUT_ROOT_DEFAULT:
        command.extend(["--output-root", str(args.output_root)])
    if args.plan:
        command.extend(["--plan", str(args.plan)])
    if args.health:
        command.extend(["--health", str(args.health)])
    if args.target:
        command.extend(["--target", str(args.target)])
    if args.skip_undocumented:
        command.append("--skip-undocumented")
    if args.no_repair:
        command.append("--no-repair")
    if args.force_full_rebuild:
        command.append("--force-full-rebuild")
    if args.plan_dir != "tools/documentation":
        command.extend(["--plan-dir", str(args.plan_dir)])
    if args.plan_id:
        command.extend(["--plan-id", str(args.plan_id)])
    if args.plan_script:
        command.extend(["--plan-script", str(args.plan_script)])
    if args.no_plan_script:
        command.append("--no-plan-script")
    if args.ask_model:
        command.append("--ask-model")
    if args.ollama_url != "http://127.0.0.1:11434":
        command.extend(["--ollama-url", str(args.ollama_url)])
    if args.ollama_model != "qwen3.6:35b-a3b":
        command.extend(["--ollama-model", str(args.ollama_model)])
    if args.ollama_timeout != 120:
        command.extend(["--ollama-timeout", str(args.ollama_timeout)])

    queue_count = len(plan.get("queue") or [])
    if queue_count:
        command.append("--run-new-doc-plan")
        if args.max_rebuilds and args.max_rebuilds > 0:
            command.extend(["--max-rebuilds", str(args.max_rebuilds)])
        elif queue_count > 1:
            command.extend(["--max-rebuilds", "1"])
    return command


def build_planner_followup_command(command: Sequence[str]) -> Optional[List[str]]:
    """Return the all-queued-targets follow-up command after a one-target first run."""
    values = list(command)
    for index in range(len(values) - 1):
        if values[index] == "--max-rebuilds" and values[index + 1] == "1":
            return values[:index] + values[index + 2 :]
    return None


def format_planner_report(args: argparse.Namespace, result: Dict[str, Any]) -> str:
    """Render a concise recommendation for how to run the crawler for real."""
    health = result["health"]
    plan = result["plan"]
    summary = health.get("summary", {})
    queue = list(plan.get("queue") or [])
    command = build_planner_real_command(args, plan)
    followup_command = build_planner_followup_command(command) if len(queue) > 1 else None
    repairs = plan.get("repairs") or []
    errors = health.get("errors") or []
    warnings = health.get("warnings") or []
    blocked = health.get("blocked_docs") or []
    unsafe = health.get("unsafe_docs") or []

    lines = [
        "[crawl-component-docs] planner mode",
        "",
        "Inspected state without writing health, plan, manifest, or plan-script files.",
        "",
        "Current state:",
        f"  manifest entries: {summary.get('total_manifest_entries', 0)}",
        f"  docs scanned: {summary.get('total_docs_scanned', 0)}",
        f"  current docs: {summary.get('current', 0)}",
        f"  stale docs: {summary.get('stale', 0)}",
        f"  missing docs: {summary.get('missing', 0)}",
        f"  blocked docs: {summary.get('blocked', 0)}",
        f"  orphaned docs: {summary.get('orphaned', 0)}",
        f"  queued rebuilds: {summary.get('queued_rebuilds', len(queue))}",
        f"  repairs available: {len(repairs)}",
        f"  warnings: {len(warnings)}",
        f"  errors: {len(errors)}",
        "",
        "Recommended real crawler command:",
        f"  {' '.join(quote_arg(part) for part in command)}",
    ]

    if queue:
        if len(queue) > 1 and followup_command:
            lines.extend([
                "",
                "Why this command:",
                "  Multiple rebuilds are queued, so the first real run is limited to one target.",
                "  After that succeeds, run the follow-up command to process the rest:",
                f"  {' '.join(quote_arg(part) for part in followup_command)}",
            ])
        else:
            lines.extend([
                "",
                "Why this command:",
                "  One rebuild is queued, so the crawler can generate and execute the new resumable plan.",
            ])
        lines.append("")
        lines.append("Queued targets:")
        for item in queue[:10]:
            reasons = ", ".join(str(r) for r in item.get("reasons", [])) or "planned"
            lines.append(f"  - {item.get('id')}: {reasons}")
        if len(queue) > 10:
            lines.append(f"  - ... {len(queue) - 10} more")
    elif repairs:
        lines.extend([
            "",
            "Why this command:",
            "  No rebuilds are queued, but safe manifest/doc repairs are available.",
            "  The recommended command writes the normal health/build outputs and applies safe repairs.",
        ])
    else:
        lines.extend([
            "",
            "Why this command:",
            "  No rebuilds are queued. The recommended command refreshes the normal health/build outputs.",
        ])

    if errors or blocked or unsafe:
        lines.append("")
        lines.append("Review before running:")
        for error in errors[:5]:
            lines.append(f"  - error: {error}")
        if len(errors) > 5:
            lines.append(f"  - ... {len(errors) - 5} more errors")
        for item in blocked[:5]:
            lines.append(f"  - blocked: {item}")
        if len(blocked) > 5:
            lines.append(f"  - ... {len(blocked) - 5} more blocked docs")
        for item in unsafe[:5]:
            lines.append(f"  - unsafe: {item}")
        if len(unsafe) > 5:
            lines.append(f"  - ... {len(unsafe) - 5} more unsafe docs")

    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings[:5]:
            lines.append(f"  - {warning}")
        if len(warnings) > 5:
            lines.append(f"  - ... {len(warnings) - 5} more")

    lines.extend([
        "",
        "Preview command:",
        f"  {' '.join(quote_arg(part) for part in command + ['--dry-run'])}",
    ])

    return "\n".join(lines)


def run_planner_mode(args: argparse.Namespace) -> int:
    """Inspect crawler state and print a recommended real invocation."""
    result = audit_and_plan(build_planner_audit_args(args))
    print(format_planner_report(args, result))
    return 0


def execute_plan(plan: Dict[str, Any], repo_root: Path, args: argparse.Namespace) -> None:
    queue = list(plan.get("queue") or [])
    if args.max_rebuilds and args.max_rebuilds > 0:
        queue = queue[: args.max_rebuilds]
    for item in queue:
        argv = item.get("argv")
        if not isinstance(argv, list) or not argv:
            continue
        if args.verbose:
            print(f"[crawl-component-docs] executing: {' '.join(quote_arg(str(a)) for a in argv)}")
        result = subprocess.run([str(a) for a in argv], cwd=repo_root)
        if result.returncode != 0:
            raise SystemExit(f"rebuild failed for {item.get('id')}: exit {result.returncode}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit, repair, and plan generated component documentation builds.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--output-root", default=OUTPUT_ROOT_DEFAULT, help="Generated docs root.")
    parser.add_argument("--planner", action="store_true", help="Inspect docs/code state and recommend the real crawler command to run.")
    parser.add_argument("--plan", default="", help="Path for doc-build.json. Default: <output-root>/doc-build.json")
    parser.add_argument("--health", default="", help="Path for doc-health.json. Default: <output-root>/doc-health.json")
    parser.add_argument("--target", default="", help="Optional id, prefix, or glob to audit/plan.")
    parser.add_argument("--check", action="store_true", default=True, help="Audit docs and code. Default on.")
    parser.add_argument("--repair", action="store_true", default=True, help="Apply safe repairs. Default on.")
    parser.add_argument("--no-repair", action="store_true", help="Audit only; do not repair manifest/docs.")
    parser.add_argument("--dry-run", action="store_true", help="Show/write no changes; do not execute plan.")
    parser.add_argument("--include-undocumented", action="store_true", default=True, help="Queue discovered components without docs. Default on.")
    parser.add_argument("--skip-undocumented", action="store_true", help="Do not queue discovered components that are missing docs.")
    parser.add_argument("--plan-dir", default="tools/documentation", help="Directory for generated Python plan scripts.")
    parser.add_argument("--plan-id", default="", help="Optional id to use in plan-<id>.py.")
    parser.add_argument("--plan-script", default="", help="Explicit path for generated Python plan script.")
    parser.add_argument("--no-plan-script", action="store_true", help="Do not write tools/documentation/plan-<id>.py.")
    parser.add_argument("--force-full-rebuild", action="store_true", help="Queue all known docs for rebuild.")
    parser.add_argument("--execute-plan", action="store_true", help="Legacy alias for --run-new-doc-plan.")
    parser.add_argument("--run-new-doc-plan", action="store_true", help="Write and run the newly generated tools/documentation/plan-<id>.py script.")
    parser.add_argument("--max-rebuilds", type=int, default=0, help="Limit executed rebuilds; 0 means no limit.")
    parser.add_argument("--ask-model", action="store_true", help="Ask local Ollama for advisory planning help.")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama base URL for --ask-model.")
    parser.add_argument("--ollama-model", default="qwen3.6:35b-a3b", help="Ollama model for --ask-model.")
    parser.add_argument("--ollama-timeout", type=int, default=120, help="Ollama timeout seconds for --ask-model.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed progress.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.planner:
        return run_planner_mode(args)
    result = audit_and_plan(args)
    health = result["health"]
    plan = result["plan"]

    if args.verbose:
        print(f"[crawl-component-docs] health: {result['health_path']}")
        print(f"[crawl-component-docs] plan: {result['plan_path']}")
        if result.get("plan_script_path"):
            print(f"[crawl-component-docs] plan script: {result['plan_script_path']}")
        print(f"[crawl-component-docs] summary: {json.dumps(health.get('summary', {}), sort_keys=True)}")
        if result.get("manifest_changed"):
            if args.dry_run or args.no_repair:
                print("[crawl-component-docs] manifest repairs available but not applied")
            else:
                print("[crawl-component-docs] manifest repaired")
        if plan.get("queue"):
            print("[crawl-component-docs] queued rebuilds:")
            for item in plan["queue"]:
                print(f"  - {item['id']}: {item.get('command')}")
    else:
        print(json.dumps({
            "ok": True,
            "summary": health.get("summary", {}),
            "plan": result["plan_path"],
            "plan_script": result.get("plan_script_path", ""),
            "health": result["health_path"],
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
