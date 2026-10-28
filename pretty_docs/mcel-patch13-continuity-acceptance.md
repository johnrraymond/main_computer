# MCEL Patch 13 Stage 4 — Acceptance Ledger

This ledger is intended to avoid conflating **source evidence**, **offline
browser behavior**, **hosted browser behavior**, and **production promotion**.

| Requirement | Offline result | Hosted result |
| --- | --- | --- |
| Same existing Application IR and read-only v2 collection | PASS (no IR changes) | Inherited Stage 3 path |
| Server instance-scoped process generation evidence | PASS in isolated unit tests | NOT RUN here; Windows source to be verified on user host |
| ISO versus CIM creation time normalization | PASS | Source stability depends on real Windows producer and two live refreshes |
| Same lifetime, changed measurements | PASS; same DOM element + focus + inspector | NOT RUN here |
| PID reuse / changed process lifetime | PASS; old row removed and selection cleared | Synthetic test only; not induced on live server |
| Server restart / changed issuer | PASS; all affected rows replaced | Synthetic test only |
| Incomplete/malformed identities | PASS; per-record quarantine | Previous Stage 3 hosted observation expected |
| Duplicate identity collision | PASS; collection refused | Not intentionally induced on hosted system |
| Refused delivery | PASS; previous rows retained | No fault injection into hosted production |
| Bounded top-N absence | PASS; absence not misreported as death | Source remains bounded |
| Existing five operational controls | PASS; unchanged | Requires hosted Frob |
| Legacy Patch 10 lifecycle | PASS offline at 320/390/560/1100 | Requires hosted Frob |
| Stage 3 non-opted-in behavior | PASS regression | Existing production route unchanged |
| Canonical UI production promotion | **NO** | **NO**, regardless of hosted outcome |

### Independent local verification

Run `python -m tools.mcel_patch13_continuity_frob` for the Stage 4 unit, actual
v2 Chromium consumer, Stage 3 browser regression, and a fresh local process
producer. Run `python -m tools.mcel_patch13_continuity_frob --host-url ...` to
attempt the actual user Windows hosted check.

The hosting report's `certification` label is *read-only canonical continuity
in hosted lab only* if every hosted assertion passes, and `NOT_CERTIFIED`
otherwise. Do not claim full production adoption based on the Frob.

### Non-goals still ahead

Presentation compatibility and authoring-reduction measurement were
separately scoped stages of Patch 13. Their absence does not affect this
stage's safety certification, and they must not be reported as completed.

### Hosted zero-row repair (subsequent corrective cut)

A Windows hosted run returned zero Task Manager rows, despite five records in
an independent local sample. This is **INCOMPLETE**, not a DOM continuity
failure. The process producer previously returned `[]` for a CIM timeout or
command failure. The Stage 4 hosted-source repair adds an opt-in diagnostic
and longer bounded deadline only to `mcel_semantic_shadow` observations, and
refuses generation evidence on unsuccessful queries. See
[mcel-patch13-hosted-source-reliability.md](mcel-patch13-hosted-source-reliability.md).
Hosted certification and production promotion remain separate gates.
