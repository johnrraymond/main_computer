# MCEL V1 Reference Authoring Examples — Four Paired Sources

**Status:** conceptual **target syntax only**; these are **not** executable current-source fixtures and are not claimed to compile. [V1 Product Contract](mcel-v1-product-contract.md), [Authoring Format](mcel-v1-authoring-format.md), [Vocabulary](mcel-v1-semantic-vocabulary.md), and [Four-App Acceptance](mcel-v1-four-app-acceptance.md) are authoritative. Existing `application.js` hostBound implementations are *working implementation baselines*, not the target way to author outer layouts.

## Common source shape

Each app has its own **valid HTML5 fragment in `application.html`** plus **strict vanilla JS in `application.js`**. HTML owns logical landmarks, identities, meaningful nesting, tasks, content shape and soft preferences. JS owns models, state, operations, authority, and relations to the HTML IDs. MCEL supplies concrete widgets, panes, geometry and responsive behavior. We show only the **minimal behavioral references necessary to explain the relationship**; comments identify declarations that must be filled using the official validated JS DSL. Empty arrays are *not* a passing real application.

### Common JavaScript skeleton (not executable as a complete app)

```javascript
"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "<application-id>", title: "<title>", requirements: "requirements.md"},
  (dsl) => {
    // The real app declares actual typed models and identity keys; authorized
    // reads, writes, state, inputs, capabilities, effects and proof scenarios.
    // Region relations refer to the IDs in the paired application.html.
    // The V1 cross-file binding syntax is a target awaiting implementation.
    return {models: [], states: [], capabilities: [], intents: [],
            surfaces: [], layouts: [], scenarios: []};
  }
);
```

The placeholder arrays above demonstrate the root syntax only and are **not** a substitute for real app definitions or real operational bindings.

## 1. File Manager / File Explorer

`application.html`:

```html
<main data-mcel-id="file-manager">
  <nav data-mcel-id="locations" data-mcel-purpose="navigation"
       data-mcel-tasks="browse locate"></nav>
  <section data-mcel-id="entries" data-mcel-purpose="workspace"
           data-mcel-tasks="browse inspect manage"
           data-mcel-content-kind="collection"
           data-mcel-content-ref="model:directory-entries"
           data-mcel-priority="primary"></section>
  <aside data-mcel-id="entry-details" data-mcel-purpose="inspection"
         data-mcel-content-kind="record"></aside>
  <section data-mcel-id="file-commands" data-mcel-purpose="commands"></section>
  <output data-mcel-id="file-status" data-mcel-purpose="status"></output>
</main>
```

`application.js` (required *real contracts*, not fabricated capability calls): declare real filesystem roots and scoped entry types with stable IDs; current-root and selected-entry state; authorized list/search/read/preview operations with typed inputs and outputs; available file commands under their real permission/effect contracts; and receipts. Express only supported `locations → entries`, `entry-details → entries` and `file-commands → entries` interface relationships when matching executable facts exist. The exact JS cross-file reference builder is a future contract, not yet a proven API. On narrower hosts MCEL may turn inspection into a contextual view without erasing selection or losing read authority.

## 2. Task Manager

`application.html`:

```html
<main data-mcel-id="task-manager">
  <section data-mcel-id="process-workspace" data-mcel-purpose="workspace"
           data-mcel-tasks="monitor compare manage"
           data-mcel-content-kind="collection"
           data-mcel-content-ref="model:processes" data-mcel-priority="primary"></section>
  <nav data-mcel-id="monitoring-views" data-mcel-purpose="navigation"></nav>
  <aside data-mcel-id="process-metrics" data-mcel-purpose="inspection"
         data-mcel-content-kind="series"></aside>
  <section data-mcel-id="process-commands" data-mcel-purpose="commands"></section>
  <output data-mcel-id="process-status" data-mcel-purpose="status"></output>
</main>
```

`application.js`: declare the *real* live process observations, stable process-generation identity where available, declared notebook perspectives and refresh, and existing terminate/kill and server-control operations with exact authorization/receipts. No fake process filters or unimplemented selection behavior. Explicit `process-commands acts-on process-workspace` must refer to the actual allowed command/subject binding. MCEL can present monitoring views compactly on a narrow host while preserving accessible, permission-checked actions.

## 3. Code Editor

`application.html`:

```html
<main data-mcel-id="editor-app">
  <nav data-mcel-id="documents" data-mcel-purpose="navigation"></nav>
  <section data-mcel-id="editor" data-mcel-purpose="workspace"
           data-mcel-tasks="edit review" data-mcel-content-kind="document"
           data-mcel-content-ref="state:active-document"
           data-mcel-priority="primary"></section>
  <aside data-mcel-id="review-assistance" data-mcel-purpose="assistance"></aside>
  <section data-mcel-id="aider-plan" data-mcel-purpose="results"
           data-mcel-content-kind="structured-data"></section>
  <section data-mcel-id="editor-actions" data-mcel-purpose="commands"></section>
  <output data-mcel-id="editor-status" data-mcel-purpose="status"></output>
</main>
```

`application.js`: declare actual document identity, Monaco editing adapter/capability, dirty state, supported save/restore, Aider plan preview, reviewed patch behavior and guarded source-writing effects. Do not invent an unsupported diagnostics intent merely to fill a panel; if diagnostic data is added later it needs its own legitimate model. MCEL chooses the editor's surrounding organization without copying Monaco's host page layout. On a narrow host, auxiliary planning/results can become a contextual view without losing unsaved edits or focus.

## 4. Calculator

`application.html`:

```html
<main data-mcel-id="calculator-app">
  <section data-mcel-id="calculation-input" data-mcel-purpose="input"
           data-mcel-tasks="compute" data-mcel-content-kind="expression"
           data-mcel-priority="primary"></section>
  <nav data-mcel-id="calculation-modes" data-mcel-purpose="mode-control"></nav>
  <section data-mcel-id="calculation-result" data-mcel-purpose="results"
           data-mcel-content-kind="structured-data"></section>
  <aside data-mcel-id="optional-graph" data-mcel-purpose="results"
         data-mcel-content-kind="series" data-mcel-prefer-representation="graphical"></aside>
  <output data-mcel-id="calculation-status" data-mcel-purpose="status"></output>
</main>
```

`application.js`: declare the actual calculator modes, typed expression/value inputs, numeric and symbolic computation capabilities as supported by the baseline, result identity/lifecycle and real graphing/Mathics backend availability where present. A series/graph suggestion is not a promise of a chart engine for every expression. No invented calculator operations. MCEL may collapse mode controls and graph information on a narrow host while keeping computation and result legible.

## Review criteria for all four

The four pairs deliberately differ in semantic *purpose*, task mix and content types but share one authoring model. The reader must not interpret any fragment as complete executable source. Certification requires actual data, capabilities, effects, cross-file bindings and proof—not decorative UI or a successful parse. See [Four-App Acceptance](mcel-v1-four-app-acceptance.md). Website Builder and MCEL Lab are excluded from this V1 qualification suite.
