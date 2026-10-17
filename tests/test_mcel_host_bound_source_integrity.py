"""Source-integrity regression for the generic host-bound browser observer.

No real browser is needed: the test verifies repository traversal and write
accounting independently of Playwright, which has separate acceptance tests.
"""

from __future__ import annotations

from pathlib import Path

from main_computer.mcel_calculator_host_bound_profile import (
    build_calculator_browser_observation_profile,
)
from main_computer.mcel_host_bound_browser_observation import (
    run_host_bound_browser_observation,
)


def _browser_result(profile):
    return {
        "freshChromiumObservation": True,
        "generatedAdapterMounted": True,
        "generatedAdapterModuleExecuted": True,
        "runtimeFacadeAvailable": True,
        "intentResults": [
            {
                "intentName": name,
                "status": "pass",
                "parity": {"status": "pass"},
                "network": {"requestCount": 0 if name in profile.local_intents else 1},
            }
            for name in profile.intent_payloads
        ],
    }


def test_observer_does_not_rglob_entire_repo_and_ignores_mutable_state(
    tmp_path: Path, monkeypatch,
) -> None:
    from main_computer import mcel_host_bound_browser_observation as browser

    profile = build_calculator_browser_observation_profile()
    app_source = tmp_path / "mcel_apps" / "calculator" / "application.js"
    app_source.parent.mkdir(parents=True)
    app_source.write_text("export default 'source';\n", encoding="utf-8")
    runtime_state = tmp_path / "runtime" / "state" / "large-ignored.bin"
    runtime_state.parent.mkdir(parents=True)
    runtime_state.write_bytes(b"before")

    def forbidden_rglob(self, pattern):
        raise AssertionError(f"unbounded Path.rglob called: {self} {pattern}")

    monkeypatch.setattr(Path, "rglob", forbidden_rglob)

    def fake_browser(_profile, *, repo, headed, operation_prefix):
        runtime_state.write_bytes(b"after")
        return _browser_result(_profile)

    monkeypatch.setattr(browser, "_run_playwright", fake_browser)
    observation = run_host_bound_browser_observation(profile, repo_root=tmp_path)

    assert observation.valid is True
    assert observation.report["checks"]["sourceTreeUnchanged"] is True
    assert observation.report["repositoryWrites"]["sourceTreeUnchanged"] is True
    assert observation.report["repositoryProvenance"]["fileCount"] == 1
    assert "mcel_apps" in observation.report["repositoryProvenance"]["sourceRoots"]


def test_observer_rejects_changes_to_authoritative_app_source(
    tmp_path: Path, monkeypatch,
) -> None:
    from main_computer import mcel_host_bound_browser_observation as browser

    profile = build_calculator_browser_observation_profile()
    app_source = tmp_path / "mcel_apps" / "calculator" / "application.js"
    app_source.parent.mkdir(parents=True)
    app_source.write_text("original", encoding="utf-8")

    def fake_browser(_profile, *, repo, headed, operation_prefix):
        app_source.write_text("modified", encoding="utf-8")
        return _browser_result(_profile)

    monkeypatch.setattr(browser, "_run_playwright", fake_browser)
    observation = run_host_bound_browser_observation(profile, repo_root=tmp_path)

    assert observation.valid is False
    assert observation.report["status"] == "fail"
    assert observation.report["checks"]["sourceTreeUnchanged"] is False
    assert any(item["semanticPath"] == "$checks.sourceTreeUnchanged" for item in observation.diagnostics)
