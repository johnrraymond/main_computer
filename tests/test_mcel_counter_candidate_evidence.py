from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from pathlib import Path

from main_computer.mcel_counter_candidate_evidence import (
    counter_explicit_package_candidate_evidence_profile,
    _build_effect_accounting,
    _evaluate_browser_effect_probe,
    _prepare_workspace,
    run_counter_candidate_evidence,
)
import main_computer.mcel_counter_candidate_evidence as counter_evidence
import main_computer.mcel_counter_effect_probe as counter_effect_probe
from main_computer.mcel_counter_candidate_projection import project_counter_candidate
from main_computer.mcel_counter_reference_fixture_profile import APP_ID, FIXTURE_ROLE

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mcel_application_ir/contract-counter.ir.json"
LIVE = ROOT / "mcel_apps/contract-counter"


def _acceptance() -> dict:
    return {"status": "pass", "passed": True}


def _observation() -> dict:
    return {"status": "pass", "ok": True}


def _receipt(*, ok: bool, status: str, code: str) -> dict:
    return {"ok": ok, "status": status, "code": code}


def _node_probe() -> dict:
    return {
        "schema": "mcel.counter-effect-probe.v1",
        "operations": [
            {
                "operationId": "candidate-increment",
                "before": {"count": 0, "revision": 0},
                "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"),
                "after": {"count": 1, "revision": 1},
            },
            {
                "operationId": "candidate-stale",
                "before": {"count": 1, "revision": 1},
                "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"),
                "after": {"count": 1, "revision": 1},
            },
            {
                "operationId": "candidate-direct-set",
                "before": {"count": 1, "revision": 1},
                "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"),
                "after": {"count": 1, "revision": 1},
            },
            {
                "operationId": "candidate-reset",
                "before": {"count": 1, "revision": 1},
                "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"),
                "after": {"count": 0, "revision": 2},
            },
        ],
    }


def _browser_probe() -> dict:
    passing_observation = {"status": "pass", "comparison": {"surfaceMatches": True}}
    return {
        "schema": "mcel.counter-browser-effect-probe.v1",
        "operations": [
            {
                "operationId": "candidate-browser-increment",
                "before": {"count": 0, "revision": 0},
                "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"),
                "after": {"count": 1, "revision": 1},
                "visible": "1",
                "observation": passing_observation,
            },
            {
                "operationId": "candidate-browser-stale",
                "before": {"count": 1, "revision": 1},
                "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"),
                "after": {"count": 1, "revision": 1},
                "visible": "1",
            },
            {
                "operationId": "candidate-browser-direct-set",
                "before": {"count": 1, "revision": 1},
                "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"),
                "after": {"count": 1, "revision": 1},
                "visible": "1",
            },
            {
                "operationId": "candidate-browser-reset",
                "before": {"count": 1, "revision": 1},
                "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"),
                "after": {"count": 0, "revision": 2},
                "visible": "0",
                "observation": passing_observation,
            },
        ],
    }


def test_counter_evidence_uses_reference_fixture_profile() -> None:
    profile = counter_explicit_package_candidate_evidence_profile()
    assert APP_ID == "contract-counter"
    assert FIXTURE_ROLE == "mcel.reference-fixture.explicit-package.counter.v1"
    assert profile.app_id == APP_ID
    assert profile.report_title == "MCEL Counter Reference Fixture Candidate Evidence"
    assert profile.invalid_dsl_message == "DSL compilation did not produce valid Counter fixture IR."


def test_counter_evidence_uses_generic_explicit_package_profile() -> None:
    profile = counter_explicit_package_candidate_evidence_profile()

    assert profile.app_id == "contract-counter"
    assert profile.report_schema == "mcel.counter-candidate-evidence-report.v1"
    assert profile.effect_accounting_filename == "mcel-counter-effect-accounting-report.json"


def test_browser_effect_probe_passes_page_url_as_explicit_evaluate_argument() -> None:
    class FakePage:
        def evaluate(self, expression, argument):
            assert "async ({pageUrl, operationPrefix})" in expression
            assert "url: pageUrl" in expression
            assert argument == {"pageUrl": "http://127.0.0.1:62186/candidate", "operationPrefix": "candidate"}
            return {"url": argument["pageUrl"], "operations": []}

    result = _evaluate_browser_effect_probe(
        FakePage(),
        "http://127.0.0.1:62186/candidate",
    )

    assert result["url"] == "http://127.0.0.1:62186/candidate"


def test_effect_accounting_closes_completed_and_refused_counter_effects() -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = _build_effect_accounting(
        ir=ir,
        acceptance=_acceptance(),
        observation=_observation(),
        node_probe=_node_probe(),
        browser_probe=_browser_probe(),
    )

    assert report["valid"] is True
    assert report["status"] == "closed"
    assert report["declaredEffectCount"] == 4
    assert report["effectInstanceCount"] == 6
    assert report["closedEffectInstanceCount"] == 6
    assert report["unexplainedEffectCount"] == 0
    assert report["directSetCanonicalWriteObserved"] is False
    assert {item["disposition"] for item in report["instances"]} == {
        "completed",
        "refused-before-attempt",
    }


def test_effect_accounting_fails_when_refusal_mutates_canonical_state() -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    node = _node_probe()
    stale = next(item for item in node["operations"] if item["operationId"] == "candidate-stale")
    stale["after"] = {"count": 2, "revision": 2}

    report = _build_effect_accounting(
        ir=ir,
        acceptance=_acceptance(),
        observation=_observation(),
        node_probe=node,
        browser_probe=_browser_probe(),
    )

    assert report["valid"] is False
    assert report["status"] == "open"
    assert "MCEL_COUNTER_EFFECT_REFUSAL_MUTATED_STATE" in {
        item["code"] for item in report["diagnostics"]
    }


def test_workspace_uses_candidate_package_without_changing_live_package(tmp_path: Path) -> None:
    projection = project_counter_candidate(candidate_root=tmp_path / "candidates", write_candidate=True)
    assert projection.valid is True and projection.candidate_directory
    workspace = tmp_path / "workspace"
    candidate_package = projection.candidate_directory / "package/mcel_apps/contract-counter"
    before = {
        path.relative_to(LIVE).as_posix(): path.read_bytes()
        for path in LIVE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    candidate_domain = (candidate_package / "contracts/domain.js").read_bytes()

    _prepare_workspace(ROOT, workspace, candidate_package)

    assert (workspace / "mcel_apps/contract-counter/contracts/domain.js").read_bytes() == candidate_domain
    after = {
        path.relative_to(LIVE).as_posix(): path.read_bytes()
        for path in LIVE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    assert after == before
    assert not (workspace / "runtime").exists()


def test_candidate_evidence_report_is_isolated_and_not_promotion_eligible(tmp_path: Path) -> None:
    report_root = tmp_path / "reports"
    candidate_root = tmp_path / "candidates"

    def fake_command_runner(command, *, cwd, **_kwargs):
        workspace = Path(cwd)
        joined = " ".join(str(item) for item in command)
        if "mcel_acceptance_runner.py" in joined:
            path = workspace / "runtime/reports/mcel-acceptance/apps/contract-counter/mcel-acceptance-report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"status": "pass", "passed": True}), encoding="utf-8")
        elif "mcel_application_observation_runner.py" in joined:
            path = workspace / "runtime/reports/mcel-observation/apps/contract-counter/mcel-operation-observation-report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"status": "pass", "ok": True}), encoding="utf-8")
        elif "mcel_app_prove.py" in joined:
            path = workspace / "runtime/reports/mcel-app-proof/apps/contract-counter/mcel-app-proof-report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "truthStatus": "semantic-runtime-proven",
                        "stages": {
                            "generatedArtifacts": {"status": "pass"},
                            "repositoryBinding": {"status": "exact"},
                        },
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="pass\n", stderr=None)

    result = run_counter_candidate_evidence(
        repo_root=ROOT,
        candidate_root=candidate_root,
        report_root=report_root,
        command_runner=fake_command_runner,
        node_probe_runner=lambda _workspace: _node_probe(),
        browser_probe_runner=lambda _workspace, _headed: _browser_probe(),
    )
    payload = result.to_dict()

    assert result.valid is True
    assert payload["status"] == "pass"
    assert payload["truthStatus"] == "semantic-runtime-proven"
    assert all(item["status"] == "pass" for item in payload["stages"].values())
    assert payload["effectAccounting"]["status"] == "closed"
    assert payload["authority"] == {
        "liveAuthority": "legacy-explicit-package",
        "candidateAuthority": "none",
        "liveApplicationChanged": False,
        "contractsGeneratedInCandidate": True,
        "evidenceReused": False,
        "candidatePromoted": False,
        "promotionEligible": False,
    }
    assert result.output_directory and (result.output_directory / "mcel-candidate-evidence-report.json").is_file()
    assert (result.output_directory / "acceptance/mcel-acceptance-report.json").is_file()
    assert (result.output_directory / "observation/mcel-operation-observation-report.json").is_file()
    assert (result.output_directory / "effects/mcel-counter-effect-accounting-report.json").is_file()
    assert (result.output_directory / "proof/mcel-app-proof-report.json").is_file()


def test_candidate_evidence_failure_does_not_write_to_live_package(tmp_path: Path) -> None:
    before = {
        path.relative_to(LIVE).as_posix(): path.read_bytes()
        for path in LIVE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }

    def failing_runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="forced failure\n", stderr=None)

    result = run_counter_candidate_evidence(
        repo_root=ROOT,
        candidate_root=tmp_path / "candidates",
        report_root=tmp_path / "reports",
        command_runner=failing_runner,
        node_probe_runner=lambda _workspace: _node_probe(),
        browser_probe_runner=lambda _workspace, _headed: _browser_probe(),
    )

    assert result.valid is False
    after = {
        path.relative_to(LIVE).as_posix(): path.read_bytes()
        for path in LIVE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    assert after == before


def test_counter_evidence_wrapper_delegates_effect_probe_to_fixture_module() -> None:
    evidence_source = inspect.getsource(counter_evidence)
    probe_source = inspect.getsource(counter_effect_probe)

    assert "def _run_counter_effect_probe(" not in evidence_source
    assert "def _run_browser_effect_probe(" not in evidence_source
    assert "def _build_effect_accounting(" not in evidence_source
    assert "def _run_counter_effect_probe(" in probe_source
    assert "def _run_browser_effect_probe(" in probe_source
    assert "def _build_effect_accounting(" in probe_source
