# MCEL P3 operational Task Manager — implementation and bounded proof

Source basis: `main_computer_test-20261101-105608.zip` (provided by user).

## Scope

This patch adds the P3 operational Task Manager candidate from official layout-free MCEL JavaScript. It extends the generic semantic composition and runtime to support declared consequential intents through a narrowly scoped port, retaining the read-only P2 candidate. It also hardens the existing Task Manager backend, the viewport Task Manager mutation routes, scheduling, and a previously unguarded heartbeat lifecycle HTTP endpoint.

## User-visible behavior and policy

The Task Manager backend refuses confirmed destructive process actions unless the precise creation timestamp matches the currently observed PID. Task mutation endpoints require a loopback-hosted same-origin session with an anti-CSRF request header. This is browser-origin/caller scoping, **not** proof of authentication to a distinct local OS user. A same-machine malicious program remains outside this claim.

Schedule creation requires an offset-aware instant; records carry revision evidence. Delete and manual run revalidate the revision. Manual run is not presented as scheduler-run history. Backend effect actions store a request-id journal so uncertain outcomes cannot simply be retried as independent commands. The outcome `accepted-deferred` is distinct from independently verified completion.

The separate heartbeat lifecycle control service also now requires an origin-scoped session. Monitoring reads no longer start/recover heartbeat as a hidden side effect; lifecycle supervision occurs outside the read route.

## Commands (after checking base hashes and applying)

```powershell
python -m tools.mcel_p3_operational_completion_frob
python -m pytest --confcutdir=tests -q tests/test_mcel_p3_operational.py tests/test_mcel_p1_generated_composition.py tests/test_mcel_p2_monitoring_interactions.py tests/test_mcel_phase2b_semantic_runtime.py tests/test_mcel_migration_preservation_patch.py tests/test_mcel_semantic_other_apps.py
```

## Candidate route

`/applications/mcel-semantic-task-manager-generated-p3` (development, local only). Production and P2 routes are preserved. No automatic promotion.

## Verified scope

The controlled Frob reported `PASS_BOUNDARY_ONLY` with 70 focused browser, source and security tests passing. Frob results and exact hashes accompany the ZIP, but are not substitutes for Windows-host tests. A broader legacy Task Manager suite cannot collect in this exported snapshot because `game_projects/webgl-demo/game.json` is missing.

## Non-completion gates

Windows-host acceptance, real isolated side-effect trace comparisons, full production-versus-P3 operational parity, cross-process scheduler locking, authenticated OS-user identity, expert visual/usability review and live promotion remain **UNVERIFIED/BLOCKED**. Do not promote or rely on this patch as a certified complete production replacement.
