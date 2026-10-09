#!/usr/bin/env python3
"""Phase 4 Part 1: verify the always-on viewscreen presentation contract and output-power isolation."""
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

SCHEMA = "game.spaceCaptainPhase4Part1ViewscreenPresentationSmoke.v1"
SCRIPT_ROOT = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts"
CONTRACT_JS = SCRIPT_ROOT / "space-captain-multirate-contract.js"
AUTHORITY_JS = SCRIPT_ROOT / "bridge-encounter-runtime.js"
PROJECTION_JS = SCRIPT_ROOT / "bridge-viewscreen-projection.js"
WRAPPER_JS = SCRIPT_ROOT / "bridge-viewscreen-encounter-runtime.js"
PRESENTATION_JS = SCRIPT_ROOT / "bridge-viewscreen-presentation.js"
SCENE_VIEWER_JS = SCRIPT_ROOT / "scene-viewer.js"
GAME_JSON = REPO_ROOT / "game_projects" / "webgl-demo" / "game.json"
PROBE_JS = HERE / "space_captain_phase4_part1_viewscreen_presentation_probe.js"


def _source_contract_checks() -> dict[str, bool]:
    scene = SCENE_VIEWER_JS.read_text(encoding="utf-8")
    presentation = PRESENTATION_JS.read_text(encoding="utf-8")
    game = json.loads(GAME_JSON.read_text(encoding="utf-8"))
    runtime_scripts = game["web"]["bundles"]["runtime-before-routing"]
    return {
        "presentationModuleLoadedBeforeSceneViewer": (
            "web/scripts/bridge-viewscreen-presentation.js" in runtime_scripts
            and runtime_scripts.index("web/scripts/bridge-viewscreen-presentation.js") < runtime_scripts.index("web/scripts/scene-viewer.js")
        ),
        "scenePreselectsEncounterBeforeBridgeEntry": 'initialMode: "encounter"' in scene,
        "sceneEncounterUpdateIsNotBridgeLocationGated": (
            "const onBridge = String(this.shipState?.location || \"\") === \"bridge.deck\";" not in scene[scene.index("updateBridgeViewscreenEncounter"):scene.index("currentSystemPlanet()", scene.index("updateBridgeViewscreenEncounter"))]
        ),
        "sceneMaintainsOneProjectionAndPresentationPerFrame": (
            "projectionRuntime.snapshot(nowMs" in scene
            and "this.bridgeViewscreenProjectionFrame = encounterProjection;" in scene
            and "this.bridgeViewscreenPresentationFrame = system.present(presentationInputs);" in scene
            and "if (this.bridgeViewscreenProjectionFrame) return this.bridgeViewscreenProjectionFrame;" in scene
        ),
        "sceneExposesIndependentDisplayPowerControl": (
            "setBridgeViewscreenDisplayPowered(powered)" in scene
            and "toggleBridgeViewscreenDisplayPower()" in scene
        ),
        "presentationDoesNotKnowPlayerLocation": (
            "bridge.deck" not in presentation
            and "shipState" not in presentation
        ),
        "presentationDoesNotOwnAuthorityMutation": all(token not in presentation for token in (".advance(", ".command(", ".playerFire(")),
    }


def run(*, headed: bool = False, timeout_seconds: float = 30.0) -> dict[str, Any]:
    source_checks = _source_contract_checks()
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CONTRACT_JS, AUTHORITY_JS, PROJECTION_JS, WRAPPER_JS, PRESENTATION_JS, PROBE_JS)
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
        "phase4Part1PresentationProbePasses": isinstance(result, dict) and result.get("ok") is True,
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
