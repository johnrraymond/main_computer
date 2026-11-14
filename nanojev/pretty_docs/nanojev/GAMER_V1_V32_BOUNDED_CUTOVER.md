# GAMER V32 bounded numerical cutover

Status: **implemented but NOT activated on the Windows host by this patch**.

V31's completed CUDA regression demonstrated 624/624 exact signed differences (288 protected TRAIN, 144 original DEV, 192 stress DEV; 208 zero, 208 positive, 208 negative). The V29 selector and V30 token-centroid decoder use the frozen **base Qwen layer-18** activations. Numerical values are reconstructed by the decoder and subtracted by deterministic `Fraction` arithmetic. This does not certify general numerical reasoning in the original Wide neural head or replace the prompt-to-math LoRA.

## V32 scope

V32 defines a **separate authority for the two V27-certified signed-difference prompt families**. A versioned local authority record at `runtime/state/gamer/v32_numerical_authority.json` can point this new numerical route at the V29/V30 inference implementation. The original Wide weights, trainer, and all preexisting Wide consumers are left untouched; no implicit fallback to Wide or to an oracle is invented for the new route.

`main_computer.tactical_ai_service.TacticalAIService.evaluate_gamer_signed_difference` is the typed Space Captain numerical boundary:

- Request: `game.captainSignedDifferenceRequest.v1`, with exactly `requestId` and `prompt`.
- Response: `game.captainSignedDifferenceResponse.v1`, with decoded operands, exact signed difference, sign and fingerprinted model receipt.
- Inputs carrying expected answers, other fields, or `game.captainDecisionRequest.v6` are rejected.
- Response always has `captainOrder: null`; no helmet/boarding order is emitted.

**Important distinct contract:** The existing Space Captain battle uses `game.captainDecisionRequest.v6` to ask its NanoJev adapter for **multi-action pairwise votes**. V29/V30 is not trained to answer those questions and cannot be substituted for tactical decision authority. V32 preserves that service unchanged. This patch adds an operational callable numerical subservice but does **not** activate it from the combat engine automatically or certify battle outcome decisions. The game runtime module lives under `game_projects/webgl-demo/tools/space_captain_fleshed_combat_smoke.py` and is not present in the available snapshot. That action-level integration requires the actual runtime and a separately certified tactical model/decision transformation; do not treat a successful numeric handoff as that certification.

## Acceptance and activation

`tools.nanojev_gamer_v1_v32_operational promote` performs, in order:

1. Recomputes the source-certified 624-case deck; compares it byte-for-record against the saved V31 deck. Re-scores all 624 saved live predictions and verifies acceptance-report hashes.
2. Re-attests frozen base-Qwen lineage, V29 selector, and V30 decoder checkpoints, including V30 TRAIN-only gate evidence.
3. Runs an additional **fresh CUDA three-sign GAMER game-payoff probe** with new operand contrasts and a reversed label ordering. It must pass 3/3 exactly.
4. Atomically activates the bounded numerical authority file.
5. Calls the actual `TacticalAIService.evaluate_gamer_signed_difference` entry point on a fresh model forward and verifies the zero answer without action selection. Any failure after activation automatically rolls back to the unmodified legacy authority.
6. Writes `cutover_acceptance.json` in the fresh output directory.

Status and rollback never require GPU:

```powershell
& $py -u -m tools.nanojev_gamer_v1_v32_operational status
& $py -u -m tools.nanojev_gamer_v1_v32_operational rollback
```

Run `promote` with an unused output folder and the existing V27, V29, V30, V31 run directories. On failure before activation the legacy authority stays active; on failure after activation the command attempts automatic rollback. A failed/canceled run may leave a partial report folder, so choose a fresh output folder for retries.

## Boundaries and known limitations

- No TEST cases were included in V31; the 624-case replay is TRAIN/DEV/stress only.
- V30's deterministic subtraction is *not* proof that the neural Wide output head learned exact arithmetic.
- Fresh-inference operating speed, persistent model residency and Space Captain battle-timestep latency have not been certified.
- The new numerical route is explicit and narrow. Existing full-game tactical selection remains with the original v6 adapter; if the V32 authority is disabled, the new typed route refuses rather than pretending to invoke a Wide prediction API that does not exist.
- Nothing promotes the overall GAMER release manifest or changes any checkpoint by itself. No integration into the external game runtime has been claimed.
