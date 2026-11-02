# MCEL P3.6: hosted acceptance and explicit evidence gates

## What changed

P3.6 is an **additive acceptance harness**, not a production Task Manager promotion. The official layout-free `mcel.defineApp` Task Manager remains the source of all surface declarations, and the existing `mcel.generated-composition.v1` path remains authoritative. The production Task Manager and P2 read-only monitor are not replaced.

The P3 source has a demonstrated parity improvement: the operator can now set `limit` (5–100) and `includeConnections` (`true`/`false`) on each monitoring form. These are semantic read inputs, not layout coordinates or new capabilities. `mcel-semantic-read-ports.js` validates the P3-only values, forwards them to the real overview endpoint, and retains P1/P2 and legacy mappings. The generic generated composition marks P3 monitoring collection/result regions for observation; the generic runtime binds polling to the **currently visible mode's** read-only observation and takes initial inputs from canonical form defaults. It never polls a destructive action or changes operational authority. The previous P1/P2 layout and authority rules remain intact.

## P3.6 components

- `tools/mcel_p36_acceptance_frob.py`: fail-closed coordinator. Always reports separate `P36_BASELINE`, `P3_WINDOWS_HOST`, `P36_PROCESS_EFFECT`, `P36_SCHEDULE_EFFECT`, `P36_SERVER_LIFECYCLE_EFFECT`, `P3_REAL_SIDE_EFFECT`, `P3_PRODUCTION_PARITY`, `P3_HUMAN_DESIGN`, and `P3_PROMOTION` statuses. It **never** promotes.
- `tools/mcel_p36_evidence.py`: source/manifest digests, loopback-only URL validation, evidence status composition, human-review accounting.
- `tools/mcel_p36_hosted_probe.py`: Playwright **`page.goto()`**, not `page.set_content`, with four real overview HTTP requests at 320, 390, 560, 1100, and 1272 CSS px, generated IR fingerprint, exact served HTML hash matching the checkout, selected/expanded record where present, auto-refresh pause/resume, layout overflow, same-machine OS PID cross-check, and unauthenticated mutation refusal. Captures five real screenshots; raw screenshots may expose local process names and must remain private.
- `tools/mcel_p36_isolated_effects.py`: separately opt-in **actual** terminate/kill effects, permitted only on two freshly spawned and generation-verified direct child processes; uses the real localhost Task Manager endpoint with session/CSRF and proves process exit independently. No process other than a run-owned child is admitted or cleaned up. A distinct temporary schedule store exercises real persistence, revisions, delete and a non-destructive recorded `server_status` manual invocation. Production schedules are never changed.
- `tools/mcel_p36_parity.py`: source-workflow inventory and explicit `UNVERIFIED` operational-parity outcome. Source coverage is not a substitute for a production-versus-generated live trace.
- `tests/test_mcel_p36_acceptance.py`, `tests/test_mcel_p36_monitor_options.py`, `tests/test_mcel_p36_hosted_real_http.py`: fail-closed tests, semantic input/port tests, and controlled real HTTP route exercise. The HTTP laboratory shim is not the production viewport, and any cloud browser that blocks localhost navigation leaves the host test explicitly blocked.

## Installation and baseline

The patch is based on `main_computer_test-20261101-123254.zip`. Verify the working tree with the separate supplied preflight script and baseline hash ledger **before** applying the manifest-free patch. Never overwrite a source that diverges from the supplied snapshot without reconciling it. Use `python new_patch.py <patch.zip> --dry-run` and inspect `actual.patch`; `verification: no_reference` is normal for a manifest-free patch.

## Operator workflows

Run the existing P3 boundary Frob first. Its result must remain `PASS_BOUNDARY_ONLY` and its recorded source hashes must match **this** checkout. A prior passing P3 report from an older source version will be refused until rerun.

```powershell
python -m tools.mcel_p3_operational_completion_frob
python -m tools.mcel_p36_acceptance_frob --mode observe `
  --host-url 'http://127.0.0.1:<actual-viewport-port>'
```

The host URL must be supplied by the operator using the real locally running viewport port. The probe compares the exact served candidate HTML with this checkout. The candidate route is `/applications/mcel-semantic-task-manager-generated-p3`. Do not substitute a cloud-browser mock or change a server to match the expected page fingerprint. The host must load the patched code (restart it through normal operator workflow only if appropriate).

For process termination/force-kill of **harness-owned disposable children only**, a separate explicit choice is required:

```powershell
python -m tools.mcel_p36_acceptance_frob --mode isolated-effects `
  --host-url 'http://127.0.0.1:<actual-viewport-port>' `
  --ack-isolated-effects
```

The effects lab does not call production server restart/shutdown, alter production schedules, kill a non-owned PID, or treat `accepted-deferred` as observed-complete. It tests the process action HTTP endpoint, not yet a verified generated-button click. Its scope is accurately labeled in the report.

Human-review template and live screenshots are produced in `runtime/reports/mcel-p36-acceptance/`. A human operator must complete the eight task rubric entries with evidence and record an explicit independent approval. Screenshots are diagnostic and not automatically a human PASS. To combine evidence from multiple runs without silently reusing stale artifacts, use `--carry-report path\to\prior\report.json` (repeat as needed); source fingerprints, evidence digests and freshness are checked. A separate promotion step is always required.

## What P3.6 cannot self-certify

1. A cloud/laboratory browser does not certify the operator's actual Windows host. If Playwright cannot navigate localhost, the host gate is BLOCKED.
2. The disposable process lab proves actual controlled termination over localhost HTTP when opted in. It does **not** prove complete generated UI click parity or service control authority.
3. The schedule lab proves a temporary file and safe recorded status operation. It does **not** prove a real isolated server restart/shutdown, scheduler execution, or the production schedule pipeline.
4. The legacy and generated operator paths require production-host comparison for **every** consequential and AI workflow. No source inventory or read-only HTTP trace suffices.
5. Human design approval requires real tasks, real screenshots and recorded reviewer sign-off. Automatic geometry checks alone never pass it.
6. Loopback+CSRF is not authentication against an untrusted process running under the same OS account.

The `P3_PROMOTION` status remains `BLOCKED` throughout this patch. Never change the production route or authorization policy simply to improve acceptance numbers.

## Acceptance artifact paths

- `runtime/reports/mcel-p36-acceptance/report.json`: gate-specific PASS/FAIL/BLOCKED/UNVERIFIED, source hashes, prior P3 evidence and exact remaining work.
- `runtime/reports/mcel-p36-acceptance/parity-ledger.json`: capability-source inventory; never labeled full live parity.
- `runtime/reports/mcel-p36-acceptance/isolated-schedule.json`: temporary schedule-state evidence.
- `runtime/reports/mcel-p36-acceptance/hosted-probe.json`: actual route/browser trace, only if host observed.
- `runtime/reports/mcel-p36-acceptance/disposable-effects.json`: actual run-owned process effects, only if explicitly authorized.
- `runtime/reports/mcel-p36-acceptance/screenshots/`: browser images for private review.
- `runtime/reports/mcel-p36-acceptance/human-review-template.json`: unapproved until completed by reviewer.
- `runtime/reports/mcel-p36-acceptance/evidence-manifest.json`: SHA-256 digests of materialized evidence.

## Rollback

Keep the pre-patch versions of the four modified files (`application.js`, `semantic_composition.py`, `mcel-semantic-read-ports.js`, `mcel-semantic-operational-runtime.js`) as recorded in the baseline ledger; restore them atomically if regressions occur. Remove the new P3.6 `tools/`, `tests/` and `pretty_docs/` files as a separate cleanup. Do not delete production schedules, journals, or other user data. The new candidate has no production promotion and its removal does not require a production Task Manager route change. Rerun the P3 boundary Frob after rollback.
