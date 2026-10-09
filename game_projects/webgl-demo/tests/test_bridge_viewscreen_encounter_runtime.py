from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
SCRIPT_ROOT = GAME_ROOT / "web" / "scripts"


def test_multirate_contract_authority_projection_presentation_and_renderer_load_in_order() -> None:
    game = json.loads((GAME_ROOT / "game.json").read_text(encoding="utf-8"))
    scripts = game["web"]["bundles"]["runtime-before-routing"]
    contract = "web/scripts/space-captain-multirate-contract.js"
    authority = "web/scripts/bridge-encounter-runtime.js"
    projection = "web/scripts/bridge-viewscreen-projection.js"
    wrapper = "web/scripts/bridge-viewscreen-encounter-runtime.js"
    presentation = "web/scripts/bridge-viewscreen-presentation.js"
    renderer = "web/scripts/bridge-viewscreen-renderer.js"
    registry = "web/scripts/shuttle3d-renderer-modules.js"
    legacy_viewscreens = "web/scripts/shuttle3d-render-viewscreens.js"
    scene = "web/scripts/scene-viewer.js"
    for script in (contract, authority, projection, wrapper, presentation, renderer, registry, scene):
        assert script in scripts
    assert legacy_viewscreens not in scripts
    assert not (SCRIPT_ROOT / "shuttle3d-render-viewscreens.js").exists()
    assert scripts.index(contract) < scripts.index(authority) < scripts.index(projection) < scripts.index(wrapper) < scripts.index(presentation) < scripts.index(renderer) < scripts.index(registry) < scripts.index(scene)


def test_phase_one_contract_centralizes_defaults_and_freezes_rules_not_tuning_values() -> None:
    contract = (SCRIPT_ROOT / "space-captain-multirate-contract.js").read_text(encoding="utf-8")
    assert 'SCHEMA = "game.spaceCaptainMultirateContract.v1"' in contract
    assert 'tacticalSliceSeconds: 5' in contract
    assert 'physicsStepSeconds: 0.1' in contract
    assert 'tacticalSliceSeconds: Object.freeze([1, 2, 3, 4, 5])' in contract
    assert 'physicsStepSeconds: Object.freeze([0.05, 0.1, 0.2])' in contract
    assert 'renderHz: Object.freeze([30, 60, 120, 165])' in contract
    assert 'tacticalGrid: "immutable-configured-grid"' in contract
    assert 'physicsGrid: "immutable-configured-grid"' in contract
    assert 'exactEventAnchors: true' in contract
    assert 'renderCadence: "requestAnimationFrame"' in contract
    assert 'renderMayMutateAuthority: false' in contract
    assert 'predictionMayMutateAuthority: false' in contract
    assert 'cameraMode: "soft-target-lock"' in contract
    assert 'physicsStepSeconds must be smaller than tacticalSliceSeconds' in contract


def test_phase_two_authority_exposes_immutable_projection_input_without_camera_logic() -> None:
    source = (SCRIPT_ROOT / "bridge-encounter-runtime.js").read_text(encoding="utf-8")
    assert 'SCHEMA: "game.bridgeEncounterRuntime.v1"' in source
    assert 'readAuthorityState()' in source
    assert 'schema: "game.bridgeEncounterAuthorityState.v1"' in source
    assert 'advance(nowMs, options = {})' in source
    assert 'BRIDGE_ENCOUNTER_AUTHORITY_STALE' in source
    assert 'combatState()' in source
    assert 'targetHullPercent' in source
    snapshot_body = source.split('snapshot(nowMs)', 1)[1].split('\n    }\n  }', 1)[0]
    assert 'this._advanceAuthorityTo(simulationSeconds);' not in snapshot_body
    assert '_updateCamera' not in source
    assert 'cameraX' not in source
    assert 'tacticalSliceSeconds: 5' not in source
    assert 'physicsStepSeconds: 0.1' not in source


def test_phase_three_projection_owns_prediction_and_camera_but_has_no_combat_mutators() -> None:
    source = (SCRIPT_ROOT / "bridge-viewscreen-projection.js").read_text(encoding="utf-8")
    assert 'SCHEMA = "game.bridgeViewscreenProjection.v1"' in source
    assert 'AUTHORITY_SCHEMA = "game.bridgeEncounterAuthorityState.v1"' in source
    assert 'function predictShips(authorityState, simulationSeconds)' in source
    assert 'function project({authorityState, simulationSeconds, presentationState = null} = {})' in source
    assert 'BRIDGE_VIEWSCREEN_PROJECTION_AUTHORITY_STALE' in source
    assert 'mode: "enemy-soft-target-lock"' in source
    for forbidden in ('.advance(', '.command(', '.playerFire(', '_integrateTo(', '_applyImpact'):
        assert forbidden not in source


def test_viewscreen_wrapper_delegates_projection_instead_of_implementing_camera_math() -> None:
    source = (SCRIPT_ROOT / "bridge-viewscreen-encounter-runtime.js").read_text(encoding="utf-8")
    assert 'MainComputerBridgeEncounterRuntime' in source
    assert 'MainComputerBridgeViewscreenProjection' in source
    assert 'this.authority.readAuthorityState()' in source
    assert 'PROJECTION.project({' in source
    assert 'PROJECTION_SCHEMA: PROJECTION.SCHEMA' in source
    assert 'BRIDGE_VIEWSCREEN_AUTHORITY_STALE' in source
    assert '_updateCamera' not in source
    assert 'Math.exp(' not in source
    assert 'cameraX' not in source
    assert '_integrateTo(' not in source
    assert '_applyImpact' not in source
    assert 'targetHullPercent =' not in source


def test_scene_game_update_advances_authority_before_viewscreen_render_observes_it() -> None:
    source = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
    assert 'this.bridgeEncounterRuntime = globalThis.MainComputerBridgeEncounterRuntime?.create?.() || null;' in source
    assert 'MainComputerBridgeViewscreenEncounterRuntime?.create?.({authority: this.bridgeEncounterRuntime})' in source
    assert 'updateBridgeViewscreenEncounter(nowMs' in source
    assert 'encounterResult = runtime.advance(nowMs, {active: true});' in source
    assert 'encounterProjection = projectionRuntime.snapshot(nowMs, {active: true});' in source
    assert 'this.bridgeViewscreenPresentationFrame = system.present(presentationInputs);' in source
    assert 'if (this.bridgeViewscreenProjectionFrame) return this.bridgeViewscreenProjectionFrame;' in source
    update_index = source.index('this.updateBridgeViewscreenEncounter(frameTime);')
    geometry_index = source.index('this.dynamicGeometry = this.buildDynamicGeometry(frameTime);', update_index)
    assert update_index < geometry_index


def test_scene_no_longer_stores_bridge_combat_truth_in_ship_flags() -> None:
    source = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
    defaults = source[source.index('function shuttle3dMotherShipInteriorStateDefaults'):source.index('function shuttle3dNormalizeMotherShipFlags')]
    for legacy in ('bridgeTacticalArmed', 'bridgeTacticalShotsFired', 'bridgeTacticalLastFireAtMs', 'enemyShipHullPercent', 'enemyShipDisabled'):
        assert legacy not in defaults
    for forbidden_assignment in ('flags.enemyShipHullPercent =', 'flags.enemyShipDisabled =', 'flags.bridgeTacticalShotsFired =', 'flags.bridgeTacticalLastFireAtMs =', 'flags.bridgeTacticalArmed ='):
        assert forbidden_assignment not in source
    assert 'this.bridgeEncounterRuntime?.command?.({type: "fire-primary-weapon"}, nowMs)' in source
    assert 'runtime.eventsSince(this.bridgeEncounterLastUiEventSequence || 0)' in source


def test_actual_viewscreen_renderer_consumes_presentation_only_and_never_reads_runtime_state() -> None:
    source = (SCRIPT_ROOT / "bridge-viewscreen-renderer.js").read_text(encoding="utf-8")
    assert 'PRESENTATION_SCHEMA = "game.bridgeViewscreenPresentation.v1"' in source
    assert 'function render({builder, surface, presentation} = {})' in source
    assert 'framePresentation.display?.powered === false' in source
    for forbidden in (
        'shipState',
        'bridgeEncounterRuntime',
        'bridgeViewscreenEncounterRuntime',
        'navigationSnapshot',
        'astrometricSnapshot',
        'enemyShipHullPercent',
        'enemyShipDisabled',
        'bridgeTacticalShotAgeMs',
        'bridgeTacticalImpactAgeMs',
        'bridgeViewscreenTrackingActive',
        '.advance(',
        '.command(',
        'requestAnimationFrame',
    ):
        assert forbidden not in source

    scene = (SCRIPT_ROOT / "scene-viewer.js").read_text(encoding="utf-8")
    draw_start = scene.index('const drawViewscreen = (prop) => {')
    draw_end = scene.index('};', draw_start) + 2
    draw_body = scene[draw_start:draw_end]
    assert 'MainComputerBridgeViewscreenRenderer' in draw_body
    assert 'bridgeViewscreenPresentationSnapshot' in draw_body
    assert 'renderer.render({' in draw_body
    assert 'appendMotherShipViewscreenDisplay' not in draw_body


def test_bridge_chromium_probe_explicitly_advances_before_snapshot() -> None:
    source = (GAME_ROOT / "tools" / "bridge_viewscreen_encounter_chromium_probe.js").read_text(encoding="utf-8")
    assert 'runtime.advance(simMs,{active:true});' in source
    assert 'runtime.snapshot(simMs,{active:true});' in source
    assert source.index('runtime.advance(simMs,{active:true});') < source.index('runtime.snapshot(simMs,{active:true});')
    assert 'runtimeStartsOnFirstAnimationFrame' in source
    assert 'runtimeCoversFullScriptedEncounter' in source
