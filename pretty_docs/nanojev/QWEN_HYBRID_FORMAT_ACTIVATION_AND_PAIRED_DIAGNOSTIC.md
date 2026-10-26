# HybridAgent format activation and paired text/latent diagnostic

This patch adds a bounded format-activation SFT stage and an **independent, read-only** comparison harness for Qwen3-0.6B-Base and the released-style HybridLink. It does **not** retrain or overwrite any of the earlier backbone, head, or failed joint checkpoints.

The reference implementation defines the format-activation stage as ordinary text cross-entropy over the *complete* supervised hybrid trajectory, including `<latent>`, its supervised textual contents, `</latent>`, and the final answer. In that stage, there is **no HybridLink loss** and **no self-conditioned latent replacement**. See `https://github.com/togethercomputer/HybridAgent/blob/main/hybrid_sft/model.py` and `hybrid_sft/data.py`.

Our **6-GB adaptation** retains frozen Qwen weights and trains only its existing numerical LoRA projections (`q_proj` and `v_proj`); the head is frozen. This is an engineering departure from full-backbone upstream format activation. It is retrospective: numerical LoRA and head activation were already run separately. The first 12-step joint experiment remains an unchanged comparator, not a source of format training data.

## Install and run the complete experiment

From `main_computer` in the existing NanoJev Python environment:

```text
python new_patch.py nanojev_qwen_hybrid_format_activation_and_paired_diagnostic_patch.zip --dry-run
python new_patch.py nanojev_qwen_hybrid_format_activation_and_paired_diagnostic_patch.zip
python -m tools.nanojev_qwen_hybridagent_format_gate --device cuda
```

The single command performs, in order:

1. **24-step format activation** on the previously trained numerical-LoRA checkpoint with the initialized (frozen) HybridLink, teacher-forced full text and both mode markers, held-out format loss/marker probabilities, checkpoint save/resume, and frozen-weight fingerprints.
2. **Read-only paired diagnostic** comparing `numerical_pre_format` (numerical LoRA + activated head), `format_activated` (new format LoRA + activated head) and `previous_joint` (existing 12-step joint checkpoint). Every variant receives *identical* held-out prompts and text budget at latent depths 0, 1, 2 and 4, plus a deliberately zeroed-HybridLink-vector control at depth 4.

These stages produce `HYBRID_FORMAT_STEP`, `HYBRID_FORMAT_RESULT`, `HYBRID_PAIRED_CASE`, `HYBRID_PAIRED_RESULT`, and the final `HYBRID_FORMAT_GATE_RESULT` record.

Defaults use six held-out examples balanced by result sign and expression family. They are a *smoke*: no significance or generalized numerical competence claims can be based on six examples. The reported `Answer:` parser only accepts explicit lines, and a separate leading-integer parser detects some cases rejected for formatting. The diagnostic retains raw completions for inspection. Gold answers are used **only for scoring**, never as model inputs.

## Separate commands and resumption

To run the two stages independently:

```text
python -m tools.nanojev_qwen_hybridagent_format_activation --device cuda
python -m tools.nanojev_qwen_hybridagent_paired_diagnostic --device cuda
```

For an intentional partial-run smoke, provide e.g. `--stop-after 6` to the format trainer. Resume with:

```text
python -m tools.nanojev_qwen_hybridagent_format_activation --device cuda --resume
python -m tools.nanojev_qwen_hybridagent_paired_diagnostic --device cuda
```

Or use the integrated runner with `--resume-format`. The integrated runner recognizes and reuses a **completed** format run only when its saved steps, evaluation cadence, and seed match. A partial run cannot be silently accepted as completed.

New paths (relative to `~/NanoJev/runs/`):

- `qwen3_06b_hybrid_format_activation_v1/` — `run_config.json`, `best_format_lora.pt`, `last.pt`, `result.json`
- `qwen3_06b_hybrid_paired_diagnostic_v1/` — `result.json`

Both are create-only, fail-closed paths. **Do not delete or overwrite previous checkpoints.** The diagnostic refuses pre-existing output. Format activation refuses existing nonempty output except exact checkpoint resumption.

## What must actually improve

The format stage reports held-out text CE, separate entry and exit marker probabilities, and the proportion of parseable `Answer:` lines after different latent depths. For a positive *mechanistic* result, we want improved recognition of latent boundaries **and** more coherent text return after latent inference.

The paired comparison keeps depth and source checkpoint explicit. It also checks whether corrupted latent inputs degrade downstream predictions. A causal latent-reasoning conclusion would require meaningful tasks and sufficient examples, not just output changes, formatting, training-loss decreases or token repetition. Current behavior after four latent steps had 0/6 correct blind answers both before and after 12-step joint training.

This is not an adaptive mode-switching, multi-turn tool-use, HybridRL, or latency-efficiency certification. The current HybridQwen inference API still enters and exits latent mode at user-selected depths and inserts `</latent>` explicitly. Format activation must not be misreported as teaching autonomous switching.

## Local regression tests

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_hybrid_format_and_pair.py
```

The focused tests cover full text supervision for both markers, gradient-backed optimization, exact interrupted/resumed LoRA equivalence, immutable head and frozen Qwen weights, read-only paired comparisons, strict artifact provenance, and refusal to overwrite outputs.
