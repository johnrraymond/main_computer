# Bridge-entry tactical activation: deterministic captain vertical slice

The opening pursuit is authored choreography before the player enters `bridge.deck`.
`ship.mother` and the enemy's massless orbital reference remain in the physical
world. The tactical encounter clock remains unstarted (`startedAtMs === null`),
with no captain orders and no enemy commanded tactical acceleration.

On the first **game frame after room entry**, `startBridgeCaptainSimulation()`
transfers the scripted pursuit's current relative position and velocity into the
encounter authority. It starts tactical time at 0 exactly once. Later bridge
returns, console interactions, and viewscreen power changes do not restart it.

`MainComputerBridgeCaptainDecisionPolicy` is a **deterministic cognition
adapter**. It receives a typed observation and ship-specific capability list.
It may issue a structured `captain-helm-order` for `captain.beta` / `ship.beta`.
The runtime validates identity, revision, duplicate ID, range and expiry, then
computes bounded acceleration to carry out the captain's chosen range goal.
Absent an active order, enemy commanded acceleration is zero, but physical
momentum and gravity continue. Player fire cannot silently create an enemy
helm order. A replacement `enemyCaptainDecisionProvider` can later supply
NanoJev decisions using precisely the same input/output contract; **this patch
does not claim the real model is already generating gameplay orders.**

Transporter limits are ship capabilities, not captain wishes. Initial authored
values are 2,500 m for the enemy's personnel transporter and 1,000,000 m for
`ship.mother`. The action generator uses the measured world range, including
exact boundary inclusion. The personnel transport execution/boarding action is
**not** added here; eligibility is made available for the captain decision.
Ship teleportation is a distinct unsupported ability.

Regression acceptance:

- Before bridge: encounter authority unstarted, no captain order, scripted enemy
  pursues, physics and ship-mounted target tracking still function.
- Bridge entry: first captain decision at tactical time zero, with exactly one
  initialization and an auditable captain-order event.
- Two different authorized commands cause different trajectories; command
  cancellation, expiry and unauthorized requests remain enforced.
- Fifteen-minute captain simulation renews decisions with bounded separation.
- Physical camera origin, world range, weapon timing, impacts and target
  destruction still pass the real Chromium game.
