from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "main_computer" / "web"
WEB_APP = WEB_ROOT / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_git_tools_status_api_node() -> dict:
    status_api = SCRIPTS / "git-tools-status-api.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
globalThis.window = globalThis;
const calls = [];
globalThis.fetch = async function(path, options) {{
  const body = JSON.parse(options.body || "{{}}");
  calls.push({{path, options, body}});
  if (path === "/raw-response") {{
    return {{ok: true, status: 200, text: async () => "plain response text"}};
  }}
  if (path === "/failed-json") {{
    return {{ok: false, status: 418, text: async () => JSON.stringify({{message: "Teapot failed", code: "teapot"}})}};
  }}
  if (path === "/failed-raw") {{
    return {{ok: false, status: 502, text: async () => "Bad gateway text"}};
  }}
  return {{ok: true, status: 200, text: async () => JSON.stringify({{ok: true, path, body}})}};
}};
vm.runInThisContext(fs.readFileSync({json.dumps(str(status_api))}, "utf8"), {{filename: "git-tools-status-api.js"}});
const api = globalThis.GitToolsStatusApi;
(async () => {{
  const status = await api.fetchStatus({{repoDir: "repo"}});
  const patches = await api.fetchPatches();
  const patch = await api.readPatch("change.patch");
  const dryRun = await api.readDryRun("run-1");
  const apply = await api.applyPatchDryRun({{patchName: "change.patch", targetRoot: "target", reverse: true, strictRoot: true}});
  const projects = await api.fetchProjects();
  const server = await api.fetchServerStatus();
  const action = await api.runServerAction("logs");
  const raw = await api.request("/raw-response", {{note: "keep raw"}});
  let jsonError = null;
  try {{
    await api.request("/failed-json", {{note: "fail"}});
  }} catch (error) {{
    jsonError = {{
      message: error.message,
      status: error.status,
      details: error.details,
      text: api.operationErrorText("Prefix", error),
    }};
  }}
  let rawError = null;
  try {{
    await api.request("/failed-raw", {{note: "raw fail"}});
  }} catch (error) {{
    rawError = {{
      message: error.message,
      status: error.status,
      details: error.details,
    }};
  }}
  console.log(JSON.stringify({{
    sourceFile: api.sourceFile,
    version: api.version,
    surfaceId: api.surfaceId,
    exportNames: Object.keys(api).sort(),
    calls,
    status,
    patches,
    patch,
    dryRun,
    apply,
    projects,
    server,
    action,
    raw,
    jsonError,
    rawError,
  }}));
}})().catch((error) => {{
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
}});
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_status_api_module_loads_before_legacy_task_manager() -> None:
    html = (WEB_ROOT / "applications.html").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    status_api = (SCRIPTS / "git-tools-status-api.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-status-api.js -->" in html
    assert html.index("git-tools-project-workflow.js") < html.index("git-tools-status-api.js")
    assert html.index("git-tools-file-basket.js") < html.index("git-tools-status-api.js")
    assert html.index("git-tools-status-api.js") < html.index("task-manager.js")
    assert html.index("git-tools-status-api.js") < html.index("git-tools.js")

    assert "global.GitToolsStatusApi" in status_api
    assert "const ENDPOINTS = Object.freeze" in status_api
    assert "/api/applications/git/status" in status_api
    assert "/api/applications/git/patch/apply" in status_api
    assert "/api/applications/git/server/status" in status_api

    assert "function gitToolsStatusApi" in task_manager
    assert "GitToolsStatusApi" in task_manager


def test_git_tools_status_api_request_contract_and_helpers() -> None:
    report = _run_git_tools_status_api_node()

    assert report["sourceFile"].endswith("git-tools-status-api.js")
    assert report["surfaceId"] == "git-tools.status-api"

    expected_exports = {
        "request",
        "operationErrorText",
        "fetchStatus",
        "fetchPatches",
        "readPatch",
        "readDryRun",
        "applyPatchDryRun",
        "fetchProjects",
        "fetchServerStatus",
        "runServerAction",
        "fetchOperationStatus",
        "cancelOperation",
    }
    assert expected_exports.issubset(set(report["exportNames"]))

    first_call = report["calls"][0]
    assert first_call["path"] == "/api/applications/git/status"
    assert first_call["options"]["method"] == "POST"
    assert first_call["options"]["headers"]["Content-Type"] == "application/json"
    assert first_call["body"] == {"repo_dir": "repo"}
    assert report["status"]["body"] == {"repo_dir": "repo"}

    apply_call = next(call for call in report["calls"] if call["path"] == "/api/applications/git/patch/apply")
    assert apply_call["body"] == {
        "patch_name": "change.patch",
        "target_root": "target",
        "dry_run": True,
        "reverse": True,
        "strict_root": True,
    }

    assert report["patches"]["path"] == "/api/applications/git/patches"
    assert report["patch"]["body"] == {"patch_name": "change.patch"}
    assert report["dryRun"]["body"] == {"run_name": "run-1"}
    assert report["projects"]["path"] == "/api/applications/git/projects"
    assert report["server"]["path"] == "/api/applications/git/server/status"
    assert report["action"]["body"] == {"action": "logs"}

    assert report["raw"] == {"raw": "plain response text"}
    assert report["jsonError"]["message"] == "Teapot failed"
    assert report["jsonError"]["status"] == 418
    assert report["jsonError"]["details"] == {"message": "Teapot failed", "code": "teapot"}
    assert "HTTP status: 418" in report["jsonError"]["text"]
    assert report["rawError"]["message"] == "Bad gateway text"
    assert report["rawError"]["status"] == 502
    assert report["rawError"]["details"] == {"raw": "Bad gateway text"}


def test_task_manager_delegates_git_api_boundary_to_status_api() -> None:
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    status_api = (SCRIPTS / "git-tools-status-api.js").read_text(encoding="utf-8")

    assert "function gitToolsRequest(path, payload = {})" in task_manager
    assert "return gitToolsStatusApi().request(path, payload);" in task_manager
    assert "return fetch(path" not in task_manager
    assert "gitToolsStatusApi().fetchStatus({repoDir})" in task_manager
    assert "gitToolsStatusApi().fetchPatches()" in task_manager
    assert "gitToolsStatusApi().readPatch(patchName)" in task_manager
    assert "gitToolsStatusApi().applyPatchDryRun({" in task_manager
    assert "gitToolsStatusApi().fetchServerStatus()" in task_manager

    assert '"/api/applications/git/status"' not in task_manager
    assert '"/api/applications/git/patches"' not in task_manager
    assert '"/api/applications/git/server/status"' not in task_manager

    assert '"/api/applications/git/status"' in status_api
    assert '"/api/applications/git/patches"' in status_api
    assert '"/api/applications/git/server/status"' in status_api
