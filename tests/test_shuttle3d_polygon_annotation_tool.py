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

    def test_game_editor_persists_annotation_events_to_disk_immediately(self) -> None:
        game_editor = (SCRIPT_ROOT / "game-editor.js").read_text(encoding="utf-8")
        self.assertIn("async function handleShuttle3dPolygonAnnotationSave", game_editor)
        self.assertIn("metadata.shuttle3d.polygonAnnotations", game_editor)
        self.assertIn("markGameEditorDirty(`saving annotation for ${label}...`)", game_editor)
        self.assertIn("onPolygonAnnotationSave: (detail = {}) => queueShuttle3dPolygonAnnotationSave(detail)", game_editor)
        self.assertIn("verifyAnnotation:", game_editor)
        self.assertIn("payload.verify_annotation", game_editor)
        self.assertIn("saveResult?.annotation_verified !== true", game_editor)
        self.assertIn("annotation saved and verified on disk for ${label}", game_editor)
        self.assertIn("queueShuttle3dPolygonAnnotationSave", game_editor)
        self.assertIn("annotationSavePromise: Promise.resolve()", game_editor)
        self.assertIn("main-computer-shuttle3d-polygon-annotation-save", game_editor)

    def test_webgl_game_surface_has_direct_disk_persistence_callback(self) -> None:
        webgl_desktop = (SCRIPT_ROOT / "webgl-desktop.js").read_text(encoding="utf-8")
        self.assertIn("async function persistWebglPolygonAnnotation", webgl_desktop)
        self.assertIn("/api/applications/game-editor/project/annotation/write", webgl_desktop)
        self.assertIn("queueWebglPolygonAnnotationSave", webgl_desktop)
        self.assertIn("onPolygonAnnotationSave:", webgl_desktop)
        self.assertIn("annotation saved to ${writePath}", webgl_desktop)
        self.assertIn("stale_hash_merged", webgl_desktop)
        self.assertIn("writePathResolved", webgl_desktop)

    def test_annotation_dialog_awaits_explicit_editor_save_callback(self) -> None:
        scene_viewer = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
        self.assertIn("async function shuttle3dSavePolygonAnnotation", scene_viewer)
        self.assertIn('typeof options.onPolygonAnnotationSave === "function"', scene_viewer)
        self.assertIn("await options.onPolygonAnnotationSave(detail)", scene_viewer)
        self.assertIn("Annotation save callback did not confirm disk persistence.", scene_viewer)
        self.assertIn('form.addEventListener("submit", async (event) => {', scene_viewer)
        self.assertIn("const result = await shuttle3dSavePolygonAnnotation(scene, annotation, options);", scene_viewer)
        self.assertIn('sourceLine.textContent = "Writing annotation to project.json...";', scene_viewer)
        self.assertIn("Save failed: ${message}", scene_viewer)
        self.assertIn("result?.writePath || result?.write_path", scene_viewer)

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
