# Patch F: Definition Validators

Contract: `game-runtime-patch-F-definition-validators.v1`

Patch F adds the first runtime validator for the mother-ship interior definition. It is intentionally a behavior-preserving architecture patch: it does not add rooms, doors, objectives, enemies, or bridge systems. It checks that the data extracted in patches B through E still agrees with the playable game world.

## Purpose

The mother-ship bugs so far have mostly been data consistency failures:

```text
visual bridge exists, but movement bounds stop before it
prompt appears, but the E-key action has no useful handler
door rule says open route, but state/geometry disagree
terminal exists, but its location is not reachable
objective points to a room the player cannot enter
```

Patch F makes those relationships explicit through `shuttle3dValidateMotherShipInteriorConfig()`.

## Runtime additions

Patch F adds these runtime helpers to `scene-viewer.js`:

```text
shuttle3dNormalizeMotherShipValidationRules()
shuttle3dMotherShipSupportedInteractionHandlers()
shuttle3dPointInsideBounds()
shuttle3dBoundsContainBounds()
shuttle3dRoomForLocation()
shuttle3dPositionFromSpawn()
shuttle3dValidateMotherShipInteriorConfig()
```

`shuttle3dMotherShipInteriorConfig(scene)` now attaches:

```text
config.validationRules
config.validationReport
```

The renderer keeps the report as:

```text
this.shipDefinitionValidation
```

No player-facing behavior changes are expected.

## Checks

The validator currently checks:

```text
movement bounds exist and cover all rooms
room ids are unique
locations resolve to rooms
objectives point at valid locations
doors reference valid locations and are not locked
exits connect valid rooms/locations
terminal locations exist
interactions use supported safe handlers
interactables are inside their room and movement bounds
interactables reference registered interactions
spawns are inside their room and movement bounds
```

Warnings are used for non-blocking consistency notes, such as a door or terminal interactable without a matching state entry.

## Validation rules

Project metadata may set `motherShipInterior.validation`:

```json
{
  "requireRoomBoundsInsideMovement": true,
  "requireConnectedRooms": true,
  "requireReachableInteractables": true,
  "requireInteractionHandlers": true,
  "requireObjectiveTargets": true,
  "requireSpawnInsideRoom": true,
  "requireOpenDoors": true
}
```

Defaults are strict. A rule should only be disabled for temporary migrations or tests.

## Acceptance checks

Patch F should pass when:

```text
scene-viewer.js parses with node --check
the default mother-ship config reports validation ok
the bridge deck remains inside movement bounds
the bridge viewscreen and tactical console remain reachable
all current E-key actions have safe registered handlers
doors remain open/informational rather than progression locks
```

## Next patch

Patch G should begin renderer decomposition. The first safe split is to move validation and definition normalization out of the giant renderer flow without changing gameplay.
