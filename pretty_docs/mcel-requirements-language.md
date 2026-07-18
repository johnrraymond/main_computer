# MCEL Requirements Language

This document defines the documentation-first grammar used by MCEL app requirements
documents. It is intentionally readable Markdown with machine-readable fenced blocks.
The goal is to let humans write product requirements first, then let MCEL tools parse
those requirements into app contracts, semantic-adapter gaps, layout findings, and
acceptance checks.

This grammar is informed by the current Main Computer codebase and by common
requirements/specification practices:

- BCP 14 requirement levels: use `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY`
  sparingly and only for normative behavior.
- Gherkin-style acceptance thinking: use cases should state context, action, and
  observable result, even when written in compact YAML-like lists.
- Schema-style validation: every block has required fields, allowed status values,
  stable identifiers, and predictable list fields.
- Operation-style intent definitions: every intent declares trigger, risk, input
  evidence, output receipt, and execution or adapter status.
- Responsibility-based architecture: layout regions describe ownership and product
  responsibility, not only screen position.

## What problem this solves

The current app requirements docs have proven that MCEL needs a stable middle layer:

```text
human product docs
→ parsed MCEL requirement blocks
→ app contract registry
→ Lab inspection / findings
→ adapter and test gaps
→ implementation patches
→ verified requirement status
```

Without this layer, docs can drift into persuasive prose. With this layer, a requirement
can be traced from a stable ID to an app region, source owner, semantic intent, test,
receipt, and acceptance result.

## Current coverage baseline

The grammar is based on the requirement documents currently in `pretty_docs`:

```text
mcel-code-editor-requirements.md
mcel-git-tools-requirements.md
mcel-calculator-requirements.md
mcel-file-explorer-requirements.md
mcel-website-builder-requirements.md
```

Those documents already use these block families:

```text
mcel-app
mcel-use-case
mcel-region
mcel-requirement
mcel-intent
mcel-acceptance
mcel-finding
```

They also exposed the need for these extended grammar families:

```text
mcel-object
mcel-evidence
mcel-receipt
mcel-boundary
mcel-risk
mcel-adapter
mcel-layout-pattern
mcel-source-binding
mcel-test-binding
mcel-runtime-check
```

The first family is the required core. The second family is optional until the app needs
more precision than a single requirement or intent block can hold.

## Normative keywords

Normative keywords may appear in prose, but they only carry MCEL normative meaning
when the block status is `specified`, `implemented`, `partially-implemented`, or
`verified`.

- `MUST` means an absolute product law or safety boundary.
- `MUST NOT` means an absolute prohibition.
- `SHOULD` means a strong default with documented exceptions.
- `SHOULD NOT` means avoid unless the exception is explicit.
- `MAY` means allowed but not required.

Do not use uppercase normative keywords for decorative emphasis.

## Identifier rules

Every machine-readable block must have an `id`.

IDs are stable, dotted, lowercase identifiers:

```text
<app-or-domain>.<family>.<specific-name>
```

Examples:

```text
calculator.use-case.compare-monthly-costs
git-tools.intent.push-current-branch
file-explorer.region.preview
website-builder.boundary.git-tools-handoff
```

IDs should not be renamed after tests, code, findings, or receipts reference them. If a
requirement is replaced, deprecate the old ID and add a new ID.

## Status vocabulary

Use these status values across all block types:

| Status | Meaning |
| --- | --- |
| `draft` | Useful thinking, not yet a stable contract. |
| `planned` | Stable target, not implemented. |
| `specified` | Contract is stable enough for tests and implementation planning. |
| `partially-implemented` | Some live behavior exists, but coverage is incomplete. |
| `implemented` | Live behavior exists, but verification may be incomplete. |
| `verified` | Live behavior is implemented and acceptance evidence exists. |
| `current-plus-planned` | A block explicitly describes current state plus planned target state. |
| `open` | A finding is active. |
| `prohibited` | The behavior is intentionally disallowed. |
| `deprecated` | Retained for traceability but should not drive new work. |

A doc must not call an app `verified` merely because the prose is persuasive. `verified`
requires evidence from tests, adapters, receipts, or another named verification source.

## Runtime and adapter status vocabulary

These values should be used when connecting docs to MCEL runtime truth gates:

| Field | Allowed examples |
| --- | --- |
| `current_runtime_status` | `structural-only`, `domain-enrichment-only`, `scope-limited-semantic-runtime`, `not-registered` |
| `target_runtime_status` | `scope-limited-semantic-runtime`, `fullApplicationSemanticReady` |
| `current_adapter_status` | `not-registered`, `declared-only`, `preflight-only`, `executable`, `prohibited` |
| `target_adapter_status` | `declared-only`, `preflight-only`, `executable`, `prohibited` |

Adapter readiness is not a prose claim. It must be truth-gated by the adapter registry
when implementation exists.

## Risk vocabulary

Intent risk describes side effects:

| Risk | Meaning |
| --- | --- |
| `read-only` | Inspects or previews state without mutation. |
| `local-state` | Mutates app-local memory, draft state, or UI state. |
| `local-file-mutation` | Writes project files, site files, ignore files, or source files. |
| `local-repository-mutation` | Stages, commits, switches branches, or changes local Git state. |
| `remote-mutation` | Pushes, deploys, publishes, sends, syncs, or contacts remote state. |
| `execution` | Runs code, shell commands, generated scripts, or user-program code. |
| `security-sensitive` | Touches credentials, wallet state, tokens, secrets, identity, or signing. |
| `prohibited` | The app must not expose this path by default or at all. |

Higher-risk intents must declare evidence, confirmation, receipt, and recovery.

## Core grammar blocks

### `mcel-app`

Defines the app-level contract.

Required fields:

```text
id
title
status
current_runtime_status
target_runtime_status
dominant_object
primary_user_goal
current_sources
verification
```

Recommended fields:

```text
current_semantic_runtime_scope
planned_adapter
non_goals
```

```mcel-grammar
id: grammar.block.mcel-app
status: specified
block: mcel-app
purpose: Defines the app contract, dominant object, runtime status, source roots, and verification posture.
required_fields:
  - id
  - title
  - status
  - current_runtime_status
  - target_runtime_status
  - dominant_object
  - primary_user_goal
  - current_sources
  - verification
```

### `mcel-use-case`

Defines a roadmap scenario that gives the rest of the doc a product shape.

Required fields:

```text
id
app
status
type
primary_object
user_goal
acceptance
```

Recommended fields:

```text
scenario
current_support
planned_support
requires
layout_implications
```

Use cases should be concrete enough that a person can open the app and judge whether
the flow is visible.

```mcel-grammar
id: grammar.block.mcel-use-case
status: specified
block: mcel-use-case
purpose: Defines a concrete scenario with a user goal, workflow object, layout implications, and observable acceptance.
required_fields:
  - id
  - app
  - status
  - type
  - primary_object
  - user_goal
  - acceptance
```

### `mcel-object`

Defines a domain object or workflow object that regions and intents operate on.

Required fields:

```text
id
app
status
object
identity
state_model
owned_by
```

Recommended fields:

```text
relationships
invariants
source_candidates
```

```mcel-grammar
id: grammar.block.mcel-object
status: specified
block: mcel-object
purpose: Defines a domain object, workflow object, identity model, state model, owner, and invariants.
required_fields:
  - id
  - app
  - status
  - object
  - identity
  - state_model
  - owned_by
```

### `mcel-region`

Defines layout responsibility. A region is not just a visual position; it is an owned
product responsibility.

Required fields:

```text
id
app
status
region
role
responsibility
```

Recommended fields:

```text
layout_zone
object
contains
owns
must_show
must_not_contain
expected_elements
layout_laws
```

```mcel-grammar
id: grammar.block.mcel-region
status: specified
block: mcel-region
purpose: Defines a responsibility-bearing layout region and its allowed objects, actions, evidence, and exclusions.
required_fields:
  - id
  - app
  - status
  - region
  - role
  - responsibility
```

### `mcel-requirement`

Defines a product law.

Required fields:

```text
id
app
status
type
aspect
object
requirement
acceptance
```

Recommended fields:

```text
current_state
target_state
non_goals
source_candidates
test_candidates
```

```mcel-grammar
id: grammar.block.mcel-requirement
status: specified
block: mcel-requirement
purpose: Defines a product law or safety rule with aspect, object, requirement text, and acceptance.
required_fields:
  - id
  - app
  - status
  - type
  - aspect
  - object
  - requirement
  - acceptance
```

### `mcel-intent`

Defines an executable, preflight-only, read-only, prohibited, or planned user intent.

Required fields:

```text
id
app
status
intent
risk
requires
produces
```

Recommended fields:

```text
current_adapter_status
target_adapter_status
trigger
preconditions
effects
evidence
preflight
default_execution
receipt
recovery
acceptance
```

```mcel-grammar
id: grammar.block.mcel-intent
status: specified
block: mcel-intent
purpose: Defines a user/domain intent with risk, adapter status, required evidence, outputs, receipt, and recovery posture.
required_fields:
  - id
  - app
  - status
  - intent
  - risk
  - requires
  - produces
```

### `mcel-acceptance`

Defines proof that a requirement, use case, region, or intent is complete.

Required fields:

```text
id
app
status
requires
```

Recommended fields:

```text
type
target
scope
evidence_sources
```

Acceptance requirements should prefer observable consequences. Given/When/Then wording
is allowed when it makes the check clearer.

```mcel-grammar
id: grammar.block.mcel-acceptance
status: specified
block: mcel-acceptance
purpose: Defines observable checks required before a use case, intent, or app can be called complete.
required_fields:
  - id
  - app
  - status
  - requires
```

### `mcel-finding`

Defines a durable gap between the requirement contract and the live implementation.

Required fields:

```text
id
app
status
aspect
severity
problem
desired_behavior
```

Recommended fields:

```text
evidence
source_candidates
required_checks
related_requirements
```

```mcel-grammar
id: grammar.block.mcel-finding
status: specified
block: mcel-finding
purpose: Defines a structured gap between expected behavior and observed or unimplemented behavior.
required_fields:
  - id
  - app
  - status
  - aspect
  - severity
  - problem
  - desired_behavior
```

## Extended grammar blocks

### `mcel-evidence`

Use this when evidence is shared by several intents or requirements.

Required fields:

```text
id
app
status
evidence
proves
source
freshness
```

```mcel-grammar
id: grammar.block.mcel-evidence
status: specified
block: mcel-evidence
purpose: Defines an evidence packet, what it proves, where it comes from, and how fresh it must be.
required_fields:
  - id
  - app
  - status
  - evidence
  - proves
  - source
  - freshness
```

### `mcel-receipt`

Use this when an action mutates state or needs post-action auditability.

Required fields:

```text
id
app
status
receipt
emitted_after
must_include
recovery
```

```mcel-grammar
id: grammar.block.mcel-receipt
status: specified
block: mcel-receipt
purpose: Defines the post-action record that proves what changed, what did not change, and what recovery path exists.
required_fields:
  - id
  - app
  - status
  - receipt
  - emitted_after
  - must_include
  - recovery
```

### `mcel-boundary`

Use this when an app must keep two concepts separate.

Examples:

```text
save is not publish
preview is not commit
commit is not push
explain is not calculate
browse is not modify
suggest is not apply
```

Required fields:

```text
id
app
status
boundary
left_side
right_side
rule
prohibited_confusion
```

```mcel-grammar
id: grammar.block.mcel-boundary
status: specified
block: mcel-boundary
purpose: Defines a separation law between two actions, states, systems, or responsibilities that users and code must not conflate.
required_fields:
  - id
  - app
  - status
  - boundary
  - left_side
  - right_side
  - rule
  - prohibited_confusion
```

### `mcel-risk`

Use this when a risk category needs app-specific explanation.

Required fields:

```text
id
app
status
risk
applies_to
requires
must_not_allow
```

```mcel-grammar
id: grammar.block.mcel-risk
status: specified
block: mcel-risk
purpose: Defines a risk class, the intents it applies to, and the evidence/confirmation/recovery obligations it creates.
required_fields:
  - id
  - app
  - status
  - risk
  - applies_to
  - requires
  - must_not_allow
```

### `mcel-adapter`

Use this to connect docs to an executable semantic adapter.

Required fields:

```text
id
app
status
adapter
current_runtime_status
target_runtime_status
required_intents
readiness_gate
```

```mcel-grammar
id: grammar.block.mcel-adapter
status: specified
block: mcel-adapter
purpose: Defines the semantic adapter target and the truth-gated readiness conditions.
required_fields:
  - id
  - app
  - status
  - adapter
  - current_runtime_status
  - target_runtime_status
  - required_intents
  - readiness_gate
```

### `mcel-layout-pattern`

Use this when a repeated layout grammar appears across apps.

Required fields:

```text
id
status
pattern
regions
responsibility_law
applies_to
```

```mcel-grammar
id: grammar.block.mcel-layout-pattern
status: specified
block: mcel-layout-pattern
purpose: Defines a reusable responsibility-based layout pattern across apps.
required_fields:
  - id
  - status
  - pattern
  - regions
  - responsibility_law
  - applies_to
```

### `mcel-source-binding`

Use this when a requirement must trace to likely code owners.

Required fields:

```text
id
app
status
target
source_candidates
binding_confidence
verification
```

```mcel-grammar
id: grammar.block.mcel-source-binding
status: specified
block: mcel-source-binding
purpose: Defines likely source ownership for a requirement, region, intent, or finding.
required_fields:
  - id
  - app
  - status
  - target
  - source_candidates
  - binding_confidence
  - verification
```

### `mcel-test-binding`

Use this when a requirement must trace to tests or missing tests.

Required fields:

```text
id
app
status
target
test_candidates
missing_tests
verification
```

```mcel-grammar
id: grammar.block.mcel-test-binding
status: specified
block: mcel-test-binding
purpose: Defines existing and missing test coverage for a requirement, use case, intent, or finding.
required_fields:
  - id
  - app
  - status
  - target
  - test_candidates
  - missing_tests
  - verification
```

```mcel-grammar
id: grammar.block.mcel-runtime-check
status: specified
block: mcel-runtime-check
purpose: Defines a runtime-observable diagnosis check derived from the app contract.
required_fields:
  - id
  - app
  - status
  - mode
  - contract
  - check
  - severity
  - observes
  - expects
```

## Runtime-observable contract checks

`mcel-runtime-check` turns a requirements claim into a diagnosis contract that the
running app can observe. It does not repair the app and it does not prove backend or
adapter readiness. It defines what the browser can inspect when a mode claims a region,
surface, or boundary is healthy.

Use runtime checks for facts like:

```text
- exactly one primary editor surface is visible
- the primary surface has useful geometry
- required regions are present and visible
- forbidden diagnostic surfaces are hidden in normal mode
- a lifecycle event such as file selection preserves the same contract
- the next probe should inspect layout ownership, overlays, panes, or editor surfaces
```

Runtime checks are intentionally tied back to normal MCEL blocks:

```text
mcel-region          says which UI regions matter
mcel-requirement     says what MUST / MUST NOT be true
mcel-boundary        says which surfaces must not be confused
mcel-acceptance      says which lifecycle facts should be checked
mcel-runtime-check   says how to observe those claims in the live browser
```

A runtime check may use compact `id | selector | label` list entries for fields such as
`required_regions` and `forbidden_regions`. Registry tooling normalizes those entries
into browser-side diagnosis contracts.

Runtime checks can also define shared health-contract policies:

```text
primary-surface              one named work surface must be visible and useful
required-regions-visible     named UI regions must exist and be visible
forbidden-surfaces-hidden    named diagnostic/proof/fallback surfaces must not be visible
secondary-surface-policy     optional support regions may be visible without becoming primary
surface-exactly-one-visible  only one authoritative work/editor surface may be visible
geometry-minimum             a surface or region must preserve useful width and height
overlay-policy               overlays are allowed only where the mode contract assigns them
lifecycle-contract-preserved startup, route, file-click, mode-switch, and resize preserve the same contract
source-test-ownership        findings include likely source and test owners when known
```

A runtime check may also declare normalized diagnostic fields:

```text
check_category     stable bucket such as surface, layout, overlays, lifecycle, ownership, or geometry
focus              the narrower probe focus such as primary-editor, right-pane, panes, or resize
optional_regions   compact id | selector | label entries for secondary regions that may be visible
allowed_regions    compact id | selector | label entries for explicit controls, counters, rails, or panes
geometry_policies  named geometry laws the runtime should evaluate when it has measurements
overlay_policy     named overlay laws that separate contained diagnostics from leaked overlays
```

Optional regions are not failures merely because they are hidden, collapsed, tabbed, or
trigger-only. They become findings only when the app-specific policy says their visible
state covers the primary surface, breaks the minimum geometry, or creates a competing
authority.

Overlay policy is deliberately softer than an app's primary surface law. A visible widget
editor, proof surface, or floating diagnostic tab should be reported with selector and
box evidence, but it should not automatically turn a healthy primary surface into a
failed app unless the app-specific contract marks that overlay as a hard boundary.

## Codebase-derived aspects

Requirement blocks should use MCEL aspects that match the app blueprint layer when
possible:

```text
overview
objects
workflows
layout
actions
capabilities
evidence
source
tests
annotations
findings
repair
```

## Codebase-derived layout zones

Region blocks should use these generic layout zones when possible:

```text
identity
navigation
primary
inspector
evidence
actions
status
advanced
```

The region may also declare an app-specific `region`, but `layout_zone` should map it
back to the generic vocabulary.

## Repeated layout patterns discovered so far

### Tool / deterministic workspace

Seen in Calculator.

```text
identity/header
scenario or expression workspace
input/control strip
result/evidence panel
graph or visualization surface
explanation panel
status strip
advanced boundary
```

Core law: deterministic domain output is authoritative; helper explanation is secondary.

### Navigation + list + preview

Seen in File Explorer and likely useful for Website Builder assets, Code Editor file
trees, and Git Tools working-tree views.

```text
identity/header
navigation roots
path/search controls
primary collection list
preview/inspector panel
classification/evidence strip
status strip
advanced boundary
```

Core law: browsing and previewing are read-only unless a separate mutation intent is
explicitly selected.

### Authoring + review-before-write

Seen in Code Editor and Website Builder.

```text
object navigation
primary authoring surface
draft/dirty state
preview or diff evidence
review/approval controls
save/apply receipt
status strip
advanced boundary
```

Core law: suggestion, preview, and review do not equal write/apply.

### Authoring cockpit with right assistant pane

Seen in the Code Editor target layout and useful for other source/design tools that
need a large primary authoring surface plus a secondary assistant or diagnostics pane.

```text
left navigation/explorer
central primary authoring surface
optional right assistant/diagnostics pane
bottom status strip
mode-gated preview/proof/debug surfaces
```

Core law: the right pane is an allowed secondary surface, not a second primary editor.
It may contain diagnostics, assistant output, contract findings, ownership hints, and
MCEL tools, but it must collapse before the primary surface becomes unusable.

```mcel-layout-pattern
id: layout-pattern.authoring-cockpit-with-right-assistant
status: specified
pattern: authoring-cockpit-with-right-assistant
regions:
  - navigation/explorer
  - primary authoring surface
  - optional right assistant or diagnostics pane
  - persistent status strip
  - mode-gated preview/proof/debug boundary
responsibility_law: >
  Every visible surface in authoring mode must have an owned region, role, and
  runtime-check policy. The central primary surface remains authoritative; the
  right pane supports diagnosis, assistance, and ownership evidence without
  replacing or covering the primary work surface.
applies_to:
  - code-editor
```

### Governed mutation / repository workflow

Seen in Git Tools and Website Builder publishing handoff.

```text
repository or project identity
state/evidence surface
action planner
preflight panel
confirmation panel
receipt/recovery panel
status strip
advanced Git plumbing
```

Core law: local mutation and remote mutation are separate; remote mutation is never
implicit.

## Required safety boundaries

Every app requirements doc should explicitly cover relevant boundaries:

```text
save is not publish
preview is not commit
commit is not push
explain is not calculate
suggest is not apply
browse is not modify
inspect is not execute
stage is not commit
commit is not push
local checkpoint is not remote sync
```

These phrases are not slogans; they identify app boundaries that should become
requirements, intents, or findings when an app violates or obscures them.

## Minimum complete app requirements document

A complete first-pass app requirements document should include:

```text
1 mcel-app
at least 1 mcel-use-case
at least 1 mcel-object or clearly named dominant_object
at least 5 mcel-region blocks, or enough to cover the live app shell
at least 5 mcel-requirement blocks
at least 5 mcel-intent blocks, including prohibited intents where relevant
at least 1 mcel-acceptance block
at least 1 mcel-finding block for known gaps
```

For high-risk apps, add:

```text
mcel-risk
mcel-evidence
mcel-receipt
mcel-boundary
mcel-adapter
```

For implementation planning, add:

```text
mcel-source-binding
mcel-test-binding
```

## Parser requirements

The first parser does not need to understand all YAML features. It should support the
current fenced block form. In prose, this means a Markdown fence named after the
block type followed by YAML-like top-level fields, for example:

    <fence named mcel-requirement>
    id: app.family.name
    app: app-id
    status: specified
    ...
    </fence>

Parser requirements:

```text
- discover all fenced blocks named mcel-*
- preserve the block type
- read top-level scalar fields
- read simple list fields
- reject duplicate IDs
- reject missing required fields
- reject unknown status values unless explicitly allowed by the grammar
- report warnings for planned/current ambiguity
- never treat prose as implementation proof
```

## Truth-gate rule

A requirement doc can say what should be true. It cannot make the live app semantically
ready by itself.

MCEL readiness must be derived from implementation evidence:

```text
documentation contract
+ app blueprint / workbench spec
+ semantic adapter registry
+ intent coverage
+ recovery coverage
+ tests / receipts
= readiness claim
```

The docs are the source of product intent. The adapter registry and tests are the source
of runtime truth.

## Phase D requirements registry

The first machine-readable registry is implemented by:

```text
tools/mcel_requirements_registry.py
tests/test_mcel_requirements_registry.py
```

It reads `pretty_docs/*.md`, extracts every fenced `mcel-*` block, preserves source
file/line evidence, validates stable IDs, loads required-field rules from the
`mcel-grammar` blocks in this document, classifies intent risk, and emits JSON plus
a Markdown report and a compact MCEL Lab app-comparison payload.

The registry started in adoption mode so early docs could become machine-readable
without pretending that all existing blocks were strict-schema complete. The current
normalized app docs are now strict-schema clean: the registry reports zero warnings and
`strict_schema_ready: True` under `--strict-schema`.

The remaining truth gate is implementation evidence. Strict docs mean the requirements
are parseable and complete enough to compare; they do not prove that the live app has
implemented every requirement.

Useful commands:

```bash
python tools/mcel_requirements_registry.py
python tools/mcel_requirements_registry.py --json --no-blocks
python tools/mcel_requirements_registry.py --json --output runtime/mcel-requirements-registry.json
python tools/mcel_requirements_registry.py --report
python tools/mcel_requirements_registry.py --lab-json
python tools/mcel_requirements_registry.py --strict-schema
```

The registry is a report and planning input, not an automatic code generator. Code
changes should still come only after findings, source bindings, and acceptance checks
are clear. The browser-side `McelRequirementsRegistry` API exposes the compact Lab
payload and can compare app contracts with live `McelDomainAdapterRegistry` readiness
snapshots.

The first normalization pass is region-focused. Every parsed `mcel-region` block
should now include:

```text
region         stable layout-region name
role           product role or responsibility class
responsibility one-sentence ownership law for the region
```

That normalization is intentionally about product responsibility, not CSS coordinates.
It lets the registry compare apps by what each region owns: navigation, primary work,
inspection, evidence, governed actions, status, advanced boundaries, or helper
companions.

## Practical next phase

The next phase after the registry is to connect it to MCEL Lab and app truth gates:

```text
read the requirements registry
compare docs to app blueprints
compare intents to adapter coverage
create MCEL Lab findings for gaps
list missing acceptance tests
show which apps are parseable, strict-schema-ready, adapter-ready, and verified
```

The docs are the source of product intent. The registry makes that intent inspectable.
Adapters, tests, receipts, and browser evidence remain the source of implementation truth.
