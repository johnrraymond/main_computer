# Gamer v1: LoRA → `to_numerical` / C0 Wide handoff (implementation v5 — superseded)

> **Current v6 correction:** The earlier statement that no dataset generator exists is no longer accurate. `tools.nanojev_gamer_v1_pairs` now generates source-recomputed 33-game sparse-table training records, and `tools.nanojev_gamer_v1_handoff prepare --output RUN` creates `certified_pairs.jsonl` directly. The `experiment` stage also creates the dataset automatically when missing. For the current working code, commands and test status see `GAMER_V1_GENERATED_SPARSE_TABLE_DATASET_V6.md`. The rest of this document records the historical v5 handoff design only.

## Governing architecture — not optional

The **original game-native *08 prompt** is the only model-side input for both learned tasks:

```text
                                    ┌─ Qwen LoRA completion supervision ─→ sparse mathematical table text
Original *08 prompt ─→ Qwen + LoRA ┤
                                    └─ frozen original-prompt hidden states ─→ existing C0 Wide head ─→ number
```

There is **no path** from the generated table, an oracle table, or any math-prompt text into `to_numerical`. The table text is exclusively an offline LoRA **supervised completion target**. The head uses the original prompt hidden activations and the **independently oracle-verified numeric answer**. Qwen does not have to emit a math prompt before numerical inference.

The existing autoencoder-derived/compressive methodology remains `tools.nanojev_qwen_c0_compressive_train`: `VARIANTS['wide'] = (1024, 512, 128, 32)`, `make_network`, `capture_features`, `signed_log`, `neural_loss`, and `eval_heads`. This is the existing C0 z′/z″ direct-numerical architecture; no new regression head replaces it.

## Implemented command and stages

New source-only module: `tools/nanojev_gamer_v1_handoff.py`. Run commands from the **existing repository root** using the **same Python environment** already used by the C0 experiments.

```powershell
$py = ".\.venv\Scripts\python.exe"   # or the existing GPU-capable interpreter
$cases = "runtime\reports\gamer-v1\certified_pairs.jsonl"
$run   = "runtime\reports\gamer-v1\cutover-smoke-001"

& $py -m tools.nanojev_gamer_v1_handoff validate --dataset $cases --output $run

& $py -m tools.nanojev_gamer_v1_handoff lora --dataset $cases --output $run `
  --model Qwen/Qwen3-0.6B --device cuda --lora-rank 16 `
  --lora-steps 200 --lora-eval-every 50

& $py -m tools.nanojev_gamer_v1_handoff switch --dataset $cases --output $run

& $py -m tools.nanojev_gamer_v1_handoff to-numerical --dataset $cases --output $run `
  --model Qwen/Qwen3-0.6B --device cuda --head-steps 1200 --head-eval-every 100

& $py -m tools.nanojev_gamer_v1_handoff probe --dataset $cases --output $run `
  --model Qwen/Qwen3-0.6B --device cuda
```

`switch` does **not** train or activate a production model. It validates the LoRA phase report, pins the exact adapter directory/content hash, verifies the original-prompt and data hashes, confirms handoff eligibility, then atomically writes `handoff.json`. `to-numerical` will refuse to run without the exact matching `handoff.json`. It loads Qwen with the frozen LoRA adapter, captures **only the original *08 prompt** in the existing C0 hidden-state encoder, and trains the Wide network using direct numerical targets. `probe` reopens the exact adapter and network and verifies the same lineage before evaluating dev and test splits.

An initial bounded experiment using a subset of games must explicitly pass **`--allow-partial-smoke` to every stage** and, because it cannot meet the 33-game gate, **`--allow-experimental` to `switch`, `to-numerical`, and `probe`**. This does not claim certification. For an interrupted training phase, pass `--resume` on that **same phase**. Resumption restores the saved optimizer and RNG state from `lora/latest.pt` or `to_numerical/latest.pt`; training totals may be extended, but backbone/adapter/data/hyperparameters must remain compatible. Already finalized phases reject accidental reruns, and all experiments require their own output directory.

## Input contract: externally certified data

The **sparse numerical-table experiment** from `GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md` still has to produce certified training triples. This handoff patch does not fabricate, infer, or automatically certify the missing table models. A row in the prospective JSONL dataset has the following fields:

```json
{
  "schema": "nanojev.gamer.v1.oracle-table-pair.v1",
  "game_id": "hawk_dove",
  "split": "train",
  "prompt_08": "... original game-native human-readable question ...",
  "math_prompt": "... populated sparse table, English labels, explicit arithmetic; NO solved answer ...",
  "answer": "... exact game-oracle numerical target ...",
  "oracle_verified": true,
  "table_verified": true,
  "oracle_fingerprint": "... SHA-256 of upstream oracle proof ...",
  "table_fingerprint": "... SHA-256 of upstream table reconstruction proof ...",
  "zero_line_residual": 0
}
```

The record shown is a **schema illustration**, not a supplied certified example. `zero_line_residual` is optional in the data contract, but recommended for meaningful zero-line diagnostics. It is **the signed residual of the game's defining balance condition**, NOT necessarily the sign of its final numerical answer. Numeric targets can be fractional. For full coverage the dataset must contain cases from **all 33** registered executable games and nonoverlapping **train/dev/test** splits. Model input fingerprints are computed directly from `prompt_08`; literal `math_prompt` contents do not enter the numerical-head capture function.

The loader verifies fields, finite targets, real game IDs, exact dataset provenance, distinct original prompts and declared attestations. **It does not independently reconstruct the mathematical tables or validate cryptographic signatures on the upstream proof strings**. Until the factorization harness produces actual oracle/table verification records, `oracle_verified` and `table_verified` remain upstream attestations, not a fresh reconstruction test performed by this module. The executable source-verified factorization gate therefore remains a separate required part of the cutover before real LoRA training is authorized.

## LoRA and switch gates

LoRA uses the current Qwen/PEFT causal-LM LoRA implementation with `q_proj`, `k_proj`, `v_proj`, `o_proj`; each supervised sequence is `original_prompt_08 + math_prompt` and the loss is **masked over all original-prompt tokens**. Greedy dev decoding reports strict exact match to mathematical-table targets, by game. This is a conservative metric and not yet a semantically equivalent table parser.

The best adapter is saved at `lora/best_adapter`, with adapter content fingerprint. Normal handoff requires **strict-match improvement over the base, no per-game dev regression, dev coverage of all 33 verified games, and nonpartial mode**. An explicit `--allow-experimental` option permits an isolated, unpromoted smoke without representing it as a passing model. Neither a LoRA handoff nor a Wide checkpoint constitutes release promotion.

## Existing Wide numerical implementation and checks

The Wide head's C0 exact-label `signed_log1p` target transform and auxiliary raw-head loss are reused, with no deterministic-teacher gradient, no table-feature leakage, and no generated-math-prompt input. Qwen/LoRA are entirely frozen during capture and Wide training. The original prompt is rejected if it would be silently truncated by the C0 tokenizer capture. Predictions are inverted to the original numerical domain for dev/test MAE, sign accuracy, integer rounded exactness where applicable, per-game metrics, and per-zero-line-residual diagnostics if supplied. The `to_numerical/wide.pt` checkpoint contains both head weights and pinned adapter/dataset fingerprints; it cannot silently be retargeted to a different LoRA adapter or dataset.

`wide.pt`, `lora/best_adapter`, and run reports are **experiment artifacts**, not production runtime activations. The existing Gamer release gates, model accuracy/retention targets, and original-prompt-only input invariant remain binding.

## Actual verification status

The new stage contracts, source integration, switch integrity checks, real PyTorch C0 Wide gradient updates, bounded Wide resume and re-probe were tested locally with simulated Qwen activations. No real Qwen+PEFT GPU training was executed in the patch environment (Transformers and PEFT were unavailable). The 33-game factor-table generator and its reconstruction audit are **not implemented in this specific patch**. A genuine Gamer model cannot be promoted from this smoke alone.

## Source-grounded dependencies

- `tools/nanojev_qwen_c0_compressive_train.py`: existing C0 variants, Wide network, capture, signed-log evaluation and supervision.
- `tools/nanojev_qwen_c0_backbone_phase.py`: existing Qwen3 PEFT LoRA training convention and target-module choices.
- `tools/nanojev_game_transformation_library.py`: exactly 14 matrix + 19 scalar executable game identities, excluding catalog-only entries.
- `tools/nanojev_game_native_objectives_v6.py`: verified game-specific scalar oracles and matrix objectives.
- `pretty_docs/nanojev/GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md`: governing factorization test and reconstruction criteria.
