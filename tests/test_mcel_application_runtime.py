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
        "readApplicationLocalState",
        "readApplicationDerivedState",
        "readApplicationProvisionalState",
        "readApplicationViewState",
        "updateApplicationLocalState",
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
    layout: await importContract("main_computer/web/applications/mcel-packages/contract-counter/contracts/layout.js"),
    observation: await importContract("main_computer/web/applications/mcel-packages/contract-counter/contracts/observation.js")
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


def test_contract_workbench_renderer_local_and_derived_state_recompute(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
(async () => {
  const domainModule = await importContract("mcel_apps/contract-workbench/contracts/domain.js");
  const intentsModule = await importContract("mcel_apps/contract-workbench/contracts/intents.js");
  const adapterModule = await importContract("mcel_apps/contract-workbench/contracts/adapter.js");
  const definition = McelApplicationRuntime.defineApplication({
    appId: "contract-workbench",
    domain: domainModule.ContractWorkbenchDomain,
    intents: intentsModule.ContractWorkbenchIntents,
    adapter: adapterModule.ContractWorkbenchAdapter
  });
  const app = McelApplicationRuntime.createApplicationInstance(definition, {
    id: "contract-workbench-local-derived"
  });

  const initialCanonical = app.readState();
  const initialLocal = app.readLocalState();
  const initialDerived = app.readDerivedState();
  const initialView = app.readViewState();
  const localUpdate = app.updateLocalState({draftName: "Steel", filterText: "steel"});
  const committed = app.dispatch({
    operationId: "workbench-op-1",
    expectedRevision: 0,
    intentId: "add-contract",
    payload: {name: "Steel", quantity: 12, category: "materials"}
  });
  const finalView = app.readViewState();
  const evidence = app.exportEvidence();

  process.stdout.write(JSON.stringify({
    definition: {
      localStateIds: definition.localStateIds,
      derivedStateIds: definition.derivedStateIds
    },
    initialCanonical,
    initialLocal,
    initialDerived,
    initialView,
    localUpdate,
    committed,
    finalView,
    evidence
  }));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
'''
    data = _run_node_json(tmp_path, script)

    assert data["definition"] == {
        "localStateIds": ["draftCategory", "draftName", "draftQuantity", "filterText", "sortMode"],
        "derivedStateIds": ["canSubmit", "totalQuantity", "visibleContracts"],
    }
    assert data["initialCanonical"] == {"contracts": [], "nextContractId": 1, "revision": 0}
    assert data["initialLocal"] == {
        "draftCategory": "materials",
        "draftName": "",
        "draftQuantity": "1",
        "filterText": "",
        "sortMode": "name",
    }
    assert data["initialDerived"] == {
        "canSubmit": False,
        "totalQuantity": 0,
        "visibleContracts": [],
    }
    assert data["initialView"] == {
        **data["initialCanonical"],
        **data["initialLocal"],
        **data["initialDerived"],
    }

    assert data["localUpdate"]["changed"] is True
    assert data["localUpdate"]["localRevision"] == 1
    assert data["localUpdate"]["after"]["derivedState"]["canSubmit"] is True
    assert data["localUpdate"]["after"]["viewState"]["filterText"] == "steel"
    assert data["initialCanonical"] == {"contracts": [], "nextContractId": 1, "revision": 0}

    assert data["committed"]["ok"] is True
    assert data["committed"]["receipt"]["adapter"]["derivedState"] == {"ok": True, "violation": None}
    assert data["finalView"]["totalQuantity"] == 12
    assert data["finalView"]["canSubmit"] is True
    assert [entry["id"] for entry in data["finalView"]["visibleContracts"]] == ["contract-1"]
    assert data["evidence"]["localRevision"] == 1
    assert data["evidence"]["stateAuthorities"]["canonical"]["revision"] == 1
    assert data["evidence"]["stateAuthorities"]["rendererLocal"]["draftName"] == "Steel"
    assert data["evidence"]["stateAuthorities"]["derived"]["totalQuantity"] == 12


def test_renderer_local_state_is_schema_bounded_and_instance_isolated(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
(async () => {
  const domainModule = await importContract("mcel_apps/contract-workbench/contracts/domain.js");
  const intentsModule = await importContract("mcel_apps/contract-workbench/contracts/intents.js");
  const adapterModule = await importContract("mcel_apps/contract-workbench/contracts/adapter.js");
  const definition = McelApplicationRuntime.defineApplication({
    appId: "contract-workbench",
    domain: domainModule.ContractWorkbenchDomain,
    intents: intentsModule.ContractWorkbenchIntents,
    adapter: adapterModule.ContractWorkbenchAdapter
  });
  const left = McelApplicationRuntime.createApplicationInstance(definition, {id: "workbench-left"});
  const right = McelApplicationRuntime.createApplicationInstance(definition, {id: "workbench-right"});

  left.updateLocalState({draftName: "Left only", sortMode: "quantity"});
  const invalidCodes = [];
  for (const patch of [{sortMode: "invalid"}, {unknownState: true}]) {
    try {
      left.updateLocalState(patch);
    } catch (error) {
      invalidCodes.push(error.violation.code);
    }
  }
  const leftResult = left.dispatch({
    operationId: "left-op-1",
    expectedRevision: 0,
    intentId: "add-contract",
    payload: {name: "Left only", quantity: 2, category: "services"}
  });
  const leftLocal = left.readLocalState();
  const rightLocal = right.readLocalState();
  const leftView = left.readViewState();
  const rightView = right.readViewState();

  let frozenMutationBlocked = false;
  try {
    leftLocal.draftName = "mutated";
  } catch (_error) {
    frozenMutationBlocked = true;
  }

  process.stdout.write(JSON.stringify({
    invalidCodes,
    leftResult,
    leftLocal,
    rightLocal,
    leftView,
    rightView,
    localRevisions: [left.localRevision, right.localRevision],
    frozen: Object.isFrozen(leftLocal),
    frozenMutationBlocked
  }));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
'''
    data = _run_node_json(tmp_path, script)

    assert data["invalidCodes"] == [
        "APPLICATION_LOCAL_STATE_SCHEMA_FAILED",
        "APPLICATION_LOCAL_STATE_UNKNOWN",
    ]
    assert data["leftResult"]["ok"] is True
    assert data["leftLocal"]["draftName"] == "Left only"
    assert data["leftLocal"]["sortMode"] == "quantity"
    assert data["rightLocal"]["draftName"] == ""
    assert data["rightLocal"]["sortMode"] == "name"
    assert data["leftView"]["totalQuantity"] == 2
    assert data["rightView"]["totalQuantity"] == 0
    assert data["rightView"]["contracts"] == []
    assert data["localRevisions"] == [1, 0]
    assert data["frozen"] is True
    assert data["frozenMutationBlocked"] is True


def test_derived_state_rejects_unknown_dependencies_cycles_and_invalid_results(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
function adapter(appId) {
  return Object.freeze({
    appId,
    preflight() { return {ok: true}; },
    transition({state}) { return state; },
    validateEffects() { return true; }
  });
}
function intents() {
  return Object.freeze({
    noop: Object.freeze({
      id: "noop",
      kind: "mutation",
      reads: Object.freeze(["state.value"]),
      writes: Object.freeze(["state.value"])
    })
  });
}
function domain(appId, derivedState) {
  return Object.freeze({
    appId,
    initialState: Object.freeze({value: 1}),
    rendererLocalState: Object.freeze({draft: ""}),
    rendererLocalStateDefinitions: Object.freeze([
      Object.freeze({id: "draft", initial: "", schema: Object.freeze({name: "string", describe: Object.freeze({minLength: 0, maxLength: null})})})
    ]),
    derivedState: Object.freeze(derivedState),
    invariantReads: Object.freeze(["state.value"]),
    invariants: Object.freeze([])
  });
}
const codes = [];
try {
  McelApplicationRuntime.defineApplication({
    appId: "unknown-derived",
    domain: domain("unknown-derived", [
      Object.freeze({id: "summary", reads: Object.freeze(["missing"]), compute() { return 1; }, schema: Object.freeze({name: "integer", describe: Object.freeze({minimum: 0, maximum: null})})})
    ]),
    intents: intents(),
    adapter: adapter("unknown-derived")
  });
} catch (error) {
  codes.push(error.violation.code);
}
try {
  McelApplicationRuntime.defineApplication({
    appId: "cyclic-derived",
    domain: domain("cyclic-derived", [
      Object.freeze({id: "left", reads: Object.freeze(["right"]), compute({right}) { return right; }, schema: Object.freeze({name: "integer", describe: Object.freeze({minimum: null, maximum: null})})}),
      Object.freeze({id: "right", reads: Object.freeze(["left"]), compute({left}) { return left; }, schema: Object.freeze({name: "integer", describe: Object.freeze({minimum: null, maximum: null})})})
    ]),
    intents: intents(),
    adapter: adapter("cyclic-derived")
  });
} catch (error) {
  codes.push(error.violation.code);
}
try {
  const definition = McelApplicationRuntime.defineApplication({
    appId: "invalid-derived",
    domain: domain("invalid-derived", [
      Object.freeze({id: "summary", reads: Object.freeze(["value"]), compute() { return "wrong"; }, schema: Object.freeze({name: "integer", describe: Object.freeze({minimum: 0, maximum: null})})})
    ]),
    intents: intents(),
    adapter: adapter("invalid-derived")
  });
  McelApplicationRuntime.createApplicationInstance(definition);
} catch (error) {
  codes.push(error.violation.code);
}
process.stdout.write(JSON.stringify({codes}));
'''
    data = _run_node_json(tmp_path, script)
    assert data["codes"] == [
        "APPLICATION_DERIVED_STATE_DEPENDENCY_UNKNOWN",
        "APPLICATION_DERIVED_STATE_CYCLE",
        "APPLICATION_DERIVED_STATE_SCHEMA_FAILED",
    ]


def test_mount_application_package_binds_renderer_local_inputs_and_extracts_typed_payloads(tmp_path: Path) -> None:
    catalog = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-package-catalog.js"
    manifest = ROOT / "main_computer" / "web" / "applications" / "mcel-packages" / "contract-workbench" / "mcel.runtime.json"
    script = _runtime_bootstrap() + f'''
const packageCatalog = require({json.dumps(str(catalog))});
const runtimeManifest = JSON.parse(fs.readFileSync({json.dumps(str(manifest))}, "utf8"));

class FakeElement {{
  constructor(tagName, attrs = {{}}, children = []) {{
    this.tagName = String(tagName || "div").toUpperCase();
    this.attrs = {{...attrs}};
    this.children = children;
    this.dataset = {{}};
    this.textContent = "";
    this.value = Object.prototype.hasOwnProperty.call(attrs, "value") ? String(attrs.value) : "";
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
  querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
  addEventListener(name, handler) {{ this.listeners.set(name, handler); }}
  removeEventListener(name, handler) {{ if (this.listeners.get(name) === handler) this.listeners.delete(name); }}
  emit(name) {{ const handler = this.listeners.get(name); return handler ? handler({{type: name, target: this}}) : undefined; }}
  click() {{ return this.emit("click"); }}
}}

const name = new FakeElement("input", {{"type": "text", "data-mcel-node-id": "contract-workbench.draft-name"}});
const quantity = new FakeElement("input", {{"type": "number", "data-mcel-node-id": "contract-workbench.draft-quantity", "value": "1"}});
const category = new FakeElement("select", {{"data-mcel-node-id": "contract-workbench.draft-category"}});
const sortMode = new FakeElement("select", {{"data-mcel-node-id": "contract-workbench.sort-mode"}});
const add = new FakeElement("button", {{"data-mcel-node-id": "contract-workbench.add-control", "data-mcel-intent-id": "add-contract"}});
const editor = new FakeElement("section", {{"data-mcel-region-id": "contract-workbench.region.editor"}}, [name, quantity, category, sortMode, add]);
const receipt = new FakeElement("pre", {{"data-mcel-node-id": "contract-workbench.latest-receipt"}});
const evidence = new FakeElement("section", {{"data-mcel-region-id": "contract-workbench.region.evidence"}}, [receipt]);
const status = new FakeElement("p", {{"data-mcel-runtime-status": "pending"}});
const root = new FakeElement("main", {{
  "data-mcel-surface-id": "contract-workbench.surface.input-wave",
  "data-mcel-region-id": "contract-workbench.region.shell"
}}, [editor, evidence, status]);

const surface = Object.freeze({{
  schema: "mcel.semantic-surface-ir.v1",
  appId: "contract-workbench",
  surfaceId: "contract-workbench.surface.input-wave",
  regions: Object.freeze([
    Object.freeze({{id: "contract-workbench.region.shell", role: "application"}}),
    Object.freeze({{id: "contract-workbench.region.editor", role: "form"}}),
    Object.freeze({{id: "contract-workbench.region.evidence", role: "status"}})
  ]),
  nodes: Object.freeze([
    Object.freeze({{id: "contract-workbench.draft-name", kind: "input", regionId: "contract-workbench.region.editor", inputType: "text", localPath: "draftName"}}),
    Object.freeze({{id: "contract-workbench.draft-quantity", kind: "input", regionId: "contract-workbench.region.editor", inputType: "number", localPath: "draftQuantity"}}),
    Object.freeze({{id: "contract-workbench.draft-category", kind: "input", regionId: "contract-workbench.region.editor", inputType: "select", localPath: "draftCategory"}}),
    Object.freeze({{id: "contract-workbench.sort-mode", kind: "input", regionId: "contract-workbench.region.editor", inputType: "select", localPath: "sortMode"}}),
    Object.freeze({{
      id: "contract-workbench.add-control",
      kind: "control",
      regionId: "contract-workbench.region.editor",
      intentId: "add-contract",
      payload: Object.freeze({{
        name: Object.freeze({{fromNode: "contract-workbench.draft-name", property: "value", normalize: "trim"}}),
        quantity: Object.freeze({{fromNode: "contract-workbench.draft-quantity", property: "value", parse: "integer"}}),
        category: Object.freeze({{fromNode: "contract-workbench.draft-category", property: "value"}})
      }})
    }}),
    Object.freeze({{id: "contract-workbench.latest-receipt", kind: "operation-evidence", regionId: "contract-workbench.region.evidence"}})
  ])
}});
const layout = Object.freeze({{
  schema: "mcel.layout-grammar.v1",
  surfaceId: surface.surfaceId,
  regions: Object.freeze({{
    "contract-workbench.region.shell": Object.freeze({{direction: "column"}}),
    "contract-workbench.region.editor": Object.freeze({{direction: "column"}}),
    "contract-workbench.region.evidence": Object.freeze({{direction: "column"}})
  }}),
  constraints: Object.freeze([])
}});

(async () => {{
  const modules = {{
    ContractWorkbenchDomain: await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/domain.js"),
    ContractWorkbenchIntents: await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/intents.js"),
    ContractWorkbenchAdapter: await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/adapter.js")
  }};
  const moduleLoader = async (_url, entry) => {{
    if (entry.export === "ContractWorkbenchSurface") return {{ContractWorkbenchSurface: surface}};
    if (entry.export === "ContractWorkbenchLayout") return {{ContractWorkbenchLayout: layout}};
    if (entry.export === "ContractWorkbenchObservation") return {{ContractWorkbenchObservation: {{schema: "mcel.browser-observation.v1", appId: "contract-workbench"}}}};
    return modules[entry.export];
  }};
  const mount = await McelApplicationRuntime.mountApplicationPackage({{
    appId: "contract-workbench",
    root,
    packageCatalog,
    manifest: runtimeManifest,
    manifestUrl: "http://example.test/applications/mcel-packages/contract-workbench/mcel.runtime.json",
    moduleLoader,
    operationIdFactory: ({{intentId, revision}}) => `input-wave:${{intentId}}:${{revision}}`
  }});

  const initial = {{
    elementValues: [name.value, quantity.value, category.value, sortMode.value],
    local: mount.readLocalState(),
    derived: mount.readDerivedState()
  }};
  name.value = "  Steel  ";
  name.emit("input");
  quantity.value = "12";
  quantity.emit("input");
  category.value = "services";
  category.emit("change");
  const afterInput = {{local: mount.readLocalState(), derived: mount.readDerivedState()}};
  add.click();
  const committed = {{
    state: mount.readState(),
    result: mount.readLastResult(),
    receipt: JSON.parse(receipt.textContent),
    status: status.textContent
  }};

  mount.updateLocalState({{draftName: "Aluminum"}});
  const programmaticValue = name.value;

  quantity.value = "not-an-integer";
  quantity.emit("input");
  add.click();
  const parseFailure = {{
    state: mount.readState(),
    result: mount.readLastResult(),
    receiptCount: mount.application.receipts.length,
    status: status.textContent
  }};

  sortMode.value = "invalid";
  sortMode.emit("change");
  const invalidLocal = {{
    elementValue: sortMode.value,
    local: mount.readLocalState(),
    result: mount.readLastResult()
  }};

  mount.unmount();
  name.value = "After unmount";
  name.emit("input");
  const afterUnmount = mount.readLocalState();

  process.stdout.write(JSON.stringify({{initial, afterInput, committed, programmaticValue, parseFailure, invalidLocal, afterUnmount}}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
'''
    data = _run_node_json(tmp_path, script)

    assert data["initial"]["elementValues"] == ["", "1", "materials", "name"]
    assert data["initial"]["local"]["draftQuantity"] == "1"
    assert data["initial"]["derived"]["canSubmit"] is False
    assert data["afterInput"]["local"]["draftName"] == "  Steel  "
    assert data["afterInput"]["local"]["draftQuantity"] == "12"
    assert data["afterInput"]["local"]["draftCategory"] == "services"
    assert data["afterInput"]["derived"]["canSubmit"] is True
    assert data["committed"]["result"]["ok"] is True
    assert data["committed"]["state"]["contracts"] == [
        {
            "id": "contract-1",
            "name": "Steel",
            "category": "services",
            "quantity": 12,
            "quoteStatus": "idle",
            "quoteAmount": 0,
        }
    ]
    assert data["committed"]["receipt"]["status"] == "committed"
    assert "committed at revision 1" in data["committed"]["status"]
    assert data["programmaticValue"] == "Aluminum"
    assert data["parseFailure"]["result"]["ok"] is False
    assert data["parseFailure"]["result"]["code"] == "APPLICATION_CONTROL_PAYLOAD_PARSE_FAILED"
    assert data["parseFailure"]["result"]["receipt"] is None
    assert data["parseFailure"]["state"]["revision"] == 1
    assert data["parseFailure"]["receiptCount"] == 1
    assert "refused" in data["parseFailure"]["status"]
    assert data["invalidLocal"]["elementValue"] == "name"
    assert data["invalidLocal"]["local"]["sortMode"] == "name"
    assert data["invalidLocal"]["result"]["code"] == "APPLICATION_LOCAL_STATE_SCHEMA_FAILED"
    assert data["afterUnmount"]["draftName"] == "Aluminum"


def test_input_and_static_payload_contracts_fail_closed_with_stable_codes(tmp_path: Path) -> None:
    catalog = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-package-catalog.js"
    manifest = ROOT / "main_computer" / "web" / "applications" / "mcel-packages" / "contract-workbench" / "mcel.runtime.json"
    script = _runtime_bootstrap() + f'''
const packageCatalog = require({json.dumps(str(catalog))});
const runtimeManifest = JSON.parse(fs.readFileSync({json.dumps(str(manifest))}, "utf8"));
class FakeElement {{
  constructor(tagName, attrs = {{}}, children = []) {{ this.tagName=String(tagName||"div").toUpperCase(); this.attrs={{...attrs}}; this.children=children; this.dataset={{}}; this.textContent=""; this.value=""; this.listeners=new Map(); }}
  getAttribute(name) {{ return Object.prototype.hasOwnProperty.call(this.attrs,name) ? this.attrs[name] : null; }}
  querySelectorAll(selector) {{ const m=/^\\[([^\\]]+)\\]$/.exec(selector); const a=m?m[1]:null; const out=[]; const walk=(n)=>n.children.forEach((c)=>{{if(a&&c.getAttribute(a)!==null)out.push(c);walk(c);}}); walk(this); return out; }}
  querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
  addEventListener(name, handler) {{ this.listeners.set(name, handler); }}
  removeEventListener(name, handler) {{ if(this.listeners.get(name)===handler)this.listeners.delete(name); }}
}}
(async () => {{
  const domain = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/domain.js");
  const intents = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/intents.js");
  const adapter = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/adapter.js");
  const failures=[];
  const cases = [
    {{code:"APPLICATION_INPUT_LOCAL_PATH_UNKNOWN", input:{{localPath:"missing",inputType:"text"}}, payload:{{}}}},
    {{code:"APPLICATION_INPUT_TYPE_UNSUPPORTED", input:{{localPath:"draftName",inputType:"checkbox"}}, payload:{{}}}},
    {{code:"APPLICATION_CONTROL_PAYLOAD_SOURCE_MISSING", input:{{localPath:"draftName",inputType:"text"}}, payload:{{name:{{fromNode:"missing",property:"value"}}}}}},
    {{code:"APPLICATION_CONTROL_PAYLOAD_PROPERTY_UNSUPPORTED", input:{{localPath:"draftName",inputType:"text"}}, payload:{{name:{{fromNode:"input-node",property:"textContent"}}}}}},
    {{code:"APPLICATION_CONTROL_PAYLOAD_NORMALIZER_UNKNOWN", input:{{localPath:"draftName",inputType:"text"}}, payload:{{name:{{fromNode:"input-node",property:"value",normalize:"lowercase"}}}}}},
    {{code:"APPLICATION_CONTROL_PAYLOAD_PARSER_UNKNOWN", input:{{localPath:"draftName",inputType:"text"}}, payload:{{name:{{fromNode:"input-node",property:"value",parse:"float"}}}}}}
  ];
  for (let index=0; index<cases.length; index+=1) {{
    const item=cases[index];
    const input=new FakeElement("input",{{type:item.input.inputType,"data-mcel-node-id":"input-node"}});
    const control=new FakeElement("button",{{"data-mcel-node-id":"control-node","data-mcel-intent-id":"add-contract"}});
    const editor=new FakeElement("section",{{"data-mcel-region-id":"editor"}},[input,control]);
    const root=new FakeElement("main",{{"data-mcel-surface-id":`surface-${{index}}`,"data-mcel-region-id":"shell"}},[editor]);
    const surface={{schema:"mcel.semantic-surface-ir.v1",appId:"contract-workbench",surfaceId:`surface-${{index}}`,regions:[{{id:"shell",role:"application"}},{{id:"editor",role:"form"}}],nodes:[{{id:"input-node",kind:"input",regionId:"editor",...item.input}},{{id:"control-node",kind:"control",regionId:"editor",intentId:"add-contract",payload:item.payload}}]}};
    const layout={{schema:"mcel.layout-grammar.v1",surfaceId:surface.surfaceId,regions:{{shell:{{direction:"column"}},editor:{{direction:"column"}}}},constraints:[]}};
    try {{
      await McelApplicationRuntime.mountApplicationPackage({{
        appId:"contract-workbench",root,packageCatalog,manifest:runtimeManifest,
        manifestUrl:"http://example.test/applications/mcel-packages/contract-workbench/mcel.runtime.json",
        moduleLoader:async(_url,entry)=>{{
          if(entry.export==="ContractWorkbenchDomain")return domain;
          if(entry.export==="ContractWorkbenchIntents")return intents;
          if(entry.export==="ContractWorkbenchAdapter")return adapter;
          if(entry.export==="ContractWorkbenchSurface")return {{ContractWorkbenchSurface:surface}};
          if(entry.export==="ContractWorkbenchLayout")return {{ContractWorkbenchLayout:layout}};
          return {{ContractWorkbenchObservation:{{schema:"mcel.browser-observation.v1",appId:"contract-workbench"}}}};
        }}
      }});
    }} catch(error) {{ failures.push(error.violation.code); }}
  }}
  process.stdout.write(JSON.stringify({{failures,expected:cases.map((item)=>item.code)}}));
}})().catch((error)=>{{console.error(error);process.exit(1);}});
'''
    data = _run_node_json(tmp_path, script)
    assert data["failures"] == data["expected"]


def test_mount_application_package_projects_safe_properties_and_conditionals(tmp_path: Path) -> None:
    catalog = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-package-catalog.js"
    manifest = ROOT / "main_computer" / "web" / "applications" / "mcel-packages" / "contract-workbench" / "mcel.runtime.json"
    script = _runtime_bootstrap() + f'''
const packageCatalog = require({json.dumps(str(catalog))});
const runtimeManifest = JSON.parse(fs.readFileSync({json.dumps(str(manifest))}, "utf8"));

class FakeFragment {{
  constructor(children = []) {{ this.children = children; this.isFragment = true; }}
  cloneNode(deep) {{ return new FakeFragment(deep ? this.children.map((child) => child.cloneNode(true)) : []); }}
}}
class FakeElement {{
  constructor(tagName, attrs = {{}}, children = []) {{
    this.tagName = String(tagName || "div").toUpperCase();
    this.attrs = {{...attrs}};
    this.children = children;
    this.dataset = {{}};
    this.textContent = Object.prototype.hasOwnProperty.call(attrs, "textContent") ? String(attrs.textContent) : "";
    this.value = Object.prototype.hasOwnProperty.call(attrs, "value") ? String(attrs.value) : "";
    this.disabled = false;
    this.listeners = new Map();
  }}
  get firstChild() {{ return this.children[0] || null; }}
  get firstElementChild() {{ return this.children[0] || null; }}
  getAttribute(name) {{ return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }}
  setAttribute(name, value) {{ this.attrs[name] = String(value); }}
  removeAttribute(name) {{ delete this.attrs[name]; }}
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
  querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
  addEventListener(name, handler) {{ this.listeners.set(name, handler); }}
  removeEventListener(name, handler) {{ if (this.listeners.get(name) === handler) this.listeners.delete(name); }}
  emit(name) {{ const handler = this.listeners.get(name); if (handler) return handler({{type: name, target: this}}); return null; }}
  click() {{ if (this.disabled) return null; return this.emit("click"); }}
  replaceChildren(...children) {{ this.children = children; }}
  appendChild(child) {{
    if (child?.isFragment) this.children.push(...child.children);
    else this.children.push(child);
    return child;
  }}
  cloneNode(deep) {{
    const clone = new FakeElement(this.tagName, {{...this.attrs, value: this.value, textContent: this.textContent}}, deep ? this.children.map((child) => child.cloneNode(true)) : []);
    clone.textContent = this.textContent;
    clone.disabled = this.disabled;
    return clone;
  }}
}}
class FakeTemplate extends FakeElement {{
  constructor(templateId, children) {{
    super("template", {{"data-mcel-template-id": templateId}}, []);
    this.content = new FakeFragment(children);
  }}
}}

const name = new FakeElement("input", {{type: "text", "data-mcel-node-id": "contract-workbench.draft-name"}});
const quantity = new FakeElement("input", {{type: "number", "data-mcel-node-id": "contract-workbench.draft-quantity", value: "1"}});
const category = new FakeElement("select", {{"data-mcel-node-id": "contract-workbench.draft-category", value: "materials"}});
const add = new FakeElement("button", {{"data-mcel-node-id": "contract-workbench.add-control", "data-mcel-intent-id": "add-contract"}});
const validation = new FakeElement("div", {{"data-mcel-node-id": "contract-workbench.validation", "data-mcel-conditional-host": ""}});
const editor = new FakeElement("section", {{"data-mcel-region-id": "contract-workbench.region.editor"}}, [name, quantity, category, add, validation]);
const total = new FakeElement("strong", {{"data-mcel-node-id": "contract-workbench.total-quantity"}});
const visible = new FakeElement("strong", {{"data-mcel-node-id": "contract-workbench.visible-count"}});
const summary = new FakeElement("section", {{"data-mcel-region-id": "contract-workbench.region.summary"}}, [total, visible]);
const empty = new FakeElement("div", {{"data-mcel-node-id": "contract-workbench.empty-state", "data-mcel-conditional-host": ""}});
const collection = new FakeElement("section", {{"data-mcel-region-id": "contract-workbench.region.collection"}}, [empty]);
const receipt = new FakeElement("pre", {{"data-mcel-node-id": "contract-workbench.latest-receipt"}});
const evidence = new FakeElement("section", {{"data-mcel-region-id": "contract-workbench.region.evidence"}}, [receipt]);
const status = new FakeElement("p", {{"data-mcel-runtime-status": "pending"}});
const validationTemplate = new FakeTemplate("contract-workbench.validation-message", [new FakeElement("p", {{role: "alert"}})]);
const emptyTemplate = new FakeTemplate("contract-workbench.empty-state-template", [new FakeElement("p")]);
const root = new FakeElement("main", {{
  "data-mcel-surface-id": "contract-workbench.surface.property-conditional-wave",
  "data-mcel-region-id": "contract-workbench.region.shell"
}}, [editor, summary, collection, evidence, status, validationTemplate, emptyTemplate]);

const surface = Object.freeze({{
  schema: "mcel.semantic-surface-ir.v1",
  appId: "contract-workbench",
  surfaceId: "contract-workbench.surface.property-conditional-wave",
  regions: Object.freeze([
    Object.freeze({{id: "contract-workbench.region.shell", role: "application"}}),
    Object.freeze({{id: "contract-workbench.region.editor", role: "form"}}),
    Object.freeze({{id: "contract-workbench.region.summary", role: "status"}}),
    Object.freeze({{id: "contract-workbench.region.collection", role: "list"}}),
    Object.freeze({{id: "contract-workbench.region.evidence", role: "status"}})
  ]),
  nodes: Object.freeze([
    Object.freeze({{id: "contract-workbench.draft-name", kind: "input", regionId: "contract-workbench.region.editor", inputType: "text", localPath: "draftName"}}),
    Object.freeze({{id: "contract-workbench.draft-quantity", kind: "input", regionId: "contract-workbench.region.editor", inputType: "number", localPath: "draftQuantity"}}),
    Object.freeze({{id: "contract-workbench.draft-category", kind: "input", regionId: "contract-workbench.region.editor", inputType: "select", localPath: "draftCategory"}}),
    Object.freeze({{
      id: "contract-workbench.add-control", kind: "control", regionId: "contract-workbench.region.editor", intentId: "add-contract",
      properties: Object.freeze([Object.freeze({{statePath: "canSubmit", property: "disabled", transform: "not"}})]),
      payload: Object.freeze({{
        name: Object.freeze({{fromNode: "contract-workbench.draft-name", property: "value", normalize: "trim"}}),
        quantity: Object.freeze({{fromNode: "contract-workbench.draft-quantity", property: "value", parse: "integer"}}),
        category: Object.freeze({{fromNode: "contract-workbench.draft-category", property: "value"}})
      }})
    }}),
    Object.freeze({{
      id: "contract-workbench.validation", kind: "conditional", regionId: "contract-workbench.region.editor",
      source: Object.freeze({{fromLatestReceipt: "message"}}), templateId: "contract-workbench.validation-message",
      when: Object.freeze({{predicate: "nonempty"}}), content: Object.freeze({{property: "textContent"}})
    }}),
    Object.freeze({{id: "contract-workbench.total-quantity", kind: "property", regionId: "contract-workbench.region.summary", statePath: "totalQuantity", property: "textContent", transform: "string"}}),
    Object.freeze({{id: "contract-workbench.visible-count", kind: "property", regionId: "contract-workbench.region.summary", statePath: "visibleContracts.length", property: "textContent", transform: "string"}}),
    Object.freeze({{
      id: "contract-workbench.empty-state", kind: "conditional", regionId: "contract-workbench.region.collection",
      statePath: "visibleContracts", templateId: "contract-workbench.empty-state-template",
      when: Object.freeze({{predicate: "empty"}}), content: Object.freeze({{literal: "No contracts match the current view."}})
    }}),
    Object.freeze({{id: "contract-workbench.latest-receipt", kind: "operation-evidence", regionId: "contract-workbench.region.evidence"}})
  ])
}});
const layout = Object.freeze({{
  schema: "mcel.layout-grammar.v1", surfaceId: surface.surfaceId,
  regions: Object.freeze({{
    "contract-workbench.region.shell": Object.freeze({{direction: "column"}}),
    "contract-workbench.region.editor": Object.freeze({{direction: "column"}}),
    "contract-workbench.region.summary": Object.freeze({{direction: "row"}}),
    "contract-workbench.region.collection": Object.freeze({{direction: "column"}}),
    "contract-workbench.region.evidence": Object.freeze({{direction: "column"}})
  }}), constraints: Object.freeze([])
}});

(async () => {{
  const modules = {{
    ContractWorkbenchDomain: await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/domain.js"),
    ContractWorkbenchIntents: await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/intents.js"),
    ContractWorkbenchAdapter: await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/adapter.js")
  }};
  const moduleLoader = async (_url, entry) => {{
    if (entry.export === "ContractWorkbenchSurface") return {{ContractWorkbenchSurface: surface}};
    if (entry.export === "ContractWorkbenchLayout") return {{ContractWorkbenchLayout: layout}};
    if (entry.export === "ContractWorkbenchObservation") return {{ContractWorkbenchObservation: {{schema: "mcel.browser-observation.v1", appId: "contract-workbench"}}}};
    return modules[entry.export];
  }};
  const mount = await McelApplicationRuntime.mountApplicationPackage({{
    appId: "contract-workbench", root, packageCatalog, manifest: runtimeManifest,
    manifestUrl: "http://example.test/applications/mcel-packages/contract-workbench/mcel.runtime.json",
    moduleLoader,
    operationIdFactory: ({{intentId, revision}}) => `property-wave:${{intentId}}:${{revision}}`
  }});

  const initial = {{
    total: total.textContent,
    visible: visible.textContent,
    addDisabled: add.disabled,
    emptyCount: empty.children.length,
    emptyText: empty.firstChild?.textContent || "",
    validationCount: validation.children.length
  }};

  name.value = "Steel";
  name.emit("input");
  quantity.value = "12";
  quantity.emit("input");
  const ready = {{addDisabled: add.disabled, canSubmit: mount.readDerivedState().canSubmit}};
  add.click();
  const committed = {{
    total: total.textContent,
    visible: visible.textContent,
    emptyCount: empty.children.length,
    validationCount: validation.children.length,
    state: mount.readState()
  }};

  mount.dispatch("add-contract", {{name: "", quantity: 1, category: "materials"}}, {{operationId: "property-wave:refusal", expectedRevision: 1}});
  mount.render(mount.readLastResult());
  const refused = {{
    message: mount.readLastResult().receipt.message,
    validationCount: validation.children.length,
    validationText: validation.firstChild?.textContent || ""
  }};

  mount.updateLocalState({{filterText: "does-not-match"}});
  mount.render(mount.readLastResult());
  const filtered = {{visible: visible.textContent, emptyCount: empty.children.length, emptyText: empty.firstChild?.textContent || ""}};
  mount.updateLocalState({{filterText: "", draftName: ""}});
  const restored = {{visible: visible.textContent, emptyCount: empty.children.length, addDisabled: add.disabled}};

  mount.unmount();
  const afterUnmount = {{emptyCount: empty.children.length, validationCount: validation.children.length}};
  process.stdout.write(JSON.stringify({{initial, ready, committed, refused, filtered, restored, afterUnmount}}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
'''
    data = _run_node_json(tmp_path, script)

    assert data["initial"] == {
        "total": "0",
        "visible": "0",
        "addDisabled": True,
        "emptyCount": 1,
        "emptyText": "No contracts match the current view.",
        "validationCount": 0,
    }
    assert data["ready"] == {"addDisabled": False, "canSubmit": True}
    assert data["committed"]["total"] == "12"
    assert data["committed"]["visible"] == "1"
    assert data["committed"]["emptyCount"] == 0
    assert data["committed"]["validationCount"] == 0
    assert data["committed"]["state"]["revision"] == 1
    assert data["refused"] == {
        "message": "A contract name is required.",
        "validationCount": 1,
        "validationText": "A contract name is required.",
    }
    assert data["filtered"] == {
        "visible": "0",
        "emptyCount": 1,
        "emptyText": "No contracts match the current view.",
    }
    assert data["restored"] == {"visible": "1", "emptyCount": 0, "addDisabled": True}
    assert data["afterUnmount"] == {"emptyCount": 0, "validationCount": 0}


def test_property_and_conditional_contracts_fail_closed_with_stable_codes(tmp_path: Path) -> None:
    catalog = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-package-catalog.js"
    manifest = ROOT / "main_computer" / "web" / "applications" / "mcel-packages" / "contract-workbench" / "mcel.runtime.json"
    script = _runtime_bootstrap() + f'''
const packageCatalog = require({json.dumps(str(catalog))});
const runtimeManifest = JSON.parse(fs.readFileSync({json.dumps(str(manifest))}, "utf8"));
class FakeElement {{
  constructor(attrs={{}},children=[]) {{ this.attrs={{...attrs}}; this.children=children; this.dataset={{}}; this.textContent=""; this.disabled=false; }}
  get firstChild() {{ return this.children[0] || null; }}
  get firstElementChild() {{ return this.children[0] || null; }}
  getAttribute(name) {{ return Object.prototype.hasOwnProperty.call(this.attrs,name) ? this.attrs[name] : null; }}
  setAttribute(name,value) {{ this.attrs[name]=String(value); }}
  removeAttribute(name) {{ delete this.attrs[name]; }}
  querySelectorAll(selector) {{ const m=/^\\[([^\\]]+)\\]$/.exec(selector); const a=m?m[1]:null; const out=[]; const walk=(n)=>n.children.forEach((c)=>{{if(a&&c.getAttribute(a)!==null)out.push(c);walk(c);}}); walk(this); return out; }}
  querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}
  addEventListener() {{}}
  removeEventListener() {{}}
  replaceChildren(...children) {{ this.children=children; }}
  appendChild(child) {{ this.children.push(child); return child; }}
}}
(async()=>{{
  const domain=await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/domain.js");
  const intents=await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/intents.js");
  const adapter=await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/adapter.js");
  const cases=[
    {{code:"APPLICATION_PROPERTY_STATE_PATH_UNKNOWN", node:{{kind:"property",statePath:"missing",property:"textContent"}}}},
    {{code:"APPLICATION_PROPERTY_UNSUPPORTED", node:{{kind:"property",statePath:"totalQuantity",property:"innerHTML"}}}},
    {{code:"APPLICATION_PROPERTY_TRANSFORM_UNSUPPORTED", node:{{kind:"property",statePath:"totalQuantity",property:"textContent",transform:"json"}}}},
    {{code:"APPLICATION_PROPERTY_VALUE_INVALID", node:{{kind:"property",statePath:"draftName",property:"disabled"}}}},
    {{code:"APPLICATION_CONDITIONAL_HOST_REQUIRED", node:{{kind:"conditional",statePath:"visibleContracts",templateId:"template",when:{{predicate:"empty"}},content:{{literal:"Empty"}}}}, host:false, template:true}},
    {{code:"APPLICATION_CONDITIONAL_SOURCE_INVALID", node:{{kind:"conditional",templateId:"template",when:{{predicate:"empty"}},content:{{literal:"Empty"}}}}, host:true, template:true}},
    {{code:"APPLICATION_CONDITIONAL_PREDICATE_UNSUPPORTED", node:{{kind:"conditional",statePath:"visibleContracts",templateId:"template",when:{{predicate:"contains"}},content:{{literal:"Empty"}}}}, host:true, template:true}},
    {{code:"APPLICATION_CONDITIONAL_TEMPLATE_MISSING", node:{{kind:"conditional",statePath:"visibleContracts",templateId:"missing",when:{{predicate:"empty"}},content:{{literal:"Empty"}}}}, host:true, template:false}},
    {{code:"APPLICATION_CONDITIONAL_CONTENT_UNSUPPORTED", node:{{kind:"conditional",statePath:"visibleContracts",templateId:"template",when:{{predicate:"empty"}},content:{{property:"innerHTML"}}}}, host:true, template:true}}
  ];
  const failures=[];
  for(let index=0;index<cases.length;index+=1){{
    const item=cases[index];
    const attrs={{"data-mcel-node-id":"node"}};
    if(item.host)attrs["data-mcel-conditional-host"]="";
    const element=new FakeElement(attrs);
    const editor=new FakeElement({{"data-mcel-region-id":"editor"}},[element]);
    const children=[editor];
    if(item.template)children.push(new FakeElement({{"data-mcel-template-id":"template"}}));
    const root=new FakeElement({{"data-mcel-surface-id":`surface-${{index}}`,"data-mcel-region-id":"shell"}},children);
    const surface={{schema:"mcel.semantic-surface-ir.v1",appId:"contract-workbench",surfaceId:`surface-${{index}}`,regions:[{{id:"shell",role:"application"}},{{id:"editor",role:"status"}}],nodes:[{{id:"node",regionId:"editor",...item.node}}]}};
    const layout={{schema:"mcel.layout-grammar.v1",surfaceId:surface.surfaceId,regions:{{shell:{{direction:"column"}},editor:{{direction:"column"}}}},constraints:[]}};
    try{{
      await McelApplicationRuntime.mountApplicationPackage({{appId:"contract-workbench",root,packageCatalog,manifest:runtimeManifest,manifestUrl:"http://example.test/applications/mcel-packages/contract-workbench/mcel.runtime.json",moduleLoader:async(_url,entry)=>{{
        if(entry.export==="ContractWorkbenchDomain")return domain;
        if(entry.export==="ContractWorkbenchIntents")return intents;
        if(entry.export==="ContractWorkbenchAdapter")return adapter;
        if(entry.export==="ContractWorkbenchSurface")return {{ContractWorkbenchSurface:surface}};
        if(entry.export==="ContractWorkbenchLayout")return {{ContractWorkbenchLayout:layout}};
        return {{ContractWorkbenchObservation:{{schema:"mcel.browser-observation.v1",appId:"contract-workbench"}}}};
      }}}});
    }}catch(error){{failures.push(error.violation.code);}}
  }}
  process.stdout.write(JSON.stringify({{failures,expected:cases.map((item)=>item.code)}}));
}})().catch((error)=>{{console.error(error);process.exit(1);}});
'''
    data = _run_node_json(tmp_path, script)
    assert data["failures"] == data["expected"]


def test_capability_runtime_streams_provisional_state_and_commits_once(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
(async () => {
  const domainModule = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/domain.js");
  const intentsModule = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/intents.js");
  const adapterModule = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/adapter.js");
  const definition = McelApplicationRuntime.defineApplication({
    appId: "contract-workbench",
    domain: domainModule.ContractWorkbenchDomain,
    intents: intentsModule.ContractWorkbenchIntents,
    adapter: adapterModule.ContractWorkbenchAdapter
  }, {replace: true});

  function addContract(app, operationId) {
    return app.dispatch({
      operationId,
      expectedRevision: app.revision,
      intentId: "add-contract",
      payload: {name: "Steel", quantity: 10, category: "materials"}
    });
  }

  const missing = McelApplicationRuntime.createApplicationInstance(definition, {id: "capability-missing"});
  addContract(missing, "missing-add");
  const missingResult = await missing.dispatch({
    operationId: "missing-quote",
    expectedRevision: 1,
    intentId: "request-quote",
    payload: {contractId: "contract-1"}
  });

  let release;
  let started;
  const gate = new Promise((resolve) => { release = resolve; });
  const startedGate = new Promise((resolve) => { started = resolve; });
  const progress = [];
  const app = McelApplicationRuntime.createApplicationInstance(definition, {
    id: "capability-stream",
    capabilities: {
      quotes: {
        async *requestQuote() {
          yield {type: "quote.started", expected: 2};
          started();
          await gate;
          yield {type: "quote.received", report: {amount: 100, source: "alpha"}};
          yield {type: "quote.received", report: {amount: 140, source: "beta"}};
        }
      }
    }
  });
  addContract(app, "stream-add");
  const pending = app.dispatch({
    operationId: "stream-quote",
    expectedRevision: 1,
    intentId: "request-quote",
    payload: {contractId: "contract-1"},
    onProgress(result) {
      progress.push({result, provisional: app.readProvisionalState(), revision: app.revision});
    }
  });
  await startedGate;
  const replacement = app.dispatch({
    operationId: "stream-quote-2",
    expectedRevision: 1,
    intentId: "request-quote",
    payload: {contractId: "contract-1"}
  });
  release();
  const superseded = await pending;
  const committed = await replacement;
  const evidence = app.exportEvidence();

  process.stdout.write(JSON.stringify({
    missingResult,
    progress,
    superseded,
    committed,
    finalState: app.state,
    provisional: app.readProvisionalState(),
    provisionalRevision: app.provisionalRevision,
    evidence
  }));
})().catch((error) => { console.error(error); process.exit(1); });
'''
    data = _run_node_json(tmp_path, script)
    assert data["missingResult"]["code"] == "APPLICATION_CAPABILITY_PROVIDER_MISSING"
    assert data["progress"][0]["result"]["status"] == "running"
    assert data["progress"][0]["provisional"]["quoteProgress"]["contract-1"] == {
        "status": "running", "received": 0, "expected": 2, "reports": [], "failures": []
    }
    assert all(entry["revision"] == 1 for entry in data["progress"])
    assert data["superseded"]["code"] == "APPLICATION_ASYNC_OPERATION_SUPERSEDED"
    assert data["superseded"]["status"] == "superseded"
    assert data["committed"]["code"] == "APPLICATION_CAPABILITY_OPERATION_COMMITTED"
    assert data["committed"]["revision"] == 2
    contract = data["finalState"]["contracts"][0]
    assert contract["quoteStatus"] == "quoted"
    assert contract["quoteAmount"] == 120
    assert data["provisional"] == {"quoteProgress": {}}
    assert data["provisionalRevision"] == 6
    assert data["evidence"]["stateAuthorities"]["provisional"] == {"quoteProgress": {}}
    assert data["evidence"]["activeOperations"] == []


def test_latest_per_item_key_cancellation_parallelism_and_late_event_suppression(tmp_path: Path) -> None:
    script = _runtime_bootstrap() + r'''
(async () => {
  const domainModule = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/domain.js");
  const intentsModule = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/intents.js");
  const adapterModule = await importContract("main_computer/web/applications/mcel-packages/contract-workbench/contracts/adapter.js");
  const definition = McelApplicationRuntime.defineApplication({
    appId: "contract-workbench",
    domain: domainModule.ContractWorkbenchDomain,
    intents: intentsModule.ContractWorkbenchIntents,
    adapter: adapterModule.ContractWorkbenchAdapter
  }, {replace: true});

  function add(app, operationId, name, quantity, category) {
    return app.dispatch({operationId, expectedRevision: app.revision, intentId: "add-contract", payload: {name, quantity, category}});
  }

  let cancelRelease;
  let cancelStarted;
  let cancellationSignal = null;
  const cancelGate = new Promise((resolve) => { cancelRelease = resolve; });
  const cancelStartedGate = new Promise((resolve) => { cancelStarted = resolve; });
  const cancelledApp = McelApplicationRuntime.createApplicationInstance(definition, {
    id: "cancel-app",
    capabilities: {quotes: {async *requestQuote(_request, context) {
      cancellationSignal = context.signal;
      yield {type: "quote.started", expected: 1};
      cancelStarted();
      await cancelGate;
      yield {type: "quote.received", report: {amount: 999, source: "late"}};
    }}}
  });
  add(cancelledApp, "cancel-add", "Steel", 10, "materials");
  const pendingCancelled = cancelledApp.dispatch({
    operationId: "cancel-target", expectedRevision: 1, intentId: "request-quote", payload: {contractId: "contract-1"}
  });
  await cancelStartedGate;
  const cancellation = cancelledApp.dispatch({
    operationId: "cancel-command", expectedRevision: 1, intentId: "cancel-quote", payload: {contractId: "contract-1"}
  });
  const afterCancel = {revision: cancelledApp.revision, state: cancelledApp.state, provisional: cancelledApp.readProvisionalState(), signalAborted: cancellationSignal.aborted};
  cancelRelease();
  const cancelledTarget = await pendingCancelled;
  const noActive = cancelledApp.dispatch({
    operationId: "cancel-command-2", expectedRevision: 1, intentId: "cancel-quote", payload: {contractId: "contract-1"}
  });

  const releases = {};
  const startedKeys = [];
  let bothStarted;
  const bothStartedGate = new Promise((resolve) => { bothStarted = resolve; });
  const parallelApp = McelApplicationRuntime.createApplicationInstance(definition, {
    id: "parallel-app",
    capabilities: {quotes: {async *requestQuote(request) {
      yield {type: "quote.started", expected: 1};
      startedKeys.push(request.contractId);
      if (startedKeys.length === 2) bothStarted();
      await new Promise((resolve) => { releases[request.contractId] = resolve; });
      yield {type: "quote.received", report: {amount: request.contractId === "contract-1" ? 110 : 220, source: request.contractId}};
    }}}
  });
  add(parallelApp, "parallel-add-1", "Steel", 10, "materials");
  add(parallelApp, "parallel-add-2", "Transport", 5, "transport");
  const parallelOne = parallelApp.dispatch({operationId: "parallel-1", expectedRevision: 2, intentId: "request-quote", payload: {contractId: "contract-1"}});
  const parallelTwo = parallelApp.dispatch({operationId: "parallel-2", expectedRevision: 2, intentId: "request-quote", payload: {contractId: "contract-2"}});
  await bothStartedGate;
  const parallelEvidence = parallelApp.exportEvidence();
  releases["contract-1"]();
  const resultOne = await parallelOne;
  releases["contract-2"]();
  const resultTwo = await parallelTwo;

  let unmountRelease;
  let unmountStarted;
  const unmountGate = new Promise((resolve) => { unmountRelease = resolve; });
  const unmountStartedGate = new Promise((resolve) => { unmountStarted = resolve; });
  const unmountApp = McelApplicationRuntime.createApplicationInstance(definition, {
    id: "unmount-app",
    capabilities: {quotes: {async *requestQuote() {
      yield {type: "quote.started", expected: 1};
      unmountStarted();
      await unmountGate;
      yield {type: "quote.received", report: {amount: 500, source: "late-unmount"}};
    }}}
  });
  add(unmountApp, "unmount-add", "Services", 3, "services");
  const pendingUnmount = unmountApp.dispatch({operationId: "unmount-target", expectedRevision: 1, intentId: "request-quote", payload: {contractId: "contract-1"}});
  await unmountStartedGate;
  const abortResult = McelApplicationRuntime.abortApplicationOperations(unmountApp, "unmounted");
  unmountRelease();
  const unmountedTarget = await pendingUnmount;

  process.stdout.write(JSON.stringify({
    cancellation,
    afterCancel,
    cancelledTarget,
    noActive,
    cancelledEvidence: cancelledApp.exportEvidence(),
    parallelEvidence,
    resultOne,
    resultTwo,
    parallelState: parallelApp.state,
    parallelProvisional: parallelApp.readProvisionalState(),
    abortResult,
    unmountedTarget,
    unmountState: unmountApp.state,
    unmountProvisional: unmountApp.readProvisionalState(),
    unmountEvidence: unmountApp.exportEvidence()
  }));
})().catch((error) => { console.error(error); process.exit(1); });
'''
    data = _run_node_json(tmp_path, script)
    assert data["cancellation"]["status"] == "cancelled"
    assert data["cancellation"]["code"] == "APPLICATION_ASYNC_OPERATION_CANCELLED"
    assert data["afterCancel"]["revision"] == 1
    assert data["afterCancel"]["state"]["contracts"][0]["quoteStatus"] == "idle"
    assert data["afterCancel"]["provisional"] == {"quoteProgress": {}}
    assert data["afterCancel"]["signalAborted"] is True
    assert data["cancelledTarget"]["status"] == "cancelled"
    assert data["cancelledTarget"]["code"] == "APPLICATION_ASYNC_OPERATION_CANCELLED"
    assert data["cancelledTarget"]["revision"] == 1
    assert data["noActive"]["code"] == "APPLICATION_ASYNC_OPERATION_NOT_ACTIVE"
    assert data["cancelledEvidence"]["activeOperations"] == []

    assert len(data["parallelEvidence"]["activeOperations"]) == 2
    assert {entry["itemKey"] for entry in data["parallelEvidence"]["activeOperations"]} == {"contract-1", "contract-2"}
    assert data["resultOne"]["status"] == "committed"
    assert data["resultTwo"]["status"] == "committed"
    assert data["resultOne"]["revision"] == 3
    assert data["resultTwo"]["revision"] == 4
    amounts = {entry["id"]: entry["quoteAmount"] for entry in data["parallelState"]["contracts"]}
    assert amounts == {"contract-1": 110, "contract-2": 220}
    assert data["parallelProvisional"] == {"quoteProgress": {}}

    assert data["abortResult"]["operationIds"] == ["unmount-target"]
    assert data["unmountedTarget"]["status"] == "cancelled"
    assert data["unmountedTarget"]["revision"] == 1
    assert data["unmountState"]["contracts"][0]["quoteStatus"] == "idle"
    assert data["unmountProvisional"] == {"quoteProgress": {}}
    assert data["unmountEvidence"]["activeOperations"] == []
