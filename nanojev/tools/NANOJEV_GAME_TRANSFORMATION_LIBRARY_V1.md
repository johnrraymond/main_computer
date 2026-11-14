# NanoJev — Game / Mathematical Transformation Library v1

## Deliverable and limits

An offline, deterministic, SQLite-backed *game as training unit* library, grounded in
existing NanoJev code conventions. Its goals are (1) a broad hierarchical catalog,
(2) verified numerical and structural transformations where an executable oracle exists,
(3) a single persisted training objective and a declared **fixed distribution** rather
than step-number milestones, (4) sealed train/dev/test cases and alternate presentations,
(5) a `TrainingTaskStore`-compatible export, and (6) a positive-and-negative smoke evaluator.

**Not a trained model or a complete implementation of every game:** 196 of the 229 catalog
entries are *catalog_only*. Only 33 game entries have checked matrix or scalar oracles.
The catalog-only games are **never falsely emitted as supervised gold**. Sequential,
Bayesian, stochastic and multiplayer games beyond the scalar exemplars require their
specific transition, belief or equilibrium oracles before adding supervised samples.

The existing V3 trainer is specialized to X/Y operand regression and operation classification.
`nanojev_game_tasks.sqlite3` gives its data-store family compatible X/Y bridge examples,
but **does not** automatically register an additional `--training-mix` stage or train
full matrix/structural targets in V3. Training those targets requires a new structured
transformation head/adapter. No live `latest.pt` checkpoint is changed by this package.

## Package structure

- `tools/nanojev_game_transformation_library.py` — deterministic builder, authoritative
  schema, catalog, dictionary, 25 transformation definitions, checked generators,
  stable group splitting, integrity validation, JSONL and TrainingTaskStore exports.
- `tools/nanojev_game_transform_eval.py` — scoring numerical 5% and 10% thresholds,
  operator exact match, transformed tensor/value and equilibria exact match, and strict
  **three-presentation** consistency. An oracle-positive/corrupted-negative smoke.
- `tools/nanojev_game_transformation_schema.sql` — full DDL for inspection.
- `tools/nanojev_game_training_objective.json` — readable mirror of the same objective
  stored in SQL table `training_objectives`.
- `tests/test_nanojev_game_transformation_library.py` — 22 offline unit/integration tests.
- `runtime/game-transformation-library/game_transformations.sqlite3` — populated DB,
  **generated during installation**, not bundled as binary in this source patch.
- `runtime/game-transformation-library/nanojev_game_tasks.sqlite3` — compatible task store,
  **generated during installation**.
- `runtime/game-transformation-library/jsonl/{train,dev,test}.jsonl` — presentation-rich
  structured training/evaluation samples, **generated during installation**.

## Coverage in this release

- 23 mathematical families including normal form, coordination, commons, population,
  temporal, bargaining, private information, reputation, stochastic/Markov, differential,
  contests, security, pursuit, congestion, auctions, coalition, Byzantine consensus,
  deception/hypergames, mean-field, markets, robust games, Space Captain, board/card games.
- 229 game concepts; 73 term/symbol/semantic-role dictionary entries;
  25 transformation definitions; 27 annotated mathematical relation edges.
- 528 parameterized game instances; **2,096 oracle-verified transformations**;
  **6,288 training presentations** (forward, reordered, symbolic).
- Example matrix transformations: swap players, permute row/column action labels,
  independent positive affine payoff changes, common payoff shift, change one payoff,
  negate one player's payoff, and compose a transformation with its inverse.
- Checked scalar transformations: Hawk–Dove mixed proportion `V/C` for `0<V<C`,
  Bayesian posterior, discounted value, bargaining division, routing delay,
  Cournot quantity, attrition power difference, pursuit gap, Captain signed relative
  position and fuel reserve, inspection loss, Blotto budget remainder, quorum margin,
  etc. All oracle checks are repeated when verifying the DB.

### Canonical relationship: Hawk–Dove and Chicken

Both have two anti-coordination pure Nash equilibria under the stated payoff ordering.
Hawk–Dove with value `V=6`, fight cost `C=10` has matrix

```
                     Opponent Hawk      Opponent Dove
Captain Hawk             (-2,-2)            (6,0)
Captain Dove               (0,6)            (3,3)
```

If `T` independently transforms row payoff to `2u+3` and column payoff to `3v-2`,
its resulting matrix has numerically changed payoffs but the corresponding pure Nash
profiles remain `(Hawk,Dove)` and `(Dove,Hawk)`. `T` is a strategically equivalent
positive-affine transformation, not a claim that all ordinally related games
have identical numeric payoffs. By contrast, altering a single payoff or turning
one player's utility negative can change equilibrium; the oracle recomputes it.

### Two distinct types of problem

1. **Same game, different representation**: order of narration, symbolic math,
   action label permutations, player permutation, independently positive affine
   transformation with the accompanying correct payoff map. The model predicts
   both the transformed numerical values and the invariant strategic properties.
2. **New game created by changing the game**: modify a payoff, resource constraint,
   opponent information, available actions, coalition, objective/jacket or physical
   reachability. The model must predict *what actually changes*, not pretend the
   equilibrium is invariant. Only specific matrix payoff modifications and scalar
   parameter counterfactuals have executable truth in this release; the remainder
   exist in the SQL transformation grammar as catalog-only operators.

## Exactly how to install

From the repository root `C:\Users\subsi\main_computer`, use the installed
`new_patch.py` to preview and apply the source-only patch ZIP (it contains exactly the
six source, schema, objective, test, and documentation files, no generated binaries).

```powershell
python new_patch.py .\nanojev_game_transformation_library_mc3_patch_v1.zip --dry-run
python new_patch.py .\nanojev_game_transformation_library_mc3_patch_v1.zip
```

Create both populated SQLite databases and the train/dev/test JSONL exports with
this deterministic **one-time build** command (it refuses to overwrite an
existing database):

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
$db = '.\runtime\game-transformation-library\game_transformations.sqlite3'
& $py -m tools.nanojev_game_transformation_library `
  --output $db `
  --seed 20261015 `
  --instances-per-game 16 `
  --export-jsonl .\runtime\game-transformation-library\jsonl `
  --export-task-bank .\runtime\game-transformation-library\nanojev_game_tasks.sqlite3 `
  --smoke

& $py -m tools.nanojev_game_transformation_library --verify --output $db
& $py -m tools.nanojev_game_transform_eval --database $db --split dev --smoke
& $py -m unittest tests.test_nanojev_game_transformation_library -v
```

The main game DB will contain 23 families, 229 entries, the dictionary and full
training objective, 2,096 verified transformation cases and 6,288 presentations.
The companion task store supports the repository's existing `TrainingTaskStore`
contract; neither database automatically adds a new structured transformation head
or alters the current NanoJev checkpoint. If these paths exist, archive or choose a
fresh destination before building.

For a *fresh* corpus at a new destination with a different seed:

```powershell
& $py -m tools.nanojev_game_transformation_library `
  --output .\runtime\game-transformation-library-alt\games.sqlite3 `
  --seed 20261016 `
  --instances-per-game 16 `
  --export-jsonl .\runtime\game-transformation-library-alt\jsonl `
  --export-task-bank .\runtime\game-transformation-library-alt\nanojev_game_tasks.sqlite3 `
  --smoke
```

The builder **refuses to overwrite an existing database**. All variants and
counterfactuals generated from one base instance share one reserved split.
This prevents orientation and counterfactual leakage from train to dev/test.
A stricter unseen-**family** holdout must be added when a sufficiently broad set
of family-specific oracles exists.

## SQL queries

```sql
SELECT f.label, COUNT(*) AS games
FROM games g JOIN families f USING(family_id)
GROUP BY f.family_id ORDER BY games DESC;

SELECT game_id, name, math, status FROM games
WHERE family_id='captain' OR game_id LIKE '%hawk%';

SELECT term_id,definition,math FROM dictionary WHERE role='transform';

SELECT op_id,kind,definition,claim,status FROM transformations ORDER BY kind,op_id;

SELECT objective_id,full_objective_json FROM training_objectives;

SELECT family_id,examples_per_batch,purpose
FROM training_mix WHERE mix_id='game_transform_bridge_v1';

SELECT c.op_id,c.target_json,p.orientation,p.prompt
FROM transformation_cases c JOIN presentations p USING(case_id)
WHERE c.op_id='positive_affine' LIMIT 6;

SELECT split,COUNT(*) FROM transformation_cases GROUP BY split;
```

## Training objective and readiness

The entire *full objective* is persisted in `training_objectives.full_objective_json`.
It separately describes the current pretrained and continued numeric head, the
required new structural transformation head, targets/losses, semantics, validation,
family generalization and promotion criteria. The `training_mix` table is a
16-position *family composition*, explicitly not a cycle/step progression.

For verified cases, the next structured head should be evaluated on:

- operator classification; X/Y 1%, 5%, and 10% accuracy;
- exact post-transform scalar or matrix target, including correctly re-evaluated
  pure Nash equilibria;
- strategic equivalence or difference under the stipulated operator;
- strict three-presentation correctness and game-family/operator breakdown;
- new parameter instances and new narrative frames, with later unseen families.

Candidate competence gates are listed as desired measurements, **not achieved
results**. The evaluator smoke's `100%` score reflects oracle predictions fed
into the evaluator, not NanoJev predictions. No GPU model is loaded by the smoke.

## Catalog foundations

This is an original practical ontology drawing on the large-game-family approach
of GAMUT, finite strategic/extensive equilibrium semantics of Gambit and the
variety of dynamics/information games in OpenSpiel. It is not a copy of all games
or payoff generators from those systems. Pointers are stored in SQL table
`source_references`:

- https://ai.stanford.edu/~eugnud/papers/gamut.pdf
- https://www.gambit-project.org/
- https://github.com/google-deepmind/open_spiel/blob/master/docs/games.md

The Space Captain and meta-game vocabulary extends the user's earlier project
framework: `G=(N,S,A,T,O,I,U,H)`, game as learning unit; Captain's jacket supplies
priorities, scenario supplies state, rubric yields numerical/structural targets;
players may transform the game's options, constraints, coalitions, incentives,
information or temporal structure. Those changes are represented as first-class
operators; they must not be mistaken for mere changes of wording.
