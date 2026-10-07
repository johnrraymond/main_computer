from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer.game_web_loader import GameWebLoaderError, load_game_manifest, read_game_bundle


def write_manifest(root: Path, game_id: str, bundles: dict[str, list[str]]) -> None:
    game_root = root / "game_projects" / game_id
    game_root.mkdir(parents=True, exist_ok=True)
    (game_root / "game.json").write_text(
        json.dumps(
            {
                "schema": "main-computer-game-web.v1",
                "gameId": game_id,
                "title": game_id,
                "web": {"applicationId": "webgl", "bundles": bundles},
            }
        ),
        encoding="utf-8",
    )


def test_reads_manifest_bundle_in_declared_order(tmp_path: Path) -> None:
    game_root = tmp_path / "game_projects" / "alpha"
    game_root.mkdir(parents=True)
    (game_root / "a.js").write_text("alpha", encoding="utf-8")
    (game_root / "b.js").write_text("beta", encoding="utf-8")
    write_manifest(tmp_path, "alpha", {"runtime": ["a.js", "b.js"]})

    manifest = load_game_manifest(tmp_path, "alpha")
    assert manifest["gameId"] == "alpha"
    assert read_game_bundle(tmp_path, "runtime", "alpha") == "alpha\nbeta"


def test_expands_game_local_includes_relative_to_containing_file(tmp_path: Path) -> None:
    game_root = tmp_path / "game_projects" / "alpha"
    nested = game_root / "web" / "scripts"
    nested.mkdir(parents=True)
    (nested / "state.js").write_text("const state = 7;", encoding="utf-8")
    (nested / "main.js").write_text(
        "before\n<!-- @include state.js -->\nafter",
        encoding="utf-8",
    )
    write_manifest(tmp_path, "alpha", {"runtime": ["web/scripts/main.js"]})

    assert read_game_bundle(tmp_path, "runtime", "alpha") == "before\nconst state = 7;\nafter"


def test_rejects_bundle_path_escape(tmp_path: Path) -> None:
    write_manifest(tmp_path, "alpha", {"runtime": ["../outside.js"]})
    (tmp_path / "game_projects" / "outside.js").write_text("bad", encoding="utf-8")

    with pytest.raises(GameWebLoaderError, match="stay relative"):
        read_game_bundle(tmp_path, "runtime", "alpha")


def test_rejects_game_include_path_escape(tmp_path: Path) -> None:
    game_root = tmp_path / "game_projects" / "alpha"
    game_root.mkdir(parents=True)
    (game_root / "main.js").write_text("<!-- @include ../outside.js -->", encoding="utf-8")
    (tmp_path / "game_projects" / "outside.js").write_text("bad", encoding="utf-8")
    write_manifest(tmp_path, "alpha", {"runtime": ["main.js"]})

    with pytest.raises(GameWebLoaderError, match="include path must stay relative"):
        read_game_bundle(tmp_path, "runtime", "alpha")


def test_rejects_recursive_game_include(tmp_path: Path) -> None:
    game_root = tmp_path / "game_projects" / "alpha"
    game_root.mkdir(parents=True)
    (game_root / "a.js").write_text("<!-- @include b.js -->", encoding="utf-8")
    (game_root / "b.js").write_text("<!-- @include a.js -->", encoding="utf-8")
    write_manifest(tmp_path, "alpha", {"runtime": ["a.js"]})

    with pytest.raises(GameWebLoaderError, match="Recursive game include"):
        read_game_bundle(tmp_path, "runtime", "alpha")


def test_rejects_manifest_identity_mismatch(tmp_path: Path) -> None:
    game_root = tmp_path / "game_projects" / "alpha"
    game_root.mkdir(parents=True)
    (game_root / "game.json").write_text(
        json.dumps(
            {
                "schema": "main-computer-game-web.v1",
                "gameId": "beta",
                "web": {"bundles": {"runtime": ["x.js"]}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GameWebLoaderError, match="id mismatch"):
        load_game_manifest(tmp_path, "alpha")
