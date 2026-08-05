from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
SESSION_PATH = SCRIPT_ROOT / "strategic-ai-session.js"
PANEL_PATH = SCRIPT_ROOT / "strategic-ai-debug-panel.js"
WEBGL_DESKTOP_PATH = SCRIPT_ROOT / "webgl-desktop.js"
SCENE_VIEWER_PATH = SCRIPT_ROOT / "scene-viewer.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
WEBGL_APP_PATH = ROOT / "main_computer" / "web" / "applications" / "apps" / "webgl.html"
STYLE_PATH = (
    ROOT
    / "main_computer"
    / "web"
    / "applications"
    / "styles"
    / "strategic-ai-debug.css"
)
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"

RUNTIME_NAMES = (
    "strategic-ai-runtime.js",
    "strategic-ai-action-runtime.js",
    "strategic-ai-social-runtime.js",
    "strategic-ai-commitment-runtime.js",
    "strategic-ai-director-runtime.js",
    "strategic-ai-communication-runtime.js",
    "strategic-ai-coordinator.js",
    "strategic-ai-offscreen-runtime.js",
    "strategic-ai-session.js",
)


class StrategicAILiveSessionTests(unittest.TestCase):
    def test_live_session_owns_state_across_rerender_reload_and_snapshot_roundtrip(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the strategic live-session smoke")

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
            const api = require(path.join(root, "strategic-ai-session.js"));

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

            const first = api.ensure("webgl-demo", project, {
              storage,
              seed: 9917
            });
            if (first.summary().canonicalRevision !== 0) {
              throw new Error("session did not start at canonical revision zero");
            }
            const turn = first.runActorTurn("actor.fixture.watch-officer");
            if (turn.outcome.status !== "accepted") {
              throw new Error("live session did not execute a verified actor turn");
            }
            if (first.summary().canonicalRevision !== 1) {
              throw new Error("live session did not retain the committed revision");
            }

            const rerender = api.ensure("webgl-demo", project, {
              storage,
              seed: 9917
            });
            if (rerender !== first) {
              throw new Error("same-project rerender replaced the strategic session");
            }
            if (rerender.summary().canonicalRevision !== 1) {
              throw new Error("same-project rerender reset strategic state");
            }

            rerender.setActiveSystemId("system.vela-gate");
            const exported = rerender.exportSnapshot(2);
            api.clearCurrent();

            const reloaded = api.ensure("webgl-demo", project, {
              storage,
              seed: 9917
            });
            if (reloaded.summary().canonicalRevision !== 1) {
              throw new Error("persisted strategic revision was not restored");
            }
            if (reloaded.activeSystemId !== "system.vela-gate") {
              throw new Error("persisted navigation system was not restored");
            }

            reloaded.reset();
            if (reloaded.summary().canonicalRevision !== 0) {
              throw new Error("session reset did not restore project defaults");
            }
            reloaded.restore(exported);
            if (reloaded.summary().canonicalRevision !== 1) {
              throw new Error("snapshot import did not restore the verified state");
            }
            if (reloaded.activeSystemId !== "system.vela-gate") {
              throw new Error("snapshot import did not restore the active system");
            }

            const result = {
              schema: reloaded.snapshot().schema,
              definitionVersion: reloaded.summary().definitionVersion,
              stateVersion: reloaded.summary().stateVersion,
              revision: reloaded.summary().canonicalRevision,
              activeSystemId: reloaded.activeSystemId,
              turnId: turn.turnId,
              storageEntries: values.size
            };
            process.stdout.write(JSON.stringify(result));
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
        self.assertEqual(report["schema"], "game.strategicAI.session.v1")
        self.assertEqual(
            report["definitionVersion"],
            "game.strategicAI.definition.v8",
        )
        self.assertEqual(report["stateVersion"], "game.strategicAI.state.v8")
        self.assertEqual(report["revision"], 1)
        self.assertEqual(report["activeSystemId"], "system.vela-gate")
        self.assertTrue(report["turnId"].startswith("turn.runtime."))
        self.assertEqual(report["storageEntries"], 1)

    def test_snapshot_rejects_another_project_or_definition(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the strategic live-session smoke")

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
            const api = require(path.join(root, "strategic-ai-session.js"));
            const session = new api.StrategicAISession("webgl-demo", project, {
              storage: null,
              restore: false,
              seed: 17
            });
            const foreign = session.snapshot();
            foreign.projectId = "starter-game";
            let projectCode = "";
            try {
              session.restore(foreign);
            } catch (error) {
              projectCode = error.code;
            }
            if (projectCode !== "snapshot-project-mismatch") {
              throw new Error(`unexpected project mismatch code ${projectCode}`);
            }

            const stale = session.snapshot();
            stale.definitionFingerprint = "fnv1a-deadbeef";
            let definitionCode = "";
            try {
              session.restore(stale);
            } catch (error) {
              definitionCode = error.code;
            }
            if (definitionCode !== "snapshot-definition-mismatch") {
              throw new Error(`unexpected definition mismatch code ${definitionCode}`);
            }
            process.stdout.write(JSON.stringify({projectCode, definitionCode}));
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
        self.assertEqual(
            json.loads(result.stdout),
            {
                "projectCode": "snapshot-project-mismatch",
                "definitionCode": "snapshot-definition-mismatch",
            },
        )

    def test_live_harness_scripts_parse(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for JavaScript syntax checks")
        for name in RUNTIME_NAMES:
            path = SCRIPT_ROOT / name
            result = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_panel_markup_styles_and_script_order_are_present(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        webgl = WEBGL_APP_PATH.read_text(encoding="utf-8")
        styles = STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "<!-- @include applications/styles/strategic-ai-debug.css -->",
            applications,
        )
        self.assertLess(
            applications.index(
                "<!-- @include applications/scripts/strategic-ai-offscreen-runtime.js -->"
            ),
            applications.index(
                "<!-- @include applications/scripts/strategic-ai-session.js -->"
            ),
        )
        self.assertLess(
            applications.index(
                "<!-- @include applications/scripts/strategic-ai-session.js -->"
            ),
            applications.index(
                "<!-- @include applications/scripts/strategic-ai-debug-panel.js -->"
            ),
        )
        self.assertLess(
            applications.index(
                "<!-- @include applications/scripts/strategic-ai-debug-panel.js -->"
            ),
            applications.index(
                "<!-- @include applications/scripts/scene-viewer.js -->"
            ),
        )
        for element_id in (
            "strategic-ai-debug-toggle",
            "strategic-ai-debug-panel",
            "strategic-ai-debug-actor",
            "strategic-ai-debug-active-system",
            "strategic-ai-debug-target-time",
            "strategic-ai-debug-opportunity",
            "strategic-ai-debug-commitment-type",
            "strategic-ai-debug-intent",
            "strategic-ai-debug-snapshot",
            "strategic-ai-debug-output",
            "strategic-ai-debug-log",
        ):
            self.assertIn(f'id="{element_id}"', webgl)
        self.assertIn(
            'body:not([data-active-app="webgl"]) .strategic-ai-debug-panel',
            styles,
        )
        self.assertIn(".strategic-ai-debug-controls", styles)

    def test_game_surface_binds_project_navigation_and_panel_to_one_session(self) -> None:
        webgl = WEBGL_DESKTOP_PATH.read_text(encoding="utf-8")
        scene = SCENE_VIEWER_PATH.read_text(encoding="utf-8")
        panel = PANEL_PATH.read_text(encoding="utf-8")
        session = SESSION_PATH.read_text(encoding="utf-8")

        self.assertIn("function ensureWebglStrategicSession(", webgl)
        self.assertIn("window.MainComputerStrategicAISession", webgl)
        self.assertIn("window.MainComputerStrategicAIDebugPanel?.setSession?.", webgl)
        self.assertIn("onNavigationChanged: syncWebglStrategicNavigation", webgl)
        self.assertIn("session.setActiveSystemId(systemId)", webgl)
        self.assertIn('options.onNavigationChanged({...navigation})', scene)
        self.assertIn("function setSession(session)", panel)
        self.assertIn("session.runActorTurn(ui.actor.value)", panel)
        self.assertIn("session.advanceOffscreen(", panel)
        self.assertIn("session.exportSnapshot(2)", panel)
        self.assertIn("session.restore(ui.snapshot.value)", panel)
        self.assertIn("session.reset()", panel)
        self.assertIn("function ensure(projectId, project, options = {})", session)
        self.assertIn("currentSession.definitionFingerprint === fingerprint", session)


if __name__ == "__main__":
    unittest.main()
