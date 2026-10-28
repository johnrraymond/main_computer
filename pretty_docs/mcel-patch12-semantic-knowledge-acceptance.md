# MCEL Patch 12 — Requirements, Evidence, and Remaining Gates

**Status:** BOUNDED OFFLINE PASS; **live app integration:** NOT YET VERIFIED; **MCEL promise:** INCOMPLETE.

| Requirement | Status | Evidence and remaining qualification |
|---|---|---|
| P12.0 audit existing schemas; avoid parallel application IR | PASS | Compiler emits validated, normalized `mcel.application-ir.v1`, with catalog concept refs on model/field nodes; no changes to application IR schema or compiler |
| P12.1 generic concept resolver and source compiler | PASS (offline) | `compile_knowledge_ir`, `resolve_knowledge`; no source/application name branching; source renaming changes binding fingerprint, not semantic fingerprint |
| P12.2 Task Manager, File Explorer, Worker, scheduling mapped | PASS (representative saved-source fixtures) | One IR bridge and runtime handles all four real-source families; source-shape migrations and alternative aliases tested. **Hosted source parity is UNVERIFIED** |
| P12.3 semantic interpretation, scoped identity and uncertain relations | PASS (offline) | Native computer type values, `present` vs unavailable, PID-generation composite identity, file-root identity, Worker lease context and candidate-only links retained |
| P12.3 operation safety | PASS | Descriptions stay unauthorized/non-executable; no runtime intents/capabilities/effects added by compiler |
| P12.4 existing MCEL semantic collection consumer | PASS (Node/real existing JS projection) | Generated v2 spec+rows accepted by existing `McelSemanticValueContract` **and** `McelSemanticCollectionWorkspace.project` |
| P12.4 hosted application read-only consumer | UNVERIFIED | No production host JS or server routes changed; requires future opt-in or real data feed |
| P12.5 normalization, fingerprints, integrity and cross-domain tests | PASS (offline) | Frob plus pytest suite below |
| P12.5 Patch 9/10 browser lifecycle preservation | NOT RE-ESTABLISHED LIVE | Production browser code untouched, but previous hosted evidence is historical and has not been re-run on user server |
| Authoring economy relative to an equivalently correct hand-authored app | UNVERIFIED | Demonstrated *source-only rebinding*, not measured person-time or controlled paired baseline |
| Representation quality/unhinted app generation | OUT OF SCOPE | No new design policy or hint system; future experimental gates |

## Verification commands and actual evidence

- `python -m tools.mcel_knowledge_ir_frob` — 21/21 independent offline assertions PASS.
- `python -m pytest -q tests/test_mcel_application_ir.py tests/test_mcel_computer_knowledge.py tests/test_mcel_computer_knowledge_multiapp.py tests/test_mcel_knowledge_ir_bridge.py` — 61 PASS, including existing Application IR tests and the Node/browser-consumer projection witness.
- `python -m tools.mcel_semantic_generality_frob --output <report-dir>` — 43 independent checks plus 8 real registered browser cases PASS (offline).
- `python -m tools.mcel_task_manager_live_lifecycle_frob --output <report-dir>` — 4/4 offline browser widths PASS; hosted BLOCKED without Windows URL.
- Previously established Patch 11 `mcel_semantic_knowledge_frob` (11/11) and `mcel_semantic_knowledge_acquisition_frob` (16/16) are also executed as acceptance dependencies within Patch 12's Frob.

## Failure controls

- Unknown observation, unregistered concept, invalid source/property binding or stale catalog fingerprint → refusal.
- Mutated normalized IR → refusal instead of interpreting a changed ontology silently.
- Duplicated or incomplete scoped identity → refusal to generate a stable collection. A complete identity does **not** automatically imply lifetime continuity; generational reconciliation is disabled unless a catalog explicitly declares `identity.continuity: verified-generation`.
- Wrong numeric type, unmeasured telemetry or impossible availability → source-level evidence retained, never silently converted to measured zero.
- Unsupported structured fields and numeric timestamps in v2 presentation → explicitly deferred, with exact canonical source retained for inspection.
- Worker/session matching by identifiers alone → candidate-only and never operational authority.
- Descriptions of kill, shutdown, scheduling or job execution → no capability grants, operational calls or effects executed.

## Open work

1. A real hosted Task Manager/File Explorer/Worker read-only integration and parity Frob, without changing existing operations or Website Builder.
2. Wider native v2 semantic field support (structured paths, numeric timestamps and other concepts) with exactness and accessibility tests.
3. A controlled authoring-economy comparison to confirm that catalog-backed source bindings actually reduce developer work for equal correctness.
4. The later fully semantic unhinted application construction and optional-hint research. Patch 12 does **not** certify these outcomes.

**Truth condition:** If a downstream application bypasses `compile_knowledge_ir`/`resolve_knowledge`, its `conceptRef` annotations alone do not prove catalog consistency. Integration into the application author's build lifecycle remains a separate, testable acceptance step.
