from __future__ import annotations

from pathlib import Path

from main_computer.mcel_calculator_candidate_projection import project_calculator_candidate
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.calculator_shadow_v1 import project_calculator_ir


REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "mcel_apps/calculator/application.js"


def test_calculator_shadow_dsl_compiles_and_projects_deterministically() -> None:
    compiled = compile_dsl_application(SOURCE, write_candidate=False)
    assert compiled.valid is True
    assert compiled.normalized_ir is not None
    assert compiled.semantic_fingerprint
    assert len(compiled.normalized_ir["intents"]) == 11
    assert len(compiled.normalized_ir["capabilities"]) == 3

    first = project_calculator_ir(compiled.normalized_ir)
    second = project_calculator_ir(compiled.normalized_ir)
    assert first.files == second.files
    assert first.file_hashes == second.file_hashes
    assert len(first.files) == 8


def test_calculator_shadow_candidate_projection_is_non_promoting() -> None:
    result = project_calculator_candidate(
        dsl_source_path=SOURCE,
        live_package_root=REPO / "mcel_apps/calculator",
        candidate_root=REPO / "runtime/state/mcel/compiler-candidates",
        write_candidate=False,
    )
    payload = result.to_dict()
    assert result.valid is True
    assert result.status == "pass"
    assert payload["projection"]["intentCount"] == 11
    assert payload["projection"]["capabilityCount"] == 3
    assert payload["authority"]["liveCalculatorChanged"] is False
    assert payload["authority"]["hostBoundRuntimeActive"] is True
    assert payload["projection"]["hostBoundRuntimeActive"] is True
    assert payload["authority"]["candidatePromoted"] is False
    assert payload["authority"]["promotionEligible"] is False
