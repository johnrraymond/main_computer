# HybridLink T00-First Diagnostic Cutover (v1)

**Purpose:** Identify *what breaks* between a frozen Qwen Stage 1 state, a generated HybridLink embedding, the post-feedback hidden state, the learned Answer Detector, and the direct/complex readouts. **This is a diagnostic, not a new training run.** It does not change or save any model weights, and will not promote the zero-success step-40 checkpoint automatically.

## Specific failures this patch separates

- **Generator:** Does the one-step T00 output get closer to the frozen Qwen *reference answer state* than the initial problem state? Is it better than a zero-embedding control?
- **Detector:** Does the *same* detector accept the gold Qwen answer representation while rejecting the HybridLink-generated one? `gold_positive_generated_negative` measures exactly this gap.
- **Prefix shortcut:** Compare the detector on `ANSWER: N`, `PROBLEM: N`, and bare `N`, all encoded by Qwen. These are explicitly **counterfactual, labeled reference probes**, never runtime states. Large gold-versus-problem probability changes suggest reliance on the marker, not proof of semantic completion.
- **Numbers:** For each transition record the direct numerical readout, complex `a`, `b`, radius, phase, log radius, difference in the two log-coordinate readouts, and errors against the *value produced in that transition*.
- **Complex causality:** This version of HybridLink has no complex geometry in its transition generator. The report explicitly marks `numerical_geometry_in_feedback=false` and `COMPLEX_HEAD_IS_OBSERVER_ONLY`. Swapping readout predictions cannot affect the generated embedding. A genuine with/without geometry training ablation requires *a later change to the transition loss or input*; no zero-effect control is presented as evidence for geometrical improvement.
- **Actual multi-step progression:** Optional `--stage all` records actual generated Qwen states and detector probabilities, drifts, and readouts at each step until normal stop or safety exhaustion. It then attaches gold targets **after** the rollout, solely for diagnostic scoring.

## Important: T00 means final hop, not an easy root-only example

For any complete chain, T00 is its final `PROBLEM: ... -> ANSWER: ...` transition. Diagnostics select complete DEV chains from a variety of *origin depths*, then extract the **final pre-answer state** as the independent T00 starting point. No oracle intermediate vectors enter generation. Longer, autonomous starts are tested separately by `--stage all`.

For intermediate states, the numerical output is obtained from the *previous* problem, not by evaluating the next problem. Example: `(2+(-1))+5 -> 1+5` produces `1`; `1+5 -> ANSWER: 6` produces `6`.

## Apply

```powershell
python .\new_patch.py 'C:\Users\subsi\Downloads\hybridlink_latent_t00_diagnostic_patch_v1.zip'
```

## Preview (no model or GPU load)

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage preview --device cpu --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt"
```

## T00-first diagnosis (recommended first run)

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage t00 --device cuda --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_latent_loop_smoke_v1\best.pt" `
  --t00-cases 12 --encoder-batch 12 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_latent_diagnostics_v1"
```

The tool reports incremental `LATENT_DIAGNOSTIC_T00_PROGRESS` and a compact `LATENT_DIAGNOSTIC_RESULT`. Full details are written to `diagnostic.json`, including per-case sources, expected values, raw complex coordinates, zero-feedback comparisons, and detector probabilities for gold, generated and counterfactual-prefix hidden states. No stage requires training or downloading new weights.

## Full multi-step traces (only after inspecting T00 results)

```powershell
& $py -m tools.nanojev_hybridlink_latent_diagnostics `
  --stage all --device cuda --split dev `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_latent_loop_smoke_v1\best.pt" `
  --t00-cases 12 --trace-cases 6 --max-depth 12 `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_latent_diagnostics_trace_v1"
```

### How to interpret the report

| Evidence | Likely failure to investigate |
| --- | --- |
| Gold answer detected, generated answer not detected | Generated-state distribution gap; inspect generator cosine to target |
| Intact gain over zero embedding is nonpositive | Generator is not providing useful direction toward reference states |
| Gold `ANSWER:` detected but identical-number `PROBLEM:` not | Possible marker shortcut; detector's learned completion isn't semantic yet |
| Both gold and generated detector probabilities are low | Detector has not learned relevant answer representations |
| Complex and direct numerical errors both large | Readout training inadequate; don't rewrite Qwen to compensate |
| Complex predictions change but latent trajectory does not | Expected: complex head is currently an observer, not a control or loss in HybridLink |
| No actual answer exits | **Not latent-certified** regardless of zero premature stops |

`diagnostic.json` is deliberately verbose; it does not save hidden vectors or any model weights. Input checkpoint SHA-256 hashes are checked before and after. Reference target text and vectors are used only for selecting diagnostic cases and scoring completed generated rollouts, not as inference inputs. The generator does still receive the starting `PROBLEM:` textual embedding, as in Patch 3; the detector sees the subsequently generated Qwen hidden state.

## Test

```powershell
& $py -m pytest tests/test_nanojev_hybridlink_latent_diagnostics.py -q
```

**Boundary:** The experiment provides attribution, not a proposed new geometrically conditioned HybridLink. If the complex head remains observational, the next patch should explicitly train a geometry-conditioned versus unconditioned transition and measure causal effects, rather than claiming the complex formulation is useful from its readout alone.
