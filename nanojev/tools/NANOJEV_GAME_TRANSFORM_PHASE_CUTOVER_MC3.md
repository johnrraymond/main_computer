# NanoJev MC3: T02 -> T03 direct phase cutover (no pre-rewrite script)

This **source-only, additive** patch installs `tools.nanojev_game_transform_phase_cutover`.
It does not replace `nanojev_game_transform_ladder_map.py`, the database, or any
checkpoint. The T02 v2 isolation diagnostic remains valid because the existing
prompt-generator source file is unchanged.

## Domain invariant

**Forking the phase must preserve the trained step-220,000 decoder, operator head,
optimizer and RNG exactly, while putting the new user-selected quotas, cached
features and further optimizer updates in a separate output directory.** T08 is
reserved for withheld-game transfer evaluation and cannot be included in the
training quota map.

## Run the new phase directly

From `C:\Users\subsi\main_computer`, with the downloaded ZIP already applied:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m tools.nanojev_game_transform_phase_cutover `
  --device cuda `
  --dtype float16 `
  --steps 420000 `
  --save-every 1000 `
  --report-every 500 `
  --max-wall-hours 8
```

There is **no manual map rewrite, parent checkpoint rewrite, or preliminary fork
script**. The command reads the T02 parent from
`$HOME\NanoJev\runs\game_transform_ladder_t02_corrected\latest.pt`, verifies its
sealed config and source/SQL/diagnostic hashes, and atomically creates
`$HOME\NanoJev\runs\game_transform_ladder_t03_priority\` with:

- `latest.pt`: byte-for-byte initial copy of the completed T02 checkpoint;
- `ladder-config.json`: automatically sealed child phase configuration;
- `level-map.json`: the selected weighted T03 phase, user-editable **only before creation** via `--slots`;
- `cutover-provenance.json`: parent identity and initial fork step;
- `frozen-capture-cache-t03_priority.pt`: generated on first training start.

The original existing ladder trainer then resumes from `220000` (or whichever
parent step is actually saved), **including AdamW optimizer state and RNG state**.
`--steps 420000` means *cumulative* total target, so if the parent ends at
220,000 this schedules 200,000 new steps, not 420,000 new steps.

Reissuing the same command resumes the **child** checkpoint; it does not
recopy or reset the child from the parent. A wall-time stop checkpoints safely
and can be continued with the same command.

### Exact quota map

| Level | Logical slots | Input presentations per step |
| --- | ---: | ---: |
| T03 | 30 | 60 |
| T01 | 1 | 2 |
| T02f | 1 | 2 |
| T04 | 2 | 4 |
| T05 | 2 | 4 |
| T06 | 2 | 4 |
| T07 | 2 | 4 |
| Legacy retention | 1 | 1 |
| T08 | 0 | 0 |
| **Total** | **41** | **81** |

This is 74.1% T03, 2.47% each T01/T02f, 4.94% each T04-T07,
and 1.23% legacy retention **by presented examples**. Retention is required
by the existing trainer. T08 cannot enter train caches, even with custom slots.

Optional custom slot schedule at first creation:

```powershell
& $py -m tools.nanojev_game_transform_phase_cutover `
  --slots 'T03=30,T01=1,T02f=1,T04=2,T05=2,T06=2,T07=2,retention=1' `
  --steps 420000 --max-wall-hours 8
```

A different schedule or parent must use a **different** `--output` directory;
the command never silently changes an established child config. Optional
`--from-output`, `--output`, `--database`, `--source-checkpoint`, and
`--diagnostic` override default paths. The command inherits `lr`, `op_weight`,
`seed`, cache sizes and refresh interval from the sealed parent config; for
the intended T02 parent, this includes `op_weight=0`.

## Evaluate learning independently

```powershell
& $py -m tools.nanojev_game_transform_ladder_map diagnose `
  --device cuda --dtype float16 `
  --levels T00,T02e,T02f,T03,T04,T05,T06,T07,T08 `
  --limit-per-split 48 `
  --experimental-checkpoint "$HOME\NanoJev\runs\game_transform_ladder_t03_priority\latest.pt" `
  --report .\runtime\game-transform-experiment\t03-priority-evaluation.json
```

T03/T04 scored examples may still exclude noninteger native V4 registers;
T07 may have limited composition coverage. T08 is held out; T05/T06/T07
must be evaluated on development, test and transfer separately. Very low
cached training loss is not proof of mathematical transformation ability.

## Targeted verification

```powershell
& $py -m unittest tests.test_nanojev_game_transform_phase_cutover -v
```

Tests exercise sealed identity, quota validation, byte-identical checkpoint
copy, phase-history preservation, safe resume, the T08 prohibition, and a
synthetic CPU optimizer continuation after fork. The CPU smoke is not a
substitute for running Qwen3-0.6B on the workstation.
