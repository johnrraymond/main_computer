# Gamer v1 — *08 prompt-to-math curriculum, commands and probes

**Specification only.** The `tools.nanojev_gamer_v1` commands in this document are a **proposed implementation interface**; they do not exist in the verified November 3 source snapshot. Do not run them as though completed code were shipped. Stage 1 must be implemented and proven before Stage 2 begins. The mathematical prompt is a LoRA text-supervision TARGET, never an input to `to_numerical`. Both stages consume the original *08 prompt (Stage 2 via frozen Qwen+LoRA hidden activations).

## Training objective and supervision boundary

**Exactly:** `[T08/HDB08-style human-readable game situation] -> [uncomputed, English-readable mathematical prompt with explicit numerical tables]` (table format pending the revision-4 factorization test). The model's output is a deterministic, typed representation of *the game mathematics to compute*, NOT its computed result. Source-owned oracle provides the independent numerical answer in the offline record, withheld from the input and Stage-1 completion. The target should explain the mathematical setup in ordinary English and numerically populated tables. Internal typed expression trees may validate equivalence; they are **not** themselves a required Qwen output language.

**Revision 4 first gate:** run the source-verified table experiment in [`GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md`](GAMER_V1_SPARSE_NUMERICAL_TABLE_TEST.md) **before** implementing this LoRA curriculum. The three table families are candidate reusable math targets, not a proven complete schema. This document's CLI is still hypothetical.

Illustrative source-verified math-prompt presentation (Hawk–Dove opponent action = Hawk):

**Original *08 input:** Two competitors can choose Hawk (aggressive) or Dove (peaceful). If both choose Hawk, each loses 2 points. A Hawk facing a Dove receives 6 points while the Dove gets 0; if both choose Dove, each receives 3. Your opponent chooses Hawk. Which action gives you the better payoff?

**Stage 1 table-backed target (not solved):** “The opponent is playing Hawk. Compare your possible choices using the row-player payoffs below.”

| Your action | Payoff vs Hawk |
|---|---:|
| Hawk | -2 |
| Dove | 0 |

“Select the action with the greater payoff.” The text has no computed answer. This small target displays the *decision calculation*; the full source-verified mathematical representation must also retain relevant source rules, action labels and any additional constraints/objectives.

**Offline numeric oracle target:** kept separately and excluded from both the original *08 input and the Stage 1 completion. It trains `to_numerical` only through features captured from the **original *08 input**, never from the table.

## Zero-line sampling over the full game library

- Registry = entire 231-entry catalog. Eligible verified training population = all **33 executable v6 models** with objective-level proof; the 196 catalog-only and 2 provisional pilots cannot be gold.
- Build a **game × objective × mathematical kernel × *08 form × residual regime × operand magnitude** diagnostic grid. Do not confuse three output types with three game families.
- Each supported equality or boundary yields linked cases at exact zero, negative and positive neighbors, plus far examples, in valid game states. Use rational thresholds and documented numerical precision for nonintegral objectives. Multiple constraints or non-boundary objectives have their own verified sampling contract; do not fabricate zero-lines.
- Interleave models and kernels across the LoRA optimizer. Failure-targeted sampling may increase exposure without starving the rest. Use new scenario seeds and alternate prose to distinguish memorization of a story from reusable math-model extraction.
- Every scenario group and its wordings, parameter relabelings, and paired residual cases must be partitioned together whenever they share source variables/templates that could leak evaluation answers.
- Preserve T/HDB, C0 P0–P7 and nine-family math as **independent** diagnostic/transfer datasets rather than falsely labeling them native game objectives.

## Proposed command family (NOT YET IMPLEMENTED)

Stage 0 source-verified pairs and splits:

```powershell
& $py -m tools.nanojev_gamer_v1 `
  --stage prepare `
  --library verified-v6-all `
  --input-style star08 `
  --target-style canonical-math-v1 `
  --zero-line balanced-signed `
  --seed 1729 `
  --output runtime/runs/gamer-v1
```

Stage 1 Qwen prompt-to-math LoRA only:

```powershell
& $py -m tools.nanojev_gamer_v1 `
  --stage lora `
  --objective prompt-to-math `
  --model Qwen/Qwen3-0.6B `
  --device cuda `
  --rank 16 --lr 0.0001 `
  --batch 1 --accum 8 --steps 2000 `
  --probe-every 100 --eval-every 500 `
  --output runtime/runs/gamer-v1
```

The rank/learning rate/step count are initial **experiment defaults** informed by a different C0 research run, not established Gamer settings. Compare fresh-base LoRA against optional historical C0 LoRA initialization in distinct fingerprinted branches. No Wide optimizer runs here.

Real continuation (restore model, optimizer, scheduler, sampler, RNG):

```powershell
& $py -m tools.nanojev_gamer_v1 `
  --stage lora `
  --resume runtime/runs/gamer-v1/stage1/latest `
  --additional-steps 2000 `
  --device cuda
```

Independent Stage 1 probe (no weight mutation):

```powershell
& $py -m tools.nanojev_gamer_v1 `
  --stage probe `
  --checkpoint runtime/runs/gamer-v1/stage1/best `
  --suite translation-full `
  --output runtime/reports/gamer-v1-stage1-probe
```

Stage 2 `to_numerical` / Wide **only after** Stage-1 certification, using activations from the SAME original *08 prompt, NEVER the math prompt:

```powershell
& $py -m tools.nanojev_gamer_v1 `
  --stage wide `
  --backbone runtime/runs/gamer-v1/stage1/promoted `
  --input original-star08 `
  --device cuda `
  --variant wide `
  --steps 10000 `
  --output runtime/runs/gamer-v1
```

Final probe must include `--suite end-to-end-star08`, never only gold-math readout. All options/paths above are **interface requirements, not current code flags**.

## Diagnostics and probe specification

Every 100 optimizer steps (illustrative cadence) emit a machine-readable record and concise terminal line with `stage`, `global_step`, `source_bank_sha`, `game_count`, `trainable_param_count`, token CE/loss and checkpoint identity; `parse_valid_rate`, `game_id_exact`, `rule_exact`, `operand_exact`, `operator_tree_exact`, `semantic_math_equivalent`, `insufficient_info_precision/recall`, and per-cell denominators for `game/objective/residual_sign/residual_distance/operand_magnitude/*08_form`. Track correct -> wrong and wrong -> correct cases and the first stable failure frontier. Log sampler share by game and focus weights. Stage 1 **must not** report lower numeric MAE as evidence it learned to translate mathematical prompts; its target is a math *representation*.

Probe at fixed independent seeds and fresh generated scenarios, with at least: exact-zero balance, nearest legal negatives/positives, operand scales, contradictory/insufficient narratives, paraphrase invariance, irrelevant facts, swapped labels, sign/order reversals, equivalent mathematical forms, multi-step rules and game-family changes. Compare a supervised canonical target against model output with a parser and source equivalence checker. Include explicit `unsupported` handling. A good aggregate parse rate cannot hide a failed game; report worst cells and numerators/denominators.

**Stage-1 promotion** requires adequate coverage of all declared training objectives and predefined per-objective/zero-line/form gates on held-out scenarios, verified mathematical equivalence and retention. **Stage-2 promotion** requires typed numeric accuracy from original *08 prompt activations, not from generated/gold math text. **Release** requires two parallel predictions from the same original *08 input: math text and Wide number/action, with source gold offline only. See `GAMER_V1_RELEASE_GATES.md`.

Do not start large training if the dataset builder cannot demonstrate the game-source proof, render/parse/semantic-equivalence smoke, exact split integrity and explicit gold-free human input for every claimed supported mathematical objective.

## Architectural invariant — shared original prompt (revision 3)

**MUST:** Both training paths are conditioned on the same original *08 human-readable prompt. Qwen LoRA is supervised to emit an English-readable explicit mathematical prompt; `to_numerical` / Wide is supervised against the numerical oracle target using hidden activations from the frozen Qwen+LoRA processing the **original *08 prompt**. The mathematical prompt is a textual target/diagnostic only; it is **NEVER** an input, intermediate preprocessor, hidden-state capture source, teacher-forced substitute, or inference dependency for `to_numerical`. Training and inference must assert the original-prompt input fingerprint for Wide. Fail closed on a math-prompt-conditioned Wide input.
