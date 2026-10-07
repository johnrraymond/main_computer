# Hub operation-functionality-module specification

Status: module-contract companion to `hub-o-f.md`; first-Hub add path implemented

Sources reviewed:

```text
hub.md SHA-256: dc1def412907396cc63b5456a284d298284c3176e7559a80a70c2724ce8a6be7
hub-o.md SHA-256: 171f0c5bdfdb431ef2549d8478e8bf9f956081ea3217e9fe227d76b9762e42cd
hub-o-f.md SHA-256: 2f6370469bed58e4ef719b0509f31e84a3a2d61ed70cb92e68208b194edcf7bc
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

`remove-hub` is implemented behind the same frozen surface, including ordinary contraction and explicit `--allow-full-deletion` protection for the final `1 -> 0` transition.

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

The first-Hub add path now implements these responsibilities in the existing bounded modules. Some originally planned file splits remain logical responsibilities inside `common/deployment.py` and `common/state.py`; they do not expand the operator surface. `remove_hub.py` remains future work.

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
| `common.deployment` | live-adapter/protocol/observer | Hub deployment payload generation, runtime-projection bootstrap, exact Coolify deployment wait, and public Hub runtime proof |
| `common.observer` | logical responsibility | Hub runtime/dependency observation; currently implemented within `common.deployment` |
| `common.operation_store` | logical responsibility | frozen operation records; currently implemented within `common.state` |
| `common.runtime_projection` | logical responsibility | combine Hub + FDB + Chain inputs into runtime config; currently implemented within `common.deployment` |
| `common.verification` | logical responsibility | operation and topology proof evaluation; currently implemented by add/inspect plus deployment observer |
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

`tools/coolify_hub_service.py` supplies low-level Coolify transport/helpers only, including runtime-env synchronization. Hub Control owns projection bootstrap and the lifecycle protocol: it writes the frozen FDB/topology projection at container start, then after triggering deployment extracts the returned deployment UUID and polls that exact Coolify deployment to a terminal result before public Hub verification. Hub-Control applications explicitly disable Coolify's application-level rolling health gate; the Hub observer, not Coolify readiness, is the acceptance authority for Hub/FDB/Chain startup. The image-level temporary healthcheck exists only to keep the candidate container alive long enough to inspect.

`tools/coolify_hub_cluster.py` is a source for multi-Hub iteration and public-entry behavior.

They are not imported as lifecycle authority by the new package. Useful behavior is harvested into bounded modules so legacy combined placement/dependency authority does not survive accidentally.

## 10. Runtime code boundary

The Hub application code under `main_computer/hub.py`, `stable_hub.py`, credit/indexer/bridge modules, and associated runtime components remains application code. Hub Control deploys and observes it; lifecycle control does not rewrite application semantics merely to establish the new authority boundary.

## 11. Contract and runtime-control tests

Tests freeze:

- exact public operation names and mutation authorization;
- automatic resume matching and `unborn -> 1` semantics;
- FDB/Chain dependency discovery and accepted generation-1 finalization;
- exact Coolify deployment-UUID extraction across supported response shapes;
- queued/in-progress deployment wait through successful terminal completion;
- failed deployment log-tail propagation;
- partial Hub runtime proof, including separate Hub/FDB/Chain truth;
- persistence of failed `do` deployment and observer evidence;
- accepted-state read schema for inspection.

## 12. Explicit remaining implementation gap

`add-hub` is live. `remove-hub` continues to return the explicit structured code:

```text
HUB_CONTROL_REMOVE_IMPLEMENTATION_PENDING
```

This remains deliberate until contraction/full-deletion functionality is implemented behind the same authority boundary.
