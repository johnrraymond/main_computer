from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
SCRIPTS = WEB_ROOT / "applications" / "scripts"


SPLIT_MODULES = {
    "git-tools-project-shared.js": "GitToolsProjectShared",
    "git-tools-commit-workbench.js": "GitToolsCommitWorkbench",
    "git-tools-secrets-filter-workbench.js": "GitToolsSecretsFilterWorkbench",
    "git-tools-archive-workbench.js": "GitToolsArchiveWorkbench",
    "git-tools-project-card-subscreen.js": "GitToolsProjectCardSubscreen",
    "git-tools-project-wizard-rendering.js": "GitToolsProjectWizardRendering",
    "git-tools-status-refresh-bridge.js": "GitToolsStatusRefreshBridge",
    "git-tools-page-wizard.js": "GitToolsPageWizard",
    "git-tools-shim-console.js": "GitToolsShimConsole",
}


def _run_split_module_node() -> dict:
    module_paths = [SCRIPTS / name for name in SPLIT_MODULES]
    legacy_bridge = SCRIPTS / "git-tools-legacy-ui-bridge.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
const moduleMap = {json.dumps(SPLIT_MODULES)};
const modulePaths = {json.dumps([str(path) for path in module_paths])};
const context = {{
  console,
  window: null,
  localStorage: {{getItem: () => null, setItem: () => null}},
  document: {{querySelector: () => null, querySelectorAll: () => [], createElement: () => ({{}})}},
  escapeHtml: (value) => String(value ?? ""),
  gitWorkflowSections: new Map(),
}};
context.window = context;
vm.createContext(context);
for (const filename of modulePaths) {{
  vm.runInContext(fs.readFileSync(filename, "utf8"), context, {{filename}});
}}
vm.runInContext(fs.readFileSync({json.dumps(str(legacy_bridge))}, "utf8"), context, {{filename: "git-tools-legacy-ui-bridge.js"}});
const globals = {{}};
for (const [filename, globalName] of Object.entries(moduleMap)) {{
  globals[filename] = {{
    present: Boolean(context[globalName]),
    surfaceId: context[globalName]?.surfaceId || "",
    exportCount: Object.keys(context[globalName] || {{}}).length,
  }};
}}
console.log(JSON.stringify({{
  globals,
  legacySurfaceId: context.GitToolsLegacyUiBridge?.surfaceId || "",
  legacyRole: context.GitToolsLegacyUiBridge?.role || "",
  legacyModuleGlobals: context.GitToolsLegacyUiBridge?.moduleGlobals || {{}},
  legacyReadiness: context.GitToolsLegacyUiBridge?.readiness?.() || {{}},
  hasCommitCompat: typeof context.gitProjectCommitWorkbenchHtml === "function",
  hasShimCompat: typeof context.refreshGitShims === "function",
  hasPageWizardCompat: typeof context.renderGitPageWizard === "function",
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_legacy_bridge_is_small_and_split_modules_are_loaded_before_task_manager() -> None:
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
    legacy_bridge = (SCRIPTS / "git-tools-legacy-ui-bridge.js").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")

    for filename, global_name in SPLIT_MODULES.items():
        module_text = (SCRIPTS / filename).read_text(encoding="utf-8")
        assert f"<!-- @include applications/scripts/{filename} -->" in html
        assert html.index(filename) < html.index("git-tools-legacy-ui-bridge.js")
        assert html.index(filename) < html.index("task-manager.js")
        assert global_name in module_text

    assert "global.GitToolsLegacyUiBridge" in legacy_bridge
    assert "post-split shared glue and readiness only" in legacy_bridge
    assert len(legacy_bridge.splitlines()) <= 80
    assert "function gitProjectCommitWorkbenchHtml" not in legacy_bridge
    assert "function renderGitProjectWizard" not in legacy_bridge
    assert "async function refreshGitShims" not in legacy_bridge
    assert "function gitProjectCommitWorkbenchHtml" not in task_manager
    assert "async function refreshGitTools" not in task_manager


def test_split_modules_export_compatibility_globals_and_bridge_reports_readiness() -> None:
    report = _run_split_module_node()

    assert report["legacySurfaceId"] == "git-tools.legacy-ui-bridge"
    assert report["legacyRole"] == "post-split shared glue and readiness only"
    assert set(report["legacyModuleGlobals"].values()) == set(SPLIT_MODULES.values())
    assert all(item["present"] for item in report["globals"].values())
    assert all(report["legacyReadiness"].values())
    assert report["hasCommitCompat"]
    assert report["hasShimCompat"]
    assert report["hasPageWizardCompat"]

    assert report["globals"]["git-tools-commit-workbench.js"]["surfaceId"] == "git-tools.commit-workbench"
    assert report["globals"]["git-tools-project-shared.js"]["surfaceId"] == "git-tools.project-shared"
    assert report["globals"]["git-tools-status-refresh-bridge.js"]["surfaceId"] == "git-tools.status-refresh-bridge"
