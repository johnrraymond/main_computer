from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
SCENE_VIEWER = SCRIPT_ROOT / "scene-viewer.js"
SCENARIO_RUNTIME = SCRIPT_ROOT / "system-scenario-runtime.js"
CHARACTER_RUNTIME = SCRIPT_ROOT / "character-ai-runtime.js"
PAX_INTERACTION = SCRIPT_ROOT / "pax-scenario-interaction.js"


class PaxShuttleArchitectureCharacterizationTests(unittest.TestCase):
    """Freeze the current cross-system contracts before architectural extraction."""

    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for runtime characterization")
        result = subprocess.run(
            [
                "node",
                "-e",
                textwrap.dedent(script),
                str(SCENARIO_RUNTIME),
                str(CHARACTER_RUNTIME),
                str(PROJECT_PATH),
                str(PAX_INTERACTION),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_space_routes_to_phaser_and_weapon_pause_is_not_simulation_pause(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")

        binding_start = source.index("function bindShuttle3dLookaround")
        binding_end = source.index("function shuttle3d", binding_start)
        binding = source[binding_start:binding_end]
        self.assertIn('const firingCodes = new Set(["Space", "KeyF"])', binding)
        self.assertIn("if (!event.repeat) shuttle?.firePhaser?.();", binding)

        weapon_pause_start = source.index("isWeaponFirePaused()")
        weapon_pause_end = source.index("dockingCutsceneSnapshot", weapon_pause_start)
        weapon_pause = source[weapon_pause_start:weapon_pause_end]
        self.assertIn('this.characterAIPhase?.() === "mother-ship"', weapon_pause)
        self.assertIn("this.isDockingCutsceneActive()", weapon_pause)
        self.assertIn("this.isWarpTravelActive()", weapon_pause)
        self.assertIn("return this.isBoardingPaused();", weapon_pause)

        fire_start = source.index("firePhaser(nowMs")
        fire_guard = source[fire_start:source.index("this.combatClockMs", fire_start)]
        self.assertIn("this.isWeaponFirePaused()", fire_guard)
        self.assertNotIn("this.isBoardingPaused()", fire_guard)

        update_start = source.index("updateCombat(nowMs")
        update_guard = source[update_start:source.index("this.combatClockMs = nowMs;", update_start)]
        self.assertIn("this.isBoardingPaused()", update_guard)
        self.assertNotIn("this.isWeaponFirePaused()", update_guard)

    def test_shuttle_legacy_boarders_are_isolated_from_character_ai_entities(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")
        self.assertIn(
            'if (this.characterAIPhase() === "shuttle" && nowMs >= this.nextTransportAtMs)',
            source,
        )
        self.assertNotIn(
            "if (!this.characterAIRuntime && nowMs >= this.nextTransportAtMs)",
            source,
        )

        build_start = source.index("buildDynamicGeometry(nowMs)")
        mother_ship_branch = source.index("if (this.isShuttleBaySceneActive())", build_start)
        mother_ship_return = source.index("return builder.toFloat32Array();", mother_ship_branch)
        legacy_alien_render = source.index("this.aliens.forEach((alien)", mother_ship_return)
        shuttle_segment = source[mother_ship_return:legacy_alien_render]
        self.assertNotIn("appendCharacterAIGeometry", shuttle_segment)

    def test_pax_boarders_are_ai_eligible_only_during_protection_stage(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const characterApi = require(process.argv[2]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
            const runtime = characterApi.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });
            const boarderIds = project.metadata.systemScenarios.scenarios[0]
              .completionCharacterIds;

            const world = (stageId) => ({
              phase: "mother-ship",
              player: {alive: true, health: 100, position: [0, -0.55, -31]},
              ship: {
                power: "online",
                security: "alert",
                currentSystemId: "system.pax"
              },
              scenario: {
                id: "scenario.pax.neutrality-under-fire",
                status: "active",
                stageId
              },
              canOccupy() { return true; }
            });

            const idsFor = (stageId) => runtime
              .activeCharactersForWorld(world(stageId))
              .map((item) => item.id)
              .filter((id) => boarderIds.includes(id))
              .sort();

            process.stdout.write(JSON.stringify({
              boarderIds: [...boarderIds].sort(),
              protection: idsFor("protect-witness"),
              investigation: idsFor("investigation"),
              arrival: idsFor("arrival")
            }));
            '''
        )
        self.assertEqual(result["protection"], result["boarderIds"])
        self.assertEqual(result["investigation"], [])
        self.assertEqual(result["arrival"], [])

    def test_atomic_restart_resets_scenario_characters_evidence_metrics_and_receipt(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const characterApi = require(process.argv[2]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
            const scenario = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo",
              storage: null,
              restore: false,
              activeSystemId: "system.pax"
            });
            const characters = characterApi.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });

            global.MainComputerSystemScenarioRuntime = {current: () => scenario};
            global.MainComputerCharacterAIRuntime = {current: () => characters};
            global.document = {querySelector: () => null};

            const interaction = require(process.argv[4]);
            interaction.setRuntime(scenario);
            interaction.setCharacterRuntime(characters);
            scenario.startScenario(interaction.SCENARIO_ID, {nowMs: 10});

            interaction.boarderIds.forEach((id, index) => {
              characters.damageCharacter(id, 999, {
                sourceId: "player",
                nowMs: 20 + index
              });
            });
            scenario.syncCharacterRuntime(
              interaction.SCENARIO_ID,
              characters,
              {nowMs: 40}
            );

            const before = scenario.view(interaction.SCENARIO_ID);
            const reset = interaction.commands.resetProtectionEncounter(
              "architecture-characterization",
              {nowMs: 50}
            );
            const after = scenario.view(interaction.SCENARIO_ID);

            process.stdout.write(JSON.stringify({
              beforeStage: before.state.stageId,
              afterStage: after.state.stageId,
              reset: reset.reset,
              forced: reset.forced,
              evidenceIds: after.state.evidenceIds,
              metrics: after.state.metrics,
              boarders: interaction.boarderIds.map((id) => {
                const character = characters.character(id);
                return {
                  status: character.status,
                  health: character.health,
                  action: character.currentActionId
                };
              }),
              reasons: after.state.receipts.map((receipt) => receipt.reason)
            }));
            '''
        )
        self.assertEqual(result["beforeStage"], "investigation")
        self.assertTrue(result["reset"], result)
        self.assertTrue(result["forced"], result)
        self.assertEqual(result["afterStage"], "protect-witness")
        self.assertEqual(result["evidenceIds"], [])
        self.assertEqual(
            result["metrics"],
            {
                "weaponDischarges": 0,
                "defensiveDischarges": 0,
                "intimidationDischarges": 0,
            },
        )
        self.assertTrue(all(row["status"] == "active" for row in result["boarders"]))
        self.assertTrue(all(row["health"] > 0 for row in result["boarders"]))
        self.assertIn("protection-encounter-reset", result["reasons"])

    def test_stale_investigation_with_six_down_recovers_on_character_attach(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const characterApi = require(process.argv[2]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
            const scenario = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo",
              storage: null,
              restore: false,
              activeSystemId: "system.pax"
            });
            const characters = characterApi.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });

            global.MainComputerSystemScenarioRuntime = {current: () => scenario};
            global.MainComputerCharacterAIRuntime = {current: () => characters};
            global.document = {querySelector: () => null};

            const interaction = require(process.argv[4]);
            interaction.setRuntime(scenario);
            scenario.startScenario(interaction.SCENARIO_ID, {nowMs: 10});
            interaction.boarderIds.forEach((id, index) => {
              characters.damageCharacter(id, 999, {
                sourceId: "player",
                nowMs: 20 + index
              });
            });
            scenario.syncCharacterRuntime(
              interaction.SCENARIO_ID,
              characters,
              {nowMs: 40}
            );

            const before = scenario.view(interaction.SCENARIO_ID);
            interaction.setCharacterRuntime(characters);
            const after = scenario.view(interaction.SCENARIO_ID);

            process.stdout.write(JSON.stringify({
              beforeStage: before.state.stageId,
              afterStage: after.state.stageId,
              evidenceIds: after.state.evidenceIds,
              boarders: interaction.boarderIds.map((id) => {
                const character = characters.character(id);
                return {status: character.status, health: character.health};
              }),
              reasons: after.state.receipts.map((receipt) => receipt.reason)
            }));
            '''
        )
        self.assertEqual(result["beforeStage"], "investigation")
        self.assertEqual(result["afterStage"], "protect-witness")
        self.assertEqual(result["evidenceIds"], [])
        self.assertTrue(all(row["status"] == "active" for row in result["boarders"]))
        self.assertTrue(all(row["health"] > 0 for row in result["boarders"]))
        self.assertIn("protection-encounter-reset", result["reasons"])

    @unittest.expectedFailure
    def test_legitimate_completed_investigation_is_not_restarted_on_attach(self) -> None:
        """Known debt: startup recovery cannot yet distinguish completion from corruption."""
        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const characterApi = require(process.argv[2]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
            const scenario = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo",
              storage: null,
              restore: false,
              activeSystemId: "system.pax"
            });
            const characters = characterApi.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });

            global.MainComputerSystemScenarioRuntime = {current: () => scenario};
            global.MainComputerCharacterAIRuntime = {current: () => characters};
            global.document = {querySelector: () => null};

            const interaction = require(process.argv[4]);
            interaction.setRuntime(scenario);
            scenario.startScenario(interaction.SCENARIO_ID, {nowMs: 10});
            interaction.boarderIds.forEach((id, index) => {
              characters.damageCharacter(id, 999, {
                sourceId: "player",
                nowMs: 20 + index
              });
            });
            scenario.syncCharacterRuntime(
              interaction.SCENARIO_ID,
              characters,
              {nowMs: 40}
            );

            const completed = scenario.view(interaction.SCENARIO_ID);
            interaction.setCharacterRuntime(characters);
            const afterAttach = scenario.view(interaction.SCENARIO_ID);

            process.stdout.write(JSON.stringify({
              completedStage: completed.state.stageId,
              stageAfterAttach: afterAttach.state.stageId
            }));
            '''
        )
        self.assertEqual(result["completedStage"], "investigation")
        self.assertEqual(result["stageAfterAttach"], "investigation")


if __name__ == "__main__":
    unittest.main()
