# GAMER V42: read-only out-of-family development audit

V41 failed at 39/120 complete payoff structures and 79/120 decisions on
DEV descriptions with disjoint surface families. V40 had passed familiar DEV
but failed its sealed OOD holdout. Do not treat V41's prospective test as opened.

V42 does **not** train or promote anything. It loads V41's selected failed
checkpoint, captures fresh Qwen states for the same already-used V41 DEV prompts,
and verifies exact reproduction of V41's stored DEV metrics. It splits errors
by payoff language family, probability language family, and intervention. It
compares number-first with number-last statements and separately measures
V34 decisions with gold DEV semantic slots as an **oracle-assisted control**.
That oracle control never feeds the semantic model or scoring of its predictions.

The audit rejects replay if V41 checkpoint provenance changes, the original
DEV deck hash changes, the metrics fail to reproduce, or TEST artifacts exist
where they should be sealed. It does not construct or open the prospective TEST.

Run on the Windows host (no variables beyond `$py`):

```powershell
& $py -u -m tools.nanojev_gamer_v1_v42_ood_diagnostic `
  --v41-run "runtime\reports\gamer-v1\transition-005-v41-prospective-surface-0001" `
  --output "runtime\reports\gamer-v1\transition-005-v42-ood-dev-audit-0001" `
  --device cuda
```

Read `ood_dev_report.json`. This is a diagnostic of a *previously selected*
DEV set, not a new blind generalization score. Any changed architecture or
training distribution must be assessed on newly reserved prospective material;
V41 DEV cannot be recycled as an unbiased acceptance test.
