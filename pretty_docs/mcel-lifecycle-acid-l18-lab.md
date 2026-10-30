# MCEL Lifecycle Acid Lab — 18 flat, live Taguchi experiments

The MCEL Lab **Lifecycle Acid Test · L18** workspace presents **all eighteen independent experiments as a vertical series of live MCEL collections**. Scroll down the page; no one-at-a-time selector, screenshot, or separate mount button is required. The data are offline read-only Task Manager semantic fixtures, **not** connected live OS telemetry. All actual row interactions and presentation decisions run through MCEL's universal collection, negotiation, selection continuity, observation integrity, and retirement services.

## Human-operated experiment (primary workflow)

Each `L18-01` … `L18-18` section shows its eight factor assignments and a real, independent collection. Hover actual process rows to test floating cards; click to test inline expansion and scroll-to-reveal; use normal browser highlighting, Ctrl+C and copy controls. The explicit buttons request inline/hover presentation, create native selection, clear selection, deliver source updates or authoritative disappearance, attempt stale observations, scroll the local list, inject a failed transaction, and let retirement timers expire. Use **Apply next planned condition** to step through this case's intended event sequence without automatically running it. **Restart specimen** resets this case only. Events, source revisions, and retirement clocks remain independent between cases.

Each card starts with **simulated live source updates enabled** at a three-second interval **while the card is onscreen**. The lab uses a single shared timer and an IntersectionObserver instead of eighteen independent polling timers. Updates are complete source observations of the same record generation, never synthetic source disappearance. Uncheck the live-updates control if you need to freeze one case for a particular inspection or text selection. Updates for background/offscreen cases are suspended to limit work; this is a laboratory workload policy rather than a production source guarantee.

Each experiment has its own **Works correctly / Something is wrong / Needs investigation** buttons, notes, and expandable event trace. **Export all reviews and traces (JSON)** collects all 18 settings, per-case actions, event histories and review notes. Notes are **session-local**, not persisted across reloads; export before refreshing the page.

Page scrolling dismisses source-anchored floating previews from the gallery so an overlay cannot remain covering a different case after its source moves. Scrolling *inside* a floating card still uses that card's own interaction behavior.

## Orthogonal design and automated diagnostics

The L18 array has one two-level factor and seven three-level factors. Its 18 assignments are pairwise balanced and validated at initialization. Factors: data density (normal/stressed); reveal request (select/hover/near-edge); source observation (unchanged/partial/authoritative removal); interaction (idle/native selection/floating inspection); host capabilities (all/no scroll/no overlay); retirement timer (resume/reset/continue); presentation width (360/620/900); and record identity (verified/unverified/new generation). It screens possible **main effects**; it does not establish individual causal interaction effects.

The automated screening is an *optional secondary diagnostic*, inside its own disclosure. `Run L18 screening` executes 1–5 seeded repetitions per configuration, measures browser execution time, validates source identities/layout/invariants, exercises a fallback and an exception rollback, and checks stale sequence refusal. The matrix underneath can jump to any of the 18 visible live experiments. Advanced one-case editing and pairwise drill are also secondary options and do not replace the flat gallery.

## Reproducibility

```powershell
python -m tools.mcel_lifecycle_taguchi_acid_frob
python -m pytest -q tests/test_mcel_lifecycle_acid_lab.py tests/test_mcel_lifecycle_flat_gallery.py
```

The Frob runs at 390px and 1440px. Its screenshot captures the **browser viewport**, not one unbounded raster containing all eighteen live cases; the JSON report retains the full automated results. The gallery browser tests assert that all 18 render independently, updates and selection work, review/export works, and the legacy screening remains accessible.

## Boundaries

The real renderer runs against **simulated** process snapshots; this does not certify production Task Manager or real operating-system process control. The test does not prove browser-worker concurrency, virtualization, nested scroll arbitration, accessibility in every browser, or complete rollback atomicity. An L18 green result means only the declared checks passed. Human judgments are independent evidence and should not be overwritten by automated verdicts.
