# NanoJev Hawk–Dove: pure-Lisp X/Y curriculum experiment

**Status:** deterministic teacher built and tested; CUDA NanoJev checkpoint training must run on the user's machine. This package does not modify the existing V10 trainer or continue the abandoned continuation-syntax experiment.

## One game, four hidden fixed payoffs, two numerical inputs

The game has exactly four source-of-truth semantic bindings, with immutable teacher-side values:

- `HAWK_HAWK_PAYOFF`
- `HAWK_DOVE_PAYOFF`
- `DOVE_HAWK_PAYOFF`
- `DOVE_DOVE_PAYOFF`

The two injected literal values are the probability of rival Hawk and probability of rival Dove, respectively. They are not printed as `INPUT_A = ...`, `INPUT_B = ...`, `X: ...`, `Y: ...`, or any other numeric registration. The default `probabilities` input mode requires the pair to sum to one; optional `weights` mode permits two independently variable nonnegative inputs and uses `(WEIGHTS ...)` instead of `(BELIEFS ...)` in compressed prompts. Select one mode per immutable teacher DB. Both input values can be reused in multiple subexpressions without creating additional independent variables. The hidden payoff bindings are never numerically revealed to the model. Changing them is a *new teacher and a new training lineage*, not a presentation mutation.

The game-level teacher always computes

```
X = a*HAWK_HAWK_PAYOFF + b*HAWK_DOVE_PAYOFF
Y = a*DOVE_HAWK_PAYOFF + b*DOVE_DOVE_PAYOFF
Z = ARGMAX(X,Y)  # diagnostic only
```

`X` and `Y` are **expected-payoff regression targets** from T02 onward. `Z` carries no gradient. The T00–T01 preliminary calibration stages temporarily use the decoder's two scalar output slots to supervise isolated semantic bindings or weighted terms; those calibration slots are not the game-level X/Y quantities. The tier's `objective` field makes the distinction explicit. The game-level X/Y targets never change from T02 onward, even as their arithmetic disappears from the prompt.

## Pure Lisp ladder (all prompts emitted by code)

| Tier | Learnable relationship | Model-facing shape |
|---|---|---|
| T00 | Acquire fixed numerical payoff-token meanings, two columns | `(PAIR HAWK_HAWK_PAYOFF DOVE_HAWK_PAYOFF)` and `(PAIR HAWK_DOVE_PAYOFF DOVE_DOVE_PAYOFF)` |
| T01 | Isolated multiplication of a numeric literal by a fixed tag | `(PAIR (MUL 0.2 HAWK_HAWK_PAYOFF) (MUL 0.2 DOVE_HAWK_PAYOFF))` |
| T02 | Full X/Y expected-payoff arithmetic; one `ARGMAX` | `(ARGMAX (ADD (MUL 0.2 HAWK_HAWK_PAYOFF) (MUL 0.8 HAWK_DOVE_PAYOFF)) (ADD (MUL 0.2 DOVE_HAWK_PAYOFF) (MUL 0.8 DOVE_DOVE_PAYOFF)))` |
| T03 | Certified independent term-order / numeric-LogBar variations | Same pure Lisp grammar as T02, with two independently swappable `ADD` terms and raw / numeric-LogBar literals |
| T04 | Collapse only the entire Hawk X branch | `(HAWK_DOVE (BELIEFS (HAWK 0.2) (DOVE 0.8)) (ARGMAX (X) (ADD ...)))` |
| T05 | Collapse only the entire Dove Y branch | `(HAWK_DOVE (BELIEFS (HAWK 0.2) (DOVE 0.8)) (ARGMAX (ADD ...) (Y)))` |
| T06 | Collapse both branches | `(HAWK_DOVE (BELIEFS (HAWK 0.2) (DOVE 0.8)) (ARGMAX (X) (Y)))` |
| T07 | Collapse comparison into game decision semantics | `(HAWK_DOVE (BELIEFS (HAWK 0.2) (DOVE 0.8)) (CHOOSE))` |
| T08 | Maximum compression while retaining both numeric beliefs | `(HAWK_DOVE (OPPONENT (HAWK 0.2) (DOVE 0.8)) (BEST_RESPONSE))` |

T04 and T05 are independent collapse **siblings**; presenting them in tier order is a curriculum scheduling convention, not a mathematical requirement. T03 explores all sixteen combinations of two LogBar toggles and two valid within-row term swaps. Parenthesized math remains Lisp syntax all the way through T08; the previous `Add(... With(...))` continuation language is deliberately absent.

The teacher validates each generated example against a direct independent payoff oracle. It also tests rewriting with a different set of *unlearned* binding values to check that equivalence is algebraic, not an accident of the chosen fixture. Tests with alternative bindings do not redefine the bindings used in the training database.

### LogBar interpretation

A display token such as `0.2,|.` preserves the exact numeric prefix `0.2` while providing optional coarse magnitude assistance. This implementation treats the LogBar as a rendering hint only: the teacher parses and uses the numeric prefix. Its formatting must be checked against NanoJev's preferred tokenizer convention before broad production use. No phantom registers are inserted.

## Install and smoke (from `C:\Users\subsi\main_computer`)

Overlay the `main_computer_test/` files from the ZIP into the repository using the project's normal patch-transplant process. These are new files and do not replace the existing V10 tools.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py -m unittest discover -s tests -p test_nanojev_hawk_dove_lisp.py -v

# Rebuild the teacher in a temporary directory, independently audit every row,
# and test all partial collapses across alternative payoff bindings.
& $py -m tools.nanojev_hawk_dove_lisp_smoke `
  --bindings examples/hawk_dove_DEMO_ONLY.json `
  --report runtime/hawk-dove-pure-lisp/smoke.json

# Print actual model-facing prompts and teacher-only X/Y values.
& $py -m tools.nanojev_hawk_dove_lisp show `
  --bindings examples/hawk_dove_DEMO_ONLY.json --a 0.2 --b 0.8
```

The `examples/hawk_dove_DEMO_ONLY.json` payoffs are **illustrative, not the actual current NanoJev learned constants**. They match the earlier deterministic demo. Before training for actual game deployment, use a separately created binding manifest holding the correct chosen immutable payoff numbers; do not accidentally mix teacher definitions in one lineage.

## Build a source-generated training database

```powershell
& $py -m tools.nanojev_hawk_dove_lisp build `
  --bindings examples/hawk_dove_DEMO_ONLY.json `
  --out runtime/hawk-dove-pure-lisp/teacher.sqlite3 `
  --intervals 400

& $py -m tools.nanojev_hawk_dove_lisp_train preflight `
  --teacher runtime/hawk-dove-pure-lisp/teacher.sqlite3
```

The default probability-mode build produces **10,426** audited presentations from 401 input pairs, with each pair assigned to exactly one of `train`, `dev`, or `test`. The SQLite file is built on the target workstation from source code; **no generated database binary is included in the ZIP**. Existing teacher DBs are never overwritten; use a new destination for changed bindings or rebuilds.

For two independently sampled nonnegative belief **weights** rather than complementary probabilities, add `--input-mode weights` to the teacher builder and use a separate database/output lineage. In weights mode `0.2` and `0.3` are valid simultaneous inputs; the expected payoffs are still weighted sums, not normalized expected utilities. The chosen input mode is stamped into the DB manifest and the training checkpoint's teacher hash.

## Actual NanoJev checkpoint training and probes

```powershell
$source = 'runtime/game-transform-experiment/source-v4.pt'
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_operand_audited\latest.pt"
$teacher = 'runtime/hawk-dove-pure-lisp/teacher.sqlite3'
$out = "$HOME\NanoJev\runs\hawk_dove_pure_lisp_v1"

# No optimizer updates. Establish a real pre-training baseline first.
& $py -m tools.nanojev_hawk_dove_lisp_train baseline `
  --teacher $teacher --source $source --parent $parent --output $out `
  --device cuda --probe-per-tier 24

# Fresh, isolated optimizer; loads parent weights, does not touch parent.
& $py -m tools.nanojev_hawk_dove_lisp_train train `
  --teacher $teacher --source $source --parent $parent --output $out `
  --device cuda --steps 50 --chunk-steps 25 --probe-per-tier 24

# Continue same local experiment to a larger total local step count.
& $py -m tools.nanojev_hawk_dove_lisp_train train `
  --teacher $teacher --source $source --parent $parent --output $out `
  --device cuda --steps 250 --chunk-steps 25 --probe-per-tier 24

# Read-only probes from the trained experiment; dev and held-out test separate.
& $py -m tools.nanojev_hawk_dove_lisp_train probe `
  --teacher $teacher --source $source --parent $parent --output $out --device cuda
& $py -m tools.nanojev_hawk_dove_lisp_train test `
  --teacher $teacher --source $source --parent $parent --output $out --device cuda
```

`baseline` loads Qwen and the decoder but does **not** create a local trained checkpoint. `train` writes to the isolated `$out` directory only; `latest.pt` includes decoder weights, unchanged gamehead state, optimizer state, RNG, current focus, and provenance including parent/source/teacher SHA256. `probe` and `test` require the local experiment checkpoint. `test` never participates in learning, focus selection or promotion.

The scheduler initially concentrates on T00's direct payoff-token learning. It advances only after two consecutive probes confirm the current stage and all preceding stages meet the **50% joint X/Y within 5% tolerance** gate. Earlier regressions move the focus back. Failures are visible in per-tier X/Y metrics and sample prompts. An independent `ARGMAX`/Z score is diagnostic only.

The model uses the preexisting Qwen3-0.6B frozen backbone and attention-regression decoder via `nanojev_game_math_curriculum_v10_runtime.load_model` and `nanojev_game_transform_experiment.forward`. The package does not implement or substitute a toy neural model. Qwen weights and actual trainer checkpoints are **not available in the authoring environment**, so CUDA training here has not been run. The source-generated teacher and arithmetic proof tests have been executed successfully.

## Required future checks

Before merging this experiment into an active training lineage, inspect tokenizer eligibility, payoffs and numeric precision conventions, the T00/T01-to-T02 transition, held-out joint X/Y errors, and whether collapsed X/Y prompts recover the same numerical results without target leakage. Any change to fixed payoff values, interpretation of A/B, or semantic program operators requires an explicit new teacher version and independent training lineage.
