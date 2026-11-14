# Captain Jacket Matrix database smoke v1

`python tools/nanojev_captain_jacket_database_smoke.py --self-test`

Creates **no trained model**. It does not contact NanoJev, install dependencies, download data, or touch active game processes. Without `--self-test`, it writes a new, non-overwriting dataset directory:

`python tools/nanojev_captain_jacket_database_smoke.py --output-dir runtime/captain_jacket_matrix/v1`

The generator replays the existing **production deterministic boarding encounter** through `space_captain_boarding_commitment_smoke.py`, requiring all of its checks to pass. It imports four distinct recorded observations (boarding eligible, boarders deployed under attack, recovered, abandoned), and **constructs** three time-in-progress observations (deploying, recalling, abandoning). Optional additional examples are explicitly tagged counterfactuals (hull threat and spatial/speed perturbations). The scene IDs and `source_kind` columns preserve these distinctions; constructed examples must never be reported as measured game outcomes.

## Matrix and meaning

Eight provisional, continuous jacket dimensions, each in `[0,1]`: mission commitment, ship preservation, crew loyalty, risk tolerance, initiative, patience, adaptability, and evidence discipline. A jacket is the acting captain's own disposition; it is not another actor's belief about the captain. The mission and observations are separate inputs. Eight named example jackets and seeded varied jackets populate the first corpus. These values are **design seed data**, not empirically validated personality scores.

The legal action catalog follows the bridge captain controls: approach, hold inner (max transporter range minus 450 m), hold outer (minus 100 m), withdraw, coast, initiate boarding (30 s), recall (24 s), abandon (12 s). Withdraw is unavailable while personnel commitments remain unresolved. Initiation requires range and low relative speed; recall additionally requires transporter range. The database models the *commitment state*, not crew combat or personnel transport.

## Files

| File | Purpose |
|---|---|
| `captain-jackets.sqlite` | Versioned source of truth; indexed jackets, dimensions, missions, scenes, actions, cases, option legality/features, preferences, immutable split reserves |
| `rom-{train,dev,test}.jsonl` | Structured fixed-input and fixed-output supervision, with jacket, observation, two legal options, preferred option and action ID; output-side evidence includes both options' provisional utility and all eight feature contributions |
| `raw-{train,dev,test}.jsonl` | Normal text question/completion pairs (`A` or `B`) suitable for a future standard objective |
| `manifest.json` | Counts, generation seed, SHA-256 of all JSONL exports, holdout and labeling rules |

Both exports derive from *the same* labeled SQLite preferences. Every preference produces two examples: `AB` and `BA`; swapping candidate position swaps the target. Pair groups must stay together in every consumer (`groupId`). The train/dev/test split is already reserved at the SQLite level. Both **jacket IDs and exact scene IDs** are disjoint across splits. Since some counterfactual scenes share a parent deterministic observation, these splits are not an independently collected game-trajectory benchmark.

The `preference_reserve` table records train/dev/test membership with independent `train_count` and `eval_count` counters. `CaptainJacketStore.claim_preference(id, 'train'|'eval')` rejects attempts to consume held-out preferences for training or train preferences for evaluation.

## Provisional labels: do not confuse with ground truth

In v1, preferred choices are made by a documented, deterministic **utility-feature scaffold** conditioning mission, ship, crew, risk, initiative, patience, adaptability and evidence dimensions. Candidate features are stored in `case_actions.features_json`, as are the calculated utility scores. Comparisons with absolute utility margin below `--minimum-margin` remain in SQLite as `ambiguous` and are **not exported** as arbitrarily selected winners.

**This is not proof of tactical optimality**, a model-generated label, or a simulation of alternative futures. Only the initial observations come from the existing game runtime. Before supervised or ROM training, review or replace the provisional oracle, especially for mission tradeoffs, and consider an outcome-based teacher that actually executes each possible action. The dataset makes that upgrade possible without changing jacket/scenario/option identities.

## Example SQLite queries

```sql
SELECT key, value FROM meta ORDER BY key;
SELECT split, COUNT(*) FROM cases GROUP BY split;
SELECT source_kind, COUNT(*) FROM scenes GROUP BY source_kind;
SELECT status, COUNT(*) FROM preferences GROUP BY status;
SELECT action_id, COUNT(*) FROM case_actions GROUP BY action_id;
SELECT split, COUNT(*), SUM(train_count), SUM(eval_count)
  FROM preference_reserve GROUP BY split;
```

For a fixed seed, the exported files are byte-for-byte reproducible on the same source snapshot. Running against an existing output directory fails rather than destroying previously reserved material; select a new `--output-dir` to regenerate. The CLI emits a machine-readable JSON manifest of what it actually produced.

The generator and SQLite layer use only Python's standard library plus the repository's existing Node-based deterministic smoke. **Training integrations are deliberately not part of this patch.**
