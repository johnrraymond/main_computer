# GAMER V1: register oracle and frozen-representation recovery diagnostic

## Goal and boundaries

`register-oracle` is the first, **read-only** stage of register-resolved GAMER
training. It converts the existing source-certified problem definitions into
explicit, machine-checkable input registers and arithmetic dependencies. It then
trains disposable ridge probes to ask whether each input-register value can be
recovered from the **existing frozen Qwen/LoRA activations**. It does not yet
replace C0 Wide, train LoRA, or implement the learned operation/role router.

```powershell
& $py -m tools.nanojev_gamer_v1_staircase register-oracle `
  --run runtime\reports\gamer-v1\transition-005
```

The command reads the exact certified run, its pinned LoRA/head lineage and the
existing C0 frozen-feature cache. **It does not recapture features**, download
Qwen, resume LoRA, train C0 Wide, or alter production checkpoints. The CPU is
used for register probes even if `--device cuda` is passed.

By default, the command covers every certified training/development case and
all 146 named games. To screen a smaller deterministic sample:

```powershell
& $py -m tools.nanojev_gamer_v1_staircase register-oracle `
  --run runtime\reports\gamer-v1\transition-005 `
  --register-max-games 12 `
  --register-probe-limit 64
```

`--register-max-games 0`, `--register-train-per-game 0` and
`--register-dev-per-game 0` mean all available cases. `--register-probe-limit`
bounds the number of training examples *per role probe*, not the oracle
coverage. An optional `--register-output` specifies a **new**, nonexisting
folder outside the source run.

## Register oracle

The original case generator already supplies:

- Source input observations (`source.parameters` for scalar/transition cases;
  player-by-action payoff cells and selected opponent action for matrices).
- A registered source operation formula and ordered `factor_steps` DAG.
- Verified oracle numerical answers and table-family memberships.

The implementation uses **certified source regeneration** plus an independent
prompt-literal audit to reject missing/unobservable source values. It uses the
registered formula AST to bind operands to **their actual named source
registers**, not merely to another register containing an equal numeric value.
Formula constants are distinguished from public observations. Every factor
result is recomputed with exact rational arithmetic and checked against the
existing numerical oracle.

Each per-case oracle identifies input registers, their semantic roles, exact
values, literal witnesses, the ordered operations, dependency references, and
intermediate results. Intermediates and the final answer are
`SUPERVISION_ONLY_NOT_MODEL_INPUT`. Hidden generator metadata (e.g., affine
sampling parameters, hidden transition types) is not an input register.

**No game/source/role label, register value, table, intermediate result or
answer is passed into the frozen-feature probe input.** Feature arrays are
loaded from the existing original-`prompt_08` cache with their original
certified source fingerprints checked.

## Recovery probes and what they can establish

For each register identity, diagnostic ridge readouts receive only the cached
original-prompt feature vectors and train labels from the oracle. Two routing
conditions are reported:

1. `oracle_game_and_role`: upper-bound-style per-game/per-register decoding,
   which relies on known **oracle game identity**. It is *not* a deployable
   inference path because learned game/role selection is not implemented.
2. `role_only`: pooling all games with matching register IDs, without oracle
   game routing. This still **assumes the register role requested is known**;
   selecting or binding the role in a new game is not yet implemented.

The probes use a deterministic fixed regularization setting, signed-log target
normalization, training-only centering/scaling and disjoint certified dev cases.
Every probe is compared against a training-set-mean baseline. Results include
per-register dev predictions, raw/signed-log errors, per-final-answer zone
(including zero/small negative/small positive), and coverage/skip reasons.

**Interpretation discipline:** Sparse case counts (typically 12 train/3 dev
per game) and linear decoders limit inference. Failure of a ridge probe does
NOT prove necessary information is absent from LoRA; passing it does NOT prove
learned register selection or downstream composition will succeed. These
experiments are preparatory evidence for choosing a slot resolver, token
attention, router or other structured decoder. They do not claim a complete
architecture migration.

## Outputs

The command chooses a unique sibling directory like
`transition-005-register-oracle-0001` and emits:

- `train_register_oracles.jsonl`, `dev_register_oracles.jsonl`: certified
  input-register/operation graphs, intermediate-value supervision and identity.
- `register_catalog.json`: per-game register vocabulary, table families and
  operation frequencies.
- `probe_groups.json`: per-register, per-route loss/baseline and sample counts.
- `probe_predictions.csv`: every held-out register prediction, target and
  baseline, annotated with final-answer numeric zone.
- `events.jsonl`: flushed progress and outcome records.
- `final_report.json`: coverage, frozen source SHA identities, zone metrics,
  explicit limitations, and next experiment gate.

If an experiment fails after its output folder is opened, the failure and
stage are preserved in `failure_report.json` and `events.jsonl`.

## Next training stage (not performed by this diagnostic)

Train a **register selector / role resolver** from the same frozen features,
with oracle labels only as supervision. Then feed predicted registers into the
already-certified arithmetic DAG, and separately train operation selection
where it is not determinable. Verify *zero-line retention*, sign accuracy,
intermediate execution, and game-to-game interference before changing the
production C0 head. LoRA remains frozen until a controlled experiment
identifies missing upstream representations rather than insufficient decoding.
