# NanoJev game-native mathematical ladders and full source rebuild (v6)

## Scope and corrected semantics

Previous verified v5 bridges are preserved. Their `Z` is explicitly a **legacy before-minus-after numeric projection**, not a strategic choice. V6 adds independent **game-native** outcomes, generated for all 33 existing executable game models.

- For each executable 2×2 matrix game: best response against **each** opponent action (deterministic tie selection) and both mixed-equilibrium strategy probabilities **only when the frozen payoff matrix admits an interior 2×2 equilibrium**.
- For all 19 executable scalar game models: the source-defined scalar mathematical value itself, not its before-after difference.
- For numerical output, `ARGMAX` returns the selected row action index (0 or 1), `DIVIDE` returns mixed-strategy probability, and the scalar formula root returns its own calculation result. The answer record also carries game identity and semantic role.
- `permute_row` and `permute_column` reorder action **labels together with the relevant payoff tensor axis**. The legacy tensor-only transformer is left unchanged, preserving its 2,096 verified test vectors. The v6 semantic verifier checks action-keyed payoffs, equilibrium profiles, and row-player best-response labels.
- `positive_affine` and `shared_payoff_shift` are recorded as strategically equivalent but **not** mere representational relabelings. `change_one_payoff` and `reverse_payoff_sign` are not assumed equilibrium-preserving.
- The 196 catalog-only games are explicitly blocked from the new gold corpus. The separate two synthetic pilot games remain provisional and do not become verified source-native games.

## All definitions and source-generated outputs

`tools/nanojev_game_native_objectives_v6.py` owns the 19 scalar expression definitions, mixed-equilibrium expressions, action-label permutation semantics, eight successive extraction programs, strict story grammar, local operator traces, and independent final oracles.

`tools/nanojev_game_math_smoke.py` registers four additional typed mathematical operators: `MATRIX_NATIVE_BEST_RESPONSE`, `MATRIX_MIXED_ROW_PROBABILITY`, `MATRIX_MIXED_COLUMN_PROBABILITY`, and `MATRIX_ACTION_LABEL_RELABEL`, with their dependencies, semantics, source provenance, and verified test vectors.

The smoke emits a new `native_game_task_bank.sqlite3`, marked `proof_only` (not integrated into the active trainer). It also records v6 cases, tiers, edges, game coverage, and matrix invariants in `game_transformations.sqlite3`; `native_game_ladder_proof.json`, `native_game_coverage.json`, and the manifest are emitted alongside the existing v1-v5 files.

**No SQLite or other generated binaries are distributed with the patch.** The live model, checkpoints and active training banks are not touched.

## Apply / rebuild on Windows (PowerShell)

Apply the **v6 incremental patch** to a repository that already has v5; or apply the **cumulative source bundle** to a compatible baseline `main_computer` repository with `tools/nanojev_training_task_store.py`. Both ZIPs are rooted at `main_computer_test/`; preview the change set with the repository's `new_patch.py --dry-run` mechanism if available before applying.

From `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py -m unittest `
    tests.test_nanojev_game_transformation_library `
    tests.test_nanojev_game_math_smoke `
    tests.test_nanojev_game_ladder_bidirectional_proof `
    tests.test_nanojev_game_ladder_edge_programs `
    tests.test_nanojev_game_ladder_state_machine `
    tests.test_nanojev_game_ladder_full_catalog `
    tests.test_nanojev_game_native_objectives_v6 -v

& $py -m tools.nanojev_game_math_smoke --generate --verify-all --report

& $py -m tools.nanojev_game_math_rebuild_audit --instances-per-game 16

& $py -m tools.nanojev_game_native_objectives_v6 `
    --database runtime/game-math-source-smoke-v6/game_transformations.sqlite3 `
    --limit 1
```

Generated files go to `runtime/game-math-source-smoke-v6/`, leaving v5 undisturbed. The audit creates **two separate empty directories**, generates all 21 artifacts from source in each, verifies manifests and compares every artifact's bytes including all SQLite databases, then deletes only its own temporary directory. A passing rebuild audit is not a mathematical or empirical validation of catalog-only games.

Never overwrite a destination without a valid same-generator manifest; `--replace` uses the existing safety checks. To regenerate an independent corpus, supply `--output-dir <new empty path>`. The full-size audit defaults above to 16 instances/game and seed 20261016; audit test independently confirms full rebuild deterministic.

## Expected verified full-size results (seed 20261016, 16 instances/game)

- Mathematical definitions: 109; mathematical oracle vectors: 72
- Legacy transformation cases: 2,096 (still separately verified)
- Native game families/models: 33 (14 matrix, 19 scalar)
- Native game problems: 1,136; tier presentations: 10,224; state-threaded edges: 9,088
- Action-label transformation invariants checked: 1,792
- Catalog-only games blocked: 196
- Combined regression tests: 67 passed
- Rebuild proof: 2 independent empty-directory builds, 21/21 artifacts byte-for-byte identical

## Proof boundary

The downstream transformations work under an explicit constrained public-text grammar, not unrestricted natural-language parsing. Scalar targets are model values, and matrix targets are actions or two-player interior mixed-equilibrium probabilities; neither claims to solve an unspecified game. No inference about real opponent behavior is implied, and none of the synthetic auction assumptions is promoted to a theorem. This release does not train or measure NanoJev's neural head.
