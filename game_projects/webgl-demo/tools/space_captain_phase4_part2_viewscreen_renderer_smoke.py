#!/usr/bin/env python3
"""Phase 4 Part 2: verify the pure presentation-only bridge viewscreen renderer and production draw cutover."""
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

SCHEMA = "game.spaceCaptainPhase4Part2ViewscreenRendererSmoke.v1"
GAME_ROOT = REPO_ROOT / "game_projects" / "webgl-demo"
SCRIPT_ROOT = GAME_ROOT / "web" / "scripts"
GAME_JSON = GAME_ROOT / "game.json"
CONTRACT_JS = SCRIPT_ROOT / "space-captain-multirate-contract.js"
AUTHORITY_JS = SCRIPT_ROOT / "bridge-encounter-runtime.js"
PROJECTION_JS = SCRIPT_ROOT / "bridge-viewscreen-projection.js"
WRAPPER_JS = SCRIPT_ROOT / "bridge-viewscreen-encounter-runtime.js"
PRESENTATION_JS = SCRIPT_ROOT / "bridge-viewscreen-presentation.js"
RENDERER_JS = SCRIPT_ROOT / "bridge-viewscreen-renderer.js"
LEGACY_RENDERER_JS = SCRIPT_ROOT / "shuttle3d-render-viewscreens.js"
SCENE_VIEWER_JS = SCRIPT_ROOT / "scene-viewer.js"
PROBE_JS = HERE / "space_captain_phase4_part2_viewscreen_renderer_probe.js"


def _source_contract_checks() -> dict[str, bool]:
    game = json.loads(GAME_JSON.read_text(encoding="utf-8"))
    scripts = game["web"]["bundles"]["runtime-before-routing"]
    renderer = RENDERER_JS.read_text(encoding="utf-8")
    scene = SCENE_VIEWER_JS.read_text(encoding="utf-8")
    draw_start = scene.index("const drawViewscreen = (prop) => {")
    draw_end = scene.index("          };", draw_start) + len("          };")
    draw_body = scene[draw_start:draw_end]
    forbidden = (
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
        ".playerFire(",
        "requestAnimationFrame",
        "performance.now",
        "Math.random",
    )
    return {
        "rendererModuleLoadedBeforeSceneViewer": (
            "web/scripts/bridge-viewscreen-renderer.js" in scripts
            and scripts.index("web/scripts/bridge-viewscreen-renderer.js") < scripts.index("web/scripts/scene-viewer.js")
        ),
        "legacyViewscreenRendererNotLoaded": "web/scripts/shuttle3d-render-viewscreens.js" not in scripts,
        "productionDrawPathUsesNewRendererDirectly": (
            "MainComputerBridgeViewscreenRenderer" in draw_body
            and "bridgeViewscreenPresentationSnapshot" in draw_body
            and "renderer.render({" in draw_body
            and "appendMotherShipViewscreenDisplay" not in draw_body
        ),
        "rendererConsumesPresentationOnly": (
            'PRESENTATION_SCHEMA = "game.bridgeViewscreenPresentation.v1"' in renderer
            and "function render({builder, surface, presentation} = {})" in renderer
            and all(token not in renderer for token in forbidden)
        ),
        "rendererUsesPresentationTimeNotWallClock": (
            "presentation.time?.simulationSeconds" in renderer
            and "nowMs" not in renderer
        ),
        "displayPowerHandledOnlyByRendererOutput": "framePresentation.display?.powered === false" in renderer,
        "unsupportedModesFailInsteadOfFallingBack": "BRIDGE_VIEWSCREEN_RENDERER_MODE_NOT_IMPLEMENTED" in renderer,
        "legacyRendererSourceRemovedAfterModePort": not LEGACY_RENDERER_JS.exists(),
    }


def run(*, headed: bool = False, timeout_seconds: float = 30.0) -> dict[str, Any]:
    source_checks = _source_contract_checks()
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CONTRACT_JS, AUTHORITY_JS, PROJECTION_JS, WRAPPER_JS, PRESENTATION_JS, RENDERER_JS, PROBE_JS)
    )
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
        "phase4Part2RendererProbePasses": isinstance(result, dict) and result.get("ok") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "ok": not failed,
        "schema": SCHEMA,
        "checks": checks,
        "failedChecks": failed,
        "chromium": chromium,
    }


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
