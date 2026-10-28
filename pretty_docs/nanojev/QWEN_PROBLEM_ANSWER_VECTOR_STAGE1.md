# Stage 1 — Qwen numerical ladder representations (`PROBLEM` / `ANSWER`)

This is the first stage of the **Qwen → HybridLink → final math head** program. It tunes Qwen's *existing attention LoRA* to distinguish and represent both transition types:

```
PROBLEM: 6+6                  → ANSWER: 12
PROBLEM: (6+6)+3              → PROBLEM: 12+3
PROBLEM: 12+3                 → ANSWER: 15
PROBLEM: ((6+6)+3)+4          → PROBLEM: (12+3)+4
PROBLEM: (12+3)+4             → PROBLEM: 15+4
PROBLEM: 15+4                 → ANSWER: 19
```

The textual examples above are **supervision definitions**, not generated inference tokens. The training process **does not invoke the LM head**, and outputs vectors + direct two-way `PROBLEM`/`ANSWER` logits from a temporary training probe. The model is still Qwen; **HybridLink, its trained head and the final math head are not trained in this stage**.

## Training objective

1. Load the existing micro-text LoRA checkpoint for Qwen as a **warm start**, without overwriting it.
2. Extract each next-state target's last-layer hidden vector through Qwen's transformer (`get_decoder()`) **once before training**. These teacher vectors are fixed; the teacher is not permitted to track the changing student.
3. Enable gradients on Qwen's q/v LoRA parameters only. Base Qwen weights, embeddings, output vocabulary projection, and the legacy HybridLink remain frozen.
4. Train the student Qwen representation together with a **provisional** control/payload probe. The probe predicts `PROBLEM` vs `ANSWER` and maps the final hidden state to the frozen next-state teacher's vector space.
5. Optimize supervised control loss, same-kind contrastive payload alignment with repeated identical targets treated as positives, and direct cosine alignment. Signed arithmetic and ladder sequencing are provided by a deterministic teacher *during construction of training targets only*, never during inference.

The provisional mapping is **not** HybridLink or the final math head. Retain its checkpoint to initialize the Stage 2 research; do not pretend its nearest-neighbor candidate retrieval constitutes generating a new latent problem vector that can already be recurrently consumed. For Stage 2, the mapping can be handed off to a trained HybridLink that must generate and reinject actual latent payloads.

## Dataset, controls, and expected metrics

- Deterministic disjoint source states from chains with initial depths 1, 2, 3, 4, 6, 8 and 12 operations.
- Four splits: `train`, `dev`, `ood`, sealed `test`. OOD uses alternate operand scales. Values include signed inputs, exact zero, near cancellation, and some large operands.
- Both actions always appear, with supervised target **vectors** representing the next problem or the answer.
- The evaluator checks action accuracy and target-**vector** retrieval separately, including type-specific metrics. A retrieved candidate comes from a bank of all teacher-encoded possible next states for that split; the correct candidate is present by design. **This is a representation-alignment diagnostic and does NOT certify free-running recursion or numerical inference.**
- The probe compares fine-tuned Qwen to the unmodified warm-start LoRA **under the same trained readout**, isolating whether the backbone update is useful.
- The full vocabulary LM head is never invoked. No HybridBeast inference, QW5 source, HybridLink optimization, or math head is involved.
- Checkpoints include only Qwen LoRA weights, the provisional control/payload probe, data/config/source hashes and training metrics.

## Apply the patch

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\qwen_problem_answer_vector_stage1_patch.zip'
```

## CPU preview, no model loaded

```powershell
& $py -m tools.nanojev_qwen_problem_answer_stage1 `
  --stage preview `
  --device cpu `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

Expected sample starts with `PROBLEM: 6+6 → ANSWER: 12` and a multi-hop sequence. Output includes counts per class and split, plus a reproducible deck hash.

### Dataset collision repair (2026-10-27)

The initial Stage 1 generator could raise `STAGE1_CANNOT_GENERATE_DISJOINT_DECK` at default case `train-00175`: the depth-one cancellation examples repeatedly drew from only a few `scale=8` operand pairs. The corrected generator keeps the first eight attempts at the nominal operand scale and **widens the sampling range on repeated collisions**. It does not drop cases, relax cross-split source-disjointness, train on OOD/test targets, or alter the initial `6+6 → 12` example. The scale list identifies initial sampling bands, not strict hard caps after collision retries.

The full default deck produces 448 train + 96 DEV + 96 OOD + 96 TEST chains, **3,756 unique source transitions** (2,304 / 484 / 484 / 484). The complete deck hash is determined by the fixed seed. The patch fixes the preview before model loading; no existing checkpoints are changed.


## Fine-tune Qwen, **without** HybridLink training

```powershell
& $py -m tools.nanojev_qwen_problem_answer_stage1 `
  --stage train `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --steps 1200 `
  --eval-every 100 `
  --batch-size 12 `
  --encoder-batch 12 `
  --max-tokens 192 `
  --lr 0.00005 `
  --probe-lr 0.0002 `
  --output "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1"
```

The trainer checkpoints the best **DEV vector-retrieval accuracy** (tie-break by control accuracy), not the last optimizer step. It saves `best.pt`, `run_config.json`, and `result.json`. Output directories must be new or empty. Original weights/checkpoints remain unchanged. The initial teacher target vectors are computed with the warm-start model **before any parameter updates**.

**Observe:** `QWEN_STAGE1_PROGRESS` reports separate `dev_control_accuracy`, `dev_payload_accuracy`, `dev_answer_payload_accuracy`, and `dev_next_problem_payload_accuracy`. A 100% control score can be achieved by simple syntactic distinctions; that alone is not a success criterion. The payload must retain content, and ideally adapted Qwen must outperform the frozen parent under the same trained probe.

If GPU memory is tight, try `--batch-size 4 --encoder-batch 4`, in a new output directory. The frozen parent may consume device memory in model setup; never move or delete existing research runs just to make this experiment fit.

## Held-out OOD diagnostic (read-only)

```powershell
& $py -m tools.nanojev_qwen_problem_answer_stage1 `
  --stage probe `
  --split ood `
  --device cuda `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --output "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_ood_v1"
```

Do not use the sealed TEST split while tuning the run. It supports one-shot evaluation only, marked under the checkpoint directory after a completed test run.

## Next stages (not bundled)

**Stage 2 — HybridLink**: freeze the adapted Qwen initially; use these supervised transition vectors to train HybridLink to turn Qwen's last-layer state into a real next-state latent embedding, reinject it into the transformer, and independently recognize `PROBLEM` / `ANSWER` at each iteration. Evaluate free-running transitions, actual vector propagation, learned stopping, and eventual parent unwinding. **No LM-head tokens inside the loop.**

**Stage 3 — math-training sidecar**: after a functioning recursive protocol, add supervised numerical objectives on terminal hidden states and propagate loss back into HybridLink and optionally Qwen. The purpose is improving numerical accuracy, not making the protocol itself work.

**Stage 4 — final direct math readout**: install a numerical head on Qwen's last-layer final `ANSWER` hidden state to emit the signed number without the vocabulary LM head. The LM head can remain diagnostic but must not participate in runtime recursion or number readout.

Training from this Stage 1 checkpoint makes no claims about arithmetic generalization until these later stages demonstrate it.
