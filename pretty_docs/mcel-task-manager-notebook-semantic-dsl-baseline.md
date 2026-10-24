# Task Manager notebook — semantic contract and canonical DSL v1

**Status:** experimental authoring baseline; *compiled DSL, not a generated application or a certified runtime.* This document follows `mcel-authoring-principles.md` and `mcel-canonical-app-authoring.md`.

## Purpose and boundaries

Task Manager's **existing tabbed data notebook** is the first development subject for MCEL's four authoring priorities. We are *not* inventing a separate Task Board, porting the entire Task Manager, or using MCEL Lab as the application runtime. The present UI is the behavioral reference; its legacy event handlers and styles are **not** semantic authority for the new implementation.

Canonical candidate DSL: `mcel_experiments/task-manager-notebook/application.js`. It is deliberately **outside `mcel_apps/`**. This prevents incomplete authoring from being discovered as a package, registered as an app, projected into the current Task Manager, or admitted as application proof. It uses the existing, supported `mcel.defineApp` / `application.hostBound` entry point, but no code from this experiment runs in the live application.

## Step 1 — The semantics

| Concern | Required meaning | Not the meaning |
| --- | --- | --- |
| Tab group | Exactly one selected tab; four stable identities | Four unrelated command buttons |
| Tab/panel relationship | Each tab has one uniquely associated panel and accessible label/control linkage | Hand-maintained element IDs as application business logic |
| Selection | Changes the active view, visible panel, ARIA selection, and applicable route state; keeps other panel data | Starting/stopping a server, process, schedule, or AI request |
| Notebook data | Read-only host-supplied snapshots of server processes, all processes, connections, and hardware | Ownership of machine operations or mutation permission |
| Inactive panel | Hidden from view but retains its observed data and scroll/state ownership | Clearing the data or destroying the data source |
| Missing/invalid selection | Refuse or normalize according to a declared policy, without operational effects | An arbitrary tab silently controlling an unrelated panel |
| Data status | Not requested / loading / available / empty / failed is meaningful view state | A fabricated successful load |
| Presentation | Readable tab controls and scroll-contained tables that adapt to available space; keyboard/accessibility obligations | Application-specific CSS and event plumbing dictated by author |

### Current notebook identities

| Stable tab ID | Label | Existing observed tab | Existing observed panel | Read-only source |
| --- | --- | --- | --- | --- |
| `server-processes` | Server Processes | `#task-tab-processes` | `#task-panel-processes` | `processes` |
| `all-processes` | All Processes | `#task-tab-all-processes` | `#task-panel-all-processes` | `all_processes` |
| `connections` | Connections | `#task-tab-connections` | `#task-panel-connections` | `connections` |
| `hardware` | CPU + GPU | `#task-tab-hardware` | `#task-panel-hardware` | `hardware` |

The selectors above are **temporary observation anchors**, not a claim that authors should hand-maintain DOM wiring. The candidate DSL gives each item a semantic identity and declares its relationship. The existing `task-manager-mcel.js` tabbed-workspace contract supplies further historical context but is not a second authored source of truth for the candidate.

## Step 2 — Official DSL v1

The candidate expresses a host-bound surface, four tab/panel relationships, renderer-local selected-tab state, derived data/status, a local selection intent, two invariants, layout semantics, and positive/negative scenario declarations. The source is not copied HTML, CSS, or the old `setTaskNotebookTab` implementation.

**Compile (read-only):**

```text
python tools/mcel_dsl_compile.py --input mcel_experiments/task-manager-notebook/application.js --json
```

**Check the semantic baseline:**

```text
python -m pytest -q tests/test_mcel_task_manager_notebook_dsl.py
```

**Measured first baseline:** 121 lines of experimental DSL source for one four-tab read-only notebook, including repeated semantic selectors, control mappings, and non-executable proof declarations. This is **not yet minimal authoring effort**. The exact line count is only a starting measurement; it includes comments and repetition and is not a claim about user effort. Removing author obligations for ID linkage, state wiring, ARIA, and presentational CSS is the planned library work.

The compiler's `pass` means the declared graph is accepted as **valid Application IR**, not that the notebook has been generated, rendered, or runtime-proven. The `semanticRuntimeProven` proof target in the DSL is a target contract only.

## What the compiler supports, and what prevents retirement

| Capability | Phase-1 result | Required MCEL work |
| --- | --- | --- |
| Host-bound source and root identity | Compiles | Later replace the host-owned presentation authority with generated MCEL structure |
| Four semantic tab/panel relationships | Preserved as `semanticSurface.tabGroup` metadata | Add **general** tab/panel composition validation and accessible binding generation; metadata alone does not enforce relationships |
| Selected-tab state | `rendererLocal` state compiles | Define a typed selection input and constrained state transition through the public DSL |
| Tab activation | An intent is declared, but **has no input, write transition, or executable binding** | Generate selected state, panel visibility, ARIA updates, route synchronization, and keyboard behavior; do not reuse Task Manager event logic as a special case |
| Read-only datasets | State/source identities declared | Add reusable snapshot/data view binding and loading/empty/error semantics when needed |
| Responsive notebook/table composition | Layout grammar compiles as data | Generate layout/default styles and verify actual overflow, tab wrapping, and keyboard focus in a browser |
| Composition correctness | Local baseline test checks present HTML and catches injected broken links | The bad DSL with duplicate panel ownership still compiles today. Move this negative control into the **general** MCEL compiler/composition law; no successful runtime claim before that |
| Isolation from operational authority | No capabilities or effects in the compiled notebook IR | Prove selection cannot invoke PID, server, schedule, or AI operations when generated runtime exists |
| Appearance by default | Not demonstrated | Evaluate actual generated first render, then improve shared presentation rules rather than add notebook CSS |

**First concrete blocker discovered while writing the DSL:** declaring `writes: ["active-tab-id"]` for the host-bound selection intent is rejected by `MCEL_IR_INTENT_WRITE_SET_MISMATCH`. The current builder does not provide the constrained selection transition needed to authorize that write. The DSL therefore intentionally declares **no write**, rather than producing an invalid or falsely capable IR. This is the first general capability to implement in the next patch.

## Retirement gate (not part of this patch)

Retire existing tab markup/event plumbing/overlapping MCEL annotations **only after** MCEL can generate the tabbed workspace using this semantic contract, with genuine state changes, visual quality, accessibility, route parity, read-only snapshot behavior, and forbidden-effect controls proven by independent checks. Retain backend telemetry and permitted operator actions. Compare semantics and user outcomes, not the original DOM's incidental structure. No direct change to Task Manager, Website Builder, or MCEL Lab is authorized by this baseline.

The development measure is whether **MCEL makes the correct composition the easy option**, not whether another set of annotations can be layered onto the legacy UI.

## Phase 2 — Typed view transition and enforceable tab composition

**Status (phase-2 candidate):** The DSL now declares a bounded `selectedTabId` string input, with an enum of the four stable tab identities, and a renderer-local `viewTransition` targeting `active-tab-id`. This is deliberately distinct from `intent.transition`/canonical mutation and never grants PID, server, scheduler, AI, filesystem or other capability authority. Unknown selections must be refused without modifying state. The reusable pure `McelViewTransition.apply` implementation returns a new local-state snapshot; it has no DOM or external effects.

The shared IR compiler now enforces `semanticSurface.tabGroup` composition generically: unique tab IDs, tab selectors and panel selectors; an existing default member; selected renderer-local state and matching choices; one-to-one controls/regions and the controlling selection intent; and standard accessible tab roles and keyboard contract. Duplicate ownership and broken declarations produce `MCEL_IR_TAB_GROUP_COMPOSITION_INVALID`. Untyped or unauthorized view transitions produce `MCEL_IR_VIEW_TRANSITION_INVALID`. These obligations are not special-cased to the Task Manager app ID.

**Still missing:** A generated tab/panel renderer that consumes these semantics, controls visibility and ARIA, handles keyboard events and route synchronization, and supplies attractive responsive defaults. No live Task Manager change, host binding, generated UI, or replacement certification is claimed. The old event handling remains authoritative until that independent generated replacement is working and proven. The phase-one gap table above is retained as the original baseline, rather than retroactively portraying phase-one as capable of these behaviors.

Read-only verification:

```text
python tools/mcel_dsl_compile.py --input mcel_experiments/task-manager-notebook/application.js --json
python -m pytest -q tests/test_mcel_task_manager_notebook_dsl.py tests/test_mcel_view_transition_composition.py
```

## Phase 3 — Generated tab workspace preview

**Status:** Standalone, generated *experimental preview* implemented using the same canonical Phase-2 DSL, shared MCEL renderer, pure typed transition executor, and one reusable CSS policy. **Not mounted into production Task Manager, not packaged as an application, and not certified as a replacement.** Original Task Manager HTML/JS/CSS remain authoritative.

### Build and inspect

Generate a disposable notebook from the public DSL with no live data:

```text
python tools/mcel_notebook_preview.py
```

Or generate it with **explicit synthetic, read-only sample data**, which must never be presented as real machine telemetry:

```text
python tools/mcel_notebook_preview.py --fixture mcel_experiments/task-manager-notebook/read-only-sample.json
```

Open `runtime/reports/mcel-experiments/task-manager-notebook/generated-notebook.html` in a browser. `--check` verifies that the source compiles and projects without writing a preview file. Generated output includes its compiled IR, the shared `mcel-view-transition.js`, the shared `mcel-tab-workspace.js`, and `mcel-tab-workspace.css`. It is self-contained and network-blocked by its preview CSP.

### What now works through the library

- The tab buttons and their uniquely associated panels are created from `semanticSurface.tabGroup`, not copied from legacy HTML. The source selectors remain *observation anchors*, not required DOM ids in the new view.
- The selected tab is maintained through `McelViewTransition.apply`, including typed input validation, effect-free state publication, single visible panel, roving `tabIndex`, accessible roles/labels, keyboard arrows/Home/End, and optional view-only URL-fragment restoration.
- A generic read-only table renderer consumes declared `dataSource` identities and separate snapshots. It supplies empty, loading, failed, and no-request notices without issuing any host requests; preview samples are explicitly synthetic.
- Shared default visual rules provide hierarchy, focus indication, selected-state contrast, wrapping controls, and scroll-contained tables at narrow and wide widths. Authors can override CSS variables; the semantics do not fix an application's aesthetic.
- Invalid input, malformed tab relationships, canonical writes, declared effects/capabilities, and invalid snapshot shapes are refused. The generated preview cannot call Task Manager operational endpoints.

**Measured facts:** The Phase-3 browser test exercises the generated HTML at 360, 768, and 1280 pixels, including keyboard behavior and no network requests. The rendering is a real candidate demonstration of MCEL composition, **not** proof of a finished visual design or full Task Manager behavior.

### Still blocking retirement

- The real Task Manager has not yet been mounted from generated source; the old UI remains untouched. We have not demonstrated behavioral parity with *live*, continuously changing telemetry or the full original view contracts.
- The read-only data table renderer is deliberately generic and bounded. Rich hardware graphs, table sorting/filtering, virtualization, asynchronous provider lifecycles, and accessible large-data patterns require additional shared capabilities, rather than per-Task Manager hacks.
- The preview's compact CSS is a candidate shared visual system; further visual inspection and accessible design review are needed. A successful browser test is not itself an attractiveness guarantee.
- This preview generator is not a general arbitrary-application renderer. It supports one validated, consequence-free tab workspace, deliberately refusing broader app/effect schemas. We should extend MCEL through general composition primitives when real new requirements appear.
- No existing MCEL promise, host-bound certificate, or canonical release verdict is promoted by this experiment. Retirement requires independent hosted comparison, source and effect safety proofs, explicit promotion/rollback, and preservation of existing Task Manager behavior.

**Development rule:** Increase supported generic composition and remove handwritten app logic *only after* the generated version proves it can replace that logic. Do not wrap existing Task Manager methods and call that DSL generation.
