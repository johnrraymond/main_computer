# MCEL semantic reference-app parity — 2026-10-31 snapshot

Status: **Editor candidate work surface implemented; production parity not achieved.** This is an implementation audit, not an amendment to the authoritative MCEL V1 product contract. Production routes are unchanged. Website Builder and MCEL Lab are excluded.

## Source-grounded findings

| Application | Existing behavior in snapshot | Semantic candidate at this patch | Important remaining gap |
|---|---|---|---|
| File Manager | Root and nested-directory traversal, parent/up navigation, search, rich Wunderbaum tree, selected entry preview. `file-explorer.js`, `file-explorer.html` | Root browsing, selection, read-only preview | Nested and parent navigation, search, rich tree, current-path visibility. File Manager's original backend is primarily **read-only**; do not invent file-write permission. |
| Task Manager | Four real notebook views (server processes, all processes, connections, CPU/GPU), filter/limits/connection toggle, auto refresh, process terminate/kill, server start/stop/restart, scheduling and AI actions. `task-manager.html`, `task-manager.js`, task action/schedule/AI endpoints | Process observation, query and selection | Everything beyond the observed process list. Candidate intentionally has **no** consequential authority; original guarded process controls must be recovered through the real action policy, never recreated by guessed PID-only identity. |
| Code Editor | Monaco, source inspection/load/edit/save, dirty/discard/close, nested file explorer, Aider preview and reviewed-patch application. `mcel_apps/code-editor/application.js`, `code-editor-monaco-adapter.js`, `code-editor-capabilities.js` | Real Monaco adapter mounted through the shared semantic `document-editor` node; typed selected-path/source/draft; directory browsing; explicit save port using the existing backend stale-source hash and user confirmation | Complete file navigation, multi-document lifecycle, Aider preview, reviewed-patch authority and full hosted-source-write verification remain outstanding. |

## Editor architecture and security

- `application.html` defines the document workarea, using `data-mcel-tasks="edit"` and `data-mcel-content-kind="document"`; `application.js` uses `dsl.surface.documentEditor` to bind the existing source, path, draft and admitted intents.
- The shared operational runtime selects the `document-editor` component by **node kind**. Its presentation uses generic semantic styling, not application-ID-specific layout.
- Monaco is provided by the pre-existing `code-editor-monaco-adapter.js`; local Monaco vendor assets are preferred and the existing adapter permits its established CDN fallback. The candidate does not embed a second editor implementation.
- `open-source` must succeed for a listed file before an editable model is mounted. Source responses with truncation markers or replacement characters are read-only and cannot be saved.
- Editing changes renderer-local draft state; switching to another selected file is refused while the draft is dirty. Discard is local. Save is an explicit, confirmed `source-write` capability request.
- The dedicated capability port verifies exact app/intent/effect/capability/risk binding, opened path, local response provenance and SHA-256 of read content. The existing server endpoint verifies stale source and applies its own author-owned-path and transaction controls. The adapter **does not** itself grant server write authority.
- Tests use mocked backend responses and a controlled Monaco adapter; real Windows Monaco loading and a live server write are **not certified**. The full-production Code Editor remains unchanged.

## Next implementation gates

1. Test the semantic Code Editor on the running Windows viewport: select a real file, open it in real Monaco, edit, save, reopen, verify bytes and receipt; test stale edits and source-write refusal. Never promote on mocked-port evidence alone.
2. Recover Code Editor full editor/document state and Aider/reviewed-patch workflows without special-casing layout.
3. Recover File Manager navigation, search and directory context; match actual read-only capabilities of the original.
4. Recover Task Manager's four notebook perspectives, refresh lifecycle, schedules/AI functions and governed consequential actions, using actual process identity and existing safety checks.
5. Run real functional parity, responsive/identity/focus tests and human design acceptance for each app before promoting an opt-in candidate.

Run `python -m tools.mcel_semantic_reference_parity_frob` for the machine-readable comparison in `runtime/reports/mcel-semantic-reference-parity/report.json`. The report explicitly marks each application `INCOMPLETE`.
