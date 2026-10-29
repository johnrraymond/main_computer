# Patch 14.4 — Intelligent Visual Design Frob

**Scope:** read-only, offline/browser experiment, no production promotion. **Status:** engineering acceptance only; independent design judgment remains UNVERIFIED until reviews are submitted.

## Objective and inputs

Patch 14.3 established a general semantic-to-layout composition pipeline but did **not** establish useful or attractive results. Patch 14.4 makes that claim testable by generating matched, original, hash-checked Chromium PNG captures using the **existing MCEL semantic collection renderer and canonical-tree lab**, not a mock renderer. It reuses the old Design Critic and Frob visual-review conventions (PNG archive, gallery, contact sheets, review template) and the Patch 14.3 baseline, blind, and semantic candidates.

From the repository root:

```powershell
python -m tools.mcel_patch14_visual_quality_frob --with-regressions
```

Alternate output destination:

```powershell
python -m tools.mcel_patch14_visual_quality_frob --output runtime/reports/my-visual-review
```

The module requires the existing Playwright Chromium environment. Contact sheets are rendered in Chromium; no Pillow dependency is added. It does **not** connect to live Main Computer endpoints or invoke operational commands.

## Experimental matrix

| Dimension | Values |
|---|---|
| Source semantic domains | Task Manager processes, File Explorer entries, Worker market leases |
| Compared approaches | Existing v2 baseline, blind field-order control, universal semantic composer |
| Viewports | 390 and 1440 CSS pixels |
| States | Normal fixture and deterministic synthetic stress mutation |
| Matched PNGs | 3 × 3 × 2 × 2 = **36** |
| Side-by-side contact sheets | **12**, each comparing three arms for one domain/state/width |

Every arm in a matched set receives exactly the **same resolved source observation** and the **same browser renderer**. The existing Task Manager compatibility overlay remains the strong baseline; File Explorer and Worker use their current generic v2 baseline. The blind and semantic arms use their **frozen candidate zero**, not the per-cell best-of-N oracle. The blind alternative uses field-key hash ordering; this is a limited control for field-order guidance, not an equally rich structural search.

The stressed state is a fixture mutation, **not a real application measurement**. Stress variants change text lengths, record counts and selected measurement magnitudes while preserving registered concepts and source-contract validity. No row identity is manufactured from a missing generation. No authority is inferred from worker data.

## Output directory

`runtime/reports/mcel-patch14-visual-quality/`

- `gallery.html`: offline visual gallery, original resolution images and contact sheets.
- `*.png`: 36 actual Chromium captures of the **complete MCEL component**, not the containing parent Task Manager page. This keeps the comparison fair across semantic domains.
- `contact-*.png`: 12 side-by-side triptychs; originals remain available.
- `visual-manifest.json`: screenshot identifiers, hashes, dimensions and honest capture scope.
- `review-packet.json`, `review-request.md`, `review-template.json`, `visual-review-template.csv`: independent review inputs.
- `answer-key.json`: **keep locally**, outside the blind review ZIP; reveals shuffled A/B/C labels.
- `mcel-patch14-blind-visual-review.zip`: portable blinded review packet, excludes `answer-key.json`.
- `acceptance.json`: browser and regression evidence, acceptance checks and unverified quality gates.

## Blinding and integrity

Approach labels are deterministically shuffled **independently per domain/state**. They are not globally stable (e.g., A is not always baseline). Do not include `answer-key.json` when requesting independent review. Screenshot SHA-256 hashes and a stable source-snapshot SHA-256 are recorded to prevent cherry-picking or accidental mismatches. The reviewer can compare A/B/C without being told the generating arm.

Use the review template to report task clarity, hierarchy, readability, density, information access and concrete failure cases with PNG IDs. Request reviewers to distinguish visible evidence from interactive/semantic properties that PNGs cannot establish. Retain failures and rejected hypotheses. An independent critic or human review must be *separate* from the Frob's field selection and browser hard gates.

## Acceptance interpretation

The Frob can PASS engineering gates: correct PNG matrix, deterministic candidate policy, matched source observations, unchanged production code, valid exact-record inspectors, minimum row width, no horizontal overflow and no page errors.

It **cannot** PASS the independent-quality gates by itself:

- **G6 semantic advantage:** UNVERIFIED without a precommitted independent comparison and useful task evidence. Browser geometry alone proves no superiority.
- **G7 independent quality:** UNVERIFIED until blind reviewers have actually inspected the images and returned judgments.
- **Interaction quality:** PNGs cannot certify keyboard traversal, effect authority, live lifecycle or real hosted application usability.

Known limitations: identities are often overly prominent; the raw exact-record inspector is excessively verbose; File Explorer hierarchy lacks verified parent-child semantics. These are candidate general policy deficiencies for subsequent revision, not claims that the universal profile already solves them. Do not silently change the composition algorithm during evidence gathering.

## Next research iteration

1. Generate a frozen **before** packet and obtain blind reviews.
2. Identify deficiencies common to domains, not one-off application-specific CSS.
3. Make one general semantic composition adjustment at a time.
4. Generate a separately versioned **after** packet, retaining the prior packet.
5. Compare objective task performance and independent visual judgments without using the test packet to train the selector.

**No production layouts or Website Builder behavior are modified.**
