# QW5: first patch — five Qwen-conditioned EMP heads

**Status:** Phase 1 implementation (QW5 training) with explicit phase-2 and phase-3 gates. This is *not* a deployed HybridAgent output replacement and not an arithmetic accuracy certificate.

## Target architecture

```text
story problem + original numerical operands
                 |
            Qwen3 transformer
          /      |        \
  intermediate  final     existing HybridLink context
      layers    layer      (intact / preserved)
          \      /
       QW5  (4 ordinary EMP banks + 1 Hard EMP specialist)
             |                         |
       five hidden states        five candidate readouts
             |                         |
    feedback projection          trained QW5 fusion
             |                         |
    [PHASE 2: augment existing       [PHASE 3: use this
     HybridLink input; OFF]           instead of LM head; OFF]
             |                         |
          HybridLink                 numeric answer
             |
        Qwen again (0+ cycles)
```

Phase 1 uses Qwen's initial hidden states (zero HybridLink steps). It makes the *same* conditioned five-bank QW5 object available for both later roles. Phase 2 will augment, **not replace**, existing HybridLink input. Phase 3 will use the QW5 readout as the final numerical answer, **not** a Qwen LM-head token decision.

**Input boundary:** The stage-1 harness supplies ground-truth numeric *operands* from its generated word-problem deck to the existing EMP encoders. Qwen supplies the hidden states from the story text. A general story-to-operand parser is not yet certified; arbitrary natural-language math questions cannot be sent directly to this experimental harness as if extraction had already been solved.

**QW5 actually learns in phase 1:** Each of the five independent EMPs produces a final backbone hidden state from the two original operands. Four Qwen hidden-layer taps (7, 14, 21, 28) are projected by separately trainable per-head conditioners. Each head adds learned sign and digit corrections; a trainable five-way fusion predicts the signed numerical result. No rule-based arithmetic answer is available to forward inference, and the answer is never supplied as a Qwen feature. The original five AdditionNet checkpoints and frozen Qwen weights are preserved.

## Inputs and prerequisites

The first patch deliberately consumes the **existing committed** EMP campaign, rather than starting new ordinary/Hard EMP training or changing their acceptance rules:

- The four ordinary heads are in `CAMPAIGN/stages/outward_<radius>/latest.pt`.
- The one specialist is in `CAMPAIGN/<hard-state-dir>/latest.pt` (defaults to `hard_emp`; match the directory used by `nanojev_emp_hard_join`), with **exactly one** `selected` source and evidence of the same ordinary-stage SHA in `processed`. Existing two-specialist Hard EMP campaigns are left unchanged and refused by this specific five-head interface.
- `CAMPAIGN/source_left.pt` and `CAMPAIGN/source_right.pt` are the snapshotted ordinary-head sources.
- `LADDER` is the original certified zero-line ladder directory with `rungs/radius_127_passed.pt` and `config.json`, required to reconstruct the AdditionNets exactly.
- A cached or explicitly downloaded pinned Qwen3-0.6B model, PyTorch, and `transformers` are needed for actual phase-1 training. The training sidecar intentionally does *not* load the existing HybridLink checkpoint in phase 1.

**Important:** Before starting QW5, commit/finish an ordinary EMP probe with its Hard EMP sidecar. If the ordinary stage changes after QW5 preparation, the harness rejects the identity drift instead of silently mixing generations. QW5 never touches the source campaign.

## One-command start (Windows, from repo root)

Use `python` from the activated NanoJev-enabled environment. Use the EMP outward campaign as `--campaign` (not the `--source-run` original dual-bank directory), and the certified zero-line ladder as `--ladder-output`. Pass the same Hard EMP sidecar directory used by the source run:

```powershell
python -m tools.nanojev_qw5_join run --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" --hard-state-dir hard_emp_frontier_v2 --device cuda --steps 500 --probe-every 100
```

If `QW5_NO_COMMITTED_EMP_STAGE` appears, the campaign has no `stages/outward_*/latest.pt` checkpoint at that path. If `QW5_MISSING_EMP_SOURCE` appears, check the source files and the `--hard-state-dir` sidecar. QW5 still refuses if the Hard EMP sidecar does not match the ordinary-stage SHA or its specialist count differs from one.

`run` creates an isolated QW5 checkpoint if absent, validates the four-plus-one source identities, builds frozen real-Qwen hidden-state features from story-form signed-cancellation problems, and trains the **QW5-specific** parameters. On subsequent calls, `run` resumes training and refuses source drift. There is no requirement to run `prepare` manually.

For a smaller first experiment:

```powershell
python -m tools.nanojev_qw5_join run --campaign "C:\...\<campaign>" --ladder-output "C:\...\<ladder>" --device cuda --radius 256 --train-cards 96 --dev-cards 32 --steps 100 --probe-every 25
```

To report the current run without loading Qwen:

```powershell
python -m tools.nanojev_qw5_join status
```

To train after an earlier `prepare`:

```powershell
python -m tools.nanojev_qw5_join train --device cuda --steps 500
```

By default runs go under `~/NanoJev/runs/qw5_phase1_v1`. Pass `--output` to isolate variants. Qwen will be used from the local cache unless `--allow-model-download` is explicitly provided. Story-state caches are written to the run directory; these are **Qwen states, not oracle answers**. The held-out DEV sample is fixed, and its magnitude families do not overlap TRAIN. TEST is not opened by this harness.

## What appears in the reports

`QW5_PROBE` and `latest_report.json` include:

- Exact signed numerical accuracy of QW5 and all five Qwen-conditioned heads.
- The best original operand-only head as a baseline.
- QW5 accuracy with Qwen states zeroed and shifted across mismatched examples.
- Numerical accuracy for positive, negative, zero, and near-zero residuals.
- How often QW5 recovers cases for which all five operand-only heads were wrong.
- A conspicuous disabled status for both later architecture stages.

These are measurements, not automatic certification. A zero or mismatched Qwen ablation that performs equally well warns that QW5 is not demonstrably using Qwen information. Better training loss by itself does not meet that requirement.

## Stage gates — deliberately NOT live in patch 1

The `QW5HybridRuntime` compatibility wrapper in `tools/nanojev_qw5_runtime.py` provides an active zero-cycle Qwen → QW5 probe, a recurrent stage-2 interface, and a stage-3 numerical-output method. Both later-stage methods remain disabled by default. The wrapper holds **one shared QW5 object** across all recurrent steps and final readout.

**Phase 2: augment HybridLink.** `HybridLinkAugmentation` is an explicit, zero-initialized residual input adapter. The existing `HybridLink` and its inputs stay intact. The adapter refuses its call until a future integration harness authorizes it after causal tests. Phase 2 must adapt QW5 state on *every recurrent cycle* while respecting Qwen's KV-cache alignment and the existing learned link checkpoint.

**Phase 3: QW5 as the output authority.** `numerical_output()` exposes the trained QW5 numerical readout, but refuses ordinary use until a separate certification step. A future runtime integration must replace the LM head only for the numerical-output path, retain Qwen text functionality where required, measure regressions, and validate the exact same QW5 weights are used during recurrence and final output.

The commands `stage2` and `stage3` deliberately return `QW5_REFUSED` in patch 1. No stubs masquerade as operating stages, and no changes to `tools/nanojev_qwen_hybridagent.py` or the original EMP trainers are required to exercise phase-1 learning.

## Tests

```powershell
python -m pytest -q tests/test_nanojev_qw5.py
```

Tests verify five independent source models, Qwen feature conditioning and live gradients, frozen EMP weights, original-input preservation, magnitude-family split isolation, stage-2/3 refusal, zero-cycle runtime integration, one shared QW5 object, source SHA enforcement, and one-command checkpointed training/resumption with mocked Qwen states. They run on CPU without downloading Qwen. A complete end-to-end GPU training run needs the actual local EMP checkpoints and Qwen model; it cannot be certified using the exported source-only snapshot.
