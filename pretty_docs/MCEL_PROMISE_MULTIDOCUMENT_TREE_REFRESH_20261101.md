# MCEL Promise: document tabs, hierarchical navigation, and live monitoring

**Patch:** `mcel_promise_tabs_tree_monitor_20261101.zip`  
**Source base:** `main_computer_test-20261031-183933.zip`  
**Status:** implementation + browser-contract tests; live Windows/browser production parity is not certified.

## Objective

Continue MCEL's paired semantic application migration without replacing DSL source with per-app HTML, bypassing existing capability ports, or treating backend HTTP success as UI parity. These changes live in the generic semantic operational runtime and its scoped port, plus generic styling, diagnostics, and regression tests.

## What changed

### Code Editor — document lifecycle

- A document-editor surface generates a tab strip for multiple open sources; each tab has a distinct loaded baseline, dirty draft, editability, and conflict state.
- Switching to a previously opened tab re-reads the source through the already-declared `open-source` capability, then restores its local draft. A source changed since a dirty draft's baseline becomes a *conflict*: the draft is shown read-only and saving is blocked. Discarding a conflicted draft re-reads the current source, rather than silently overwriting the baseline.
- Save writes are explicitly confirmed, and tab switches/closing are blocked during an in-flight save. Independent dirty tabs require confirmation to close. The last tab can be closed without leaving write authorization.
- Previously opened paths can be reopened after changing directories. New paths still require membership in a successfully loaded directory listing. Existing backend stale-source SHA-256 and receipt verification remain authoritative.
- The parity plugin now reports the semantic tab count and dirty-tab count, and the runtime emits redacted tab-switch, close, and conflict events.

### File Manager — lazy hierarchy

- The generated browse collection retains successfully visited folder listings as nested branches, including ancestors and previously expanded descendants. Branch open calls the declared `enter-directory` capability, not a new direct endpoint.
- Search results are displayed as selectable results without destroying the cached tree. File selection in a previously visited but inactive folder requires reopening that folder before preview, preserving the host port's listing-membership rule.
- Directory traversal and unobserved-directory refusal are unchanged.

### Task Manager — read-only automatic monitoring

- The renderer detects a single read-only capability whose result is bound to the currently visible semantic `monitor` region and polls it every five seconds while the page is visible.
- A pause/resume control appears. One request is allowed in flight at a time; unmount removes the timer. User-entered monitoring queries are retained. It does **not** automatically invoke scheduling, AI, termination, or server-control operations.
- Operation receipts and trace events distinguish manual data retrieval from automatic refresh.

## Regressions and invariants

Run from the repository root:

```powershell
python -m pytest -q tests/test_mcel_semantic_editor_migration.py
python -m pytest -q tests/test_mcel_semantic_parity_recovery.py
python -m pytest -q tests/test_mcel_semantic_other_apps.py tests/test_mcel_calculator_expert_presentation.py tests/test_mcel_semantic_runtime_integration.py tests/test_mcel_app_parity_traces.py
```

Acceptance checks include independent dirty drafts, dirty-tab close confirmation, detected stale source, tree ancestors and nested descendants, search/preview compatibility, periodic monitoring, pause/resume, timer cleanup, endpoint scope and write guards. No repository project source is modified by mocked browser tests.

## Live acceptance (still required)

1. Apply with `python new_patch.py <path-to-zip>` and restart/reload the local app host.
2. Open `/applications/mcel-semantic-code-editor?mcel_trace=1`. Open two different files, edit both without saving, switch between them, close one with a dirty guard, and save a disposable file only after confirmation. Test external modifications while an unsaved tab is inactive.
3. Open `/applications/mcel-semantic-file-manager?mcel_trace=1`. Load a root, browse at least two folder levels, verify nested branches persist, and search/preview an authorized file.
4. Open `/applications/mcel-semantic-task-manager?mcel_trace=1`. Verify a process listing updates approximately every five seconds, pauses, resumes, and follows the selected monitor view; confirm no automatic process or server mutation occurs.
5. Export `MCELAppParityTrace.download()` in production and semantic variants for correlated behavior, not just API results.

## Unclosed Promise gates

- Code Editor's complete Aider mutating workflow and full Monaco production parity.
- File Manager's full production behavior, including any additional search/basket operations absent from this semantic candidate.
- Task Manager's governed consequential controls (process/server), scheduler mutation, full notebook coverage.
- Calculator's remaining authoritative two-file conformance and independent multi-application expert-design acceptance.
- Live Windows acceptance for all new behavior.

**Do not certify full Promise completion based on this patch.** It advances three reusable interaction classes while preserving the existing authority boundaries.
