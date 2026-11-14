# NanoJev joint Shotgun population

Shotgun is a **fourth population in the existing single-optimizer T/HDB/mathematics trainer**, not a separate model, checkpoint, or training phase. All four populations receive one supervised example per optimizer step at `--batch-size 4`. At batch sizes larger than four, extra examples follow the ordinary focus-weighted population schedule.

## Why this population exists

HDB07/HDB08 fixed-deck audits show signed-balance direction can be learned without reliable magnitude. Shotgun deliberately varies scale, encounter offsets, signs, payoff coefficients, representations, and cancellation while keeping the exact arithmetic answer authoritative. Eight views (`products`, `contributions`, `structured`, `prose`, `reordered_prose`, `table`, `intermediate`, `word_problem`) present corresponding Hawk/Dove balance equations. Matched index and seed identify the same underlying calculation in every view. Each case expects a single canonical `PROBLEM: <signed integer>` result. No approximate-answer training shortcut is introduced.

Unlike the previous HDB offset curriculum, Shotgun's difficulty distribution is **independent of `--joint-steps`**. It samples offsets including 0, ±1, ±2, ±8, ±64, and ±1024 and scales ranging from 1 to 10,000,000. The total number of encounters depends on the payoff coefficients and the selected scale.

## Resume existing run

Use the same `python -m tools.nanojev_qwen_lm_radius_ladder --stage run --frontier --frontier-hdb` command and parameters, including the existing `--focus`. The next *newly launched process* detects a pre-Shotgun `working.pt`, snapshots it to `pre_shotgun_frontier_migration.pt`, evaluates the independent frozen Shotgun deck, and adds a Shotgun baseline to the checkpoint state. Optimizer weights, LoRA parameters, current step, T/HDB/math evaluation decks, and their historical baselines remain intact. The live process that was already running before installation must be stopped at a saved checkpoint and restarted to import the new code. Never launch two writers for the same workspace.

Optional flags:

- `--joint-shotgun-eval-per-mode 6` (default). Pinned after initial Shotgun baseline, 6 cases × 8 modes = 48 heldout cases per full audit.
- `--focus "[HDB08=12,HDB07=12,HDB00=7,HDB04=6,fractions_units=8,algebra=4,logic_graphs=4,probability=4,shotgun:prose=12]"` emphasizes prose within Shotgun, without disabling any other mode or population.
- `--focus "[shotgun=6]"` emphasizes Shotgun in optional surplus slots at batch sizes above four. It does **not** replace the mandatory one Shotgun case per four-example batch.

The previously chosen focus carries through resume when `--focus` is omitted.

## Evaluation and acceptance

`shotgun_eval_deck.json` is frozen and fingerprinted separately from existing evaluation decks; examples in this deck never enter supervised training. `joint_frontier_report.json` now contains `shotgun.from_baseline`, `shotgun.interval`, by-mode/by-scale/by-side counts, normalized balance-error distributions, and `training_exposure.shotgun`. `QWEN_JOINT_FRONTIER_CHECK` and `QWEN_JOINT_WEAKNESSES` show Shotgun competency as a separate population. The heartbeat reports cumulative Shotgun training exposure.

Do not count reduced median normalized error as exact success. Examine exact match, median absolute offset-unit error, robust signed-log IQR variance, P90, sign accuracy, and old T/HDB/math retention. In particular, a drop in central error with worsening tail or sign accuracy is mixed progress, not convergence.

The expansion necessarily changes four-example minibatch composition: the former surplus slot becomes a guaranteed Shotgun example. With `--batch-size 4`, the previous disproportionate HDB-vs-T **population share** from the surplus slot no longer applies, but HDB tier and mathematics-family focus weights still govern choice *inside* their respective populations. To retain multiple HDB examples alongside Shotgun, use a larger batch size only in a separately planned run; do not change existing checkpoint batch-size policy without explicitly managing compatibility.
