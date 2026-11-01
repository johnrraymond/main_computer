# MCEL Production ↔ Semantic Application Trace Protocol

## Purpose and scope

The semantic code editor at `/applications/mcel-semantic-code-editor` is **a separate Phase-2 semantic candidate**, not the canonical production Code Editor at `/applications/code-editor`. Prior host API smoke checks never established their UI parity. The screenshot's unmounted/blank editor is not fixed merely by logging it.

This patch registers the same **`mcel-app-parity`** opt-in, local-only diagnostic plugin in the main Applications host and all three bounded semantic candidate hosts. It reuses `MCELDiagnosticPlugins`' existing `enable`/`disable` lifecycle rather than introducing an independent logging backend. The onHover plugin remains unaffected.

## Record a pair

For each app, visit the existing production route and the candidate route. Append `?mcel_trace=1` to each URL (or `&mcel_trace=1` if there are already query arguments). Examples:

- Code Editor: `/applications/code-editor?mcel_trace=1` and `/applications/mcel-semantic-code-editor?mcel_trace=1`.
- File Manager: `/applications/file-explorer?mcel_trace=1` and `/applications/mcel-semantic-file-manager?mcel_trace=1`.
- Task Manager: `/applications/task-manager?mcel_trace=1` and `/applications/mcel-semantic-task-manager?mcel_trace=1`.

If the production route is different in your installation, preserve its actual route and append the same query parameter; do not guess. The recorder is off without `mcel_trace=1`.

Exercise equivalent workflows in both apps, in the same order. Then, in DevTools Console for each tab:

```js
MCELDiagnosticPlugins.list().filter(x => x.id === 'mcel-app-parity')
MCELAppParityTrace.status()
MCELAppParityTrace.download()
```

The last command saves `mcel-parity-<app>-<variant>.json` in your browser's downloads. You can also explicitly start/stop without a query parameter:

```js
await MCELDiagnosticPlugins.enable('mcel-app-parity')
await MCELDiagnosticPlugins.disable('mcel-app-parity')
```

Compare two completed recordings from a repository-root PowerShell:

```powershell
python -m tools.mcel_app_trace_compare `
  "C:\Users\subsi\Downloads\mcel-parity-code-editor-production.json" `
  "C:\Users\subsi\Downloads\mcel-parity-code-editor-semantic.json" `
  --output runtime\reports\mcel-migrations\code-editor-parity.json
```

Repeat with `file-manager` and `task-manager` filenames. The comparison shows which backend operations were observed in both versions, response outcomes, UI state snapshots, and intent disposition counts. **It does not certify parity**: `production-only` may mean the workflow was not exercised in the candidate, or it may be absent. Investigate both possibilities.

### Intended parity exercises

Code Editor: list workspace, select and open source, verify Monaco mounted, edit a disposable document, change tabs, save and close; exercise Aider preview and a reviewed transaction only against a disposable project. Note that the current semantic candidate has fewer document-tab/Aider controls than production.

File Manager: choose root, browse into a nested directory, go back, search, select file, preview it, expand tree. Task Manager: refresh processes and hardware, switch views, inspect connections, observe periodic refresh, then (in an isolated environment only) exercise controlled process and server actions. The current semantic Task Manager declares **read-only** capabilities and cannot be presumed to have lifecycle action parity.

### Privacy and authority

The recorder operates in-page and exports JSON only when called. No recorder-controlled network transmission, persistent storage or remote service is created. It records API pathname (not query string), outcome/status/duration, safe control identifiers, intent lifecycle disposition, boolean presence and bounded counts. It does **not** read response bodies, request bodies, field values, source text, prompts, repository paths, raw PID data, credentials or editor content. It never executes app operations. High-frequency inputs are throttled. The trace is in memory; export before navigating away or initiating a server restart, because a page reload discards the buffer. It wraps `fetch` only while enabled and restores it when disabled if still owned. Destructive actions remain subject to the existing backend and UI controls.

Some startup diagnostics already captured by the existing early collector are not replayed into this plugin when activated late. Browser-side source files, warnings and errors should still be reviewed when comparing a failed mount.

### Verification limits

The patch is an **instrumentation and comparison** patch, not a fix for Monaco, document tabs, Aider workflow parity, File Manager rich tree, Task Manager automatic refresh, or server controls. Their live behavior must be tested and corrected from the resulting evidence. API success is not evidence of UI success.
