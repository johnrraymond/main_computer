from __future__ import annotations

import json
import shutil
from pathlib import Path

from main_computer.gameplay_plugin_activation import enable_gameplay_plugin
from main_computer.gameplay_plugin_catalog_export import write_gameplay_plugin_catalog_export
from main_computer.gameplay_plugin_materializer import materialize_gameplay_plugin_generated_content
from main_computer.gameplay_plugin_project_catalog import read_gameplay_plugin_project_catalog


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT / "game_projects" / "webgl-demo"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
WEBGL_DESKTOP = ROOT / "main_computer" / "web" / "applications" / "scripts" / "webgl-desktop.js"
GAME_EDITOR_CSS = ROOT / "main_computer" / "web" / "applications" / "styles" / "game-editor.css"
GAME_ROUTES = ROOT / "main_computer" / "viewport_routes_game.py"

OPENING_SHUTTLE_PACK = "plugin.hand-authored.opening-shuttle-ambush.001"
PACK_SELECTION_KEY = "main-computer.webgl.enabled-gameplay-packs.v2"


def _copy_project(tmp_path: Path) -> Path:
    target = tmp_path / "webgl-demo"
    ignore = shutil.ignore_patterns("generated")
    shutil.copytree(PROJECT_ROOT, target, ignore=ignore)
    return target


def test_server_loader_route_materializes_enables_and_exports_selected_pack(tmp_path: Path) -> None:
    project = _copy_project(tmp_path)

    materialized = materialize_gameplay_plugin_generated_content(
        project,
        OPENING_SHUTTLE_PACK,
        overwrite=True,
    )
    assert materialized.valid, materialized.problems
    assert materialized.target_paths == (
        "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/scenarios/opening-shuttle-ambush-elite-wave.json",
        "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/encounters/opening-shuttle-ambush-elite-wave.json",
    )

    activated = enable_gameplay_plugin(project, OPENING_SHUTTLE_PACK)
    assert activated.valid, activated.problems

    exported = write_gameplay_plugin_catalog_export(project, overwrite=True)
    assert exported.valid, exported.problems
    assert exported.wrote_file is True
    assert exported.project_json_modified is False
    assert exported.runtime_loaded is False
    assert exported.activated_in_runtime is False
    assert (project / "generated/gameplay-plugins/runtime-catalog.json").is_file()

    catalog = read_gameplay_plugin_project_catalog(project, project_id="webgl-demo")
    assert catalog.valid, catalog.problems
    assert catalog.payload["status"] == "ready"
    assert catalog.payload["enabledPluginIds"] == [OPENING_SHUTTLE_PACK]
    assert catalog.payload["runtimeLoaded"] is False
    assert catalog.payload["projectJsonModified"] is False
    assert catalog.payload["activatedInRuntime"] is False

    encounter = next(
        document
        for document in catalog.payload["documents"]
        if document["id"] == "encounter.plugin.opening-shuttle-ambush.elite-wave"
    )
    assert encounter["template"] == "encounter-template.shuttle-ambush"
    assert encounter["participants"] == [
        {
            "role": "hostile",
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "count": 3,
        }
    ]


def test_game_routes_expose_real_gameplay_pack_load_endpoint() -> None:
    source = GAME_ROUTES.read_text(encoding="utf-8")

    assert '"/api/applications/game-editor/gameplay-pack/load"' in source
    assert "def _game_gameplay_pack_load_payload" in source
    assert "materialize_gameplay_plugin_generated_content(root, requested, overwrite=True)" in source
    assert "enable_gameplay_plugin(root, requested)" in source
    assert "write_gameplay_plugin_catalog_export(root, overwrite=True)" in source
    assert '"mode": "none"' in source
    assert '"activeGameplayPackIds": []' in source
    assert '"projectJsonModified": False' in source
    assert '"saveStateMutated": False' in source


def test_project_read_exposes_available_gameplay_packs_without_project_json_mutation() -> None:
    source = GAME_ROUTES.read_text(encoding="utf-8")

    assert "def _game_available_gameplay_packs_payload" in source
    assert "metadata[\"availableGameplayPacks\"]" in source
    assert "discover_gameplay_plugin_registry(root)" in source
    assert "project = self._game_attach_available_gameplay_packs(root, project)" in source


def test_visible_selector_has_none_path_pack_option_and_loader_call() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
    css = GAME_EDITOR_CSS.read_text(encoding="utf-8")

    assert 'id="webgl-gameplay-pack-selector"' in html
    assert 'id="webgl-gameplay-pack-checklist"' in html
    assert 'data-webgl-gameplay-pack-checkbox' in html
    assert 'id="webgl-gameplay-pack-apply"' in html
    assert "START GAME" in html
    assert "Opening Shuttle: Elite Boarders" in html
    assert "Main Ship: Bay Boarders" in html

    assert PACK_SELECTION_KEY in desktop
    assert "WEBGL_LEGACY_ACTIVE_GAMEPLAY_PACKS_KEY" in desktop
    assert "bindWebglGameplayPackSelector()" in desktop
    assert "syncWebglGameplayPackSelector(webglProjectState.project)" in desktop
    assert "startWebglGameFromGameplayPackLobby" in desktop
    assert "webglStoreGameplayPackSelection" in desktop

    assert ".webgl-gameplay-pack-selector" in css
    assert ".webgl-gameplay-pack-checklist" in css
    assert ".webgl-gameplay-pack-status" in css


def test_reload_selection_precedence_allows_explicit_none_to_override_metadata() -> None:
    desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
    function_start = desktop.index("function webglReloadGameplayPackSelection")
    function_end = desktop.index("function webglGameplayPackSelectorNodes")
    function_source = desktop[function_start:function_end]

    metadata_index = function_source.index("project-metadata")
    local_index = function_source.index("window.localStorage?.getItem")
    query_index = function_source.index("new URLSearchParams")

    assert metadata_index < local_index < query_index
    assert "if (!selected.length && raw)" not in function_source
    assert 'selectionSource = "local-storage"' in function_source
    assert "WEBGL_ENABLED_GAMEPLAY_PACKS_KEY" in function_source
    assert "WEBGL_LEGACY_ACTIVE_GAMEPLAY_PACKS_KEY" in function_source
    assert 'selectionSource = "query-param"' in function_source
    assert '"None"' in function_source or '"none"' in function_source
