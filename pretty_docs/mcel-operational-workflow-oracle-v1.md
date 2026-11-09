# MCEL Operational Workflow Oracle V1 — Frozen before compiler changes

**Status:** independent red/green acceptance contract and captured pre-repair baseline.
**Source under test:** the November 8, 2026 SLH semantic-first-run application.
**Scope:** semantic applicability and backend-derived operational state, *not* the graphical layout.

This oracle is intentionally a **separate patch**. It does not modify the DSL, the
semantic compiler, the composition engine, the SLH application source, SLH
backend authority, or the viewport runtime. Tests are expected to **fail on
the current implementation** for the exact missing capability. They are
acceptance requirements, not pass/fail proof of a completed compiler patch.

## Frozen artifacts

- `mcel_oracles/operational-workflows/oracle.v1.json` — nine authoritative
  observations, their expected next action, forbidden competing actions, six
  lifecycle transitions, stale/unknown failure semantics, shared input identity,
  security boundaries and native navigation rules.
- `mcel_oracles/operational-workflows/generic_application.js` — non-SLH
  layout-free canary with an action dependent on a capability-observed enum.
  General MCEL support is required; SLH-specific hidden-button scripts cannot
  satisfy this case.
- `mcel_oracles/operational-workflows/baseline.v1.json` — pre-change compiled
  source checks and actual headless Chromium observations, tied to SHA-256
  digests of the contract and generic canary.
- Optional diagnostic screenshots are generated on demand with `--screenshots-dir`
  (for example under `runtime/reports/mcel-operational-workflow-screenshots`).
  The historical baseline records the screenshot filenames but the binary images
  are deliberately **not included in this source patch**. Screenshots and
  pixels are not pass/fail criteria.
- `tools/mcel_operational_workflow_oracle.py` — read-only baseline recorder.
- Three oracle test modules under `tests/` — pure contract, cross-application
  compiler/runtime, and generated-browser acceptance.

## Meaning of the expected actions

The objective is that an administrator can progress from SLH unavailable to
initialized to configured to running to authenticated, and that the generated
interface selects an applicable step from freshly observed backend state.
The expected primary intent may coexist with harmless inspection, navigation,
help and refresh controls. Administrative setup operations may remain available
through an appropriately separated maintenance/recovery context; they must not
compete as current-workflow actions. This oracle does not specify columns,
colors, widgets, pixel positions, or a visual-design implementation.

The screenshot captured on November 8 depicts **RUNNING / INITIALIZED /
Organization Service running / no authenticated session**. Its next action is
`intent:login-administrator`. `start-local`, `bootstrap-administrator`,
`configure-organization`, `activate-organization`, and `logout-administrator`
must not compete. The duplicate `adminDeviceId` inputs must be consolidated
when they represent one semantic administrator identity.

Crucially, **not being shown is not sufficient to revoke authority**. When
backend evidence is unknown or stale, a programmatic `app.execute()` must not
invoke a consequential host port. The backend continues to enforce its own
independent authorization and idempotence. A successful click or receipt alone
must not advance the workflow; a fresh, confirmed backend observation must.

## Baseline observations (before any MCEL patch)

The saved baseline confirms all nine observed-state scenarios currently expose
competing actions. The screenshot scenario has **three** simultaneous
administrator device-ID inputs. A stale status can still cause a programmatic
activation request to reach the synthetic HTTP port, which returns a completed
receipt. Those are deliberately failing safety and usability expectations;
**no actual SLH operation was performed**. The source gate also fails because
the current DSL does not allow a derived-state `when` condition on the generic
canary's action, and the SLH app has no backend-conditioned action nodes.

## Reproduce and verify (from repository root)

First, check the immutable expectations and baseline provenance without
requiring any SLH backend:

```powershell
python -m pytest -q `
  tests/test_mcel_operational_workflow_oracle.py::test_frozen_contract_has_the_screenshot_and_full_lifecycle `
  tests/test_mcel_operational_workflow_oracle.py::test_saved_baseline_is_bound_to_this_unchanged_oracle
```

To capture a current source/browser report without modifying the backend:

```powershell
python -m tools.mcel_operational_workflow_oracle `
  --browser `
  --screenshots-dir runtime/reports/mcel-operational-workflow-screenshots `
  --report runtime/reports/mcel-operational-workflow-current.json
```

To run the unchanged **strict red/green acceptance** suite:

```powershell
python -m pytest -q `
  tests/test_mcel_operational_workflow_oracle.py `
  tests/test_mcel_slh_workflow_oracle.py `
  tests/test_mcel_slh_workflow_browser_oracle.py
```

**Before the compiler patch, the failed tests are expected.** After the patch,
that same command must pass without rewriting the oracle or treating a known
failure as `xfail`. Do not regenerate `baseline.v1.json` when the compiler
changes; it is historical evidence of the pre-change implementation. Write a
new, separate current report if needed.

The general non-SLH canary must compile and react to an updated capability
observation in Chromium, while SLH must stop exposing irrelevant setup actions,
block programmatic consequential attempts on stale observations, deduplicate
shared administrator inputs, and change available interactions after verified
login, logout, activation, or failure.

For **real disposable backend effects**, also run the existing independent
acceptance lane (not invoked by the read-only browser fixtures):

```powershell
python -m pytest -q `
  tests/test_mcel_slh_first_run.py `
  tests/test_mcel_slh_authenticated_workers.py `
  tests/test_mcel_slh_first_slice.py `
  tests/test_mcel_navigation_contract.py
```

This checks the current authorized HTTP and Worker paths without using a
production SLH. A passing inert browser test alone must never be reported as
live service acceptance. Do **not** enable Windows boot-start while testing.
