# Game Runtime Patch O.1: Docking Handoff Void Guard

Patch O.1 is a corrective follow-up to Patch O. It keeps the room-geometry
extraction intact, but fixes a live handoff/visibility defect where the player
could appear to be thrown into empty space after docking.

## Defect

After the shuttle docking cutscene, held flight keys could immediately become
mother-ship walking keys. If the player kept holding `W` through the transition,
the camera could continue down the main corridor before the player had visual
context.

The corridor trunk also had only a floor shell and one centerline beam in
`rooms[].geometry`, which made the long center route read like a void when the
player reached it quickly.

## Runtime change

The docking-to-bay handoff now records movement keys that were already held when
bay control starts. Those keys are suppressed until released, and newly pressed
movement keys are ignored for a short handoff window.

This keeps the player at the shuttle-bay arrival point unless they deliberately
release and press movement again after the transition.

## Data change

`corridor.trunk.geometry` now includes data-defined side rail boxes, floor guide
strips, and overhead/side beams. The route remains open; these are visual
affordances only and do not add locked-door progression.

## Door rule

Mother-ship doors are still not progression locks. This patch does not add a
closed collider, route gate, or required door interaction.

## Regression intent

The corrected flow is:

```text
dock shuttle
handoff starts at the shuttle-bay spawn
held W/S/A/D from flight does not carry the player down the corridor
the corridor trunk has visible rails/lights instead of a void-like center path
viewscreen and tactical console gameplay remain unchanged
```
