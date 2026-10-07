# Game Runtime Patch T — Save/Migration Model

## Purpose

Patch T adds an explicit compatibility layer for the mother-ship data-driven runtime.

The architecture patches through S made more of the playable ship depend on project JSON:

```text
rooms
movement
exits
spawns
props
interactables
interactions
interaction effect metadata
room visuals
room geometry
viewscreen display metadata
renderer module seams
```

That is the correct direction, but it means older project files and partially edited project files need a safe load path. Patch T centralizes that load path instead of asking each renderer pass or interaction handler to guess which fields might be missing.

## Runtime contract

Current mother-ship definitions declare:

```text
schema: game.motherShipInterior.v1
definitionVersion: game.motherShipInterior.definition.v2
stateVersion: game.motherShipInterior.state.v1
```

Before validation or rendering, `scene-viewer.js` now passes the raw authored definition through:

```text
shuttle3dMigrateMotherShipInteriorDefinition(...)
```

The migration pass:

```text
detects missing/legacy version metadata
fills safe scalar defaults
fills missing map/list sections from the current level defaults
preserves authored values when they exist
adds a migration report to the normalized runtime config
```

## Game-editor read contract

The game-editor project read route mirrors the runtime defaulting model for legacy project JSON.

When a project is read through:

```text
/api/applications/game-editor/project/read
```

the route now applies mother-ship runtime migrations to the response payload. This keeps the browser editor and game surface working with older project shapes without silently rewriting `project.json` on disk.

## Migration report

Normalized runtime/read payloads may include:

```json
{
  "migration": {
    "schema": "game.motherShipInterior.migration.v1",
    "sourceDefinitionVersion": "legacy-unversioned",
    "targetDefinitionVersion": "game.motherShipInterior.definition.v2",
    "stateVersion": "game.motherShipInterior.state.v1",
    "migratedAtLoad": true,
    "migrations": [
      "legacy-unversioned->game.motherShipInterior.definition.v2"
    ],
    "defaultsApplied": [
      "rooms",
      "props",
      "interactions"
    ]
  }
}
```

This report is load-time compatibility metadata. It does not imply a file rewrite.

## Behavior preserved

Patch T should not change gameplay:

```text
shuttle boarding defense
console hover + E pilot mode
shuttle flight and docking
mother-ship shuttle-bay handoff
open-door traversal rule
room geometry rendering
content-defined bridge viewscreen
viewscreen tracking interaction
tactical console firing interaction
enemy ship disabled state
```

## Acceptance checks

- Current project files declare `schema`, `definitionVersion`, and `stateVersion`.
- Runtime bundle contains the migration/defaulting helper.
- Legacy project read payloads receive missing modern fields at load time.
- Patch R validator harness still passes.
- The no-locked-door rule remains enforced.
- `new_patch.py --dry-run` verifies exact replacement paths.

## Follow-up

Patch U can resume content expansion now that the project definition shape has a predictable version/default path. Later migration patches should only change `definitionVersion` when the load semantics change.
