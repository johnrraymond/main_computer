# Deterministic Hawk–Dove continuation head: executable prototype

This patch is a **standalone, stdlib-only, source-generated prototype**, not a trained NanoJev model, and has not been merged into the user's existing repository. It implements the specific nested continuation expression developed in the conversation and generates numerically checked learning subunits.

## Model

The four payoff tags `HAWK_HAWK_PAYOFF`, `HAWK_DOVE_PAYOFF`, `DOVE_HAWK_PAYOFF`, `DOVE_DOVE_PAYOFF` are **immutable semantic numerical bindings**. They are supplied to the teacher in a separate JSON manifest. Their numbers are never printed into the model-facing prompt. Changing any binding's value or binding version changes the SHA-256 identity; use `--expected-binding-hash` to reject such changes instead of silently swapping learned semantics.

The canonical mathematical program is:

```lisp
Add(
    HAWK_HAWK_PAYOFF(x,
        HAWK_DOVE_PAYOFF(y,
            With(
                ARGMAX(
                    Add(
                        DOVE_HAWK_PAYOFF(x,
                            DOVE_DOVE_PAYOFF(y, END())
                        )
                    )
                )
            )
        )
    )
)
```

**Exact continuation semantics:**

- Each payoff application `PAYOFF(x|y, continuation)` contributes the input belief times the **fixed** hidden tag value to the enclosing addition scope; its second argument is the continuation, not a numerical operand.
- The outer `Add` sums the Hawk-row contributions and stops at `With`.
- `With` retains that completed sum while `ARGMAX` obtains the second sum.
- The inner `Add` sums the Dove-row contributions and stops at `END`.
- `ARGMAX` compares the two completed totals, returning `HAWK`, `DOVE`, or `TIE`.
- Each of the four payoff tags occurs **once** in the complete canonical expression; X and Y are referenced twice each. No `BIND` is emitted into prompts.
- The two `Add` nodes are distinct *continuation-scoped* operations. They both happen to use numeric addition in this game, but their termini/relational roles differ.

Equivalently, with hidden constants `HH`, `HD`, `DH`, `DD`:

```text
Hawk expected = x * HH + y * HD
Dove expected = x * DH + y * DD
Decision      = argmax(Hawk expected, Dove expected)
```

The example manifest uses a consistent standard Hawk–Dove fixture with `V=3` (resource value), `C=5` (fight cost): `HH=(3-5)/2=-1`, `HD=3`, `DH=0`, `DD=3/2`. These are **illustrative**, not learned values extracted from your actual database or checkpoint. To work with the actual head, export its verified numeric binding manifest, preserve its binding version and fingerprint, and connect this teacher's generated prompts and labels to the existing training harness. No claim is made that this prototype loads weights or measures learned model accuracy.

## Run on Windows / PowerShell

Unpack the zip; its root is `main_computer_test/`. In the extracted `main_computer_test` directory:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest discover -s tests -p test_hawk_dove_continuation_head.py -v
& $py -m tools.hawk_dove_head_smoke --manifest examples/hawk_dove_DEMO_ONLY.json --output examples/smoke_probabilities.json
& $py -m tools.hawk_dove_continuation_head --manifest examples/hawk_dove_DEMO_ONLY.json --x 0.2 --y 0.8 --probabilities --level full --write-training examples/units.jsonl --write-trace examples/trace.json
```

The same commands work using `python` on Linux and macOS. The demo bindings do not change across the simulated cases; only X and Y change.

### Belief conventions

`x` and `y` are two exposed, independently configurable **nonnegative weights**. If they represent exclusive, exhaustive probabilities of the opponent's two strategies, add `--probabilities` to enforce `x+y=1`; in that mode only one probability is free. Without that flag they are independent *weights*, not both probabilities. Nonzero positive rescaling preserves ARGMAX but changes numerical totals. The zero-zero case is rejected.

### Generated subunits

For each input pair, the tool emits 12 executable or canonically evaluated training units, in this order:

1. Four **binding** probes (`x=1,y=0` or `x=0,y=1`) learning each tag's fixed numerical contribution.
2. Four **weighted-payoff** probes for the requested two numerical inputs.
3. Two separate **Add-scope** probes: Hawk and Dove.
4. One fully expanded **ARGMAX** case with both expected-payoff targets.
5. One compressed **game-semantics** case with the same numerical targets.

Each JSONL record separates `model_prompt` (safe for the model) from `target` / `hawk_target` / `dove_target` (teacher supervision). `winner_diagnostic` is only a computed diagnostic, matching the previous XY-head design in which the categorical Z output is not directly regressed. You must explicitly map the two payoff totals to your current training head's supervised output columns; the present tool does **not** train its parameters.

To verify an isolated unit, `evaluate_fragment(parse(unit), x, y, bindings)` computes the same value with an `END()` boundary. E.g., `DOVE_DOVE_PAYOFF(y, END())` at `y=0.8` computes `1.2` with the demo bindings.

### Expand, rearrange, collapse

- `--level full` renders the canonical continuation with payoff names but no numerical payoffs.
- `--level rows` collapses the two row sums into `HAWK_EXPECTED_PAYOFF(x,y, continuation)` and `DOVE_EXPECTED_PAYOFF(x,y, continuation)` macros. Their continuation scopes remain unchanged. These macros are parseable and executable: the engine expands them back into the certified original payoff tree before evaluation. **No extra payoff values or X/Y injections are created.**
- `--level game` shows the game identity, the two input numbers and a question. This is a semantic rendering backed by the source AST; unlike Lisp macros, arbitrary English paraphrases cannot be mathematically verified by the parser.
- `--level unitary` strips X/Y from the model-facing text, leaving the game question. **There is not a uniquely determined numerical answer to the bare prompt** unless the model supplies beliefs or receives them via separate runtime context.

The `variant()` function swaps the order of the two terms *within* either `Add` scope, yielding four distinct continuation trees. `symbolic_signature()` certifies exact row/variable/payoff identity; each variant is compared to an independent matrix-vector oracle over 48 nonzero pairs of X/Y. This is an exact structural equivalence argument for this narrow grammar (commutativity of addition inside each row), **not** a claim that arbitrary rewrites, cross-row swaps, or arbitrary English paraphrases are proven equivalent. Scope violations, incorrect belief assignments, missing payoff tags and replaced `With` boundaries are rejected.

### Files

- `tools/hawk_dove_continuation_head.py` — parser, typed AST, continuation oracle, subunit generation, prompt collapse, SHA-256 binding lock, CLI.
- `tools/hawk_dove_head_smoke.py` — multi-input verification and report.
- `tests/test_hawk_dove_continuation_head.py` — standalone unit tests (no pytest required).
- `examples/hawk_dove_DEMO_ONLY.json` — demonstrative payoff manifest **not actual NanoJev bindings**.
- `examples/generated_training_defined_0p2_0p8.jsonl` — deterministic generated teacher training subunits.
- `examples/trace_defined_0p2_0p8.json` — one full execution trace.
- `examples/smoke_probabilities.json` — multiple scenarios, including a numerical tie and a behavioral reversal.

## Integration boundary

Because no fresh `main_computer_test` repo snapshot, current NanoJev checkpoint, or actual semantic-tag binding database was supplied in this exchange, **this is not an in-place alteration to the actual NanoJev trainer**. It is a self-contained implementation and simulated teacher whose immutable demo constants are explicitly marked. Replace the demo manifest only as part of a controlled binding-import/retraining workflow; never silently rebind already learned payoff tokens.

## V2 extension — explicit, versioned definitions of X and Y

**X and Y in the continuation expression are inputs describing the actor's beliefs about the opponent; they are not the X/Y regression output channels of the existing NanoJev head.** The current teacher's supervised game-level outputs are the two expected-payoff totals (Hawk and Dove). The winning action is diagnostic-only.

Previously the meaning of `x` and `y` was inferred from their position. The V2 interpreter explicitly defines their semantic contracts in separate JSON files. **These files contain no numerical payoff bindings.** You can change the text used to describe a belief or choose which of X and Y names each opponent action, without modifying any learned payoff number. If the meaning of a belief changes, issue a new `definition_version` and use the definition fingerprint to avoid mixing training sets unintentionally.

Example `examples/hawk_dove_beliefs_probability.json`:

```json
{
  "definition_version": "hawk-dove-forecast-probabilities-v1",
  "mode": "probabilities",
  "variables": {
    "x": {
      "event": "opponent_hawk",
      "meaning": "your probability that the opponent plays Hawk"
    },
    "y": {
      "event": "opponent_dove",
      "meaning": "your probability that the opponent plays Dove"
    }
  }
}
```

The `event` is a **structural role**, checked by the interpreter, not free-form prose. In the normal mapping, `x` goes with `HAWK_HAWK_PAYOFF` and `DOVE_HAWK_PAYOFF`; `y` goes with `HAWK_DOVE_PAYOFF` and `DOVE_DOVE_PAYOFF`. Both references to a variable share its one definition. The four payoff symbols remain immutable and each occurs exactly once in the full tree.

There are two explicit belief domains:

* `probabilities`: Both inputs lie in `[0,1]` and must satisfy `x + y = 1` (because Hawk and Dove exhaust the opponent's options). This implies only **one independent degree of freedom**; changing X changes Y.
* `weights`: X and Y can vary independently as finite nonnegative values, as long as at least one is positive. They are **unnormalized belief weights**, not two normalized probabilities. The weighted sums are valid; rescaling both by the same positive factor changes their numerical totals, not the winning action.

The same interpreter also supports a *swapped variable naming convention*, demonstrated in `examples/hawk_dove_beliefs_swapped_probability.json`: X names the Dove belief, Y names the Hawk belief. This does not swap game actions or payoff values: it rewrites the **variable references** throughout the tree. With X=0.2, Y=0.8 and the unchanged demonstration payoff bindings:

| Definitions | Hawk expected | Dove expected | Winner |
| --- | ---: | ---: | --- |
| X=opponent Hawk, Y=opponent Dove | 2.2 | 1.2 | Hawk |
| X=opponent Dove, Y=opponent Hawk | -0.2 | 0.3 | Dove |

Do not change the variable/event mapping accidentally. The structural validator rejects a tree whose payoff columns use the wrong named beliefs, and `--expected-belief-definition-hash` can reject changed definitions. Changing the *textual* description changes the definition fingerprint; the interpreter cannot prove the natural-language description is truthful, so the author must keep it consistent with `event`.

The generated model-facing prompt begins with the semantic definitions **once**:

```text
You realize you are playing Hawk–Dove ::
x = 0.2 [your probability that the opponent plays Hawk] ::
y = 0.8 [your probability that the opponent plays Dove] ::
probabilities; x + y = 1 ::
Add(HAWK_HAWK_PAYOFF(x, HAWK_DOVE_PAYOFF(y, With(ARGMAX(Add(DOVE_HAWK_PAYOFF(x, DOVE_DOVE_PAYOFF(y, END())))))))
```

The interpreter preserves the exact expression's parenthesization in actual output; this compact rendering is just a guide. At `--level unitary`, both input values and their explicit definitions disappear from the prompt. Such a bare game question cannot uniquely specify numerical expected payoffs without separately supplied beliefs or an explicitly defined prior.

### Run explicit x/y definitions

From the extracted `main_computer_test/` root:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest discover -s tests -p test_hawk_dove_continuation_head.py -v

& $py -m tools.hawk_dove_continuation_head `
  --manifest examples/hawk_dove_DEMO_ONLY.json `
  --belief-definitions examples/hawk_dove_beliefs_probability.json `
  --x 0.2 --y 0.8 --level full `
  --write-training examples/defined_units.jsonl `
  --write-trace examples/defined_trace.json

& $py -m tools.hawk_dove_head_smoke `
  --manifest examples/hawk_dove_DEMO_ONLY.json `
  --belief-definitions examples/hawk_dove_beliefs_swapped_probability.json
```

To permit separately variable X and Y, use `examples/hawk_dove_beliefs_weights.json`. You can supply your own belief manifest with exactly two variables (`x`, `y`), one for each opponent event. The user-supplied string descriptions are visible in model-facing prompts; hidden numerical payoffs are **not**.

### Testing and integration boundary

V2 adds regression coverage for role mapping, swapped definitions, probability normalization, independent weights, generated model-facing definitions, hashes, unchanged payoff bindings, macro expansion and valid `ARGMAX` results. The deterministic teacher is executable and verified against the independent matrix oracle. **This is not yet a trained NanoJev model**, and the included payoff values are still demonstrative rather than values recovered from the user's learned checkpoint.
