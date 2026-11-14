# NanoJev game mathematics — V7 seeded mutation checkpoint brain

## Purpose

V7 adds a **source-generated, reproducible proof scheduler** above the verified V6 game-native mathematical ladders. It supplies deterministic training targets and examples of varied *realizations*. It does **not** train or update the NanoJev head; it is not a replacement for the learned head.

The checkpoint is a completed, bounded composition at a level:

\[
M_i(p) = XY\{Z_1,\ldots,Z_n\}'_i(p),\quad n\ge 0.
\]

`X` and `Y` name the two preferred source-expression mutation branches. Their **actual numerical footprint** is measured after execution as `X`, `Y`, `XY`, or `INVARIANT` and must not be assumed from the branch name. `Z1` and `Z2` are optional additional source mutations; neither is the final native mathematical answer `Z`. A terminal `Z_DOMAIN` checkpoint validation depends on *all* required source mutations.

A seeded topological scheduler chooses a legal order of the ready source mutations within each level. The checkpoint gate cannot run early. After it succeeds, the level's existing native descent program is applied. The adjacent-state result is compared against the same tier rebuilt canonically from the mutated facts. Finally T00 is coupled to V6's typed native runner and independently verified native oracle.

## Two orthogonal seeds

- **Order seed:** randomizes scheduling order within each level, but must **not** change the canonical checkpoint, `(X,Y,OP,Z)`, or final answer for fixed source mutations.
- **Mutation seed:** selects bounded signed changes to distinct numerical source paths; it can alter extracted X/Y, the final Z, or both. A categorical optimal action may remain unchanged even when the underlying payoffs change.

Every proposed edit is domain-tested. The two independent schedules (seeded and canonical sorted) must produce the same mathematical checkpoint. Source paths are disjoint; their numerical consequences can be coupled. Some games have no independent X-only path, so a source mutation may be correctly classified as `XY`. If a proposed mutation violates the domain or an intermediate state, the generator rejects it rather than making up a target.

This is a *bounded source-leaf mutation subset*. Operator replacement, arbitrary rule introduction, unconstrained natural-language T08.5 changes, and general compositional discovery are future classes, not falsely claimed by V7.

### Current implementation and performance boundary

To verify a changed source fact at an arbitrary tier, V7 recompiles that tier from its updated public facts using existing V6 programs. This is intentionally a **correctness-first reference execution**, not a production-optimized incremental inference runtime. V6's scalar validator also invokes the original scalar oracle during domain validation before T00; V7 therefore does **not** claim exactly one oracle invocation throughout a walk. The final authoritative game-native target is emitted and independently checked by `finish()` at T00.

For each of the 33 executable native game models, the default V7 proof bank records two parameter-seeded executions, each across nine levels. The current proof bank is sampled at **one V6 case per game**, not all 1,136 game-native cases. It is a proof-only bank. The remaining 196 catalog-only games still have no supervised gold.

## Files, full rebuild, and reproducibility

`python -m tools.nanojev_game_math_smoke --generate --verify-all --report` regenerates V1–V7 data under `runtime/game-math-source-smoke-v7/`, including:

- `mutation_brain_v7.sqlite3`: source-generated runs and every tier's local mutations, schedule, canonical checkpoint hash, and Z projection.
- `mutation_brain_checkpoint_proof.json`: coverage and result variation counters.
- `manifest.json`: hashes of 23 generated artifacts and of the source modules.

The earlier full transformation and native proof banks are still generated. No prebuilt SQLite database is distributed. No active training task bank or checkpoint is overwritten. The existing `tools.nanojev_training_task_store` source dependency must be present in the target repository, just as for V6.

### Apply v7 patch (existing v6 checkout)

Use your standard patch installer on the `main_computer_test/`-rooted archive. Then from the repository root:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

# Fast full-generator verification with four instances per game.
& $py -m tools.nanojev_game_math_smoke `
  --generate --verify-all --report --instances-per-game 4

# Inspect seed-controlled realization on the first native game.
& $py -m tools.nanojev_game_mutation_brain_v7 `
  --database runtime/game-math-source-smoke-v7/game_transformations.sqlite3 `
  --order-seed 11 --mutation-seed 17

# Same parameter mutations; order-only variation.
& $py -m tools.nanojev_game_mutation_brain_v7 `
  --database runtime/game-math-source-smoke-v7/game_transformations.sqlite3 `
  --order-seed 29 --mutation-seed 17

# Same order; different numerical mutations.
& $py -m tools.nanojev_game_mutation_brain_v7 `
  --database runtime/game-math-source-smoke-v7/game_transformations.sqlite3 `
  --order-seed 11 --mutation-seed 18

# Cold reproducible rebuild from two new empty directories.
& $py -m tools.nanojev_game_math_rebuild_audit --instances-per-game 4

# Full-size cold rebuild; run on the workstation.
& $py -m tools.nanojev_game_math_rebuild_audit --instances-per-game 16
```

To generate the default-size full database, omit `--instances-per-game 4` in the first command. V7 has its own output directory so V6 data remains untouched.

### Verified locally during patch construction

- V7's 9 tests passed, including two byte-identical source rebuilds.
- V6 native + V7 focused regression: **19 tests passed**.
- Standalone `nanojev_game_math_rebuild_audit --instances-per-game 4`: **PASS**, 23/23 byte-identical outputs.
- V7 integrated 4-instance run: **PASS**; 33 games, 66 realizations, 594 checkpoints, 1,404 source mutations, 216 auxiliary source mutations, 185 differences in schedule ordering, all 33 with different X/Y projections and 23 with different final Z under distinct mutation seeds.
- A larger all-version regression invocation and the 16-instance two-build audit **timed out** here. They are not counted as completed passes.

## Next engineering target

Convert the V7 reference recompilation into a truly incremental mutation interpreter over typed state graphs, then admit structural/operator-changing `Z` classes with explicit dependency DAGs and independent end-to-end oracles. Preserve checkpoint closure and the two-seed contract. Generate larger held-out composition families for NanoJev regression only after the deterministic proofs pass.
