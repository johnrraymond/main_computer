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


def test_calculator_authoritative_package_is_authored_only_repository_authority() -> None:
    catalog = build_application_package_catalog(ROOT)
    assert catalog.ok is True
    record = next(item for item in catalog.packages if item.app_id == "calculator")
    assert record.valid is True
    assert record.conformance["shadow"] is False
    assert record.conformance["currentMode"] == "semantic-runtime-proven"
    assert record.runtime == {}
    assert not (PACKAGE / "contracts").exists()
    assert not (PACKAGE / "generated").exists()
    assert not (PACKAGE / "mcel.generated.json").exists()
    assert not (PACKAGE / "src").exists()


def test_calculator_authoritative_dsl_compiles_and_projects_through_generic_commands() -> None:
    compiled = compile_application(app_id="calculator", repo_root=ROOT)
    assert compiled.valid is True
    assert compiled.report["sourceAuthority"] == "mcel.dsl.v1"
    assert compiled.report["promotionExecuted"] is True

    projected = project_application(app_id="calculator", repo_root=ROOT)
    assert projected.valid is True
    assert projected.report["projectionProfile"] == "mcel.calculator.host-bound-projection.v1"
    assert projected.report["projection"]["projection"]["intentCount"] == 11
    assert projected.report["projection"]["authority"]["liveCalculatorChanged"] is True
    assert projected.report["projection"]["authority"]["promotionEligible"] is True


def test_calculator_authoritative_candidate_write_is_isolated_and_deterministic(tmp_path: Path) -> None:
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


def test_authoritative_package_is_published_only_as_host_bound_calculator_runtime() -> None:
    runtime = build_runtime_projection_set(ROOT)
    browser = build_repository_browser_catalog_payload(ROOT)
    runtime_records = [item for item in runtime.projections if item.app_id == "calculator"]
    browser_records = [item for item in browser["packages"] if item["appId"] == "calculator"]

    assert len(runtime_records) == 1
    assert runtime_records[0].mount_mode == "host-bound"
    assert runtime_records[0].document_url is None
    assert runtime_records[0].script_url is None
    assert runtime_records[0].style_url is None
    assert len(browser_records) == 1
    assert browser_records[0]["runtimeProjection"]["mountMode"] == "host-bound"


def test_calculator_core_and_capability_bridge_include_precede_runtime_include_and_legacy_layers_are_removed() -> None:
    shell = (ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")
    core = "<!-- @include applications/scripts/calculator-core.js -->"
    view_model = "<!-- @include applications/scripts/calculator-view-model.js -->"
    capabilities = "<!-- @include applications/scripts/calculator-capabilities.js -->"
    runtime = "<!-- @include applications/scripts/calculator.js -->"
    assert core in shell
    assert view_model in shell
    assert capabilities in shell
    assert runtime in shell
    assert shell.index(core) < shell.index(view_model) < shell.index(capabilities) < shell.index(runtime)
    assert "calculator-semantic-adapter.js" not in shell
    assert "mcel-calculator-surface.js" not in shell


def test_calculator_authoritative_dsl_source_reads_as_fluent_app_declaration() -> None:
    source = (PACKAGE / "application.js").read_text(encoding="utf-8")
    runtime = (ROOT / "main_computer/mcel_dsl_runtime.js").read_text(encoding="utf-8")

    assert "const portableApplication =" not in source
    assert "Shadow Calculator authority" not in source
    assert "STATE_FIELDS" not in source
    assert "CAPABILITY_LANES" not in source
    assert "INTENT_DEFINITIONS" not in source
    assert "createCalculatorApplication" not in source
    assert "function stateRecord(" not in source
    assert "function intentRecord(" not in source
    assert "function capabilityRecord(" not in source
    assert "function effectRecord(" not in source
    assert "function scenarioRecord(" not in source
    assert "function invariantRecord(" not in source
    assert "toIr()" not in source
    assert "application.hostBound(declareCalculator)" in source
    assert "declareCalculator(app)" in source
    assert "app.presentation.hostBound(" in source
    assert "app.state.rendererLocal(" in source
    assert "app.field.string()" in source
    assert "app.field.record()" in source
    assert "unit-mode" in source
    assert "unit-result" in source
    assert "app.capability" in source
    assert "app.intent.interaction(" in source
    assert "compatible-units-normalize-before-result" in source
    assert "metric-length-scalar-arithmetic" in source
    assert "metric-time-normalization" in source
    assert "same-dimension-unit-ratio" in source
    assert "visible-unit-affordance" not in source
    assert "unit-affordance" not in source
    assert "app.invariant.semantic(" in source
    assert "app.scenario.example(" in source
    assert "app.intent.capabilityRequest(" in source
    assert "app.proof.semanticRuntimeProven()" in source
    assert "function createHostBoundApplicationBuilder(" in runtime
    assert "hostBound(declaration)" in runtime
    assert len(source.splitlines()) < 250
