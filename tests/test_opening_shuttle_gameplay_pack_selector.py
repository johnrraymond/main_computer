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
        self.assertIn('id="webgl-gameplay-pack-selection"', html)
        self.assertIn('data-webgl-gameplay-pack-selector', html)
        self.assertIn('None — base game', html)
        self.assertIn('plugin.hand-authored.opening-shuttle-ambush.001', html)
        self.assertIn('Opening Shuttle Ambush Elite Wave', html)
        self.assertIn('id="webgl-gameplay-pack-apply"', html)
        self.assertIn('Apply + Reload', html)
        self.assertIn('id="webgl-gameplay-pack-status"', html)

    def test_selector_styles_are_present(self) -> None:
        css = GAME_EDITOR_CSS.read_text(encoding="utf-8")
        self.assertIn(".webgl-gameplay-pack-controls", css)
        self.assertIn(".webgl-gameplay-pack-controls select", css)
        self.assertIn(".webgl-gameplay-pack-controls output", css)

    def test_reload_selection_precedence_is_query_then_local_storage_then_metadata(self) -> None:
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        self.assertIn("WEBGL_ACTIVE_GAMEPLAY_PACKS_STORAGE_KEY", desktop)
        self.assertIn('"main-computer.webgl.active-gameplay-packs.v1"', desktop)
        self.assertIn("function webglStoredGameplayPackSelection()", desktop)
        self.assertIn("function webglQueryGameplayPackSelection()", desktop)
        self.assertIn("function bindWebglGameplayPackSelector()", desktop)
        self.assertIn("syncWebglGameplayPackSelector(webglProjectState.project)", desktop)
        self.assertIn("window.localStorage?.setItem?.(WEBGL_ACTIVE_GAMEPLAY_PACKS_STORAGE_KEY, JSON.stringify(ids))", desktop)

        function_start = desktop.index("function webglReloadGameplayPackSelection(project)")
        function_end = desktop.index("function webglGameplayPackNodes()", function_start)
        body = desktop[function_start:function_end]
        metadata_index = body.index("webglNormalizeGameplayPackIds(configured)")
        local_index = body.index("webglStoredGameplayPackSelection()")
        query_index = body.index("webglQueryGameplayPackSelection()")
        self.assertLess(metadata_index, local_index)
        self.assertLess(local_index, query_index)
        self.assertIn('source = "localStorage"', body)
        self.assertIn('source = "query"', body)

    def test_selector_keeps_opening_shuttle_pack_available_even_before_catalog_export(self) -> None:
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        self.assertIn('const WEBGL_OPENING_SHUTTLE_PACK_ID = "plugin.hand-authored.opening-shuttle-ambush.001"', desktop)
        self.assertIn('const WEBGL_OPENING_SHUTTLE_PACK_LABEL = "Opening Shuttle Ambush Elite Wave"', desktop)
        self.assertIn("WEBGL_OPENING_SHUTTLE_PACK_ID,", desktop)
        self.assertIn("...catalogPluginIds", desktop)
        self.assertIn("...active", desktop)


if __name__ == "__main__":
    unittest.main()
