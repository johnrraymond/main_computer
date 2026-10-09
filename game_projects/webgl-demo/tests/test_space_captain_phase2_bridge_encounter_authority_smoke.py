from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


GAME_ROOT = Path(__file__).resolve().parents[1]
TOOL = GAME_ROOT / "tools" / "space_captain_phase2_bridge_encounter_authority_smoke.py"
SPEC = importlib.util.spec_from_file_location("space_captain_phase2_bridge_encounter_authority_smoke", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_phase_two_probe_encodes_single_authority_combat_contract() -> None:
    source = MODULE.PROBE_JS.read_text(encoding="utf-8")
    for check in (
        "initialCombatTruthIsOwnedByRuntime",
        "firingDoesNotApplyDamageBeforeImpact",
        "firstImpactAppliesAuthoritativeDamage",
        "secondFireDoesNotMoveTacticalGrid",
        "destructionIsAuthoritativeRuntimeState",
        "destroyedTargetRejectsFurtherFire",
        "snapshotPredictionCannotMutateEncounterTruth",
        "runtimeOwnsProjectileLedger",
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
def test_phase_two_bridge_encounter_authority_passes_in_real_chromium() -> None:
    report = MODULE.run(timeout_seconds=30.0)
    assert report["ok"] is True
    assert report["checks"]["playwrightChromiumHarnessPasses"] is True
    assert report["checks"]["bridgeEncounterAuthorityProbePasses"] is True
    result = report["chromium"]["execution"]["result"]
    assert result["ok"] is True
    assert result["failedChecks"] == []
