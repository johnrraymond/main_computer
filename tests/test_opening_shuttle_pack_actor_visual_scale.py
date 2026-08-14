from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
GAMEPLAY_PACK_RUNTIME = SCRIPT_ROOT / "gameplay-pack-runtime.js"
SCENE_VIEWER = SCRIPT_ROOT / "scene-viewer.js"


SCALED_PACK_SOURCE = """
export default defineGameplayPack({
  id: "pack.experimental.scaled-boarder",
  title: "Scaled Boarder",
  version: "0.1.0",
  setup(pack) {
    pack.encounter("opening-shuttle-ambush", (encounter) => {
      encounter.onStart(() => {
        encounter.spawnWave({
          id: "scaled-boarder-wave",
          location: "location.shuttle.starboard",
          actors: [
            {
              archetype: "shuttle-raider",
              count: 1,
              displayName: "Scaled Boarder",
              healthMultiplier: 2.5,
              scale: 2.0
            }
          ],
          hudMessage: "Scaled boarder inbound."
        });
      });
    });
  }
});
"""


class OpeningShuttlePackActorVisualScaleTests(unittest.TestCase):
    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for opening shuttle visual scale tests")
        result = subprocess.run(
            [
                "node",
                "-e",
                textwrap.dedent(script),
                str(GAMEPLAY_PACK_RUNTIME),
                str(SCENE_VIEWER),
                SCALED_PACK_SOURCE,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_runtime_preserves_spawn_wave_actor_scale(self) -> None:
        result = self.run_node(
            """
            const packApi = require(process.argv[1]);
            const packSource = process.argv[3];
            const harness = packApi.loadGameplayPackCommandHarnessFromSource(packSource);
            const commands = packApi.dispatchGameplayPackEncounterEvent(
              harness,
              "opening-shuttle-ambush",
              {type: "start"}
            );
            const spawnWave = commands.find((command) => command.type === "spawn-wave");
            console.log(JSON.stringify({spawnWave}));
            """
        )

        actor = result["spawnWave"]["payload"]["actors"][0]
        self.assertEqual(actor["displayName"], "Scaled Boarder")
        self.assertEqual(actor["scale"], 2.0)

    def test_scene_adapter_applies_actor_scale_and_location_to_spawned_boarder(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const packApi = require(process.argv[1]);
            const sceneSource = fs.readFileSync(process.argv[2], "utf8");
            const packSource = process.argv[3];

            const start = sceneSource.indexOf("        createOpeningShuttleEncounterRuntimeState");
            const end = sceneSource.indexOf("        canAlienOccupy(", start);
            if (start < 0 || end < 0) throw new Error("opening shuttle combat methods not found");
            const methodSource = sceneSource.slice(start, end).trim()
              .replace(/\n        (?=(isOpening|opening|award|completeOpening|record|spawnOpening|resolve|combatSnapshot|publish|emitCombatState|spawnAlien))/g, ",\n        ");
            const methods = Function(`return ({${methodSource}});`)();

            globalThis.MainComputerGameplayPackRuntime = packApi;
            globalThis.MainComputerSystemScenarioRuntime = {
              current() {
                return {
                  setOpeningShuttleEncounterBridgeSnapshot(snapshot) { return snapshot; },
                  openingShuttleEncounterBridgeDiagnostic() {
                    return {kind: "diagnostic", available: true, safety: {}, coverage: {}};
                  }
                };
              }
            };

            const runtime = {
              combat: {
                enabled: true,
                transport: {
                  maxAlive: 6,
                  beamDurationMs: 900,
                  spawnPoints: [
                    {id: "port-aft-pad", position: [-2.9, -0.55, 2.55]},
                    {id: "starboard-aft-pad", position: [2.9, -0.55, 2.55]},
                    {id: "center-pad", position: [0, -0.55, 0.3]},
                    {id: "forward-pad", position: [0, -0.55, -3.25]}
                  ]
                },
                alien: {maxHealth: 60}
              },
              aliens: [],
              transportSequence: 0,
              gameOver: false,
              combatClockMs: 0,
              emitted: 0,
              characterAIPhase() { return "shuttle"; },
              emitCombatState() { this.emitted += 1; },
              awardOpeningShuttleEncounterReceipt() {},
              publishOpeningShuttleEncounterBridge() {}
            };
            Object.assign(runtime, methods);
            runtime.openingShuttleEncounter = runtime.createOpeningShuttleEncounterRuntimeState();

            const harness = packApi.loadGameplayPackCommandHarnessFromSource(packSource);
            runtime.openingShuttleInstallGameplayPackCommandHarness(harness, 100);

            const alien = runtime.aliens[0];
            const snapshot = runtime.openingShuttleEncounterSnapshot();
            console.log(JSON.stringify({alien, snapshot, emitted: runtime.emitted}));
            """
        )

        alien = result["alien"]
        self.assertEqual(alien["displayName"], "Scaled Boarder")
        self.assertEqual(alien["visualScale"], 2)
        self.assertEqual(alien["spawnId"], "starboard-aft-pad")
        self.assertEqual(alien["healthMultiplier"], 2.5)
        self.assertEqual(result["snapshot"]["eliteWave"]["visualScale"], 2)
        self.assertEqual(result["snapshot"]["eliteWave"]["location"], "location.shuttle.starboard")
        self.assertIn("PACK SCALE 2X", result["snapshot"]["packLine"].upper())

    def test_scene_render_path_has_visible_scaled_pack_actor_cue(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")
        self.assertIn("packScaleCue", source)
        self.assertIn("packScaleRing", source)
        self.assertIn("packScaleAccent", source)
        self.assertIn("dataset.packVisualScale", source)


if __name__ == "__main__":
    unittest.main()
