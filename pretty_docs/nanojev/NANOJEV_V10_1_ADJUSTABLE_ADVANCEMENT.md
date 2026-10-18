# NanoJev V10.1 — Adjustable Advancement Percentage

## Objective

Make the training advancement requirement adjustable on restart while preserving the
existing 141-stage teacher, numerical 5% tolerance, two-consecutive-passing-probe
promotion, and automatic return to the first failing stage. The higher 80%, 90%,
and 95% gates remain **diagnostics** and do not hold back advancement under a
lower gate. No deterministic brain, teacher database, mathematical targets, model
architecture, loss function, or optimizer hyperparameters are changed.

Both CLI spellings are accepted:

- Canonical: `--advancement-percent 70`
- Requested spelling: `--advancement-precent 70`

`--gate-fraction 0.70` remains compatible with existing scripts. The default
remains 80% when neither is provided. Conflicting percentage and fraction flags
are rejected rather than silently choosing one. The percent must be in `(0,100]`.

The gate requires **X, Y, AND joint X/Y** each at least the requested percentage,
within the existing 5% error range, with the same minimum scorable-case rule.
The coupled Z diagnostic is excluded from advancement and training loss.

## Applying the patch and resuming ongoing training

**Stop the existing V10.1 training process before applying and launching this
patch.** Wait for a `v10_xy_probe` event (this is printed after the checkpoint is
saved) and then use Ctrl+C, so at worst only an incomplete chunk is lost. Do
not allow two processes to write the same `latest.pt` and `v10-config.json`.
A backup copy of `latest.pt` is recommended.

The patch zip is rooted at `main_computer_test/`; merge its contents with your
existing `C:\Users\subsi\main_computer` repository, stripping the leading root
folder. Preserve the existing V10 bridge database and the original V9 parent.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$run = "$HOME\NanoJev\runs\game_math_xy_v10_bridge"
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_parent.pt"

# After the old trainer stops and completes a checkpoint:
Copy-Item "$run\latest.pt" "$run\before-gate-70.pt" -ErrorAction Stop

& $py -m tools.nanojev_game_math_curriculum_v10_train `
  --teacher-db runtime/game-math-curriculum-v10-bridge/teacher.sqlite3 `
  --source-checkpoint runtime/game-transform-experiment/source-v4.pt `
  --parent-checkpoint $parent `
  --output $run `
  --steps 2500000 `
  --chunk-steps 250 `
  --probe-limit 48 `
  --advancement-precent 70 `
  --device cuda
```

`--steps 2500000` is the **total** V10 steps since parent import; it is not
2.5 million more steps on each restart. The trainer loads existing decoder,
optimizer, gamehead, CPU/CUDA RNG, and numerical step state; it does **not**
reset to V9. Changing to 75% or 65% later uses the same command and output
directory with a different percentage and a target `--steps` exceeding the
currently completed V10 step count.

If the threshold changes on resume, the trainer:

1. Validates immutable checkpoint provenance and training hyperparameters.
2. Accepts the **known pre-patch trainer code hash** or the current patched hash;
   arbitrary unknown trainer changes and unrelated config changes remain errors.
3. Re-evaluates the checkpoint across **all 141 stages** using the new gate.
4. Resets the two-probe pass streak to zero (a pass at 80% is not carried over
   as a pass at 70%, or vice versa).
5. Sets focus to the first failing stage at the newly requested threshold and
   resumes the existing optimizer and RNG states.
6. Saves a report named
   `gate-change-step-XXXXXXXXX-to-70-percent.json` and logs
   `v10_advancement_threshold_changed`.
7. Saves the effective gate inside the next `latest.pt` and `v10-config.json`.

Teacher hashes, parent hashes, LR, batch count, probe selection, seed,
source checkpoint, and optimizer shape remain protected by config checks.

## Read-only probe with the corresponding gate

```powershell
& $py -m tools.nanojev_game_math_curriculum_v10_probe `
  --teacher-db runtime/game-math-curriculum-v10-bridge/teacher.sqlite3 `
  --source-checkpoint runtime/game-transform-experiment/source-v4.pt `
  --checkpoint "$HOME\NanoJev\runs\game_math_xy_v10_bridge\latest.pt" `
  --limit 48 `
  --output "$HOME\NanoJev\runs\game_math_xy_v10_bridge\manual-gate-70.json" `
  --device cuda `
  --advancement-precent 70
```

The probe console shows the requested advancement gate and PASS/FAIL under that
gate. Its JSON additionally records per-stage PASS/FAIL and total counts at
70%, 80%, 90%, and 95%, independently of the scheduler. All actual X, Y,
joint X/Y, and coupled Z scores remain in the report. To probe a subset, add
`--tiers T00 T01 T02 T07 T08` **on the final PowerShell line**, without a
trailing backtick (to avoid embedding a newline in `T08`).

## Verification and limitations

Run:

```powershell
& $py -m unittest tests.test_nanojev_game_math_curriculum_v10_runtime tests.test_nanojev_game_math_curriculum_v10 -q
```

The CPU tests exercise CLI aliases and validation, independent diagnostics,
compatibility constraints, known-legacy-code migration, resumable mock
optimization at 80% → 70% → 65%, preserved parent, optimizer state, and
checkpoint step, as well as existing teacher and probe contracts.

The production GPU checkpoint and running CUDA process are not available in the
patch environment, so workstation restart and accuracy outcomes require the
actual run shown above. The patch does not alter the deterministic teacher or
rebuild the database.
