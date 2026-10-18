# NanoJev V10 — Cumulative X/Y mutation lattice and immutable teacher

## Scope and governing rule

V10 changes **only deterministic mathematical-case freezing, learner-facing
presentation generation, SQLite database creation, and source-only validation**.
No trainer, live probe, installed model, hub, V8 source, V9 database, or
checkpoint is modified. V10 teacher data is **not yet** an active training mix.

We no longer regard T00–T08 as individual prompt formats. Every mathematical
tier has *its own* assistance lattice. The same verified mathematical snapshot
is rendered at all nine mathematical tiers. Within each tier, independent
mutations reduce the assistance available for X and Y.

For an operand, assistance has three states:

0. `full` — registered numeric value and signed LogBar.
1. `no_logbar` — registered numeric value, no LogBar.
2. `raw` — no explicit operand registration; recover from the tier's task.

The complete product of these states is **3 × 3 = 9 sublevels per tier**.

| X state | Y full | Y without LogBar | Y without register |
| --- | --- | --- | --- |
| X full | `full` | `logbar_y` | `y_raw_x_full` |
| X no LogBar | `logbar_x` | `no_logbars` | `register_y` |
| X without register | `x_raw_y_full` | `register_x` | `raw` |

A mutation adds only one of `drop_x_logbar`, `drop_y_logbar`,
`drop_x_register`, `drop_y_register`. Dropping a register requires first
dropping that operand's LogBar. Both paths below must produce byte-identical
final prompts and unchanged X/Y/OP/Z truth:

```
Full → X no LogBar → X no register → Y no LogBar → Y no register
Full → Y no LogBar → Y no register → X no LogBar → X no register
```

Four *atomic* commuting squares and one *whole-side* commuting pair are
verified for each mathematical tier. This yields **five XY equivalence proofs
per tier and 45 per case**, with complete mutation-order paths in SQLite.

A stage has its own `stage_id`, e.g. `T08/x_raw_y_full` or `T02/register_y`.
`T02a-X`, `T02a-Y`, and combined `T02b` should later be trainer aliases for
validated assistance classes, not additional hard-coded mathematical oracles.

## Mathematical teacher contract

1. Read only V6 `native_v6_cases` with `status='PASS'` and canonical tier-8
   prompts; verify each source `t08_sha256`.
2. Execute **one** closed V8 source mutation checkpoint per seeded realization,
   preserving the V8 order-independence proof. Freeze that source state.
3. Descend from T08 to T00 **without re-mutating** the source facts at each
   mathematical tier.
4. Execute the coupled T00 calculation and compare it to the independent
   game oracle. Store exact rational X/Y/OP/Z labels and proof hashes.
5. Generate all 9×9 presentation variants from that **same frozen snapshot**.
   Only the representation changes; X/Y/OP/Z stay identical.
6. Validate each atomic mutation, each commutative square, the complete
   X-first / Y-first compositions, and T08's raw-story equivalence.
7. Persist labels, prompts, mutation classes, parent-child stage edges,
   source ancestry, mathematical snapshots, exact proof paths, and hashes.

**Source mathematical mutations are separate.** Some V8 source edits affect
both derived operands, even when older code calls a mutation `X` or `Y`.
V10 does not misrepresent these as operand-specific mathematical edits.
X-only and Y-only **presentation** changes retain a fixed numerical state.

## Important behavior of the upper mathematical tiers

The raw prompt at T08 is **exactly** the native V6 story from the frozen
source facts, with no injected answer register, no LogBar, and no auxiliary
model-provided target. Its full-scaffold sibling contains the same story plus
verified X/Y registers and LogBars. T03–T07 similarly retain the actual native
mathematical-task text while their assistance is gradually withdrawn.

T00–T02 need additional care because their original operation refers to
registers: when a register is removed, its operand is substituted as a direct
numerical literal in the operation at that operand's position. In particular,
`T02/x_raw_y_full` and `T02/y_raw_x_full` are the two independent branches,
while `T02/raw` is the original compact `Compute OP using X and Y` formulation.

The new LogBar renderer extends the earlier integer-only signed `log1p`
notation to fractional values **without integer truncation**. The numeric
label remains lossless in the database. Prompt numeric text follows the
existing V6 rendering. The LogBar is an approximate auxiliary representation,
not a second mathematical target.

The representation lattice provides an *ordered removal of assistance*;
it does **not** claim measured model difficulty must increase monotonically.
Learner performance must be established by a later V10 training/probing patch.

## SQLite schema

- `manifest`: schema and full source hash, seed, policy, generator version.
- `mutation_classes`: explicit mutation family, affected operand and invariant.
- `stages`: 81 stages (9 tiers × 9 assistance states) and inherited mutation sets.
- `stage_edges`: atomic presentation transitions plus explicit cross-tier
  reset-to-full-scaffolding transitions. Cross-tier resets are labeled as such;
  they are not falsely asserted to be atomic operand mutations.
- `cases`: source-case and instance ancestry, split, seed, frozen source state,
  exact mathematical answer, native tier prompts, V8 checkpoint/descent proof.
- `presentations`: stage-specific prompt plus snapshot and answer hash.
- `equivalence_proofs`: 4 atomic diamonds and one whole-side XY commuting
  pair for each tier and each case, including both mutation sequences.

This table layout permits same-case comparisons across every stage, including
pairing X-first and Y-first branches without leaking ancestry across splits.

All file operations are create-only. The builder writes a staging SQLite file,
closes all connections, checks all proofs, then atomically hard-links a new
output filename. It refuses to replace an existing teacher.

## Commands (PowerShell, repository root)

### Tests, no model required

```powershell
python -m unittest -v tests.test_nanojev_game_math_curriculum_v10
python -m tools.nanojev_game_math_curriculum_v10_smoke
```

The full smoke generates **temporary synthetic test cases** using the 33
executable native game families (19 scalar and 14 matrix), verifies their
native mathematical oracles, constructs the teacher twice, compares bytes,
and rechecks every stage and path. It discards these temporary databases.
This is not a substitute for building from the workstation's source DB.

### Production teacher build (does not touch V9)

The source is the **V8 native mathematical source**, not the legacy
`runtime/game-transformation-experiment/game_transformations.sqlite3`.
The legacy DB lacks the `native_v6_cases` and `native_v6_tiers` tables;
its use caused an `sqlite3.OperationalError` in the original patch.
Use the same native-source path as V9's default.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m tools.nanojev_game_math_curriculum_v10_build source-info `
  --native-db runtime/game-math-source-smoke-v8/game_transformations.sqlite3

& $py -m tools.nanojev_game_math_curriculum_v10_build build `
  --native-db runtime/game-math-source-smoke-v8/game_transformations.sqlite3 `
  --output runtime/game-math-curriculum-v10/teacher.sqlite3
```

`source-info` performs a read-only schema and passing-T08 case preflight.
If the V8 database is elsewhere, supply its actual path. This command does
not convert or modify the legacy transformation DB.

```powershell
& $py -m tools.nanojev_game_math_curriculum_v10_build verify `
  --teacher-db runtime/game-math-curriculum-v10/teacher.sqlite3

& $py -m tools.nanojev_game_math_curriculum_v10_build inspect `
  --teacher-db runtime/game-math-curriculum-v10/teacher.sqlite3 `
  --tier T02

& $py -m tools.nanojev_game_math_curriculum_v10_build inspect `
  --teacher-db runtime/game-math-curriculum-v10/teacher.sqlite3 `
  --tier T08
```

`inspect` returns all 9 prompts for the selected case/tier plus exact
X-first and Y-first paths. It is read-only and makes no model predictions.

### Expected verification metrics

For `N` verified frozen mathematical cases and one realization each:

- **81 × N** presentations.
- **45 × N** persisted XY equivalence proofs.
- **81** stage IDs and **116** directed stage edges (9 × 12 intra-tier, 8 cross-tier).
- The same target SHA-256 for all 81 presentations of any given case.
- The same raw T08 story as `native.render08(snapshot)`.
- Two clean rebuilds with the same inputs and seed give the same SQLite bytes.

## Boundaries and next step

This patch does **not** modify `nanojev_game_math_curriculum_v9.py`, the
legacy T03 bridge, the optimizer, or live probe. Therefore V9 training
currently **cannot** consume the new stages. A separate trainer/probe cutover
must use V10 `stage_id` and the stored ancestry/proofs as first-class data,
retain the isolated-vs-combined X/Y gates, and import the existing decoder
checkpoint into a new lineage only after a measured baseline.

A passing V10 proof establishes *mathematical target consistency, true atomic
presentation mutations, and path commutativity*. It does **not** establish
that the model has learned the target or that each stage is empirically harder.
