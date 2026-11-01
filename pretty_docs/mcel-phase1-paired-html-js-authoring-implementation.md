# MCEL V1 Phase 1 — Paired Semantic HTML and JavaScript Compilation

**Implemented scope: authoring and canonical-IR compilation only.** See [MCEL V1 Authoring Format](mcel-v1-authoring-format.md) for the final product target. This patch does **not** certify expert layout synthesis, browser rendering or the four real application migrations.

## Official paired source

Create `application.html` alongside `application.js`. `python -m tools.mcel_dsl_compile --input <path>/application.js` automatically discovers the HTML companion; `--html <path>` supports explicit selection. Compilation is atomic: if either source or cross-file relationship is invalid, no candidate IR is produced. JS-only and legacy host-bound compilation remain supported for compatibility.

`application.html` owns the logical hierarchy, semantic region IDs, native landmark meanings, child order, static heading/label text, V1 purpose/task/content/priority/preferences, and HTML file/line provenance. It is a strictly validated **semantic HTML subset**: supported structural tags include `main`, `nav`, `section`, `aside`, `header`, `footer`, `article`, `form`, `output`, `div`, `search`, `details`, `dialog`, document wrappers, and semantic text tags. It forbids executable markup, style/class/CSS, unknown HTML and `data-mcel-*` attributes, and silent loss of non-whitespace content. It is not a general HTML page renderer. Unsupported but valid general HTML must produce a diagnostic, not silently disappear.

`application.js` owns typed models/state, operations, effects and authority. It uses the same `@mcel/app` and `mcel.defineApp(...)` official runtime. A single new `dsl.surface.bindHtml(name, {bindings,relations})` declaration binds actual typed surface-node handles to HTML region IDs; `dsl.surface.relate("region-id", "kind", "other-id")` expresses optional semantic region relations. Neither call duplicates the HTML tree, supplies geometry, or creates executable authority.

## Example

`application.html`:

```html
<main data-mcel-id="application">
  <nav data-mcel-id="locations" data-mcel-purpose="navigation"
       data-mcel-priority="secondary"></nav>
  <section data-mcel-id="workspace" data-mcel-purpose="workspace"
           data-mcel-tasks="browse inspect" data-mcel-priority="primary"
           data-mcel-content-kind="collection" data-mcel-content-ref="state:entries"></section>
  <aside data-mcel-id="details" data-mcel-purpose="inspection"></aside>
</main>
```

`application.js` (fragment inside `defineApp` builder, after real state and surface-node declarations):

```js
const surface = dsl.surface.bindHtml("file-manager", {
  bindings: {locations: [rootList], workspace: [entryList], details: [previewText]},
  relations: [dsl.surface.relate("details", "contextual-to", "workspace")],
});
return {states: [roots, entries, preview], surfaces: [surface]};
```

The fixture `tools/fixtures/mcel_html_pair` contains a fully compiling bounded-state example. It is **not** an operational File Manager and has no filesystem authority.

## Canonical representation and invariants

The compiler produces one `mcel.application-ir.v1` object. In its surface's `interfaceIntent`, each region carries a stable `interface-region:<app-id>.<id>`, `parentId` (or null at the root), `ordinal` preserving authored semantic/text reading order, `nativeTag`, optional `purpose`, `tasks`, `content`, `priority`, `preferences`, `staticContent`, `members`, and `htmlSource` provenance. `hierarchy.rootId` names the sole root. Relationships reference existing region IDs. The two authored files are bound to source hashes under `provenance.frontend.sourceFiles`.

The compiler and canonical IR validator reject duplicate IDs, missing/surplus/duplicated surface bindings, unresolved content refs and relations, invalid role-purpose contradictions, malformed parent/ordinal chains, invalid vocabulary and layouts authored alongside paired HTML. No HTML content or relation grants authority. Semantic fingerprints include semantic HTML hierarchy but exclude source path metadata; source-binding fingerprints retain both files' provenance.

## Run acceptance

```powershell
python -m tools.mcel_dsl_compile --input tools/fixtures/mcel_html_pair/application.js
python -m tools.mcel_phase1_paired_authoring_frob
python -m pytest -q tests/test_mcel_phase1_paired_authoring.py tests/test_mcel_dsl_compiler.py
```

Expected Frob result: `MCEL_PHASE1_PAIRED_AUTHORING PASS_PAIRED_COMPILER_ONLY`. It does not imply generated layout/browser functionality. Subsequent phases must consume the preserved hierarchy in a real structural synthesizer, close DSL operational gaps and pass four-app functional and visual acceptance.
