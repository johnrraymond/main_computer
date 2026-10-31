# MCEL Promise Closure — Patch 05 (independent File Explorer candidate)

## Objective and limits

Test whether the same official vanilla-JavaScript DSL, canonical Application IR,
shared semantic application shell, presentation profiles, and intent/effect/receipt
runtime can generate a **second structurally different existing application**.

This patch introduces an opt-in, read-only File Explorer candidate. The existing
File Explorer retains its route and entire legacy implementation as rollback.
The candidate uses the already-existing `FileExplorerSemanticAdapter` and its
preflight, execution, receipt, and filesystem authority checks. No new backend
endpoints, filesystem mutations, or command execution are admitted.

The candidate source is `mcel_experiments/file-explorer-candidate/application.js`;
the bundled browser IR is built from that file by
`python -m tools.mcel_file_explorer_candidate_bundle --write` and checked by
`python -m tools.mcel_file_explorer_candidate_bundle --check`.

## Generic MCEL change

Selectable `collection` regions now accept optional `rowKey` and `selectControl`
semantics. The shell validates the control against the IR and dispatches through
the existing effect runtime. Its UI rows are keyed: unchanged row DOM and keyboard
focus survive updates. For an application whose main semantic region is a
collection, the shell no longer requires an unrelated `notebook` slot.

No File Explorer-specific selectors, APIs, layouts, or permissions have been
added to shared MCEL core. The domain bridge only translates declared read
intents to the existing File Explorer semantic adapter. The same presentation
profiles used by Task Manager work on File Explorer.

## Browser acceptance

Run:

```powershell
python -m tools.mcel_file_explorer_candidate_bundle --check
python -m tools.mcel_promise_wave5_frob `
  --host-url "http://127.0.0.1:8765/applications/file-explorer"
```

Open the candidate using the server's application URL with the query:

`?mcel_file_explorer=candidate&mcel_design=analytical`

Or use `mcel_design=operational`. Remove the `mcel_file_explorer=candidate`
query parameter and reopen File Explorer to return to legacy mode. The
bridge also provides `McelFileExplorerCandidate.destroy()` as an explicit
rollback route.

The Frob runs an independent browser fixture for both design profiles at
320, 390, 560, 1100, and 1272 pixels, using the real semantic adapter with a
controlled transport. It checks roots, navigation, search, file preview,
source provenance, receipts, refusal of mutation, keyed row/focus retention,
profile switching, and legacy rollback. With `--host-url`, it additionally
checks real roots and directory-count parity and read-only receipts on Windows.

## Evidence boundary

A deterministic offline PASS is not a live Windows hosted PASS. Even a hosted
PASS does not close the whole MCEL promise. The existing user's aesthetic
judgment of Patch 04 was unsatisfactory; this patch does not reverse it.
Comparative author labor/economy and independently accepted design quality
remain unverified, as do any filesystem mutation capabilities (intentionally
out of scope). The original Phase 3 Task Manager author source was missing from
the supplied October 30 export, so source-rebuild regressions depending on that
file are blocked in the export; they must be rerun in the author's working tree.

Patch 05 should therefore be called a **bounded second-application generality
candidate**, not a full-promise certificate, unless all these gates are separately
satisfied.
