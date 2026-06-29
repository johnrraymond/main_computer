# MCEL User-Space Contract

MCEL needs a user-facing contract that is as easy to plan around as a framework contract. React can be explained to a builder as a small set of durable rules: components render from props and state, effects run after render, keys control identity, and callers must not mutate state directly. MCEL needs the same kind of planning surface.

This page is that surface. It does not describe internal "laws" as magic. A law module is implementation detail. A MCEL user should plan around the contract below.

## The short contract

Use MCEL when you want this bounded promise:

> Author-owned `data-mc` source traits are the durable input. MCEL may generate runtime structure from those traits, but generated runtime structure is discardable, repairable, and must be stripped before save/export. MCEL reports validation, proof, browser facts, and adoption readiness as gates; callers must fail closed when those gates fail.

That is the MCEL equivalent of a framework mental model. It tells you what you can rely on and what you must not assume.

## The planning model

| Concern | MCEL user contract | What not to assume |
| --- | --- | --- |
| Source | `data-mc` plus supported `data-mc-*` traits are the author-owned planning surface. | Non-MCEL DOM is not automatically owned by MCEL. |
| Runtime | Generated parts marked `data-mc-generated="true"` are rebuildable runtime output. | Generated DOM is not a stable authoring format. |
| Serialization | `MCEL.serialize()` / `McelLabEngine.serializeRuntimeRoot()` are the source firewall. | Raw runtime `innerHTML` is not safe to save. |
| Repair | Repair regenerates MCEL-owned runtime structure from existing source traits. | Repair does not recover deleted source semantics or infer missing product intent. |
| Validation | Reports are gates: failed, uncovered, or warning states must block trust. | Rendering is not proof. A law module is not a contract. |
| Browser facts | Observations are runtime snapshots for a specific DOM, CSS, viewport, and content state. | Browser facts are not durable source policy. |
| Adoption | MCEL owns only named workflows with named clauses and evidence. | One passed workflow does not make MCEL the default platform everywhere. |

## Stable user-space clauses

The machine-readable contract lives in:

```text
main_computer/web/applications/scripts/mcel-contract.js
```

It is exposed as:

```js
McelLabContract.buildUserSpaceContract()
McelLabContract.listUserContractClauses()
MCEL.buildUserSpaceContract()
MCEL.listUserContractClauses()
```

The current clause IDs are:

```text
mcel.user.source-traits-are-planning-surface.v1
mcel.user.runtime-generation-is-discardable.v1
mcel.user.serialization-is-source-firewall.v1
mcel.user.repair-is-bounded-regeneration.v1
mcel.user.validation-is-evidence-not-trust.v1
mcel.user.browser-facts-are-snapshots.v1
mcel.user.adoption-is-narrow-and-reversible.v1
```

Every user-space clause maps back to one or more executable contract guarantees. Each clause states what MCEL users can rely on, what they must provide, what they must not assume, and the fail-closed signal that blocks trust. If a clause cannot be tied to an executable guarantee, it is not ready to use as a planning rule.

## What MCEL users can rely on

### 1. Source traits are the planning surface

If an element has `data-mc`, MCEL treats that element's supported `data-mc-*` traits as source-owned intent. Compile may normalize unsupported values, but normalization must be visible through warning events.

User rule:

```text
Plan with supported data-mc traits, not generated runtime markup.
```

### 2. Runtime generation is discardable

MCEL-generated nodes and runtime-owned attributes are outputs. They can be removed and rebuilt from source traits.

User rule:

```text
Keep authored content in source-owned elements. Treat generated MCEL parts as cache.
```

### 3. Serialization is the source firewall

Before saving or exporting MCEL-owned runtime DOM, use MCEL serialization. Serialization strips generated nodes, runtime-owned attributes, and MCEL runtime classes while preserving source-owned policy traits.

User rule:

```text
Do not persist runtime innerHTML. Persist serialized MCEL source.
```

### 4. Repair is bounded regeneration

Repair is not magic. It regenerates MCEL-owned runtime structure from existing source traits and reports what changed. It does not recover deleted source semantics or invent missing product intent.

User rule:

```text
Use repair for MCEL-owned generated structure, not arbitrary DOM correctness.
```

### 5. Validation is evidence, not trust

A UI that renders is not proven. A law module is not a contract. The trusted surface is a passing report with covered guarantees.

User rule:

```text
Treat failed guarantees, uncovered guarantees, a11y failures, serializerClean=false, and adoption hold verdicts as blockers.
```

### 6. Browser facts are snapshots

Layout, overflow, geometry, hydration, and performance observations are runtime facts for a specific DOM, viewport, CSS, and content state. They can guide diagnostics or repair, but they do not become source policy.

User rule:

```text
Re-observe after visual/context changes. Do not save browser facts as source.
```

### 7. Adoption is narrow and reversible

MCEL should own one named workflow at a time. A workflow must name the clauses it needs and the evidence that proves them.

User rule:

```text
Adopt MCEL for a workflow only when that workflow's user-space clauses pass.
```

## How to plan a feature with MCEL

Before using MCEL for a workflow, write the workflow decision in this shape:

```text
Workflow:
  Website Builder source-safe export

MCEL may own:
  source trait compilation
  generated runtime wrappers
  serialization cleanup

Required user-space clauses:
  mcel.user.source-traits-are-planning-surface.v1
  mcel.user.runtime-generation-is-discardable.v1
  mcel.user.serialization-is-source-firewall.v1

Fail closed when:
  serializerClean=false
  contract guarantees are failed or uncovered
  generated runtime nodes appear in serialized output

MCEL may not own yet:
  arbitrary non-MCEL DOM
  product-specific content validation
  visual correctness across unobserved browser states
```

That is the usable contract. It is narrow enough to test and strong enough to build around.

## Verification commands

Run the user-space contract tests:

```bash
python -m pytest tests/test_mcel_user_space_contract.py tests/test_mcel_contract_guarantees.py -q
```

Run the full targeted MCEL verification:

```bash
python tools/build_mcel_runtime.py
python -m pytest tests/test_mcel_user_space_contract.py tests/test_mcel_contract_guarantees.py tests/test_mcel_architecture_boundaries.py tests/test_mcel_lab_app.py tests/test_mcel_runtime_package.py -q
```

## Rule for future MCEL work

Do not explain a MCEL feature to users using internal law language first. Explain it as:

1. the user-facing promise;
2. the exact preconditions;
3. what the user must not assume;
4. the fail-closed signal;
5. the executable guarantee IDs that prove it.

If those five things are missing, MCEL has implementation terminology, not a user-space contract.
