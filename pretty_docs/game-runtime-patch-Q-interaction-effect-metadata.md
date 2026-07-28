# Game Runtime Patch Q: Interaction Effect Metadata

## Purpose

Patch Q keeps the safe mother-ship interaction registry, but adds data-authored expectations for what a successful E-key action should visibly change.

This patch is meant to catch the class of defect where a prompt appears, the player presses E, and nothing obvious changes.

## Data added

Each `motherShipInterior.interactions` entry may now include:

```json
{
  "changesState": ["objectiveId", "lastInteractionStatus"],
  "successStatus": "Bay Operations online. Route to Security Checkpoint is available.",
  "nextObjective": "objective.enter-corridor"
}
```

`nextObjective` may be a single objective id or a list when a handler branches, such as the tactical console choosing between `objective.enemy-attack` and `objective.enemy-disabled`.

## Runtime behavior

The runtime still calls only registered handler functions.

Patch Q does not let project data execute arbitrary code. The new fields are normalized into the registry as observable expectations and are used by validation/documentation. Existing handler behavior remains authoritative for the actual state transition.

## Validation

Patch Q adds `requireInteractionEffects`.

When enabled, validation checks:

```text
each interaction handler still resolves through the supported handler registry
changesState entries are non-empty
successStatus or status is present, otherwise validation warns
nextObjective values reference known objectives
```

## Gameplay contract

Gameplay should be unchanged:

```text
shuttle cockpit
→ docking
→ shuttle-bay arrival
→ Bay Operations
→ Engineering Power
→ bridge viewscreen
→ tactical console
→ enemy ship disabled
```

Mother-ship doors remain open/informational and are not progression locks.
