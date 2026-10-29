# MCEL Patch 14.7: Universal Capability Negotiation

## Purpose

An element chooses its preferred presentation and its own ordered fallbacks. A
container reports which capabilities it can provide, including explicit
refusals. The universal coordinator checks the element's candidates in their
own priority order; it does **not** choose an alternative on the element's
behalf. No application or domain name is embedded in the negotiation engine.

Patch 14.7 extends the existing registered semantic-collection consumer and
single universal profile. It does not add another profile, change the canonical
semantic composer, enable operational Task Manager actions, or promote the
laboratory renderer into the production route.

## Files and contracts

- `mcel-presentation-negotiation.js`: versioned
  `mcel.element-presentation-policy.v1` validation and ordered negotiation.
- `mcel-presentation-hosts.js`: generic scrolling and floating-card hosts.
- `mcel-element-registry.js`, `mcel-elements-core.js`: optional per-element
  presentation-policy declarations and collection-row defaults.
- `mcel-semantic-collection-workspace.js`: shared semantic detail content,
  negotiated selection/hover presentations, evidence and copy controls.
- `mcel-universal-semantic-layout-lab.js`: opt-in integration with the current
  Task Manager's universal semantic collection (read only).
- `mcel-semantic-collection-workspace.css`: opt-in geometry and overlay rules.
- `tools/mcel_patch14_7_capability_negotiation_frob.py`: real Chromium checks.

### Vocabulary

`geometry.child-resize`, `scroll.reveal`, `scroll.anchor-preserve`,
`scroll.observe-visibility`, `scroll.user-activity`, `overlay.anchor-source`,
`overlay.collision`, `overlay.portal`, `overlay.dismiss`.

The default element selection policy attempts:

1. Inline expansion **requiring** resize and scroll-to-reveal.
2. Floating expansion over its source **requiring** overlay services.
3. Inline expansion requiring resize only (accept no scroll).

The default hover policy requests a source-anchored floating card with a
2,000 ms pointer-leave timer, user-scroll-induced fade, and protection of
active native text selection. Both presentations render the same canonical
v2 semantic record. The source row remains in the collection beneath a
floating card; selecting `Expand inline` transfers to a resizable list row.
Selecting an expanded row again collapses it.

These defaults are **not** requirements imposed on all elements. The caller
may supply `presentationPolicy` and `capabilities` in the opt-in mount call.
The policy is validated before executing; unsupported capabilities are
reported as refusals. A refused or failed candidate causes evaluation of
the element's *next declared* candidate, never an invented alternative.

### Example

```javascript
const mount = McelUniversalSemanticLayoutLab.mount(target, taskManagerPlan, {
  lab: true,
  presentationNegotiation: true,
  capabilities: { allowScroll: false },
  // presentationPolicy: optional per-element contract
});
const id = mount.collection.ids[0];
const result = mount.collection.request(id, 'onSelect');
console.log(result.mode, result.attempts);
mount.destroy();
```

When scrolling is denied under the default policy, selection requests the
floating fallback. If overlay capability is also denied, the element accepts
inline expansion without a scroll request. If resize is denied too, it
returns `no-acceptable-presentation` without claiming success.

## Real Task Manager acceptance

Run after Patch 14.5 and 14.6:

```powershell
python -m tools.mcel_patch14_7_capability_negotiation_frob
python -m pytest tests/test_mcel_patch14_7_capability_negotiation.py -q
```

The Frob writes JSON plus actual Chromium inline and hover screenshots to
`runtime/reports/mcel-patch14-7-capability-negotiation/`.

The browser cases cover both **390px** and **1440px**, using **normal** and
**stressed** Task Manager process fixtures. They verify:

- Inline details remain **within** their list item and actually affect row
  geometry; no separate selected-inspector region is displayed.
- All canonical source records and provenance remain retrievable. Copy-field
  and copy-record payloads preserve the authoritative bytes.
- Default hover overlay is anchored over the row and under the pointer, without
  changing list geometry. The floating card includes `Expand inline`.
- Timed pointer-leave dismissal can be canceled/protected by native selection.
  User-originated scroll requests a fade; Escape dismisses without immediately
  reopening the card under a stationary pointer.
- Keyboard Enter selects an item; a second select collapses it; the overlay
  action requests inline expansion.
- Denied scroll, denied resize, denied overlay, all-denied and a custom element
  preference are each negotiated and reported.
- Source nodes are not claimed continuous on refresh when generation proof is
  absent. An overlay attached to a replaced/disappeared source is dismissed.
- No network calls or Task Manager operational effects are performed.

The Frob also uses a generic non-process policy to verify the coordinator is
not Task Manager-specific. `new_patch.py` accepts the ZIP as a manifest-free
snapshot patch; do not introduce a manifest into the distributed ZIP.

## Explicit limitations

This is **an opt-in, read-only integration** of the existing Task Manager HTML
and semantic renderer, *not* an automatically enabled production Task Manager
replacement or a live operational data-source integration. Task Manager's
actual actions remain unchanged. The Frob uses a stubbed Clipboard API to
verify copy payloads; host/browser permission is a separate consideration.
Native selection is tested in Chromium, not timed human usability. No separate
human-study advantage is claimed. Complex nested scrolling and virtualization
are not accepted as proven by these initial cases, and lifecycle events not
configured in a given element policy are intentionally inert.

The design is intentionally bounded to one universal element negotiation
contract and one existing application acceptance surface.
