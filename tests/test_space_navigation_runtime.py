from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "space-navigation-runtime.js"
SCENE_VIEWER_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "scene-viewer.js"
VIEWSCREEN_MODULE_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "shuttle3d-render-viewscreens.js"
GAME_EDITOR_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "game-editor.js"
WEBGL_DESKTOP_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "webgl-desktop.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
STYLE_PATH = ROOT / "main_computer" / "web" / "applications" / "styles" / "game-editor.css"
PROJECT_PATHS = (
    ROOT / "game_projects" / "webgl-demo" / "project.json",
    ROOT / "game_projects" / "starter-game" / "project.json",
    ROOT / "game_projects" / "new-game" / "project.json",
)


class SpaceNavigationRuntimeTests(unittest.TestCase):
    def test_node_runtime_executes_adjacent_jump_and_commits_world_time_once(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the browser-runtime smoke")
        script = textwrap.dedent(
            f"""
            const fs = require('fs');
            const api = require({json.dumps(str(RUNTIME_PATH))});
            const project = JSON.parse(fs.readFileSync({json.dumps(str(PROJECT_PATHS[0]))}, 'utf8'));
            const runtime = api.create(project.metadata.spaceNavigation, {{projectId: project.id}});
            const initial = runtime.snapshot();
            if (initial.startSystemId !== 'system.solace-reach') throw new Error('wrong definition start system');
            if (initial.currentSystemId !== 'system.solace-reach') throw new Error('wrong start system');
            if (initial.currentPlanetId !== 'planet.haven') throw new Error('wrong start planet');
            if (initial.currentPlanetLabel !== 'Haven') throw new Error('wrong start planet label');
            if (initial.destinations.length !== 2) throw new Error('wrong initial destination count');
            if (!initial.destinations.some((entry) => entry.systemId === 'system.vela-gate')) throw new Error('Vela Gate unavailable');
            runtime.plotCourse('system.vela-gate');
            if (runtime.snapshot().travelPhase !== 'course-plotted') throw new Error('course not plotted');
            runtime.engage(1000);
            if (runtime.snapshot().travelPhase !== 'warp-charging') throw new Error('warp not charging');
            const route = runtime.route('route.solace-reach-vela-gate');
            const mid = runtime.update(1000 + Math.round(route.presentationDurationMs * 0.5)).snapshot;
            if (mid.travelPhase !== 'in-warp') throw new Error('in-warp phase not reached');
            const arrival = runtime.update(1001 + route.presentationDurationMs);
            if (!arrival.arrived) throw new Error('arrival was not committed');
            const arrived = arrival.snapshot;
            if (arrived.currentSystemId !== 'system.vela-gate') throw new Error('wrong arrival system');
            if (arrived.currentPlanetId !== 'planet.velaris') throw new Error('wrong arrival planet');
            if (arrived.currentPlanetLabel !== 'Velaris') throw new Error('wrong arrival planet label');
            if (arrived.elapsedWorldTime !== route.worldTimeCost) throw new Error('world time not committed exactly once');
            if (arrived.travelPhase !== 'in-system') throw new Error('arrival phase not closed');
            if (arrived.plottedRouteId !== null) throw new Error('plotted route not cleared');
            const after = runtime.update(9000).snapshot;
            if (after.elapsedWorldTime !== route.worldTimeCost) throw new Error('world time committed twice');
            if (!after.destinations.some((entry) => entry.systemId === 'system.solace-reach')) throw new Error('return route unavailable');
            console.log(JSON.stringify({{ok: true, current: after.currentSystemId, worldTime: after.elapsedWorldTime}}));
            """
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('"ok":true', result.stdout)

    def test_viewscreen_restores_opening_raider_then_switches_to_warp_and_planet(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the viewscreen dispatch smoke")
        script = textwrap.dedent(
            f"""
            let registered = null;
            globalThis.MainComputerShuttle3DRendererModules = {{
              register(name, methods) {{
                if (name === 'viewscreens') registered = methods;
              }}
            }};
            require({json.dumps(str(VIEWSCREEN_MODULE_PATH))});
            if (!registered) throw new Error('viewscreen module did not register');

            const calls = [];
            const builder = {{
              color(value) {{ return value; }},
              box() {{}},
              beam() {{}},
              ellipsoid() {{}}
            }};
            const prop = {{position: [0, -39.12], size: [6.9, 2.1, 0.08], color: '#38bdf8'}};
            const context = {{
              shipState: {{flags: {{}}}},
              bridgeViewscreenTrackingActive() {{ return false; }},
              appendWarpTransitDisplay() {{ calls.push('warp'); }},
              appendEnemyShipTacticalDisplay() {{ calls.push('enemy'); }}
            }};

            context.navigationSnapshot = () => ({{
              travelling: false,
              startSystemId: 'system.solace-reach',
              currentSystemId: 'system.solace-reach',
              elapsedWorldTime: 0,
              lastCompletedRouteId: null,
              lastArrivalAtMs: null,
              currentPlanet: {{id: 'planet.haven'}}
            }});
            registered.appendSystemPlanetDisplay.call(context, builder, prop, 0);
            if (calls.join(',') !== 'enemy') throw new Error(`opening encounter rendered ${{calls.join(',') || 'planet'}} instead of enemy`);

            calls.length = 0;
            context.navigationSnapshot = () => ({{
              travelling: true,
              startSystemId: 'system.solace-reach',
              currentSystemId: 'system.solace-reach',
              destinationSystemId: 'system.vela-gate',
              currentPlanet: {{id: 'planet.haven'}},
              destinationPlanet: {{id: 'planet.velaris'}}
            }});
            registered.appendSystemPlanetDisplay.call(context, builder, prop, 1000);
            if (calls.join(',') !== 'warp') throw new Error(`warp rendered ${{calls.join(',') || 'planet'}} instead of transit`);

            calls.length = 0;
            context.navigationSnapshot = () => ({{
              travelling: false,
              startSystemId: 'system.solace-reach',
              currentSystemId: 'system.vela-gate',
              elapsedWorldTime: 4,
              lastCompletedRouteId: 'route.solace-reach-vela-gate',
              lastArrivalAtMs: 5500,
              currentPlanet: {{
                id: 'planet.velaris',
                surfaceColor: '#2563eb',
                secondaryColor: '#16a34a',
                atmosphereColor: '#67e8f9',
                cloudColor: '#f8fafc',
                radiusScale: 1,
                moonCount: 0,
                rings: {{enabled: false, color: '#94a3b8'}}
              }}
            }});
            registered.appendSystemPlanetDisplay.call(context, builder, prop, 6000);
            if (calls.length) throw new Error(`post-warp planet incorrectly dispatched to ${{calls.join(',')}}`);
            console.log('viewscreen-state-dispatch-ok');
            """
        )
        result = subprocess.run(["node", "-e", script], cwd=ROOT, check=False, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("viewscreen-state-dispatch-ok", result.stdout)

    def test_bridge_tactical_console_destroys_opening_raider_in_two_hits_then_scans_planets(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the tactical-console smoke")
        script = textwrap.dedent(
            f"""
            const fs = require('fs');
            const source = fs.readFileSync({json.dumps(str(SCENE_VIEWER_PATH))}, 'utf8');
            const start = source.indexOf('        fireBridgeTacticalConsole() {{');
            const end = source.indexOf('\\n        shipLocationForPosition(', start);
            if (start < 0 || end < 0) throw new Error('tactical method not found');
            const methodSource = source.slice(start, end).trim();
            const fire = Function(`return ({{${{methodSource}}}}).fireBridgeTacticalConsole;`)();

            const calls = [];
            const context = {{
              lastFrameTime: 1000,
              shipState: {{
                flags: {{
                  enemyShipHullPercent: 100,
                  enemyShipDisabled: false,
                  bridgeTacticalShotsFired: 0,
                  bridgeTacticalLastFireAtMs: 0,
                  currentSystemPlanetSurveyed: false,
                  planetScansCompleted: 0
                }}
              }},
              openingEnemyEncounterActive() {{ return true; }},
              enemyShipHullPercent() {{ return Number(this.shipState.flags.enemyShipHullPercent); }},
              setShipTerminalState(id, state) {{ calls.push(['terminal', id, state]); }},
              setShipObjective(id) {{ this.objectiveId = id; calls.push(['objective', id]); }},
              setShipInteractionStatus(message) {{ this.status = message; }},
              emitShipState() {{}},
              navigationSnapshot() {{ return {{}}; }}
            }};

            fire.call(context);
            if (context.shipState.flags.enemyShipHullPercent !== 50) throw new Error('first hit did not reduce hull to 50');
            if (context.shipState.flags.enemyShipDisabled) throw new Error('enemy destroyed after only one hit');
            if (!/one more volley/i.test(context.status)) throw new Error('first-hit status missing');

            context.lastFrameTime = 2000;
            fire.call(context);
            if (context.shipState.flags.enemyShipHullPercent !== 0) throw new Error('second hit did not reduce hull to zero');
            if (!context.shipState.flags.enemyShipDisabled) throw new Error('second hit did not destroy enemy');
            if (context.objectiveId !== 'objective.enemy-disabled') throw new Error('destroyed objective not selected');
            if (!/destroyed in an expanding fireball/i.test(context.status)) throw new Error('destruction status missing');

            const shotsAfterKill = context.shipState.flags.bridgeTacticalShotsFired;
            context.lastFrameTime = 3000;
            fire.call(context);
            if (context.shipState.flags.enemyShipHullPercent !== 0) throw new Error('destroyed hull changed');
            if (context.shipState.flags.bridgeTacticalShotsFired !== shotsAfterKill + 1) throw new Error('status review shot was not recorded');

            context.openingEnemyEncounterActive = () => false;
            context.navigationSnapshot = () => ({{
              currentSystemId: 'system.vela-gate',
              currentSystemLabel: 'Vela Gate',
              currentPlanetId: 'planet.velaris',
              currentPlanetLabel: 'Velaris',
              currentPlanet: {{id: 'planet.velaris', label: 'Velaris'}}
            }});
            context.lastFrameTime = 4000;
            fire.call(context);
            if (!context.shipState.flags.currentSystemPlanetSurveyed) throw new Error('post-warp planet scan did not complete');
            if (context.objectiveId !== 'objective.planet-surveyed') throw new Error('planet-surveyed objective not selected');
            if (context.shipState.flags.enemyShipHullPercent !== 0) throw new Error('planet scan changed destroyed enemy hull');
            console.log('two-hit-raider-destruction-ok');
            """
        )
        result = subprocess.run(["node", "-e", script], cwd=ROOT, check=False, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("two-hit-raider-destruction-ok", result.stdout)

    def test_destroyed_raider_viewscreen_renders_explosion_then_debris(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the raider explosion render smoke")
        script = textwrap.dedent(
            f"""
            let registered = null;
            globalThis.MainComputerShuttle3DRendererModules = {{
              register(name, module) {{
                if (name === 'viewscreens') registered = module;
              }}
            }};
            require({json.dumps(str(VIEWSCREEN_MODULE_PATH))});
            if (!registered) throw new Error('viewscreen module did not register');
            const prop = {{position: [0, -39.12], size: [6.9, 2.1, 0.08], color: '#38bdf8'}};

            function render(age) {{
              const calls = {{box: 0, beam: 0, ellipsoid: 0}};
              const builder = {{
                color(value, emissive) {{ return {{value, emissive}}; }},
                box() {{ calls.box += 1; }},
                beam() {{ calls.beam += 1; }},
                ellipsoid() {{ calls.ellipsoid += 1; }}
              }};
              const context = {{
                enemyShipHullPercent() {{ return 0; }},
                enemyShipDisabled() {{ return true; }},
                bridgeTacticalShotAgeMs() {{ return age; }},
                bridgeViewscreenTrackingActive() {{ return true; }}
              }};
              registered.appendEnemyShipTacticalDisplay.call(context, builder, prop, 5000);
              return calls;
            }}

            const explosion = render(300);
            if (explosion.ellipsoid < 3) throw new Error(`expected expanding explosion, got ${{explosion.ellipsoid}} ellipsoids`);
            if (explosion.beam < 20) throw new Error(`expected radial explosion streaks, got ${{explosion.beam}} beams`);
            const debris = render(2600);
            if (debris.box < 7) throw new Error(`expected debris field, got ${{debris.box}} boxes`);
            if (debris.ellipsoid < 1) throw new Error('expected residual debris glow');
            console.log(JSON.stringify({{explosion, debris}}));
            """
        )
        result = subprocess.run(["node", "-e", script], cwd=ROOT, check=False, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('"explosion"', result.stdout)

    def test_runtime_rejects_nonadjacent_jump(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the browser-runtime smoke")
        script = textwrap.dedent(
            f"""
            const fs = require('fs');
            const api = require({json.dumps(str(RUNTIME_PATH))});
            const project = JSON.parse(fs.readFileSync({json.dumps(str(PROJECT_PATHS[0]))}, 'utf8'));
            const runtime = api.create(project.metadata.spaceNavigation);
            let rejected = false;
            try {{ runtime.plotCourse('system.meridian-prime'); }} catch (error) {{ rejected = /No direct route/.test(String(error.message)); }}
            if (!rejected) throw new Error('nonadjacent jump was accepted');
            console.log('rejected');
            """
        )
        result = subprocess.run(["node", "-e", script], cwd=ROOT, check=False, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("rejected", result.stdout)

    def test_browser_surface_requires_bridge_controls_and_keeps_player_mobile_during_warp(self) -> None:
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        viewer = SCENE_VIEWER_PATH.read_text(encoding="utf-8")
        editor = GAME_EDITOR_PATH.read_text(encoding="utf-8")
        desktop = WEBGL_DESKTOP_PATH.read_text(encoding="utf-8")
        styles = STYLE_PATH.read_text(encoding="utf-8")
        viewscreens = VIEWSCREEN_MODULE_PATH.read_text(encoding="utf-8")

        self.assertLess(applications.index("space-navigation-runtime.js"), applications.index("scene-viewer.js"))
        self.assertIn("spaceNavigation: gameEditorState.project?.metadata?.spaceNavigation", editor)
        self.assertIn("spaceNavigation: candidate?.project?.metadata?.spaceNavigation", desktop)
        self.assertIn("new Shuttle3dVertexRenderer(canvas, scene, options)", viewer)
        self.assertIn("this.spaceNavigationRuntime.engage(nowMs)", viewer)
        self.assertIn("this.updateSpaceNavigation(frameTime)", viewer)
        self.assertNotIn('event.code === "KeyN"', viewer)
        self.assertNotIn("scene-shuttle3d-navigation-toggle", viewer)
        self.assertIn("scene-shuttle3d-navigation-panel", viewer)
        self.assertIn("scene-shuttle3d-warp-overlay", viewer)
        self.assertIn("openBridgeNavigationConsole", viewer)
        self.assertIn("bridgeNavigationConsoleZone()", viewer)
        self.assertIn("bridgeNavigationControlTarget()", viewer)
        self.assertIn('zone?.id === "terminal.bridge-navigation"', viewer)
        self.assertIn("distance <= range", viewer)
        self.assertIn("requireBridgeNavigationControls", viewer)
        self.assertIn('target?.id !== "terminal.bridge-navigation"', viewer)
        self.assertIn('this.navigationConsoleAccessTargetId === "terminal.bridge-navigation"', viewer)
        self.assertNotIn("if (shuttle?.isWarpTravelActive?.())", viewer)
        self.assertNotIn("if (this.isWarpTravelActive()) {", viewer)
        self.assertNotIn("this.clearMovementKeys();\n            this.spaceNavigationRuntime.engage(nowMs)", viewer)
        self.assertIn("W/A/S/D move during warp", viewer)
        self.assertIn("appendSystemPlanetDisplay", viewer)
        self.assertIn("appendWarpTransitDisplay", viewscreens)
        self.assertIn("appendWarpTransitDisplay(builder, prop, nowMs = 0, navigationState = null)", viewer)
        self.assertIn("if (navigation.travelling)", viewscreens)
        self.assertIn("openingEncounterActive", viewscreens)
        self.assertIn("navigation.currentSystemId === navigation.startSystemId", viewscreens)
        self.assertIn("this.appendEnemyShipTacticalDisplay(builder, prop, nowMs)", viewscreens)
        self.assertIn("navigation.destinationPlanet", viewscreens)
        self.assertIn('display === "systemplanet"', viewscreens)
        self.assertIn("navigation.currentPlanet", viewscreens)
        self.assertIn(".scene-shuttle3d-navigation-panel", styles)
        self.assertNotIn(".scene-shuttle3d-navigation-toggle", styles)
        self.assertNotIn("scene-shuttle3d-warp-tunnel", styles)



    def test_bridge_navigation_session_uses_console_range_not_nearest_overlapping_target(self) -> None:
        viewer = SCENE_VIEWER_PATH.read_text(encoding="utf-8")
        control_start = viewer.index("        bridgeNavigationControlTarget() {")
        control_end = viewer.index("        canAccessBridgeNavigationConsole()", control_start)
        control_body = viewer[control_start:control_end]
        self.assertIn("bridgeNavigationConsoleZone()", viewer)
        self.assertIn("this.shipLocationForPosition(this.camera[0], this.camera[2])", control_body)
        self.assertIn("Math.hypot(dx, dz)", control_body)
        self.assertIn("distance <= range", control_body)
        self.assertNotIn("this.shipInteractionTarget", control_body)
        target_start = viewer.index("        shipInteractionTarget() {")
        target_end = viewer.index("        shipInteractionHint(", target_start)
        target_body = viewer[target_start:target_end]
        self.assertIn("this.navigationConsoleOpen", target_body)
        self.assertIn("this.bridgeNavigationControlTarget()", target_body)

    def test_viewscreen_renderer_draws_live_warp_transit_geometry(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the viewscreen renderer smoke")
        script = textwrap.dedent(
            f"""
            let registered = null;
            globalThis.MainComputerShuttle3DRendererModules = {{
              register(name, module) {{
                if (name === 'viewscreens') registered = module;
              }}
            }};
            require({json.dumps(str(VIEWSCREEN_MODULE_PATH))});
            if (!registered) throw new Error('viewscreen module did not register');
            const calls = {{box: 0, beam: 0, ellipsoid: 0}};
            const builder = {{
              color(value, emissive) {{ return {{value, emissive}}; }},
              box() {{ calls.box += 1; }},
              beam() {{ calls.beam += 1; }},
              ellipsoid() {{ calls.ellipsoid += 1; }}
            }};
            const context = Object.assign({{
              shipState: {{flags: {{}}}},
              bridgeViewscreenTrackingActive() {{ return false; }},
              navigationSnapshot() {{
                return {{
                  travelling: true,
                  travelPhase: 'in-warp',
                  travelProgress: 0.5,
                  currentPlanet: {{surfaceColor: '#2563eb', secondaryColor: '#16a34a', atmosphereColor: '#67e8f9'}},
                  destinationPlanet: {{surfaceColor: '#b45309', secondaryColor: '#fde68a', atmosphereColor: '#fbbf24'}}
                }};
              }}
            }}, registered);
            context.appendSystemPlanetDisplay(builder, {{display: 'systemPlanet', position: [0, -39.12], size: [6.9, 2.1, 0.08]}}, 5000);
            if (calls.beam < 35) throw new Error(`expected moving star streaks, got ${{calls.beam}} beams`);
            if (calls.ellipsoid < 6) throw new Error(`expected tunnel and planet geometry, got ${{calls.ellipsoid}} ellipsoids`);
            if (calls.box < 2) throw new Error(`expected display frame and progress geometry, got ${{calls.box}} boxes`);
            console.log(JSON.stringify(calls));
            """
        )
        result = subprocess.run(["node", "-e", script], cwd=ROOT, check=False, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('"beam":', result.stdout)

    def test_default_projects_expose_a_bridge_navigation_terminal(self) -> None:
        for path in PROJECT_PATHS:
            project = json.loads(path.read_text(encoding="utf-8"))
            interior = project["scenes"][0]["metadata"]["shuttle3d"]["motherShipInterior"]
            self.assertIn("terminal.bridge-navigation", interior["terminals"])
            self.assertIn("openBridgeNavigationConsole", interior["interactions"])
            self.assertTrue(
                any(item.get("id") == "terminal.bridge-navigation" for item in interior["interactables"])
            )
            self.assertTrue(
                any(item.get("id") == "prop.console.bridge-navigation" for item in interior["props"])
            )
            self.assertIn("objective.planet-view", interior["objectives"])
            self.assertIn("objective.planet-scan", interior["objectives"])
            self.assertIn("objective.planet-surveyed", interior["objectives"])
            self.assertEqual(
                interior["objectives"]["objective.enemy-disabled"]["label"],
                "Enemy raider destroyed. Open navigation and choose the next system.",
            )
            tactical = next(item for item in interior["interactables"] if item.get("id") == "terminal.bridge-tactical")
            self.assertEqual(tactical["label"], "Bridge Tactical Console / Sensor Array")


if __name__ == "__main__":
    unittest.main()
