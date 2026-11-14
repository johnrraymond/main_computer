# NanoJev four-bank outward ladder: best distinct pair gate

## Selection objective

The four complete, independently optimized candidate backbones are:
`left.left`, `left.right`, `right.left`, and `right.right`. Their prefixes
identify training lineage, **not mandatory final roles**.

Each DEV probe finds an **ordered pair of distinct banks**, choosing the Left
candidate and Right candidate from *any* of the four, including two from the
same EMP. An eligible Left bank scores at least `--left-gate` on both frontier
DEV views **and** both outer DEV views; the Right bank independently satisfies
`--right-gate` on the same four decks. For each bank its ranking number is the
**minimum** 5%-relative accuracy across those decks. Among eligible pairs,
prefer the highest Left worst-deck accuracy, then highest Right; ties are stable.
No bank can fill both roles in a single pair.

All four banks **continue training**. Selection is **provisional per probe**;
`--gate-streak 2` counts two consecutive probes with *any* qualifying distinct
pair, not necessarily the same pairing. The output includes `left_bank`,
`right_bank`, selected bank scores, bias/error stats from the existing monitor,
and all four banks' tipping diagnostics. Per-probe history and newly accepted
checkpoints record the chosen pair and gate policy. Final deployment winners
are chosen *after* training and independent validation; the gate does not
produce final certification or assert previously accepted radii satisfy this
new policy.

## Resume the current ±8,192 run

Stop the trainer before applying this patch; **do not** run two trainers in the
same campaign. Apply the new ZIP via `python new_patch.py <zip-path>`.

A campaign created with the original fixed-bank ladder has a source-code SHA
pin and accepted-stage checksum lineage. We **do not** overwrite any existing
`config.json`, `accepted.pt`, `acceptance.json`, or completed stage config to
migrate it. On the first resumed run only, authorize an explicit sidecar
`best_pair_gate_migration.json`. It records the legacy code SHA, the new source
SHA, the original campaign config SHA, and the new gate policy. The old
acceptances remain historical events under their original gate.

```powershell
& $py -m tools.nanojev_emp_dual_bank_error_monitor `
    --source-run "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_512_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --device cuda `
    --target 4000000 `
    --left-gate 0.80 `
    --right-gate 0.50 `
    --gate-streak 2 `
    --stage-steps 400000 `
    --probe-every 250 `
    --max-stages 16 `
    --resume `
    --migrate-best-pair
```

Step budgets must exceed the current step for a new training probe. The
published diagnostic had reached step 302,000 at radius 8,192, so 400,000 is
an illustrative upper budget, **not** a mandatory run length. The stage may
advance as soon as two qualifying DEV probes occur. Increasing the stage
budget later is allowed. Future resumes need only `--resume`: the migration
sidecar is validated automatically. Fresh campaigns use the new policy
without migration.

## Safety and reporting

- **No change** to batch generation, per-bank gradient calculation, learning
  rates, optimizers, model weights, or the existing error/bias monitor.
- An old checkpoint's `streak` is discarded on migration because it was
  awarded under the former fixed-bank policy; trained weights and optimizer
  state are restored normally.
- The accepted checkpoint retains all **four** banks. `selected_pair` records
  the pair that met the gate at acceptance, not a pruned artifact.
- A campaign with an unknown or modified historical source code SHA is
  refused instead of silently reinterpreted. A modified sidecar is refused.
- Existing accepted radii are preserved exactly and are **not retroactively
  relabeled** as best-pair demonstrations. The sealed TEST split remains
  untouched during the outward DEV acquisition ladder.
