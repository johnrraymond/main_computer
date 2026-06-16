from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
WEB_APP = WEB_ROOT / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_git_tools_entrypoint_node() -> dict:
    git_tools = SCRIPTS / "git-tools.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
const calls = [];
const lifecycle = [];
function control(name) {{
  return {{
    name,
    dataset: {{gitServerRemotePreset: `${{name}}-preset`}},
    addEventListener: (eventName, handler) => calls.push({{name, eventName, handlerType: typeof handler}})
  }};
}}
const context = {{
  console,
  window: null,
  GitToolsStatusApi: {{surfaceId: "git-tools.status-api"}},
  GitToolsLegacyUiBridge: {{surfaceId: "git-tools.legacy-ui-bridge"}},
  GitToolsFileBasket: {{SURFACE_ID: "git-tools.file-basket"}},
  GitToolsProjectWorkflow: {{SOURCE_FILE: "git-tools-project-workflow.js"}},
  GitToolsServerPanel: {{surfaceId: "git-tools.server-panel"}},
  GitToolsProjectPanel: {{surfaceId: "git-tools.project-panel"}},
  GitToolsPatchInventory: {{surfaceId: "git-tools.patch-inventory"}},
  GitToolsGitignoreWorkbench: {{surfaceId: "git-tools.gitignore-workbench"}},
  gitToolsInitialized: false,
  gitServerRemotePresetButtons: [control("gitServerRemotePresetLocal"), control("gitServerRemotePresetExternal")],
}};
context.window = context;
[
  "gitStatusRefresh",
  "gitPatchesRefresh",
  "gitProjectAdd",
  "gitProjectRescan",
  "gitProjectLock",
  "gitProjectUnlock",
  "gitPatchPreview",
  "gitPatchDryRun",
  "gitDryRunRefresh",
  "gitConsoleRun",
  "gitConsoleExtract",
  "gitAiShim",
  "gitControlPlan",
  "gitShimView",
  "gitShimRun",
  "gitShimOrdain",
  "gitShimUnordain",
  "gitShimDelete",
  "gitPageWizardNext",
  "gitPageWizardReset",
  "gitPageWizardSendConsole",
  "gitServerStatusRefresh",
  "gitServerStart",
  "gitServerRestart",
  "gitServerStop",
  "gitServerLogs",
  "gitServerUseLocal",
  "gitServerRemoteApplyLocal",
  "gitServerPushLocal",
  "gitServerOperationCancel",
  "gitServerOperationRefresh",
  "gitServerUseExternal",
  "gitServerMirrorPlan",
  "gitServerMirrorSetup",
  "gitServerRemoteMode",
  "gitServerRemoteRun",
  "gitServerRemoteCopyConsole",
  "gitPageWizardInput"
].forEach((name) => {{
  context[name] = control(name);
}});
[
  "refreshGitStatus",
  "refreshGitPatches",
  "addGitProjectFromInput",
  "inspectSelectedGitProject",
  "previewGitPatch",
  "runGitPatchDryRun",
  "loadGitDryRun",
  "runGitConsoleCommand",
  "extractGitConsoleShims",
  "askGitAiForShim",
  "createGitPlanShim",
  "viewGitShim",
  "runGitShim",
  "deleteGitShim",
  "advanceGitPageWizard",
  "resetGitPageWizard",
  "sendGitPageWizardToConsole",
  "refreshGitServerStatus",
  "fillGitServerRemoteCommand",
  "useLocalGitServerRemote",
  "applyLocalGitServerRemote",
  "pushLocalGitServerRemote",
  "cancelGitServerOperation",
  "useExternalGitRemoteDirect",
  "planGiteaPushMirror",
  "setupGiteaPushMirror",
  "updateGitServerRemoteMode",
  "runGitServerRemoteCommand",
  "copyGitServerRemoteCommandToConsole",
  "initializeGitWorkflowDisclosure",
  "initializeGitServerHiddenPane",
  "initializeGitServerRemoteComposer",
  "renderGitPageWizard"
].forEach((name) => {{
  context[name] = (...args) => lifecycle.push({{name, args}});
}});
context.setSelectedGitProjectLock = (locked) => lifecycle.push({{name: "setSelectedGitProjectLock", locked}});
context.setGitShimOrdination = (ordained) => lifecycle.push({{name: "setGitShimOrdination", ordained}});
context.runGitServerAction = (action) => lifecycle.push({{name: "runGitServerAction", action}});
context.refreshGitOperationStatus = (options) => {{
  lifecycle.push({{name: "refreshGitOperationStatus", options}});
  return Promise.resolve(null);
}};
context.loadGitProjects = () => {{
  lifecycle.push({{name: "loadGitProjects"}});
  return Promise.resolve(null);
}};
context.refreshGitTools = () => lifecycle.push({{name: "refreshGitTools"}});
vm.createContext(context);
vm.runInContext(fs.readFileSync({json.dumps(str(git_tools))}, "utf8"), context, {{filename: "git-tools.js"}});
context.GitToolsEntrypoint.init();
context.GitToolsEntrypoint.init();
console.log(JSON.stringify({{
  sourceFile: context.GitToolsEntrypoint.sourceFile,
  surfaceId: context.GitToolsEntrypoint.surfaceId,
  readiness: context.GitToolsEntrypoint.readiness(),
  exportedInitIsCompat: context.initGitToolsApp === context.GitToolsEntrypoint.init,
  exportedBindIsCompat: context.bindGitToolsControl === context.GitToolsEntrypoint.bindControl,
  calls,
  lifecycle,
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_entrypoint_owns_only_git_startup_boundary() -> None:
    git_tools = (SCRIPTS / "git-tools.js").read_text(encoding="utf-8")
    boot = (SCRIPTS / "boot.js").read_text(encoding="utf-8")
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools.js -->" in html
    assert html.index("git-tools-project-workflow.js") < html.index("git-tools.js")
    assert html.index("git-tools-file-basket.js") < html.index("git-tools.js")
    assert html.index("git-tools-status-api.js") < html.index("git-tools-legacy-ui-bridge.js")
    assert html.index("git-tools-legacy-ui-bridge.js") < html.index("git-tools.js")
    assert html.index("git-tools-server-panel.js") < html.index("git-tools-project-panel.js")
    assert html.index("git-tools-project-panel.js") < html.index("git-tools-patch-inventory.js")
    assert html.index("git-tools-patch-inventory.js") < html.index("task-manager.js")
    assert html.index("git-tools-project-panel.js") < html.index("git-tools.js")
    assert html.index("git-tools-patch-inventory.js") < html.index("git-tools.js")
    assert html.index("git-tools-server-panel.js") < html.index("git-tools.js")
    assert html.index("task-manager.js") < html.index("git-tools.js")
    assert html.index("git-tools.js") < html.index("boot.js")

    assert "global.GitToolsEntrypoint" in git_tools
    assert "git-tools.entrypoint" in git_tools
    assert "function initGitToolsApp()" in git_tools
    assert "function bindGitToolsControl(control, eventName, handler)" in git_tools
    assert "GitToolsStatusApi" in git_tools
    assert "GitToolsLegacyUiBridge" in git_tools
    assert "GitToolsFileBasket" in git_tools
    assert "GitToolsProjectWorkflow" in git_tools
    assert "GitToolsServerPanel" in git_tools
    assert "GitToolsProjectPanel" in git_tools
    assert "GitToolsPatchInventory" in git_tools

    assert "#restart-gl" not in git_tools
    assert "#pause-gl" not in git_tools
    assert 'window.addEventListener("popstate"' not in git_tools
    assert "fitXterm()" not in git_tools

    assert "function bindApplicationShellControls()" in boot
    assert "#restart-gl" in boot
    assert 'window.addEventListener("popstate"' in boot
    assert "setActiveApp(applicationFromPath(window.location.pathname), {replaceRoute: true});" in boot


def test_git_tools_entrypoint_exports_compatibility_init_and_binds_controls() -> None:
    report = _run_git_tools_entrypoint_node()

    assert report["sourceFile"].endswith("git-tools.js")
    assert report["surfaceId"] == "git-tools.entrypoint"
    assert report["readiness"] == {
        "statusApi": True,
        "legacyUiBridge": True,
        "fileBasket": True,
        "projectWorkflow": True,
        "serverPanel": True,
        "projectPanel": True,
        "patchInventory": True,
        "gitignoreWorkbench": True,
    }
    assert report["exportedInitIsCompat"]
    assert report["exportedBindIsCompat"]

    bound = {(call["name"], call["eventName"]) for call in report["calls"]}
    assert ("gitStatusRefresh", "click") in bound
    assert ("gitPatchesRefresh", "click") in bound
    assert ("gitPatchPreview", "click") in bound
    assert ("gitPatchDryRun", "click") in bound
    assert ("gitDryRunRefresh", "click") in bound
    assert ("gitServerRemoteMode", "change") in bound
    assert ("gitPageWizardInput", "keydown") in bound
    assert ("gitServerRemotePresetLocal", "click") in bound
    assert ("gitServerRemotePresetExternal", "click") in bound

    lifecycle_names = [entry["name"] for entry in report["lifecycle"]]
    assert lifecycle_names.count("refreshGitOperationStatus") == 1
    assert lifecycle_names.count("loadGitProjects") == 1
    assert lifecycle_names.count("refreshGitTools") == 2
