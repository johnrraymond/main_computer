# MCEL Design Quality Measurement Frob (experimental)

MCEL's design critics (ChatGPT and local Gemma) remain **offline R&D instruments**. Their votes, prose, schemas, and preference archives are **not inputs to the optimizer**, the semantic compiler, or production rendering. Production MCEL defaults are unchanged.

This Frob tests a falsifiable hypothesis from the A/B/C review: geometric and task-access measurements may explain why a supporting rail can help ordinary browsing, why verified failures need prominence, and why two narrow mobile cards can be technically fitting but hard to read.

## Run

From `main_computer` with Python, PyYAML, Playwright and a locally installed Chromium-family browser:

```text
python tools/mcel_layout_frob.py --mode design-quality
python tools/mcel_design_quality_frob.py --mode run
python tools/mcel_design_quality_frob.py --mode evaluate --review-root runtime/reports/mcel-layout-frob/design-review
python -m unittest tests.test_mcel_design_quality_frob -q
```

The `run` mode invokes existing canonical MCEL A/B/C candidate generation, then **reopens each generated preview in a separate Chromium session** to measure quality. `evaluate` reuses a generated Design Critic root containing `packet.json` and `generated-A/B/C/preview.html`. A portable packet ZIP alone lacks HTML/DOM and cannot support these independent measurements; the runner refuses rather than fabricating them. Browser selection uses Playwright's Windows-safe browser discovery.

Outputs under `runtime/reports/mcel-layout-frob/design-quality/`:

- `quality-report.json`: full independent measurements, feasibility refusals, experimental numeric utility and selected candidate by viewport/task/state.
- `quality-gallery.html`: all candidates, measurements and selections; links to unchanged full-page screenshots.
- `screenshots/*.png`: twelve integrity-verified screenshots copied from the existing Design Critic packet.
- `quality-review.zip`: self-contained evidence for human inspection.
- `design-review/`: original generated candidates and frozen semantic/browser verification packet.

## Experimental policy v1

**Hard gates first:** canonical runtime mounted, all registered MCEL components preserved, search attached to its collection, no horizontal overflow, visible records meeting a preregistered 240px floor, text without unbroken-token overflow, and observed verified failure with a visible prominent notification. If every candidate fails, the run refuses.

**Separate measurements (never claim a geometry score is an aesthetic verdict):** primary workspace width fraction, distance from viewport top to workspace and first record, scrolling to first record, secondary tools-area fraction, minimum actual record width, visible record count, and verified failure width/position. The current scoring policy combines these measurements with published task-dependent coefficients: collection browsing prioritizes early, sufficiently wide workspace and penalizes wasteful tools regions; failure recovery prioritizes wide, early failure feedback. Candidate ID is never a score input; ties use deterministic source fingerprints. The current policy has **not** been human calibrated across independent design samples.

A/B/C remain a small bounded candidate family. The selector chooses **among them**; it does not synthesize a new arbitrary composition, claim a global optimum, change application semantic truth, or install a production preference rule.

## Hypotheses and acceptance

On the initial Task Manager fixture we expect A/B/C to expose these defects: C's 175px mobile cards fail the experimental width gate, B's early browsing workspace competes well at desktop, and A's wide verified failure presentation competes well during recovery. **These expectations are test cases, not a license to special-case A/B/C or the Task Manager name.** The same evaluator is exercised on an inventory design with a different component inventory.

Pass criteria for this experimental patch:

1. At least three genuinely different canonical compositions and 12 browser screenshots, with intact semantic component identities.
2. Independent Chromium measurements and hard refusals on counterexamples.
3. Different selections based on task, not simply a universal preferred composition.
4. Identical reruns with the same inputs, browser/layout engine and policy version yield the same measurements and selections within recorded geometric precision; save all fingerprints.
5. Existing layout/semantic/critic/browser regressions remain green.
6. Human visual approval stays `PENDING`, live app/effect authority stays untouched.

Future promotion requires independent applications and human comparative design decisions beyond the fixtures used to define these weights. This patch is **not** a production-quality certification or an automatic feedback-learning pipeline.
