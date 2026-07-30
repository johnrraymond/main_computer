from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCM = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-scm.js"


def _run_node_json(tmp_path: Path, body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; SCM operation-guard test cannot run")
    script = tmp_path / "mcel-scm-operation-guard-smoke.js"
    script.write_text(
        '"use strict";\n'
        "const window = {};\n"
        f"{SCM.read_text(encoding='utf-8')}\n"
        "McelLabScm.clearDefinitions();\n"
        f"{body}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


MANIFEST = r"""
{
  version: "1.0.0",
  contract: "operation-guard.test.v1",
  owns: {
    source: ["document"],
    state: ["count"],
    runtime: ["status", "serialized"],
    effects: ["mark"],
  },
  source: {document: {title: "Original"}},
  state: {count: 0},
  runtime: {status: "idle", serialized: ""},
  children: {
    counter: {
      component: "CounterChild",
      slot: "body",
      inputs: {count: "state.count"},
      outputs: {},
      mayMutate: ["state.count"],
      maySerialize: false,
    },
  },
  transitions: {
    increment: {
      reads: ["state.count"],
      writes: ["state.count"],
      apply(ctx) {
        ctx.set("state.count", ctx.get("state.count") + 1);
      },
    },
  },
  effects: {
    mark: {
      kind: "local",
      triggers: [],
      reads: ["runtime.status"],
      writes: ["runtime.status"],
      external: {resource: "local-test", operation: "mark"},
      cancellation: "none",
      racePolicy: "single-flight",
      errorPolicy: {onFailure: "raise"},
      run(ctx, payload = {}) {
        ctx.set("runtime.status", payload.status || "marked");
        return {status: payload.status || "marked"};
      },
    },
  },
  serializationContract: {
    sourceOwns: ["source.document"],
    runtimeOnly: ["runtime.status", "runtime.serialized"],
    failIfRuntimeLeaks: true,
    output: {format: "clean-source-json", writeTo: "runtime.serialized"},
  },
  repairContract: {
    allowed: ["runtime.status"],
    forbidden: ["source.document", "state.count"],
    strategies: {
      repair: {
        reads: ["runtime.status"],
        writes: ["runtime.status"],
        apply(ctx) {
          ctx.set("runtime.status", "repaired");
        },
        post(ctx) {
          return ctx.get("runtime.status") === "repaired";
        },
      },
    },
  },
}
"""


def test_duplicate_stale_and_missing_envelopes_refuse_with_zero_change(tmp_path: Path) -> None:
    data = _run_node_json(
        tmp_path,
        f"""
McelLabScm.defineComponent("Guarded", {MANIFEST});
const instance = McelLabScm.createComponentInstance("Guarded");
const firstOperation = {{expectedRevision: 0, operationId: "operation-1"}};
const first = McelLabScm.transition(instance, "increment", {{}}, firstOperation);

function snapshot() {{
  return JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
    evidence: McelLabScm.exportEvidence(instance).evidence,
    revision: instance.revision,
    appliedOperationIds: instance.appliedOperationIds,
  }});
}}
function refuse(operation) {{
  const before = snapshot();
  let violation = null;
  try {{
    McelLabScm.transition(instance, "increment", {{}}, operation);
  }} catch (error) {{
    violation = error.violation;
  }}
  return {{violation, unchanged: before === snapshot()}};
}}

const duplicate = refuse(firstOperation);
const stale = refuse({{expectedRevision: 0, operationId: "operation-2"}});
const missing = refuse(null);
process.stdout.write(JSON.stringify({{
  first,
  duplicate,
  stale,
  missing,
  count: instance.state.count,
  revision: instance.revision,
  operationIds: instance.appliedOperationIds,
}}));
""",
    )

    assert data["first"]["operationId"] == "operation-1"
    assert data["first"]["previousRevision"] == 0
    assert data["first"]["revision"] == 1
    assert data["duplicate"]["violation"]["code"] == "SCM_DUPLICATE_OPERATION"
    assert data["stale"]["violation"]["code"] == "SCM_STALE_REVISION"
    assert data["missing"]["violation"]["code"] == "SCM_OPERATION_ENVELOPE_REQUIRED"
    assert data["duplicate"]["unchanged"] is True
    assert data["stale"]["unchanged"] is True
    assert data["missing"]["unchanged"] is True
    assert data["count"] == 1
    assert data["revision"] == 1
    assert data["operationIds"] == ["operation-1"]


def test_every_public_root_mutation_path_requires_operation_authority(tmp_path: Path) -> None:
    data = _run_node_json(
        tmp_path,
        f"""
McelLabScm.defineComponent("AllMutationPaths", {MANIFEST});
const instance = McelLabScm.createComponentInstance("AllMutationPaths");
const child = McelLabScm.createChildContext(instance, "counter");
function snapshot() {{
  return JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
    evidence: McelLabScm.exportEvidence(instance).evidence,
    revision: instance.revision,
    appliedOperationIds: instance.appliedOperationIds,
  }});
}}
function missingEnvelope(call) {{
  const before = snapshot();
  let violation = null;
  try {{ call(); }} catch (error) {{ violation = error.violation; }}
  return {{code: violation && violation.code, unchanged: before === snapshot()}};
}}

const missing = [
  missingEnvelope(() => McelLabScm.runEffect(instance, "mark")),
  missingEnvelope(() => McelLabScm.serializeComponent(instance)),
  missingEnvelope(() => McelLabScm.repairComponent(instance, "repair")),
  missingEnvelope(() => child.set("state.count", 3)),
];

const effect = McelLabScm.runEffect(
  instance,
  "mark",
  {{status: "effected"}},
  {{expectedRevision: 0, operationId: "effect-1"}}
);
const serialized = McelLabScm.serializeComponent(
  instance,
  {{}},
  {{expectedRevision: 1, operationId: "serialize-1"}}
);
const repair = McelLabScm.repairComponent(
  instance,
  "repair",
  {{}},
  {{expectedRevision: 2, operationId: "repair-1"}}
);
child.set(
  "state.count",
  7,
  {{expectedRevision: 3, operationId: "child-1"}}
);

const beforeDirectContexts = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
let effectContextCode = "";
let repairContextCode = "";
try {{
  McelLabScm.createEffectContext(instance, "mark").set("runtime.status", "bypass");
}} catch (error) {{
  effectContextCode = error.violation && error.violation.code;
}}
try {{
  McelLabScm.createRepairContext(instance, "repair").set("runtime.status", "bypass");
}} catch (error) {{
  repairContextCode = error.violation && error.violation.code;
}}

process.stdout.write(JSON.stringify({{
  missing,
  revisions: [effect.revision, serialized.revision, repair.revision, instance.revision],
  state: instance.state,
  runtime: instance.runtime,
  appliedOperationIds: instance.appliedOperationIds,
  effectContextCode,
  repairContextCode,
  directContextRootsUnchanged: beforeDirectContexts === JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
}}));
""",
    )

    assert data["missing"] == [
        {"code": "SCM_OPERATION_ENVELOPE_REQUIRED", "unchanged": True},
        {"code": "SCM_OPERATION_ENVELOPE_REQUIRED", "unchanged": True},
        {"code": "SCM_OPERATION_ENVELOPE_REQUIRED", "unchanged": True},
        {"code": "SCM_OPERATION_ENVELOPE_REQUIRED", "unchanged": True},
    ]
    assert data["revisions"] == [1, 2, 3, 4]
    assert data["state"]["count"] == 7
    assert data["runtime"]["status"] == "repaired"
    assert '"title": "Original"' in data["runtime"]["serialized"]
    assert data["appliedOperationIds"] == [
        "effect-1",
        "serialize-1",
        "repair-1",
        "child-1",
    ]
    assert data["effectContextCode"] == "SCM_DIRECT_CONTEXT_MUTATION_BLOCKED"
    assert data["repairContextCode"] == "SCM_DIRECT_CONTEXT_MUTATION_BLOCKED"
    assert data["directContextRootsUnchanged"] is True


def test_async_effect_holds_exclusive_revision_authority_until_commit(tmp_path: Path) -> None:
    data = _run_node_json(
        tmp_path,
        """
McelLabScm.defineComponent("AsyncGuarded", {
  version: "1.0.0",
  contract: "operation-guard.async.v1",
  owns: {
    state: ["count"],
    runtime: ["status"],
    effects: ["wait"],
  },
  state: {count: 0},
  runtime: {status: "idle"},
  transitions: {
    increment: {
      reads: ["state.count"],
      writes: ["state.count"],
      apply(ctx) {
        ctx.set("state.count", ctx.get("state.count") + 1);
      },
    },
  },
  effects: {
    wait: {
      kind: "async-data",
      triggers: [],
      reads: ["runtime.status"],
      writes: ["runtime.status"],
      external: {resource: "test", operation: "wait"},
      cancellation: "none",
      racePolicy: "single-flight",
      errorPolicy: {onFailure: "raise"},
      run() {
        return new Promise((resolve) => {
          globalThis.resolveEffect = resolve;
        });
      },
      commit(ctx, result) {
        ctx.set("runtime.status", result);
      },
    },
  },
});

(async () => {
  const instance = McelLabScm.createComponentInstance("AsyncGuarded");
  const pending = McelLabScm.runEffect(
    instance,
    "wait",
    {},
    {expectedRevision: 0, operationId: "async-1"}
  );
  const beforeConflict = JSON.stringify({
    state: instance.state,
    runtime: instance.runtime,
    evidence: McelLabScm.exportEvidence(instance).evidence,
    revision: instance.revision,
    appliedOperationIds: instance.appliedOperationIds,
  });
  let conflict = null;
  try {
    McelLabScm.transition(
      instance,
      "increment",
      {},
      {expectedRevision: 0, operationId: "transition-during-async"}
    );
  } catch (error) {
    conflict = error.violation;
  }
  const conflictUnchanged = beforeConflict === JSON.stringify({
    state: instance.state,
    runtime: instance.runtime,
    evidence: McelLabScm.exportEvidence(instance).evidence,
    revision: instance.revision,
    appliedOperationIds: instance.appliedOperationIds,
  });
  globalThis.resolveEffect("done");
  const effect = await pending;
  const transition = McelLabScm.transition(
    instance,
    "increment",
    {},
    {expectedRevision: 1, operationId: "transition-after-async"}
  );
  process.stdout.write(JSON.stringify({
    conflict,
    conflictUnchanged,
    effect,
    transition,
    state: instance.state,
    runtime: instance.runtime,
    revision: instance.revision,
    appliedOperationIds: instance.appliedOperationIds,
  }));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exitCode = 1;
});
""",
    )

    assert data["conflict"]["code"] == "SCM_OPERATION_IN_PROGRESS"
    assert data["conflictUnchanged"] is True
    assert data["effect"]["revision"] == 1
    assert data["transition"]["revision"] == 2
    assert data["state"]["count"] == 1
    assert data["runtime"]["status"] == "done"
    assert data["revision"] == 2
    assert data["appliedOperationIds"] == ["async-1", "transition-after-async"]


def test_applied_operation_ledger_is_bounded(tmp_path: Path) -> None:
    data = _run_node_json(
        tmp_path,
        f"""
McelLabScm.defineComponent("BoundedLedger", {MANIFEST});
const instance = McelLabScm.createComponentInstance("BoundedLedger");
for (let index = 0; index < 260; index += 1) {{
  McelLabScm.transition(
    instance,
    "increment",
    {{}},
    {{expectedRevision: instance.revision, operationId: `operation-${{index}}`}}
  );
}}
process.stdout.write(JSON.stringify({{
  count: instance.state.count,
  revision: instance.revision,
  ledgerLength: instance.appliedOperationIds.length,
  firstRetained: instance.appliedOperationIds[0],
  lastRetained: instance.appliedOperationIds.at(-1),
  frozen: Object.isFrozen(instance.appliedOperationIds),
}}));
""",
    )

    assert data == {
        "count": 260,
        "revision": 260,
        "ledgerLength": 256,
        "firstRetained": "operation-4",
        "lastRetained": "operation-259",
        "frozen": True,
    }
