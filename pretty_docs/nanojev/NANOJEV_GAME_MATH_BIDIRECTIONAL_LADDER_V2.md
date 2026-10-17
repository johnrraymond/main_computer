# NanoJev Game–Math Ladder v2 — bidirectional extraction proof

## Scope and result

The v1 source-generated smoke **did not prove** that its nine prompts could be independently reduced to the same mathematical answer; it primarily checked that the stored target was identical. This extension adds a prompt compiler, source-witness-backed reconstruction, real adjacent-step traversals, multiple parameterized cases, persistence tests and deliberate corruption probes. It **does not** train or change the NanoJev head.

- Three game families, **24 frozen cases per family** (72 total).
- **648** complete T00–T08 presentations; each independently compiled by a constrained source-owned grammar, then executed by the operator registry.
- **576** adjacent edges traversed T00→T08 and T08→T00 using stored scenario witnesses.
- Binary targets include both 0 and 1 for each decision game; first-price auctions produce distinct numeric bids.
- The verified legacy catalog (2,096 cases with default settings), mathematical definitions and active task banks are preserved.
- Extra mathematical definitions `HAWK_VS_HAWK` and `DOVE_VS_HAWK` cover the opponent-Hawk counterfactuals.

**The precise proof claim:** For every generated prompt in the **supported constrained grammar**, the grammar/parser and versioned mathematical oracle reconstruct the same `(X,Y,OP,Z)`, and a persisted case witness can regenerate the original prompt at every tier. This is **not a proof of general language understanding** or of empirical correctness of synthetic opponent-behavior models. `proof_only` cases are not shipped into the live training mix.

## Why the witness is needed

From `T08`, the frozen rules and observations compile down to the explicit `T00` operands. In the other direction, `T00` is generally lossy: two Hawk–Dove stories with the same prize and different conflict costs can have identical T00 outcomes when the rival plays Dove. The original story cannot be recovered uniquely from X,Y,OP. **Ascending therefore requires a sealed, versioned scenario witness.** The database stores the witness, math trace, each tier compilation, and each adjacent up/down verification. The smoke must fail if any expected prompt, operand, operator, execution or witness is modified independently.

## Source-only patch installation

Apply this patch **after** `nanojev_game_math_source_smoke_v1_patch.zip` using the repository's usual rooted ZIP patch procedure. The patch contains source and tests only: no generated SQLite data or checkpoint. No direct active-checkpoint or trainer changes.

From `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_game_ladder_bidirectional_proof `
    tests.test_nanojev_game_math_smoke `
    tests.test_nanojev_game_transformation_library -v
& $py -m tools.nanojev_game_math_smoke --generate --verify-all --report
```

By default v2 writes into a **new** directory, `runtime/game-math-source-smoke-v2`, leaving v1 untouched. The same fail-closed safe-replace policy still applies for reruns.

To visually inspect the entire auction walk up and down:

```powershell
& $py -m tools.nanojev_game_ladder_proof `
    --database runtime/game-math-source-smoke-v2/game_transformations.sqlite3 `
    --case first_price_captain_uncertainty:case-00 --direction both
```

For Hawk–Dove and the provisional reputation rubric use `hawk_dove:case-01` and `betrayal_loyalty_synthetic:case-00`, respectively. Add `--json` for machine-readable output.

## Generated database artifacts

Original generated files remain in place. The v2 `game_transformations.sqlite3` additionally contains:

- `ladder_proof_cases`: original scenario witness, expected derived operands and intermediate math trace.
- `ladder_proof_tiers`: generated prompt, independently recompiled `(X,Y,OP,Z)`, and tier-level proof.
- `ladder_proof_edges`: adjacent up/down evidence, witness-based reconstruction fingerprint, extracted result.
- `game_math_requirements` links for all newly exercised math operators.

Additional generated files: `ladder_roundtrip_proof.json` and `ladder_bidirectional_task_bank.sqlite3` (all rows tagged `proof_only`). Source checksums and generated-file checksums are included in the v2 manifest.

## Interpretive limitations

1. Hawk–Dove's two-row matrix is a standard opponent-contingent payoff structure under the stated symmetric contest assumptions; the resulting action depends on the rival's observed action. The rival is not secretly inferred in these examples.
2. The Betrayal–Loyalty case is a **synthetic** H/M/L reputation rubric, not independently validated strategic equilibrium mathematics.
3. The first-price auction uses frozen **synthetic** opponent-bid assumptions. Its MIDPOINT reduction is verified only in the declared interior case `lower_rival_bid < our_value <= upper_rival_bid` and does not claim to solve general auctions.
4. T08 is a template-defined natural-language question, and the smoke's proof applies to that grammar. Unconstrained language paraphrases require separate evaluation.
5. No checkpoint was evaluated and the `proof_only` task bank is **not training-ready by virtue of passing these proofs**. A training population and appropriate splits must be built separately.

## Fail-closed proof tests

The tests reject a forged operator in T00, changed auction mechanism at T08, conflicting payoff modifers, corrupted persisted prompts, mathematical oracle drift, and unprotected output folder replacement. Mathematical execution uses exact `Fraction` outputs, not float coercion. The smoke only writes its own output folder and does not modify live model weights.
