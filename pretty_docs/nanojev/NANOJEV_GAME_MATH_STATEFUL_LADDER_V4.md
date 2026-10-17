# NanoJev mathematical extraction ladder — v4 stateful execution

## Purpose

The v3 smoke showed all nine game prompts agreed on one frozen target, and
individually replayed v3 DOWN edge programs. But its early transitions called
the full game oracle, counting the entire calculation multiple times. This
was not a proof of **local** mathematical extraction.

V4 keeps v1–v3 source and their existing reports intact. It adds
`tools/nanojev_game_ladder_state_machine.py`, a **new authoritative stateful
edge proof**. Each edge consumes the **output state of the previous edge**,
performs its own extraction, and emits the next state and prompt. DOWN never
receives a case witness, a stored gold label, or a hidden prior solution.

The state contains only the fields already derived:

- `game`, `tier`, `prompt` — public game grammar and current input
- `facts` — T08→T07 extracts public observations
- `rules` — T07→T06 selects registered, game-specific operators
- `expressions`, `OP` — T06→T05 constructs symbolic mathematical expressions
- `Y` — T04→T03 evaluates **only** the tagged right operand
- `X` — T03→T02 evaluates **only** the tagged left operand
- `Z` — calculated in a separate *finalization* step at T00, with an independent
  oracle validation. No state edge calls the full-game oracle.

T05→T04 binds names, T02→T01 establishes registers, and T01→T00 exposes the
operator. These are representation transitions, not purported arithmetic.

For the sample first-price auction:

```
T08  public scenario             -> facts only
T07  rule table                 -> selected rule IDs
T06  named rules               -> symbolic X_EXPR and Y_EXPR; OP=MIDPOINT
T05  named symbolic operands    -> same expressions, explicitly bound
T04  X and Y tagged            -> Y = LOWER_BOUND(HIGH_NEED(LOW_INTELLIGENCE(125)))
T03  X tagged, Y=62.5          -> X = OWN_MEDIUM_NEED(100) = 100
T02  100, 62.5                 -> register bindings
T01  registers                 -> named MIDPOINT operation
T00  OP(X,Y)                   -> 81.25 (final-only execution)
INDEPENDENT AUCTION ORACLE -> confirms assumptions, domain and 81.25
```

Each v4 edge persists its `input_state_json`, `output_state_json`,
`local_steps_json`, plus a record digest in `ladder_v4_state_edges`. The smoke
replays the entire state chain from the public T08 prompt, compares every
state and mathematical substep, validates the source-authored edge definitions,
and rejects tampering. `ladder_v4_state_cases` stores the starting prompt,
final state, and independently computed oracle result. `ladder_v4_programs`
records source-defined edge descriptions and implementation hashes.

**One frozen case is an integration proof, not a population training sample.**
Across the three pilot games the v4 test generates 72 cases with 576 stateful
adjacent transitions. It also tests an unseen auction input, operator blocking
at early tiers, corrupted state, corrupted DB traces, and an auction input
outside the proven MIDPOINT-reduction domain.

## Apply and run

Apply v4 **after v3**; source-only ZIP rooted `main_computer_test/`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest `
    tests.test_nanojev_game_ladder_state_machine `
    tests.test_nanojev_game_ladder_edge_programs `
    tests.test_nanojev_game_ladder_bidirectional_proof `
    tests.test_nanojev_game_math_smoke `
    tests.test_nanojev_game_transformation_library -v

& $py -m tools.nanojev_game_math_smoke --generate --verify-all --report

& $py -m tools.nanojev_game_ladder_state_machine `
    --database runtime/game-math-source-smoke-v4/game_transformations.sqlite3 `
    --case first_price_captain_uncertainty:case-00
```

The generator writes **new** output under
`runtime/game-math-source-smoke-v4/`; v3 databases and all training
checkpoints remain untouched. The ZIP does **not** contain generated SQLite
binaries. `ladder_state_chain_proof.json` summarizes the new v4 proof, and
`manifest.json` includes the hashes of the generator's source files.

## Limitations and scope

- The grammar is intentionally constrained and cannot parse arbitrary natural
  language or prove a trained model's competence.
- The auction's opponent distribution and need modifiers are **synthetic,
  frozen assumptions**, not empirical behavior or a Bayesian equilibrium.
- The mathematical shortcut `MIDPOINT` is valid for the checked **interior
  optimum** only. The independent auction oracle rejects inputs outside it.
- The real mathematical substeps of the Y tag are still grouped inside
  T04→T03; v4 records each separately. A future microtier curriculum may
  distribute these substeps across additional training rungs.
- Upward story reconstruction remains witness-backed in v3; the new
  incremental state proof is downward-only. This is intentional: numerical
  reductions do not uniquely determine the original story.
- These cases remain `proof_only`. No model is trained and no checkpoint or
  active `TrainingTaskStore` is modified.
