# MCEL Code Studio Example

MCEL Code Studio is the flagship example for showing where MCEL can be more useful than a general UI framework.

It is intentionally not a counter app. It is a VS Code-like source workbench whose hard problem is source safety:

```text
author-owned source is canonical
generated editor chrome is runtime-only
dirty runtime drafts do not serialize until committed
broken generated runtime structure can be repaired from source intent
save/export is blocked by contract failures
```

React, Vue, Svelte, and Web Components are better choices when the main problem is ordinary application rendering. MCEL is only better when the user is building a tool where live runtime machinery must not corrupt the saved artifact.

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

The user owns file paths, language labels, required fields, and file contents. MCEL owns the generated workbench around that source.

## What the app must prove

A credible MCEL editor must let the user run this loop:

1. Validate the source contract.
2. Mount generated runtime editor chrome.
3. Damage generated runtime chrome.
4. Repair runtime chrome from the author-owned source.
5. Commit a runtime draft into source.
6. Serialize clean source without generated runtime nodes.

If the app cannot demonstrate that loop, it is not a strong MCEL example.

## What MCEL owns in this app

MCEL owns generated runtime structure:

- tabs
- editor chrome
- runtime draft textarea
- dirty-state badges
- generated diagnostics
- repair metadata
- serialization filtering

Generated runtime nodes are marked with MCEL runtime metadata and are omitted from clean serialization.

## What MCEL does not own

MCEL does not own arbitrary application meaning. It does not claim to be a better React for every app. It should not be used when ordinary static markup or a normal component framework is enough.

The point of Code Studio is narrower: a real editing tool can have rich runtime behavior while preserving a clean source artifact.
