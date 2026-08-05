from __future__ import annotations

from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.calculator_shadow_v1 import project_calculator_ir


REPO = Path(__file__).resolve().parents[3]


def test_calculator_shadow_surface_preserves_existing_html_authority() -> None:
    compiled = compile_dsl_application(REPO / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    surface = compiled.normalized_ir["surfaces"][0]
    assert surface["route"] == "/applications/calculator"
    assert surface["root"] == "#calculator-app"
    assert surface["presentationAuthority"] == "existing-host-html"
    assert len(surface["nodes"]) == 11

    projected = project_calculator_ir(compiled.normalized_ir)
    surface_module = projected.files["contracts/surface.js"].decode("utf-8")
    assert '"rootSelector": "#calculator-app"' in surface_module
    assert '"presentationAuthority": "existing-host-html"' in surface_module
