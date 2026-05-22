from __future__ import annotations

import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML, _application_route_target


class ViewportGrapesJsPhaseOneTests(unittest.TestCase):
    def test_applications_index_replaces_layout_builder_with_game_editor(self) -> None:
        self.assertIn('data-app="game-editor"', APPLICATIONS_INDEX_HTML)
        self.assertIn("Game Editor", APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-app"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="game-editor-preview"', APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/applications/game-editor/project/write", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("GrapesJS Layout Builder", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("grapesjs@0.22.15", APPLICATIONS_INDEX_HTML)

    def test_layout_builder_routes_alias_to_game_editor(self) -> None:
        self.assertEqual(_application_route_target("/applications/game-editor"), "game-editor")
        self.assertEqual(_application_route_target("/applications/layout-builder"), "game-editor")
        self.assertEqual(_application_route_target("/applications/layout-builder/"), "game-editor")
        self.assertEqual(_application_route_target("/apps/layout-builder"), "game-editor")
        self.assertEqual(_application_route_target("/app/layout-builder"), "game-editor")
        self.assertEqual(_application_route_target("/applications/task-manager/connections"), "task-manager")
        self.assertEqual(_application_route_target("/applications/task-manager/all-processes"), "task-manager")
        self.assertEqual(_application_route_target("/applications/task-manager/hardware"), "task-manager")
        self.assertEqual(_application_route_target("/applications/task-manager/server-processes"), "task-manager")
        self.assertIsNone(_application_route_target("/applications/task-manager/unknown"))
        self.assertIsNone(_application_route_target("/applications/git-tools/status"))


if __name__ == "__main__":
    unittest.main()
