# MCEL Patch 14.6 — Promise Verification (Task Manager)

**Status:** Offline, read-only experiment. **Depends on Patch 14.5.** This is *not* a production renderer promotion and does not add a second profile. No manifest, no new application, no operational API calls.

## Question

Does registered semantic information *cause useful changes* in the organization of the existing Task Manager interface, while preserving exact machine evidence under secondary disclosure?

This is three separate propositions:

1. **Retrieval accessibility.** In the same layout engine, are `name`, `state`, and `resident_memory` more readily accessible in the semantic field order than in a frozen nonsemantic order?
2. **Causal adaptation.** Does changing only *semantic meaning*, with unchanged source data, change the universal composition? Do ordinary data changes avoid triggering a structural redesign?
3. **Source preservation.** Can a user select a row by keyboard, recover the *entire* exact canonical record and verified field-level provenance on demand, and avoid having raw JSON dominate the default presentation?

## Run

From the repository root, after applying **14.5**:

```powershell
python -m tools.mcel_patch14_6_promise_verification_frob
python -m pytest -q tests/test_mcel_patch14_6_promise_verification.py tests/test_mcel_patch14_5_human_first.py
```

Write a report outside the default location if preferred:

```powershell
python -m tools.mcel_patch14_6_promise_verification_frob --output runtime/reports/mcel-promise-146
```

`--no-screenshots` skips reference screenshots while still exercising actual Chromium. Default output is `runtime/reports/mcel-patch14-6-promise-verification/report.json` plus four normal-fixture PNGs. In an environment without the browser/Playwright dependencies, the command reports **FAIL**, not fictitious verification.

## Experimental design (frozen before collection)

**Application:** Existing Task Manager host HTML plus registered MCEL v2 collection, canonical layout tree and 14.5 human-first universal profile. The browser is offline; Task Manager operational JavaScript and endpoints are not executed. **Data:** existing `task_manager_snapshot.v1.json`, both normal and previously established deterministic stress transformation. **Viewport:** 390 and 1440 CSS pixels. **Tasks:** locate `name`, `state`, and `resident_memory` among the rendered row fields. **Readout:** field present, visible, visible order, and rendered contract value; measured costs are `(requires-reveal, visible-scan-rank)`, lexicographically smaller being better. These are **mechanical acquisition proxies**, not human completion times or error rates.

**Arms:** The normal semantic composition versus a *single frozen* hash-ordered, semantics-blind counterfactual. Both arms are mounted through exactly the same single universal renderer, with identical semantic record bytes, field set and tiers, field formatting, human-first inspector, summary, and region tree. Only the order of the fields is intervened upon. The control seed is a checked-in constant. We do not search for a favorable control, train on these fixtures, or select the best of multiple variants. Screenshots are supplemental.

**Causal controls:**

- *Content churn*: rename a process, preserving semantic structure and lifetime identity; a decision signature change is a failure. In-place update must succeed and show the new name.
- *Purpose*: switch `monitor` to `inspect`; the region set/signature must change and in-place update must refuse rather than silently redesign.
- *Semantic intervention*: with the **same source bytes**, change the catalog declaration of `state` from a registered state concept to uninterpreted text in a disposable **in-memory catalog copy**. The changed semantic signature and field organization, and removal of the state-count summary, must all be observable. The existing host/catalog are never modified. Browser update must refuse this structural change; explicit remount must render the new organization.

**Integrity controls:** For each arm and each browser case, the selected row is opened with Enter; the exact canonical JSON is compared structurally to the source record; identity matches; availability/freshness/source provenance match the value contract; evidence starts visually collapsed and opens with disclosure; keyboard focus is preserved; unauthorized-operation and production-promotion plans are refused; no network requests, page errors or document overflow; original five operational controls remain present and untouched.

## Interpretation

The gate `engineeringStatus=PASS` means the equal-arm setup, actual browser rendering, causal semantic intervention and fail-closed integrity contracts operated correctly. **It deliberately does not depend on the semantic arm winning the accessibility proxy**. The report separately records semantic wins, blind wins and ties; a negative result is retained, not treated as a test failure. The `promiseVerdict` is at most `DIRECTIONAL_PROXY_ADVANTAGE`, never “proved superior”.

Reference run (Oct 28, 2026, Chromium; synthetic Task Manager fixture): engineering **PASS**; retrieval **7 semantic wins / 4 blind wins / 1 tie** over 12 matched field comparisons. These are *not 12 independent human trials*: three repeated fields at two widths and two dataset densities. A single hash-ordered comparator is a weak control; this is indicative and not a statistical treatment-effect estimate. The existing semantic profile also continues to display a low-salience machine identity in the row. That is a separate design limitation, not a source-integrity failure.

## Next falsification step: human task retrieval

Before saying MCEL is measurably better for users, run a **counterbalanced, blinded** participant test on the actual Task Manager. Same tasks, same snapshots, both render arms. Randomize participant order; do not reveal which arm is semantically informed. Log correct answer, errors, time-to-correct-answer, number of inspections/disclosures, and whether the requested source evidence is recoverable. Avoid showing the same answer in both arms to one participant without counterbalancing learning; use independent participants or equivalent randomized targets. A blank record format:

| Participant | Arm code | Task target | Snapshot | Width | Correct | Time (s) | Inspections | Disclosure clicks | Notes |
|---|---|---|---|---|---|---|---|---|---|
| | A/B | process name/state/memory/provenance | normal/stressed | 390/1440 | | | | | |

**Promotion boundary:** A passing 14.6 permits further evaluation of the single universal profile and Phase 3 Task Manager typed tabs/monitoring-first work. It does *not* permit replacing production views, claiming human performance superiority, granting new operations, or removing exact canonical evidence.

## Files included

- `tools/mcel_patch14_6_promise_verification_frob.py` — full experiment, browser probes, report and optional PNG capture.
- `tests/test_mcel_patch14_6_promise_verification.py` — causal-control, equal-budget and Chromium regression tests.
- `pretty_docs/mcel-patch14-6-promise-verification.md` — this protocol, result interpretation and human acceptance boundary.
