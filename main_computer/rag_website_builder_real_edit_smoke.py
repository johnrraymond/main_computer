#!/usr/bin/env python3
"""
Standalone Website Builder RAG golden-path real-edit smoke.

Purpose:
  Prove the first Website Builder mounted-editing patch without wiring the live UI:

    active Website Builder site
      + allowlisted Website Builder implementation files
      -> scoped editable evidence
      -> AI structured proposal JSON, or deterministic offline fixture
      -> deterministic server-side validation
      -> full replacement payload materialization
      -> explicit apply into a minimal temp apply root by default
      -> post-apply hash verification

The model may infer from evidence. The model is not the verifier.

Typical runs from the repository root:

  python main_computer/rag_website_builder_real_edit_smoke.py --offline-self-check

  python main_computer/rag_website_builder_real_edit_smoke.py ^
    --site-id johnrraymond ^
    --offline-fixture ^
    --prompt "change the hero headline to Welcome to Arcstorm"

  python main_computer/rag_website_builder_real_edit_smoke.py ^
    --site-id johnrraymond ^
    --prompt "fix the Website Builder preview refresh bug"

By default this smoke writes only to diagnostics_output/.../applied_repo.
It never mutates the source repository unless both --apply-live and
--yes-really-write-source are passed.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODE = "rag_website_builder_real_edit_smoke"
PROPOSAL_MODE = "website_builder_rag_edit_proposal"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:26b"

TEXT_FILE_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
PRIMARY_SITE_FILES = ["site.json", "builder.json", "index.html", "style.css", "script.js"]
MAX_TEXT_FILE_CHARS = 18_000
MAX_BUILDER_SNIPPET_CHARS = 7_000
MAX_SITE_RECORDS = 120
MAX_FILE_COUNT = 24
MAX_TOTAL_REPLACEMENT_CHARS = 500_000

BUILDER_ALLOWLIST_EXPLICIT = [
    "main_computer/web/applications/scripts/website-builder.js",
    "main_computer/web/applications/styles/website-builder.css",
    "main_computer/viewport_routes_applications.py",
    "main_computer/viewport_route_dispatch.py",
    "main_computer/website_project_manifest.py",
]
BUILDER_ALLOWLIST_GLOBS = [
    "tests/test_website_builder*.py",
]


class SmokeFailure(RuntimeError):
    pass


@dataclass
class MaterializedFile:
    path: str
    operation: str
    original_sha256: str | None
    replacement_sha256: str
    replacement_text: str


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def compact_json(value: Any, *, limit: int = 2200) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def repo_root_from(start: Path) -> Path:
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "runtime" / "websites").is_dir() and (candidate / "main_computer").is_dir():
            return candidate
    raise SmokeFailure("Could not find repo root containing runtime/websites/ and main_computer/.")


def detect_repo_root_arg(value: str | None) -> Path:
    if value:
        root = Path(value).resolve()
        require(root.is_dir(), f"--repo does not exist or is not a directory: {root}")
        require((root / "runtime" / "websites").is_dir(), f"--repo lacks runtime/websites/: {root}")
        require((root / "main_computer").is_dir(), f"--repo lacks main_computer/: {root}")
        return root
    return repo_root_from(Path.cwd())


def safe_relpath(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.replace("\\", "/").strip().lstrip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def path_inside_root(path: str, root: str) -> bool:
    safe = safe_relpath(path)
    clean_root = root.rstrip("/")
    return bool(safe and (safe == clean_root or safe.startswith(clean_root + "/")))


def validate_site_id(raw: object) -> str:
    site_id = str(raw or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]", site_id):
        raise SmokeFailure("Website id must be 3-64 lowercase letters, numbers, and hyphens.")
    return site_id


def list_site_ids(repo: Path) -> list[str]:
    root = repo / "runtime" / "websites"
    ids: list[str] = []
    for manifest_path in sorted(root.glob("*/site.json")):
        try:
            site_id = validate_site_id(manifest_path.parent.name)
        except SmokeFailure:
            continue
        ids.append(site_id)
    return ids


def select_site_id(repo: Path, requested: str | None) -> str:
    if requested:
        site_id = validate_site_id(requested)
        require((repo / "runtime" / "websites" / site_id / "site.json").is_file(), f"Unknown website project: {site_id}")
        return site_id
    ids = list_site_ids(repo)
    require(bool(ids), "No Website Builder sites found under runtime/websites/.")
    return ids[0]


def discover_builder_allowlist(repo: Path) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()

    def add(rel: str) -> None:
        safe = safe_relpath(rel)
        if not safe or safe in seen:
            return
        path = repo / safe
        if path.is_file() and path.suffix.lower() in TEXT_FILE_EXTENSIONS:
            discovered.append(safe)
            seen.add(safe)

    for rel in BUILDER_ALLOWLIST_EXPLICIT:
        add(rel)
    for pattern in BUILDER_ALLOWLIST_GLOBS:
        for path in sorted(repo.glob(pattern)):
            try:
                rel = path.relative_to(repo).as_posix()
            except ValueError:
                continue
            add(rel)
    return discovered


def is_builder_path(path: str, builder_allowlist: set[str]) -> bool:
    safe = safe_relpath(path)
    return bool(safe and safe in builder_allowlist)


def is_site_path(path: str, site_root: str) -> bool:
    return path_inside_root(path, site_root)


def allowed_path(path: str, site_root: str, builder_allowlist: set[str]) -> bool:
    safe = safe_relpath(path)
    return bool(safe and (is_site_path(safe, site_root) or is_builder_path(safe, builder_allowlist)))


def repo_path(repo: Path, rel: str) -> Path:
    safe = safe_relpath(rel)
    require(safe is not None, f"Unsafe path: {rel!r}")
    return repo / safe


def json_pointer_escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def json_pointer_unescape(part: str) -> str:
    return part.replace("~1", "/").replace("~0", "~")


def json_pointer_get(doc: Any, pointer: str) -> Any:
    require(isinstance(pointer, str), "json_pointer must be a string")
    if pointer == "":
        return doc
    require(pointer.startswith("/"), f"Invalid JSON pointer: {pointer!r}")
    value = doc
    for raw in pointer.split("/")[1:]:
        part = json_pointer_unescape(raw)
        if isinstance(value, list):
            require(part.isdigit(), f"JSON pointer expected list index at {pointer!r}")
            index = int(part)
            require(0 <= index < len(value), f"JSON pointer index out of range at {pointer!r}")
            value = value[index]
        elif isinstance(value, dict):
            require(part in value, f"JSON pointer key missing at {pointer!r}")
            value = value[part]
        else:
            raise SmokeFailure(f"JSON pointer traversed into scalar at {pointer!r}")
    return value


def json_pointer_set(doc: Any, pointer: str, replacement: Any) -> Any:
    if pointer == "":
        return replacement
    require(pointer.startswith("/"), f"Invalid JSON pointer: {pointer!r}")
    parts = [json_pointer_unescape(part) for part in pointer.split("/")[1:]]
    require(parts, f"Invalid JSON pointer: {pointer!r}")
    target = doc
    for part in parts[:-1]:
        if isinstance(target, list):
            require(part.isdigit(), f"JSON pointer expected list index at {pointer!r}")
            index = int(part)
            require(0 <= index < len(target), f"JSON pointer index out of range at {pointer!r}")
            target = target[index]
        elif isinstance(target, dict):
            require(part in target, f"JSON pointer key missing at {pointer!r}")
            target = target[part]
        else:
            raise SmokeFailure(f"JSON pointer traversed into scalar at {pointer!r}")
    leaf = parts[-1]
    if isinstance(target, list):
        require(leaf.isdigit(), f"JSON pointer expected list index at {pointer!r}")
        index = int(leaf)
        require(0 <= index < len(target), f"JSON pointer index out of range at {pointer!r}")
        target[index] = replacement
    elif isinstance(target, dict):
        require(leaf in target, f"JSON pointer key missing at {pointer!r}")
        target[leaf] = replacement
    else:
        raise SmokeFailure(f"JSON pointer target is scalar at {pointer!r}")
    return doc


def limited_file_record(repo: Path, rel: str, *, snippet_limit: int = MAX_TEXT_FILE_CHARS) -> dict[str, Any]:
    path = repo_path(repo, rel)
    text = read_text(path)
    return {
        "path": rel,
        "sha256": sha256_text(text),
        "chars": len(text),
        "truncated": len(text) > snippet_limit,
        "text": text[:snippet_limit],
    }


def extract_html_text_records(rel: str, html: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    tag_re = re.compile(r"<(h[1-6]|p|a|button|span|li|title)\b[^>]*>(.*?)</\1>", re.I | re.S)
    for index, match in enumerate(tag_re.finditer(html)):
        tag = match.group(1).lower()
        raw_inner = match.group(2)
        text = re.sub(r"<[^>]+>", "", raw_inner)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        anchor = match.group(0)
        records.append(
            {
                "record_type": "site_html_text",
                "path": rel,
                "tag": tag,
                "index": index,
                "exact_value": text,
                "exact_anchor": anchor,
                "editable_as": "text_replacement",
            }
        )
        if len(records) >= 40:
            break
    return records


def walk_json_scalar_records(path: str, value: Any, pointer: str = "", records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if records is None:
        records = []
    if len(records) >= MAX_SITE_RECORDS:
        return records
    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            child_pointer = pointer + "/" + json_pointer_escape(str(key))
            if key in {"text", "title", "heading", "label", "name", "description", "headline", "subheading", "cta", "background", "color"}:
                if isinstance(child, (str, int, float, bool)) or child is None:
                    records.append(
                        {
                            "record_type": "site_json_scalar",
                            "path": path,
                            "json_pointer": child_pointer,
                            "key": key,
                            "exact_value": child,
                            "editable_as": "json_edit",
                        }
                    )
            walk_json_scalar_records(path, child, child_pointer, records)
            if len(records) >= MAX_SITE_RECORDS:
                break
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_json_scalar_records(path, child, pointer + "/" + str(index), records)
            if len(records) >= MAX_SITE_RECORDS:
                break
    return records


def extract_css_records(rel: str, css: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for match in re.finditer(r"(?m)^(\s*(?:body|:root|\.hero|main|html)\b[^{]*\{[^}]*\})", css):
        block = match.group(1)
        if any(token in block.lower() for token in ["background", "color", "--"]):
            records.append(
                {
                    "record_type": "site_css_block",
                    "path": rel,
                    "exact_anchor": block,
                    "editable_as": "text_replacement",
                    "reason": "CSS block contains color/background/theme declarations.",
                }
            )
        if len(records) >= 20:
            break
    return records


def builder_focus_snippet(text: str, keywords: list[str], limit: int = MAX_BUILDER_SNIPPET_CHARS) -> str:
    lowered = text.lower()
    windows: list[str] = []
    used_ranges: list[tuple[int, int]] = []
    for keyword in keywords:
        pos = lowered.find(keyword.lower())
        if pos < 0:
            continue
        start = max(0, pos - 1200)
        end = min(len(text), pos + 2200)
        if any(not (end < s or start > e) for s, e in used_ranges):
            continue
        used_ranges.append((start, end))
        windows.append(text[start:end])
        if sum(len(item) for item in windows) >= limit:
            break
    if not windows:
        return text[:limit]
    joined = "\n\n/* --- evidence window --- */\n\n".join(windows)
    return joined[:limit]


def build_site_evidence(repo: Path, site_id: str) -> dict[str, Any]:
    site_root = f"runtime/websites/{site_id}"
    site_dir = repo / site_root
    manifest_path = site_dir / "site.json"
    require(manifest_path.is_file(), f"Missing site manifest: {site_root}/site.json")

    files: list[dict[str, Any]] = []
    editable_records: list[dict[str, Any]] = []
    site_manifest: dict[str, Any] = {}
    for filename in PRIMARY_SITE_FILES:
        path = site_dir / filename
        if not path.is_file():
            continue
        rel = f"{site_root}/{filename}"
        text = read_text(path)
        files.append(limited_file_record(repo, rel, snippet_limit=MAX_TEXT_FILE_CHARS))
        if filename.endswith(".json"):
            try:
                doc = json.loads(text)
                if filename == "site.json" and isinstance(doc, dict):
                    site_manifest = doc
                editable_records.extend(walk_json_scalar_records(rel, doc))
            except json.JSONDecodeError as exc:
                editable_records.append(
                    {
                        "record_type": "site_json_parse_error",
                        "path": rel,
                        "error": str(exc),
                        "editable_as": "none",
                    }
                )
        elif filename == "index.html":
            editable_records.extend(extract_html_text_records(rel, text))
        elif filename == "style.css":
            editable_records.extend(extract_css_records(rel, text))

    visible_files: list[str] = []
    for path in sorted(site_dir.rglob("*")):
        if path.is_file():
            visible_files.append(path.relative_to(repo).as_posix())
        if len(visible_files) >= 120:
            break

    return {
        "site_id": site_id,
        "site_root": site_root + "/",
        "site_manifest_summary": {
            "id": site_manifest.get("id") if isinstance(site_manifest, dict) else site_id,
            "name": site_manifest.get("name") if isinstance(site_manifest, dict) else site_id,
            "kind": site_manifest.get("kind") if isinstance(site_manifest, dict) else "",
            "lane": site_manifest.get("lane") if isinstance(site_manifest, dict) else "",
            "artifacts": site_manifest.get("artifacts") if isinstance(site_manifest, dict) else {},
        },
        "visible_site_files": visible_files,
        "site_files": files,
        "editable_site_records": editable_records[:MAX_SITE_RECORDS],
    }


def build_builder_evidence(repo: Path, builder_allowlist: list[str]) -> dict[str, Any]:
    keywords = [
        "website-builder-edit",
        "chat",
        "proposal",
        "apply",
        "preview",
        "refresh",
        "reload",
        "iframe",
        "srcdoc",
        "setWebsiteBuilderDraftPreview",
        "saveWebsiteBuilderSite",
        "selectWebsiteBuilderSite",
    ]
    files: list[dict[str, Any]] = []
    for rel in builder_allowlist:
        path = repo_path(repo, rel)
        text = read_text(path)
        snippet = builder_focus_snippet(text, keywords)
        files.append(
            {
                "path": rel,
                "sha256": sha256_text(text),
                "chars": len(text),
                "snippet_chars": len(snippet),
                "snippet": snippet,
            }
        )
    return {
        "builder_allowlist": builder_allowlist,
        "builder_files": files,
    }


def build_evidence(repo: Path, site_id: str) -> dict[str, Any]:
    builder_allowlist = discover_builder_allowlist(repo)
    require(builder_allowlist, "No Website Builder implementation allowlist files were discovered.")
    site = build_site_evidence(repo, site_id)
    builder = build_builder_evidence(repo, builder_allowlist)
    return {
        "mode": MODE,
        "repo_root": str(repo),
        "site_id": site_id,
        "allowed_roots": [site["site_root"]],
        "builder_allowlist": builder_allowlist,
        "site": site,
        "builder": builder,
        "instructions": {
            "model_may_reason": True,
            "server_validates": True,
            "site_edits_must_stay_inside": site["site_root"],
            "builder_edits_must_match_allowlist_exactly": builder_allowlist,
            "raw_omission_does_not_delete": True,
        },
    }


def proposal_prompt(user_prompt: str, evidence: dict[str, Any]) -> str:
    compact_evidence = {
        "site": evidence["site"],
        "builder": evidence["builder"],
        "allowed_roots": evidence["allowed_roots"],
        "builder_allowlist": evidence["builder_allowlist"],
        "instructions": evidence["instructions"],
    }
    return (
        "You are the Website Builder mounted-chat structured proposal engine.\n"
        "Return JSON only. Do not include Markdown.\n\n"
        "The model may infer the user's intent from evidence, but the server will validate every path, "
        "old value, JSON pointer, and hash before anything can be applied.\n\n"
        "Allowed edit zones:\n"
        "1. The active website/site project under the exact site root in allowed_roots.\n"
        "2. Website Builder implementation files matching builder_allowlist exactly.\n\n"
        "Return this JSON shape:\n"
        "{\n"
        '  "ok": true,\n'
        f'  "mode": "{PROPOSAL_MODE}",\n'
        '  "target_kind": "website-or-builder",\n'
        '  "target_id": "<site id>",\n'
        '  "summary": "...",\n'
        '  "grounding": [{"evidence_type": "...", "path": "...", "json_pointer": "...", "exact_value": "...", "reason": "..."}],\n'
        '  "json_edits": [{"path": "...", "json_pointer": "...", "old_value": "...", "new_value": "...", "reason": "..."}],\n'
        '  "text_replacements": [{"path": "...", "old_text": "...", "new_text": "...", "replace_all": false, "reason": "..."}],\n'
        '  "create_files": [{"path": "...", "content": "...", "reason": "..."}],\n'
        '  "warnings": []\n'
        "}\n\n"
        "Validation requirements you must satisfy:\n"
        "- Every path must be either inside allowed_roots[0] or exactly one builder_allowlist path.\n"
        "- For json_edits, old_value must exactly equal the current value at json_pointer.\n"
        "- For text_replacements, old_text must exactly occur in the current file.\n"
        "- For create_files, path must not exist and must be under the active site root.\n"
        "- Do not propose deletes.\n\n"
        f"User prompt:\n{user_prompt}\n\n"
        f"Evidence JSON:\n{json.dumps(compact_evidence, ensure_ascii=False, indent=2)}\n"
    )


def call_ollama(prompt: str, *, base_url: str, model: str, timeout: float) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    url = base_url.rstrip("/") + "/api/generate"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"Ollama request failed at {url}: {exc}") from exc
    envelope = json.loads(raw)
    text = str(envelope.get("response") or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    require(start >= 0 and end >= start, f"Model did not return JSON object: {text[:500]!r}")
    return json.loads(text[start : end + 1])


def extract_requested_value(prompt: str, fallback: str) -> str:
    text = str(prompt or "").strip()
    patterns = [
        r"\bto\s+['\"]([^'\"]+)['\"]",
        r"\bto\s+(.+)$",
        r"\bsay\s+['\"]([^'\"]+)['\"]",
        r"\bsay\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = match.group(1).strip()
        value = re.sub(r"[.?!]\s*$", "", value).strip()
        if value:
            return value
    return fallback


def first_json_record(records: list[dict[str, Any]], *, key_names: set[str]) -> dict[str, Any] | None:
    for record in records:
        if record.get("record_type") == "site_json_scalar" and record.get("key") in key_names:
            return record
    return None


def first_html_record(records: list[dict[str, Any]], *, tags: set[str]) -> dict[str, Any] | None:
    for record in records:
        if record.get("record_type") == "site_html_text" and record.get("tag") in tags:
            return record
    return None


def offline_fixture_proposal(prompt: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Deterministic proposal used to self-check validators/materializers without a model."""
    prompt_lower = prompt.lower()
    site_id = str(evidence["site_id"])
    site_root = str(evidence["site"]["site_root"])
    records = evidence["site"].get("editable_site_records") if isinstance(evidence["site"].get("editable_site_records"), list) else []
    builder_allowlist = set(evidence.get("builder_allowlist") or [])

    base: dict[str, Any] = {
        "ok": True,
        "mode": PROPOSAL_MODE,
        "target_kind": "website-or-builder",
        "target_id": site_id,
        "allowed_roots": [site_root],
        "summary": "",
        "grounding": [],
        "json_edits": [],
        "text_replacements": [],
        "create_files": [],
        "warnings": [],
    }

    if any(word in prompt_lower for word in ["builder", "preview", "refresh", "iframe", "reload"]):
        js_path = "main_computer/web/applications/scripts/website-builder.js"
        require(js_path in builder_allowlist, f"Offline builder fixture requires allowlisted {js_path}.")
        old_text = (
            "        setWebsiteBuilderLog(`Saved ${payload.site?.id || siteId}.`);\n"
            "        setWebsiteBuilderDraftPreview();\n"
            "        await loadWebsiteBuilderSites();\n"
            "        return payload;"
        )
        new_text = (
            "        setWebsiteBuilderLog(`Saved ${payload.site?.id || siteId}.`);\n"
            "        await loadWebsiteBuilderSites();\n"
            "        setWebsiteBuilderDraftPreview();\n"
            "        return payload;"
        )
        base["target_kind"] = "website-builder"
        base["summary"] = "Move Website Builder draft preview refresh after reloading saved site data."
        base["grounding"].append(
            {
                "evidence_type": "builder_source",
                "path": js_path,
                "exact_value": old_text,
                "reason": "saveWebsiteBuilderSite currently refreshes the draft preview before reloading site data.",
            }
        )
        base["text_replacements"].append(
            {
                "path": js_path,
                "old_text": old_text,
                "new_text": new_text,
                "replace_all": False,
                "reason": "Refreshing after loadWebsiteBuilderSites lets the preview use the post-save state.",
            }
        )
        return base

    if "faq" in prompt_lower:
        html_path = site_root + "index.html"
        section = (
            "\n<section class=\"faq-section\">\n"
            "  <h2>Frequently Asked Questions</h2>\n"
            "  <p>Everything you need to know before you start building.</p>\n"
            "</section>\n"
        )
        old_text = "</main>"
        base["target_kind"] = "website"
        base["summary"] = "Add a simple FAQ section to the active website."
        base["grounding"].append(
            {
                "evidence_type": "site_file",
                "path": html_path,
                "exact_value": old_text,
                "reason": "The FAQ section can be inserted before the closing main tag.",
            }
        )
        base["text_replacements"].append(
            {
                "path": html_path,
                "old_text": old_text,
                "new_text": section + old_text,
                "replace_all": False,
                "reason": "Append a basic FAQ section inside the main page content.",
            }
        )
        return base

    if any(word in prompt_lower for word in ["background", "darker", "darken"]):
        css_path = site_root + "style.css"
        site_files = evidence["site"].get("site_files") or []
        css_text = ""
        for item in site_files:
            if item.get("path") == css_path:
                css_text = str(item.get("text") or "")
                break
        match = re.search(r"background(?:-color)?\s*:\s*([^;]+);", css_text, re.I)
        require(match is not None, "Offline background fixture could not find a background declaration.")
        old_decl = match.group(0)
        new_decl = re.sub(r":\s*[^;]+;", ": #020617;", old_decl, count=1)
        base["target_kind"] = "website"
        base["summary"] = "Make the active website background darker."
        base["grounding"].append(
            {
                "evidence_type": "site_css_block",
                "path": css_path,
                "exact_value": old_decl,
                "reason": "The CSS background declaration is editable text evidence.",
            }
        )
        base["text_replacements"].append(
            {
                "path": css_path,
                "old_text": old_decl,
                "new_text": new_decl,
                "replace_all": False,
                "reason": "Replace the first background declaration with a darker color.",
            }
        )
        return base

    if any(word in prompt_lower for word in ["cta", "button"]):
        html_record = first_html_record(records, tags={"button", "a"})
        require(html_record is not None, "Offline CTA fixture could not find a button/link text record.")
        requested = extract_requested_value(prompt, "Start Building")
        old_anchor = str(html_record["exact_anchor"])
        old_text = str(html_record["exact_value"])
        new_anchor = old_anchor.replace(old_text, requested, 1)
        base["target_kind"] = "website"
        base["summary"] = f"Change the primary CTA text to {requested!r}."
        base["grounding"].append(
            {
                "evidence_type": "site_html_text",
                "path": html_record["path"],
                "exact_value": old_text,
                "reason": "The first button/link text record is the primary CTA candidate.",
            }
        )
        base["text_replacements"].append(
            {
                "path": html_record["path"],
                "old_text": old_anchor,
                "new_text": new_anchor,
                "replace_all": False,
                "reason": "Replace the full HTML anchor to preserve attributes while changing visible text.",
            }
        )
        return base

    requested = extract_requested_value(prompt, "Welcome to Arcstorm")
    json_record = first_json_record(records, key_names={"heading", "headline", "title", "text", "name"})
    if json_record and str(json_record.get("path", "")).endswith("builder.json"):
        base["target_kind"] = "website"
        base["summary"] = f"Change the active website headline to {requested!r}."
        base["grounding"].append(
            {
                "evidence_type": "site_json_scalar",
                "path": json_record["path"],
                "json_pointer": json_record["json_pointer"],
                "exact_value": json_record["exact_value"],
                "reason": "The selected JSON scalar is a headline/title-like editable site record.",
            }
        )
        base["json_edits"].append(
            {
                "path": json_record["path"],
                "json_pointer": json_record["json_pointer"],
                "old_value": json_record["exact_value"],
                "new_value": requested,
                "reason": "Update the existing heading/title-like JSON field.",
            }
        )
        return base

    html_record = first_html_record(records, tags={"h1", "h2"})
    require(html_record is not None, "Offline headline fixture could not find JSON or HTML heading evidence.")
    old_anchor = str(html_record["exact_anchor"])
    old_text = str(html_record["exact_value"])
    new_anchor = old_anchor.replace(old_text, requested, 1)
    base["target_kind"] = "website"
    base["summary"] = f"Change the active website headline to {requested!r}."
    base["grounding"].append(
        {
            "evidence_type": "site_html_text",
            "path": html_record["path"],
            "exact_value": old_text,
            "reason": "The first heading text record is the hero headline candidate.",
        }
    )
    base["text_replacements"].append(
        {
            "path": html_record["path"],
            "old_text": old_anchor,
            "new_text": new_anchor,
            "replace_all": False,
            "reason": "Replace the full heading anchor to preserve tag attributes.",
        }
    )
    return base


def validate_payload_shape(proposal: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if proposal.get("ok") is not True:
        issues.append("proposal ok must be true")
    if proposal.get("mode") != PROPOSAL_MODE:
        issues.append(f"mode must be {PROPOSAL_MODE}")
    if str(proposal.get("target_id") or "") != str(evidence.get("site_id")):
        issues.append("target_id must match active site_id")
    for key in ["json_edits", "text_replacements", "create_files", "grounding", "warnings"]:
        if key in proposal and not isinstance(proposal.get(key), list):
            issues.append(f"{key} must be a list")
    if not proposal.get("json_edits") and not proposal.get("text_replacements") and not proposal.get("create_files"):
        issues.append("proposal must include at least one edit")
    return issues


def current_file_texts(repo: Path, evidence: dict[str, Any]) -> dict[str, str]:
    texts: dict[str, str] = {}
    site_root = str(evidence["site"]["site_root"])
    builder_allowlist = set(evidence.get("builder_allowlist") or [])
    paths = set()
    for collection in ["json_edits", "text_replacements"]:
        for item in evidence.get("_proposal_paths", {}).get(collection, []):
            paths.add(item)
    for file_record in evidence["site"].get("site_files", []):
        path = file_record.get("path")
        if isinstance(path, str):
            paths.add(path)
    for rel in builder_allowlist:
        paths.add(rel)
    result: dict[str, str] = {}
    for rel in sorted(paths):
        safe = safe_relpath(rel)
        if safe and allowed_path(safe, site_root, builder_allowlist) and (repo / safe).is_file():
            result[safe] = read_text(repo / safe)
    return result


def source_text_for_path(repo: Path, rel: str) -> str:
    path = repo_path(repo, rel)
    require(path.is_file(), f"File does not exist: {rel}")
    require(path.suffix.lower() in TEXT_FILE_EXTENSIONS, f"Refusing non-text file: {rel}")
    return read_text(path)


def materialize_proposal(repo: Path, evidence: dict[str, Any], proposal: dict[str, Any]) -> tuple[list[MaterializedFile], dict[str, Any]]:
    issues = validate_payload_shape(proposal, evidence)
    warnings: list[str] = []
    site_root = str(evidence["site"]["site_root"])
    builder_allowlist = set(evidence.get("builder_allowlist") or [])

    replacements: dict[str, MaterializedFile] = {}
    working_texts: dict[str, str] = {}
    original_texts: dict[str, str] = {}

    def ensure_existing_editable_file(raw_path: object) -> str | None:
        safe = safe_relpath(raw_path)
        if not safe:
            issues.append(f"Unsafe path: {raw_path!r}")
            return None
        if not allowed_path(safe, site_root, builder_allowlist):
            issues.append(f"Path is outside active site root and builder allowlist: {safe}")
            return None
        path = repo / safe
        if not path.is_file():
            issues.append(f"Edit target does not exist: {safe}")
            return None
        if path.suffix.lower() not in TEXT_FILE_EXTENSIONS:
            issues.append(f"Edit target is not a text file: {safe}")
            return None
        if safe not in working_texts:
            text = read_text(path)
            working_texts[safe] = text
            original_texts[safe] = text
        return safe

    for index, item in enumerate(proposal.get("json_edits") or []):
        if not isinstance(item, dict):
            issues.append(f"json_edits[{index}] must be an object")
            continue
        rel = ensure_existing_editable_file(item.get("path"))
        if not rel:
            continue
        pointer = item.get("json_pointer")
        if not isinstance(pointer, str):
            issues.append(f"json_edits[{index}].json_pointer must be a string")
            continue
        try:
            doc = json.loads(working_texts[rel])
        except json.JSONDecodeError as exc:
            issues.append(f"json_edits[{index}] target is not valid JSON: {rel}: {exc}")
            continue
        try:
            current_value = json_pointer_get(doc, pointer)
            if current_value != item.get("old_value"):
                issues.append(
                    f"json_edits[{index}] old_value mismatch at {rel}{pointer}: "
                    f"expected {item.get('old_value')!r}, found {current_value!r}"
                )
                continue
            doc = json_pointer_set(doc, pointer, item.get("new_value"))
        except SmokeFailure as exc:
            issues.append(f"json_edits[{index}] failed pointer validation: {exc}")
            continue
        working_texts[rel] = json.dumps(doc, indent=2, sort_keys=True) + "\n"

    for index, item in enumerate(proposal.get("text_replacements") or []):
        if not isinstance(item, dict):
            issues.append(f"text_replacements[{index}] must be an object")
            continue
        rel = ensure_existing_editable_file(item.get("path"))
        if not rel:
            continue
        old_text = item.get("old_text")
        new_text = item.get("new_text")
        if not isinstance(old_text, str) or old_text == "":
            issues.append(f"text_replacements[{index}].old_text must be a non-empty string")
            continue
        if not isinstance(new_text, str):
            issues.append(f"text_replacements[{index}].new_text must be a string")
            continue
        count = working_texts[rel].count(old_text)
        if count <= 0:
            issues.append(f"text_replacements[{index}] old_text not found exactly in {rel}")
            continue
        if item.get("replace_all") is True:
            working_texts[rel] = working_texts[rel].replace(old_text, new_text)
        else:
            if count > 1:
                warnings.append(f"text_replacements[{index}] old_text occurs {count} times in {rel}; replacing first occurrence only.")
            working_texts[rel] = working_texts[rel].replace(old_text, new_text, 1)

    created_texts: dict[str, str] = {}
    for index, item in enumerate(proposal.get("create_files") or []):
        if not isinstance(item, dict):
            issues.append(f"create_files[{index}] must be an object")
            continue
        safe = safe_relpath(item.get("path"))
        if not safe:
            issues.append(f"create_files[{index}] unsafe path: {item.get('path')!r}")
            continue
        if not is_site_path(safe, site_root):
            issues.append(f"create_files[{index}] must be under active site root only: {safe}")
            continue
        if (repo / safe).exists():
            issues.append(f"create_files[{index}] target already exists: {safe}")
            continue
        content = item.get("content")
        if not isinstance(content, str):
            issues.append(f"create_files[{index}].content must be a string")
            continue
        created_texts[safe] = content

    if issues:
        return [], {"ok": False, "issues": issues, "warnings": warnings}

    for rel, replacement_text in sorted(working_texts.items()):
        original_text = original_texts[rel]
        if replacement_text == original_text:
            warnings.append(f"No byte change for {rel}; omitting replacement payload.")
            continue
        materialized = MaterializedFile(
            path=rel,
            operation="modify",
            original_sha256=sha256_text(original_text),
            replacement_sha256=sha256_text(replacement_text),
            replacement_text=replacement_text,
        )
        replacements[rel] = materialized

    for rel, content in sorted(created_texts.items()):
        materialized = MaterializedFile(
            path=rel,
            operation="create",
            original_sha256=None,
            replacement_sha256=sha256_text(content),
            replacement_text=content,
        )
        replacements[rel] = materialized

    materialized_list = list(replacements.values())
    if not materialized_list:
        issues.append("No materialized byte changes were produced.")
    if len(materialized_list) > MAX_FILE_COUNT:
        issues.append(f"Too many files materialized: {len(materialized_list)} > {MAX_FILE_COUNT}")
    total_chars = sum(len(item.replacement_text) for item in materialized_list)
    if total_chars > MAX_TOTAL_REPLACEMENT_CHARS:
        issues.append(f"Replacement payload too large: {total_chars} > {MAX_TOTAL_REPLACEMENT_CHARS}")
    if issues:
        return [], {"ok": False, "issues": issues, "warnings": warnings}

    validation = {
        "ok": True,
        "issues": [],
        "warnings": warnings,
        "materialized_files": [
            {
                "operation": item.operation,
                "path": item.path,
                "original_sha256": item.original_sha256,
                "replacement_sha256": item.replacement_sha256,
                "replacement_chars": len(item.replacement_text),
            }
            for item in materialized_list
        ],
    }
    return materialized_list, validation


def replacement_payload_path(output_dir: Path, rel: str) -> Path:
    safe = safe_relpath(rel)
    require(safe is not None, f"Unsafe payload path: {rel!r}")
    return output_dir / "files" / safe


def write_materialized_bundle(repo: Path, output_dir: Path, materialized: list[MaterializedFile]) -> dict[str, Any]:
    files_meta: list[dict[str, Any]] = []
    diff_chunks: list[str] = []
    for item in materialized:
        payload_path = replacement_payload_path(output_dir, item.path)
        write_text(payload_path, item.replacement_text)

        original_lines: list[str] = []
        original_path = repo / item.path
        if item.operation == "modify":
            original_lines = read_text(original_path).splitlines(keepends=True)
        replacement_lines = item.replacement_text.splitlines(keepends=True)
        diff_chunks.extend(
            difflib.unified_diff(
                original_lines,
                replacement_lines,
                fromfile=("a/" + item.path if item.operation == "modify" else "/dev/null"),
                tofile="b/" + item.path,
                lineterm="",
            )
        )

        files_meta.append(
            {
                "operation": item.operation,
                "path": item.path,
                "original_sha256": item.original_sha256,
                "replacement_sha256": item.replacement_sha256,
                "payload": f"files/{item.path}",
                "replacement_chars": len(item.replacement_text),
            }
        )

    reference_patch = "\n".join(diff_chunks)
    if reference_patch and not reference_patch.endswith("\n"):
        reference_patch += "\n"
    write_text(output_dir / "reference.patch", reference_patch)

    manifest = {
        "mode": MODE,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "files": files_meta,
        "reference_patch": "reference.patch",
        "payload_root": "files/",
        "delete_semantics": "No deletes are represented; raw omission never implies deletion.",
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def prepare_minimal_apply_root(repo: Path, output_dir: Path, materialized: list[MaterializedFile]) -> Path:
    """Create a tiny apply root containing only files touched by this run.

    Earlier versions copied the entire repository before applying replacement
    payloads. On long-lived Windows worktrees that can recurse into historical
    diagnostics or patch-report folders and fail with path-length errors before
    the smoke reaches the actual validation target. The smoke only needs to
    verify stale-hash checks and final replacement hashes for the touched files,
    so temp-copy mode intentionally mirrors only those files.
    """
    apply_root = output_dir / "applied_repo"

    if apply_root.exists():
        # Avoid relying on a full recursive remove: stale failed runs on Windows
        # can contain paths that are too long to traverse. Use a fresh sibling
        # when the preferred directory cannot be removed cheaply.
        try:
            for child in apply_root.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
                else:
                    import shutil as _shutil
                    _shutil.rmtree(child)
            apply_root.rmdir()
        except Exception:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            apply_root = output_dir / f"applied_repo_{stamp}"
            require(not apply_root.exists(), f"Apply root already exists: {apply_root}")

    apply_root.mkdir(parents=True, exist_ok=True)

    mirrored: set[str] = set()
    for item in materialized:
        rel = item.path
        rel_path = Path(rel)
        require(not rel_path.is_absolute(), f"Apply path must be repo-relative: {rel}")
        require(".." not in rel_path.parts, f"Apply path may not traverse upward: {rel}")

        target = apply_root / rel_path
        if item.operation == "modify":
            source = repo / rel_path
            require(source.is_file(), f"Source file missing for temp apply: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            write_text(target, read_text(source))
            mirrored.add(rel)
        elif item.operation == "create":
            require(not (repo / rel_path).exists(), f"Create target already exists in source repo: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
        else:
            raise SmokeFailure(f"Unsupported operation for temp apply: {item.operation}")

    write_json(
        output_dir / "apply_root_manifest.json",
        {
            "mode": "minimal-temp-apply-root",
            "root": str(apply_root),
            "mirrored_files": sorted(mirrored),
            "note": "Only touched files are mirrored; no full repository copy is performed.",
        },
    )
    return apply_root


def apply_materialized_files(
    *,
    repo: Path,
    output_dir: Path,
    materialized: list[MaterializedFile],
    apply_mode: str,
    yes_really_write_source: bool,
) -> dict[str, Any]:
    if apply_mode == "none":
        return {"mode": "none", "ok": True, "applied_root": None, "files": []}

    if apply_mode == "live":
        require(yes_really_write_source, "--apply-live requires --yes-really-write-source.")
        apply_root = repo
    elif apply_mode == "temp-copy":
        apply_root = prepare_minimal_apply_root(repo, output_dir, materialized)
    else:
        raise SmokeFailure(f"Unknown apply mode: {apply_mode}")

    applied_files: list[dict[str, Any]] = []
    for item in materialized:
        target = apply_root / item.path
        if item.operation == "modify":
            require(target.is_file(), f"Apply target missing: {item.path}")
            current_text = read_text(target)
            current_sha = sha256_text(current_text)
            require(current_sha == item.original_sha256, f"Stale source hash for {item.path}: {current_sha} != {item.original_sha256}")
        elif item.operation == "create":
            require(not target.exists(), f"Create target already exists: {item.path}")
        else:
            raise SmokeFailure(f"Unsupported operation: {item.operation}")
        write_text(target, item.replacement_text)
        final_sha = sha256_text(read_text(target))
        require(final_sha == item.replacement_sha256, f"Post-apply hash mismatch for {item.path}")
        applied_files.append(
            {
                "operation": item.operation,
                "path": item.path,
                "replacement_sha256": item.replacement_sha256,
                "verified": True,
            }
        )
    return {"mode": apply_mode, "ok": True, "applied_root": str(apply_root), "files": applied_files}


def run_once(
    *,
    repo: Path,
    site_id: str,
    prompt: str,
    output_dir: Path,
    proposal_file: str | None,
    offline_fixture: bool,
    ollama_base_url: str,
    ollama_model: str,
    ollama_timeout: float,
    apply_mode: str,
    yes_really_write_source: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    evidence = build_evidence(repo, site_id)
    prompt_text = proposal_prompt(prompt, evidence)

    if proposal_file:
        proposal = json.loads(Path(proposal_file).read_text(encoding="utf-8"))
        proposal_source = f"file:{proposal_file}"
    elif offline_fixture:
        proposal = offline_fixture_proposal(prompt, evidence)
        proposal_source = "offline-fixture"
    else:
        proposal = call_ollama(prompt_text, base_url=ollama_base_url, model=ollama_model, timeout=ollama_timeout)
        proposal_source = f"ollama:{ollama_model}"

    write_json(output_dir / "prompt.json", {"prompt": prompt, "proposal_prompt": prompt_text})
    write_json(output_dir / "evidence.json", evidence)
    write_json(output_dir / "proposal.json", proposal)

    materialized, validation = materialize_proposal(repo, evidence, proposal)
    write_json(output_dir / "validation.json", validation)
    require(validation.get("ok") is True, "Proposal validation failed; see validation.json.")

    manifest = write_materialized_bundle(repo, output_dir, materialized)
    apply_report = apply_materialized_files(
        repo=repo,
        output_dir=output_dir,
        materialized=materialized,
        apply_mode=apply_mode,
        yes_really_write_source=yes_really_write_source,
    )
    report = {
        "ok": True,
        "mode": MODE,
        "repo": str(repo),
        "site_id": site_id,
        "site_root": evidence["site"]["site_root"],
        "proposal_source": proposal_source,
        "output_dir": str(output_dir),
        "summary": proposal.get("summary"),
        "manifest": manifest,
        "validation": validation,
        "apply": apply_report,
        "warnings": validation.get("warnings", []),
    }
    write_json(output_dir / "report.json", report)
    return report


def default_output_dir(repo: Path, suffix: str = "") -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{MODE}-{stamp}{suffix}"
    return repo / "diagnostics_output" / name


def read_prompt_from_args(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.prompt:
        parts.append(str(args.prompt))
    if args.prompt_file:
        parts.append(Path(args.prompt_file).read_text(encoding="utf-8"))
    if args.prompt_parts:
        parts.append(" ".join(args.prompt_parts))
    prompt = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return prompt


def offline_self_check(repo: Path, site_id: str, args: argparse.Namespace) -> dict[str, Any]:
    checks = [
        ("site-headline", "change the hero headline to Welcome to Arcstorm"),
        ("builder-preview-refresh", "fix the Website Builder preview refresh bug"),
    ]
    reports: list[dict[str, Any]] = []
    base_output = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(repo, suffix="-offline-self-check")
    for label, prompt in checks:
        report = run_once(
            repo=repo,
            site_id=site_id,
            prompt=prompt,
            output_dir=base_output / label,
            proposal_file=None,
            offline_fixture=True,
            ollama_base_url=args.ollama_base_url,
            ollama_model=args.ollama_model,
            ollama_timeout=args.ollama_timeout,
            apply_mode="temp-copy",
            yes_really_write_source=False,
        )
        reports.append(report)
    summary = {
        "ok": True,
        "mode": MODE,
        "repo": str(repo),
        "site_id": site_id,
        "checks": [
            {
                "label": label,
                "output_dir": report["output_dir"],
                "touched_files": [item["path"] for item in report["manifest"]["files"]],
                "apply_mode": report["apply"]["mode"],
            }
            for (label, _), report in zip(checks, reports)
        ],
    }
    write_json(base_output / "offline-self-check-report.json", summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Website Builder RAG real-edit smoke.")
    parser.add_argument("prompt_parts", nargs="*", help="Prompt text, if --prompt is not used.")
    parser.add_argument("--repo", help="Repository root. Defaults to walking up from cwd.")
    parser.add_argument("--site-id", help="Active Website Builder site id. Defaults to first runtime/websites/*/site.json.")
    parser.add_argument("--prompt", help="Natural-language edit request.")
    parser.add_argument("--prompt-file", help="Read natural-language edit request from a file.")
    parser.add_argument("--proposal-file", help="Use an existing proposal JSON instead of calling a model.")
    parser.add_argument("--offline-fixture", action="store_true", help="Use deterministic built-in proposal fixture.")
    parser.add_argument("--offline-self-check", action="store_true", help="Run site + builder deterministic checks.")
    parser.add_argument("--output-dir", help="Diagnostics output directory.")
    parser.add_argument("--no-apply", action="store_true", help="Do not write replacements anywhere.")
    parser.add_argument("--apply-live", action="store_true", help="Apply to the source repo; requires --yes-really-write-source.")
    parser.add_argument("--yes-really-write-source", action="store_true", help="Required with --apply-live.")
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--ollama-timeout", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        repo = detect_repo_root_arg(args.repo)
        site_id = select_site_id(repo, args.site_id)
        if args.offline_self_check:
            summary = offline_self_check(repo, site_id, args)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0

        prompt = read_prompt_from_args(args)
        require(prompt, "Provide --prompt, --prompt-file, positional prompt text, or --offline-self-check.")

        if args.apply_live:
            apply_mode = "live"
        elif args.no_apply:
            apply_mode = "none"
        else:
            apply_mode = "temp-copy"

        output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(repo)
        report = run_once(
            repo=repo,
            site_id=site_id,
            prompt=prompt,
            output_dir=output_dir,
            proposal_file=args.proposal_file,
            offline_fixture=args.offline_fixture,
            ollama_base_url=args.ollama_base_url,
            ollama_model=args.ollama_model,
            ollama_timeout=args.ollama_timeout,
            apply_mode=apply_mode,
            yes_really_write_source=args.yes_really_write_source,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except SmokeFailure as exc:
        print(json.dumps({"ok": False, "mode": MODE, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "mode": MODE, "error": f"{type(exc).__name__}: {exc}"}, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
