from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCM = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-scm.js"
RUNTIME = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-runtime.js"
CORE = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-core.js"
APPLICATIONS = ROOT / "main_computer" / "web" / "applications.html"


def _script(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL application runtime functional test cannot run")
    script_path = tmp_path / "application-runtime-test.js"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def _runtime_bootstrap() -> str:
    return f'''\
"use strict";
const fs = require("fs");
const path = require("path");
{_script(SCM)}
{_script(RUNTIME)}

async function importContract(relativePath) {{
  const source = fs.readFileSync(path.join({json.dumps(str(ROOT))}, relativePath), "utf8");
  const url = `data:text/javascript;base64,${{Buffer.from(source, "utf8").toString("base64")}}`;
  return import(url);
}}
'''


def test_contract_counter_executes_declared_intents_through_scm(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
(async () => {
  const domainModule = await importContract("mcel_apps/contract-counter/contracts/domain.js");
  const intentsModule = await importContract("mcel_apps/contract-counter/contracts/intents.js");
  const adapterModule = await importContract("mcel_apps/contract-counter/contracts/adapter.js");

  const definition = McelApplicationRuntime.defineApplication({
    appId: "contract-counter",
    domain: domainModule.ContractCounterDomain,
    intents: intentsModule.ContractCounterIntents,
    adapter: adapterModule.ContractCounterAdapter
  });
  const app = McelApplicationRuntime.createApplicationInstance(definition, {
    id: "contract-counter-test-instance"
  });

  const initial = app.readState();
  let mutationBlocked = false;
  try {
    initial.count = 99;
  } catch (_error) {
    mutationBlocked = true;
  }

  const increment = app.dispatch({
    operationId: "counter-op-1",
    expectedRevision: 0,
    intentId: "increment",
    payload: {}
  });
  const duplicate = app.dispatch({
    operationId: "counter-op-1",
    expectedRevision: 1,
    intentId: "increment",
    payload: {}
  });
  const stale = app.dispatch({
    operationId: "counter-op-stale",
    expectedRevision: 0,
    intentId: "increment",
    payload: {}
  });
  const prohibited = app.dispatch({
    operationId: "counter-op-prohibited",
    expectedRevision: 1,
    intentId: "direct-set",
    payload: {value: 99}
  });
  const reset = app.dispatch({
    operationId: "counter-op-2",
    expectedRevision: 1,
    intentId: "reset",
    payload: {}
  });
  const evidence = app.exportEvidence();

  process.stdout.write(JSON.stringify({
    definition: {
      appId: definition.appId,
      componentName: definition.componentName,
      intentIds: definition.intentIds
    },
    initial: {
      value: initial,
      frozen: Object.isFrozen(initial),
      mutationBlocked
    },
    increment,
    duplicate,
    stale,
    prohibited,
    reset,
    final: {
      state: app.state,
      revision: app.revision,
      appliedOperationIds: app.appliedOperationIds
    },
    evidence
  }));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
'''
    data = _run_node_json(tmp_path, script)

    assert data["definition"] == {
        "appId": "contract-counter",
        "componentName": "application.contract-counter",
        "intentIds": ["direct-set", "increment", "reset"],
    }
    assert data["initial"]["value"] == {"count": 0, "revision": 0}
    assert data["initial"]["frozen"] is True
    assert data["initial"]["mutationBlocked"] is True

    assert data["increment"]["ok"] is True
    assert data["increment"]["status"] == "committed"
    assert data["increment"]["state"] == {"count": 1, "revision": 1}
    assert data["increment"]["receipt"]["scm"]["previousRevision"] == 0
    assert data["increment"]["receipt"]["scm"]["revision"] == 1

    assert data["duplicate"]["ok"] is False
    assert data["duplicate"]["code"] == "SCM_DUPLICATE_OPERATION"
    assert data["duplicate"]["state"] == {"count": 1, "revision": 1}

    assert data["stale"]["ok"] is False
    assert data["stale"]["code"] == "SCM_STALE_REVISION"
    assert data["stale"]["state"] == {"count": 1, "revision": 1}

    assert data["prohibited"]["ok"] is False
    assert data["prohibited"]["code"] == "INTENT_PROHIBITED"
    assert data["prohibited"]["state"] == {"count": 1, "revision": 1}

    assert data["reset"]["ok"] is True
    assert data["reset"]["state"] == {"count": 0, "revision": 2}
    assert data["final"] == {
        "state": {"count": 0, "revision": 2},
        "revision": 2,
        "appliedOperationIds": ["counter-op-1", "counter-op-2"],
    }

    assert len(data["evidence"]["receipts"]) == 5
    assert data["evidence"]["scm"]["revision"] == 2
    assert data["evidence"]["scm"]["appliedOperationIds"] == ["counter-op-1", "counter-op-2"]


def test_failed_application_postcondition_discards_scm_draft(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
const domain = Object.freeze({
  appId: "failed-counter",
  initialState: Object.freeze({count: 0, revision: 0}),
  invariantReads: Object.freeze(["state.count", "state.revision"]),
  invariants: Object.freeze([
    Object.freeze({id: "count-nonnegative", check(state) { return state.count >= 0; }})
  ])
});
const intents = Object.freeze({
  increment: Object.freeze({
    id: "increment",
    kind: "mutation",
    reads: Object.freeze(["state.count", "state.revision"]),
    writes: Object.freeze(["state.count", "state.revision"])
  })
});
const adapter = Object.freeze({
  appId: "failed-counter",
  adapterId: "failed-counter.adapter.v1",
  preflight() { return Object.freeze({ok: true}); },
  transition({state}) { return {count: state.count + 1, revision: state.revision + 1}; },
  validateEffects() { return false; }
});

const definition = McelApplicationRuntime.defineApplication({
  appId: "failed-counter",
  domain,
  intents,
  adapter
});
const app = McelApplicationRuntime.createApplicationInstance(definition);
const result = McelApplicationRuntime.dispatchApplicationIntent(app, {
  operationId: "failed-op-1",
  expectedRevision: 0,
  intentId: "increment",
  payload: {}
});
const evidence = McelApplicationRuntime.exportApplicationEvidence(app);

process.stdout.write(JSON.stringify({
  result,
  state: app.state,
  revision: app.revision,
  appliedOperationIds: app.appliedOperationIds,
  scm: evidence.scm
}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["result"]["ok"] is False
    assert data["result"]["code"] == "SCM_TRANSITION_POSTCONDITION_FAILED"
    assert data["state"] == {"count": 0, "revision": 0}
    assert data["revision"] == 0
    assert data["appliedOperationIds"] == []
    assert data["scm"]["revision"] == 0
    assert data["scm"]["appliedOperationIds"] == []


def test_adapter_cannot_write_outside_declared_intent_paths(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
const domain = Object.freeze({
  appId: "bounded-counter",
  initialState: Object.freeze({count: 0, revision: 0, secret: "stable"}),
  invariantReads: Object.freeze(["state.count", "state.revision", "state.secret"]),
  invariants: Object.freeze([])
});
const intents = Object.freeze({
  increment: Object.freeze({
    id: "increment",
    kind: "mutation",
    reads: Object.freeze(["state.count", "state.revision", "state.secret"]),
    writes: Object.freeze(["state.count", "state.revision"])
  })
});
const adapter = Object.freeze({
  appId: "bounded-counter",
  preflight() { return {ok: true}; },
  transition({state}) {
    return {count: state.count + 1, revision: state.revision + 1, secret: "changed"};
  },
  validateEffects() { return true; }
});

const definition = McelApplicationRuntime.defineApplication({
  appId: "bounded-counter",
  domain,
  intents,
  adapter
});
const app = McelApplicationRuntime.createApplicationInstance(definition);
const result = McelApplicationRuntime.dispatchApplicationIntent(app, {
  operationId: "bounded-op-1",
  expectedRevision: 0,
  intentId: "increment",
  payload: {}
});

process.stdout.write(JSON.stringify({
  result,
  state: app.state,
  revision: app.revision
}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["result"]["ok"] is False
    assert data["result"]["code"] == "SCM_TRANSITION_EXCEPTION"
    assert "undeclared state paths" in data["result"]["receipt"]["violation"]["message"]
    assert data["state"] == {"count": 0, "revision": 0, "secret": "stable"}
    assert data["revision"] == 0


def test_application_runtime_loads_between_scm_and_core_and_is_public() -> None:
    applications = APPLICATIONS.read_text(encoding="utf-8")
    scm_include = "<!-- @include applications/scripts/mcel-scm.js -->"
    runtime_include = "<!-- @include applications/scripts/mcel-application-runtime.js -->"
    core_include = "<!-- @include applications/scripts/mcel-core.js -->"

    assert applications.count(runtime_include) == 1
    assert applications.index(scm_include) < applications.index(runtime_include) < applications.index(core_include)

    core = CORE.read_text(encoding="utf-8")
    for method in (
        "defineApplication",
        "applicationDefinition",
        "listApplicationDefinitions",
        "createApplicationInstance",
        "readApplicationState",
        "createApplicationOperation",
        "dispatchApplicationIntent",
        "exportApplicationEvidence",
        "mountApplicationPackage",
        "applicationPackageMount",
    ):
        assert method in core

    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "McelLabScm" in runtime
    assert "scm.defineComponent" in runtime
    assert "scm.transition" in runtime
    assert "SCM_DUPLICATE_OPERATION" in runtime
    assert "SCM_STALE_REVISION" in runtime
    assert "APPLICATION_RUNTIME_PROJECTION_FINGERPRINT_MISMATCH" in runtime
    assert "APPLICATION_SURFACE_IDENTITY_MISMATCH" in runtime


def test_mcel_core_facade_executes_application_runtime(tmp_path: Path) -> None:
    stubs = r'''
const window = {};
var McelLabContract = {contractVersion: "mcel.facade.test", defaultSource: "", attributes: {}};
var McelLabEngine = {};
var McelLabEditor = {};
var McelLabStyleLaw = {};
var McelLabLayoutLaw = {};
var McelLabChromeLaw = {};
var McelLabBrowserObserver = {};
var McelLabPlatformSpine = {};
var McelLabWorkbench = {};
var McelLabBrowserRunner = {};
var McelLabCommandSurface = {};
var McelLabGraph = {};
var McelLabOpsRunner = {};
var McelLabAcidTests = {};
var McelLabSupervisor = {};
var McelLabLawRegistry = {};
'''
    script = (
        '"use strict";\n'
        + stubs
        + _script(SCM)
        + "\n"
        + _script(RUNTIME)
        + "\n"
        + _script(CORE)
        + r'''
const domain = Object.freeze({
  appId: "facade-counter",
  initialState: Object.freeze({count: 0, revision: 0}),
  invariantReads: Object.freeze(["state.count", "state.revision"]),
  invariants: Object.freeze([])
});
const intents = Object.freeze({
  increment: Object.freeze({
    id: "increment",
    kind: "mutation",
    reads: Object.freeze(["state.count", "state.revision"]),
    writes: Object.freeze(["state.count", "state.revision"])
  })
});
const adapter = Object.freeze({
  appId: "facade-counter",
  preflight() { return {ok: true}; },
  transition({state}) { return {count: state.count + 1, revision: state.revision + 1}; },
  validateEffects({before, after}) {
    return after.count === before.count + 1 && after.revision === before.revision + 1;
  }
});
const definition = MCEL.defineApplication({appId: "facade-counter", domain, intents, adapter});
const app = MCEL.createApplicationInstance(definition);
const operation = MCEL.createApplicationOperation(app, "facade-increment");
const result = MCEL.dispatchApplicationIntent(app, {...operation, intentId: "increment", payload: {}});
process.stdout.write(JSON.stringify({
  result,
  state: MCEL.readApplicationState(app),
  evidence: MCEL.exportApplicationEvidence(app),
  runtimeVersion: MCEL.applicationRuntime.contractVersion
}));
'''
    )
    data = _run_node_json(tmp_path, script)

    assert data["result"]["ok"] is True
    assert data["state"] == {"count": 1, "revision": 1}
    assert data["evidence"]["revision"] == 1
    assert data["runtimeVersion"] == "mcel.application-runtime.v1"


def test_mount_application_package_projects_state_and_binds_controls(tmp_path: Path) -> None:
    catalog = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-package-catalog.js"
    manifest = ROOT / "main_computer" / "web" / "applications" / "mcel-packages" / "contract-counter" / "mcel.runtime.json"
    script = _runtime_bootstrap() + f'''
const packageCatalog = require({json.dumps(str(catalog))});
const runtimeManifest = JSON.parse(fs.readFileSync({json.dumps(str(manifest))}, "utf8"));

class FakeElement {{
  constructor(attrs = {{}}, children = []) {{
    this.attrs = {{...attrs}};
    this.children = children;
    this.dataset = {{}};
    this.textContent = "";
    this.listeners = new Map();
  }}
  getAttribute(name) {{
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }}
  querySelectorAll(selector) {{
    const match = /^\\[([^\\]]+)\\]$/.exec(selector);
    const attr = match ? match[1] : null;
    const found = [];
    const walk = (node) => {{
      node.children.forEach((child) => {{
        if (attr && child.getAttribute(attr) !== null) found.push(child);
        walk(child);
      }});
    }};
    walk(this);
    return found;
  }}
  querySelector(selector) {{
    return this.querySelectorAll(selector)[0] || null;
  }}
  addEventListener(name, handler) {{
    this.listeners.set(name, handler);
  }}
  removeEventListener(name, handler) {{
    if (this.listeners.get(name) === handler) this.listeners.delete(name);
  }}
  click() {{
    const handler = this.listeners.get("click");
    if (handler) handler({{type: "click", target: this}});
  }}
}}

const value = new FakeElement({{
  "data-mcel-node-id": "contract-counter.value",
  "data-mcel-region-id": "contract-counter.region.value"
}});
const increment = new FakeElement({{
  "data-mcel-node-id": "contract-counter.increment-control",
  "data-mcel-intent-id": "increment"
}});
const reset = new FakeElement({{
  "data-mcel-node-id": "contract-counter.reset-control",
  "data-mcel-intent-id": "reset"
}});
const controls = new FakeElement({{"data-mcel-region-id": "contract-counter.region.controls"}}, [increment, reset]);
const receipt = new FakeElement({{"data-mcel-node-id": "contract-counter.latest-receipt"}});
const evidence = new FakeElement({{"data-mcel-region-id": "contract-counter.region.evidence"}}, [receipt]);
const status = new FakeElement({{"data-mcel-runtime-status": "pending"}});
const root = new FakeElement({{
  "data-mcel-surface-id": "contract-counter.surface.primary",
  "data-mcel-region-id": "contract-counter.region.shell"
}}, [value, controls, evidence, status]);

(async () => {{
  const moduleLoader = async (url) => {{
    const marker = "/mcel-packages/contract-counter/";
    const relative = url.slice(url.indexOf(marker) + marker.length);
    return importContract(`main_computer/web/applications/mcel-packages/contract-counter/${{relative}}`);
  }};
  const mount = await McelApplicationRuntime.mountApplicationPackage({{
    appId: "contract-counter",
    root,
    packageCatalog,
    manifest: runtimeManifest,
    manifestUrl: "http://example.test/applications/mcel-packages/contract-counter/mcel.runtime.json",
    moduleLoader,
    operationIdFactory: ({{intentId, revision}}) => `mount:${{intentId}}:${{revision}}`
  }});

  const initial = {{value: value.textContent, status: status.textContent, revision: mount.application.revision}};
  increment.click();
  const incremented = {{
    value: value.textContent,
    count: mount.readState().count,
    revision: mount.application.revision,
    status: status.textContent,
    receipt: JSON.parse(receipt.textContent)
  }};
  reset.click();
  const resetState = {{value: value.textContent, count: mount.readState().count, revision: mount.application.revision}};
  const unmounted = mount.unmount();
  increment.click();

  process.stdout.write(JSON.stringify({{
    initial,
    incremented,
    resetState,
    unmounted,
    afterUnmount: mount.readState(),
    activeMount: McelApplicationRuntime.applicationPackageMount(root)
  }}));
}})();
'''
    data = _run_node_json(tmp_path, script)

    assert data["initial"]["value"] == "0"
    assert "mounted at revision 0" in data["initial"]["status"]
    assert data["incremented"]["value"] == "1"
    assert data["incremented"]["count"] == 1
    assert data["incremented"]["revision"] == 1
    assert data["incremented"]["receipt"]["status"] == "committed"
    assert data["resetState"] == {"value": "0", "count": 0, "revision": 2}
    assert data["unmounted"] is True
    assert data["afterUnmount"] == {"count": 0, "revision": 2}
    assert data["activeMount"] is None


def test_mount_application_package_refuses_fingerprint_and_surface_mismatch(tmp_path: Path) -> None:
    catalog = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-package-catalog.js"
    manifest = ROOT / "main_computer" / "web" / "applications" / "mcel-packages" / "contract-counter" / "mcel.runtime.json"
    script = _runtime_bootstrap() + f'''
const packageCatalog = require({json.dumps(str(catalog))});
const runtimeManifest = JSON.parse(fs.readFileSync({json.dumps(str(manifest))}, "utf8"));
const root = {{
  dataset: {{}},
  getAttribute(name) {{ return name === "data-mcel-surface-id" ? "wrong.surface" : null; }},
  querySelectorAll() {{ return []; }},
  querySelector() {{ return null; }}
}};
(async () => {{
  const failures = [];
  const badFingerprint = JSON.parse(JSON.stringify(runtimeManifest));
  badFingerprint.source.packageFingerprint = "sha256:wrong";
  try {{
    await McelApplicationRuntime.mountApplicationPackage({{
      appId: "contract-counter",
      root,
      packageCatalog,
      manifest: badFingerprint,
      manifestUrl: "http://example.test/applications/mcel-packages/contract-counter/mcel.runtime.json",
      moduleLoader: async () => ({{}})
    }});
  }} catch (error) {{
    failures.push(error.violation.code);
  }}

  const modules = {{
    domain: await importContract("main_computer/web/applications/mcel-packages/contract-counter/contracts/domain.js"),
    intents: await importContract("main_computer/web/applications/mcel-packages/contract-counter/contracts/intents.js"),
    adapter: await importContract("main_computer/web/applications/mcel-packages/contract-counter/contracts/adapter.js"),
    surface: await importContract("main_computer/web/applications/mcel-packages/contract-counter/contracts/surface.js"),
    layout: await importContract("main_computer/web/applications/mcel-packages/contract-counter/contracts/layout.js")
  }};
  try {{
    await McelApplicationRuntime.mountApplicationPackage({{
      appId: "contract-counter",
      root,
      packageCatalog,
      manifest: runtimeManifest,
      manifestUrl: "http://example.test/applications/mcel-packages/contract-counter/mcel.runtime.json",
      moduleLoader: async (_url, entry) => modules[Object.keys(runtimeManifest.modules).find((key) => runtimeManifest.modules[key].export === entry.export)]
    }});
  }} catch (error) {{
    failures.push(error.violation.code);
  }}
  process.stdout.write(JSON.stringify({{failures}}));
}})();
'''
    data = _run_node_json(tmp_path, script)

    assert data["failures"] == [
        "APPLICATION_PACKAGE_FINGERPRINT_MISMATCH",
        "APPLICATION_SURFACE_IDENTITY_MISMATCH",
    ]
