# HybridLink: anonymous one-operation micro-reduction experiment

**Status: runnable experimental patch; CPU generator/validator and mocked inference tested; no CUDA model accuracy established.**

The first text-rung experiment (`tools.nanojev_hybridlink_rung_reduction`) achieved low
teacher-forced DEV loss, but its initial one-case DEV smoke showed a failed
`B0b → B0a` hop. That hop was asked to compute **both** compound totals and
rewrite the whole question. The new experiment preserves all other macro
transitions and replaces **only** this compound section with variable-depth
arithmetic micro-reductions.

## Contract (no semantic hints below B0b)

The semantic interpreter's job is to emit the numerical representation at the
boundary. **The micro-model receives none of the original domain nouns,
roles, or question provenance.** It receives only three anonymous registers:

```text
<REDUCE>
A = (8 * 2) + (4 * 1)
B = (8 * 1) + (4 * 2)
R = A - B
</REDUCE>
```

One invocation can produce:

```text
<REDUCE>
A = 16 + (4 * 1)
B = (8 * 1) + (4 * 2)
R = A - B
</REDUCE>
END_RUNG
```

Repeat. It takes **six arithmetic invocations** to obtain `A = 20`, `B = 16`,
`R = A - B`. The successful existing macro `B0a → T0 → Answer` is **not**
retrained or replaced in this patch. A later integration may hand those
completed registers to the already-tested macro path. A larger expression
gets as many micro-invocations as it has binary operations, not a hardcoded
six-step recipe.

The teacher picks the leftmost reducible operation to form training targets,
but the independent validator accepts **any** valid one-operation reduction
anywhere in A or B. It verifies:

- exactly one binary `+`, `-`, or `*` node is computed; no multi-step shortcut;
- every unrelated AST subtree remains identical;
- register identifiers and subtraction direction remain identical;
- final signed value is unchanged;
- complete `<REDUCE>` block and `END_RUNG` terminator are present, with no extra answer or second attempt.

All independent numeric evaluation occurs **outside the model**, in the
supervision builder and validator. No Python evaluation result is supplied to
the model during a free-running chain, and failed chains are never corrected.
The validator parses a restricted arithmetic AST, never executes arbitrary
model-generated Python code.

Training covers zero, positive and negative signed residuals, both directions,
permuted expression order, and the anonymous register triples `A,B,R`,
`X,Y,Z`, `U,V,W`, and `M,N,Q`.

### What is and is not tested

The program trains the *micro portion* of the rung ladder. It does **not**
claim to test all semantic hops end-to-end, nor does it establish QW5 Stage 2
competence. It tests **one-step** accuracy with correct current states and
**autonomous six-step** micro-chain accuracy from a fresh initial expression.
The new lineage is independent of the old coarse text and latent heads.

## Apply patch

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\hybridlink_anonymous_micro_reduction_patch.zip'
```

## 1. CPU preview (no Qwen, no weights)

```powershell
& $py -m tools.nanojev_hybridlink_micro_reduction `
  --stage preview --device cpu `
  --band 0-4 --scales 8,16,32,64,128,256
```

`MICRO_PREVIEW` prints the seven states (initial + six reductions) and the
entire deck sizes: **36 train scenarios = 216 one-operation examples;
12 DEV = 72; 12 sealed TEST = 72.**

## 2. Train Qwen's text adapter on one-operation transitions

This is deliberately a **new directory**, leaving the accepted old
`hybridlink_rung_text_v1` checkpoint unchanged.

```powershell
& $py -m tools.nanojev_hybridlink_micro_reduction `
  --stage train-text --device cuda `
  --band 0-4 --scales 8,16,32,64,128,256 `
  --steps 1200 --eval-every 100 --lr 0.00001 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1'
```

Optional transfer comparison: repeat in a **different** output directory and
add `--warm-start 'C:\Users\subsi\NanoJev\runs\hybridlink_rung_text_v1\best.pt'`.
That existing model's bird-related training is in its *weights*, but there are
**no domain hints in the micro prompt or target**. For the first clean
experiment, use **no warm start**, as above.

Evaluation checkpoints are selected by a reproducible DEV sample balanced
across all six operation positions and signed residual groups. The full DEV
one-step and autonomous-chain probe remains necessary; low likelihood loss
alone is **not** certification. Source hashes and frozen backbone are checked.

## 3. Smoke probe (one held-out scenario; text-only)

```powershell
& $py -m tools.nanojev_hybridlink_micro_reduction `
  --stage probe --device cuda --split dev `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --controls text_only --case-limit 1 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_smoke_v1'
```

This executes **six independent one-step generations** and up to **six
additional free-running generations**. Progress is reported before each
inference. It stops on the first invalid autonomous output.

## 4. Full DEV micro-chain probe (preserve TEST)

```powershell
& $py -m tools.nanojev_hybridlink_micro_reduction `
  --stage probe --device cuda --split dev `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --controls text_only `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_dev_v1'
```

Read `MICRO_PROBE_RESULT.controls.text_only.one_step_accuracy` and
`chains_correct/chains_total`. Detailed per-hop failures, generated text,
and entire free-chain traces are written to `records.json`.

## 5. ONLY if text reduction succeeds: train the actual latent head

Freeze the trained text adapter, train one learned HybridLink latent step in a
separate lineage; no baseline weights are edited:

```powershell
& $py -m tools.nanojev_hybridlink_micro_reduction `
  --stage train-link --device cuda `
  --band 0-4 --scales 8,16,32,64,128,256 `
  --text-checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt' `
  --steps 400 --eval-every 50 --lr 0.0001 `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_v1'
```

```powershell
& $py -m tools.nanojev_hybridlink_micro_reduction `
  --stage probe --device cuda --split dev `
  --checkpoint 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_v1\best.pt' `
  --controls text_only,zero_latent,intact `
  --output 'C:\Users\subsi\NanoJev\runs\hybridlink_micro_link_dev_v1'
```

The proper latent contribution comparison is **intact versus zero_latent** on
the same held-out questions. Text-only gives the additional no-latent control.
**No claim of latent benefit** should be made from the text-only stage.

## Independent TEST and limitations

The full TEST split is one-shot guarded by `test_consumed.json`, cannot be
partially probed, and should be run only after DEV experimentation is done.
Results are labeled `PROBE_UNCERTIFIED` because positive micro-ladder results
alone do not validate the semantic interpretation-to-anonymous-state boundary
nor the downstream terminal cancellation computation.

The default source scenarios use a deliberately simple class of two-term
product-and-sum expression trees. A nine- or ten-operation arithmetic tree is
validated in CPU tests, but generalized neural competence on such unseen
trees requires an additional independent GPU/OOD study. Do not extrapolate
from the 0–4 band to residuals or operands of four million.
