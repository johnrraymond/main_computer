# Gamer v1 — missing dataset generator fixed (source overlay v6)

This is a **source overlay** for the existing Main Computer repository. It extends the existing v5 LoRA → C0 Wide handoff; it does not replace the C0 autoencoder or activate production models.

## Install on Windows

Save `gamer_v1_complete_cutover_v6.zip` to Downloads. From the `main_computer` repository root, using the already-active Python environment:

```powershell
Expand-Archive "$HOME\Downloads\gamer_v1_complete_cutover_v6.zip" -DestinationPath . -Force
python -m pytest -q tests\test_nanojev_gamer_v1_handoff.py tests\test_nanojev_gamer_v1_pairs.py
```

Only the specifically named files in this ZIP are created or replaced. No checkpoints, production configurations, training data, or unrelated source files are included.

## Run the previously missing step

```powershell
python -m tools.nanojev_gamer_v1_handoff prepare --output runtime\reports\gamer-v1\cutover-002
```

This single command automatically creates `certified_pairs.jsonl` under that output directory. It then rederives every record from the real 33-game source registry (14 matrix, 19 scalar), evaluates generic factor calculations, compares them against independent game source oracles/operators, fingerprints the source, and pins the dataset manifest. It also writes `sample_pairs.md` and `factorization_summary.csv`. No model download or CUDA is required to prepare the dataset.

The default deterministic build creates **594 original *08-style English prompts**, their **English + numerical sparse table** targets with blank result cells, and oracle numerical answers; 396 train / 99 dev / 99 test, 18 per game. The alternative `--cases-per-game 6` produces a shorter **nonrepresentative** coverage smoke but is not needed. To inspect the training targets, open `runtime\reports\gamer-v1\cutover-002\sample_pairs.md`.

To recheck that output without creating anything else:

```powershell
python -m tools.nanojev_gamer_v1_handoff validate --output runtime\reports\gamer-v1\cutover-002
```

**Do not run `prepare` twice at the same output directory.** The command refuses to overwrite a generated dataset or repoint an existing training manifest. Choose a fresh directory such as `cutover-003` for a new campaign. The exact old `cutover-001` directory can remain untouched.

## One-command training experiment (optional, GPU needed)

Once `prepare` passes, the same handoff code can perform the complete isolated training chain:

```powershell
python -m tools.nanojev_gamer_v1_handoff experiment --output runtime\reports\gamer-v1\cutover-002 --device cuda --allow-experimental
```

`experiment` *also prepares the dataset automatically* if run against a fresh output directory, so you can choose to use that one command instead of preparing separately. It trains Qwen LoRA on the original *08 → math-table targets, freezes and fingerprints the adapter, trains the existing `C0 Wide` / `to_numerical` on **hidden states of the original *08 prompt only**, and probes the numerical results. There is no path for oracle/generated table text to enter C0 numerical inputs.

The `--allow-experimental` flag is important: it authorizes an isolated handoff if the LoRA has not passed promotion gates. It does **not** mark the trained model production ready. This optional run uses the existing Qwen/Transformers/PEFT/PyTorch CUDA dependencies, model downloads/cache, and the v5 staged checkpoint/resume rules. A smoke experiment may be smaller via `--lora-steps 20 --lora-eval-every 20 --head-steps 20 --head-eval-every 20`, without claiming useful learned competence. Any interrupted stages require `--resume` and their exact existing output directory.

## Source and factor-table policy

The new `tools/nanojev_gamer_v1_pairs.py` reads the verified executable model registry from `tools.nanojev_game_transformation_library` and the verified mathematical formulas from `tools.nanojev_game_native_objectives_v6`. It supports all 33 **specific executable source objectives**, not all 231 catalog entries and not every mathematical subobjective one could define within those game families. The 14 matrix objectives generated in this first smoke are best responses to a given rival action, including deliberate negative/zero/positive payoff margins; mixed Nash games and richer semantic variants remain a separate coverage question. The 19 scalar objectives are the source-defined formula values. Matrix payoffs vary in explicit hypothetical variants; a variant is **not asserted** to preserve the original game's strategic identity or equilibrium.

The factorization comprises three candidate table families: payoff matrices, a reusable sparse arithmetic step table with populated numerical cells (add/subtract/multiply/divide/maximum/action comparison), and optional probability/transition outcome tables. Each step contains its original operands or references earlier steps; all unfilled operation/result cells are marked **–**. Exact `Fraction`-based reconstruction is checked independently against the source's native oracle/operator; no answers are filled into the LoRA target. This is an initial reconstructing representation, **not proof that these are the minimum number of table types**.

Zero-line metadata is attached only to games whose source objective or action-margin defines a meaningful signed boundary. Other games omit the residual rather than fabricate a zero-line. Numerical diagnostics report measured versus excluded cases independently.

The input to **both** learning tasks is always `prompt_08`. The `math_prompt` is **LoRA completion supervision only**; `to_numerical` uses original-prompt activations and numerical target labels only. Source-certified records carry `source`, `factor_steps`, `source_registry_sha256`, `oracle_fingerprint`, and `table_fingerprint`; the loader recomputes the entire generated record and rejects tampering, source drift, or dataset lineage changes. Legacy self-attested fixtures remain valid for limited local contract tests but are refused by full `experiment` and nonpartial LoRA training.

## Actual acceptance status

The full source-generated dataset build/validation and test suite have been run locally. Real PEFT Qwen GPU training is **not verified** in this environment; the v5 bounded Wide training/probe tests use simulated Qwen feature capture. Do not interpret passing tests as model accuracy or release certification.
