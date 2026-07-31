# MCEL Status and Roadmap

This document is the canonical human-readable statement of current MCEL status for this repository snapshot. It does not replace generated registry or truth-audit output. When prose disagrees with generated evidence, the generated evidence wins.

## Authority order

Use MCEL information in this order:

1. Generated requirements-registry and repository truth-audit output.
2. This status and roadmap document.
3. `pretty_docs/mcel-system-guide.md`.
4. App requirements, semantic-adapter, surface, evidence, and contract documents.
5. Historical patch notes and design boundaries.

Requirements prose records product intent. Adapter registries record executable semantic coverage. Runtime and acceptance evidence record proof. None of those layers may silently stand in for another.

## Current generated baseline

The current snapshot produces the following requirements-registry baseline:

```text
schema: mcel-requirements-registry-v1
blocks: 304
apps: calculator, code-editor, file-explorer, git-tools, mcel-lab, website-builder
errors: 0
warnings: 0
strict_schema_ready: True
```

The repository truth audit currently runs in report-only mode across 21 registered app surfaces. With no repository-bound runtime or acceptance evidence supplied, it reports:

```text
blocking_apps: 0
truth_status_counts:
  partially-implemented: 14
  verification-incomplete: 7
declared_level_counts:
  legacy: 14
  runtime-baseline: 1
  semantic-runtime: 6
runtime_surface_proven: 0
semantic_runtime_proven: 0
promotion_ready: []
runtime_evidence_binding: absent
acceptance_evidence_binding: absent
```

This is not a failure of the registry. It means declared maturity and implementation presence are visible, while current repository-bound proof is absent.

## Declared application maturity

The app-surface registry currently declares:

| Declared level | Applications |
| --- | --- |
| `semantic-runtime` | Calculator, Code Editor, File Explorer, Git Tools, MCEL Lab, Website Builder |
| `runtime-baseline` | Document Editor |
| `legacy` | AI Control, Astrometric, Chat Console, Conductor, Email, Layout Builder, OnlyOffice, Spreadsheet, Spreadsheet Smoke, Task Manager, Terminal, Wallet, WebGL, Worker |

All six requirements-registry apps have registered domain adapters whose registry snapshots currently report `fullApplicationSemanticReady`. That is adapter coverage, not repository-bound runtime or acceptance proof. In the current audit, each of those six apps remains `verification-incomplete` because runtime and acceptance evidence are missing.

Document Editor remains intentionally parked at `runtime-baseline`. Its current live surface still uses the Patch 22b-era Pretty Docs library, document page, and Document AI companion. Patch 24a documents a later outline/modal-picker/docked-companion target; that target is not implemented in this snapshot. Patch 43 corrected the maturity declaration without claiming the target UI or a full semantic adapter.

## Implemented authority layers

The current repository contains these active layers:

| Layer | Current authority |
| --- | --- |
| Documentation-first product contracts | `tools/mcel_requirements_registry.py` and the parsed app requirements docs |
| Adapter readiness | `mcel-domain-adapter-registry.js` |
| Surface enrollment and declared maturity | `mcel-app-surface-registry.js` |
| Runtime conformance policy | `mcel-app-surface-conformance.js` |
| Aggregated app truth | `mcel-app-truth-gate.js` |
| Repository-wide reporting | `main_computer/mcel_truth_audit.py` |
| Runtime evidence provenance | repository-bound FLOG evidence inputs |
| Acceptance evidence | `main_computer/mcel_acceptance_runner.py` and binding catalog |
| Epistemic claim states | `mcel.epistemic-status.v1` |
| Read-only observation envelope | `mcel.observation-bundle.v1` |
| DOM/authored-accessibility observation producer | `mcel-browser-observation-producer.js` |
| SCM mutation isolation | canonical instances, revision-bound operation envelopes, duplicate refusal, and overlapping-operation refusal in `mcel-scm.js` |

## Documented MCEL milestones

The current docs record these relevant milestones:

- Patch 25a established the app truth gate.
- Patch 25b added FLOG and MCEL Lab consumers without creating a second aggregation authority.
- Patch 26 added the repository truth-audit consumer.
- Patch 27a bound evidence to repository provenance.
- Patch 28a added acceptance-evidence binding.
- Patch 32 extracted reusable semantic-adapter helpers from proven adapters.
- Patch 33 froze the shared semantic-adapter toolkit contract.
- Patches 37 through 42 expanded and promoted the Git Tools semantic adapter.
- Patch 43 parked Document Editor at `runtime-baseline` until its requirements and semantic adapter are truth-auditable.
- The read-only browser observation producer established deterministic DOM and authored-accessibility capture into repository-bound observation bundles using static fixtures.
- The producer hardening pass added exact surface-binding validation and deterministic capture ceilings before any additional lens or live-browser integration.

Patch numbers are historical labels, not current truth. Use generated outputs and the authority order above for present-state decisions.

## Authorized next code candidate

The next authorized MCEL code candidate is repository-bound deployed runtime and acceptance evidence for MCEL Lab.

This is an evidence-closing candidate, not a feature-expansion candidate. Its bounded implementation may:

- add a deterministic browser fixture that loads the deployed `/applications/mcel-lab` route at `1280 × 720`, `1440 × 900`, and the supported stacked-layout breakpoint;
- exercise the existing authoritative work surface, right-rail overflow behavior, responsive layout, and self-diagnosis without mutating application state;
- emit runtime evidence through the existing FLOG provenance boundary and execute the existing `mcel-lab.acceptance.semantic-runtime` binding through the acceptance runner;
- update only directly coupled evidence plumbing, tests, and documentation needed to bind both results to the exact repository fingerprint.

Completion requires all of the following:

- the deployed route, rather than a generated stand-in page, is the measured surface;
- exactly one authoritative work surface is observed;
- applicable desktop layouts preserve at least the current `640 × 420` surface contract;
- direct right-rail children are not internally clipped, overflow is scrollable when required, stacked layout returns to content-sized block flow, and sibling rectangles do not overlap;
- the truth audit reports `runtime_evidence_binding: exact` and `acceptance_evidence_binding: exact` for evidence generated from the same repository state;
- the requirements registry remains at 304 blocks with 0 errors, 0 warnings, and `strict_schema_ready: True`;
- no declared application maturity is changed.

Authorization ends at evidence production and binding. It does not authorize any application maturity promotion, new MCEL Lab product features, active browser exploration, browser mutation, repair application, or expansion of the observation producer.

The read-only browser producer in `mcel-browser-observation-producer.js` still requires a matching app, route, surface, and locator descriptor; proves that the locator resolves uniquely to the supplied attached root; and records the binding result in provenance. Capture continues to use the versioned `mcel.browser-observation.capture-limits.v1` policy with default limits and hard ceilings for elements, tree depth, facts, attributes, text payload, and state markers. Limit-driven omissions remain explicit, deterministic, and recorded as partial capture rather than silently presented as complete.

Redaction remains a non-functional contract stub: `mcel.redaction-policy.stub.v1` reports `not-implemented` and `redactedFactCount: 0`. It performs no masking and provides no sensitive-data protection. The producer continues to emit no verifying claims.

Layout, visual, source, transition, ridge, and general live-browser collection by the observation producer remain deferred. The authorized deployed-route fixture may collect only the bounded conformance and acceptance evidence listed above.


## Not authorized

The following remain out of scope until separately specified, policy-gated, and tested:

- active browser exploration;
- autonomous clicks, typing, navigation, or workflow execution;
- source, state, runtime, filesystem, Git, publish, or network mutation;
- repair proposal application;
- semantic intent assignment from appearance alone;
- promotion of `observed` or `inferred` claims to `verified`;
- maturity promotion based only on a browser capture.

SCM operation guards remove one mutation-safety prerequisite. They do not authorize active exploration by themselves.

## Verification commands

Use these commands to re-establish the documentation baseline:

```bat
python tools/mcel_requirements_registry.py
python tools/mcel_requirements_registry.py --report
python main_computer/mcel_truth_audit.py
python -m pytest -q tests/test_mcel_documentation.py tests/test_mcel_documentation_authority.py tests/test_mcel_requirements_registry.py tests/test_mcel_app_truth_gate.py tests/test_mcel_truth_audit.py tests/test_mcel_observation_bundle.py tests/test_mcel_browser_observation_producer.py tests/test_mcel_document_editor_surface.py tests/test_mcel_document_editor_layout_fit.py
```

A clean update must preserve registry validity, avoid accidental changes to machine-readable requirements, keep the truth audit report-only unless explicitly configured otherwise, and leave exactly one MCEL code-authorization section in this document. That section currently authorizes only the bounded MCEL Lab deployed runtime and acceptance evidence candidate described above.
