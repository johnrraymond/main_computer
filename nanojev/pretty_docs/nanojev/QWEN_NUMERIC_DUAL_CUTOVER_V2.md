# Qwen numerical dual-readout cutover v2

## Purpose

Add a **versioned, additive** dual numerical representation to the already-trained Qwen `PROBLEM` / `ANSWER` Stage 1 lineage. Do not overwrite Stage 1 checkpoints, retroactively re-score existing benchmarks, activate HybridLink recursion, or substitute the vocabulary LM head.

This cutover introduces two **independent, supervised numerical projections** for *every training transition's next numerical reduction*: (1) a direct floating-point numerical representation and (2) a learned complex floating-point pair `(a,b)` representing the same number through its radius. Terminal `ANSWER` representations are also explicitly trained. They share Qwen's adapted hidden state but **do not derive either branch from the other**. The loss additionally measures their agreement, but agreement is not a proof of correctness.

The existing Stage 1 `PROBLEM` / `ANSWER` probe continues to train for next-state vector alignment and action classification. Qwen's **attention q/v LoRA** is fine-tuned jointly with the continuation probe and both numerical branches. No LM logits are evaluated, no tokens are generated, and the final math head is *not* installed.

**Scope:** This is a checkpoint-lineage and numerical-training cutover, not a live T08 recursion cutover. The old `tools.nanojev_qwen_problem_answer_stage1` is intentionally left unchanged. It must already be installed on your machine (from the previously applied Stage 1 patch). Future HybridLink Stage 2 will consume the prepared numerical-state contract; it is not silently rewired by this patch.

## Mathematical information contract

The real numerical target `y` is mapped to a logarithmic radial coordinate using a fixed positive scale `k=64`:

\[\rho_y=\operatorname{asinh}\bigl((y-1)/k\bigr)\]

Two **separate** Qwen hidden-state projections are trained:

* `direct_rho(h)` — direct log-scale value estimate.
* `(a(h), b(h))` — learned complex pair, with `complex_rho(h)=log(hypot(a,b))`.

Their independent decoded numerical estimates are:

\[y_d=1+k\sinh(\rho_d),\qquad y_c=1+k\sinh(\rho_c).\]

Numerical unity `y=1` has `rho_y=0`, so the entire unit circle (`a²+b²=1`) is a legitimate encoding of the number one. **The pair `(a,b)` is an encoding, not a claim that every unit-circle point equals the literal complex number `1+0i`.** Phase is not pinned to an arbitrary single angle. Zero and negative *real results* are represented by finite log-radius coordinates. Signed/scale transformations use float64 (`torch.float64`) in the geometry, with trainable projections operating in Qwen-compatible float32.

The supervised objectives are:

\[L=L_{\rm stage1\ transition}+\lambda\bigl(L_{\rm direct}+L_{\rm complex}+0.1L_{\rm agreement}+0.5L_{\rm terminal}\bigr).\]

The log-radius transform is bijective over the real answer domain; **it is not an ordinary complex-number arithmetic representation**. It preserves the numerical target independently of phase. For literal complex-valued answers, a later schema must support two real coordinates / two mappings. This cutover targets real signed additions only.

The `tools.nanojev_numeric_complex_contract` library exposes `target_code`, `decode_code`, `log_radius`, `loss`, `diagnostics`, and `features`. The `features` tensor has five entries per state:

```
[direct_rho, complex_rho, a, b, delta_rho]
```

These can be passed to a future HybridLink; they are **not** currently injected into its inference loop. The separate Answer Detector must still be trained to decide when the *whole* computation is complete. A valid unit radius alone does not imply that the computation has finished or is correct.

## Installation

````powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\qwen_numeric_dual_readout_cutover_v2_patch.zip'
````

## 1. Inspect the preserved Stage 1 parent (no model load)

````powershell
& $py -m tools.nanojev_qwen_numeric_dual_cutover `
  --stage preview --device cpu `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
````

The preview verifies both parent hashes and deck determinism, then counts numerical boundaries by DEV/OOD/TEST and the saved train split. It never loads Qwen.

## 2. Train Stage 1 continuation with dual numerical readouts

````powershell
& $py -m tools.nanojev_qwen_numeric_dual_cutover `
  --stage train --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --steps 1200 --eval-every 100 `
  --batch-size 12 --encoder-batch 12 --max-tokens 192 `
  --lr 0.00002 --numeric-lr 0.0002 --numeric-weight 0.5 `
  --output "C:\Users\subsi\NanoJev\runs\qwen_numeric_dual_cutover_v2"
````

Original Stage 1 and text checkpoints remain immutable. The cutover checkpoint contains Qwen LoRA, original Stage 1 transition probe, both readout projections and configuration/source hashes. Training uses **oracle arithmetic only to construct labels**, not to infer a numerical answer. It supervises both current `PROBLEM` hidden states and terminal `ANSWER` hidden states. The numerical target at a source transition is **the next reducible addition**, *not* necessarily the root answer; the source-to-next-state vector probe still has its own objective.

The run selects `best.pt` using a DEV-weighted score combining action, next-state vector retrieval and direct/complex code accuracy. `NUMERIC_CUTOVER_PROGRESS` reports separate errors for each readout and their agreement. `TRAINED_UNCERTIFIED` means not yet certified for live recursion. Qwen training is in-place on a disposable model loaded from the parent; parent files remain unchanged.

If memory is limited, use `--batch-size 4 --encoder-batch 4` with a **different output folder**; no training output is overwritten.

## 3. Inspect any state without oracle arithmetic or LM-head symbols

````powershell
& $py -m tools.nanojev_qwen_numeric_dual_cutover `
  --stage inspect --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\qwen_numeric_dual_cutover_v2\best.pt" `
  --state "PROBLEM: 6+6"
````

Displays predicted control, direct number, complex-derived number, `a`, `b`, phase, logarithmic radius and disagreement. These are genuine learned predictions, **not** ground truth or externally calculated answers. A wrong prediction is shown as wrong. The control probe is a temporary Stage 1 classifier, not the final autonomous Answer Detector.

## 4. Read-only OOD evaluation

````powershell
& $py -m tools.nanojev_qwen_numeric_dual_cutover `
  --stage probe --split ood --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\qwen_numeric_dual_cutover_v2\best.pt" `
  --output "C:\Users\subsi\NanoJev\runs\qwen_numeric_dual_cutover_ood_v2"
````

Separate direct / complex code errors and agreement are reported; transition payload retrieval remains a **teacher-candidate-bank diagnostic**, not actual vector-generation or recursive completion. Do not repeatedly probe sealed TEST during tuning; a completed TEST probe writes a one-shot marker.

## 5. Explicit, not-yet-activated handoff to HybridLink

````powershell
& $py -m tools.nanojev_qwen_numeric_dual_cutover `
  --stage handoff --device cpu `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\qwen_numeric_dual_cutover_v2\best.pt" `
  --output "C:\Users\subsi\NanoJev\runs\qwen_numeric_dual_handoff_v2"
````

Creates `numeric_handoff.json` with numeric feature names, scale, checkpoint SHA256, source lineage and `PREPARED_NOT_ACTIVATED`. The next HybridLink cutover will use it to train actual vector feedback, answer detection and finally the math head. **The current patch does not replace an existing production or test worker, and doesn't claim an already working T00→T08 loop.**

## Validation

- 200 CPU tests passed (new numerical/lineage isolation tests plus existing HybridLink regression tests).
- Mocked end-to-end Stage 1 parent -> continued LoRA training -> DEV probe -> headless numerical inspect -> handoff.
- Original checkpoint SHA unchanged; no LM head called and no oracle consulted at `inspect`.
- Full CUDA compatibility with your actual Qwen weights **not measured locally**. Run `preview` first, then train and inspect.
