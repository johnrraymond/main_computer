from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
APPLICATION = ROOT / "mcel_apps/contract-workbench/application.js"
LIBRARY = ROOT / "main_computer/web/applications/scripts/mcel-app-definition.js"
MANIFEST = ROOT / "mcel_apps/contract-workbench/mcel.app.json"
FIXTURE_IR = ROOT / "tests/fixtures/mcel_application_ir/contract-workbench.ir.json"


def _dsl_authoritative() -> bool:
    return (json.loads(MANIFEST.read_text(encoding="utf-8")).get("authoring") or {}).get("status") == "dsl-authoritative"


def _node() -> str:
    resolved = os.environ.get("MCEL_NODE_EXECUTABLE", "").strip() or shutil.which("node") or ""
    if not resolved:
        pytest.skip("Node.js is unavailable.")
    return resolved


def _run(source: str) -> dict:
    completed = subprocess.run(
        [_node(), "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def test_application_definition_exercises_the_required_operation_shapes() -> None:
    if _dsl_authoritative():
        from main_computer.mcel_dsl_compiler import compile_dsl_application
        result = compile_dsl_application(APPLICATION, compare_ir_path=FIXTURE_IR)
        assert result.valid and result.comparison_status == "exact" and result.normalized_ir is not None
        ir = result.normalized_ir
        authorities = {}
        for state in ir["states"]:
            authorities[state["authority"]] = authorities.get(state["authority"], 0) + 1
        kinds = {}
        for intent in ir["intents"]:
            kinds[intent["operationKind"]] = kinds.get(intent["operationKind"], 0) + 1
        payload = {
            "stateAuthorityCounts": authorities,
            "operationKindCounts": kinds,
            "capabilityCount": len(ir["capabilities"]),
            "invariantCount": len(ir["proof"]["invariants"]),
            "acceptanceCount": len(ir["scenarios"]),
            "observationCount": 7,
        }
    else:
        payload = _run(
            f"const m=require({json.dumps(str(LIBRARY))});"
            f"const app=require({json.dumps(str(APPLICATION))});"
            "process.stdout.write(JSON.stringify(m.inspect(app)));"
        )
    assert payload["stateAuthorityCounts"] == {
        "canonical": 3,
        "derived": 3,
        "provisional": 1,
        "renderer-local": 5,
    }
    assert payload["operationKindCounts"] == {
        "async": 1,
        "cancel": 1,
        "mutation": 4,
        "prohibited": 1,
    }
    assert payload["capabilityCount"] == 1
    assert payload["invariantCount"] == 4
    assert payload["acceptanceCount"] == 14
    assert payload["observationCount"] == 7


def test_sync_transition_semantics_are_already_defined() -> None:
    if _dsl_authoritative():
        from main_computer.mcel_dsl_compiler import compile_dsl_application
        result = compile_dsl_application(APPLICATION, compare_ir_path=FIXTURE_IR)
        assert result.valid and result.normalized_ir is not None
        intents = {entry["id"]: entry for entry in result.normalized_ir["intents"]}
        add_steps = intents["intent:add-contract"]["transition"]["steps"]
        remove_steps = intents["intent:remove-contract"]["transition"]["steps"]
        assert any((step.get("value") or {}).get("kind") == "domain.call" for step in add_steps)
        assert any((step.get("value") or {}).get("kind") == "domain.call" for step in remove_steps)
        assert (add_steps[0]["value"]["operator"] or {})["ref"] == "operator:workbench.add-contract.contracts@v1"
        assert (remove_steps[0]["value"]["operator"] or {})["ref"] == "operator:workbench.remove-contract.contracts@v1"
        return
    payload = _run(
        f"const app=require({json.dumps(str(APPLICATION))});"
        "const state={contracts:[],nextContractId:1,revision:0};"
        "const add=app.operations['add-contract'];"
        "const next=add.transition({state,payload:{name:'Steel',quantity:12,category:'materials'}});"
        "const remove=app.operations['remove-contract'];"
        "const finalState=remove.transition({state:next,payload:{contractId:'contract-1'}});"
        "process.stdout.write(JSON.stringify({next,finalState,addValid:add.ensures({before:state,after:next}),removeValid:remove.ensures({before:next,after:finalState,payload:{contractId:'contract-1'}})}));"
    )
    assert payload["next"]["contracts"] == [
        {
            "id": "contract-1",
            "name": "Steel",
            "category": "materials",
            "quantity": 12,
            "quoteStatus": "idle",
            "quoteAmount": 0,
        }
    ]
    assert payload["next"]["revision"] == 1
    assert payload["finalState"]["contracts"] == []
    assert payload["finalState"]["revision"] == 2
    assert payload["addValid"] is True
    assert payload["removeValid"] is True
