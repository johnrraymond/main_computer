from __future__ import annotations

from pathlib import Path

from main_computer import mcel_application_observation_runner as runner
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_evidence_provenance import build_repository_provenance


ROOT = Path(__file__).resolve().parents[1]


def test_observation_runner_builds_package_bound_app_scoped_report(monkeypatch) -> None:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "contract-counter")
    projection = next(item for item in build_runtime_projection_set(ROOT).projections if item.app_id == "contract-counter")
    provenance = build_repository_provenance(ROOT)

    def fake_browser(**_kwargs):
        return {
            "url": "http://localhost/mcel-package-host.html?app=contract-counter",
            "browser": {"engine": "playwright-chromium", "version": "test", "headless": True},
            "operationResult": {"ok": True, "status": "committed", "operationId": "contract-counter.increment.observation-1"},
            "observation": {
                "schema": "mcel.application-operation-observation.v1",
                "status": "pass",
                "ok": True,
                "operationId": "contract-counter.increment.observation-1",
                "packageFingerprint": record.fingerprint,
                "runtimeProjectionFingerprint": projection.fingerprint,
                "repositoryFingerprint": provenance["fingerprint"],
                "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
            },
            "surfaceConformance": {
                "status": "pass",
                "valid": True,
                "surfaceId": "contract-counter.surface.primary",
                "requiredLayerStatuses": {
                    "semantic-surface": "pass",
                    "layout-grammar": "pass",
                    "runtime-ownership": "pass",
                    "runtime-visual-fit": "pass",
                    "diagnostic-no-throw": "pass",
                },
            },
        }

    monkeypatch.setattr(runner, "_run_browser", fake_browser)
    report = runner.run_observation(repo=ROOT, app_id="contract-counter")

    assert report["status"] == "pass"
    assert report["evidenceScope"] == "app-scoped"
    assert report["package"]["fingerprint"] == record.fingerprint
    assert report["observation"]["runtimeProjectionFingerprint"] == projection.fingerprint
    assert report["operations"] == 1
    assert report["passedOperations"] == 1


def test_observation_runner_markdown_exposes_independent_comparisons() -> None:
    markdown = runner._render_markdown(
        {
            "status": "pass",
            "appId": "contract-counter",
            "browser": {"engine": "playwright-chromium"},
            "package": {"fingerprint": "sha256:package"},
            "repositoryProvenance": {"fingerprint": "repo"},
            "surfaceConformance": {"status": "pass", "requiredLayerStatuses": {"semantic-surface": "pass"}},
            "observation": {
                "operationId": "op-1",
                "runtimeProjectionFingerprint": "sha256:projection",
                "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
            },
            "surfaceConformance": {
                "status": "pass",
                "valid": True,
                "surfaceId": "contract-counter.surface.primary",
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
