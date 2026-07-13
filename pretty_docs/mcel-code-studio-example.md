# MCEL Code Studio Example

MCEL Code Studio is the flagship live example for showing where MCEL can be more useful than a general UI framework.

It is intentionally not a counter app. It is a VS Code-like source workbench whose hard problems are both source safety and live workbench ownership:

```text
author-owned source is canonical
generated editor chrome is runtime-only
dirty runtime drafts do not serialize until committed
owned center space must reach the active editor surface
agent controls must remain operational while proof stays secondary
broken generated runtime structure can be repaired from source intent
save/export is blocked by contract failures
```

React, Vue, Svelte, and Web Components are better choices when the main problem is ordinary application rendering. MCEL is useful here because a live editor, agent workflow, proof system, and dock manager must coexist without corrupting the saved source artifact.

## The example use case

The use case is named:

```text
source-safe-code-editor
```

The source document declares a workspace and source files:

```html
<section
  data-mc-component="code-workspace"
  data-mc-use-case="source-safe-code-editor"
  data-mc-source-id="workspace.main"
>
  <h1 data-mc-field="workspace-title" data-mc-required>MCEL Code Studio</h1>

  <article
    data-mc-component="code-file"
    data-mc-field="active-file"
    data-mc-file-path="src/app.js"
    data-mc-language="javascript"
    data-mc-required
  >export function bootStudio(root) {
  return root;
}</article>
</section>
```

The user owns file paths, language labels, required fields, file contents, and committed drafts. MCEL owns the generated workbench around that source.

## The live workbench

The current application is authored as a dock tree:

```text
code-editor.workbench
├── titlebar                  command chrome
├── activity rail             fixed navigation chrome
├── explorer                  source navigation
│   ├── open editors
│   └── workspace tree
├── editor                    required primary work
├── Aider Control Surface     operational agent companion
│   ├── repository context
│   │   └── Aider repository file map
│   ├── selected context
│   ├── instruction
│   ├── preview / run / dry run / cancel
│   ├── session state
│   └── compact live output
├── Evidence and History      bottom proof dock
│   ├── SCM receipts
│   ├── effects / runtime / repair diagnostics
│   ├── Aider history
│   ├── documentation
│   └── logs
└── statusbar                 persistent status
```

The semantic relationship is:

```text
Explorer → navigates source
Aider file map → selects agent context
Editor → owns primary work
Aider Control → acts on selected work
Evidence and History → records and proves what happened
```

The Repo file map is part of the Aider Control Surface. It is not ordinary editor navigation, and it should not be duplicated in the Explorer.

## Live contract files

The implementation is split across:

```text
main_computer/web/applications/apps/code-editor.html
main_computer/web/applications/scripts/code-editor-layout-contract.js
main_computer/web/applications/scripts/code-editor-mcel-studio.js
main_computer/web/applications/styles/code-editor.css
main_computer/flog_code_editor_live_smoke.py
```

`code-editor.html` owns the concrete regions and stable identities.

`code-editor-layout-contract.js` owns:

- legal placements;
- preferred and fallback placements;
- minimum useful dimensions;
- semantic user operations;
- responsive remediation;
- owned-track fill;
- generated runtime containment;
- preference persistence and restoration.

The live module is exposed as:

```js
window.MainComputerCodeEditorLayout
window.MainComputerCodeEditorLayoutController
```

This is an application-local MCEL contract. It is not yet a generic `window.MCEL` layout guarantee.

## Authored layout hints

The editor is required center work:

```html
<section
  data-mc-layout-user-id="code-editor.editor"
  data-mc-layout-prefer="center"
  data-mc-layout-allowed="center split-center"
  data-mc-layout-strength="required"
  data-mc-layout-fill="owned-center-slot"
  data-mc-layout-overflow="contain"
>
```

The Aider surface is a movable companion:

```html
<aside
  data-mc-layout-user-id="code-editor.inspector"
  data-mc-layout-prefer="right"
  data-mc-layout-allowed="right bottom tab trigger"
  data-mc-layout-fallback="bottom tab trigger"
  data-mc-layout-user-mutable="placement share collapsed tab-group"
  data-mc-controls="code-editor.studio.editor-group"
>
```

The proof dock is secondary evidence:

```html
<section
  data-mc-layout-user-id="code-editor.proof"
  data-mc-layout-prefer="bottom"
  data-mc-layout-allowed="bottom trigger"
  data-mc-proves="editor.operation"
  data-mc-records="aider.operation"
>
```

## Owned center and generated containment

The center placement is not enough by itself. MCEL verifies this chain:

```text
owned center slot
→ editor group
→ active pane
→ primary editor surface
```

The live generated contract is named:

```text
mcel-owned-track-containment.v1
```

It emits runtime-only `data-mcel-layout-*` traits for the runtime preview, Monaco host, fallback editor, draft, and badges. These traits make descendants fill, shrink, scroll internally, and contain their paint inside the owned remaining track.

Generated `data-mcel-layout-*` attributes are runtime output. They must not appear in clean serialization.

## User layout operations

Code Studio supports semantic operations:

```text
dock
resize-share
tab-with
collapse
undo
reset
```

Preferences use stable IDs such as:

```text
code-editor.explorer
code-editor.inspector
code-editor.proof
```

Raw `left`, `top`, `width`, and `height` coordinates are rejected. A preferred right-side Aider surface may temporarily remediate to bottom, tab, or trigger when space is constrained, then restore when sufficient capacity returns.

## One scroll owner per region

The workbench intentionally distinguishes independent scrolling regions:

- the workspace tree may scroll;
- the editor owns its internal scroll;
- the Aider repository tree may scroll;
- live agent output may scroll;
- the proof dock may scroll when open.

Ordinary cards inside those regions should not each create another vertical scrollbar. The contract should preserve one intentional owner for each recursive workspace.

## What the app must prove

A credible MCEL editor must let the user run this loop:

1. Validate the source contract.
2. Mount generated runtime editor chrome.
3. Damage generated runtime chrome.
4. Repair runtime chrome from the author-owned source.
5. Commit a runtime draft into source.
6. Serialize clean source without generated runtime nodes.

In addition, the live workbench must prove:

1. The editor fills its owned center slot in both axes.
2. The active primary surface fills the editor group.
3. Runtime descendants remain contained in their owned tracks.
4. No foreign chrome intercepts active controls.
5. Opening or collapsing the proof dock returns the expected space.
6. Aider controls retain their existing IDs, backend bindings, selected files, and session state.
7. User preferences remediate and restore without raw geometry.
8. The source remains clean after runtime layout changes.

If the app cannot demonstrate that loop, it is not a strong MCEL example.

## What MCEL owns in this app

MCEL owns generated runtime structure:

- dock realization;
- responsive remediation;
- tabs and editor chrome;
- runtime draft textarea;
- dirty-state badges;
- generated diagnostics;
- containment traits;
- repair metadata;
- serialization filtering.

Generated runtime nodes are marked with MCEL runtime metadata and are omitted from clean serialization.

## What MCEL does not own

MCEL does not own arbitrary application meaning. It does not claim to be a better React for every app. It should not be used when ordinary static markup or a normal component framework is enough.

MCEL also does not own the Aider backend implementation. The existing repository picker, instruction, run actions, output handlers, and session APIs remain application behavior. The layout contract preserves and composes them; it must not clone or silently replace them.

The point of Code Studio is narrower: a real editing and agent-control tool can have rich runtime behavior while preserving clean source, stable application hooks, and a verified workbench hierarchy.
