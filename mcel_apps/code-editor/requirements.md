# Code Editor Host-Bound DSL Authority

This package is the authored MCEL semantic authority for Code Editor.

The durable presentation remains the existing host surface:

```text
main_computer/web/applications/apps/code-editor.html
main_computer/web/applications/styles/code-editor.css
```

The stable route/root/facade are:

```text
/applications/code-editor
#code-editor-app
window.MainComputerCodeEditorRuntime
```

Generated contracts, normalized IR, runtime manifests, browser package entries,
and ownership records are reconstructed from this package and compact
projection code. They are not checked into `mcel_apps/code-editor`.

Patch 1 established DSL/package authority. Patch 2 added the direct canonical
`MainComputerCodeEditorRuntime` implementation for the inspect/open/edit/save lane.
Patch 3 moves the reviewed patch lane into that runtime: preview prepares a
server-issued transaction handle without writing, and apply requires
reviewed/approved evidence before any source mutation. Patch 4 introduced a
runtime-owned UI. Patches 5-7 made Monaco available and restored the direct
Monaco host contract. Patch 8 preserves the authored VS Code-style host chrome
while letting `MainComputerCodeEditorRuntime` own the selected-file Monaco
authoring surface and reviewed mutation receipts. Patch 10 fixes the hidden-pane
regression by forcing the legacy runtime pane to be the active center workbench
surface before Monaco mounts or swaps file models, and by keeping adapter debug
globals aligned with the active Monaco model.

```mcel-app
id: code-editor
title: Code Editor
status: semantic-runtime-proven
current_runtime_status: dsl-authoritative-host-bound
target_runtime_status: semantic-runtime-proven
dominant_object: Repository source workspace and reviewed source edits
primary_user_goal: Inspect, edit, save, preview, and apply reviewed source changes through explicit Code Editor operation boundaries.
current_sources:
  - application.js
  - mcel.app.json
  - blueprint.json
  - requirements.md
  - main_computer/web/applications/apps/code-editor.html
  - main_computer/web/applications/styles/code-editor.css
  - main_computer/web/applications/scripts/code-editor-core.js
  - main_computer/web/applications/scripts/code-editor-view-model.js
  - main_computer/web/applications/scripts/code-editor-capabilities.js
  - main_computer/web/applications/scripts/code-editor.js
  - main_computer/web/applications/scripts/code-editor-monaco-adapter.js
  - main_computer/web/applications/scripts/code-editor-semantic-adapter.js (retired legacy marker, not host-loaded)
verification:
  - DSL compilation
  - deterministic contract projection
  - package-local acceptance
  - host-bound virtual runtime mount
  - package catalog enrollment
```

```mcel-use-case
id: code-editor.use-case.source-authoring
app: code-editor
status: verified
type: primary
primary_object: Repository source file
user_goal: Safely inspect, edit, and save author-owned source files while preserving explicit mutation receipts.
acceptance: Workspace inspection and source writes are separate declared intents; local draft edits do not become repository writes.
```

```mcel-region
id: code-editor.region.workspace
app: code-editor
status: verified
region: Code Editor workspace
role: primary
responsibility: Preserve the existing Code Editor HTML, route, root selector, controls, and source-authoring regions while MCEL semantics are compiled and projected in memory.
```

```mcel-requirement
id: code-editor.requirement.stable-host
app: code-editor
status: verified
type: architecture
aspect: source
object: Code Editor presentation
requirement: The existing HTML and CSS remain presentation authority while MCEL semantics are compiled, projected in memory, and mounted through the canonical runtime facade.
acceptance: No Code Editor HTML, CSS, generated contracts, or normalized IR snapshot is copied into the authored package.
```

```mcel-requirement
id: code-editor.requirement.reviewed-source-mutation
app: code-editor
status: verified
type: safety
aspect: actions
object: Repository source mutation
requirement: Source writes require explicit user intent, stale-source checks, and operation receipts; patch application additionally requires reviewed patch evidence and recovery information.
acceptance: Preview operations are read-only, save operations require explicit save evidence, and apply operations require reviewed patch evidence.
```

```mcel-intent
id: code-editor.intent.runtime-facade
app: code-editor
status: verified
intent: eight stable Code Editor operations
risk: mixed
requires:
  - MainComputerCodeEditorRuntime
  - explicit local, source-workspace, Aider planning, or reviewed-patch lane
produces:
  - one classified operation result
  - no claimed canonical write in host-bound IR
```


```mcel-requirement
id: code-editor.requirement.monaco-primary-editor
app: code-editor
status: verified
type: presentation
aspect: editor
object: Code Editor source authoring surface
requirement: The runtime-owned Code Editor surface uses Monaco as the primary source editor when the Monaco loader is available, and keeps a textarea fallback for blocked or unavailable Monaco loading.
acceptance: The canonical runtime mounts #code-studio-monaco-host through MainComputerMonacoAdapter, synchronizes model changes through editDraft, and still supports save/discard/close through MainComputerCodeEditorRuntime.
```


```mcel-requirement
id: code-editor.requirement.permanent-monaco-host
app: code-editor
status: verified
type: presentation
aspect: editor
object: Code Editor center editor pane
requirement: The DSL-native Code Editor keeps a permanent Monaco host in the center editor pane, including a read-only placeholder model when no source file is open.
acceptance: The canonical runtime mounts Monaco into #code-studio-monaco-host without overlay twiddles, does not dispose the editor merely because no active file exists, and switches the Monaco model between placeholder and active source file state.
```

```mcel-acceptance
id: code-editor.acceptance.dsl-authority
app: code-editor
status: verified
requires:
  - application.js compiles to valid canonical IR
  - exactly eight stable runtime intents are declared
  - source workspace, Aider planning, and reviewed patch application are explicit capabilities
  - /applications/code-editor and #code-editor-app remain the host interfaces
  - MainComputerCodeEditorRuntime is the canonical runtime facade
  - generated projection is deterministic
  - package catalog exposes one host-bound Code Editor record
```

```mcel-finding
id: code-editor.finding.dsl-authority-established
app: code-editor
status: open
aspect: implementation
severity: info
problem: The former Code Editor semantic adapter remains legacy scaffolding while the DSL authority is enrolled.
desired_behavior: Keep Code Editor DSL-authoritative, host-bound, package-enrolled, and free of checked-in generated artifacts under mcel_apps/code-editor.
```

Patch 7 restored the old Monaco host contract inside the DSL-native surface:
`#code-studio-runtime-monaco` is again the actual Monaco adapter host, not a
wrapper around a second nested host. The runtime also isolates Monaco's generated
DOM from app-wide layout and `box-sizing` rules so the mounted editor can paint
inside the center pane.



## Legacy-fidelity source workspace opening

The DSL-native runtime must preserve the authored `#code-studio-source-editor` source workspace as immutable input for the preserved Code Studio chrome. Runtime draft rendering must not overwrite that source textarea. Clicking legacy `data-code-studio-file` entries must be captured by `MainComputerCodeEditorRuntime`, opened from the authored source workspace when available, and must not fall through to the retired Code Studio mount gate.

## Legacy-fidelity editor track sizing

When the DSL runtime preserves the old Code Studio chrome, the selected-file Monaco editor must occupy a visible center workbench track. The runtime may keep `data-code-editor-mode="authoring"` for older proof tooling, but the legacy-fidelity surface must provide matching four-column grid areas (`activitybar sidebar editor inspector`) so the `code-studio-editor-group` cannot be auto-placed into a zero-width implicit grid track. The runtime pane and `#code-studio-runtime-monaco` host must remain nonzero-sized after selecting `src/app.js`.


- Legacy-fidelity runtime mode must not inherit the old simplified `data-code-editor-mode="authoring"` workbench grid; the preserved Code Studio shell owns activity, explorer, editor, and inspector tracks directly, with the inspector hidden on narrow viewports so Monaco keeps the primary lane.

- Legacy-fidelity shell children must be pinned to a single explicit shell column (`titlebar`, `workbench`, `proof`, `statusbar`). Browser zoom or viewport resize must not allow `.code-studio-shell` to re-resolve into implicit zero-width columns such as `0px 0px <remaining>`, because that collapses `.code-studio-body`, `.code-studio-editor-group`, and the Monaco host even when the app surface itself is wider.

## Legacy-fidelity diagnostics contract

The Code Editor diagnosis contract remains the authoring Monaco golden path, but the preserved legacy-fidelity shell is an allowed runtime alias for that authoring path. Diagnostics must not flag `data-code-editor-mode="legacy-fidelity"` as a mode mismatch when the selected-file Monaco surface is owned by `#code-studio-runtime-monaco`.

The primary Monaco surface has a full-size minimum of 360×320 CSS pixels and a compact viewport minimum height of 240 CSS pixels when the available app viewport is at or below 720 CSS pixels tall. This keeps the contract sensitive to true collapsed/zero-height editors while allowing browser zoom and shorter windows that still render a usable selected-file editor.

The activity rail's normal assistant button (`[data-code-studio-panel="assistant"]` inside `.code-studio-activitybar`) is an owned navigation control, not a diagnostic overlay. Overlay detection must ignore that small inline control while still reporting floating or fixed diagnostic/assistant surfaces.

- Patch 16: Default legacy-fidelity authoring hides the MCEL proof/evidence dock completely so diagnostics do not report the compact proof tab strip as a visible forbidden region; MCEL tools mode can still reveal diagnostic proof surfaces intentionally.


## Browser smoke contract

The Code Editor runtime must expose a browser-side smoke probe for the legacy-fidelity selected-file editor. The probe is non-mutating: it records the current live DOM state after refresh, resize, or browser zoom and reports pass/fail checks without applying layout overrides.

The smoke contract must verify that legacy-fidelity mode remains active, the shell grid has one non-zero explicit column, the workbench grid has no zero-width visible tracks, exactly one runtime pane is active, the Monaco primary host and editor are visible above compact smoke minimums, the proof dock is hidden by default, and the MCEL diagnosis raw verdict has no critical/error findings when the diagnosis API is available.

The probe is available in the browser as `MainComputerCodeEditorBrowserSmoke.run()` and `MainComputerCodeEditorBrowserSmoke.startResizeWatch()`.
