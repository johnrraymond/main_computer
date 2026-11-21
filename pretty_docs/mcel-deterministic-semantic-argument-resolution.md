# Deterministic semantic layout and argument resolution

## Current authority (2026-11-20)

The normal MCEL pathway is **canonical HTML/JS → verified compiled IR →
verified workspace plan → direct constrained semantic graph match against
registered reusable template signatures → source-bound template arguments →
initial renderer → interaction/Chrome/scroll contract verification**.

No metric census, eigenvectors, spectral tripwire, application name, override
JSON, DOM ordering, or post-render `code-override.js` runs during template
selection or argument binding. `metric_census.py` remains an optional offline
measurement/research tool, not part of the runtime planner.

## How we reached this implementation

1. **Positional hardcoding** (`forms[0]`, `controls.slice(...)`) was shown to
   change semantic meaning when DOM/declaration order changed.
2. **Nested semantic hierarchy** established that canonical HTML can encode
   explicit containment. The shared workspace planner was extended to preserve
   nested groups, not flatten them.
3. **Spectral research** used cross-app eigenvectors, then a metric census of
   hundreds of features. These are useful for comparing implementations, but
   outlier status cannot determine a particular template's arguments.
4. **Deterministic resolver smoke** established declaration-order invariance,
   renaming equivariance, exact coverage, and fail-closed behavior. Initially it
   used `code-override.json` as a per-app answer sheet.
5. **Coupling acceptance** established source-verified effect/capability links,
   bounded overlays, scroll ownership, and Chrome continuity. It also exposed
   that a valid semantic assignment does not guarantee usable geometry.
6. **Metric-selector integration was an architectural mistake:** it placed four
   metric thresholds and the per-app JSON policy between semantic IR and the
   resolver. The correction eliminates both dependencies. Matching the
   reusable template's complete argument signature **is** selection.

## What the registered document-workbench template requires

- Exactly one document-editor anchored to the primary semantic workspace,
  verified source-write effect/capability relationship, start navigation,
  bottom status/dock, and an end-side assistance region.
- A **unique** consequential `run/task` form as the execution anchor.
- A **unique** external-read `inspect/document` form as the preview anchor.
- Remaining assistance controls must express recognized session inspection,
  save/reset/open operations, or task-job refresh operations. Unsupported
  operations are *refused*, never placed by declaration order or fallback.
- Results attach to their unique source producer. When a session operation and
  the unique primary run form write the same output, the run form owns its
  displayed result; competing *primary* result producers refuse ambiguity.
- Every member of the assistance region must be assigned exactly once. The
  algorithm does not infer effect authorization, source-write permission, or
  operation arguments.
- If the author supplied actual nested regions instead, those relationships
  take precedence and no inferred nested groups are added.

These are **template contract predicates**, not test-fitted metric cutoffs,
Code Editor identifiers, button labels, or per-application override rules.
They describe one reusable template, not a universal definition of code.

## Proof and limits

The normal route verifies the whole source-bound plan; the browser constructs
all inferred groups during initial rendering and checks exact node coverage.
`tools/mcel_semantic_layout_pipeline_smoke.py` and
`tools/mcel_semantic_argument_resolver_smoke.py` provide read-only acceptance.
The surface-coupling tests examine geometry, scrolling, Chrome, and other apps.

New template implementations require their own well-defined argument graph
signatures and ambiguity policies. If semantic facts cannot distinguish
candidate arguments, **refuse or request an authored semantic relationship**;
do not silently reinstate `code-override.json` or a positional fallback.

## Retired artifacts

Delete `mcel_apps/code-editor/code-override.json` and
`mcel_apps/code-editor/code-override.js` after installing this replacement.
Also remove the obsolete runtime selector
`main_computer/mcel_knowledge/semantic_template_classifier.py`. Keep the
standalone metric census if desired for offline analysis; it is not a runtime
layout dependency. See the install instructions in the patch verification
report for guarded PowerShell cleanup.
