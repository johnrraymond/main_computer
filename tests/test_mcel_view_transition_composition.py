"""Generic MCEL local-selection and tab-composition contracts.

No Task Manager mount, backend capability, or application-specific adapter.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_application_ir import validate_application_ir

ROOT = Path(__file__).resolve().parents[1]
DSL = ROOT / "mcel_experiments/task-manager-notebook/application.js"
RUNTIME = ROOT / "main_computer/web/applications/scripts/mcel-view-transition.js"


def _ir():
    report = compile_dsl_application(DSL)
    assert report.valid, report.diagnostics
    return report.normalized_ir


def _codes(ir):
    return {d.code for d in validate_application_ir(ir).diagnostics}


def _mutate(mutator):
    data = json.loads(json.dumps(_ir()))
    mutator(data)
    return data


def _tab(ir):
    return ir["surfaces"][0]["semanticSurface"]["tabGroup"]


def test_generic_compiler_accepts_complete_bounded_selection():
    ir = _ir()
    intent = ir["intents"][0]
    states = {s["id"]: s for s in ir["states"]}
    target_id = intent["viewTransition"]["target"]["ref"]
    assert states[target_id]["authority"] == "renderer-local"
    assert states[target_id]["initial"] == _tab(ir)["defaultTabId"]
    assert intent["viewTransition"]["value"]["input"]["ref"] == intent["input"][0]["id"]
    assert intent["input"][0]["schema"] == states[target_id]["schema"]
    assert intent["viewWrites"] == [{"ref": target_id}]
    assert not intent["writes"] and not ir["effects"] and not ir["capabilities"]


@pytest.mark.parametrize('change', [
    lambda ir: _tab(ir)["tabs"][1].update(panel=_tab(ir)["tabs"][0]["panel"]),
    lambda ir: _tab(ir)["tabs"][1].update(id=_tab(ir)["tabs"][0]["id"]),
    lambda ir: _tab(ir)["tabs"][1].update(tab=_tab(ir)["tabs"][0]["tab"]),
    lambda ir: _tab(ir).update(defaultTabId="unknown"),
    lambda ir: _tab(ir).update(state="wrongState"),
    lambda ir: ir["surfaces"][0]["semanticSurface"]["controls"][0].update(controls="#other-panel"),
    lambda ir: ir["surfaces"][0]["semanticSurface"]["regions"][3].update(controlledBy="#wrong-tab"),
    lambda ir: _tab(ir)["a11y"].update(keyboard="none"),
    lambda ir: ir["surfaces"][0]["semanticSurface"]["controls"].pop(),
])
def test_general_tab_panel_composition_refuses_broken_relationships(change):
    ir = _mutate(change)
    assert "MCEL_IR_TAB_GROUP_COMPOSITION_INVALID" in _codes(ir)


@pytest.mark.parametrize('change', [
    lambda ir: ir["intents"][0]["input"][0]["schema"].update(enum=["server-processes", "hardware"]),
    lambda ir: ir["intents"][0]["viewWrites"].clear(),
    lambda ir: ir["intents"][0]["viewTransition"].update(invalidInput="ignore"),
    lambda ir: ir["intents"][0]["viewTransition"].update(target={"ref": "state:task-manager-notebook.notebook-data"}),
    lambda ir: ir["intents"][0].update(operationKind="mutation"),
    lambda ir: ir["intents"][0].update(lane="external-mutation"),
    lambda ir: ir["states"][0].update(authority="canonical") if ir["states"][0]["sourceName"] == "activeTabId" else next(s.update(authority="canonical") for s in ir["states"] if s["sourceName"] == "activeTabId"),
    lambda ir: ir["intents"][0].update(effectRefs=[{"ref": "effect:illegal"}]),
])
def test_typed_local_state_transition_refuses_invalid_authority(change):
    ir = _mutate(change)
    assert "MCEL_IR_VIEW_TRANSITION_INVALID" in _codes(ir)


def test_ordinary_non_tab_application_is_not_subject_to_tab_law():
    ir = _mutate(lambda data: data["surfaces"][0]["semanticSurface"].pop("tabGroup"))
    assert "MCEL_IR_TAB_GROUP_COMPOSITION_INVALID" not in _codes(ir)


def _execute(payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for pure view-state transition test")
    script = """const fs=require('fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const runtime=require(process.argv[1]);
const output=runtime.apply(input.intent,input.states,input.payload,input.definitions);
process.stdout.write(JSON.stringify(output));"""
    result = subprocess.run([node, "-e", script, str(RUNTIME)], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


def test_generic_transition_changes_only_view_state_without_mutating_source():
    ir = _ir()
    intent = ir["intents"][0]
    state_id = intent["viewTransition"]["target"]["ref"]
    initial = {state_id: "server-processes", "other": "persist"}
    definitions = ir["states"]
    good = _execute({"intent": intent, "states": initial, "definitions": definitions,
                     "payload": {"selectedTabId": "connections"}})
    assert good == {"ok": True, "nextState": {state_id: "connections", "other": "persist"},
                    "selected": "connections", "effects": []}
    assert initial[state_id] == "server-processes"
    unknown = _execute({"intent": intent, "states": initial, "definitions": definitions, "payload": {"selectedTabId": "bogus"}})
    assert unknown == {"ok": False, "code": "invalid-selection", "effects": []}
    wrong_type = _execute({"intent": intent, "states": initial, "definitions": definitions, "payload": {"selectedTabId": 123}})
    assert wrong_type == unknown
    missing = _execute({"intent": intent, "states": {}, "definitions": definitions, "payload": {"selectedTabId": "connections"}})
    assert missing == {"ok": False, "code": "state-missing", "effects": []}


def test_transition_refuses_effect_injection_even_when_input_is_valid():
    ir = _ir()
    intent = ir["intents"][0]
    state_id = intent["viewTransition"]["target"]["ref"]
    intent["effectRefs"] = [{"ref": "effect:bad"}]
    output = _execute({"intent": intent, "states": {state_id: "server-processes"},
                       "definitions": ir["states"], "payload": {"selectedTabId": "connections"}})
    assert output == {"ok": False, "code": "unproven-view-transition", "effects": []}


def test_transition_refuses_unverified_state_authority():
    ir = _ir()
    intent = ir["intents"][0]
    state_id = intent["viewTransition"]["target"]["ref"]
    definitions = ir["states"]
    for state in definitions:
        if state["id"] == state_id:
            state["authority"] = "canonical"
    output = _execute({"intent": intent, "states": {state_id: "server-processes"},
                       "definitions": definitions, "payload": {"selectedTabId": "connections"}})
    assert output == {"ok": False, "code": "unverified-local-state-authority", "effects": []}
