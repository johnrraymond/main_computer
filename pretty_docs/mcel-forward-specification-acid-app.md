# MCEL Forward-Specification Acid Application

## Purpose

`mcel_apps/contract-workbench/` is the forward specification for the next MCEL application-development boundary. Contract Counter remains the frozen semantic-runtime-proven canary for the existing fixed-surface runtime. Contract Operations Workbench defines the application code, contracts, HTML, acceptance obligations, observation obligations, and proof target that MCEL must learn to execute next.

The application is intentionally written before the shared runtime supports all of its features. This is test-driven architecture:

```text
ideal application definition
→ explicit forward contracts
→ structural and semantic validation
→ named runtime blockers
→ MCEL implementation waves
→ unchanged application reaches semantic-runtime-proven
```

## Constitutional rule

When the forward app and the current runtime disagree, first assume the runtime is incomplete. The app may change only when the desired interface is internally incoherent or violates a deeper MCEL authority boundary.

The forward app must never be promoted merely because its files validate structurally. `forward-specification` is a valid package mode, but it is not proof eligibility.

## Human authoring authority

The primary human-owned source is:

```text
mcel_apps/contract-workbench/application.js
```

It uses the declaration-only library:

```text
main_computer/web/applications/scripts/mcel-app-definition.js
```

That library validates and freezes the desired application definition. `tools/mcel_application_definition.py` now normalizes that source into deterministic explicit domain, intent, adapter, surface, layout, acceptance, and observation contracts plus `generated/mcel.application.normalized.json`. The generated files remain inspectable and fingerprinted, while executable operation and invariant behavior is generated from the one human-owned source. The library still does not render dynamic nodes, run capabilities, reconcile collections, or issue semantic-runtime proof.

The definition declares:

- canonical, provisional, renderer-local, and derived state;
- typed payload-bearing operations;
- prohibited operations;
- a streamed capability-backed quote operation;
- cancellation and per-item concurrency policy;
- dynamic inputs and properties;
- conditional projection;
- keyed collection reconciliation;
- item-level controls;
- package-local acceptance obligations;
- dynamic browser observations;
- multi-instance isolation.

## Physical interface

The app remains HTML-first. `src/index.html` contains ordinary controls, static regions, dynamic hosts, and reusable templates:

```text
form inputs
conditional validation host
summary property nodes
filter and sort inputs
conditional empty-state host
keyed collection host
contract row template
operation receipt
```

Canonical state determines which collection items exist. Renderer-local state controls drafts, filtering, and sorting. Provisional state carries quote progress. Derived state calculates the visible collection, total quantity, and submit readiness.

## Current test boundary

The package separates tests into two classes.

### Passing definition tests

These prove that:

- the package is structurally valid;
- the application definition is executable as a declaration;
- identities and references are coherent;
- operation read/write sets are explicit;
- synchronous transition semantics are already defined;
- HTML hosts and templates agree with the dynamic surface contract;
- the feature matrix exactly matches the manifest's missing bridges;
- app proof refuses false promotion.

### Strict expected failures

Runtime-dependent obligations are encoded as strict expected failures with stable codes. They include:

- renderer-local state;
- provisional state and commit;
- derived state;
- dynamic input binding;
- control payload extraction;
- dynamic property projection;
- conditional projection;
- keyed collection reconciliation;
- dynamic item control binding;
- capability-backed operations;
- cancellation;
- concurrency policy;
- dynamic browser observation;
- intent-complete proof;
- multi-instance proof.

An unexpected pass is treated as a test failure until the corresponding bridge is deliberately closed and the obligation is promoted to an enforceable test.

## Development order

The recommended implementation sequence is:

1. Add renderer-local and derived state evaluation.
2. Add dynamic input binding and payload extraction.
3. Add safe property projection.
4. Add conditional template projection.
5. Add stable keyed collection reconciliation and item controls.
6. Add provisional state and capability-backed async operations.
7. Add cancellation and concurrency policies.
8. Add collection, conditional, provisional, and multi-instance browser observation.
9. Add intent-complete proof coverage and promote the app only after every obligation passes.

## Non-goals

This package does not authorize:

- replacing SCM as canonical mutation authority;
- arbitrary DOM mutation by app code;
- a virtual DOM requirement;
- implicit reads or writes inferred from transition source code;
- hidden capability access;
- treating local or provisional state as canonical;
- weakening acceptance, observation, provenance, or truth gates.

## Completion condition

The program is complete when this unchanged application can run:

```powershell
python main_computer/mcel_app_prove.py `
  --app contract-workbench `
  --check
```

and receive:

```text
truth_status: semantic-runtime-proven
```

At that point, MCEL will have proven both a fixed-surface canonical app and a materially dynamic, payload-bearing, asynchronous application through the same package, runtime, observation, provenance, and truth authorities.
