# MCEL V1 Authoring Format: Semantic HTML + Official JavaScript DSL

**Status:** normative **V1 target, not yet certified as implemented**. [Product Contract](mcel-v1-product-contract.md) governs; [Current DSL documentation](mcel-official-vanilla-javascript-dsl.md) and [Status](mcel-status-and-roadmap.md) distinguish the implemented path.

## Canonical package

```text
mcel_apps/<app-id>/
  application.html       # author-owned semantic HTML5 hierarchy
  application.js         # author-owned official @mcel/app JavaScript DSL
  requirements.md        # app requirements/acceptance
  tests/                 # authored behavioral/conformance tests
  mcel.app.json           # package identity and authority where required
  generated/              # compiler-owned artifacts; not dual-authored
```

The existing `tools/mcel_create_app.py` must ultimately scaffold this exact authoring boundary. A compiled application has one source of semantic truth across the pair, not two independent app definitions. Generated artifacts are replaceable outputs.

## Semantic HTML

Use native HTML5 landmarks and nesting for real logical hierarchy. Provide stable `data-mcel-id` on semantic regions, optional `data-mcel-purpose`, `data-mcel-tasks`, `data-mcel-content-kind`, `data-mcel-content-ref`, `data-mcel-priority` and `data-mcel-prefer-*` from the [V1 Vocabulary](mcel-v1-semantic-vocabulary.md). Region IDs are stable semantic identities, *not* authored runtime CSS selectors. Normal headings and meaningful static content are allowed. Runtime collections and process/file rows are bound from data, **not authored as static lists of every item**.

Examples of forbidden standard-path layout authoring: CSS grids/absolute placement, fixed pixel panes, copied component DOM/table rows, app-specific layout trees, hardcoded breakpoints, legacy host-page `innerHTML` used as the generated interface, or dependencies on internal renderer selectors.

## JavaScript DSL

`application.js` uses strict vanilla JavaScript, `require("@mcel/app")`, and `mcel.defineApp(metadata, (dsl) => ...)` as already specified in the [official JS DSL](mcel-official-vanilla-javascript-dsl.md). It describes typed models, data sources, identity, state, intent inputs and results, read/write dependencies, authorized effects, capabilities, validations and scenarios. It is *declarative compiler input*, not an arbitrary compile-time hook that fetches live data or edits the DOM. It does not re-author the HTML tree with `surface.region()` for V1. Interface-region relationships, when genuinely necessary, are declared through the canonical relationship mechanism in JS, with endpoints resolved against authored HTML IDs. The compiler can internally derive compatible region handles; those are not a second authored layout.

## Identity and binding

The canonical region identifier is an exact, package-local `data-mcel-id`, unique within the authored interface tree. HTML content bindings such as `data-mcel-content-ref="model:directory-entries"` must resolve against the typed JS model/state namespace and correct content type. Region relationships use region IDs, and capability bindings resolve to existing declared operations or state; unknown, duplicate, mismatched or ambiguous references fail compilation.

**V1 target cross-source contract:** normalize each HTML region into an IR region with `{id, parentId|null, ordinal, nativeTag, purpose?, tasks?, content?, priority?, preferences?, provenance}` and link to the operational IR via resolved refs. This is a *target representation*, not a claim that the current canonical JSON schema already has these fields. Exact implementation names can be mapped in the compiler, but semantic meaning and provenance must be preserved and validated.

## Hierarchy and order

HTML ancestry defines logical containment. Child order conveys meaningful reading/tab/semantic order, which the compiler preserves. MCEL can adapt visual placement to screen size, but may not reorder essential logical relationships or break keyboard/focus sequence. HTML `<main>`, `<nav>`, `<aside>`, `<section>`, `<form>`, `<output>` and other ordinary tags provide semantic defaults where unambiguous. A tag does not imply a particular panel side, UI widget, authority or effect.

## Conflict resolution

- JS is authoritative for executable domain facts, effects, data sources, permissions, state and behavior.
- HTML is authoritative for logical hierarchy, region identity and explicitly declared interface intent.
- Native HTML semantics act as defaults; explicit compatible MCEL semantic attributes refine them. Contradictions are rejected, not overridden silently.
- MCEL owns concrete widget selection, geometry, responsive arrangement and stylistic defaults. Soft preferences never override correctness or authorization.
- Duplicate `data-mcel-id`, invalid or nested contradictory landmarks, unknown `data-mcel-*` names/values, unsupported DSL keys, unresolved bindings, cyclic parentage, and incompatible relations produce targeted errors with source file/position.
- If no safe composition exists, compilation or candidate promotion reports an unmet requirement; it does not fall back to an opaque host-bound layout while claiming semantic V1 success.

## Source ownership

The author owns both `application.html` and `application.js`, stable names, actual logic and explicit product decisions. MCEL owns its normalized IR, generated layout, CSS, instantiated widgets and runtime wiring. `requirements.md` may define correct task behavior; it cannot manufacture executable operations. Specialized controls (Monaco, math engines) may be referenced through governed capability adapters without embedding their whole page markup.

## Compile diagnostics

The two files compile as **one atomic application**. An error from either source fails the combined result with actionable provenance. The compiler must distinguish unsupported target syntax from successful current compilation and must not silently drop unknown semantic hints. No generated output can claim V1 conformance without passing cross-file reference, authority and hierarchy checks.

## Complete example

The paired fragments below illustrate the desired V1 source format; they are **target syntax and not an executable current-source fixture**. `model:directory-entries` and the referenced binding IDs must be declared in real JS; names below illustrate references, not fabricated filesystem authority.

`application.html`:

```html
<main data-mcel-id="workspace">
  <nav data-mcel-id="locations" data-mcel-purpose="navigation"></nav>
  <section data-mcel-id="files" data-mcel-purpose="workspace"
           data-mcel-tasks="browse inspect"
           data-mcel-content-kind="collection"
           data-mcel-content-ref="model:directory-entries"
           data-mcel-priority="primary"></section>
  <aside data-mcel-id="details" data-mcel-purpose="inspection"></aside>
  <section data-mcel-id="file-actions" data-mcel-purpose="commands"></section>
  <output data-mcel-id="operation-status" data-mcel-purpose="status"></output>
</main>
```

`application.js` (abridged shape; the declared handles and effects must be supplied by the real application):

```javascript
"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  { id: "file-manager", title: "File Manager", requirements: "requirements.md" },
  (dsl) => {
    // Declare typed directory-entries model, real read/list/preview intents,
    // state and identity, capability authority, effects and proof scenarios.
    // If semantic coordination is required, relate existing HTML region IDs;
    // do not redefine the <main>/<nav>/<section> hierarchy here.
    return {models: [], states: [], capabilities: [], intents: [],
            surfaces: [], layouts: [], scenarios: []};
  }
);
```

The empty arrays above are **schematic placeholders**, not a working File Manager or evidence of executable functionality. The V1 compiler must bind actual authored handles through the paired source contract.

## Compatibility

Current JS-only and `hostBound` apps remain historically accurate implementation paths and migration inputs. V1 targets **one** HTML+JS source format; migrating must preserve real functional/authority parity and never silently discard source semantics. Future vocabulary-policy versioning is a separately specified compatibility obligation. No `_v1` keys or design-scheme pins are used in the four V1 reference sources now.
