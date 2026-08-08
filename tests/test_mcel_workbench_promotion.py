from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from main_computer.mcel_app_promote import (
    execute_application_promotion,
    rollback_application_promotion,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_workbench_candidate_projection import project_workbench_candidate
from main_computer.mcel_workbench_promotion import (
    execute_workbench_promotion,
    rollback_workbench_promotion,
)
from main_computer.mcel_workbench_promotion_rehearsal import (
    _build_promotion_plan,
    _stage_material,
    _tree_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
DSL = Path("mcel_apps/contract-workbench/application.js")
FIXTURE = Path("tests/fixtures/mcel_application_ir/contract-workbench.ir.json")
SEMANTIC = "sha256:3450eddcd5b67687fc09ff7589221fff5ef176efcc2d54231a9b43e2268ca78e"


def _copy_repo(target: Path) -> Path:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {"runtime", ".git", ".pytest_cache", "__pycache__", "node_modules"}
            or name.endswith((".zip", ".pyc", ".pyo"))
        }

    shutil.copytree(ROOT, target, ignore=ignore)
    return target


def _mark_workbench_legacy(repo: Path) -> None:
    """Restore the fixture pre-promotion authority marker for transaction tests."""

    manifest_path = repo / "mcel_apps/contract-workbench/mcel.app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("authoring", {})["status"] = "semantic-runtime-proven"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class _RehearsalResult:
    valid = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def to_dict(self) -> dict:
        return dict(self.payload)


def _rehearsal_runner(**kwargs):
    repo = Path(kwargs["repo_root"])
    source = repo / DSL
    fixture = repo / FIXTURE
    dsl = compile_dsl_application(source, compare_ir_path=fixture)
    candidate_root = repo / "runtime/state/mcel/compiler-candidates"
    projection = project_workbench_candidate(
        dsl_source_path=source,
        fixture_ir_path=fixture,
        live_package_root=repo / "mcel_apps/contract-workbench",
        candidate_root=candidate_root,
        write_candidate=True,
    )
    assert dsl.valid and dsl.source_binding_fingerprint
    assert projection.valid and projection.candidate_directory
    evidence = {
        "status": "pass",
        "truthStatus": "semantic-runtime-proven",
        "candidate": {
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
        },
        "authority": {"evidenceReused": False},
    }
    plan, promoted = _build_promotion_plan(
        live_package=repo / "mcel_apps/contract-workbench",
        candidate_package=projection.candidate_directory
        / "package/mcel_apps/contract-workbench",
        dsl_source=source,
        semantic_fingerprint=str(dsl.semantic_fingerprint),
        source_binding_fingerprint=str(dsl.source_binding_fingerprint),
        evidence=evidence,
    )
    base = projection.candidate_directory / "promotion-rehearsal"
    _stage_material(base, plan, promoted, repo / "mcel_apps/contract-workbench")
    return _RehearsalResult(
        {
            "valid": True,
            "status": "pass",
            "promotionEligible": True,
            "rollbackRestoration": "exact",
            "plan": plan,
            "artifacts": {
                "promotionMaterial": (base / "promotion").relative_to(repo).as_posix(),
                "rollbackMaterial": (base / "rollback").relative_to(repo).as_posix(),
            },
        }
    )


def _fake_command_runner(command, *, cwd, **_kwargs):
    repo = Path(cwd)
    joined = " ".join(str(item) for item in command)
    if "mcel_acceptance_runner.py" in joined:
        path = repo / "runtime/reports/mcel-acceptance/apps/contract-workbench/mcel-acceptance-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "passed": True}), encoding="utf-8")
    elif "mcel_application_observation_runner.py" in joined:
        path = repo / "runtime/reports/mcel-observation/apps/contract-workbench/mcel-operation-observation-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "ok": True}), encoding="utf-8")
    elif "mcel_app_prove.py" in joined:
        path = repo / "runtime/reports/mcel-app-proof/apps/contract-workbench/mcel-app-proof-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "truthStatus": "semantic-runtime-proven",
                    "stages": {
                        "acceptanceEvidence": {"status": "pass"},
                        "browserObservation": {"status": "pass"},
                        "repositoryBinding": {"status": "exact"},
                    },
                    "intentCoverage": {
                        "status": "ir-native",
                        "passed": True,
                        "declaredIntentCount": 7,
                        "coveredIntentCount": 7,
                        "declaredScenarioCount": 14,
                        "observedScenarioCount": 14,
                        "effectAccounting": {
                            "status": "closed",
                            "declaredEffectCount": 18,
                            "closedEffectCount": 18,
                        },
                        "capabilityAccounting": {
                            "status": "closed",
                            "declaredCapabilityCount": 1,
                            "streamedOperationCount": 1,
                            "cancellableOperationCount": 1,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
    return subprocess.CompletedProcess(command, 0, stdout="pass\n", stderr=None)


def _execute(repo: Path, **kwargs):
    return execute_workbench_promotion(
        repo_root=repo,
        fixture_ir_path=FIXTURE,
        transaction_root=repo / "runtime/state/mcel/application-promotions/contract-workbench",
        report_root=repo / "runtime/reports/mcel-application-promotions/contract-workbench",
        rehearsal_report_root=repo / "runtime/reports/rehearsal",
        rehearsal_runner=_rehearsal_runner,
        command_runner=_fake_command_runner,
        **kwargs,
    )


def test_execute_workbench_promotion_is_idempotent_for_promoted_fixture(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    result = _execute(repo)
    payload = result.to_dict()
    assert result.valid is True
    assert result.status == "already-promoted"
    assert payload["promotionExecuted"] is False
    assert payload["alreadyPromoted"] is True
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["rollbackAvailable"] is False


def test_execute_workbench_promotion_commits_and_exactly_rolls_back(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    before = _tree_snapshot(repo / "mcel_apps/contract-workbench")
    result = _execute(repo, force_repromotion=True)
    payload = result.to_dict()
    assert result.valid is True
    assert payload["promotionExecuted"] is True
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["derivedArtifactAuthority"] == "mcel.workbench.portable-ir-projection.v1"
    assert payload["legacyPackageAuthority"] == "retired"
    assert payload["truthStatus"] == "semantic-runtime-proven"
    assert payload["semanticFingerprint"] == SEMANTIC
    assert payload["rollbackAvailable"] is True
    manifest = json.loads(
        (repo / "mcel_apps/contract-workbench/mcel.app.json").read_text(encoding="utf-8")
    )
    assert manifest["authoring"]["status"] == "dsl-authoritative"
    assert (repo / "mcel_apps/contract-workbench/mcel.generated.json").is_file()

    rollback = rollback_workbench_promotion(
        payload["transactionId"],
        repo_root=repo,
        transaction_root=repo / "runtime/state/mcel/application-promotions/contract-workbench",
        report_root=repo / "runtime/reports/mcel-application-promotions/contract-workbench",
    )
    assert rollback.valid is True
    assert rollback.report["restoration"] == "exact"
    assert _tree_snapshot(repo / "mcel_apps/contract-workbench") == before


def test_workbench_post_apply_failure_automatically_restores_legacy_package(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    before = _tree_snapshot(repo / "mcel_apps/contract-workbench")

    def fail(stage: str) -> None:
        if stage == "applied":
            raise RuntimeError("injected post-apply failure")

    result = _execute(repo, failure_injector=fail, force_repromotion=True)
    assert result.valid is False
    assert _tree_snapshot(repo / "mcel_apps/contract-workbench") == before
    manifest = json.loads(
        (repo / "mcel_apps/contract-workbench/mcel.app.json").read_text(encoding="utf-8")
    )
    assert manifest["authoring"]["status"] == "dsl-authoritative"


def test_workbench_rollback_refuses_later_protected_mcel_drift(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    result = _execute(repo, force_repromotion=True)
    assert result.valid is True
    protected = repo / "main_computer/mcel_application_ir.py"
    protected.write_text(protected.read_text(encoding="utf-8") + "\n# later work\n", encoding="utf-8")
    rollback = rollback_workbench_promotion(
        result.report["transactionId"],
        repo_root=repo,
        transaction_root=repo / "runtime/state/mcel/application-promotions/contract-workbench",
        report_root=repo / "runtime/reports/mcel-application-promotions/contract-workbench",
    )
    assert rollback.valid is False
    assert rollback.report["restoration"] == "blocked"
    assert any(
        item["code"] == "MCEL_WORKBENCH_PROMOTION_ROLLBACK_FAILED"
        for item in rollback.diagnostics
    )


def test_generic_dispatch_executes_and_rolls_back_workbench(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    result = execute_application_promotion(
        app_id="contract-workbench",
        repo_root=repo,
        fixture_ir_path=FIXTURE,
        transaction_root=repo / "runtime/state/mcel/application-promotions/contract-workbench",
        report_root=repo / "runtime/reports/mcel-application-promotions/contract-workbench",
        rehearsal_report_root=repo / "runtime/reports/rehearsal",
        rehearsal_runner=_rehearsal_runner,
        command_runner=_fake_command_runner,
        force_repromotion=True,
    )
    assert result.valid is True
    raw = result.to_dict()["result"]
    assert raw["promotionExecuted"] is True
    rollback = rollback_application_promotion(
        raw["transactionId"],
        app_id="contract-workbench",
        repo_root=repo,
        transaction_root=repo / "runtime/state/mcel/application-promotions/contract-workbench",
        report_root=repo / "runtime/reports/mcel-application-promotions/contract-workbench",
    )
    assert rollback.valid is True
    assert rollback.to_dict()["result"]["restoration"] == "exact"
