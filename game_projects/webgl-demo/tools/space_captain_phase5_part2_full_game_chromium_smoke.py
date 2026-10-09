#!/usr/bin/env python3
"""Full authored-scene / real-WebGL Chromium gate for shuttle-to-bridge gameplay.

Loads the *production* game.json bundle, authored project.json scene and actual
WebGL renderer into a minimal browser host. This is not a synthetic JS contract
probe. The test controls the simulation clock explicitly and fast-forwards the
walk between authored terminal locations; all E-key actions go through the
production scene's keyboard interaction handler. A genuine WebGL context is
mandatory. A simulated GL object will not pass.

This standalone harness does not depend on the Main Computer desktop shell or
external NanoJev service. A ready tactical-AI callback substitutes only the
external shuttle-exit readiness service (not encounter gameplay or physics).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

GAME_ROOT = Path(__file__).resolve().parents[1]
GAME_JSON = GAME_ROOT / "game.json"
PROJECT_JSON = GAME_ROOT / "project.json"
SURFACE_HTML = GAME_ROOT / "web/apps/webgl.html"
CONTROLS_HTML = GAME_ROOT / "web/apps/webgl-controls.html"
DRIVER_JS = Path(__file__).with_name("space_captain_phase5_part2_full_game_chromium_probe.js")
SCHEMA = "game.spaceCaptainPhase5Part2FullGameChromiumSmoke.v1"
BOOTSTRAP_CSS = """
html,body{margin:0;width:100%;height:100%;background:#060d18;color:#e2e8f0}
#webgl-demo{display:block;position:relative;width:100%;height:780px;overflow:hidden}
.scene-shuttle3d{position:relative;width:100%;height:100%;min-height:600px}
.scene-shuttle3d-canvas{display:block;width:100%;height:100%}
"""
# Simulated monotone milliseconds; authority anchors itself at the first tick.
FIRST_MS = 1000.0
BRIDGE_MS = 3500.0
FIRE_MS = 8020.0


def source_checks() -> dict[str, bool]:
    game = json.loads(GAME_JSON.read_text(encoding="utf-8"))
    project = json.loads(PROJECT_JSON.read_text(encoding="utf-8"))
    scene = project.get("scenes", [None])[0] or {}
    script_paths = game["web"]["bundles"]["runtime-before-routing"]
    scene_code = (GAME_ROOT / "web/scripts/scene-viewer.js").read_text(encoding="utf-8")
    ids = {str(x.get("id")) for x in scene.get("metadata", {}).get("shuttle3d", {}).get("motherShipInterior", {}).get("interactables", [])}
    return {
        "authoredProjectContainsShuttle3DScene": scene.get("metadata", {}).get("projection") == "shuttle-3d",
        "actualGameSurfaceExists": SURFACE_HTML.exists() and "id=\"webgl-demo\"" in SURFACE_HTML.read_text(encoding="utf-8"),
        "allDeclaredProductionScriptsExist": all((GAME_ROOT / path).exists() for path in script_paths),
        "productionSceneAndViewscreenModulesLoaded": all(path in script_paths for path in (
            "web/scripts/scene-store.js",
            "web/scripts/bridge-encounter-runtime.js",
            "web/scripts/bridge-viewscreen-projection.js",
            "web/scripts/bridge-viewscreen-presentation.js",
            "web/scripts/bridge-viewscreen-renderer.js",
            "web/scripts/scene-viewer.js",
        )),
        "legacyViewscreensAbsent": "web/scripts/shuttle3d-render-viewscreens.js" not in script_paths and not (GAME_ROOT / "web/scripts/shuttle3d-render-viewscreens.js").exists(),
        "authoredBridgePowerAndWeaponsStationsExist": {"terminal.bridge-viewscreen", "terminal.bridge-tactical"}.issubset(ids),
        "realWebGLDrawLoopAndRealInputHandlersExist": (
            "class Shuttle3dVertexRenderer" in scene_code
            and "this.gl.drawArrays(" not in scene_code  # actual draw uses the local gl handle
            and "gl.drawArrays(gl.TRIANGLES" in scene_code
            and 'event.code === "KeyE"' in scene_code
            and "shuttle.interactWithShip?.();" in scene_code
        ),
        "lateEngineeringRepairCannotRewindBridgeMission": (
            'const objectiveAlreadyBeyondBridge = this.shipState.location === "bridge.deck"' in scene_code
            and 'if (!objectiveAlreadyBeyondBridge) this.setShipObjective("objective.bridge-access");' in scene_code
        ),
        "bridgeHudHidesObsoleteShuttleObjective": 'encounterLine.hidden = !encounter.objectiveLine || motherShipControlActive;' in scene_code,
        "movementHudUsesCurrentShipLocation": 'const shipLocation = renderer.shipStateSnapshot?.()?.locationLabel || pilot.shuttleBayLabel;' in scene_code,
        "driverUsesProductionSceneAndAuthoring": all(token in DRIVER_JS.read_text(encoding="utf-8") for token in (
            "MainComputerSceneViewer.renderSceneSurface",
            "project.scenes[0]",
            "renderer.enterShuttleBayPlayerControl(true)",
            "renderer.shipInteractionZones()",
            "PHASE5_PART2_REAL_WEBGL_CONTEXT_REQUIRED",
        )),
    }


def _fixture_html() -> str:
    # The surface and controls are the actual authored DOM fragments, not
    # contract stand-ins or recreated mock UI.
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Space Captain full scene integration</title></head><body>"
        + SURFACE_HTML.read_text(encoding="utf-8")
        + CONTROLS_HTML.read_text(encoding="utf-8")
        + "</body></html>"
    )


def _install_storage_and_clock(page: Any) -> None:
    # Chromium refuses localStorage on about:blank. Only this browser-host
    # storage is in-memory; no game engine/authority/geometry is emulated.
    page.evaluate("""() => {
      const dictionary = new Map();
      const store = {
        getItem:key=>dictionary.has(String(key))?dictionary.get(String(key)):null,
        setItem:(key,value)=>{dictionary.set(String(key),String(value));},
        removeItem:key=>{dictionary.delete(String(key));},
        clear:()=>dictionary.clear(),
        key:index=>Array.from(dictionary.keys())[index]||null,
        get length(){return dictionary.size;}
      };
      Object.defineProperty(window,'localStorage',{configurable:true,value:store});
      Object.defineProperty(window,'sessionStorage',{configurable:true,value:store});
      // Deterministic test driver manually calls the actual WebGL draw method.
      window.requestAnimationFrame=()=>0;
      window.cancelAnimationFrame=()=>{};
      // Preserve actual WebGL back-buffer pixels for deterministic screenshots.
      // Only the buffer-retention option is altered, not the graphics API.
      const nativeContext=HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext=function(kind, options, ...args){
        if(kind==='webgl'||kind==='experimental-webgl') {
          return nativeContext.call(this,kind,{...(options||{}),preserveDrawingBuffer:true},...args);
        }
        return nativeContext.call(this,kind,options,...args);
      };
    }""")


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _geometry_changed(a: dict, b: dict) -> bool:
    return (a.get("dynamicGeometryHash"),a.get("dynamicVertexCount")) != (b.get("dynamicGeometryHash"),b.get("dynamicVertexCount"))


def _vec3(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(
        isinstance(x, (int, float)) and math.isfinite(x) for x in value
    )


def _separation(a: Any, b: Any) -> float | None:
    if not _vec3(a) or not _vec3(b):
        return None
    return math.dist(a, b)


def _angles_degrees(a: Any, b: Any) -> float | None:
    if not _vec3(a) or not _vec3(b):
        return None
    mag_a, mag_b = math.sqrt(sum(x*x for x in a)), math.sqrt(sum(x*x for x in b))
    if mag_a <= 0 or mag_b <= 0:
        return None
    cosine = sum(x*y for x,y in zip(a,b)) / (mag_a*mag_b)
    return math.degrees(math.acos(max(-1.0,min(1.0,cosine))))


def _spatial_report(timeline: dict[str, dict]) -> tuple[dict[str, bool], dict[str, Any]]:
    # Measurements are taken from the production physics and projection *in the same frame*.
    # Null is a failing measurement, never a legitimate zero or default origin.
    names = ('bridge-entry', 'before-first-fire', 'projectile-in-flight',
             'first-impact', 'destruction')
    samples = {name: timeline[name] for name in names if name in timeline}
    rows = []
    for name, frame in samples.items():
        pose = frame.get('authoritativeObserver') or {}
        ship = frame.get('physicalMotherPositionM')
        camera = frame.get('cameraWorldPositionM')
        enemy = frame.get('targetWorldPositionM')
        enemy_authority = frame.get('targetAuthorityWorldPositionM')
        relative = frame.get('targetRelativeWorldM')
        expected_relative = [enemy[i]-ship[i] for i in range(3)] if _vec3(enemy) and _vec3(ship) else None
        expected_range = math.sqrt(sum(x*x for x in expected_relative)) if expected_relative else None
        reported_range = frame.get('projectedRangeM')
        range_error = (abs(reported_range-expected_range)
                       if isinstance(reported_range,(int,float)) and math.isfinite(reported_range)
                       and expected_range is not None else None)
        rows.append({
            'stage':name,
            'physicsSimulationSeconds':frame.get('physicsSimulationSeconds'),
            'physicalMotherPositionM':ship,
            'cameraWorldPositionM':camera,
            'cameraOriginErrorM':_separation(ship,camera),
            'poseOriginErrorM':_separation(ship,pose.get('positionM')),
            'viewDirectionErrorDeg':_angles_degrees(pose.get('forwardWorld'),frame.get('cameraForwardWorld')),
            'targetAuthorityWorldErrorM':_separation(enemy,enemy_authority),
            'targetRelativePositionErrorM':_separation(relative,expected_relative),
            'targetRangeErrorM':range_error,
            'bodyId':frame.get('cameraObserverBodyId'),
            'poseBodyId':pose.get('bodyId'),
            'targetInFront':frame.get('targetInFront'),
            'playerShipExternalVisible':frame.get('playerShipExternalVisible'),
        })
    def good(value: Any, tolerance: float=0.01) -> bool:
        return isinstance(value,(int,float)) and math.isfinite(value) and value <= tolerance
    checks = {
        'physicsReportsShipMotherInAllEncounterSamples':len(rows)==len(names) and all(_vec3(row['physicalMotherPositionM']) for row in rows),
        'viewscreenCameraOriginMatchesPhysicalMother':len(rows)==len(names) and all(good(row['cameraOriginErrorM']) and row['bodyId']=='ship.mother' for row in rows),
        'productionObserverPoseIsFromActualMotherShip':len(rows)==len(names) and all(good(row['poseOriginErrorM']) and row['poseBodyId']=='ship.mother' for row in rows),
        'cameraViewDirectionMatchesAuthoritativeAttitude':len(rows)==len(names) and all(good(row['viewDirectionErrorDeg'],1e-4) for row in rows),
        'targetHasAuthoritativeWorldPosition':len(rows)==len(names) and all(good(row['targetAuthorityWorldErrorM']) for row in rows),
        'targetRelativePositionUsesPhysicalShipOrigin':len(rows)==len(names) and all(good(row['targetRelativePositionErrorM']) for row in rows),
        'targetRangeMatchesWorldSpaceSeparation':len(rows)==len(names) and all(good(row['targetRangeErrorM']) for row in rows),
        'playerShipNeverAppearsAsExternalContact':len(rows)==len(names) and all(row['playerShipExternalVisible'] is False for row in rows),
    }
    return checks, {'spatialSamples':rows,'spatialSampleCount':len(rows)}


def run(*, headed: bool = False, chromium_executable: str | None = None,
        output_dir: Path | None = None, screenshot_enabled: bool = True,
        timeout_seconds: float = 45.0) -> dict[str, Any]:
    output_dir = output_dir or GAME_ROOT / "builds" / "phase5-part2-chromium"
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = source_checks()
    timeline: dict[str, Any] = {}
    screenshots: dict[str, str] = {}
    failures: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []
    scripts_loaded: list[str] = []
    browser_version = ""

    def screen(page: Any, label: str) -> None:
        if screenshot_enabled:
            target = output_dir / f"{label}.png"
            page.locator("#webgl-demo").screenshot(path=str(target), animations="disabled")
            screenshots[label] = str(target)

    def state(page: Any, label: str, timestamp: float | None = None) -> dict:
        if timestamp is None:
            current = page.evaluate("() => globalThis.__phase5Part2.snapshot()")
        else:
            current = page.evaluate("t => globalThis.__phase5Part2.step(t)", timestamp)
        timeline[label] = current
        return current

    def press_e(page: Any, label: str) -> dict:
        # Same DOM keydown handler used by the actual player.
        page.locator("#webgl-demo").focus()
        page.keyboard.press("e")
        return state(page, label)

    try:
        with sync_playwright() as playwright:
            launch: dict[str, Any] = {
                "headless": not headed,
                "args": [
                    "--no-sandbox", "--enable-webgl", "--enable-unsafe-swiftshader",
                    "--ignore-gpu-blocklist", "--no-proxy-server",
                    "--proxy-bypass-list=*", "--disable-features=BlockInsecurePrivateNetworkRequests",
                ],
            }
            if chromium_executable:
                launch["executable_path"] = str(chromium_executable)
            browser = playwright.chromium.launch(**launch)
            try:
                browser_version = browser.version
                page = browser.new_page(viewport={"width": 1280, "height": 840}, device_scale_factor=1)
                page.set_default_timeout(timeout_seconds * 1000)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                page.set_content(_fixture_html(), wait_until="domcontentloaded")
                _install_storage_and_clock(page)
                page.add_style_tag(content=BOOTSTRAP_CSS)
                game = json.loads(GAME_JSON.read_text(encoding="utf-8"))
                for path in game["web"]["bundles"].get("styles", []):
                    page.add_style_tag(content=(GAME_ROOT / path).read_text(encoding="utf-8"))
                # Restore fixed test-surface dimensions after authored styles.
                page.add_style_tag(content=BOOTSTRAP_CSS)
                for path in game["web"]["bundles"]["runtime-before-routing"]:
                    page.add_script_tag(content=(GAME_ROOT / path).read_text(encoding="utf-8"))
                    scripts_loaded.append(path)
                page.add_script_tag(content=DRIVER_JS.read_text(encoding="utf-8"))
                project = json.loads(PROJECT_JSON.read_text(encoding="utf-8"))
                initial = page.evaluate("project => globalThis.__phase5Part2.boot(project)", project)
                timeline["boot"] = initial
                checks["realSceneMountedFromProjectJSON"] = initial["sceneId"] == project["scenes"][0]["id"] and initial["sceneProjection"] == "shuttle-3d"
                checks["realWebGLContextAndCanvasAvailable"] = bool(initial["realWebGLContext"] and initial["webglVersion"] and initial["canvasWidth"] > 0 and initial["canvasHeight"] > 0)
                checks["rendererStartsOutsideBridge"] = initial["location"] != "bridge.deck"

                shuttle = state(page, "shuttle", FIRST_MS)
                screen(page, "01-shuttle-opening")
                pre_bridge = state(page, "shuttle-encounter-progressed", 2500)
                checks["openingEncounterSelectedBeforeBridge"] = shuttle["selectedMode"] == "encounter" and shuttle["presentationSchema"] == "game.bridgeViewscreenPresentation.v1"
                checks["encounterAdvancesBeforeBridgeEntry"] = (pre_bridge["presentationSeconds"] > shuttle["presentationSeconds"]
                    and pre_bridge["targetScreen"] != shuttle["targetScreen"] and pre_bridge["physicalMotherPositionM"] is not None)
                checks["intactEnemyHasNoPrematureExplosionOrDebris"] = all(
                    sample["authorityHullPercent"] == 100
                    and not sample["authorityTargetDestroyed"]
                    and sample["explosionCount"] == 0
                    and sample["debrisCount"] == 0
                    for sample in (shuttle, pre_bridge)
                )
                checks["actualWebGLDrawCallsAndGeometryExist"] = bool(pre_bridge["drawCalls"] >= 2 and pre_bridge["dynamicVertexCount"] > 0)

                dock = page.evaluate("() => globalThis.__phase5Part2.dock()")
                timeline["docking-handoff"] = dock
                checks["realShuttleDockingHandoffSucceeds"] = dock["entered"] and dock["playerBayControl"]
                at_screen = page.evaluate("() => globalThis.__phase5Part2.approachTerminal('terminal.bridge-viewscreen')")
                timeline["bridge-console-approach"] = at_screen
                bridge = state(page, "bridge-entry", BRIDGE_MS)
                screen(page, "02-bridge-tracking")
                checks["bridgeEntryUsesAuthoredTerminalAndRetainsFeed"] = (
                    at_screen["interactionTargetId"] == "terminal.bridge-viewscreen"
                    and bridge["location"] == "bridge.deck"
                    and bridge["selectedMode"] == "encounter"
                    and bridge["presentationSeconds"] >= pre_bridge["presentationSeconds"]
                    and bridge["targetVisible"]
                )
                checks["bridgeCombatObjectiveStartsWithoutManualAcquisition"] = bridge["objective"] == "objective.enemy-attack"
                checks["bridgeHudUsesCurrentMissionNotShuttleBoarding"] = bridge["shipHudVisible"] and bridge["shuttleEncounterHudHidden"]
                checks["bridgeHudReportsActualLocation"] = "Bridge Deck" in bridge["movementLocationText"]
                checks["bridgeViewscreenActuallyProducesGeometry"] = bridge["dynamicVertexCount"] > 0 and bridge["drawCalls"] > pre_bridge["drawCalls"]

                off = press_e(page, "power-off-keypress")
                off_render = state(page, "power-off-rendered", 3900)
                screen(page, "03-bridge-display-off")
                checks["realEKeyTurnsOffDisplayWithoutModeChange"] = off["displayPowered"] is False and off["selectedMode"] == bridge["selectedMode"]
                checks["powerOffPreservesAuthorityAndChangesGeometry"] = (
                    off["authorityHullPercent"] == bridge["authorityHullPercent"]
                    and not off_render["displayPowered"]
                    and _geometry_changed(bridge, off_render)
                )
                checks["encounterKeepsMovingWhileScreenIsDark"] = off_render["presentationSeconds"] > bridge["presentationSeconds"] and off_render["targetScreen"] != bridge["targetScreen"]
                on = press_e(page, "power-on-keypress")
                on_render = state(page, "power-on-rendered", 4300)
                screen(page, "04-bridge-display-on")
                checks["realEKeyRestoresCurrentPresentation"] = on["displayPowered"] is True and on_render["presentationSeconds"] > off_render["presentationSeconds"] and _geometry_changed(off_render, on_render)

                at_weapon = page.evaluate("() => globalThis.__phase5Part2.approachTerminal('terminal.bridge-tactical')")
                timeline["tactical-console-approach"] = at_weapon
                # Face the viewscreen while within reach of the tactical station.
                page.evaluate("() => globalThis.__phase5Part2Renderer.setLook(-45,-2)")
                before_fire = state(page, "before-first-fire", FIRE_MS)
                first_key = press_e(page, "first-fire-keypress")
                first_shot = first_key["shots"][-1] if first_key["shots"] else {}
                first_impact = float(first_shot.get("impactAtSeconds", -1))
                checks["realEKeyRoutesWeaponToEncounterAuthority"] = (
                    at_weapon["interactionTargetId"] == "terminal.bridge-tactical"
                    and len(first_key["shots"]) == len(before_fire["shots"]) + 1
                    and first_impact > float(first_shot.get("firedAtSeconds", float("inf")))
                )
                checks["fireDoesNotCauseImmediateHullDamage"] = first_key["authorityHullPercent"] == before_fire["authorityHullPercent"] == 100
                in_flight_ms = FIRST_MS + (float(first_shot["firedAtSeconds"]) + min(0.11, (first_impact - float(first_shot["firedAtSeconds"])) * 0.4)) * 1000
                flying = state(page, "projectile-in-flight", in_flight_ms)
                screen(page, "05-projectile-flight")
                checks["projectileVisualAppearsBeforeImpact"] = flying["projectileCount"] > 0 and flying["authorityHullPercent"] == 100
                impact_ms = FIRST_MS + (first_impact + 0.10) * 1000
                impact = state(page, "first-impact", impact_ms)
                screen(page, "06-first-impact")
                checks["authoritativeImpactProducesDamageAndEffect"] = impact["authorityHullPercent"] == 50 and impact["targetHullFraction"] == 0.5 and impact["impactCount"] > 0
                checks["shotResolutionDiagnosticMatchesAuthorityImpact"] = bool(impact["shots"] and impact["shots"][-1]["resolved"]
                    and flying["shots"] and not flying["shots"][-1]["resolved"])
                checks["damagedEnemyHasImpactButNoDestructionEffects"] = all(
                    sample["authorityHullPercent"] in (50, 100)
                    and not sample["authorityTargetDestroyed"]
                    and sample["explosionCount"] == 0
                    and sample["debrisCount"] == 0
                    for sample in (before_fire, flying, impact)
                )
                checks["impactGeometryIsActualWebGLOutput"] = impact["dynamicVertexCount"] > 0 and _geometry_changed(flying, impact)

                second_fire_ms = FIRST_MS + (first_impact + 0.41) * 1000
                before_second = state(page, "before-second-fire", second_fire_ms)
                second_key = press_e(page, "second-fire-keypress")
                second_shot = second_key["shots"][-1] if second_key["shots"] else {}
                second_impact = float(second_shot.get("impactAtSeconds", -1))
                checks["secondEKeyFireAccepted"] = (
                    len(second_key["shots"]) == len(before_second["shots"]) + 1 and second_impact > float(second_shot.get("firedAtSeconds", float("inf")))
                )
                destroyed = state(page, "destruction", FIRST_MS + (second_impact + 0.10) * 1000)
                screen(page, "07-target-destruction")
                checks["secondAuthorityImpactDestroysEnemy"] = destroyed["authorityHullPercent"] == 0 and destroyed["targetVisualState"] == "destroyed"
                checks["destroyedEnemyRendersExplosionAndDebris"] = destroyed["explosionCount"] > 0 and destroyed["debrisCount"] > 0 and _geometry_changed(impact, destroyed)
                checks["actualHudObjectiveUpdatesToEnemyDisabled"] = destroyed["objective"] == "objective.enemy-disabled" and destroyed["hudObjective"] == "objective.enemy-disabled"
                after_explosion = state(page, "after-explosion", FIRST_MS + (second_impact + 2.5) * 1000)
                screen(page, "08-destruction-objective-persists")
                checks["objectivePersistsAfterExplosion"] = after_explosion["objective"] == "objective.enemy-disabled" and after_explosion["authorityTargetDestroyed"]
                checks["noUnexpectedBrowserErrors"] = not page_errors and not console_errors
                checks["realScreenshotsSaved"] = not screenshot_enabled or len(screenshots) == 8
                if screenshot_enabled and len(screenshots) == 8:
                    checks["screenshotsChangeWhenViewsceenPowerChanges"] = (
                        _fingerprint(Path(screenshots["02-bridge-tracking"]))
                        != _fingerprint(Path(screenshots["03-bridge-display-off"]))
                        and _fingerprint(Path(screenshots["03-bridge-display-off"]))
                        != _fingerprint(Path(screenshots["04-bridge-display-on"]))
                    )
                    checks["impactAndDestructionRenderDistinctImages"] = (
                        _fingerprint(Path(screenshots["06-first-impact"]))
                        != _fingerprint(Path(screenshots["07-target-destruction"]))
                    )
            finally:
                browser.close()
    except Exception as error:
        failures.append(f"{type(error).__name__}: {error}")
        # Preserve traceback to make real-browser failures actionable on Windows.
        failures.append(traceback.format_exc(limit=8))

    spatial_checks, spatial_metrics = _spatial_report(timeline)
    checks.update(spatial_checks)
    failed_checks = [key for key, passed in checks.items() if not passed]
    report = {
        "ok": not failed_checks and not failures,
        "schema": SCHEMA,
        "testKind": "actual-production-scene-with-real-webgl-and-authored-project",
        "scope": {
            "source": "game.json runtime-before-routing + actual webgl.html + project.json scene",
            "clock": "explicit simulation frame stepping",
            "walk": "authored terminal coordinates (walking fast-forwarded)",
            "controls": "real DOM E-key handlers",
            "tacticalAI": "external readiness provider injected, not NanoJev inference",
            "desktopShell": "not loaded; game renderer and scene are loaded",
            "webglCapture": "real context, preserveDrawingBuffer requested for screenshots",
        },
        "checks": checks,
        "failedChecks": failed_checks,
        "errors": failures,
        "browser": {"version": browser_version, "pageErrors": page_errors, "consoleErrors": console_errors},
        "artifacts": {"directory": str(output_dir), "screenshots": screenshots},
        "metrics": {
            "scriptsLoaded": len(scripts_loaded),
            **spatial_metrics,
            "runtimeScriptSha256": {
                path: _fingerprint(GAME_ROOT / path)
                for path in scripts_loaded if path in (
                    "web/scripts/scene-viewer.js",
                    "web/scripts/bridge-viewscreen-presentation.js",
                    "web/scripts/bridge-viewscreen-renderer.js",
                    "web/scripts/bridge-encounter-runtime.js",
                )
            },
            "authoredProjectSha256": _fingerprint(PROJECT_JSON),
            "firstImpactSeconds": timeline.get("first-fire-keypress", {}).get("shots", [{}])[-1].get("impactAtSeconds") if timeline.get("first-fire-keypress", {}).get("shots") else None,
            "secondImpactSeconds": timeline.get("second-fire-keypress", {}).get("shots", [{}])[-1].get("impactAtSeconds") if timeline.get("second-fire-keypress", {}).get("shots") else None,
            "endHullPercent": timeline.get("destruction", {}).get("authorityHullPercent"),
            "endObjective": timeline.get("after-explosion", {}).get("objective"),
        },
        "timeline": timeline,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show Chromium while running")
    parser.add_argument("--chromium-executable", default=None, help="Override Playwright's bundled Chromium binary")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-screenshots", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    options = parser.parse_args(argv)
    report = run(
        headed=options.headed,
        chromium_executable=options.chromium_executable,
        output_dir=options.output_dir,
        screenshot_enabled=not options.no_screenshots,
        timeout_seconds=options.timeout_seconds,
    )
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
