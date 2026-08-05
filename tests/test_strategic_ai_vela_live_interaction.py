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

            const after = interaction.buildViewModel(session);
            if (after.phase !== "complete" || after.canRun) {
              throw new Error("completed interaction remained runnable");
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
              consequences: after.consequenceRows
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

    def test_live_interaction_is_player_visible_and_loaded_before_webgl(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        webgl = WEBGL_APP_PATH.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP_PATH.read_text(encoding="utf-8")
        interaction = INTERACTION_PATH.read_text(encoding="utf-8").lower()

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
        self.assertIn("MainComputerStrategicAIVelaInteraction?.setSession?.(session)", desktop)
        self.assertIn("system.vela-gate", interaction)
        self.assertIn("communicative-intent.vela.official-customs-briefing", interaction)
        self.assertNotIn("innerhtml", interaction)
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
        self.assertIn("left: 12px", debug_style)
        self.assertIn("right: auto", debug_style)
        self.assertIn("What influenced the decision", webgl)
        self.assertIn("Other options considered", webgl)
        self.assertIn("Verified outcome", webgl)


if __name__ == "__main__":
    unittest.main()
