from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_GAME_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GAME_INCLUDE_RE = re.compile(r"^[ \t]*<!--\s*@include\s+([^>]+?)\s*-->", re.MULTILINE)
DEFAULT_GAME_ID = "webgl-demo"
GAME_MANIFEST_SCHEMA = "main-computer-game-web.v1"


class GameWebLoaderError(RuntimeError):
    pass


def selected_game_id() -> str:
    value = os.environ.get("MAIN_COMPUTER_GAME_ID", DEFAULT_GAME_ID).strip() or DEFAULT_GAME_ID
    if not _GAME_ID_RE.fullmatch(value):
        raise GameWebLoaderError(f"Invalid game id: {value!r}")
    return value


def _game_root(repository_root: Path, game_id: str) -> Path:
    if not _GAME_ID_RE.fullmatch(game_id):
        raise GameWebLoaderError(f"Invalid game id: {game_id!r}")
    root = (repository_root / "game_projects" / game_id).resolve()
    games_root = (repository_root / "game_projects").resolve()
    try:
        root.relative_to(games_root)
    except ValueError as exc:
        raise GameWebLoaderError("Game root escaped repository game_projects directory.") from exc
    return root


def load_game_manifest(repository_root: Path, game_id: str | None = None) -> dict[str, Any]:
    chosen = game_id or selected_game_id()
    root = _game_root(repository_root, chosen)
    manifest_path = root / "game.json"
    if not manifest_path.is_file():
        raise GameWebLoaderError(f"Game manifest not found: game_projects/{chosen}/game.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GameWebLoaderError(f"Could not read game manifest for {chosen}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise GameWebLoaderError(f"Game manifest for {chosen} must be a JSON object.")
    if manifest.get("schema") != GAME_MANIFEST_SCHEMA:
        raise GameWebLoaderError(
            f"Unsupported game manifest schema for {chosen}: {manifest.get('schema')!r}"
        )
    if manifest.get("gameId") != chosen:
        raise GameWebLoaderError(
            f"Game manifest id mismatch: requested {chosen!r}, manifest has {manifest.get('gameId')!r}."
        )
    web = manifest.get("web")
    if not isinstance(web, dict) or not isinstance(web.get("bundles"), dict):
        raise GameWebLoaderError(f"Game manifest for {chosen} is missing web.bundles.")
    return manifest


def _resolve_game_path(root: Path, relative: str, *, context: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise GameWebLoaderError(f"{context} contains an invalid path.")
    relative_path = Path(relative.replace("\\", "/"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise GameWebLoaderError(f"{context} path must stay relative: {relative!r}")
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GameWebLoaderError(f"{context} path escaped game root: {relative!r}") from exc
    return path


def _read_game_source(root: Path, path: Path, seen: tuple[Path, ...] = ()) -> str:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GameWebLoaderError(f"Game source escaped game root: {path}") from exc
    if path in seen:
        chain = " -> ".join(str(item.relative_to(root)).replace("\\", "/") for item in (*seen, path))
        raise GameWebLoaderError(f"Recursive game include detected: {chain}")
    if not path.is_file():
        relative = str(path.relative_to(root)).replace("\\", "/")
        raise GameWebLoaderError(f"Game source file not found: {relative}")

    text = path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        include_name = match.group(1).strip()
        include_relative = Path(include_name.replace("\\", "/"))
        if include_relative.is_absolute() or ".." in include_relative.parts:
            raise GameWebLoaderError(f"Game include path must stay relative: {include_name!r}")
        include_path = (path.parent / include_relative).resolve()
        try:
            include_path.relative_to(root)
        except ValueError as exc:
            raise GameWebLoaderError(f"Game include escaped game root: {include_name!r}") from exc
        return _read_game_source(root, include_path, (*seen, path))

    return _GAME_INCLUDE_RE.sub(replace, text)


def read_game_bundle(repository_root: Path, slot: str, game_id: str | None = None) -> str:
    chosen = game_id or selected_game_id()
    manifest = load_game_manifest(repository_root, chosen)
    bundle_paths = manifest["web"]["bundles"].get(slot)
    if not isinstance(bundle_paths, list) or not bundle_paths:
        raise GameWebLoaderError(f"Game {chosen!r} does not define non-empty bundle {slot!r}.")

    root = _game_root(repository_root, chosen)
    parts: list[str] = []
    for relative in bundle_paths:
        path = _resolve_game_path(root, relative, context=f"Game bundle {slot!r}")
        if not path.is_file():
            raise GameWebLoaderError(f"Game bundle file not found: game_projects/{chosen}/{relative}")
        parts.append(_read_game_source(root, path))
    return "\n".join(parts)
