# NanoJev V10.2 — Source-grounded operand derivation audit

## Defect and correction

A production read-only probe of 48 developer cases found that `T03/x_raw_y_full` showed symbolic X in every case but `T03/y_raw_x_full` retained Y's literal value in the native `COMPUTE` instruction for all 48. Thirty-three of the 48 used opaque matrix placeholder names rather than a computable payoff-index expression. The 48 original X/Y oracle labels were correct. **Prompt-generation semantics, not numerical oracle arithmetic, were defective.**

V10.2 repairs *the 9-node X/Y assistance lattice* for each mathematical tier while preserving the frozen source snapshot and its independent labels. T00–T02 remain straightforward explicit-number extraction; they are not claimed to be mathematical operand derivation. The original V8/V9 source prompts and grammar bridge are kept byte-for-byte as historical anchors; they are separate benchmark/retention material, **not** corrected operand-derivation exercises. Their original opaque placeholders (where present) intentionally remain in those historical anchor rows. The corrected stages are `x_raw_y_full`, `y_raw_x_full`, `register_x`, `register_y`, and `raw`, for T03–T08.

- T03: the hidden Y value is no longer present as an inline numerical operand. X-raw and Y-raw each show the symbolic expression of the side actually unregistered.
- T04/T05: the hidden side's opaque matrix placeholder is replaced with an executable payoff-index expression. The source `GAME_FACTS` remain intact.
- T06/T07: the hidden side's symbolic definition is explicitly supplied alongside unchanged verified `GAME_FACTS`.
- T08: the source narrative is retained, with a self-contained mathematical rule describing hidden operands; **no registered X/Y values, LogBars or `GAME_FACTS` JSON** appear in T08/raw. The story's existing facts and the rule jointly define the computation. Because the legacy story omitted some mathematical rules, T08/raw is deliberately *not* byte-identical to the historical T08 anchor.

### Exact matrix operand formulas

Index convention: `U[row][column][player]`, player 0 = row player/you, player 1 = opponent; `selector` = chosen rival column index for best responses.

| Objective | X formula | Y formula |
|---|---|---|
| Best response | `U[0][selector][0]` | `U[1][selector][0]` |
| Mixed row probability numerator/denominator | `U[1][1][1]-U[1][0][1]` | `U[0][0][1]-U[1][0][1]-U[0][1][1]+U[1][1][1]` |
| Mixed column probability numerator/denominator | `U[1][1][0]-U[0][1][0]` | `U[0][0][0]-U[0][1][0]-U[1][0][0]+U[1][1][0]` |

Scalar expressions come from the established native symbolic AST, rather than from X/Y numerical labels. **MAX stays MAX** when the original game calls for MAX; no operator is substituted. A restricted, non-`eval` interpreter independently calculates the exact Fraction of both printed formulas using the frozen source facts and compares it with the native oracle; unknown AST nodes and code-execution constructs are rejected.

## Immutable teacher and verification

The generator now writes `operand_derivation_proofs` for every case, T03–T08, and side (12 rows per source case). Each row includes formula text, its SHA256 and independently calculated target hash. The full verification rerenders all stages, recomputes all native mathematical values, validates derivation proofs, parent presentation hashes, grammar bridges, commuting X/Y paths, train/dev/test ancestry and foreign keys.

The teacher schema and deterministic-brain version are bumped to V10.2. **Old V10.1 SQLite files cannot masquerade as corrected data.** Case ID namespace, mutation seeds and stage names remain fixed for 48-case baseline comparability. The original teacher, source and old model checkpoint must remain untouched.

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

# CPU-only smoke. Regenerates the 33 game families and validates 71 native cases.
& $py -m tools.nanojev_game_math_curriculum_v10_smoke

# Create a NEW database. Never overwrite the V10.1 teacher.
& $py -m tools.nanojev_game_math_curriculum_v10_build build `
  --native-db runtime/game-math-source-smoke-v8/game_transformations.sqlite3 `
  --output runtime/game-math-curriculum-v10-operand-audited/teacher.sqlite3

# Complete read-only check of every row and mathematical target.
& $py -m tools.nanojev_game_math_curriculum_v10_build verify `
  --teacher-db runtime/game-math-curriculum-v10-operand-audited/teacher.sqlite3

# Verify all cases and print 5 real paired T03 examples with expressions.
& $py -m tools.nanojev_game_math_curriculum_v10_build audit `
  --teacher-db runtime/game-math-curriculum-v10-operand-audited/teacher.sqlite3 `
  --tier T03 --split dev --examples 5
```

For 1,136 source cases and one variant, expect 160,176 stage prompts, 51,120 commuting path proofs and **13,632 operand derivation proofs**. Counts differ if source case count/variant count changes. Do not run training if the database verification fails.

## Resume trained weights into a NEW lineage

The teacher's contents and SHA256 have changed: **resuming the old V10.1 output directory against this new teacher is deliberately forbidden.** The optional warm-start path imports *only* the trained decoder and operator-head weights from the old V10 checkpoint, checks source and parent provenance, and starts a fresh AdamW optimizer, fresh baseline probe and the normal two-successive-probe gate. The new global step begins at the inherited old global step. The original checkpoint and its optimizer history remain preserved. Changing the supplied warm-start file during a subsequent resume is rejected by checksum.

First stop the old trainer cleanly after it has saved `latest.pt`. Then:

```powershell
& $py -m tools.nanojev_game_math_curriculum_v10_train `
  --teacher-db runtime/game-math-curriculum-v10-operand-audited/teacher.sqlite3 `
  --source-checkpoint runtime/game-transform-experiment/source-v4.pt `
  --parent-checkpoint "$HOME\NanoJev\runs\game_math_xy_v10_parent.pt" `
  --warm-start-checkpoint "$HOME\NanoJev\runs\game_math_xy_v10_bridge\latest.pt" `
  --output "$HOME\NanoJev\runs\game_math_xy_v10_operand_audited" `
  --steps 2500000 `
  --chunk-steps 250 `
  --probe-limit 48 `
  --advancement-precent 50 `
  --device cuda
```

Supply the same `--warm-start-checkpoint` path on later resumes. The first probe after migration is **the new teacher baseline**, so it may select a different earliest failing stage than the old V10.1 run. Do not carry over old consecutive-passing counts: new exercises require new confirmation.

### Scope of verification performed here

The source archive used for implementation was `main_computer_test-20261017-162107.zip` plus the adjustable-advancement patch. The source-only smoke generated 71 cases from all 33 executable game families and produced 10,011 prompts, 3,195 commuting equivalence proofs, and 852 operand derivation proofs. Two clean builds were byte identical and each passed independent verification. Automated tests additionally exercised source/formula tampering and isolated warm-start migration. The **user's actual 1,136-case production SQLite and GPU weights were not available in this runtime**; production rebuild and complete 1,136-case audit must be executed locally with the commands above.
