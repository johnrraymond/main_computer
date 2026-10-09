# Ship-mounted viewscreen: RED-to-GREEN smoke gate

**Scope:** Tests and diagnostics only. No production code in this patch. The old `main_computer_test_viewscreen_no_self_ship_patch.zip` should **not** be applied.

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

For the full-game probe, `mainShipObserverPose()` on the actual scene renderer is the intended production observer-pose access point, reading the real physical ship and actual attitude. The full-game test also reads `spaceGravitySnapshot()` independently, and compares them against the *same frame's* projection. The enemy's authoritative `ships['ship.beta'].worldPositionM` must match `viewScreen.targetWorldPositionM`; merely fabricating a world position in the presentation does not pass.

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
