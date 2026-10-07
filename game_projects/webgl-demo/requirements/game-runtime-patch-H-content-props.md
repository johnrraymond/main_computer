# Patch H: Content Props Render Pass

Contract: `game.runtime.patch-H.content-props.v1`

Patch H starts the content-first expansion track after the state, room, interaction, validation, and renderer-seam patches.

## Purpose

Move non-interactive ship visual content into `motherShipInterior.props` so future room polish can be authored as data instead of hand-editing more one-off geometry into `appendShuttleBayScene()`.

This patch intentionally keeps gameplay behavior stable. The player route, viewscreen, tactical console, objectives, and no-locked-door rule remain unchanged.

## Runtime changes

Patch H adds:

- `motherShipInterior.props` default data for ship signage, route markers, bridge access beacons, tactical-console markers, and enemy-ship status panels;
- `shuttle3dNormalizeMotherShipProps()` for safe prop defaulting;
- `appendMotherShipInteriorProps(builder, nowMs)` as the first content-first render pass;
- `requireRenderableProps` validation so authored props must point at reachable rooms and movement bounds.

## Authored prop kinds

The first renderer pass supports lightweight procedural props:

| Kind | Purpose |
| --- | --- |
| `floor-marker` | Floor guide plates, route highlights, console target markers |
| `sign` | Wall-mounted destination signs and room labels |
| `beacon` | Vertical guide beacons for important route transitions |
| `light-strip` | Data-defined accent beams for future corridor polish |
| `status-panel` | Small status screens that can reflect runtime state, such as enemy ship status |

These are intentionally simple. Later patches can add richer content kinds without changing room, movement, or interaction architecture.

## Acceptance checks

A Patch H build is acceptable when:

- existing gameplay remains unchanged;
- project metadata includes `motherShipInterior.props`;
- the renderer normalizes props through `shuttle3dNormalizeMotherShipProps()`;
- the renderer draws props through `appendMotherShipInteriorProps(builder, nowMs)`;
- validators reject props outside their declared room or movement bounds;
- bridge route markers and bridge tactical/status props are present in the default project metadata.

## Future follow-up

Patch I should use this props pass to start replacing hardcoded room-decoration blocks with data-defined content groups, one room at a time.
