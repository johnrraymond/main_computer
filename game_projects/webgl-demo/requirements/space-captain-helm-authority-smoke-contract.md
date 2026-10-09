# Captain-to-helm authority acceptance contract (test-only)

This smoke deliberately fails until the encounter takes orders from the hostile
ship's captain. The current bounded boarding controller is not a captain.

## Minimal command contract proposed by this test

The existing bridge encounter runtime must consume a captain's explicit order
through its command input, and the scene adapter must expose a path from captain
decision publication into the encounter. The initial smoke command is:

```js
runtime.command({
  type: 'captain-helm-order',
  order: {
    captainId: 'captain.beta', shipId: 'ship.beta',
    decisionId: 'beta-hold-001', revision: 1,
    issuedAtSeconds: 0, validThroughSeconds: 240,
    maneuver: 'hold', rangeM: 2050
  }
}, nowMs);
```

`maneuver` is a captain-chosen intent: `approach`, `hold`, `withdraw`, or
`coast`. `rangeM` on `hold` is captain-supplied; the helm may compute bounded
acceleration to implement it, but may not invent that objective. The output
must report `accepted` and the active order and provenance must be inspectable
from `readAuthorityState().helm['ship.beta'].activeOrder` and `recentEvents`.

The authored scene must have `submitEnemyCaptainOrder(...)` that forwards the
captain's decision to the real encounter runtime. This method is the designated
minimal integration seam for the later production patch. The smoke checks both its declaration **and an executable forwarding call**
into the real encounter runtime; the final integration browser smoke must also
exercise that route with real decisions and confirm the physics response.

## Invariants

- Without an active captain order, the bridge runtime cannot infer thrust from
  the `boarding-prep` phase alone. Safe default is zero **commanded** thrust,
  not zero inertial velocity.
- Approaching, holding at the *ordered* range, withdrawing, and coasting must
  produce distinct trajectories from an identical initial state.
- New valid orders supersede previous intent; cancellation or expiry disables
  continuing thrust from that intent. Old, duplicate, invalid, or unauthorized
  orders cannot alter authority.
- The player ship stays owned by `ship.mother` physics; the hostile captain
  cannot maneuver it.
- Player firing can update awareness and prompt a captain redecision, but
  cannot silently assign a different enemy helm mission.
- Existing target-tracking camera code is read-only with respect to motion.

The smoke does **not** supply a real AI model. The test captain is a deterministic
command publisher, as in the existing `space_captain_boarding_encounter_smoke.py`.
Once this contract goes green, an additional browser smoke must verify real
AI/authoring publication and execution through `submitEnemyCaptainOrder`.

## Commands

```powershell
python .\game_projects\webgl-demo\tools\space_captain_enemy_helm_authority_smoke.py --expect-red
python .\game_projects\webgl-demo\tools\space_captain_enemy_helm_authority_smoke.py
```

The first **must** succeed against the current code, recognizing the known bug.
The second **must** fail against current code and succeed only once captain
orders govern the actual enemy's acceleration. The existing 15-minute boarding
probe's implicit 2,050 m objective will also need revision when the controller
is made subordinate to explicit captain orders.
