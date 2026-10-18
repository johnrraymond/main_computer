# MCEL Patch 13 — host-bound canonical layout runtime cutover

## Authority and lifecycle

`app.layout.tree` is the canonical source of structural layout obligations. The
validated `mcel.application-ir.v1` layout entry contains `layoutTree`, and the
**generic** host-bound IR projector emits it inside `contracts/layout.js`. The
application package and runtime projection fingerprints bind that module to
its source package. There is no Calculator- or Code-Editor-specific layout
module generator.

At host-bound mounting, `McelHostBoundApplicationRuntime`:

1. Verifies the existing package, manifest, generated modules, intent bindings,
   and facade before declaring the mount available.
2. Requires a canonical layout engine if the generated layout contract declares
   a tree. A missing engine, absent/ambiguous layout root, or malformed tree
   refuses the mount; **it does not silently fall back to application CSS**.
3. Claims exactly one declared, descendant layout root, preserving the existing
   application host HTML and module adapter. Unrelated regions remain
   application-owned during this incremental migration.
4. Marks a hidden subtree as **deferred**, never as verified. Browser
   observation promotes it only after the subtree becomes measurable. A
   visible zero-width root refuses rather than masquerading as hidden.
5. Runs `mountCanonicalLayoutTree`: bounded isolated candidate trials, CSS
   application, live geometry measurement, and independent atomic text-fit
   diagnostic. Status `pass` means the available independent detector passed;
   status `observed-only` means verification was not available and is **not**
   full conformance certification. A refused layout is recorded as such.
6. Exposes `mount.layoutStatus`, `mount.layoutReport` and
   `mount.refreshLayout()` and records `data-mcel-host-bound-layout-status`.
   Observes visibility and width changes and restores pre-mount inline styles
   on unmount.

Calculator migrates **only** the nested scientific keypad tree (range controls
and scientific function grid). Its existing workspace inferred layout remains
active; the legacy HTML-driven adaptive-grid controller stands down in any
subtree explicitly claimed by the canonical controller. Code Editor migrates
its bounded activity rail as a first independent consumer; its full dockable
workbench continues under the existing Code Editor layout contract.

## Proof and unproven claims

Tests use **real generated modules**, fingerprinted package manifest records,
and actual Calculator/Code Editor host HTML in Chromium. They check deferred
mounting, activation after visibility, layout authority, Calculator's compact
three-column keypad and no split labels, Code Editor's five activity controls,
refusal if the canonical engine is absent, and style restoration on unmount.

These tests prove this **nested host-bound cutover**. They do **not** establish
that all of Calculator or Code Editor presentation is canonical-tree governed,
that the package-document host is canonical, that all CSS declarations are
MCEL-owned, or that `fullSemanticRuntimeProven` can become true. The existing
completion gate and independent package-host truth gate remain separate.
Permanent application-source repair is not authorized by runtime layout
selection, and the generated layout does not mutate source CSS files.
