# Ship-mounted viewscreen: RED-to-GREEN smoke gate

**Scope:** This production patch includes the previously approved ship-anchor smoke suite and updates two moving-target fixtures to follow authoritative encounter motion. The old `main_computer_test_viewscreen_no_self_ship_patch.zip` should **not** be applied.

## Required behavior

`ship.mother` from the active `spaceGravityRuntime.snapshot().bodies` is the observer. The bridge viewscreen must use the authoritative three-dimensional camera position and an explicit authoritative viewing direction. `ship.alpha` cannot be a second independent world-space ship. `observerPose.validThroughSeconds` is the explicit pose-freshness boundary in the deterministic test input. The target and the projectile/impact geometry must use that same observer frame. Target tracking may rotate the view but must not translate its origin. The main ship must never be drawn as a contact on its own external display.

The standalone Phase 3 probe passes a synthetic **observerPose** and a known **targetWorldPositionM** to the production projection, then verifies the resulting `snapshot.viewScreen` contract:

- `observerBodyId: 'ship.mother'`
- `cameraWorldPositionM: [x,y,z]` (meters)
- `cameraForwardWorld: [x,y,z]` (unit vector)
- `targetWorldPositionM: [x,y,z]` (meters)
- `targetRelativeWorldM: [x,y,z]` (meters)
- `targetRelativeCameraM: [right,up,forward]` (meters; orientation-basis check)
- `targetInFront: boolean`
- `targetOffsetNormalized: [x,y]` (existing normalized display coordinates)

The projection must **reject missing/non-finite observer state**, not fall back to a fake location. The test intentionally supplies a different observer pose and target position for movement, whole-world translation, 90-degree view rotation, and behind-camera culling. Existing encounter authority is retained, so this is not a synthetic combat implementation.

For the full-game probe, `mainShipObserverPose()` on the actual scene renderer is the intended production observer-pose access point, reading the real physical ship position and explicitly declared viewing basis (the gravity body does not currently provide attitude). The full-game test also reads `spaceGravitySnapshot()` independently, and compares them against the *same frame's* projection. The browser probe derives the enemy's expected world position independently from the fixed encounter-world origin and tactical authority's target coordinates, then compares it to the projection. The encounter origin must remain fixed across sampled frames. Ship attitude is not simulated by the gravity runtime, so the current fixed forward/up basis is a deliberate limitation rather than invented real orientation.

## Quick smoke commands

From `C:\\Users\\subsi\\main_computer`:

```powershell
# BEFORE the production fix: expected success because the specified spatial tests FAIL.
python .\game_projects\webgl-demo\tools\space_captain_viewscreen_anchor_smoke.py --expect-red --output .\archive\viewscreen-anchor-red.json

# AFTER the production fix: requires every test to PASS.
python .\game_projects\webgl-demo\tools\space_captain_viewscreen_anchor_smoke.py --output .\archive\viewscreen-anchor-green.json

# End-to-end with a real Playwright Chromium + WebGL context. No fake canvas accepted.
python .\game_projects\webgl-demo\tools\space_captain_viewscreen_anchor_smoke.py --full-game --output .\archive\viewscreen-anchor-full.json

# Existing individual smokes, now with corrected contracts:
python .\game_projects\webgl-demo\tools\space_captain_phase3_viewscreen_projection_smoke.py
python .\game_projects\webgl-demo\tools\space_captain_phase4_part1_viewscreen_presentation_smoke.py
python .\game_projects\webgl-demo\tools\space_captain_phase4_part2_viewscreen_renderer_smoke.py
python .\game_projects\webgl-demo\tools\space_captain_phase4_part3_non_encounter_modes_smoke.py
python .\game_projects\webgl-demo\tools\space_captain_phase5_part2_full_game_chromium_smoke.py --no-screenshots
```

The individual tests intentionally exit nonzero against the uncorrected game. Do **not** weaken them just to obtain green. The `--expect-red` flag is for the pre-fix baseline only and requires genuine, named spatial failures without runtime errors. Do not use it as post-fix proof. Keep physics, combat/damage, projectiles, screen power, immutability, render-cadence, and actual browser input tests intact.

### Diagnostics

- Pure projection: `cameraOriginErrorM`, `targetRelativePositionErrorM`, `targetRangeErrorM`, `viewDirectionErrorDeg`, named coordinate cases, and 30/60/120/165 FPS authority fingerprints.
- Full game: every sampled frame reports the physics ship, authoritative observer pose, camera pose, enemy position and relative vector, per-frame meter/degree errors, plus WebGL gameplay outcomes.
- Screenshots remain supplementary evidence, not an alternative acceptance condition.

A null metric means data is **missing** and causes a failure. No zero-for-missing or inferred camera coordinates are allowed.

## Production correction (after approval)

The scene update reads the physical `ship.mother` from gravity before projecting the encounter. Its camera origin is always that body's physical position. The encounter target receives a persistent world-space anchor at first observation, then follows the tactical target's authoritative motion relative to that fixed anchor; movement of `ship.mother` alone cannot move the enemy. The presentation carries the observer pose, and the self-ship renderer has been removed, rather than used to conceal a detached camera. The renderer remains a consumer of immutable projected presentation frames. Planet and astrometric frames carry the same physical observer pose.

The standalone gate now includes a fifth layer (`scene-physical-wiring`) that runs the production scene adapter against the authored project gravity body and tactical authority without WebGL. All five layers (100 checks) passed in the preparation environment. The full-game Chromium test could not run to completion there because no real WebGL context was available: **run `--full-game` on the target Windows machine before accepting the production change**. No browser result is inferred from the standalone checks.

**Attitude limitation:** the current physical gravity body has position/velocity but no attitude. A declared fixed forward/up frame is used until the navigation/ship-attitude model exposes an authoritative rotation. Tracking never translates the camera. The player-ship position, not this heading fallback, is what is physically grounded.
