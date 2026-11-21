# Worker → native MCEL migration: candidate 1

## Objective and cutover status

The Worker will become a first-class MCEL application, so identity selection and authenticated SLH integration can subsequently use the same typed, generated operational pathway as other MCEL apps.

**Status: native candidate, not canonical cutover.** The canonical Worker remains the legacy `/applications/worker`. The new local-only route `/applications/mcel-semantic-worker` is available for inspection and in MCEL Lab's **Worker · Native Candidate** app preview. The canonical registry retains `mount: legacy`. The candidate does not execute or import the legacy `worker.js` DOM application.

## What is implemented

- `mcel_apps/worker/application.js` is the semantic source of truth: typed destination collection and selection, runtime observation, policy form, consequential operation receipts, and refresh intents. All page regions are synthesized through the **P3** MCEL semantic composition pipeline; there is no authored HTML page in the package.
- `main_computer/mcel_worker_application.py` compiles and verifies the semantic source and hosts the generic `McelSemanticOperationalRuntime` plus a bounded Worker capability port.
- `/api/applications/worker/mcel-observation` returns a **read-only, local-only** observation of the currently selected network, its connection state, Worker supervisor state and selected policy values. The new observation path does not call the existing mutating `network-session` GET endpoint, ping the Hub, perform an SLH login, or expose signed connections, keys, wallet state, private identity-directory paths, or offer registration material. A selected SLH target is labeled `NOT_VERIFIED`, **not enrolled**.
- `mcel-semantic-worker-ports.js` is a strict allowlist of MCEL operation identity, capability, effect identity, runtime method, risk and input schema. Explicit destination selection/disconnection uses the existing `network-session` POST; explicit policy save uses the existing `settings` POST with `changed_fields` restricted to designated policy keys. Both require an observed destination/policy and verify the resulting server observation.
- The generated forms accept refreshed observed values. Boolean checkbox hydration in the shared MCEL operational runtime was corrected so a policy save does not accidentally replace an observed true value with an unchecked form default. Consequential actions require confirmation. Changing networks through the incumbent endpoint **can clear network-scoped signed registration state**; the generated action says so.
- MCEL Lab can preview the candidate's native semantic mount and inspect its compiled IR.

## Not implemented; must not be inferred

The new candidate is **not yet a full Worker parity replacement**. Identity authority from Mother's private state, authenticated Network SLH workflow, Known Device enrollment and authorization, marketplace wallet signing, multisession authorization, credit funding, Hub offer registration, work queue execution controls and the full incumbent feature inventory are not exposed through native Worker contracts. Nothing on the candidate is evidence of identity authentication or successful SLH worker registration.

## Required migration order

1. Add a bounded, local read-only identity inventory with provenance from the canonical Mother authority. The Worker must not write identity state or treat `undefined` as an identity. User identity selection must be a semantic state distinct from destination selection.
2. Implement an explicit Network SLH workflow: select an identity, authenticate to ORG-SLH using the established administrator/known-device pathway, and pass authorized worker operations through SLH/LMS. Model `unauthenticated`, `authenticated`, `enrolling`, `verified`, `rejected`, and `disconnected` states separately; never elevate selection to authorization.
3. Inventory every remaining legacy Worker command, classify the authority and consequences, and migrate wallet, funding, marketplace registration, runtime supervision, and paid-work operations into separate typed capability contracts. Do not wrap the entire legacy controller in one generic `execute` bridge or duplicate its settings authority.
4. Test destructive destination changes, stale observations, concurrent operations, multiple sessions, restart persistence, and real browser/runtime acceptance, plus full feature parity against the incumbent.
5. Only after native operational parity and user acceptance change the canonical Worker registry entry to semantic and retire legacy mounts. Preserve the incumbent until that point.

## Local validation

- `python -m pytest -q tests/test_mcel_worker_native_migration.py mcel_apps/worker/tests/test_*.py` (the attached snapshot excludes game-project assets used by unrelated viewport imports; those must be available in the testing environment, or tests requiring the viewport module cannot collect).
- `node --check main_computer/web/applications/scripts/mcel-semantic-worker-ports.js`
- `node --check main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js`
- Native candidate generated layout and policy hydration were observed with Chromium and mocked local API responses. This does **not** certify live Windows/SLH/marketplace production behavior.

This record is deliberately a cutover gate, not a claim that the Worker migration is complete.
