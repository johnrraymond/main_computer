# Patch 13 foundation: semantic boundaries, observation evidence, and correctness

**Implementation:** Stages 1 and 2 in one additive, read-only patch. **Not** Patch 13 live adoption or layout generation.

## Why

Patch 11 defines computer-native concepts; Patch 12 compiles them to `mcel.application-ir.v1` and resolves source-shaped observations. This cut makes the boundary between shared knowledge, application knowledge, source evidence, and presentation explicit, then repairs correctness limitations before any live consumer promotion.

### Four information owners

| Owner | Information | Implemented mechanism |
| --- | --- | --- |
| Core computer catalog | Process/worker/file entities, quantity types, identity invariants | `catalog/00-computer-core.json` |
| Application semantic catalog | Which source properties describe observed concepts, application-defined facts | Observation properties and `derivedProperties` with bounded `first-true` and explicit evidence |
| Producer binding | Field paths, timestamp formats, observation producer, revision domain, scope | Extended `bindings` in existing IR bridge; **only provenance changes** for source remaps |
| Presentation | Labels, tiers, priority, truncation, responsive layout | **Out of scope**. No presentation keys accepted in semantic bindings. An optional compatibility overlay, if eventually needed, must be measured as authoring work. |

## Extended producer bindings

`properties.<property>` accepts the original dotted path or one of the following:

```json
{
  "task-manager.processes": {
    "properties": {
      "created_at": {
        "path": "created_at",
        "formats": ["iso8601", "cim-dmtf", "dotnet-json"]
      }
    },
    "envelope": {
      "producer": "TaskManagerService.snapshot",
      "observedAt": {"path": "observation_time", "format": "iso8601"},
      "revision": {"path": "publication_revision", "domain": "publication"},
      "scope": {
        "kind": "bounded-top-n",
        "parameters": {"limit": "request.limit", "query": "request.query"}
      },
      "completeness": "bounded"
    }
  }
}
```

**This is an illustrative input binding, not a claim that existing Task Manager snapshots already contain these fields.** In particular, its current producer does **not** include `observation_time`, `publication_revision`, `request.limit`, or `request.query` in the response. Missing source evidence is reported as missing; nothing is synthesized. A live caller must supply the fields it can genuinely evidence when adoption occurs. `revision.domain` distinguishes producer revisions from bridge-generated publication revisions. Producer labels and completeness are declarations, not cryptographic proof.

Supported timestamp formats: `iso8601` (timezone mandatory), `cim-dmtf` (DMTF timezone offset in minutes), `dotnet-json` (`/Date(<epoch-ms>)/`), `unix-seconds`, `unix-milliseconds`. The optional `formats` list **explicitly enumerates** accepted producer alternatives; no magnitude-based guessing. Unknown, invalid or ambiguous timestamps are marked invalid. `raw` remains identical to the source field; `normalized` and `matchedFormat` are additional evidence. Source formats and envelope bindings affect the IR **source binding** fingerprint, not the semantic fingerprint.

The envelope describes producer, observed-at, scoped parameters, completeness, and a revision **only where declared or observed**. Defaults remain unknown. In particular, top-N absence **does not** imply process nonexistence. No inferred globally total enumeration or authoritative relationships.

## Application-semantic derivation

The Task Manager role is a bounded application classification:

1. `is_current_process` true -> `current-process`;
2. `is_main_computer` true -> `main-computer`;
3. if **both values were measured present** and neither was true -> `other-process`;
4. otherwise `missing`.

The derivation includes application-heuristic provenance; it is not added as an OS fact to `mc.process`. No executable expressions or free-form Python are accepted. This does not force a presentation column or alter existing Task Manager UI.

## Correctness safeguards

- Process-lifetime timestamps can be compared in UTC **within declared source formats**. Bad formatting makes identity incomplete rather than treating an empty string as a legitimate generation.
- Host identity remains a separate evidence requirement. Current Task Manager process snapshots lack an independently verified host identity, so this cut does **not** assert cross-host process continuity.
- A malformed record is reported in `rejectedRecords` without dropping valid records from its observation family. An incomplete identity is quarantined **per row** when at least one safe row remains, with the full canonical record retained.
- Duplicate keyed identities still cause collection-level refusal (Patch 12's fail-closed policy). Without proof, the adapter must not publish ambiguous keys.
- A collection where *all* rows are quarantined is refused, not rendered as a potentially misleading empty collection.
- Source values and availability are preserved, including actual zero vs unavailable zero; no operational effects are added.
- `worker.logical-workers` is an observation of `(cluster_id, worker_id)` from marketplace lease reports; it is not a persistent authoritative worker directory. `worker.market-workers` retains the distinct four-element lease identity. The relation is a **candidate-only** match. Two active leases with one logical ID produce ambiguous logical-worker observation identity rather than overwriting each other.
- Verified-generation DOM reconciliation is **not** switched on. A syntactically complete identity does not prove stable lifetime across live observations.

## Verification

```powershell
python -m tools.mcel_patch13_foundation_frob
python -m pytest -q tests/test_mcel_patch13_foundations.py tests/test_mcel_computer_knowledge.py tests/test_mcel_computer_knowledge_multiapp.py tests/test_mcel_knowledge_ir_bridge.py
python -m tools.mcel_knowledge_ir_frob
```

The patch is noninvasive: no Task Manager, File Explorer, Worker, Website Builder, or existing browser runtime behavior is modified.

## Gated next work

1. Actual served Task Manager snapshot tapping, with measured host/provenance and source scope, plus parity against the same served response.
2. Verified-generation identity continuity and Patch 10 hosted browser lifecycle gates before read-only consumer promotion.
3. Numeric timestamp support in the shared v2 browser value contract (this patch normalizes semantic timestamps, **not** that renderer).
4. File Explorer and Worker live adoption and real Task Manager contract reduction; only then authoring-economy scoring.
5. No authoritative relationship claims until separately proved by source evidence rules.
