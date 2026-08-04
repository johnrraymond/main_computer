# MCEL Consequential Effects and Proof Accounting

## Status

This document specifies how `mcel.application-ir.v1` declares consequential effects, how runtime evidence records effect instances, and how MCEL proves that every consequential effect has an owner, evidence, a terminal disposition, and no unexplained residue.

It is a documentation specification. It does not authorize an effect-ledger implementation, capability rewrite, runtime instrumentation, browser mutation, compiler implementation, application migration, or retirement of existing receipt and evidence paths.

Read this with:

- `pretty_docs/mcel-ai-authoring-semantic-boundary.md`;
- `pretty_docs/mcel-application-ir-and-compiler-migration.md`;
- `pretty_docs/mcel-application-ir-schema-and-normalization.md`;
- `pretty_docs/mcel-constrained-expression-model.md`;
- `pretty_docs/mcel-existing-application-definition-migration-inventory.md`;
- `pretty_docs/mcel-acceptance-evidence.md`;
- `pretty_docs/mcel-observation-and-inference.md`;
- `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`;
- `pretty_docs/mcel-ai-application-authoring-cycle.md`;
- `pretty_docs/mcel-ai-authoring-pattern-catalog.md`.

## The short answer

A final screen is not a complete explanation of an application operation.

Suppose a quote eventually appears:

```text
visible result:
  Quote: $42.00
```

That does not explain:

```text
which request started
which contract owned the request
whether an older request was superseded
which progress events were accepted
whether cancellation was requested
whether the capability observed cancellation
whether a late result was rejected
which canonical write committed
whether provisional state was removed
whether another contract's request remained independent
```

MCEL therefore treats consequential effects as accounted lifecycles:

```text
declared effect obligation
  -> runtime effect instance
  -> evidence events
  -> one terminal disposition
  -> required cleanup or retention explanation
  -> independent claims
  -> proof reconciliation
```

Proof passes only when every consequential effect is explained and every consequential observed effect was declared.

### TL;DR

MCEL proves not only what became visible, but what happened, what did not happen, why, and what remained afterward.

## 1. What is a consequential effect?

A consequential effect is any occurrence, nonoccurrence, ordering decision, failure, cancellation, cleanup action, or retained resource that can change:

```text
canonical application truth
renderer behavior that affects later decisions
provisional operation state
an external system
an operation receipt or audit record
recovery rights or obligations
a later operation's eligibility or result
what the user can observe or safely infer
```

Examples:

```text
canonical write
renderer-local write that changes submit eligibility
provisional progress write
network or capability request
filesystem write
Git commit or push
publish action
confirmation acquisition
cancellation request
supersession decision
late-event rejection
resource acquisition or release
receipt emission
recovery or compensation
intentional durable retention
```

Not every implementation event is consequential.

Usually incidental:

```text
allocating a temporary JavaScript array
formatting a string twice
adding a nonsemantic CSS class
reading a cached pure derived value
internal logging that has no product, audit, or recovery role
```

The compiler and migration importer must classify the semantic effect, not every low-level instruction used to implement it.

### Consequentiality test

Ask:

> If this occurrence, nonoccurrence, ordering, failure, or leftover state changed, could canonical truth, external reality, recovery, a later operation, or a user-visible claim change?

If yes, it is consequential and must be represented or explicitly delegated to an already governed lower-level effect.

### TL;DR

Track effects that can alter truth, external reality, recovery, later behavior, or meaningful observation—not every CPU instruction.

## 2. Four different things must not be confused

MCEL separates:

1. **Effect declaration** — what an intent is allowed or required to cause.
2. **Effect instance** — one runtime occurrence associated with one operation.
3. **Effect evidence** — independently captured facts about that occurrence.
4. **Effect claim** — a proposition acceptance, observation, or proof must verify.

Example declaration:

```json
{
  "id": "effect:request-quote.capability-request",
  "kind": "effect",
  "effectKind": "capability-request",
  "owner": {"ref": "intent:request-quote"},
  "authority": {"ref": "capability:quotes.request"},
  "allowedFinalDispositions": [
    "completed",
    "refused-before-attempt",
    "cancelled",
    "superseded",
    "failed"
  ]
}
```

Example runtime instance:

```json
{
  "effectInstanceId": "effect-instance:operation-17/request",
  "effect": {"ref": "effect:request-quote.capability-request"},
  "operationInstanceId": "operation-instance:17",
  "targetKey": "contract-4",
  "status": "terminal",
  "finalDisposition": "completed"
}
```

Example evidence:

```json
{
  "evidenceKind": "capability-result",
  "effectInstanceId": "effect-instance:operation-17/request",
  "resultFingerprint": "sha256:...",
  "observedAtSequence": 8
}
```

Example claim:

```json
{
  "claimId": "claim:request-quote.result-committed-once",
  "authority": "canonical-state",
  "subject": {"ref": "state:contracts"},
  "predicate": "quote-result-committed-once",
  "effectInstance": "effect-instance:operation-17/request"
}
```

A declaration cannot prove its own occurrence. An effect instance cannot prove its own visible consequence. A visible consequence cannot prove that no undeclared effect occurred.

### TL;DR

Declaration, runtime occurrence, evidence, and proof claim are separate authorities.

## 3. Effect ownership

Every effect declaration has exactly one semantic owner.

Typical owners:

```text
intent
operation lifecycle
recovery operation
system-governed application mount or unmount lifecycle
```

Example:

```json
{
  "id": "effect:save-document.file-write",
  "owner": {"ref": "intent:save-document"},
  "effectKind": "external-mutation",
  "authority": {"ref": "capability:files.write"}
}
```

Invalid:

```text
surface button directly writes a file
callback performs a hidden network request
browser observer repairs state while observing it
capability result mutates canonical state without owning intent reconciliation
```

Generated plumbing may dispatch, instrument, or record the effect. It does not become the semantic owner.

### Shared lower-level effects

Several intents may use one capability, but each runtime effect instance remains owned by the invoking operation.

```text
capability:files.write
  used by intent:save-document
  used by intent:export-document
```

The shared capability owns external execution authority. Each intent owns why that execution belongs to the application operation and how its result is reconciled.

### TL;DR

Capabilities own external authority; intents and lifecycles own application meaning.

## 4. Initial effect taxonomy

`mcel.application-ir.v1` uses a closed, versioned effect taxonomy. Initial effect kinds are:

| Effect kind | What it represents | Typical owner |
| --- | --- | --- |
| `canonical-write` | SCM-governed canonical state transition | Mutation or async reconciliation intent |
| `renderer-local-write` | Local state change that affects behavior or later operation input | Local intent or semantic binding |
| `provisional-write` | Noncanonical operation-progress state | Async lifecycle |
| `capability-request` | Invocation of governed external or privileged authority | Async or capability intent |
| `external-read` | External observation whose value affects application behavior | Capability intent |
| `external-mutation` | Filesystem, Git, network, publish, message, shell, or other external write | Capability intent |
| `confirmation` | Acquisition, refusal, expiry, or consumption of confirmation authority | Governed mutation intent |
| `cancellation` | Request and propagation of cancellation | Cancel intent or lifecycle |
| `supersession` | Policy decision that makes older work ineligible | Concurrency lifecycle |
| `late-event-rejection` | Rejection of evidence arriving after eligibility ended | Async lifecycle |
| `resource-acquire` | Lock, subscription, handle, reservation, or other governed resource acquisition | Operation lifecycle |
| `resource-release` | Release of an acquired resource | Operation lifecycle or recovery |
| `receipt-emission` | Operation, external-system, or durable audit receipt | Operation lifecycle |
| `surface-publication` | Meaningful visible publication of canonical, provisional, refusal, or receipt state | Surface projection |
| `recovery` | Repair, rollback, compensation, or reconciliation after failure or uncertainty | Recovery operation |
| `durable-retention` | Deliberate persistence of a resource or obligation after the operation ends | Owning intent or lifecycle |

The taxonomy may grow only through a versioned specification change with:

```text
semantic definition
allowed owners
required fields
allowed lifecycle transitions
minimum evidence
proof interpretation
normalization rule
migration impact
```

### Delegated effects

A high-level effect may delegate to lower-level effects:

```text
publish-site
  -> capability request
  -> external deployment mutation
  -> deployment receipt
  -> canonical deployment record
  -> surface publication
```

The parent effect is complete only when its required child effects reconcile.

### TL;DR

Effect kinds are semantic and closed. “Run callback” is not an effect kind.

## 5. Effect declaration record

A normalized effect declaration has this minimum shape:

```json
{
  "id": "effect:git-tools.push.remote-mutation",
  "kind": "effect",
  "effectKind": "external-mutation",
  "owner": {"ref": "intent:git-tools.push"},
  "authority": {"ref": "capability:git.push"},
  "risk": "remote-write",
  "target": {
    "kind": "record.construct",
    "fields": {
      "repositoryId": {"kind": "input.read", "input": {"ref": "input:git-tools.push.repository-id"}},
      "remote": {"kind": "input.read", "input": {"ref": "input:git-tools.push.remote"}},
      "branch": {"kind": "input.read", "input": {"ref": "input:git-tools.push.branch"}}
    }
  },
  "preconditions": [
    {"ref": "invariant:git-tools.push.preflight-pass"},
    {"ref": "effect:git-tools.push.confirmation"}
  ],
  "cardinality": {"minimum": 0, "maximum": 1},
  "allowedFinalDispositions": [
    "completed",
    "refused-before-attempt",
    "failed",
    "cancelled",
    "recovered",
    "indeterminate-blocking"
  ],
  "requiredEvidence": [
    "operation-receipt",
    "capability-request",
    "external-receipt-or-error",
    "canonical-reconciliation",
    "visible-outcome"
  ],
  "cleanupObligations": [],
  "recovery": {"ref": "recovery:git-tools.push"}
}
```

Required fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable semantic effect ID |
| `effectKind` | Registered semantic class |
| `owner` | One owning intent or lifecycle |
| `authority` | Governing capability, SCM authority, or projection authority |
| `target` | Inspectable identity of the affected thing |
| `risk` | Stable risk class used by policy and proof |
| `cardinality` | Expected instance count per owning operation |
| `allowedFinalDispositions` | Closed set of legal terminal outcomes |
| `requiredEvidence` | Minimum evidence obligations |
| `cleanupObligations` | Required residue closures |
| `recovery` | Optional explicit recovery relationship |
| `source` | Authored or imported source provenance |

Author declarations may use concise DSL helpers. The IR must expand all defaults explicitly.

### TL;DR

The author declares the consequential decision; the IR makes its ownership, authority, target, cardinality, evidence, and outcomes explicit.

## 6. Operation and effect identity

One intent declaration may run many times. Proof must therefore distinguish semantic IDs from runtime IDs.

```text
semantic intent:
  intent:request-quote

runtime operation:
  operation-instance:request-quote/000017

semantic effect:
  effect:request-quote.capability-request

runtime effect:
  effect-instance:request-quote/000017/capability-request/1
```

Every runtime effect instance must bind to:

```text
one semantic effect declaration
one operation instance
one target identity when the effect has a target
one causal parent when delegated or recovered
one ordered evidence stream
```

### Operation keys are not operation IDs

For latest-per-item concurrency:

```text
operation instance A:
  unique ID: op-17
  operation key: contract-4

operation instance B:
  unique ID: op-18
  operation key: contract-4
```

The shared operation key allows B to supersede A. It must not collapse A and B into one effect instance.

### Idempotency keys

When an external system supports idempotency, the effect instance may carry a separate idempotency key:

```json
{
  "operationInstanceId": "operation-instance:17",
  "operationKey": "contract-4",
  "idempotencyKey": "quote/contract-4/revision-9"
}
```

The proof must not infer external idempotency merely because MCEL refused a duplicate local operation.

### TL;DR

Semantic ID, runtime operation ID, operation key, target key, and idempotency key serve different purposes.

## 7. Lifecycle states and terminal dispositions

Effect lifecycle state and final disposition are separate.

Lifecycle states:

```text
declared
eligible
not-eligible
started
progressing
terminal
```

Initial terminal dispositions:

```text
not-attempted
refused-before-attempt
completed
committed
failed
cancelled
superseded
ignored-as-late
recovered
compensated
intentionally-retained
indeterminate-blocking
```

### Important distinctions

`completed`:

```text
The effect fulfilled its declared noncanonical or external purpose.
```

`committed`:

```text
The effect produced an SCM-governed canonical transition.
```

`failed`:

```text
The effect ended with a classified failure and has no unresolved external uncertainty.
```

`indeterminate-blocking`:

```text
The system cannot prove whether the effect occurred or what external state remains.
```

`indeterminate-blocking` is a terminal evidence classification for the observed attempt, but it blocks application proof and usually opens a recovery obligation.

`recovered` and `compensated` do not erase the original failure. The ledger preserves the failed effect and links a successful recovery effect.

### One terminal disposition

Each effect instance receives exactly one final disposition. Related later facts use linked effects rather than rewriting history.

Wrong:

```text
push effect:
  failed -> later rewritten to completed
```

Correct:

```text
push effect:
  failed

recovery inspection effect:
  completed

reconciliation effect:
  committed

final operation outcome:
  recovered
```

### TL;DR

Effect history is append-only. Recovery explains failure; it does not erase it.

## 8. Effect event stream

Each effect instance has an ordered event stream.

Example:

```json
{
  "effectInstanceId": "effect-instance:request-quote/17/request/1",
  "events": [
    {"sequence": 1, "eventKind": "eligible"},
    {"sequence": 2, "eventKind": "request-dispatched", "requestFingerprint": "sha256:..."},
    {"sequence": 3, "eventKind": "progress-accepted", "eventFingerprint": "sha256:..."},
    {"sequence": 4, "eventKind": "result-received", "resultFingerprint": "sha256:..."},
    {"sequence": 5, "eventKind": "completed"}
  ]
}
```

The stream must be:

```text
monotonic within the operation evidence domain
append-only
source-attributed
repository and application bound
linked to the semantic declaration
safe to compare deterministically
```

Wall-clock timestamps may be retained as evidence metadata, but semantic ordering uses explicit sequence and causal references.

### Evidence sources

An event records its source authority:

```text
SCM runtime
capability adapter
external receipt
browser observer
acceptance runner
recovery controller
generated projection
```

Generated projection evidence can explain what code was wired. It cannot prove that the runtime effect occurred.

### TL;DR

Ordering comes from explicit sequence and causality, not timestamp guesses.

## 9. Evidence classes

The proof reconciler may use these evidence classes:

| Evidence class | What it can prove | What it cannot prove alone |
| --- | --- | --- |
| `declaration` | The application claims and authorizes an effect | The effect occurred |
| `projection` | Generated runtime wiring corresponds to the declaration | The wiring executed correctly |
| `operation-receipt` | The runtime classified an operation outcome | Canonical or external truth without corroboration |
| `canonical-transition` | SCM accepted a specific state transition | External effect occurrence |
| `local-transition` | Governed renderer-local behavior changed | Canonical or external truth |
| `provisional-transition` | Progress state changed under a lifecycle | Final canonical result |
| `capability-trace` | A capability request, event, result, cancellation, or failure was observed by the adapter | External system truth beyond the adapter boundary unless an external receipt exists |
| `external-receipt` | An external authority acknowledged an operation or state | Correct browser projection |
| `browser-observation` | A visible semantic consequence was observed | Hidden effects did not occur |
| `cleanup-evidence` | Required provisional/resource residue was closed | Original operation succeeded |
| `recovery-evidence` | Recovery or compensation ran and produced a classified result | Original failure never occurred |
| `repository-provenance` | Evidence and generated artifacts bind to repository content | Runtime behavior by itself |

### Minimum independence rule

A proof claim must not be satisfied solely by an artifact generated from the same declaration it is checking.

Example:

```text
DSL declares canonical write
compiler generates write contract
```

This proves consistency between source and projection, not that the write executed.

Execution requires SCM evidence. Visible consequence requires browser evidence when claimed. External mutation requires capability or external receipt evidence.

### TL;DR

Generated artifacts prove translation. Independent runtime evidence proves behavior.

## 10. Minimum evidence by effect kind

Initial minimum evidence requirements:

| Effect kind | Minimum evidence |
| --- | --- |
| `canonical-write` | operation receipt + exact SCM before/after revision and write-set evidence |
| `renderer-local-write` | governed local transition evidence; browser observation when behavior or visible state is claimed |
| `provisional-write` | lifecycle event + provisional before/after + operation ownership |
| `capability-request` | request fingerprint + capability authority + operation binding + terminal capability result/failure/cancellation |
| `external-read` | request fingerprint + result/error evidence + reconciliation or refusal evidence |
| `external-mutation` | request fingerprint + external receipt or classified uncertainty + canonical receipt/reconciliation when application truth records completion |
| `confirmation` | confirmation request + subject/scope + granted/refused/expired/consumed disposition |
| `cancellation` | cancellation request + target operation + capability propagation result + canonical-ineligibility evidence |
| `supersession` | older/newer operation relation + policy + older commit ineligibility + late-result handling |
| `late-event-rejection` | event identity + ended operation eligibility + explicit rejection reason + proof of no prohibited write |
| `resource-acquire` | resource identity + owner + acquisition result |
| `resource-release` | resource identity + matching acquisition + release result |
| `receipt-emission` | receipt fingerprint + operation/effect references + durable or visible publication evidence as required |
| `surface-publication` | semantic node identity + expected source authority + browser-observed value/state |
| `recovery` | original failed/indeterminate effect + recovery authority + recovery result + resulting truth/remainder |
| `durable-retention` | retention owner + reason + expiry/release rule or durable policy reference |

An application or domain policy may require more evidence. It may not require less without a versioned change to the effect kind or risk class.

### TL;DR

Evidence requirements depend on what kind of truth the effect changes.

## 11. Cleanup obligations and residue

An effect is not complete merely because its primary action ended.

Possible residue:

```text
provisional progress entry
active-operation registry entry
AbortController or subscription
lock or reservation
temporary file
staging directory
pending confirmation
unpublished receipt
retry obligation
recovery obligation
renderer-local disabling flag
```

Effect declarations name cleanup obligations:

```json
{
  "cleanupObligations": [
    {
      "id": "cleanup:request-quote.provisional-entry",
      "resource": {"ref": "state:quote-progress"},
      "key": {"kind": "operation.key"},
      "requiredAfter": ["completed", "cancelled", "superseded", "failed"]
    },
    {
      "id": "cleanup:request-quote.active-operation",
      "resource": {"ref": "runtime-resource:active-operations"},
      "key": {"kind": "operation.instance-id"},
      "requiredAfter": ["completed", "cancelled", "superseded", "failed"]
    }
  ]
}
```

Proof fails when:

```text
primary request completed but progress state remains
cancelled operation remains commit-eligible
lock was acquired but neither released nor intentionally retained
external write failed and an unresolved temporary artifact remains
confirmation was consumed twice
```

### Intentional retention

Some residue is a product feature or recovery requirement:

```text
durable operation receipt
recovery obligation
published deployment record
offline retry queue
```

Retention requires:

```text
semantic owner
reason
retention policy
release, expiry, or supersession rule
proof-visible disposition: intentionally-retained
```

### TL;DR

Proof accounts for what remains, not only what happened.

## 12. Effect accounting ledger

The effect ledger is a derived evidence reconciliation object, not an authoring surface.

Conceptual schema:

```json
{
  "schema": "mcel.effect-accounting-ledger.v1",
  "appId": "contract-workbench",
  "operationInstanceId": "operation-instance:request-quote/17",
  "intent": {"ref": "intent:request-quote"},
  "operationKey": "contract-4",
  "declaredEffectIds": [
    "effect:request-quote.capability-request",
    "effect:request-quote.provisional-progress",
    "effect:request-quote.canonical-commit",
    "effect:request-quote.cleanup"
  ],
  "instances": [],
  "unmatchedObservedEffects": [],
  "missingRequiredEffects": [],
  "openInstances": [],
  "unresolvedCleanupObligations": [],
  "unexplainedResidue": [],
  "conflicts": [],
  "status": "pass"
}
```

The ledger is built from declarations and evidence. The runtime must not be able to mark its own ledger `pass` without reconciliation.

### Ledger scopes

MCEL may produce ledgers at several scopes:

```text
one operation instance
one scenario
one application proof run
one migration equivalence comparison
```

Application proof passes only if all required operation/scenario ledgers pass and no application-wide unmatched effect remains.

### TL;DR

The ledger is where declarations and independent evidence are reconciled into an accounting verdict.

## 13. Proof completeness algorithm

For each proof scope, MCEL must perform these checks.

### 13.1 Declaration completeness

```text
every effect reference resolves
every effect has one owner
every effect kind is registered
every authority is legal for the owner
every allowed disposition is valid for the kind
every required cleanup obligation is defined
```

### 13.2 Cardinality completeness

```text
required effects occurred the required number of times
optional effects either occurred legally or ended not-attempted/refused
no effect exceeded its maximum cardinality
```

### 13.3 Observation matching

```text
every observed consequential effect matches one declaration
no declaration absorbs unrelated observed effects
match uses owner, operation, kind, authority, and target identity
```

### 13.4 Lifecycle completeness

```text
every effect instance reaches one final disposition
all event sequences are legal
cancellation and supersession relations resolve
late events receive explicit treatment
indeterminate results open blocking recovery obligations
```

### 13.5 Authority completeness

```text
canonical writes occurred only through declared SCM write sets
capabilities performed only declared external authority
surface publication did not become mutation authority
observation remained read-only
```

### 13.6 Evidence completeness

```text
each effect kind has its minimum required evidence
claimed canonical truth has SCM evidence
claimed visible truth has browser evidence
claimed external truth has capability/external evidence
no claim self-verifies solely through generated artifacts
```

### 13.7 Cleanup completeness

```text
all required cleanup obligations are satisfied
all retained residue has declared ownership and policy
no active operation remains accidentally eligible
no provisional or resource leak remains unexplained
```

### 13.8 Conflict completeness

```text
contradictory evidence is retained
ambiguous or indeterminate consequential truth blocks proof
proof does not choose the evidence that makes the operation pass
```

### 13.9 Migration completeness

For dual-authored applications:

```text
legacy and DSL/IR effect declarations compare semantically
generated projections preserve effect IDs and obligations
runtime evidence remains valid only when affected fingerprints align
intentional differences are versioned and re-proven
```

### Pass rule

```text
status: pass
```

requires all checks above to pass.

A missing effect is not equivalent to an effect with `not-attempted`. An unknown result is not equivalent to `failed`. Visible success is not equivalent to effect completeness.

### TL;DR

Proof completeness is a closed accounting equation: declared obligations, observed effects, terminal outcomes, evidence, and residue must reconcile with nothing unexplained.

## 14. Counter increment

### Application meaning

```text
Increment owns one canonical count write.
The committed count increases by one.
The receipt reports commit.
The visible count agrees with canonical state.
No other write occurs.
```

### Declared effects

```json
[
  {
    "id": "effect:increment.count-write",
    "effectKind": "canonical-write",
    "owner": {"ref": "intent:increment"},
    "cardinality": {"minimum": 1, "maximum": 1},
    "allowedFinalDispositions": ["committed", "failed"]
  },
  {
    "id": "effect:increment.receipt",
    "effectKind": "receipt-emission",
    "owner": {"ref": "intent:increment"},
    "cardinality": {"minimum": 1, "maximum": 1},
    "allowedFinalDispositions": ["completed"]
  },
  {
    "id": "effect:increment.visible-count",
    "effectKind": "surface-publication",
    "owner": {"ref": "intent:increment"},
    "cardinality": {"minimum": 1, "maximum": 1},
    "allowedFinalDispositions": ["completed"]
  }
]
```

### Required reconciliation

```text
one SCM transition changed count from N to N+1
write set contained only count/revision authorities declared by the intent
one committed receipt referred to the operation
browser-observed count equaled committed canonical count
no unmatched canonical write occurred
```

### Prohibited direct-set

The prohibited intent should reconcile differently:

```text
operation disposition: refused-before-attempt
canonical-write effect: not-attempted
visible refusal: completed
unmatched canonical writes: none
```

Proof must not require a canonical-write instance merely because the intent has a write-shaped user request. The refusal proves that the write did not become eligible.

### TL;DR

Even Counter distinguishes committed write, emitted receipt, visible result, and proven nonoccurrence of prohibited writes.

## 15. Workbench add-contract

### Declared effects

```text
canonical contract append
canonical next-ID/revision update
operation receipt
visible collection-row publication
local form reset when declared
```

### Proof questions

```text
Did validation refuse before any canonical effect when name was missing?
Did duplicate or stale requests avoid unauthorized writes?
Did exactly one contract record append on success?
Was the generated ID the one committed by SCM?
Did the row appear from committed state rather than optimistic hidden mutation?
Did any local reset occur only after the declared outcome?
```

### Example failure

```text
SCM append committed
browser row appeared
operation receipt passed
but nextContractId did not advance
```

A shallow UI proof would pass. Effect accounting fails because the declared canonical transition is incomplete and future ID allocation is now unsafe.

### TL;DR

A visible row is not enough; every canonical field and refusal path must reconcile.

## 16. Workbench request-quote: successful lifecycle

### Declared effect chain

```text
capability request
provisional entry creation
zero or more progress writes
capability result
canonical quote commit
provisional cleanup
operation receipt
visible quote publication
```

### Expected ledger

```text
request:
  completed

provisional progress:
  completed
  events accepted under active operation instance

canonical commit:
  committed exactly once

cleanup:
  completed

receipt:
  completed

visible publication:
  completed

open effects:
  none

unexplained residue:
  none
```

### Required causal ordering

```text
request dispatch precedes capability events
capability result precedes canonical reconciliation
canonical reconciliation precedes or agrees with visible final quote
cleanup follows terminal eligibility
```

Browser timing alone does not establish this ordering. Runtime sequence and causal references do.

### TL;DR

Success means request, progress, commit, cleanup, receipt, and visible result all agree.

## 17. Workbench request-quote: supersession

Two same-key requests:

```text
A: operation-instance-17, key contract-4
B: operation-instance-18, key contract-4
```

When policy is `latest-per-item-key`:

```text
B becomes eligible
A receives superseded disposition
A becomes canonically ineligible
A late result is rejected
B may continue and commit
A provisional state is removed or replaced under declared policy
```

Required evidence:

```text
explicit A -> B supersession relation
same operation key
policy identity
A commit-ineligibility
late result identity if it arrives
no A canonical write after supersession
A cleanup
B independent terminal outcome
```

Invalid shortcut:

```text
Final quote equals B, therefore A must not have committed.
```

The final value could hide an earlier A write later overwritten by B.

### TL;DR

Supersession proof requires proving the older operation lost authority, not merely that the newer value won last.

## 18. Workbench request-quote: cancellation

Cancellation has at least three related effects:

```text
cancellation request
capability cancellation propagation
canonical commit ineligibility and cleanup
```

Possible dispositions:

### Cancel before dispatch

```text
request: not-attempted
cancellation: completed
canonical commit: not-attempted
cleanup: completed or unnecessary by declaration
```

### Cancel after dispatch, capability acknowledges

```text
request: cancelled
cancellation propagation: completed
canonical commit: not-attempted
late events: ignored-as-late if any
cleanup: completed
```

### Cancel requested, capability result races

The lifecycle policy must decide which sequence owns eligibility. Proof may not infer the winner from the final UI.

```text
cancel sequence
result sequence
eligibility transition
commit or rejection evidence
```

### TL;DR

“User clicked Cancel” is not proof that work stopped or that commit authority ended.

## 19. Parallel item operations

Requests for different keys:

```text
A owns contract-4
B owns contract-7
```

`latest-per-item-key` allows both.

Proof must show:

```text
separate operation instances
separate operation keys
separate provisional entries
no cross-key supersession
no progress event applied to the wrong item
independent terminal dispositions
independent cleanup
```

A global “latest operation” registry would violate this meaning even if both final quotes eventually appear.

### TL;DR

Parallelism proof is ownership proof, not merely eventual completion proof.

## 20. Git Tools governed push

Git Tools is the decisive external-mutation example.

### Semantic chain

```text
repository status/preflight read
push plan construction
confirmation acquisition
Git/Gitea capability request
remote mutation
external receipt or classified error
canonical/local operation receipt
visible result
recovery or inspection when uncertain
```

### Required effect declarations

```text
confirmation
capability request
external mutation
receipt emission
surface publication
optional recovery
```

### Success proof

```text
confirmation scope matched repository, remote, branch, and plan fingerprint
confirmation was valid and consumed once
push request matched the confirmed plan
external authority returned a bound receipt
application receipt referenced that external receipt
visible result represented the same target and outcome
no undeclared second push occurred
```

### Failure after uncertain transport

Suppose the network connection drops after request dispatch.

Wrong:

```text
push: failed
```

The remote mutation may have succeeded.

Correct:

```text
push: indeterminate-blocking
recovery obligation: open
remote inspection: required
reconciliation: required
```

After inspection:

```text
original push remains indeterminate in history
inspection effect completes
reconciliation records remote truth
operation outcome becomes recovered or failed-with-no-remote-change
```

### TL;DR

External mutation proof must distinguish failure from uncertainty and preserve recovery obligations.

## 21. Code Editor save

### Semantic chain

```text
local draft exists
canonical/source hash identifies the opened version
save request includes expected hash
filesystem capability checks and writes
external file result returns new hash
canonical editor truth reconciles
local dirty state clears
visible receipt publishes
```

### Stale refusal

When expected hash differs:

```text
filesystem write: refused-before-attempt
canonical save commit: not-attempted
local draft: intentionally-retained
conflict receipt: completed
visible conflict: completed
```

The retained draft is not unexplained residue. It has an owner and recovery purpose.

### Partial write or replacement failure

The effect model must preserve:

```text
temporary artifact created?
original file replaced?
backup created?
cleanup attempted?
result certain or indeterminate?
```

A generic `save failed` receipt is insufficient when the filesystem effect may have partially changed external truth.

### TL;DR

Code Editor proof must explain both the file and the retained draft after refusal or failure.

## 22. Document Editor persistence and export

Document Editor separates:

```text
canonical document content
renderer-local selection and scroll state
semantic region identity
persistence effect
export effect
visible publication
```

### Export example

```text
construct export plan using pure expression/domain operator
invoke export capability
record external artifact identity
emit receipt
show download/export result
retain or release temporary resources
```

Proof must not claim export success merely because a button changed to “Done.” It needs capability or artifact evidence tied to the declared document revision and export plan.

### Selection and scroll

Selection and scroll changes are consequential only when they affect:

```text
which semantic region receives an edit
which operation input is constructed
anchored surface ownership
later command eligibility
```

Purely incidental visual movement need not become an effect ledger entry.

### TL;DR

The effect boundary follows semantic consequences, not whether code happens in the browser or backend.

## 23. Confirmation is an effect, not a boolean

This is too weak:

```javascript
if (confirmed) {
  push();
}
```

MCEL needs to know:

```text
who or what granted confirmation
what exact operation and target it covered
which plan fingerprint it covered
when it expires
whether it was consumed
whether the operation changed after confirmation
```

Conceptual record:

```json
{
  "effectKind": "confirmation",
  "subject": {"ref": "intent:git-tools.push"},
  "scopeFingerprint": "sha256:...",
  "allowedFinalDispositions": [
    "completed",
    "refused-before-attempt",
    "expired",
    "superseded"
  ]
}
```

A changed plan invalidates the old confirmation. Proof must reject confirmation evidence whose scope fingerprint does not match the executed request.

### TL;DR

Confirmation is scoped authority with a lifecycle, not a UI checkbox.

## 24. Receipts are evidence carriers, not truth by assertion

A receipt should carry references and fingerprints:

```json
{
  "receiptId": "receipt:operation-17",
  "operationInstanceId": "operation-instance:17",
  "intent": {"ref": "intent:request-quote"},
  "effectInstanceIds": [
    "effect-instance:operation-17/request",
    "effect-instance:operation-17/commit"
  ],
  "outcome": "committed",
  "canonicalRevisionBefore": 8,
  "canonicalRevisionAfter": 9,
  "externalReceiptFingerprint": "sha256:..."
}
```

The receipt is then checked against:

```text
SCM state evidence
capability trace
external receipt when applicable
browser observation when visibly claimed
```

A receipt that merely says `success: true` cannot establish these facts by itself.

### TL;DR

Receipts bind evidence together; they do not manufacture evidence.

## 25. Proof claims against multiple authorities

A robust scenario states outcomes against distinct authorities.

Conceptual DSL:

```javascript
prove.scenario("quote completes")
  .invoke(requestQuote, {contractId: "contract-4"})
  .expectEffect(requestQuote.request, "completed")
  .expectCanonical(contracts.byId("contract-4").quote, 42)
  .expectCleanup(quoteProgress.byKey("contract-4"))
  .expectReceipt({outcome: "committed"})
  .expectVisible(contractRow("contract-4").quote, "$42.00");
```

The compiler may generate adapters and selectors. The author must explicitly state consequential claimed outcomes.

### Cross-authority disagreement

```text
receipt says committed
SCM revision did not change
browser shows optimistic value
```

Result:

```text
conflict
proof fail
```

MCEL must not select the source that makes the scenario pass.

### TL;DR

Strong scenarios compare independent authorities instead of asking one layer whether it succeeded.

## 26. Generated obligations versus authored claims

MCEL may generate mechanical obligations:

```text
every canonical write needs SCM evidence
every cancellable lifecycle needs terminal cancellation accounting
every acquired resource needs release or retention evidence
every visible semantic claim needs browser observation
every external mutation needs external/capability evidence
```

The author must declare application-specific claims:

```text
which contract receives the quote
which fields change on add-contract
which repository and branch a push targets
whether a stale save retains the draft
which export artifact belongs to which document revision
```

Generated proof plumbing must not invent product meaning.

### TL;DR

MCEL generates universal accounting rules; authors declare application-specific outcomes.

## 27. Unmatched observed effects

Runtime instrumentation may observe a consequential effect not present in the IR.

Examples:

```text
undeclared network request
canonical write outside declared write set
second external push
hidden filesystem cleanup
browser-side local state changes operation eligibility without declaration
```

The ledger records:

```json
{
  "unmatchedObservedEffects": [
    {
      "effectKind": "external-mutation",
      "authority": "git.push",
      "targetFingerprint": "sha256:...",
      "evidenceRefs": ["evidence:..."]
    }
  ],
  "status": "fail"
}
```

An importer may classify a legacy effect as known migration debt only when it has exact source provenance and explicit blocking status. It may not silently absorb it into a generic “legacy behavior” declaration and claim DSL-v1 completeness.

### TL;DR

Observed-but-undeclared effects fail proof; they do not become automatic features.

## 28. Missing required effects

A declared effect may fail to instantiate.

Example:

```text
intent declared external save
operation receipt says success
no filesystem capability request exists
```

Possible explanations must themselves be represented:

```text
refused-before-attempt
not-attempted because a precondition failed
completed through a declared delegated effect
missing evidence
implementation defect
```

A required effect cannot disappear because the application reached the desired state through an undeclared shortcut.

### TL;DR

The desired outcome does not excuse skipping the declared authority path.

## 29. Failure, uncertainty, recovery, and compensation

### Classified failure

Use `failed` when evidence establishes that the effect did not complete and external/canonical truth is known.

### Indeterminate outcome

Use `indeterminate-blocking` when occurrence or final state is uncertain.

This opens a recovery obligation such as:

```text
inspect remote repository
read file hash
query deployment status
reconcile message delivery
```

### Recovery

Recovery determines or restores truth after failure or uncertainty.

### Compensation

Compensation performs a new effect intended to offset an already completed effect.

Example:

```text
published deployment completed
later rollback deployment completed
```

The original publication remains completed. The rollback is a separate compensating effect.

### TL;DR

Failure, uncertainty, recovery, and compensation are different facts and must remain distinct.

## 30. Policy and risk classes

Effect risk classes guide confirmation and proof strength.

Initial examples:

```text
local-behavior
canonical-local-write
external-read
external-reversible-write
external-remote-write
external-destructive-write
credential-bearing-request
publish
message-delivery
shell-execution
```

A risk class may require:

```text
confirmation
stronger target identity
idempotency key
external receipt
recovery plan
compensation plan
additional browser or acceptance scenarios
```

The DSL must not hide risk selection inside a generic capability call.

### TL;DR

Risk is a semantic property that changes authority and evidence requirements.

## 31. Legacy migration and opaque-effect debt

Current applications may perform effects through:

```text
semantic adapters
app-local event handlers
backend routes
opaque callbacks
surface-led bridges
requirements-registry bindings
```

A legacy importer must produce one of:

```text
fully classified effect declaration
registered delegated domain effect
legacy opaque-effect obligation
no consequential effect found
```

Conceptual migration-only record:

```json
{
  "id": "legacy-effect:git-tools.push.handler",
  "kind": "legacy.opaque-effect",
  "owner": {"ref": "intent:git-tools.push"},
  "source": {
    "file": "main_computer/web/applications/scripts/git-tools-mcel.js",
    "symbol": "..."
  },
  "declaredRisk": "external-remote-write",
  "knownAuthorities": ["git.push"],
  "knownEvidence": ["operation-receipt"],
  "missing": ["structured-target", "complete-disposition-set", "external-receipt-binding"],
  "migrationStatus": "blocking-dsl-v1"
}
```

This preserves existing behavior while making the missing semantics visible.

### Retirement condition

A legacy effect path may be retired only when:

```text
DSL/IR effect declarations cover its meaning
semantic comparison is exact or intentionally versioned
runtime projection preserves authority
acceptance and observation evidence are renewed
all effect instances reconcile under the new ledger
migration inventory marks the feature ready
```

### TL;DR

Legacy effects may be quarantined and mapped; they may not be forgotten or mislabeled as complete.

## 32. Per-application migration obligations

Every effect-model pass must inspect at least these application families when relevant:

| Application/family | Effect concern that must not be lost |
| --- | --- |
| Contract Counter | Canonical write, prohibited write nonoccurrence, receipt, visible projection |
| Contract Workbench | Provisional progress, capability request, cancellation, supersession, late events, cleanup |
| Git Tools | Confirmation, remote mutation, receipt, uncertainty, recovery |
| Code Editor | Stale refusal, filesystem mutation, retained draft, partial-write cleanup |
| Document Editor | Region-scoped edits, persistence, export artifact, local ownership |
| File Explorer | Read versus mutation separation, prohibited hidden mutation, file effects |
| Website Builder | Project mutation, build/publish effects, deployment receipts, rollback/recovery |
| MCEL Lab | Blueprint mutation, specimen/runtime effects, scope-limited proof |
| Legacy surface-only apps | Unmapped effect discovery and blocking migration debt |

Each pass records:

```text
unchanged and compatible
mapping added
new gap discovered
legacy opaque effect retained
IR declaration updated
DSL expression updated
projection updated
evidence renewed
ready for retirement
```

### TL;DR

Effect design is tested against the real application inventory, not only the acid app.

## 33. Normalization and fingerprints

Effect semantics participate in the semantic fingerprint.

Included:

```text
effect IDs and kinds
owners and authorities
target expressions
risk classes
cardinality
allowed dispositions
required evidence
cleanup obligations
recovery and delegation relationships
causal constraints
```

Excluded from the semantic fingerprint but included in source binding or evidence fingerprints:

```text
source file and line numbers
runtime operation IDs
wall-clock timestamps
bounded stdout/stderr
browser process IDs
machine-specific paths
```

Unordered semantic sets normalize lexicographically by stable semantic ID. Ordered lifecycle constraints preserve semantic order.

Two front ends are not semantically equivalent when one omits an effect, weakens required evidence, broadens allowed dispositions, removes cleanup, or changes recovery obligations.

### TL;DR

Effect accounting is part of application meaning, not incidental runtime metadata.

## 34. Diagnostics and AI repair

Required diagnostic examples:

```text
MCEL_EFFECT_OWNER_REQUIRED
MCEL_EFFECT_AUTHORITY_INVALID
MCEL_EFFECT_TARGET_AMBIGUOUS
MCEL_EFFECT_CARDINALITY_VIOLATION
MCEL_EFFECT_UNDECLARED_OBSERVED
MCEL_EFFECT_REQUIRED_NOT_OBSERVED
MCEL_EFFECT_TERMINAL_DISPOSITION_MISSING
MCEL_EFFECT_DISPOSITION_NOT_ALLOWED
MCEL_EFFECT_EVIDENCE_MISSING
MCEL_EFFECT_CANONICAL_WRITE_OUTSIDE_SET
MCEL_EFFECT_EXTERNAL_RESULT_INDETERMINATE
MCEL_EFFECT_CLEANUP_UNRESOLVED
MCEL_EFFECT_UNEXPLAINED_RESIDUE
MCEL_EFFECT_CONFIRMATION_SCOPE_MISMATCH
MCEL_EFFECT_LATE_EVENT_COMMITTED
MCEL_EFFECT_MIGRATION_MAPPING_INCOMPLETE
```

Example:

```text
MCEL_EFFECT_CLEANUP_UNRESOLVED

operation:
  operation-instance:request-quote/17

effect:
  effect:request-quote.provisional-progress

problem:
  terminal disposition is cancelled, but provisional state remains at
  state:quote-progress[contract-4]

required repair:
  declare and implement cleanup for cancelled operations
  or declare an intentional-retention policy with an owner and expiry rule

invalidated evidence:
  request-quote.cancel
  request-quote.intent-complete-proof
```

### TL;DR

Diagnostics identify the missing accounting fact and the narrow stage that must be repaired and re-proven.

## 35. Relationship to acceptance evidence

Acceptance contracts declare application behavior and refusal requirements.

Effect accounting adds questions such as:

```text
Did the required effect occur?
Did a prohibited effect not occur?
Did it use the declared authority?
Did it terminate legally?
Did cleanup complete?
Did the receipt and canonical/external evidence agree?
```

Package-local or legacy central pytest bindings may prove these facts today. Future DSL scenarios may generate mechanical bindings, but the acceptance runner remains an independent execution authority.

Acceptance `pass` does not automatically mean complete effect accounting unless the bound contract and evidence explicitly cover the required effects.

### TL;DR

Acceptance proves declared behavior; effect accounting proves that the behavior's consequences reconciled completely.

## 36. Relationship to browser observation

Browser observation can prove:

```text
visible canonical result
visible provisional progress
visible refusal
visible receipt
visible cleanup consequence
multi-instance isolation
```

It cannot by itself prove:

```text
no hidden external request occurred
remote mutation status
canonical transition authority
capability cancellation propagation
resource release outside the browser
```

Observation stays read-only. It does not repair, cancel, clean up, or mutate the application while gathering evidence.

### TL;DR

The browser proves visible consequences, not hidden nonoccurrence or external truth.

## 37. Relationship to the truth gate

The truth gate may promote an application only when its required effect-accounting status is eligible.

Transitional compatibility:

```text
legacy applications may retain current truth behavior while effect import coverage is incomplete
new effect claims cannot be marked proven without evidence
features migrated to DSL/IR require complete effect accounting before DSL-v1 status
```

The effect-accounting stage should eventually report:

```text
applicable: true|false
status: pass|fail|legacy-evidence|incomplete
requiredEffectCount
instantiatedEffectCount
terminalEffectCount
unmatchedObservedEffectCount
missingRequiredEffectCount
unresolvedCleanupCount
indeterminateBlockingCount
```

Like the corrected Counter intent-complete stage, a legacy application must not claim a vacuous `0 / 0` effect-accounting pass.

### TL;DR

Legacy evidence may remain honest and usable; it must not masquerade as complete effect accounting.

## 38. Non-goals

This specification does not require:

```text
logging every implementation instruction
turning CSS paint changes into effect records
trusting timestamps as causality
making the browser observer mutation-capable
assuming every external system supports idempotency
pretending compensation erases the original effect
forcing all legacy applications to migrate at once
using one universal evidence adapter for every capability
```

It does require a stable semantic accounting model for effects that matter.

### TL;DR

The goal is explainable consequence, not maximal telemetry.

## 39. Documentation acceptance criteria

This specification is complete enough to guide the next documents when:

1. Every IR effect kind has an owner, lifecycle, minimum evidence, and proof interpretation.
2. Counter increment and prohibited direct-set can be accounted without vacuous claims.
3. Workbench success, cancellation, supersession, parallelism, late-event rejection, and cleanup can be distinguished.
4. Git Tools external mutation can represent confirmation, receipts, uncertainty, recovery, and compensation.
5. Code Editor can represent stale refusal, retained drafts, external write uncertainty, and cleanup.
6. Document Editor can separate local editing state from persistence and export effects.
7. Observed undeclared effects and declared missing effects both fail.
8. Residue and intentional retention are distinguished.
9. Acceptance, browser observation, receipts, SCM evidence, capability evidence, and truth-gate roles remain separate.
10. Legacy effect paths have an explicit blocking migration record rather than disappearing.

### TL;DR

The model is ready only when it explains success, refusal, cancellation, failure, uncertainty, recovery, and what remains.

## 40. Next documentation dependencies

This document supplies the effect and proof foundation required by:

1. **Official vanilla-JavaScript DSL syntax** — completed in `pretty_docs/mcel-official-vanilla-javascript-dsl.md`; it provides the source forms for effect policy, capability lifecycles, named scenario operations, recovery, cleanup, retention, and cross-authority claims without introducing another effect model.
2. **Diagnostic and repair protocol** — specified in `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`: effect diagnostics identify ownership, authority, target, cardinality, disposition, evidence, cleanup, residue, uncertainty, invalidated proof, and the stage where repair resumes.
3. **IR projection and compatibility mapping** — specified in `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`, including generated effect IDs, candidate staging, compatibility evidence, promotion, and rollback.
4. **Semantic change and evidence impact** — completed in `pretty_docs/mcel-semantic-change-and-evidence-impact.md`; changes to effect ownership, identity, policy, capability versions, evidence, cleanup, recovery, or residue trigger dependency-aware renewal.
5. **AI authoring benchmark** — specified in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md`; it includes async supersession, cancellation, uncertain Git mutation, stale-safe save, export residue, unowned effects, and proof-independence cases.
6. **Documentation completeness review** — completed in `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md`; it confirms cross-document agreement and limits the first permissible implementation to the structural IR kernel after explicit authorization.

The DSL, change-impact model, benchmark execution, and later implementation must not invent a second effect model. They must construct, compare, and verify the records and obligations defined here.

### TL;DR

The DSL will provide convenient notation; this specification remains the semantic accounting law.

## Final rule

> Every consequential effect must be declared or explicitly imported, owned by one semantic operation, bound to independent evidence, reconciled to one final disposition, and closed through cleanup, recovery, compensation, or declared retention. Proof fails when any consequential effect or residue remains unexplained.
