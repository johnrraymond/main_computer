#!/usr/bin/env python3
"""Phase 5 Part 1: bridge interactions, objectives and display power use the new ship-system boundary."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
ROOT_TOOLS = REPO_ROOT / "tools"
if str(ROOT_TOOLS) not in sys.path:
    sys.path.insert(0, str(ROOT_TOOLS))

import playwright_chromium_code_smoke as browser_code_smoke

SCHEMA = "game.spaceCaptainPhase5Part1BridgeGameplaySmoke.v1"
GAME_ROOT = REPO_ROOT / "game_projects" / "webgl-demo"
SCRIPT_ROOT = GAME_ROOT / "web" / "scripts"
SCENE_JS = SCRIPT_ROOT / "scene-viewer.js"
PROJECT_JSON = GAME_ROOT / "project.json"
PROBE_JS = HERE / "space_captain_phase5_part1_bridge_gameplay_probe.js"
MODULES = (
    SCRIPT_ROOT / "space-captain-multirate-contract.js",
    SCRIPT_ROOT / "bridge-encounter-runtime.js",
    SCRIPT_ROOT / "bridge-viewscreen-projection.js",
    SCRIPT_ROOT / "bridge-viewscreen-encounter-runtime.js",
    SCRIPT_ROOT / "bridge-viewscreen-presentation.js",
)
METHODS = (
    "createShipInteractionHandlerMap",
    "bridgeEncounterStatus",
    "bridgeViewscreenTrackingActive",
    "bridgeViewscreenDisplayPowered",
    "setBridgeViewscreenDisplayPowered",
    "toggleBridgeViewscreenDisplayPower",
    "bridgeViewscreenSelectedMode",
    "bridgeViewscreenPresentationSnapshot",
    "syncBridgeEncounterUiFromAuthority",
    "updateBridgeViewscreenEncounter",
    "openingEnemyEncounterPendingNavigation",
    "openingEnemyEncounterActive",
    "enemyShipHullPercent",
    "enemyShipDisabled",
    "fireBridgeTacticalConsole",
    "syncShipLocationFromCamera",
    "shipInteractionHint",
)


def extract_scene_methods() -> list[str]:
    source = SCENE_JS.read_text(encoding="utf-8")
    extracted = []
    for name in METHODS:
        matches = list(re.finditer(r"(?m)^        " + re.escape(name) + r"\([^\n]*\) \{\n", source))
        if len(matches) != 1:
            raise RuntimeError(f"SCENE_METHOD_NOT_UNIQUE: {name}: {len(matches)} matches")
        start = matches[0].start()
        end = source.find("\n        }", matches[0].end())
        if end < 0:
            raise RuntimeError(f"SCENE_METHOD_END_MISSING: {name}")
        extracted.append(source[start:end + len("\n        }")].strip())
    return extracted


def source_contract_checks() -> dict[str, bool]:
    scene = SCENE_JS.read_text(encoding="utf-8")
    project = json.loads(PROJECT_JSON.read_text(encoding="utf-8"))
    interiors = [
        obj
        for entry in project["scenes"]
        if (obj := entry.get("metadata", {}).get("shuttle3d", {}).get("motherShipInterior")) is not None
    ]
    old = ("trackEnemyShipOnViewscreen", "objective.bridge-screen", "objective.enemy-track", "objective.planet-view")
    view_action = "toggleBridgeViewscreenDisplayPower"
    view_prompt = "Press E to toggle the bridge viewscreen display power."
    checks = {
        "legacyTargetAcquisitionAndCenteringObjectivesDeleted": all(key not in scene for key in old),
        "legacyAuthoredInteractionsAndObjectivesDeleted": all(key not in json.dumps(interior) for interior in interiors for key in old),
        "allAuthoredDefinitionsUsePowerToggle": bool(interiors) and all(
            view_action in interior.get("interactions", {})
            and not any(item.get("action") != view_action for item in interior.get("interactables", []) if item.get("id") == "terminal.bridge-viewscreen")
            and any(item.get("prompt") == view_prompt for item in interior.get("interactables", []) if item.get("id") == "terminal.bridge-viewscreen")
            for interior in interiors
        ),
        "authoredPowerDefaultIsOn": bool(interiors) and all(interior.get("terminals", {}).get("terminal.bridge-viewscreen", {}).get("state") == "online" for interior in interiors),
        "authoredTacticalEffectsUseEncounterAuthorityNotLegacyFlags": bool(interiors) and all(
            "bridgeEncounterRuntime" in interior.get("interactions", {}).get("fireBridgeTacticalConsole", {}).get("changesState", [])
            and not any(value in interior.get("interactions", {}).get("fireBridgeTacticalConsole", {}).get("changesState", []) for value in (
                "flags.bridgeViewscreenTrackingActive", "flags.enemyShipHullPercent", "flags.enemyShipDisabled",
                "flags.bridgeTacticalShotsFired", "flags.bridgeTacticalLastFireAtMs",
            )) for interior in interiors
        ),
        "sceneRoutesViewscreenInteractionToPowerToggle": 'action: "toggleBridgeViewscreenDisplayPower"' in scene and 'toggleBridgeViewscreenDisplayPower: (target, interaction)' in scene,
        "sceneNoLongerUsesTerminalAsTrackingAuthority": 'const tracked = selectedMode === "encounter"' in scene and 'presentation.target?.lock?.acquired' in scene,
        "sceneRebuildsPresentationOnPowerChange": "this.bridgeViewscreenPresentationFrame = system.present(this.bridgeViewscreenPresentationInputsFrame);" in scene,
        "scenePreservesAuthorityOnPowerChange": ".command(" not in scene[scene.index("        setBridgeViewscreenDisplayPowered(powered) {"):scene.index("        toggleBridgeViewscreenDisplayPower() {")],
        "tacticalActionsStillUseEncounterAuthority": 'this.bridgeEncounterRuntime?.command?.({type: "fire-primary-weapon"}, nowMs)' in scene,
        "impactEventsStillDriveDestructionObjective": 'if (event.kind !== "Impact") continue;' in scene and 'this.setShipObjective("objective.enemy-disabled", true)' in scene,
        "bridgeEntryUsesAutomaticEncounterObjective": 'this.enemyShipDisabled() ? "objective.enemy-disabled" : "objective.enemy-attack"' in scene,
        "oldRendererRemainsDeleted": not (SCRIPT_ROOT / "shuttle3d-render-viewscreens.js").exists(),
        "allRequiredProductionSceneMethodsPresent": all(bool(re.search(r"(?m)^        " + re.escape(name) + r"\(", scene)) for name in METHODS),
    }
    return checks


def probe_source() -> str:
    scene_methods = extract_scene_methods()
    return "\n".join(
        [*(path.read_text(encoding="utf-8") for path in MODULES),
         "globalThis.__phase5BridgeMethods = ({\n" + ",\n".join(scene_methods) + "\n});\n",
         PROBE_JS.read_text(encoding="utf-8")]
    )


def run(*, headed: bool = False, timeout_seconds: float = 30.0) -> dict[str, Any]:
    static_checks = source_contract_checks()
    chromium = browser_code_smoke.run_smoke(
        source=probe_source(),
        headed=headed,
        viewport=(1280, 800),
        timeout_seconds=timeout_seconds,
        fail_on_console_error=True,
    )
    result = ((chromium.get("execution") or {}).get("result")) or {}
    checks = {
        **static_checks,
        "playwrightChromiumHarnessPasses": chromium.get("ok") is True,
        "phase5Part1BridgeGameplayProbePasses": isinstance(result, dict) and result.get("ok") is True,
    }
    failed = [key for key, passed in checks.items() if not passed]
    return {"ok": not failed, "schema": SCHEMA, "checks": checks, "failedChecks": failed, "chromium": chromium}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args(argv)
    try:
        report = run(headed=options.headed, timeout_seconds=options.timeout_seconds)
    except Exception as exc:
        report = {"ok": False, "schema": SCHEMA, "error": f"{type(exc).__name__}: {exc}"}
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
