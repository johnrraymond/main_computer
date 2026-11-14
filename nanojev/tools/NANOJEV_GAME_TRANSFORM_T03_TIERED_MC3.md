# NanoJev MC3 — tiered T03 computation curriculum

## Domain invariant

The successful T02 step-220,000 numerical-binding checkpoint remains immutable; T03 must learn one computation at a time from independently checked numerical oracles, and reserved T08 games never become training examples.

This package adds one standalone module, one test module, and these instructions. It does **not** replace the installed `nanojev_game_transform_ladder_map.py` or its diagnostic generator. It requires the preceding MC3 library, levels, V2 ladder, and direct-cutover patches to be installed. The existing `t02-isolation-v2.json` remains valid because the original generator is unchanged.

## T03 levels

| Tier | Task | Target |
|---|---|---|
| T03a | X and Y explicit with a valid addition rule | Verify the bridge preserves both operands |
| T03b | X and additive offset; Y hidden | Compute addition/subtraction |
| T03c | X and a multiplier; Y hidden | Compute multiplication and sign changes |
| T03d | X and an affine formula | Compute `a*X+b` |
| T03e | Same arithmetic expressed verbally | Parse a multi-operation instruction |
| T03f | Small payoff matrix and selected coordinate | Retrieve a cell |
| T03g | Payoff matrix and an explicit row/column rotation | Follow a coordinate transformation |
| T03h | Retrieve a payoff cell, then apply an affine rule | Combine lookup and computation |
| T03i | Original MC3 full-game T03 format | Compute hidden Y from the verified transformation case |
| T03j | Withheld synthetic parameter and presentation combinations | Probe only; never training |

The generator creates 2,048 **distinct mathematical questions per synthetic training tier** by default; all answers are calculated from the source arithmetic oracle. Numerical X input parity partitions generated train and held-out sets, so no exact question is shared. Real T03i questions come from the verified SQL **train** partition, while its evaluation uses SQL dev, test, and game-transfer splits.

The frozen Qwen backbone remains frozen. The V2 decoder continues using the legacy `written_sub` operation ID; this patch does not introduce a new learned operator-conditioned architecture. `op_weight=0` is inherited from the T02 parent: numerical correctness is trained, not operator classification.

## Default weighted schedule

- 30 of 41 logical slots to T03a–T03i; every game slot has two prompt orientations, producing 74.07% of per-step presentations for T03.
- One each to T01 and T02f numerical retention.
- Two each to T04, T05, T06, and T07.
- One to legacy numerical retention.
- **Zero T08.** The code rejects T08 in custom schedules.

The chosen phase remains fixed: **no automatic tier promotion**. Adjust `--slots` when creating a *new* output lineage; a resumed lineage checks the sealed allocation and generator SHA.

## Installation

From `C:\Users\subsi\main_computer`:

```powershell
python new_patch.py .\nanojev_game_transform_t03_tiered_mc3_patch_v1.zip --dry-run
python new_patch.py .\nanojev_game_transform_t03_tiered_mc3_patch_v1.zip
```

## First run — start from the successful T02 checkpoint

The default `--from-output` is `C:\Users\subsi\NanoJev\runs\game_transform_ladder_t02_corrected` and the new default output is `C:\Users\subsi\NanoJev\runs\game_transform_t03_tiered`. The previous T03-priority experiment is **not** used or overwritten.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py -m tools.nanojev_game_transform_t03_tiered train `
    --device cuda --dtype float16 `
    --steps 220002 --save-every 1 --report-every 1 `
    --cache-per-level 64 --refresh-every 1000
```

`--steps` is the cumulative step target; two optimizer steps follow the step-220,000 parent. After that smoke, continue the **same output** toward an initial 20,000 new steps:

```powershell
& $py -m tools.nanojev_game_transform_t03_tiered train `
    --device cuda --dtype float16 `
    --steps 240000 --save-every 500 --report-every 100 `
    --cache-per-level 64 --refresh-every 1000 --max-wall-hours 8
```

Resuming reruns the same command with a larger cumulative target. The parent T02 checkpoint and source SQL are read-only, and a separate output lineage keeps both prior T02 and T03-priority checkpoints intact. The cache is computed from training-only data. Initial cache construction can be slower than subsequent updates; 20,000 or 200,000 steps do not guarantee generalization.

## Probe T03 tiers, including withheld T03j

For a stopped run:

```powershell
& $py -m tools.nanojev_game_transform_t03_tiered probe `
    --device cuda --dtype float16 `
    --limit 48 `
    --report .\runtime\game-transform-experiment\t03-tiers-after.json
```

This compares the original **step-220,000 T02** decoder against the tier-trained checkpoint and reports strict paired X/Y accuracy at 5% and 10% on both orientations. Synthetic T03a–T03h/T03j use disjoint held-out numerical questions. T03i evaluates actual SQL dev/test/game_transfer cases. The probe does not train and does not test operator classification or game equilibrium correctness.

For a live run, use a different PowerShell window, copy `game_transform_t03_tiered\latest.pt` immediately after a checkpoint message to a separate snapshot, then pass `--experimental-checkpoint <snapshot>`. Running the probe concurrently may compete for GPU memory.

To verify legacy retention independently, run the **existing** `tools.nanojev_game_transform_ladder_map diagnose` with levels `T00,T02f,T03,T04,T05,T06,T07,T08` against the tiered checkpoint. T08 stays withheld.

## Changing emphasis, deliberately

A new independent lineage can be created with custom slots, e.g.:

```powershell
--slots 'retention=1,T01=1,T02f=2,T03a=3,T03b=8,T03c=6,T03d=5,T03e=3,T03f=2,T03g=1,T03h=1,T03i=1,T04=2,T05=2,T06=2,T07=2'
```

Use a new `--output` directory for a different schedule. The prompt-oracle source SHA, pool size, quota map, caching frequency, source checkpoint SHA and DB SHA are checked on resume.

## Scope and proof

- Oracle unit tests check arithmetic and source-cell calculations on hundreds of generated cases per tier.
- Determinism, exact nonidentity targets, disjoint training/test numeric domains, quota totals, T08 exclusion, provenance, and byte-identical checkpoint forking are tested.
- A synthetic frozen-backbone CPU continuation validates an **actual optimizer update** after the fork through arithmetic and full-game T03.
- No claim of live CUDA accuracy is made until the user runs and probes the resulting checkpoint.
