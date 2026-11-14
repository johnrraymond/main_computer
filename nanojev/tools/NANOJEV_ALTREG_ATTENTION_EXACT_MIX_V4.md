# NanoJev Qwen attention curriculum v4 — exact-count, no-story cutover

This adds a **new trainer**, `tools/nanojev_altreg_attention_curriculum_mix_v4.py`. It **does not overwrite** the existing Qwen v3 attention trainer or its ongoing 100,000-step run. The new trainer forks `best.pt` (or `latest.pt`) from the v3 attention experiment into an independent checkpoint directory and trains its attention-based X/Y decoder with *different prompt presentations*. **RESULT and FINAL are not updated.** It can optionally train a lightweight auxiliary semantic OPERATION classifier; default weight is only `0.05`, configurable to `0` for X/Y-only supervision.

## Five prompt classes, in a fixed order

Every `--mix-counts` or `--phase-counts` argument supplies five **nonnegative integer** values in this order:

| Position | Name | Support retained | Example with X=17, Y=-3 |
|---|---|---|---|
| 1 | `registered_logbar` | Both registers, both LogBars | `XXXXXX = 17, ||.|||||||||`; `YYYYYY = -3, -|.||||`; `Subtract YYYYYY from XXXXXX.` |
| 2 | `one_raw_operand` | One register; both LogBars | `XXXXXX = 17, ||.|||||||||`; `Subtract -3 [LogBar -|.||||] from XXXXXX.` |
| 3 | `one_logbar_removed` | Both registers; one LogBar | `XXXXXX = 17`; `YYYYYY = -3, -|.||||`; `Subtract YYYYYY from XXXXXX.` |
| 4 | `one_raw_no_logbar` | One register and its LogBar, other operand literal | `XXXXXX = 17, ||.|||||||||`; `Subtract -3 from XXXXXX.` |
| 5 | `plain_expression` | No registers, no LogBars | `Subtract -3 from 17.` |

There are **no story problems** anywhere in this curriculum. The absent/remaining aid alternates sides across the forward/reversed presentations. `registered_logbar` is bit-for-bit the legacy v3 prompt format.

Each count is a quota **per minibatch**, not a stochastic probability. With `--batch-size 16 --mix-counts 4,3,3,3,3`, **every minibatch** contains exactly 4/3/3/3/3 prompts, shuffled randomly; the integer training stream still supplies the numeric operands and operations. Counts must add to the chosen batch size. The selected quotas, mix RNG state, numeric stream state, model, optimizer, and torch RNG are checkpointed for faithful resume.

### One fixed mix (user picks exact numbers)

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py .\tools\nanojev_altreg_attention_curriculum_mix_v4.py `
  --source-checkpoint best `
  --batch-size 16 --mix-counts 4,3,3,3,3 `
  --steps 30000 --eval-every 500 --report-every 50 `
  --device cuda --dtype float16 --allow-model-download
```

### Progressive **user-defined** phases

```powershell
& $py .\tools\nanojev_altreg_attention_curriculum_mix_v4.py `
  --source-checkpoint best `
  --batch-size 16 --phase-steps 2000 `
  --phase-counts 16,0,0,0,0 `
  --phase-counts 12,4,0,0,0 `
  --phase-counts 8,4,4,0,0 `
  --phase-counts 6,3,3,4,0 `
  --phase-counts 4,3,3,3,3 `
  --steps 30000 --eval-every 500 --report-every 50 `
  --device cuda --dtype float16 --allow-model-download
```

Phases switch at steps 2,001 / 4,001 / 6,001 / 8,001; the **last** mix persists for the remainder, even beyond the phase duration. You can provide any number of `--phase-counts` arguments. `--mix-counts` is a shorthand for a single, fixed mix. The two cannot be combined. If neither is specified, the five-phase example above is the default for batch size 16. For any other batch size, specify counts explicitly.

**Preview your exact plan and prompt text before running a model**:

```powershell
& $py .\tools\nanojev_altreg_attention_curriculum_mix_v4.py --preview `
  --batch-size 16 --mix-counts 4,3,3,3,3
```

You can test the patch offline:

```powershell
& $py -m unittest discover -s .\tests -p 'test_nanojev_altreg_attention_curriculum_mix_v4.py' -v
```

### Fork, resume, and safety

- New run root (default): `%USERPROFILE%\NanoJev\runs\independent_altreg_math_qwen_curriculum_v4_exact_mix`. Override with `--output-root` to start independent trials with different mixtures. **Do not aim different mixtures at the same output root.**
- The source v3 checkpoint is copied once to `source-v3-attention.pt` in the new run root; the v3 source remains unmodified. `best.pt` uses the old original/reversed rank; `latest.pt` uses the most recently trained v3 weights. Choose deliberately.
- `latest.pt` resumes the new curriculum; `best-phase-N.pt` preserves the best checkpoint per curriculum phase, and `best-curriculum.pt` tracks the current phase. Best is selected on the **quota-weighted averaged joint X/Y within 5%** across active prompt classes, not the old independent-order ranking. Validation also reports each class separately, with 1% and 0.1% precision and semantic operation accuracy.
- After an interruption, repeat the **same mix/phase arguments and hyperparameters**, change only `--steps` to the new **total** curriculum step target, and add `--resume`. The source is pinned, so resumed training never tracks future changes to live v3 `best.pt`.
- Resume validates training identity: weights, parent hash, mix phases, learning rate, batch size, validation parameters, etc. A mismatch fails instead of silently corrupting an existing training lineage.
- Model-loading is real Qwen token inference with frozen pretrained model parameters. The new decoder and (optionally) small operation classifier train; the old RESULT and FINAL heads are *not* changed. Plain expressions receive a constant dummy operation ID (no oracle operation shortcut) so the prompt must carry the operation semantics.
- Validation samples are reserved from the source task split. The untouched test split stays untouched. Per-step JSON files include each active class, counts, and the weighted score. This is a step-scheduled curriculum, **not a competence-gated scheduler**.
- The source v3 loop and new v4 loop should **not share a GPU simultaneously**; stop v3 at a checkpoint or use a separate device. Stop/restart support is not a guarantee against Windows reboot, power loss, full-disk, or a broken checkpoint. `--resume` restores from the most recent successful save (default every 500 steps).
