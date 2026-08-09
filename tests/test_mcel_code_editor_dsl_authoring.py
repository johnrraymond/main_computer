from __future__ import annotations

from pathlib import Path

from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.code_editor_host_bound_v1 import project_code_editor_ir


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mcel_apps/code-editor/application.js"


EXPECTED_INTENTS = {
    "applyReviewedPatch",
    "closeFile",
    "discardDraft",
    "editDraft",
    "inspectWorkspace",
    "openFile",
    "previewAiderPlan",
    "saveFile",
}


def test_code_editor_dsl_is_host_bound_runtime_authority() -> None:
    compiled = compile_dsl_application(SOURCE, write_candidate=False)

    assert compiled.valid is True
    assert compiled.normalized_ir is not None
    assert compiled.semantic_fingerprint is not None
    assert compiled.normalized_ir["application"]["appId"] == "code-editor"
    assert compiled.normalized_ir["application"]["authoringStatus"] == "dsl-authoritative"

    surface = compiled.normalized_ir["surfaces"][0]
    assert surface["route"] == "/applications/code-editor"
    assert surface["root"] == "#code-editor-app"
    assert surface["presentationAuthority"] == "existing-host-html"
    assert surface["runtimeFacade"] == "MainComputerCodeEditorRuntime"

    intents = {item["sourceName"]: item for item in compiled.normalized_ir["intents"]}
    assert set(intents) == EXPECTED_INTENTS
    assert all(item["runtimeMethod"] == name for name, item in intents.items())
    assert all(item.get("writes") == [] for item in intents.values())
    assert all("transition" not in item for item in intents.values())


def test_code_editor_projection_uses_canonical_runtime_facade_without_old_studio_fallback() -> None:
    compiled = compile_dsl_application(SOURCE, write_candidate=False)
    assert compiled.valid and compiled.normalized_ir

    projection = project_code_editor_ir(compiled.normalized_ir)
    adapter = projection.files["contracts/adapter.js"].decode("utf-8")
    surface = projection.files["contracts/surface.js"].decode("utf-8")

    assert projection.profile_id == "mcel.code-editor.host-bound-projection.v1"
    assert "globalThis.MainComputerCodeEditorRuntime" in adapter
    assert "MainComputerCodeStudio" not in adapter
    assert '"runtimeFacade": "MainComputerCodeEditorRuntime"' in surface


def test_code_editor_package_is_materialized_in_catalog_without_checked_in_generated_files() -> None:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "code-editor")

    assert record.valid is True
    assert record.runtime == {}
    assert record.conformance["currentMode"] == "semantic-runtime-proven"
    assert record.files["contracts/domain.js"]
    assert record.files["contracts/adapter.js"]
    assert record.files["generated/mcel.application.normalized.json"]
    assert record.files["mcel.generated.json"]
    assert not (ROOT / "mcel_apps/code-editor/contracts").exists()
    assert not (ROOT / "mcel_apps/code-editor/generated").exists()
