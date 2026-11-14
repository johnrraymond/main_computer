# NanoJev: First-failure neuron map and outward spiral

## Experimental question

**When a cloned, previously trained Hard EMP memorizes a finite zero-line training
shell to effectively zero arithmetic cross-entropy, what is the *first unseen*
problem it gets wrong as the argument-magnitude and signed-residual radii
expand? Which backbone activation coordinates differ most from neighboring
correct computations, and can targeted interventions alter the mistake?**

The basic invariant is `A + (-A + delta) = delta`. This is **not LASSO** and
applies no sparsity penalty. It creates no external arithmetic expert. Gold
arithmetic is only used to produce training labels and evaluate predictions.

## Run

Install the accompanying create-only patch, then:

```powershell
& $py -m tools.nanojev_emp_first_failure `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_first_failure_v1" `
    --device cuda `
    --initial-magnitude 32 --initial-delta 2 `
    --magnitude-growth 2 --delta-step 2 `
    --rings 9 --train-cards 128 --probe-anchors 24 `
    --loss-target 0.005 --max-steps 1200 `
    --neighbors 8 --ablate-top 8
```

The default *primary* failure definition is exact equality to the gold sum,
including exact zero. `--failure-mode tolerance` instead uses the prior EMP
criterion (within 5% on nonzero targets, exactly zero on zero targets). Both
rates are recorded on every probe. This setting, and all curriculum parameters,
are pinned into the output `config.json` and cannot silently drift on resume.

The initial training ring includes zero and ±1/±2 controls; set
`--initial-delta 0` to study exact cancellation alone at the first ring. Training
on only exact-zero examples risks learning the trivial always-zero response;
the default near-zero controls avoid that collapse.

## Timeline and independent held-out boundary

1. Reconstruct the trained Hard EMP from immutable campaign snapshots, the
   completed reverse zero-line checkpoint, and the saved specialist `latest.pt`.
   Clone its weights and AdamW moments into the **new output directory only**.
2. Deterministically generate one *finite* TRAIN-family deck for the current
   `(argument magnitude, signed residual)` radius. Train the cloned specialist
   until every deck answer is **exactly correct** and its **full deck mean
   cross-entropy is at most `--loss-target`**. A maximum-step exhaustion
   stops the experiment with `not_memorized`; it does not promote the ring.
3. Freeze training, expand both radii, and score a **DEV-family** grid. The
   "first outward failure" is the earliest incorrectly computed example (in
   recorded scan order) with a newly expanded magnitude OR residual. Earlier
   interior coordinates are retained as neighborhood controls. The order is
   increasing argument-magnitude anchors, then residuals `0,+1,-1,+2,-2,...`,
   and then the two operand orientations/permutations.
4. Archive **every** probe prediction and its correctness. Select nearest
   correct and failed neighbors by log magnitude, residual difference, operand
   sign, and order. Capture actual forward hooks in each Transformer block and
   the final backbone-normalization output, with multiple recurrence passes
   distinguished (`@0`, `@1`, ...). All raw captured vectors are saved.
5. For each coordinate, rank its failed-versus-correct activation anomaly by
   absolute departure divided by a variance floor. Create both an arithmetic
   grid and a layer/channel activation heat map. Candidate coordinates are
   separately ablated by zeroing their activation **during the actual forward
   pass** for the failure and the correct neighbors. Save rescue and collateral
   damage counts. A high activation anomaly is not by itself evidence of
   causality; ablation can implicate a coordinate but does not prove the
   presence of a unique arithmetic neuron.
6. Archive a complete checkpoint after the successful memorization/probe ring;
   then continue to the next spiral ring. Probe examples are never added to
   the memorization deck; future TRAIN families can expand their range without
   using DEV/TEST labels as training data.

Every magnitude family is deterministically partitioned through NanoJev's
`stable_fold` scheme using the **actual maximum operand magnitude**. DEV and
quarantined TEST families never enter optimization. The first failure depends
on the defined grid and traversal order, not on a claim that no other failure
exists between sampled coordinates. Full investigation requires multiple seeds
and denser probes.

## Outputs

```
<output>/config.json                 Immutable parameters + input hashes
<output>/latest.pt                   Independent model/optimizer checkpoint
<output>/history.json                Per-ring memorization and probe results
<output>/result.json                 Completion or honest memorization halt
<output>/ring_01/...                 Earliest outward probe
    all_predictions.jsonl.gz         ALL scored cases, successes and failures
    error_grid.json                  Counts per magnitude/residual cell
    error_heatmap.svg                Arithmetic failure fraction
    neuron_heatmap.svg               Per-layer/channel activation anomaly
    activations.pt                   Raw forward states for failure + neighbors
    event.json                       First failure, nearby controls,
                                     channel ranks and causal ablation results
```

`neuron_heatmap.svg`, `activations.pt`, and `event.json` appear when the ring
contains an outward failure. Rings with no first failure still record the
full predicted population and arithmetic map.

A failed memorization stage stops without advancing or writing a false
completed checkpoint. The previous completed checkpoint and history remain
intact, so rerunning the same command retries that stage. The training arm is
independent from the four ordinary heads, Hard EMP, detector, gate policies,
and forward/reverse runs. No model is promoted automatically.

## Interpretation

Evidence of a reusable failure mechanism requires recurrence across separate
failure neighborhoods, magnitudes, and preferably model checkpoints. Nearby
correct controls can encode different correct answers, so activation
substitution could inject answer information; this initial harness uses
**zero ablations with collateral-correctness controls**, not unconstrained
answer-leaking substitutions. Ablating a given layer coordinate is an
intervention on the model at that point; it is not a claim that the
coordinate alone computes the cancellation invariant.
