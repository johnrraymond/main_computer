# HybridLink: anonymous `ReduceOne` latent-head transfer experiment

## Intent

Train the **existing HybridLink head** to make **one** elementary calculation and replace **exactly one** subexpression in a domain-anonymous `<REDUCE>` state.  This is a new experimental run lineage; it does **not** update QW5 Stage 2, the certified numerical EMP heads, the Qwen base weights, or the successful micro-reduction text LoRA.

The old micro text model achieved 72/72 DEV hops and 12/12 free-running chains on one six-operation template, but the first OOD smoke challenge exposed failures on subtraction, negative literals, state substitution, and unfamiliar expression trees. Training the latent head directly is a *hypothesis test*, **not** an assertion that latent feedback already computes anything better.

**Core one-hop objective:**

```
<REDUCE>
A = 166 + 13
B = 135 - -44
R = A - B
</REDUCE>
    ->
<REDUCE>
A = 179
B = 135 - -44
R = A - B
</REDUCE>
END_RUNG
```

The model may legally reduce a different single available operation. No names, strategies, payoffs, Hawk/Dove semantics, or teacher answers are included in inference prompts. The arithmetic teacher is used **only** to generate train/DEV targets and to validate the result independently; a failing chain is never repaired with an oracle next state.

## Necessary latent inference corrections

This patch fixes two pre-existing issues in `tools/nanojev_hybridlink_rung_reduction.py`:

1. **Latent target-token alignment.**  The latent embedding sits at position `len(prompt)+1`; `</latent>` sits at position `len(prompt)+2`. The first target token must be predicted **from the `</latent>` hidden state**, not from the latent embedding position. Prior `latent_loss` incorrectly began one token early. This patch makes training match `model.rollout(..., depth=1)` + the end-marker inference protocol. Earlier latent training results, if any, should not be treated as evidence for this corrected objective.
2. **Truncation of long output states.**  The early-stopping decoder previously sliced complete text to 300 characters. That could corrupt valid reductions on 10/20-operation states. The patch returns the full generated text up to the configured token budget and still stops on a complete `END_RUNG` line. Strict `ReduceOne` verification remains unchanged.

Both changes have CPU regression tests. Neither changes an existing checkpoint.

## Dataset and checkpoints

The frozen text checkpoint is `C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt` (your earlier text-adapter winner at step 600).

The new dataset is freshly and deterministically generated; it is **not** produced from OOD failure examples or OOD oracle targets. It includes 192 train / 48 DEV *new* scenarios across mixed addition, subtraction and multiplication, signed inputs and negative intermediate values, different tree shapes, reverse directions, input scales 8 through 1 million, and variable 2/4/6/10-op sequences. This yields 1,056 new train and 264 new DEV hops by default. The original 36 training scenarios (216 hops) provide replay, and the original 12 DEV scenarios (72 hops) remain independent retention controls. Every fourth optimizer step draws from the original training deck. Dev checkpoint selection is weighted 75% new and 25% retention **teacher-forced losses**; this does not certify generated correctness.

The previously defined 46-case OOD suite is kept unchanged and its full/selected subsets evaluated only **after** training. Exact intermediate-state overlap with its cases is forbidden by a dataset guard. Because the new training deliberately includes multiple tree families and operators, not every original OOD *structure category* remains fully OOD as a grammar; **the exact OOD cases, the 20-operation and 4-million extremes, and matched sign/direction cases remain held out**. A second independent novel-grammar evaluation will be appropriate if this transfer works.

The run saves only the learned head weights (not a modified LoRA), a hash-bound immutable text-parent SHA, a frozen source fingerprint, and independent training/probe records. `best.pt` is selected on DEV loss, not the TEST split. No TEST cases are evaluated by this experiment.

## 1. Apply

```powershell
python .\new_patch.py `
  'C:\Users\subsi\Downloads\hybridlink_micro_latent_transfer_patch.zip'
```

## 2. CPU-only dataset preview

```powershell
& $py -m tools.nanojev_hybridlink_micro_latent_transfer `
  --stage preview `
  --device cpu `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

No source model or checkpoint weights are loaded in the preview; the existing text checkpoint and run configuration must be present to identify the retention deck.

## 3. First training attempt: only the HybridLink head

```powershell
& $py -m tools.nanojev_hybridlink_micro_latent_transfer `
  --stage train `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --steps 1600 `
  --eval-every 100 `
  --lr 0.0001 `
  --max-seq 1024 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1"
```

Live `MICRO_LINK_TRAIN_PROGRESS` shows new-DEV and retention-DEV losses **separately**. `MICRO_LINK_TRAIN_RESULT` reports `TRAINED_UNCERTIFIED`. If any prompt + target exceeds the token budget, training refuses *before* creating an output run; raise `--max-seq` to 1536 or 2048 if your GPU supports it. The output directory must be absent or empty; neither failed experiments nor previous checkpoints are overwritten.

At 1600 steps with every fourth step reserved for retention, the scheduler exposes the learner to all 1,056 shuffled new training hops at least once. This is not trainable arithmetic execution outside the model. Model-facing text remains solely anonymous numeric state. The Qwen text LoRA/base are frozen and their preservation checked after training; only the HybridLink head is optimized. This approach could fail if a frozen text decoder cannot render an unlearned numerical operation despite good feedback; that is an experimental outcome, not a reason to quietly unfreeze more weights.

## 4. Small held-out OOD comparison across identical prompts

```powershell
& $py -m tools.nanojev_hybridlink_micro_latent_transfer `
  --stage probe-ood `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt" `
  --suites control,structure,depth,negative,magnitude,direction `
  --limit-per-suite 1 `
  --controls text_only,zero_latent,intact `
  --progress-every 5 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_ood_smoke_v1"
```

`text_only` is the frozen text baseline; `zero_latent` uses an identical one-step latent protocol with zero feedback; `intact` uses the trained HybridLink head. Matched one-hop disagreements (`intact_only`, `control_only`, `both_correct`, `both_wrong`) identify causal benefit/harm in the feedback values. Full free-running chains are also scored and stop on any invalid one-step state. Training never provides the expected next state at inference.

**Success requires intact feedback beating zero_latent on unseen, previously hard reductions, without degrading familiar DEV controls.** A lower teacher-forced loss alone is not sufficient. With only one selected case per suite the smoke is diagnostic, not certification.

## 5. Full familiar DEV retention check

```powershell
& $py -m tools.nanojev_hybridlink_micro_latent_transfer `
  --stage probe-retention `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt" `
  --controls text_only,zero_latent,intact `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_retention_v1"
```

## 6. Targeted challenge before a full OOD matrix

```powershell
& $py -m tools.nanojev_hybridlink_micro_latent_transfer `
  --stage probe-ood `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt" `
  --suites structure,negative,direction `
  --controls text_only,zero_latent,intact `
  --progress-every 10 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_ood_targeted_v1"
```

Omitting `--limit-per-suite` checks every case in the selected suites. The complete 46-case × three-control matrix can make thousands of autoregressive GPU calls, so it should follow a useful smoke result. Failed generated outputs and their exact teacher-state inputs are saved to `one_steps.json`; no source files or weights change during probes.

## Interpreting an unsuccessful training result

- `text_only` remains poor, `zero_latent` remains poor, and `intact` poor: the frozen output interface may lack necessary syntax/numerical competence, or the head hasn't learned a beneficial correction. Do not assume which without inspecting predictions.
- `intact` worse than `zero_latent`: head feedback actively degrades inference at one step. Prefer zeroed feedback until improved, not an automatic multi-step expansion.
- `intact` better on one-step but not full chains: retained state information or accumulated numerical mistakes are the next bottleneck.
- Retention degrades: head learns new tasks at the expense of familiar reductions; review replay schedule and checkpoint selection separately.

Both the old QW5 and the older source HybridLink checkpoints remain untouched.
