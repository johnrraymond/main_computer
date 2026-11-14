# G2-0036 — TRAIN-Only Selective Occurrence Recovery

## Why this intervention exists

G2-0034 identified missing G1 occurrence endpoints as a candidate-coverage ceiling.
G2-0035 demonstrated that adding up to 16 margin-ranked neural proposals per
case increases the representable gold graph inventory, but badly damages
G2 precision and destroys otherwise correct graphs. The next intervention must
**learn whether to admit an additional occurrence**, not simply supply more
unfiltered nodes to a frozen relationship scorer.

## Fixed experiment

This is a bounded development experiment. G1-0030, G2-0031 occurrence-graph
weights, G2-0032 completeness heads, G2-0033 graph decoders, and the frozen
backbone are unchanged. No production graph execution, sealed-test access,
policy promotion, or retrospective DEV threshold optimization is authorized.

New G1 occurrence proposals must come from the actual frozen G1 neural proposal
catalog for the current prompt, with positive neural margin and a valid
canonical token-to-character mapping. No lexical heuristics, author graph
fields, game-family rules, or gold spans may enter inference features.
The feature-only neural admit/abstain model uses G1 proposal margin, span
length and position, kind, filter/decoder status, competing selected-span
overlap and local proposal density. Existing G1 selections are preserved.

The neural proposal catalog and its recall ceiling are separately audited,
including gold endpoints absent even from that catalog. The candidate audit
also distinguishes the 256-entry label-blind safety shortlist from the full
neural catalog. The selector may add at most **4** occurrences per graph, or
**zero** when no TRAIN-calibrated policy is trustworthy. A graph never exceeds
128 selected G1 occurrences.

## Training and evaluation separation

Generate 300 new TRAIN fitting cases, 120 separate TRAIN calibration-fit cases,
and 60 additional TRAIN calibration-audit cases, disjoint from all earlier
TRAIN, calibration, and DEV examples. Three selector seeds use 320 optimizer
steps each. Only the selector network is trainable.

Each TRAIN candidate is labeled positive only if it exactly matches a missing
gold occurrence incident to a gold binding edge. This is TRAIN supervision,
not a DEV oracle. Admission thresholds (0.55, 0.65, 0.75, 0.85, 0.92, 0.97)
and admission budgets (1, 2, 4) are fixed in the protocol. Candidate policies
must recover at least three missing TRAIN-fit endpoints, reach 75% admission
precision, and restore at least one gold-edge endpoint pair. The selected
policy must independently achieve at least two true endpoint admissions,
75% precision, and positive recovered edge reachability on TRAIN audit;
otherwise that seed **abstains** entirely. DEV labels never select policies.

Reconstruct G2-0035 lineage, verify its immutable report and ledger hashes,
row identities and aggregate metrics, and replay its frozen baseline on the
original G2-0035 independent and withheld-topology cohorts. Generate fresh,
disjoint G2-0036 development cohorts: 320 independent and 128 withheld
structural-topology cases.

Evaluate each selector seed paired with the corresponding frozen G2-0032
`group_recovery` and G2-0033 `structured_set` decoder. Compare against its
identical frozen G2-0035 baseline. End-to-end outcomes include: graph F1,
exact graphs, ranking failures, unfamiliar-topology metrics, number of gold
endpoints in the complete neural proposal catalog, the count selected by the
new acceptor, false admitted anchors, newly reachable gold edges and inventory
representation after the top-24 relationship constraint.

No model or policy is chosen by DEV performance. Development gates require
positive represented-edge gain and **no regression in F1, exact graphs,
ranking-failure rate or withheld-topology F1/exact graph count** for both
downstream decoders. A diagnostic pass is not permission to promote.

## Execute

Apply the repository-root-relative patch on top of G2-0035 and its top-24
reference-window hotfix. From the `nanojev` directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-selective-occurrence --device cuda
```

Immutable output directory:

`runtime/reports/gamer-v1/graph-g2-selective-occurrence-0036/`

Files:

- `g2_selective_occurrence_report.json` — lineage, TRAIN calibration,
  catalog coverage, frozen DEV comparisons and gates
- `g2_selective_occurrence_per_case.jsonl.gz` — case-aligned graph and
  endpoint-coverage metrics
- `g2_selective_occurrence_development.safetensors` — selector weights only
- `g2_selective_occurrence_frozen_policies.json` — TRAIN-calibrated admission
  policies, including disabled/abstaining seeds

If gold endpoints are absent from the *full* frozen neural proposal catalog,
no admission model can repair those omissions: that evidence would justify a
subsequent G1 proposal-generation training intervention. Conversely, if
high-precision proposals improve candidate reachability but frozen G2 graph
F1 still falls, the next training target must include G2 discrimination under
those new candidate distributions.
