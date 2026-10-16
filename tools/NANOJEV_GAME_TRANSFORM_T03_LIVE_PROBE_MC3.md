# NanoJev T03 — one-command live probe

## Invariant

The active T03-tiered training lineage remains unchanged: probing snapshots the most recently published `latest.pt`, uses the existing T03 oracle evaluation, and never edits training code, weights, optimizer, or database.

## Install

From `C:\Users\subsi\main_computer`:

```powershell
python new_patch.py .\nanojev_game_transform_t03_live_probe_mc3_v1.zip --dry-run
python new_patch.py .\nanojev_game_transform_t03_live_probe_mc3_v1.zip
```

## One command

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' -m tools.nanojev_game_transform_t03_live_probe
```

No manually defined `$snapshot`, `$report`, or run paths. The wrapper discovers the default T03-tiered run's `latest.pt`, snapshots that complete saved checkpoint, evaluates it with the existing T03 probe, and prints a concise table of joint, X, and Y 10%-tolerance accuracy relative to the step-220,000 T02 baseline. The table covers T03a–T03j on held-out dev/test cases and T03i on reserved transfer games.

Every run writes a uniquely named JSON report to `C:\Users\subsi\NanoJev\runs\game_transform_t03_tiered\live-probes\`. Temporary checkpoint copies are removed afterward unless `--keep-snapshot` is supplied. Reports record the evaluated step and checkpoint hash. The source checkpoint and training `latest.pt` are read-only. The trainer publishes `latest.pt` using an atomic replacement, so the copy observes one complete saved checkpoint even when training is running.

Optional arguments (not needed for the default run):

```powershell
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' -m tools.nanojev_game_transform_t03_live_probe --limit 24 --keep-snapshot
```

The wrapper shares a GPU with an active trainer, so running both simultaneously may reduce throughput or exceed memory. A probe cannot observe unsaved optimizer steps: the printed step is the latest *checkpointed* step, not the trainer's live in-memory step.

**Compatibility:** this patch adds a new module rather than modifying `nanojev_game_transform_t03_tiered.py`. The existing generator SHA sealed in the active training lineage is preserved, so the training process can resume without a generator identity mismatch. The original T03 probe remains available unchanged.
