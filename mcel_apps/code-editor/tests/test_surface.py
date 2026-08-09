from __future__ import annotations

from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.code_editor_host_bound_v1 import project_code_editor_ir


REPO = Path(__file__).resolve().parents[3]


def test_code_editor_surface_preserves_existing_html_authority_and_runtime_facade() -> None:
    compiled = compile_dsl_application(REPO / "mcel_apps/code-editor/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    surface = compiled.normalized_ir["surfaces"][0]
    assert surface["route"] == "/applications/code-editor"
    assert surface["root"] == "#code-editor-app"
    assert surface["presentationAuthority"] == "existing-host-html"
    assert surface["runtimeFacade"] == "MainComputerCodeEditorRuntime"
    assert len(surface["nodes"]) == 8

    projected = project_code_editor_ir(compiled.normalized_ir)
    surface_module = projected.files["contracts/surface.js"].decode("utf-8")
    adapter_module = projected.files["contracts/adapter.js"].decode("utf-8")
    assert '"rootSelector": "#code-editor-app"' in surface_module
    assert '"runtimeFacade": "MainComputerCodeEditorRuntime"' in surface_module
    assert "globalThis.MainComputerCodeEditorRuntime" in adapter_module
    assert "MainComputerCodeStudio" not in adapter_module
