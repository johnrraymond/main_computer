from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


TOOL = Path(__file__).resolve().parents[1] / "tools" / "space_captain_physics_to_viewport_chromium_smoke.py"
SPEC = importlib.util.spec_from_file_location("space_captain_physics_to_viewport_chromium_smoke", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_fixture_contains_authoritative_and_view_samples() -> None:
    fixture = MODULE.build_fixture(time_step_seconds=5.0, physics_step_seconds=0.1, reference_view_sample_hz=60.0)
    assert fixture["ok"] is True
    assert fixture["metrics"]["timeStepSeconds"] == 5.0
    assert fixture["metrics"]["physicsStepSeconds"] == 0.1
    assert fixture["metrics"]["referenceViewportSampleHz"] == 60.0
    assert fixture["metrics"]["browserRenderCadence"] == "requestAnimationFrame"
    assert fixture["physicsSamples"]
    assert fixture["viewportFrames"]
    assert fixture["viewScreenSamples"]


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        sys.path.insert(0, str(MODULE.GENERIC_RUNNER_ROOT))
        import playwright_chromium_code_smoke as generic
    except Exception:
        return False
    try:
        with sync_playwright() as playwright:
            launched = generic.launch_chromium(playwright, headed=False)
            launched.browser.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium is not available in this environment")
def test_real_chromium_reproduces_physics_to_viewport_cycle() -> None:
    report = MODULE.run(
        time_step_seconds=5.0,
        physics_step_seconds=0.1,
        reference_view_sample_hz=60.0,
        timeout_seconds=30.0,
    )
    assert report["ok"] is True
    result = report["chromium"]["execution"]["result"]
    assert result["ok"] is True
    assert result["checks"]["chromiumRafRunsFasterThanPhysics"] is True
    assert result["checks"]["browserPredictionTracksAuthoritativeTrajectory"] is True
    assert result["checks"]["impactReanchorDoesNotTeleportPosition"] is True
    assert result["checks"]["tacticalBoundaryDoesNotSnapVelocity"] is True
    assert result["checks"]["softLockRetainsEnemy"] is True
    assert result["checks"]["cameraHasNoFrameSnaps"] is True
