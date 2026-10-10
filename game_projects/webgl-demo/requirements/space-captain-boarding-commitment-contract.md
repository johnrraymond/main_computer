# Captain-owned first-encounter boarding commitment (abstract state)

**Scope:** This is NOT boarding combat and does not move crew between ships.
It is a timed commitment recorded by the authoritative tactical clock, enabling
an enemy captain to order a boarding attempt and subsequently face a meaningful
retreat decision. No encounter phase may issue boarding automatically.

- Entry: Pre-bridge shuttle pursuit is unchanged. Bridge entry starts tactical
  time at zero and initializes the raider captain's decision loop.
- Range: `ship.beta` has 2,500 m personnel transporter reach. Initiating the
  commitment requires a current target separation <= 2,500 m AND relative
  speed <= 5 m/s. Both constraints are checked at initiation and completion.
- Initiate: `captain-boarding-order` action `initiate` -> `deploying` for exactly
  30 tactical seconds -> `deployed` only if still in range and velocity matched.
  Otherwise return to `idle`, with a recorded failed completion.
- Commitment: boarders are considered deployed but their battle is NOT modeled.
  `captain-helm-order` `withdraw` is rejected while `deploying`, `deployed`,
  `recalling`, or `abandoning`. There is no autonomous withdrawal decision.
- Recall: captain order `recall` with deployed boarders -> `recalling` for
  exactly 24 seconds -> `recovered` if contact was maintained, otherwise stay
  `deployed` and require another decision.
- Abandon: captain order `abandon` with deployed boarders -> `abandoning` for
  exactly 12 seconds -> `abandoned` (crew lost); the captain may then withdraw.
- All actions require `captain.beta` / `ship.beta`, a fresh revision, unique
  decision ID, valid timestamp/expiry and auditable source. Events and state
  include checkpoint receipts when the source is NanoJev.
- Deterministic test doctrine: captain chooses 2,050 m standoff, deploys when
  eligible, reacts to an actual player shot, and chooses recall or abandonment.
  Both simulations are verified independently with exact event timestamps.
- NanoJev doctrine: the same physical observations yield state-dependent legal
  choices. The adapter provides `actionType='helm'` or `actionType='boarding'`.
  The live scene forwards boarding through `submitEnemyCaptainBoardingOrder`,
  using exactly the authority used by the deterministic tests. Illegal choices
  are not offered and invalid replies are rejected.
- For live model evaluation use the existing NanoJev manager's `9765` batch
  API and prepared checkpoint, independently of the Main Computer viewport
  route on `8765`. A successful native model call does not prove that a
  live browser has executed the order; the reports preserve that distinction.

Default timing values are **initial game-design assumptions**, not a physics
claim or a claim about how transporter systems really operate. Adjust them in
one authoritative location and update the scenario tests together.
