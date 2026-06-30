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
        pytest.skip("node is unavailable; SCM effect kernel functional smoke test cannot run")

    script_path = tmp_path / "mcel-scm-effect-smoke.js"
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
"""


def _valid_effect_component_manifest() -> str:
    return """
{
  version: "1.0.0",
  contract: "effect.studio.v1",
  owns: {
    state: ["activeFileId"],
    runtime: ["loadedFile", "status"],
    effects: ["loadActiveFile"]
  },
  state: {
    activeFileId: "src-app"
  },
  runtime: {
    loadedFile: null,
    status: "idle"
  },
  effects: {
    loadActiveFile: {
      kind: "async-data",
      triggers: ["state.activeFileId"],
      reads: ["state.activeFileId"],
      writes: ["runtime.loadedFile", "runtime.status"],
      external: {resource: "filesystem", operation: "readFile"},
      cancellation: "cancel-previous",
      racePolicy: "latest-inputs-win",
      errorPolicy: {onFailure: "set-runtime-error"},
      run(ctx, event) {
        return {
          id: ctx.get("state.activeFileId"),
          text: event.text
        };
      },
      commit(ctx, result) {
        ctx.set("runtime.loadedFile", result);
        ctx.set("runtime.status", "loaded");
        return result.id;
      }
    }
  },
  transitions: {}
}
"""


def _route_bootstrap() -> str:
    return f"""
{_scm_bootstrap()}
McelLabScm.defineComponent("RouteEffectStudio", {{
  version: "1.0.0",
  contract: "route.effect.studio.v1",
  owns: {{
    state: ["dirty"],
    runtime: ["loadedFile"]
  }},
  state: {{
    dirty: false
  }},
  runtime: {{
    loadedFile: null
  }},
  transitions: {{}}
}});
"""


def _valid_route_manifest() -> str:
    return """
{
  version: "1.0.0",
  contract: "route.effect.v1",
  segments: [
    {literal: "workspace"},
    {param: "workspaceId", type: "id", required: true},
    {literal: "file"},
    {param: "fileId", type: "id", required: true}
  ],
  query: {
    panel: {type: "enum", values: ["source", "debug"], default: "source"}
  },
  mounts: {
    component: "RouteEffectStudio",
    inputs: {
      selectedPanel: "route.query.panel"
    }
  },
  data: {
    loadFile: {
      kind: "async-data",
      triggers: ["route.params.workspaceId", "route.params.fileId"],
      reads: ["route.params.workspaceId", "route.params.fileId"],
      writes: ["route.data.activeFile"],
      external: {resource: "workspace-files", operation: "loadFile"},
      cancellation: "cancel-previous",
      racePolicy: "latest-route-wins",
      errorPolicy: {onFailure: "set-route-error"},
      run(ctx) {
        return {
          workspaceId: ctx.get("route.params.workspaceId"),
          fileId: ctx.get("route.params.fileId")
        };
      },
      commit(ctx, result) {
        ctx.set("route.data.activeFile", result);
        return result.fileId;
      }
    }
  },
  lifecycle: {
    onEnter: ["validateParams", "loadFile"],
    onLeave: {
      blockedBy: ["component.state.dirty"],
      resolutions: ["discardDraft", "cancelNavigation"]
    }
  }
}
"""


def test_scm_component_effect_runs_through_declared_read_write_context(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("EffectStudio", {_valid_effect_component_manifest()});
const instance = McelLabScm.createComponentInstance("EffectStudio");
const result = McelLabScm.runEffect(instance, "loadActiveFile", {{text: "hello"}});
const packet = McelLabScm.exportEvidence(instance);

process.stdout.write(JSON.stringify({{
  ok: result.ok,
  loadedFile: instance.runtime.loadedFile,
  status: instance.runtime.status,
  phases: packet.evidence.map((entry) => entry.phase),
  resultKind: result.kind
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is True
    assert data["loadedFile"] == {"id": "src-app", "text": "hello"}
    assert data["status"] == "loaded"
    assert data["resultKind"] == "mcel-scm-effect-result"
    assert "effect-start" in data["phases"]
    assert "effect-commit" in data["phases"]


def test_scm_rejects_async_component_effect_without_required_policy(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const validation = McelLabScm.validateComponentManifest("BadEffectStudio", {{
  owns: {{
    runtime: ["loadedFile"],
    effects: ["loadActiveFile"]
  }},
  runtime: {{
    loadedFile: null
  }},
  effects: {{
    loadActiveFile: {{
      kind: "async-data",
      triggers: ["runtime.loadedFile"],
      reads: ["runtime.loadedFile"],
      writes: ["runtime.loadedFile"]
    }}
  }},
  transitions: {{}}
}});

process.stdout.write(JSON.stringify({{
  ok: validation.ok,
  codes: validation.issues.map((issue) => issue.code)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_EFFECT_EXTERNAL_MISSING" in data["codes"]
    assert "SCM_EFFECT_ERROR_POLICY_MISSING" in data["codes"]
    assert "SCM_EFFECT_MISSING_CANCELLATION" in data["codes"]
    assert "SCM_EFFECT_MISSING_RACE_POLICY" in data["codes"]


def test_scm_component_effect_blocks_undeclared_write_and_records_evidence(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("EffectStudio", {{
  ...{_valid_effect_component_manifest()},
  effects: {{
    loadActiveFile: {{
      ...{_valid_effect_component_manifest()}.effects.loadActiveFile,
      writes: ["runtime.loadedFile"],
      commit(ctx, result) {{
        ctx.set("runtime.status", "bad");
        return result;
      }}
    }}
  }}
}});

const instance = McelLabScm.createComponentInstance("EffectStudio");
let violation = null;
try {{
  McelLabScm.runEffect(instance, "loadActiveFile", {{text: "hello"}});
}} catch (error) {{
  violation = error.violation;
}}

const packet = McelLabScm.exportEvidence(instance);

process.stdout.write(JSON.stringify({{
  violation,
  phases: packet.evidence.map((entry) => entry.phase),
  codes: packet.evidence.map((entry) => entry.code).filter(Boolean)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_EFFECT_UNDECLARED_WRITE"
    assert data["violation"]["effectName"] == "loadActiveFile"
    assert "effect-failure" in data["phases"]
    assert "SCM_EFFECT_UNDECLARED_WRITE" in data["codes"]


def test_scm_route_loader_runs_through_declared_route_context(tmp_path: Path) -> None:
    script = f"""
{_route_bootstrap()}

McelLabScm.defineRoute("workspace.file", {_valid_route_manifest()});
const route = McelLabScm.createRouteInstance("workspace.file");
McelLabScm.enterRoute(route, {{
  params: {{
    workspaceId: "workspace-main",
    fileId: "src-app"
  }},
  query: {{}}
}});

const result = McelLabScm.runRouteLoader(route, "loadFile");
const packet = McelLabScm.exportRouteEvidence(route);

process.stdout.write(JSON.stringify({{
  ok: result.ok,
  activeFile: route.data.activeFile,
  phases: packet.evidence.map((entry) => entry.phase),
  resultKind: result.kind
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is True
    assert data["activeFile"] == {"workspaceId": "workspace-main", "fileId": "src-app"}
    assert data["resultKind"] == "mcel-scm-route-loader-result"
    assert "route-loader-start" in data["phases"]
    assert "route-loader-commit" in data["phases"]


def test_scm_route_loader_blocks_undeclared_access_and_can_record_cancel(tmp_path: Path) -> None:
    script = f"""
{_route_bootstrap()}

McelLabScm.defineRoute("workspace.file", {{
  ...{_valid_route_manifest()},
  data: {{
    loadFile: {{
      ...{_valid_route_manifest()}.data.loadFile,
      reads: ["route.params.fileId"],
      run(ctx) {{
        return ctx.get("route.params.workspaceId");
      }}
    }}
  }}
}});
const route = McelLabScm.createRouteInstance("workspace.file");
McelLabScm.enterRoute(route, {{
  params: {{
    workspaceId: "workspace-main",
    fileId: "src-app"
  }},
  query: {{}}
}});

let violation = null;
try {{
  McelLabScm.runRouteLoader(route, "loadFile");
}} catch (error) {{
  violation = error.violation;
}}

const cancelled = McelLabScm.cancelRouteLoader(route, "loadFile", "superseded");
const packet = McelLabScm.exportRouteEvidence(route);

process.stdout.write(JSON.stringify({{
  violation,
  cancelled,
  phases: packet.evidence.map((entry) => entry.phase),
  codes: packet.evidence.map((entry) => entry.code).filter(Boolean)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_ROUTE_LOADER_UNDECLARED_READ"
    assert data["cancelled"]["ok"] is True
    assert data["cancelled"]["evidence"]["phase"] == "route-loader-cancel"
    assert "route-loader-failure" in data["phases"]
    assert "SCM_ROUTE_LOADER_UNDECLARED_READ" in data["codes"]
