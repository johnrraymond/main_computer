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
INTERACTION_PATH = SCRIPT_ROOT / "strategic-ai-solace-interaction.js"
INTERACTION_STYLE_PATH = STYLE_ROOT / "strategic-ai-solace-interaction.css"


class StrategicAISolaceLiveInteractionTests(unittest.TestCase):
    def run_node_scenario(self) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for the Solace live-interaction smoke")

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
            const interaction = require(path.join(root, "strategic-ai-solace-interaction.js"));

            function scenario(choice) {
              const session = new sessionApi.StrategicAISession("webgl-demo", project, {
                storage: null,
                restore: false,
                seed: 6206,
                activeSystemId: "system.solace-reach"
              });

              const before = interaction.buildViewModel(session);
              if (
                !before.visible
                || !before.canBegin
                || before.phase !== "ready"
                || before.shuttleQuantity !== 1
                || before.trust !== 0.55
              ) {
                throw new Error("Solace interaction did not start at the authored resource and trust state");
              }

              const started = interaction.beginEncounter(session);
              const promised = interaction.buildViewModel(session);
              if (
                started.reused
                || promised.phase !== "choice"
                || !promised.canChoose
                || promised.commitmentStatus !== "pending"
              ) {
                throw new Error("typed promise phase was not created");
              }
              if (
                !started.communication.text.includes("Osprey evacuation is critical")
                || !started.communication.text.includes("is in force")
              ) {
                throw new Error("knowledge-safe structured promise wording was not rendered");
              }
              if (
                started.communication.structuredCommitmentIds[0]
                !== started.commitment.commitmentId
              ) {
                throw new Error("promise wording was not bound to the structured commitment");
              }

              const resolved = interaction.resolveChoice(session, choice);
              const after = interaction.buildViewModel(session);
              if (
                resolved.reused
                || after.phase !== "complete"
                || after.canChoose
                || after.shuttleQuantity !== 0
                || after.canonicalRevision !== 2
              ) {
                throw new Error("Solace allocation did not reach one verified completed state");
              }

              const sequenceBeforeReplay = session.summary().sequence;
              const replay = interaction.resolveChoice(session, choice);
              if (!replay.reused || session.summary().sequence !== sequenceBeforeReplay) {
                throw new Error("completed Solace interaction was not idempotent");
              }

              const exported = session.exportSnapshot(2);
              const restored = new sessionApi.StrategicAISession("webgl-demo", project, {
                storage: null,
                restore: false,
                seed: 6206,
                activeSystemId: "system.solace-reach"
              });
              restored.restore(exported, {record: false});
              const restoredView = interaction.buildViewModel(restored);
              if (
                restoredView.phase !== "complete"
                || restoredView.commitmentStatus !== after.commitmentStatus
                || restoredView.ospreyActionTypeId !== after.ospreyActionTypeId
                || restoredView.canonicalRevision !== 2
              ) {
                throw new Error("completed Solace interaction did not survive snapshot restore");
              }

              return {
                before,
                promised,
                after,
                restored: restoredView,
                sequence: session.summary().sequence
              };
            }

            const kept = scenario("keep");
            const broken = scenario("divert");

            if (
              kept.after.commitmentStatus !== "kept"
              || kept.after.trust !== 0.91
              || kept.after.allocationActionTypeId
                !== "action.solace.allocate-shuttle-osprey"
              || kept.after.ospreyActionTypeId
                !== "action.solace.share-osprey-manifests"
              || kept.after.shuttleDestinationId
                !== "destination.solace-reach.osprey-anchorage"
            ) {
              throw new Error("kept-promise branch did not produce cooperative Osprey behavior");
            }
            if (
              broken.after.commitmentStatus !== "broken"
              || broken.after.trust !== 0.11
              || broken.after.allocationActionTypeId
                !== "action.solace.claim-shuttle-lyria"
              || broken.after.ospreyActionTypeId
                !== "action.solace.withhold-osprey-manifests"
              || broken.after.shuttleDestinationId
                !== "destination.solace-reach.lyria-transfer"
            ) {
              throw new Error("broken-promise branch did not produce distrustful Osprey behavior");
            }

            const away = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 6206,
              activeSystemId: "system.vela-gate"
            });
            const awayView = interaction.buildViewModel(away);
            if (awayView.visible || awayView.canBegin || awayView.phase !== "away") {
              throw new Error("Solace interaction appeared outside Solace Reach");
            }

            process.stdout.write(JSON.stringify({kept, broken, away: awayView}));
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
        return json.loads(result.stdout)

    def test_kept_and_broken_promises_change_later_cooperation(self) -> None:
        report = self.run_node_scenario()
        kept = report["kept"]["after"]
        broken = report["broken"]["after"]

        self.assertEqual(kept["commitmentStatus"], "kept")
        self.assertEqual(kept["trustPercent"], 91)
        self.assertEqual(
            kept["ospreyActionTypeId"],
            "action.solace.share-osprey-manifests",
        )
        self.assertIn("is kept", kept["promiseText"])
        self.assertEqual(
            [row["label"] for row in kept["outcomeRows"]],
            [
                "Promise",
                "Shuttle allocation",
                "Osprey trust",
                "Osprey response",
                "World state",
            ],
        )

        self.assertEqual(broken["commitmentStatus"], "broken")
        self.assertEqual(broken["trustPercent"], 11)
        self.assertEqual(
            broken["ospreyActionTypeId"],
            "action.solace.withhold-osprey-manifests",
        )
        self.assertIn("is broken", broken["promiseText"])
        self.assertEqual(kept["canonicalRevision"], broken["canonicalRevision"], 2)

    def test_solace_interaction_is_player_visible_and_loaded_before_webgl(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        webgl = WEBGL_APP_PATH.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP_PATH.read_text(encoding="utf-8")
        interaction = INTERACTION_PATH.read_text(encoding="utf-8").lower()
        style = INTERACTION_STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "<!-- @include applications/styles/strategic-ai-solace-interaction.css -->",
            applications,
        )
        self.assertIn(
            "<!-- @include applications/scripts/strategic-ai-solace-interaction.js -->",
            applications,
        )
        self.assertLess(
            applications.index("strategic-ai-solace-interaction.js"),
            applications.index("webgl-desktop.js"),
        )
        self.assertIn('id="solace-strategic-contact"', webgl)
        self.assertIn('id="solace-strategic-begin"', webgl)
        self.assertIn('id="solace-strategic-keep"', webgl)
        self.assertIn('id="solace-strategic-divert"', webgl)
        self.assertIn('id="solace-strategic-trust-fill"', webgl)
        self.assertIn(
            "MainComputerStrategicAISolaceInteraction?.setSession?.(session)",
            desktop,
        )
        self.assertIn("system.solace-reach", interaction)
        self.assertIn("commitment.solace.shuttle-to-osprey", interaction)
        self.assertIn("actor.solace.osprey-captain", interaction)
        self.assertNotIn("innerhtml", interaction)
        self.assertIn("container-type: inline-size", style)
        self.assertIn("@container (max-width: 520px)", style)
        self.assertTrue(INTERACTION_STYLE_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
