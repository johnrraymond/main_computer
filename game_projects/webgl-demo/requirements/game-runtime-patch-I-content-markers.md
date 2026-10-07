# Patch I: Data-Defined Content Markers

Contract: `game.runtime.patch-I.content-markers.v1`

Patch I continues the content-first expansion track that started in Patch H.

## Purpose

Move repeated mother-ship room markers out of one-off renderer calls and into `motherShipInterior.props`.

The goal is not to add new gameplay. The goal is to reduce another class of geometry/state drift by letting small visual affordances live next to the room, terminal, route, and objective data that they support.

## Runtime changes

Patch I adds:

- a `map-marker` procedural prop kind;
- data-authored markers for Bay Ops, Engineering, Medbay, Science/Ops, Bridge Access, and Bridge Viewscreen targets;
- renderer support for drawing map markers from `appendMotherShipInteriorProps(builder, nowMs)`;
- default project metadata entries for those map markers.

Patch I also removes the older local `mapMarker(...)` helper calls from the mother-ship render pass. The same marker intent now flows through prop data.

## Supported marker content

A `map-marker` prop uses:

```json
{
  "id": "prop.marker.bridge-viewscreen",
  "room": "bridge.deck",
  "kind": "map-marker",
  "position": [0.0, -37.15],
  "size": [0.36, 0.66],
  "target": "terminal.bridge-viewscreen",
  "label": "Bridge viewscreen marker"
}
```

The first size entry controls the floor plate width. The second controls the height of the vertical marker beam.

## Acceptance checks

A Patch I build is acceptable when:

- existing gameplay remains unchanged;
- the renderer supports `kind === "map-marker"`;
- default project metadata includes the new marker props;
- markers remain validated through Patch F/Patch H prop reachability rules;
- the bridge viewscreen and bridge tactical console interactions are unchanged.

## Follow-up

Future content patches should keep moving small, repeated visual affordances into content data before adding new hand-built room branches.
