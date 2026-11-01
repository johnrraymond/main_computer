# MCEL V1 Product Contract

**Authority:** normative description of the *finished target* for MCEL V1. **Certification status:** not certified by publication of these documents. For currently implemented behavior and evidence see [MCEL Status and Roadmap](mcel-status-and-roadmap.md). These documents never override actual generated proof or claim that an absent capability exists.

## Promise

MCEL enables an author to create a working application by describing its domain behavior, semantic HTML hierarchy, user-facing purposes, and design intent. MCEL constructs and operates a coherent, accessible, responsive interface, without requiring the author to engineer the surrounding visual layout. **The author specifies meaning and product choices; MCEL supplies the design expertise and its mechanical implementation.** The system must neither invent executable application behavior nor discard supported semantic meaning.

## One application format

An ordinary MCEL V1 application has two complementary, authored sources in one app package:

- `application.html`: valid HTML5 expressing logical containment, meaningful order, landmarks, stable region identity, purpose, tasks, content references, and optional soft design preferences.
- `application.js`: the *one official* vanilla-JavaScript `@mcel/app` DSL, defining typed data, identity, state, operations, effect authority, capabilities, and proof/behavior requirements.

Both compile together into **one** canonical Application IR. The author does not put HTML in JavaScript template strings, maintain a second nested layout tree, or supply separate hand-written grids/CSS to fulfill the standard path. A specialized functional component (for example Monaco) is permitted as a *capability*; it does not own the surrounding app composition. The required package, bindings, and examples are specified in [V1 Authoring Format](mcel-v1-authoring-format.md).

## Responsibility boundary

| Author owns | MCEL owns |
| --- | --- |
| Domain entities, real data sources, stable identity rules | Faithful data presentation, record/selection projections |
| Inputs, commands, outcomes, state transitions, effects, permissions | Correct binding, transaction/receipt presentation, error handling |
| Logical HTML hierarchy, semantic regions and meaningful reading order | Choice and realization of suitable widgets, presentation patterns and geometry |
| Purpose, task focus, importance, optional nonbinding placement/density preferences | Default design policy, grouping, constraints and responsive adaptation |
| Essential workflow decisions not established by code | Inferable mechanics; diagnose genuine ambiguity rather than invent intent |
| Application acceptance expectations | Compiler, runtime, independent browser tests and design acceptance evidence |

The HTML hierarchy is *logical*, not a declaration of CSS geometry. Operational semantics cannot be changed by `data-mcel-*` hints. Author-specified region relationships must not create dataflow, operations, state transitions or write authority.

## Canonical compilation

The compiler validates both sources, all cross-file references and semantic conflicts. It preserves application identity, parentage, meaningful child order, types, relations, dataflow, capabilities and provenance in one deterministic normalized IR. Unknown supported-namespace attributes/options, broken references and incompatible statements are **errors**, never silently dropped. Compiler internals and legacy adapters may exist, but cannot become competing authored truths. See [Compiler, Layout and Runtime Contract](mcel-v1-compiler-layout-runtime-contract.md).

## Expert semantic design

MCEL interprets the [closed V1 Semantic Vocabulary](mcel-v1-semantic-vocabulary.md), HTML landmarks, author constraints, and executable semantics to select appropriate interface structures, widgets, and responsive organizations. It supplies default layouts of professional quality for navigation, document work, monitoring, collections, inspection, commands and computation. It may consider bounded alternatives and make deterministic selections. **No app-name-specific layout branches, silent recreation of legacy DOM/CSS, or continuous runtime redesign.** A declared soft preference can yield to correctness, accessibility, and available space, with a traceable design reason.

## Runtime and proof

The generated interface binds to real data, live updates, dynamic identities, selection, edits, authorization, receipts, error states and lifecycle as declared and permitted. Operational effects require their actual authorization. An interface is not complete if controls are missing, results are fictional, state is lost, a permitted operation is inaccessible, or a prohibited effect is exposed. Responsive behavior may adapt via preselected policies without re-synthesizing the app whenever data changes. Keyboard use, focus order, accessible status and scroll ownership are MCEL obligations.

## Scaffolding

The existing `tools/mcel_create_app.py` is the intended one-command starter entry point. **V1 target output** includes `application.html`, `application.js`, minimal requirements/tests, package authority where required, and compiler-owned generated output; it does **not** require authored layout.js or existing app host markup. The existing scaffolder's actual output remains documented in [Application Scaffolding](mcel-application-scaffolding.md); this target is not a statement that its generator has already been upgraded.

## Four-app qualification

The V1 qualification suite contains **File Manager (File Explorer), Task Manager, Code Editor and Calculator**. Each must be fully authored under the same HTML+JS contract, compile via the same canonical pipeline, and pass real hosted behavior and practical design acceptance. Existing function and authorization must not be silently removed to make a reference pass. Website Builder and MCEL Lab are expressly **not** members of this qualification suite. The detailed matrix is [V1 Four-App Acceptance](mcel-v1-four-app-acceptance.md); [Reference Authoring Examples](mcel-v1-reference-authoring-examples.md) show *target syntax only*.

## Definition of V1 complete

V1 is certified only when **all four** reference apps: (1) have authored two-file semantic sources and no manual outer layout; (2) compile without semantic loss; (3) have appropriate and coherent MCEL-generated interfaces; (4) preserve full required operational capabilities, identity, effects and authority; (5) pass responsive, accessibility, lifecycle, refactor and independent real-browser acceptance; and (6) receive documented human design-acceptance decisions. Compilation-only or no-overflow-only green signals do not constitute success. Four applications demonstrate a general architecture across those categories, **not** universal perfection for every possible application.

## Compatibility and non-goals

Published V1 authoring, semantic and runtime meanings must remain compatible with future MCEL versions. Explicit versioned vocabulary names and design-policy pins may be added later, but are **not** part of authored V1 reference examples or this documentation patch. Historical host-bound packages and prior experiments remain supported or documented according to their existing proven status; they are *not* V1's standard authoring target. There is no author-hour/comparative-labor benchmark as a V1 acceptance gate. This document specifies the target, not its implementation or an authorization to modify source code.
