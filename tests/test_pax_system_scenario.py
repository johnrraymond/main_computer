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
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
WEBGL_HTML = ROOT / "main_computer" / "web" / "applications" / "apps" / "webgl.html"
SCENARIO_RUNTIME = SCRIPT_ROOT / "system-scenario-runtime.js"
CHARACTER_RUNTIME = SCRIPT_ROOT / "character-ai-runtime.js"
PAX_INTERACTION = SCRIPT_ROOT / "pax-scenario-interaction.js"
PAX_STYLE = STYLE_ROOT / "pax-scenario-interaction.css"
WEBGL_DESKTOP = SCRIPT_ROOT / "webgl-desktop.js"
SCENE_VIEWER = SCRIPT_ROOT / "scene-viewer.js"


class PaxSystemScenarioTests(unittest.TestCase):
    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for Pax scenario runtime tests")
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

    def test_pax_definition_and_complete_refugee_power_route(self) -> None:
        project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
        definition = project["metadata"]["systemScenarios"]
        self.assertEqual(definition["schema"], "game.systemScenarios.v1")
        self.assertEqual(
            definition["definitionVersion"],
            "game.systemScenarios.definition.v1",
        )
        self.assertEqual(
            definition["stateVersion"],
            "game.systemScenarios.state.v1",
        )

        scenario = definition["scenarios"][0]
        self.assertEqual(
            scenario["id"],
            "scenario.pax.neutrality-under-fire",
        )
        self.assertEqual(scenario["systemId"], "system.pax")
        self.assertEqual(
            scenario["completionCharacterId"],
            "enemy.pax.quiet-service-assassin-01",
        )
        self.assertEqual(len(scenario["evidence"]), 3)
        self.assertEqual(len(scenario["resolutions"]), 4)
        self.assertIn("Defensive force", scenario["localRule"])

        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const characterApi = require(process.argv[2]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

            const report = scenarioApi.validateDefinition(
              project.metadata.systemScenarios
            );
            const scenario = scenarioApi.create(
              project.metadata.systemScenarios,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false,
                activeSystemId: "system.pax"
              }
            );
            const characters = characterApi.create(
              project.metadata.characterAI,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false
              }
            );

            const before = scenario.view(
              "scenario.pax.neutrality-under-fire"
            );
            const started = scenario.startScenario(
              "scenario.pax.neutrality-under-fire",
              {nowMs: 10}
            );
            characters.damageCharacter(
              "enemy.pax.quiet-service-assassin-01",
              999,
              {sourceId: "player", nowMs: 20}
            );
            const synced = scenario.syncCharacterRuntime(
              "scenario.pax.neutrality-under-fire",
              characters,
              {nowMs: 30}
            );

            [
              "evidence.pax.weapon-serial",
              "evidence.pax.refugee-testimony",
              "evidence.pax.cutter-signal"
            ].forEach((evidenceId, index) => {
              scenario.recordEvidence(
                "scenario.pax.neutrality-under-fire",
                evidenceId,
                {nowMs: 40 + index}
              );
            });
            scenario.proceedToConference(
              "scenario.pax.neutrality-under-fire",
              {nowMs: 50}
            );
            const resolved = scenario.resolveScenario(
              "scenario.pax.neutrality-under-fire",
              "resolution.pax.refugee-power",
              {nowMs: 60}
            );
            const extension = scenario.campaignExtension();
            const restored = scenarioApi.create(
              project.metadata.systemScenarios,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false,
                activeSystemId: "system.solace-reach"
              }
            );
            restored.restoreCampaignExtension(extension, {emit: false});
            const restoredView = restored.view(
              "scenario.pax.neutrality-under-fire"
            );

            process.stdout.write(JSON.stringify({
              reportOk: report.ok,
              errors: report.errors,
              beforeStatus: before.state.status,
              beforeStage: before.state.stageId,
              startStage: started.view.state.stageId,
              syncChanged: synced.changed,
              syncStage: synced.view.state.stageId,
              finalStatus: resolved.view.state.status,
              finalStage: resolved.view.state.stageId,
              resolutionId: resolved.view.state.resolutionId,
              refugeeSupport:
                resolved.view.state.consequences.refugeeSupport,
              kestrelGateway:
                resolved.view.state.consequences.kestrelGateway,
              vesselStatus: resolved.view.vesselStatus,
              restoredStatus: restoredView.state.status,
              restoredEvidence: restoredView.state.evidenceIds.length,
              restoredResolution: restoredView.state.resolutionId,
              sequence: restored.snapshot().sequence,
              campaignSchema: extension.schema
            }));
            '''
        )

        self.assertTrue(result["reportOk"], result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["beforeStatus"], "available")
        self.assertEqual(result["beforeStage"], "arrival")
        self.assertEqual(result["startStage"], "protect-witness")
        self.assertTrue(result["syncChanged"])
        self.assertEqual(result["syncStage"], "investigation")
        self.assertEqual(result["finalStatus"], "resolved")
        self.assertEqual(result["finalStage"], "resolved")
        self.assertEqual(
            result["resolutionId"],
            "resolution.pax.refugee-power",
        )
        self.assertEqual(result["refugeeSupport"], "allied")
        self.assertEqual(
            result["kestrelGateway"],
            "civilian-courier-protected",
        )
        self.assertIn("joint civilian commission", result["vesselStatus"])
        self.assertEqual(result["restoredStatus"], "resolved")
        self.assertEqual(result["restoredEvidence"], 3)
        self.assertEqual(
            result["restoredResolution"],
            "resolution.pax.refugee-power",
        )
        self.assertGreaterEqual(result["sequence"], 7)
        self.assertEqual(
            result["campaignSchema"],
            "game.systemScenarios.campaignExtension.v1",
        )

    def test_pax_arrival_commit_starts_once_and_reload_resumes(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
            const interaction = require(process.argv[4]);

            const scenarioId = "scenario.pax.neutrality-under-fire";
            const runtime = scenarioApi.create(
              project.metadata.systemScenarios,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false,
                activeSystemId: "system.solace-reach"
              }
            );
            interaction.setRuntime(runtime);

            const ignored = interaction.handleNavigation({
              currentSystemId: "system.pax",
              travelPhase: "arriving",
              changeReason: "phase-arriving",
              lastCompletedRouteId: null,
              lastArrivalAtMs: null,
              sequence: 7
            });

            runtime.setActiveSystemId("system.pax", {
              nowMs: 100,
              record: false
            });
            const arrival = {
              currentSystemId: "system.pax",
              travelPhase: "in-system",
              changeReason: "arrival-committed",
              lastCompletedRouteId: "route.solace-pax",
              lastArrivalAtMs: 200,
              sequence: 8
            };
            const started = interaction.handleNavigation(arrival);
            const duplicate = interaction.handleNavigation(arrival);
            const startedView = runtime.view(scenarioId);
            const startReceipts = startedView.state.receipts.filter(
              (receipt) => receipt.reason === "scenario-started"
            );

            const snapshot = runtime.snapshot();
            const restored = scenarioApi.create(
              project.metadata.systemScenarios,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false,
                state: snapshot,
                activeSystemId: "system.pax"
              }
            );
            interaction.setRuntime(restored);
            const recovered = interaction.handleNavigation({
              currentSystemId: "system.pax",
              travelPhase: "in-system",
              lastCompletedRouteId: "route.solace-pax",
              lastArrivalAtMs: 200,
              sequence: 8
            });
            const restoredView = restored.view(scenarioId);
            const restoredStarts = restoredView.state.receipts.filter(
              (receipt) => receipt.reason === "scenario-started"
            );

            const legacy = scenarioApi.create(
              project.metadata.systemScenarios,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false,
                activeSystemId: "system.pax"
              }
            );
            interaction.setRuntime(legacy);
            const legacyStarted = interaction.handleNavigation({
              currentSystemId: "system.pax",
              travelPhase: "in-system",
              lastCompletedRouteId: null,
              lastArrivalAtMs: null,
              sequence: 3
            });
            const legacyView = legacy.view(scenarioId);
            const legacyObjective = interaction.objectivePresentation(
              legacyView
            );
            const cueUi = {
              briefing: {hidden: true, dataset: {}},
              objective: {hidden: true, dataset: {}},
              objectiveKicker: {textContent: ""},
              objectiveTitle: {textContent: ""},
              objectiveDetail: {textContent: ""}
            };
            const cues = interaction.renderMissionCues(cueUi, legacyView);

            process.stdout.write(JSON.stringify({
              ignoredHandled: ignored.handled,
              ignoredReason: ignored.reason,
              started: started.started,
              startedTrigger: started.receipt.trigger,
              startedActivationKey: started.receipt.activationKey,
              startedRouteId: started.receipt.routeId,
              startedNavigationSequence:
                started.receipt.navigationSequence,
              duplicateStarted: duplicate.started,
              duplicateReused: duplicate.reused,
              status: startedView.state.status,
              stageId: startedView.state.stageId,
              startReceiptCount: startReceipts.length,
              recoveredStarted: recovered.started,
              recoveredReused: recovered.reused,
              restoredStatus: restoredView.state.status,
              restoredStageId: restoredView.state.stageId,
              restoredStartReceiptCount: restoredStarts.length,
              legacyStarted: legacyStarted.started,
              legacyTrigger: legacyStarted.receipt.trigger,
              legacyActivationKey: legacyStarted.receipt.activationKey,
              legacyStageId: legacyView.state.stageId,
              legacyObjectiveVisible: legacyObjective.visible,
              legacyObjectiveUrgent: legacyObjective.urgent,
              legacyObjectiveTitle: legacyObjective.title,
              briefingVisible: !cueUi.briefing.hidden,
              objectiveVisible: !cueUi.objective.hidden,
              renderedObjectiveTitle: cueUi.objectiveTitle.textContent,
              cueKey: cues.cueKey
            }));
            """
        )

        self.assertFalse(result["ignoredHandled"], result)
        self.assertEqual(result["ignoredReason"], "phase-arriving")
        self.assertTrue(result["started"], result)
        self.assertEqual(
            result["startedTrigger"],
            "navigation-arrival",
        )
        self.assertIn("route.solace-pax", result["startedActivationKey"])
        self.assertEqual(result["startedRouteId"], "route.solace-pax")
        self.assertEqual(result["startedNavigationSequence"], 8)
        self.assertFalse(result["duplicateStarted"])
        self.assertTrue(result["duplicateReused"])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["stageId"], "protect-witness")
        self.assertEqual(result["startReceiptCount"], 1)
        self.assertFalse(result["recoveredStarted"])
        self.assertTrue(result["recoveredReused"])
        self.assertEqual(result["restoredStatus"], "active")
        self.assertEqual(result["restoredStageId"], "protect-witness")
        self.assertEqual(result["restoredStartReceiptCount"], 1)
        self.assertTrue(result["legacyStarted"], result)
        self.assertEqual(
            result["legacyTrigger"],
            "navigation-current-system-recovery",
        )
        self.assertIn("current-system-recovery", result["legacyActivationKey"])
        self.assertEqual(result["legacyStageId"], "protect-witness")
        self.assertTrue(result["legacyObjectiveVisible"])
        self.assertTrue(result["legacyObjectiveUrgent"])
        self.assertEqual(result["legacyObjectiveTitle"], "PROTECT NERA SAYE")
        self.assertTrue(result["briefingVisible"])
        self.assertTrue(result["objectiveVisible"])
        self.assertEqual(
            result["renderedObjectiveTitle"],
            "PROTECT NERA SAYE",
        )
        self.assertTrue(result["cueKey"])

    def test_pax_local_rule_distinguishes_defense_from_intimidation(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const characterApi = require(process.argv[2]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
            const scenario = scenarioApi.create(
              project.metadata.systemScenarios,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false,
                activeSystemId: "system.pax"
              }
            );
            const characters = characterApi.create(
              project.metadata.characterAI,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false
              }
            );

            scenario.startScenario(
              "scenario.pax.neutrality-under-fire",
              {nowMs: 1}
            );
            const defensive = scenario.recordPlayerAction(
              "scenario.pax.neutrality-under-fire",
              "weapon-discharge",
              {
                targetId: "enemy.pax.quiet-service-assassin-01",
                targetKind: "character",
                defensive: true
              },
              {nowMs: 2}
            );
            characters.damageCharacter(
              "enemy.pax.quiet-service-assassin-01",
              999,
              {sourceId: "player", nowMs: 3}
            );
            scenario.syncCharacterRuntime(
              "scenario.pax.neutrality-under-fire",
              characters,
              {nowMs: 4}
            );
            const intimidation = scenario.recordPlayerAction(
              "scenario.pax.neutrality-under-fire",
              "weapon-discharge",
              {
                targetId: "",
                targetKind: "none",
                defensive: false
              },
              {nowMs: 5}
            );
            [
              "evidence.pax.weapon-serial",
              "evidence.pax.refugee-testimony",
              "evidence.pax.cutter-signal"
            ].forEach((evidenceId, index) => scenario.recordEvidence(
              "scenario.pax.neutrality-under-fire",
              evidenceId,
              {nowMs: 10 + index}
            ));
            scenario.proceedToConference(
              "scenario.pax.neutrality-under-fire",
              {nowMs: 20}
            );
            const view = scenario.view(
              "scenario.pax.neutrality-under-fire"
            );
            const availability = Object.fromEntries(
              view.resolutions.map((item) => [item.id, {
                available: item.available,
                conductSatisfied: item.conductSatisfied,
                currentIntimidationShots: item.currentIntimidationShots,
                maxIntimidationShots: item.maxIntimidationShots
              }])
            );
            process.stdout.write(JSON.stringify({
              defensive: defensive.defensive,
              intimidation: intimidation.defensive,
              metrics: view.state.metrics,
              availability
            }));
            """
        )

        self.assertTrue(result["defensive"])
        self.assertFalse(result["intimidation"])
        self.assertEqual(result["metrics"]["weaponDischarges"], 2)
        self.assertEqual(result["metrics"]["defensiveDischarges"], 1)
        self.assertEqual(result["metrics"]["intimidationDischarges"], 1)
        self.assertFalse(
            result["availability"]["resolution.pax.refugee-power"]["available"]
        )
        self.assertFalse(
            result["availability"]["resolution.pax.expose-quiet-service"]["available"]
        )
        self.assertTrue(
            result["availability"]["resolution.pax.controlled-disclosure"]["available"]
        )
        self.assertTrue(
            result["availability"]["resolution.pax.withdraw"]["available"]
        )

    def test_character_runtime_gates_pax_cast_and_authorizes_cutter_support(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const characterApi = require(process.argv[2]);
            const project = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
            const characters = characterApi.create(
              project.metadata.characterAI,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false
              }
            );
            const scenario = scenarioApi.create(
              project.metadata.systemScenarios,
              {
                projectId: "webgl-demo",
                storage: null,
                restore: false,
                activeSystemId: "system.pax"
              }
            );

            const world = (systemId, stageId, status = "active") => ({
              phase: "mother-ship",
              player: {
                alive: true,
                health: 100,
                position: [0, -0.55, -31]
              },
              ship: {
                power: "online",
                security: "alert",
                currentSystemId: systemId
              },
              scenario: {
                id: "scenario.pax.neutrality-under-fire",
                status,
                stageId
              },
              canOccupy() {
                return true;
              }
            });

            const unavailable = characters.activeCharactersForWorld(
              world("system.pax", "arrival", "available")
            ).map((item) => item.id);
            scenario.startScenario(
              "scenario.pax.neutrality-under-fire",
              {nowMs: 1}
            );
            const paxWorld = world(
              "system.pax",
              scenario.activeScenarioContext().stageId
            );
            const active = characters.activeCharactersForWorld(paxWorld)
              .map((item) => item.id)
              .sort();
            const solace = characters.activeCharactersForWorld(
              world("system.solace-reach", "protect-witness")
            ).map((item) => item.id).sort();
            const step = characters.step(paxWorld, 1000);
            const assassin = step.decisions.find(
              (item) => (
                item.characterId
                === "enemy.pax.quiet-service-assassin-01"
              )
            );
            const support = step.effects.find(
              (item) => item.type === "support-requested"
            );
            const perception = characters.buildPerception(
              "enemy.pax.quiet-service-assassin-01",
              paxWorld,
              1000
            );

            process.stdout.write(JSON.stringify({
              unavailable,
              active,
              solace,
              assassinAction: assassin.actionId,
              assassinTarget: assassin.targetId,
              supportShipId: support.shipId,
              knownVessels:
                perception.ship.knownVessels.map((item) => item.id),
              supportVesselId: perception.actor.supportVesselId
            }));
            '''
        )

        self.assertEqual(result["unavailable"], [])
        self.assertEqual(
            result["active"],
            [
                "enemy.pax.quiet-service-assassin-01",
                "npc.pax.neutrality-marshal-01",
                "npc.pax.refugee-witness-01",
            ],
        )
        self.assertEqual(
            result["solace"],
            [
                "enemy.raider-boarder-01",
                "npc.engineering-officer-01",
            ],
        )
        self.assertEqual(result["assassinAction"], "call_support")
        self.assertEqual(
            result["assassinTarget"],
            "ship.pax.quiet-service-cutter-01",
        )
        self.assertEqual(
            result["supportShipId"],
            "ship.pax.quiet-service-cutter-01",
        )
        self.assertEqual(
            result["knownVessels"],
            [
                "ship.raider-01",
                "ship.pax.quiet-service-cutter-01",
            ],
        )
        self.assertEqual(
            result["supportVesselId"],
            "ship.pax.quiet-service-cutter-01",
        )

    def test_game_surface_exposes_pax_scenario_without_polling(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        webgl = WEBGL_HTML.read_text(encoding="utf-8")
        interaction = PAX_INTERACTION.read_text(encoding="utf-8").lower()
        style = PAX_STYLE.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        scene = SCENE_VIEWER.read_text(encoding="utf-8")
        runtime = SCENARIO_RUNTIME.read_text(encoding="utf-8").lower()

        self.assertIn(
            "<!-- @include applications/scripts/system-scenario-runtime.js -->",
            applications,
        )
        self.assertIn(
            "<!-- @include applications/scripts/pax-scenario-interaction.js -->",
            applications,
        )
        self.assertIn(
            "<!-- @include applications/styles/pax-scenario-interaction.css -->",
            applications,
        )
        self.assertLess(
            applications.index("system-scenario-runtime.js"),
            applications.index("scene-viewer.js"),
        )
        self.assertLess(
            applications.index("pax-scenario-interaction.js"),
            applications.index("scene-viewer.js"),
        )
        self.assertIn('id="pax-scenario-contact"', webgl)
        self.assertIn('data-strategic-ai-panel-id="pax-scenario"', webgl)
        self.assertIn('id="pax-scenario-arrival-briefing"', webgl)
        self.assertIn('id="pax-scenario-arrival-ack"', webgl)
        self.assertIn('id="pax-scenario-objective-banner"', webgl)
        self.assertIn("ASSASSIN ABOARD", webgl)
        self.assertIn("Bridge Access, behind the navigation stations", webgl)
        self.assertNotIn('id="pax-scenario-start"', webgl)
        self.assertNotIn("Take the conference protection detail", webgl)
        self.assertIn('id="pax-scenario-evidence-list"', webgl)
        self.assertIn('id="pax-scenario-resolution-list"', webgl)
        self.assertIn("ensureWebglSystemScenarioRuntime", desktop)
        self.assertIn("activeScenarioContext", scene)
        self.assertIn("recordPlayerAction", scene)
        self.assertIn("activeCharactersForWorld", scene)
        self.assertIn("synccharacterruntime", interaction)
        self.assertIn("handlenavigation", interaction)
        self.assertIn('"arrival-committed"', interaction)
        self.assertIn("navigation-current-system-recovery", interaction)
        self.assertIn("rendermissioncues", interaction)
        self.assertIn("objectivepresentation", interaction)
        self.assertIn("briefingacknowledged", interaction)
        self.assertIn("paxinteraction?.handlenavigation?.(navigation)", desktop.lower())
        self.assertIn('update.arrived ? "arrival-committed" : ""', scene)
        self.assertIn("proceedtoconference", interaction)
        self.assertIn("resolvescenario", interaction)
        self.assertIn("campaignextension()", runtime)
        self.assertNotIn("setinterval", interaction)
        self.assertNotIn("requestanimationframe", interaction)
        self.assertNotIn("setinterval", runtime)
        self.assertIn(
            '[data-strategic-panel-mode="compact"] .pax-scenario-local-rule',
            style,
        )
        self.assertIn("@media (max-width: 620px)", style)
        self.assertIn(".pax-scenario-arrival-briefing", style)
        self.assertIn(".pax-scenario-objective-banner", style)
        self.assertIn("@keyframes pax-arrival-pulse", style)


if __name__ == "__main__":
    unittest.main()
