from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
SCRIPTS = WEB_ROOT / "applications" / "scripts"


def _run_legacy_bridge_node() -> dict:
    legacy_bridge = SCRIPTS / "git-tools-legacy-ui-bridge.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync({json.dumps(str(legacy_bridge))}, "utf8"), {{filename: "git-tools-legacy-ui-bridge.js"}});
const api = globalThis.GitToolsLegacyUiBridge;
console.log(JSON.stringify({{
  sourceFile: api.sourceFile,
  surfaceId: api.surfaceId,
  exportNames: Object.keys(api).sort(),
  refreshType: typeof globalThis.refreshGitTools,
  commitWorkbenchType: typeof globalThis.gitProjectCommitWorkbenchHtml,
  wizardType: typeof globalThis.renderGitProjectWizard,
  shimType: typeof globalThis.refreshGitShims,
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_legacy_ui_bridge_load_order_and_task_manager_boundary() -> None:
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    legacy_bridge = (SCRIPTS / "git-tools-legacy-ui-bridge.js").read_text(encoding="utf-8")
    git_tools = (SCRIPTS / "git-tools.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-legacy-ui-bridge.js -->" in html
    assert html.index("git-tools-status-api.js") < html.index("git-tools-legacy-ui-bridge.js")
    assert html.index("git-tools-legacy-ui-bridge.js") < html.index("git-tools-server-panel.js")
    assert html.index("git-tools-legacy-ui-bridge.js") < html.index("task-manager.js")
    assert html.index("git-tools-legacy-ui-bridge.js") < html.index("git-tools.js")

    assert "git-tools.legacy-ui-bridge" in legacy_bridge
    assert "global.GitToolsLegacyUiBridge" in legacy_bridge
    assert "function gitProjectCommitWorkbenchHtml(" in legacy_bridge
    assert "function renderGitProjectWizard(" in legacy_bridge
    assert "async function refreshGitTools(" in legacy_bridge
    assert "async function refreshGitStatus(" in legacy_bridge
    assert "async function refreshGitShims(" in legacy_bridge

    assert "function gitProjectCommitWorkbenchHtml(" not in task_manager
    assert "function renderGitProjectWizard(" not in task_manager
    assert "async function refreshGitTools(" not in task_manager
    assert "async function refreshGitStatus(" not in task_manager
    assert "async function refreshGitShims(" not in task_manager

    assert "legacyUiBridge" in git_tools
    assert "GitToolsLegacyUiBridge" in git_tools


def test_git_tools_legacy_ui_bridge_exports_compatibility_globals() -> None:
    report = _run_legacy_bridge_node()

    assert report["sourceFile"].endswith("git-tools-legacy-ui-bridge.js")
    assert report["surfaceId"] == "git-tools.legacy-ui-bridge"
    assert report["refreshType"] == "function"
    assert report["commitWorkbenchType"] == "function"
    assert report["wizardType"] == "function"
    assert report["shimType"] == "function"

    expected = {
        "refreshGitTools",
        "refreshGitStatus",
        "gitProjectCommitWorkbenchHtml",
        "renderGitProjectWizard",
        "renderGitPageWizard",
        "refreshGitShims",
    }
    assert expected.issubset(set(report["exportNames"]))
