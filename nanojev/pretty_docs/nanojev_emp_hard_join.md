# Non-hybrid NanoJev: lagging Hard EMP + Hard Detector

## Objective

Test whether two specialist arithmetic readouts and a learned hard/easy detector can improve the existing four-independent-backbone zero-line EMP system, **without** arithmetic oracles at inference, frozen reference heads, a hardcoded carry/borrow definition, or changes to the ladder's Left/Right advancement gate.

This is an **opt-in companion** to `tools.nanojev_emp_dual_bank_ladder`, not an in-place edit of that trainer. It leaves original source snapshots, four-head stage checkpoint schema, original DEV advancement tests, and TEST embargo unchanged. The specialist is experimental; it is **not** currently wired into production inference.

## Training cycle

For every **committed** four-head accuracy probe:

1. The four regular independent `AdditionNet` heads continue their ordinary training and the original ladder computes its DEV acceptance metrics unchanged.
2. The companion scores the four just-trained heads on a *fresh, separate TRAIN-family accuracy deck*. These examples must not come from the regular DEV/TEST acceptance decks: the specialist will eventually train on their answers. The score for each head is evaluated **before** any specialist training on the new deck. It supplies cases only when its accuracy is **strictly greater than 0.60**, never equal to 0.60. If none qualify, record the accuracy scores and skip intake; do not use unqualified head failures.
3. From each qualified supplier, record all gold answers, source predictions, and the **exact final LayerNorm hidden states consumed by that head's sign/digit readouts**. The detector sees the same readout input, not handcrafted arithmetic features or the source head's correctness/answer. Each wrong supplier prediction creates a `hard` example; each correct prediction creates an `easy` example. Each hard example is paired with a similarly scaled easy example **from the same supplier**, in the same split. Multiple heads may label the same operands differently; provenance is preserved instead of overwriting contradictory labels.
4. A specialist starts as a **deep clone** of the best qualifying source head when that head leads the runner-up by at least 15 percentage points (or is the only qualifier); otherwise, start two independent clones of the best two qualifying heads. The clones each get their own optimizer and **never update the originals**. The source selection and clone count are fixed for that companion campaign, even though regular heads keep training and their supplier eligibility can change every probe.
5. **One-probe lag:** train the specialist computation clone(s) on the **preceding** probe's hard examples and correct answers. Train the independent neural detector on those same cases *paired with easy examples*, using the source heads' captured hidden states. Do not train on the just-scored probe's cases until the next cycle. The detector uses balanced hard/easy batches; computation clones train on hard examples only.
6. Before advancing the queue, evaluate the currently trained detector + arithmetic specialist on this probe's **held-out anchor families**. They are never put into the training queue. Report detection precision/recall, true hard-case arithmetic recovery, joint detected-and-solved rate, and paired-test combined accuracy versus the supplier baseline. In the combined offline simulation, a detector-negative case falls back to that example's original competent supplier; the simulation **does not deploy a live runtime router**. The second specialist's `either_hard_accuracy_upper_bound` is an oracle *upper bound for analysis only*, not an inference-time claim.

All sampling uses the four-head zero-line sampler, not a fixed carry/borrow hypothesis. The source accuracy gate applies to the *fresh accuracy deck*, not training-batch loss. Unlike the original ladder's acceptance metric (which tests two distinct heads on separate frontier/outer DEV views), the **60% gate tests each head's individual accuracy on these fresh cases**.

## How to run

**Stop the ordinary four-head trainer first. Do not run both on the same campaign concurrently.** Use the same Python environment and values of all ordinary ladder options as the original run. To attach to an **existing** `nanojev_emp_dual_bank_outward_v1` campaign, run with `--resume`:

```powershell
& $py -m tools.nanojev_emp_hard_join `
    --source-run "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --device cuda `
    --target 4000000 `
    --stage-steps 10000 `
    --probe-every 250 `
    --max-stages 16 `
    --left-gate 0.80 --right-gate 0.50 --gate-streak 2 `
    --resume `
    --hard-cards 2048 --hard-batch 64 --hard-updates 24 `
    --hard-max-queue 2048 --hard-lr 0.0001
```

If your original campaign used **different** ladder options, preserve those exact options. If the campaign was created under the old fixed-bank gate policy, follow the original ladder documentation's **explicit one-time** `--migrate-best-pair` requirement; the Hard EMP attachment does not bypass it. New campaigns omit `--resume` and must use an empty new output location.

A newly attached companion handles a pending last committed probe and then follows each subsequent normal probe. Its first qualifying probe initializes its clones; the *next* probe is the first opportunity to train on the preceding probe's examples. If it attaches while no head qualifies, it records that fact and keeps waiting. All four normal heads continue updating; none is frozen.

To test without a GPU, run:

```powershell
python -m pytest -q tests/test_nanojev_emp_hard_join.py tests/test_nanojev_emp_dual_bank_ladder.py tests/test_nanojev_emp_dual_bank.py
```

## Evidence and resume

The campaign stores separate files under `<output>/hard_emp/`:

- `config.json`: immutable companion hyperparameters, strictly greater than 60% supplier gate, and companion code SHA; changing these mid-run is refused.
- `latest.pt`: specialist weights, detector weights, optimizers, last one-probe training queue, processed probe identifiers, and outcomes. This is *separate* from the original ladder checkpoint.
- `evidence/<radius>-<step>.pt`: complete source accuracy cards, predictions of all four heads, qualified suppliers, all training pairs, all validation pairs, and captured readout-input representations. These contain research data and can be large.
- `latest_report.json` and `EMP_HARD_JOIN_PROBE=` terminal records: intake status and independent evaluation.

A processed probe is idempotent and checked against the stage checkpoint SHA. On `--resume`, an unprocessed committed probe is replayed. If configuration or checkpoint provenance drifts, the runner refuses. Do not delete only parts of the sidecar or repurpose its output directory. The full scientific archive can be retained even though the one-probe training queue is capped by `--hard-max-queue`.

## Limits and what the results do *not* prove

- At radius 16,384, the previous discovery scan found ~4.77% four-way failures; this scheme instead learns **supplier-specific failure** for qualifying heads. Those are different definitions and their observed rates should not be conflated.
- The validation split is by magnitude anchor; examples at previously observed *training* anchors are never used in the held-out evaluation. The paired validation population is 50% hard by construction, so its `combined_accuracy` and `baseline_accuracy` **are not estimates of deployment/population accuracy**. Precision and recall on that balanced population likewise require a natural-rate evaluation before making deployment claims.
- A specialist detector trained from changing regular-head representations can become stale; ongoing results test whether the one-probe lag actually works.
- This patch does not modify the four-head selection/gating logic, merge model weights, grant specialist authority over acceptance, open sealed TEST, or certify million-scale generalization.


## Frontier-only resume repair (2026-10-26)

The initial join patch's `recover_pending()` replayed **every historical stage's
latest checkpoint** in lexicographic filename order, e.g. `1024`, `16384`,
`2048`, `4096`, `512`, `8192`. Those checkpoints are not sequential probes.
This inadvertently initialized the specialist on an old state, trained it on
unrelated radii, and invalidated interpretation of the resulting specialist
metrics. It did not corrupt the ordinary four-head ladder.

This revised runner recovers **only the current, highest-radius frontier**.
When joining an already-running campaign for the first time, it clones the best
qualifying head(s) at that frontier. Subsequently, new live accuracy probes
follow in actual time order with the intended one-probe lag.

To preserve your previous experiment and start a clean Hard EMP state, add
`--hard-state-dir hard_emp_frontier_v2` to the normal invocation. The previous
`hard_emp/` directory remains untouched. This new specialist begins at the
latest saved frontier probe; it **does not** claim to have learned from earlier
completed stages, and its first probe trains no prior pairs. The original
four-head ladder is unchanged, and its resume SHA checks remain authoritative.

Do not reuse the previous `hard_emp` state with this repaired runner: code hash
pinning will reject incompatible history. Use the separate directory flag.

The parent ladder's `qualified` frontier acceptance and the Hard EMP's `>60%`
supplier qualification measure different things; neither replaces the other.


## Forward-only command-line gate tuning (October 2026)

Stop the running process at a completed `EMP_HARD_JOIN_PROBE` (Ctrl+C after the
probe is printed). The new gate controls are research acceptance and sampling
policy; they do **not** change the signed arithmetic correctness rule (5% relative
error on nonzero targets and exact zero on zero targets).

Apply the small gate tuning patch, then restart the **same** campaign and the
**same** hard specialist directory. For the existing `hard_emp_frontier_v2`
campaign, raise each of the four policy thresholds by five percentage points:

```powershell
& $py -m tools.nanojev_emp_hard_join `
    --source-run "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --device cuda `
    --target 4000000 `
    --stage-steps 10000 `
    --probe-every 250 `
    --max-stages 16 `
    --hard-state-dir hard_emp_frontier_v2 `
    --left-gate 0.85 `
    --right-gate 0.55 `
    --supplier-gate 0.65 `
    --detector-gate 0.55 `
    --allow-gate-change `
    --resume
```

- `--supplier-gate` is strictly-greater-than accuracy for an ordinary head to
  contribute hard/easy pairs. Exactly 65% **does not** qualify at `0.65`.
- `--detector-gate` changes the sigmoid score used at evaluation/routing. The
  neural detector still trains on paired hard/easy logits; this does not change
  its learned loss target or the specialist's arithmetic training objective.
- `--left-gate` and `--right-gate` apply to the original ladder's worst DEV
  frontier/outer 5%-accuracy scores. Both distinct roles must qualify.
- `--allow-gate-change` explicitly authorizes a forward-only change in any of
  these thresholds during `--resume`. For later resumes with the **same** four
  values, the authorization flag is optional; **keep the four numeric flags**.
  To revise thresholds again, supply new values and authorize again.

The campaign records the old and revised values in the new file
`<output>/hard_emp_gate_tuning.json`. Existing `config.json`, stage
`config.json`, accepted stage checkpoints, the hard specialist's original
`config.json`, and the historical probe/evidence archives are **not modified**.
Each new probe records the new gate revision. The in-progress ladder's
consecutive qualification streak restarts at zero when the gate policy is
revised: a stage cannot be accepted using a mixed old/new streak. Previously
accepted radii remain accepted; no previous stage is re-certified.

**Important:** If a ladder checkpoint was committed just before an interruption
but the matching hard probe was not saved, the patch refuses a gate change with
`HARD_JOIN_PENDING_PROBE_BEFORE_GATE_CHANGE`. Resume once with the *old* gate
values to finish that pending probe, stop after its report, then issue the new
gate command above. This prevents accidentally re-labeling a prior probe under
new criteria. Invalid gate values and unapproved changes fail closed.

## Persistent descending curriculum: `--reverse` (2026-10-26)

Use `--reverse` to **abandon the current outward schedule** and reverse the
radius progression using the **latest live four-bank model and optimizer
state**. This is *not* a rollback to older accepted checkpoints. The forward
stage history remains immutable. The new reverse plan is persisted to
`hard_emp_reverse/plan.json`. Once present, this runner refuses ordinary
outward `--resume` on the same campaign; every later restart must include
`--reverse`. A different training direction would require an explicit new
campaign rather than silently reusing the old forward path.

Stop the current runner just after a completed probe, apply this patch, and run:

```powershell
& $py -m tools.nanojev_emp_hard_join `
    --source-run "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --device cuda `
    --target 4000000 `
    --stage-steps 20000 `
    --probe-every 250 `
    --max-stages 16 `
    --hard-state-dir hard_emp_frontier_v2 `
    --left-gate 0.85 `
    --right-gate 0.55 `
    --supplier-gate 0.65 `
    --detector-gate 0.55 `
    --reverse `
    --resume
```

Do not add `--allow-gate-change` unless you are intentionally changing the
thresholds again; `--reverse` is a **scheduler change**, not a gate revision.
With current forward frontier ±32,768, the reverse stages are ±16,384, ±8,192,
±4,096, ±2,048, ±1,024, ±512, and exact zero-line. Each stage needs a new
consecutive qualification streak under the active `--left-gate` and
`--right-gate`, with the original distinct-bank selection rule. Unlike the
outward schedule, a qualifying stage progresses to the **next smaller** radius.
If a stage exhausts `--stage-steps` without qualifying, descent halts there;
increase that **per-stage total step budget** and use the same `--reverse
--resume` command to continue the existing stage rather than resetting it.

The exact zero-line terminal stage uses cancellation with nonzero operands
throughout the magnitude bands, plus near-zero signed controls. Its DEV gate
requires both exact-zero accuracy and nearby nonzero arithmetic accuracy,
which prevents a constant-zero prediction from qualifying. Training retains a
mixture of reverse-shell focus, full-previous-frontier replay, and signed
zero-line examples; it does not become a trivial zero-constant dataset.

`hard_emp_frontier_v2/` retains its existing single specialist, detector,
optimizer, hard/easy evidence, and one-probe training lag. Accuracy probes in
reverse use the same TRAIN-family return sampler and qualified-supplier rule
(strictly over 65% with the command above). Reverse probe evidence is named
`reverse-RADIUS-STEP.pt`, distinct from prior outward probes. The normal
four-bank DEV acceptance cards and sealed TEST families are never fed into
Hard EMP training. The accepted outward stages and their source SHA
certificates are not modified by the reverse scheduler.

New outputs:

- `hard_emp_reverse/plan.json` — immutable direction, starting checkpoint SHA,
  stage list, specialist directory
- `hard_emp_reverse/stages/return_RADIUS/` — live and accepted four-bank
  state, optimizer/RNG state, DEV results and history
- `hard_emp_reverse/stages/zero_line/` — final exact-cancellation stage
- `EMP_HARD_REVERSE_PROBE`, `EMP_HARD_REVERSE_ACCEPTED`, and
  `EMP_HARD_REVERSE_SUMMARY` in stdout

The first reverse stage begins from the **latest** forward checkpoint, not an
older accepted copy. The current forward stage's unfinished effort is not
transferred as an acceptance milestone. Specialist evidence already committed
before the switch remains available and continues in the same training queue.
