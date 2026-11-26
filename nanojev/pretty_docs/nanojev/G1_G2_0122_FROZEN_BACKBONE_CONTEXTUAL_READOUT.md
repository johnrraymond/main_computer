# G1/G2-0122 — Frozen-backbone contextual readout

## Research premise

0121 established that, with matched two-epoch new readers, removing the historical LoRA improved fresh G1 F1 (67.97% versus 61.87%). S/R reached 75.99% G1 and 17.19% G2 fresh graph F1, but lost three previously correct native decisions on the broader preservation review. All six 0121 arms were at 50% conditional binding pairwise accuracy with zero correct fresh reversals. This motivates candidate-conditioned contextual evidence and a directly supervised contrast objective, not more transmitter weight changes.

## Controlled arms

| Arm | External reader | S prefix | R suffix | Candidate-specific attention |
|---|---|---|---|---|
| A_bare | 192-wide | none | none | no |
| B_fixed_R | same | none | fixed numerical register | no |
| C_learned_R | same | none | learned numerical register | no |
| D_SR | same | learned | learned | no |
| E_attention | same | none | none | yes |
| F_R_attention | same | none | learned | yes |

No LoRA (old or new), direct weight modifications, or trainable backbone parameters in any arm. The pinned Qwen3-0.6B revision and tokenizer are reused from the 0120 model receipt; the 0121 parent source and completed experiment identity are checked. Common reader layers are initialized identically across arms. B, C, D and F begin from the identical R vector. B stores R as a fixed buffer, not an optimizer parameter. S and R are separate from the backbone. R is appended after the complete graph prompt and read from the causal decoder; it cannot alter earlier causal token states. The 192-wide projection is wider than 0121's 64, but not an uncompressed reader. Two pair-conditioned attention heads in E/F query the actual prompt-token memory, and the result is combined with directional endpoint and contextual interaction features. The attention arms have extra parameters, so the comparison does not isolate attention versus an equal-parameter alternative. All arms use the same graph and contrast losses, data and update order; they are not direct reproductions of the 0121 readers.

## Data and objectives

Graph training: the 96 historical fit and 64 validation prompts. Conditional binding training: 32 from 0118 fit and 64 from 0119 fit; validation: 16 from 0118 validation and 32 from 0119 validation. No held-out answers are inputs to operational prediction. Contrast loss explicitly favors both correct candidates over both incorrect ones with pairwise softplus and a binary classification term. G1 loss balances positive objects and hard negative spans; G2 balances each relationship type's positives and hard negatives, including false object proposals. Training gold endpoints are permitted only for supervised learning. The graph and contrast lanes each contribute one loss per update.

The object threshold is selected by validation G1 F1 using a grid spanning negative and positive margins. The operational edge threshold is then selected by validation G2 F1. An independent gold-endpoint edge threshold is selected on validation only. The selection utility is the equally weighted mean of validation G1 F1, operational G2 F1 and exact reversal-pair rate, with pairwise binding accuracy as tie breaker. Preservation eligibility precedes this utility. The default is up to six epochs with at least three epochs and two epochs of validation non-improvement before stopping. This is bounded adaptive training, not proof of convergence. Completed arms and prediction shards resume; an interrupted in-progress arm restarts. Changing any experiment setting requires a separate --output directory.

## Evaluation

- Historical 0113 test (192), exposed 0117 graph (28), exposed 0121 graph (48), newly generated graph (48 by default; new seed, same historical generator).
- Native and numerical binding evaluation on 0120 calibration (16), exposed 0118 (32), exposed 0119 (48), 0120 holdout (16), exposed 0121 binding (32), and new 0122 binding (48, 24 reversal pairs, three new authored constructions across four relation types).
- New binding uses opaque hash IDs; the mode and base pairing are held in the answer file. Prediction only consumes prompt and public anchors/relation for conditional testing. The binding-cue removal control tests the identical inventory prefix without the evidence suffix. Exact prompt hashes are checked against available prior prompt manifests. New graph seeds do not establish structural novelty.
- G1 inventory reports gold spans mapped by the tokenizer/span enumeration, correct top kind before admission, and admitted gold objects. G2 operational metrics include endpoint reachability, precision, recall and exact graph reconstruction. Post-lock gold-endpoint diagnostic supplies only mapped, correctly typed gold endpoints, to isolate G2; this is not an operational score. That diagnostic happens only after all arms' operational predictions and scoring have been locked. No post-lock reselection.
- Conditional rankings provide pairwise accuracy, both-binding exactness, reversal and paraphrase accuracy, plus per-relation breakdown. Fresh cue removal provides a negative control. Native likelihood is order-symmetrized and not unrestricted generation; order disagreement and answer-token mass are retained. R is used only in extraction, not the native interface. S changes the native input in D, so D also receives bare-teacher KL training and epoch preservation gates. No backbone modifications are allowed, but preservation results remain scoped to the tested interface and populations.

## Run

From the nanojev directory, with the existing NanoJEV Python environment:

```powershell
& $py -u -m tools.nanojev_gamer_v1_contextual_readout_0122 audit
& $py -u -m tools.nanojev_gamer_v1_contextual_readout_0122 selftest
& $py -u -m tools.nanojev_gamer_v1_contextual_readout_0122 run --device cuda
```

Existing 0120/0121 sources and completed 0121 report lineage, 0118/0119 seals, 0110 generator and model files must remain installed. Audit verifies their identity; model/CUDA are not loaded by audit. The generator and live checkpoint were not available to execute in the patch-development environment. CPU fixtures exercise actual reader and register gradients, all six arm training/validation/selection/prediction/scoring/oracle/report functions, answer guard in a separate subprocess, and tamper rejection. The local fixture is not evidence of live model competence. The answer guard is a Python open audit hook, not OS isolation.

Output: `../runtime/reports/gamer-v1/g1-g2-contextual-readout-0122/result-with-oracles.json`. The original locked operational report remains at `result.json`; each arm has a separate gold-endpoint diagnostic. The baseline phase reuses the 0121 helper and therefore emits `G12_0121_ACQUISITION_BARE_REPLAY` and `...BARE_PRESERVATION` diagnostics. No production changes or promotion. All reported experimental eligibility and metrics are research-only.
