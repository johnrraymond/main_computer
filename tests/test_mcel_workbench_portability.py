from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from main_computer.mcel_app_authoring_profiles import get_app_authoring_profile, registered_app_authoring_profiles
from main_computer.mcel_app_compile import compile_application
from main_computer.mcel_app_portability import prove_application_portability
from main_computer.mcel_app_project import project_application
from main_computer.mcel_app_promote import inspect_application_authority
from main_computer.mcel_application_definition_ir import import_application_definition
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_constrained_expression import analyze_application_expressions
from main_computer.mcel_workbench_expression_profile import (
    count_native_calls,
    count_opaque_callbacks,
)
from main_computer.mcel_workbench_candidate_projection import project_workbench_candidate

REPO = Path(__file__).resolve().parents[1]
DSL = REPO / "mcel_apps/contract-workbench/application.js"
IR = REPO / "tests/fixtures/mcel_application_ir/contract-workbench.ir.json"
SEMANTIC = "sha256:3450eddcd5b67687fc09ff7589221fff5ef176efcc2d54231a9b43e2268ca78e"


def test_workbench_definition_imports_to_valid_application_ir() -> None:
    result = import_application_definition("contract-workbench", REPO)
    assert result.valid is True
    assert result.semantic_fingerprint == SEMANTIC
    assert result.normalized_ir is not None
    assert len(result.normalized_ir["states"]) == 12
    assert len(result.normalized_ir["intents"]) == 7
    assert len(result.normalized_ir["scenarios"]) == 14
    assert len(result.normalized_ir["effects"]) == 18
    assert result.diagnostics == ()
    assert count_native_calls(result.normalized_ir) == 26
    assert count_opaque_callbacks(result.normalized_ir) == 0
    expressions = analyze_application_expressions(result.normalized_ir, emit_reference_diagnostics=True)
    assert expressions.valid is True
    assert all(analysis.purity == "pure" for _, analysis in expressions.analyses)
    assert all(analysis.determinism == "deterministic" for _, analysis in expressions.analyses)


def test_workbench_dsl_migration_candidate_is_semantically_exact() -> None:
    result = compile_dsl_application(DSL, compare_ir_path=IR)
    assert result.valid is True
    assert result.status == "pass"
    assert result.comparison_status == "exact"
    assert result.semantic_fingerprint == SEMANTIC


def test_workbench_runs_through_generic_compile_project_and_authority_inspection(tmp_path: Path) -> None:
    compiled = compile_application(app_id="contract-workbench", repo_root=REPO)
    assert compiled.valid is True
    assert compiled.report["candidateFrontend"] == "mcel.dsl.v1"
    assert compiled.report["semanticFingerprint"] == SEMANTIC

    projected = project_application(
        app_id="contract-workbench",
        repo_root=REPO,
        candidate_root=tmp_path / "candidates",
        write_candidate=True,
    )
    assert projected.valid is True
    assert projected.status == "exact"
    assert projected.report["counterSpecificExecutionPathRequired"] is False
    assert projected.report["portableIrProjectionComplete"] is True

    authority = inspect_application_authority(app_id="contract-workbench", repo_root=REPO)
    assert authority.valid is True
    assert authority.status == "promoted"
    assert authority.report["promotionExecuted"] is True
    assert authority.report["promotionSupported"] is True


def test_workbench_candidate_portability_uses_fresh_isolated_evidence(tmp_path: Path) -> None:
    def fake_runner(command, *, cwd, **_kwargs):
        workspace = Path(cwd)
        joined = " ".join(str(value) for value in command)
        if "mcel_acceptance_runner.py" in joined:
            path = workspace / "runtime/reports/mcel-acceptance/apps/contract-workbench/mcel-acceptance-report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"status": "pass", "passed": True}) + "\n", encoding="utf-8")
        elif "mcel_application_observation_runner.py" in joined:
            path = workspace / "runtime/reports/mcel-observation/apps/contract-workbench/mcel-operation-observation-report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"status": "pass", "ok": True}) + "\n", encoding="utf-8")
        elif "mcel_app_prove.py" in joined:
            path = workspace / "runtime/reports/mcel-app-proof/apps/contract-workbench/mcel-app-proof-report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "truthStatus": "semantic-runtime-proven",
                        "stages": {"repositoryBinding": {"status": "exact"}},
                        "intentCoverage": {
                            "passed": True,
                            "coverageMode": "normalized-definition-intent-convergence",
                            "declaredIntentCount": 7,
                            "coveredIntentCount": 7,
                            "declaredScenarioCount": 14,
                            "observedScenarioCount": 14,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="pass\n")

    result = prove_application_portability(
        app_id="contract-workbench",
        repo_root=REPO,
        candidate_root=tmp_path / "candidates",
        write_report=True,
        command_runner=fake_runner,
    )
    assert result.valid is True
    assert result.report["semanticCompatibility"] == "exact"
    assert result.report["candidateTruthStatus"] == "semantic-runtime-proven"
    assert result.report["counterSpecificExecutionPathRequired"] is False
    assert result.report["liveAuthority"] == "legacy-explicit-package"
    assert result.report["promotionExecuted"] is False
    assert result.report["evidenceReused"] is False
    assert result.report["portableIrProjectionComplete"] is True
    assert result.report["authoringFrontend"] == "mcel.dsl.v1"
    assert result.report["migrationDebt"]["opaqueCallbacks"] == 0
    assert result.report["migrationDebt"]["nativeDomainCalls"] == 26
    assert result.output_directory is not None
    assert (result.output_directory / "mcel-candidate-evidence-report.json").is_file()


def test_workbench_generated_candidate_drift_fails_closed(tmp_path: Path) -> None:
    candidate_root = tmp_path / "candidates"
    first = project_workbench_candidate(candidate_root=candidate_root, write_candidate=True)
    assert first.valid is True
    assert first.candidate_directory is not None
    drifted = first.candidate_directory / "projections/contracts/domain.js"
    drifted.write_text(drifted.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="manual drift"):
        project_workbench_candidate(candidate_root=candidate_root, write_candidate=True)


def test_workbench_profile_is_registered_with_transactional_promotion_authority() -> None:
    assert "contract-workbench" in registered_app_authoring_profiles()
    profile = get_app_authoring_profile("contract-workbench")
    assert profile.promotion_supported is True
    assert profile.promotion_rehearsal_supported is True
    assert profile.portable_ir_projection_complete is True
    assert profile.authoring_frontend == "mcel.dsl.v1"
    assert callable(profile.execute_promotion)
    assert callable(profile.rollback_promotion)


def test_workbench_native_operator_migration_preserves_v1_semantic_identity() -> None:
    import copy

    from main_computer.mcel_application_ir import compare_application_ir, validate_application_ir

    native = json.loads(IR.read_text(encoding="utf-8"))
    legacy = copy.deepcopy(native)

    def downgrade(value):
        if isinstance(value, dict):
            if value.get("kind") == "domain.call":
                compatibility = value.get("compatibility") or {}
                old = compatibility.get("legacyOpaqueFunction")
                assert isinstance(old, dict)
                return copy.deepcopy(old)
            return {key: downgrade(child) for key, child in value.items()}
        if isinstance(value, list):
            return [downgrade(child) for child in value]
        return value

    legacy = downgrade(legacy)
    legacy.pop("fingerprints", None)
    legacy.pop("normalization", None)
    legacy_report = validate_application_ir(legacy)
    assert legacy_report.valid is True
    assert legacy_report.semantic_fingerprint == SEMANTIC
    assert compare_application_ir(native, legacy_report.normalized)["status"] == "exact"


def test_workbench_projection_profile_is_deterministic_in_memory_and_fails_closed() -> None:
    from main_computer.mcel_projection_profiles.contract_workbench_v1 import (
        WorkbenchProjectionProfileError,
        project_workbench_ir,
    )

    compiled = compile_dsl_application(DSL, compare_ir_path=IR)
    assert compiled.valid is True
    assert compiled.normalized_ir is not None

    first = project_workbench_ir(compiled.normalized_ir)
    second = project_workbench_ir(compiled.normalized_ir)
    assert first.profile["portableIrProjectionComplete"] is True
    assert first.profile["materialization"] == "in-memory"
    assert first.definition_fingerprint == "sha256:6cb3c6d27a351fdd12e9d9e714e70ba75ed87468bc56815a54bf2078784c408d"
    assert first.files == second.files
    assert len(first.files) == 8
    assert {
        path: hashlib.sha256(content).hexdigest()
        for path, content in sorted(first.files.items())
    } == {
        "contracts/acceptance.js": "5467b5046b047c7115d9d71d697a88708b0017d097633506cf0fde6fa6998649",
        "contracts/adapter.js": "687f7c5796f762db76ae225930f1cb6b41b6ef8dd7a04b859514ef7b5d65b0de",
        "contracts/domain.js": "c3521c700d23a511f421367963b4d289c45c69cc6061dfea306c02de5d012ef7",
        "contracts/intents.js": "5af3195bbb6807c29bde73e938ebd50cde2873b86499c3acccf6aa18b341561a",
        "contracts/layout.js": "26208932cfd0e3de4f8fddd43768850f8935fe32afde6314de4ff7df903f4a04",
        "contracts/observation.js": "c6e81543499933b634b0f93b6f869fd3566f3e2fbe3349175aa075026be70fa4",
        "contracts/surface.js": "ef28950b2af3acdcc7fb7cb5c7be9e806f7651b799135d15f2a88917f258688f",
        "generated/mcel.application.normalized.json": "5c3b7436338c93aba3085c5ec2c8937d71e7c6320c56ba16e9b4af92b96b0cfe",
    }
    legacy_profile = REPO / "main_computer/mcel_projection_profiles/contract-workbench-v1"
    assert not legacy_profile.exists() or not any(path.is_file() for path in legacy_profile.rglob("*"))

    drifted = json.loads(json.dumps(compiled.normalized_ir))
    drifted["fingerprints"]["semantic"] = "sha256:stale"
    with pytest.raises(WorkbenchProjectionProfileError, match="semantic fingerprint"):
        project_workbench_ir(drifted)
