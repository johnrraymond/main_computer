from __future__ import annotations

import json
import re
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
        pytest.skip("node is unavailable; SCM mutation-authority test cannot run")

    script_path = tmp_path / "mcel-scm-mutation-authority-smoke.js"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _bootstrap() -> str:
    return f'''
"use strict";
const window = {{}};
{_script("mcel-scm.js")}
McelLabScm.clearDefinitions();
let nextTestOperationId = 1;
function scmOperation(instance, scope) {{
  return McelLabScm.createOperation(instance, `${{scope}}:${{nextTestOperationId++}}`);
}}
function scmTransition(instance, name, payload = {{}}) {{
  return McelLabScm.transition(instance, name, payload, scmOperation(instance, `transition:${{name}}`));
}}
function scmRepair(instance, name, payload = {{}}) {{
  return McelLabScm.repairComponent(instance, name, payload, scmOperation(instance, `repair:${{name}}`));
}}
function scmEffect(instance, name, payload = {{}}) {{
  return McelLabScm.runEffect(instance, name, payload, scmOperation(instance, `effect:${{name}}`));
}}
function scmSerialize(instance, options = {{}}) {{
  return McelLabScm.serializeComponent(instance, options, scmOperation(instance, "serialize"));
}}
'''


def _manifest() -> str:
    return r'''
{
  version: "1.0.0",
  contract: "mutation-authority.test.v1",
  owns: {
    source: ["document"],
    state: ["count"],
    runtime: ["status", "history"],
    effects: ["authorized.update"],
  },
  source: {
    document: {title: "Original", nested: {stable: true}},
  },
  state: {
    count: 0,
  },
  runtime: {
    status: "idle",
    history: ["created"],
  },
  transitions: {
    increment: {
      reads: ["state.count"],
      writes: ["state.count"],
      apply(ctx) {
        globalThis.leakedTransitionContext = ctx;
        ctx.set("state.count", ctx.get("state.count") + 1);
      },
    },
  },
  effects: {
    "authorized.update": {
      kind: "internal-runtime-effect",
      triggers: ["runtime.status"],
      reads: ["runtime.status", "runtime.history"],
      writes: ["runtime.status", "runtime.history"],
      external: {
        resource: "scm-test-runtime",
        operation: "authorized-update",
      },
      errorPolicy: {
        onFailure: "preserve-current-runtime",
      },
      run(ctx, payload = {}) {
        const history = ctx.get("runtime.history") || [];
        ctx.set("runtime.status", payload.status || "updated");
        ctx.set("runtime.history", [...history, payload.status || "updated"]);
        return {status: payload.status || "updated"};
      },
    },
  },
  repairContract: {
    allowed: ["runtime.status"],
    forbidden: ["source.document", "state.count"],
    strategies: {
      repair: {
        reads: ["runtime.status"],
        writes: ["runtime.status"],
        apply(ctx) {
          globalThis.leakedRepairContext = ctx;
          ctx.set("runtime.status", "repaired");
        },
        post(ctx) {
          return ctx.get("runtime.status") === "repaired";
        },
      },
    },
  },
}
'''


def test_component_roots_and_evidence_refuse_direct_mutation(tmp_path: Path) -> None:
    script = f'''
{_bootstrap()}
McelLabScm.defineComponent("MutationAuthority", {_manifest()});
const instance = McelLabScm.createComponentInstance("MutationAuthority");
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
  evidence: McelLabScm.exportEvidence(instance).evidence,
}});
const attempts = [];
function attempt(name, operation) {{
  try {{
    operation();
    attempts.push({{name, refused: false}});
  }} catch (error) {{
    attempts.push({{name, refused: true, errorName: error.name}});
  }}
}}
attempt("nested-source-write", () => {{
  instance.source.document.title = "Tampered";
}});
attempt("nested-state-write", () => {{
  instance.state.count = 99;
}});
attempt("runtime-array-push", () => {{
  instance.runtime.history.push("tampered");
}});
attempt("nested-runtime-delete", () => {{
  delete instance.runtime.status;
}});
attempt("evidence-injection", () => {{
  instance.evidence.push({{phase: "forged", ok: true}});
}});
attempt("root-replacement", () => {{
  instance.runtime = {{status: "replaced"}};
}});
attempt("root-redefinition", () => {{
  Object.defineProperty(instance, "state", {{value: {{count: 500}}}});
}});
const after = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
  evidence: McelLabScm.exportEvidence(instance).evidence,
}});
process.stdout.write(JSON.stringify({{
  attempts,
  unchanged: before === after,
  frozen: {{
    handle: Object.isFrozen(instance),
    source: Object.isFrozen(instance.source),
    sourceNested: Object.isFrozen(instance.source.document),
    state: Object.isFrozen(instance.state),
    runtime: Object.isFrozen(instance.runtime),
    runtimeArray: Object.isFrozen(instance.runtime.history),
    evidence: Object.isFrozen(instance.evidence),
  }},
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert all(attempt["refused"] for attempt in data["attempts"])
    assert data["unchanged"] is True
    assert data["frozen"] == {
        "handle": True,
        "source": True,
        "sourceNested": True,
        "state": True,
        "runtime": True,
        "runtimeArray": True,
        "evidence": True,
    }


def test_authorized_operations_replace_snapshots_and_preserve_stale_views(
    tmp_path: Path,
) -> None:
    script = f'''
{_bootstrap()}
McelLabScm.defineComponent("AuthorizedOperations", {_manifest()});
const instance = McelLabScm.createComponentInstance("AuthorizedOperations");
const initialSource = instance.source;
const initialState = instance.state;
const initialRuntime = instance.runtime;
const effectResult = scmEffect(instance, "authorized.update", {{status: "effect-updated"}});
const afterEffectRuntime = instance.runtime;
const transitionResult = scmTransition(instance, "increment");
const afterTransitionState = instance.state;
const repairResult = scmRepair(instance, "repair");
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  effectResult,
  transitionResult,
  repairResult,
  current: {{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }},
  stale: {{
    source: initialSource,
    state: initialState,
    runtime: initialRuntime,
  }},
  identities: {{
    sourceStable: initialSource === instance.source,
    runtimeReplacedByEffect: initialRuntime !== afterEffectRuntime,
    stateReplacedByTransition: initialState !== afterTransitionState,
    runtimeReplacedByRepair: afterEffectRuntime !== instance.runtime,
  }},
  phases: packet.evidence.map((entry) => entry.phase),
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["effectResult"]["ok"] is True
    assert data["transitionResult"]["ok"] is True
    assert data["repairResult"]["ok"] is True
    assert data["current"]["source"]["document"]["title"] == "Original"
    assert data["current"]["state"]["count"] == 1
    assert data["current"]["runtime"]["status"] == "repaired"
    assert data["current"]["runtime"]["history"] == ["created", "effect-updated"]
    assert data["stale"]["state"]["count"] == 0
    assert data["stale"]["runtime"] == {"status": "idle", "history": ["created"]}
    assert data["identities"] == {
        "sourceStable": True,
        "runtimeReplacedByEffect": True,
        "stateReplacedByTransition": True,
        "runtimeReplacedByRepair": True,
    }
    assert "effect-commit" in data["phases"]
    assert "transition" in data["phases"]
    assert "repair-commit" in data["phases"]


def test_leaked_operation_contexts_cannot_mutate_committed_roots(
    tmp_path: Path,
) -> None:
    script = f'''
{_bootstrap()}
McelLabScm.defineComponent("StaleOperationContext", {_manifest()});
const instance = McelLabScm.createComponentInstance("StaleOperationContext");
scmTransition(instance, "increment");
globalThis.leakedTransitionContext.set("state.count", 40);
scmRepair(instance, "repair");
globalThis.leakedRepairContext.set("runtime.status", "stale-context-tamper");
scmTransition(instance, "increment");
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  state: instance.state,
  runtime: instance.runtime,
  transitionCommits: packet.evidence.filter(
    (entry) => entry.phase === "transition" && entry.ok === true
  ).length,
  repairCommits: packet.evidence.filter(
    (entry) => entry.phase === "repair-commit" && entry.ok === true
  ).length,
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["state"]["count"] == 2
    assert data["runtime"]["status"] == "repaired"
    assert data["transitionCommits"] == 2
    assert data["repairCommits"] == 1


def test_forged_component_handle_is_rejected(tmp_path: Path) -> None:
    script = f'''
{_bootstrap()}
McelLabScm.defineComponent("ForgedHandle", {_manifest()});
const instance = McelLabScm.createComponentInstance("ForgedHandle");
const forged = {{
  kind: instance.kind,
  contractVersion: instance.contractVersion,
  id: instance.id,
  componentName: instance.componentName,
  definition: instance.definition,
  source: JSON.parse(JSON.stringify(instance.source)),
  state: JSON.parse(JSON.stringify(instance.state)),
  runtime: JSON.parse(JSON.stringify(instance.runtime)),
  evidence: [],
}};
let violation = null;
try {{
  McelLabScm.transition(
    forged,
    "increment",
    {{}},
    {{expectedRevision: 0, operationId: "forged-handle-operation"}}
  );
}} catch (error) {{
  violation = error.violation;
}}
process.stdout.write(JSON.stringify({{
  violation,
  forgedState: forged.state,
  canonicalState: instance.state,
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_INVALID_INSTANCE"
    assert data["forgedState"]["count"] == 0
    assert data["canonicalState"]["count"] == 0


@pytest.mark.parametrize(
    "script_name, aliases",
    [
        ("mcel-lab.js", ("instance",)),
        ("code-editor-mcel-studio.js", ("scmInstance",)),
    ],
)
def test_production_consumers_have_no_direct_component_root_assignment(
    script_name: str,
    aliases: tuple[str, ...],
) -> None:
    source = _script(script_name)
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    nested_assignments = re.findall(
        rf"\b(?:{alias_pattern})\.(?:source|state|runtime|evidence)"
        r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*|\[[^\]]+\])*\s*=(?!=)",
        source,
    )
    destructive_calls = re.findall(
        rf"\b(?:{alias_pattern})\.(?:source|state|runtime|evidence)"
        r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*|\[[^\]]+\])*"
        r"\.(?:push|pop|shift|unshift|splice|sort|reverse)\s*\(",
        source,
    )

    assert nested_assignments == []
    assert destructive_calls == []
    if script_name == "mcel-lab.js":
        assert '"scm.projectWalletBoundary"' in source
        assert 'runMcelScmEffect(window.McelLabScm, instance, "scm.projectWalletBoundary"' in source
        assert "scm.createOperation(instance" in source
    if script_name == "code-editor-mcel-studio.js":
        assert 'bridge.mcel.transition(scmInstance, "syncLiveSurface"' in source


def test_child_effect_and_serialization_paths_remain_authorized_after_root_lock(
    tmp_path: Path,
) -> None:
    script = f'''
{_bootstrap()}
McelLabScm.defineComponent("MutationAuthorityContexts", {{
  version: "1.0.0",
  contract: "mutation-authority.contexts.v1",
  owns: {{
    source: ["document"],
    state: ["count"],
    runtime: ["status", "serialized"],
    effects: ["mark"],
  }},
  source: {{
    document: {{title: "Original"}},
  }},
  state: {{
    count: 0,
  }},
  runtime: {{
    status: "idle",
    serialized: "",
  }},
  children: {{
    counter: {{
      component: "CounterChild",
      slot: "body",
      inputs: {{count: "state.count"}},
      outputs: {{}},
      mayMutate: ["state.count"],
      maySerialize: false,
    }},
  }},
  effects: {{
    mark: {{
      kind: "local",
      triggers: [],
      reads: ["runtime.status"],
      writes: ["runtime.status"],
      external: {{resource: "local-test", operation: "mark"}},
      cancellation: "none",
      racePolicy: "single-flight",
      errorPolicy: {{onFailure: "raise"}},
      run(ctx) {{
        ctx.set("runtime.status", "effect-marked");
        return {{status: "effect-marked"}};
      }},
    }},
  }},
  serializationContract: {{
    sourceOwns: ["source.document"],
    runtimeOnly: ["runtime.status", "runtime.serialized"],
    failIfRuntimeLeaks: true,
    output: {{
      format: "clean-source-json",
      writeTo: "runtime.serialized",
    }},
  }},
}});
const instance = McelLabScm.createComponentInstance("MutationAuthorityContexts");
const initialState = instance.state;
const initialRuntime = instance.runtime;
const child = McelLabScm.createChildContext(instance, "counter");
child.set("state.count", 7, scmOperation(instance, "child:set"));
const afterChildState = instance.state;
const effectResult = scmEffect(instance, "mark");
const afterEffectRuntime = instance.runtime;
const serialized = scmSerialize(instance);
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  effectResult,
  serialized,
  current: {{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }},
  stale: {{
    state: initialState,
    runtime: initialRuntime,
  }},
  identities: {{
    childReplacedState: initialState !== afterChildState,
    effectReplacedRuntime: initialRuntime !== afterEffectRuntime,
    serializationReplacedRuntime: afterEffectRuntime !== instance.runtime,
  }},
  phases: packet.evidence.map((entry) => entry.phase),
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["effectResult"]["ok"] is True
    assert data["current"]["state"]["count"] == 7
    assert data["current"]["runtime"]["status"] == "effect-marked"
    assert data["serialized"]["source"]["document"]["title"] == "Original"
    assert '"title": "Original"' in data["current"]["runtime"]["serialized"]
    assert data["stale"]["state"]["count"] == 0
    assert data["stale"]["runtime"]["status"] == "idle"
    assert data["identities"] == {
        "childReplacedState": True,
        "effectReplacedRuntime": True,
        "serializationReplacedRuntime": True,
    }
    assert "child-mutation" in data["phases"]
    assert "effect-commit" in data["phases"]
    assert "serialize-commit" in data["phases"]
