# Patch J: Prop Target Validation

Contract: `game.runtime.patch-J.prop-target-validation.v1`

Patch J follows the content-first work from Patches H and I.

## Purpose

Data-defined props can now point at other content through an optional `target` field. Map markers, status panels, route beacons, and later world labels use this to explain what they support.

Patch J makes those references verifiable. The renderer should not ship a marker that points at a missing terminal, a status panel that points at an unknown system, or a route sign that references a deleted room.

## Runtime changes

Patch J adds:

- `requirePropTargets` to the mother-ship validation profile;
- target validation inside `shuttle3dValidateMotherShipInteriorConfig`;
- support for known prop targets:
  - room ids and location ids;
  - terminal ids;
  - door ids;
  - interactable ids;
  - objective ids;
  - the explicit runtime system target `enemyShip`;
- project metadata defaults that opt into `requirePropTargets`.

This is a validation hardening patch. It should not change normal gameplay, rendering, interactions, movement, bridge access, or tactical-console behavior.

## Acceptance checks

A Patch J build is acceptable when:

- existing gameplay remains unchanged;
- `requirePropTargets` is present in runtime validation and schema docs;
- every default prop with a `target` resolves to known content;
- missing prop targets would produce a validation error;
- `node --check` passes for `scene-viewer.js`;
- targeted tests still pass.

## Follow-up

Future content patches should prefer targeted props over one-off renderer markers, but any prop target must resolve through the Patch J validation set.
