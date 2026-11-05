# GAMER V1 — Per-update numerical failure localization

Opt-in, isolated experiment for the numerical head's catastrophic cross-game
sign and magnitude regressions. This mode does **not** train or reload LoRA,
does not alter the active staircase or head weights, and does not promote a
resulting head. It uses the verified `prompt_08` frozen LoRA feature cache.

## Invocation

```powershell
& $py -m tools.nanojev_gamer_v1_staircase diagnostic-localize `
  --run runtime\reports\gamer-v1\transition-005 `
  --device cuda
```

Default: `captain_crew_risk` and `bertrand_oligopoly`, 20 consecutive updates,
seeds 1729 and 2738, each from both a fresh C0 Wide head and the saved
step-2000 head **with its existing optimizer state**. This reproduces the
same first 20 isolated-game batch selections as the original 30-step fresh-head
experiments. If you want a mixed-workload control:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase diagnostic-localize `
  --run runtime\reports\gamer-v1\transition-005 `
  --device cuda `
  --localize-games captain_crew_risk,bertrand_oligopoly,mixed
```

`--localize-start fresh|checkpoint|both`, `--localize-steps N`,
`--localize-seeds N`, `--localize-radius R`, and `--localize-output DIR` are
exclusive to this subcommand. Output defaults to a **new sibling** directory
such as `transition-005-diagnostic-localize-0001`.

## Observable evidence

Each optimizer step logs the exact training indices and numerical regimes, the
corrected and weighted auxiliary objectives, groupwise gradient cosine, clipping
and per-layer parameter-update norms. Every development example is evaluated
*after each update* (not every ten steps); sampled training examples are also
recorded before and after the update.

The actual forward path is decomposed as LayerNorm; three Linear/GELU pairs;
linear/GELU bottleneck; corrected signed-log scalar, plus separate auxiliary
signed-log scalar; and the inverse signed-log that produces the reported
numeric answer. `layer_traces.jsonl` records per-stage activation RMS, changed
fraction/cosine, sign distribution, and update provenance.

For each trainable component, two independent causal counterfactuals are
performed on the same inputs: restore its old weights inside the updated head
(necessity), and apply its new weights alone to the previous head (sufficiency).
The comparisons include the complete corrected readout, its weight and bias
separately, the bottleneck weight and bias separately, encoder linears,
LayerNorm, and auxiliary readout. All counterfactual weights are restored
before resuming the real experimental update. The report **does not call one
parameter the root cause** unless both necessity and sufficiency are measured,
and even then its verdict applies to that **particular update only**.

The fixed inverse transform cannot change the sign of the corrected scalar.
The log records the signed-log value, raw value, and inverse amplification
factor for every dev case, so a sign collapse inside the head is distinguishable
from a large raw numerical error caused by exponentiation.

## Files

- `final_report.json` — full trial outcomes, first observed regression and
  sign-collapse steps, bounded candidate layers, limitations.
- `events.jsonl` — flushed event stream, including full step evidence.
- `layer_traces.jsonl` — per-step activation and module weight/gradient telemetry.
- `localization_cases.csv` — **every** dev prediction plus selected train-batch
  predictions before and after each step (including both heads and inverse gain).
- `counterfactuals.csv` — each component intervention's full-dev cohort outcomes.
- `counterfactual_cases.csv` — counterfactual predictions for sign/tolerance
  regressions; identifies which layer repairs each specific failure.
- `forgetting.jsonl` — per-case regression and large inverse-amplification alerts.
- `checkpoints/` — disposable per-trial start, first update, final, and first
  observed sign-collapse snapshots.
- `provenance.json`, `partial_summary.json`, and `failure_report.json` — exact
  source hashes, incremental progress, and crash forensics.

The diagnostic never performs more LoRA training. A result may implicate a
readout bias, bottleneck weights, encoder layers, auxiliary gradient pressure,
or amplification, or may conclude `NOT_ISOLATED` when layers interact. It is
not a production acceptance or deployment gate.
