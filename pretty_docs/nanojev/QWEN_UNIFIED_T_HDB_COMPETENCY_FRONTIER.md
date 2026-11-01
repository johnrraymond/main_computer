> **Current joint frontier (v3):** The T/HDB-only joint scheduler described below has been superseded by the three-population T + HDB + nine mathematical-family runner. See `pretty_docs/nanojev/QWEN_UNIFIED_MATHEMATICAL_T_HDB_FRONTIER_V3.md`. Existing v2 checkpoints are migrated without losing LoRA or optimizer state.

# Qwen unified numerical frontier: T00–T08 + Balanced Hawks and Doves HDB00–HDB08

## Governing contract — numeric balance only

The purpose of the frontier is to discover and enlarge Qwen's **exact numerical competence**. T and HDB train in **one LoRA and one optimizer**, not in successive training stages.

- **T00–T08**: canonical signed-addition one-step reduction, with independent full-chain evaluation.
- **HDB00–HDB06**: zero-line *signed-addition problems using exactly the T packet format*; HDB levels refer to specific cancellation structures, and merge onto T rungs 00, 01, 02, 03, 03, 05, and 07 respectively.
- **HDB07**: encounter counts, signed per-encounter contributions, and an explicit numerical expression; produce the exact integer balance.
- **HDB08**: encounter narrative plus payoff coefficients, without explicit balance expression; derive and produce the exact integer balance.
- **NEVER** target `HAWK`, `DOVE`, `TIE`, `ARGMAX`, or auxiliary X/Y totals in this numeric frontier. Those describe a superseded classifier experiment, not this contract.

For one million encounters, define the **integer balance in half-point units** by

`B = (-p * c * hawk_count) + (q * c * dove_count)`

where `c,p,q` are positive integers, `hawk_count + dove_count = 1000000`, and `B` is a signed integer. The exact zero crossing is `hawk_count = 1000000*q/(p+q)`, requiring integral crossings in the current training and evaluation families. Near this pivot, sample offsets `-k, 0, +k` and preserve exact sign and magnitude. Teacher calculations are exact integers. The model must emit `PROBLEM: <signed integer>` for terminal tasks. T reduction tasks use the T grammar and canonical **next** `PROBLEM:` packet as target.

For the historical payoff example Hawk–Hawk -1, Hawk–Dove 3, Dove–Hawk 0, Dove–Dove 1.5, choose `c=1,p=2,q=3`. Then `B = -2H+3D`: when `H=599999,D=400001`, `B=5` (=2.5 original points). The neutral encounter count is `H=600000,D=400000`. Correct results at `H=600001,600000,599999` are `-5,0,+5` respectively.

## The nine tiers and merge points

| HDB tier | Model input | Target contract | T merge |
|---|---|---|---|
| HDB00 | `PROBLEM: (-1199998)+1200003` | `PROBLEM: 5` | T00 |
| HDB01 | `PROBLEM: ((-599999)+(-599999))+1200003` | first reduction `PROBLEM: (-1199998)+1200003` | T01 |
| HDB02 | `PROBLEM: (((-1199998)+400001)+400001)+400001` | first canonical reduction | T02 |
| HDB03 | 2 negative and 3 positive signed components | first canonical reduction | T03 |
| HDB04 | same 5 contributions reordered to alternate signs | first canonical reduction | T03 |
| HDB05 | 3 negative and 4 positive signed components | first canonical reduction | T05 |
| HDB06 | 4 negative and 5 positive signed components | first canonical reduction | T07 |
| HDB07 | counts, coefficients, explicit `PROBLEM: (-2*599999)+(3*400001)` | exact terminal `PROBLEM: 5` | constructs numeric T problem |
| HDB08 | counts and payoff narrative (no explicit formula) | exact terminal `PROBLEM: 5` | reconstructs numeric T problem |

HDB00–06 have both **one-step** and sampled **autonomous full-chain** diagnostics. Correct first reductions do NOT count as correct full-chain balance unless the model generates all remaining reductions and reaches the exact terminal. HDB07/08 are supervised directly on their exact numeric terminal, not a strategy decision. Distinguish these acquisition measures; do not merge them into a single unsupported arithmetic-capability claim.

For HDB08, the mathematical payoff matrix constructed by the teacher satisfies these half-point-unit differences: Hawk–Hawk minus Dove–Hawk `= -c*p`; Hawk–Dove minus Dove–Dove `= c*q`. The familiar original payoff matrix is recovered for `c=1,p=2,q=3`.

## Scheduling and frontier logic

- Every optimizer step includes both T and HDB training. HDB-major minibatches contain a negative, exact-zero, and positive case for the **same tier and coefficients**, plus T replay. T-major minibatches include three T reductions and one fresh HDB example. Default batch size is four.
- The HDB encounter offset widens 1→2→4→8→16→32→64 while ties and both signs remain trainable. Training coefficient families and heldout coefficient families are disjoint.
- T keeps extending its signed-result frontier from 64 toward 256 with refreshed problem pools; its current sampled operands are bounded by `--operand-limit 1024`. HDB uses the same reduction grammar on larger, million-encounter-derived integers; HDB diagnostics must be reported separately rather than claiming T's ordinary 1024-operand deck certifies million-scale competence.
- Priority comes from paired fixed-deck gains/losses, accuracy and direct training exposure. Holdout prompts must not become training rows.
- Preserve the arithmetic `STRESSED` source checkpoint and optimizer state. The corrected numerical joint experiment starts in a new isolated **v2 workspace**, not the older `_joint_t_hdb_frontier_v1` workspace which contains categorical HDB LoRA training. It does not use the wrong classifier checkpoint as parent.

## Explicit PowerShell launch — no argument arrays

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage run `
    --frontier `
    --frontier-hdb `
    --device cuda `
    --terminal-mode numeric `
    --stage1-checkpoint "C:\Users\subsi\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
    --text-checkpoint "C:\Users\subsi\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1" `
    --radii 0,1,2,3,4 `
    --operand-limit 1024 `
    --max-rung 8 `
    --batch-size 4 `
    --eval-batch 8 `
    --frontier-steps 1200 `
    --frontier-check-every 100 `
    --frontier-max-radius 64 `
    --local-radius 4 `
    --joint-steps 1200 `
    --joint-check-every 100 `
    --joint-t-max-radius 256 `
    --joint-t-refresh-every 20 `
    --hdb-max-offset 64 `
    --hdb-max-seq 768 `
    --hdb-max-new-tokens 48
```

Default workspace:

`C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1_aggressive_frontier_v1_joint_t_hdb_numeric_frontier_v2`

Rerunning the same command resumes this **v2** checkpoint, optimizer and training exposure. Any policy, source SHA, or evaluation-deck change is a hard error. Never run concurrent writers to the same joint workspace.

## Diagnostic outputs

At each check, `QWEN_JOINT_FRONTIER_CHECK` includes per-family net acquisition and the **HDB full-balance chain success count**, in addition to original arithmetic chain performance. `QWEN_HDB_BALANCE_CHAIN_PROGRESS` reports the autonomous heldout chain check. Reports include:

- `hdb` — one-step exact T-packet accuracy for HDB00–06; direct exact terminal accuracy for HDB07/08; per-tier gains/losses/exposure and sign/offset family splits.
- `hdb_numeric_balance_chains` — autonomous full-balance accuracy and first failing rung on 27 heldout cases: one reserved matrix × nine HDB tiers × offset -1/0/+1; these are a small diagnostic, not exhaustive certification.
- `t` — the independent signed-integer T frontier and per-rung retention.
- `chains` — independent T full-chain accuracy.
- `hdb_difficulty_report.json` / `hdb_difficulty_failures.jsonl` — numerical failures categorized as `wrong_numeric_reduction`, `wrong_numeric_balance`, `invalid_numeric_packet`, or `empty_output`, plus precise expected and emitted packets. Reports still support reading archived choice-checkpoint evidence, but **new numerical training does not use those targets**.

Read already saved numerical weakness measurements without CUDA inference:

```powershell
& $py -m tools.nanojev_qwen_lm_radius_ladder `
    --stage hdb-difficulty `
    --frontier `
    --frontier-hdb `
    --output "C:\Users\subsi\NanoJev\runs\qwen_lm_numeric_terminal_v1"
```

The report exists only after the first completed training check. `--stage status --frontier --frontier-hdb` and `--stage competency --frontier --frontier-hdb` read the same v2 workspace.

## Verification boundary

CPU tests validate exact arithmetic, rung mapping, canonical packets, zero-line crossing, disjoint evaluation coefficient families, mock training/resume, and autonomous-chain scoring. They do not certify GPU training throughput, numerical generalization, or latent multiplication ability. T/HDB both train textual `PROBLEM:` packets on Qwen LoRA; this stage does **not** run HybridLink's separate numerical head.
