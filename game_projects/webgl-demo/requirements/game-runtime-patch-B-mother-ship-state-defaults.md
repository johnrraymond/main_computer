# Patch B: Mother-Ship State Defaults Extraction

Contract: `game.runtime-patch-B-mother-ship-state-defaults.v1`

Patch B is the first implementation step after the architecture/schema documents. Its goal is to centralize the default state for the current mother-ship slice without changing player-facing behavior.

## Scope

Patch B keeps the existing playable route intact:

```text
shuttle docking
→ shuttle-bay control handoff
→ starboard access
→ Bay Operations
→ security / corridor / engineering / bridge route
→ bridge viewscreen tracking
→ tactical console volleys
```

The patch does not add new rooms, lock doors, alter combat, or change the bridge tactical flow.

## New runtime boundary

The renderer now has a single default-state factory for mother-ship interior gameplay:

```text
shuttle3dMotherShipInteriorStateDefaults()
```

That factory is the source for:

```text
initial location
initial objective
ship power/security labels
known location labels
objective definitions
door state defaults
terminal state defaults
bridge/enemy-ship flags
```

The existing `shuttle3dMotherShipInteriorConfig(scene)` still accepts legacy project metadata. Old projects can continue to provide `initialLocation`, `initialObjective`, `power`, `security`, `doors`, `terminals`, and `flags`. The runtime normalizes those values into one canonical `stateDefaults` object before creating `shipState`.

## Why this matters

The previous implementation duplicated default-state handling in more than one place. That makes it easier for content to drift:

```text
a terminal default exists in metadata but not in shipState
an objective exists in HUD text but not in runtime state
a bridge flag exists in tactical code but not in default initialization
door-lock cleanup lives separately from the state factory
```

Patch B makes the default state a named runtime boundary that future patches can extract into project data or schema-validated game definitions.

## Acceptance checks

Patch B is acceptable when:

```text
existing gameplay strings still appear in the served Game Surface
createShipState() delegates through createShipStateFromDefaults()
stateDefaults includes door, terminal, objective, and bridge tactical flag defaults
no locked-door mechanic is reintroduced
bridge viewscreen and tactical console state remain initialized
enemy ship hull and tactical shot counters remain normalized
```

## Next patch

Patch C should extract rooms and movement bounds into a level definition while preserving current behavior. That patch should specifically cover shuttle-bay, Bay Ops, corridor, engineering, bridge access, bridge deck, spawn/facing data, and reachability checks.
