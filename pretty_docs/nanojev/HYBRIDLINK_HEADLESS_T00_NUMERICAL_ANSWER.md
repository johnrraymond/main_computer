# HybridLink headless T00 — `PROBLEM: 6+6` → `(ANSWER, 12)`

## Objective and scope

This is the **first** headless numerical inference experiment, intentionally separated from the HybridBeast and existing QW5 Stage 2.

We want a Qwen hidden-state representation of an anonymous arithmetic input to enter a trainable HybridLink and produce (1) a **vector-native control decision**, `PROBLEM` versus `ANSWER`, and (2) a **signed numerical readout**, with the vocabulary LM head entirely bypassed during feature encoding, training, probing, and prediction.

```
Input tokenizer:           PROBLEM: 6+6
Frozen Qwen transformer:   hidden state h          (get_decoder(), no LM logits)
Trainable HybridLink:      z = Link(h)
Tiny control projection:   ANSWER                   (2 logits, not vocabulary logits)
Tiny numerical projection: +12                      (sign + 8 independent base-10 digits)
```

**The examples `PROBLEM: 6+6` and `ANSWER: 12` express the supervision contract, not the model's text output.** `--stage predict` prints JSON for diagnostics; no autoregressive text decoding occurs. The numerical output is from a fixed-width signed-number codec, not from Qwen's vocabulary head.

The old Qwen text checkpoint and newly trained HybridLink head are loaded strictly as source/warm-start artifacts, not rewritten. Qwen's base/LoRA are frozen. Training changes **only** the new experiment's HybridLink copy and numerical/control readout; the checkpoint contains no LM or Qwen weights.

**Current limitations (deliberate):** this stage learns T00 terminal signed *addition*, and it teaches the control head that two-operation inputs are still `PROBLEM`. It does **not** yet emit the full next-PROBLEM vector, feed that vector through another rung, unwind parent frames, or demonstrate termination at unknown depth. Therefore, detecting `PROBLEM` here is not proof of autonomous recursion. This is the necessary first experiment, not the complete ladder.

## Training data and boundaries

Default generated splits are 1,024 train, 256 DEV, 256 OOD, and 256 reserved TEST cases; exact input strings are disjoint. A training sample really is `PROBLEM: 6+6`, so the requested elementary relationship is included. Other samples include signed operands, equal-and-opposite cancellation, residuals 0/±1/±4 at large anchors, and conventional additions. Every fifth problem is a nested sum such as `PROBLEM: (6+6)+3`, with expected control `PROBLEM` and **no** numerical answer target; other inputs expect an `ANSWER` and a signed integer.

A separate OOD split uses different input scales. This is a *distributional* holdout, not a proof of unseen-operator generalization. The fixed eight-digit codec supports signed magnitudes up to 99,999,999, but the training scale does not justify claiming competence across that entire range. Genuine arithmetic and signed zero-line transfer must be demonstrated by DEV/OOD accuracy, including negative and exactly zero answers. Training controls cannot certify themselves.

Scored metrics: action accuracy, terminal-answer exact accuracy, nonterminal-gate accuracy, zero-answer accuracy, near-zero accuracy, sign accuracy, plus a zero-vector ablation. The zero-vector ablation is an interface check; it is not by itself evidence that numerical reasoning improved versus a separately trained readout on frozen features.

## Install

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\hybridlink_headless_t00_numeric_answer_patch.zip'
```

## 1. Validate corpus without loading Qwen

```powershell
& $py -m tools.nanojev_hybridlink_headless_t00 `
  --stage preview --device cpu `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt"
```

## 2. Train the HybridLink + numerical/control projection (no LM-head generation)

```powershell
& $py -m tools.nanojev_hybridlink_headless_t00 `
  --stage train `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt" `
  --steps 2400 --eval-every 200 `
  --batch-size 64 --encoder-batch 16 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_headless_t00_v1"
```

Each input first receives **one** frozen Qwen decoder pass. Hidden states are cached as frozen float32 features; the trainable HybridLink/readout optimize on this cache. Training output includes DEV action, exact numerical, signed zero, and near-zero accuracies. The checkpoint is selected by **DEV loss**, but probe accuracy must decide whether training actually worked. Neither prior source checkpoint is overwritten. The output directory must be empty or absent.

## 3. Read `PROBLEM: 6+6` without invoking the LM head

```powershell
& $py -m tools.nanojev_hybridlink_headless_t00 `
  --stage predict `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_headless_t00_v1\best.pt" `
  --problem "6+6"
```

The result is a `HEADLESS_T00_PREDICTION` JSON record: it reports the **model's** predicted `control` and `answer`. The script does **not** compute the correct answer as a fallback; if it predicts the wrong number, it reports the wrong number. If it says `PROBLEM`, no numerical result is claimed.

To inspect a nonterminal control decision instead, supply `--problem '(6+6)+3'`. Its correct label is `PROBLEM`, but a further-problem payload and recursive transition have **not** been trained yet.

## 4. DEV and OOD numerical accuracy, then sealed TEST

```powershell
& $py -m tools.nanojev_hybridlink_headless_t00 `
  --stage probe --split dev --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_headless_t00_v1\best.pt" `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_headless_t00_dev_v1"
```

```powershell
& $py -m tools.nanojev_hybridlink_headless_t00 `
  --stage probe --split ood --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --link-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_transfer_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_headless_t00_v1\best.pt" `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_headless_t00_ood_v1"
```

Do **not** use the reserved TEST split to iterate hyperparameters. Probe reports `HEADLESS_T00_PROBE_RESULT` with both intact and ablated metrics, and writes `result.json` / `errors.json`. It refuses to overwrite an existing output. Full TEST probing is one-shot per checkpoint and writes a consumption marker.

## Interpretation / next project milestone

Headless **readout** is successful only if the predicted control and signed answer are accurate on held-out cases, especially large opposite-signed operands with residual 0/±1/±4. `T00_TRAINED_UNCERTIFIED` is never a certification claim.

After it passes, extend the same `control, payload` interface so a nonterminal `PROBLEM` outputs a **latent next-problem payload** (not text), is consumed by Qwen/HybridLink at the next rung, and emits an `ANSWER` only after the terminal operation is resolved. That subsequent experiment must test learned stopping across unseen depths and correct stack unwinding. The current training experiment intentionally does neither.
