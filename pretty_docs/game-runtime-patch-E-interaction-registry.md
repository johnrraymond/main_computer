# Game Runtime Patch E: Interaction Registry

Contract: `game.runtime-patch-E.interaction-registry.v1`

Patch E is the first runtime step after extracting mother-ship interactables. Patch D made prompts, ranges, and action ids data-driven. Patch E makes the action ids dispatch through a registry instead of a one-off `switch` inside the E-key path.

## Goal

Keep current gameplay unchanged while creating a stable interaction boundary:

```text
interactable data
→ action id
→ normalized interaction definition
→ registered safe handler
→ state/objective/HUD update
```

The renderer may still own the concrete handlers for this patch, but the E-key path should no longer hardcode the behavior list directly.

## Scope

Patch E covers the current mother-ship interactions:

| Action id | Handler | Expected result |
| --- | --- | --- |
| `enterBayOpsAccess` | `enterBayOpsAccess` | Move from shuttle bay into Bay Operations access |
| `activateBayOperationsTerminal` | `activateBayOperationsTerminal` | Bring Bay Ops online and advance corridor objective |
| `restoreEngineeringPower` | `restoreEngineeringPower` | Set main power/security status and advance bridge route objective |
| `inspectOpenDoorRoute` | `inspectOpenDoorRoute` | Report that the route is open; no locked-door mechanic returns |
| `trackEnemyShipOnViewscreen` | `trackEnemyShipOnViewscreen` | Put the enemy raider into bridge tactical tracking |
| `fireBridgeTacticalConsole` | `fireBridgeTacticalConsole` | Fire a bridge tactical volley and update enemy hull state |

## Data shape

`motherShipInterior.interactables` still controls where prompts appear and which action id is requested.

`motherShipInterior.interactions` now describes which action ids are valid and which safe runtime handler they use:

```json
{
  "interactions": {
    "trackEnemyShipOnViewscreen": {
      "id": "trackEnemyShipOnViewscreen",
      "label": "Track enemy ship on bridge viewscreen",
      "handler": "trackEnemyShipOnViewscreen",
      "status": "Bridge tactical lock engaged. Enemy raider is tracked on the main viewscreen. Use the Bridge Tactical Console to fire."
    }
  }
}
```

The handler name is not arbitrary executable code. It must resolve to a known method in the runtime handler map.

## Acceptance checks

Patch E is successful when:

```text
current mother-ship route still works
E-key prompts still appear from interactable data
each current action id has a registry entry
each registry entry resolves to a safe handler
unregistered action ids fail safely with a visible status message
viewscreen tracking still changes state
tactical console still damages/disables the enemy ship
no locked-door mechanic returns
```

## Follow-up

Patch F should add validators that inspect the content definition before runtime use. Those validators should check that every interactable action has a registered interaction, every interaction has a handler, and every prompt target is reachable inside a room.
