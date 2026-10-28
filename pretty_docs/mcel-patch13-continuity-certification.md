# MCEL Patch 13 — Stage 4: Source-Evidenced Continuity

## Purpose

This is a **read-only continuity certification cut** built on Stage 3's real
Task Manager semantic shadow and the existing `mcel.application-ir.v1`. It
is **not** a replacement for the production Task Manager UI.

The known deficit after Stage 3 was that the new v2 semantic collection
recreated every row on refresh, because `collection_projection` correctly
withheld generation evidence for all concepts by default. This cut permits
a **Task Manager process-specific, source-proven, per-row** reconciliation
trial while leaving other entities and untrusted sources gated.

## Source of the evidence

The server route `/api/applications/task/overview` already takes the live
`TaskManagerService.snapshot()` once and computes the opt-in shadow from the
same data. The route now adds a server-process-scoped identifier, generated
once at import and rotated whenever that Python web-server process restarts,
and labels which actual producer path emitted process creation times:

- `psutil-create-time` on the psutil-backed process source;
- `win32-cim-creation-date` on the Windows PowerShell/CIM path.

For each canonical process with a **complete, valid identity**, a **positive
integer PID**, and a **timestamp normalized by an explicitly declared source
format**, the read-only continuity adapter computes a deterministic scoped
fingerprint from `(server instance, PID, normalized process start)`. These
fingerprints are labelled `mcelGeneration` in the source-preserving v2 row.

**Important boundaries:** the instance identifier is not a secret or an
attestation of OS provenance. The producer label is selected on the trusted
server route, never from browser or user input. The timestamp is normalized
rather than guessed. Matching PID alone, process names, or raw datetime
strings never establish continuity. No claims are made about a process's
existence when it falls out of the bounded top-N observation.

## Safe behavior

1. Both the producer instance and a recognized source path must be supplied.
2. Same-response source parity must already pass.
3. Only qualified rows receive `mcelGeneration`; incomplete observations are
   quarantined or have no generation, and cannot inherit UI state.
4. Duplicate collection identities still refuse the entire collection.
5. The existing v2 consumer retains DOM nodes, focus, selection, and exact
   inspection **only when the same row ID and generation both match**.
6. Changed process lifetime means a different canonical ID; old inspection
   is cleared. A server restart rotates the generation even if PID and
   creation time happen to match.
7. Refused shadow updates keep the last valid read-only collection. Production
   operational controls and the original Task Manager renderer are untouched.
8. Stage 3 callers that do not provide producer evidence continue to receive
   the old non-reconciling projection.
9. Neither the source adapter nor the browser ever claims `continuityCertified`
   based solely on valid tokens. Actual hosted browser evidence is required.

The shared collection's disappeared-row text was also corrected: a row absent
from a bounded observation does **not** mean its process has terminated.

## Install and tests

Apply using the existing manifest-free ZIP installer:

```powershell
python new_patch.py "C:\Users\subsi\Downloads\mcel_patch13_stage4_continuity_certification.zip"
```

Offline verification:

```powershell
python -m tools.mcel_patch13_continuity_frob
```

To test live Windows CIM data and hosted DOM continuity, **with the Main
Computer server running**:

```powershell
python -m tools.mcel_patch13_continuity_frob `
  --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab"
```

Opt-in browser lab:

`http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab&mcel_semantic_shadow=lab`

The report is `runtime/reports/mcel-patch13-continuity.json`. It contains
independent `offlineBrowser`, `previousStage3Browser`, `realLocalTaskProducer`,
`hosted`, and `productionPromotion` fields. **Hosted `NOT_RUN` is not PASS**.

## Acceptance gate

The offline browser uses the actual MCEL v2 workspace with synthetic process
records and confirms all of the following: retention across measurement
changes, exact inspector refresh, keyboard focus retention, correct removal
on PID reuse, no continuity across a server restart, bounded-scope
non-death semantics, isolated invalid records, refusal preserving last-known
rows, unchanged operational controls, no browser errors.

The hosted test additionally requires live server-issued source evidence,
actual DOM identity and focus retention across a fresh Task Manager refresh,
inspector continuity, and the old Patch 10 lab remaining operational. Hosted
certification covers **the read-only canonical lab only** and explicitly does
not promote the canonical UI or alter write paths.

## Out of scope

- Automatically replacing production Task Manager's process view.
- Extending process-generation evidence to files, sockets, or worker leases.
- Proving external/cryptographic OS attestation or universal CIM reliability.
- Changing display tiers, labels, hints, or authoring economy.
- Executing any operation described by the semantic catalog.
- Changing Website Builder.
