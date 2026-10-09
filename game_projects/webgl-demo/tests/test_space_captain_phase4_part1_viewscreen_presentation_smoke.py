from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


GAME_ROOT = Path(__file__).resolve().parents[1]
TOOL = GAME_ROOT / "tools" / "space_captain_phase4_part1_viewscreen_presentation_smoke.py"
SPEC = importlib.util.spec_from_file_location("space_captain_phase4_part1_viewscreen_presentation_smoke", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_phase_four_part_one_source_contract_is_ship_system_not_player_ui() -> None:
    checks = MODULE._source_contract_checks()
    assert checks
    assert all(checks.values()), checks


def test_presentation_source_has_discriminated_immutable_contract() -> None:
    source = MODULE.PRESENTATION_JS.read_text(encoding="utf-8")
    assert 'SCHEMA = "game.bridgeViewscreenPresentation.v1"' in source
    assert 'SYSTEM_STATE_SCHEMA = "game.bridgeViewscreenSystemState.v1"' in source
    assert 'ENCOUNTER_MODE = "encounter"' in source
    for mode in ('"planet"', '"warp-transit"', '"astrometric"', '"idle"'):
        assert mode in source
    assert "deepFreeze" in source
    assert "displayPowered" in source
    assert "selectedMode" in source
    assert "xNormalized" in source
    assert "yNormalized" in source
    assert "projectiles" in source
    assert "impacts" in source
    assert "explosions" in source
    assert "debris" in source


def test_probe_covers_power_and_visibility_independence() -> None:
    source = MODULE.PROBE_JS.read_text(encoding="utf-8")
    for check in (
        "initialPresentationModeIsPreselectedEncounter",
        "displayPowerIsSeparateFromSelectedMode",
        "presentationRunsBeforeBridgeEntry",
        "bridgeEntryDoesNotSelectOrResetPresentation",
        "displayPowerDoesNotChangeAuthority",
        "displayPowerDoesNotResetProjectionCamera",
        "selectedPresentationContinuesWhileDisplayIsOff",
        "repowerShowsCurrentStateNotReplay",
        "presentationFrameIsDeepFrozen",
        "playerShipIsNotExternalVisibleVessel",
        "presentationKeepsAuthoritativeObserver",
        "projectileEffectIsDerivedFromExactShotTimes",
        "impactEffectAndDamageAppearAfterAuthorityImpact",
        "destructionPresentationComesFromAuthorityState",
    ):
        assert check in source


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
def test_phase_four_part_one_passes_in_real_chromium() -> None:
    report = MODULE.run(timeout_seconds=30.0)
    assert report["ok"] is True
    assert report["failedChecks"] == []
    result = report["chromium"]["execution"]["result"]
    assert result["ok"] is True
    assert result["failedChecks"] == []
