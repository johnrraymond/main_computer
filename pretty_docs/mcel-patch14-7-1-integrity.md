# Patch 14.7.1 — Contract Integrity, Transactional Fallback, Live Continuity

**Status:** offline engineering acceptance; laboratory-only, read-only Task Manager integration. **Requires Patch 14.7.** Does not promote the universal profile into the production Task Manager route.

## Corrections

1. **Canonical collection contract.** A negotiated `mcel-row` may have **at most one** inline-detail presentation slot, identified by `data-mcel-presentation-slot="inline-detail"` and the exact owning `data-source-row-id`, in addition to its ordered semantic fields. The existing layout authority still validates every registered field, exact rendered values, source identities, and refuses unknown/forged children. The slot is never treated as an extra semantic field or hidden by semantic field-visibility decisions.
2. **Outer layout contract.** When an opted-in negotiated collection contains that registered presentation slot, the canonical workspace tree excludes the slot and its row's aggregate text from the region-level *ordinary control* gate. The collection authority remains responsible for the slot and its semantic fields. Other region controls retain their original validations. This prevents copy controls and exact-source evidence from being mistaken for normal sibling controls.
3. **Transactional fallback.** Inline expansion is staged without first removing a committed expansion or hover card. If scroll-to-reveal fails or throws, staged geometry is removed and the prior scroll offset, selected record, and committed DOM are preserved. The element's negotiation policy decides the next fallback. Selection and `aria-pressed` update only upon successful fulfillment; successful floating selection clears old inline geometry.
4. **Verified identity / live updates.** When a generation token proves record continuity, retained rows aren't unnecessarily moved in the DOM. The existing detail shell is retained; its displayed facts, exact source values, provenance, and copy payloads refresh against the new authoritative data. While native selection or keyboard focus is inside the detail surface, a pending update is deferred until interaction ends, rather than deleting the selected text. No generation token still means no continuity claim.
5. **Capability freshness and anchoring.** The overlay host consults the live capability object rather than only the initial snapshot. The scroll host exposes capture/restore of a visible row anchor (and a raw scroll-position fallback when its anchor disappears).

## Verification

Run these after applying Patch 14.7.1:

```powershell
python -m tools.mcel_patch14_7_1_integrity_frob
python -m pytest -q tests/test_mcel_patch14_7_1_integrity.py tests/test_mcel_patch14_7_capability_negotiation.py tests/test_mcel_patch14_6_promise_verification.py tests/test_mcel_patch14_5_human_first.py
```

The new Chromium Frob exercises **normal and stressed** actual Task Manager semantic snapshots at **390px and 1440px**, including successful inline layout revalidation, refusal of a forged slot, live detail and exact-record copying, deferred updates under native selection, hover card retention and refresh, complete denial preserving prior expansion, execution-time scroll failure followed by element-selected floating fallback, and stable scroll anchors. Source provenance remains authoritative; the Frob executes no operator actions or network requests.

The report is written to `runtime/reports/mcel-patch14-7-1-integrity/report.json`.

## Limitations / subsequent work

This patch does **not** implement full nested multi-host negotiation, every advertised event trigger, complex touch behaviors, or a production promotion. It does not establish human usability superiority. Active text selection may deliberately display the previously seen snapshot until selection ends. Caller-driven reordering of a selected row may still require an explicit interaction-preservation strategy. Tests are browser-level offline laboratory fixtures; operational Task Manager needs a separate production acceptance campaign.
