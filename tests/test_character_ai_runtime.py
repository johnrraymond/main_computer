from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
STYLE_ROOT = ROOT / "main_computer" / "web" / "applications" / "styles"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
RUNTIME_PATH = SCRIPT_ROOT / "character-ai-runtime.js"
SCENE_VIEWER_PATH = SCRIPT_ROOT / "scene-viewer.js"
GAME_EDITOR_STYLE = STYLE_ROOT / "game-editor.css"


class CharacterAIRuntimeTests(unittest.TestCase):
    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for character AI runtime tests")
        result = subprocess.run(
            [
                "node",
                "-e",
                textwrap.dedent(script),
                str(RUNTIME_PATH),
                str(PROJECT_PATH),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_project_definition_has_two_stable_characters_and_raider_ship(self) -> None:
        project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
        definition = project["metadata"]["characterAI"]
        self.assertEqual(definition["schema"], "game.characterAI.v1")
        self.assertEqual(
            definition["definitionVersion"],
            "game.characterAI.definition.v1",
        )
        self.assertEqual(
            definition["stateVersion"],
            "game.characterAI.state.v1",
        )

        characters = {item["id"]: item for item in definition["characters"]}
        self.assertEqual(
            set(characters),
            {
                "enemy.raider-boarder-01",
                "npc.engineering-officer-01",
            },
        )
        self.assertEqual(
            characters["enemy.raider-boarder-01"]["kind"],
            "enemy",
        )
        self.assertIn(
            "attack_player",
            characters["enemy.raider-boarder-01"]["allowedActions"],
        )
        self.assertEqual(
            characters["npc.engineering-officer-01"]["kind"],
            "npc",
        )
        self.assertIn(
            "repair_power",
            characters["npc.engineering-officer-01"]["allowedActions"],
        )
        self.assertEqual(
            characters["npc.engineering-officer-01"]["activePhases"],
            ["mother-ship"],
        )

        vessels = {item["id"]: item for item in definition["vessels"]}
        self.assertEqual(set(vessels), {"ship.raider-01"})
        self.assertEqual(
            vessels["ship.raider-01"]["sceneObjectId"],
            "alien-raider",
        )

        result = self.run_node(
            r'''
            const fs = require("fs");
            const api = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const report = api.validateDefinition(project.metadata.characterAI);
            process.stdout.write(JSON.stringify({
              ok: report.ok,
              errors: report.errors,
              warnings: report.warnings,
              fingerprint: api.definitionFingerprint(report.definition)
            }));
            '''
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["errors"], [])
        self.assertRegex(result["fingerprint"], r"^fnv1a-[0-9a-f]{8}$")

    def test_enemy_and_engineer_follow_verified_deterministic_actions(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const api = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

            const enemyRuntime = api.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });
            const shuttleWorld = {
              phase: "shuttle",
              player: {
                alive: true,
                health: 100,
                position: [0, 0.75, 2.45]
              },
              ship: {
                power: "emergency",
                security: "quarantine",
                currentSystemId: "system.solace-reach"
              }
            };
            const enemyActions = [];
            const enemyEffects = [];
            [0, 650, 1300, 1950].forEach((nowMs) => {
              const result = enemyRuntime.step(shuttleWorld, nowMs);
              enemyActions.push(...result.decisions.map((item) => item.actionId));
              enemyEffects.push(...result.effects);
            });
            enemyRuntime.damageCharacter(
              "enemy.raider-boarder-01",
              40,
              {sourceId: "player", nowMs: 2000}
            );
            const cover = enemyRuntime.step(shuttleWorld, 2600);
            enemyRuntime.damageCharacter(
              "enemy.raider-boarder-01",
              20,
              {sourceId: "player", nowMs: 2700}
            );
            const retreat = enemyRuntime.step(shuttleWorld, 3250);

            const engineerRuntime = api.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });
            const engineeringWorld = {
              phase: "mother-ship",
              player: {
                alive: true,
                health: 100,
                position: [7, 0.9, -20]
              },
              ship: {
                power: "emergency",
                security: "quarantine",
                currentSystemId: "system.solace-reach"
              }
            };
            const repair = engineerRuntime.step(engineeringWorld, 0);
            engineeringWorld.ship.power = "online";
            const warning = engineerRuntime.step(engineeringWorld, 650);

            process.stdout.write(JSON.stringify({
              enemyActions,
              enemyEffects,
              coverAction: cover.decisions[0].actionId,
              retreatAction: retreat.decisions[0].actionId,
              enemy: enemyRuntime.character("enemy.raider-boarder-01"),
              repairAction: repair.decisions.find(
                (item) => item.characterId === "npc.engineering-officer-01"
              ).actionId,
              repairEffect: repair.effects.find(
                (item) => item.type === "repair-ship-power"
              ),
              warningAction: warning.decisions.find(
                (item) => item.characterId === "npc.engineering-officer-01"
              ).actionId,
              warningEffect: warning.effects.find(
                (item) => item.type === "character-message"
              )
            }));
            '''
        )

        self.assertEqual(
            result["enemyActions"],
            [
                "call_support",
                "move_to_player",
                "move_to_player",
                "attack_player",
            ],
        )
        self.assertEqual(result["enemyEffects"][0]["type"], "support-requested")
        self.assertEqual(
            result["enemyEffects"][0]["shipId"],
            "ship.raider-01",
        )
        self.assertEqual(result["enemyEffects"][-1]["type"], "damage-player")
        self.assertEqual(result["coverAction"], "take_cover")
        self.assertEqual(result["retreatAction"], "retreat")
        self.assertEqual(result["enemy"]["health"], 12)
        self.assertEqual(result["repairAction"], "repair_power")
        self.assertEqual(result["repairEffect"]["value"], "online")
        self.assertEqual(result["warningAction"], "warn_player")
        self.assertIn("Mara Venn", result["warningEffect"]["message"])

    def test_external_policy_is_nonblocking_and_illegal_results_fall_back(self) -> None:
        result = self.run_node(
            r'''
            (async () => {
              const fs = require("fs");
              const api = require(process.argv[1]);
              const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
              const world = {
                phase: "shuttle",
                player: {
                  alive: true,
                  health: 100,
                  position: [0, 0.75, 2.45]
                },
                ship: {
                  power: "emergency",
                  security: "quarantine",
                  currentSystemId: "system.solace-reach"
                }
              };

              const unavailableSeed = api.create(project.metadata.characterAI, {
                projectId: "webgl-demo",
                storage: null,
                restore: false
              }).snapshot();
              unavailableSeed.characters["enemy.raider-boarder-01"].policyId =
                "policy.test.not-registered";
              const unavailableRuntime = api.create(project.metadata.characterAI, {
                projectId: "webgl-demo",
                storage: null,
                state: unavailableSeed
              });
              const unavailable = unavailableRuntime.step(world, 0);

              const invalidRuntime = api.create(project.metadata.characterAI, {
                projectId: "webgl-demo",
                storage: null,
                restore: false
              });
              invalidRuntime.registerPolicy("policy.test.invalid", {
                chooseAction() {
                  return {
                    actionId: "delete_world",
                    targetId: "everything"
                  };
                }
              });
              invalidRuntime.setCharacterPolicy(
                "enemy.raider-boarder-01",
                "policy.test.invalid"
              );
              const invalid = invalidRuntime.step(world, 0);

              const remoteRuntime = api.create(project.metadata.characterAI, {
                projectId: "webgl-demo",
                storage: null,
                restore: false
              });
              const remote = api.createRemotePolicy(
                async (context) => ({
                  schema: api.POLICY_RESULT_SCHEMA,
                  requestId: context.requestId,
                  characterId: context.actor.id,
                  actionId: "move_to_player",
                  targetId: "player",
                  rationale: "remote policy selected a legal action"
                }),
                {id: "policy.test.remote"}
              );
              remoteRuntime.registerPolicy("policy.test.remote", remote);
              remoteRuntime.setCharacterPolicy(
                "enemy.raider-boarder-01",
                "policy.test.remote"
              );
              const first = remoteRuntime.step(world, 0);
              await new Promise((resolve) => setImmediate(resolve));
              const second = remoteRuntime.step(world, 650);

              process.stdout.write(JSON.stringify({
                unavailableAction: unavailable.decisions[0].actionId,
                unavailableFallback: unavailable.decisions[0].fallbackUsed,
                unavailableReason: unavailable.decisions[0].fallbackReason,
                invalidAction: invalid.decisions[0].actionId,
                invalidFallback: invalid.decisions[0].fallbackUsed,
                invalidReason: invalid.decisions[0].fallbackReason,
                firstAction: first.decisions[0].actionId,
                firstFallback: first.decisions[0].fallbackUsed,
                firstReason: first.decisions[0].fallbackReason,
                secondAction: second.decisions[0].actionId,
                secondFallback: second.decisions[0].fallbackUsed,
                secondReason: second.decisions[0].fallbackReason
              }));
            })().catch((error) => {
              console.error(error);
              process.exitCode = 1;
            });
            '''
        )

        self.assertEqual(result["unavailableAction"], "call_support")
        self.assertTrue(result["unavailableFallback"])
        self.assertEqual(result["unavailableReason"], "policy-unavailable")
        self.assertEqual(result["invalidAction"], "call_support")
        self.assertTrue(result["invalidFallback"])
        self.assertEqual(
            result["invalidReason"],
            "policy-result-action-not-allowed",
        )
        self.assertEqual(result["firstAction"], "call_support")
        self.assertTrue(result["firstFallback"])
        self.assertEqual(result["firstReason"], "policy-pending")
        self.assertEqual(result["secondAction"], "move_to_player")
        self.assertFalse(result["secondFallback"])
        self.assertEqual(result["secondReason"], "")

    def test_state_and_campaign_extension_restore_identity_memory_and_receipts(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const api = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const world = {
              phase: "shuttle",
              player: {
                alive: true,
                health: 100,
                position: [0, 0.75, 2.45]
              },
              ship: {
                power: "emergency",
                security: "quarantine",
                currentSystemId: "system.solace-reach"
              }
            };
            const source = api.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });
            source.step(world, 0);
            source.damageCharacter(
              "enemy.raider-boarder-01",
              34,
              {sourceId: "player", nowMs: 100}
            );
            source.markProtectedByPlayer(
              "npc.engineering-officer-01",
              120
            );
            const extension = source.campaignExtension();

            const restored = api.create(project.metadata.characterAI, {
              projectId: "webgl-demo",
              storage: null,
              restore: false
            });
            restored.restoreCampaignExtension(extension, {emit: false});
            const beforeStep = restored.snapshot();
            const early = restored.step(world, 10);
            const due = restored.step(world, 660);

            process.stdout.write(JSON.stringify({
              extensionSchema: extension.schema,
              sourceSequence: extension.characterAI.sequence,
              restoredSequence: beforeStep.sequence,
              sourceReceipts: extension.characterAI.receipts.length,
              restoredReceipts: beforeStep.receipts.length,
              enemy: restored.character("enemy.raider-boarder-01"),
              engineer: restored.character("npc.engineering-officer-01"),
              earlyDecisionCount: early.decisions.length,
              dueDecisionCount: due.decisions.length
            }));
            '''
        )

        self.assertEqual(
            result["extensionSchema"],
            "game.characterAI.campaignExtension.v1",
        )
        self.assertEqual(result["sourceSequence"], result["restoredSequence"])
        self.assertEqual(result["sourceReceipts"], result["restoredReceipts"])
        self.assertEqual(result["enemy"]["health"], 38)
        self.assertTrue(result["enemy"]["memory"]["supportCalled"])
        self.assertTrue(result["engineer"]["memory"]["protectedByPlayer"])
        self.assertEqual(result["earlyDecisionCount"], 0)
        self.assertGreaterEqual(result["dueDecisionCount"], 1)

    def test_game_surface_integrates_real_character_runtime_and_hud(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        scene = SCENE_VIEWER_PATH.read_text(encoding="utf-8")
        runtime = RUNTIME_PATH.read_text(encoding="utf-8")
        style = GAME_EDITOR_STYLE.read_text(encoding="utf-8")

        self.assertIn(
            "<!-- @include applications/scripts/character-ai-runtime.js -->",
            applications,
        )
        self.assertLess(
            applications.index("character-ai-runtime.js"),
            applications.index("scene-viewer.js"),
        )
        self.assertIn("createCharacterAIRuntime(options", scene)
        self.assertIn("appendCharacterAIGeometry(builder", scene)
        self.assertIn("updateCharacterAI(frameTime", scene)
        self.assertIn("damageCharacter(", scene)
        self.assertIn("markProtectedByPlayer(", scene)
        self.assertIn("!this.characterAIRuntime && nowMs >=", scene)
        self.assertIn("scene-shuttle3d-character-line", scene)
        self.assertIn(".scene-shuttle3d-character-line", style)

        self.assertIn("class DeterministicCharacterPolicy", runtime)
        self.assertIn("class RemoteCharacterPolicy", runtime)
        self.assertIn("registerPolicy(policyId, policy)", runtime)
        self.assertIn("function ensure(projectId, definition", runtime)
        self.assertIn("function current()", runtime)
        self.assertIn("normalizePolicyResult(", runtime)
        self.assertIn("validateAction(", runtime)
        self.assertIn("campaignExtension()", runtime)
        self.assertNotIn("setInterval(", runtime)
        self.assertNotIn("requestAnimationFrame(", runtime)


if __name__ == "__main__":
    unittest.main()
