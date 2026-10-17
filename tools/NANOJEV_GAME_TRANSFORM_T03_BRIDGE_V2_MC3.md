# NanoJev T03 computational bridge V2 (MC3)

## Purpose

Continue from the *existing* successful T02 checkpoint at step 220,000 using a new, independent training lineage. T03 V1 and T02 sources, checkpoint files, and their sealed generator identities are never changed.

The central invariant is that the transition starts with **exact native T02 numerical serialization**, introduces one new instructional requirement at a time, and requires **development-set evidence plus T00/T02f retention** before promoting difficulty.

## Files

- New `tools/nanojev_game_transform_t03_tiered_v2.py`: native-bridge generator, oracle cases, split isolation, stage-specific quotas, checkpoint cutover/resume, automatic advancement, retention/computation evaluator, and one-command probe.
- Updated `tools/nanojev_game_transform_t03_live_probe.py`: its original no-argument command automatically probes V2 if a V2 checkpoint exists; all explicit legacy options still invoke the V1 probe as before. **Does not modify the sealed V1 question generator.**
- New `tests/test_nanojev_game_transform_t03_tiered_v2.py`: oracle and no-leak validation, exact T02 byte representation, sealed phases, real CPU optimizer continuation, V2 probe, promotion/blocking, and old-probe routing.
- This note.

## Training exercise progression

- **T03a0:** exact original `registered_logbar` prompt, both operands visible, small signed numbers.
- **T03a1:** same native prompt preceded by a true equation, both visible.
- **T03a2:** same native prompt preceded by a verbal calculation rule, both visible.
- **T03b0:** one-digit X, addition with positive small delta; Y hidden.
- **T03b1:** modest signed addition/subtraction; Y hidden.
- **T03b2:** wider signed integer addition/subtraction; Y hidden.
- **T03c–T03h:** existing V1 arithmetic, affine, worded math, payoff-cell and permutation tasks, incrementally weighted.
- **T03i:** unmodified original full-game T03 prompt.
- **T03j:** existing withheld arithmetic generalization diagnostic only.
- **T08:** excluded from all training phases.

Bridge training, development, and test use mutually disjoint `X % 3` partitions and source-generated arithmetic oracles. The small initial finite arithmetic domain is deliberate; an increasing numerical range begins at T03b1/b2. The target for a hidden Y is never placed into its register as an oracle answer.

## Initial smoke (Windows PowerShell)

From `C:\Users\subsi\main_computer` in the correct virtual environment:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m tools.nanojev_game_transform_t03_tiered_v2 train `
    --device cuda --dtype float16 --steps 220002 `
    --chunk-steps 2 --save-every 1 --report-every 1
```

Initial fork reads `C:\Users\subsi\NanoJev\runs\game_transform_ladder_t02_corrected\latest.pt` and creates `C:\Users\subsi\NanoJev\runs\game_transform_t03_tiered_v2\stage-00\latest.pt`. It continues the parent's decoder, optimizer, and RNG state, but seals a new stage quota map.

Note: even a two-update smoke includes a frozen-feature cache build and a baseline/retention probe, and may use considerable GPU time. CPU smokes use a synthetic backbone; actual CUDA results must be verified locally.

## Continue and probe

```powershell
& $py -m tools.nanojev_game_transform_t03_tiered_v2 train `
    --device cuda --dtype float16 --steps 230000
```

Target steps are **cumulative**, not additional. The default evaluation interval is 1,000 steps. The controller trains the current stage, evaluates **only development examples of its promotion prerequisites and T00/T02f**, and requires **two consecutive passed checks** to create the next stage. At the final stage it simply keeps training. Development data controls promotion; test and game-transfer records are never consulted by the automatic controller.

Manual **full** probe (includes separate test, withheld T03j, and game transfer):

```powershell
& $py -m tools.nanojev_game_transform_t03_live_probe
```

The same existing command still probes V1 until a V2 checkpoint exists. To always select V2:

```powershell
& $py -m tools.nanojev_game_transform_t03_tiered_v2 probe
```

Each probe snapshots the latest published checkpoint, checks its exact step/hash, compares it against the original T02 parent, prints retention and tier accuracy, shows a few real X/Y predictions, writes a uniquely named JSON report under the V2 root, and removes the temporary checkpoint copy. No manual snapshot/report parameters are required.

## Advancement guards

- Retention (development only): T00 and T02f paired-X/Y accuracy within 10% must remain within **five percentage points** of the T02 baseline, with an absolute minimum of 75%. Missing or unscorable evidence fails closed.
- Progression requires stage-dependent development-set prerequisites, including near-integer Y correctness within an absolute ±0.5 for hidden-operand arithmetic. The probe additionally reports paired 5% and 10% accuracy and actual predictions.
- Two consecutive successful gates are necessary to advance. Promotions create `stage-01`, `stage-02`, etc., with the prior checkpoint copied without modifying the original.
- The generated V2 source hash, stage quotas, pool size, cache settings, optimizer identity, and input hashes are checked on resume.
- Do not edit this new generator in-place after starting a lineage; changing its SHA requires a new experiment/output directory.

## Limitations

The attached CPU integration smoke confirms checkpoint fork, update, probe, continuation, resume, and phase promotion with a synthetic frozen backbone. It does **not** demonstrate arithmetic competence of the actual CUDA Qwen head. The thresholds are conservative initial policies, not experimentally established optimal values. Run the V2 live probe to measure actual generalization, exact-integer arithmetic, T00/T02f retention, and full-game transfer before scaling to long runs.
