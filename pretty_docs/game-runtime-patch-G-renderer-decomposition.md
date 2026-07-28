# Patch G: Renderer Decomposition

Contract: `game.runtime-patch-G-renderer-decomposition.v1`

Patch G is the first behavior-preserving renderer decomposition patch. It does not add new gameplay. It creates named seams inside the shuttle 3D renderer so later patches can move systems into smaller modules without changing the currently playable shuttle, mother-ship, bridge, viewscreen, and tactical-console path.

## Problem

`scene-viewer.js` still owns too many responsibilities:

```text
WebGL setup
frame/camera state
gameplay subsystem configuration
combat runtime state
mother-ship state
interaction registry
geometry construction
buffer upload
canvas lifecycle hooks
draw loop startup
```

That made it hard to tell whether a patch was changing gameplay or only changing renderer plumbing.

## Implementation

Patch G keeps the existing class and file in place, but splits constructor bootstrapping into explicit internal seams:

```text
initializeRendererFrameState(scene)
initializeGameplaySubsystems(scene)
initializeCombatRuntimeState()
initializeGeometryBuffers()
initializeCanvasLifecycle(canvas)
```

The constructor now establishes the WebGL context, calls these seams in order, then starts the existing draw loop.

## Behavioral contract

Patch G should preserve behavior:

```text
shuttle boarding defense still starts
console hover + E still enters pilot mode
shuttle flight and docking still work
mother-ship shuttle bay handoff still works
interior movement still uses Patch C room/movement data
E-key ship interactions still use Patch E registry data
Patch F validation still runs
bridge viewscreen and tactical console still work
```

## Future extraction path

Later decomposition patches can move these seams into smaller files or classes:

| Current seam | Future target |
| --- | --- |
| `initializeRendererFrameState` | frame/input/camera runtime |
| `initializeGameplaySubsystems` | game-state and config loader |
| `initializeCombatRuntimeState` | combat subsystem |
| `initializeGeometryBuffers` | renderer buffer manager |
| `initializeCanvasLifecycle` | WebGL/canvas lifecycle wrapper |

## Acceptance checks

- `scene-viewer.js` passes JavaScript syntax validation.
- The served Game Surface still contains all Patch B-F runtime symbols.
- The renderer exposes the Patch G seams.
- No project data behavior changes are required.
