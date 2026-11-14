# Continue an existing `mix_v4` training lineage with Pass-One captain numeracy

This patch **modifies `tools/nanojev_altreg_attention_curriculum_mix_v4.py` in place**. No new trainer, new run directory, new attention head, or optimizer reset. It adds a sealed SQLite Pass-One task-bank overlay, while leaving the legacy five-class exact-count curriculum and **its saved config identity unchanged**. The numerical X/Y decoder and auxiliary operation classifier continue training. RESULT/FINAL remain out of the loss.

## One-time prerequisites

1. Install the patch into your repository root so `tools/nanojev_altreg_attention_curriculum_mix_v4.py` is replaced. The patch also includes the previous Pass-One populator for convenience.
2. If the bank is not yet populated, run:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m tools.nanojev_captain_numeracy_populate `
  --output-dir .\runtime\captain-numeracy-pass1 `
  --train-groups 4096 --dev-groups 512 --predev-groups 512 --test-groups 512 `
  --seed 20261015
& $py -m tools.nanojev_captain_numeracy_populate `
  --verify-bank .\runtime\captain-numeracy-pass1
```

The database already exists? Do not rerun the populator against an existing destination. Verify the existing bank instead.

3. Allow your **current running process** to finish writing a healthy `latest.pt` checkpoint, then stop it. Replacing Python source files on disk does not change code already loaded inside a running process. Resume that SAME output root, with your existing identity-sensitive arguments unchanged, and add the following new arguments. This is one restart, **not a fork or cutover**.

## Add the schedule to your existing resume command

The following example assumes an active `--batch-size 16 --mix-counts 4,3,3,3,3` run and that the checkpoint's absolute `step` is less than 24001. If your original run used `--phase-counts`, `--phase-steps`, a custom seed/learning rate, or a different run path, preserve those existing options exactly. The important part is the three `--captain-*` lines:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_altreg_attention_curriculum_mix_v4.py `
  --resume --steps 100000 `
  --batch-size 16 --mix-counts 4,3,3,3,3 `
  --device cuda --dtype float16 `
  --captain-bank .\runtime\captain-numeracy-pass1 `
  --captain-milestone '24001:story_full_logbar=2,story_one_logbar_x=1' `
  --captain-milestone '28001:story_full_logbar=1,story_numeric_registers=1,story_numeric_x_semantic_y=1' `
  --captain-milestone '32001:story_semantic_with_op=2,story_numeric_no_op=1' `
  --captain-milestone '36001:story_semantic_no_op=2,story_one_semantic_x=1' `
  --captain-milestone '40001:story_one_semantic_y=1,story_op_only=1,story_raw=1'
```

**Absolute steps:** The first milestone must be strictly GREATER than the `step` in the existing `latest.pt`. These are absolute steps in the current `mix_v4` run, not steps since installing the patch. `--steps` is likewise the absolute final target. If `latest.pt` already shows 24001 or later, choose future milestone steps before beginning. The example is intentionally illustrative, not presumed to be your live step.

Each milestone **replaces the previous set** of captain quotas; it does not add to it. Omitted variants have zero count. Total captain quota per minibatch must not exceed `--batch-size`. Remaining slots preserve the legacy exact-count task schedule. Replacements consume legacy `plain_expression` slots first, followed by the next-hardest old tiers. Every original numeric stream draw still occurs; the legacy stream and minibatch shuffler remain checkpointed exactly as before.

`--captain-milestone` may name any of these 15 variants: `registered_logbar`, `plain_expression`, `story_full_logbar`, `story_one_logbar_x`, `story_one_logbar_y`, `story_numeric_registers`, `story_numeric_no_op`, `story_numeric_x_semantic_y`, `story_semantic_x_numeric_y`, `story_semantic_with_op`, `story_semantic_no_op`, `story_one_semantic_x`, `story_one_semantic_y`, `story_op_only`, `story_raw`. All selected representations come from **the database**. For captain variants that lack an explicit operation line, the decoder receives the neutral operation ID, as it already does for the unscaffolded plain-expression tier. The auxiliary operation classifier never receives the gold operation ID.

### Inspect the schedule without loading Qwen

```powershell
& $py .\tools\nanojev_altreg_attention_curriculum_mix_v4.py --preview `
  --mix-counts 4,3,3,3,3 `
  --captain-milestone '24001:story_full_logbar=2,story_one_logbar_x=1' `
  --captain-milestone '28001:story_semantic_with_op=2,story_raw=1'
```

### Resume again later

After installing the bank schedule once, the trainer records `captain-numeracy-schedule.json` in the SAME output root. On subsequent `--resume` runs, you need not repeat `--captain-bank` or `--captain-milestone`; the trainer automatically reloads and verifies that pinned plan, along with the database SHA-256. Changing the plan in place or using a different database identity is deliberately rejected. Plan all desired future transitions before first installation.

## Verification and reporting

- Both the old validation examples and independent **Pass-One dev** examples are evaluated. New variants have `captain:<variant>` names in `validation-step-*.json` and `curriculum_validation` events.
- `weighted_joint_5pct` uses **the effective minibatch counts** after replacement. `effective_mix_counts` is written into validation JSON. It cannot be directly compared across different task-mix milestones.
- The bank is checked against its manifest hash on startup, and Pass-One `train` rows are filtered against reserved numerical pairs from the existing experiment. Pass-One evaluation samples are read from `dev`, never `test`.
- `latest.pt` remains the active optimizer/model/RNG/stream checkpoint. Historical source checkpoints are not modified. At first installation, the old `best-curriculum.pt` is copied to `best-pre-captain.pt`; future captain champions use `best-phase-<phase>-captain-<startstep>.pt` to avoid overwriting the old phase champion.
- Validation is not a competence gate. The milestones are exact user-defined steps; the script does not invent a new scheduler.

## Tests

```powershell
& $py -m unittest discover -s .\tests -p 'test_nanojev_altreg_attention_curriculum_mix_v4.py' -v
& $py -m unittest discover -s .\tests -p 'test_nanojev_altreg_attention_captain_overlay.py' -v
& $py -m unittest discover -s .\tests -p 'test_nanojev_captain_numeracy_populate.py' -v
```

The included tests run without downloading Qwen or using the GPU. They verify resume from a pre-overlay checkpoint through an overlay milestone and a second resume, along with real database round trips and reserved split boundaries. This is not a claim that live CUDA training has been run in the model's environment.
