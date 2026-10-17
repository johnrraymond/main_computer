# NanoJev v5 — Whole executable game-library ladder expansion

## Intent and limits

Extend the source-generated mathematical database horizontally **over every existing
executable transformation case**. Do not invent oracles for catalog-only games or
pretend scalar payoff changes are optimal strategic decisions. This is a
**mathematical transformation bridge curriculum**, complementary to the three
v2–v4 pilot *decision* ladders.

V5 compiles all 14 verified normal-form matrix models (eight transformations each)
and all 19 verified parameterized scalar models (one verified parameter shift each).
With the default 16 instances per model, there are **2,096 existing cases**, now
producing **18,864 tier prompts** and **16,768 state-threaded adjacent edges**.
The 196 catalog-only games remain catalog-only, without generated gold labels.
Two separate pilot games are retained in the existing three-game decision-ladder
proof (a canonical Hawk–Dove pilot overlaps the library). The game catalog has
231 rows: 33 games with full v5 bridges, 196 catalog-only, 2 pilot-only records.

The 14 source-verified matrices and 19 source-verified scalars include variants
of Hawk–Dove, bargaining, Bayesian updating, discounted games, inspection,
Cournot, second-price utility, and captain-specific models. V5 **does not**
upgrade the second-price utility model to an optimal-bidding oracle.

## Mathematical contract

Each source game gives a verified numerical transformation:

- **Matrix models**: source payoff matrix `U`; transformation `op`; transformed
  matrix `U_prime=matrix_transform(U,op)`; independently calculated pure Nash
  equilibria. The operand projection is `X=U[0][0][0]`, `Y=U_prime[0][0][0]`.
- **Scalar models**: frozen `parameters`, one registered `changed` parameter and
  `increment`, plus executable `scalar_oracle`. `X=oracle(original)`;
  `Y=oracle(shifted)`.
- Both have `OP=SUBTRACT` and `Z=X-Y`, retaining the **legacy projection**. The
  game-theoretic full oracle (transformed tensor/equilibria or scalar value) is
  separately recorded and independently validated. `Z` is **not** a bid, policy,
  Nash equilibrium or agent decision in the new v5 bridges.

No case result, hidden source witness or stored label is supplied to downward
compilation. The complete required source game data is present in its T08 public
prompt, in a constrained JSON-bearing textual grammar. A game may have more
than two input numbers because the real payoff matrices and parameterized models
need them. This is not yet a natural-language narrative game curriculum.

### Adjacent transformations

| Edge | Program | Evaluates arithmetic? |
|------|---------|-----------------------|
| T08→T07 | Extract game and source data | No |
| T07→T06 | Resolve registered mathematical transformation | No |
| T06→T05 | Construct X/Y symbolic operand expressions | No |
| T05→T04 | Bind original and transformed semantic roles | No |
| T04→T03 | Apply transformed game oracle and resolve Y | Yes, local |
| T03→T02 | Evaluate original value and resolve X | Yes, local |
| T02→T01 | Introduce register representation | No |
| T01→T00 | Reveal explicit SUBTRACT operation | No |
| T00→Z | Execute SUBTRACT, then independently check entire game oracle | Yes, at finish |

All 9 prompts, all 8 edge records, full oracle result, source split and operator
requirements are stored in SQLite. Every `PASS` is replayed in verification.
A changed stored prompt, operator, game rule, or oracle value fails verification.

## How to run

Apply the **source-only** v5 patch rooted at `main_computer_test/` after v4. It
modifies no active training or checkpoint. Run from the `main_computer` root:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& $py -m unittest `
  tests.test_nanojev_game_ladder_full_catalog `
  tests.test_nanojev_game_ladder_state_machine `
  tests.test_nanojev_game_ladder_edge_programs `
  tests.test_nanojev_game_ladder_bidirectional_proof `
  tests.test_nanojev_game_math_smoke `
  tests.test_nanojev_game_transformation_library -v

& $py -m tools.nanojev_game_math_smoke --generate --verify-all --report
```

The output directory is newly versioned as
`runtime/game-math-source-smoke-v5/`. Previous results are not overwritten.
The main DB adds `full_ladder_v5_cases`, `full_ladder_v5_tiers`,
`full_ladder_v5_edges`, `full_ladder_v5_programs`, and
`full_ladder_v5_game_coverage`.

A separate **proof-only** export
`full_ladder_task_bank.sqlite3` contains the full tier population. It is not
registered into the live trainer. The generator also writes
`full_ladder_coverage.json`, `full_ladder_state_proof.json`, and hashes each
source/generated artifact into the manifest.

Inspect one real ladder:

```powershell
$db = 'runtime/game-math-source-smoke-v5/game_transformations.sqlite3'
$case = & $py -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute("SELECT case_id FROM full_ladder_v5_cases ORDER BY case_id LIMIT 1").fetchone()[0])' $db

& $py -m tools.nanojev_game_ladder_full_catalog --database $db --case $case
```

Browse coverage for all 231 catalog entries in `full_ladder_coverage.json`.
Entries flagged `catalog_only_no_oracle` are requirements/backlog, not learned
or executable game representations.

## Smoke results in the source build

- 57 regression tests passed (including 5 new v5 tests).
- Default generation: 2,096 source cases, 18,864 tier prompts, 16,768
  state-threaded edges; 33 game models covered.
- 196 catalog-only game definitions explicitly blocked.
- 105 mathematical source definitions preserved and original 2,096 legacy
  transformation cases independently verified.
- Three earlier semantic decision pilots and their 72 scenario proofs remain
  separate and continue to pass.

## Follow-on requirements

1. Add independent executable oracles for catalog-only game entries. Only then
   can their full source-derived ladders be generated.
2. Extend beyond legacy original-minus-transformed scalar targets to structural
   equilibria, distributions, optimal decisions and action policies.
3. Author game-natural T08 grammars as independently tested extraction programs,
   not merely structured payoff data embedded in prose.
4. Test withheld game families and unseen mathematical transformations before
   training a structural math head. The generated cases alone do not establish
   model learning or universal game-theoretic understanding.
