# QW5 frozen-source drift / checkpoint recovery

## Why `QW5_SOURCE_SHA_DRIFT:hard` occurs

The original `qw5_phase1_v1/latest.pt` stores QW5's **trained adapters and readouts**, **not** the 5 frozen EMP source backbones. Its `config.json` records the SHA-256 checksums of the stage, hard specialist, and two source banks. The original implementation reloaded all four from the living EMP campaign, where `hard_emp_frontier_v2/latest.pt` is repeatedly replaced as the Hard EMP trainer progresses.

Thus a successfully trained 500-step QW5 and an accepted Z0/Z1/Z2 ladder can legitimately refuse after the Hard EMP sidecar advances. **Do not turn off SHA verification, edit `config.json`, or pair the new specialist weights with adapters trained against the old one.** That would make the old accuracy certificates invalid.

## Diagnose (read-only)

```powershell
& $py -m tools.nanojev_qw5_source_archive inspect `
  --qw5-run "C:\Users\subsi\NanoJev\runs\qw5_phase1_v1"
```

This prints the four originally recorded SHA-256 values, whether the live EMP file still matches, and whether an immutable `source_archive` copy is available. For `hard`, an archive may be absent because the older QW5 code did not store one.

## Recover the ORIGINAL specialist, not an updated specialist

If you have a historical copy of **the exact `hard_emp_frontier_v2/latest.pt` that existed when QW5 Phase 1 was prepared**, supply its path:

```powershell
& $py -m tools.nanojev_qw5_source_archive recover `
  --qw5-run "C:\Users\subsi\NanoJev\runs\qw5_phase1_v1" `
  --hard-checkpoint "C:\path\to\historical\hard-EMP-checkpoint.pt"
```

Alternatively, search a *limited* existing backup directory that holds `.pt` checkpoints (be aware hashing large files takes time):

```powershell
& $py -m tools.nanojev_qw5_source_archive recover `
  --qw5-run "C:\Users\subsi\NanoJev\runs\qw5_phase1_v1" `
  --search-root "C:\path\to\older\NanoJev\checkpoints"
```

`recover` requires the exact original hash for all four EMP source files and checks the Hard EMP / ordinary-stage pairing. It creates `qw5_phase1_v1/source_archive/{stage,hard,left,right}.pt`, verifying every copy. **It does not change the live EMP campaign, config, QW5 checkpoint, or accepted ladder.** On future restores, the pinned archive takes priority over the mutable live `latest.pt` files.

Do NOT run a broad scan of all NanoJev runs by default if the directory contains hundreds of gigabytes. Select a backup directory known to contain historical checkpoints. A valid backup with a different filename is acceptable; the content hash and checkpoint structure must match.

## Resume from the already accepted Z2 stage

Once `recover` prints `QW5_SOURCE_RECOVERED`, run the existing transition command:

```powershell
& $py -m tools.nanojev_qw5_balance_ladder run `
  --qw5-run "C:\Users\subsi\NanoJev\runs\qw5_phase1_v1" `
  --seed-accepted-from "C:\Users\subsi\NanoJev\runs\qw5_zero_balance_ladder_v2" `
  --output "C:\Users\subsi\NanoJev\runs\qw5_zero_balance_ladder_v3" `
  --through R0 `
  --device cuda `
  --steps-per-rung 1000 `
  --probe-every 100
```

If `v3` was not created due to the early refusal, the normal seeding logic should then proceed.

## If the old specialist does not exist

The accepted `v2/accepted.pt` contains trainable QW5 parameters, not the source bank tensors. It cannot reconstruct an earlier Hard EMP specialist. The previous `qw5_phase1_v1` and `qw5_zero_balance_ladder_v2` must remain historical artifacts; they cannot be safely paired with a new source. Start a **new independent** QW5 Stage-1 run with the current, internally consistent EMP stage/specialist, under a new output directory, then run the new ladder from Z0. It would be misleading to treat the old accepted Z2 checkpoint as verified with the new source.

## Prevention for all new Phase-1 QW5 runs

New `tools.nanojev_qw5_join prepare/run` automatically SHA-verifies and pins its source EMP checkpoint files inside its own `source_archive` directory before publishing the trained checkpoint. `restore` and `status` prefer that immutable copy and explicitly reject corrupt archives. Concurrent changes to live EMP `latest.pt` files therefore cannot quietly rebase a prepared QW5 model. The original source provenance in `config.json` stays unchanged.

### Tests

```powershell
& $py -m pytest -q tests/test_nanojev_qw5_source_archive.py tests/test_nanojev_qw5.py tests/test_nanojev_qw5_balance_ladder.py
```

All tests use dummy checkpoint files/models or reproducible CPU fixtures. They do not require the user's historical checkpoint. Actual recovery of the real user's old Hard EMP weights is unverified until the exact historical checkpoint is supplied or found locally.
