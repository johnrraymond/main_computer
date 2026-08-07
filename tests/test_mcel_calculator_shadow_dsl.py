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


def test_calculator_candidate_projection_delegates_to_shared_host_bound_projector() -> None:
    calculator_projection = (ROOT / "main_computer/mcel_calculator_candidate_projection.py").read_text(encoding="utf-8")
    profile = (ROOT / "main_computer/mcel_calculator_host_bound_profile.py").read_text(encoding="utf-8")
    generic_projection = (ROOT / "main_computer/mcel_host_bound_candidate_projection.py").read_text(encoding="utf-8")

    assert "build_calculator_projection_profile(" in calculator_projection
    assert "project_host_bound_candidate(" in calculator_projection
    assert "HostBoundProjectionProfile(" in profile
    assert "def _write_shadow_package(" not in calculator_projection
    assert "def _existing_drift(" not in calculator_projection
    assert "def project_host_bound_candidate(" in generic_projection
    assert "class HostBoundProjectionProfile" in generic_projection
    assert "liveCalculatorChanged" not in generic_projection


def test_calculator_browser_observation_delegates_to_shared_host_bound_observer() -> None:
    calculator_observation = (ROOT / "main_computer/mcel_calculator_browser_observation.py").read_text(encoding="utf-8")
    profile = (ROOT / "main_computer/mcel_calculator_host_bound_profile.py").read_text(encoding="utf-8")
    generic_observation = (ROOT / "main_computer/mcel_host_bound_browser_observation.py").read_text(encoding="utf-8")

    assert "build_calculator_browser_observation_profile(" in calculator_observation
    assert "run_host_bound_browser_observation(" in calculator_observation
    assert "HostBoundBrowserObservationProfile(" in profile
    assert "BROWSER_OBSERVATION_SCRIPT" in profile
    assert "def _run_playwright(" not in calculator_observation
    assert "def _browser_injected_projection(" not in calculator_observation
    assert "def _effect_accounting(" not in calculator_observation
    assert "def run_host_bound_browser_observation(" in generic_observation
    assert "class HostBoundBrowserObservationProfile" in generic_observation
    assert "MainComputerCalculatorRuntime" not in generic_observation
    assert "calculator-browser-observation-source-v1" not in generic_observation


def test_calculator_candidate_evidence_delegates_to_shared_host_bound_collector() -> None:
    calculator_evidence = (ROOT / "main_computer/mcel_calculator_candidate_evidence.py").read_text(encoding="utf-8")
    profile = (ROOT / "main_computer/mcel_calculator_host_bound_profile.py").read_text(encoding="utf-8")
    generic_evidence = (ROOT / "main_computer/mcel_host_bound_candidate_evidence.py").read_text(encoding="utf-8")

    assert "build_calculator_candidate_evidence_profile(" in calculator_evidence
    assert "run_host_bound_candidate_evidence(" in calculator_evidence
    assert "HostBoundCandidateEvidenceProfile(" in profile
    assert "def _tree_fingerprint(" not in calculator_evidence
    assert "def _calculator_record(" not in calculator_evidence
    assert "def _render_markdown(" not in calculator_evidence
    assert "def run_host_bound_candidate_evidence(" in generic_evidence
    assert "class HostBoundCandidateEvidenceProfile" in generic_evidence
    assert "mcel.calculator-candidate-evidence-report.v1" not in generic_evidence
    assert "existing-html-calculator-runtime" not in generic_evidence


def test_calculator_promotion_rehearsal_delegates_to_shared_host_bound_promoter() -> None:
    calculator_promotion = (ROOT / "main_computer/mcel_calculator_promotion_rehearsal.py").read_text(encoding="utf-8")
    profile = (ROOT / "main_computer/mcel_calculator_host_bound_profile.py").read_text(encoding="utf-8")
    generic_promotion = (ROOT / "main_computer/mcel_host_bound_promotion_rehearsal.py").read_text(encoding="utf-8")

    assert "build_calculator_promotion_profile(" in calculator_promotion
    assert "run_host_bound_promotion_rehearsal(" in calculator_promotion
    assert "execute_host_bound_promotion(" in calculator_promotion
    assert "rollback_host_bound_promotion(" in calculator_promotion
    assert "HostBoundPromotionProfile(" in profile
    assert "def _build_promotion_plan(" not in calculator_promotion
    assert "def _rehearse_apply_and_rollback(" not in calculator_promotion
    assert "def _validate_promoted_workspace(" not in calculator_promotion
    assert "def _render_markdown(" not in calculator_promotion
    assert "def run_host_bound_promotion_rehearsal(" in generic_promotion
    assert "class HostBoundPromotionProfile" in generic_promotion
    assert "mcel.calculator-promotion-rehearsal-report.v1" not in generic_promotion
    assert "mcel.calculator.host-bound-projection.v1" not in generic_promotion


def test_calculator_runtime_parity_delegates_to_shared_host_bound_parity() -> None:
    calculator_parity = (ROOT / "main_computer/mcel_calculator_parity.py").read_text(encoding="utf-8")
    profile = (ROOT / "main_computer/mcel_calculator_host_bound_profile.py").read_text(encoding="utf-8")
    generic_parity = (ROOT / "main_computer/mcel_host_bound_runtime_parity.py").read_text(encoding="utf-8")

    assert "build_calculator_runtime_parity_profile(" in calculator_parity
    assert "run_host_bound_generated_adapter_parity(" in calculator_parity
    assert "run_host_bound_browser_parity_probe(" in calculator_parity
    assert "HostBoundRuntimeParityProfile(" in profile
    assert "def _generated_bindings(" not in calculator_parity
    assert "def _capability_accounting(" not in calculator_parity
    assert "def _manifest(" not in calculator_parity
    assert "def run_host_bound_generated_adapter_parity(" in generic_parity
    assert "class HostBoundRuntimeParityProfile" in generic_parity
    assert "MainComputerCalculatorRuntime" not in generic_parity
    assert "mcel.calculator-generated-adapter-authority.v1" not in generic_parity


def test_calculator_ir_native_proof_delegates_to_shared_host_bound_proof() -> None:
    calculator_proof = (ROOT / "main_computer/mcel_calculator_ir_native_proof.py").read_text(encoding="utf-8")
    profile = (ROOT / "main_computer/mcel_calculator_host_bound_profile.py").read_text(encoding="utf-8")
    generic_proof = (ROOT / "main_computer/mcel_host_bound_ir_native_proof.py").read_text(encoding="utf-8")

    assert "build_calculator_ir_native_proof_profile(" in calculator_proof
    assert "run_host_bound_ir_native_intent_proof(" in calculator_proof
    assert "HostBoundIrNativeProofProfile(" in profile
    assert "def _scenario_count(" not in calculator_proof
    assert "def _acceptance_status(" not in calculator_proof
    assert "def _observation_status(" not in calculator_proof
    assert "def run_host_bound_ir_native_intent_proof(" in generic_proof
    assert "class HostBoundIrNativeProofProfile" in generic_proof
    assert "mcel.calculator-ir-native-authoritative-proof.v1" not in generic_proof
    assert "Calculator generated-adapter authority evidence did not pass" not in generic_proof


def test_calculator_host_bound_profile_centralizes_app_specific_facts() -> None:
    profile = (ROOT / "main_computer/mcel_calculator_host_bound_profile.py").read_text(encoding="utf-8")

    assert 'APP_ID = "calculator"' in profile
    assert 'ROUTE = "/applications/calculator"' in profile
    assert 'ROOT_SELECTOR = "#calculator-app"' in profile
    assert 'RUNTIME_FACADE = "MainComputerCalculatorRuntime"' in profile
    assert "INTENT_PAYLOADS" in profile
    assert "LOCAL_INTENTS" in profile
    assert "RETIRED_ARTIFACTS" in profile
    assert "build_calculator_projection_profile" in profile
    assert "build_calculator_browser_observation_profile" in profile
    assert "build_calculator_candidate_evidence_profile" in profile
    assert "build_calculator_promotion_profile" in profile
    assert "build_calculator_runtime_parity_profile" in profile
    assert "build_calculator_ir_native_proof_profile" in profile
