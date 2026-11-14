# Qwen3-0.6B HybridAgent — repairing the latent interface

## Purpose

The prior format-only LoRA run reduced CE but left the newly added `<latent>` and `</latent>` output-token probabilities near zero. The prior HybridLink was trained against a backbone **before** later backbone changes. This repair is a **new lineage**, not an in-place mutation or continuation of those checkpoints.

This is an implementation and **bounded experimental test** of the suspected interface failure, **not** a guarantee that the proposed causal explanation is complete or that the paper's performance claims will be replicated on 0.6B.

## Install / run

From `main_computer` in the existing NanoJev Python environment:

```text
python new_patch.py nanojev_qwen_hybrid_interface_repair_patch.zip --dry-run
python new_patch.py nanojev_qwen_hybrid_interface_repair_patch.zip
python -m tools.nanojev_qwen_hybrid_interface_repair --device cuda
```

The `all` command runs three stages in sequence and exits nonzero if either behavioral gate fails. It makes **no modifications** to original model files, the numerical-LoRA directory, previous format checkpoint, previous HybridLink activation checkpoint, or the previous self-conditioned checkpoints. It may load the cached Base model but does not download unless `--allow-model-download` is specified.

Output: `~/NanoJev/runs/qwen3_06b_hybrid_interface_repair_v1/`

- `format/`: `config.json`, `best.pt`, `last.pt`, `result.json`. New parameters: two isolated input marker rows, two isolated output marker rows, and attention LoRA. Original input/output vocabulary rows outside those two are frozen, and HybridLink remains untouched. The weighted next-token loss supervises both markers and ordinary teacher-forced text.
- `align/`: head-only realignment to the **format-updated** frozen Qwen/LoRA/marker checkpoint. Actual Qwen teacher-forced hidden states are targets, with cosine/MSE plus exit-anchor objective. LoRA and the frozen original Qwen weights cannot change in this stage. The head is not silently substituted from the pre-format checkpoint: the prior head is an *initialization*, and a distinct best realigned head is published.
- `gate_result.json`: read-only text-only versus **one actual latent step** versus **one zeroed latent step** on balanced held-out problems. Reports exact answers, parseable answers, marker probabilities, and separate gates. Gold answers are used **only in scoring**, not in the model input or latent recurrence.

When the format training finishes, the `all` runner checks the **best format checkpoint's entry and exit token probabilities** against a minimum of `1e-4` each. It does not train HybridLink unless both pass. This is a *screening threshold*, not a published HybridAgent performance threshold. The behavioral gate additionally requires some correct parseable answers under an intact one-step latent, at least as many as the zeroed control, and no more than one fewer than text-only on the initial six cases. If either gate fails, **deeper training is not authorized**.

The trainer initially uses 240 format optimizer steps and 120 head-only realignment steps, both checkpointed every 20. These counts are diagnostic, not proof of model capability. On the first failure, preserve the run logs. Do not blindly add steps without inspecting marker probabilities and the paired controls.

## Recovery

Training phases are independently resumable:

```text
python -m tools.nanojev_qwen_hybrid_interface_repair --stage format --device cuda --resume
python -m tools.nanojev_qwen_hybrid_interface_repair --stage align --device cuda --resume
python -m tools.nanojev_qwen_hybrid_interface_repair --stage gate --device cuda
```

A one-command `all` run refuses an existing finished stage rather than overwrite it. For a deliberate new experimental lineage use a different `--output` directory. Resume requires the **same** sources, config, data fingerprint, optimizer state and RNG as the saved run; changing a hyperparameter is not an in-place extension.

The CPU regression suite (no model download):

```text
python -m pytest --noconftest -q tests/test_nanojev_qwen_hybrid_interface_repair.py
```

## What is and isn't verified

The CPU tests establish marker-specific gradient flow without dense embedding updates, frozen-base integrity, interruption/resume consistency, a separately versioned realigned head, and refusal on low marker/latent scores. They do **not** establish success on your CUDA machine; only its real `HYBRID_INTERFACE_FORMAT_STEP`, `HYBRID_INTERFACE_MARKER_GATE`, `HYBRID_INTERFACE_ALIGNMENT_STEP` and `HYBRID_INTERFACE_GATE_RESULT` can do that.

The exit marker is still **explicitly appended** in the fixed-depth round-trip evaluation. Learned autonomous entry/exit scheduling, multi-step self-conditioned reasoning, multi-turn tool calls, pseudo-text interpretation, and the paper's speed/accuracy gains remain unverified. A passing one-step gate authorizes follow-up controlled experiments, not certification.
