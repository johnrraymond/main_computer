# Patch N: Room Visual Metadata

Contract: `game.runtime.patch-N.room-visual-metadata.v1`

Patch N continues the content-first mother-ship runtime track after Patch M.

## Purpose

The ship already has data-defined rooms, movement bounds, props, interactables, and terminal-console props. Patch N makes room-level visual guidance data-driven too.

The intent is to keep the rendered room affordances aligned with the same room data used for movement, location detection, objectives, and validation.

## Runtime changes

Patch N adds:

- `shuttle3dRoomVisualDefaults(kind)`
- `shuttle3dNormalizeMotherShipRoomVisual(...)`
- `rooms[].visual`
- `appendMotherShipRoomVisuals(builder, nowMs)`

The render pass draws additive room boundary and wayfinding affordances from normalized room data. It does not change collision, doors, E-key behavior, objectives, combat, bridge viewscreen behavior, or tactical console behavior.

## Supported room visual fields

```text
color
edgeColor
labelColor
boundary
floorBand
label
labelHeight
```

If a project omits room visual metadata, the runtime supplies defaults by room kind.

## Authoring rule

Room visual metadata must stay attached to the room definition. Avoid adding one-off renderer calls just to make a room readable.

Good:

```json
{
  "id": "bridge.deck",
  "kind": "bridge",
  "bounds": { "minX": -4.65, "maxX": 4.65, "minZ": -39.35, "maxZ": -31.25 },
  "visual": {
    "color": "#ef4444",
    "edgeColor": "#f97316",
    "labelColor": "#fee2e2",
    "boundary": true,
    "floorBand": true,
    "label": true
  }
}
```

Avoid:

```text
hardcoded bridge-only boundary beams outside the room definition
```

## Acceptance checks

- Existing mother-ship route remains playable.
- Room visuals render from `motherShipInterior.rooms`.
- Old projects without `rooms[].visual` still get default room visuals.
- Bridge viewscreen and tactical console behavior remain unchanged.
- No locked-door mechanic returns.

## Verification

Use:

```bash
node --check main_computer/web/applications/scripts/scene-viewer.js
python -m py_compile main_computer/viewport_routes_game.py
python new_patch.py game_runtime_patch_N_room_visual_metadata.zip --dry-run
```
