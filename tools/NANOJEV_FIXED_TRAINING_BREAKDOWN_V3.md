# NanoJev fixed training breakdown (v3)

This **supersedes** the previous `nanojev_captain_semantic_binding_fix_v2_20261015.zip` and includes its paired-orientation correction. Do not layer the patches. Apply only this v3 archive to the unchanged original source OR to the exact v2 source hash listed below.

## Contract

`--training-mix` is the exact distribution of **all 16 primary training positions**, not a milestone schedule. Every subsequent training step uses those quotas, in deterministic shuffled order. All declared Captain positions additionally supervise the exact same group's opposite orientation. Those partner prompts are additional forward/backward work, but **not additional groups** in the declared core mix. There are no hidden focus or replay groups under the fixed breakdown. This distinction must be considered when interpreting relative gradient allocation.

Unprefixed names are the 5 original classes; `captain:...` names refer to bank-backed story classes. Missing counts are zero. The sum must be **exactly `--batch-size`**; duplicates, unknown names, negatives and mismatched sums fail closed. The `--steps` parameter is only a duration/stop boundary, not a scheduler.

The existing `captain-numeracy-schedule.json` remains intact for historical provenance, but neither its step milestones nor `--phase-steps` affect the training mix after the fixed breakdown is activated. The original checkpoint config and optimizer/stream states remain unchanged. `captain-training-breakdown.json` records the exact new composition and persists across restarts without repeating the CLI flag. You can change the mix deliberately on a later resume by supplying a NEW `--training-mix` from a saved checkpoint; the sidecar records its predecessor and copies `latest.pt` and `best-curriculum.pt` before changing it. A distribution change resets checkpoint champion comparison by mix identity; it does **not** reset network weights or the optimizer.

The corrective paired-orientation training is activated with `--captain-paired-supervision` the first time if not already installed. The existing v2 paired sidecar remains pinned when the breakdown takes over, but its extra semantic-focus and semantic-rehearsal groups are suppressed so the fixed mix is authoritative. Checkpoint selection uses per-class joint X/Y correctness in **both** orientations for Captain classes, weighted by the chosen quotas. Existing numerical and operation metrics are still reported.

## Installation: Windows PowerShell

First stop the live trainer after a validation checkpoint. Do not install while the trainer is running. In `C:\Users\subsi\main_computer`:

```powershell
$allowed = @(
  '23ba8ddfac6a00b5b9ec8a6ccb6b94ea68bb5906a13b5ed5ed70363cd8b5bf8e', # Original 14:24 snapshot
  '78d91fc4d348de500af6085d76e7093fbc493f6e25df9375973200b937a0f648'  # V2 if already installed
)
$sourceHash = (Get-FileHash .\tools\nanojev_altreg_attention_curriculum_mix_v4.py -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -notin $allowed) { throw "Local trainer differs from both known sources. Stop and merge; do not overwrite: $sourceHash" }

$patch = 'C:\path\to\nanojev_captain_fixed_training_breakdown_v3_20261015.zip'
$unpacked = Join-Path $env:TEMP 'nanojev-fixed-breakdown-v3'
Expand-Archive -LiteralPath $patch -DestinationPath $unpacked -Force
Copy-Item -LiteralPath (Join-Path $unpacked 'main_computer_test\tools\nanojev_altreg_attention_curriculum_mix_v4.py') -Destination .\tools\ -Force
Copy-Item -LiteralPath (Join-Path $unpacked 'main_computer_test\tests\test_nanojev_altreg_attention_curriculum_mix_v4.py') -Destination .\tests\ -Force
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest discover -s .\tests -p 'test_nanojev_altreg_attention_curriculum_mix_v4.py' -v
```

## Example: one explicitly defined whole-batch breakdown

This is a **proposed** mix for the current semantic-binding bottleneck; change it freely. The example sum is 16. This command has **no milestone flags**:

```powershell
$mix = @(
  'registered_logbar=1'
  'one_raw_operand=1'
  'one_logbar_removed=1'
  'one_raw_no_logbar=1'
  'plain_expression=1'
  'captain:plain_expression=1'
  'captain:story_full_logbar=1'
  'captain:story_one_logbar_x=1'
  'captain:story_numeric_registers=1'
  'captain:story_numeric_x_semantic_y=1'
  'captain:story_semantic_with_op=2'
  'captain:story_numeric_no_op=1'
  'captain:story_semantic_no_op=2'
  'captain:story_one_semantic_x=1'
) -join ','

& $py .\tools\nanojev_altreg_attention_curriculum_mix_v4.py `
  --preview --batch-size 16 --training-mix $mix

& $py .\tools\nanojev_altreg_attention_curriculum_mix_v4.py `
  --resume `
  --steps 100000 `
  --batch-size 16 `
  --device cuda `
  --dtype float16 `
  --training-mix $mix `
  --captain-paired-supervision
```

`--training-mix` and `--captain-milestone` on one invocation are intentionally rejected. Keep the original `--mix-counts` / `--phase-counts` arguments unchanged when resuming historical checkpoints, if you previously specified them; they remain part of v4's immutable checkpoint configuration but their batch schedule is ignored after the fixed cutover. The original live training command used default phase settings, so none are needed in the example above.

## Reuse or change the composition

For a later restart, omit `--training-mix` and the pinned breakdown will be reused. To change allocations, stop after a validation checkpoint, set `$mix` to a new 16-position allocation, and use the same resume command with `--training-mix $mix`. Sidecar history and prechange backups are created automatically.

Review `captain-training-breakdown.json` for the distribution, `curriculum_fixed_breakdown` in startup logs for `old_step_milestones_ignored: true`, `curriculum_progress.mix_counts` for actual per-batch quotas, `validation-step-*.json` for the mix hash and per-class orientation metrics. There are **zero extra semantic/replay groups** outside the 16 primary examples. Captain paired partners are logged separately as `captain_paired_slots`.

## Verification

20 CPU-only offline unit tests passed, including parser failures, deterministic whole-batch mix assembly, matching paired labels, mid-run cutover from a historical milestone schedule, identical quotas across restarts, explicit reallocation, and persisted checkpoint metadata. This does not prove CUDA throughput, loss reductions, or later story accuracy. There is no automatic competence threshold, and no silent move to raw stories.
