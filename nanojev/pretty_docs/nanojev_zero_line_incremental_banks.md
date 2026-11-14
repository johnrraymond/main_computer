# NanoJev: incremental signed-residual Gaussian banks

## Research question

Can we **append** new numerical capacity for signed residual intervals while keeping
previously demonstrated behavior untouched? This differs from the prior
Gaussian-radius experiment, in which every bank influenced the original readout.

The radius-127 source checkpoint is frozen. No external arithmetic operator or
oracle router is used. New predictions come only from the frozen recurrent
representation and learned numerical readouts. Original sign/digit heads remain
frozen. TRAIN uses ordinary arithmetic labels, as in prior supervised learning;
DEV/OOD labels are used only for scoring and checkpoint decisions.

## Data and bank assignment

The default intervals are:

| Interval | New bank covers | Meaning |
|---|---|---|
| `3:15` | −15..−3 and +3..+15 | Experimental gap between tested near-zero neighborhood and ±15 |
| `16:32` | −32..−16 and +16..+32 | First bank beyond a hypothetically working ±15 base |
| `33:64` | −64..−33 and +33..+64 | Next outward bank |
| `65:128` | −128..−65 and +65..+128 | Next outward bank |

**±15 has not been certified.** The prior radius-255 probe established an
aggregate 98.2% across −2..+2 but the strict signed-cell head-radius probe
certified only ±1. We therefore do not assume a working ±15 base; the `3:15`
bank is included to evaluate the missing span. Use `--intervals 16:32` to study
exactly the requested 16–32 bank independently. That run cannot by itself
certify a contiguous radius from zero to 32.

Every specialist receives only TRAIN samples whose **true target falls in its
assigned interval**, with both signs. The specialist uses a learned signed
residual coordinate and Gaussian response populations over positive/negative
receptive fields in that interval. It also receives frozen backbone features
through a neural readout. A coordinate-regression auxiliary loss encourages
meaningful coordinates; neither the coordinate target nor true residual is
used during inference.

Routing is a *separate training phase*. Its labels come from whether the new
specialist fixes errors of the prior incumbent **on TRAIN samples**. The
routing module receives only frozen neural features and emits a binary choice
between the previous composite and the new specialist. Existing promoted
specialists and gates are frozen. If no TRAIN samples can be improved by the
specialist, routing reports `NO_FIXABLE_TRAIN_EXAMPLES`, and does not fabricate
a positive training set.

The input-domain guard restores the original frozen logits whenever both
operand magnitudes fall within 127. This guard uses **only A and B**, never
`A+B`. Guarded and **unguarded** results are both reported. Guarding cannot
satisfy the promotion gate on its own: the unguarded route must also retain
all 65,025 original cards exactly.

## Run

Apply this patch to `main_computer_test/` within your Main Computer repo, which
already contains the earlier zero-line ladder, probe, head radius, and Gaussian
radius patch modules.

Full interval sequence:

```powershell
& $py -m tools.nanojev_zero_line_incremental_banks `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_incremental_banks_v1" `
    --device cuda `
    --intervals "3:15,16:32,33:64,65:128" `
    --steps-per-bank 750 `
    --router-steps 400 `
    --probe-every 150 `
    --batch-size 128
```

To directly test the proposed 16–32 bank **without claiming inner coverage**:

```powershell
& $py -m tools.nanojev_zero_line_incremental_banks `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_16_32_v1" `
    --device cuda `
    --intervals 16:32
```

A new output folder is required. Source overlap and overwrites are refused.
Existing source checkpoint SHA256 is verified after the run. TEST remains
untouched. The bank has not been certified on CUDA by the supplied CPU tests.

## Training and evaluation

* TRAIN/DEV magnitude bands: 128–159, 160–191, 192–223, 224–255.
* OOD operand magnitude bands: 256–319, 320–383, 384–447, 448–511.
* TRAIN vs DEV are disjoint by the unsigned, order-invariant operand family;
  operand swaps and global sign mirrors stay together in a split.
* Both signs of the residual and both signs of A are scored separately.
* Every signed-residual × magnitude-band × operand-orientation cell must meet
  the 80% exact criterion with at least six examples to certify an interval.
* Exactness includes sign and **all seven digit tokens**, not numerical error
  tolerance.
* Historical retention uses the **entire** 65,025-card ±127 deck.
* The newly correct region near zero (−2..+2) and all prior promoted regions
  are protected from newly introduced per-sample errors on DEV and OOD. The
  router also sees TRAIN negatives from these regions.
* Promotion requires an interval to pass both strict DEV and OOD cell gates,
  increase routed DEV accuracy versus the incumbent, avoid OOD regression,
  preserve exact unguarded historical retention, and introduce **zero** new
  errors in the protected near-zero/previous-bank regions.
* If the bank learns its isolated interval but routing fails, the `specialist`
  and `candidate` sections reveal this distinction. If no bank passes, the
  previous composite remains unchanged; later banks can still be trained as
  diagnostic candidates, but this **does not imply** contiguous coverage.

## Outputs

`result.json`: complete source provenance, per-bank audit and promotions.

`bank_LO_HI/result.json`: specialist-only DEV/OOD, incumbent baselines,
learned-route (guarded and unguarded), full historical retention, protections,
and router selection rates.

`bank_LO_HI/specialist.pt`: isolated bank weights, even if routing fails.

`bank_LO_HI/candidate.pt`: separately trained bank and gate with eligibility
and audit, even when rejected.

`bank_LO_HI/promoted.pt`: produced **only** if all promotion gates pass.

The runner is an experiment, not a deployment. It does not alter original
Main Computer or NanoJev checkpoints, trained workers, or production apps.
