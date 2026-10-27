# NanoJev EMP hard-case detector — diagnostic experiment

## Research question

Can we predict *in advance, from operand structure*, which examples will defeat both currently selected banks? And which interpretable features of those examples predict difficulty on previously unseen operand **anchors**?

This is **not** the specialist head and **not** routing. It trains a tiny interpretable decision tree as an analysis instrument. Its risk score is not a computed addition result.

## Run the existing diagnostic with the detector

Stop the ladder trainer before reading its checkpoint; this existing diagnostic deliberately refuses a checkpoint that changes during inspection.

```powershell
& $py -m tools.nanojev_emp_dual_bank_diagnostic `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --device cuda `
    --mine-hard `
    --detect-hard `
    --hard-candidates 8192 `
    --hard-max-cases 512 `
    --detector-target both_selected_wrong
```

To focus instead on examples that defeat all four banks, set `--detector-target all_four_wrong`. Other choices: `two_or_more_wrong` or a specific bank name, e.g. `right.right`.

The run produces **both** the existing JSONL failure export and a new JSON report: `...outward_v1_hard_detector_<radius>.json`, outside the campaign. The console prints `EMP_DIAG_HARD_DETECTOR=...` with:

- Target hardness definition and the two selected banks.
- Baseline failure rate among untouched detector-evaluation anchors.
- Heldout ROC-AUC, precision and recall among the 10% highest-ranked risks, and lift against the baseline.
- Simple, depth-limited decision rules with their heldout sample counts, measured failure rate, 95% binomial interval, and baseline-relative lift.
- Post-hoc categories of observed failures: sign crossings, exact decimal displacements of 10/100/1000/10000, and other errors. **These are never model inputs.**

Evaluate one arbitrary operand pair with the saved detector (no neural model required):

```powershell
& $py -m tools.nanojev_emp_hard_case_detector `
    --model "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1_hard_detector_16384.json" `
    --a -1097642 --b 1097742
```

It reports a diagnostic probability and interpretable rules. It does **not** calculate the sum, choose a runtime expert, or alter the four banks.

## Why we don't train on the 512 hard cases

Every example in the exported JSONL has already failed. With only positive labels, a classifier cannot learn what distinguishes hard examples from easy examples. This detector therefore consumes the *complete, mixed-success* fresh TRAIN scan **before** the hard-case filter is applied.

## Feature and split contract

Feature extraction only receives `a` and `b`, not `target`, neural predictions, errors, or any failure label. Features include signs, lexical digit lengths, matching leading decimal prefixes, suffix patterns, and carry/borrow-chain indicators. The latter describe digit structure but do **not** provide the target result.

Labels are computed only during training/evaluation from the checkpoint's already-computed predictions versus ground truth; the selected pair is provisional. Inside the existing TRAIN `mine` partition, detector fit and detector evaluation use disjoint SHA-partitioned **anchor** families (about 75/25). The 20% TRAIN families independently reserved for a future specialist are never opened. DEV and sealed TEST are not used to train or assess the detector.

The four base banks may have encountered similar anchors during their earlier training; this is an **unseen-to-detector** holdout, *not* proof of neural-bank out-of-distribution generalization.

The generator's 8192 candidates are *not* necessarily an unbiased sample of all possible arithmetic examples. Interpretation of error rates and lift is conditional on that sampling distribution and current checkpoint.

## Success/failure interpretation

A shallow tree that cannot beat baseline on heldout anchors is **not a useful detector yet**, even if the in-sample groups seem striking. The console's `predictive_signal` is a modest exploratory heuristic (ROC-AUC >= 0.6 and top-decile lift >= 1.3); it is not scientific certification.

A tree that beats baseline suggests testable **candidate causes**, not established causal mechanisms. Before training a specialist on a hypothesized family, validate the mechanism with controlled matching and interventions (e.g., modify digit carry chains while holding operand magnitude, answer magnitude, and sign distribution as constant as feasible), repeated on new independent anchors. Training on this dataset without that step could overfit superficial correlates.

This patch changes only the diagnostic and adds a detector, tests, and this documentation. It does not touch the main ladder, its gate, checkpoints, training rates, or inference router.
