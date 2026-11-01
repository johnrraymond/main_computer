# MCEL Semantic Code Editor: file selection to Monaco mount

## Evidence and failure

The paired opt-in `mcel.app-parity-trace.v1` traces showed the production editor finishing with one document and a Monaco DOM editor, while the semantic candidate's final state had `documentSelected: true`, `documentOpen: false`, `tabs: 0`, `monacoHostPresent: true`, and `monacoPresent: false`. The candidate dispatched `intent:select-file` repeatedly but did not dispatch `intent:open-source` or request `/api/applications/editor/read`. Consequently its existing `document-editor` runtime never called `MainComputerMonacoAdapter.mount`.

The production trace separately reported a 404 on `/applications/vendor/monaco-editor/min/vs/loader.js` before observing Monaco present. There is no vendored Monaco distribution in this repository snapshot; the shared adapter attempts the local URL and then its existing CDN fallback. This patch does **not** claim to correct the 404 or certify offline loading.

## Implemented repair

The generic semantic operational renderer now links a `collectionSelection.target` state to the declared `document-editor.path` state when they are the **same state reference**. Selecting a file emits a normal `selected` receipt and dispatches the document's explicitly declared `loadIntent`. The existing capability port authorizes the path from a prior server-backed file listing, reads the source, performs ordinary schema checks, and updates source/draft state; the existing Monaco adapter then mounts the model. The button is labeled `Open` for document-bound collections. Other collection selections, including File Manager and Task Manager, retain their previous behavior.

The renderer emits content-free parity events for `editor.monaco.load` and `editor.monaco.mount` and reports loading, success, or failure in the document status area. It defends against a stale asynchronous Monaco mount installing the wrong document after a newer selection. Saving remains explicitly confirmed and guarded by the original stale-source verification. A dirty document still blocks replacing the selection until saved/discarded.

## Browser acceptance

1. Start Main Computer and open `http://127.0.0.1:8765/applications/mcel-semantic-code-editor?mcel_trace=1`.
2. Select **List Files**, then click **Open** next to one source file. Do not click the separate **Open Source** button.
3. Confirm syntax-highlighted Monaco UI appears in **Source Document**, and the document name replaces `No document opened`.
4. In browser devtools, run `MCELAppParityTrace.exportTrace()` or `MCELAppParityTrace.download()`.
5. Confirm the trace contains `intent:open-source` (dispatch and completed), a `/api/applications/editor/read` backend request and successful response, and `editor.monaco.mount` with disposition `mounted`. The final state should report `documentOpen: true` and `monacoPresent: true`.
6. If it reports `Monaco unavailable`, inspect the trace's `editor.monaco.load` disposition and browser resource errors; investigate local loader 404, CDN accessibility, and worker CSP separately.
7. On a disposable file, verify unsaved edits block selecting another document, that discard works, and that an explicitly confirmed save produces a validated server receipt.

## Verification scope

Playwright tests exercise one-click read and visible Monaco DOM (mock runtime), the shipped adapter with a simulated Monaco API, dirty-source safety, a delayed mount superseded by a newer file, and the surrounding semantic apps. This is not a real Monaco distribution/network test and is not complete editor parity. The semantic candidate still lacks production-equivalent multi-document tabs and complete Aider workflow; those remain separate migration gates.
