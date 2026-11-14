# Qwen unified mathematical frontier: mutable focus scheduling

The `--focus` switch is a **training-schedule override** for the joint
`--stage run --frontier --frontier-hdb` campaign. It does not replace the
arithmetic T ladder, HDB ladder, or any of the nine mathematical families;
**every optimizer minibatch still includes T, HDB, and mathematics**. It
changes how frequently an area receives examples, **not** its loss multiplier,
model parameters, frozen checkpoints, or the immutable evaluation deck.

## Quick commands

Append to the **same full training command** previously used (same checkpoint
paths, output, geometry, parent parameters and fixed evaluation settings):

```powershell
--focus '[T07=12,HDB08=8,algebra:L1=10]'
```

These are *relative emphasis weights*, **not absolute proportions**. They
increase both the probability of selecting a targeted tier for its guaranteed
population slot and the probability that the extra minibatch slots train that
population. For example, `T07=12` does not mean 12 optimizer steps. The
sampling rules are seeded and deterministic given saved state and step.

Accepted targets:

- `T` (all T tiers) and individually `T00` through `T08`.
- `HDB` (all HDB tiers) and individually `HDB00` through `HDB08`.
- `math` (all nine mathematical families, all active levels; `mathematics` is an alias).
- Any mathematical family: `signed_arithmetic`, `place_value`,
  `arithmetic_operators`, `fractions_units`, `algebra`, `logic_graphs`,
  `coordinates_geometry`, `probability`, `symbolic_scale`.
- Specific level `algebra:L1`, `probability:L0`, etc. Levels must already be
  in the **pinned** `--joint-math-levels` configuration. `math:algebra:L1`
  is also accepted.

Separate selections with commas; brackets are optional. A target without a
weight defaults to **4×**. Weights must be finite, between 1 and 20 inclusive;
1× is neutral and drops out. When broad and narrow selections overlap, the
**maximum** weight applies, not a product. Unknown tiers and nonexistent
mathematical levels fail *before the model is loaded*.

`--focus none` **clears** the saved focus. Omitting `--focus` on a resumed run
**retains** the last saved focus. You can update the focus between runs without
resetting learned weights, optimizer, training exposure, or competence
measurements; each focus change creates a complete pre-change snapshot in
`focus_snapshots/`. A completed run requires **increasing `--joint-steps`**
to do any further optimizer work.

Preview without touching a model:

```powershell
python -m tools.nanojev_qwen_lm_radius_ladder `
  --stage preview --frontier --frontier-hdb `
  --radii 0,1,2,3,4 --frontier-max-radius 64 --operand-limit 1024 `
  --focus '[T07=12,HDB08=8,algebra:L1=10]'
```

## Diagnosing impact and deciding when exercises need to change

Continue using the existing **read-only** diagnostic command:

```powershell
python -m tools.nanojev_qwen_lm_radius_ladder `
  --stage competency --frontier --frontier-hdb `
  --output "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1" `
  --radii 0,1,2,3,4 --frontier-max-radius 64
```

Do not supply `--focus` to `--stage competency` or `--stage status`:
those commands do **not** change the optimizer schedule. The latest joint
`joint_frontier_report.json` and `progress.json` record focus weights, the
focused tiers' effective weights, optimizer steps since focus activation,
**direct examples since focus**, initial vs latest heldout-corpus counts,
and difference in correct answers on the same fixed prompts. Focus changes
and their precise optimizer steps are recorded in `focus_history`.

This evidence can distinguish **insufficient focused practice** (few direct
examples) from **a provisional stall despite focused practice** (many
examples and no measured gain), *but it does not itself prove that a training
exercise is structurally incapable of teaching the skill*. That requires the
subsequent exercise-structure diagnosis, not another increase in emphasis.

A report made before a focus change is explicitly distinguishable from the
active schedule via `focus_report_is_current`, `focus_schedule`, and
`active_focus_schedule`. No new model evaluation occurs in read-only status
or competency mode.

## Checkpoint and coverage invariants

- One continuous optimizer, no branching glass or isolated specialist.
- One example of each of the three training populations at **every** step,
  including while focus is active.
- Unselected skills retain positive sampling probability and the existing
  score-based acquisition priority. A focus cannot directly train on the
  separate frozen evaluation corpus.
- The focus change is excluded from immutable model/parent/eval policy drift
  checking; pinned changes are still rejected on resume.
- Sample-frequency emphasis is intentionally not misrepresented as
  gradient-weighted training or proof of competency.

CPU fixture tests exercise the CLI, parser, focus coverage and measured extra
exposures, checkpoint continuity, focus switching, reporting, and clearing.
They do not claim successful CUDA learning with the actual Qwen model.
