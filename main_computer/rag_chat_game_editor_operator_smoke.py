#!/usr/bin/env python3
"""
Game Editor chat/RAG operator smoke.

This is the Game Editor counterpart to the Website Editor operator smoke. It
checks that the Game Assistant handoff is scoped to one game project, receives a
grounded read-only project context, keeps the thread-rail chat embed enabled,
and does not create any patch/apply/Git mutation path.

Typical PowerShell runs from the repo root:

    python .\tools\rag_chat_game_editor_operator_smoke.py --skip-ai --prompt "What is in this game project?"
    python .\tools\rag_chat_game_editor_operator_smoke.py --prompt "Add a jump script to the player capsule"

The first command verifies the local route/static/API contract only. The second
also asks the configured local Ollama model to produce a read-only Game Editor
chat handoff response and validates that it does not propose file operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SMOKE_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
SMOKE_DEFAULT_OLLAMA_MODEL = "gemma4:26b"
DEFAULT_PROJECT_ID = "webgl-demo"
MODE = "chat_app_game_editor_read_only_operator_smoke"

MUTATION_WORDS = re.compile(
    r"\b(add|apply|change|commit|create|delete|edit|fix|modify|patch|publish|remove|rename|save|update|write)\b",
    re.IGNORECASE,
)


class SmokeFailure(RuntimeError):
    pass


def repo_root() -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve())
    except NameError:
        pass
    candidates.append(Path.cwd().resolve())

    for start in candidates:
        for candidate in [start, *start.parents]:
            if (
                (candidate / "main_computer").is_dir()
                and (candidate / "tests").is_dir()
                and (candidate / "game_projects").is_dir()
            ):
                return candidate

    raise SmokeFailure("Could not locate repo root containing main_computer/, tests/, and game_projects/.")


def add_repo_to_path(root: Path) -> None:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)


def json_print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def require_contains(label: str, text: str, needle: str) -> None:
    if needle not in text:
        raise SmokeFailure(f"{label} is missing expected text: {needle!r}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_tree(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            hashes[rel] = sha256_bytes(path.read_bytes())
    return hashes


def copy_game_projects(repo: Path, workspace: Path) -> Path:
    src = repo / "game_projects"
    dst = workspace / "game_projects"
    require(src.is_dir(), f"Missing repo game_projects directory: {src}")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def get_text(base_url: str, path: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(base_url + path, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def start_viewport(root: Path, workspace: Path):
    add_repo_to_path(root)
    from main_computer.config import MainComputerConfig
    from main_computer.viewport import ViewportServer

    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=workspace), verbose=False)
    server.debug_root = workspace
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return server, thread, base_url


def stop_viewport(server: Any, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def run_node_check(root: Path) -> dict[str, Any]:
    script = root / "main_computer" / "web" / "applications" / "scripts" / "game-editor.js"
    if not script.exists():
        return {"ok": False, "skipped": False, "error": f"Missing {script}"}
    try:
        completed = subprocess.run(
            ["node", "--check", str(script)],
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": True, "skipped": True, "reason": "node executable was not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "skipped": False, "error": "node --check timed out"}

    return {
        "ok": completed.returncode == 0,
        "skipped": False,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }


def static_game_editor_contract(root: Path) -> dict[str, Any]:
    js_path = root / "main_computer" / "web" / "applications" / "scripts" / "game-editor.js"
    css_path = root / "main_computer" / "web" / "applications" / "styles" / "game-editor.css"
    js = read_text(js_path)
    css = read_text(css_path)

    expected_js = [
        'data-chat-console-embed="game-editor"',
        'data-chat-console-active-app="game-editor"',
        'data-chat-console-target-kind="game-project"',
        'data-chat-console-layout="full"',
        'data-chat-console-show-thread-rail="1"',
        'data-chat-console-show-current-thread-bar="1"',
        'layout: "full"',
        "showThreadRail: true",
        "showCurrentThreadBar: true",
        "getEmbeddedContext: gameEditorChatContextSnapshot",
        'app: "game-editor"',
        'target_kind: "game-project"',
        'edit_mode: "read-only-context"',
        "mutation_allowed: false",
        'target_policy: "selected_game_project_only"',
        'phase: "ui-context-only"',
        "allowed_paths:",
        "/api/applications/game-editor/project/read",
        "/api/applications/game-editor/project/write",
        "/api/applications/game-editor/asset/upload",
        "window.MainComputerGameEditorContext",
    ]
    for needle in expected_js:
        require_contains("game-editor.js", js, needle)

    expected_css = [
        ".game-editor-chat-popout",
        "width: min(980px, calc(100% - 28px))",
        ".game-editor-chat-panel .chat-console-thread-layout",
        ".game-editor-chat-panel .chat-thread-workspace",
        ".game-editor-chat-panel .chat-console-notebook",
    ]
    for needle in expected_css:
        require_contains("game-editor.css", css, needle)

    forbidden_js = [
        "new_patch.py",
        "reference.patch",
        "git commit",
        "commit_site_change",
        "run_new_patch",
    ]
    found_forbidden = [needle for needle in forbidden_js if needle in js]
    require(not found_forbidden, f"game-editor.js contains mutation pipeline text: {found_forbidden}")

    return {
        "ok": True,
        "script": str(js_path.relative_to(root)),
        "style": str(css_path.relative_to(root)),
        "thread_rail": "enabled",
        "layout": "full",
        "popout_width": "980px",
        "mutation_pipeline_text_found": found_forbidden,
    }


def route_contract(html: str) -> dict[str, Any]:
    expected = [
        'data-app="game-editor"',
        'id="game-editor-app"',
        'id="game-editor-preview"',
        'id="game-editor-webgl-canvas"',
        'id="game-editor-chat-toggle"',
        'aria-controls="game-editor-chat-popout"',
        'id="game-editor-chat-popout"',
        'id="game-editor-chat-panel"',
        'data-chat-console-embed="game-editor"',
        'data-chat-console-target-kind="game-project"',
        'data-chat-console-layout="full"',
        'data-chat-console-show-thread-rail="1"',
        'data-chat-console-show-current-thread-bar="1"',
        "Game Assistant",
        "function setGameEditorChatOpen",
        "window.MainComputerGameEditorContext",
        "getEmbeddedContext: gameEditorChatContextSnapshot",
    ]
    for needle in expected:
        require_contains("/applications/game-editor", html, needle)

    game_panel = re.search(r'<aside\s+[^>]*id="game-editor-chat-panel"[^>]*>', html, re.S)
    require(game_panel is not None, "Game Editor chat panel markup was not found.")
    game_panel_markup = game_panel.group(0)
    require('data-chat-console-layout="compact"' not in game_panel_markup, "Game Editor route still advertises compact chat layout.")
    require('data-chat-console-show-thread-rail="0"' not in game_panel_markup, "Game Editor route still hides the chat thread rail.")

    return {"ok": True, "checked": len(expected)}


def load_project(base_url: str, project_id: str) -> dict[str, Any]:
    projects = post_json(base_url, "/api/applications/game-editor/projects", {})
    require(projects.get("ok") is True, "projects API did not return ok=true")
    ids = [project.get("id") for project in projects.get("projects", [])]
    require(project_id in ids, f"Project {project_id!r} was not listed by the Game Editor projects API: {ids!r}")

    data = post_json(base_url, "/api/applications/game-editor/project/read", {"project_id": project_id})
    require(data.get("ok") is True, "project/read API did not return ok=true")
    require(isinstance(data.get("project"), dict), "project/read API did not return a project object")
    return data


def summarize_project_context(project_data: dict[str, Any], project_id: str) -> dict[str, Any]:
    project = project_data["project"]
    active_scene_id = str(project.get("activeSceneId") or "default-empty-scene")
    scenes = project.get("scenes") if isinstance(project.get("scenes"), list) else []
    active_scene = None
    for scene in scenes:
        if str(scene.get("id", "")) == active_scene_id:
            active_scene = scene
            break
    if active_scene is None and scenes:
        active_scene = scenes[0]
        active_scene_id = str(active_scene.get("id") or active_scene_id)

    objects = active_scene.get("objects", []) if isinstance(active_scene, dict) else []
    if not isinstance(objects, list):
        objects = []

    assets = project_data.get("assets") if isinstance(project_data.get("assets"), list) else project.get("assets", [])
    if not isinstance(assets, list):
        assets = []

    scripts = project.get("scripts", [])
    if not isinstance(scripts, list):
        scripts = []

    project_path = f"game_projects/{project_id}"
    return {
        "app": "game-editor",
        "target_kind": "game-project",
        "target_id": project_id,
        "project_id": project_id,
        "project_path": project_path,
        "allowed_root": project_path,
        "allowed_paths": [
            f"{project_path}/project.json",
            f"{project_path}/scripts/**",
            f"{project_path}/data/**",
            f"{project_path}/assets/**",
        ],
        "edit_mode": "read-only-context",
        "content_hash": str(project_data.get("content_hash") or ""),
        "active_scene_id": active_scene_id,
        "project": {
            "id": project_id,
            "name": str(project.get("name") or project_id),
            "description": str(project.get("description") or ""),
            "scene_count": len(scenes),
        },
        "scenes": [
            {
                "id": str(scene.get("id", "")),
                "name": str(scene.get("name", "")),
                "active": str(scene.get("id", "")) == active_scene_id,
                "object_count": len(scene.get("objects", [])) if isinstance(scene.get("objects"), list) else 0,
            }
            for scene in scenes
            if isinstance(scene, dict)
        ],
        "active_scene": {
            "id": str(active_scene.get("id", "")) if isinstance(active_scene, dict) else "",
            "name": str(active_scene.get("name", "")) if isinstance(active_scene, dict) else "",
            "object_count": len(objects),
            "background": active_scene.get("background") if isinstance(active_scene, dict) else None,
            "objects": [
                {
                    "id": str(obj.get("id", "")),
                    "type": str(obj.get("type", "")),
                    "x": obj.get("x"),
                    "y": obj.get("y"),
                    "width": obj.get("width"),
                    "height": obj.get("height"),
                    "props": obj.get("props") if isinstance(obj.get("props"), dict) else {},
                }
                for obj in objects[:12]
                if isinstance(obj, dict)
            ],
        },
        "assets": [
            {
                "name": str(asset.get("name", "")) if isinstance(asset, dict) else str(asset),
                "path": str(asset.get("path") or asset.get("name") or "") if isinstance(asset, dict) else str(asset),
                "kind": str(asset.get("kind", "asset")) if isinstance(asset, dict) else "asset",
            }
            for asset in assets[:24]
        ],
        "scripts": [
            (
                {"path": script}
                if isinstance(script, str)
                else {
                    "name": str(script.get("name", "")),
                    "path": str(script.get("path") or script.get("name") or ""),
                    "kind": str(script.get("kind", "script")),
                }
            )
            for script in scripts[:24]
        ],
        "guardrails": {
            "target_policy": "selected_game_project_only",
            "phase": "ui-context-only",
            "mutation_allowed": False,
        },
    }


def read_prompt(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.prompt:
        parts.append(args.prompt)
    if args.prompt_file:
        parts.append(Path(args.prompt_file).read_text(encoding="utf-8"))
    if args.positional_prompt:
        parts.append(" ".join(args.positional_prompt))
    prompt = "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()
    if not prompt:
        raise SmokeFailure("A prompt is required. Pass --prompt, --prompt-file, or a positional prompt.")
    return prompt


def build_game_editor_messages(prompt: str, context: dict[str, Any]) -> list[dict[str, str]]:
    system = """\
You are the Game Editor chat handoff layer inside Main Computer.

You are not allowed to edit files, call patch tools, call Git, write project JSON,
upload assets, or claim that an edit was applied. Phase 1 is read-only project
context only.

You may answer questions about the selected game project, active scene, selected
entity, scripts, and assets using only the supplied context. If the user asks for
a change, you may describe a safe implementation plan or manual UI steps, but
you must clearly preserve mutation_allowed=false and proposed operations must be
empty.

Return JSON only with this exact shape:
{
  "ok": true,
  "mode": "game_editor_read_only_chat_handoff",
  "target_kind": "game-project",
  "target_id": "<exact supplied project_id>",
  "project_path": "<exact supplied project_path>",
  "mutation_allowed": false,
  "proposed_file_operations": [],
  "proposed_git_operations": [],
  "answer": "<grounded answer for the user>",
  "cited_context": ["<project/scene/object/asset/script ids or names used>"],
  "refusal_reason": ""
}
"""
    user = {
        "runtime_user_prompt": prompt,
        "selected_game_editor_context": context,
        "validation_notes": [
            "Use only selected_game_editor_context.",
            "Do not broaden scope beyond this one game project.",
            "Do not propose or perform file operations.",
            "Do not propose or perform Git operations.",
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, indent=2, sort_keys=True)},
    ]


def ollama_generate_url_from_base(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/chat"


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
        ollama_generate_url_from_base(base_url),
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
        raise SmokeFailure(
            f"Ollama returned no message.content. Raw keys: "
            f"{sorted(data.keys()) if isinstance(data, dict) else type(data).__name__}"
        )
    return content


def parse_jsonish(text: str) -> dict[str, Any]:
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise SmokeFailure(f"Model response did not contain a JSON object: {text[:500]!r}")
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise SmokeFailure("Model JSON response was not an object.")
    return data


def validate_model_payload(payload: dict[str, Any], context: dict[str, Any], prompt: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    expected_target_id = context["project_id"]
    expected_project_path = context["project_path"]

    if payload.get("mode") != "game_editor_read_only_chat_handoff":
        errors.append("mode must be game_editor_read_only_chat_handoff")
    if payload.get("target_kind") != "game-project":
        errors.append("target_kind must be game-project")
    if payload.get("target_id") != expected_target_id:
        errors.append(f"target_id must be {expected_target_id!r}")
    if payload.get("project_path") != expected_project_path:
        errors.append(f"project_path must be {expected_project_path!r}")
    if payload.get("mutation_allowed") is not False:
        errors.append("mutation_allowed must be false")

    file_ops = payload.get("proposed_file_operations")
    git_ops = payload.get("proposed_git_operations")
    if file_ops not in ([], None):
        errors.append("proposed_file_operations must be empty")
    if git_ops not in ([], None):
        errors.append("proposed_git_operations must be empty")

    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer must be a non-empty string")
        answer = ""
    else:
        applied_claims = [
            "i changed",
            "i updated",
            "i committed",
            "i applied",
            "patch created",
            "file has been",
            "saved the project",
        ]
        lowered = answer.lower()
        found_claims = [claim for claim in applied_claims if claim in lowered]
        if found_claims:
            errors.append(f"answer appears to claim a mutation happened: {found_claims}")

    cited_context = payload.get("cited_context")
    if not isinstance(cited_context, list) or not cited_context:
        warnings.append("cited_context is empty or not a list")
        cited_context_values: list[str] = []
    else:
        cited_context_values = [str(item) for item in cited_context]

    grounding_needles = {
        context["project_id"],
        context["project"]["name"],
        context["active_scene_id"],
    }
    active_scene = context.get("active_scene")
    if isinstance(active_scene, dict):
        grounding_needles.add(str(active_scene.get("name", "")))
        for obj in active_scene.get("objects", [])[:4]:
            if isinstance(obj, dict):
                grounding_needles.add(str(obj.get("id", "")))
                grounding_needles.add(str(obj.get("type", "")))

    answer_and_citations = " ".join([answer, *cited_context_values]).lower()
    grounded = any(needle and str(needle).lower() in answer_and_citations for needle in grounding_needles)
    if not grounded:
        errors.append("answer/cited_context did not reference supplied project, scene, or object context")

    if MUTATION_WORDS.search(prompt):
        mutation_disclaimer = any(
            phrase in answer.lower()
            for phrase in [
                "read-only",
                "can't edit",
                "cannot edit",
                "can’t edit",
                "not allowed to edit",
                "mutation_allowed=false",
                "phase 1",
            ]
        )
        if not mutation_disclaimer:
            errors.append("mutation-style prompt did not produce a read-only/Phase 1 disclaimer")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Game Editor read-only chat/RAG operator smoke.")
    parser.add_argument("positional_prompt", nargs="*", help="Runtime user prompt, if --prompt/--prompt-file is not used.")
    parser.add_argument("--prompt", default=None, help="Runtime user prompt to send through the Game Editor chat handoff.")
    parser.add_argument("--prompt-file", default=None, help="Read the runtime user prompt from a UTF-8 text file.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID, help="Game project id to load from the Game Editor API.")
    parser.add_argument("--skip-ai", action="store_true", help="Verify route/static/API contracts only; do not call Ollama.")
    parser.add_argument("--ollama-url", default=None, help="Ollama base URL. Defaults to env or http://127.0.0.1:11434.")
    parser.add_argument("--model", default=None, help="Ollama model. Defaults to env or gemma4:26b.")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--ai-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--num-predict", type=int, default=900)
    parser.add_argument("--format-mode", choices=["json", "none"], default="json")
    parser.add_argument("--think-mode", choices=["omit", "false", "true", "low", "medium", "high"], default="false")
    parser.add_argument("--quiet", action="store_true")
    return parser


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    prompt = read_prompt(args)
    root = repo_root()
    add_repo_to_path(root)

    report: dict[str, Any] = {
        "ok": False,
        "mode": MODE,
        "repo_root": str(root),
        "project_id": args.project_id,
        "skip_ai": bool(args.skip_ai),
        "checks": {},
    }

    static_report = static_game_editor_contract(root)
    report["checks"]["static_game_editor_contract"] = static_report

    node_report = run_node_check(root)
    report["checks"]["node_check"] = node_report
    require(node_report.get("ok") is True, f"node --check failed: {node_report}")

    with tempfile.TemporaryDirectory(prefix="mc-game-editor-smoke-") as tmp:
        workspace = Path(tmp)
        game_projects_root = copy_game_projects(root, workspace)
        before_hashes = hash_tree(game_projects_root)

        server = None
        thread = None
        try:
            server, thread, base_url = start_viewport(root, workspace)
            route_html = get_text(base_url, "/applications/game-editor", timeout=args.timeout_seconds)
            report["checks"]["route_contract"] = route_contract(route_html)

            project_data = load_project(base_url, args.project_id)
            context = summarize_project_context(project_data, args.project_id)
            require(context["guardrails"]["mutation_allowed"] is False, "Summarized context did not preserve mutation_allowed=false")
            require(context["edit_mode"] == "read-only-context", "Summarized context did not preserve read-only edit mode")
            report["checks"]["project_context"] = {
                "ok": True,
                "target_kind": context["target_kind"],
                "target_id": context["target_id"],
                "project_name": context["project"]["name"],
                "active_scene_id": context["active_scene_id"],
                "object_count": context["active_scene"]["object_count"],
                "mutation_allowed": context["guardrails"]["mutation_allowed"],
            }

            if not args.skip_ai:
                ollama_url = (
                    args.ollama_url
                    or os.environ.get("MAIN_COMPUTER_OLLAMA_URL")
                    or os.environ.get("OLLAMA_BASE_URL")
                    or SMOKE_DEFAULT_OLLAMA_BASE_URL
                )
                model = (
                    args.model
                    or os.environ.get("MAIN_COMPUTER_OLLAMA_MODEL")
                    or os.environ.get("OLLAMA_MODEL")
                    or SMOKE_DEFAULT_OLLAMA_MODEL
                )
                messages = build_game_editor_messages(prompt, context)
                started = time.time()
                raw = call_ollama_chat(
                    base_url=ollama_url,
                    model=model,
                    messages=messages,
                    timeout_seconds=args.ai_timeout_seconds,
                    num_predict=args.num_predict,
                    format_mode=args.format_mode,
                    think_mode=args.think_mode,
                )
                payload = parse_jsonish(raw)
                validation = validate_model_payload(payload, context, prompt)
                report["checks"]["ollama_game_editor_handoff"] = {
                    "ok": validation["ok"],
                    "model": model,
                    "ollama_url": ollama_url,
                    "elapsed_seconds": round(time.time() - started, 3),
                    "validation": validation,
                    "payload": payload,
                    "raw_response_preview": raw[:1000],
                }
                require(validation["ok"], f"Model payload failed validation: {validation}")

            after_hashes = hash_tree(game_projects_root)
            changed = {
                path: {"before": before_hashes.get(path), "after": after_hashes.get(path)}
                for path in sorted(set(before_hashes) | set(after_hashes))
                if before_hashes.get(path) != after_hashes.get(path)
            }
            report["checks"]["no_game_project_mutation"] = {"ok": not changed, "changed_files": changed}
            require(not changed, f"Smoke mutated game project files: {sorted(changed)}")

        finally:
            if server is not None and thread is not None:
                stop_viewport(server, thread)

    report["ok"] = True
    return report


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