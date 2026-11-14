# Qwen frontier information-rate test: LoRA 1 versus additive LoRA 2

This is a **Qwen attention-adapter diagnostic**, not a HybridLink, numerical-head, or terminal-glass experiment. It introduces a new entry point and does not rewrite the existing `nanojev_qwen_lm_frontier_rate_diagnostic` A/B/C glass runner.

## Exact A/B experimental arms

All begin with the **same frozen pretrained Qwen and the same trained LoRA 1 checkpoint**. They receive the same deterministic failure/replay training sequence and use the same disjoint greedy-evaluation deck.

- **A (`A_continue`)**: train existing q/v LoRA 1, preserving AdamW moment state from the starting checkpoint. This is a true continuation control.
- **B (`B_stack`)**: preserve and freeze original Qwen **and** LoRA 1; insert a *second* q/v low-rank adapter, zero initialized in its B matrices. Train only its A/B matrices. Rank and scaling match LoRA 1. No merging or vocabulary-glass insertion.

There is **no optimizer-reset control or third branch**. The experiment compares continuing the first adapter with freezing it and training a second additive adapter.

At each q/v projection, the additive arm computes `base(x) + LoRA1(x) + LoRA2(x)`. At time zero, `LoRA2(x) == 0` and its result must equal the source checkpoint's output. Both branches expose the same number of *trainable* adapter parameters, but the additive arm has more **total** representational capacity; the experiment tests an engineering crossover, not the isolated causal effect of freezing.

## Installation

Install the manifest-free ZIP through the existing `python new_patch.py "<downloaded-file>.zip"` workflow.

## Command

Pause other training on the same GPU, to avoid contaminating timing:

```powershell
& $py -m tools.nanojev_qwen_lm_lora_stack_rate_diagnostic `
    --stage run --device cuda `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --balance-output "C:\Users\subsi\NanoJev\runs\qwen_lm_frontier_mixed_balance_v1" `
    --budget-seconds 120 --check-seconds 40
```

Optionally specify `--source-checkpoint <path>` to choose a particular raw Qwen checkpoint; otherwise it reads `best.pt` from `--balance-output`. The starting checkpoint must contain LoRA 1 tensors **and** its optimizer. The experiment snapshots that checkpoint to a dedicated output tree so source weights and the live raw optimizer remain untouched.

Default output: `C:\Users\subsi\NanoJev\runs\qwen_lm_lora_stack_rate_ab_v2`. Run `--stage preview` for a read-only deck inspection. Rerun `--stage run` with identical settings to resume. Use `--stage report` to print saved results. An existing comparison's settings and decks cannot be silently changed.

## Measurements

`QWEN_LORA_RATE_CHECK` reports gained, lost, net, and net correct per GPU-hour-reserved **training time** for `all`, `fresh`, `fresh_expanded`, and `archive_eval`. Also reports net per hour inclusive of repeated evaluation time. These values use **the same paired evaluation questions** before and after training, and cannot be interpreted as global mathematical accuracy.

`QWEN_LORA_RATE_DONE` reports A and B and relative acquisition rates. The output keeps individual checkpoint histories (`*_curve.jsonl`), a final resumable checkpoint (`*_working.pt`), an exploratory best checkpoint (`*_best.pt`), and the final comparison (`report.json`). The run measures greedy answer correctness, not only teacher-forced loss. Best-curve selection is not independent verification and should be replicated with new fresh-frontier probes at later source steps before selecting a training strategy.

`--eval-per-cell 5 --eval-frontier-per-cell 5 --extra-levels 2` (defaults) deliberately sample more advanced levels and use a much larger evaluation than the initial 74-case diagnostic. Historical cases are a separately labeled enriched cohort. If the training deck contains no remaining greedy failures, the diagnostic refuses to infer a growth rate: expand difficulty or supply a different source checkpoint.

## Known limitations

- This compares q/v attention LoRA adaptation within the text-decoding frontier pipeline. It does not train HybridLink or the numerical math head.
- Same wall time does not entail same number of optimization steps; both are recorded. Timings require exclusive use of the GPU.
- The initial loss plateau does not establish zero accuracy growth; the raw continuation is the proper control.
- Training and evaluation are disjoint, but repeated evaluation can still create statistical selection bias in best checkpoint selection.
- This is an empirical diagnostic of the *rate* of net competence growth. A larger, harder frontier and multiple checkpoints are needed before changing the main training policy.
- CUDA performance must be measured on the user's actual device; CPU unit tests only establish behavior/invariants.
