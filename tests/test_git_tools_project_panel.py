from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
WEB_APP = WEB_ROOT / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_git_tools_project_panel_node() -> dict:
    project_panel = SCRIPTS / "git-tools-project-panel.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
const lifecycle = [];
function element(name) {{
  return {{
    name,
    value: "",
    innerHTML: "",
    textContent: "",
    dispatchEvent: (event) => lifecycle.push({{name, event: event.type}}),
    querySelectorAll: (_selector) => []
  }};
}}
const projectList = element("gitProjectList");
const archiveList = element("gitProjectArchiveList");
const currentNode = element("gitProjectCurrent");
const context = {{
  console,
  window: null,
  gitProjectsLastState: null,
  gitProjectLastInspection: null,
  gitProjectCurrent: currentNode,
  gitProjectList: projectList,
  gitProjectArchiveList: archiveList,
  gitProjectPath: element("gitProjectPath"),
  gitRepoDir: element("gitRepoDir"),
  gitProjectDashboard: element("gitProjectDashboard"),
  gitConsoleOutput: element("gitConsoleOutput"),
  CSS: {{escape: (value) => String(value).replace(/[^a-zA-Z0-9_-]/g, "_")}},
  Event: function Event(type, _options) {{ this.type = type; }},
  document: {{
    querySelector: (_selector) => null,
    querySelectorAll: (_selector) => []
  }},
  localStorage: {{
    data: {{}},
    getItem(key) {{ return this.data[key] || null; }},
    setItem(key, value) {{ this.data[key] = String(value); }}
  }},
  escapeHtml: (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;"),
  gitProjectMcSlug: (value, fallback = "item") => String(value || fallback).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || fallback,
  gitProjectMcComponentAttrs: (componentId, kind, label, owner = "owner") => `data-mc-component-id="${{componentId}}" data-mc-component-kind="${{kind}}" data-mc-component-label="${{label}}" data-mc-component-owner="${{owner}}"`,
  gitProjectSetTargetPathInputs: (path) => lifecycle.push({{name: "gitProjectSetTargetPathInputs", path}}),
  renderGitProjectNextStep: (data) => lifecycle.push({{name: "renderGitProjectNextStep", projectId: data?.project?.id || null}}),
  renderGitProjectInspection: (data) => lifecycle.push({{name: "renderGitProjectInspection", projectId: data?.project?.id || null}}),
  setGitProjectNextStep: (...args) => lifecycle.push({{name: "setGitProjectNextStep", args}}),
  refreshGitServerTargetPrefunk: (options) => {{ lifecycle.push({{name: "refreshGitServerTargetPrefunk", options}}); return Promise.resolve(null); }},
  clearGitServerTargetForProjectChange: () => lifecycle.push({{name: "clearGitServerTargetForProjectChange"}}),
  refreshGitStatus: () => {{ lifecycle.push({{name: "refreshGitStatus"}}); return Promise.resolve(null); }},
  gitToolsOperationErrorText: (prefix, error) => `${{prefix}}: ${{error.message || error}}`,
  currentGitProject: () => context.gitProjectsLastState?.current_project || null,
  gitProjectWizardActionMap: new Map(),
  gitProjectExecutableLinesFromCommands: (commands) => (Array.isArray(commands) ? commands : []).filter(Boolean),
  gitProjectCommandIsRunnable: (command) => String(command || "").startsWith("git "),
  expandGitWorkflowSection: (...args) => lifecycle.push({{name: "expandGitWorkflowSection", args}}),
  updateGitWorkflowSectionSummary: (...args) => lifecycle.push({{name: "updateGitWorkflowSectionSummary", args}}),
  showGitConsolePayload: (data) => lifecycle.push({{name: "showGitConsolePayload", ok: data.ok}}),
  gitProjectPanelStateForStep: () => ({{repo: "repo"}}),
  gitProjectWizardStepComponentId: () => "step.component",
  gitProjectVisibleStepLabel: () => "Step",
  gitProjectRunnableCommandInfo: () => ({{details: "git status", state: {{repo: "repo"}}}}),
}};
context.window = context;
const apiCalls = [];
const statusApi = {{
  fetchProjects: async () => {{
    apiCalls.push({{name: "fetchProjects"}});
    return {{
      current_project_id: "p1",
      current_project: {{id: "p1", name: "Project One", path: "C:/repo", last_inspection: {{is_git_repo: true, has_head: false, dirty_score: 42}}}},
      projects: [{{id: "p1", name: "Project One", path: "C:/repo", can_archive: true}}],
      archived_projects: []
    }};
  }},
  inspectProject: async (payload) => {{
    apiCalls.push({{name: "inspectProject", payload}});
    return {{project: {{id: "p1", name: "Project One", path: "C:/repo"}}, selected_project: "C:/repo"}};
  }},
  setProjectLock: async (payload) => {{
    apiCalls.push({{name: "setProjectLock", payload}});
    return context.gitProjectsLastState;
  }},
  runProjectAction: async (payload) => {{
    apiCalls.push({{name: "runProjectAction", payload}});
    return {{ok: true, operation: {{logs: []}}}};
  }},
  fetchOperationStatus: async () => ({{active: null}}),
  cancelOperation: async () => ({{ok: true}})
}};
context.gitToolsStatusApi = () => statusApi;
vm.createContext(context);
vm.runInContext(fs.readFileSync({json.dumps(str(project_panel))}, "utf8"), context, {{filename: "git-tools-project-panel.js"}});
(async () => {{
  const api = context.GitToolsProjectPanel;
  const badges = api.projectBadges({{vip: true, locked: true, last_inspection: {{is_git_repo: true, has_head: false, dirty_score: 42}}}});
  api.renderGitProjectList(projectList, [{{id: "p1", name: "Project One", path: "C:/repo", can_archive: true}}], {{archived: false}});
  await api.loadGitProjects();
  api.appendGitProjectActionHistory("action-1", {{status: "completed", label: "Action", result: {{returncode: 0}}}});
  console.log(JSON.stringify({{
    sourceFile: api.sourceFile,
    surfaceId: api.surfaceId,
    exportNames: Object.keys(api).sort(),
    compatLoad: context.loadGitProjects === api.loadGitProjects,
    compatInspect: context.inspectSelectedGitProject === api.inspectSelectedGitProject,
    badges,
    projectListHtml: projectList.innerHTML,
    currentHtml: currentNode.innerHTML,
    archiveHtml: archiveList.innerHTML,
    apiCalls,
    lifecycle,
    stateProjectId: context.gitProjectsLastState?.current_project?.id || null,
    historyRaw: context.localStorage.data["main-computer.git-project-action-history.p1.action-1"] || ""
  }}));
}})().catch((error) => {{
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
}});
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_project_panel_module_load_order_and_static_boundary() -> None:
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    project_panel = (SCRIPTS / "git-tools-project-panel.js").read_text(encoding="utf-8")
    git_tools = (SCRIPTS / "git-tools.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-project-panel.js -->" in html
    assert html.index("git-tools-status-api.js") < html.index("git-tools-project-panel.js")
    assert html.index("git-tools-server-panel.js") < html.index("git-tools-project-panel.js")
    assert html.index("git-tools-project-panel.js") < html.index("task-manager.js")
    assert html.index("git-tools-project-panel.js") < html.index("git-tools.js")

    assert "global.GitToolsProjectPanel" in project_panel
    assert "git-tools.project-panel" in project_panel
    assert "async function loadGitProjects()" in project_panel
    assert "function renderGitProjectList(" in project_panel
    assert "async function handleGitProjectAction(" in project_panel
    assert "async function inspectSelectedGitProject(" in project_panel
    assert "function renderGitProjectCommandBox(" in project_panel
    assert "async function runGitProjectAction(" in project_panel

    assert "function loadGitProjects()" not in task_manager
    assert "function renderGitProjectList(" not in task_manager
    assert "async function handleGitProjectAction(" not in task_manager
    assert "async function inspectSelectedGitProject(" not in task_manager
    assert "async function runGitProjectAction(" not in task_manager

    assert "GitToolsProjectPanel" in git_tools


def test_project_panel_exports_compatibility_globals_and_preserves_project_flow() -> None:
    report = _run_git_tools_project_panel_node()

    assert report["sourceFile"].endswith("git-tools-project-panel.js")
    assert report["surfaceId"] == "git-tools.project-panel"
    assert report["compatLoad"]
    assert report["compatInspect"]

    expected_exports = {
        "loadGitProjects",
        "renderGitProjects",
        "renderGitProjectList",
        "handleGitProjectAction",
        "addGitProjectFromInput",
        "setSelectedGitProjectLock",
        "inspectSelectedGitProject",
        "renderGitProjectCommandBox",
        "runGitProjectAction",
    }
    assert expected_exports.issubset(set(report["exportNames"]))

    assert "VIP" in report["badges"]
    assert "Locked" in report["badges"]
    assert "Dirty 42/100" in report["badges"]
    assert "Project One" in report["projectListHtml"]
    assert "data-git-project-action=\"select\"" in report["projectListHtml"]
    assert "data-git-project-action=\"archive\"" in report["projectListHtml"]
    assert "No archived projects." in report["archiveHtml"]
    assert report["stateProjectId"] == "p1"

    api_call_names = [call["name"] for call in report["apiCalls"]]
    assert api_call_names[:2] == ["fetchProjects", "inspectProject"]
    lifecycle_names = [entry["name"] for entry in report["lifecycle"]]
    assert "gitProjectSetTargetPathInputs" in lifecycle_names
    assert "renderGitProjectInspection" in lifecycle_names
    assert "renderGitProjectNextStep" in lifecycle_names
    assert "completed" in report["historyRaw"]
