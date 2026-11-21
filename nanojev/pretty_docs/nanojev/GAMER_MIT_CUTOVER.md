# Gamer MIT version — G2 cutover

Version name: **MIT**. This is our experimental adaptation of ideas from recent MIT mathematical-AI research, not software or a model published or certified by MIT. The cutover is specifically the G2 relationship-learning and graph-assembly path; G1 and later-stage neural weights remain as they were.

## What actually changes

- **MIT becomes the default** of the versioned Gamer G2 entry point `tools.nanojev_gamer_v1_g2_active`. `--version legacy` continues to execute the original 0088 greedy joint decoder on the identical 0087 checkpoint and predicted G1 objects.
- **Hard-constrained edge projection:** the MIT decoder maximizes the G2 neural margin under one-label-per-pair, no-self-edge, frozen per-type outgoing/incoming capacities, and maximum graph size. Unlike the old greedy algorithm, SciPy/HiGHS must prove optimality. It considers up to four model-predicted edge labels per ordered pair rather than silently discarding all but the argmax. It is NOT guaranteed to predict the correct semantic relations.
- **Targeted sparse weight edits:** optional training selects high-gradient-saliency coordinates only in `pair_projection.weight`, `pair_projection.bias`, `binding.weight`, and `binding.bias`; locks every other coefficient, enforces a 0.2% selected-coordinate budget of the editable surface and 0.03 absolute coefficient displacement, then verifies the saved checkpoint against the immutable 0087 parent. This is a gradient-guided weight-space experiment, not an implementation of the paper's LLM-mediated weight-editing procedure.
- **Source lineage:** parent G2-0087 and decoder G2-0088 must pass their existing provenance checks. Checkpoint material is never overwritten. A newly created MIT campaign has an isolated folder with separate training evidence and a new checkpoint. No certification, production promotion, or G3 unlock is claimed.
- **Blinded experiment:** a distinct dev/challenge cohort is sealed before training. Inference reads only prompts, locks predictions, and only then scores. Paired arms: `legacy_greedy`, `mit_projection_only`, `mit_weight_edit_only`, `mit_full`. Per-family exact graph rates, edge F1 and deltas are reported. The cohort comes from the same synthetic source generator, so this is *not* proof of independent-distribution generalization.

## Commands

Run from `main_computer\nanojev` using the NanoJev Python environment `$py` (SciPy, NumPy, PyTorch, and safetensors required).

Version lookup (no GPU or checkpoint required):

```powershell
& $py -m tools.nanojev_gamer_v1_g2_active version
```

Default MIT query, using existing G2-0087 weights and exact projection immediately. Once a MIT training campaign is complete it automatically uses that campaign's sparse-edited G2 checkpoint:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_active query --device cuda --prompt "example game prompt"
```

Rollback comparator using the original unmodified G2-0087 weights and 0088 joint decoder:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_active query --version legacy --device cuda --prompt "example game prompt"
```

A full sealed MIT G2 training/comparison campaign (requires the existing G1, G2-0087, G2-0088 assets under `..\runtime\reports\gamer-v1`):

```powershell
& $py -u -m tools.nanojev_gamer_v1_mit run --device cuda
```

Or split its phases across separate processes to preserve the gold-blind workflow and recover from interruptions:

```powershell
& $py -u -m tools.nanojev_gamer_v1_mit prepare
& $py -u -m tools.nanojev_gamer_v1_mit train --device cuda
& $py -u -m tools.nanojev_gamer_v1_mit predict --device cuda
& $py -u -m tools.nanojev_gamer_v1_mit score
```

Results: `..\runtime\reports\gamer-v1\oracle-fabric-g2-mit-0001\scored\report.json` and `scored\cases.jsonl`. The MIT decoder is exact for its *limited structural objective*; these scores determine whether it improves actual graph correctness. If the MILP solver cannot prove optimality under the budget, inference refuses rather than falsely labeling a heuristic result optimal.

## Cutover boundary and limitations

This patch is scoped to the supplied `nanojev` snapshot. It makes MIT the default of the **versioned G2** entry point, and keeps a functioning legacy rollback. It does not alter the broader Main Computer server/router (not present in this snapshot), does not automatically activate a trained checkpoint that does not yet exist, and does not certify G2 or unlock G3.

The original G2 weights, G1 composition, decoder-selection files, existing tests and historical evidence remain immutable. When live, the projection-only MIT variant has new behavior even before new training; sparse intervention activates once the campaign completes. Pinned runtime assets are required for actual G2 execution and experimental scores.

## Source ideas

- Weight-space intervention: Rikiya Takehi et al., *Sparks of Weight-Space Programming*, https://rikiyat.github.io/blog/weight-space-programming/
- Constraint-enforcing neural models: MIT News, *New approach enables physically consistent AI models of chaotic systems*, November 2026, https://news.mit.edu/2026/new-approach-enables-physically-consistent-ai-models-chaotic-systems-1120

Neither result establishes that these particular G2 changes will improve performance; that claim is tested through the paired frozen evaluation.
