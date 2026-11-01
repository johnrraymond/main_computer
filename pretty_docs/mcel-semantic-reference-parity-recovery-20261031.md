# MCEL Semantic Reference Application Parity Recovery — 31 October 2026

## Status and scope

This patch is an **opt-in recovery increment, not V1 application certification**. It retains `application.html` + `application.js` and the common canonical-IR runtime. All original production applications remain unchanged. No `_v1` aliases or custom application layout trees were introduced.

The production implementations and their guarded APIs remain authoritative for functional parity. Automated tests use controlled browser transports; they do not demonstrate live Windows backends, live Monaco loading, or human design acceptance.

## Source-grounded changes

| Application | Implemented in this increment | Still incomplete / not verified |
| --- | --- | --- |
| File Manager | Nested directory browsing, parent navigation, scoped recursive search, file preview, traversal refusal; existing read-only endpoints | Rich Wunderbaum-equivalent file tree; real hosted Windows root traversal and search |
| Task Manager | Four separate backend-grounded notebooks: server processes, all processes, connections, CPU/GPU summary; schedule listing and explicit-request AI brief | Automatic refresh, server controls, terminate/kill with adequate process identity evidence, schedule create/delete, complete live parity |
| Code Editor | Existing Monaco mount remains; close-file and dirty-confirm handling; stronger applied-write receipt verification; existing project transaction prepare/apply for reviewed draft; explicit dry-run Aider command preview | Real Windows Monaco and write tests; Aider run/context lifecycle; multi-document tabs and complete file tree; complete production parity |
| Calculator | No changes in this patch; previously accepted semantic candidate retained | Outstanding prior Calculator parity gates remain |

## Safety invariants

- File Manager and Task Manager use **external-read** capabilities only; no file mutation or process control is gained through the semantic candidate.
- File Manager subdirectory requests require the root previously observed by the adapter and a scoped directory or ancestor. The server must enforce the actual filesystem boundary.
- Code Editor source saves require a previously opened, non-truncated source, explicit user confirmation, exact original hash, and a **full applied transaction receipt** with before/after/written hashes. HTTP success alone does not prove a source write.
- Reviewed Code Editor changes require a server-issued handle, matching draft bytes, explicit confirmation, and a hash-matched applied transaction receipt. Changes after review are refused before apply. The server remains the final authority on stale source and project containment.
- Aider is limited to an explicit **dry-run command preview**, not executing arbitrary Aider commands. Its separate run/context workflow is not yet migrated.
- AI analysis is not called automatically. The user must submit its form.
- Runtime semantic layout is still generic. No application-name-specific structural layouts are introduced; production routes are not promoted.

## Verification and evidence

- `python -m pytest -q tests/test_mcel_semantic_other_apps.py tests/test_mcel_semantic_editor_migration.py tests/test_mcel_semantic_parity_recovery.py`
- `python -m tools.mcel_semantic_other_apps_frob`
- `python -m tools.mcel_semantic_reference_parity_frob --audit-only` (source audit can succeed; **full parity remains incomplete**)
- `python -m tools.mcel_semantic_reference_parity_frob` (returns **2** while parity is incomplete, intentionally; a successful audit is not certification)

The parity report is `runtime/reports/mcel-semantic-reference-parity/report.json`. It separates declared source evidence from live verification; none of the above is allowed to silently convert `NOT_VERIFIED` into `PASS`.

## Stop conditions for promotion

Do not replace File Manager until rich tree/real hosted access and all required navigation/search operations pass. Do not replace Task Manager while process and server controls, auto-refresh, schedules, AI, and real notebooks are incomplete. Do not replace Code Editor until actual Monaco, multi-document lifecycle, guarded saves, full Aider/review workflows, and live Windows tests pass. The accepted Calculator remains separate and unchanged. Full V1 certification requires all four and human layout acceptance.
