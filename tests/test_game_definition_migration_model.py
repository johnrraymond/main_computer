from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer


ROOT = Path(__file__).resolve().parents[1]
PROJECT_IDS = ("webgl-demo", "starter-game", "new-game")
MOTHER_SHIP_INTERIOR_SCHEMA = "game.motherShipInterior.v1"
MOTHER_SHIP_INTERIOR_DEFINITION_VERSION = "game.motherShipInterior.definition.v2"
MOTHER_SHIP_INTERIOR_STATE_VERSION = "game.motherShipInterior.state.v1"


def _post_json(base_url: str, path: str, payload: dict) -> dict:
    request = Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _project_mother_ship_interior(project: dict) -> dict:
    for scene in project.get("scenes", []):
        interior = (((scene.get("metadata") or {}).get("shuttle3d") or {}).get("motherShipInterior") if isinstance(scene, dict) else None)
        if isinstance(interior, dict):
            return interior
    raise AssertionError("missing metadata.shuttle3d.motherShipInterior")


class MotherShipDefinitionMigrationModelTests(unittest.TestCase):
    """Patch T: old project definitions get explicit version/default migration before play."""

    def test_current_project_files_declare_definition_and_state_versions(self) -> None:
        for project_id in PROJECT_IDS:
            project_path = ROOT / "game_projects" / project_id / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            interior = _project_mother_ship_interior(project)

            self.assertEqual(interior["schema"], MOTHER_SHIP_INTERIOR_SCHEMA)
            self.assertEqual(interior["definitionVersion"], MOTHER_SHIP_INTERIOR_DEFINITION_VERSION)
            self.assertEqual(interior["stateVersion"], MOTHER_SHIP_INTERIOR_STATE_VERSION)
            self.assertTrue(interior["validation"]["requireDefinitionVersion"])

    def test_browser_bundle_contains_runtime_migration_model(self) -> None:
        self.assertIn("Patch T centralizes compatibility defaults", APPLICATIONS_INDEX_HTML)
        self.assertIn("shuttle3dMigrateMotherShipInteriorDefinition", APPLICATIONS_INDEX_HTML)
        self.assertIn(MOTHER_SHIP_INTERIOR_DEFINITION_VERSION, APPLICATIONS_INDEX_HTML)
        self.assertIn("mother-ship migration supplied defaults", APPLICATIONS_INDEX_HTML)

    def test_project_read_migrates_legacy_mother_ship_interior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            projects_root = workspace / "game_projects"
            default_root = projects_root / "webgl-demo"
            legacy_root = projects_root / "legacy-game"

            for project_id, target_root in (("webgl-demo", default_root), ("webgl-demo", legacy_root)):
                source_root = ROOT / "game_projects" / project_id
                for folder in ("assets", "scripts", "data", "builds"):
                    source_folder = source_root / folder
                    target_folder = target_root / folder
                    if source_folder.exists():
                        shutil.copytree(source_folder, target_folder, dirs_exist_ok=True)
                    else:
                        target_folder.mkdir(parents=True, exist_ok=True)

            default_project = json.loads((ROOT / "game_projects" / "webgl-demo" / "project.json").read_text(encoding="utf-8"))
            (default_root / "project.json").write_text(json.dumps(default_project, indent=2), encoding="utf-8")

            legacy_project = json.loads(json.dumps(default_project))
            legacy_project["id"] = "legacy-game"
            legacy_project["name"] = "Legacy Game"
            legacy_interior = _project_mother_ship_interior(legacy_project)
            legacy_interior.clear()
            legacy_interior.update(
                {
                    "enabled": True,
                    "initialLocation": "bay.shuttle",
                    "power": "emergency",
                }
            )
            (legacy_root / "project.json").write_text(json.dumps(legacy_project, indent=2), encoding="utf-8")

            server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=workspace), verbose=False)
            server.debug_root = workspace
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                payload = _post_json(base_url, "/api/applications/game-editor/project/read", {"project_id": "legacy-game"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            migrated = _project_mother_ship_interior(payload["project"])
            self.assertEqual(migrated["schema"], MOTHER_SHIP_INTERIOR_SCHEMA)
            self.assertEqual(migrated["definitionVersion"], MOTHER_SHIP_INTERIOR_DEFINITION_VERSION)
            self.assertEqual(migrated["stateVersion"], MOTHER_SHIP_INTERIOR_STATE_VERSION)
            self.assertEqual(migrated["initialObjective"], "objective.bay-ops")
            self.assertIn("bridge.deck", {room["id"] for room in migrated["rooms"]})
            self.assertIn("prop.display.bridge-viewscreen", {prop["id"] for prop in migrated["props"]})
            self.assertIn("trackEnemyShipOnViewscreen", migrated["interactions"])
            self.assertTrue(migrated["validation"]["requireDefinitionVersion"])
            self.assertTrue(migrated["migration"]["migratedAtLoad"])
            self.assertEqual(migrated["migration"]["sourceDefinitionVersion"], "legacy-unversioned")
            self.assertIn("rooms", migrated["migration"]["defaultsApplied"])
            self.assertIn("props", migrated["migration"]["defaultsApplied"])
            self.assertIn("interactions", migrated["migration"]["defaultsApplied"])


if __name__ == "__main__":
    unittest.main()
