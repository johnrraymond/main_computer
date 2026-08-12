from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
WEBGL_DESKTOP = ROOT / "main_computer" / "web" / "applications" / "scripts" / "webgl-desktop.js"
GAME_EDITOR_CSS = ROOT / "main_computer" / "web" / "applications" / "styles" / "game-editor.css"


class OpeningShuttleGameplayPackSelectorTests(unittest.TestCase):
    def test_visible_reload_selector_has_none_and_opening_shuttle_pack(self) -> None:
        html = APPLICATIONS_HTML.read_text(encoding="utf-8")
        self.assertIn('id="webgl-gameplay-pack-select"', html)
        self.assertIn('data-webgl-gameplay-pack-controls', html)
        self.assertIn('None — base game', html)
        self.assertIn('pack.opening-shuttle.elite-boarders', html)
        self.assertIn('Opening Shuttle: Elite Boarders', html)
        self.assertIn('id="webgl-gameplay-pack-apply"', html)
        self.assertIn('Apply + Reload', html)
        self.assertIn('id="webgl-gameplay-pack-status"', html)

    def test_selector_styles_are_present(self) -> None:
        css = GAME_EDITOR_CSS.read_text(encoding="utf-8")
        self.assertIn(".webgl-gameplay-pack-controls", css)
        self.assertIn(".webgl-gameplay-pack-selector select", css)
        self.assertIn(".webgl-gameplay-pack-status", css)

    def test_reload_selection_precedence_is_query_then_local_storage_then_metadata(self) -> None:
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        self.assertIn("WEBGL_ACTIVE_GAMEPLAY_PACKS_STORAGE_KEY", desktop)
        self.assertIn('"main-computer.webgl.active-gameplay-packs.v1"', desktop)
        self.assertIn("function webglCanonicalGameplayPackId(value)", desktop)
        self.assertIn("function webglSelectedJsGameplayPackId", desktop)
        self.assertIn("function bindWebglGameplayPackSelector()", desktop)
        self.assertIn("syncWebglGameplayPackSelector(webglProjectState.project)", desktop)
        self.assertIn("window.localStorage?.setItem?.(", desktop)
        self.assertIn("WEBGL_ACTIVE_GAMEPLAY_PACKS_KEY", desktop)

        function_start = desktop.index("function webglReloadGameplayPackSelection(project)")
        function_end = desktop.index("function webglGameplayPackSelectorNodes()", function_start)
        body = desktop[function_start:function_end]
        metadata_index = body.index("webglNormalizeGameplayPackIds(configured)")
        local_index = body.index("window.localStorage?.getItem")
        query_index = body.index("new URLSearchParams")
        self.assertLess(metadata_index, local_index)
        self.assertLess(local_index, query_index)
        self.assertIn('selectionSource = "local-storage"', body)
        self.assertIn('selectionSource = "query-param"', body)

    def test_selector_keeps_opening_shuttle_pack_available_even_before_catalog_export(self) -> None:
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        self.assertIn('const WEBGL_OPENING_SHUTTLE_JS_PACK_ID = "pack.opening-shuttle.elite-boarders"', desktop)
        self.assertIn('const WEBGL_OPENING_SHUTTLE_JS_PACK_LABEL = "Opening Shuttle: Elite Boarders"', desktop)
        self.assertIn("WEBGL_OPENING_SHUTTLE_JS_PACK_ID", desktop)
        self.assertIn("byId.set(WEBGL_OPENING_SHUTTLE_JS_PACK_ID", desktop)
        self.assertIn("webglCanonicalGameplayPackId(rawPluginId)", desktop)


if __name__ == "__main__":
    unittest.main()
