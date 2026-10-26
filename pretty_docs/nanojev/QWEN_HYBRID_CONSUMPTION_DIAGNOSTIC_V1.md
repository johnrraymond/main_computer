# Qwen HybridAgent: head-generation vs backbone-consumption diagnostic

**Purpose:** Read-only experiment after `nanojev_qwen_hybrid_alignment_repair` has completed, preserving its failed one-step gate. This diagnostic does not change weights, select a new checkpoint, or authorize deeper training.

## Run

```bash
python -m tools.nanojev_qwen_hybrid_consumption_diagnostic --device cuda
```

A shorter smoke evaluation on the original balanced six-case set:

```bash
python -m tools.nanojev_qwen_hybrid_consumption_diagnostic --device cuda --cases 6 --output "C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_consumption_smoke_v1"
```

For faster inference-side first-answer-token log-probabilities without free-running generation:

```bash
python -m tools.nanojev_qwen_hybrid_consumption_diagnostic --device cuda --no-generation --output "C:\Users\subsi\NanoJev\runs\qwen3_06b_hybrid_consumption_score_only_v1"
```

The default evaluates 18 held-out problems, six from the earlier regression set and twelve from the independently seeded blind set. All conditions use the same prompt and, except where noted, the same entry/exit markers. The diagnostic saves `result.json` under the new output root and prints `HYBRID_CONSUMPTION_CASE` and `HYBRID_CONSUMPTION_RESULT`.

## Controlled interventions

| Condition | Meaning |
| --- | --- |
| `text` | Qwen without latent markers, reference baseline |
| `marker_only` | Enter and exit markers, no injected vector |
| `zero` | One all-zero embedding between markers |
| `intact` | **Production** one-step repaired HybridLink output |
| `teacher_one` | **Diagnostic only**: the true native embedding of the *first answer-free teacher-reasoning token* in exactly one slot |
| `teacher_full` | **Diagnostic only**: all native embeddings of the *answer-free teacher-reasoning prefix*, requiring additional slots |
| `teacher_wrong_full` | Answer-free teacher prefix for the opposite prompt family, preserving a plausible but incorrect reasoning template |

Format activation's answer-free reasoning prefix is the fixed text `Equal and opposite terms cancel. ...` with a `direct` or `composed` continuation. No number, gold residual or gold answer is encoded in either teacher reference. `teacher_one` can be identical for every problem: it is a valid on-manifold token but **not** a fully informative oracle latent representation. An inability to recover the answer from this token alone is *not* evidence that Qwen cannot consume useful latent information. `teacher_full` uses a different number of input slots and must **not** be described as a fair one-step baseline.

The per-case `first_answer_logprob` also probes answer decoding after a fixed `\nAnswer: ` suffix. The true **first answer token is only an indexed log-probability target**; it is never fed into the model. This complements strict `Answer:` parseability and catches some formatting failures. It is a controlled diagnostic, not a learned reasoning capability score.

## Interpretation and boundaries

- `teacher_one` succeeds while `intact` fails: stronger reason to inspect learned head outputs and their alignment, although one first-token embedding may contain little task information.
- `teacher_full` succeeds while `teacher_one` fails: Qwen can use longer answer-free textual context; this does **not** establish that it can consume a **single** useful latent thought.
- All teacher controls fail while text-only succeeds: investigate marker handling, input positions, conditional dependencies, and the downstream model; do **not** claim backbone inability on these tests alone.
- An intact-vector advantage over corrupted vectors is necessary but insufficient for scientific certification; this harness never promotes training or modifies checkpoints.

The saved repair and format checkpoints are checked for drift, inputs are inference-only, the program creates no optimizer, and nonempty output directories are refused. No answer-bearing teacher inputs are used in free-running inference.
