# Patch 13 Stage 3 — Acceptance and limitations

**Evidence from this exported repository (offline):**

| Gate | Status | Evidence |
| --- | --- | --- |
| Real local Task Manager producer | PASS | `TaskManagerService.snapshot(limit=5)` through shared semantic resolver and same-response parity |
| Single existing MCEL Application IR | PASS | All live profiles compile into `mcel.application-ir.v1` |
| Timestamp normalizations (CIM and ISO) | PASS | Canonical identity equivalent while raw values differ |
| Missing versus measured zero | PASS | Frob confirms zero-byte raw, unavailable record remains unavailable |
| Malformed records isolated | PASS | Valid rows retained; invalid timestamps quarantined |
| Ambiguous duplicate identities | PASS | Whole affected collection refuses, never reconciles incorrectly |
| Source scope, revision evidence | PASS | Bounded top-N, source revisions absent, no invented observed-at |
| Browser v2 shadow consumer | PASS (offline Chromium fixture) | Existing Task Manager markup, read-only mount, second refresh, refused delivery preserving last-known view |
| Existing Task Manager controls untouched | PASS (offline fixture) | All five control elements retained before and after shadow refresh |
| File Explorer and Worker shapes | PASS (read-only fixtures + generic interpreter) | Existing producer-shaped snapshots, distinct logical worker/lease semantics |
| Previous MCEL Application IR and knowledge regression | PASS | 97 pytest tests and 8 subtests |
| Patch 10 offline lifecycle | PASS (4 widths) | Reused existing Frob at 320/390/560/1100; hosted explicitly BLOCKED without server |
| Hosted Task Manager parity and browser second refresh | NOT RUN in container | Execute Frob with `--host-url` on the user's running Windows server |
| Hosted File Explorer and Worker acquisition | NOT RUN in container | Read-only host probes, with local permission/refusal reported separately |
| Patch 10 hosted focus/selection/inspector continuity | NOT RE-CERTIFIED | Canonical shadow has no reconciliation; existing production path unmodified |
| Canonical UI production promotion | GATED | Requires verified host identity and lifecycle continuity evidence |
| Authoring reduction | NOT MEASURED | Stage 7 |

The standalone Stage 3 Frob runs focused unit tests, a fresh local Task Manager producer sample, and a Chromium browser fixture. With `--host-url` it additionally requests the real Task Manager overview response using the explicit shadow flag, acquires a File Explorer roots/list response, reads the local Worker runtime status, and checks an opt-in hosted browser second refresh. Failed or inaccessible host probes report `INCOMPLETE`/`BLOCKED`, not PASS.

**Important:** The browser fixture is not hosted evidence. Numeric File Explorer timestamps remain canonically exact and are deferred from the existing v2 visual projection. Missing Worker sources are not fabricated. A producer identity is not automatically cross-refresh continuity proof.
