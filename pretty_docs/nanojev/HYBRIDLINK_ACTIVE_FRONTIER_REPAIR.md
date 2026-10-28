# HybridLink Active Frontier + Targeted Repair

**Command:** `python -m tools.nanojev_hybridlink_frontier_repair`

This is the active counterpart to `nanojev_hybridlink_t01_ladder_diagnostic`. The
original T01 cutover accepts T01 on a 12-case T01 gate with 12 retained T00 cases;
this harness deliberately broadens recorded-chain coverage and attempts higher
rungs, but never promotes merely because a loss decreases. It does not change
`nanojev_hybridlink_t01_retention_cutover.py`, its accepted weights, the frozen
Stage1 Qwen, Answer Detector, or numerical heads.

## Preview the available recorded transitions

```powershell
& $py -m tools.nanojev_hybridlink_frontier_repair `
  --stage preview `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --max-rung 5 --cases-per-rung 24
```

`HYBRID_FRONTIER_PREVIEW` reports available chain depths and selected counts
without loading Qwen model weights. **A rung without complete recorded source →
intermediate(s) → answer chains is blocked**; the harness neither fabricates
intermediate gold states nor relabels a two-hop task as a longer task.

## Actual CPU/CUDA frontier run

```powershell
& $py -m tools.nanojev_hybridlink_frontier_repair `
  --stage run --device cuda `
  --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --detector-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_detector_v1\best.pt" `
  --warm-start-t00 "C:\Users\subsi\NanoJev\runs\hybridlink_t00_repair_v1\best.pt" `
  --start-t01 "C:\Users\subsi\NanoJev\runs\hybridlink_t01_retention_v1\best.pt" `
  --cache-dir "C:\Users\subsi\NanoJev\runs\hybridlink_frozen_shared_cache_v1" `
  --output "C:\Users\subsi\NanoJev\runs\hybridlink_active_frontier_v1" `
  --max-rung 4 --cases-per-rung 24 `
  --repair-cycles 6 --repair-steps 150 --check-every 50 `
  --replay-limit 64 --min-gold-cosine 0.60 --max-cosine-drop 0.025
```

Use a **new output directory** on each run. Running the same command against an
output containing `latest.pt`, `pending.pt`, or `result.json` is refused.

To continue from a previous frontier checkpoint, use its `latest.pt` (fully
promoted) or `pending.pt` (partial, **not** promoted) as `--start-t01`, and
use another new output directory. The runner validates every checkpoint's
schema, config hash, Qwen backbone, Stage1, text and detector lineage. It imports
the previous frontier SQLite hard-case bank through a consistent read-only
SQLite backup when available. It also restores the **exact previously audited
case set** and each case's first successful retention baseline; cumulative
forgetting across restarts is rejected. Original T01 and T00 checkpoints are never
modified.

## What the harness does

1. Restores the accepted T01 link and checks all frozen parent hashes. Uses
   **generated feedback** from the starting PROBLEM only; gold intermediate
   and answer Qwen vectors are consulted **after** an autonomous rollout.
2. Builds a seeded coverage set from genuine `train` and `dev` chain suffixes:
   hard numerical forms, easier controls, and reproducible shuffled examples.
   The **original accepted T00/T01 DEV gate cases are mandatory**, regardless
   of hard-case prioritization. A case cap below the original gate size is
   rejected.
3. Audits rung T00, T01, T02, ... in order. Each case records its complete
   generated action/depth/probability, target cosine and detector-margin
   trajectory. Wrong depth, missing answer, or cosine below the configured
   floor is a **failure**. Near-threshold correct exits are **fragile**.
4. Discovers parameter groups for repairs using gradient signal from the
   currently failing chains. The only optimized weights are in the existing
   `HybridLink` generator (`link.0`, `link.1`, `link.3`); Qwen, detector,
   numerical heads stay frozen. Trains on exact offending chain suffixes,
   rehearses previously correct generated trajectories, and includes a small
   proximity penalty to the accepted generator.
5. Tests scaled updates (default 0.125, 0.25, 0.5, 1.0, 0.0625). Rejects any
   candidate that loses **even one previously correct selected case** or
   reduces its per-case target cosine beyond `--max-cosine-drop`. Retention is
   enforced both relative to the immediately previous update **and** to
   historical accepted baselines (cumulative drift cannot evade the gate).
6. Promotes only after a fresh full audit of every selected case in all
   acquired rungs. An incremental safe improvement not yet completing a rung
   is written as `pending.pt`, **not** as a promoted checkpoint. On failure,
   the last accepted rung and original input checkpoints stay untouched.

An experiment may halt on T00 or T01: that means the broader selected cases
exposed gaps even though the original 12-case T01 gate passed. This is
**intended frontier stress**, not invalidation of that accepted gate.

## Outputs

| Artifact | Contents |
|---|---|
| `latest.pt` | Last fully promoted selected-case rung, if any; compatible with recorded frozen lineage. |
| `pending.pt` | Safe partial repair that has **not** passed the current rung. Never call this accepted. |
| `run_config.json` | Model provenance hashes and experiment controls. |
| `selected_cases.json` | Exact recorded chain suffixes selected for every rung; preserved across resumes. |
| `result.json` | Status, highest passed rung, last valid/partial checkpoints, bank counts, limitations. |
| `latest_audit.json` | Full per-case, per-step generated trajectory observations (not hidden-state tensor dumps). |
| `audit_history.jsonl` | A stream of per-case audits across rungs and successful repair cycles. |
| `history.json` | Compact per-rung audit, accepted-repair and advancement chronology. |
| `hard_cases.sqlite3` | Immutable occurrence log plus cumulative scores for failures, fragile passes, historical T00 losses, threatened examples, and successful repairs. |
| `hard_cases.csv` | Prioritized case shortlist for analysis. |
| `hard_numbers.csv` | Numbers that recur in historically difficult/fragile/problematic input expressions. |

Progress events: `HYBRID_FRONTIER_AUDIT`, `HYBRID_FRONTIER_GROUPS`,
`HYBRID_FRONTIER_REPAIR_CHECK`, `HYBRID_FRONTIER_REPAIR_ACCEPT`,
`HYBRID_FRONTIER_STALLED`, `HYBRID_FRONTIER_BLOCKED`,
`HYBRID_FRONTIER_ADVANCE`, and `HYBRID_FRONTIER_RESULT`.

## Boundaries of the claims

- A rung is **passed over explicitly selected recorded cases only**. This
  is not exhaustive certification of every latent transition or arbitrary
  arithmetic. Sampling the same recorded dataset and memorizing it is allowed;
  the report states exactly what was tested.
- A successful stopping decision is not proof of correct semantic arithmetic
  or causal latent reasoning. Target-state cosine and per-hop readout remain
  *diagnostics*. The complex geometry observer is not used as a shortcut
  into feedback.
- This is a **repair experiment**, not a guarantee that every failure can be
  repaired. The search can stall while enforcing zero observed regressions.
- The accepted T01 gate remains the official inherited T01 result; the
  harness does not silently replace its criteria with larger claims.
- Do not raise `--max-cosine-drop` or lower the hard gate just to force
  apparent progress. Instead inspect `latest_audit.json`, the hard-case bank,
  and the results of rejected repair trials.
- For higher-rung training, targets are real states recorded in the existing
  Stage1 deck. This patch does not add automatically generated multi-step
  training chains; it will report their absence if that is the limiting factor.

## Offline verification

```powershell
& $py -m pytest -q tests/test_nanojev_hybridlink_frontier_repair.py `
  tests/test_nanojev_hybridlink_t01_retention_cutover.py `
  tests/test_nanojev_hybridlink_t00_curriculum_repair.py `
  tests/test_nanojev_hybridlink_latent_loop_cutover.py `
  tests/test_nanojev_hybridlink_latent_diagnostics.py
```
