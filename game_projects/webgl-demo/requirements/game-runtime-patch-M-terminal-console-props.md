# Patch M: Terminal Console Props

Contract: `game.runtime.patch-M.terminal-console-props.v1`

Patch M follows Patch L's interactable visual metadata.

## Purpose

Patch K made E-key affordances visible and Patch L made those affordances styleable. Patch M moves the physical console bodies for current terminals into `motherShipInterior.props`.

The rule is:

> Interactable data owns prompt/range/action behavior. Prop data owns non-interactive visual bodies and readable silhouettes.

This keeps terminal presentation data-first without changing how the E-key dispatch path works.

## Runtime changes

Patch M adds:

- `terminal-console` as a supported procedural prop kind;
- terminal-console rendering in `appendMotherShipInteriorProps(builder, nowMs)`;
- data-defined console bodies for Bay Operations, Engineering Power, Bridge Viewscreen, and Bridge Tactical;
- active-target highlighting that follows the existing `shipInteractionTarget()` result;
- schema documentation for the new prop kind.

## Behavior guarantee

Patch M should preserve gameplay behavior:

- movement bounds do not change;
- interactable positions and ranges do not change;
- prompts do not change;
- interaction handlers do not change;
- the bridge viewscreen and tactical console remain usable;
- doors remain open/informational, not locks.

## Authoring guidance

Use a `terminal-console` prop when a terminal needs a visible physical console body.

```json
{
  "id": "prop.console.bridge-tactical",
  "room": "bridge.deck",
  "kind": "terminal-console",
  "position": [2.85, -36.7],
  "size": [0.95, 0.54, 0.82],
  "color": "#f97316",
  "emissive": true,
  "facing": "west",
  "target": "terminal.bridge-tactical",
  "label": "Bridge tactical console body"
}
```

The `target` should point at the terminal or interactable it visually represents. Patch J validation checks that the target resolves.

## Acceptance checks

A Patch M build is acceptable when:

- `terminal-console` is rendered by the content prop pass;
- current project metadata includes console-body props for current terminals;
- prop targets point at known terminals;
- existing E-key behavior remains routed through the interaction registry;
- `game-definition.v1` documents the new prop kind;
- `node --check` passes for `scene-viewer.js`.

## Follow-up

Future patches can extract more hardcoded room fixtures into props, then move wall/corridor geometry toward room- and exit-driven render data.
