# Gamer v1 — next test: sparse numerical math-prompt tables

> **Implementation update — Gamer v1 v6:** The initial executable 33-game **best-response + scalar-value dataset generator** is now included in `tools.nanojev_gamer_v1_pairs` and exposed as `python -m tools.nanojev_gamer_v1_handoff prepare --output RUN`. It constructs ordinary-English *08 prompts and sparse numeric operation tables, evaluates the table factors generically, and independently checks the source game operators/oracles. The initial 594-case deterministic generation and local tests passed. **This is an implemented subset of the full test below, not proof of complete mathematical-objective coverage or minimum-table compression.** Mixed Nash probabilities and all richer semantic objectives from the full V4 test, as well as comprehensive byte-accurate compression comparisons against naive encodings, are still unverified. Full release/promotion remains blocked by those gates; bounded LoRA experiments can run in an isolated output with explicit `--allow-experimental`. See `GAMER_V1_GENERATED_SPARSE_TABLE_DATASET_V6.md` for concrete commands and measured scope.

**Original v4 scope (2026-11-03): DOCUMENTED FULL EXPERIMENT; NOT YET FULLY EXECUTED.** This is the **next test**, before building a broad `*08 -> math_prompt` corpus or starting LoRA. The aim is to learn whether the 33 verified game models can be represented faithfully by **a small number of reusable tables containing the actual numerical factors**, rather than a separate bespoke math description for each game.

## Governing hypothesis and exact boundaries

One source-verified game scenario becomes (a) an original human-readable *08 question; (b) a **readable, populated mathematical table or small set of linked tables** showing numbers, action labels, operation order, units and what remains to calculate; and (c) an independent, offline oracle answer. The math target still uses ordinary English to say what is being solved. It is not a new macro language, a checkmark matrix, or a completed numeric answer.

**The symbol `–` means a factor/field is INAPPLICABLE or absent from that particular term; `0` is a genuine numeric zero.** Never silently replace an absent probability, cost, discount, opponent payoff or constraint with zero. Each table template defines explicitly how an absent *optional* term is skipped. A missing *required* term means the representation is invalid, not `–`-as-a-guess. All signed numbers and subtractions must retain their signs and parentheses; exact rational values remain exact.

The **three table families below are candidate hypotheses**. They are not yet proven sufficient for all 33. Add a fourth family only if the source math demonstrably cannot be represented by these three without hiding a game-specific formula. Record that as a finding, not as a silent success. The table may have several rows per game/scenario when necessary; a game may legitimately use linked tables.

The 33 are **14 verified 2x2 matrix game models and 19 verified scalar models** from `tools/nanojev_game_transformation_library.py` and `tools/nanojev_game_native_objectives_v6.py`. Only source-verified objectives count: matrix best responses against each opponent action; mixed-strategy probabilities where a valid interior solution exists; and each of the 19 scalar game values. Do not turn 196 catalog-only or two provisional cases into oracle gold. The source matrices are parameterized examples, not universal payoffs for every game bearing that name.

## Candidate table A — action payoff factors

For a 2x2 matrix game store row actions and column actions in a definite order, the four row-player payoffs, and a **complete** method for obtaining the four column-player payoffs. The source v6 matrices yield this representative table (numbers are source-defined examples):

| Game | Row actions (0,1) | Column actions (0,1) | Row payoff (0,0) | (0,1) | (1,0) | (1,1) | Column payoff representation |
|---|---|---|---:|---:|---:|---:|---|
| Prisoner's Dilemma | Cooperate, Defect | Cooperate, Defect | 3 | 0 | 5 | 1 | transpose row matrix |
| Hawk–Dove | Hawk, Dove | Hawk, Dove | -2 | 6 | 0 | 3 | transpose row matrix |
| Matching Pennies | Heads, Tails | Heads, Tails | 1 | -1 | -1 | 1 | negative row matrix |
| Battle of the Sexes | Opera, Football | Opera, Football | 3 | 0 | 0 | 2 | explicit: 2, 0, 0, 3 |
| Captain Hull Preservation | Intercept, Evade | Raid, Withdraw | 3 | -2 | -4 | 2 | explicit: -2, 0, 3, 2 |

`Transpose` means `u_column(i,j)=u_row(j,i)`; `negative` means `u_column(i,j)=-u_row(i,j)`; `explicit` stores four additional numbers in the stated action order. For source-defined best response against column action 0, compare row-payoff cells `(0,0)` and `(1,0)`; for column action 1 compare `(0,1)` and `(1,1)`. For mixed equilibrium, preserve both players' matrices and the game's source-verified interior-equilibrium conditions. Merely storing four row-payoff numbers is **not** enough for every equilibrium objective.

**Human-readable target illustration, Hawk–Dove:** “The opponent chooses Hawk. The row player can choose Hawk or Dove. Read the Hawk column in the payoff table: choosing Hawk gives -2 and choosing Dove gives 0. Compare these payoffs and identify the better action.” The target does **not** append the selected action or numerical difference.

## Candidate table B — signed arithmetic factors

Try the shared structure `start + add - deduct_1 - deduct_2 + rate × quantity`, skipping whole optional terms marked `–`; a rate is invalid without its quantity. The following are actual baseline parameter values from the source scalar models, not invented game constants:

| Scalar game | Start | Add | Deduct 1 | Deduct 2 | Rate | Quantity |
|---|---:|---:|---:|---:|---:|---:|
| Colonel Blotto remaining | 30 | – | 8 | 9 | – | – |
| Pursuit Evasion gap | 15 | 5 | 7 | – | – | – |
| Second Price Auction utility | 30 | – | 20 | – | – | – |
| Coalition Formation surplus | 22 | – | 8 | 11 | – | – |
| Captain Fuel Reserve | 100 | – | 20 | 35 | – | – |
| Captain Relative Navigation | 18 | – | -9 | – | – | – |
| Routing Game delay | 5 | – | – | – | 0.4 | 10 |
| Population Congestion | 12 | – | – | – | -0.5 | 10 |

For example, “In the routing game, start with base delay 5; add the congestion coefficient 0.4 multiplied by flow 10. Calculate the resulting delay.” For relative navigation, the intended operation is `18 - (-9)`, not `18 - 9`; signs must survive reconstruction. The **value/result column is deliberately absent from this Stage-1 table**. The oracle computes it offline to test fidelity.

The other scalar forms include ratios, Bayesian normalization, discounted series, squared values, expected outcomes and minimax. They are NOT claimed to fit table B merely because all arithmetic can eventually be flattened into a long formula. Test the ability to reuse generic factors and links; do not smuggle a bespoke per-game expression into an opaque cell.

## Candidate table C — weighted outcome / conditional transition factors

Test whether probabilities, immediate rewards, conditional survival/failure and discounts can share a branch table, with one row per legitimate outcome. The shared calculation is an immediate value plus the **sum of branch contributions**. Each contribution multiplies its event probability, any applicable conditional factor, outcome value and discount. Missing optional discount or conditional factor is omitted, **not set to numeric zero**.

| Source case | Branch | Event probability | Blocking / detection probability | Outcome value | Discount | Immediate reward |
|---|---|---:|---:|---:|---:|---:|
| Markov Game | Good future state | 0.4 | – | 7 | 0.8 | 2 (once) |
| Markov Game | Bad future state | 0.6 | – | 1 | 0.8 | – |
| Inspection expected loss | Attack goes undetected | 0.5 | 0.6 | 100 damage | – | – |

For Markov, use `2 + 0.8 × (0.4 × 7 + 0.6 × 1)`. The immediate reward of 2 is counted **once**, not once per branch. For Inspection, the source expression is `0.5 × (1 - 0.6) × 100`; the blocking/detection column is used as the complement `1-p` **only when the applicable rule is explicit**. Expected loss is positive damage in the source objective; do not silently turn it into negative utility. These examples display **unsolved operations** and numerical inputs, not completed branch results.

Bayesian normalization might require a reusable *division of weighted totals*; a minimax objective might require selecting a maximum; a Cournot objective might require a difference divided by a product. Whether those can be represented by connections of the three families **without an extra generic table type** is precisely what the test must establish.

## What the test actually does (small, source-first)

1. **Inventory 33/33:** load the 14 matrix and 19 scalar source models and their valid verified objectives. Use one source baseline instance per model first; include row-best-responses against **both** opponent actions and only valid mixed-equilibrium objectives. Exclude unsupported tasks explicitly.
2. **Populate numerical cells:** construct the candidate table(s) with actual source parameters, action names and exact operation/connection rules. Do not use checkmarks or per-game English blobs as substitute data. Put `–` in genuinely inapplicable cells and retain numerical `0` as data. Source provenance, role, units and output type must be recorded.
3. **Reconstruct:** independently turn the populated tables into the source mathematical expression or typed decision objective, and render one **English-plus-spelled-out-math, uncomputed** target prompt. The reconstruction may use a small shared inventory of ordinary operations `+`, `−`, `×`, `÷`, `sum`, `max`, comparison and explicit conditional probability; any additional operation is recorded and justified.
4. **Compare against oracle:** for each model/objective, compare the reconstructed result and action semantics against the actual source oracle, including signs, constraints, mixed-equilibrium validity and exact fractions/declared tolerance. Check the **expression's structural/general mathematical equivalence** as well; equality on a single instance cannot certify a formula. Perturb with a second deterministic parameter set and a legal zero-line/near-zero probe wherever applicable.
5. **Measure compression:** compare the three-family representation with a naive complete per-game formula/tensor representation. Report **number of table families; shared factors/rules; non-dash numerical cells; rules and action labels stored; total serialized bytes; per-model exceptions; and exact reconstruction coverage**. Do not claim 39% total compression based solely on counting payoff entries while excluding rule/label costs.
6. **Decide:** publish the 33-row coverage matrix and one report containing passing games, failing objective(s), new required table families (if any), and the actual cost of the factorization. Only if this passes should we settle a table-based supervised math-prompt target and update the LoRA dataset generator.

**Primary correctness gate:** Every attempted verified objective must reconstruct correctly from table values and **shared, inspectable composition rules**, with zero oracle mismatches; 33/33 verified games must be attempted and reported, including failures. A game requiring hidden game-specific arithmetic, guessed input values, lost action labels, unsupported mixed equilibrium or a changed objective **fails** the three-table hypothesis. There is no partial-success spin: report exactly which games and objectives did not fit. The question of how much compression is useful is answered by **measured total representation cost**, not a presumed target ratio. This experiment may conclude that 3 tables are insufficient.

**Stop conditions:** no neural training, `to_numerical` optimization, model promotion or release status change as part of this initial factorization test. Do not mutate game oracles, historical source banks, checkpoints or production runtime. The three tables are candidate representations **until source replay proves them**.

## Mandatory existing input contract (unchanged)

The **original human-readable *08 prompt** is the input for both learned paths. The sparse numerical tables, like any math-prompt text generated from them, are **only an offline teacher target/diagnostic for the Qwen LoRA path**. `to_numerical` is trained on the oracle numerical answer using frozen Qwen+LoRA hidden activations of the **original *08 prompt**. It must **never** read, embed, encode, or depend on populated tables, oracle math prompts or generated math prompts as its input. Preserve the same original-prompt fingerprint on all Wide capture/training/inference paths.

## Evidence / source status

Verified definitions inspected: `tools/nanojev_game_transformation_library.py` (`MATRICES`, `SCALAR_MODELS`, `scalar_oracle`) and `tools/nanojev_game_native_objectives_v6.py` (`SCALAR_FORMULAS`, matrix mixed-strategy conditions). The table examples above use the source baseline entries. The factorization and compression findings are **not yet measured**. This document is the test specification, not the test result.
