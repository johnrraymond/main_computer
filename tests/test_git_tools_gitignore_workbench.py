from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
SCRIPTS = WEB_ROOT / "applications" / "scripts"


def _run_git_tools_gitignore_workbench_node() -> dict:
    gitignore_workbench = SCRIPTS / "git-tools-gitignore-workbench.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
const context = {{
  console,
  window: null,
  gitProjectLastInspection: {{project: {{id: "project-1"}}}},
  document: {{
    querySelectorAll: () => []
  }},
  escapeHtml: (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;"),
  gitProjectPathChips: (paths) => `<div data-path-chips>${{(paths || []).join("|")}}</div>`,
  gitToolsStatusApi: () => ({{
    saveGitignore: async (payload) => ({{ok: true, payload}})
  }}),
  gitToolsOperationErrorText: (prefix, error) => `${{prefix}}: ${{error.message || error}}`,
  currentGitProject: () => ({{id: "current-project"}})
}};
context.window = context;
context.window.addEventListener = () => null;
context.window.confirm = () => true;
vm.createContext(context);
vm.runInContext(fs.readFileSync({json.dumps(str(gitignore_workbench))}, "utf8"), context, {{filename: "git-tools-gitignore-workbench.js"}});
const api = context.GitToolsGitignoreWorkbench;
const html = api.gitProjectIgnoreWorkbenchHtml({{
  gitignore_file: {{
    exists: true,
    content_read: true,
    newline: "lf",
    path: ".gitignore",
    lines: [{{number: 1, text: "runtime/"}}]
  }},
  ignore_rules: ["dist/", "node_modules/"],
  questionable_ignore_rules: ["*.db"],
  paths: ["dist/app.js"]
}});
const normalized = api.gitProjectNormalizeGitignoreMatchText("  dist/\\n");
const baseline = api.gitProjectGitignoreBaselineLines({{
  content_read: true,
  lines: [{{text: "dist/\\r\\n"}}, {{text: "node_modules/"}}]
}});
console.log(JSON.stringify({{
  sourceFile: api.sourceFile,
  surfaceId: api.surfaceId,
  version: api.version,
  exportNames: Object.keys(api).sort(),
  compatHtml: context.gitProjectIgnoreWorkbenchHtml === api.gitProjectIgnoreWorkbenchHtml,
  compatInit: context.gitProjectInitializeGitignoreWorkbenches === api.gitProjectInitializeGitignoreWorkbenches,
  html,
  normalized,
  baseline
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_gitignore_workbench_loads_before_legacy_bridge() -> None:
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    legacy_bridge = (SCRIPTS / "git-tools-legacy-ui-bridge.js").read_text(encoding="utf-8")
    git_tools = (SCRIPTS / "git-tools.js").read_text(encoding="utf-8")
    gitignore_workbench = (SCRIPTS / "git-tools-gitignore-workbench.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-gitignore-workbench.js -->" in html
    assert html.index("git-tools-patch-inventory.js") < html.index("git-tools-gitignore-workbench.js")
    assert html.index("git-tools-gitignore-workbench.js") < html.index("task-manager.js")
    assert html.index("git-tools-gitignore-workbench.js") < html.index("git-tools.js")

    assert "git-tools.gitignore-workbench" in gitignore_workbench
    assert "global.GitToolsGitignoreWorkbench" in gitignore_workbench
    assert "function gitProjectIgnoreWorkbenchHtml(step = {})" in gitignore_workbench
    assert "function gitProjectSaveGitignoreWorkbench(workbench)" in gitignore_workbench
    assert "function gitProjectInitializeGitignoreWorkbenches(container)" in gitignore_workbench
    assert "function gitProjectConfirmDiscardGitignoreChanges(subscreen)" in gitignore_workbench

    assert "function gitProjectIgnoreWorkbenchHtml(step = {})" not in task_manager
    assert "function gitProjectSaveGitignoreWorkbench(workbench)" not in task_manager
    assert "function gitProjectInitializeGitignoreWorkbenches(container)" not in task_manager
    assert "function gitProjectConfirmDiscardGitignoreChanges(subscreen)" not in task_manager
    assert "gitProjectIgnoreWorkbenchHtml" in legacy_bridge
    assert "gitProjectInitializeGitignoreWorkbenches" in legacy_bridge
    assert "gitProjectConfirmDiscardGitignoreChanges" in legacy_bridge

    assert "GitToolsGitignoreWorkbench" in git_tools
    assert "gitignoreWorkbench" in git_tools


def test_git_tools_gitignore_workbench_exports_compatibility_surface_and_renders() -> None:
    report = _run_git_tools_gitignore_workbench_node()

    assert report["sourceFile"].endswith("git-tools-gitignore-workbench.js")
    assert report["surfaceId"] == "git-tools.gitignore-workbench"
    assert report["compatHtml"]
    assert report["compatInit"]
    assert "gitProjectIgnoreWorkbenchHtml" in report["exportNames"]
    assert "gitProjectSaveGitignoreWorkbench" in report["exportNames"]
    assert "gitProjectInitializeGitignoreWorkbenches" in report["exportNames"]

    assert report["normalized"] == "dist/"
    assert report["baseline"] == ["dist/", "node_modules/"]
    assert "Planner suggestions" in report["html"]
    assert "dist/" in report["html"]
    assert "Actual .gitignore on disk" in report["html"]
    assert "data-gitignore-baseline" in report["html"]
