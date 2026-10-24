# MCEL Authoring Mission and Guiding Principles

## Purpose and authority

**MCEL's principal product promise is a library for authoring good-looking, correctly functioning web applications with minimal effort and maximal flexibility, constrained by good engineering and user-interface practices rather than by the author's knowledge of the framework's internals.**

The library's parts should know their own responsibilities, relationships, valid combinations, and lifecycle obligations. Authors should be able to express what they want, compose those parts, and obtain a coherent application without manually recreating state wiring, layout glue, event plumbing, accessibility behavior, validation, and recovery rules. MCEL should generate the mechanical implementation and prevent unworkable compositions *where it has enough semantic information to do so*.

This document governs **development direction, design reviews, and evaluation priorities**. It is not a claim that today's MCEL already fulfills all four principles. The existing executable guarantees remain governed by `pretty_docs/mcel-contract-guarantees.md`, the user-space clauses by `pretty_docs/mcel-user-space-contract.md`, and current source-bound certification by `pretty_docs/promise-coverage-mcel.md`. Where a desirable behavior has no validated general implementation, label it as a **target** and retain the missing obligation. Do not declare it proven because an app renders or a design document describes it.

This is an **authoring** project. Reverse-engineering an unfamiliar application may help with migration or later tooling, but is not a prerequisite for the main promise and must not outrank ordinary new-app construction in development priorities.

## The four guiding principles

### 1. Minimal authoring effort

The author expresses each **independent semantic decision once**: application meaning, important identity, state ownership, intended behavior, necessary authority, meaningful design choices, and domain-specific exceptions. MCEL supplies the repeatable mechanical consequences of those decisions.

An MCEL author should not need to know its internal renderer, reconciliation strategy, generated selectors, dock-tree implementation, proof plumbing, or lifecycle code simply to create a conventional UI correctly. Ordinary controls and application structures should have useful, discoverable defaults. When information really cannot be derived safely, request the **smallest missing semantic decision**, at the authoring site, with a usable explanation and correction.

A reduction in source length is not success if the work resurfaces as manual CSS patches, scattered callbacks, bespoke adapters, debugging sessions, or repeated framework-specific declarations.

**Measure**: independent author decisions; authored lines and declarations; manual wiring/styling; steps from specification to working preview; corrective iterations; and amount of framework-internal knowledge required. Record generated output separately from author-written input.

### 2. Attractive results by default

MCEL should make visual competence the default: legible hierarchy, spacing rhythm, typography, sensible density, alignment, responsive containment, focus and interaction states, accessible controls, and coherent use of theme and chrome. A newly composed screen should look deliberate before the author writes corrective CSS.

Good-looking is not a single mandatory visual style. MCEL should support restrained, dense, playful, editorial, and other valid product designs. Defaults should be attractive; intentional aesthetic departures should remain possible through semantic choices, themes, tokens, and governed layout controls.

Layout should adapt to actual content and available space, not merely to a hard-coded screenshot. Component contracts should handle overflow, shrink/fill behavior, scrolling ownership, overlay positioning, and information hierarchy wherever those concerns are generic.

**Measure**: uncorrected initial render quality in contrasting applications; content-length and viewport variation; responsive and keyboard behavior; overflow/collision/focus failures; and human review of hierarchy and polish. Automated geometry checks help, but cannot by themselves prove aesthetic quality.

### 3. Maximum flexibility within sound practices

Authors should be able to express different application structures, visual directions, navigation patterns, and interaction flows **without escaping the library for ordinary valid cases**. The library must not mistake an unconventional but sound design for an invalid one.

Separate **hard invariants** (such as unique identity where required, state/effect ownership, authorized mutation, accessible interaction semantics, and impossible layout constraints) from **defaults and preferences** (such as arrangement, density, typography, color, responsive strategies, and presentation variants). Hard invariants may be rejected with an actionable diagnostic. Defaults must be overridable within their soundness constraints; preference conflicts should be explained or remediated without silently rewriting product intent.

Extension points should be compositional and stable. Adding one valid variant should not require an application-specific patch to shared machinery or a private competing layout/runtime system.

**Measure**: contrasting apps and visual styles produced through the same public contracts; legitimate overrides that remain valid; frequency of raw-CSS, DOM, or runtime escape hatches; and whether a new feature requires application-specific framework changes.

### 4. Correctness through composition

Each component or semantic primitive should declare what it **owns, requires, provides, emits, accepts, and refuses**. When components are combined, MCEL should enforce their relationship and derive the mechanical wiring needed for the composition to function.

For example, a form field knows how it contributes a typed value; a submit control knows which governed form/action it activates; a collection knows its stable row identity; an overlay knows focus and dismissal obligations; a layout region knows containment and scroll ownership; and a consequential action knows its authority, effect, confirmation, receipt, and recovery policy. Those are examples of the **target composition contract**, not a statement that every primitive exposes these APIs today.

MCEL should reject or explain an unworkable composition **before** it becomes an obscure runtime defect when static evidence suffices; dynamic guarantees still require browser/effect evidence. Never turn a failed constraint into a silent success or silently authorize an effect. Repair should regenerate owned machinery from valid source meaning rather than encouraging authors to hand-edit generated output.

**Measure**: composition failures prevented at authoring/compile time; actionable diagnostic quality; runtime/acceptance failures that escape earlier checks; negative controls (invalid identity, missing bindings, contradictory layout, unauthorized effects, unsafe state transitions); and the proportion of ordinary app wiring generated rather than authored.

## What MCEL must infer, ask, and refuse

| Kind of decision | Library responsibility | Author responsibility |
| --- | --- | --- |
| Generic implementation mechanics | Generate IDs and bindings with known derivation, focus/validation plumbing, lifecycle cleanup, common responsive behavior, source-safe runtime artifacts | Do not reimplement the generated machinery |
| Product semantics | Surface meaningful options; preserve declared intent | Specify data meaning, important identity, business decisions, permissions, and exceptions |
| Appearance | Supply good defaults and sound constraints, with expressive semantic themes/layout choices | Select distinctive aesthetic direction or override defaults when desired |
| Ambiguity | Identify the smallest uncertain decision and offer a reasoned choice | Decide where two reasonable products legitimately differ |
| Invalid composition | Refuse with a stable, local, actionable diagnostic; never fabricate a proof | Change the authored semantics or an incompatible constraint |
| Consequential effects | Enforce declared authority, reconcile effects and receipts, fail closed | Authorize actual mutations and their applicable policies |
| Verification | Provide tests, browser observations, and honest claim scopes | Evaluate whether the application satisfies the actual product requirement |

**Decision rule:** infer the mechanical; default the presentational; ask for the genuinely semantic; refuse the unsound; permit the sound but unusual.

Good practices are design constraints and well-founded defaults, **not an excuse to limit expressive freedom**, and no library can guarantee that arbitrary product requirements will be good without the author's judgment.

## Practical authoring loop (target experience)

1. **Declare the application** in ordinary domain terms and select a presentation direction. Prefer the supported semantic DSL/package path over manually assembling internal mechanisms.
2. **Compose known primitives** for structure, controls, interactions, state, and effects. MCEL supplies default layout and interaction contracts.
3. **Resolve only the missing decisions** using narrow diagnostics; no framework archaeology should be needed to get a simple form, collection, or workspace to function.
4. **Preview the actual result** with representative content, keyboard interaction, and multiple sizes. Author controls valid customization; MCEL resolves generic layout and binding mechanics.
5. **Verify and repair**: check visual containment/accessibility, semantics, independent behavioral evidence, effect authorization, and source-safe regeneration. Keep failed/unknown evidence visible and reversible.

This sequence is a **development target**, not a promise that one present CLI command delivers the entire workflow today. For actual supported entry points, use `pretty_docs/mcel-dsl-app-authoring-surface.md` and `pretty_docs/mcel-canonical-app-authoring.md`.

## Evaluation: how to decide what to build next

Every MCEL feature proposal or regression should answer four questions, in order:

1. **Author effort:** Which unnecessary decision or mechanical step does MCEL remove? Does it move any work elsewhere?
2. **Default appearance:** What happens on first render with realistic content and responsive constraints, before corrective styling?
3. **Flexibility:** Which valid alternative designs or behaviors can the same public machinery express? What new escape hatch, if any, becomes necessary?
4. **Composition correctness:** What invalid combinations are refused, and what valid combinations automatically function? Which independent negative and browser/effect checks establish that distinction?

A feature that reduces code while degrading visual quality, excluding valid patterns, or concealing unowned effects **does not fulfill the authoring promise**. A feature that makes one reference application green through special cases is not yet generalized.

### Required evidence, without pretending it already exists

- A **greenfield authoring task**, rather than only migration or extraction, performed through the documented public authoring surface.
- At least two **contrasting** application shapes or presentation styles, so a single preferred layout cannot stand in for flexibility.
- A **default-render review** with varied viewport sizes, data volumes, long strings, empty/error/loading states, and keyboard/focus paths; retain human aesthetic review beside objective checks.
- **Negative composition controls** that must fail predictably, plus valid unconventional cases that must still work.
- An **author effort accounting** that distinguishes independent decisions from mechanical declarations, generated code, CSS overrides, manual fixes, and undocumented knowledge.
- Exact, independent **semantic and effect proof** before making claims of correctness. Missing coverage is not success.

Thresholds and qualification datasets must be fixed *before* an experiment, never retrofitted to a preferred outcome. The more specific AI migration benchmark in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` remains applicable to its own scope; it must not be misrepresented as proof of attractive defaults or beginner usability.

## Relation to the existing promises

The original source/runtime promises are necessary **enablers**: durable authored semantics, disposable runtime generation, clean serialization, bounded repair, evidence-not-trust, transient browser facts, and narrow reversible adoption. These remain exactly scoped to their defined contracts.

The platform promises—semantic runtime, portable fixtures, layout generalization, governed effects, unseen-app reconstruction, AI authoring/repair, and development advantage—describe more ambitious evidence targets. Their names and verification status are not replaced by these four principles. The priorities above supply the **reason to implement and assess them**:

| Existing promise or effort | Relation to authoring mission |
| --- | --- |
| Source semantics / serialization / repair | The author describes meaning once; generated implementation remains replaceable |
| Semantic runtime / portable fixtures | Independently verify correct functioning across different app shapes and hosts |
| Canonical layout generalization | Default polish and responsive composition without per-app layout patches |
| Governed consequential effects | Interactions behave correctly and cannot silently perform unauthorized work |
| Narrow reversible adoption | Authors can try MCEL without losing the last working application |
| AI authoring / repair | AI or human authors spend decisions on product meaning rather than framework plumbing |
| Development advantage | Measure actual authoring effort, default quality, flexibility, and reliability—not just source-token reduction |
| Unseen-app reconstruction | A possible migration tool; **not a prerequisite** to fulfilling the primary greenfield authoring promise |

## Current limits and near-term order

As of the **October 23, 2026 canonical promise report** supplied for this repository generation: 7 of 14 bounded claims are verified; 6 larger experimental claims are unverified; the all-enrolled-app semantic-runtime claim fails because Website Builder's 12 runtime bindings remain unproven. Five of six enrolled semantic-runtime apps are proven, and the portable Calculator/Code Editor 19-intent fixture certificate is independently admitted. These facts are **not** proof that a novice can create a polished new app with minimal effort.

For now, preserve the five proven applications and the existing global gate. **Do not change or disenroll Website Builder to make the gate green.** Develop and verify the authoring principles using an explicitly bounded active-app scope, with Website Builder's separate failure still visible.

The next engineering work should prioritize the biggest gaps between an author's small semantic description and a good-looking, functioning, flexible result: composition contracts, polished reusable defaults, general layout behavior, and diagnostic/repair quality. Independent authoring trials come when their required public mechanisms exist; we need not demand an external novice trial before building those mechanisms.
