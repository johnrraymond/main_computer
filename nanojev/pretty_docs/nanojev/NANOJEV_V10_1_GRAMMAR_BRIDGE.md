# NanoJev V10.1 — verified V9-to-V10 grammar bridge

## Problem and objective

A controlled 48-case probe of the original V9 checkpoint found:

| Presentation | X | Y | Joint X/Y |
|---|---:|---:|---:|
| Original V9 T00 | 89.6% | 89.6% | 85.4% |
| Original V10 T00/full | 70.8% | 72.9% | 52.1% |
| Original V9 T02 | 87.5% | 85.4% | 75.0% |
| Original V10 T02/raw | 83.3% | 85.4% | 72.9% |

The difference is not evidence of lost checkpoint learning: grammar and assistance
were both changed before baseline. V10.1 decomposes the grammar bridge into
verified, individually reported transformations on **one frozen mathematical
case with one unchanged exact X/Y/OP/Z target**.

## New bridge nodes

V10.1 preserves all original 81 T00–T08 assistance nodes and their 51,120
commuting-path proofs (for 1,136 cases), adding 60 bridge nodes across nine
mathematical tiers. Every bridge starts at the V8/V9 native prompt, byte for
byte, as recorded by the independent V8 descent.

- T00: `v9_anchor` → X-only `:` to `=` or Y-only `:` to `=` → merged equals
  → remove redundant blank lines → add explicit compute instruction → replace
  response instruction with `Answer:` → add X LogBar or Y LogBar → `full`.
- T01: same isolated field punctuation, compact spacing, append output marker,
  then independent X/Y LogBars → `full`.
- T02: original raw numerical prompt → append output marker → introduce X
  register or Y register independently → merge → add either LogBar → `full`.
- T03–T08: exact native V8/V9 mathematical prompt → introduce X register or Y
  register independently → merge → add either LogBar → `full`.

The bridge graph records X-first/Y-first commutativity for punctuation (T00–T01),
register introduction (T02–T08), and LogBar introduction (all tiers). The
original **removal** lattice remains unchanged. These are different edges:
adding assistance during bridging does not imply removing assistance is easier.

The probe evaluates both bridge and assistance stages. The trainer starts the
T00/T01 sequence with the exact established V9 grammar. At T02–T08 it retains
its full-to-raw scaffolding-reduction order, followed by bridge verification
stages, so harder mathematical tiers are not forced to pass a raw story before
seeing a fully scaffolded version.

## Source-only, no destructive migration

The existing source teacher and `runtime/game-math-curriculum-v10/teacher.sqlite3`
are never modified. V10.1 writes only to:

`runtime/game-math-curriculum-v10-bridge/teacher.sqlite3`

Checkpoints are not changed by the build or probe. The optional V10.1 training
output defaults to `~/NanoJev/runs/game_math_xy_v10_bridge`, separate from V9 and
V10 checkpoints. Do not initiate V10.1 training before evaluating the 48-case
baseline and reviewing the first failing bridge edge.

## Workstation execution

From `C:\Users\subsi\main_computer`:

```powershell
& .\tools\run_nanojev_v10_bridge_baseline.ps1
```

The script (a) builds the new immutable database when absent; (b) verifies every
case, prompt, mathematical oracle target, and commuting-path proof; (c) ensures
the old and new databases select **exactly the same 48 development cases**, with
identical mathematical targets and original 81 assistance prompts; (d) runs
T00/T01/T02 through the new CUDA probe and prints each stage's X/Y/joint/Z
metrics, saving complete paired-edge diagnostics.

To validate only the same-case dataset alignment without CUDA:

```powershell
& $py -m tools.nanojev_game_math_curriculum_v10_bridge_compare `
  --old-teacher runtime/game-math-curriculum-v10/teacher.sqlite3 `
  --teacher-db runtime/game-math-curriculum-v10-bridge/teacher.sqlite3 `
  --limit 48
```

The original neural values above are observations, not guaranteed future results.
V10.1 must *measure* them on the user's existing CUDA checkpoint. The source
patch cannot access that workstation checkpoint or production SQLite database.

## Proofs and exclusions

- Source version and split ancestry preserved. Case ID, mutation seed, and
  order seed intentionally continue using the original V10 identity namespace
  even though the teacher schema is versioned V10.1.
- All old lattice prompts and their target hashes are byte-for-byte preserved.
- Every new bridge presentation is independently regenerated and verified,
  including its exact native grammar anchor.
- `coupled_Z` remains diagnostic only; X and Y control the gate.
- This patch does not change the neural backbone, mathematical objective,
  optimizer, or verified source database.
