# NanoJev Captain Numeracy — Pass One

This is an **offline, deterministic SQLite database populator** for the existing
`nanojev_training_task_store.TrainingTaskStore`. It does not train or change any
model and never alters v4 checkpoints. The current curriculum does **not yet**
sample from this bank; trainer integration is a separate cutover.

The task is numeracy only: reconstruct X, Y, and the semantic arithmetic
operation from increasingly weak aids. The result is stored as verified
ground truth for diagnostics. There are no decision, action, legality, jacket,
or preference labels, and no claim that generated descriptions are actual game
simulation observations.

## Run on Windows (repository root, existing `.venv`)

```powershell
python -m tools.nanojev_captain_numeracy_populate --self-test
python -m tools.nanojev_captain_numeracy_populate --preview
python -m tools.nanojev_captain_numeracy_populate `
  --output-dir .\runtime\captain-numeracy-pass1 `
  --train-groups 4096 `
  --dev-groups 512 `
  --predev-groups 512 `
  --test-groups 512 `
  --seed 20261015
python -m tools.nanojev_captain_numeracy_populate `
  --verify-bank .\runtime\captain-numeracy-pass1
python -m unittest discover -s tests -p test_nanojev_captain_numeracy_populate.py -v
```

The output directory must not exist before population. It contains
`training-tasks.sqlite` and `manifest.json` with a database SHA-256 digest,
operation catalog, exact presentation counts, and illustrative prompts. Set
`--include-heldout-eval` only if the original four reserved operations should
appear in predev/test; they never appear in training.

## Database contract

- **Family:** `captain_numeracy_pass1`
- **Canonical group:** unordered numeric pair (both orientations reserved in
  the same split), one concrete X/Y orientation, one source operation.
- **Target:** `{a, b, x, y, operation, semanticOperation, result}`.
- **Provenance:** distinct semantic roles, story frame identifier, seed,
  operation holdout flag, source and pair fingerprint.
- **Presentations:** 15 representations × 2 narrative/line orders = 30 samples
  per group. Both orders preserve the same operand targets.
- **Oracle:** exact integer arithmetic, including integer-valued booleans and
  half-integer means. No independent JSON result/FINAL head is updated.
- **Split:** immutable group partition into train/dev/predev/test. The same
  unordered pair cannot appear elsewhere in the corpus, even with operands
  exchanged.

Each presentation's `sample_json` is a dictionary containing `prompt`,
`variant`, `order`, and `semanticFrameId`. **Only `prompt` goes to the
backbone.** Targets remain in `task_groups.target_json`, never inside the raw
prompt. To retrieve examples:

```python
from tools.nanojev_training_task_store import TrainingTaskStore
from pathlib import Path
with TrainingTaskStore(Path('runtime/captain-numeracy-pass1/training-tasks.sqlite')) as db:
    for row in db.iter_examples('train', 'story_semantic_no_op__forward',
                                family='captain_numeracy_pass1'):
        prompt = row['example']['prompt']
        x, y = row['target']['x'], row['target']['y']
        operation = row['target']['operation']
        break
```

## Representations

Baseline controls: `registered_logbar`, `plain_expression`.

Scaffolded stories: `story_full_logbar`, `story_one_logbar_x`,
`story_one_logbar_y`, `story_numeric_registers`, `story_numeric_no_op`.

Partial semantic roles: `story_numeric_x_semantic_y`,
`story_semantic_x_numeric_y`, `story_semantic_with_op`,
`story_semantic_no_op`, `story_one_semantic_x`, `story_one_semantic_y`,
`story_op_only`.

Unscaffolded: `story_raw`.

Suffix every name with `__forward` or `__reordered`. The latter reorders
operand mentions without changing their roles or target values.

**Difficulty order is a hypothesis, not a guarantee.** Probe each stage and
operation-input policy independently. The populator never supplies or changes
the decoder's operation-ID side channel; the trainer must explicitly select a
neutral operation ID where appropriate to prevent leakage. Measure the
numerical error cliff separately for raw plain expressions and natural English.

The source arithmetic stream covers signed values from -2,000,000 through
2,000,000. The captain-domain stories use signed telemetry, balance and
navigation values rather than physically impossible negative fuel volumes.
Story frames are handcrafted and remain finitely templated; broader natural
language generation is a later pass, not claimed here.
