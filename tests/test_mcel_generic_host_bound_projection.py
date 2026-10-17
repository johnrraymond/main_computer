"""Unregistered DSL application projection, deterministic materialization and refusals."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from main_computer.mcel_app_compile import compile_application
from main_computer.mcel_app_project import AppProjectionError, project_application
from main_computer.mcel_application_materialization import materialize_generated_package_files
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.host_bound_generic_v1 import (
    HostBoundIrProjectionError,
    project_host_bound_ir,
)
from main_computer.mcel_projection_profiles.host_bound_policy import project_with_policy

ROOT = Path(__file__).resolve().parents[1]


def _third_application(tmp_path: Path) -> tuple[Path, Path]:
    source = ROOT / "mcel_apps/calculator"
    destination = tmp_path / "mcel_apps/third-probe"
    shutil.copytree(source, destination)
    for path in destination.rglob("*"):
        if not path.is_file() or path.suffix not in {".js", ".json", ".md", ".py"}:
            continue
        text = path.read_text(encoding="utf-8")
        text = (text.replace("calculator", "third-probe")
                .replace("Calculator", "ThirdProbe")
                .replace("CALCULATOR", "THIRD_PROBE"))
        if path.name == "application.js":
            text = text.replace(
                'surfaceId: "third-probe.surface.workspace",',
                'surfaceId: "third-probe.surface.workspace",\n  contractId: "third-probe.contract.workspace",',
                1,
            )
        path.write_text(text, encoding="utf-8", newline="\n")
    return tmp_path, destination


def test_third_application_fixture_writes_lf_on_all_platforms(tmp_path: Path) -> None:
    _, package = _third_application(tmp_path)
    for path in package.rglob("*"):
        if path.is_file() and path.suffix in {".js", ".json", ".md", ".py"}:
            assert b"\r\n" not in path.read_bytes(), str(path.relative_to(package))


def test_third_unregistered_dsl_app_compiles_projects_and_materializes(tmp_path: Path) -> None:
    root, package = _third_application(tmp_path)
    catalog = build_application_package_catalog(root)
    assert catalog.ok, [str(issue) for record in catalog.packages for issue in record.errors]
    record, = catalog.packages
    assert record.valid and record.app_id == "third-probe"
    assert "contracts/adapter.js" in record.files
    assert "mcel.generated.json" in record.files

    compiled = compile_application(app_id="third-probe", repo_root=root)
    assert compiled.valid and compiled.report["profileFreeCompilation"] is True
    first = project_application(app_id="third-probe", repo_root=root)
    second = project_application(app_id="third-probe", repo_root=root)
    assert first.valid and second.valid
    assert first.to_dict() == second.to_dict()
    assert first.report["applicationProfile"] is None
    assert first.report["projection"]["projection"]["fileCount"] == 9
    assert first.report["semanticFingerprint"] == compiled.report["semanticFingerprint"]
    assert first.report["projection"]["authority"]["hostBoundRuntimeActive"] is True
    # Rendering is not proof that the third application has a matching host runtime.
    assert not (package / "contracts").exists()
    assert not (package / "generated").exists()


def test_third_app_candidate_is_isolated_and_deterministic(tmp_path: Path) -> None:
    root, package = _third_application(tmp_path)
    before = {p.relative_to(package).as_posix(): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    candidate_root = tmp_path / "candidate-output"
    first = project_application(app_id="third-probe", repo_root=root, candidate_root=candidate_root, write_candidate=True)
    second = project_application(app_id="third-probe", repo_root=root, candidate_root=candidate_root, write_candidate=True)
    assert first.valid and second.valid
    assert first.report["projection"]["projection"] == second.report["projection"]["projection"]
    after = {p.relative_to(package).as_posix(): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    assert after == before
    assert (candidate_root / "third-probe").is_dir()


def test_unknown_app_requires_explicit_contract_not_invented_one(tmp_path: Path) -> None:
    _, package = _third_application(tmp_path)
    source = package / "application.js"
    source.write_text(source.read_text().replace('  contractId: "third-probe.contract.workspace",\n', ''), encoding="utf-8", newline="\n")
    compiled = compile_dsl_application(source)
    assert compiled.valid and compiled.normalized_ir
    with pytest.raises(HostBoundIrProjectionError, match="contractId"):
        project_host_bound_ir(compiled.normalized_ir)
    result = project_application(app_id="third-probe", repo_root=tmp_path)
    assert not result.valid
    assert result.status == "unsupported-ir"


def test_generic_projector_refuses_unsupported_ir_without_output(tmp_path: Path) -> None:
    _, package = _third_application(tmp_path)
    compiled = compile_dsl_application(package / "application.js")
    assert compiled.valid and compiled.normalized_ir
    from copy import deepcopy
    ir = deepcopy(compiled.normalized_ir)
    ir["intents"][0]["writes"] = [{"ref": "state:third-probe.data"}]
    with pytest.raises(HostBoundIrProjectionError, match="canonical state writes"):
        project_host_bound_ir(ir)
    ir = deepcopy(compiled.normalized_ir)
    ir["surfaces"][0]["route"] = "/admin"
    with pytest.raises(HostBoundIrProjectionError, match="Host route"):
        project_host_bound_ir(ir)
    ir = deepcopy(compiled.normalized_ir)
    ir["surfaces"].append(ir["surfaces"][0])
    with pytest.raises(HostBoundIrProjectionError, match="exactly one surface"):
        project_host_bound_ir(ir)


def test_legacy_projector_generated_bytes_agree_with_common_ir_engine() -> None:
    from main_computer.mcel_projection_profiles.calculator_shadow_v1 import project_calculator_ir
    from main_computer.mcel_projection_profiles.code_editor_host_bound_v1 import project_code_editor_ir
    for app, legacy in (("calculator", project_calculator_ir), ("code-editor", project_code_editor_ir)):
        ir = compile_dsl_application(ROOT / "mcel_apps" / app / "application.js").normalized_ir
        assert ir is not None
        assert legacy(ir).files == project_with_policy(ir).files
        manifest = json.loads((ROOT / "mcel_apps" / app / "mcel.app.json").read_text())
        files = materialize_generated_package_files(ROOT, ROOT / "mcel_apps" / app, {
            "mcel.app.json": json.dumps(manifest),
        })
        for relative, contents in legacy(ir).files.items():
            assert files[relative] == contents


def test_declared_identity_must_match_package_identity(tmp_path: Path) -> None:
    _, package = _third_application(tmp_path)
    source = package / "application.js"
    source.write_text(
        source.read_text().replace('const APP_ID = "third-probe";', 'const APP_ID = "other-probe";'),
        encoding="utf-8",
        newline="\n",
    )
    # No successful projection may be attributed to a different app manifest.
    result = project_application(app_id="third-probe", repo_root=tmp_path)
    assert not result.valid and result.status == "unsupported-ir"
    assert any("package identity" in str(item.get("summary")) for item in result.diagnostics)
