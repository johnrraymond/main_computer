# Contract Counter

This package is generated from `mcel.canonical-application-template.v1`. Its semantic intents execute through the shared SCM-controlled application runtime, and its declared surface can now mount through the browser-safe runtime projection. The complete application remains `structural-only` because app-oriented proof orchestration is still a target contract; package-local acceptance and operation-linked browser observation are live.

```mcel-app
id: contract-counter
title: Contract Counter
status: specified
current_runtime_status: structural-only
target_runtime_status: fullApplicationSemanticReady
dominant_object: Counter state
primary_user_goal: Exercise the complete MCEL application contract through one visible state transition.
current_sources:
  - mcel.app.json
  - blueprint.json
  - contracts/domain.js
  - contracts/intents.js
  - contracts/adapter.js
  - contracts/surface.js
  - contracts/layout.js
  - contracts/observation.js
  - contracts/acceptance.js
  - src/index.html
  - src/app.js
  - src/app.css
verification:
  - generated package structural validation
  - package-local scaffold tests
```

```mcel-use-case
id: contract-counter.use-case.counter
app: contract-counter
status: specified
type: primary
primary_object: Counter state
user_goal: Increment or reset the counter only through declared semantic intents.
acceptance: The visible count and latest result must derive from committed canonical state once the target application runtime exists.
```

```mcel-object
id: contract-counter.object.counter-state
app: contract-counter
status: specified
object: Counter state
identity: One canonical state object for the generated application instance.
state_model:
  - count is a nonnegative integer
  - revision is a nonnegative integer
owned_by: The shared adapter-to-SCM application runtime owns commitment; browser code owns no canonical mutation.
```

```mcel-region
id: contract-counter.region.primary
app: contract-counter
status: specified
region: Primary counter surface
role: primary
responsibility: Display committed count, expose declared controls, and show the latest operation result.
```

```mcel-requirement
id: contract-counter.requirement.increment
app: contract-counter
status: specified
type: behavior
aspect: actions
object: Counter state
requirement: A successful increment operation increases count and revision by exactly one.
acceptance: Starting at count 0 and revision 0, one accepted increment yields count 1 and revision 1.
```

```mcel-requirement
id: contract-counter.requirement.refusal
app: contract-counter
status: specified
type: safety
aspect: actions
object: Counter state
requirement: Stale, duplicate, prohibited, undeclared-write, and failed-postcondition operations must not create an additional canonical effect.
acceptance: Each refusal case leaves canonical count and revision unchanged and emits a classified result.
```

```mcel-intent
id: contract-counter.intent.increment
app: contract-counter
status: specified
intent: increment
risk: local-state
requires:
  - current expected revision
  - unique operation id
produces:
  - count plus one
  - revision plus one
  - operation receipt
```

```mcel-intent
id: contract-counter.intent.reset
app: contract-counter
status: specified
intent: reset
risk: local-state
requires:
  - current expected revision
  - unique operation id
produces:
  - count zero
  - revision plus one
  - operation receipt
```

```mcel-intent
id: contract-counter.intent.direct-set
app: contract-counter
status: prohibited
intent: direct-set
risk: prohibited
requires:
  - explicit refusal
produces:
  - no canonical mutation
```

```mcel-acceptance
id: contract-counter.acceptance.operation-control
app: contract-counter
status: specified
requires:
  - accepted increment commits exactly once
  - reset commits exactly once
  - stale revision is refused
  - duplicate operation has no additional effect
  - direct-set is refused
  - failed postcondition leaves canonical state unchanged
```

```mcel-evidence
id: contract-counter.evidence.operation-receipt
app: contract-counter
status: specified
evidence: Application operation receipt
proves: A declared intent passed controlled mutation checks and committed one canonical transition.
source: Shared `mcel-application-runtime.js` layered over `mcel-scm.js`.
freshness: Bound to operation id and application revision; repository binding remains part of later proof orchestration.
```

```mcel-boundary
id: contract-counter.boundary.browser-canonical-state
app: contract-counter
status: specified
boundary: Browser projection versus canonical state authority
left_side: Browser controls and visible semantic nodes
right_side: Controlled canonical state and operation ledger
rule: Browser code dispatches declared intent and renders committed state; it does not assign canonical state.
prohibited_confusion: Visible text or a local variable must not be treated as proof of canonical mutation.
```

```mcel-finding
id: contract-counter.finding.platform-gaps
app: contract-counter
status: open
aspect: implementation
severity: blocking
problem: The generated package is structurally valid, discoverable, executable through the shared SCM-controlled application runtime, mountable through generic semantic-surface projection, and discoverable by the package-local acceptance runner, and operation-linked browser observation is live, but app-oriented proof orchestration is not yet live.
desired_behavior: MCEL mounts, observes, and proves the package without user-authored central registry edits or application-local mutation authority.
```

```mcel-adapter
id: contract-counter.adapter.contract
app: contract-counter
status: specified
adapter: contract-counter semantic adapter
current_runtime_status: scm-controlled
target_runtime_status: fullApplicationSemanticReady
required_intents:
  - increment
  - reset
  - direct-set refusal
readiness_gate: Compose the live package, SCM runtime, semantic projection, package-local acceptance, and operation-linked browser observation authorities under repository-bound app-oriented proof.
```
