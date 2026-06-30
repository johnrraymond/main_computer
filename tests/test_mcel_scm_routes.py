from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"


def _script(relative_path: str) -> str:
    return (SCRIPTS / relative_path).read_text(encoding="utf-8")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; SCM route kernel functional smoke test cannot run")

    script_path = tmp_path / "mcel-scm-route-smoke.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _scm_bootstrap() -> str:
    return f"""
const window = {{}};
{_script("mcel-scm.js")}
McelLabScm.clearDefinitions();

McelLabScm.defineComponent("RouteStudio", {{
  version: "1.0.0",
  contract: "route.studio.v1",
  owns: {{
    source: ["workspace.files"],
    state: ["activeFileId", "dirty"],
    runtime: ["loadedFile"]
  }},
  state: {{
    activeFileId: null,
    dirty: false
  }},
  transitions: {{}}
}});
"""


def _valid_route_manifest() -> str:
    return """
{
  version: "1.0.0",
  contract: "route.workspace-file.v1",
  segments: [
    {literal: "workspace"},
    {param: "workspaceId", type: "id", required: true},
    {literal: "file"},
    {param: "fileId", type: "id", required: true}
  ],
  query: {
    panel: {
      type: "enum",
      values: ["source", "runtime", "serialized", "contract", "debug"],
      default: "source"
    },
    line: {
      type: "integer",
      optional: true
    }
  },
  mounts: {
    component: "RouteStudio",
    inputs: {
      workspaceId: "route.params.workspaceId",
      activeFileId: "route.params.fileId",
      selectedPanel: "route.query.panel"
    }
  },
  data: {
    loadWorkspace: {
      kind: "async-data",
      triggers: ["route.params.workspaceId"],
      reads: ["route.params.workspaceId"],
      writes: ["route.data.workspace"],
      external: {resource: "workspace-registry", operation: "loadWorkspace"},
      cancellation: "cancel-previous",
      racePolicy: "latest-route-wins",
      errorPolicy: {onFailure: "set-route-error"}
    },
    loadFile: {
      kind: "async-data",
      triggers: ["route.params.workspaceId", "route.params.fileId"],
      reads: ["route.params.workspaceId", "route.params.fileId"],
      writes: ["route.data.activeFile"],
      external: {resource: "workspace-files", operation: "loadFile"},
      cancellation: "cancel-previous",
      racePolicy: "latest-route-wins",
      errorPolicy: {onFailure: "set-route-error"}
    }
  },
  lifecycle: {
    onEnter: ["validateParams", "loadWorkspace", "loadFile"],
    onLeave: {
      blockedBy: ["component.state.dirty"],
      resolutions: ["commitDraft", "discardDraft", "cancelNavigation"]
    }
  }
}
"""


def test_scm_defines_and_lists_structured_route(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const definition = McelLabScm.defineRoute("workspace.file", {_valid_route_manifest()});

process.stdout.write(JSON.stringify({{
  definition,
  listed: McelLabScm.listRouteDefinitions(),
  lookupName: McelLabScm.routeDefinition("workspace.file").name
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["definition"]["name"] == "workspace.file"
    assert data["definition"]["displayPath"] == "workspace/{workspaceId}/file/{fileId}"
    assert data["lookupName"] == "workspace.file"
    assert data["listed"] == [
        {
            "kind": "mcel-scm-route-definition-summary",
            "name": "workspace.file",
            "version": "1.0.0",
            "contract": "route.workspace-file.v1",
            "segments": [
                {"literal": "workspace"},
                {"param": "workspaceId", "type": "id", "required": True},
                {"literal": "file"},
                {"param": "fileId", "type": "id", "required": True},
            ],
            "displayPath": "workspace/{workspaceId}/file/{fileId}",
            "mountComponent": "RouteStudio",
            "dataLoaders": ["loadWorkspace", "loadFile"],
        }
    ]


def test_scm_rejects_react_style_path_as_canonical_route_shape(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const validation = McelLabScm.validateRouteManifest("workspace.file", {{
  path: "/workspace/:workspaceId/file/:fileId",
  query: {{}},
  mounts: {{
    component: "RouteStudio",
    inputs: {{}}
  }}
}});

process.stdout.write(JSON.stringify(validation));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    codes = [issue["code"] for issue in data["issues"]]
    assert "SCM_ROUTE_PATH_NOT_STRUCTURED" in codes
    assert "SCM_ROUTE_MISSING_SEGMENTS" in codes


def test_scm_validates_route_mounts_loaders_lifecycle_and_component_blockers(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const invalid = McelLabScm.validateRouteManifest("bad.route", {{
  segments: [
    {{literal: "workspace"}},
    {{param: "workspaceId", type: "id", required: true}}
  ],
  query: {{
    panel: {{type: "enum", values: ["source"], default: "source"}}
  }},
  mounts: {{
    component: "RouteStudio",
    inputs: {{
      missingParam: "route.params.fileId",
      missingQuery: "route.query.unknown",
      unownedComponent: "component.state.secret"
    }}
  }},
  data: {{
    loadFile: {{
      reads: ["route.params.fileId"],
      writes: ["component.state.activeFileId"]
    }}
  }},
  lifecycle: {{
    onEnter: ["loadMissing"],
    onLeave: {{
      blockedBy: ["component.state.secret"],
      resolutions: []
    }}
  }}
}});

process.stdout.write(JSON.stringify({{
  ok: invalid.ok,
  codes: invalid.issues.map((issue) => issue.code)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_ROUTE_PARAM_REFERENCE_MISSING" in data["codes"]
    assert "SCM_ROUTE_QUERY_REFERENCE_MISSING" in data["codes"]
    assert "SCM_ROUTE_COMPONENT_PATH_UNOWNED" in data["codes"]
    assert "SCM_ROUTE_LOADER_WRITE_TARGET_INVALID" in data["codes"]
    assert "SCM_EFFECT_MISSING_KIND" in data["codes"]
    assert "SCM_ROUTE_MISSING_PATH_LIST" in data["codes"]
    assert "SCM_EFFECT_EXTERNAL_MISSING" in data["codes"]
    assert "SCM_EFFECT_ERROR_POLICY_MISSING" in data["codes"]
    assert "SCM_ROUTE_ON_ENTER_STEP_UNKNOWN" in data["codes"]
    assert "SCM_ROUTE_ON_LEAVE_RESOLUTIONS_INVALID" in data["codes"]


def test_scm_route_enter_validates_params_query_and_resolves_mount_inputs(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineRoute("workspace.file", {_valid_route_manifest()});
const instance = McelLabScm.createRouteInstance("workspace.file");

const entered = McelLabScm.enterRoute(instance, {{
  params: {{
    workspaceId: "workspace-main",
    fileId: "src-app"
  }},
  query: {{
    panel: "debug",
    line: "42"
  }}
}});

let invalidParam = null;
try {{
  const bad = McelLabScm.createRouteInstance("workspace.file");
  McelLabScm.enterRoute(bad, {{
    params: {{
      workspaceId: "workspace-main",
      fileId: "../bad"
    }},
    query: {{}}
  }});
}} catch (error) {{
  invalidParam = error.violation;
}}

let invalidQuery = null;
try {{
  const badQuery = McelLabScm.createRouteInstance("workspace.file");
  McelLabScm.enterRoute(badQuery, {{
    params: {{
      workspaceId: "workspace-main",
      fileId: "src-app"
    }},
    query: {{
      panel: "missing-panel"
    }}
  }});
}} catch (error) {{
  invalidQuery = error.violation;
}}

process.stdout.write(JSON.stringify({{
  entered,
  instanceParams: instance.params,
  instanceQuery: instance.query,
  invalidParam,
  invalidQuery,
  packet: McelLabScm.exportRouteEvidence(instance)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["entered"]["ok"] is True
    assert data["entered"]["params"] == {
        "workspaceId": "workspace-main",
        "fileId": "src-app",
    }
    assert data["entered"]["query"] == {
        "panel": "debug",
        "line": 42,
    }
    assert data["entered"]["mountInputs"] == {
        "workspaceId": "workspace-main",
        "activeFileId": "src-app",
        "selectedPanel": "debug",
    }
    assert data["invalidParam"]["code"] == "SCM_ROUTE_PARAM_INVALID"
    assert data["invalidParam"]["paramName"] == "fileId"
    assert data["invalidQuery"]["code"] == "SCM_ROUTE_QUERY_INVALID"
    assert data["invalidQuery"]["queryName"] == "panel"
    assert data["packet"]["kind"] == "mcel-scm-route-evidence-packet"
    assert data["packet"]["evidence"][-1]["phase"] == "route-enter"


def test_scm_route_leave_blocks_dirty_component_state_and_records_evidence(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineRoute("workspace.file", {_valid_route_manifest()});
const componentInstance = McelLabScm.createComponentInstance("RouteStudio", {{
  state: {{
    activeFileId: "src-app",
    dirty: true
  }}
}});
const routeInstance = McelLabScm.createRouteInstance("workspace.file", {{
  componentInstance
}});

McelLabScm.enterRoute(routeInstance, {{
  params: {{
    workspaceId: "workspace-main",
    fileId: "src-app"
  }},
  query: {{}}
}});

const blocked = McelLabScm.leaveRoute(routeInstance);
const allowed = McelLabScm.leaveRoute(routeInstance, {{resolution: "discardDraft"}});
const packet = McelLabScm.exportRouteEvidence(routeInstance);

process.stdout.write(JSON.stringify({{
  blocked,
  allowed,
  packet
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["blocked"]["ok"] is False
    assert data["blocked"]["blocked"] is True
    assert data["blocked"]["blockers"] == ["component.state.dirty"]
    assert data["blocked"]["resolutions"] == ["commitDraft", "discardDraft", "cancelNavigation"]
    assert data["blocked"]["evidence"]["code"] == "SCM_ROUTE_LEAVE_BLOCKED"
    assert data["blocked"]["evidence"]["severity"] == "user-action-required"
    assert data["allowed"]["ok"] is True
    assert data["allowed"]["blocked"] is False
    assert data["packet"]["evidence"][-2]["code"] == "SCM_ROUTE_LEAVE_BLOCKED"
    assert data["packet"]["evidence"][-1]["phase"] == "route-leave"

