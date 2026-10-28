# MCEL Patch 13 Stage 3 — Live Semantic Adoption (read-only shadow)

**Design constraint:** Patch 11 computer knowledge and the existing `mcel.application-ir.v1` are reused. No replacement IR, domain-specific renderer, new application, permission/capability, task execution, or Website Builder modification.

## Execution path

1. Existing `ViewportTaskRoutesMixin._handle_task_overview` calls `TaskManagerService.snapshot(...)` **once** for its normal response. Without the explicit POST flag `mcel_semantic_shadow: true`, behavior is unchanged.
2. When explicitly flagged, the handler calls `build_live_shadow(snapshot, profile='task-manager', request_scope=...)` against **that same snapshot**. A semantic exception is reported under `_mcel_semantic_shadow` without interrupting the original response.
3. The shared bridge compiles registered `task-manager.processes` knowledge to the existing Application IR, resolves the process entities and their application-specific derived role, and produces the existing semantic collection v2 contract. It preserves the source-shaped data, evidence, typed zero/unavailable values, candidate relationships, quarantine, and failures.
4. In the browser, `?mcel_semantic_shadow=lab` opts in. The original Task Manager request includes the shadow flag. The `McelTaskManagerSemanticShadow` consumer independently compares field values with the **same response**, mounts the existing `McelSemanticCollectionWorkspace` in a separate experimental section, and updates it on refresh. It never replaces Task Manager or its operational controls. A refusal keeps the existing legacy UI and last-known shadow contents intact.
5. No independent OS sample is called "parity": volatile process values can change between calls. Exact served-source parity is verified by field comparison and record accounting. A separate Task Manager producer check runs on local machinery in the Frob.

## Generic cross-domain acquisition

`build_live_shadow` accepts three actual application response shapes with the same compiler/resolver/collection adapter:

- `task-manager`: the Task Manager overview response, with explicit ISO8601/CIM/.NET `created_at` format bindings and bounded-top-N query scope.
- `file-explorer`: existing File Explorer list/search response. The catalog's root-scoped identity is preserved, and `mtime` is explicitly bound as Unix seconds. Numeric timestamps remain deferred in v2 projection, with exact original values preserved in canonical records.
- `worker`: existing worker-market/session source snapshots and the real read-only `/api/applications/worker/runtime-status` compact response (declaring a top-level runtime source remap). Logical worker and lease identities remain distinct. Worker runtime without a keyed identity is **canonical-only**, not fabricated into a collection.

The hosted Frob accesses existing File Explorer and Worker read-only routes; **this patch does not add shadow routes to those applications**. Missing source families are `not-observed`, not silently materialized empty.

## Invariants

- Same snapshot; no second producer call for the live shadow.
- Input payload untouched, raw fields preserved with evidence.
- `producer` and bounded scope declared; neither producer revision nor observed-at is invented. A client-local publish counter is not a source revision.
- Invalid records quarantined when safe; duplicate identities still refuse the entire affected keyed collection.
- Process identity canonicalized with explicit formats, but host-level and cross-refresh generation evidence is **not yet certified**, so `reconciliation` is **not** enabled in the canonical consumer. Its DOM continuity is intentionally unverified.
- Patch 10's existing Task Manager lab and production UI remain untouched; promotion requires their hosted lifecycle gate, including stable focus, selection, inspector and row identity.
- Worker read-only APIs retain their existing local-client security checks; the Frob never calls write, worker sync, wallet, shutdown, kill, or schedule routes.

## Installation and verification

```powershell
python new_patch.py "C:\Users\subsi\Downloads\mcel_patch13_stage3_live_semantic_adoption.zip"
python -m tools.mcel_patch13_live_adoption_frob
python -m pytest -q tests/test_mcel_patch13_live_shadow.py tests/test_mcel_patch13_foundations.py tests/test_mcel_knowledge_ir_bridge.py
```

Hosted acceptance requires the Main Computer server already running:

```powershell
python -m tools.mcel_patch13_live_adoption_frob `
  --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab"
```

For manual visual inspection, open:

`http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab&mcel_semantic_shadow=lab`

The Frob uses `--chromium-path` if a Chromium browser is installed outside Playwright's usual browser installation. Without `--host-url`, it reports hosted **NOT_RUN** and does not claim hosted acceptance.

**Not included:** automatic production promotion, canonical DOM reconciliation, a new visual grammar, authoring-economy results, or hosted File Explorer/Worker write paths.
