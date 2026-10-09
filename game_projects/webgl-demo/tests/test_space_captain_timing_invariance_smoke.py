from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


GAME_ROOT = Path(__file__).resolve().parents[1]
TOOL = GAME_ROOT / "tools" / "space_captain_timing_invariance_smoke.py"
SPEC = importlib.util.spec_from_file_location("space_captain_timing_invariance_smoke", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_default_phase_zero_matrix_proves_python_timing_coherence() -> None:
    report = MODULE.run(skip_chromium=True)
    assert report["ok"] is True
    assert report["configuredEnvelope"]["timingPairCount"] == 15
    assert report["checks"]["everyTimingPairProducesCoherentEncounter"] is True
    assert report["checks"]["physicsResolutionChangesConvergeOnSameSolution"] is True
    assert report["checks"]["tacticalCadenceChangesRemainSemanticallyCoherent"] is True
    assert all(row["ok"] for row in report["pythonMatrix"])
    assert all(row["ok"] for row in report["physicsConvergenceByTacticalSlice"])


def test_runtime_authority_is_grid_anchored_not_render_anchored() -> None:
    source = MODULE.AUTHORITY_JS.read_text(encoding="utf-8")
    assert 'eventKind = "render-only"' not in source
    assert "const nextTime = Math.min(this.nextPhysicsAtSeconds, nextBoundary, nextImpact);" in source
    assert "this.authorityUpdateCount += 1;" in source


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
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
def test_chromium_render_cadence_does_not_change_authoritative_solution() -> None:
    report = MODULE.run(
        tactical_slices=(1.0, 3.0, 5.0),
        physics_steps=(0.05, 0.10, 0.20),
        render_hz_values=(30.0, 60.0, 120.0, 165.0),
        timeout_seconds=30.0,
    )
    assert report["ok"] is True
    assert report["checks"]["chromiumTimingMatrixPasses"] is True
    assert report["checks"]["renderCadenceDoesNotChangeAuthoritativeSolution"] is True
    chromium_result = report["chromiumMatrix"]["execution"]["result"]
    assert chromium_result["rowCount"] == 36
    assert all(row["ok"] for row in chromium_result["renderInvariance"])
