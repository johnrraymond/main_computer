from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
SCRIPTS = GAME_ROOT / "web" / "scripts"
UNIVERSE_RUNTIME = SCRIPTS / "space-universe-runtime.js"
ASTROMETRICS_RUNTIME = SCRIPTS / "space-astrometrics-runtime.js"
GRAVITY_RUNTIME = SCRIPTS / "space-gravity-runtime.js"
SCENE_VIEWER = SCRIPTS / "scene-viewer.js"
VIEWSCREENS = SCRIPTS / "shuttle3d-render-viewscreens.js"
GAME_MANIFEST = GAME_ROOT / "game.json"
SMOKE = GAME_ROOT / "tools" / "space_universe_astrometrics_smoke.py"
PROJECTS = (
    ROOT / "game_projects" / "webgl-demo" / "project.json",
    ROOT / "game_projects" / "starter-game" / "project.json",
    ROOT / "game_projects" / "new-game" / "project.json",
)


class SpaceUniverseAstrometricsTests(unittest.TestCase):
    def test_projects_define_resident_hyperbolic_universe_catalog(self) -> None:
        for project_path in PROJECTS:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            universe = project["metadata"]["spaceUniverse"]
            navigation = project["metadata"]["spaceNavigation"]
            self.assertEqual(universe["schema"], "game.spaceUniverse.v1")
            self.assertEqual(universe["model"], "poincare-ball")
            self.assertEqual(universe["observerBodyId"], "ship.mother")
            self.assertEqual(len(universe["systemAnchors"]), len(navigation["systems"]))
            self.assertEqual(
                {entry["id"] for entry in universe["systemAnchors"]},
                {entry["id"] for entry in navigation["systems"]},
            )
            for anchor in universe["systemAnchors"]:
                self.assertLess(sum(component * component for component in anchor["poincare"]), 1.0)

    def test_boot_smoke_reaches_ship_centered_astrometric_snapshot(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the universe smoke")
        result = subprocess.run(
            [str(Path(shutil.which("python") or "python")), str(SMOKE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(all(payload["checks"].values()))
        self.assertEqual(payload["metrics"]["systemCount"], 32)
        self.assertEqual(payload["metrics"]["startupBaselineCount"], 31)
        self.assertEqual(payload["metrics"]["paxEstimatorCountAfterSecondBaseline"], 2)
        self.assertGreater(payload["metrics"]["targetAngularRadiusDeg"], 0)
        self.assertEqual(payload["metrics"]["frontendPhaseVectorDimensions"], 6)
        self.assertEqual(payload["metrics"]["differentiatedBodyCount"], 9)
        self.assertLess(payload["metrics"]["maxPositionDerivativeRelativeError"], 1e-6)
        self.assertLess(payload["metrics"]["maxVelocityDerivativeRelativeError"], 1e-3)
        self.assertEqual(payload["metrics"]["movingFrameOriginBodyId"], "star.solace-reach-a")
        self.assertGreater(payload["metrics"]["movingFrameDisplacementM"], 0)
        self.assertLess(payload["metrics"]["nestedFramePositionReconstructionErrorM"], 1e-3)
        self.assertLess(payload["metrics"]["nestedFrameVelocityReconstructionErrorMps"], 1e-6)
        self.assertEqual(payload["metrics"]["impactPositionJumpM"], 0)
        self.assertLess(payload["metrics"]["preImpactSegmentDerivativeErrorMps"], 1e-12)
        self.assertLess(payload["metrics"]["postImpactSegmentDerivativeErrorMps"], 1e-12)

    def test_hyperbolic_recentering_makes_each_system_a_valid_observer_origin(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the universe runtime test")
        project_path = PROJECTS[0]
        script = f"""
const fs = require('fs');
const universeApi = require({json.dumps(str(UNIVERSE_RUNTIME))});
const project = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const runtime = universeApi.create(project.metadata.spaceUniverse, {{navigationDefinition: project.metadata.spaceNavigation}});
const solaceToVela = runtime.relativeSystemObservation('system.solace-reach', 'system.vela-gate', false);
const velaToSolace = runtime.relativeSystemObservation('system.vela-gate', 'system.solace-reach', false);
if (!(solaceToVela.hyperbolicDistance > 0)) throw new Error('missing Solace->Vela distance');
if (Math.abs(solaceToVela.hyperbolicDistance - velaToSolace.hyperbolicDistance) > 1e-10) throw new Error('hyperbolic distance is not symmetric');
if (Math.hypot(...solaceToVela.centeredPosition) >= 1) throw new Error('observer-centered Vela left ball');
if (Math.hypot(...velaToSolace.centeredPosition) >= 1) throw new Error('observer-centered Solace left ball');
runtime.observeCatalogFromSystem('system.solace-reach');
runtime.observeCatalogFromSystem('system.vela-gate');
const pax = runtime.snapshot('system.vela-gate').catalog.find((entry) => entry.id === 'system.pax');
if (pax.estimatorCount !== 2) throw new Error(`expected two Pax estimators, got ${{pax.estimatorCount}}`);
console.log('hyperbolic-observer-centers-ok');
"""
        result = subprocess.run(["node", "-e", script, str(project_path)], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("hyperbolic-observer-centers-ok", result.stdout)

    def test_actual_game_boot_order_and_viewscreen_consume_astrometrics(self) -> None:
        manifest = json.loads(GAME_MANIFEST.read_text(encoding="utf-8"))
        runtime_bundle = manifest["web"]["bundles"]["runtime-before-routing"]
        viewer = SCENE_VIEWER.read_text(encoding="utf-8")
        viewscreens = VIEWSCREENS.read_text(encoding="utf-8")
        self.assertLess(runtime_bundle.index("web/scripts/space-navigation-runtime.js"), runtime_bundle.index("web/scripts/space-universe-runtime.js"))
        self.assertLess(runtime_bundle.index("web/scripts/space-universe-runtime.js"), runtime_bundle.index("web/scripts/space-gravity-runtime.js"))
        self.assertLess(runtime_bundle.index("web/scripts/space-gravity-runtime.js"), runtime_bundle.index("web/scripts/space-astrometrics-runtime.js"))
        self.assertLess(runtime_bundle.index("web/scripts/space-astrometrics-runtime.js"), runtime_bundle.index("web/scripts/scene-viewer.js"))
        self.assertIn("this.spaceUniverseRuntime = this.createSpaceUniverseRuntime(options)", viewer)
        self.assertIn("this.spaceAstrometricsRuntime = this.createSpaceAstrometricsRuntime(options)", viewer)
        self.assertIn("this.updateSpaceUniverse(frameTime, deltaSeconds)", viewer)
        self.assertIn("this.updateSpaceGravity(frameTime, deltaSeconds)", viewer)
        self.assertIn("this.updateSpaceAstrometrics(frameTime)", viewer)
        self.assertLess(viewer.index("this.updateSpaceUniverse(frameTime, deltaSeconds)"), viewer.index("this.updateSpaceGravity(frameTime, deltaSeconds)"))
        self.assertLess(viewer.index("this.updateSpaceGravity(frameTime, deltaSeconds)"), viewer.index("this.updateSpaceAstrometrics(frameTime)"))
        self.assertIn("const astrometrics = this.astrometricSnapshot?.() || null", viewscreens)
        self.assertIn("this.appendAstrometricSystemDisplay(builder, prop, nowMs, navigation, astrometrics)", viewscreens)
        self.assertIn(
            "appendAstrometricSystemDisplay(builder, prop, nowMs = 0, navigationState = null, astrometricState = null)",
            viewer,
        )
        self.assertIn('"appendAstrometricSystemDisplay",', viewer)
        self.assertIn("if (!openingEncounterBaseState) return false;", viewer)
        self.assertIn("if (!this.enemyShipDisabled()) return true;", viewer)
        self.assertIn("return Number(this.bridgeTacticalShotAgeMs(nowMs)) < 1900;", viewer)
        astrometrics_runtime = ASTROMETRICS_RUNTIME.read_text(encoding="utf-8")
        self.assertIn('source: "hyperbolic-catalog"', astrometrics_runtime)
        self.assertIn('source: "local-gravity"', astrometrics_runtime)
        self.assertIn("Moons are no longer generated from planet.moonCount", viewscreens)

    def test_javascript_runtime_files_parse(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for JavaScript parse checks")
        for path in (UNIVERSE_RUNTIME, ASTROMETRICS_RUNTIME, GRAVITY_RUNTIME, SCENE_VIEWER, VIEWSCREENS):
            result = subprocess.run(["node", "--check", str(path)], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr or result.stdout}")


if __name__ == "__main__":
    unittest.main()
