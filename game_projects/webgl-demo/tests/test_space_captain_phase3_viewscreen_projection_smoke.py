from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


GAME_ROOT = Path(__file__).resolve().parents[1]
TOOL = GAME_ROOT / "tools" / "space_captain_phase3_viewscreen_projection_smoke.py"
SPEC = importlib.util.spec_from_file_location("space_captain_phase3_viewscreen_projection_smoke", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_phase_three_probe_encodes_pure_projection_contract() -> None:
    source = MODULE.PROBE_JS.read_text(encoding="utf-8")
    for check in (
        "projectionReadLeavesAuthorityObjectUnchanged",
        "projectionIsDeterministicForSameInputs",
        "projectionPredictsFromAuthorityAnchor",
        "cameraStateIsExplicitOutputNotInputMutation",
        "staleAuthorityProjectionIsRejected",
        "projectionCarriesNoCombatMutationApi",
        "renderCadenceDoesNotChangeAuthority",
        "cameraOriginIsAuthoritativeAcrossCadences",
        "cameraAtRealShipPosition",
        "cameraTracksMovedShipExactly",
        "targetRelativeWorldPositionIsCorrect",
        "movingShipChangesRangeNotWorldTarget",
        "translatingEntireWorldPreservesView",
        "rotatingViewDoesNotTranslateCamera",
        "objectBehindCameraIsExcluded",
        "missingAuthoritativeObserverIsRejected",
        "transitionalWrapperDelegatesToProjection",
    ):
        assert check in source


def test_projection_source_has_no_authority_mutation_surface() -> None:
    source = MODULE.PROJECTION_JS.read_text(encoding="utf-8")
    assert 'SCHEMA = "game.bridgeViewscreenProjection.v1"' in source
    assert 'AUTHORITY_SCHEMA = "game.bridgeEncounterAuthorityState.v1"' in source
    assert "BRIDGE_VIEWSCREEN_PROJECTION_AUTHORITY_STALE" in source
    for forbidden in (".advance(", ".command(", ".playerFire(", "_integrateTo(", "_applyImpact"):
        assert forbidden not in source


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
def test_phase_three_projection_passes_in_real_chromium() -> None:
    report = MODULE.run(timeout_seconds=30.0)
    assert report["ok"] is True
    assert report["checks"]["playwrightChromiumHarnessPasses"] is True
    assert report["checks"]["phase3ProjectionProbePasses"] is True
    result = report["chromium"]["execution"]["result"]
    assert result["ok"] is True
    assert result["failedChecks"] == []
