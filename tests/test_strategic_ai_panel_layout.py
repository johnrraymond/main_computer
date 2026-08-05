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
WEBGL_APP = ROOT / "main_computer" / "web" / "applications" / "apps" / "webgl.html"
LAYOUT_SCRIPT = SCRIPT_ROOT / "strategic-ai-panel-layout.js"
LAYOUT_STYLE = STYLE_ROOT / "strategic-ai-panel-layout.css"


class StrategicAIPanelLayoutTests(unittest.TestCase):
    def test_panel_modes_are_normalized_prioritized_and_persisted(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the strategic panel-layout smoke")

        script = textwrap.dedent(
            """
            const api = require(process.argv[1]);
            const values = new Map();
            const storage = {
              getItem(key) {
                return values.has(key) ? values.get(key) : null;
              },
              setItem(key, value) {
                values.set(key, String(value));
              }
            };

            if (api.normalizeMode("COMPACT") !== "compact") {
              throw new Error("compact mode normalization failed");
            }
            if (api.normalizeMode("unknown") !== "expanded") {
              throw new Error("invalid mode did not fall back to expanded");
            }

            api.writePanelMode("solace-reach", "collapsed", storage);
            if (api.readPanelMode("solace-reach", storage) !== "collapsed") {
              throw new Error("collapsed panel preference was not restored");
            }
            if (
              api.storageKey("solace-reach")
              !== "main-computer.strategic-ai.panel-mode.v1:solace-reach"
            ) {
              throw new Error("panel storage key is unstable");
            }

            const panels = [
              {hidden: false, dataset: {strategicPanelMode: "collapsed"}},
              {hidden: false, dataset: {strategicPanelMode: "compact"}},
              {hidden: true, dataset: {strategicPanelMode: "expanded"}}
            ];
            const dock = {
              querySelectorAll(selector) {
                if (selector !== "[data-strategic-ai-panel]") {
                  throw new Error(`unexpected selector ${selector}`);
                }
                return panels;
              }
            };
            const visible = api.visiblePanels(dock);
            if (visible.length !== 2) {
              throw new Error("hidden strategic panel remained active");
            }
            if (api.preferredDockMode(visible) !== "compact") {
              throw new Error("dock did not select the highest visible detail mode");
            }
            panels[0].dataset.strategicPanelMode = "expanded";
            if (api.preferredDockMode(visible) !== "expanded") {
              throw new Error("expanded panel did not request the expanded dock");
            }

            const properties = {};
            api.state.host = {
              clientWidth: 700,
              clientHeight: 380,
              dataset: {},
              style: {
                setProperty(name, value) {
                  properties[name] = value;
                }
              }
            };
            const metrics = api.syncHostMetrics();
            if (
              metrics.axis !== "bottom"
              || metrics.expandedHeight !== 160
              || api.state.host.dataset.strategicDockAxis !== "bottom"
            ) {
              throw new Error("narrow host metrics did not preserve the bottom drawer");
            }
            api.state.host.clientWidth = 1200;
            if (api.syncHostMetrics().axis !== "side") {
              throw new Error("wide host metrics did not restore the side dock");
            }

            process.stdout.write(JSON.stringify({
              storageEntries: values.size,
              storedMode: api.readPanelMode("solace-reach", storage),
              visiblePanels: visible.length,
              dockMode: api.preferredDockMode(visible),
              expandedHeight: metrics.expandedHeight,
              finalAxis: api.state.host.dataset.strategicDockAxis
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(LAYOUT_SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "storageEntries": 1,
                "storedMode": "collapsed",
                "visiblePanels": 2,
                "dockMode": "expanded",
                "expandedHeight": 160,
                "finalAxis": "side",
            },
        )

    def test_player_panels_share_a_non_obscuring_collapsible_dock(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        webgl = WEBGL_APP.read_text(encoding="utf-8")
        style = LAYOUT_STYLE.read_text(encoding="utf-8")
        script = LAYOUT_SCRIPT.read_text(encoding="utf-8").lower()

        self.assertEqual(webgl.count('data-strategic-ai-panel\n'), 3)
        self.assertEqual(webgl.count("data-strategic-ai-panel-collapse"), 3)
        self.assertEqual(webgl.count("data-strategic-ai-panel-compact"), 3)
        self.assertIn('id="strategic-ai-panel-dock"', webgl)
        self.assertLess(
            webgl.index('id="strategic-ai-panel-dock"'),
            webgl.index('id="strategic-ai-return-summary"'),
        )
        self.assertLess(
            webgl.index('id="solace-strategic-contact"'),
            webgl.index('id="strategic-ai-debug-toggle"'),
        )

        self.assertIn(
            "<!-- @include applications/styles/strategic-ai-panel-layout.css -->",
            applications,
        )
        self.assertIn(
            "<!-- @include applications/scripts/strategic-ai-panel-layout.js -->",
            applications,
        )
        self.assertGreater(
            applications.index("strategic-ai-panel-layout.css"),
            applications.index("strategic-ai-travel-integration.css"),
        )
        self.assertGreater(
            applications.index("strategic-ai-panel-layout.js"),
            applications.index("strategic-ai-solace-interaction.js"),
        )
        self.assertLess(
            applications.index("strategic-ai-panel-layout.js"),
            applications.index("scene-viewer.js"),
        )

        self.assertIn(
            '.canvas-wrap[data-strategic-dock-active="true"] #webgl-demo',
            style,
        )
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) var(--strategic-dock-width)",
            style,
        )
        self.assertIn(
            '.canvas-wrap[data-strategic-dock-axis="bottom"]',
            style,
        )
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) var(--strategic-dock-height)",
            style,
        )
        self.assertIn("--strategic-dock-collapsed-width: 72px", style)
        self.assertIn("--strategic-dock-collapsed-height: 64px", style)
        self.assertIn(
            '[data-strategic-ai-panel][data-strategic-panel-mode="collapsed"]',
            style,
        )
        self.assertIn("position: relative !important", style)
        self.assertIn("mutationobserver", script)
        self.assertIn("localstorage", script)
        self.assertNotIn("setinterval", script)
        self.assertNotIn("settimeout", script)
        self.assertNotIn("requestanimationframe", script)
        self.assertTrue(LAYOUT_STYLE.is_file())
        self.assertTrue(LAYOUT_SCRIPT.is_file())


if __name__ == "__main__":
    unittest.main()
