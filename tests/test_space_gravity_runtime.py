from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
RUNTIME_PATH = GAME_ROOT / "web" / "scripts" / "space-gravity-runtime.js"
SCENE_VIEWER_PATH = GAME_ROOT / "web" / "scripts" / "scene-viewer.js"
GAME_MANIFEST = GAME_ROOT / "game.json"
SMOKE_PATH = GAME_ROOT / "tools" / "space_gravity_smoke.py"
PROJECT_PATHS = (
    ROOT / "game_projects" / "webgl-demo" / "project.json",
    ROOT / "game_projects" / "starter-game" / "project.json",
    ROOT / "game_projects" / "new-game" / "project.json",
)


class SpaceGravityRuntimeTests(unittest.TestCase):
    def test_projects_expose_solace_gravity_definition(self) -> None:
        for project_path in PROJECT_PATHS:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            definition = project["metadata"]["spacePhysics"]
            self.assertEqual(definition["schema"], "game.spacePhysics.v1")
            self.assertEqual(definition["definitionVersion"], "game.spacePhysics.definition.v1")
            self.assertEqual(definition["stateVersion"], "game.spacePhysics.state.v1")
            self.assertEqual(definition["gravitationalConstant"], 6.67430e-11)
            self.assertEqual([system["id"] for system in definition["systems"]], ["system.solace-reach"])
            bodies = definition["systems"][0]["bodies"]
            self.assertEqual(len(bodies), 9)
            mother = next(body for body in bodies if body["id"] == "ship.mother")
            self.assertEqual(mother["massKg"], 0)
            self.assertNotIn("thrust", mother)
            self.assertNotIn("propulsion", mother)

    def test_node_runtime_conserves_energy_and_keeps_nested_orbits_bound(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the gravity runtime smoke")
        result = subprocess.run(
            [str(Path(shutil.which("python") or "python")), str(SMOKE_PATH)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(all(payload["checks"].values()))
        self.assertLess(payload["metrics"]["energyRelativeDrift"], 1e-8)
        self.assertLess(payload["metrics"]["shipRadiusRelativeDrift"], 0.01)

    def test_runtime_accepts_keplerian_and_state_vector_initialization(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the gravity runtime smoke")
        script = f"""
const api = require({json.dumps(str(RUNTIME_PATH))});
const definition = {{
  schema: 'game.spacePhysics.v1',
  definitionVersion: 'game.spacePhysics.definition.v1',
  stateVersion: 'game.spacePhysics.state.v1',
  gravitationalConstant: 6.67430e-11,
  fixedStepSeconds: 10,
  timeScale: 1,
  systems: [{{
    id: 'system.test',
    anchors: [{{id: 'anchor.origin', massKg: 1.98847e30, positionM: [0,0,0], velocityMps: [0,0,0]}}],
    bodies: [
      {{id: 'star.test', label: 'Test', kind: 'star', massKg: 1.98847e30, radiusM: 6.957e8,
        initialState: {{positionM:[0,0,0], velocityMps:[0,0,0]}}}},
      {{id: 'planet.test', label: 'Planet', kind: 'planet', massKg: 5.9722e24, radiusM: 6.371e6,
        orbit: {{primaryId:'anchor.origin', semiMajorAxisM:149597870700, eccentricity:0.1, inclinationDeg:20, longitudeAscendingNodeDeg:30, argumentPeriapsisDeg:40, meanAnomalyDeg:50}}}}
    ]
  }}]
}};
const report = api.validateDefinition(definition);
if (!report.ok) throw new Error(report.errors.join('; '));
const runtime = api.create(definition);
runtime.setActiveSystem('system.test');
const snapshot = runtime.snapshot();
if (snapshot.bodyCount !== 2) throw new Error('wrong body count');
if (!snapshot.bodies.every((body) => body.positionM.every(Number.isFinite) && body.velocityMps.every(Number.isFinite))) throw new Error('non-finite state vector');
console.log('gravity-initializers-ok');
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, check=False, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("gravity-initializers-ok", result.stdout)

    def test_scene_viewer_ticks_gravity_after_navigation(self) -> None:
        manifest = json.loads(GAME_MANIFEST.read_text(encoding="utf-8"))
        runtime_bundle = manifest["web"]["bundles"]["runtime-before-routing"]
        viewer = SCENE_VIEWER_PATH.read_text(encoding="utf-8")
        self.assertLess(
            runtime_bundle.index("web/scripts/space-navigation-runtime.js"),
            runtime_bundle.index("web/scripts/space-gravity-runtime.js"),
        )
        self.assertLess(
            runtime_bundle.index("web/scripts/space-gravity-runtime.js"),
            runtime_bundle.index("web/scripts/scene-viewer.js"),
        )
        self.assertIn("this.spaceGravityRuntime = this.createSpaceGravityRuntime(options)", viewer)
        self.assertIn("options.project?.metadata?.spacePhysics", viewer)
        self.assertIn("runtime.setActiveSystem(navigation?.currentSystemId || \"\")", viewer)
        self.assertIn("this.spaceUniverseRuntime = this.createSpaceUniverseRuntime(options)", viewer)
        self.assertIn("this.spaceGravityRuntime.advanceToSimulationSeconds?.(Number(universeSeconds))", viewer)
        self.assertIn("this.updateSpaceUniverse(frameTime, deltaSeconds)", viewer)
        self.assertIn("this.updateSpaceGravity(frameTime, deltaSeconds)", viewer)


if __name__ == "__main__":
    unittest.main()
