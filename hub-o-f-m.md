# Hub operation-functionality-module specification

Status: module-contract companion to `hub-o-f.md`; first-Hub add path implemented

Sources reviewed:

```text
hub.md SHA-256: dc1def412907396cc63b5456a284d298284c3176e7559a80a70c2724ce8a6be7
hub-o.md SHA-256: a47ed7f246be3fa2888b0372a94b3c60c383af59fed240660794e1d3f605df17
hub-o-f.md SHA-256: f2e35cd9329f339775f204c485a144dc9387b6419ea9d058e086046f8c237a9d
```

Repository implementation evidence reviewed:

```text
tools/coolify_hub_service.py
tools/coolify_hub_cluster.py
main_computer/hub.py
main_computer/stable_hub.py
main_computer/stable_hub_topology.py
```

## Implemented first-birth module slice

```text
hub_mutate_harness.py

tools/hub_control/
    __main__.py
    inspect.py
    add_hub.py
    common/
        canonical.py
        errors.py
        models.py
        state.py
        privates.py
        placement.py
        fdb_contract.py
        chain_contract.py
        deployment.py

run-exp-fdb-hub.py
main_computer/stable_hub_topology.py
```

`add_hub.py` owns `prep/do/operation-inspect/finalize` for the first live birth path. `deployment.py` is the neutral Coolify/application adapter and public Hub observer. `run-exp-fdb-hub.py` is extended only to materialize the frozen Hub Control projection before executing the existing Hub runtime. `stable_hub_topology.py` permits a single concrete Hub so `0 -> 1` is representable.

`remove-hub` remains behind the frozen surface but is not implemented by this slice.

## 1. Purpose and authority

This document maps the Hub lifecycle functionality chain to implementation ownership. The authority order is:

```text
hub.md
  -> hub-o.md
    -> hub-o-f.md
      -> hub-o-f-m.md
        -> contract tests
          -> implementation
```

Module convenience never changes operator meaning or authority boundaries.

## 2. Public surface

The normal operator entry point is:

```text
hub_mutate_harness.py
```

It owns:

- public argument parsing;
- mutation authorization pairing;
- automatic resume selection;
- stage orchestration;
- operator-facing stage summaries;
- harness evidence capture.

It does not implement Coolify transport, placement, dependency contracts, or accepted-state mutation directly.

## 3. Internal package target

The target package is:

```text
tools/hub_control/
    __main__.py
    inspect.py
    add_hub.py
    remove_hub.py

    common/
        models.py
        state.py
        privates.py
        placement.py
        coolify.py
        deployment.py
        observer.py
        operation_store.py
        runtime_projection.py
        verification.py
        fdb_contract.py
        chain_contract.py
```

This surface patch creates only the package boundary and internal CLI contract. The live modules are introduced in later patches as their functional contracts are implemented.

## 4. Module authority classes

| Module | Authority class | Responsibility |
|---|---|---|
| `hub_mutate_harness.py` | operator orchestrator | public lifecycle intent, resume, evidence, summaries |
| `tools.hub_control.__main__` | internal CLI adapter | machine-readable stage seam for the harness |
| `inspect.py` | reader/orchestrator | accepted + observed Hub inspection |
| `add_hub.py` | protocol | add/rebirth protocol |
| `remove_hub.py` | protocol | remove/full-delete protocol |
| `common.models` | core-pure | typed accepted/operation/dependency models |
| `common.state` | accepted-authority writer | canonical Hub accepted state |
| `common.privates` | reader | narrow shared private infrastructure reader |
| `common.placement` | core-pure/reader | logical Hub ID to physical placement resolution |
| `common.coolify` | live-adapter | Coolify API transport and exact deployment identity |
| `common.deployment` | protocol/renderer | Hub deployment payload generation |
| `common.observer` | reader | Hub runtime/deployment/dependency observations |
| `common.operation_store` | state-writer | frozen operation records |
| `common.runtime_projection` | core-pure | combine Hub + FDB + Chain inputs into runtime config |
| `common.verification` | reader/core-pure | operation and topology proof evaluation |
| `common.fdb_contract` | reader | canonical FDB consumer contract |
| `common.chain_contract` | reader/verifier | canonical Chain consumer contract; required-vs-optional contract preflight |

## 5. Dependency direction

```text
hub_mutate_harness.py
  -> tools.hub_control.__main__
      -> operation modules
          -> common protocol/state/read adapters
              -> core models/derivations
```

The internal Hub package must not import FDB lifecycle or Mother lifecycle modules as authority. It consumes dependency contract artifacts through narrow readers.

## 6. Accepted state location

Canonical Hub accepted state is targeted at:

```text
runtime/state/hub/<network>/accepted.json
```

The surface patch defines schema name:

```text
main-computer.hub-accepted.v1
```

The accepted-state writer does not exist in this patch. Read-only internal inspection may recognize that schema when present.

## 7. Harness run state

Harness execution evidence lives below:

```text
runtime/state/hub/harness-runs/<run-id>/
```

Each run records:

```text
harness-state.json
00-pre-inspect.command.txt/.stdout.txt/.stderr.txt/.json
01-prep.*
02-do.*
03-operation-inspect.*
04-finalize.*
05-final-inspect.*
```

Low-level commands are evidence, not normal operator output.

## 8. Internal CLI contract

The harness-owned seam is:

```text
python -m tools.hub_control --json inspect <network>
python -m tools.hub_control --json inspect <network> --operation-id <id>
python -m tools.hub_control --json add-hub prep <network> --hub <hub-id>
python -m tools.hub_control --json add-hub do <network> --operation-id <id>
python -m tools.hub_control --json add-hub finalize <network> --operation-id <id>
python -m tools.hub_control --json remove-hub prep <network> --hub <hub-id> [--allow-full-deletion]
python -m tools.hub_control --json remove-hub do <network> --operation-id <id>
python -m tools.hub_control --json remove-hub finalize <network> --operation-id <id>
```

Operators do not type these commands.

## 9. Legacy code migration

`tools/coolify_hub_service.py` is a source for transport, payload, health, runtime configuration, bridge-material, and deployment-wait behavior.

`tools/coolify_hub_cluster.py` is a source for multi-Hub iteration and public-entry behavior.

They are not imported as lifecycle authority by the new package. Useful behavior is harvested into bounded modules so legacy combined placement/dependency authority does not survive accidentally.

## 10. Runtime code boundary

The Hub application code under `main_computer/hub.py`, `stable_hub.py`, credit/indexer/bridge modules, and associated runtime components remains application code. Hub Control deploys and observes it; lifecycle control does not rewrite application semantics merely to establish the new authority boundary.

## 11. Contract tests in this patch

This patch adds tests that freeze:

- exact public operation names;
- default `mainnet` network;
- required/forbidden `--hub` usage;
- paired mutation authorization;
- final-deletion acknowledgement scope;
- absence of low-level public flags;
- automatic resume matching;
- accepted-empty add/rebirth semantics at the harness boundary;
- exact final membership checks;
- internal CLI structured gap instead of legacy delegation;
- accepted-state read schema for read-only inspection.

## 12. Explicit implementation gap

Until the next implementation patches land, internal mutation stages return:

```text
HUB_CONTROL_MUTATION_IMPLEMENTATION_PENDING
```

This is deliberate. The new surface must not route a clean operator command into the old mixed Hub/FDB deployment authority merely to appear complete.
