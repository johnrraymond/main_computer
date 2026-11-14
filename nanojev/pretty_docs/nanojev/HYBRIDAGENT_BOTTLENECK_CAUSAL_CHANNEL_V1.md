# HybridAgent: question-hidden causal latent-channel experiment (v1)

This is a **new, experimental checkpoint lineage** for Qwen3-0.6B + HybridLink, not a revision to the published HybridLink internal architecture. It addresses a demonstrated failure of the previous causal joint run: 15/18 answers with intact latent feedback, 16/18 with mismatched feedback, and 15/18 with zero feedback. Answer prediction improved but latent information was not proven useful.

## Start

```text
python -m tools.nanojev_qwen_hybrid_bottleneck --device cuda
```

The launcher is a **master harness**: gradient audit → question-hidden channel training → separate qualification set → question-visible restoration with channel replay → one-shot held-out final test. It stops before restoration if the channel qualification fails. It never overwrites any of the earlier checkpoints.

Default root: `~/NanoJev/runs/qwen3_06b_hybrid_bottleneck_v1`. Required source root: `~/NanoJev/runs/qwen3_06b_hybrid_causal_joint_v1` with a complete previous training result and gate. Prior `ONE_STEP_FAIL` is accepted as evidence, not used as a prohibition on experiments in a separate lineage. The original format and geometry lineages are checked before training.

## Explicit stages

```text
python -m tools.nanojev_qwen_hybrid_bottleneck --stage gradient --device cuda
python -m tools.nanojev_qwen_hybrid_bottleneck --stage channel --device cuda
python -m tools.nanojev_qwen_hybrid_bottleneck --stage qualify --device cuda
python -m tools.nanojev_qwen_hybrid_bottleneck --stage restore --device cuda
python -m tools.nanojev_qwen_hybrid_bottleneck --stage gate --device cuda
```

Channel training defaults to 480 updates, restore to 160; adjust with `--channel-steps`, `--restore-steps`, `--eval-every`. A deliberately interrupted run can be resumed with the **same arguments** and `--resume`. To run an independent experimental configuration, select a **new output directory**; changing the configuration of an existing run is refused. The final gate is opened once, and interrupted gates cannot be opportunistically retried against the same held-out examples.

## Stage contracts

1. **Gradient audit.** Compute gradient from full-answer cross-entropy alone, measure norms on the injected latent output, HybridLink weights, and attention LoRA weights independently. Check for nonfinite gradients, frozen base weights, and deliberately detached producer source hidden states. A positive gradient proves a differentiable path, *not* successful information transfer.
2. **Constrained channel.** The producer sees the complete signed-cancellation expression and generates a **self-generated** latent through HybridLink. The consumer's only task-specific input is this vector. It receives a fixed public prompt and `<latent>` / `</latent>` markers; the producer's question tokens and KV cache never enter the consumer. No oracle teacher thoughts or target answers are used as inference inputs. Training uses full answer-token cross-entropy plus margin ranking against zeros and self-generated vectors from different examples. Mismatches are chosen by a fixed permutation, without selecting by gold label. Producer hidden states are detached as a 6-GB-memory compromise; gradient still updates the head and consumer LoRA.
3. **Qualification.** Evaluate with an *independent* (sign- and family-balanced) 18-case set of previously unseen large operands. Intact must beat both zero and mismatch by at least two answers and achieve at least 50% correct. Failure is `CHANNEL_FAIL`; no restoration or final test is allowed.
4. **Protected restoration.** Restore the full question-visible path. Jointly train normal-task answer loss while continuing to replay the constrained channel loss. Select the best checkpoint using **both** normal-task development loss and channel retention loss, not channel geometry alone.
5. **Final gate.** On a **different** 18-example held-out set, compare intact, zero, mismatch, marker-only, and question-visible text-only conditions under the **same trained model**. Also repeat the separate question-hidden consumer check. Qualification and final tests never supply oracle latents. The gate requires an intact advantage over both corrupted controls **in both question-hidden and question-visible conditions**, plus no more than one lost correct answer versus text-only under the same checkpoint. `ONE_STEP_FAIL` must not authorize deeper recurrent training.

The train/dev/test magnitude intervals remain disjoint as in `nanojev_qwen_zero_line_backbone.make_cases`: roughly hundreds-to-tens-of-thousands for training, hundreds-of-thousands for development, and millions for qualification/final tests. It tests transfer of small positive/negative/exact-zero cancellation residuals across unseen large operands.

## Output markers

- `HYBRID_BOT_GRADIENT` — head, LoRA and latent gradient measurements.
- `HYBRID_BOT_CHANNEL_STEP`, `HYBRID_BOT_CHANNEL_RESULT` — information-channel training.
- `HYBRID_BOT_QUALIFY` — early qualification decision; `CHANNEL_FAIL` intentionally stops the master harness.
- `HYBRID_BOT_RESTORE_STEP`, `HYBRID_BOT_RESTORE_RESULT` — normal operation restored with a retention replay objective.
- `HYBRID_BOT_GATE_RESULT` — final independent held-out scores and authorization decision.
- `HYBRID_BOT_RESULT` — terminal compact summary; `HYBRID_BOT_REFUSED` — an explicit contract violation.

All metrics and complete per-case generations are stored under the run directory. Earlier checkpoints are input artifacts, not mutation targets.

## Scientific limits

- This is **not** a full-gradient HybridSFT reproduction: the source pass is detached and the decoder is an approximation for constrained VRAM.
- Successful masked-channel transfer would establish that task information survives the latent boundary; it would **not** establish a generally useful arithmetic algorithm or recurrent planning capability.
- Failing the channel qualification is a legitimate outcome; it means the shortcut is blocked, but the architecture/training budget still needs work.
- Passing the final gate requires strictly better intact feedback than mismatched and zeroed feedback, not merely lower teacher-forced loss.
- The final gate's nominal ±2-answer margins on 18 cases are *screening thresholds*, not statistically conclusive evidence. Follow-up replication on substantially larger disjoint sets is required.
- Tests included here run with a causal miniature model on CPU. CUDA training and blind answer improvements must be established on the user's actual Qwen environment.
