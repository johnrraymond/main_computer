# G1-0082 — Native G1→G2 integration and prospective certification

## Objective

Eliminate the two G1-0081 blockers without retraining or altering the G1 model or G1 acceptance gates:

1. G2 must consume the exact **learned** G1 semantic objects, not the legacy neural-span decoder.
2. The native runtime must be evaluated on newly generated, previously unexamined examples with answers committed before blind inference.

The G1-0081 frozen composite (native head + G1-0076/0078 trained discriminator + G1-0079 quantity-preserving decoder) is the only allowed G1 source. All its coefficient hashes, head hash, deterministic initial-state digest, source-adapter lineage and source-code lineage are verified. The G1-0081 exposed development and challenge reports must already have passed.

## Native consumption implementation

`GamerGraphHead.forward(..., inference_spans=...)` is an **explicit** G2-only input, mutually exclusive with `training_spans` and unavailable for G1. It validates semantic kinds, ordering, token bounds, eligible tokens, and nonoverlap. No default inference behavior changes for callers that omit it.

The `NativeCompositeG1` bridge independently reconstructs the exact G1-0081 candidate lattice from frozen neural logits. It decodes the selected complete object set, resolves author-space coordinates back into disjoint native token intervals and passes them to the *actual* native G2 head. It confirms the resulting native surface nodes exactly equal the composite's predictions. The bridge contains no oracle-label input.

Native G2 curriculum's `--native-composite-g1` and `--native-composite-cert` options are opt-in and permitted **only for G2**. It verifies the prospective G1-0082 certificate first, then trains binding relationships on the **predicted** G1 spans. Gold G1 spans are not teacher-forced into the G2 forward path. Training labels are remapped to the G1 objects that are actually present; missing objects never masquerade as predicted ones. The checkpoint records and verifies composite lineage during G2 evaluation and G1 retention testing. Higher stages remain explicitly locked until their native integration is certified.

## One-command prospective certification

Run from `main_computer\nanojev` (the user supplies `$py`):

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_native_certification_0082 run --device cuda
```

The runner refuses any existing destination. It verifies and reproduces **each of the 112 G1-0081 development/challenge predictions** in the native G2 head, not just their aggregate scores. It then constructs 96 new development and 64 new challenge cases from the graph test vocabulary, excluding the original curriculum, G1-0077 and G1-0079 train/selection/audit prompts. The deterministic seed and cohort sizes are locked; this evaluates transfer *within the generator's distribution*, not out-of-distribution robustness.

Before prediction, it commits separate prompt and gold answer files with SHA-256 digests. A separate Python subprocess consumes **only** the committed prompts and the frozen inference checkpoint. It produces gold-blind full, zero-hidden and stage-component predictions and native G2 surface-consumption evidence. All predictions and the complete G1-0081 per-case parity ledger are hashed before scoring. The scorer reads the gold answers **only afterward**, independently recomputes exact sets, per-kind recall, macro-F1, both matched causal ablations and family diversity, and applies the original thresholds without modification.

Outputs under `runtime/reports/gamer-v1/oracle-fabric-g1-native-certification-0082`:

- `seal/commit.json`, `seal/prompts.jsonl`, `seal/answers.SECRET.jsonl`
- `blind/predictions.jsonl`, `blind/historical-parity.jsonl`, `blind/lock.json`
- `scored/certificate.json`

A native pass issues `NATIVE_COMPOSITE_G1_PROSPECTIVE_GATE_PASSED`. This authorizes **only** the explicit composite-aware native G2 training path, not G2 certification, activation, production promotion, or native G3. A failed criterion emits a blocked certificate; no G2 path is unlocked. The original G1 development/challenge cohort is identified as previously exposed evidence and is used strictly for exact native parity, not as fresh acceptance evidence.

## Optional G2 learning after native certification

Once (and only once) G1-0082 passes, the newly enabled predicted-span training mode can be invoked:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum train `
  --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl `
  --source-adapter ..\runtime\reports\gamer-v1\transition-005\lora\best_adapter `
  --stage G2 `
  --parent-checkpoint ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 `
  --prerequisite-cert ..\runtime\reports\gamer-v1\oracle-fabric-g1-native-certification-0082\scored\certificate.json `
  --native-composite-g1 ..\runtime\reports\gamer-v1\oracle-fabric-g1-certification-completion-0081\checkpoint `
  --native-composite-cert ..\runtime\reports\gamer-v1\oracle-fabric-g1-native-certification-0082\scored\certificate.json `
  --output ..\runtime\reports\gamer-v1\oracle-fabric-g2-native-0082 `
  --device cuda --steps 400
```

G2 training and G2 certification are separate. The new G1-0082 result **must** be reviewed before executing this next stage. The ordinary G2 training mode never silently accepts the composite certificate; it requires the explicit native flags.

## Integrity requirements and limits

The G1-0081 manifest commits the historical curriculum code SHA-256 `0f6c5639385aab8227abe127b1e57ec675c2b82d90ef4df7e2f381ef56aee6eb`. G1-0082 pins the **specific reviewed native-curriculum extension** as well, rather than ignoring changes to source code. Its downstream certificate reopens all inputs and independently re-scores the prospective cohort before authorizing G2 training.

The original 80+32 cohort, the G1-0077/0079 observations, and a single prospective generator-derived cohort do not by themselves establish out-of-distribution robustness or production approval. Nothing in this patch mutates pretrained Qwen/LoRA, G1 head weights, saved learned G1 discriminator, prior G1 certificates, or stage thresholds. Native graph runtime extension is opt-in; the default G2 path remains unmodified.
