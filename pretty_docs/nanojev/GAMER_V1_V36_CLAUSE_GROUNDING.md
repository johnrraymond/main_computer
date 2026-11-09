# GAMER V36: Clause-Complete Qwen Semantic Grounding

## Hypothesis and limits

V35 passed belief-reference recovery on all 120 development cases but produced only 95/120 exact four-payoff assignments and 51/60 exact paired decisions. Its feature capture read a token at a fixed 16-token offset after each number. That token can fall inside a **different** payoff proposition, contaminating contextual role evidence. This is a source-based hypothesis, not a verified causal diagnosis.

V36 tests **one** capture change: replace that arbitrary future index with the causal Qwen hidden state at the punctuation ending the **same number's clause**. The sentence-boundary logic recognizes only punctuation, not roles, action names, tables, value comparisons, or answers. All dataset generation, seed, Qwen model and layers, token-scanner, role/belief head topology, training objective, numerical V34 head, thresholds, and holdout policy remain the same as V35. The V35 `v35-...` seed namespace is deliberately retained in the data generator for direct A/B comparability.

## Acceptance

DEV must meet the existing 108/120 exact complete semantics, 108/120 decisions, 54/60 paired decisions, and defeat the identical-number ceiling before any holdout access. Only then may the **fresh** V36 sealed test be opened, once. No V35 or V34 report is repurposed for certification. V32 and tactical-v6 authority are never modified.

`dev_failure_diagnostics.json` records DEV-only mismatches. Its `v34_with_gold_dev_semantics_exact` result is explicitly **oracle-assisted diagnostic scoring**, never an input to learning, predictions, or acceptance selection. It separates error from semantic extraction versus the unchanged V34 numerical decision head. A separately recorded hash binds this DEV diagnostic to the checkpoint provenance.

## Execution

```powershell
& $py -u -m tools.nanojev_gamer_v1_v36_clause_grounding run `
  --run "runtime\reports\gamer-v1\transition-005-v36-clause-grounding-0001" `
  --v34-run "runtime\reports\gamer-v1\transition-005-v34-grounded-learning-0001" `
  --device cuda
```

A failed DEV run leaves the V36 holdout sealed and emits failures for inspection. A passed DEV run automatically attempts the one-shot V36 holdout. Neither outcome changes production authority.
