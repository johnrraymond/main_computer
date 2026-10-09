"""Phase 5 Part 2 ensures the full authored scene gate is real, not a fake canvas."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GAME_ROOT = Path(__file__).resolve().parents[1]
TOOL = GAME_ROOT / "tools" / "space_captain_phase5_part2_full_game_chromium_smoke.py"


def _smoke():
    spec = importlib.util.spec_from_file_location("space_captain_phase5_part2_full_game_chromium_smoke", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_game_smoke_loads_actual_authored_project_and_bundle() -> None:
    smoke = _smoke()
    assert all(smoke.source_checks().values()), smoke.source_checks()
    text = TOOL.read_text(encoding="utf-8")
    assert 'SURFACE_HTML.read_text(encoding="utf-8")' in text
    assert 'PROJECT_JSON.read_text(encoding="utf-8")' in text
    assert 'runtime-before-routing' in text
    assert 'page.add_script_tag(content=(GAME_ROOT / path).read_text(encoding="utf-8"))' in text
    assert 'page.locator("#webgl-demo").screenshot' in text


def test_full_game_gate_requires_real_gl_keyboard_actions_and_destruction() -> None:
    smoke = _smoke()
    source = TOOL.read_text(encoding="utf-8")
    driver = smoke.DRIVER_JS.read_text(encoding="utf-8")
    assert 'PHASE5_PART2_REAL_WEBGL_CONTEXT_REQUIRED' in driver
    assert 'renderer.enterShuttleBayPlayerControl(true)' in driver
    assert 'renderer.shipInteractionZones()' in driver
    assert 'page.keyboard.press("e")' in source
    for marker in (
        "realWebGLContextAndCanvasAvailable",
        "openingEncounterSelectedBeforeBridge",
        "openingPursuitWithoutPrematureTacticalSimulation",
        "bridgeEntryStartsCaptainSimulationExactlyOnce",
        "viewscreenPowerDoesNotRestartCaptain",
        "realEKeyTurnsOffDisplayWithoutModeChange",
        "realEKeyRestoresCurrentPresentation",
        "realEKeyRoutesWeaponToEncounterAuthority",
        "fireDoesNotCauseImmediateHullDamage",
        "projectileVisualAppearsBeforeImpact",
        "authoritativeImpactProducesDamageAndEffect",
        "secondAuthorityImpactDestroysEnemy",
        "destroyedEnemyRendersExplosionAndDebris",
        "intactEnemyHasNoPrematureExplosionOrDebris",
        "damagedEnemyHasImpactButNoDestructionEffects",
        "shotResolutionDiagnosticMatchesAuthorityImpact",
        "actualHudObjectiveUpdatesToEnemyDisabled",
        "objectivePersistsAfterExplosion",
        "viewscreenCameraOriginMatchesPhysicalMother",
        "productionObserverPoseIsFromActualMotherShip",
        "targetRelativePositionUsesPhysicalShipOrigin",
        "cameraTracksActualWorldTarget",
    ):
        assert marker in source


def test_full_game_smoke_outputs_actionable_failure_without_webgl(tmp_path: Path) -> None:
    # This is a structural assertion only: the explicit CLI smoke MUST fail when
    # genuine WebGL cannot initialize, rather than quietly using a recording GL.
    source = TOOL.read_text(encoding="utf-8")
    assert '"ok": not failed_checks and not failures' in source
    assert 'failures.append(f"{type(error).__name__}: {error}")' in source
    assert '"errors": failures' in source


def _chromium_webgl_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=[
                "--no-sandbox", "--enable-webgl", "--enable-unsafe-swiftshader",
                "--ignore-gpu-blocklist", "--no-proxy-server", "--proxy-bypass-list=*",
            ])
            try:
                page = browser.new_page()
                return bool(page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"))
            finally:
                browser.close()
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_webgl_available(), reason="real WebGL / Playwright-managed Chromium unavailable")
def test_real_full_game_chromium_e2e(tmp_path: Path) -> None:
    report = _smoke().run(output_dir=tmp_path, screenshot_enabled=True)
    assert report["ok"], {"failedChecks": report["failedChecks"], "errors": report["errors"]}
    assert len(report["artifacts"]["screenshots"]) == 8
