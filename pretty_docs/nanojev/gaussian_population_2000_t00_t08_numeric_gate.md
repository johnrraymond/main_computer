# 2,000 Gaussian banks: T00–T08 numerical reconstruction gate

## Acceptance contract

The target is **at least 80% of presentations in every tier T00 through T08**, with **both** reconstructed numerical answers, X and Y, within **5% of their own oracle values on the same presentation**. The test is separate for each tier; pooled performance and Z/ARGMAX accuracy cannot substitute for this gate.

For nonzero oracle values, the absolute prediction error must be at most `0.05 * abs(oracle)`. For a zero oracle value, only an exactly zero reconstructed value passes. This is deliberately strict, and the report counts zero targets separately. The gate reports X, Y, and joint X/Y hit rates per tier, plus the number of independent count instances. Each mutation presentation is an observation; masks of a common instance are not independent training examples. A grouped instance success diagnostic is included.

DEV controls checkpoint choice. TEST is held out for final reporting, never for gradients or promotion. T00 supplies both oracle numerals as visible inputs, T01–T02 supply one oracle numeral; T03–T08 do not supply raw oracle values. Tier labels and parsed numeric literals are structured features, not a raw-text language model.

## First audit the existing best DEV checkpoint (no retraining)

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_conditional audit `
    --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_population_2000_conditional_v1\best.pt" `
    --database runtime/hawk-dove-million/teacher.sqlite3 `
    --device cuda `
    --splits dev,test `
    --tolerance 0.05 `
    --min-rate 0.80 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_population_2000_conditional_v1\t00_t08_gate_80in5.json"
```

The checkpoint and database SHA-256 are checked for identity. The audit is read-only and writes only the requested JSON report. A `FAIL` report is a valid observed result and does not imply a harness error. Already-seen TEST outcomes should not be used for tuning hyperparameters.

## Fresh tier-gated training experiment

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_conditional train `
    --database runtime/hawk-dove-million/teacher.sqlite3 `
    --banks 2000 `
    --selection tier-gate `
    --device cuda `
    --steps 15000 `
    --probe-every 500 `
    --batch-size 64 `
    --lr 0.003 `
    --min-lr 0.00003 `
    --view-cases 2 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_population_2000_tier_gate_v1"
```

Every DEV probe reports the weakest-tier joint numerical success and whether all nine tiers reach the gate. With `--selection tier-gate`, checkpoint promotion maximizes the minimum tier's joint success rate, then the unweighted mean of nine tier rates, then uses density-TV as a tie-breaker. It does **not** change model architecture or gradients. An improvement in checkpoint selection cannot guarantee reaching the gate if the model cannot learn the missing numerical accuracy.

The final summary includes DEV and TEST numeric gates and retains the original density and Z metrics. Run the `audit` command on the new `best.pt` to obtain independently reusable per-tier evidence. To compare with original behavior, omit `--selection tier-gate` or use `--selection dev-tv` in a different output directory.

## Scope of the result

This measures conditional numerical reconstruction using 2,000 Gaussian components per output on the existing fixed Hawk–Dove teacher fixture and its independent TRAIN/DEV/TEST count splits. It does not establish generalization to withheld payoff matrices or to raw-token understanding. Do not interpret the 100% Z decision score as evidence of the 80%-within-5% numerical gate.
