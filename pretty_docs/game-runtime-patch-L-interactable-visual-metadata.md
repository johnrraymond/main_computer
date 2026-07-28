# Patch L: Interactable Visual Metadata

Contract: `game.runtime.patch-L.interactable-visual-metadata.v1`

Patch L follows Patch K's in-world hotspot render pass.

## Purpose

Patch K made every E-key interactable visible. Patch L makes those visible affordances content-authored instead of only kind-authored.

The rule is:

> Interactable behavior still comes from `action`, but hotspot presentation should come from normalized `visual` metadata.

This lets designers tune terminal, door, access, tactical, and viewscreen affordances without editing the renderer every time a console needs a different readable silhouette.

## Runtime changes

Patch L adds:

- `shuttle3dInteractableVisualDefaults(kind)`;
- `shuttle3dNormalizeMotherShipInteractableVisual(value, fallbackValue, kind)`;
- normalized `interactable.visual` metadata;
- hotspot rendering that uses `visual.color`, `visual.activeColor`, `visual.radiusScale`, `visual.height`, `visual.activeHeight`, `visual.baseSize`, `visual.terminalPanel`, and `visual.routeBeam`;
- project metadata examples for the current mother-ship interactables.

The E-key action path does not change. Interactions still dispatch through the Patch E registry.

## Behavior guarantee

Patch L should preserve gameplay behavior:

- movement bounds do not change;
- interactable positions and ranges do not change;
- prompts do not change;
- interaction handlers do not change;
- the bridge viewscreen and tactical console remain usable;
- doors remain open/informational, not locks.

## Authoring guidance

Use `visual` only for presentation.

```json
{
  "id": "terminal.bridge-tactical",
  "kind": "terminal",
  "position": [2.85, -36.7],
  "range": 1.85,
  "action": "fireBridgeTacticalConsole",
  "visual": {
    "color": "#f97316",
    "activeColor": "#fef3c7",
    "radiusScale": 0.38,
    "height": 0.72,
    "activeHeight": 1.02,
    "baseSize": 0.2,
    "terminalPanel": true,
    "routeBeam": false
  }
}
```

The renderer supplies fallback visuals by `kind`, so older projects without explicit visual metadata still show hotspots.

## Acceptance checks

A Patch L build is acceptable when:

- normalized interactables always include `visual`;
- the hotspot pass reads `target.visual`;
- existing interaction dispatch behavior is unchanged;
- current project metadata includes visual examples;
- `game-definition.v1` documents interactable visual metadata;
- `node --check` passes for `scene-viewer.js`.

## Follow-up

Future patches can extract terminal/console meshes into data-defined props or merge prop markers and interactable hotspots into a single authored affordance system.
