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
