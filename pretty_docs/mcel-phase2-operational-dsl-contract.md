# MCEL Phase 2 — Public operational DSL contracts (implementation evidence)

This document records implemented source/compiler capabilities and the remaining
runtime gaps. It does not change the normative MCEL V1 product target in
`mcel-v1-product-contract.md`.

## Implemented in the public `mcel.defineApp` DSL

- `dsl.state.canonical`, `dsl.state.rendererLocal`, and `dsl.state.derived`
  issue typed states with declared authority. Canonical state requires an
  initial value. Result targets must be derived; view selectors must be local.
- `dsl.capability.external(name, {risk})` declares a *contract*, not permission.
  Each `.operation(name, runtimeMethod, {inputs, result, cancellable})` declares
  a typed operation and its runtime method. Allowed risk labels in the new
  authoring path: `external-read`, `source-write`, `consequential`.
- `dsl.intent.capabilityRequest(name, capability, {operation, inputs,
  arguments, resultTo})` emits a governed `capability-request` effect and
  typed response-to-derived-state binding. `inputs` are named user arguments;
  `arguments` are typed references to declared states. Every required operation
  parameter is bound exactly once and matches its declared schema.
- `dsl.intent.selectFromCollection(name, collectionState, rendererLocalState)`
  declares dynamic keyed identity selection with mandatory membership,
  rejection of unknown keys, and no canonical-write authority. Collections
  can attach this intent with `dsl.surface.collection(..., {select})`.
- `dsl.intent.selectView(name, localEnumState)` declares a bounded view/mode
  selection using the existing canonical IR `viewTransition` validation.
- The paired `application.html` + `application.js` source preserves semantic
  hierarchy and produces canonical IR with both source-file hashes.

Canonical IR validates operation registration, typed argument binding,
capability/effect risk equality, result target authority/schema, and dynamic
selection source/key/target/membership. Hand-edited IR that violates those
conditions fails validation.

## Example

See `tools/fixtures/mcel_phase2_operations/` for a paired source with a
filesystem-read capability, typed record collections, dynamic selection,
selection-dependent preview request, and result binding. Four smaller probes
under `tools/fixtures/mcel_phase2_reference_apps/` exercise the same DSL
against File Manager, Task Manager, Code Editor and Calculator semantics.
These probes are **not** migrated applications; no runtime facades are
registered by them. Their purpose is to test generic authoring expressibility.

## What is **not** complete

No browser runtime currently interprets `resultBindings` and
`collectionSelection` for these public DSL applications. The actual
existing service/backends, authorization gates, subscriptions, full document
lifecycle, and four-app operational parity are **not** proven by compilation.
The compiler does not grant capability authority. A real runtime must bind
methods through the governed capability registry, check request identity and
risk, enforce membership, publish only completed result responses, and emit
receipts before these declarations count as functioning operations.

Do not promote the four probes. Existing host-bound production apps remain
untouched. Phase 2 as a whole remains **INCOMPLETE** until runtime and
full application parity gates are satisfied.

## Test commands

```powershell
python -m pytest -q tests/test_mcel_phase2_operational_dsl.py
python -m tools.mcel_phase2_operational_dsl_frob
python -m pytest -q tests/test_mcel_dsl_compiler.py tests/test_mcel_application_ir.py tests/test_mcel_phase1_paired_authoring.py
```
