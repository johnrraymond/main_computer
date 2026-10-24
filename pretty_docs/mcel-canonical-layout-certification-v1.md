# MCEL Patch 15 — Canonical whole-workspace layout certificate

## Why layout certification exists

One of MCEL's principal authoring responsibilities is to produce **good-looking, adaptable layouts by default** without forcing authors to understand layout internals or repair each application with custom CSS. This certifier is a bounded structural geometry authority toward that goal, not an aesthetic rating. Apply `pretty_docs/mcel-authoring-principles.md`: assess initial hierarchy and polish, varied content, valid alternative presentations, and invalid composition refusals in addition to geometric correctness. Its fixed Calculator/Code Editor matrix does **not** yet prove arbitrary layout generalization or freedom from application-specific authoring glue.

A design that intentionally differs from the default remains valid if it preserves the applicable hard constraints. A visually awkward default may meet this certificate and still require a reusable MCEL-library improvement.


## Contract

The independent certifier is `python tools/mcel_layout_certify.py --json`.
It recompiles the current DSL and reconstructs the package catalog and runtime
projection without using historical reports. It verifies the DSL semantic
fingerprint, package fingerprint, catalog fingerprint and runtime projection
fingerprint, then runs real Chromium against actual host HTML and CSS and the
projected ES modules via the generic MCEL host-bound runtime. No source is
changed. Each scenario must produce fresh observed evidence.

The browser checks the projected layout tree against the compiled IR tree,
measures every declared region, checks required visible nodes and modes,
independently reranks accepted candidates under the declared height/empty-cell/
row-count objective, and uses the independent atomic-label detector. It checks
runtime and layout authority, candidate verification, CSS track/area geometry,
control preservation, and unmount restoration. The Python adjudicator separately
requires complete node coverage and all mandatory evidence; an empty or
self-reported PASS alone is never sufficient.

The required scenario matrix is:

| Application | Required scenarios |
|---|---|
| Calculator | graph-wide 1140px; basic-wide 1140px; graph-compact 750px |
| Code Editor | dock-wide 1220px; dock-compact 1050px |

Every scenario must pass; unavailable Chromium, failed mount, deferred or
observed-only layouts, missing region, broken atomic label, incomplete candidate
search, unverified CSS, source changes during observation, and mismatch between
compiled and projected tree all refuse certification. The output includes
semantic, package, projection and input-content SHA-256 fingerprints, measured
node geometries and computed structural CSS, candidate quality, exact failure
reason and a deterministic SHA-256 evidence-report fingerprint.

## Scope limitation

A passing report has scope `host-bound-canonical-layout-only` and
`fullSemanticRuntimeProven: false`. The test substitutes an intent facade that
**refuses intent execution**: it proves layout authority and module/binding
availability, not behavior of Calculator arithmetic or Code Editor file writes.
It does not assert a distinct package-host truth-gate proof, a full-semantic-
runtime certificate, accessibility audit or arbitrary application support.

Option `--with-intent-proof` additionally invokes the *existing independent*
Phase One acceptance/IR-native browser proof. This produces scope
`host-bound-canonical-layout-only+host-bound-ir-native-intents` only when that
separate proof passes with matching package, projection and semantic fingerprints.
Even then `fullSemanticRuntimeProven` remains false. As of Patch 15,
Calculator's registered IR-native mechanics support the combined scope. Code
Editor has no registered IR-native proof profile, so `--with-intent-proof`
for Code Editor (and the default both-app selection) must refuse; its separate
layout-only certificate can still pass. A subsequent release must establish
Code Editor's independent intent proof before claiming that combined scope.
Evidence is generated
fresh; the certifier does not reuse a prior Phase One JSON report.

## Commands

```
python tools/mcel_layout_certify.py --json
python tools/mcel_layout_certify.py --app calculator --json
python tools/mcel_layout_certify.py --with-intent-proof --json
python tools/mcel_layout_certify.py --output runtime/state/mcel/layout-certification.json --json
python -m pytest tests/test_mcel_layout_certification.py -q
```

Exit status is zero only when the requested certification scope passes.
`--output` is optional; absent this flag no persistent certificate is written.
The certificate is not an instruction to promote or mutate application source.

## Patch 15b — Viewport-bound certification and containment

Previous Patch 15 observations set an inline `root.style.width` but always opened
Chromium at **1480px**. This did not establish the 1220px and 1050px Code Editor
conditions: its authored CSS applies `width: 100vw !important` to the host root
and absolutely positions the workbench shell. The two certificates could both
measure an approximately 1464px shell and return PASS.

Patch 15b increments the evidence schema to
`mcel.canonical-layout-certification.v2`. For every scenario the certifier now:

- Opens a **fresh browser page with the scenario's declared viewport width**;
  the height remains 920px. It sets `body[data-active-app]` so the correct
  application's active CSS rules apply. The test fixture removes only the
  browser's default body/html margins. App CSS source remains unchanged.
- Uses `width:auto` for the Calculator's outer host and no ineffective inline
  width on Code Editor. The root must occupy the declared viewport and be
  horizontally contained in it; CSS `!important` cannot bypass the measurement.
- Captures `innerWidth`, document client width, effective host and canonical
  tree-root rectangles, `clientWidth` and `scrollWidth`, and left/right/width
  for each declared layout region. The browser checks those measurements
  independently of the layout engine's own status.
- Re-adjudicates them in Python against the **original scenario width**;
  a forged PASS or reused 1480px observation cannot meet the certificate.
  Visible declared regions, root containment, and unexpected horizontal
  scrolling are hard refusal conditions.
- Rejects otherwise PASS wide/compact scenarios if their effective host-width
  difference does not reflect the requested viewport difference.
- Preserves refused evidence (including the actual attempted viewport and host
  bounds) when the runtime has no verified layout candidate.

**Observed baseline from the supplied source snapshot** under Linux Chromium:
Code Editor at 1220px passes at its true viewport (active shell approximately
1210px). Code Editor at 1050px currently refuses a verified candidate; it is
**not** certified at that width. Calculator's three scenarios expose an authored
content-box shell that overflows the actual viewport (e.g. at 1140px the shell
right edge is approximately 1152px); the new certificate refuses them. These
are outcomes to fix in the application layout contracts or planner in a separate
patch, **not** reasons to weaken or bypass the certification conditions.
Browser-dependent differences require fresh Windows proof; no full semantic
runtime claim follows from this layout-only gate.

The command remains:

```
python tools/mcel_layout_certify.py --json
```

It exits nonzero when any required scenario refuses. Inspect
`applications[*].scenarios[*].observed.viewport`, `hostBox`, `canonicalBox`,
`issues`, and `certificationRefusals` to see the exact failing condition.

## Patch 15c — Application conformance under the unchanged viewport gate

The strict Patch 15b viewport dimensions, horizontal-containment tests,
source/projection fingerprint checks, and hard-refusal semantics are retained.
The input report identified two independent application-contract defects:

1. Calculator's `.calculator-shell` had `width: 100%` with padding and a border
   in the content-box model. Its rendered right edge exceeded the browser
   viewport in graphing and basic modes. The authored shell now uses
   `box-sizing: border-box`; the generated layout is still governed by its
   canonical tree, and the observed root must remain inside the viewport.
2. Code Editor's authored responsive CSS hides the inspector at 1050px, but
   every canonical workbench variant demanded an inspector area. Its DSL layout
   tree now offers an explicit three-track `rail sidebar editor` variant.
   The wide four-track variants remain required when the inspector is visible.
   The layout engine's existing visibility and candidate-ranking checks reject
   mismatched variants rather than unhiding a CSS-hidden pane.

The certifier's Code Editor dock-area assertion now checks the **specified
scenario**, the compiled tree's available variant, the measured CSS area map,
and the inspector's rendered visibility. The independent Python adjudicator
also rejects a compact PASS carrying wide inspector areas, or a wide PASS
carrying compact hidden-inspector evidence. It does not relax general
viewport containment, text integrity, layout quality, or source identity.

The unchanged five-scenario matrix now passes on the patched source under
Linux Chromium (Calculator three modes/widths, Code Editor 1220px and
1050px). Windows must establish fresh browser evidence with the same command:

```
python tools/mcel_layout_certify.py --json
```

Certification scope remains `host-bound-canonical-layout-only`, with
`fullSemanticRuntimeProven: false`.
