# Patch 14.1 — Active Deterministic Layout Continuity

**Status:** Experimental, opt-in collection continuity lab. Not production-promoted. No new renderer, data schema, operational authority, or saved user profiles.

## Objective

A semantic collection's established layout is a commitment. Routine changes to text, measurements, record order, or nearby viewport widths should not arbitrarily replace it. The normal layout engine remains the default. This patch exercises the Patch 14.0 pure selector and serializable committed-state substrate in the **existing registered MCEL collection renderer**, only when requested by the caller.

## Opt-in (browser API)

Load `mcel-layout-continuity.js` **before** `mcel-semantic-layout-inference.js`. Both must be loaded when using the new lab mode.

```js
const mounted = McelSemanticLayoutInference.mountSemanticCollectionLayout(
  host, contract, McelElementRegistry,
  { observeResize: true, continuity: "lab" });
// Existing mounting without `continuity: "lab"` is unchanged.

const result = mounted.refresh(nextContract); // compatible updated source projection
const state = mounted.getCommittedState(); // JSON-serializable instance-local state
mounted.commitSourceRowOrder(); // explicit user sort: adopt source order
mounted.unmount();

// Future mount of a compatible contract can restore the incumbent:
const restored = McelSemanticLayoutInference.mountSemanticCollectionLayout(
  host, compatibleContract, McelElementRegistry,
  { continuity: "lab", previousState: state });
```

The generic `McelSemanticCollectionWorkspace.mount(..., {layoutContinuity:"lab"})` also forwards the opt-in and carries the committed state across its **own** reconciled updates. No existing caller changes by default. A missing selector refuses the explicit lab mode instead of silently substituting a different policy.

## Decision and measurement separation

- Pure `McelLayoutContinuity.planCollection` accepts the **evaluated** existing layout, semantic field contracts, source scope, previous committed state and exact row IDs. It produces `{plan,state,record}` without accessing DOM, timers, locale, or storage.
- It uses the Patch 14.0 canonical decision comparator and incumbent selector. Candidate selection is deterministic from the same committed state and canonical inputs. A layout with inadequate required fields or viewport space is not retained.
- The committed state (`mcel.collection-committed-state.v1`) contains the discrete decision, field-binding signature, track allocations, exact row-identity order, current width, policy version and commit ordinal. It can be serialized. This **is not yet a portable named user profile**: layout identity is instance-local and can include source-shaped field bindings. Cross-application profiles remain a later gate.
- A bounded track cap of 50% of current available row width applies when calculating new content minimums. Once tracks are committed, changing the longest live value does not force neighboring tracks to reallocate; exact source remains recoverable through the semantic contract/inspector. If a viewport becomes too small, the engine shrinks within minimums or changes modes rather than overflowing.
- A 24px breakpoint hysteresis band preserves the incumbent when still feasible; switching to/from stacked requires crossing the wider transition boundary. Semantic requirements take precedence.
- Visible fields are sticky until infeasible or contract structure changes. Optional fields may be disclosed. Required fields are never silently dropped.
- Stable visual row ordering uses CSS flex `order` keyed by full canonical row ID (including generation when provided); this does **not** mutate source objects or reparent DOM nodes. A **user-authorized sort** calls `commitSourceRowOrder()` to adopt the new source order. A producer that itself reparents DOM nodes may drop browser keyboard focus; the new layout policy does not claim to restore focus after externally owned DOM moves.
- On unmount, original collection inline styles, row styles, field visibility, and layout authority are restored. The default renderer and application operations are unaffected.

## Frob and acceptance

Run:

```powershell
python -m tools.mcel_patch14_active_continuity_frob
```

The Frob validates the pure selector with a deterministic 1,000-observation replay, explicit row reorder, adversarial text lengths and a breakpoint round trip; tests real Chromium layout geometry after a large change to an actual registered MCEL collection; measures horizontal column movement and DOM identity, inspector and focus continuity during data-only refresh; tests display ordering after producer DOM reordering, source-order override, and serialized incumbent remount; verifies no overflow/browser errors. It also runs prior Patch 14.0, representation layout, canonical tree, Patch 13 completion and other relevant regression tests. The script saves `runtime/reports/mcel-patch14-active-continuity.json` and browser metrics `runtime/reports/mcel-patch14-active-browser-evidence.json`.

**Conditions not certified by this patch:** any production application deployment; cross-platform pixel equivalence; canonical-tree continuity activation; saved semantic layout profiles; autonomous search/AI layout design; correct focus after a producer destructively reparents nodes. Explicit source sorting must be communicated through `commitSourceRowOrder()` in lab mode. Width allocation may change when the established geometry becomes infeasible.

## Follow-on

Before production use, extend the harness to real hosted application feeds and verify state propagation through their actual update lifecycle. Patch 14.2 should introduce the comparable continuity policy for canonical-tree layouts, followed by named semantic profiles and the semantic-versus-blind intelligent layout Frob. No promotion is implied by a passing lab test.
