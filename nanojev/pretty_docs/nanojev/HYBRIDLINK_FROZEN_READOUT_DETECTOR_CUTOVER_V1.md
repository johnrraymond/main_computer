# HybridLink frozen Qwen numerical readouts + Answer Detector cutover (Patch 1 + Patch 2)

## Why

The previous `tools.nanojev_qwen_numeric_dual_cutover --stage train` modifies Qwen q/v LoRA and the Stage 1 probe while fitting numerical sidecars; Stage 1 teacher-target retrieval accuracy can regress. This new, **additive and versioned** cutover starts from the **original Stage 1 `best.pt`** and **never modifies Qwen or its transition probe**. It does **not** depend on the previous v2 numeric checkpoint. It reuses the v2 *implementation* of the independent real and complex readouts and their geometry but trains new sidecars on the original frozen Qwen state.

The program trains two modules:

1. A direct real-number code readout plus separately learned complex `(a,b)` code readout. Both are supervised on numerical labels independently; agreement is an auxiliary loss. `rho=asinh((y-1)/64)`, `rho_complex=log(hypot(a,b))`, so every complex phase on the unit circle is valid for numerical `y=1`. Geometry in float64, trainable projections in float32.
2. An Answer Detector `sigmoid(head(h))`. Its labels are positive for finished root `ANSWER` states and negative for all `PROBLEM` states, including valid intermediate results. In particular, a correct intermediate `1` must not induce a stop in `(2+(-1))+5`.

**Important limitation:** the Stage 1 training deck uses explicit `PROBLEM:`/`ANSWER:` text prefixes. The detector can potentially learn the prefixes instead of a transferable, latent notion of completion. It is only a supervised *proxy* completion detector until evaluated on independently generated HybridLink → Qwen continuous states. The checkpoint and handoff are marked **not latent certified**; this patch does not implement the recursive Stage 2 HybridLink loop and does not silently enable termination.

## Immutable/fast training

- Load `qwen_problem_answer_stage1_v1/best.pt` with strict Stage 1 lineage checks, and the original `hybridlink_micro_text_v1/best.pt` target teacher.
- Freeze **every** Qwen backbone parameter and Stage 1 transition probe; the optimizer only receives the new numerical and detector heads.
- Cache parent Qwen embeddings for **every distinct train and DEV source/target state**, keyed on the Stage 1 checkpoint digest, deck digest, encoder code digest, input texts, and encoding parameters; cache original text teacher DEV targets separately, keyed by text checkpoint digest.
- Only one frozen DEV Stage 1 transition evaluation per training invocation; output the original `payload_accuracy` and `control_accuracy` as the immutable retention baseline. No source Qwen forward/backward operations occur during optimization and no frozen DEV targets are recomputed on every evaluation step.
- Remove exact training/DEV state-string overlap from detector DEV evaluation. Fail closed if either DEV class becomes empty. Report the count removed.
- Print progress while encoding cache misses (`FROZEN_CACHE_PROGRESS`), and hits (`FROZEN_CACHE_HIT`). Cache entries are atomic and provenance validated. Reuse a cache between separate run directories with `--cache-dir`.
- Prevent overwriting the Stage 1 parent, text teacher, or other protected model dirs. Save `best.pt` containing **only** the new sidecar state dicts and metadata, never a copy of Qwen adapters. Validate frozen Qwen weight hashes and absence of gradients.
- No LM head, textual intermediate generation, or oracle target used in `inspect`. No test split consumed.

## Install

This ZIP is a manifest-free overlay containing three files under `main_computer_test/`, compatible with `new_patch.py`. The prior v2 numeric cutover files and the original Stage 1 script must already be installed.

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\hybridlink_frozen_readout_answer_detector_patch_v1.zip'
```

## Run

Use `(.venv-diagram)` and your `$py` Python variable, as in Stage 1/v2 runs.

**Preview: no model load:**

```powershell
& $py -m tools.nanojev_hybridlink_frozen_detector_cutover `
  --stage preview --device cpu `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

**Freeze Qwen, cache vectors, train both new heads:**

```powershell
& $py -m tools.nanojev_hybridlink_frozen_detector_cutover `
  --stage train --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --steps 1200 --eval-every 100 --batch-size 64 --encoder-batch 12 `
  --max-tokens 192 --numeric-lr 0.0002 `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1"
```

**DEV probe using frozen cache, without reading the original text teacher again:**

```powershell
& $py -m tools.nanojev_hybridlink_frozen_detector_cutover `
  --stage probe --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1"
```

**Headless inspection of a nonterminal state:**

```powershell
& $py -m tools.nanojev_hybridlink_frozen_detector_cutover `
  --stage inspect --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --state "PROBLEM: (2+(-1))+5"
```

**Prepare the still-inactive Stage 2 handoff:**

```powershell
& $py -m tools.nanojev_hybridlink_frozen_detector_cutover `
  --stage handoff --device cpu `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_handoff_v1"
```

## Stage 2 bridge / stability interface (not yet activated)

Import `detector_head`, `vector_drift`, and `classify_latent_step` from `tools.nanojev_hybridlink_frozen_detector_cutover`. The intended next cutover calls HybridLink to generate a **new Qwen latent input**, obtains the Qwen hidden state after feedback, and compares adjacent matched-context state vectors:

```
relative_drift = norm(h_next - h_before) / max(norm(h_before), epsilon)
```

The pure transition contract returns `ANSWER` if the detector's completion probability meets the threshold; `STALLED` if it fails but the vector is stable; otherwise `CONTINUE`. **Stalled does not mean solved**. Do not infer a fixed point from independently prompted text, teacher targets, unchanged cached embeddings, or vectors from different incompatible contexts. The actual T00–T08 HybridLink loop and closed-loop detector validation belong to the next patch.

## Acceptance and measured output

The console reports `FROZEN_DETECTOR_PROGRESS` every N sidecar optimizer steps and `FROZEN_DETECTOR_RESULT` at completion. Confirm that `qwen_frozen_verified=true`, `stage1_probe_frozen_verified=true`, `parent_unchanged=true`, and that Stage 1 DEV control/payload retention is exactly the parent model's (no Qwen changes are possible). Inspect DEV detector false-positive rate, true-positive rate, and hard intermediate-unity false stops, plus both numerical readout code MAEs and agreement. **A low false-positive rate paired with zero true-positive rate is NOT a successful detector**, even if a checkpoint is saved.

The final `best.pt` is tagged `UNVALIDATED_FOR_LATENT_FEEDBACK`; **no automatic promotion**. CUDA Qwen training and generalization to actual continuous latent states have not been evaluated by the CPU fixtures. Continue to the actual self-terminating HybridLink runtime only after the detector demonstrates useful completion discrimination and the new Stage 2 produces meaningful vector feedback.
