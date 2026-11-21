# Gamer MIT Weight-Space Programming — research protocol

This is a new **MIT-WSP** experiment for **G2**, distinct from the original MIT cutover. It is based on Takehi, Teoh, Zhang & Khattab's November 2, 2026 paper *Sparks of Weight-Space Programming* and, specifically, their Experiments 4 and 8. The MIT researchers asked a frontier LLM to inspect model weights and failure behavior and **reason out a direct edit without training or gradients**. In their Qwen experiment, a selective subtraction of a looping direction from the output columns of layers 15 and 19 raised MATH-500 from 7.9% to 48.1%. The published precise logs and tensor diffs are at https://github.com/RikiyaT/lm_understand-logs/tree/main/exp8_math.

**The former `nanojev_gamer_v1_mit` experiment used gradient-based saliency and AdamW. It was not a reproduction of weight-space programming.** This extension does not change the earlier experiment or its generated model. Rather, it enables a real independent research protocol that makes it possible to conduct a reasoning-driven edit on Gamer's G2 head.

## Run it

From `C:\Users\subsi\main_computer\nanojev`, using the Python interpreter already configured in `$py`:

```
& $py -u -m tools.nanojev_gamer_v1_mit_wsp prepare --device cuda
```

The output path is `../runtime/reports/gamer-v1/oracle-fabric-g2-mit-wsp-0001/inspection.json`. **The next step requires a reasoning model to review this actual file, not a copy of our earlier AdamW code.** The inspection shows the real trained weights of `binding` and statistical summaries of `pair_projection`, plus training-only examples with gold graph edges and erroneous predictions. It also seals 48 fresh development and 32 fresh challenge cases *before the proposed edit*. The editor does not see their answers.

For detailed weight inspection, use:

```
& $py -u -m tools.nanojev_gamer_v1_mit_wsp inspect --tensor binding.weight --row 1
& $py -u -m tools.nanojev_gamer_v1_mit_wsp inspect --tensor pair_projection.weight --row 42 --start 0 --count 192
```

Feed the `inspection.json` to an LLM and ask it to reason about the precise failure mechanism. The LLM must write a JSON edit proposal, backed by its explicit diagnosis. It can select a handful of coefficients (`set`), zero or scale selected input columns (`zero_columns`, `scale_columns`), or subtract a selected output direction from specified columns (`remove_output_direction`). The last operation computes `W[:,J] -= alpha * u (u^T W[:,J])` for normalized `u` and the selected columns `J` — a direct analogue of the selective-subspace removal in the MIT math result, but only if a causal Gamer failure supports it.

There is **no hardcoded proposed edit**: applying arbitrary zeros solely to obtain an apparently active program would not reproduce the MIT experiment. The proposal must be model-authored and refer to the inspected Gamer evidence.

Put the proposal at an explicit path, for example `./mit-wsp-edit.json`, then run:

```
& $py -u -m tools.nanojev_gamer_v1_mit_wsp apply --proposal ./mit-wsp-edit.json
& $py -u -m tools.nanojev_gamer_v1_mit_wsp predict --device cuda
& $py -u -m tools.nanojev_gamer_v1_mit_wsp score
```

`apply` checks the baseline head SHA, edit scope (only `pair_projection` and `binding`), number of modified coefficients (maximum 3.5% of that parameter surface), coefficient deltas, finite values, and that G1/G3+ weights remain unchanged. `predict` uses the same exact projection decoder for the base and directly edited heads, so only the weight edit changes. Predictions are hash-locked before `score` opens the sealed answers. `score` reports per-split exact graphs, edge F1, and deltas versus the unedited MIT baseline.

Results are written under the **new** `oracle-fabric-g2-mit-wsp-0001` campaign. The previous MIT campaign and every original source checkpoint remain intact. This is a method-level replication **on a different problem**, not an independent reproduction of the exact Qwen MATH-500 numerical result. Genuine generalization/certification remains a separate step and a fresh data generator remains an unresolved distribution limitation.

## If you want the exact MIT Qwen reproduction first

MIT published the complete experiment logs, original and modified tensor hashes, the edit diff, and rebuilding/evaluation scripts at https://github.com/RikiyaT/lm_understand-logs/tree/main/exp8_math. The modified model is at https://huggingface.co/RikiyaT/Qwen3.5-0.8B-weight-space-programming. Rerunning the original MATH-500 benchmark requires the official sampling protocol and substantial CUDA inference, and it is **not** the same as the new Gamer-G2 method adaptation.
