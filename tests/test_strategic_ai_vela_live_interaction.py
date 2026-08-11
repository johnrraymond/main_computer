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
WEBGL_APP_PATH = ROOT / "main_computer" / "web" / "applications" / "apps" / "webgl.html"
WEBGL_DESKTOP_PATH = SCRIPT_ROOT / "webgl-desktop.js"
INTERACTION_PATH = SCRIPT_ROOT / "strategic-ai-vela-interaction.js"
SCENE_VIEWER_PATH = SCRIPT_ROOT / "scene-viewer.js"
INTERACTION_STYLE_PATH = STYLE_ROOT / "strategic-ai-vela-interaction.css"


class StrategicAIVelaLiveInteractionTests(unittest.TestCase):
    def test_player_interaction_runs_verified_turn_and_safe_briefing_once(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the Vela live-interaction smoke")

        script = textwrap.dedent(
            """
            const fs = require("fs");
            const path = require("path");
            const root = process.argv[1];
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

            [
              "strategic-ai-runtime.js",
              "strategic-ai-action-runtime.js",
              "strategic-ai-social-runtime.js",
              "strategic-ai-commitment-runtime.js",
              "strategic-ai-director-runtime.js",
              "strategic-ai-communication-runtime.js",
              "strategic-ai-coordinator.js",
              "strategic-ai-offscreen-runtime.js"
            ].forEach((name) => require(path.join(root, name)));
            const sessionApi = require(path.join(root, "strategic-ai-session.js"));
            const interaction = require(path.join(root, "strategic-ai-vela-interaction.js"));

            const session = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 9912,
              activeSystemId: "system.vela-gate"
            });

            const before = interaction.buildViewModel(session);
            if (!before.visible || !before.canRun || before.phase !== "ready") {
              throw new Error("Vela interaction was not available in the active system");
            }

            const first = interaction.runInteraction(session);
            if (first.reused) throw new Error("first Vela interaction was incorrectly reused");
            if (first.turn.outcome.status !== "accepted") {
              throw new Error("Vela official turn did not commit");
            }
            if (
              first.turn.decision.selectedActionTypeId
              !== "action.vela.move-patrol-to-chiron"
            ) {
              throw new Error("unexpected Vela official action");
            }
            if (
              first.briefing.text
              !== "Our current assessment is that the customs explanation accounts for the incident."
            ) {
              throw new Error("unexpected or unsafe official briefing");
            }
            if (
              first.briefing.claimIds.includes(
                "communication-claim.vela.organizer-compromised"
              )
            ) {
              throw new Error("private organizer suspicion leaked into the briefing");
            }
            if (!first.escape?.trapTriggered || first.escape.phase !== "underground-captive") {
              throw new Error("first Vela interaction did not spring the underground trap");
            }

            const after = interaction.buildViewModel(session);
            if (after.phase !== "captive" || !after.canRun || !after.guardTakedownAvailable) {
              throw new Error("Vela trap did not move the investigation into actionable captive mode");
            }
            if (!after.escape?.trapTriggered || after.escape.stageId !== "captive-under-surface") {
              throw new Error("Vela underground escape scenario was not activated");
            }
            if (
              after.escape.locationId !== "destination.vela-gate.subsurface-cavern"
              || after.escape.guardState.watching !== 1
              || after.escape.playerEquipment.phaser !== "stripped"
              || after.escape.guardState.combatMode !== "hand-to-hand-pending"
            ) {
              throw new Error("Vela captive scenario did not describe the one-guard hand-to-hand setup");
            }
            if (after.actionLabel !== "Move patrol to Chiron") {
              throw new Error(`unexpected player-facing action label ${after.actionLabel}`);
            }
            if (after.alternatives.length !== 3 || after.scoreSignals.length !== 3) {
              throw new Error("decision explanation is incomplete");
            }
            if (
              after.scoreSignals[0].label !== "Mission priorities"
              || after.scoreSignals[0].assessment !== "Major influence"
            ) {
              throw new Error("player-facing signal wording was not applied");
            }
            if (
              after.alternatives[0].assessment !== "Viable alternative"
              || after.alternatives[1].assessment !== "Lower-confidence option"
            ) {
              throw new Error("player-facing alternative assessments were not applied");
            }
            if (after.resultingObservationCount !== 3) {
              throw new Error("actor learning consequence was not exposed");
            }
            if (
              after.consequenceRows.length !== 4
              || after.consequenceRows[0].label !== "Verification"
              || after.consequenceRows[0].value !== "Accepted by the action verifier"
              || after.consequenceRows[1].value !== "Advanced to revision 1"
              || after.consequenceRows[2].value !== "3 Vela actors received updates"
              || after.consequenceRows[3].value !== "1 patrol deployment"
            ) {
              throw new Error("verified consequence rows were not player-readable");
            }

            const sequenceBeforeReplay = session.summary().sequence;
            const second = interaction.runInteraction(session);
            if (!second.reused) throw new Error("completed interaction ran a second actor turn");
            if (session.summary().sequence !== sequenceBeforeReplay) {
              throw new Error("reopening the completed interaction mutated the session");
            }
            if (session.summary().canonicalRevision !== 1) {
              throw new Error("Vela interaction produced an unexpected revision count");
            }

            const away = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 9912,
              activeSystemId: "system.solace-reach"
            });
            const awayView = interaction.buildViewModel(away);
            if (awayView.visible || awayView.canRun || awayView.phase !== "away") {
              throw new Error("Vela interaction appeared outside Vela Gate");
            }

            process.stdout.write(JSON.stringify({
              actionTypeId: after.actionTypeId,
              briefingText: after.briefingText,
              confidence: after.confidence,
              revision: session.summary().canonicalRevision,
              sequence: session.summary().sequence,
              alternatives: after.alternatives.length,
              scoreSignals: after.scoreSignals.length,
              observations: after.resultingObservationCount,
              firstSignal: after.scoreSignals[0],
              firstAlternative: after.alternatives[0],
              consequences: after.consequenceRows,
              escape: after.escape
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(SCRIPT_ROOT), str(PROJECT_PATH)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["actionTypeId"], "action.vela.move-patrol-to-chiron")
        self.assertIn("customs explanation", report["briefingText"])
        self.assertEqual(report["revision"], 1)
        self.assertEqual(report["alternatives"], 3)
        self.assertEqual(report["scoreSignals"], 3)
        self.assertEqual(report["observations"], 3)
        self.assertEqual(report["firstSignal"]["label"], "Mission priorities")
        self.assertEqual(report["firstSignal"]["assessment"], "Major influence")
        self.assertEqual(
            report["firstAlternative"]["assessment"],
            "Viable alternative",
        )
        self.assertEqual(
            [row["label"] for row in report["consequences"]],
            ["Verification", "World state", "Shared knowledge", "Resource used"],
        )
        self.assertEqual(report["escape"]["stageId"], "captive-under-surface")
        self.assertEqual(report["escape"]["locationId"], "destination.vela-gate.subsurface-cavern")
        self.assertEqual(report["escape"]["playerEquipment"]["phaser"], "stripped")
        self.assertEqual(report["escape"]["guardState"]["watching"], 1)
        self.assertEqual(report["escape"]["guardState"]["combatMode"], "hand-to-hand-pending")



    def test_preexisting_briefing_state_still_exposes_trap_continuation(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the Vela reload-continuation smoke")

        script = textwrap.dedent(
            """
            const fs = require("fs");
            const path = require("path");
            const root = process.argv[1];
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

            [
              "strategic-ai-runtime.js",
              "strategic-ai-action-runtime.js",
              "strategic-ai-social-runtime.js",
              "strategic-ai-commitment-runtime.js",
              "strategic-ai-director-runtime.js",
              "strategic-ai-communication-runtime.js",
              "strategic-ai-coordinator.js",
              "strategic-ai-offscreen-runtime.js"
            ].forEach((name) => require(path.join(root, name)));

            const sessionApi = require(path.join(root, "strategic-ai-session.js"));
            const interactionPath = path.join(root, "strategic-ai-vela-interaction.js");
            const firstInteraction = require(interactionPath);

            const session = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 1208,
              activeSystemId: "system.vela-gate"
            });

            const first = firstInteraction.runInteraction(session);
            if (!first.escape?.trapTriggered) {
              throw new Error("test setup failed to create an accepted Vela briefing");
            }

            // Simulate the user's observed state: strategic turn/briefing receipts survived,
            // but the new in-memory underground escape state did not.
            delete require.cache[require.resolve(interactionPath)];
            const reloadedInteraction = require(interactionPath);

            const continuation = reloadedInteraction.buildViewModel(session);
            if (continuation.phase !== "trap-ready" || !continuation.canRun) {
              throw new Error(
                `preexisting Vela briefing was not actionable after reload: ${continuation.phase}/${continuation.canRun}`
              );
            }
            if (!continuation.trapContinuationAvailable) {
              throw new Error("trap continuation flag was not exposed");
            }

            const continued = reloadedInteraction.runInteraction(session);
            if (!continued.reused) {
              throw new Error("continuing from accepted briefing reran the official actor turn");
            }

            const after = reloadedInteraction.buildViewModel(session);
            if (after.phase !== "captive" || !after.canRun || !after.guardTakedownAvailable) {
              throw new Error("continuing the stale briefing state did not move to actionable captive mode");
            }
            if (
              after.escape.stageId !== "captive-under-surface"
              || after.escape.playerEquipment.phaser !== "stripped"
              || after.escape.guardState.watching !== 1
            ) {
              throw new Error("continued Vela state did not expose the captive one-guard setup");
            }

            process.stdout.write(JSON.stringify({
              continuationPhase: continuation.phase,
              continuationCanRun: continuation.canRun,
              continuationButtonMeaning: continuation.trapContinuationAvailable,
              afterPhase: after.phase,
              escapeStage: after.escape.stageId
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(SCRIPT_ROOT), str(PROJECT_PATH)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["continuationPhase"], "trap-ready")
        self.assertTrue(report["continuationCanRun"])
        self.assertTrue(report["continuationButtonMeaning"])
        self.assertEqual(report["afterPhase"], "captive")
        self.assertEqual(report["escapeStage"], "captive-under-surface")



    def test_vela_card_binds_after_webgl_markup_arrives_late(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the Vela late-DOM binding smoke")

        script = textwrap.dedent(
            """
            const fs = require("fs");
            const path = require("path");
            const root = process.argv[1];
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

            [
              "strategic-ai-runtime.js",
              "strategic-ai-action-runtime.js",
              "strategic-ai-social-runtime.js",
              "strategic-ai-commitment-runtime.js",
              "strategic-ai-director-runtime.js",
              "strategic-ai-communication-runtime.js",
              "strategic-ai-coordinator.js",
              "strategic-ai-offscreen-runtime.js"
            ].forEach((name) => require(path.join(root, name)));

            function fakeElement(id) {
              return {
                id,
                className: "",
                dataset: {},
                hidden: false,
                disabled: false,
                textContent: "",
                children: [],
                listeners: {},
                append(...items) {
                  this.children.push(...items);
                },
                replaceChildren(...items) {
                  this.children = [...items];
                },
                addEventListener(type, callback) {
                  this.listeners[type] = callback;
                },
                setAttribute(name, value) {
                  this[name] = String(value);
                }
              };
            }

            const observed = [];
            global.MutationObserver = class {
              constructor(callback) {
                this.callback = callback;
                this.disconnected = false;
                observed.push(this);
              }
              observe() {}
              disconnect() {
                this.disconnected = true;
              }
            };

            let installed = false;
            const elements = new Map();
            const ids = [
              "vela-gate-strategic-contact",
              "vela-gate-strategic-status",
              "vela-gate-strategic-request",
              "vela-gate-strategic-briefing",
              "vela-gate-strategic-action",
              "vela-gate-strategic-confidence",
              "vela-gate-strategic-reasons",
              "vela-gate-strategic-alternatives",
              "vela-gate-strategic-consequences",
              "vela-gate-strategic-explanation"
            ];
            ids.forEach((id) => elements.set(`#${id}`, fakeElement(id)));

            global.document = {
              body: fakeElement("body"),
              querySelector(selector) {
                return installed ? elements.get(selector) || null : null;
              },
              createElement(tagName) {
                return fakeElement(tagName);
              }
            };
            global.addEventListener = () => {};

            const sessionApi = require(path.join(root, "strategic-ai-session.js"));
            const interaction = require(path.join(root, "strategic-ai-vela-interaction.js"));

            if (!interaction.state.domObserver || observed.length !== 1) {
              throw new Error("Vela interaction did not watch for late WebGL markup");
            }

            installed = true;
            observed[0].callback();

            const session = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 777,
              activeSystemId: "system.vela-gate"
            });
            interaction.setSession(session);

            const panel = elements.get("#vela-gate-strategic-contact");
            const request = elements.get("#vela-gate-strategic-request");
            if (panel.hidden) {
              throw new Error("late-mounted Vela interaction panel remained hidden");
            }
            if (!request.listeners.click || request.dataset.velaGateRequestBound !== "true") {
              throw new Error("late-mounted Vela request button was not bound");
            }
            if (request.disabled || request.textContent !== "Start Vela investigation") {
              throw new Error("late-mounted Vela request was not visibly actionable");
            }

            request.listeners.click();

            const escape = interaction.velaEscapeScenarioSnapshot(session);
            if (escape.stageId !== "captive-under-surface") {
              throw new Error("late-mounted Vela request did not trigger the capture scenario");
            }
            if (!panel.hidden || panel.dataset.inWorldVelaEscape !== "true") {
              throw new Error("Vela strategic side panel did not hide for in-world cave play");
            }
            if (elements.has("#vela-gate-captive-action")) {
              throw new Error("test DOM should not expose a captive side-panel action");
            }

            const captiveView = interaction.buildViewModel(session);
            if (!captiveView.guardTakedownAvailable || !captiveView.canRun) {
              throw new Error("Vela captive state did not keep the guard takedown available to the renderer");
            }

            const breakout = interaction.resolveVelaGuardMelee({
              reason: "test-renderer-e-key-unarmed-melee",
              playerPosition: [1.05, 1.08, 1.08],
              guardPosition: [1.2, -0.03, 0.05],
              distance: 1.04
            });
            if (
              breakout.stageId !== "hand-to-hand-breakout"
              || breakout.guardState.watching !== 0
              || breakout.guardState.defeated !== 1
              || breakout.guardState.combatMode !== "guard-disabled"
            ) {
              throw new Error("Vela renderer melee command did not advance to the breakout stage");
            }
            const breakoutView = interaction.buildViewModel(session);
            if (!panel.hidden || breakoutView.phase !== "breakout" || !breakoutView.phaserRecoveryAvailable) {
              throw new Error("Vela side panel did not stay hidden after in-world guard takedown");
            }

            const armed = interaction.resolveVelaPhaserRecovery({
              reason: "test-renderer-e-key-recover-phaser",
              playerPosition: [1.78, 1.08, 0.82],
              pickupPosition: [1.78, -0.42, 0.82],
              distance: 0
            });
            const armedView = interaction.buildViewModel(session);
            if (
              armed.stageId !== "surface-transporter-extraction"
              || armed.playerEquipment.phaser !== "recovered"
              || armed.guardState.combatMode !== "phaser-recovered"
              || armedView.phase !== "armed-breakout"
            ) {
              throw new Error("Vela phaser recovery did not arm the player for the surface-transporter run");
            }

            process.stdout.write(JSON.stringify({
              panelHidden: panel.hidden,
              inWorldVelaEscape: panel.dataset.inWorldVelaEscape,
              escapeStage: escape.stageId,
              captiveCanRun: captiveView.canRun,
              guardTakedownAvailable: captiveView.guardTakedownAvailable,
              breakoutStage: breakout.stageId,
              breakoutGuardWatching: breakout.guardState.watching,
              breakoutPhase: breakoutView.phase,
              phaserRecoveryAvailable: breakoutView.phaserRecoveryAvailable,
              armedStage: armed.stageId,
              armedPhaser: armed.playerEquipment.phaser,
              armedPhase: armedView.phase,
              armedPanelHidden: panel.hidden,
              observerDisconnected: observed[0].disconnected
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(SCRIPT_ROOT), str(PROJECT_PATH)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report["panelHidden"])
        self.assertEqual(report["inWorldVelaEscape"], "true")
        self.assertEqual(report["escapeStage"], "captive-under-surface")
        self.assertTrue(report["captiveCanRun"])
        self.assertTrue(report["guardTakedownAvailable"])
        self.assertEqual(report["breakoutStage"], "hand-to-hand-breakout")
        self.assertEqual(report["breakoutGuardWatching"], 0)
        self.assertEqual(report["breakoutPhase"], "breakout")
        self.assertTrue(report["phaserRecoveryAvailable"])
        self.assertEqual(report["armedStage"], "surface-transporter-extraction")
        self.assertEqual(report["armedPhaser"], "recovered")
        self.assertEqual(report["armedPhase"], "armed-breakout")
        self.assertTrue(report["armedPanelHidden"])
        self.assertTrue(report["observerDisconnected"])


    def test_capture_syncs_the_renderer_into_subsurface_cave_mode(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the Vela renderer-sync smoke")

        script = textwrap.dedent(
            """
            const fs = require("fs");
            const path = require("path");
            const root = process.argv[1];
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

            [
              "strategic-ai-runtime.js",
              "strategic-ai-action-runtime.js",
              "strategic-ai-social-runtime.js",
              "strategic-ai-commitment-runtime.js",
              "strategic-ai-director-runtime.js",
              "strategic-ai-communication-runtime.js",
              "strategic-ai-coordinator.js",
              "strategic-ai-offscreen-runtime.js"
            ].forEach((name) => require(path.join(root, name)));

            const sessionApi = require(path.join(root, "strategic-ai-session.js"));
            const interaction = require(path.join(root, "strategic-ai-vela-interaction.js"));

            const rendererSyncs = [];
            global.document = {
              querySelector(selector) {
                if (selector !== "#webgl-demo") return null;
                return {
                  __mainComputerShuttle3dRenderer: {
                    syncVelaSubsurfaceScene(snapshot) {
                      rendererSyncs.push({
                        stageId: snapshot && snapshot.stageId,
                        locationId: snapshot && snapshot.locationId,
                        guardMode: snapshot && snapshot.guardState && snapshot.guardState.combatMode
                      });
                      return true;
                    }
                  }
                };
              }
            };

            const session = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 412,
              activeSystemId: "system.vela-gate"
            });
            interaction.setSession(session);

            const first = interaction.runInteraction(session);
            if (!first.escape || first.escape.stageId !== "captive-under-surface") {
              throw new Error("Vela capture did not enter the captive stage");
            }
            if (!rendererSyncs.length) {
              throw new Error("Vela capture did not notify the 3D renderer");
            }
            const active = interaction.activeVelaEscapeScenarioSnapshot();
            if (active.stageId !== "captive-under-surface") {
              throw new Error("active Vela escape snapshot did not expose the captive stage");
            }

            process.stdout.write(JSON.stringify({
              syncCount: rendererSyncs.length,
              lastSync: rendererSyncs[rendererSyncs.length - 1],
              activeStage: active.stageId,
              activeLocation: active.locationId
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(SCRIPT_ROOT), str(PROJECT_PATH)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertGreaterEqual(report["syncCount"], 1)
        self.assertEqual(report["lastSync"]["stageId"], "captive-under-surface")
        self.assertEqual(
            report["lastSync"]["locationId"],
            "destination.vela-gate.subsurface-cavern",
        )
        self.assertEqual(report["lastSync"]["guardMode"], "hand-to-hand-pending")
        self.assertEqual(report["activeStage"], "captive-under-surface")
        self.assertEqual(
            report["activeLocation"],
            "destination.vela-gate.subsurface-cavern",
        )

    def test_subsurface_cave_scene_has_no_prison_bars_and_supports_phaser_recovery(self) -> None:
        scene_source = SCENE_VIEWER_PATH.read_text(encoding="utf-8")
        interaction_source = INTERACTION_PATH.read_text(encoding="utf-8")

        self.assertNotIn("barCount", scene_source)
        self.assertNotIn("barred front", scene_source)
        self.assertIn("velaSubsurfacePhaserPickupPosition", scene_source)
        self.assertIn("velaSubsurfacePhaserAvailable", scene_source)
        self.assertIn("velaSubsurfaceGuardDefeated", scene_source)
        self.assertIn("const guardDefeated = this.velaSubsurfaceGuardDefeated(snapshot)", scene_source)
        self.assertNotIn('const guardDefeated = stageId === "hand-to-hand-breakout";', scene_source)
        self.assertIn("resolveVelaPhaserRecovery", scene_source)
        self.assertIn("return !this.velaSubsurfacePhaserAvailable?.()", scene_source)
        self.assertIn("resolveVelaGuardPhaserRecovery", interaction_source)
        self.assertIn("phaserRecoveryAvailable", interaction_source)
        self.assertIn("VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER", interaction_source)
        self.assertIn('"encounter-template.cave-combat-run"', interaction_source)
        self.assertIn('"objective-type.clear-hostiles"', interaction_source)
        self.assertIn("VELA_CAVE_ROOMS", interaction_source)
        self.assertIn("VELA_CAVE_HOSTILES", interaction_source)
        self.assertIn("resolveVelaCaveEnemyPhaserHit", interaction_source)
        self.assertIn("resolveVelaSurfaceTransporter", interaction_source)
        self.assertIn("velaSubsurfaceCaveSystem", scene_source)
        self.assertIn("velaSubsurfaceActiveCaveEnemies", scene_source)
        self.assertIn("velaSubsurfaceTransporterStatus", scene_source)
        self.assertIn("vela-cave.surface-transporter-room", scene_source)


    def test_vela_phaser_run_has_six_cave_sectors_hostiles_and_beam_back(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the Vela cave-system smoke")

        script = textwrap.dedent(
            """
            const fs = require("fs");
            const path = require("path");
            const root = process.argv[1];
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

            [
              "strategic-ai-runtime.js",
              "strategic-ai-action-runtime.js",
              "strategic-ai-social-runtime.js",
              "strategic-ai-commitment-runtime.js",
              "strategic-ai-director-runtime.js",
              "strategic-ai-communication-runtime.js",
              "strategic-ai-coordinator.js",
              "strategic-ai-offscreen-runtime.js"
            ].forEach((name) => require(path.join(root, name)));

            const sessionApi = require(path.join(root, "strategic-ai-session.js"));
            const interaction = require(path.join(root, "strategic-ai-vela-interaction.js"));
            global.document = {
              querySelector(selector) {
                if (selector !== "#webgl-demo") return null;
                return {
                  __mainComputerShuttle3dRenderer: {
                    syncVelaSubsurfaceScene() { return true; }
                  }
                };
              }
            };

            const session = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 412,
              activeSystemId: "system.vela-gate"
            });
            interaction.setSession(session);

            interaction.runInteraction(session);
            interaction.resolveVelaGuardMelee({reason: "test-melee"});
            const armed = interaction.resolveVelaPhaserRecovery({reason: "test-phaser-recovery"});
            if (armed.stageId !== "surface-transporter-extraction") {
              throw new Error("Vela phaser recovery did not enter the transporter run");
            }
            if (!armed.caveSystem || armed.caveSystem.roomCount !== 6) {
              throw new Error("Vela cave system did not expose six sectors");
            }
            if (
              armed.encounterTemplateId !== "encounter-template.cave-combat-run"
              || !armed.gameplayTemplate
              || armed.gameplayTemplate.id !== "built-in.vela-gate.subsurface-cave-escape"
              || armed.gameplayTemplate.templateId !== "encounter-template.cave-combat-run"
            ) {
              throw new Error("Vela escape snapshot did not declare its built-in cave-combat-run template consumer");
            }
            if (
              !armed.caveSystem.objectiveTypeIds.includes("objective-type.clear-hostiles")
              || !armed.caveSystem.objectiveTypeIds.includes("objective-type.reach-extraction")
              || !armed.caveSystem.actorArchetypeIds.includes("actor-archetype.vela-cave-guard")
              || !armed.caveSystem.consequenceTypeIds.includes("consequence-type.unlock-route")
            ) {
              throw new Error("Vela cave system did not expose registry-aligned gameplay primitives");
            }
            if (armed.caveSystem.enemiesActive !== 8) {
              throw new Error(`expected 8 active cave hostiles, got ${armed.caveSystem.enemiesActive}`);
            }

            const firstHit = interaction.resolveVelaCaveEnemyPhaserHit({
              enemyId: "enemy.vela.cave-guard-02",
              reason: "test-first-phaser-hit"
            });
            if (firstHit.caveSystem.enemiesActive !== 7 || firstHit.caveSystem.enemiesDefeated !== 1) {
              throw new Error("Vela cave hostile hit did not update enemy counts");
            }

            let snapshot = firstHit;
            for (const enemy of snapshot.caveSystem.enemies) {
              if (enemy.status !== "defeated") {
                snapshot = interaction.resolveVelaCaveEnemyPhaserHit({
                  enemyId: enemy.id,
                  reason: "test-clear-route"
                });
              }
            }
            if (snapshot.caveSystem.enemiesActive !== 0 || snapshot.extraction.beamBack !== "ready") {
              throw new Error("clearing cave hostiles did not unlock the transporter route");
            }

            const complete = interaction.resolveVelaSurfaceTransporter({
              reason: "test-beam-back"
            });
            if (
              complete.stageId !== "beam-back-complete"
              || complete.investigationComplete !== true
              || complete.extraction.beamBack !== "complete"
            ) {
              throw new Error("surface transporter did not complete the Vela escape");
            }

            process.stdout.write(JSON.stringify({
              roomCount: armed.caveSystem.roomCount,
              enemiesInitiallyActive: armed.caveSystem.enemiesActive,
              afterFirstHit: firstHit.caveSystem.enemiesActive,
              finalEnemiesActive: snapshot.caveSystem.enemiesActive,
              finalStage: complete.stageId,
              beamBack: complete.extraction.beamBack
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(SCRIPT_ROOT), str(PROJECT_PATH)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["roomCount"], 6)
        self.assertEqual(report["enemiesInitiallyActive"], 8)
        self.assertEqual(report["afterFirstHit"], 7)
        self.assertEqual(report["finalEnemiesActive"], 0)
        self.assertEqual(report["finalStage"], "beam-back-complete")
        self.assertEqual(report["beamBack"], "complete")



    def test_live_interaction_is_player_visible_and_loaded_before_webgl(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        webgl = WEBGL_APP_PATH.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP_PATH.read_text(encoding="utf-8")
        interaction = INTERACTION_PATH.read_text(encoding="utf-8").lower()
        scene_viewer = SCENE_VIEWER_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "<!-- @include applications/styles/strategic-ai-vela-interaction.css -->",
            applications,
        )
        self.assertIn(
            "<!-- @include applications/scripts/strategic-ai-vela-interaction.js -->",
            applications,
        )
        self.assertLess(
            applications.index("strategic-ai-vela-interaction.js"),
            applications.index("webgl-desktop.js"),
        )
        self.assertIn('id="vela-gate-strategic-contact"', webgl)
        self.assertIn('id="vela-gate-strategic-request"', webgl)
        self.assertIn('id="vela-gate-strategic-explanation"', webgl)
        self.assertNotIn('id="vela-gate-captive-scene"', webgl)
        self.assertNotIn('id="vela-gate-captive-action"', webgl)
        self.assertIn("MainComputerStrategicAIVelaInteraction?.setSession?.(session)", desktop)
        self.assertIn("system.vela-gate", interaction)
        self.assertIn("communicative-intent.vela.official-customs-briefing", interaction)
        self.assertIn("scenario.vela.underground-captivity-escape", interaction)
        self.assertIn("destination.vela-gate.subsurface-cavern", interaction)
        self.assertIn("hand-to-hand-pending", interaction)
        self.assertIn("resolvevelaguardtakedown", interaction)
        self.assertIn("resolvevelaguardmelee", interaction)
        self.assertIn("hand-to-hand-breakout", interaction)
        self.assertIn("activevelaescapescenariosnapshot", interaction)
        self.assertNotIn("innerhtml", interaction)

        self.assertIn("appendVelaSubsurfaceCaveGeometry", scene_viewer)
        self.assertIn("syncVelaSubsurfaceScene", scene_viewer)
        self.assertIn("isVelaSubsurfaceSceneActive", scene_viewer)
        self.assertIn("velaSubsurfaceMovementConfig", scene_viewer)
        self.assertIn("handleVelaSubsurfaceInteract", scene_viewer)
        self.assertIn("velaSubsurfaceGuardInteractionStatus", scene_viewer)
        self.assertIn("captive-under-surface", scene_viewer)
        self.assertIn("hand-to-hand-breakout", scene_viewer)
        self.assertIn("surface-transporter-extraction", scene_viewer)
        self.assertIn("vela-subsurface", scene_viewer)
        self.assertIn(
            "dockingCutsceneActive || shuttleBaySceneActive || velaSubsurfaceSceneActive",
            scene_viewer,
        )
        self.assertTrue(INTERACTION_STYLE_PATH.is_file())

        interaction_style = INTERACTION_STYLE_PATH.read_text(encoding="utf-8")
        debug_style = (
            STYLE_ROOT / "strategic-ai-debug.css"
        ).read_text(encoding="utf-8")
        self.assertIn("container-type: inline-size", interaction_style)
        self.assertIn("@container (max-width: 520px)", interaction_style)
        self.assertIn(
            ".vela-gate-strategic-section-consequences",
            interaction_style,
        )
        self.assertIn(
            '.vela-gate-strategic-request[data-state="complete"]',
            interaction_style,
        )
        self.assertIn(".vela-gate-captive-scene", interaction_style)
        self.assertIn(".vela-gate-captive-guard-marker", interaction_style)
        self.assertIn('.vela-gate-strategic-request[data-state="captive"]', interaction_style)
        self.assertIn("left: 12px", debug_style)
        self.assertIn("right: auto", debug_style)
        self.assertIn("What influenced the decision", webgl)
        self.assertIn("Other options considered", webgl)
        self.assertIn("Verified outcome", webgl)


if __name__ == "__main__":
    unittest.main()
