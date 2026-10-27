# HybridLink → frozen Qwen hidden-state operand probe

This experiment isolates the failure observed after Head 1 and Head 2 passed their
local gates: **can Qwen's internal activations retain the exact A/B numerical
identities, even if its native language-model output does not decode them?**

## What actually trains

The numerical localization head (Head 1), six-bit operand writer (Head 2),
Qwen3-0.6B-Base backbone, and any optional existing attention LoRA are **frozen**.
Qwen receives the ordinary input prompt, with raw number-token embeddings zeroed
outside the Head-2 edit coordinates, via `inputs_embeds`. An intact, zeroed, or
value-mismatched Head-2 correction is then inserted using the existing certified
code. The probe collects the **causal suffix last-position hidden state** from
Qwen (`output_hidden_states=True`) for two separate prompts asking for operand
A and operand B. Only a small 33-class linear or 128-wide MLP readout trains
on intact TRAIN hidden states. No arithmetic answer is used anywhere.

Train/DEV/TEST are the existing pair-disjoint, stratified EMP cards at operand
radius 16. Normalization uses *TRAIN intact features only*. The same probe is
tested on intact, zeroed and genuinely mismatched corrections. The native LM
head's 33-class predictions are also reported *without training it*. The
sealed TEST split is evaluated only if DEV obtains at least 98% exact pair and
operand accuracy, 98% in every signed-value cell, and ten-point exact-pair
advantage over **both** controls. A failed DEV run is still scientifically
useful and explicitly reports the failure; it does not consume TEST.

## Install and run

```text
python new_patch.py nanojev_hybrid_qwen_hidden_operand_probe_v1_patch.zip
python -m tools.nanojev_hybrid_qwen_hidden_probe --stage preview --device cpu
python -m tools.nanojev_hybrid_qwen_hidden_probe --device cuda --source consumption-last
```

`--source consumption-last` reads the existing failed/ongoing operand-consumption
LoRA's `last.pt` (not its arithmetic successor); `--source consumption-best`
reads `best.pt`, and `--source base` probes the completely unadapted Qwen
backbone. Each source must use a distinct `--output` directory. The tool
checks the LoRA checkpoint's model identity and Head-2 hashes against the
certified checkpoint. It does **not** require the LoRA to have passed its failed
consumption gate, because this is a diagnostic. No source checkpoint is changed.

`--kind mlp` trains a nonlinear diagnostic reader instead of a linear reader.
`--layer -1` (default) tests Qwen's final hidden state; other hidden-state
indices can be selected. A change of source, architecture, or layer requires
an independent output directory; the harness refuses to overwrite a run.

Output directory by default:
`~/NanoJev/runs/qwen3_06b_hybrid_hidden_operand_probe_v1`.

## Interpretation

* **Intact probe passes; native LM head fails:** The final hidden state retains
  *linearly/MLP-decodable* operand information; the native output mapping or
  its training interface is implicated. This is **not** proof that Qwen
  autonomously reasons numerically: a newly trained readout does the decoding.
* **Probe and LM head fail:** No evidence that the selected hidden layer retains
  operand identity in a recoverable form under this particular diagnostic.
  Test an earlier layer or nonlinear readout before attributing the failure to
  the numerical writer or discarding the channel.
* **Intact ≈ zero/wrong controls:** Probe predictions may depend on prompt
  structure or priors rather than the transmitted numerical code. No pass.
* **Probe and native output pass:** Qwen appears capable of decoding the
  transmitted operand identities on this distribution. Arithmetic remains an
  independent test.

The patch is **diagnostic only**: it does not unlock signed-zero-line training,
does not certify Head 2 for larger literals, and does not update HybridAgent or
Qwen weights. Held-out classification across repeated values is not proof of
extrapolation to unseen integers or of end-to-end HybridAgent feedback-cycle
competence.
