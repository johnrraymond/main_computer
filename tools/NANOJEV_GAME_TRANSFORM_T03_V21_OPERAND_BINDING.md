# T03 V2.1 — distinct arithmetic offset and register target

## Cause and invariant

The active V2 `T03a1`/hidden-Y generator prepends `Equation: YYYYYY = XXXXXX + (delta).` to a native numerical prompt that already has a `YYYYYY =` register. This creates a competing assignment, and observed predictions of hidden Y approximately equal `delta` instead of `X + delta`.

**Invariant:** the native prompt contains the only `XXXXXX =` and `YYYYYY =` register assignments. The arithmetic **OFFSET** is a named transformation parameter, not the Y register or oracle answer. Both orientations preserve identical targets, and hidden-Y targets cannot appear in a register.

V2.1 changes the rule to `Expression (not a register assignment): XXXXXX + OFFSET -> YYYYYY; OFFSET is +5.` and the hidden register to `YYYYYY = [compute from expression]`. T03a2's previously high-performing verbal presentation, T03a0, oracle computation, stages, quotas, thresholds, other game tiers, and retention rules are unchanged.

The full V2.1 trainer is copied under a **new module and sealed generator hash**. It defaults to `$HOME/NanoJev/runs/game_transform_t03_tiered_v21`, forked from the same T02 step-220000 checkpoint. Existing V2 `stage-00` is untouched. Do not overwrite or patch the old V2 generator.

The existing one-command `tools.nanojev_game_transform_t03_live_probe` automatically selects V2.1 when its first checkpoint exists, otherwise V2 (if present), otherwise V1. Explicit V1 probe options retain their meaning. The V2.1 live summary includes a new dev-set diagnostic comparing paired Y predictions close to `OFFSET` versus correct calculated Y (absolute ±0.5), along with examples displaying X, OFFSET, expected Y, and predicted Y.

## Install

```
python new_patch.py .\nanojev_game_transform_t03_v21_operand_binding_mc3.zip --dry-run
python new_patch.py .\nanojev_game_transform_t03_v21_operand_binding_mc3.zip
```

## Smoke and training

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m tools.nanojev_game_transform_t03_tiered_v21 train `
  --device cuda --dtype float16 --steps 220002 `
  --chunk-steps 2 --save-every 1 --report-every 1
& $py -m tools.nanojev_game_transform_t03_live_probe
```

Then resume the V2.1 lineage to step 230000, or another bounded target, with:

```powershell
& $py -m tools.nanojev_game_transform_t03_tiered_v21 train `
  --device cuda --dtype float16 --steps 230000
```

On first fork, V2.1 imports the T02 decoder, optimizer, RNG and phase history; only the new generator and training sequence change. GPU gains are **not** proven by the CPU smoke; actual before/after dev/test Y computation and offset-echo rates determine whether the correction succeeds.
