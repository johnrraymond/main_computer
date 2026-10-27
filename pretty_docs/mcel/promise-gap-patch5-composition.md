# MCEL Patch 5 — Public composition and bounded Inventory browser slice

## Purpose

This patch attacks the first observed barrier in the pinned Promise Gap Frob:
`mcel.dsl.v1` did not expose keyed list state, record schemas, bound forms, or
collection surfaces. It adds public declarations and a deliberately bounded,
generic runtime consumer for a *single keyed collection per mounted app*.

No Website Builder files, production routes, persistent state, deployment, or
canonical promise-certification policy are changed.

## What is reusable

- `dsl.field.record(...)`, `dsl.field.list(record,{key})`, and canonical list
  state produce portable semantic records.
- `dsl.intent.keyedCollection(name,state)` declares add/update/remove mutations
  against a stable key and canonical-write effect metadata.
- `dsl.surface.form(...,{intent:...})` and
  `dsl.surface.collection(...,{state,actions,...})` bind the authoring surfaces.
- `main_computer.mcel_declarative_collection_package.build` compiles a public
  authored declaration and copies the **same** shared runtime/CSS plus the
  validated application IR into a standalone package. It emits no domain-specific
  JavaScript or CSS.
- `McelCollectionApplication.mount(root,ir)` refuses mismatched schemas, invalid
  action signatures, unregistered effect authority, malformed initial records,
  and duplicate mounts before altering the target root. Its state is isolated
  per mount, validated before updates, and volatile (memory only).
- Row editors are generated from non-key record fields, not an Inventory-specific
  quantity field. Record keys remain immutable under row editing.

## Run the acceptance harness

From the `main_computer` repository root:

```text
python -m tools.mcel_collection_browser_acceptance
python tools/mcel_promise_gap_frob.py
```

The dedicated acceptance harness writes `acceptance.json`, screenshots, an IR
snapshot, and the packaged standalone candidate beneath
`runtime/reports/mcel-promise-gap/inventory-browser/`. The main Promise Gap
Frob runs the same acceptance path, retains the frozen Inventory/Work Orders
packet, and reports `PARTIAL_VERIFIED` for composition and functionality when
that bounded Inventory slice passes. The final promise **remains INCOMPLETE**.

For a local browser preview, serve the generated `candidate/` directory with
Python's static server; fetch of `app.ir.json` needs HTTP, not a `file://` URL:

```text
python -m http.server 8900 --bind 127.0.0.1 --directory runtime/reports/mcel-promise-gap/inventory-browser/candidate
```

Open `http://127.0.0.1:8900` in a local browser. Do not expose the preview to a
public address or use it as a deployed production service.

## What the evidence proves — and does not prove

The four public DSL probes compile. Real Chromium DOM execution of the packaged
JS, CSS, and compiler-produced IR tests record creation, independent instance
state, search, sorting, text/numeric updates, keyed deletion, refused duplicate
keys, invalid values and stale actions, and contract tampering. The Frob observes
viewport widths 320, 390, 560, and 1100 px for basic containment.

Browser navigation to local servers was unavailable in the original execution
environment, so the harness inserts the *unchanged packaged assets* into a
Chromium document and mounts the same IR. This is not proof of the host's normal
route, app install procedure, deployment, remote effects, persisted canonical
state, or independent authority receipts. Displayed in-memory receipts are
self-observed local evidence only. The view uses generic CSS, not a claim of
production-quality design or shared-layout-engine integration.

**Work Orders remains unimplemented and held out**. Its failure to be independently
authored must not be converted to a PASS by Inventory results. Aesthetic quality,
contrasting variants, and comparative authoring economy also remain unverified.

## Next barrier

Demonstrate a second, structurally different application through the same public
contracts and attach independent runtime/action authority. Promote neither
application until normal-route execution and the full pinned packet succeed.
