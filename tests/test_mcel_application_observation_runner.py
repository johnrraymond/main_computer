from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from main_computer import mcel_application_observation_runner as runner
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_evidence_provenance import build_repository_provenance
from mcel_dsl_authoring_harness import require_package_acceptance_case, surface_bundle_cases


ROOT = Path(__file__).resolve().parents[1]


def _observation_case():
    cases = surface_bundle_cases(ROOT)
    if cases:
        selected = cases[0]
        return selected.app_id, selected.record, selected.surface_bundle
    selected = require_package_acceptance_case(ROOT)
    return selected.app_id, selected.record, None


def _record_projection_provenance(app_id: str):
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == app_id)
    projection = next(item for item in build_runtime_projection_set(ROOT).projections if item.app_id == app_id)
    provenance = build_repository_provenance(ROOT)
    return catalog, record, projection, provenance


def _surface_id(app_id: str, bundle: dict | None) -> str:
    if bundle and bundle.get("surfaceId"):
        return str(bundle["surfaceId"])
    return f"{app_id}.surface.primary"


def test_observation_runner_composes_app_scoped_operation_report(monkeypatch) -> None:
    app_id, _case_record, bundle = _observation_case()
    _catalog, record, projection, provenance = _record_projection_provenance(app_id)
    surface_id = _surface_id(app_id, bundle)

    def fake_browser(**kwargs):
        assert kwargs["app_id"] == app_id
        assert kwargs["scenario_linked"] is False
        return {
            "url": f"http://localhost/mcel-package-host.html?app={app_id}",
            "browser": {"engine": "playwright-chromium", "version": "test", "headless": True},
            "operationResult": {"ok": True, "status": "committed", "operationId": f"{app_id}.operation.observation-1"},
            "observation": {
                "schema": "mcel.application-operation-observation.v1",
                "status": "pass",
                "ok": True,
                "operationId": f"{app_id}.operation.observation-1",
                "packageFingerprint": record.fingerprint,
                "runtimeProjectionFingerprint": projection.fingerprint,
                "repositoryFingerprint": provenance["fingerprint"],
                "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
            },
            "surfaceConformance": {
                "status": "pass",
                "valid": True,
                "surfaceId": surface_id,
                "requiredLayerStatuses": {
                    "semantic-surface": "pass",
                    "layout-grammar": "pass",
                    "runtime-ownership": "pass",
                    "runtime-visual-fit": "pass",
                    "diagnostic-no-throw": "pass",
                },
            },
        }

    monkeypatch.setattr(runner, "_scenario_linked_observation", lambda _repo, _record: False)
    monkeypatch.setattr(runner, "_run_browser", fake_browser)
    report = runner.run_observation(repo=ROOT, app_id=app_id)

    assert report["status"] == "pass"
    assert report["evidenceScope"] == "app-scoped"
    assert report["appId"] == app_id
    assert report["package"]["fingerprint"] == record.fingerprint
    assert report["observation"]["runtimeProjectionFingerprint"] == projection.fingerprint
    assert report["operations"] == 1
    assert report["passedOperations"] == 1


def test_observation_runner_markdown_exposes_independent_comparisons() -> None:
    markdown = runner._render_markdown(
        {
            "status": "pass",
            "appId": "sample-app",
            "browser": {"engine": "playwright-chromium"},
            "package": {"fingerprint": "sha256:package"},
            "repositoryProvenance": {"fingerprint": "repo"},
            "observation": {
                "operationId": "sample-app.operation-1",
                "runtimeProjectionFingerprint": "sha256:projection",
                "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
            },
            "surfaceConformance": {
                "status": "pass",
                "valid": True,
                "surfaceId": "sample-app.surface.primary",
                "requiredLayerStatuses": {
                    "semantic-surface": "pass",
                    "layout-grammar": "pass",
                    "runtime-ownership": "pass",
                    "runtime-visual-fit": "pass",
                    "diagnostic-no-throw": "pass",
                },
            },
        }
    )

    assert "Surface conformance" in markdown
    assert "Canonical/browser state: `True`" in markdown
    assert "Visible receipt: `True`" in markdown
    assert "Surface identity: `True`" in markdown


def test_observation_runner_accepts_scenario_linked_report(monkeypatch) -> None:
    app_id, _case_record, bundle = _observation_case()
    _catalog, record, projection, provenance = _record_projection_provenance(app_id)
    surface_id = _surface_id(app_id, bundle)

    def fake_browser(**kwargs):
        assert kwargs["app_id"] == app_id
        assert kwargs["scenario_linked"] is True
        return {
            "url": f"http://127.0.0.1/mcel-package-host.html?app={app_id}&observation=1",
            "browser": {"engine": "playwright-chromium", "version": "test", "headless": True},
            "operationResult": {"ok": True, "status": "pass", "operationId": f"{app_id}.browser-scenario-suite"},
            "observation": {
                "schema": "mcel.application-browser-scenario-observation.v1",
                "status": "pass",
                "ok": True,
                "operationId": f"{app_id}.browser-scenario-suite",
                "scenarioCount": 3,
                "passedScenarioCount": 3,
                "failedScenarioCount": 0,
                "packageFingerprint": record.fingerprint,
                "runtimeProjectionFingerprint": projection.fingerprint,
                "repositoryFingerprint": provenance["fingerprint"],
                "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
            },
            "surfaceConformance": {
                "status": "pass",
                "valid": True,
                "surfaceId": surface_id,
                "requiredLayerStatuses": {
                    "semantic-surface": "pass",
                    "layout-grammar": "pass",
                    "runtime-ownership": "pass",
                    "runtime-visual-fit": "pass",
                    "diagnostic-no-throw": "pass",
                },
            },
        }

    monkeypatch.setattr(runner, "_scenario_linked_observation", lambda _repo, _record: True)
    monkeypatch.setattr(runner, "_run_browser", fake_browser)
    report = runner.run_observation(repo=ROOT, app_id=app_id)

    assert report["operations"] == 3
    assert report["passedOperations"] == 3
    assert report["failedOperations"] == 0
    assert report["observation"]["schema"] == "mcel.application-browser-scenario-observation.v1"


def test_scenario_linked_observation_contract_is_detected_from_package_content() -> None:
    record = SimpleNamespace(
        package_root="mcel_apps/sample-scenario",
        contracts={"observation": "mcel_apps/sample-scenario/contracts/observation.js"},
        files={
            "contracts/observation.js": b'export const observation = {currentStatus: "scenario-linked"};'
        },
    )
    assert runner._scenario_linked_observation(ROOT, record) is True
