# Game Runtime Patch S — Renderer Module Split

## Purpose

Patch S begins the browser-safe split of the large `scene-viewer.js` renderer into smaller shuttle-3D renderer modules.

This patch is intentionally behavior-preserving. It does not add rooms, change progression, change combat, or reintroduce locked doors. It only moves existing render-pass implementation code behind explicit module seams.

## Scope

Patch S adds these script modules:

```text
main_computer/web/applications/scripts/shuttle3d-renderer-modules.js
main_computer/web/applications/scripts/shuttle3d-render-room-geometry.js
main_computer/web/applications/scripts/shuttle3d-render-viewscreens.js
```

The modules are included before `scene-viewer.js` in `applications.html`, so the existing single-page static include pipeline can concatenate them without ES module imports or a bundler.

## Module registry

`shuttle3d-renderer-modules.js` creates:

```text
globalThis.MainComputerShuttle3DRendererModules
```

The registry supports:

```text
register(name, methods)
method(name, methodName)
call(name, methodName, context, ...args)
```

`scene-viewer.js` keeps the public renderer method names as delegating seams, so existing call sites remain stable.

## Extracted render passes

Patch S moves the heavy implementation for these existing passes:

```text
appendMotherShipRoomGeometry(builder, nowMs)
appendMotherShipViewscreenDisplay(builder, prop, nowMs)
appendEnemyShipTacticalDisplay(builder, prop, nowMs)
```

The room-geometry implementation now lives in:

```text
shuttle3d-render-room-geometry.js
```

The viewscreen/display implementation now lives in:

```text
shuttle3d-render-viewscreens.js
```

`scene-viewer.js` still owns the renderer class, frame lifecycle, gameplay state, and call order. The extracted modules are plain browser-safe IIFEs registered on `globalThis`.

## Behavioral contract

Patch S should preserve:

```text
shuttle boarding defense
console hover + E pilot mode
shuttle flight and docking
mother-ship shuttle-bay handoff
mother-ship walking
open-door traversal rule
room geometry rendering from rooms[].geometry
room visual affordances from rooms[].visual
content-defined bridge viewscreen rendering
bridge viewscreen tracking interaction
tactical console firing interaction
enemy ship disabled state
```

## Acceptance checks

- `scene-viewer.js` passes JavaScript syntax validation.
- Each new shuttle-3D renderer module passes JavaScript syntax validation.
- `applications.html` includes the module registry and extracted render modules before `scene-viewer.js`.
- The served application bundle contains the extracted module registrations.
- `scene-viewer.js` delegates the extracted passes through `MainComputerShuttle3DRendererModules`.
- The Patch R validator harness still passes.

## Follow-up extraction candidates

Later patches can extract additional seams carefully:

```text
shuttle3d-interactions.js
shuttle3d-validation.js
shuttle3d-render-props.js
shuttle3d-render-hotspots.js
shuttle3d-render-room-visuals.js
shuttle3d-game-state.js
```

Each future split should keep replacement-file safety and exact `new_patch.py --dry-run` verification.
