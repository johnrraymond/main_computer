from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
WEB_APP = WEB_ROOT / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_git_tools_patch_inventory_node() -> dict:
    patch_inventory = SCRIPTS / "git-tools-patch-inventory.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
const lifecycle = [];
function element(name) {{
  const node = {{
    name,
    value: "",
    checked: false,
    _innerHTML: "",
    textContent: "",
    children: [],
    handlers: {{}},
    className: "",
    type: "",
    classList: {{
      toggles: [],
      toggle(className, active) {{
        this.toggles.push({{className, active: Boolean(active)}});
      }}
    }},
    append(child) {{
      this.children.push(child);
    }},
    addEventListener(eventName, handler) {{
      this.handlers[eventName] = handler;
    }}
  }};
  Object.defineProperty(node, "innerHTML", {{
    get() {{
      return this._innerHTML;
    }},
    set(value) {{
      this._innerHTML = String(value);
      this.children = [];
    }}
  }});
  return node;
}}
const patchList = element("gitPatchList");
const patchName = element("gitPatchName");
const patchPreviewOutput = element("gitPatchPreviewOutput");
const dryRunName = element("gitDryRunName");
const dryRunOutput = element("gitDryRunOutput");
const patchTarget = element("gitPatchTarget");
patchTarget.value = "repo-root";
const patchReverse = element("gitPatchReverse");
patchReverse.checked = true;
const calls = [];
const context = {{
  console,
  window: null,
  gitPatchList: patchList,
  gitPatchName: patchName,
  gitPatchPreviewOutput: patchPreviewOutput,
  gitDryRunName: dryRunName,
  gitDryRunOutput: dryRunOutput,
  gitPatchTarget: patchTarget,
  gitPatchReverse: patchReverse,
  gitToolsSelectedPatch: "",
  gitToolsSelectedDryRun: "",
  document: {{
    createElement: (name) => element(name)
  }},
  escapeHtml: (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;"),
  updateGitWorkflowSectionSummary: (section, message) => lifecycle.push({{name: "summary", section, message}}),
  expandGitWorkflowSection: (section, message) => lifecycle.push({{name: "expand", section, message}}),
  refreshGitStatus: () => {{
    lifecycle.push({{name: "refreshGitStatus"}});
    return Promise.resolve(null);
  }},
}};
context.window = context;
const patchData = {{
  incoming: [{{name: "incoming.patch", relative_path: "incoming/incoming.patch"}}],
  applied: [],
  archive: [],
  dry_runs: [{{name: "run-1", relative_path: "dry-runs/run-1"}}],
}};
context.gitToolsStatusApi = () => ({{
  fetchPatches: () => {{
    calls.push({{name: "fetchPatches"}});
    return Promise.resolve(patchData);
  }},
  readPatch: (patch) => {{
    calls.push({{name: "readPatch", patch}});
    return Promise.resolve({{preview: `preview for ${{patch}}`}});
  }},
  readDryRun: (run) => {{
    calls.push({{name: "readDryRun", run}});
    return Promise.resolve({{
      manifest: {{run}},
      preview_files: [{{relative_path: "main_computer/file.js"}}],
      deletions: [{{relative_path: "old.js"}}],
    }});
  }},
  applyPatchDryRun: (payload) => {{
    calls.push({{name: "applyPatchDryRun", payload}});
    return Promise.resolve({{
      result: {{ok: true}},
      dry_run_output_dir: "dry-runs/run-2",
    }});
  }},
}});
vm.createContext(context);
vm.runInContext(fs.readFileSync({json.dumps(str(patch_inventory))}, "utf8"), context, {{filename: "git-tools-patch-inventory.js"}});
(async () => {{
  const api = context.GitToolsPatchInventory;
  await api.refreshGitPatches();
  patchName.value = "incoming.patch";
  await api.previewGitPatch();
  dryRunName.value = "run-1";
  await api.loadGitDryRun();
  await api.runGitPatchDryRun();
  console.log(JSON.stringify({{
    sourceFile: api.sourceFile,
    surfaceId: api.surfaceId,
    version: api.version,
    exportNames: Object.keys(api).sort(),
    compatRefresh: context.refreshGitPatches === api.refreshGitPatches,
    compatPreview: context.previewGitPatch === api.previewGitPatch,
    listChildren: patchList.children.length,
    dryRunName: dryRunName.value,
    previewText: patchPreviewOutput.textContent,
    dryRunText: dryRunOutput.textContent,
    calls,
    lifecycle,
  }}));
}})().catch((error) => {{
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
}});
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_patch_inventory_loads_before_legacy_bridge() -> None:
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    legacy_bridge = (SCRIPTS / "git-tools-legacy-ui-bridge.js").read_text(encoding="utf-8")
    git_tools = (SCRIPTS / "git-tools.js").read_text(encoding="utf-8")
    patch_inventory = (SCRIPTS / "git-tools-patch-inventory.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-patch-inventory.js -->" in html
    assert html.index("git-tools-status-api.js") < html.index("git-tools-patch-inventory.js")
    assert html.index("git-tools-patch-inventory.js") < html.index("task-manager.js")
    assert html.index("git-tools-patch-inventory.js") < html.index("git-tools.js")

    assert "git-tools.patch-inventory" in patch_inventory
    assert "global.GitToolsPatchInventory" in patch_inventory
    assert "function renderGitPatchGroups(data)" in patch_inventory
    assert "async function refreshGitPatches()" in patch_inventory
    assert "async function previewGitPatch()" in patch_inventory
    assert "async function loadGitDryRun()" in patch_inventory
    assert "async function runGitPatchDryRun()" in patch_inventory
    assert "GitToolsStatusApi" not in patch_inventory or "gitToolsStatusApi()" in patch_inventory

    assert "function renderGitPatchGroups(data)" not in task_manager
    assert "async function refreshGitPatches()" not in task_manager
    assert "async function previewGitPatch()" not in task_manager
    assert "async function loadGitDryRun()" not in task_manager
    assert "async function runGitPatchDryRun()" not in task_manager
    assert "refreshGitPatches()" in legacy_bridge

    assert "GitToolsPatchInventory" in git_tools
    assert "patchInventory" in git_tools


def test_git_tools_patch_inventory_exports_compatibility_surface_and_behaves() -> None:
    report = _run_git_tools_patch_inventory_node()

    assert report["sourceFile"].endswith("git-tools-patch-inventory.js")
    assert report["surfaceId"] == "git-tools.patch-inventory"
    assert report["compatRefresh"]
    assert report["compatPreview"]
    assert "refreshGitPatches" in report["exportNames"]
    assert "runGitPatchDryRun" in report["exportNames"]

    call_names = [call["name"] for call in report["calls"]]
    assert call_names.count("fetchPatches") >= 4
    assert {"name": "readPatch", "patch": "incoming.patch"} in report["calls"]
    assert {"name": "readDryRun", "run": "run-1"} in report["calls"]
    assert {"name": "readDryRun", "run": "run-2"} in report["calls"]
    assert any(call["name"] == "applyPatchDryRun" and call["payload"]["patch_name"] == "incoming.patch" for call in report["calls"])

    assert report["listChildren"] == 2
    assert report["dryRunName"] == "run-2"
    assert "preview for incoming.patch" in report["previewText"]
    assert "main_computer/file.js" in report["dryRunText"]

    lifecycle_names = [entry["name"] for entry in report["lifecycle"]]
    assert "refreshGitStatus" in lifecycle_names
    assert any(entry["name"] == "summary" and entry["section"] == "patch-inventory" for entry in report["lifecycle"])
