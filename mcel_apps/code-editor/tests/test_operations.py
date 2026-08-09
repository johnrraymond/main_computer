from __future__ import annotations

from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application


REPO = Path(__file__).resolve().parents[3]
EXPECTED = {
    "applyReviewedPatch",
    "closeFile",
    "discardDraft",
    "editDraft",
    "inspectWorkspace",
    "openFile",
    "previewAiderPlan",
    "saveFile",
}


def test_code_editor_declares_the_stable_runtime_facade_operations() -> None:
    compiled = compile_dsl_application(REPO / "mcel_apps/code-editor/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    intents = {item["sourceName"]: item for item in compiled.normalized_ir["intents"]}
    assert set(intents) == EXPECTED
    assert all(item["runtimeMethod"] == name for name, item in intents.items())
    assert all(item.get("writes") == [] for item in intents.values())
    assert all("transition" not in item for item in intents.values())


def test_source_workspace_aider_and_reviewed_patch_lanes_are_explicit_capabilities() -> None:
    compiled = compile_dsl_application(REPO / "mcel_apps/code-editor/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    intents = {item["sourceName"]: item for item in compiled.normalized_ir["intents"]}
    capability_names = {
        "applyReviewedPatch",
        "inspectWorkspace",
        "openFile",
        "previewAiderPlan",
        "saveFile",
    }
    assert all(intents[name]["operationKind"] == "capability" for name in capability_names)
    assert all(len(intents[name]["effectRefs"]) == 1 for name in capability_names)
    assert intents["editDraft"]["operationKind"] == "interaction"
    assert intents["discardDraft"]["operationKind"] == "interaction"
    assert intents["closeFile"]["operationKind"] == "interaction"
