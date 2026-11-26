# Gamer v1 — backbone research: current direction and operating guide

Status: 2026-11-24; completed through G1-0114; integrated G1-0115 implementation delivered, operator run pending. Research only. No canonical promotion; G3 remains locked.

Read this document first when resuming the backbone/interface research lane. It supplements, rather than supersedes, the existing [stage contracts](GAMER_V1_STAGE_CONTRACTS.md), [release gates](GAMER_V1_RELEASE_GATES.md), and historical [training operations](GAMER_V1_CURRENT_TRAINING_OPERATIONS_V13.md). The quantitative history is in [GAMER_V1_BACKBONE_RESEARCH_EVIDENCE_0103_0114.md](GAMER_V1_BACKBONE_RESEARCH_EVIDENCE_0103_0114.md). The immediate work is specified in [G1_0115_INTEGRATED_SEMANTIC_RECONSTRUCTION_CERTIFICATION.md](G1_0115_INTEGRATED_SEMANTIC_RECONSTRUCTION_CERTIFICATION.md).

## Objective and reason for the change in direction

NanoJEV needs to turn an original natural-language problem into a faithful typed semantic graph (G1 objects and G2 relationships), suitable for later verified reasoning. The objective is not a high average F1 alone: it is complete, correct, generalizable reconstruction, including exact object identity, boundaries, relevance and relationships. A missing object can prevent several true edges from even being considered. An extra object can generate spurious edge candidates. A correct graph must be distinguished from a correct numerical answer obtained accidentally.

Repeated G2 training improved average scores but did not remove systematic semantic failures. G2-0103 to 0109 showed that the errors were not simply wrong labels on otherwise correct endpoint pairs. Strong experts agreed on false edges; lexical identity and ordering shortcuts worked on the old author but failed on new aliases, pronouns and forward references. The frozen G1 extractor then failed on most of the new challenge cases. G1-0110 characterized that upstream failure at scale. This is why the program moved from additional graph training to investigating the interface with the frozen backbone itself.

The governing research question is: **Can we understand the numerical transformation sufficiently to build reliable writers and readers around it, instead of repeatedly retraining the machine?**

## Working architecture

```text
Original prompt (no gold, no computed answer)
        |
        +--> ordinary token embeddings ------------------+
        |                                                |
        +--> optional numerical writer/reference S ------+
                                                         v
                                              Frozen Qwen3-0.6B
                                       /       |        |       \
                                    L7        L14      L21      L28
                                       \       |        |       /
                                      independent / combined readers
                                               |
                                      G1 candidate objects
                                      + confidence / provenance
                                               |
                                      gold-free reconstruction
                                               |
                                      G2 candidate relationships
                                               |
                                      verified graph -> later reasoning
```

The diagram is an experimental architecture, not a description of a promoted pipeline. G1-0113 reads cached representations at embedding input and layers 7, 14, 21, 28. Layer combinations are trainable mixtures, not a claim that information occupies disjoint physical channels. No original *08 prompt may be replaced with a gold or model-generated mathematical target; the established original-prompt contract remains in force. Numerical preparation is an experimental input-embedding intervention on that original prompt.

## Research thesis versus evidence

- **Writer:** We can change the numerical input without changing Qwen. G1-0111 demonstrates an improvement in some held-out semantic extractions; G1-0112 demonstrates independently assigned payload transmission through original prompt positions. This is not proof of arbitrary semantic controllability.
- **Backbone:** It is a fixed nonlinear contextual transformation for this experiment. We can measure sample distributions, layer-dependent decodability, perturbation response, effective rank and restricted local control. These measurements do not characterize all possible inputs, the full internal dynamics or the model's total channel capacity.
- **Reader:** The strongest measured learning leverage in G1-0112 was at the output interface. G1-0113 demonstrates substantial benefits from intermediate-layer readers and complementary competence. Better readout is not necessarily proof that the backbone has an explicit, complete symbolic representation of the answer; reader inductive bias and synthetic templates contribute.
- **Reference beams:** The operator's proposed logarithmic holographic stop codes treat numerical preparation as a structured reference signal: different ways of turning information on/off, amplitude, directional composition and interference, with a compatible numerical reader. This remains a hypothesis. Vector superposition alone is not holography. Do not conflate zero, negative, orthogonal and absent references; a zero reference token is not a neutral input. Test matched norms and ordinary embedding controls before attributing improvement to learned geometry.
- **Generalization rather than cheating:** A numerical writer may learn to prepare unseen prompts; it must not receive gold labels, answer-specific oracle information, or hidden witnesses at inference. Per-case gold-guided control and oracle selection are diagnostics only. Memorization can establish optimization capacity, not generalization. Independent evaluation is required.

## Current experimental decision — conditional acceptance (2026-11-24)

**Research objective: optimize the complete processing pipeline, not each component in isolation.** Accept a component conditionally when the measured evidence supports using it and a downstream component is the more consequential bottleneck. State the premises and explicit falsification conditions. Reopen only the affected investigation if the premise fails. Conditional acceptance is not release, universal generalization, a production certificate, or authorization to unlock G3.

G1-0114 completed: architecture_6 recovered 5,452/5,488 required objects (98.62% object F1, 99.34% recall, 157/192 complete cores, 87/192 strict extractions). The reader inventory contained all gold objects. The G1-0115 integrated laboratory completed its development search and was suspended or may be suspended during the fresh diagnostic; its first operator-reported interim 83/3,008 cases represented six independent bases (five complete). 0114 recovered 1,978/1,981 objects, had 27 extras, 99.2474% F1 and 80/83 complete cores. The 0115 maximum-recall route recovered 1,981/1,981, had 81 extras, 97.9965% F1 and 83/83 complete cores. Both results support using G1 now, subject to downstream falsification. The original historical G2-0087 binding classifier remained around 38.3% F1 despite 100% endpoint reach on the 0115 route. This was not the advanced G2-0103 raw reranker. Do not infer that every current G2 architecture scores 38%. The full 3,008-case 0115 evaluation is no longer a prerequisite for downstream research; preserve its existing seals and shards for later work. No assertion that the population is fully evaluated.

### Conditional premises and reopening conditions

| Premise | Reopen if |
|---|---|
| Reader inventory is sufficiently observable | Required G1 objects are systematically absent in new problem structures. |
| 0114 balanced route and 0115 recall route are usable | Missing endpoints or spurious objects materially reduce downstream graph accuracy. |
| Further G1 optimization is not the current priority | Same-cohort oracle G1 endpoint intervention materially improves G2, or an actual G1 route comparison reveals a consequential deficiency. |
| Frozen Qwen contains useful relationship information | Relationship readers fail to recover gold even under exact endpoint diagnostics; then investigate representations and reader competence rather than presupposing sufficient observability. |

G2-0116 transfers the method: **observability → complementarity → invariance → reconstruction → conditional acceptance**. Use cached 0113 states for development, matched G2 readers on embeddings/layers 7, 14, 21, 28 and combinations; explicit S and lexical controls; seven frozen 0095/0096 constituent experts and the historical 0087 comparator; automatic evidence-integration and greedy reduction; fresh base-disjoint research cohort. Four G1 conditions: original, 0114, 0115 and exact gold endpoints as a separately labeled diagnostic after blind predictions are locked. The exact later 0103 raw-graph reranker adapter is not implemented in this first patch because its source interface could not be inspected; the older 0103 admission-stability module is a different experiment, not a substitute. The seven frozen heads and their new validation-calibrated ensemble do not constitute a replay of the 0103 reranker. Report this distinction.

## Operating procedure

From `main_computer\nanojev`, using the existing `$py` environment:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_multilayer_reconstruction_0116 audit
& $py -u -m tools.nanojev_gamer_v1_g2_multilayer_reconstruction_0116 selftest
& $py -u -m tools.nanojev_gamer_v1_g2_multilayer_reconstruction_0116 run --device cuda
```

The default fresh diagnostic is 12 independent bases, 48 prompts from two author generators, interleaved by base before further variants. This is a research population, not a reliability guarantee or universal certification. Development uses only exposed 0113 groups, validates and selects before fresh generation, and preserves completed reader fits. Fresh prediction shards, numerical cache and gold-endpoint diagnostic shards are resumable. No need to complete 0115 first. See [0116 protocol](G2_0116_MULTILAYER_RELATIONSHIP_RECONSTRUCTION.md). Preserve existing production checkpoints and gates. Record the next scientific decision in the generated `RESEARCH_RECORD.md` and here.

## Research discipline and documentation maintenance

1. Preserve the frozen parent hashes, source/configuration, cohort seal, seeds, cache/checkpoint hashes, selected checkpoint, and raw operator report. Group splits by independent base **before** rendering variants. Reuse fit cohorts only with explicit disclosure. Record whether a test set has previously been opened.
2. Record the hypothesis, intervention, controls, what each arm can access, metrics, actual outcome, causal alternatives and next decision. Separate directly measured results, inference and hypotheses. A local CPU fixture pass is not a CUDA run; an audit is not training; a loss decrease is not held-out success.
3. Measure complete core and strict exact alongside TP/FP/FN, precision/recall/F1, category and transformation errors, clean-case regressions, base-cluster uncertainty and G2 endpoint reachability. Do not treat repeated variants as independent problems. Compare within the same cohort and decoder.
4. Cache expensive frozen features once where practical; diagnose actual errors before adding arbitrary epochs, architectural complexity or data scale. Retain complementary competent arms rather than discarding them based on one aggregate number.
5. Keep research artifacts in `runtime/reports/gamer-v1/`; do not overwrite canonical weights, certify a new G1/G2, or unlock G3 based on these experiments. Existing production gates remain unchanged. Update this guide and the evidence ledger when each operator run completes; record unresolved questions and the next causal test rather than leaving them only in chat.
