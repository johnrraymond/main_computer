# FDB operation-functionality specification

Status: operation-first functional companion to `fdb-o.md`

Sources:

```text
fdb.md
SHA-256: fe0dd9fcbe702c3d7b04bbb6a8ba2072f59e4a5f6de300d735d929561d39df1f

fdb-o.md
SHA-256: 0f45df125ce6ea6a8600c6830697094b77a0e5cc7a578f55b11b55df97bc551b
```

## 1. Purpose and authority

This document specifies the reusable functionalities that compose every
operation in `fdb-o.md`.

Its organizing unit is the operation:

```text
operation
  stage
    ordered functionality
      operation-specific use
```

`fdb.md` governs the FDB architectural boundary and system invariants.
`fdb-o.md` governs operator-visible operation meaning and lifecycle semantics.
This document governs the functional decomposition required to implement those
operations. If the documents conflict, the higher-level source governs.

This file intentionally does not freeze Python package names, classes, helper
function names, Coolify adapter layout, or FDB wrapper modules. Those belong in
`fdb-o-f-m.md`.

The existing experimental FDB deployment code, including
`tools/coolify_fdb_cluster.py`, is implementation evidence and may supply useful
behavior, but it is not the authority for this functional design. Existing code
may be reused only where it satisfies the contracts below without reintroducing
one-service-per-host, Hub ownership, Mother lifecycle dependency, or
fire-and-forget deployment assumptions.


## 2. Functional decomposition rules

### 2.1 Functionality boundary

A functionality is a bounded, independently testable capability that:

- accepts defined inputs;
- produces defined outputs or postconditions;
- declares whether it is read-only, mutates deployment state, mutates live FDB,
  changes accepted FDB authority, or only writes derived evidence;
- identifies its required predecessor state;
- has deterministic failure semantics;
- can be traced to one or more operator operations and contract tests.

A script, Docker command, Coolify API request, FDB CLI command, JSON file, YAML
record, or Python function is not automatically a functionality. Those are
possible implementations or evidence carriers.

### 2.2 Reuse rule

A shared functionality keeps the same stable ID wherever it appears.

Operation sections repeat shared functionality IDs so every operation remains
readable as a complete pipeline. Operation-specific use may narrow inputs,
targets, verification assertions, or selected services, but MUST NOT weaken the
canonical functionality contract.

### 2.3 No hidden-functionality rule

An implementation MUST NOT:

- invoke a mutating capability absent from the operation pipeline;
- reinterpret desired topology during `do`;
- widen service, host, coordinator, configuration, or secret scope after
  `prep`;
- silently convert `reconcile` into membership change;
- silently convert service addition into coordinator addition;
- silently convert service removal into coordinator replacement;
- mutate Hub services from any FDB Control functionality;
- mutate Mother or Besu state from any FDB Control functionality;
- update accepted FDB topology before operation-specific finalization proves the
  intended postcondition;
- treat transport success, container health, or a deployment API success code as
  proof of FDB convergence.

### 2.4 Functional status

| Status | Meaning |
|---|---|
| `specified` | The governing documents define enough behavior to implement and test safely |
| `surface-open` | Functional behavior is defined, but CLI spelling or presentation remains open |
| `contract-open` | The capability is required, but a safety-critical contract is not yet sufficiently frozen |
| `conditional` | Required only when the prepared operation meets the stated condition |
| `legacy-reusable` | Existing code appears reusable in part, but must be wrapped by the new contract and tests |

### 2.5 Effect classes

| Class | Effect |
|---|---|
| `read-only` | Reads and reports; creates no live or accepted-state mutation |
| `derived-local` | Writes deterministic derived evidence or consumer-contract material without changing accepted FDB authority |
| `operation-ledger` | Writes prepared-operation, stage, scope, retry, or evidence records without changing FDB topology |
| `live-deployment` | Creates, updates, starts, stops, or removes deployment resources that host FDB services |
| `live-fdb` | Changes live FoundationDB state, configuration, coordinator membership, or data-placement participation |
| `accepted-authority` | Changes accepted FDB topology/configuration/retirement authority after verified finalization |

### 2.6 Shared-private dependency rule

FDB Control shares the canonical private infrastructure source used by Mother:

```text
runtime/state/mother/identity.private.yaml
```

This shared dependency is infrastructure input, not Mother authority.

A functionality may read private infrastructure bindings needed to reach a
host or deployment controller. It MUST NOT:

- require Mother to be running;
- read Mother journals or topology as FDB authority;
- derive FDB service membership from Besu membership;
- copy private token/key bytes into ordinary evidence or consumer contracts;
- rewrite shared privates as a side effect of ordinary FDB operations.

### 2.7 Automatic add-service placement contract

The normal `add-service` operator input is the network plus one logical service
identity. The identity MUST match:

```text
<network><single-lowercase-placement-token>-fdb<positive-ordinal>
```

The placement token resolves against the canonical Mother private-state controller
registry at `networks.<network>.coolify.controllers`. Resolution succeeds only
when exactly one configured controller exists with the canonical identity
`coolify-<token>`.

For the resolved controller, FDB Control selects the first non-placeholder usable value
from these keys in order:

```text
fdb_vpn_ip
vpn_ip
private_vpn_ip
wireguard_ip
tailscale_ip
```

Loopback, unspecified, whitespace-bearing, path-like, or otherwise unusable
values are rejected. The FDB port allocator starts at `4550` and chooses the
first port not already occupied by an accepted FDB service on the same resolved controller placement.
The resulting host/controller identity, address, port, endpoint, placement token, and non-secret
address-source reference are frozen by `prep` and are not recomputed by `do` or
`finalize`.

This contract closes the normal add-service host/address/port inference surface;
it does not imply that every future operation must derive placement from service
naming.


## 3. Stable functionality registry

The registry defines canonical functionality IDs and responsibilities.
Operation sections below define stage placement and operation-specific use.

### 3.1 Private infrastructure and host resolution

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-PRIV-001` | Load and validate the shared `runtime/state/mother/identity.private.yaml` document without exposing secret values | `specified` |
| `FDB-OF-PRIV-002` | Resolve a host identity to its controller/deployment binding and exact FDB-routable private/VPN address using the frozen shared-privates priority rule | `specified` |
| `FDB-OF-PRIV-003` | Validate that every prepared target host has the private bindings required by the requested operation | `specified` |
| `FDB-OF-PRIV-004` | Produce stable non-secret references to private bindings for evidence and causality | `specified` |
| `FDB-OF-PRIV-005` | Redact controller tokens, credentials, TLS private material, and other secret bytes from output/evidence | `specified` |

### 3.2 Accepted state and operation observation

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-OBS-001` | Load and validate the accepted logical FDB cluster identity and accepted topology/configuration state | `specified` |
| `FDB-OF-OBS-002` | Inspect active or incomplete FDB Control operation state, stage, frozen target, scopes, and retry identity | `specified` |
| `FDB-OF-OBS-003` | Inventory deployed FDB service resources and map each service to host placement and deployment identity | `specified` |
| `FDB-OF-OBS-004` | Probe runtime service/process state without treating process liveness as proof of cluster participation | `specified` |
| `FDB-OF-OBS-005` | Query FoundationDB's live cluster status and normalize cluster identity, roles, process addresses, recovery state, and data availability | `specified` |
| `FDB-OF-OBS-006` | Resolve observed FDB services/processes to accepted service identities without inferring membership from host count | `specified` |
| `FDB-OF-OBS-007` | Determine observed coordinator membership and coordinator reachability | `specified` |
| `FDB-OF-OBS-008` | Determine configured redundancy/storage policy and current redundancy/degraded state from FDB evidence | `specified` |
| `FDB-OF-OBS-009` | Calculate independent host/failure-domain count separately from service/process count | `specified` |
| `FDB-OF-OBS-010` | Run a bounded database usability transaction probe against the intended logical cluster | `contract-open` |
| `FDB-OF-OBS-011` | Classify accepted-vs-observed drift, missing services, unexpected services, unreachable services, and unknown mappings | `specified` |
| `FDB-OF-OBS-012` | Calculate current failure-tolerance conclusions only where they are supported by FDB and placement evidence | `specified` |
| `FDB-OF-OBS-013` | Derive allowed next operator actions from current operation and FDB state | `specified` |
| `FDB-OF-OBS-014` | Export deterministic inspection/evidence snapshots with hashes and secret redaction | `specified` |

### 3.3 Operation control and forward-only lifecycle

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-CTL-001` | Parse and validate explicit operator intent for one canonical FDB operation | `specified` |
| `FDB-OF-CTL-002` | Freeze exact service, host, coordinator, configuration, and cluster targets during `prep` | `specified` |
| `FDB-OF-CTL-003` | Calculate ordered functional dependencies and preconditions for the frozen target | `specified` |
| `FDB-OF-CTL-004` | Declare logical mutation scopes and detect conflicting active/incomplete FDB operations | `specified` |
| `FDB-OF-CTL-005` | Write the immutable prepared operation record with accepted/observed prestate hashes and frozen target | `specified` |
| `FDB-OF-CTL-006` | Revalidate frozen assumptions before each mutating dispatch without reinterpreting intent | `specified` |
| `FDB-OF-CTL-007` | Advance durable operation stage/progress state after independently verified milestones | `specified` |
| `FDB-OF-CTL-008` | Preserve idempotent request/mutation identity so exact `do` or `finalize` can resume after interruption | `specified` |
| `FDB-OF-CTL-009` | Refuse a new conflicting mutation while an incomplete operation still owns overlapping scope | `specified` |
| `FDB-OF-CTL-010` | Close and release operation scopes only after verified terminal finalization | `specified` |
| `FDB-OF-CTL-011` | Classify an interrupted operation into retry-`do`, retry-`finalize`, reconcile, or explicit forward-supersession work | `specified` |
| `FDB-OF-CTL-012` | Record explicit supersession of an abandoned frozen target before another overlapping target may proceed | `contract-open` |

### 3.4 Network and service-address correctness

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-NET-001` | Validate service listen/advertised address syntax and uniqueness and allocate the first unused accepted host-local FDB port starting at 4550 for normal add-service | `specified` |
| `FDB-OF-NET-002` | Prove that addresses intended for cross-host FDB communication are not container-local-only addresses | `specified` |
| `FDB-OF-NET-003` | Probe required host-to-service FDB reachability before and after mutation | `specified` |
| `FDB-OF-NET-004` | Determine Hub-reachable coordinator/bootstrap addresses for the consumer contract without discovering Hub membership | `specified` |
| `FDB-OF-NET-005` | Validate TLS/transport references and reachability when transport security is enabled | `contract-open` |

### 3.5 Deployment service lifecycle

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-SVC-001` | Resolve exact FDB service deployment identity independently from host identity; for normal add-service, parse `<network><placement>-fdb<N>` and derive one configured host from the placement token | `specified` |
| `FDB-OF-SVC-002` | Capture current service deployment/runtime descriptor for evidence and retry reconciliation | `specified` |
| `FDB-OF-SVC-003` | Render the exact intended deployment descriptor for one FDB service from frozen topology and private host binding | `specified` |
| `FDB-OF-SVC-004` | Create or update an FDB service resource on the selected host without affecting sibling FDB services | `specified` |
| `FDB-OF-SVC-005` | Start/deploy or resume the intended service resource idempotently | `specified` |
| `FDB-OF-SVC-006` | Verify deployment health and exact service identity while keeping deployment health distinct from FDB participation | `specified` |
| `FDB-OF-SVC-007` | Stop/withdraw a selected FDB service after FDB-level safety prerequisites are proved | `specified` |
| `FDB-OF-SVC-008` | Remove/archive the selected service deployment resource without implying host retirement or sibling removal | `specified` |
| `FDB-OF-SVC-009` | Restore a missing accepted service deployment from accepted topology during `reconcile` | `specified` |

### 3.6 FDB cluster identity, bootstrap, and participation

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-FDB-001` | Generate or accept the explicit new logical cluster identity only for a proven `create-cluster` birth | `specified` |
| `FDB-OF-FDB-002` | Materialize the cluster connection seed/cluster-file content required to bootstrap intended FDB services | `specified` |
| `FDB-OF-FDB-003` | Establish a new unconfigured FDB cluster from the frozen initial service/coordinator topology | `surface-open` |
| `FDB-OF-FDB-004` | Attach a newly deployed service/process to the existing logical cluster using the accepted cluster identity | `specified` |
| `FDB-OF-FDB-005` | Verify that a service/process participates in the intended cluster rather than another reachable cluster | `specified` |
| `FDB-OF-FDB-006` | Observe and verify cluster recovery/convergence after service membership or configuration change | `contract-open` |
| `FDB-OF-FDB-007` | Validate that configured redundancy is supportable by current accepted host/failure-domain topology | `specified` |
| `FDB-OF-FDB-008` | Validate that current live redundancy/data placement satisfies finalization requirements | `contract-open` |
| `FDB-OF-FDB-009` | Prepare a service for safe withdrawal by moving/excluding affected FDB responsibility as required by the final removal policy | `contract-open` |
| `FDB-OF-FDB-010` | Verify that withdrawal of a selected service will not violate the frozen availability/redundancy safety contract | `contract-open` |
| `FDB-OF-FDB-011` | Remove/exclude the selected service address/process from intended live FDB participation where required | `contract-open` |
| `FDB-OF-FDB-012` | Detect stale exclusion or partial participation state relevant to retry/reconcile | `specified` |

### 3.7 Coordinator management

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-COORD-001` | Resolve requested coordinator service identities to explicit reachable FDB coordinator endpoints | `specified` |
| `FDB-OF-COORD-002` | Validate requested coordinator-set policy, uniqueness, odd/even policy if configured, and failure-domain consequences | `specified` |
| `FDB-OF-COORD-003` | Apply the exact frozen coordinator set through supported FDB coordination mechanisms | `specified` |
| `FDB-OF-COORD-004` | Verify that the live cluster reports the exact intended coordinator set and remains available | `specified` |
| `FDB-OF-COORD-005` | Derive resulting cluster connection information after coordinator change | `specified` |
| `FDB-OF-COORD-006` | Derive and freeze the topology coordinator overlay from resulting services, current coordinators, and distinct failure domains | `specified` |

### 3.8 FDB-native configuration

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-CFG-001` | Parse and validate supported FDB-native configuration intent | `contract-open` |
| `FDB-OF-CFG-002` | Prove the accepted/observed service and failure-domain topology can support the requested configuration | `specified` |
| `FDB-OF-CFG-003` | Apply the exact frozen FDB-native configuration | `contract-open` |
| `FDB-OF-CFG-004` | Verify live FDB reports the requested configuration and required post-change availability | `contract-open` |

### 3.9 Accepted topology/configuration authority

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-AUTH-001` | Construct canonical accepted cluster state from the verified finalized poststate | `specified` |
| `FDB-OF-AUTH-002` | Atomically publish a new accepted topology/configuration generation after finalization proof | `specified` |
| `FDB-OF-AUTH-003` | Preserve accepted service membership when observed services disappear without explicit mutation | `specified` |
| `FDB-OF-AUTH-004` | Preserve cluster lineage identity across add/remove/replace/reconcile operations | `specified` |
| `FDB-OF-AUTH-005` | Mark a logical cluster lineage explicitly retired without equating runtime absence with retirement | `specified` |
| `FDB-OF-AUTH-006` | Reject ordinary mutations against a retired lineage unless a future explicit reactivation contract exists | `specified` |

### 3.10 Consumer contract

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-CON-001` | Derive the Hub-facing FDB consumer contract from accepted/live FDB facts and network reachability rules | `specified` |
| `FDB-OF-CON-002` | Canonically serialize and hash the consumer contract while excluding secret bytes | `specified` |
| `FDB-OF-CON-003` | Compare pre/post consumer contracts semantically and by canonical hash | `specified` |
| `FDB-OF-CON-004` | Advance consumer-contract generation only when the canonical Hub-relevant contract changes | `specified` |
| `FDB-OF-CON-005` | Emit deterministic contract evidence/artifact for the separate Hub-FDB rectifier | `specified` |
| `FDB-OF-CON-006` | Report whether Hub-FDB rectification is required without mutating Hub services | `specified` |
| `FDB-OF-CON-007` | Represent FDB client/server/API compatibility requirements in the consumer contract | `contract-open` |
| `FDB-OF-CON-008` | Represent TLS/transport material references without embedding secret private material | `contract-open` |
| `FDB-OF-CON-009` | Represent Hub storage namespace/application scope inputs owned by this dependency boundary | `contract-open` |
| `FDB-OF-CON-010` | Emit terminal/unavailable consumer state for an explicitly retired cluster | `specified` |

### 3.11 Evidence and reporting

| Functionality ID | Canonical responsibility | Status |
|---|---|---|
| `FDB-OF-EV-001` | Hash and retain accepted and observed prestate snapshots used by a prepared operation | `specified` |
| `FDB-OF-EV-002` | Record frozen target, affected services, hosts, failure domains, coordinator target, and configuration target | `specified` |
| `FDB-OF-EV-003` | Record each verified mutating milestone and unresolved ambiguity without claiming completion early | `specified` |
| `FDB-OF-EV-004` | Record fresh finalized poststate, transaction/usability proof, convergence proof, and accepted-generation result | `specified` |
| `FDB-OF-EV-005` | Record consumer-contract pre/post hashes and Hub-FDB rectification requirement | `specified` |
| `FDB-OF-EV-006` | Produce human-readable operator summary and machine-readable structured output from the same verified facts | `specified` |


## 4. Shared lifecycle functionality

The operator lifecycle for every mutating operation is:

```text
prep -> do -> finalize
```

There is no rollback functionality family.

### 4.1 `prep` shared pipeline

Every mutating `prep` uses, in order where applicable:

1. `FDB-OF-CTL-001` parse operator intent;
2. `FDB-OF-PRIV-001` load shared privates;
3. `FDB-OF-OBS-001` load accepted FDB state;
4. `FDB-OF-OBS-002` inspect active/incomplete operation state;
5. `FDB-OF-OBS-003` through `FDB-OF-OBS-012` establish observed reality required by the operation;
6. `FDB-OF-CTL-002` freeze exact target;
7. operation-specific safety and planning functions;
8. `FDB-OF-CTL-003` calculate dependency order;
9. `FDB-OF-CTL-004` acquire logical scope/no-conflict decision;
10. `FDB-OF-EV-001` and `FDB-OF-EV-002` freeze prestate/target evidence;
11. `FDB-OF-CTL-005` write immutable prepared operation state.

`prep` MUST NOT mutate live FDB or deployment resources.

### 4.2 `do` shared pipeline

Every mutating `do`:

1. loads the exact prepared operation;
2. uses `FDB-OF-CTL-006` to revalidate frozen assumptions;
3. uses `FDB-OF-CTL-008` to preserve retry identity;
4. performs only the operation-specific mutating functions frozen by `prep`;
5. independently observes each completed milestone;
6. uses `FDB-OF-CTL-007` and `FDB-OF-EV-003` to record verified progress;
7. stops with explicit incomplete state when an assumption or postcondition cannot be proved.

Rerunning `do` MUST resume or reconcile the exact same frozen target. It MUST NOT
invent a different service, host, coordinator set, or configuration target.

### 4.3 `finalize` shared pipeline

Every mutating `finalize`:

1. freshly observes deployment and FDB state;
2. runs operation-specific postcondition verification;
3. runs `FDB-OF-OBS-010` bounded database usability proof when the cluster is expected to remain usable;
4. derives the post-operation consumer contract through `FDB-OF-CON-001` through `FDB-OF-CON-006`;
5. commits accepted FDB authority only after all required postconditions pass;
6. writes terminal operation/evidence state;
7. releases operation scopes only after terminal proof.

A successful API request, healthy container, or even a visible FDB process is not
sufficient for finalization.

### 4.4 Forward-only interruption handling

If `do` or `finalize` is interrupted:

```text
inspect actual state
  -> classify exact frozen-target progress
  -> retry exact do
     or retry exact finalize
     or reconcile accepted topology
     or explicitly supersede the abandoned target and begin a new forward operation
```

No functionality restores distributed reality by assumption.

`FDB-OF-CTL-012` remains `contract-open` until the exact supersession record and
scope-release rules are frozen. Until then, an overlapping abandoned target MUST
block a new conflicting mutation rather than being silently replaced.


## 5. Operation: `inspect`

Operation ID: `FDB-OP-INSPECT`

Class: read-only observation

Lifecycle: one shot

### 5.1 Functional pipeline

| Order | Functionality | Operation-specific use | Effect |
|---:|---|---|---|
| 1 | `FDB-OF-PRIV-001` | Load shared infrastructure bindings for known hosts without exposing secrets | read-only |
| 2 | `FDB-OF-OBS-001` | Load accepted cluster/topology/configuration state | read-only |
| 3 | `FDB-OF-OBS-002` | Report active/incomplete operation and allowed continuation | read-only |
| 4 | `FDB-OF-OBS-003` | Inventory deployed FDB service resources | read-only |
| 5 | `FDB-OF-OBS-004` | Probe runtime process state | read-only |
| 6 | `FDB-OF-OBS-005` | Query live FDB status | read-only |
| 7 | `FDB-OF-OBS-006` | Map observed processes/services to accepted identities | read-only |
| 8 | `FDB-OF-OBS-007` | Determine coordinator set/reachability | read-only |
| 9 | `FDB-OF-OBS-008` | Determine redundancy/degraded state | read-only |
| 10 | `FDB-OF-OBS-009` | Calculate host/failure-domain count separately from service count | read-only |
| 11 | `FDB-OF-OBS-010` | Perform bounded database usability transaction probe | read-only |
| 12 | `FDB-OF-OBS-011` | Classify accepted-vs-observed drift | read-only |
| 13 | `FDB-OF-OBS-012` | Derive supported failure-tolerance conclusion | read-only |
| 14 | `FDB-OF-CON-001` through `FDB-OF-CON-003` | Derive current Hub-facing consumer contract and hash | read-only / derived-local |
| 15 | `FDB-OF-OBS-013` | Produce allowed next operation guidance | read-only |
| 16 | `FDB-OF-OBS-014`, `FDB-OF-EV-006` | Emit redacted human/machine inspection result | derived-local |

### 5.2 Required output distinctions

`inspect` MUST keep these facts distinct:

```text
accepted topology
observed deployment topology
observed FDB cluster participation
coordinator set
service count
independent host/failure-domain count
FDB configuration
current availability/degradation
active operation state
consumer-contract identity/hash
unknown/unprovable state
```

### 5.3 Acceptance

`inspect` is accepted when every reported conclusion is traceable to current
observations or accepted state and every unavailable conclusion is labeled
unknown rather than guessed.


## 6. Operation: `consumer-contract`

Operation ID: `FDB-OP-CONSUMER-CONTRACT`

Class: read-only derivation

Lifecycle: one shot

### 6.1 Functional pipeline

| Order | Functionality | Operation-specific use | Effect |
|---:|---|---|---|
| 1 | `FDB-OF-PRIV-001` through `FDB-OF-PRIV-004` | Resolve non-secret infrastructure facts needed to derive reachable FDB addresses | read-only |
| 2 | `FDB-OF-OBS-001` | Load accepted cluster identity/topology | read-only |
| 3 | `FDB-OF-OBS-005`, `FDB-OF-OBS-007` | Confirm live cluster/coordinator facts needed by the contract | read-only |
| 4 | `FDB-OF-NET-004` | Select Hub-reachable bootstrap/coordinator addresses without enumerating Hub membership | read-only |
| 5 | `FDB-OF-CON-001` | Derive canonical consumer contract | read-only |
| 6 | `FDB-OF-CON-002` | Canonically serialize/hash | derived-local |
| 7 | `FDB-OF-CON-004` | Determine contract generation from prior canonical contract | read-only |
| 8 | `FDB-OF-CON-005` | Emit deterministic contract artifact/evidence | derived-local |
| 9 | `FDB-OF-CON-006` | Report Hub-FDB rectification requirement | derived-local |

### 6.2 Functional boundary

This operation MUST NOT:

- discover or mutate Hub membership;
- deploy/restart Hub services;
- copy private controller tokens or TLS private keys into the contract;
- derive FDB membership from Hub topology;
- change accepted FDB topology merely because the derived contract differs.


## 7. Operation: `create-cluster`

Operation ID: `FDB-OP-CREATE-CLUSTER`

Class: authoritative FDB birth

### 7.1 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001` | Parse new-cluster intent, initial services, hosts, coordinators, and FDB configuration |
| 2 | `FDB-OF-PRIV-001` through `FDB-OF-PRIV-003` | Prove every target host has usable private deployment bindings |
| 3 | `FDB-OF-OBS-001` | Prove no accepted live lineage already occupies the requested logical cluster identity |
| 4 | `FDB-OF-OBS-003` through `FDB-OF-OBS-005` | Detect existing deployment/FDB evidence that could indicate an already-existing cluster |
| 5 | `FDB-OF-CTL-002` | Freeze cluster identity, initial service set, host placement, addresses, coordinator set, and configuration |
| 6 | `FDB-OF-NET-001` through `FDB-OF-NET-003` | Validate cross-host address model and required reachability |
| 7 | `FDB-OF-COORD-001`, `FDB-OF-COORD-002` | Validate initial explicit coordinator set |
| 8 | `FDB-OF-CFG-001`, `FDB-OF-CFG-002` | Validate initial supported FDB configuration and topology sufficiency |
| 9 | `FDB-OF-CTL-003`, `FDB-OF-CTL-004` | Freeze dependency order and acquire birth scope |
| 10 | `FDB-OF-EV-001`, `FDB-OF-EV-002`, `FDB-OF-CTL-005` | Persist birth prestate and immutable prepared target |

`prep` MUST stop if an existing cluster identity cannot be safely distinguished
from an unborn network.

### 7.2 `do` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-006`, `FDB-OF-CTL-008` | Revalidate and resume exact birth target |
| 2 | `FDB-OF-FDB-001`, `FDB-OF-FDB-002` | Establish frozen cluster identity/connection seed |
| 3 | `FDB-OF-SVC-001` through `FDB-OF-SVC-006` | Create/deploy every initial service independently, including multiple services on one host when requested |
| 4 | `FDB-OF-NET-003` | Verify required inter-service reachability |
| 5 | `FDB-OF-FDB-003` | Establish the new FDB cluster using only the frozen initial topology |
| 6 | `FDB-OF-COORD-003` | Establish the exact frozen coordinator set if not already part of bootstrap |
| 7 | `FDB-OF-CFG-003` | Apply frozen initial FDB-native configuration |
| 8 | `FDB-OF-FDB-005`, `FDB-OF-FDB-006` | Verify all intended services belong to the intended cluster and convergence is progressing/complete |
| 9 | `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Persist verified progress milestones |

### 7.3 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-003` through `FDB-OF-OBS-010` | Freshly prove exact intended services, cluster identity, coordinators, configuration, redundancy, and usability |
| 2 | `FDB-OF-FDB-007`, `FDB-OF-FDB-008` | Prove configured redundancy/failure-domain requirements are satisfied |
| 3 | `FDB-OF-COORD-004`, `FDB-OF-CFG-004` | Prove exact intended coordinator/configuration state |
| 4 | `FDB-OF-CON-001` through `FDB-OF-CON-006` | Derive first consumer contract and rectification requirement |
| 5 | `FDB-OF-AUTH-001`, `FDB-OF-AUTH-002` | Publish first accepted FDB generation only after proof |
| 6 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Persist terminal evidence and close operation |


## 8. Operation: `add-service`

Operation ID: `FDB-OP-ADD-SERVICE`

Class: authoritative service membership expansion

### 8.1 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001`, `FDB-OF-SVC-001` | Parse the logical service identity, validate its network prefix, and extract its placement token/ordinal |
| 2 | `FDB-OF-PRIV-001` through `FDB-OF-PRIV-003` | Resolve the placement token to exactly one configured Coolify host, its deployment binding, and its FDB-routable private/VPN address |
| 3 | `FDB-OF-OBS-001` through `FDB-OF-OBS-012` | Establish accepted/live cluster state and current failure domains |
| 4 | `FDB-OF-NET-001` | Allocate the first unused accepted FDB port on the resolved host, beginning at 4550, and reject endpoint collisions |
| 5 | `FDB-OF-COORD-006`, `FDB-OF-CTL-002` | Derive and freeze one new service plus the source/target coordinator overlay for the resulting failure-domain topology |
| 6 | `FDB-OF-NET-002`, `FDB-OF-NET-003` | Validate routability/reachability without accepting container-local addressing |
| 7 | `FDB-OF-SVC-001` | Prove new service identity does not collide with an accepted or unrelated deployment |
| 8 | `FDB-OF-CTL-003` through `FDB-OF-CTL-005` | Freeze dependencies/scope and prepared record |
| 9 | `FDB-OF-EV-001`, `FDB-OF-EV-002` | Record exact prestate and target |

Adding a service MUST NOT make that service a coordinator merely because it was added. Any coordinator change is a separately frozen sub-transition derived from the resulting distinct failure domains.

### 8.2 `do` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-006`, `FDB-OF-CTL-008` | Resume exact frozen add |
| 2 | `FDB-OF-SVC-003` through `FDB-OF-SVC-006` | Create/deploy exact service on selected host without touching siblings |
| 3 | `FDB-OF-NET-003` | Verify cluster-network reachability |
| 4 | `FDB-OF-FDB-004`, `FDB-OF-FDB-005` | Join/identify service in existing logical cluster |
| 5 | `FDB-OF-FDB-006` | Prove new-service participation/convergence through the source coordinator set |
| 6 | `FDB-OF-COORD-003`, `FDB-OF-COORD-004` | Conditional: apply and prove the exact frozen derived coordinator overlay, capturing actual rewritten connection information |
| 7 | `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Persist verified progress |

### 8.3 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-003` through `FDB-OF-OBS-010` | Freshly prove service deployment, participation, cluster usability, and exact frozen coordinator overlay |
| 2 | `FDB-OF-FDB-005`, `FDB-OF-FDB-006`, `FDB-OF-FDB-008` | Prove service belongs to correct cluster and final convergence/redundancy requirements |
| 3 | `FDB-OF-CON-001` through `FDB-OF-CON-006` | Determine whether topology expansion changed Hub contract |
| 4 | `FDB-OF-AUTH-001`, `FDB-OF-AUTH-002`, `FDB-OF-AUTH-004` | Advance accepted topology, preserving cluster description and accepting the actual FDB-rewritten cluster ID exactly when coordinators changed |
| 5 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Record terminal proof and close |


## 9. Operation: `remove-service`

Operation ID: `FDB-OP-REMOVE-SERVICE`

Class: authoritative service membership contraction

### 9.1 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001` | Parse exact service to remove |
| 2 | `FDB-OF-OBS-001` through `FDB-OF-OBS-012` | Establish accepted/live state, placement, redundancy, coordinator role, and failure domains |
| 3 | `FDB-OF-COORD-006` | Derive source/target coordinator overlay from the surviving services and failure domains |
| 4 | `FDB-OF-FDB-010` | Prove intended removal can satisfy frozen availability/redundancy safety contract |
| 5 | `FDB-OF-CTL-002` | Freeze exact post-removal service topology and exact derived coordinator target |
| 6 | `FDB-OF-CTL-003` through `FDB-OF-CTL-005` | Freeze dependencies/scope/prepared record |
| 7 | `FDB-OF-EV-001`, `FDB-OF-EV-002` | Record removal prestate and target |

### 9.2 `do` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-006`, `FDB-OF-CTL-008` | Resume exact frozen removal |
| 2 | `FDB-OF-COORD-003`, `FDB-OF-COORD-004` | Conditional: move and prove the exact frozen coordinator overlay before any destructive withdrawal |
| 3 | `FDB-OF-FDB-009` | Prepare/evacuate selected service for safe withdrawal as required by FDB policy |
| 4 | `FDB-OF-FDB-010` | Recheck safety after evacuation/convergence |
| 5 | `FDB-OF-SVC-007` | Stop/withdraw selected service only after FDB safety proof |
| 6 | `FDB-OF-FDB-011` | Complete any required exclusion/removal from live participation |
| 7 | `FDB-OF-SVC-008` | Remove/archive selected deployment resource without touching siblings/host |
| 8 | `FDB-OF-FDB-006` | Observe cluster convergence after withdrawal |
| 9 | `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Persist verified progress |

### 9.3 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-003` through `FDB-OF-OBS-010` | Prove target service absent, siblings preserved, cluster available, and resulting coordinator state exact |
| 2 | `FDB-OF-FDB-008`, `FDB-OF-COORD-004` | Prove required redundancy and coordinator state |
| 3 | `FDB-OF-CON-001` through `FDB-OF-CON-006` | Derive post-removal consumer contract and rectification requirement |
| 4 | `FDB-OF-AUTH-001`, `FDB-OF-AUTH-002`, `FDB-OF-AUTH-004` | Commit accepted membership contraction |
| 5 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Record terminal proof and close |


## 10. Operation: `replace-service`

Operation ID: `FDB-OP-REPLACE-SERVICE`

Class: authoritative service substitution

### 10.1 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001` | Parse old service, replacement service, replacement host, and addressing |
| 2 | `FDB-OF-PRIV-001` through `FDB-OF-PRIV-003` | Resolve replacement host access |
| 3 | `FDB-OF-OBS-001` through `FDB-OF-OBS-012` | Establish old service state, surviving topology, failure domains, and coordinators |
| 4 | `FDB-OF-COORD-006` | Derive and freeze the coordinator overlay for the replacement post-topology |
| 5 | `FDB-OF-CTL-002` | Freeze causal old->new replacement target and ordering |
| 6 | `FDB-OF-NET-001` through `FDB-OF-NET-003` | Validate replacement address/reachability |
| 7 | `FDB-OF-FDB-010` | Prove a safe replacement path exists under current topology |
| 8 | `FDB-OF-CTL-003` through `FDB-OF-CTL-005`, `FDB-OF-EV-001`, `FDB-OF-EV-002` | Freeze operation and evidence |

### 10.2 `do` functionalities

Replacement MUST add and prove the replacement before retiring the old service
unless the old service is already absent and the prepared safety contract
explicitly handles that case.

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-006`, `FDB-OF-CTL-008` | Resume exact frozen replacement |
| 2 | `FDB-OF-SVC-003` through `FDB-OF-SVC-006` | Deploy replacement service |
| 3 | `FDB-OF-NET-003`, `FDB-OF-FDB-004`, `FDB-OF-FDB-005` | Join and verify replacement in intended cluster |
| 4 | `FDB-OF-FDB-006` | Prove replacement convergence threshold before old-service retirement |
| 5 | `FDB-OF-COORD-003`, `FDB-OF-COORD-004` | Conditional: establish prepared resulting coordinator set |
| 6 | `FDB-OF-FDB-009`, `FDB-OF-FDB-010` | Prepare old service for safe withdrawal |
| 7 | `FDB-OF-SVC-007`, `FDB-OF-FDB-011`, `FDB-OF-SVC-008` | Withdraw/remove old service only after replacement proof |
| 8 | `FDB-OF-FDB-006` | Observe final convergence |
| 9 | `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Persist verified progress |

### 10.3 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-003` through `FDB-OF-OBS-010` | Freshly prove replacement present, old service retired, siblings preserved, and cluster usable |
| 2 | `FDB-OF-FDB-005`, `FDB-OF-FDB-008`, `FDB-OF-COORD-004` | Prove replacement membership, redundancy, and coordinator state |
| 3 | `FDB-OF-CON-001` through `FDB-OF-CON-006` | Derive consumer-contract effect |
| 4 | `FDB-OF-AUTH-001`, `FDB-OF-AUTH-002`, `FDB-OF-AUTH-004` | Commit one accepted replacement transition |
| 5 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Record terminal proof and close |


## 11. Operation: `set-coordinators`

Operation ID: `FDB-OP-SET-COORDINATORS`

Class: authoritative coordinator-set mutation

### 11.1 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001` | Parse complete desired coordinator service set |
| 2 | `FDB-OF-OBS-001`, `FDB-OF-OBS-005`, `FDB-OF-OBS-007`, `FDB-OF-OBS-009` | Establish cluster, current coordinator set, process addresses, and failure domains |
| 3 | `FDB-OF-COORD-001` | Resolve every requested coordinator to explicit endpoint |
| 4 | `FDB-OF-COORD-002` | Validate resulting coordinator policy/failure-domain implications |
| 5 | `FDB-OF-NET-001` through `FDB-OF-NET-004` | Prove coordinator endpoints are routable for FDB and consumer bootstrap use |
| 6 | `FDB-OF-CTL-002` through `FDB-OF-CTL-005`, `FDB-OF-EV-001`, `FDB-OF-EV-002` | Freeze exact desired set and operation |

### 11.2 `do` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-006`, `FDB-OF-CTL-008` | Resume exact set change |
| 2 | `FDB-OF-COORD-003` | Apply exact frozen coordinator set |
| 3 | `FDB-OF-COORD-004` | Verify cluster reports exact set and remains available |
| 4 | `FDB-OF-COORD-005` | Derive resulting cluster connection information |
| 5 | `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Persist verified progress |

### 11.3 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-005`, `FDB-OF-OBS-007`, `FDB-OF-OBS-010` | Freshly prove exact coordinators and usable cluster |
| 2 | `FDB-OF-CON-001` through `FDB-OF-CON-006` | Derive changed/unchanged consumer contract and rectification flag |
| 3 | `FDB-OF-AUTH-001`, `FDB-OF-AUTH-002` | Commit coordinator state as accepted FDB generation |
| 4 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Record terminal proof and close |


## 12. Operation: `configure-cluster`

Operation ID: `FDB-OP-CONFIGURE-CLUSTER`

Class: authoritative FDB-native configuration mutation

### 12.1 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001`, `FDB-OF-CFG-001` | Parse exact supported FDB-native configuration target |
| 2 | `FDB-OF-OBS-001`, `FDB-OF-OBS-005`, `FDB-OF-OBS-008`, `FDB-OF-OBS-009` | Establish current config and failure-domain topology |
| 3 | `FDB-OF-CFG-002`, `FDB-OF-FDB-007` | Prove topology supports requested policy |
| 4 | `FDB-OF-CTL-002` through `FDB-OF-CTL-005`, `FDB-OF-EV-001`, `FDB-OF-EV-002` | Freeze exact configuration target and operation |

### 12.2 `do` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-006`, `FDB-OF-CTL-008` | Resume exact frozen configuration change |
| 2 | `FDB-OF-CFG-003` | Apply only the prepared FDB-native settings |
| 3 | `FDB-OF-FDB-006`, `FDB-OF-CFG-004` | Observe recovery/convergence to requested config |
| 4 | `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Persist verified progress |

### 12.3 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-005`, `FDB-OF-OBS-008`, `FDB-OF-OBS-010` | Freshly prove requested config, availability, and usability |
| 2 | `FDB-OF-FDB-008`, `FDB-OF-CFG-004` | Prove redundancy/data-placement postcondition |
| 3 | `FDB-OF-CON-001` through `FDB-OF-CON-006` | Derive consumer-contract effect |
| 4 | `FDB-OF-AUTH-001`, `FDB-OF-AUTH-002` | Commit accepted configuration generation |
| 5 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Record terminal proof and close |


## 13. Operation: `reconcile`

Operation ID: `FDB-OP-RECONCILE`

Class: restoration to already accepted intent

### 13.1 Defining functional rule

`reconcile` may restore missing or drifted deployment/runtime state only when the
accepted topology remains the intended target.

It MUST NOT:

- add a new accepted service identity;
- remove an accepted service identity;
- replace one accepted service with another;
- change coordinators unless restoring the already accepted coordinator set;
- change FDB-native configuration unless restoring the already accepted config.

### 13.2 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001` | Parse optional narrowed reconcile scope; default target remains accepted topology |
| 2 | `FDB-OF-PRIV-001` through `FDB-OF-PRIV-003` | Resolve accepted host bindings needed for restoration |
| 3 | `FDB-OF-OBS-001` through `FDB-OF-OBS-012` | Compare accepted topology/config with observed deployment/FDB reality |
| 4 | `FDB-OF-OBS-011` | Classify only restorable drift versus intentional-topology-change requirement |
| 5 | `FDB-OF-CTL-002` | Freeze exact restoration actions that preserve accepted intent |
| 6 | `FDB-OF-CTL-003` through `FDB-OF-CTL-005`, `FDB-OF-EV-001`, `FDB-OF-EV-002` | Freeze operation and evidence |

### 13.3 `do` functionalities

Depending on prepared drift, `do` may use:

| Functionality | Reconcile use |
|---|---|
| `FDB-OF-SVC-009` | Restore a missing accepted service deployment |
| `FDB-OF-SVC-003` through `FDB-OF-SVC-006` | Correct drifted deployment descriptor/runtime state for an accepted service |
| `FDB-OF-NET-001` through `FDB-OF-NET-003` | Restore required accepted addressing/reachability |
| `FDB-OF-FDB-004`, `FDB-OF-FDB-005`, `FDB-OF-FDB-012` | Reattach/verify an accepted service or clear partial participation state without changing membership intent |
| `FDB-OF-COORD-003`, `FDB-OF-COORD-004` | Restore the already accepted coordinator set only |
| `FDB-OF-CFG-003`, `FDB-OF-CFG-004` | Restore the already accepted FDB config only |
| `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Record verified restoration progress |

### 13.4 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-003` through `FDB-OF-OBS-011` | Prove observed state again conforms to accepted intent |
| 2 | `FDB-OF-OBS-010`, `FDB-OF-FDB-008` | Prove usability and required redundancy where cluster should be available |
| 3 | `FDB-OF-CON-001` through `FDB-OF-CON-006` | Re-derive consumer contract; accepted FDB membership remains unchanged |
| 4 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Record terminal restoration proof and close |

`reconcile` does not call `FDB-OF-AUTH-002` merely because it repaired live
state. Accepted topology/config authority changes only if the accepted document
itself must be republished identically for integrity mechanics; such a storage
implementation detail MUST NOT create a semantic topology generation change.


## 14. Operation: `retire-cluster`

Operation ID: `FDB-OP-RETIRE-CLUSTER`

Class: terminal logical lineage retirement

### 14.1 `prep` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-001` | Parse explicit whole-cluster retirement intent and data-retention selection when that contract is closed |
| 2 | `FDB-OF-OBS-001` through `FDB-OF-OBS-012` | Establish cluster identity, accepted membership, live state, and current consumer contract |
| 3 | `FDB-OF-CTL-002` | Freeze terminal lineage target and service set |
| 4 | `FDB-OF-CTL-003` through `FDB-OF-CTL-005`, `FDB-OF-EV-001`, `FDB-OF-EV-002` | Freeze operation and evidence |

### 14.2 `do` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-CTL-006`, `FDB-OF-CTL-008` | Resume exact retirement target |
| 2 | `FDB-OF-SVC-007` | Stop/withdraw every frozen accepted FDB service according to terminal policy |
| 3 | `FDB-OF-SVC-008` | Remove/archive deployment resources when requested by frozen retirement policy |
| 4 | `FDB-OF-CTL-007`, `FDB-OF-EV-003` | Record terminal shutdown progress |

Data-volume destruction is not an implicit functionality of retirement.

### 14.3 `finalize` functionalities

| Order | Functionality | Operation-specific use |
|---:|---|---|
| 1 | `FDB-OF-OBS-003`, `FDB-OF-OBS-004` | Prove intended FDB services are no longer active under the retired lineage |
| 2 | `FDB-OF-AUTH-005`, `FDB-OF-AUTH-006` | Mark lineage explicitly retired and block ordinary future mutation |
| 3 | `FDB-OF-CON-010`, `FDB-OF-CON-002`, `FDB-OF-CON-005` | Emit terminal/unavailable consumer contract state |
| 4 | `FDB-OF-EV-004`, `FDB-OF-EV-005`, `FDB-OF-CTL-010` | Record terminal retirement proof and close |

The exact destructive-data policy remains contract-open and MUST NOT be invented
by implementation.


## 15. Cross-operation functional invariants

### 15.1 Service and host cardinality

Every functionality that reasons about placement MUST support:

```text
one host -> zero FDB services
one host -> one FDB service
one host -> multiple FDB services
```

No function may use host count as service count or service count as independent
failure-domain count.

### 15.2 Coordinator independence

Coordinator membership may change only when `prep` has frozen an exact source
and target set. Normal add/remove/replace derive that target from the resulting
service/failure-domain topology; `set-coordinators` is the explicit override for
a coordinator-only operator intent. No `do` stage may choose a different set.

### 15.3 Consumer-contract independence

Every finalized mutation derives the consumer contract again.

```text
FDB topology changed
    does not imply
consumer contract changed
```

Hub-FDB rectification is required only when the canonical Hub-relevant contract
changes or the cluster becomes explicitly retired/unavailable in a way the
rectifier must consume.

### 15.4 Accepted state versus observed reality

These state domains remain distinct in every functionality:

```text
shared private infrastructure bindings
accepted FDB topology/configuration
observed deployment state
observed FDB live state
active operation state
consumer contract
Hub state
```

No one domain automatically overwrites another.

### 15.5 No Mother lifecycle coupling

FDB functionality may reuse low-level private-infrastructure parsing patterns or
adapters also used by Mother, but it may not require Mother operation state,
Mother topology generation, Mother replica authority, or Besu node membership.


## 16. Functional coverage matrix

Legend:

```text
R = read-only use
P = prep/control use
D = live do-stage mutation
F = finalize/accepted-state use
C = conditional
— = not used
```

| Operation | Privates | Observe | Control | Network | Service lifecycle | FDB participation | Coordinators | FDB config | Accepted authority | Consumer contract | Evidence |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `inspect` | R | R | — | R | R | R | R | R | R | R | R |
| `consumer-contract` | R | R | — | R | — | R | R | R | R | R | R |
| `create-cluster` | P | P/F | P/D/F | P/D/F | D/F | D/F | P/D/F | P/D/F | F | F | P/D/F |
| `add-service` | P | P/F | P/D/F | P/D/F | D/F | D/F | R | R | F | F | P/D/F |
| `remove-service` | R | P/F | P/D/F | R | D/F | P/D/F | C | R | F | F | P/D/F |
| `replace-service` | P | P/F | P/D/F | P/D/F | D/F | P/D/F | C | R | F | F | P/D/F |
| `set-coordinators` | R | P/F | P/D/F | P/F | — | R/F | P/D/F | R | F | F | P/D/F |
| `configure-cluster` | R | P/F | P/D/F | R | — | P/F | R | P/D/F | F | F | P/D/F |
| `reconcile` | P | P/F | P/D/F | C | C | C | C | C | unchanged | F | P/D/F |
| `retire-cluster` | R | P/F | P/D/F | R | D/F | R | R | R | F | F | P/D/F |


## 17. Mutation harness composition

`fdb_mutate_harness.py` is an operator driver over existing operation
functionalities; it is not a new authority class and owns no direct FDB,
Coolify, accepted-state, or coordinator mutation capability.

For `add-service` and `remove-service` it composes the public control surface as:

```text
inspect accepted/live baseline
  -> operation prep
  -> stop at mutation gate unless explicitly authorized
  -> exact do
  -> operation-scoped inspect/proof
  -> exact finalize
  -> final inspect
```

The harness persists only run-local commands, stdout/stderr, decoded JSON, and
resume state under `runtime/state/fdb/harness-runs/`. Its resume state may store
the operation ID and the host/endpoint already returned by prep, but it MUST NOT
become an alternate topology authority.

For `add-service`, the harness passes only the network and service ID to prep and
accepts the placement frozen by the automatic-placement contract in section 2.7.
For `remove-service`, it accepts the placement resolved from accepted topology.
The harness then proves the accepted generation increments by one and that the
cluster description remains unchanged and the final coordinator set equals the
frozen topology-derived target for these service mutations.


## 18. Relationship to the existing experimental FDB deployer

The repository currently contains `tools/coolify_fdb_cluster.py`, whose observed
responsibilities include private-state loading, Coolify binding resolution,
placement parsing, cluster-file rendering, service Compose rendering, Coolify
service create/update/deploy, and plan/apply output.

Those behaviors map only partially to this functionality specification.

Potentially reusable behavior includes:

| Existing behavior | New functionality it may help implement |
|---|---|
| shared private-state parsing | `FDB-OF-PRIV-001` through `FDB-OF-PRIV-004` |
| per-server Coolify binding resolution | `FDB-OF-PRIV-002`, `FDB-OF-SVC-001` |
| IP/address validation | `FDB-OF-NET-001` |
| cluster-file content generation | `FDB-OF-FDB-002`, `FDB-OF-COORD-005` |
| Compose rendering | `FDB-OF-SVC-003` |
| Coolify create/update/deploy | `FDB-OF-SVC-004`, `FDB-OF-SVC-005` |
| redaction | `FDB-OF-PRIV-005` |

The legacy `plan`/`apply` surface does not satisfy the new operator model by
itself. In particular, it does not become authority for:

- accepted-vs-observed topology;
- operation `prep/do/finalize` state;
- forward-only retry ownership;
- service-vs-host cardinality semantics;
- exact coordinator lifecycle;
- FDB convergence proof;
- safe removal/replacement;
- consumer-contract generation/change detection;
- Hub-FDB rectification boundary.

The better implementation cycle may reuse implementation fragments, but it MUST
wrap them behind the functionality and module contracts rather than preserving
legacy `plan`/`apply` semantics as architecture.


## 19. Functional coverage gaps

These gaps come directly from open contracts in `fdb-o.md`. They block only the
mutating functionality that depends on them; they do not justify inventing
behavior.

### 18.1 Contract-open gaps

| Gap ID | Functionality | Missing contract |
|---|---|---|
| `FDB-OF-GAP-001` | `FDB-OF-OBS-010` | Exact bounded transaction/usability probe, timeout, retry limit, and non-destructive keyspace policy |
| `FDB-OF-GAP-002` | `FDB-OF-FDB-006` | Exact evidence that a newly joined/replacement service has converged sufficiently for operation progress/finalization |
| `FDB-OF-GAP-003` | `FDB-OF-FDB-008` | Exact final redundancy/data-placement proof required for each topology/configuration operation |
| `FDB-OF-GAP-004` | `FDB-OF-FDB-009` through `FDB-OF-FDB-011` | Exact FDB exclusion/evacuation/withdrawal semantics and success evidence for safe service removal |
| `FDB-OF-GAP-005` | `FDB-OF-CFG-001`, `FDB-OF-CFG-003`, `FDB-OF-CFG-004` | Initial supported FDB-native configuration set and exact validation/application contracts |
| `FDB-OF-GAP-006` | `FDB-OF-NET-005`, `FDB-OF-CON-008` | TLS/transport material reference, distribution, rotation, and verification model |
| `FDB-OF-GAP-007` | `FDB-OF-CON-007` | Exact client/server/API compatibility representation and compatibility rules |
| `FDB-OF-GAP-008` | `FDB-OF-CON-009` | Exact namespace/application-scope ownership and serialization at the FDB->Hub boundary |
| `FDB-OF-GAP-009` | `FDB-OF-CTL-012` | Explicit forward supersession record, conflict release, and evidence when an original frozen target is abandoned |
| `FDB-OF-GAP-010` | `FDB-OF-AUTH-005`, retirement do-path policy | Exact data-retention versus destructive-volume semantics for `retire-cluster` |
| `FDB-OF-GAP-011` | service/process model | Exact relationship between one accepted FDB service and one or more runtime `fdbserver` processes |
| `FDB-OF-GAP-012` | accepted-state persistence | Exact accepted-topology/configuration and operation-record persistence schemas and atomicity mechanism |
| `FDB-OF-GAP-013` | service descriptor | Exact service descriptor schema beyond the now-specified normal add-service address/port allocation and accepted-topology collision policy |
| `FDB-OF-GAP-015` | consumer-contract generation | Exact canonical schema/serialization and generation-advance rule, although semantic hash behavior is already required |

### 18.2 Surface-open gaps

| Gap ID | Area | Missing surface |
|---|---|---|
| `FDB-OF-GAP-016` | FDB Control entry point | Final Python/module path and command parser layout |
| `FDB-OF-GAP-017` | service target syntax outside normal add-service | Composite/multi-service target syntax for operations that still need more than one logical service identity; normal add-service is closed as `<network> --service <service-id>` |
| `FDB-OF-GAP-018` | output | Final JSON schema names and human-readable presentation |

No `surface-open` gap permits a different operator meaning from `fdb-o.md`.


## 20. Requirements for `fdb-o-f-m.md`

The module document MUST map every functionality ID above to one or more concrete
modules/public seams without changing operation composition.

At minimum it must define module ownership for:

```text
shared-private loading and host binding
accepted-state persistence
operation records/scopes/retry state
Coolify/deployment transport
service descriptor rendering
FDB CLI/native client observation
FDB transaction probe
cluster birth/bootstrap
service participation/convergence
service evacuation/removal
coordinator management
FDB-native configuration
consumer-contract derivation/canonicalization
inspection/evidence output
```

It MUST also make the following dependencies explicit:

```text
operation layer
    -> functionality seams
        -> deployment/FDB/private-state adapters
```

and not:

```text
operation layer
    -> giant deploy script with hidden behavior
```


## 21. Traceability and acceptance requirements

Every implementation unit and contract test MUST be traceable through:

```text
fdb.md architectural invariant
  -> fdb-o.md operation contract
    -> fdb-o-f.md functionality
      -> fdb-o-f-m.md module/public seam
        -> traced contract test
          -> implementation unit
            -> retained execution evidence where applicable
```

Contract tests are executable verification of the documented contract. They MUST
NOT invent requirements. If a test needs an answer that `fdb.md`, `fdb-o.md`, or
this file does not provide, implementation pauses and the highest affected
contract is corrected first.

For every functionality occurrence, acceptance evidence MUST prove as
applicable:

1. it ran in the correct operation/stage;
2. required predecessor functionality completed;
3. inputs matched the frozen operation target;
4. secret values remained outside ordinary output/evidence;
5. deployment success was not confused with FDB participation;
6. FDB participation was not confused with accepted topology authority;
7. service count was not confused with host/failure-domain count;
8. coordinator state changed only under an exact target frozen by prep, whether topology-derived by add/remove/replace or explicitly overridden by set-coordinators;
9. accepted topology/configuration changed only after fresh finalization proof;
10. retries reused the same frozen target and operation identity;
11. interrupted state moved forward rather than invoking an invented rollback;
12. every finalized mutation re-derived the consumer contract;
13. Hub-FDB rectification was reported, not performed, by FDB Control;
14. Mother/Besu lifecycle state was not used as FDB authority.


## 22. Implementation rule

`fdb-o-f-m.md` will be the canonical source for module/package layout and public
module seams. This document remains canonical for operation, stage, and
functionality placement.

One module MAY implement several functionalities. One functionality MAY span
several implementation helpers if the module document declares the public seam.
Neither fact changes:

- which operation owns the functionality;
- which stage may invoke it;
- its predecessor dependencies;
- its effect class;
- its accepted/observed state rules;
- its forward-only retry semantics;
- its verification contract;
- its test obligations.

The operation is the unit of operator intent. The functionality is the unit of
composable, testable behavior. The service is the normal FDB membership unit.
The host is placement and failure-domain context. The consumer contract is the
FDB-to-Hub dependency artifact. None of those concepts may be collapsed by the
implementation.
