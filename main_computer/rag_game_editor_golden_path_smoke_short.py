#!/usr/bin/env python3
"""
Standalone Game Editor RAG golden-path smoke.

This is the template I would use before wiring the mounted Game Editor chat to
real editing. It takes one game project plus one prompt and runs the proposal-only
golden path:

  selected game -> scoped evidence -> AI proposal -> deterministic validation
  -> full replacement payloads in diagnostics_output -> no source writes

The model can infer targets from evidence fields such as id, label, role, type,
parentId, scene membership, file names, and editable props. The script does not
hard-code project-specific aliases like "main char means hero-sprite".

Run from repo root:

  python main_computer/rag_game_editor_golden_path_smoke.py \
    --project-id webgl-demo \
    --prompt "change the main char's color red"

Self-check without Ollama:

  python main_computer/rag_game_editor_golden_path_smoke.py --offline-self-check
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
from datetime import datetime
from pathlib import Path
from typing import Any


MODE = "rag_game_editor_golden_path_smoke"
DEFAULT_PROJECT_ID = "webgl-demo"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:26b"
TEXT_EXTS = {".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".ts", ".tsx", ".txt", ".yaml", ".yml"}
MAX_TEXT_CHARS = 18_000
MAX_OBJECT_RECORDS = 80


class SmokeFailure(RuntimeError):
    pass


def fail(message: str) -> None:
    raise SmokeFailure(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_rel(raw: str) -> str | None:
    text = str(raw or "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def repo_root_from(start: Path) -> Path:
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / "game_projects").is_dir() and (candidate / "main_computer").is_dir():
            return candidate
    fail("could not find repo root containing game_projects/ and main_computer/")


def resolve_repo(value: str | None) -> Path:
    if value:
        root = Path(value).resolve()
        require(root.is_dir(), f"--repo is not a directory: {root}")
        return root
    return repo_root_from(Path.cwd())


def resolve_project(repo: Path, args: argparse.Namespace) -> tuple[str, Path, str]:
    if args.project_path:
        rel = safe_rel(args.project_path)
        require(rel is not None, f"unsafe --project-path: {args.project_path!r}")
        project_root = (repo / rel).resolve()
        require(project_root.is_dir(), f"project path is not a directory: {project_root}")
        require((project_root / "project.json").is_file(), f"project path has no project.json: {project_root}")
        try:
            project_root.relative_to(repo)
        except ValueError:
            fail("--project-path must be inside --repo")
        return project_root.name, project_root, rel.rstrip("/") + "/"

    project_id = str(args.project_id or DEFAULT_PROJECT_ID).strip()
    require(re.fullmatch(r"[A-Za-z0-9_.-]+", project_id) is not None, f"unsafe project id: {project_id!r}")
    project_root = repo / "game_projects" / project_id
    require(project_root.is_dir(), f"missing game project: {project_root}")
    require((project_root / "project.json").is_file(), f"missing project.json: {project_root}")
    return project_id, project_root, f"game_projects/{project_id}/"


def pointer_escape(token: str) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer_get(doc: Any, pointer: str) -> Any:
    require(pointer.startswith("/"), f"JSON pointer must start with /: {pointer!r}")
    current = doc
    for raw in pointer.split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            require(part.isdigit(), f"invalid list index in pointer: {pointer!r}")
            index = int(part)
            require(0 <= index < len(current), f"list index out of range in pointer: {pointer!r}")
            current = current[index]
        elif isinstance(current, dict):
            require(part in current, f"missing key {part!r} in pointer: {pointer!r}")
            current = current[part]
        else:
            fail(f"pointer descends through scalar value: {pointer!r}")
    return current


def pointer_set(doc: Any, pointer: str, value: Any) -> None:
    require(pointer.startswith("/") and pointer != "/", f"set pointer must address a child: {pointer!r}")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
    parent = doc
    for part in parts[:-1]:
        if isinstance(parent, list):
            require(part.isdigit(), f"invalid list index in pointer: {pointer!r}")
            parent = parent[int(part)]
        elif isinstance(parent, dict):
            require(part in parent, f"missing key {part!r} in pointer: {pointer!r}")
            parent = parent[part]
        else:
            fail(f"pointer descends through scalar value: {pointer!r}")
    leaf = parts[-1]
    if isinstance(parent, list):
        require(leaf.isdigit(), f"invalid list index in pointer: {pointer!r}")
        index = int(leaf)
        require(0 <= index < len(parent), f"list index out of range in pointer: {pointer!r}")
        parent[index] = value
    elif isinstance(parent, dict):
        require(leaf in parent, f"missing key {leaf!r} in pointer: {pointer!r}")
        parent[leaf] = value
    else:
        fail(f"pointer parent is scalar: {pointer!r}")


def normalize_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    colors = {
        "black": "#000000",
        "blue": "#0000ff",
        "cyan": "#00ffff",
        "green": "#00ff00",
        "magenta": "#ff00ff",
        "orange": "#ffa500",
        "purple": "#800080",
        "red": "#ff0000",
        "white": "#ffffff",
        "yellow": "#ffff00",
    }
    return colors.get(value.strip().lower(), value)


def inside_allowed(path: str, allowed_root: str) -> bool:
    rel = safe_rel(path)
    root = allowed_root.rstrip("/")
    return bool(rel and (rel == root or rel.startswith(root + "/")))


def iter_project_files(project_root: Path) -> list[Path]:
    return sorted(path for path in project_root.rglob("*") if path.is_file())


def object_record(allowed_root: str, scene_index: int, scene: dict[str, Any], object_index: int, obj: dict[str, Any]) -> dict[str, Any]:
    props = obj.get("props") if isinstance(obj.get("props"), dict) else {}
    pointer = f"/scenes/{scene_index}/objects/{object_index}"
    prop_pointers = {
        f"props.{key}": f"{pointer}/props/{pointer_escape(key)}"
        for key, value in sorted(props.items())
        if isinstance(value, (str, int, float, bool, list, dict))
    }
    editable = {
        "x": f"{pointer}/x",
        "y": f"{pointer}/y",
        "width": f"{pointer}/width",
        "height": f"{pointer}/height",
        **prop_pointers,
    }
    return {
        "record_type": "scene_object",
        "file": allowed_root + "project.json",
        "json_pointer": pointer,
        "scene": {
            "id": str(scene.get("id") or ""),
            "name": str(scene.get("name") or ""),
            "index": scene_index,
        },
        "object": {
            "id": str(obj.get("id") or ""),
            "type": str(obj.get("type") or ""),
            "index": object_index,
            "parentId": str(obj.get("parentId") or ""),
            "x": obj.get("x"),
            "y": obj.get("y"),
            "width": obj.get("width"),
            "height": obj.get("height"),
            "props": props,
        },
        "editable_json_pointers": editable,
        "retrieval_text": " ".join(
            str(part)
            for part in [
                obj.get("id"),
                obj.get("type"),
                obj.get("parentId"),
                props.get("label"),
                props.get("role"),
                props.get("motion"),
                props.get("color"),
                "spawn" if props.get("spawn") is True else "",
                scene.get("id"),
                scene.get("name"),
            ]
            if part is not None
        ),
    }


def build_evidence(project_root: Path, project_id: str, allowed_root: str) -> dict[str, Any]:
    project_text = (project_root / "project.json").read_text(encoding="utf-8")
    project = json.loads(project_text)
    scenes = project.get("scenes") if isinstance(project.get("scenes"), list) else []

    scene_summaries: list[dict[str, Any]] = []
    object_records: list[dict[str, Any]] = []
    for scene_index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
        type_counts: dict[str, int] = {}
        for object_index, obj in enumerate(objects):
            if not isinstance(obj, dict):
                continue
            obj_type = str(obj.get("type") or "unknown")
            type_counts[obj_type] = type_counts.get(obj_type, 0) + 1
            if len(object_records) < MAX_OBJECT_RECORDS:
                object_records.append(object_record(allowed_root, scene_index, scene, object_index, obj))
        scene_summaries.append(
            {
                "id": str(scene.get("id") or ""),
                "name": str(scene.get("name") or ""),
                "index": scene_index,
                "active": str(scene.get("id") or "") == str(project.get("activeSceneId") or ""),
                "object_count": len(objects),
                "object_types": dict(sorted(type_counts.items())),
                "metadata": scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {},
            }
        )

    file_inventory: list[dict[str, Any]] = []
    text_files: list[dict[str, Any]] = []
    for path in iter_project_files(project_root):
        rel_project = path.relative_to(project_root).as_posix()
        rel_repo = allowed_root + rel_project
        raw = path.read_bytes()
        record = {
            "path": rel_repo,
            "relative_to_project": rel_project,
            "kind": rel_project.split("/", 1)[0] if "/" in rel_project else "manifest",
            "suffix": path.suffix.lower(),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        file_inventory.append(record)
        if path.suffix.lower() in TEXT_EXTS and len(raw) <= MAX_TEXT_CHARS:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            text_files.append({"path": rel_repo, "sha256": sha256_text(text), "content": text})

    return {
        "mode": "game_editor_rag_evidence",
        "app": "game-editor",
        "target_kind": "game-project",
        "project_id": project_id,
        "allowed_root": allowed_root,
        "project_manifest": {
            "path": allowed_root + "project.json",
            "sha256": sha256_text(project_text),
            "id": str(project.get("id") or project_id),
            "name": str(project.get("name") or project_id),
            "description": str(project.get("description") or ""),
            "version": project.get("version"),
            "activeSceneId": project.get("activeSceneId"),
            "settings": project.get("settings") if isinstance(project.get("settings"), dict) else {},
        },
        "scenes": scene_summaries,
        "editable_object_records": object_records,
        "file_inventory": file_inventory,
        "text_files": text_files,
        "write_policy": {
            "mode": "proposal-only",
            "writes_enabled": False,
            "auto_apply": False,
            "server_derived_allowed_root": True,
        },
    }


def build_messages(prompt: str, evidence: dict[str, Any]) -> list[dict[str, str]]:
    system = """\
You are the proposal-only RAG edit planner for a mounted Game Editor.

Use only the supplied evidence. The evidence includes editable object records,
JSON pointers, text files, and file inventory for exactly one selected game.
Do not claim hidden files, hidden texture data, or engine behavior that is not
in evidence.

Infer likely targets from generic evidence fields only: object id, label, role,
type, parentId, scene membership, selected/editable props, file names, and exact
text content. Do not use hard-coded project-specific synonym rules.

Return JSON only:
{
  "ok": true,
  "mode": "game_editor_rag_edit_proposal",
  "target_kind": "game-project",
  "target_id": "<exact project_id>",
  "allowed_root": "<exact allowed_root>",
  "summary": "brief user-facing summary",
  "grounding": [
    {
      "evidence_type": "scene_object|text_file|project_manifest|file_inventory",
      "path": "repo-relative path from evidence",
      "json_pointer": "JSON pointer when relevant, else empty string",
      "exact_value": "exact current value when relevant",
      "reason": "why this evidence supports the edit"
    }
  ],
  "json_edits": [
    {
      "path": "repo-relative .json path",
      "json_pointer": "/pointer/to/existing/value",
      "old_value": "exact current value at pointer",
      "new_value": "replacement value",
      "reason": "why this edit satisfies the prompt"
    }
  ],
  "text_replacements": [
    {
      "path": "repo-relative text file path",
      "old_text": "exact substring copied from evidence",
      "new_text": "replacement substring",
      "reason": "why this edit satisfies the prompt"
    }
  ],
  "create_files": [
    {
      "path": "repo-relative path inside scripts/ or data/",
      "content": "complete file content",
      "reason": "why this new file is necessary"
    }
  ],
  "warnings": []
}

Rules:
- Prefer existing editable fields over invented metadata.
- For project.json object/scene edits, use json_edits and supplied JSON pointers.
- old_value must exactly equal the current value at json_pointer.
- text old_text must be copied exactly and appear once.
- All paths must stay inside allowed_root.
- Do not include apply, Git, or mutation claims.
"""
    user = {
        "runtime_user_prompt": prompt,
        "selected_game_editor_evidence": evidence,
        "deterministic_validator": [
            "verifies every path stays inside allowed_root",
            "verifies every json_edit old_value at json_pointer",
            "verifies every text_replacement old_text is exact source text",
            "materializes full replacement files without modifying source files",
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, indent=2, sort_keys=True)},
    ]


def call_ollama(args: argparse.Namespace, messages: list[dict[str, str]]) -> str:
    base_url = args.ollama_url or os.environ.get("MAIN_COMPUTER_OLLAMA_URL") or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
    model = args.model or os.environ.get("MAIN_COMPUTER_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": args.num_predict},
    }
    if args.format_mode == "json":
        payload["format"] = "json"
    if args.think_mode != "omit":
        payload["think"] = {"false": False, "true": True}.get(args.think_mode, args.think_mode)

    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        fail(f"Ollama request failed at {base_url!r}: {exc}")
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    require(isinstance(content, str) and content.strip(), "Ollama returned no message.content")
    return content


def parse_jsonish(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        require(start >= 0 and end > start, "model response did not contain a JSON object")
        value = json.loads(text[start : end + 1])
    require(isinstance(value, dict), "model response JSON must be an object")
    return value


def source_texts(evidence: dict[str, Any]) -> dict[str, str]:
    return {
        item["path"]: item["content"]
        for item in evidence.get("text_files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str)
    }


def offline_fixture(prompt: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Validator self-check only. Real runs use the model."""
    records = evidence.get("editable_object_records", [])
    target = next((r for r in records if "props.color" in r.get("editable_json_pointers", {})), None)
    require(target is not None, "offline fixture could not find editable props.color")
    pointer = target["editable_json_pointers"]["props.color"]
    old_value = target["object"]["props"]["color"]
    return {
        "ok": True,
        "mode": "game_editor_rag_edit_proposal",
        "target_kind": "game-project",
        "target_id": evidence["project_id"],
        "allowed_root": evidence["allowed_root"],
        "summary": f"Proposal only: update {target['object']['id']} color.",
        "grounding": [
            {
                "evidence_type": "scene_object",
                "path": evidence["allowed_root"] + "project.json",
                "json_pointer": target["json_pointer"],
                "exact_value": target["object"],
                "reason": "The object record exposes the editable color prop.",
            }
        ],
        "json_edits": [
            {
                "path": evidence["allowed_root"] + "project.json",
                "json_pointer": pointer,
                "old_value": old_value,
                "new_value": "#ff0000" if "red" in prompt.lower() else old_value,
                "reason": "Color is an existing editable object prop.",
            }
        ],
        "text_replacements": [],
        "create_files": [],
        "warnings": [],
    }


def validate_shape(proposal: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    issues = []
    expected = {
        "ok": True,
        "mode": "game_editor_rag_edit_proposal",
        "target_kind": "game-project",
        "target_id": evidence["project_id"],
        "allowed_root": evidence["allowed_root"],
    }
    for key, value in expected.items():
        if proposal.get(key) != value:
            issues.append(f"{key} must be {value!r}")
    for key in ("grounding", "json_edits", "text_replacements", "create_files", "warnings"):
        if not isinstance(proposal.get(key), list):
            issues.append(f"{key} must be a list")
    if not isinstance(proposal.get("summary"), str) or not proposal["summary"].strip():
        issues.append("summary must be a non-empty string")
    return issues


def materialize(repo: Path, evidence: dict[str, Any], proposal: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues = validate_shape(proposal, evidence)
    warnings: list[str] = []
    texts = source_texts(evidence)
    allowed_root = evidence["allowed_root"]
    replacements: dict[str, str] = {}

    staged_json: dict[str, Any] = {}
    for index, edit in enumerate(proposal.get("json_edits", [])):
        if not isinstance(edit, dict):
            issues.append(f"json_edits[{index}] must be an object")
            continue
        path = safe_rel(str(edit.get("path") or ""))
        pointer = str(edit.get("json_pointer") or "")
        if not path or not inside_allowed(path, allowed_root):
            issues.append(f"json_edits[{index}] path outside allowed root: {edit.get('path')!r}")
            continue
        if not path.endswith(".json") or path not in texts:
            issues.append(f"json_edits[{index}] target must be an evidenced JSON file: {path!r}")
            continue
        staged_json.setdefault(path, json.loads(texts[path]))
        try:
            current = pointer_get(staged_json[path], pointer)
        except SmokeFailure as exc:
            issues.append(f"json_edits[{index}] invalid pointer: {exc}")
            continue
        old_value = normalize_value(edit.get("old_value"))
        new_value = normalize_value(edit.get("new_value"))
        if current != old_value:
            issues.append(f"json_edits[{index}] old_value mismatch at {path}{pointer}: found {current!r}, expected {old_value!r}")
            continue
        pointer_set(staged_json[path], pointer, new_value)

    for path, doc in staged_json.items():
        replacement = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        if replacement != texts[path]:
            replacements[path] = replacement

    for index, change in enumerate(proposal.get("text_replacements", [])):
        if not isinstance(change, dict):
            issues.append(f"text_replacements[{index}] must be an object")
            continue
        path = safe_rel(str(change.get("path") or ""))
        old_text, new_text = change.get("old_text"), change.get("new_text")
        if not path or not inside_allowed(path, allowed_root):
            issues.append(f"text_replacements[{index}] path outside allowed root: {change.get('path')!r}")
            continue
        if path not in texts:
            issues.append(f"text_replacements[{index}] path not in text evidence: {path!r}")
            continue
        if not isinstance(old_text, str) or not old_text:
            issues.append(f"text_replacements[{index}] old_text must be non-empty")
            continue
        if not isinstance(new_text, str):
            issues.append(f"text_replacements[{index}] new_text must be string")
            continue
        original = replacements.get(path, texts[path])
        count = original.count(old_text)
        if count != 1:
            issues.append(f"text_replacements[{index}] old_text occurrence count in {path} must be 1, found {count}")
            continue
        replacements[path] = original.replace(old_text, new_text, 1)

    for index, create in enumerate(proposal.get("create_files", [])):
        if not isinstance(create, dict):
            issues.append(f"create_files[{index}] must be an object")
            continue
        path = safe_rel(str(create.get("path") or ""))
        content = create.get("content")
        if not path or not inside_allowed(path, allowed_root):
            issues.append(f"create_files[{index}] path outside allowed root: {create.get('path')!r}")
            continue
        rel_to_game = path.removeprefix(allowed_root).lstrip("/")
        folder = rel_to_game.split("/", 1)[0] if "/" in rel_to_game else ""
        if folder not in {"scripts", "data"}:
            issues.append(f"create_files[{index}] can only create under scripts/ or data/: {path}")
            continue
        if path in texts or (repo / path).exists():
            issues.append(f"create_files[{index}] target exists already: {path}")
            continue
        if not isinstance(content, str) or not content.strip():
            issues.append(f"create_files[{index}] content must be non-empty")
            continue
        replacements[path] = content if content.endswith("\n") else content + "\n"

    files = []
    for path, replacement in sorted(replacements.items()):
        original = texts.get(path, "")
        files.append(
            {
                "path": path,
                "operation": "create" if path not in texts else "modify",
                "original_sha256": sha256_text(original) if path in texts else None,
                "replacement_sha256": sha256_text(replacement),
                "replacement_text": replacement,
            }
        )
    if not files and not issues:
        warnings.append("valid proposal materialized no file changes")
    return files, {"ok": not issues, "issues": issues, "warnings": warnings}


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def write_outputs(out: Path, evidence: dict[str, Any], messages: list[dict[str, str]], proposal: dict[str, Any], files: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, str]:
    texts = source_texts(evidence)
    write_json(out / "prompt.json", {"messages": messages})
    write_json(out / "evidence.json", evidence)
    write_json(out / "proposal.json", proposal)

    manifest_files = []
    patch_parts = []
    for item in files:
        payload_rel = "files/" + item["path"]
        write_text(out / payload_rel, item["replacement_text"])
        before = texts.get(item["path"], "")
        patch_parts.append(unified_diff(item["path"], before, item["replacement_text"]))
        manifest_files.append({k: item[k] for k in ("path", "operation", "original_sha256", "replacement_sha256")} | {"payload": payload_rel})

    manifest = {
        "mode": MODE,
        "artifact_type": "proposal_only_full_replacement_payloads",
        "project_id": evidence["project_id"],
        "allowed_root": evidence["allowed_root"],
        "auto_apply": False,
        "files": manifest_files,
    }
    write_json(out / "manifest.json", manifest)
    write_text(out / "reference.patch", "\n".join(part for part in patch_parts if part))
    write_json(out / "report.json", report)
    return {
        "output_dir": str(out),
        "report": str(out / "report.json"),
        "manifest": str(out / "manifest.json"),
        "reference_patch": str(out / "reference.patch"),
    }


def read_prompt(args: argparse.Namespace) -> str:
    chunks = []
    if args.prompt:
        chunks.append(args.prompt)
    if args.prompt_file:
        chunks.append(Path(args.prompt_file).read_text(encoding="utf-8"))
    if args.positional_prompt:
        chunks.append(" ".join(args.positional_prompt))
    prompt = "\n\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip()).strip()
    if not prompt and not args.offline_self_check:
        fail("prompt required: pass --prompt, --prompt-file, or positional prompt")
    return prompt or "change the first editable character color red"


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    repo = resolve_repo(args.repo)
    project_id, project_root, allowed_root = resolve_project(repo, args)
    prompt = read_prompt(args)
    out = Path(args.output_dir).resolve() if args.output_dir else repo / "diagnostics_output" / "game_editor_rag_smoke" / f"{project_id}_{datetime.now():%Y%m%d_%H%M%S}"

    evidence = build_evidence(project_root, project_id, allowed_root)
    messages = build_messages(prompt, evidence)
    report: dict[str, Any] = {
        "ok": False,
        "mode": MODE,
        "repo_root": str(repo),
        "project_id": project_id,
        "project_path": str(project_root),
        "allowed_root": allowed_root,
        "prompt": prompt,
        "offline_self_check": bool(args.offline_self_check),
        "checks": {
            "evidence": {
                "ok": True,
                "editable_object_records": len(evidence["editable_object_records"]),
                "file_inventory": len(evidence["file_inventory"]),
                "text_files": len(evidence["text_files"]),
                "project_manifest": evidence["project_manifest"],
            }
        },
    }

    if args.offline_self_check:
        raw = json.dumps(offline_fixture(prompt, evidence), indent=2, sort_keys=True)
        proposal = json.loads(raw)
        report["checks"]["model_or_fixture"] = {"offline_fixture": True, "raw_response_preview": raw[:1200]}
    else:
        started = time.time()
        raw = call_ollama(args, messages)
        proposal = parse_jsonish(raw)
        report["checks"]["model_or_fixture"] = {
            "offline_fixture": False,
            "model": args.model or os.environ.get("MAIN_COMPUTER_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
            "elapsed_seconds": round(time.time() - started, 3),
            "raw_response_preview": raw[:1200],
        }

    files, validation = materialize(repo, evidence, proposal)
    report["checks"]["proposal_validation"] = validation
    report["checks"]["materialized_payloads"] = {
        "ok": validation["ok"],
        "count": len(files),
        "files": [{k: item[k] for k in ("path", "operation", "original_sha256", "replacement_sha256")} for item in files],
    }

    outputs = write_outputs(out, evidence, messages, proposal, files, report)
    report["outputs"] = outputs

    require(validation["ok"], f"proposal failed deterministic validation: {validation}")
    report["ok"] = True
    write_json(out / "report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Game Editor RAG golden-path proposal smoke.")
    parser.add_argument("positional_prompt", nargs="*", help="Prompt if --prompt/--prompt-file is not used.")
    parser.add_argument("--repo", default=None, help="Repo root. Defaults to walking upward from cwd.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID, help="Game project id under game_projects/.")
    parser.add_argument("--project-path", default=None, help="Repo-relative game project path.")
    parser.add_argument("--prompt", default=None, help="Runtime user prompt.")
    parser.add_argument("--prompt-file", default=None, help="Read prompt from file.")
    parser.add_argument("--output-dir", default=None, help="Diagnostics/output directory.")
    parser.add_argument("--offline-self-check", action="store_true", help="Use deterministic fixture instead of Ollama.")
    parser.add_argument("--ollama-url", default=None, help="Ollama base URL.")
    parser.add_argument("--model", default=None, help="Ollama model.")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--num-predict", type=int, default=2200)
    parser.add_argument("--format-mode", choices=["json", "none"], default="json")
    parser.add_argument("--think-mode", choices=["omit", "false", "true", "low", "medium", "high"], default="false")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = evaluate(args)
    except Exception as exc:
        report = {"ok": False, "mode": MODE, "failed_stage": type(exc).__name__, "error": str(exc)}
    json_print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
