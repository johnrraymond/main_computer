# NanoJev MC3 — T02 interference cutover and bounded overnight training (v2)

This is a **source-only replacement patch** for the existing MC3 ladder module, weight map, and targeted test file. It requires the previously applied MC3 integrated experiment and T00–T08 level probe. It does not modify the source V4 checkpoint, the prior 20-step experiment, the verified SQLite database, or the running V4 trainer.

## Invariant

The numerical targets and canonical SQL splits never change when the presentation changes. Exactly one controlled source-context factor is introduced at a time; train/dev/test/whole-game-transfer never mix. Training is an isolated optimizer lineage whose frozen Qwen representations may be cached and reused, but **the attention decoder and game-operator head still receive genuine gradient updates**.

## What is fixed

The original T02c was labeled "one source payoff" but it actually prefixed a payoff *pair* and two coordinate indices. The fix splits these concerns and separates prepend/append effects. Each prompt still uses the exact native V4 register/LogBar representation and `Answer:` suffix:

| Level | Controlled presentation |
| --- | --- |
| T00 | Native registered numerals; X/Y explicitly present |
| T01 | Native values plus appended transformation label |
| T02a | **Prefixed** game name |
| T02k | Same game name **appended** before `Answer:` |
| T02b | The **same text** as T01's label, **prefixed**, isolating position |
| T02c | Exactly **one unrelated numeric datum**, prefixed; no coordinate indices |
| T02j | The **same unrelated datum**, appended |
| T02h | Exactly one original source-game payoff, prefixed |
| T02i | The two original payoffs in a source cell, prefixed; no coordinate indices |
| T02d | Transformation definition |
| T02e | Full source structure |
| T02f | Full original formal prefix (reproduces prior failing T02) |
| T02g | Same full context in the middle |
| T02n | Neutral text length control |
| T03 onward | Hidden-operand and true transformation tasks (unchanged) |

The **16-position fixed T02 focus map** assigns 12 slots to T02 variants, 2 to T00/T01, and 2 to legacy arithmetic retention. No automatic phase advancement is performed. Edit `tools/nanojev_game_transform_ladder_map.json` **before beginning a new training lineage** to choose a different mix. The map is hashed and sealed in each lineage.

## 1. Re-run the required read-only diagnostic

The revised diagnostic embeds the generator source SHA. The older `t02-isolation.json` does **not** qualify the new generator and is intentionally rejected as a training gate.

From `C:\Users\subsi\main_computer`, with the NanoJev virtual environment:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py -m tools.nanojev_game_transform_ladder_map diagnose `
  --device cuda --dtype float16 `
  --limit-per-split 12 `
  --report .\runtime\game-transform-experiment\t02-isolation-v2.json
```

The read-only diagnostic compares the source checkpoint and the prior experimental checkpoint using identical held-out cases. It does not write weights or SQL. Inspect T02c (single unrelated numeral), T02h (one source payoff), T02i (payoff pair), and T02j (append-position control). The original observed numeric failures remain historical evidence; they are not presented as results on the *revised* prompts.

## 2. Two-step real training smoke (new isolated lineage)

```powershell
& $py -m tools.nanojev_game_transform_ladder_map train `
  --device cuda --dtype float16 `
  --diagnostic .\runtime\game-transform-experiment\t02-isolation-v2.json `
  --phase t02_focus --steps 2 --save-every 1 `
  --cache-per-level 64 --retention-cache 64 `
  --refresh-every 5000 --report-every 1
```

Default output: `$HOME\NanoJev\runs\game_transform_ladder_map_v2\`. The experiment writes `latest.pt`, `ladder-config.json`, and a generated `frozen-capture-cache-t02_focus.pt`. The cache may be large and can consume substantial system RAM/disk. No cache or checkpoint is included in the ZIP.

Unlike v1, the training loop captures the **frozen backbone once per cached training presentation**, stores the detached token sequence (FP16 CPU), then performs a **single batched optimizer update** per step over the explicitly selected 16 slots. Non-retention cases use both orientations; retention examples retain native X/Y supervision. X/Y and game-operator losses are separate; retention has no game-operator label. A deterministic rotating bank is rebuilt every `--refresh-every` steps to cover additional training-only cases over a long run, with a window-specific cache identity and a replace-safe checkpoint. No reserved game-transfer examples enter the cache.

## 3. Overnight continuation (total-target step semantics)

```powershell
& $py -m tools.nanojev_game_transform_ladder_map train `
  --device cuda --dtype float16 `
  --diagnostic .\runtime\game-transform-experiment\t02-isolation-v2.json `
  --phase t02_focus `
  --steps 220000 `
  --save-every 500 `
  --report-every 100 `
  --cache-per-level 64 `
  --retention-cache 64 `
  --refresh-every 5000 `
  --max-wall-hours 10
```

**Important:** `--steps 220000` is a total optimizer-step target, **not** a promise that all 220,000 steps will complete overnight. `--max-wall-hours 10` stops after the time budget and saves a checkpoint with `stop_reason=wall_limit_reached`. The run can be resumed by reissuing the same command (or increasing the `--steps` target). Cached frozen representations make updates substantially cheaper than the prior 32-Qwen-captures-per-step implementation, but runtime depends on local hardware, cache-building throughput, CPU RAM, and storage. Monitor the emitted `ladder_map_progress`, `ladder_cache_level_ready`, and `ladder_map_checkpoint` events.

**Resume invariant:** keep the same output directory, source checkpoint, SQL corpus, map file, diagnostic, learning rate, seed, cache quotas, and refresh interval. Altering those inputs must create a **new** lineage; the trainer refuses to silently reinterpret a saved optimizer state. `--phase` can be changed explicitly to an existing map phase; each phase gets its own deterministic cache. Never modify the active V4 checkpoint to perform this experiment.

## 4. Evaluate the actual overnight result

```powershell
& $py -m tools.nanojev_game_transform_ladder_map diagnose `
  --device cuda --dtype float16 `
  --limit-per-split 48 `
  --experimental-checkpoint "$HOME\NanoJev\runs\game_transform_ladder_map_v2\latest.pt" `
  --report .\runtime\game-transform-experiment\t02-after-overnight.json
```

Measure T00/T01 retention, X-only/Y-only accuracy, paired X/Y at both 5% and 10%, operation accuracy, all controlled T02 cases, and reserved whole-game transfer separately. Training loss reductions alone do not establish learned transformation competence. The active 16-slot map is a T02 **interference** curriculum, **not** full gameplay transformation training; progress on T03 and later must still be demonstrated experimentally.

## Offline validation

```powershell
& $py -m unittest tests.test_nanojev_game_transform_ladder_map -v
```

The CPU synthetic smoke verifies actual optimizer backpropagation, checkpoint roundtrip, manual phase transition, generated cache reuse and integrity, and source/SQL immutability. That smoke does **not** prove learning on real Qwen checkpoints. The real checkpoint and six-gigabyte CUDA hardware are available only on your workstation.
