from __future__ import annotations

from pathlib import Path

from main_computer.mcel_app_compile import compile_application
from main_computer.mcel_app_project import project_application
from main_computer.mcel_application_package_browser_catalog import (
    build_repository_browser_catalog_payload,
)
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_calculator_candidate_projection import project_calculator_candidate


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mcel_apps/calculator"


def test_calculator_shadow_is_valid_authored_only_repository_authority() -> None:
    catalog = build_application_package_catalog(ROOT)
    assert catalog.ok is True
    record = next(item for item in catalog.packages if item.app_id == "calculator")
    assert record.valid is True
    assert record.conformance["shadow"] is True
    assert record.runtime == {}
    assert len(record.files) == 20
    assert not (PACKAGE / "contracts").exists()
    assert not (PACKAGE / "generated").exists()
    assert not (PACKAGE / "mcel.generated.json").exists()
    assert not (PACKAGE / "src").exists()


def test_calculator_shadow_compiles_and_projects_through_generic_commands() -> None:
    compiled = compile_application(app_id="calculator", repo_root=ROOT)
    assert compiled.valid is True
    assert compiled.report["sourceAuthority"] == "mcel.dsl.shadow.v1"
    assert compiled.report["promotionExecuted"] is False

    projected = project_application(app_id="calculator", repo_root=ROOT)
    assert projected.valid is True
    assert projected.report["projectionProfile"] == "mcel.calculator.shadow-projection.v1"
    assert projected.report["projection"]["projection"]["intentCount"] == 11
    assert projected.report["projection"]["authority"]["liveCalculatorChanged"] is False


def test_calculator_shadow_candidate_write_is_isolated_and_deterministic(tmp_path: Path) -> None:
    before = {
        path.relative_to(PACKAGE).as_posix(): path.read_bytes()
        for path in PACKAGE.rglob("*")
        if path.is_file()
    }
    first = project_calculator_candidate(
        dsl_source_path=PACKAGE / "application.js",
        live_package_root=PACKAGE,
        candidate_root=tmp_path / "candidates",
        write_candidate=True,
    )
    second = project_calculator_candidate(
        dsl_source_path=PACKAGE / "application.js",
        live_package_root=PACKAGE,
        candidate_root=tmp_path / "candidates",
        write_candidate=True,
    )
    after = {
        path.relative_to(PACKAGE).as_posix(): path.read_bytes()
        for path in PACKAGE.rglob("*")
        if path.is_file()
    }

    assert first.valid is True
    assert second.valid is True
    assert first.to_dict()["projection"] == second.to_dict()["projection"]
    assert before == after
    assert first.candidate_directory is not None
    assert (first.candidate_directory / "projections/contracts/adapter.js").is_file()
    assert (first.candidate_directory / "projections/generated/mcel.application.normalized.json").is_file()


def test_shadow_package_is_not_published_as_a_second_calculator_application() -> None:
    runtime = build_runtime_projection_set(ROOT)
    browser = build_repository_browser_catalog_payload(ROOT)
    assert "calculator" not in {item.app_id for item in runtime.projections}
    assert "calculator" not in {item["appId"] for item in browser["packages"]}


def test_calculator_core_include_precedes_runtime_include() -> None:
    shell = (ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")
    core = "<!-- @include applications/scripts/calculator-core.js -->"
    runtime = "<!-- @include applications/scripts/calculator.js -->"
    assert core in shell
    assert runtime in shell
    assert shell.index(core) < shell.index(runtime)
