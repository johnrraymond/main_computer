# MCEL Observation and Epistemic Contracts

This document freezes `mcel.observation-bundle.v1` and
`mcel.epistemic-status.v1`.

These are companion contracts to `mcel.semantic-surface-ir.v1`. They do not
change the existing surface IR, infer application behavior, operate controls,
or grant mutation authority. They establish how later reconstruction work must
separate facts from proposals and proof.

## Epistemic states

Every semantic claim entering the observation and reconstruction pipeline
carries one of six states:

| Status | Meaning | May satisfy a truth-gate requirement |
| --- | --- | --- |
| `declared` | An authored ridge or contract explicitly supplied the value. | No |
| `observed` | A browser, source, geometry, visual, or runtime lens directly captured the value. | No |
| `inferred` | A deterministic rule, heuristic, graph, embedding, or model proposed the value. | No |
| `verified` | A deterministic validator checked the claim and its evidence without unresolved contradiction. | Yes |
| `rejected` | Validation or counterevidence falsified the claim. | No |
| `ambiguous` | Evidence remains conflicting or underdetermined. | No |

Only `verified` is truth-gate eligible. A generator, model, adapter, authored
ridge, or observation collector cannot promote its own output by assigning
that status: a verified claim requires at least one passing deterministic
validator result, no failing validator result, and no unresolved
contradictions.

Each claim contains:

```text
claimId
subject
predicate
value
status
sources[]
confidence
contradictions[]
observedAt
repositoryFingerprint
validatorResults[]
requiredForTruthGate
truthGateRequirementIds[]
```

An inferred claim requires an explicit confidence, but confidence never
substitutes for verification.

## Authored semantics

Authored semantics remain the strongest direct statement of author intent, but
only within their explicit fields. A `declared` claim must point to an exact
`authored-ridge` or `authored-contract` locator, mark that source explicit, and
list the fields it actually declares. A declared role cannot silently become a
declared action, effect, precondition, or mutation permission.

Authorship is evidence, not self-verification. Declared claims still require an
independent deterministic validator before satisfying a truth gate.

## Conflicts

Claims sharing a subject and predicate are resolved without source precedence.
If their canonical values differ, the result is `ambiguous`, its value is
unset, and every competing claim is retained in `contradictions[]`. MCEL must
not choose the ridge, browser, model, or most confident source merely because
one source would make the pipeline continue.

## Observation bundle

`mcel.observation-bundle.v1` is repository-bound and read-only. It always
contains these lenses:

```text
dom
accessibility
layout
visual
source
transition
ridges
```

Each lens is `captured`, `missing`, or `unavailable`. Missing and unavailable
lenses require a reason. This prevents absence of evidence from becoming an
empty successful observation.

The bundle also contains:

```text
observationId
appId
route
mode: read-only
capturedAt
repositoryFingerprint
viewport
stateMarkers[]
provenance
lenses
claims[]
resolvedClaims[]
bundleFingerprint
```

DOM facts may include structure and attributes. Accessibility facts may include
roles, names, descriptions, states, and available actions. Layout facts may
include computed bounds and styles. Visual facts may identify screenshot,
canvas, or hard-object evidence. Source facts may bind components, symbols, and
code locations. Transition facts may record already-supplied runtime evidence,
but v1 does not execute actions to obtain it. Ridge facts preserve authored
MCEL declarations.

The canonical bundle fingerprint covers all observations, claims, resolutions,
provenance, and missing-evidence states. Reordering facts or claims does not
change it.

## Truth-gate boundary

The app truth gate accepts optional epistemic evidence. Existing applications
without epistemic evidence retain their current behavior while the new
pipeline is introduced. Once an input identifies a claim as required, that
claim must be present and `verified`. `declared`, `observed`, `inferred`,
`rejected`, and `ambiguous` required claims produce a blocking finding.

This compatibility rule allows the contract to land before browser capture
without letting provisional semantic reconstruction manufacture readiness.

## Explicit exclusions

This patch does not:

- collect a DOM snapshot or accessibility tree;
- perform active browser exploration;
- infer native or ARIA semantics;
- infer transitions, effects, or responsive layout;
- propose or apply repairs;
- authorize source, state, runtime, filesystem, Git, publish, or network
  mutation;
- make any existing MCEL claim proven.

Browser capture is the next layer after these contracts. Active exploration
remains prohibited until SCM canonical-state isolation, revision checking, and
duplicate-operation refusal are separately proven.
