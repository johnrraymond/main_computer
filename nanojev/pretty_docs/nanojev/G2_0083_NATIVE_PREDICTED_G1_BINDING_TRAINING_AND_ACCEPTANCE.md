# G2-0083 — Native predicted-G1 relationship training and acceptance

## Governing contract

This is an opt-in **training** and prospective G2 evaluation workflow. It uses the *existing hash-locked* G1-0082 native curriculum rather than changing any certified G1 sources. It cannot manufacture a G2 certificate, authorize G3, or promote a production runtime. G1-0082 must already report `NATIVE_COMPOSITE_G1_PROSPECTIVE_GATE_PASSED` and its complete certificate is reverified, not trusted by status alone.

The original G2 acceptance requirements are untouched: `binding_exact_rate >= .80` on development, `>= .70` on challenge, and both causal gains `>= .08`. G1 preservation is checked on the trained native G2 checkpoint. The G2 and G1 development/challenge cohorts from the original curriculum are **historically examined** and are not represented as independent certification.

## Single operator command

Extract the patch at the `main_computer` repository root. From `main_computer\nanojev` run:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_native_training_0083 run --device cuda
```

No PowerShell variables other than the operator's existing `$py` are required.

This command requires the existing files:

- `../runtime/reports/gamer-v1/graph-span-curriculum-0005.jsonl`
- `../runtime/reports/gamer-v1/transition-005/lora/best_adapter`
- `../runtime/reports/gamer-v1/oracle-fabric-cutover-0001/stages/G1`
- `../runtime/reports/gamer-v1/oracle-fabric-g1-certification-completion-0081/checkpoint`
- `../runtime/reports/gamer-v1/oracle-fabric-g1-native-certification-0082/scored/certificate.json`
- Prior G1-0077/0078/0079 prompt ledgers and the G1-0082 sealed prompt file for exclusion.

## Method and failure barriers

1. **Preflight**: Reopen G1-0082 sealed certification, compare its original head digest, source-adapter fingerprint and corpus hash with the G1 parent. The deterministic-fabric ID must be `oracle-fabric-0001` and is passed verbatim to the existing native G2 trainer. No G2 run can start from a status flag or mismatched certificate.
2. **Commit new evaluation answers before training**: Using the author-annotated `graph_corpus` test vocabulary with fixed seed 830083, generate 96 new development and 64 new challenge examples. Exclude the original corpus and G1-0077/0078/0079/0082 cohorts. Write prompts and secret author graphs separately, commit both SHA-256 digests before loading the trainable G2 model. The generator is for author-created evaluation data; it is not imported by the native model or inference decoder.
3. **Actual native G2 training**: Call `graph_curriculum.train` with stage G2, certified native composite G1 inputs, predicted-token-span inputs, remapped binding supervision, 400 steps, gradient accumulation 4 and learning rate `1e-4`. **No gold G1 spans are teacher-forced to the G2 head**. Train only the binding, pair projection and allowed G1→G2 residual coupler. Preserve the parent/future stage weights byte-for-byte.
4. **Historical development checks**: Evaluate G2 binding exact sets on original 80 development and 32 challenge examples with both zero-hidden and binding-component causal ablations. Evaluate G1 retention on the same checkpoint and splits. Call the original fail-closed developmental `certify` function with both G1 retention reports. Record all failures, even when the training itself succeeded.
5. **Prospective blind binding test**: Invoke a separate `predict` process that opens **only prompts, Qwen/LoRA and the saved G1/G2 weights**, not answer graphs. Run four inference modes: trained G2, untrained parent G2, zero-hidden, and no-binding-component. Every mode derives nodes from the native frozen composite G1 and passes them to the native G2 head; the first three preserve identical G1 nodes. Save and SHA-256-lock all object/edge predictions.
6. **Scoring**: Only after locking predictions, open the author graphs and independently compare predicted binding-edge triples, anchored to semantic type and exact character spans. Report exact binding sets, precision, recall and F1, by-family scores, G1 exact sets, G1-limited gold-binding coverage, paired gains and losses, and causal controls. Require development `.80`, challenge `.70`, causal gains `.08`, and positive net improvement over untrained G2. The final run report keeps G3 locked whether it passes or fails.

## Immutable artifact layout

`../runtime/reports/gamer-v1/oracle-fabric-g2-native-training-0083/` contains:

- `seal/prompts.jsonl`, `seal/answers.SECRET.jsonl`, `seal/commit.json`
- `training/gamer_graph_head.safetensors`, `training/manifest.json`
- `evaluation/G2_dev|G2_challenge|G1_dev|G1_challenge/report.json`
- `development-gate/certification.json`
- `blind/predictions.jsonl`, `blind/lock.json`
- `prospective/report.json`, `prospective/cases.jsonl`
- `report.json` (final, whole-workflow decision)

All destinations are create-only; interrupted runs cannot silently replace reports or unseal an already-used acceptance cohort. Reruns require a separately designated destination and a genuinely distinct future acceptance cohort before its results can be counted as independent.

## Limits and follow-up

- This stage learns **semantic bindings (G2)**, not G3's relation-class prediction or G4–G7 mathematical graph completeness. Future stages stay disabled.
- Binding loss in the certified G1-0082 curriculum remaps gold positive endpoints to *correctly matched* predicted G1 nodes. Unmatched G1 nodes are present at inference, but the inherited G2 loss does not independently label all their pairs as training negatives. Prospective false-edge diagnostics expose the consequence; a future improvement to that loss would require a new reviewed native-code lineage and G1 certificate integrity review, not a silent modification of G1-0082.
- The prospective examples are new and were not used in training/selection, but they arise from the existing graph generator. This demonstrates within-generator transfer, not robust out-of-distribution capability or production approval.
- Nothing in this patch modifies the original G1 checkpoints, composite coefficients, earlier stage reports, original curriculum, G1-0082 certificate, or stage-gate requirements.
