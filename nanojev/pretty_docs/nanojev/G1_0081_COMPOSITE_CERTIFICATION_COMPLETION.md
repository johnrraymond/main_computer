# G1-0081: Composite G1 Certification Completion

## Purpose

Complete, **without fitting or retuning**, the original G1 developmental evidence omitted by G1-0080. G1-0080 verified the prospective G1-0079 evidence (cutover 107/128 exact, 99.316% micro-F1) but explicitly did not evaluate the current interface on the declared **dev and challenge** splits or run matched causal ablations. This new command measures those missing conditions, exports the exact G1 head and learned interface as a reproducible hash-bound composite, and writes a development-gate decision.

The original G1-0079 learned interface is NOT a native `GamerGraphHead` stage and is NOT consumed by the native G2 curriculum. **Do not interpret a passed composite developmental gate as permission to train G2**. The native G1-to-G2 wiring must be implemented and verified before any native G1 certificate or G2 unlock. Neither original gate values nor previous blocked certificates are changed.

## Prerequisites

Apply all previously issued G1-0072–0080 patches and the G1-0080 coefficient-shape fix, run G1-0079 `prepare`, `fit-predict`, `score` and G1-0080 audit successfully. Run from the `main_computer/nanojev` directory using the existing `$py` PowerShell variable; all other inputs have known default paths relative to this directory.

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_certification_completion_0081 --device cuda
```

The output directory is `../runtime/reports/gamer-v1/oracle-fabric-g1-certification-completion-0081`. The command creates the final directory only after a successful complete evidence export; an evaluation error leaves no partial final output. Existing evidence is never overwritten.

## What the command verifies and measures

1. Reopens and independently validates the G1-0079 seal, one-time scoring chain, 0080 report, source-code commitments, original dataset and model checkpoints. It independently recounts all **128** G1-0079 prospective examples, rather than just trusting the audit's status bit.
2. Reconstructs the exact selected cutover **`pinned_historical_continued_64`**: frozen G1 head from the cutover, structural G1-0074 interface weights, cutover G1-0071 candidate source, frozen G1-0076/0078 discriminators, budget **64** additional proposals, and the G1-0079 quantity pin.
3. Evaluates this interface on the complete original `dev` and `challenge` splits. Prompts and frozen predictions are constructed **without object spans or graph labels**; gold spans are used only afterward for scoring.
4. Repeats the same interface with (a) Qwen hidden vectors explicitly zeroed before the G1 head and (b) the G1 span-logit component explicitly zeroed. Both ablations use the same examples and frozen coefficients. By default, each ablation is performed for **every** dev and challenge example, exceeding the minimum eight cases.
5. Applies the *original* G1 requirements read from `nanojev_gamer_v1_graph_curriculum.py`: dev exact-set rate ≥80%, challenge ≥70%, macro-F1 ≥90% per split, actor/action/quantity recall ≥82% per split, both family types, at least 20/12 cases, and each ablation's net causal exact-set gain ≥8 percentage points per split.
6. Exports a standalone versioned composite **head safetensors + original head manifest + scored interface coefficients and lineage**. This is a research checkpoint; the backbone source adapter remains a separately hash-verified dependency.
7. Produces a decision. All original numerical gates must pass to claim the **composite developmental** gate. Native G1 certification and G2 activation always remain false until integration is actually verified.

## Output

- `evaluations/dev/report.json` and `evaluations/challenge/report.json`: full current-interface exact-set, precision/recall/F1, per-kind and per-family, and paired ablation measurements.
- `evaluations/*/cases.jsonl`: per-example frozen predictions, gold labels (now unsealed/historical), and ablation outcomes.
- `checkpoint/gamer_graph_head.safetensors`: byte-for-byte copy of the verified cutover G1 head.
- `checkpoint/head-manifest.json`: byte-for-byte copy of its manifest.
- `checkpoint/composite-interface.json`: exact frozen coefficient bundles, prior, structured base weights, candidate budget, original model SHA-256, source adapter SHA-256, code hashes and upstream report hashes.
- `certificate.json`: `COMPOSITE_DEVELOPMENT_GATE_PASSED_NOT_NATIVE` or `COMPOSITE_DEVELOPMENT_GATE_BLOCKED`; detailed unmet gates, no native certificate, no G2 unlock.
- `input-integrity.json`: all original pre/post-run protected source digests.

## Hard limits, by design

The original dev and challenge splits have been inspected during the G1-0070–0079 development process. Their replay is **developmental validation**, not a new blind generalization test. The 128-case G1-0079 audit is prospective within the existing author generator and has already been unsealed. This work does not create independent-distribution evidence.

There is no legitimate shortcut from a high composite score to a native G2-ready certificate: G2 must consume the same learned spans that G1-0079's structured interface produces, and the native verifier must bind that exact interface to any stage certificate. G1-0081 does not silently modify `GamerGraphHead`, `graph_curriculum`, source adapter, existing G1 certificate, or any G2 weights.

If a numerical gate fails, retain the recorded failure and resolve it through a new experiment; do not modify source thresholds, swap the selected interface post hoc, or relabel the old audit as a new challenge.
