from __future__ import annotations

from pathlib import Path

from main_computer.mcel_app_authoring_profiles import get_app_authoring_profile
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_calculator_browser_observation import run_calculator_browser_observation
from main_computer.mcel_calculator_candidate_evidence import run_calculator_candidate_evidence
from main_computer.mcel_calculator_ir_native_proof import run_calculator_ir_native_intent_proof
from main_computer.mcel_calculator_parity import run_calculator_generated_adapter_parity


ROOT = Path(__file__).resolve().parents[1]


def _calculator_record():
    return next(record for record in build_application_package_catalog(ROOT).packages if record.app_id == "calculator")


def test_calculator_generated_adapter_matches_legacy_semantic_adapter_without_promotion() -> None:
    parity = run_calculator_generated_adapter_parity(repo_root=ROOT)

    assert parity.valid is True
    payload = parity.to_dict()
    assert payload["status"] == "pass"
    assert payload["intentCount"] == 11
    assert payload["checks"]["runtimeProjectionHostBound"] is True
    assert payload["checks"]["browserCatalogHostBound"] is True
    assert payload["checks"]["generatedLegacyBindingsMatch"] is True
    assert payload["checks"]["localIntentsProviderFree"] is True
    assert payload["capabilityAccounting"]["status"] == "closed"
    assert payload["authority"]["legacySemanticAdapterRemainsLive"] is True
    assert payload["authority"]["promotionEligible"] is False
    assert payload["authority"]["freshChromiumObservation"] is False


def test_calculator_fresh_browser_observation_exercises_generated_and_legacy_adapters() -> None:
    observation = run_calculator_browser_observation(repo_root=ROOT)

    payload = observation.to_dict()
    assert observation.valid is True
    assert payload["status"] == "pass"
    assert payload["checks"]["freshChromiumObservation"] is True
    assert payload["checks"]["generatedAdapterMounted"] is True
    assert payload["checks"]["legacyAdapterMounted"] is True
    assert payload["checks"]["intentsObserved"] is True
    assert payload["checks"]["localProviderFree"] is True
    assert payload["effectAccounting"]["status"] == "closed"
    assert payload["observedIntentCount"] == 11
    assert [item["status"] for item in payload["intentResults"]] == ["pass"] * 11
    assert all(
        item["network"]["requestCount"] == 0
        for item in payload["intentResults"]
        if item["intentName"] in {"switchMode", "enterToken", "clearExpression", "evaluateExpression", "drawGraph", "resetGraph"}
    )
    assert all(
        item["network"]["requestCount"] > 0
        for item in payload["intentResults"]
        if item["intentName"] in {"askModelForExpression", "askModelForGraphExpression", "askModelForMathicsExpression", "evaluateMathics", "askResultQuestion"}
    )
    assert payload["authority"]["freshChromiumObservation"] is True
    assert payload["authority"]["promotionEligible"] is False


def test_calculator_authoring_profile_wires_shadow_evidence_hooks_only() -> None:
    profile = get_app_authoring_profile("calculator")

    assert profile.run_browser_probe is not None
    assert profile.run_ir_native_proof is not None
    assert profile.run_candidate_evidence is not None
    assert profile.run_node_probe is None
    assert profile.promotion_supported is False
    assert profile.promotion_rehearsal_supported is True


def test_calculator_shadow_ir_native_proof_converges_from_parity_evidence() -> None:
    proof = run_calculator_ir_native_intent_proof(
        repo=ROOT,
        record=_calculator_record(),
        acceptance={},
        observation={},
    )

    assert proof["passed"] is True
    assert proof["status"] == "ir-native-shadow"
    assert proof["coverageMode"] == "fresh-browser-shadow-dsl-host-bound-generated-adapter-parity"
    assert proof["declaredIntentCount"] == 11
    assert proof["coveredIntentCount"] == 11
    assert proof["effectAccounting"]["status"] == "closed"
    assert proof["crossCuttingChecks"]["freshBrowserParity"] is True
    assert proof["parityEvidence"]["freshChromiumObservation"] is True
    assert proof["promotionEligible"] is False


def test_calculator_candidate_evidence_isolated_and_non_promoting(tmp_path: Path) -> None:
    package = ROOT / "mcel_apps/calculator"
    before = {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file()
    }

    result = run_calculator_candidate_evidence(
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        report_root=tmp_path / "reports",
        write_report=True,
    )

    after = {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file()
    }
    payload = result.to_dict()

    assert result.valid is True
    assert payload["status"] == "pass"
    assert payload["truthStatus"] == "fresh-browser-shadow-ir-native-parity"
    assert payload["authority"]["candidatePromoted"] is False
    assert payload["authority"]["promotionEligible"] is False
    assert payload["authority"]["legacySemanticAdapterRemainsLive"] is True
    assert payload["authority"]["freshChromiumObservation"] is True
    assert payload["stages"]["freshBrowserParity"]["status"] == "pass"
    assert all(stage["status"] == "pass" for stage in payload["stages"].values())
    assert before == after
    assert result.output_directory is not None
    assert (result.output_directory / "mcel-calculator-candidate-evidence-report.json").is_file()
    assert (result.output_directory / "mcel.application.ir.json").is_file()
