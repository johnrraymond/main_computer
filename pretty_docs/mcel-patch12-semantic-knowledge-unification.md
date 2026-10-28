# MCEL Patch 12 — Semantic Knowledge Unification and Existing Application IR Bridge

**Implementation status:** bounded offline integration PASS. **Full live-app migration:** UNVERIFIED. **Overall MCEL promise:** INCOMPLETE.

## Purpose

Patch 11 accumulated the Main Computer knowledge database from actual Task Manager, File Explorer, scheduling, and Worker producer shapes. Patch 12 makes that existing knowledge directly usable through **`mcel.application-ir.v1`**, rather than creating a competing application IR or inventing another UI language.

The author's domain contribution is a *source binding*, e.g. a process producer calls the PID `os_pid` instead of `pid`. The semantic meaning remains `mc.process.pid`, and the existing MCEL Application IR semantic fingerprint remains the same. The source-binding fingerprint changes.

## Implemented contract

`main_computer/mcel_knowledge/ir_bridge.py` exports:

- `compile_knowledge_ir(app_id, observation_ids, bindings, catalog)` — resolves concept/observation IDs from the existing registered flat-file catalog; emits a **normalized and validated `mcel.application-ir.v1`** with `models`, typed field `conceptRef`s, `observationRef`s, scoped identity, and source bindings in IR `provenance.nodeBindings`. No effects, intents, or capabilities are synthesized.
- `resolve_knowledge(snapshot, bridge, catalog)` — verifies the catalog fingerprint, source-binding fingerprint, semantic fingerprint, and entire canonical IR before interpreting read-only data. Produces typed properties with raw values, validity, provenance, scoped identities, candidates-only relationships and non-executable operation descriptions.
- `collection_projection(view, observation_id, catalog)` — adapts a source family into the **existing** `mcel.semantic-collection-spec.v2` plus bounded rows. Each row also carries its full exact canonical record (`mcelCanonicalRecord`) for inspection; values omitted from the compact display are not thrown away. This is an optional read-only consumer adapter, **not a replacement Task Manager renderer**.

Bridge envelope `mcel.knowledge-application-bridge.v1`; resolved view envelope `mcel.knowledge-consumer-projection.v1`; collection adapter envelope `mcel.knowledge-collection-adapter.v1`. These are binding/projection transport envelopes, **not new competing application IR schemas**. The authoritative application IR remains `mcel.application-ir.v1`, and the sole source of domain definitions remains the Patch 11 JSON catalog.

## Source-binding authoring example

This is an input to the bridge CLI, **not a new layout DSL**:

```json
{
  "task-manager.processes": {
    "source": "procItems",
    "properties": {
      "pid": "os_pid",
      "created_at": "began",
      "name": "executable"
    }
  }
}
```

The observation identity remains **(PID, start time) within its declared lifetime scope**. Missing required identity parts or duplicate identities refuse stable-collection projection. **Complete scoped identity does not prove continuity across refreshes**: v2 reconciliation is disabled by default and may be enabled only through explicit `identity.continuity: verified-generation` catalog evidence. An unavailable memory value remains unavailable even when the untrusted producer carries `0`; the original `0` is preserved as raw evidence. Worker/session join evidence is never promoted from `candidate-only` into ownership/authority.

## Operator workflow (all commands read-only)

```powershell
python -m tools.mcel_knowledge_ir_bridge compile --app-id computer-observations --observation task-manager.processes --output runtime/reports/mcel-patch12-ir.json

python -m tools.mcel_knowledge_ir_bridge resolve --app-id computer-observations --observation file-explorer.entries --observation worker.sessions --observation worker.market-workers --snapshot tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json --output runtime/reports/mcel-patch12-resolved.json

python -m tools.mcel_knowledge_ir_bridge collection --app-id computer-observations --observation file-explorer.entries --snapshot tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json --output runtime/reports/mcel-patch12-collection.json

python -m tools.mcel_knowledge_ir_frob
python -m pytest -q tests/test_mcel_application_ir.py tests/test_mcel_computer_knowledge.py tests/test_mcel_computer_knowledge_multiapp.py tests/test_mcel_knowledge_ir_bridge.py
```

**Limitation of the collection adapter:** `mcel.semantic-collection-spec.v2` currently supports at most 16 displayed fields / 500 rows and cannot render structured values as typed cells or numeric-source timestamps as typed timestamp cells. Rather than fabricating failed/converted values, the adapter lists these in `deferredFields` and preserves originals in `mcelCanonicalRecord`. The interpreter itself retains their original semantic types and values.

## Architectural boundaries

- No website/server endpoint or hosted Task Manager, File Explorer, Worker route is modified or automatically switched to the new bridge.
- The existing `mcel.application-ir.v1` structural validator accepts `conceptRef` and `observationRef` extension properties; **the bridge** is responsible for resolving them against the catalog and refusing a stale/tampered reference.
- The bridge never executes a task or infers operational authorization from stored declarations. Operations remain `executable:false` and `authorized:false`.
- No hints, layouts, automatic appearance/interaction design, new optimizer, or actual operator UI are generated here.
- Source aliases are finite dotted property lookups. They cannot execute arbitrary expressions.
- The catalog can continue acquiring computer-native concepts from additional actual applications without branching the resolver on application names.

## Implementation files

- `main_computer/mcel_knowledge/ir_bridge.py`
- `tools/mcel_knowledge_ir_bridge.py`
- `tools/mcel_knowledge_ir_frob.py`
- `tests/test_mcel_knowledge_ir_bridge.py`

See [Patch 12 acceptance and limitations](mcel-patch12-semantic-knowledge-acceptance.md). Also see [Patch 11 cross-application knowledge acquisition](mcel-patch11-multi-app-semantic-acquisition.md).
