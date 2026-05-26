#!/usr/bin/env python3
"""
Standalone Game Editor RAG golden-path real-edit smoke.

Purpose:
  Pass this script a game project and a natural-language prompt, and it performs
  the full isolated edit lifecycle the mounted Game Editor chat should eventually do:

    selected game project
      -> deterministic scoped evidence extraction
      -> model proposes grounded edits from that evidence
      -> deterministic validator verifies paths/anchors/old values
      -> deterministic materializer creates full replacement payloads
      -> explicit apply step into an isolated temp copy by default
      -> deterministic post-apply verification

The model is allowed to infer from evidence. It is not allowed to verify itself.
There are no project-specific synonym rules such as "main char means hero-sprite".
If the evidence contains labels, roles, ids, parent ids, and editable props, the
model has enough raw material to infer likely targets.

Typical runs from repo root:

  python main_computer/rag_game_editor_golden_path_smoke.py ^
    --project-id webgl-demo ^
    --prompt "change the main char's color red"

  python main_computer/rag_game_editor_golden_path_smoke.py ^
    --project-path game_projects/webgl-demo ^
    --prompt "make the enemy bigger"

  python main_computer/rag_game_editor_golden_path_smoke.py --offline-self-check

The output directory contains:
  report.json
  prompt.json
  evidence.json
  proposal.json
  manifest.json
  reference.patch
  files/<repo-relative replacement payloads>

By default this smoke applies into diagnostics_output/.../applied_repo, not your source tree.
Use --apply-live --yes-really-write-source only when you explicitly want to mutate the live repo.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MODE = "rag_game_editor_real_edit_smoke"
DEFAULT_PROJECT_ID = "webgl-demo"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:26b"

TEXT_FILE_EXTENSIONS = {
    ".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".ts", ".tsx", ".txt", ".yaml", ".yml"
}
EDITABLE_PROJECT_FOLDERS = {"scripts", "data", "assets", "builds"}
MAX_OBJECT_RECORDS = 80
MAX_TEXT_FILE_CHARS = 18_000


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


def json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def compact_json(value: Any, *, limit: int = 1800) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def repo_root_from(start: Path) -> Path:
    start = start.resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / "game_projects").is_dir() and (candidate / "main_computer").is_dir():
            return candidate
    raise SmokeFailure("Could not find repo root containing game_projects/ and main_computer/.")


def safe_relpath(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.replace("\\", "/").strip().lstrip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def path_inside_allowed(path: str, allowed_root: str) -> bool:
    safe = safe_relpath(path)
    return bool(safe and (safe == allowed_root.rstrip("/") or safe.startswith(allowed_root.rstrip("/") + "/")))


def detect_repo_root_arg(value: str | None) -> Path:
    if value:
        root = Path(value).resolve()
        require(root.is_dir(), f"--repo does not exist or is not a directory: {root}")
        return root
    return repo_root_from(Path.cwd())


def project_path_from_args(repo: Path, args: argparse.Namespace) -> tuple[str, Path, str]:
    if args.project_path:
        rel = safe_relpath(args.project_path)
        require(rel is not None, f"Unsafe --project-path: {args.project_path!r}")
        path = (repo / rel).resolve()
        require(path.is_dir(), f"--project-path is not a directory: {path}")
        require((path / "project.json").is_file(), f"--project-path has no project.json: {path}")
        try:
            path.relative_to(repo.resolve())
        except ValueError as exc:
            raise SmokeFailure("--project-path must be inside the repo root") from exc
        project_id = path.name
        return project_id, path, rel.rstrip("/") + "/"

    project_id = str(args.project_id or DEFAULT_PROJECT_ID).strip()
    require(re.fullmatch(r"[A-Za-z0-9_.-]+", project_id) is not None, f"Unsafe project id: {project_id!r}")
    rel = f"game_projects/{project_id}"
    path = repo / rel
    require(path.is_dir(), f"Game project directory not found: {path}")
    require((path / "project.json").is_file(), f"Game project has no project.json: {path}")
    return project_id, path, rel + "/"


def json_pointer_escape(token: str) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def json_pointer_get(document: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return document
    require(pointer.startswith("/"), f"JSON pointer must start with /: {pointer!r}")
    current = document
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            require(re.fullmatch(r"0|[1-9][0-9]*", part) is not None, f"Invalid list index in pointer {pointer!r}")
            index = int(part)
            require(0 <= index < len(current), f"List index out of range in pointer {pointer!r}")
            current = current[index]
        elif isinstance(current, dict):
            require(part in current, f"Missing object key in pointer {pointer!r}: {part!r}")
            current = current[part]
        else:
            raise SmokeFailure(f"Pointer descends through non-container at {part!r}: {pointer!r}")
    return current


def json_pointer_set(document: Any, pointer: str, value: Any) -> None:
    require(pointer.startswith("/") and pointer != "/", f"Set pointer must address a child value: {pointer!r}")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            require(re.fullmatch(r"0|[1-9][0-9]*", part) is not None, f"Invalid list index in pointer {pointer!r}")
            index = int(part)
            require(0 <= index < len(current), f"List index out of range in pointer {pointer!r}")
            current = current[index]
        elif isinstance(current, dict):
            require(part in current, f"Missing object key in pointer {pointer!r}: {part!r}")
            current = current[part]
        else:
            raise SmokeFailure(f"Pointer descends through non-container at {part!r}: {pointer!r}")

    leaf = parts[-1]
    if isinstance(current, list):
        require(re.fullmatch(r"0|[1-9][0-9]*", leaf) is not None, f"Invalid list index in pointer {pointer!r}")
        index = int(leaf)
        require(0 <= index < len(current), f"List index out of range in pointer {pointer!r}")
        current[index] = value
    elif isinstance(current, dict):
        require(leaf in current, f"Missing object key in pointer {pointer!r}: {leaf!r}")
        current[leaf] = value
    else:
        raise SmokeFailure(f"Pointer parent is not a container: {pointer!r}")


def color_name_to_hex(value: str) -> str:
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
    text = str(value or "").strip().lower()
    return colors.get(text, value)


def normalize_model_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in {"red", "blue", "green", "yellow", "purple", "orange", "cyan", "magenta", "white", "black"}:
            return color_name_to_hex(text)
    return value


def project_record_for_object(
    *,
    project_id: str,
    scene_index: int,
    scene: dict[str, Any],
    object_index: int,
    obj: dict[str, Any],
    allowed_root: str,
) -> dict[str, Any]:
    props = obj.get("props") if isinstance(obj.get("props"), dict) else {}
    pointer_prefix = f"/scenes/{scene_index}/objects/{object_index}"
    editable_props = {key: value for key, value in props.items() if isinstance(value, (str, int, float, bool, list, dict))}

    prop_pointers = {
        key: f"{pointer_prefix}/props/{json_pointer_escape(str(key))}"
        for key in sorted(editable_props)
    }

    return {
        "record_type": "scene_object",
        "file": f"{allowed_root}project.json",
        "json_pointer": pointer_prefix,
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
            "props": editable_props,
        },
        "editable_json_pointers": {
            **{
                "x": f"{pointer_prefix}/x",
                "y": f"{pointer_prefix}/y",
                "width": f"{pointer_prefix}/width",
                "height": f"{pointer_prefix}/height",
            },
            **{f"props.{key}": pointer for key, pointer in prop_pointers.items()},
        },
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


def iter_project_files(project_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".") and path.name != ".gitkeep":
            continue
        files.append(path)
    return files


def build_game_evidence(repo: Path, project_root: Path, project_id: str, allowed_root: str) -> dict[str, Any]:
    project_file = project_root / "project.json"
    project_text = read_text(project_file)
    project = json.loads(project_text)

    scenes = project.get("scenes") if isinstance(project.get("scenes"), list) else []
    active_scene_id = str(project.get("activeSceneId") or "")
    active_scene_index = 0
    for index, scene in enumerate(scenes):
        if isinstance(scene, dict) and str(scene.get("id") or "") == active_scene_id:
            active_scene_index = index
            break

    records: list[dict[str, Any]] = []
    for scene_index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
        for object_index, obj in enumerate(objects):
            if not isinstance(obj, dict):
                continue
            records.append(
                project_record_for_object(
                    project_id=project_id,
                    scene_index=scene_index,
                    scene=scene,
                    object_index=object_index,
                    obj=obj,
                    allowed_root=allowed_root,
                )
            )
            if len(records) >= MAX_OBJECT_RECORDS:
                break
        if len(records) >= MAX_OBJECT_RECORDS:
            break

    file_inventory: list[dict[str, Any]] = []
    text_files: list[dict[str, Any]] = []
    for path in iter_project_files(project_root):
        rel_to_project = path.relative_to(project_root).as_posix()
        repo_rel = allowed_root + rel_to_project
        kind = rel_to_project.split("/", 1)[0] if "/" in rel_to_project else "manifest"
        file_record = {
            "path": repo_rel,
            "relative_to_project": rel_to_project,
            "kind": kind,
            "suffix": path.suffix.lower(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        file_inventory.append(file_record)

        if path.suffix.lower() in TEXT_FILE_EXTENSIONS and path.stat().st_size <= MAX_TEXT_FILE_CHARS:
            try:
                text = read_text(path)
            except UnicodeDecodeError:
                continue
            text_files.append(
                {
                    "path": repo_rel,
                    "kind": kind,
                    "sha256": sha256_text(text),
                    "content": text,
                }
            )

    scene_summaries: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
        type_counts: dict[str, int] = {}
        for obj in objects:
            if isinstance(obj, dict):
                obj_type = str(obj.get("type") or "unknown")
                type_counts[obj_type] = type_counts.get(obj_type, 0) + 1
        scene_summaries.append(
            {
                "id": str(scene.get("id") or ""),
                "name": str(scene.get("name") or ""),
                "index": index,
                "active": index == active_scene_index,
                "object_count": len(objects),
                "object_types": dict(sorted(type_counts.items())),
                "metadata": scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {},
            }
        )

    return {
        "mode": "game_editor_rag_evidence",
        "app": "game-editor",
        "target_kind": "game-project",
        "project_id": project_id,
        "project_path": allowed_root.rstrip("/"),
        "allowed_root": allowed_root,
        "write_policy": {
            "mode": "proposal-only",
            "writes_enabled": False,
            "auto_apply": False,
            "server_derived_allowed_root": True,
        },
        "project_manifest": {
            "path": allowed_root + "project.json",
            "sha256": sha256_text(project_text),
            "id": str(project.get("id") or project_id),
            "name": str(project.get("name") or project_id),
            "description": str(project.get("description") or ""),
            "version": project.get("version"),
            "activeSceneId": active_scene_id,
            "settings": project.get("settings") if isinstance(project.get("settings"), dict) else {},
        },
        "scenes": scene_summaries,
        "editable_object_records": records,
        "file_inventory": file_inventory,
        "text_files": text_files,
    }


def build_model_messages(prompt: str, evidence: dict[str, Any]) -> list[dict[str, str]]:
    system = """\
You are the proposal-only RAG edit planner for a mounted Game Editor.

Use only the supplied evidence. Do not claim hidden files, hidden texture data, or
engine behavior that is not in evidence.

You may infer likely targets from generic evidence fields: object id, label, role,
type, parentId, selected scene membership, editable props, file names, and literal
file content. Do not use hard-coded project-specific synonym rules.

Return JSON only with this shape:
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
      "exact_value": "exact old scalar/object value when relevant",
      "reason": "why this evidence supports the proposed edit"
    }
  ],
  "json_edits": [
    {
      "path": "repo-relative .json file path",
      "json_pointer": "/pointer/to/existing/value",
      "old_value": "exact current value at pointer",
      "new_value": "replacement value",
      "reason": "why this edit satisfies the prompt"
    }
  ],
  "text_replacements": [
    {
      "path": "repo-relative text file path",
      "old_text": "exact substring copied from that file",
      "new_text": "replacement substring",
      "reason": "why this edit satisfies the prompt"
    }
  ],
  "create_files": [
    {
      "path": "repo-relative path inside scripts/ or data/",
      "content": "complete file content",
      "reason": "why a new file is necessary"
    }
  ],
  "warnings": []
}

Rules:
- Prefer editing existing editable fields over inventing new metadata.
- For project.json object/scene edits, use json_edits with JSON pointers supplied
  by editable_object_records whenever possible.
- old_value must equal the current value at json_pointer.
- text replacement old_text must be copied exactly from evidence.
- All paths must stay inside allowed_root.
- Do not include Git operations, apply instructions, or mutation claims.
- Empty edit lists are allowed only when the evidence is insufficient; explain in warnings.
"""
    user_payload = {
        "runtime_user_prompt": prompt,
        "selected_game_editor_evidence": evidence,
        "validation_contract": [
            "The deterministic smoke will verify every path is inside allowed_root.",
            "The deterministic smoke will verify every json_edit old_value at json_pointer.",
            "The deterministic smoke will verify every text_replacement old_text is exact source text.",
            "The deterministic smoke will materialize full replacement files without modifying the source project.",
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, indent=2, sort_keys=True)},
    ]


def call_ollama_chat(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_seconds: float,
    num_predict: int,
    format_mode: str,
    think_mode: str,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": num_predict},
    }
    if format_mode == "json":
        payload["format"] = "json"
    if think_mode != "omit":
        payload["think"] = {"false": False, "true": True}.get(think_mode, think_mode)

    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"Ollama request failed at {base_url!r}: {exc}") from exc

    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise SmokeFailure("Ollama returned no message.content")
    return content


def parse_jsonish(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise SmokeFailure(f"Model response did not contain a JSON object: {raw[:500]!r}")
        payload = json.loads(raw[start : end + 1])
    require(isinstance(payload, dict), "Model JSON response must be an object")
    return payload


def offline_fixture_proposal(prompt: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """A deterministic fixture used only to self-check validators/materializers."""
    allowed_root = str(evidence["allowed_root"])
    records = evidence.get("editable_object_records") if isinstance(evidence.get("editable_object_records"), list) else []
    target = None
    # Generic-ish fixture selection: choose the first object with an editable color
    # and a label/id/role mentioned by the prompt, otherwise choose the first color.
    prompt_l = prompt.lower()
    for record in records:
        obj = record.get("object") if isinstance(record.get("object"), dict) else {}
        haystack = " ".join(
            str(value).lower()
            for value in [obj.get("id"), obj.get("type"), obj.get("parentId"), obj.get("props", {}).get("label"), obj.get("props", {}).get("role")]
            if value
        )
        if "props.color" in record.get("editable_json_pointers", {}) and any(token in haystack for token in re.findall(r"[a-z0-9_-]{3,}", prompt_l)):
            target = record
            break
    if target is None:
        target = next((record for record in records if "props.color" in record.get("editable_json_pointers", {})), None)
    require(target is not None, "offline fixture could not find any editable object color")

    pointer = target["editable_json_pointers"]["props.color"]
    old_value = target["object"]["props"]["color"]
    new_value = "#ff0000" if "red" in prompt_l else old_value

    return {
        "ok": True,
        "mode": "game_editor_rag_edit_proposal",
        "target_kind": "game-project",
        "target_id": evidence["project_id"],
        "allowed_root": allowed_root,
        "summary": f"Proposal only: update {target['object']['id']} color.",
        "grounding": [
            {
                "evidence_type": "scene_object",
                "path": allowed_root + "project.json",
                "json_pointer": target["json_pointer"],
                "exact_value": target["object"],
                "reason": "The editable object record exposes the object identity and current props.",
            }
        ],
        "json_edits": [
            {
                "path": allowed_root + "project.json",
                "json_pointer": pointer,
                "old_value": old_value,
                "new_value": new_value,
                "reason": "Color is an existing editable prop on the selected scene object.",
            }
        ],
        "text_replacements": [],
        "create_files": [],
        "warnings": [],
    }


def validate_payload_shape(payload: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if payload.get("ok") is not True:
        issues.append("proposal ok must be true")
    if payload.get("mode") != "game_editor_rag_edit_proposal":
        issues.append("mode must be game_editor_rag_edit_proposal")
    if payload.get("target_kind") != "game-project":
        issues.append("target_kind must be game-project")
    if payload.get("target_id") != evidence.get("project_id"):
        issues.append("target_id must equal evidence project_id")
    if payload.get("allowed_root") != evidence.get("allowed_root"):
        issues.append("allowed_root must equal evidence allowed_root")

    for key in ("grounding", "json_edits", "text_replacements", "create_files", "warnings"):
        if key not in payload:
            issues.append(f"missing key: {key}")
        elif not isinstance(payload.get(key), list):
            issues.append(f"{key} must be a list")
    if not isinstance(payload.get("summary"), str) or not payload.get("summary", "").strip():
        issues.append("summary must be non-empty")
    return issues


def source_file_map(evidence: dict[str, Any]) -> dict[str, str]:
    files: dict[str, str] = {}
    for item in evidence.get("text_files", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str):
            files[item["path"]] = item["content"]
    return files


def materialize_proposal(repo: Path, project_root: Path, evidence: dict[str, Any], proposal: dict[str, Any]) -> tuple[list[MaterializedFile], dict[str, Any]]:
    issues = validate_payload_shape(proposal, evidence)
    warnings: list[str] = []
    allowed_root = str(evidence["allowed_root"])

    replacements: dict[str, MaterializedFile] = {}
    source_texts = source_file_map(evidence)

    project_json_path = allowed_root + "project.json"
    project_doc = json.loads(source_texts[project_json_path])
    staged_json_docs: dict[str, Any] = {project_json_path: copy.deepcopy(project_doc)}

    for index, edit in enumerate(proposal.get("json_edits", [])):
        if not isinstance(edit, dict):
            issues.append(f"json_edits[{index}] must be an object")
            continue
        path = safe_relpath(str(edit.get("path") or ""))
        pointer = str(edit.get("json_pointer") or "")
        if not path or not path_inside_allowed(path, allowed_root):
            issues.append(f"json_edits[{index}] path is outside allowed_root: {edit.get('path')!r}")
            continue
        if not path.endswith(".json"):
            issues.append(f"json_edits[{index}] path must be a .json file: {path}")
            continue
        if path not in source_texts:
            issues.append(f"json_edits[{index}] path is not in text evidence: {path}")
            continue

        if path not in staged_json_docs:
            try:
                staged_json_docs[path] = json.loads(source_texts[path])
            except json.JSONDecodeError as exc:
                issues.append(f"json_edits[{index}] target is not valid JSON: {path}: {exc}")
                continue

        old_expected = normalize_model_value(edit.get("old_value"))
        new_value = normalize_model_value(edit.get("new_value"))
        try:
            current = json_pointer_get(staged_json_docs[path], pointer)
        except SmokeFailure as exc:
            issues.append(f"json_edits[{index}] invalid pointer: {exc}")
            continue

        if current != old_expected:
            issues.append(
                f"json_edits[{index}] old_value mismatch at {path}{pointer}: "
                f"expected {old_expected!r}, found {current!r}"
            )
            continue
        json_pointer_set(staged_json_docs[path], pointer, new_value)

    for path, doc in staged_json_docs.items():
        original = source_texts[path]
        replacement = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        if replacement != original:
            replacements[path] = MaterializedFile(
                path=path,
                operation="modify",
                original_sha256=sha256_text(original),
                replacement_sha256=sha256_text(replacement),
                replacement_text=replacement,
            )

    for index, repl in enumerate(proposal.get("text_replacements", [])):
        if not isinstance(repl, dict):
            issues.append(f"text_replacements[{index}] must be an object")
            continue
        path = safe_relpath(str(repl.get("path") or ""))
        if not path or not path_inside_allowed(path, allowed_root):
            issues.append(f"text_replacements[{index}] path is outside allowed_root: {repl.get('path')!r}")
            continue
        old_text = repl.get("old_text")
        new_text = repl.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            issues.append(f"text_replacements[{index}] old_text must be a non-empty string")
            continue
        if not isinstance(new_text, str):
            issues.append(f"text_replacements[{index}] new_text must be a string")
            continue
        if path not in source_texts:
            issues.append(f"text_replacements[{index}] path is not in text evidence: {path}")
            continue
        original = replacements[path].replacement_text if path in replacements else source_texts[path]
        count = original.count(old_text)
        if count != 1:
            issues.append(f"text_replacements[{index}] old_text occurrence count must be 1 in {path}; found {count}")
            continue
        replacement = original.replace(old_text, new_text, 1)
        replacements[path] = MaterializedFile(
            path=path,
            operation="modify",
            original_sha256=sha256_text(source_texts[path]),
            replacement_sha256=sha256_text(replacement),
            replacement_text=replacement,
        )

    for index, create in enumerate(proposal.get("create_files", [])):
        if not isinstance(create, dict):
            issues.append(f"create_files[{index}] must be an object")
            continue
        path = safe_relpath(str(create.get("path") or ""))
        if not path or not path_inside_allowed(path, allowed_root):
            issues.append(f"create_files[{index}] path is outside allowed_root: {create.get('path')!r}")
            continue
        rel_to_game = path.removeprefix(allowed_root).lstrip("/")
        folder = rel_to_game.split("/", 1)[0] if "/" in rel_to_game else ""
        if folder not in {"scripts", "data"}:
            issues.append(f"create_files[{index}] may only create under scripts/ or data/: {path}")
            continue
        if path in source_texts or (repo / path).exists():
            issues.append(f"create_files[{index}] target already exists; use text_replacements/json_edits instead: {path}")
            continue
        content = create.get("content")
        if not isinstance(content, str) or not content.strip():
            issues.append(f"create_files[{index}] content must be non-empty string")
            continue
        replacements[path] = MaterializedFile(
            path=path,
            operation="create",
            original_sha256=None,
            replacement_sha256=sha256_text(content),
            replacement_text=content if content.endswith("\n") else content + "\n",
        )

    if not replacements and not issues:
        warnings.append("Proposal was valid but materialized no file changes.")

    return list(replacements.values()), {"ok": not issues, "issues": issues, "warnings": warnings}


def unified_diff_for_file(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def write_outputs(output_dir: Path, evidence: dict[str, Any], proposal: dict[str, Any], materialized: list[MaterializedFile], report: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "evidence.json", evidence)
    write_json(output_dir / "proposal.json", proposal)

    source_texts = source_file_map(evidence)
    manifest_files: list[dict[str, Any]] = []
    diff_parts: list[str] = []

    for item in materialized:
        dest = output_dir / "files" / item.path
        write_text(dest, item.replacement_text)
        before = source_texts.get(item.path, "")
        if item.operation == "create":
            before = ""
        diff_parts.append(unified_diff_for_file(item.path, before, item.replacement_text))
        manifest_files.append(
            {
                "path": item.path,
                "operation": item.operation,
                "original_sha256": item.original_sha256,
                "replacement_sha256": item.replacement_sha256,
                "payload": f"files/{item.path}",
            }
        )

    manifest = {
        "mode": MODE,
        "artifact_type": "proposal_only_full_replacement_payloads",
        "project_id": evidence["project_id"],
        "allowed_root": evidence["allowed_root"],
        "auto_apply": False,
        "files": manifest_files,
    }
    write_json(output_dir / "manifest.json", manifest)
    write_text(output_dir / "reference.patch", "\n".join(part for part in diff_parts if part) or "")
    write_json(output_dir / "report.json", report)
    return {"output_dir": str(output_dir), "manifest": str(output_dir / "manifest.json"), "reference_patch": str(output_dir / "reference.patch")}



def copy_project_to_apply_root(repo: Path, project_root: Path, allowed_root: str, output_dir: Path) -> Path:
    apply_root = output_dir / "applied_repo"
    destination = apply_root / allowed_root.rstrip("/")
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(project_root, destination)
    return apply_root


def apply_materialized_files(
    *,
    repo: Path,
    project_root: Path,
    allowed_root: str,
    output_dir: Path,
    materialized: list[MaterializedFile],
    apply_mode: str,
) -> dict[str, Any]:
    """Apply validated replacements and verify the final bytes.

    apply_mode:
      none      -> do not write replacements anywhere
      temp-copy -> copy the active game project under output_dir/applied_repo and write there
      live      -> write to the source repo after original-sha checks and backup creation
    """
    result: dict[str, Any] = {
        "ok": False,
        "mode": apply_mode,
        "allowed_root": allowed_root,
        "files": [],
        "issues": [],
        "warnings": [],
    }

    if apply_mode == "none":
        result["ok"] = True
        result["warnings"].append("Apply disabled; materialized payloads were not written.")
        return result

    if apply_mode == "temp-copy":
        apply_root = copy_project_to_apply_root(repo, project_root, allowed_root, output_dir)
        result["apply_root"] = str(apply_root)
    elif apply_mode == "live":
        apply_root = repo
        backup_root = output_dir / "live_backups"
        result["apply_root"] = str(apply_root)
        result["backup_root"] = str(backup_root)
    else:
        result["issues"].append(f"Unknown apply mode: {apply_mode!r}")
        return result

    for item in materialized:
        safe_path = safe_relpath(item.path)
        if not safe_path or not path_inside_allowed(safe_path, allowed_root):
            result["issues"].append(f"Unsafe materialized path: {item.path!r}")
            continue

        target = (apply_root / safe_path).resolve()
        try:
            target.relative_to(apply_root.resolve())
        except ValueError:
            result["issues"].append(f"Resolved path escapes apply root: {target}")
            continue

        before_text: str | None = None
        if item.operation == "modify":
            if not target.is_file():
                result["issues"].append(f"Modify target is missing: {safe_path}")
                continue
            before_text = read_text(target)
            before_sha = sha256_text(before_text)
            if item.original_sha256 and before_sha != item.original_sha256:
                result["issues"].append(
                    f"Original sha mismatch before apply for {safe_path}: expected {item.original_sha256}, found {before_sha}"
                )
                continue
            if apply_mode == "live":
                backup_path = output_dir / "live_backups" / safe_path
                write_text(backup_path, before_text)
        elif item.operation == "create":
            if target.exists():
                result["issues"].append(f"Create target already exists: {safe_path}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
        else:
            result["issues"].append(f"Unsupported operation for {safe_path}: {item.operation!r}")
            continue

        write_text(target, item.replacement_text)
        after_text = read_text(target)
        after_sha = sha256_text(after_text)
        if after_sha != item.replacement_sha256:
            result["issues"].append(
                f"Replacement sha mismatch after apply for {safe_path}: expected {item.replacement_sha256}, found {after_sha}"
            )
            continue

        result["files"].append(
            {
                "path": safe_path,
                "operation": item.operation,
                "original_sha256": item.original_sha256,
                "replacement_sha256": item.replacement_sha256,
                "written_sha256": after_sha,
            }
        )

    result["ok"] = not result["issues"] and len(result["files"]) == len(materialized)
    return result


def infer_apply_mode(args: argparse.Namespace) -> str:
    if getattr(args, "no_apply", False):
        return "none"
    if getattr(args, "apply_live", False):
        require(
            getattr(args, "yes_really_write_source", False),
            "--apply-live requires --yes-really-write-source to prevent accidental source edits.",
        )
        return "live"
    return "temp-copy"

def default_output_dir(repo: Path, project_id: str) -> Path:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    return repo / "diagnostics_output" / "game_editor_rag_real_edit_smoke" / f"{project_id}_{run_id}"


def read_prompt(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.prompt:
        parts.append(args.prompt)
    if args.prompt_file:
        parts.append(Path(args.prompt_file).read_text(encoding="utf-8"))
    if args.positional_prompt:
        parts.append(" ".join(args.positional_prompt))
    prompt = "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()
    if not prompt and not args.offline_self_check:
        raise SmokeFailure("A prompt is required. Pass --prompt, --prompt-file, or a positional prompt.")
    return prompt or "change the first editable character color red"


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    repo = detect_repo_root_arg(args.repo)
    prompt = read_prompt(args)
    project_id, project_root, allowed_root = project_path_from_args(repo, args)
    evidence = build_game_evidence(repo, project_root, project_id, allowed_root)

    messages = build_model_messages(prompt, evidence)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(repo, project_id)

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

    write_json(output_dir / "prompt.json", {"messages": messages, "prompt": prompt})

    if args.offline_self_check:
        proposal = offline_fixture_proposal(prompt, evidence)
        raw_preview = json.dumps(proposal, indent=2, sort_keys=True)[:1000]
        model_info = {"offline_fixture": True}
    else:
        base_url = args.ollama_url or os.environ.get("MAIN_COMPUTER_OLLAMA_URL") or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        model = args.model or os.environ.get("MAIN_COMPUTER_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        started = time.time()
        raw = call_ollama_chat(
            base_url=base_url,
            model=model,
            messages=messages,
            timeout_seconds=args.timeout_seconds,
            num_predict=args.num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
        )
        proposal = parse_jsonish(raw)
        raw_preview = raw[:1200]
        model_info = {
            "offline_fixture": False,
            "model": model,
            "ollama_url": base_url,
            "elapsed_seconds": round(time.time() - started, 3),
        }

    materialized, validation = materialize_proposal(repo, project_root, evidence, proposal)
    report["checks"]["model_or_fixture"] = {**model_info, "raw_response_preview": raw_preview}
    report["checks"]["proposal_validation"] = validation
    report["checks"]["materialized_payloads"] = {
        "ok": validation["ok"],
        "count": len(materialized),
        "files": [
            {
                "path": item.path,
                "operation": item.operation,
                "original_sha256": item.original_sha256,
                "replacement_sha256": item.replacement_sha256,
            }
            for item in materialized
        ],
    }

    apply_mode = infer_apply_mode(args)
    apply_report = {"ok": False, "mode": apply_mode, "skipped": True}
    if validation["ok"]:
        apply_report = apply_materialized_files(
            repo=repo,
            project_root=project_root,
            allowed_root=allowed_root,
            output_dir=output_dir,
            materialized=materialized,
            apply_mode=apply_mode,
        )
    report["checks"]["apply"] = apply_report

    outputs = write_outputs(output_dir, evidence, proposal, materialized, report)
    report["outputs"] = {
        **outputs,
        "applied_repo": str(output_dir / "applied_repo") if apply_mode == "temp-copy" else None,
        "live_backups": str(output_dir / "live_backups") if apply_mode == "live" else None,
    }

    require(validation["ok"], f"Proposal failed deterministic validation: {validation}")
    require(apply_report.get("ok") is True, f"Apply/post-apply verification failed: {apply_report}")
    report["ok"] = True
    write_json(output_dir / "report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Game Editor RAG golden-path real-edit smoke.")
    parser.add_argument("positional_prompt", nargs="*", help="Runtime user prompt, if --prompt/--prompt-file is not used.")
    parser.add_argument("--repo", default=None, help="Repo root. Defaults to walking up from cwd.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID, help="Game project id under game_projects/.")
    parser.add_argument("--project-path", default=None, help="Repo-relative path to a game project directory.")
    parser.add_argument("--prompt", default=None, help="Runtime user prompt.")
    parser.add_argument("--prompt-file", default=None, help="Read runtime user prompt from a UTF-8 text file.")
    parser.add_argument("--output-dir", default=None, help="Directory for report/proposal/full replacement payloads.")
    parser.add_argument("--offline-self-check", action="store_true", help="Use a deterministic fixture proposal instead of calling Ollama.")
    parser.add_argument("--ollama-url", default=None, help="Ollama base URL.")
    parser.add_argument("--model", default=None, help="Ollama model.")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--num-predict", type=int, default=2200)
    parser.add_argument("--format-mode", choices=["json", "none"], default="json")
    parser.add_argument("--think-mode", choices=["omit", "false", "true", "low", "medium", "high"], default="false")
    parser.add_argument("--no-apply", action="store_true", help="Stop after validation/materialization; do not apply anywhere.")
    parser.add_argument("--apply-live", action="store_true", help="Apply validated replacements to the live repo instead of an isolated temp copy.")
    parser.add_argument("--yes-really-write-source", action="store_true", help="Required with --apply-live.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = evaluate(args)
    except Exception as exc:
        report = {
            "ok": False,
            "mode": MODE,
            "failed_stage": type(exc).__name__,
            "error": str(exc),
        }
    json_print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
