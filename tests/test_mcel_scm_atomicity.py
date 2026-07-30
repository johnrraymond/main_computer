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
        pytest.skip("node is unavailable; SCM atomicity functional test cannot run")

    script_path = tmp_path / "mcel-scm-atomicity-smoke.js"
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
    return f'''
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
'''


def _atomic_component_manifest(
    *,
    repair_apply: str = 'ctx.set("runtime.status", "repaired");',
    repair_post: str = "return true;",
    transition_apply: str = 'ctx.set("state.count", ctx.get("state.count") + 1); ctx.set("runtime.status", "transitioned");',
    transition_post: str = "return true;",
) -> str:
    return f'''
{{
  version: "1.0.0",
  contract: "atomicity.test.v1",
  owns: {{
    source: ["document"],
    state: ["count"],
    runtime: ["status", "details"],
  }},
  source: {{
    document: {{title: "Original", nested: {{stable: true}}}},
  }},
  state: {{
    count: 0,
  }},
  runtime: {{
    status: "idle",
    details: {{stable: true}},
  }},
  transitions: {{
    mutate: {{
      reads: ["state.count"],
      writes: ["state.count", "runtime.status"],
      apply(ctx, payload) {{
        {transition_apply}
      }},
      post(ctx, payload, result) {{
        {transition_post}
      }},
    }},
  }},
  repairContract: {{
    allowed: ["runtime.status", "runtime.details"],
    forbidden: ["source.document", "state.count"],
    strategies: {{
      repair: {{
        reads: ["runtime.status", "runtime.details"],
        writes: ["runtime.status"],
        apply(ctx, payload) {{
          {repair_apply}
        }},
        post(ctx, payload, result) {{
          {repair_post}
        }},
      }},
    }},
  }},
}}
'''


def test_repair_undeclared_second_write_leaves_all_roots_unchanged(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest(
        repair_apply='''
ctx.set("runtime.status", "partially-repaired");
ctx.evidence({step: "first-write-complete"});
ctx.set("runtime.details.unlisted", true);
''',
    )
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicRepair", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicRepair");
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
const evidenceBefore = McelLabScm.exportEvidence(instance).evidence.length;
let violation = null;
try {{
  scmRepair(instance, "repair");
}} catch (error) {{
  violation = error.violation;
}}
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  violation,
  unchanged: before === JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
  evidenceBefore,
  evidenceAfter: packet.evidence.length,
  evidencePhases: packet.evidence.map((entry) => entry.phase),
  evidenceCodes: packet.evidence.map((entry) => entry.code || ""),
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_REPAIR_UNDECLARED_WRITE"
    assert data["unchanged"] is True
    assert data["evidenceAfter"] == data["evidenceBefore"] + 1
    assert data["evidencePhases"][-1] == "repair"
    assert data["evidenceCodes"][-1] == "SCM_REPAIR_UNDECLARED_WRITE"
    assert "repair-start" not in data["evidencePhases"]
    assert "first-write-complete" not in data["evidencePhases"]


def test_repair_failed_postcondition_discards_runtime_and_operation_evidence(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest(
        repair_apply='''
ctx.set("runtime.status", "postcondition-will-fail");
ctx.evidence({phase: "repair-intermediate", ok: true});
return "tentative";
''',
        repair_post="return false;",
    )
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicRepairPost", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicRepairPost");
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
const evidenceBefore = McelLabScm.exportEvidence(instance).evidence.length;
let violation = null;
try {{
  scmRepair(instance, "repair");
}} catch (error) {{
  violation = error.violation;
}}
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  violation,
  unchanged: before === JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
  evidenceBefore,
  evidenceAfter: packet.evidence.length,
  phases: packet.evidence.map((entry) => entry.phase),
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_REPAIR_POSTCONDITION_FAILED"
    assert data["unchanged"] is True
    assert data["evidenceAfter"] == data["evidenceBefore"] + 1
    assert "repair-start" not in data["phases"]
    assert "repair-intermediate" not in data["phases"]


def test_repair_exception_discards_runtime_and_records_one_refusal(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest(
        repair_apply='''
ctx.set("runtime.status", "exception-will-follow");
ctx.delete("runtime.status");
throw new Error("repair exploded");
''',
    )
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicRepairException", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicRepairException");
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
const evidenceBefore = McelLabScm.exportEvidence(instance).evidence.length;
let violation = null;
try {{
  scmRepair(instance, "repair");
}} catch (error) {{
  violation = error.violation;
}}
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  violation,
  unchanged: before === JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
  evidenceBefore,
  evidenceAfter: packet.evidence.length,
  lastCode: packet.evidence.at(-1)?.code || "",
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_REPAIR_EXCEPTION"
    assert data["violation"]["message"] == "repair exploded"
    assert data["unchanged"] is True
    assert data["evidenceAfter"] == data["evidenceBefore"] + 1
    assert data["lastCode"] == "SCM_REPAIR_EXCEPTION"


def test_transition_failed_postcondition_leaves_all_roots_unchanged(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest(
        transition_apply='''
ctx.set("state.count", ctx.get("state.count") + 1);
ctx.set("runtime.status", "postcondition-will-fail");
ctx.evidence({phase: "transition-intermediate", ok: true});
''',
        transition_post="return false;",
    )
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicTransitionPost", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicTransitionPost");
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
const evidenceBefore = McelLabScm.exportEvidence(instance).evidence.length;
let violation = null;
try {{
  scmTransition(instance, "mutate");
}} catch (error) {{
  violation = error.violation;
}}
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  violation,
  unchanged: before === JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
  evidenceBefore,
  evidenceAfter: packet.evidence.length,
  phases: packet.evidence.map((entry) => entry.phase),
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_TRANSITION_POSTCONDITION_FAILED"
    assert data["unchanged"] is True
    assert data["evidenceAfter"] == data["evidenceBefore"] + 1
    assert "transition-intermediate" not in data["phases"]


def test_transition_exception_leaves_all_roots_unchanged(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest(
        transition_apply='''
ctx.set("state.count", ctx.get("state.count") + 1);
ctx.set("runtime.status", "exception-will-follow");
throw new TypeError("transition exploded");
''',
    )
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicTransitionException", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicTransitionException");
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
const evidenceBefore = McelLabScm.exportEvidence(instance).evidence.length;
let violation = null;
try {{
  scmTransition(instance, "mutate");
}} catch (error) {{
  violation = error.violation;
}}
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  violation,
  unchanged: before === JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
  evidenceBefore,
  evidenceAfter: packet.evidence.length,
  lastCode: packet.evidence.at(-1)?.code || "",
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["violation"]["code"] == "SCM_TRANSITION_EXCEPTION"
    assert data["violation"]["message"] == "transition exploded"
    assert data["unchanged"] is True
    assert data["evidenceAfter"] == data["evidenceBefore"] + 1
    assert data["lastCode"] == "SCM_TRANSITION_EXCEPTION"


def test_successful_transition_and_repair_commit_draft_roots_and_evidence(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest(
        repair_apply='''
ctx.set("runtime.status", "repaired");
ctx.evidence({phase: "repair-detail", ok: true});
return "repair-result";
''',
        transition_apply='''
ctx.set("state.count", ctx.get("state.count") + 1);
ctx.set("runtime.status", "transitioned");
ctx.evidence({phase: "transition-detail", ok: true});
return "transition-result";
''',
    )
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicSuccess", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicSuccess");
const transitionResult = scmTransition(instance, "mutate");
const repairResult = scmRepair(instance, "repair");
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  transitionResult,
  repairResult,
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
  phases: packet.evidence.map((entry) => entry.phase),
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["source"] == {"document": {"title": "Original", "nested": {"stable": True}}}
    assert data["state"]["count"] == 1
    assert data["runtime"]["status"] == "repaired"
    assert data["transitionResult"]["ok"] is True
    assert data["repairResult"]["ok"] is True
    assert "transition-detail" in data["phases"]
    assert "transition" in data["phases"]
    assert "repair-start" in data["phases"]
    assert "repair-detail" in data["phases"]
    assert "repair-commit" in data["phases"]


def test_transition_commit_survives_refused_public_root_redefinition(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest()
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicTransitionCommitFailure", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicTransitionCommitFailure");
const sourceRef = instance.source;
const stateRef = instance.state;
const runtimeRef = instance.runtime;
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
const evidenceBefore = McelLabScm.exportEvidence(instance).evidence.length;
let tamperError = null;
try {{
  Object.defineProperty(instance, "state", {{
    configurable: true,
    enumerable: true,
    writable: false,
    value: instance.state,
  }});
}} catch (error) {{
  tamperError = {{name: error.name, message: error.message}};
}}
const result = scmTransition(instance, "mutate");
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  tamperError,
  result,
  changed: before !== JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
  count: instance.state.count,
  runtimeStatus: instance.runtime.status,
  sameRefs: {{
    source: instance.source === sourceRef,
    state: instance.state === stateRef,
    runtime: instance.runtime === runtimeRef,
  }},
  evidenceBefore,
  evidenceAfter: packet.evidence.length,
  lastCode: packet.evidence.at(-1)?.code || "",
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["tamperError"]["name"] == "TypeError"
    assert data["result"]["ok"] is True
    assert data["changed"] is True
    assert data["count"] == 1
    assert data["runtimeStatus"] == "transitioned"
    assert data["sameRefs"] == {"source": True, "state": False, "runtime": False}
    assert data["evidenceAfter"] == data["evidenceBefore"] + 1
    assert data["lastCode"] == ""


def test_repair_commit_survives_refused_public_root_redefinition(tmp_path: Path) -> None:
    manifest = _atomic_component_manifest()
    script = f'''
{_scm_bootstrap()}
McelLabScm.defineComponent("AtomicRepairCommitFailure", {manifest});
const instance = McelLabScm.createComponentInstance("AtomicRepairCommitFailure");
const runtimeRef = instance.runtime;
const before = JSON.stringify({{
  source: instance.source,
  state: instance.state,
  runtime: instance.runtime,
}});
const evidenceBefore = McelLabScm.exportEvidence(instance).evidence.length;
let tamperError = null;
try {{
  Object.defineProperty(instance, "runtime", {{
    configurable: true,
    enumerable: true,
    writable: false,
    value: instance.runtime,
  }});
}} catch (error) {{
  tamperError = {{name: error.name, message: error.message}};
}}
const result = scmRepair(instance, "repair");
const packet = McelLabScm.exportEvidence(instance);
process.stdout.write(JSON.stringify({{
  tamperError,
  result,
  changed: before !== JSON.stringify({{
    source: instance.source,
    state: instance.state,
    runtime: instance.runtime,
  }}),
  runtimeStatus: instance.runtime.status,
  sameRuntimeRef: instance.runtime === runtimeRef,
  evidenceBefore,
  evidenceAfter: packet.evidence.length,
  lastCode: packet.evidence.at(-1)?.code || "",
}}));
'''
    data = _run_node_json(tmp_path, script)

    assert data["tamperError"]["name"] == "TypeError"
    assert data["result"]["ok"] is True
    assert data["changed"] is True
    assert data["runtimeStatus"] == "repaired"
    assert data["sameRuntimeRef"] is False
    assert data["evidenceAfter"] == data["evidenceBefore"] + 2
    assert data["lastCode"] == ""
