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
SESSION_PATH = SCRIPT_ROOT / "strategic-ai-session.js"
TRAVEL_PATH = SCRIPT_ROOT / "strategic-ai-travel-integration.js"
TRAVEL_STYLE_PATH = STYLE_ROOT / "strategic-ai-travel-integration.css"


class StrategicAITravelIntegrationTests(unittest.TestCase):
    def test_completed_travel_advances_once_excludes_destination_and_builds_return_summary(
        self,
    ) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the strategic travel integration smoke")

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
            const travel = require(path.join(root, "strategic-ai-travel-integration.js"));

            const values = new Map();
            const storage = {
              getItem(key) {
                return values.has(key) ? values.get(key) : null;
              },
              setItem(key, value) {
                values.set(key, String(value));
              },
              removeItem(key) {
                values.delete(key);
              }
            };

            const session = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage,
              restore: false,
              seed: 9912,
              activeSystemId: "system.solace-reach"
            });

            const toVela = {
              currentSystemId: "system.vela-gate",
              lastCompletedRouteId: "route.solace-reach-vela-gate",
              lastArrivalAtMs: 4500,
              elapsedWorldTime: 4,
              travelPhase: "in-system",
              travelling: false
            };
            const first = travel.handleNavigation(session, toVela);
            if (first.reused) throw new Error("first completed travel was reused");
            if (session.summary().activeSystemId !== "system.vela-gate") {
              throw new Error("arrival did not update the active strategic system");
            }
            if (
              first.simulation.receipt.skippedScheduleIds.length !== 1
              || first.simulation.receipt.skippedScheduleIds[0]
                !== "offscreen-schedule.vela-gate-opening"
            ) {
              throw new Error("newly active Vela Gate was not excluded");
            }
            if (
              first.simulation.receipt.processedStepIds.join("|")
              !== [
                "offscreen-step.solace.create-shuttle-promise",
                "offscreen-step.solace.word-shuttle-promise",
                "offscreen-step.solace.allocate-shuttle"
              ].join("|")
            ) {
              throw new Error("Solace off-screen work was not processed deterministically");
            }
            if (session.summary().offscreenSimulationTime !== 4) {
              throw new Error("strategic time did not advance to navigation world time");
            }
            if (travel.buildViewModel(session).visible) {
              throw new Error("first visit incorrectly produced a return summary");
            }

            const sequenceAfterFirst = session.summary().sequence;
            const receiptCountAfterFirst = session.summary().offscreenReceiptCount;
            const repeatedFirst = travel.handleNavigation(session, toVela);
            if (!repeatedFirst.reused) {
              throw new Error("repeated arrival callback was not deduplicated");
            }
            if (
              session.summary().sequence !== sequenceAfterFirst
              || session.summary().offscreenReceiptCount !== receiptCountAfterFirst
            ) {
              throw new Error("repeated arrival callback advanced state twice");
            }

            const toSolace = {
              currentSystemId: "system.solace-reach",
              lastCompletedRouteId: "route.solace-reach-vela-gate",
              lastArrivalAtMs: 9000,
              elapsedWorldTime: 8,
              travelPhase: "in-system",
              travelling: false
            };
            const second = travel.handleNavigation(session, toSolace);
            if (second.reused) throw new Error("return travel was incorrectly reused");
            if (
              second.simulation.receipt.skippedScheduleIds[0]
              !== "offscreen-schedule.solace-reach-relief"
            ) {
              throw new Error("newly active Solace Reach was not excluded");
            }
            if (
              second.simulation.receipt.processedStepIds.join("|")
              !== [
                "offscreen-step.vela.activate-window",
                "offscreen-step.vela.inspect-traffic-gaps",
                "offscreen-step.vela.rumor-reaches-official"
              ].join("|")
            ) {
              throw new Error("Vela off-screen processing was not budgeted deterministically");
            }
            if (
              second.simulation.receipt.deferredStepIds.join("|")
              !== "offscreen-step.vela.official-briefing"
            ) {
              throw new Error("the four-unit travel budget did not defer the Vela briefing");
            }

            const view = travel.buildViewModel(session);
            if (!view.visible || view.systemLabel !== "Solace Reach") {
              throw new Error("return summary was not visible for the returned system");
            }
            if (view.changes.length !== 3) {
              throw new Error("return summary did not contain the three Solace developments");
            }
            if (
              view.changes.map((change) => change.status).join("|")
              !== "completed|completed|completed"
            ) {
              throw new Error("return summary statuses were not derived from receipts");
            }
            if (
              !view.changes[0].description.includes("typed rescue-shuttle promise")
              || !view.changes[2].description.includes("verified shuttle allocation")
            ) {
              throw new Error("return summary did not preserve authored descriptions");
            }

            const sequenceAfterSecond = session.summary().sequence;
            const repeatedSecond = travel.handleNavigation(session, toSolace);
            if (!repeatedSecond.reused || session.summary().sequence !== sequenceAfterSecond) {
              throw new Error("return arrival was processed more than once");
            }

            const acknowledged = session.acknowledgeReturnNotice(view.arrivalKey);
            if (!acknowledged.acknowledged || travel.buildViewModel(session).visible) {
              throw new Error("acknowledged return summary remained player-visible");
            }

            const exported = session.exportSnapshot(0);
            const processedBeforeReload = session.summary().processedTravelCount;
            const receiptsBeforeReload = session.summary().offscreenReceiptCount;
            const reloaded = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 9912
            });
            reloaded.restore(exported, {record: false});
            if (
              reloaded.summary().processedTravelCount !== processedBeforeReload
              || reloaded.summary().offscreenReceiptCount !== receiptsBeforeReload
              || reloaded.summary().returnNoticeAvailable
            ) {
              throw new Error("travel deduplication or acknowledgement did not survive restore");
            }
            const replayAfterReload = travel.handleNavigation(reloaded, toSolace);
            if (
              !replayAfterReload.reused
              || reloaded.summary().offscreenReceiptCount !== receiptsBeforeReload
            ) {
              throw new Error("restored session repeated a completed travel");
            }

            process.stdout.write(JSON.stringify({
              firstReceipt: first.simulation.receipt,
              secondReceipt: second.simulation.receipt,
              returnView: view,
              processedTravelCount: reloaded.summary().processedTravelCount,
              offscreenReceiptCount: reloaded.summary().offscreenReceiptCount,
              canonicalRevision: reloaded.summary().canonicalRevision,
              sequenceAfterSecond
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
        self.assertEqual(report["processedTravelCount"], 2)
        self.assertEqual(report["offscreenReceiptCount"], 2)
        self.assertEqual(report["canonicalRevision"], 2)
        self.assertEqual(report["returnView"]["systemLabel"], "Solace Reach")
        self.assertEqual(len(report["returnView"]["changes"]), 3)

    def test_incomplete_navigation_does_not_run_offscreen_progression(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the strategic travel integration smoke")

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
            const travel = require(path.join(root, "strategic-ai-travel-integration.js"));
            const session = new sessionApi.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 7,
              activeSystemId: "system.solace-reach"
            });
            const result = travel.handleNavigation(session, {
              currentSystemId: "system.solace-reach",
              destinationSystemId: "system.vela-gate",
              elapsedWorldTime: 0,
              travelPhase: "in-warp",
              travelling: true
            });
            if (!result.reused || result.reason !== "navigation-unchanged") {
              throw new Error("incomplete travel changed navigation state");
            }
            if (
              session.summary().offscreenReceiptCount !== 0
              || session.summary().processedTravelCount !== 0
              || session.summary().sequence !== 0
            ) {
              throw new Error("incomplete travel ran off-screen progression");
            }
            process.stdout.write(JSON.stringify(session.summary()));
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
        self.assertEqual(report["offscreenReceiptCount"], 0)
        self.assertEqual(report["processedTravelCount"], 0)

    def test_travel_controller_is_loaded_and_wired_to_game_surface(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        webgl = WEBGL_APP_PATH.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP_PATH.read_text(encoding="utf-8")
        session = SESSION_PATH.read_text(encoding="utf-8")
        travel = TRAVEL_PATH.read_text(encoding="utf-8").lower()
        style = TRAVEL_STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "<!-- @include applications/styles/strategic-ai-travel-integration.css -->",
            applications,
        )
        self.assertIn(
            "<!-- @include applications/scripts/strategic-ai-travel-integration.js -->",
            applications,
        )
        self.assertLess(
            applications.index("strategic-ai-session.js"),
            applications.index("strategic-ai-travel-integration.js"),
        )
        self.assertLess(
            applications.index("strategic-ai-travel-integration.js"),
            applications.index("webgl-desktop.js"),
        )
        self.assertIn('id="strategic-ai-return-summary"', webgl)
        self.assertIn('id="strategic-ai-return-dismiss"', webgl)
        self.assertIn("While you were away", webgl)
        self.assertIn("MainComputerStrategicAITravelIntegration?.setSession?.(session)", desktop)
        self.assertIn("integration.handleNavigation(session, navigation)", desktop)
        self.assertIn("completeTravel(navigation, options = {})", session)
        self.assertIn("processedArrivalKeys", session)
        self.assertIn("lastcompletedrouteid", travel)
        self.assertNotIn("setinterval", travel)
        self.assertNotIn("settimeout", travel)
        self.assertIn("position: absolute", style)
        self.assertIn("left: 18px", style)


if __name__ == "__main__":
    unittest.main()
