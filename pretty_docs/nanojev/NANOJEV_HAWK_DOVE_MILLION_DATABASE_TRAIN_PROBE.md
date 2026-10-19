# NanoJev Hawk–Dove: million-count T08 → T00 training experiment

This is a **source-only patch** (`main_computer_test/` root), intended to be copied into the `main_computer` repository. It does not change any existing trainer, running head, or checkpoint.

## Contracts and limitations

- The game has **four immutable symbolic payoff bindings**, used exactly once each in the fully explicit Lisp expression. The fixture supplies demo-only hidden teacher values: `HH=-1`, `HD=3`, `DH=0`, `DD=1.5`. The actual trained NanoJev head has **not** been shown to know these values.
- The two numerical inputs are integer opponent encounter counts, `a+b=1,000,000`. The unitary English prompt says “200000 out of a million times,” using the word *million* rather than an additional numeric literal.
- X/Y are **cumulative** total payoff targets, not per-encounter averages. For `a=200000`, `b=800000`, the oracle gives `X=2200000`, `Y=1200000`, `Z=HAWK`.
- T00 displays *only the raw X and Y numbers*; the underlying count pair remains in the teacher instance record. T01/T02 display precisely one raw oracle result and the two injected encounter counts; T03–T08 display only the two counts. There are **no model-facing numeric registrations or `(X)` / `(Y)` placeholders**.
- The same two counts may be repeated. Maximum distinct values per prompt: **two normally, three only at T01/T02**.
- `ARGMAX`, `ADD`, `MUL`, and the four symbolic payoffs are the sole Lisp arithmetic language. No continuation `Add(...With(...))` machinery. T07 bridges English and Lisp, T08 is **English only**.
- Every number occurrence independently supports `raw` or `raw, <LogBar>`. The patch implements a **local** range-extended LogBar encoder using the source project's rounding formula, and tests it at the original 2000 boundary and the million-count sample. It **does not modify the global original encoder**, whose accepted input range ends at 2000.
- T01 and T02 are **sibling partial-expansion paths**. T03 combines them. A mutation edge removes exactly one LogBar occurrence; all admissible edges form a certified boolean lattice.
- The existing NanoJev attention head's standard numerical decoder caps predictions at **2,000,000**, below the teacher's X=2,200,000. The **new isolated** probe explicitly decodes with a 10,000,000 cap using the existing exponent classes; there is no global head or parent-checkpoint rewrite. Existing model capability to learn those targets remains unverified until CUDA training.
- Z/`ARGMAX` is a diagnostic only: `Z_loss_weight = 0`. The trained outputs are **numerical X and Y**.

## Files in this patch

| File | Function |
| --- | --- |
| `tools/nanojev_hawk_dove_million.py` | Deterministic math oracle, tiers and templates, LogBar transformations, SQLite builder, sample display |
| `tools/nanojev_hawk_dove_million_test.py` | **Independent full-database audit** of all stored prompts and single-strip edges |
| `tools/nanojev_hawk_dove_million_train.py` | Isolated training, baseline, dev probe, withheld test, checkpoint-resume |
| `tests/test_nanojev_hawk_dove_million.py` | Mathematical, mutation, SQLite, and CPU-mock training tests |
| `examples/hawk_dove_million_DEMO_ONLY.json` | Immutable demonstration payoff fixture, not learned values |
| `pretty_docs/nanojev/NANOJEV_HAWK_DOVE_MILLION_41_PROMPTS.md` | One complete T08 → T00 flow with each single-strip step |

## PowerShell: run from `C:\Users\subsi\main_computer`

After copying the **contents** of `main_computer_test/` from the patch into your existing repository:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

# 1. Unit tests: no GPU required.
& $py -m unittest tests.test_nanojev_hawk_dove_million -v

# 2. Build the database from code; no prebuilt database binary is shipped.
& $py -m tools.nanojev_hawk_dove_million build `
  --fixture examples/hawk_dove_million_DEMO_ONLY.json `
  --out runtime/hawk-dove-million/teacher.sqlite3 `
  --instances 201 `
  --mutation-mode lattice

# 3. Independently recompute every presentation and mutation edge.
& $py -m tools.nanojev_hawk_dove_million_test `
  --db runtime/hawk-dove-million/teacher.sqlite3 `
  --fixture examples/hawk_dove_million_DEMO_ONLY.json

# 4. Display one complete source example in reverse ladder order.
& $py -m tools.nanojev_hawk_dove_million show `
  --a 200000 --b 800000 --reverse --mutation-mode path

# 5. Teacher and split preflight (does not load model).
& $py -m tools.nanojev_hawk_dove_million_train preflight

# 6. Baseline from the existing V10.2 checkpoint, before training.
$parent = "$HOME\NanoJev\runs\game_math_xy_v10_operand_audited\latest.pt"
& $py -m tools.nanojev_hawk_dove_million_train baseline `
  --parent $parent --device cuda

# 7. Create or resume a wholly separate Hawk–Dove decoder lineage.
& $py -m tools.nanojev_hawk_dove_million_train train `
  --parent $parent --device cuda --steps 50 --chunk-steps 25

# 8. Fresh dev probe from the saved isolated checkpoint.
& $py -m tools.nanojev_hawk_dove_million_train probe `
  --parent $parent --device cuda

# 9. Withheld split, after the training configuration is settled.
& $py -m tools.nanojev_hawk_dove_million_train test `
  --parent $parent --device cuda
```

Output checkpoint and reports are written to `$HOME\NanoJev\runs\hawk_dove_million_v1\` by default. The existing V10.2 `latest.pt` file remains unmodified. `--steps` is **the target total number of isolated optimizer steps**, not an additional number of steps. To continue from step 50 to 250, rerun `train --steps 250` with the same parameters and unchanged database. The trainer rejects resume if source, parent, database, training code, or configuration hashes drift.

**If `runtime/hawk-dove-million/teacher.sqlite3` already exists, the builder fails rather than overwriting it.** Delete or move a prior intentional experiment database before rebuilding; do not silently overwrite its provenance.

## Database structure

- `instances(instance_id, a, b, x, y, z, split)`: **source-instance grouping**, all tier views for a pair go to one split.
- `presentations(presentation_id, instance_id, tier, mask, full_count, prompt, x, y, z, split)`: exact oracle X/Y for every distinct numerical presentation.
- `mutation_edges(source_id, target_id, slot)`: every certified one-LogBar-removal edge.
- `manifest`: immutable fixture SHA, generator schema, input count, seed, mutation coverage.

With `--instances 201 --mutation-mode lattice` the builder produces **30,552 presentations, 70,752 edges**, and exactly **201 source instances** across train/dev/test. Each tier's complete mutation lattice contains `2^n` states for `n` numerical occurrences: T00 and T08 have 4 each; T01 and T02 have 8 each; T03–T06 have 16 each; T07 has 64. The default independent database audit recomputes everything.

`--mutation-mode path` is also supported when you only want the previously illustrated single ordered path, rather than the full family; use a **different output database path** and a separate checkpoint lineage.

## Runtime validation boundary

**Verified in the build environment:** Oracle arithmetic, prompt restrictions, correctly signed high-range LogBars, tier targets, single-strip edges, SQLite integrity, distinct train/dev/test instance splits, isolated CPU-mock optimizer/checkpoint/probe behavior. **Not run here:** a genuine CUDA Qwen/NanoJev weight update or actual checkpoint predictions. The `baseline`, `train`, `probe`, and `test` modes above are for your workstation.
