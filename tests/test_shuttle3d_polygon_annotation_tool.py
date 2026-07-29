from __future__ import annotations

import json
import unittest
from pathlib import Path

from main_computer.viewport import APPLICATIONS_INDEX_HTML


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
PROJECT_PATHS = (
    ROOT / "game_projects" / "webgl-demo" / "project.json",
    ROOT / "game_projects" / "starter-game" / "project.json",
    ROOT / "game_projects" / "new-game" / "project.json",
)


class Shuttle3DPolygonAnnotationToolTests(unittest.TestCase):
    """Patch U: hold-P polygon/object annotation support for the WebGL game surface."""

    def test_scene_bundle_contains_hold_p_annotation_flow(self) -> None:
        self.assertIn("setPolygonAnnotationKeyHeld", APPLICATIONS_INDEX_HTML)
        self.assertIn("pickPolygonAnnotationTarget", APPLICATIONS_INDEX_HTML)
        self.assertIn("openShuttle3dPolygonAnnotationDialog", APPLICATIONS_INDEX_HTML)
        self.assertIn("game.shuttle3d.polygonAnnotation.v1", APPLICATIONS_INDEX_HTML)
        self.assertIn("main-computer-shuttle3d-polygon-annotation-save", APPLICATIONS_INDEX_HTML)
        self.assertIn("hold P + click", APPLICATIONS_INDEX_HTML)

    def test_annotation_picker_targets_data_defined_ship_elements(self) -> None:
        scene_viewer = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
        self.assertIn("rooms[].geometry.walls", scene_viewer)
        self.assertIn("rooms[].geometry.openings", scene_viewer)
        self.assertIn("rooms[].geometry.doorPanels", scene_viewer)
        self.assertIn("motherShipInterior.props", scene_viewer)
        self.assertIn("motherShipInterior.interactables", scene_viewer)
        self.assertIn("shuttle3d.pilotStations", scene_viewer)
        self.assertIn("shuttle3dRayIntersectsBounds", scene_viewer)

    def test_game_editor_persists_annotation_events_as_dirty_project_metadata(self) -> None:
        game_editor = (SCRIPT_ROOT / "game-editor.js").read_text(encoding="utf-8")
        self.assertIn("handleShuttle3dPolygonAnnotationSave", game_editor)
        self.assertIn("metadata.shuttle3d.polygonAnnotations", game_editor)
        self.assertIn("markGameEditorDirty(`dirty - annotated", game_editor)
        self.assertIn("main-computer-shuttle3d-polygon-annotation-save", game_editor)

    def test_project_control_hints_expose_annotation_shortcut(self) -> None:
        for project_path in PROJECT_PATHS:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            scenes = project.get("scenes", [])
            self.assertTrue(scenes, project_path)
            shuttle = ((scenes[0].get("metadata") or {}).get("shuttle3d") or {})
            self.assertIn("hold P + click polygons/objects to annotate", str(shuttle.get("controlsHint", "")))


    def test_annotation_picker_has_rendered_primitive_fallback_for_one_off_bars(self) -> None:
        scene_viewer = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
        self.assertIn("recordAnnotationPrimitive", scene_viewer)
        self.assertIn("annotationPrimitiveTargets", scene_viewer)
        self.assertIn("rendered-${normalizedKind}", scene_viewer)
        self.assertIn("scene-viewer.dynamic", scene_viewer)
        self.assertIn("visible beams/bars", scene_viewer)
        self.assertIn("P held: no selectable rendered primitive under cursor", scene_viewer)

    def test_annotation_modal_suspends_gameplay_keyboard_capture(self) -> None:
        scene_viewer = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
        self.assertIn("shuttle3dAnnotationDialogOpen", scene_viewer)
        self.assertIn("shuttle3dAnnotationDialogEventTarget", scene_viewer)
        self.assertIn("shuttle3dStopGameplayInputForAnnotationDialog", scene_viewer)
        self.assertIn("shuttle?.clearMovementKeys?.()", scene_viewer)
        self.assertIn("if (shuttle3dAnnotationDialogOpen(container))", scene_viewer)
        self.assertIn("if (!shuttle3dAnnotationDialogEventTarget(event)) event.preventDefault();", scene_viewer)
        self.assertIn('dialog.addEventListener("close", removeDialog, {once: true});', scene_viewer)


if __name__ == "__main__":
    unittest.main()
