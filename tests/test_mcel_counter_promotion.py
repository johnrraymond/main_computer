from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from main_computer.mcel_counter_candidate_projection import project_counter_candidate
from main_computer.mcel_counter_promotion import (
    execute_counter_promotion,
    rollback_counter_promotion,
)
from main_computer.mcel_counter_promotion_rehearsal import (
    _build_promotion_plan,
    _stage_material,
    _tree_snapshot,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
DSL = ROOT / "mcel_apps/contract-counter/application.js"
FIXTURE = ROOT / "tests/fixtures/mcel_application_ir/contract-counter.ir.json"
SEMANTIC = "sha256:a9dbe6b7ec49978d313f18836b30c3394539c18f29430c3a7553837bc46eb0ef"
SOURCE = "sha256:54e16c919103023872d62eb258871d0d61b65a5754534c0bd85bb122c4a3cfa2"


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


def _restore_legacy_counter(repo: Path) -> None:
    package = repo / "mcel_apps/contract-counter"
    (package / "application.js").unlink(missing_ok=True)
    (package / "mcel.generated.json").unlink(missing_ok=True)
    manifest_path = package / "mcel.app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("authoring", None)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


class _RehearsalResult:
    valid = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def to_dict(self) -> dict:
        return dict(self.payload)


def _rehearsal_runner(**kwargs):
    repo = Path(kwargs["repo_root"])
    dsl = compile_dsl_application(DSL, compare_ir_path=FIXTURE)
    candidate_root = repo / "runtime/state/mcel/compiler-candidates"
    projection = project_counter_candidate(
        dsl_source_path=DSL,
        fixture_ir_path=FIXTURE,
        live_package_root=repo / "mcel_apps/contract-counter",
        candidate_root=candidate_root,
        write_candidate=True,
    )
    assert dsl.valid and projection.valid and projection.candidate_directory
    evidence = {
        "status": "pass",
        "truthStatus": "semantic-runtime-proven",
        "candidate": {
            "semanticFingerprint": SEMANTIC,
            "sourceBindingFingerprint": SOURCE,
        },
        "authority": {"evidenceReused": False},
    }
    plan, promoted = _build_promotion_plan(
        repo=repo,
        live_package=repo / "mcel_apps/contract-counter",
        candidate_package=projection.candidate_directory / "package/mcel_apps/contract-counter",
        dsl_source_path=DSL,
        semantic_fingerprint=SEMANTIC,
        source_binding_fingerprint=SOURCE,
        evidence_payload=evidence,
    )
    base = projection.candidate_directory / "promotion-rehearsal"
    promotion = base / "promotion"
    rollback = base / "rollback"
    _stage_material(plan, promoted, repo / "mcel_apps/contract-counter", promotion, rollback)
    return _RehearsalResult({
        "valid": True,
        "status": "pass",
        "promotionEligible": True,
        "rollbackRestoration": "exact",
        "plan": plan,
        "artifacts": {
            "promotionMaterial": promotion.relative_to(repo).as_posix(),
            "rollbackMaterial": rollback.relative_to(repo).as_posix(),
        },
    })


def _receipt(*, ok: bool, status: str, code: str) -> dict:
    return {"ok": ok, "status": status, "code": code, "receipt": {"status": status, "code": code}}


def _node_probe() -> dict:
    return {
        "schema": "mcel.counter-effect-probe.v1",
        "operations": [
            {"operationId": "candidate-increment", "before": {"count": 0, "revision": 0}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 1, "revision": 1}},
            {"operationId": "candidate-stale", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"), "after": {"count": 1, "revision": 1}},
            {"operationId": "candidate-direct-set", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"), "after": {"count": 1, "revision": 1}},
            {"operationId": "candidate-reset", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 0, "revision": 2}},
        ],
    }


def _browser_probe() -> dict:
    passing = {"status": "pass", "comparison": {"surfaceMatches": True}}
    return {
        "schema": "mcel.counter-browser-effect-probe.v1",
        "operations": [
            {"operationId": "candidate-browser-increment", "before": {"count": 0, "revision": 0}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 1, "revision": 1}, "visible": "1", "observation": passing},
            {"operationId": "candidate-browser-stale", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"), "after": {"count": 1, "revision": 1}, "visible": "1"},
            {"operationId": "candidate-browser-direct-set", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"), "after": {"count": 1, "revision": 1}, "visible": "1"},
            {"operationId": "candidate-browser-reset", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 0, "revision": 2}, "visible": "0", "observation": passing},
        ],
    }


def _fake_command_runner(command, *, cwd, **_kwargs):
    repo = Path(cwd)
    joined = " ".join(str(item) for item in command)
    if "mcel_acceptance_runner.py" in joined:
        path = repo / "runtime/reports/mcel-acceptance/apps/contract-counter/mcel-acceptance-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "passed": True}), encoding="utf-8")
    elif "mcel_application_observation_runner.py" in joined:
        path = repo / "runtime/reports/mcel-observation/apps/contract-counter/mcel-operation-observation-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "pass", "ok": True}), encoding="utf-8")
    elif "mcel_app_prove.py" in joined:
        path = repo / "runtime/reports/mcel-app-proof/apps/contract-counter/mcel-app-proof-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "status": "pass",
            "truthStatus": "semantic-runtime-proven",
            "stages": {"repositoryBinding": {"status": "exact"}},
        }), encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, stdout="pass\n", stderr=None)


def _execute(repo: Path, **kwargs):
    return execute_counter_promotion(
        repo_root=repo,
        fixture_ir_path=FIXTURE,
        transaction_root=repo / "runtime/state/mcel/counter-promotions",
        report_root=repo / "runtime/reports/mcel-counter-promotions",
        rehearsal_report_root=repo / "runtime/reports/rehearsal",
        rehearsal_runner=_rehearsal_runner,
        command_runner=_fake_command_runner,
        node_probe_runner=lambda _repo: _node_probe(),
        browser_probe_runner=lambda _repo, _headed: _browser_probe(),
        **kwargs,
    )


def test_execute_promotion_commits_dsl_authority_and_supports_exact_rollback(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    _restore_legacy_counter(repo)
    before = _tree_snapshot(repo / "mcel_apps/contract-counter")
    result = _execute(repo)
    payload = result.to_dict()
    assert result.valid is True
    assert payload["promotionExecuted"] is True
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["legacyPackageAuthority"] == "retired"
    assert payload["truthStatus"] == "semantic-runtime-proven"
    assert payload["rollbackAvailable"] is True
    manifest = json.loads((repo / "mcel_apps/contract-counter/mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["authoring"]["status"] == "dsl-authoritative"
    assert (repo / "mcel_apps/contract-counter/application.js").is_file()
    assert (repo / "mcel_apps/contract-counter/mcel.generated.json").is_file()

    rollback = rollback_counter_promotion(
        payload["transactionId"],
        repo_root=repo,
        transaction_root=repo / "runtime/state/mcel/counter-promotions",
        report_root=repo / "runtime/reports/mcel-counter-promotions",
    )
    assert rollback.valid is True
    assert rollback.report["restoration"] == "exact"
    assert _tree_snapshot(repo / "mcel_apps/contract-counter") == before


def test_failed_post_apply_check_automatically_restores_legacy_package(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    _restore_legacy_counter(repo)
    before = _tree_snapshot(repo / "mcel_apps/contract-counter")

    def fail(stage: str) -> None:
        if stage == "applied":
            raise RuntimeError("injected post-apply failure")

    result = _execute(repo, failure_injector=fail)
    assert result.valid is False
    assert _tree_snapshot(repo / "mcel_apps/contract-counter") == before
    manifest = json.loads((repo / "mcel_apps/contract-counter/mcel.app.json").read_text(encoding="utf-8"))
    assert "authoring" not in manifest


def test_rollback_refuses_to_overwrite_later_protected_source_drift(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path / "repo")
    _restore_legacy_counter(repo)
    result = _execute(repo)
    assert result.valid is True
    domain = repo / "runtime/build/mcel/web/applications/mcel-packages/contract-counter/contracts/domain.js"
    domain.write_text(domain.read_text(encoding="utf-8") + "\n// later protected change\n", encoding="utf-8")

    rollback = rollback_counter_promotion(
        result.report["transactionId"],
        repo_root=repo,
        transaction_root=repo / "runtime/state/mcel/counter-promotions",
        report_root=repo / "runtime/reports/mcel-counter-promotions",
    )
    assert rollback.valid is False
    assert any(item["code"] == "MCEL_COUNTER_PROMOTION_ROLLBACK_FAILED" for item in rollback.diagnostics)
    assert "later protected change" in domain.read_text(encoding="utf-8")
