# NanoJev — Learned Router for the Certified Zero Line and Absolute Bank 128–159

This is a **separate experiment**. It does not alter the radius-127 ladder, the promoted 128–159 bank, the absolute-bank training harness, or any Gaussian experiments.

## The scientific question

Can a neural router, receiving only NanoJev's frozen representations of the *input operands*, learn when to use the already-certified base model versus the independently trained 128–159 MLP bank? This must work on unseen inputs **without** code calculating a bank label or a known sum to make its choice.

The earlier implementation's `max(abs(A), abs(B))` rule is not neural routing. This experiment measures that rule solely as a **diagnostic comparator**. No deterministic fallback is permitted in the learned path.

## Run

Apply this ZIP so `main_computer_test/tools/nanojev_zero_line_learned_router.py` becomes `tools/nanojev_zero_line_learned_router.py` in the Main Computer repository. (The paths inside the ZIP are rooted at `main_computer_test/`.)

Use the same certified backbone and promoted bank that produced the audited results:

```powershell
& $py -m tools.nanojev_zero_line_learned_router `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_learned_router_128_159_v1" `
    --band "128:159" `
    --device cuda `
    --steps 2000 `
    --probe-every 200 `
    --batch-size 128
```

At each probe: `CENTER` is complete-domain arithmetic accuracy, `BAND_DEV` is arithmetic accuracy on held-out pair families, `BANK_ROUTE` is the bank-choice fraction on band DEV, and `CENTER_ROUTE` is the bank-choice fraction on the certified base deck. All are derived from the **hard neural choice**. The best checkpoint is chosen using center retention and band DEV, not OOD.

Read `result.json` for full counts and comparison. Candidate always saved as `candidate.pt`; only a passing candidate also receives `promoted.pt`. Neither the source nor specialist checkpoints are changed.

## Prove the inference path contains no answer

After the run, invoke inference with arbitrary operands:

```powershell
& $py -m tools.nanojev_zero_line_learned_router `
    --phase predict `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --bank-output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_128_159_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_learned_router_128_159_v1" `
    --device cuda `
    --a 149 --b -133
```

It prints the neural bank probability, selected expert, and predicted signed sum. It does **not** construct any correct-answer target, look up the magnitude interval, invoke the Python magnitude router, or correct the model's answer. The `candidate.pt` choice is diagnostic until promotion; pass `--promoted-only` to refuse an unqualified checkpoint.

For a representation ablation, run a *separate output directory* with `--backbone-only`. This denies the router the frozen input-token embeddings and leaves only the frozen recurrent state.

## How training works

- Frozen certified backbone produces features, original signed-digit logits and the existing operand-token embeddings. The router sees the features and token embeddings, **not** original logits, predicted result, the correct result, or magnitude labels. The bank is also frozen.
- The router's single scalar logit chooses the **entire** base answer or the **entire** bank answer. Training uses a differentiable mixture likelihood of the complete correct signed-digit answer; it does not supervise a bank-ID class.
- The supervised `A+B` label appears only inside training loss and evaluation. Magnitude is used solely to construct controlled TRAIN/DEV/OOD populations and compute the **comparison baseline after inference**. Neither enters router.forward(), `hard_select()`, or the standalone `--phase predict` path.
- Equal-sized random samples from the complete certified base deck and new-bank TRAIN examples are supplied in each optimization batch. This addresses both competence and old-domain retention. New-bank DEV and held-out maximum-magnitude OOD examples are never used for gradients.
- Original and specialist logits are cached before router training, with no gradient paths back into either network.

## Four comparisons from the same examples

1. Certified base alone.
2. Frozen new bank alone.
3. **Python input-magnitude reference:** the previous engineered routing rule, for comparison only.
4. **Learned neural route:** a hard neural choice, no fallback.

`oracle_any_expert_upper_bound` is an **evaluation-only** upper bound that checks both answers against gold targets. It is not used in training, routing, or promotion, and must never be presented as deployable performance.

The full 65,025-card certified deck is evaluated through the *learned hard router*; it is not automatically bypassed. Thus, 100% retention would be an actual routing result, not guaranteed protection.

## Go/no-go thresholds (defaults)

- Exactly **100%** correct on the full original deck through learned routing.
- At most **1%** bank selection over original-deck examples (router mostly recognizes its base region).
- At least **90%** bank selection on held-out DEV and OOD 128–159 examples.
- Learned arithmetic accuracy at least **90%** on both DEV and OOD.
- Learned arithmetic within **2 percentage points** of the deterministic input-magnitude comparator on both DEV and OOD.
- No newly incorrect predictions on residuals 0, ±1, ±2 when compared with the base in DEV/OOD.

All thresholds are checked **after** hard routing, with no forced correct selection and no rescue on failure. These are meaningful operational tests, not proof that the network has learned scale-independent arithmetic. The already-reused OOD population is a development benchmark; reserve a novel final test for later certification.

## Fail-closed and interpretation

The run refuses checkpoint hash mismatches, unpromoted banks, overlapping output paths, missing certified base accuracy, and absent data splits. Source and bank SHA-256 hashes are rechecked after training. No actual CUDA training was possible in the patch development environment; local CPU/unit tests do not prove the router will pass on your real checkpoint.

If the model gets the correct answer but selects the bank on almost the entire base deck, it has **not** met the desired routing criterion. If it cannot match engineered routing after training, the next research task is improving the router's representation or training, **not adding 160–191 yet**.
