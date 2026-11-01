# MCEL four-app semantic interface authoring contract (07B.1c)

> **MCEL V1 product target (normative):** [V1 Product Contract](mcel-v1-product-contract.md) · [HTML + JavaScript Authoring Format](mcel-v1-authoring-format.md) · [Semantic Vocabulary](mcel-v1-semantic-vocabulary.md) · [Compiler/Layout/Runtime](mcel-v1-compiler-layout-runtime-contract.md) · [Four-App Acceptance](mcel-v1-four-app-acceptance.md).  
> **Implementation boundary:** This document records current/historical implementation and evidence where indicated. The paired-file V1 contract is the required finished product, **not** a claim that current source supports it.

This page records the **07B.1c experimental JS-only semantic role probe** and its verified scope. The finished V1 target instead uses semantic `application.html` plus official `application.js`, with the [complete five-axis vocabulary](mcel-v1-semantic-vocabulary.md) and [four-app acceptance](mcel-v1-four-app-acceptance.md). The role probe is not an alternative final authoring contract.


## Governing objective

The authored JavaScript defines application behavior and **interface meaning**. The MCEL
compiler retains those meanings; a future synthesizer chooses representations and layout.
Neither the author nor the layout engine may invent operations or authority.

**Reference applications**: File Manager, Task Manager, Code Editor, Calculator.
**Excluded**: Website Builder and MCEL Lab.

## Official source path

`mcel.defineApp(...)` → `mcel_dsl_runtime.js` → validated
`mcel.application-ir.v1` → future interface synthesis → existing runtime.

No new authoring language, secondary canonical IR, new renderer, application-name dispatch,
or browser-time redesign is introduced by 07B.1c.

The public `dsl.surface.region(name, {children, role?, priority?, prefer?})`
records the author's meaning. Roles have *no implicit pixel, selector, DOM, or
zone meaning*. `priority` and `prefer` are design guidance, not authorization or
hard runtime constraints. In particular, `prefer.side` supports `start`, `end`,
`top`, `bottom`. Navigation has **no hardcoded side**. MCEL's default positioning
is a later versioned design-policy decision.

The closed initial role set is:

| Role | What the author means |
| --- | --- |
| `main-workarea` | Center of the current task |
| `navigation` | Changes active working context |
| `collection` | Browse or compare entities |
| `document` | Document work surface (not an editor implementation) |
| `inspector` | Information about a chosen subject (relationship remains author intent) |
| `commands` | Available, governed operations |
| `results` | Computation or query outputs |
| `status` | Status and receipts |
| `auxiliary-workarea` | Supporting task space |
| `mode-selector` | Changes task/view mode |

A role-less `surface.region` remains an ordinary structural authoring wrapper
for existing source compatibility. A role-bearing region owns at least one
surface node. Each node must belong to exactly one role-bearing region when
interface intent is present. Members are canonical identity references, **not**
DOM selectors. Sibling order is not a layout instruction.

`dsl.surface.relate(fromRegion, kind, toRegion)` expresses interface-purpose
relationships, with the closed kinds: `selects`, `contextual-to`, `supports`,
`reports-for`, `switches-mode-of`. These relationships are **author-declared
intent, not proof of operational dataflow**. Runtime selection/side effects
must still be grounded separately in validated application semantics.

`dsl.surface.define(name, {root, relations?})` emits optional
`surfaces[].interfaceIntent` containing `mcel.interface-intent.v1`, `regions`,
and `relations` in canonical ID order. Each region is an IR semantic node of
kind `interface-region`; all references resolve through the existing IR
reference validator. A single canonical Python validator checks the complete
interface-intent contract (roles, preferences, unique IDs, membership,
relations, no extraneous keys). The JavaScript builder also rejects unknown
options at source time so no field is silently dropped.

### Example (supported official syntax)

```javascript
const list = dsl.surface.collection('files', {state: files, key: 'path', fields: ['name']});
const editor = dsl.surface.text('document-preview', {value: documentText});
const nav = dsl.surface.region('files-nav', {
  role: 'navigation', priority: 'secondary', prefer: {side: 'start'}, children: [list]
});
const work = dsl.surface.region('document-work', {
  role: 'document', priority: 'primary', children: [editor]
});
const surface = dsl.surface.define('app', {
  root: dsl.surface.region('application', {children: [nav, work]}),
  relations: [dsl.surface.relate(nav, 'selects', work)]
});
```

**This does not create an editable editor.** `dsl.surface.text` still exposes
a state value. Compilation proves the semantic description is retained, not
that the application can edit files or that the runtime implements navigation.

## Acceptance separation

1. **Semantic-role probe PASS**: Official JS compiles; IR preserves and validates
   declared roles/relationships; no executable layout or unauthorized effects.
2. **Full authoring conformance** (NOT YET ACHIEVED): Real models, operations,
   effects, state, identity, and result delivery of all four applications are
   expressible through the official public DSL without host-authored layout.
3. **Synthesis conformance** (NOT YET ACHIEVED): MCEL selects valid presentations
   from roles/relationships without inventing workflows.
4. **Hosted runtime conformance** (NOT YET ACHIEVED): At widths
   320/390/560/1100/1272, complete effects, receipts, focus, data identity,
   keyboard interactions, scroll and lifecycle are verified.

The live Calculator/Code Editor `application.hostBound` sources are preserved
as authoritative behavioral references; the existing Task Manager and File
Explorer executable/compiled artifacts are also references. These new fixtures
are **independent probes**, not replacements or historical-source recovery.

See `pretty_docs/mcel-promise-patch07b1c-four-app-authoring.md` for the
machine-executable implementation sequence and known blockers.
