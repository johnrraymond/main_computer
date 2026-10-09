#!/usr/bin/env python3
"""Phase 4 Part 3: verify all non-encounter viewscreen modes use the new presentation/renderer stack and legacy code is gone."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
ROOT_TOOLS = REPO_ROOT / "tools"
if str(ROOT_TOOLS) not in sys.path:
    sys.path.insert(0, str(ROOT_TOOLS))

import playwright_chromium_code_smoke as browser_code_smoke

SCHEMA = "game.spaceCaptainPhase4Part3NonEncounterModesSmoke.v1"
GAME_ROOT = REPO_ROOT / "game_projects" / "webgl-demo"
SCRIPT_ROOT = GAME_ROOT / "web" / "scripts"
GAME_JSON = GAME_ROOT / "game.json"
CONTRACT_JS = SCRIPT_ROOT / "space-captain-multirate-contract.js"
PROJECTION_JS = SCRIPT_ROOT / "bridge-viewscreen-projection.js"
PRESENTATION_JS = SCRIPT_ROOT / "bridge-viewscreen-presentation.js"
RENDERER_JS = SCRIPT_ROOT / "bridge-viewscreen-renderer.js"
LEGACY_JS = SCRIPT_ROOT / "shuttle3d-render-viewscreens.js"
SCENE_JS = SCRIPT_ROOT / "scene-viewer.js"
RELOCATION_MAP = GAME_ROOT / "relocation-source-map.json"
PROBE_JS = HERE / "space_captain_phase4_part3_non_encounter_modes_probe.js"

LEGACY_METHODS = (
    "appendMotherShipViewscreenDisplay",
    "appendSystemPlanetDisplay",
    "appendAstrometricSystemDisplay",
    "appendWarpTransitDisplay",
    "appendEnemyShipTacticalDisplay",
)


def _source_contract_checks() -> dict[str, bool]:
    game = json.loads(GAME_JSON.read_text(encoding="utf-8"))
    scripts = game["web"]["bundles"]["runtime-before-routing"]
    presentation = PRESENTATION_JS.read_text(encoding="utf-8")
    renderer = RENDERER_JS.read_text(encoding="utf-8")
    scene = SCENE_JS.read_text(encoding="utf-8")
    relocation = RELOCATION_MAP.read_text(encoding="utf-8")
    forbidden_renderer = (
        "shipState",
        "bridgeEncounterRuntime",
        "bridgeViewscreenEncounterRuntime",
        "navigationSnapshot",
        "astrometricSnapshot",
        "enemyShipHullPercent",
        "enemyShipDisabled",
        "bridgeTacticalShotAgeMs",
        "bridgeTacticalImpactAgeMs",
        "bridgeViewscreenTrackingActive",
        ".advance(",
        ".command(",
        "requestAnimationFrame",
        "performance.now",
        "Math.random",
    )
    return {
        "allFiveModesDeclared": all(f'"{mode}"' in presentation for mode in ("encounter", "planet", "warp-transit", "astrometric", "idle")),
        "presentationSelectsOneModeFromGameSources": "function selectModeForSources(" in presentation,
        "planetPresentationImplemented": "function buildPlanet(" in presentation,
        "warpPresentationImplemented": "function buildWarpTransit(" in presentation,
        "astrometricPresentationImplemented": "function buildAstrometric(" in presentation,
        "idlePresentationImplemented": "function buildIdle(" in presentation,
        "rendererImplementsEveryMode": all(f'case "{mode}"' in renderer for mode in ("encounter", "planet", "warp-transit", "astrometric", "idle")),
        "rendererConsumesPresentationOnly": all(token not in renderer for token in forbidden_renderer),
        "sceneRoutesModeBeforePresentation": "selectModeForSources({" in scene and "system.select?.(selectedMode, simulationSeconds);" in scene,
        "legacyViewscreenRendererDeleted": not LEGACY_JS.exists(),
        "legacyViewscreenRendererNotLoaded": "web/scripts/shuttle3d-render-viewscreens.js" not in scripts,
        "legacyViewscreenMethodsRemovedFromScene": all(method not in scene for method in LEGACY_METHODS),
        "noLegacyRendererModuleCallRemains": '"viewscreens"' not in scene,
        "relocationMapCannotResurrectLegacyRenderer": "shuttle3d-render-viewscreens.js" not in relocation,
    }


def run(*, headed: bool = False, timeout_seconds: float = 30.0) -> dict[str, Any]:
    source_checks = _source_contract_checks()
    source = "\n".join(path.read_text(encoding="utf-8") for path in (CONTRACT_JS, PROJECTION_JS, PRESENTATION_JS, RENDERER_JS, PROBE_JS))
    chromium = browser_code_smoke.run_smoke(
        source=source,
        headed=headed,
        viewport=(1280, 800),
        timeout_seconds=timeout_seconds,
        fail_on_console_error=True,
    )
    result = ((chromium.get("execution") or {}).get("result")) or {}
    checks = {
        **source_checks,
        "playwrightChromiumHarnessPasses": chromium.get("ok") is True,
        "phase4Part3NonEncounterProbePasses": isinstance(result, dict) and result.get("ok") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {"ok": not failed, "schema": SCHEMA, "checks": checks, "failedChecks": failed, "chromium": chromium}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    ns = parser.parse_args(argv)
    if ns.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    try:
        report = run(headed=bool(ns.headed), timeout_seconds=float(ns.timeout_seconds))
    except Exception as exc:
        report = {"ok": False, "schema": SCHEMA, "error": f"{type(exc).__name__}: {exc}"}
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if ns.output:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
