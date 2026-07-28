# Game Runtime Patch P: Content-defined Viewscreens and Displays

Patch P moves the bridge viewscreen surface and enemy-ship tactical display into
mother-ship prop metadata.

## Goal

The bridge viewscreen is now authored as a normal content prop:

```json
{
  "id": "prop.display.bridge-viewscreen",
  "room": "bridge.deck",
  "kind": "viewscreen",
  "position": [0.0, -39.12],
  "size": [6.9, 2.1, 0.08],
  "display": "enemyShipTactical",
  "target": "enemyShip"
}
```

The renderer chooses a display program from `prop.display` instead of calling a
bridge-specific viewscreen draw routine from the main room scene.

## Runtime changes

Patch P adds:

```text
appendMotherShipViewscreenDisplay(builder, prop, nowMs)
appendEnemyShipTacticalDisplay(builder, prop, nowMs)
```

`appendMotherShipInteriorProps(builder, nowMs)` now handles
`kind: "viewscreen"` and passes the prop to the display renderer. The enemy ship
display still reads live state from the existing bridge tactical systems:

```text
bridgeViewscreenTrackingActive
bridgeTacticalLastFireAtMs
enemyShipHullPercent
enemyShipDisabled
```

This keeps gameplay behavior unchanged while making the display's position,
size, room ownership, target, and display program visible in project data.

## Validation

Patch P extends prop validation so authored `display` values must resolve to a
supported display program. The first supported display id is:

```text
enemyShipTactical
```

The existing `target` validation still verifies that `target: "enemyShip"` is an
allowed runtime system target.

## Door and progression rule

This patch does not change movement, doors, objectives, or interaction handlers.
Mother-ship doors remain open/informational and must not become progression
locks.
