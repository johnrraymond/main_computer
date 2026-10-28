# MCEL Patch 11.1–11.4 — Multi-application semantic knowledge acquisition

**Scope:** additive expansion of the UI-independent computer-knowledge database; **implementation:** first cross-application acquisition pass; **status:** offline tests pass, live application integration not attempted. **MCEL promise remains INCOMPLETE.**

## Architectural purpose

Main Computer's author describes the computer system once. Applications provide source bindings for computer-native semantic concepts; a read-only interpreter produces typed, source-preserving explanations. Presentation/representation inference, application generation, execution authority and user-supplied layout hints are **out of scope** until the shared knowledge has matured.

This patch implements in one delivery the earlier planned 11.1 File Explorer, 11.2 scheduling, 11.3 Worker and 11.4 shared-semantic consolidation passes. It **does not invent a standalone Scheduler application**: schedule semantics are grounded in Task Manager's `TaskManagerService.create_schedule` / `delete_schedule`, with task-queue semantics grounded in the existing Worker/Hub accepted-session records.

## Authoritative source audit

| Existing system / real source | Observations / domain contribution | Caution |
|---|---|---|
| `viewport_routes_file_explorer.py`: `_file_explorer_roots`, `_file_explorer_entry_payload`, `_handle_file_explorer_list`, `_handle_file_explorer_search`, `_handle_file_explorer_read` | `file-explorer.roots`, `file-explorer.entries`, `file-explorer.read-preview` | `root_id` scopes relative paths; `bytes=0` for a directory is **not** measured file size; `mtime=0` may be the stat-failure sentinel; `suggested_app` and `category` are heuristics |
| `task_manager.py`: `create_schedule`, `delete_schedule`, `_read_schedules` | Reuses `task-manager.schedules` with built-in `mc.schedule` and action semantic type; `scheduler.create-server-action` and `.delete-server-action` describe effects | Stored/enabled/planned **does not imply executed**; executable and authorized remain false |
| `stable_hub_worker_sessions.py`: `StableHubWorkerMarketDirectory.list_workers`, `record_worker_live`, `list_open_sessions_for_worker_connection`, `record_accepted`, `record_failed` | `worker.market-workers`, `worker.sessions` with shared task, queue, worker and lease vocabulary | IDs, sockets and epochs are scoped; connection-level relation remains **candidate-only**; no marketplace offers, payments or execution are performed by knowledge code |
| `viewport_routes_energy.py`: `_worker_runtime_status_response_payload` | `worker.runtime` read-only status | `allowed_to_accept` is reported policy, never MCEL authority or sufficient proof of a live session |

## Semantic catalog and API

Built-in concepts are in `main_computer/mcel_knowledge/catalog/00-computer-core.json`, with computer invariants and inferred affordances recorded directly against selected native concepts (`mc.process`, `mc.file.entry`, `mc.file.path`, `mc.schedule`, `mc.worker.market-lease`, `mc.worker.session`, etc.). Binding fragments extend them:

- `10-task-manager.json` — the original eight families, with schedule action refined.
- `15-file-explorer.json` — roots, source-contextual entries, list/search alternative source, preview metadata.
- `16-scheduling.json` — two Task Manager planning operations, declared-only.
- `20-worker.json` — worker-market lease, accepted session, runtime and declared-only runtime operations.

These fragments share `mcel.computer-knowledge.v1`; the existing Python loader and CLI are backward compatible. New generic semantics in the interpreter include:

- `sourceAlternates`: reuse one observation definition for a known equivalent source shape (File Explorer list versus search, Worker map versus listing).
- `collectionFormat: "map-values"`: accept producer record dictionaries or lists, without treating map keys as authoritative identity.
- `context` / `identity.contextFields`: bind identities to explicit envelope context such as File Explorer root IDs. If context is absent, identity becomes **incomplete**, not silently global.
- `fromContext: "snapshot" | "parent"`: read wrapper-level source facts while retaining provenance.
- `availableWhen`: narrow, non-executable equality/inequality predicate to mark producer placeholders as unavailable rather than measured zero.
- `representation`: primitive structured source validation for strings, objects and string lists; no stringification of invalid values.
- `relationships[].matches`: explicit multi-property equality for conservative candidate links. Matching alone never asserts relationship authority.

Existing availability states, source preservation, duplicate detection, and operation boundaries remain intact. `explain` returns concept semantics or raw source schemas; `inspect` emits semantic observations without UI or operational execution.

### Examples

```powershell
python -m main_computer.mcel_knowledge list
python -m main_computer.mcel_knowledge explain mc.worker.market-lease
python -m main_computer.mcel_knowledge explain file-explorer.entries
python -m main_computer.mcel_knowledge explain worker.sessions
python -m main_computer.mcel_knowledge inspect tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json
python -m tools.mcel_semantic_knowledge_acquisition_frob
python -m tools.mcel_semantic_knowledge_frob
```

The new sample fixture is assembled from **representative shapes** of existing service and API results, **not** captured live telemetry. File Explorer and Task Manager can each be interpreted in their own source-shaped response; Worker market/session records in the fixture are grouped under explicit `worker_market` and `worker_sessions` envelopes so their origins remain separate. Interpreting a saved record does not perform any HTTP request or contact a Worker.

## Why this is a semantic language and not an app-specific UI DSL

MCEL's concept records describe the *meaning* of source values: filesystem hierarchy, scoped identity, process generation, measurement validity, planned versus executed work, worker lease generation, work session state, and the limitations of observed relationships. This knowledge remains useful with no graphical UI. None of the new fragments specifies a table, card, viewport breakpoint, automatic compression policy, or CSS instruction. The loader never branches on the originating application's name.

The same generic `mc.identifier`, `mc.timestamp`, `mc.boolean`, `mc.quantity.count`, `mc.task`, `mc.task.queue` and observation semantics now serve distinct application producers. This is a **bounded** form of knowledge consolidation; universal ontological roles are still not compiled into the main MCEL Application IR.

## Evidence and limitations

- 30 semantic knowledge tests pass (15 Patch 11.0 plus 15 multi-app cases).
- 14 independent compatibility tests pass (File Explorer semantic adapter, Scheduler Lab report and Patch 10 Task Manager producer).
- Existing `tools.mcel_semantic_knowledge_frob` remains PASS on all 11 first-cut checks (its eight Task Manager families remain exactly eight).
- `tools.mcel_semantic_knowledge_acquisition_frob` passes all 16 bounded checks across the expanded database.
- The exported project omits `game_projects/webgl-demo/game.json`; two broader app test modules fail during **collection**, so neither is claimed as a pass or an observed regression from this patch.
- No actual hosted File Explorer or Worker API was reached here. Live integration, operation semantics completeness, performance, and UI/layout quality are **UNVERIFIED**.

**Uncovered knowledge:** File Explorer filesystem operation authorization, scheduler recurrence/trigger execution, Worker payments/settlement semantics, Git/Code Editor and other Main Computer applications, exact platform-dependent pathname semantics, proof of successful worker execution, and the full MCEL IR bridge. These remain future evidence requirements, not presumed solved by 11.4. Catalog expansion must continue when new actual application sources reveal missing concepts.

## Invariants for future acquisition

1. Native concepts have stable names and source-independent meaning; applications bind observed data into them.
2. No observed PID, file-root ID, socket, Worker ID, or schedule ID is assumed globally unique without scope.
3. No computed quantity is presented as measured when producer validity is absent.
4. Relationships never acquire certainty solely from matching weak identifiers.
5. Operations remain external-authority descriptions, never executable from the knowledge database.
6. New applications extend the same flat-file catalog without branching the interpreter on app names.
7. Default layout and hinted presentation are **subsequent consumers** of this knowledge, not embedded authoring obligations in the knowledge catalog.
