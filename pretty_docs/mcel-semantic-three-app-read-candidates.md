# MCEL semantic workbench read-integration candidates

Status: **Opt-in implementation candidates, not MCEL V1 certification**.

The existing Calculator's accepted expert-presentation candidate provides the
baseline for extending shared semantic rendering to File Manager, Task Manager,
and Code Editor. Each candidate is compiled from paired `application.html` and
`application.js` files using the official DSL and canonical IR; no authored CSS
layout trees, handwritten widget grids, or app-name branches in the shared
runtime or workbench stylesheet.

| Candidate route | Proven capability | Not yet migrated |
| --- | --- | --- |
| `/applications/mcel-semantic-file-manager` | Existing filesystem roots API, root listing, selection and bounded file preview | Directory recursion, search, mutations, file handoff |
| `/applications/mcel-semantic-task-manager` | Existing task overview API, process query, process selection | Other notebook views, continuous monitoring, authorized consequential operations |
| `/applications/mcel-semantic-code-editor` | Existing editor file-list API, selection and source read | Monaco edit, save/transaction authorization, dirty state, undo/redo |

All candidate pages are restricted to local clients by the existing viewport
route guard. The capability adapter is separately allowlisted per application
instance, exact intent/effect/capability/risk, request inputs and previously
observed selection. The adapters invoke only the existing read endpoints and
do not introduce new server-side file/process authority. Operational state,
receipts and browser controls are driven by the existing generic semantic
runtime. Missing operations are not silently stubbed or claimed as available.

The workbench stylesheet uses semantic purposes (navigation, workspace,
inspection, query-controls, status) and content kind (document, collection)
without app names or region identifiers. It is a provisional presentation
implementation, not full expert-design certification. A minor generic runtime
projection exposes existing IR `content.kind` to styling. The real production
app routes remain untouched.

Test with:

```
python -m tools.mcel_semantic_other_apps_frob
```

To check local hosted routes with the viewport server running:

```
python -m tools.mcel_semantic_other_apps_frob --hosted-base http://127.0.0.1:8765
```

The Frob uses Playwright with mocked JSON endpoint responses for deterministic
interaction evidence, including widths 320, 390, 560, 1100 and 1272. A hosted
route check does not by itself establish that its live backend operations work;
that requires separately exercised and reviewed Windows service evidence.

Promotion requires full functional parity and human review per
`pretty_docs/mcel-v1-four-app-acceptance.md`. For now, the expected verdict is
`PASS_READ_ONLY_CANDIDATES_ONLY`; do not interpret it as complete application
parity or MCEL's V1 promise fulfilled.
