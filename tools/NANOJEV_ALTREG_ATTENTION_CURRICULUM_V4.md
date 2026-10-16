# NanoJev v4 — progressive prompt curriculum (Qwen attention X/Y)

This is a **new training fork**. It **never edits** the existing
`independent_altreg_math_qwen_v3_attention` root, its running `latest.pt`, its
`best.pt`, the frozen Qwen weights, or the parent precision-v2 head.
It loads a single existing attention champion into a separate v4 run root,
starts a **fresh low-rate optimizer** and a new deterministic numeric stream,
and trains the same X/Y sign/exponent/mantissa decoder.
RESULT and FINAL are **not regressed** in this cutover; keep their audit
purely diagnostic.

## Ladder and preservation

There are six ordered forms; `--preview` renders both orientations using
`X=17, Y=-3` so the actual text can be audited before GPU use.

| Tier | Name | Visible supports | Examples |
|---|---|---|---|
| 0 | `registered_logbar` | Both XXXXXX/YYYYYY definitions, two numeric LogBars | Identical to v3 prompt |
| 1 | `one_raw_operand` | One definition, one raw operand embedded in the question, **both LogBars** | Remove one operand's explicit register |
| 2 | `one_logbar_removed` | Both definitions, **one** LogBar | Remove a numerical encoding aid, retaining both registers |
| 3 | `one_raw_no_logbar` | One definition with LogBar, other operand raw with no LogBar | Joint removal of one register and one LogBar |
| 4 | `plain_expression` | Bare numbers and task text, no registers or bars | `Subtract -3 from 17.` |
| 5 | `story_problem` | Contextual real-world-language problems with signed numbers, no registers or bars | `An elevator goes from floor -3 to floor 17. What is its signed change in floors?` |

Removing a register and removing a LogBar are two independent axes, so tiers 1
and 2 are distinct probes. The curriculum progressively combines both changes.
The side of the removed aid alternates across prompt order. In the story stage, reversed prompts use genuine context-preserving paraphrases with the opposite first-mentioned operand, rather than input-label preambles or duplicate numbers.

From tier 4 onward, **all actual operations share the same constant operation
ID** inside the decoder; this prevents it learning the operation from an
out-of-band categorical side channel. The operation must be inferred from the
prompt. This keeps the v3 head's tensor dimensions and checkpoint import intact.

Each phase trains on a cumulative mix, retaining earlier formats. The weights:

```
phase 0: registered 100%
phase 1: registered 60%, one_raw_operand 40%
phase 2: registered 40%, one_raw_operand 20%, one_logbar_removed 40%
phase 3: registered 25%, one_raw_operand 15%, one_logbar_removed 20%, one_raw_no_logbar 40%
phase 4: registered 20%, one_raw_operand 10%, one_logbar_removed 10%, one_raw_no_logbar 20%, plain_expression 40%
phase 5+: registered 15%, one_raw_operand 10%, one_logbar_removed 10%, one_raw_no_logbar 10%, plain_expression 25%, story_problem 30%
```

These are **fixed-duration phases**, not automatic proficiency gates. The
replay fraction discourages forgetting, and `validation-step-*.json` reports
joint X/Y accuracy separately for all tiers reached. Advancement currently
occurs by phase step count; the recorded metrics can inform a future
competence-gated cutover.

## Install on Windows

Extract the ZIP into the main repository using its `main_computer_test/`
contents as the target root, so that these exist:

```
tools/nanojev_altreg_attention_curriculum_v4.py
tools/NANOJEV_ALTREG_ATTENTION_CURRICULUM_V4.md
tests/test_nanojev_altreg_attention_curriculum_v4.py
```

From `C:\Users\subsi\main_computer`:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_altreg_attention_curriculum_v4.py --version
& $py .\tools\nanojev_altreg_attention_curriculum_v4.py --preview
& $py -m unittest discover -s .\tests -p 'test_nanojev_altreg_attention_curriculum_v4.py' -v
```

Training, fork from the best currently stored Qwen v3 attention checkpoint:

```powershell
& $py .\tools\nanojev_altreg_attention_curriculum_v4.py `
  --source-checkpoint best `
  --device cuda --dtype float16 `
  --steps 12000 --phase-steps 2000 `
  --batch-size 16 --lr 0.00002 `
  --validation-examples 32 `
  --eval-every 500 --report-every 50 `
  --allow-model-download
```

**For GPU memory and meaningful throughput, stop the old 100k v3 loop at a
checkpoint before running the v4 fork.** The original run remains recoverable
from its latest checkpoint. The v4 fork makes a pinned copy named
`source-v3-attention.pt` in its new output directory. You may later restart the
old v3 run separately.

Resume after an interruption, or extend to a larger **total curriculum step**
target (phase 5 mix continues beyond 12k):

```powershell
& $py .\tools\nanojev_altreg_attention_curriculum_v4.py `
  --device cuda --dtype float16 `
  --steps 30000 --phase-steps 2000 `
  --batch-size 16 --lr 0.00002 `
  --validation-examples 32 `
  --eval-every 500 --report-every 50 `
  --allow-model-download --resume
```

The configuration is identity-checked on resume, so changing batch size,
learning rate, seed, phase duration or holdout size on the same run is rejected.
`--steps` is a total target, not additional steps. Resume restores the head,
optimizer, RNG and numeric stream. The last 500 updates can be lost if the
process stops between checkpoints. There is no automatic restart after a Windows
reboot or a hung process; use the existing outer PowerShell supervision logic
for those situations.

Validation is held out by the **original operand pair** (the old Qwen parent
split), and evaluated separately per tier. It uses the frozen Qwen live rather
than falsely reusing token features cached for the old prompt. The untouched
test split is not consumed. `best-phase-<n>.pt` preserves the best averaged
joint X/Y checkpoint in each phase; `best-curriculum.pt` tracks the best of the
**current reached phase**, not an across-phase comparison.

## Scientific limitations

- Changing the rendered prompt and changing operation-ID input at tiers 4–5
  produces a representation-distribution shift. Scores may collapse before
  recovering. Watch per-stage validation, not merely loss.
- 5% X/Y agreement is not 1% accuracy, precise arithmetic, or RESULT/FINAL
  correctness. The decoder is intentionally X/Y-only.
- The old 128-example validation pool has already informed earlier champion
  selection. Retain the original untouched test until the curriculum is selected.
- The new prompt families share underlying arithmetic pairs and operation
  truths, intentionally, to isolate representation difficulty. Story prose
  is templated; generalization to arbitrary narratives remains unproven.

## v4.2 story-prompt correction

The original v4.1 reverse story prepended "second input" and "first input"
labels and repeated both operand values. That offered a shortcut around reading
the prose, especially when the task is supervised only on X/Y. In v4.2, every
story has two natural paraphrases, each mentioning each operand once, with
reversed operand presentation but identical intended calculation.

This prompt revision is included in the resume configuration identity. A v4.1
training output cannot be silently resumed with these different prompts. The
existing v3 source head is unaffected. If v4.1 was already trained, keep its
run root and fork v4.2 into a *new, distinct* `--output-root` instead of
reusing the old v4 path. The default v4 output root is suitable if it has not
been trained yet (running `--preview` does not create it).

The curriculum still supervises X/Y extraction, not operation execution:
a constant operation ID at the later stages prevents operation-ID leakage but
does not itself establish RESULT/FINAL reasoning. Those stay diagnostic until
explicit downstream supervision is introduced.
