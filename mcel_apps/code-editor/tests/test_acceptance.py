from __future__ import annotations

from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.code_editor_host_bound_v1 import project_code_editor_ir


REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "mcel_apps/code-editor/application.js"


def test_code_editor_authoritative_dsl_compiles_and_projects_deterministically() -> None:
    compiled = compile_dsl_application(SOURCE, write_candidate=False)
    assert compiled.valid is True
    assert compiled.normalized_ir is not None
    assert compiled.semantic_fingerprint
    assert len(compiled.normalized_ir["intents"]) == 8
    assert len(compiled.normalized_ir["capabilities"]) == 3

    first = project_code_editor_ir(compiled.normalized_ir)
    second = project_code_editor_ir(compiled.normalized_ir)
    assert first.files == second.files
    assert first.file_hashes == second.file_hashes
    assert len(first.files) == 8
