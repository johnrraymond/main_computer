# Patch K: Interactable Hotspot Render Pass

Contract: `game.runtime.patch-K.interactable-hotspots.v1`

Patch K follows the data-first interaction work from Patches D and E and the visual-content work from Patches H through J.

## Purpose

The game already knows where every E-key ship interaction lives through `motherShipInterior.interactables`. Patch K makes that data visible in the world.

The rule is:

> If the HUD can show a prompt for an E-key interaction, the ship scene should also render a matching in-world hotspot at that interaction position.

This reduces the chance that a prompt, terminal, door, or access point exists only in UI logic while the player sees no clear physical affordance in the ship.

## Runtime changes

Patch K adds:

- `appendMotherShipInteractableHotspots(builder, nowMs)`;
- a call to that render pass after data-defined props render;
- terminal, access, and door hotspot styling derived from each interactable's `kind`;
- an active-target highlight for the interactable currently selected by `shipInteractionTarget()`.

The hotspot pass reads normalized `motherShipInterior.interactables`; it does not introduce new gameplay state or new interaction handlers.

## Behavior guarantee

Patch K should preserve gameplay behavior:

- movement bounds do not change;
- E-key dispatch still flows through the Patch E interaction registry;
- validation still flows through the Patch F/J validation path;
- the bridge viewscreen and tactical console remain usable;
- doors remain open/informational, not locks.

## Acceptance checks

A Patch K build is acceptable when:

- every normalized interactable can be rendered as a small in-world hotspot;
- the active prompt target is visually emphasized;
- `appendMotherShipInteractableHotspots(builder, nowMs)` is present in the Game Surface;
- existing targeted gameplay tests still pass;
- `node --check` passes for `scene-viewer.js`.

## Follow-up

Future patches can add richer per-interactable visual metadata, but the baseline renderer should continue to provide a fallback hotspot for every E-key target.
