# MCEL V1 Semantic Interface Vocabulary

**Authority:** closed normative **V1 target vocabulary**, not a claim that today's DSL, compiler or renderer accepts each token. Governed by [V1 Product Contract](mcel-v1-product-contract.md) and the two-file [Authoring Format](mcel-v1-authoring-format.md). Tokens are MCEL-owned proposals informed by general interface-design practice, not tokens mandated by HTML or any external standards.

## Model and ownership

An author may describe an area's **purpose** (why it exists), **user tasks** (goals it serves), **content** (what it presents), **relationships** (how areas coordinate), **priority**, and soft **preferences**. These are independent axes and may not be collapsed into a single `role` enumerator. The HTML file owns region purpose/tasks/content references/priority/preferences and logical hierarchy. JavaScript owns the actual models, state, operations, effects and authority, plus canonical region relationships where needed. MCEL owns inferred presentation defaults, actual components, styling and responsive geometry.

**Provenance:** Each meaningful field is `explicit` (author), `verified-derived` (entailed by validated semantics), `candidate` (design hypothesis) or `system-policy` (layout default). Candidate inference is never an established executable fact. Absence of a term means *unspecified*, not an invented intent. A task or relationship cannot manufacture an operation, selection state, read-result mapping, mutation or permission. Per-token consumer verification must establish at least one meaningful design, eligibility, diagnostic or acceptance effect—not necessarily a different region tree in every case.

## Purpose (10 tokens)

One primary purpose per semantic region. HTML landmark defaults may establish purpose where unambiguous; explicit compatible purpose overrides a default only when both convey the same general meaning. A region can serve multiple user tasks, but has one dominant purpose. `data-mcel-purpose` is the author-facing carrier.

| Token | Normative meaning | Inference and design consumer | Must not imply |
| --- | --- | --- | --- |
| `workspace` | Context in which the user performs substantive work | Consider an expansive, focusable work surface with primary content | A specific editor or pane geometry |
| `navigation` | Change active location, document, view or operating context | Offer navigation patterns and context preservation | Hierarchy, a tree widget, a particular side |
| `inspection` | Examine a subject already established by state/context | Offer detail reading and conditional detail access | A selection/dataflow edge absent from JS |
| `commands` | Expose permitted operations in relevant context | Group and enable only authorized commands | Write permissions or invented commands |
| `status` | Report application condition, progress, errors or receipts | Non-disruptive, accessible feedback policy | An operation, successful receipt or live feed |
| `results` | Present outputs of actual operations or computation | Result presentation appropriate to output type | An operation result that JS did not declare |
| `input` | Receive user-supplied data for real operations | Input affordances, validation and focus strategy | A submission effect or endpoint |
| `mode-control` | Switch a valid declared mode or view | Context-switch affordance preserving state | Modes that the application does not support |
| `query-controls` | Find, filter or refine information in a collection/context | Query interaction patterns and relation to bound data | Filtering/search implementation absent in JS |
| `assistance` | Provide explanatory guidance about a task/context | Contextual guidance, non-primary salience | An AI agent, suggestion engine or external call |

## User task (12 tokens)

Tasks describe **goals**, not clicks or low-level control mechanics. Multiple tasks per region are allowed (`data-mcel-tasks` is a whitespace-separated set). They may be author-explicit or derived when provable from application semantics; task labels never grant effects. Task choices inform importance, candidate patterns and evaluation.

| Token | Goal and permissible interpretation | Not implied |
| --- | --- | --- |
| `browse` | Explore a set or space of available things | A tree or pagination |
| `locate` | Find a target item, location or information | Search/filter operation without a backing capability |
| `inspect` | Read/examine a subject in detail | A side inspector or auto-preview effect |
| `compare` | Evaluate two or more items or states | A diff source or synchronized selection |
| `monitor` | Observe changing state over time | A polling source or subscription not declared |
| `edit` | Change content under declared edit authority | Source-write permission or save function |
| `create` | Produce new content/entity through authorized operations | Write effect or object creation policy |
| `compute` | Evaluate input into computed output | Mathematical operation implementation |
| `review` | Evaluate a proposed change/result before commitment | Approval operation or automatic application |
| `manage` | Organize/control resources as permitted | Destructive process or file authority |
| `configure` | Adjust persistent settings via real declared behavior | Persistence effect absent from JS |
| `diagnose` | Investigate failures, warnings or unexpected conditions | A diagnostic backend or scanner |

Low-level `select`, `sort`, `filter`, `undo`, `redo`, `click`, `navigate`, and `execute` are mechanics/operation kinds, not additional v1 user-goal tokens. Their truth belongs in operational IR.

## Content kind (12 tokens)

`data-mcel-content-kind` selects a content-shape claim; `data-mcel-content-ref` is an independently validated ref to a real typed model/state. Content kinds are **not** widgets. Content kind should be inferred from the validated JS type if unambiguous, otherwise declared and verified against its reference. Zero or one principal typed binding per region in the V1 target; composite workareas use child semantic regions. Unknown/unbound references fail compilation.

| Token | Shape and meaning | A permitted presentation candidate, not a requirement |
| --- | --- | --- |
| `collection` | Identifiable set of entities | list, table, gallery |
| `record` | Structured individual entity | details or property presentation |
| `hierarchy` | Entities with verified parent/child relations | tree, nested list, breadcrumbs |
| `document` | Content with document identity/lifecycle | text editor, rich view |
| `scalar` | Single atomic value (number, text, boolean, etc.) | metric, field or label |
| `expression` | Structured evaluable expression | expression entry or readout |
| `series` | Ordered measurements or values, potentially temporal | chart, table, sparkline |
| `log` | Ordered event/history entries | activity stream |
| `diagnostic` | Problem, warning or actionable finding | problem panel, inline issue |
| `diff` | Differences between identifiable versions/states | unified or side-by-side diff |
| `media` | Image, audio, video or associated content | viewer or playback affordance |
| `structured-data` | Nested/composite typed data not fitting a narrower kind | form, outline or structured editor |

A region's content kind cannot invent source facts (e.g., `hierarchy` never establishes unverified parent IDs; `series` never establishes polling). The content resolver must retain source provenance and record-identity contracts.

## Interface relationships (8 tokens)

Relations are declared against **existing named regions**, with an exact `from` and `to`, under a single JS relationship contract; they are *not* arbitrary DOM selector edges. Direction is meaningful. Region reference resolution, duplicate edges, unsupported kinds and incompatible endpoints must be diagnosed. Neither endpoint may be implicitly created. Verified operational edges remain in the JS domain graph; the interface relation describes an author-desired coordination/interpretation and does not assert a causal dataflow that the executable semantics lack.

| Token | Direction `from` → `to` | Presentation implication | Forbidden inference |
| --- | --- | --- | --- |
| `selects` | Selecting in `from` establishes/requests subject for `to` | Coordinate selection and context if supported | Fabricated selection state, read link or producer |
| `contextual-to` | `from` is contextual information for `to` | Associated access or responsive inspector treatment | Causal dataflow or data subscription |
| `supports` | `from` assists work occurring in `to` | Secondary prominence and discoverability | A required execution order |
| `reports-for` | `from` reports on operation/activity in `to` | Appropriate status location and accessibility | A successful effect receipt |
| `switches-mode-of` | `from` chooses the declared working mode of `to` | Coordinated mode UI and state preservation | New modes or state writes |
| `acts-on` | Commands exposed in `from` target subject/context in `to` | Relevant placement and enabled/disabled state | Authority, binding or capability not actually declared |
| `informs` | `from` contributes explanatory data to `to` | Supportive emphasis or comparison | Verified data dependency or automatic refresh |
| `accompanies` | `from` should remain conceptually associated with `to` | Soft proximity/accessibility across breakpoints | Causality, hard adjacency, identical scroll ownership |

`accompanies` is deliberately **noncausal**. Existing `data-mcel-reveals` and Calculator's `SELECTS`, `GROUNDS`, `INFORMS`, `SUPPORTS` markup carriers have separate provenance/semantics; identically spelled words do *not* equate the contracts without explicit migration proof. The first five relation tokens occur in 07B.1c's experimental interface-intent contract; the last three are V1 target additions, not implemented assertions.

## Priority and soft preferences

`data-mcel-priority` takes **one** of `primary` (main salience/work), `secondary` (important companion), or `supporting` (contextual). It affects relative emphasis but does not establish hierarchy or a fixed size.

Optional soft preferences, where a real design consumer can demonstrate their effect:

| Field | Closed allowed values | Meaning |
| --- | --- | --- |
| `prefer.side` | `start`, `end`, `top`, `bottom` | Preferred spatial side under current writing direction and available space; may yield to hard constraints |
| `prefer.representation` | `automatic`, `tabular`, `graphical`, `textual` | General representation class, subject to content compatibility and accessibility |
| `prefer.density` | `automatic`, `compact`, `comfortable` | Target information density, not a pixel or zoom constant |

**Target HTML carriers** are `data-mcel-prefer-side`, `data-mcel-prefer-representation`, and `data-mcel-prefer-density`; these are proposed V1 attribute spellings, not validated as currently implemented. `automatic` delegates selection to MCEL. No author-specified CSS grids, pixel sizes, breakpoint thresholds or widget IDs are needed.

## Defaults, inference, and validation

- A `<nav>` element implies navigation when unambiguous; `<main>` implies principal landmark but does **not** by itself assign `priority:primary` to every descendant. `<aside>` suggests support, not a mandatory right-side placement. `<output>` supports result/feedback semantics only as far as actual bindings validate.
- Multiple tasks can coexist; one purpose and at most one principal content binding govern each region. Authors may omit optional hints where MCEL can infer them. Missing facts are *unresolved*, not silently filled with app-name heuristics.
- Explicit semantic instructions must be checked against HTML native semantics and executable JS type/authority facts. Contradictory purpose, invalid token, undefined ref, invalid relationship endpoint, duplicated ID, or unsupported named attribute produces a source-located compile diagnostic.
- MCEL's accepted layout plan must record the consumed semantic evidence and any rejected soft preference, enabling precise audits. Mechanical accessibility/focus/responsiveness are framework-owned and not additional author vocabulary.
- Every admitted term needs a consumer and a falsifiable test: alter/omit it in a four-app semantic specimen and demonstrate appropriate difference in pattern eligibility, selection, ranking, constraint evaluation, diagnostics or acceptance. A tree-shape difference is not universally required.

## Legacy 07B.1c role normalization and compatibility

Legacy `role` is not silently renamed in persisted artifacts. Migrations preserve original role and provenance, and retain previous compiled semantics until explicitly promoted:

| Legacy `role` | Target V1 interpretation, only where source evidence permits |
| --- | --- |
| `main-workarea` | `purpose: workspace`; preserve known explicit primary priority, otherwise leave priority unresolved |
| `auxiliary-workarea` | `purpose: workspace`; supporting if independently established, otherwise unresolved |
| `collection` | `content.kind: collection`, **purpose unspecified** |
| `document` | `content.kind: document`, **purpose unspecified** |
| `inspector` | `purpose: inspection` |
| `commands` | `purpose: commands` |
| `navigation` | `purpose: navigation` |
| `results` | `purpose: results` |
| `status` | `purpose: status` |
| `mode-selector` | `purpose: mode-control` |

`role`-based records in 07B.1c are an existing *experimental implementation*, not a second normative V1 authoring standard. Conversion requires explicit compatibility checks and must not change permission or identity semantics.

## Compatibility and versioning boundary

No `_v1` aliases or application-wide version pins appear in the four V1 reference sources for now. Released V1 vocabulary meaning and accepted design-policy semantics must remain supportable in later MCEL versions; future explicit alias/policy versioning is a separate implementation decision. This documentation patch does **not** add versioned runtime/compiler behavior.

## Qualification consumers

The [Four-App Acceptance](mcel-v1-four-app-acceptance.md) matrix exercises navigation/collection/inspection (File Manager), monitoring/series/commands (Task Manager), document/diagnostics/review (Code Editor), and expression/input/results/mode-control (Calculator). The [Reference Examples](mcel-v1-reference-authoring-examples.md) illustrate canonical region declarations but do not certify their compilation or behavior.
