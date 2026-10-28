# Patch 11.0 — Main Computer semantic knowledge foundation

**Scope:** first cut of a **data-and-knowledge language**, independent of UI or layout. **Status:** implemented with offline verification; incremental database incomplete by design. The larger MCEL promise remains `INCOMPLETE`.

## Why this exists

MCEL should know what computers do. Authors describe a computer's observable reality in semantic terms once. Applications then become different consumers of that shared meaning. Layout is a downstream consequence, not the purpose of the knowledge database.

The first source is the **existing Task Manager** (`main_computer/task_manager.py:TaskManagerService.snapshot()`), which produces processes, connections, CPU/GPU telemetry, server state, and stored schedules. We will add concepts from other *existing applications* to this database **before** pursuing new automatic layout mechanisms or full application generation. This patch deliberately does not redesign Task Manager or change its running server.

## Files and public contracts

- `main_computer/mcel_knowledge/catalog/00-computer-core.json` — the initial 26 built-in computing concepts; semantic definitions distinguish identity, structured values, quantities, measurements, states, entities and operation descriptions.
- `main_computer/mcel_knowledge/catalog/10-task-manager.json` — Task Manager observation mappings (eight families), identity rules, candidate-only links, and six action descriptions.
- `main_computer/mcel_knowledge/__init__.py` — read-only loader, validator, interpreter, and `explain` function.
- `main_computer/mcel_knowledge/__main__.py` — read-only CLI.
- `tools/fixtures/mcel_computer_knowledge/task_manager_snapshot.v1.json` — representative producer-shaped fixture, not a genuine live observation.
- `tests/test_mcel_computer_knowledge.py` and `tools/mcel_semantic_knowledge_frob.py` — test, inspect, report.

Catalog schema: `mcel.computer-knowledge.v1`. A catalog fragment is JSON containing its distinct `namespace` and additive lists of `concepts`, `observations` and `operations`. A `concept` has a stable ID and kind, with optional units, bounds, parent type and explanatory meaning. An `observation` maps a real source path to one concept, its typed properties, identity evidence, optional relationships and optional availability markers. `operations` *describe* effect boundaries only; they have no executor.

The interpreter produces `mcel.computer-knowledge-observation.v1`, not a DOM tree, renderer recipe, or claim that the entire MCEL Application IR has been replaced.

### Example: source-native declarations, not layout commands

```json
{
  "id": "task-manager.processes",
  "concept": "mc.process",
  "source": "processes",
  "cardinality": "many",
  "identity": {
    "fields": ["pid", "created_at"],
    "scope": "host-process-lifetime",
    "requiresAll": true
  },
  "properties": [
    {"name": "pid", "source": "pid", "type": "mc.process.pid"},
    {"name": "resident_memory", "source": "memory_rss", "type": "mc.quantity.bytes", "availabilityFrom": "memory_availability"}
  ]
}
```

This small excerpt illustrates the author-facing meaning. The actual fragment also describes command lines, state, membership evidence, creation time, CPU/GPU, connections, the server and schedules. There is **no** `table`, `card`, `column`, `width`, `breakpoint`, or regularity hint in the catalog.

## What the interpreter can establish now

1. **Typed value and source truth.** It retains `raw` source values and source field names. Missing, null, empty, unavailable and present remain distinct. `present` paired with a missing/null source is contradictory, not silently zero. Numeric bounds are checked; a percentage of 240 is invalid rather than clamped.
2. **Scoped identity.** Process identity uses `(pid, created_at)`; a missing creation stamp makes it incomplete. Duplicate identity tuples are marked ambiguous. Completeness is not proof of global uniqueness: these IDs remain scoped to host and lifetime.
3. **Honest relationships.** A connection's owner PID provides only a *candidate* process link, even when one candidate matches, because the connection lacks generation evidence. It cannot authorize an action or prove the owner identity.
4. **Computing quantities and states.** CPU percentages, byte measurements, reported GPU memory units, timestamps, process states, endpoints, service readiness and scheduled operations are semantically different objects. It does not pretend to parse all platform-dependent formats yet.
5. **Operation meaning without capability.** The six named Task Manager operations are descriptions of external operation contracts. `authorized=false`, `executable=false`, and consequential operations declare the need for confirmation. No action is ever dispatched by this module.
6. **Independent use.** The catalog can be inspected and a JSON snapshot interpreted with no browser, app server, Task Manager service instance or layout runtime.

## Use it

From the project root:

```powershell
python -m main_computer.mcel_knowledge list
python -m main_computer.mcel_knowledge explain mc.process
python -m main_computer.mcel_knowledge explain task-manager.connections
python -m main_computer.mcel_knowledge inspect tools/fixtures/mcel_computer_knowledge/task_manager_snapshot.v1.json
python -m tools.mcel_semantic_knowledge_frob
python -m pytest tests/test_mcel_computer_knowledge.py -q
```

`inspect` reads a **saved** JSON snapshot. It does not fetch `/api/applications/task/overview` or call the Task Manager. Do not interpret saved fixture evidence as live acceptance.

## How to grow this database

The next step is to audit the *already-existing* Main Computer applications, identify which concepts are truly shared, and add catalog fragments without modifying this interpreter merely to accommodate a new application. Candidate discovery order:

1. **File Explorer / File Manager:** files, directories, paths, filesystem identity, metadata, hierarchy, operations, and exact/relative path semantics.
2. **Scheduler and Worker:** jobs, scheduled actions, ownership, task state, attempts, result claims, progress, authority and effects.
3. **Code Editor, Git Tools and service management:** documents, revisions, repositories, worktrees, processes, artifacts, and effects.

These are *future audits*, **not** claims that their sources were modeled in this patch. Extend concepts only from actual producer/API evidence; add independent snapshots and negative tests per app. If concepts conflict, refine the schema intentionally and version migrations rather than quietly redefining meanings.

## Boundaries and architecture decisions

- The catalog is **not** an alternate application compiler or an executable DSL. It is a versioned, declarative, easily editable first-cut knowledge *database* and an operationally useful read-only interpreter.
- Existing `mcel.semantic-collection-spec.v2`, Task Manager UI contracts, live reconciliation and MCEL Application IR remain authoritative in their respective domains; full compiler/IR unification is a subsequent, separately gated migration.
- No general layout selection, representation optimizer, hint machinery, code generation, interactive operation execution or new application is introduced.
- Flat-file JSON is sufficient while vocabulary is being discovered. No SQL service, graph database, external index or migration service is justified yet.
- `filename` regularity and compression are not hardcoded. The future shared semantics/methods layer can infer such structure once the vocabulary is sufficiently grounded.

## Falsification and next-stage gate

A second existing application must be describable with **additional catalog files**, not application-specific `if` branches in the interpreter. The expanded catalog must keep exact provenance, distinguish unavailable from measured zero, and refuse unproved identity/authority. Only after real cross-application coverage and verified usefulness without any GUI do we revisit unhinted layout inference or optional hints.
