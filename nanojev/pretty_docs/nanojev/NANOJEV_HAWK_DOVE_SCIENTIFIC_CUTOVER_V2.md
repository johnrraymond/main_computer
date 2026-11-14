# NanoJev Hawk–Dove: exact scientific-notation cutover v2

This is a **separate, source-only scientific-notation experiment**. It changes no existing `hawk_dove_million_v1` checkpoint, parent, teacher, optimizer, original trainer, runtime service, or historical training log. Apply the patch's `main_computer_test/` contents over `C:\Users\subsi\main_computer`, then run in PowerShell from that repository root. Existing generation defaults remain v1.

## Objective and intervention

The v1 full numeric occurrence is `raw, LogBar`. In the v2 teacher it becomes `raw, LogBar, scientific` using exact base-10 integers:

```text
650000, <exact computed LogBar>, 6.5e5
-400000, <exact computed LogBar>, -4e5
0, ., 0e0
```

(The LogBar values displayed above are illustrative; the generator computes and validates each actual LogBar.) No approximate float formatting is used. A full slot adds two redundant annotations. A single mutation strips **both annotations of exactly one slot**, leaving its raw number. The existing tiers, T01's eight masks, exact game, source numerical instances, targets, train/dev/test partitions, and graph edges are unchanged. Mask 0 in every tier must have *exactly the original prompt bytes*.

The experimental question is whether numerical decomposition can correct the observed *systematic Y compression* at T01 (for this fixture, `Y=1.5*b`). To distinguish annotation benefit from optimizer benefit, obtain three separate probes: (1) old checkpoint on old teacher, (2) **same decoder/optimizer step** on new teacher before training, (3) new checkpoint on new teacher after training. Compare same dev case IDs / masks; the raw-only mask is the unchanged control.

## Inputs required on the workstation

- Python with PyTorch and the existing NanoJev local Qwen runtime, e.g. `C:\Users\subsi\NanoJev\.venv\Scripts\python.exe`.
- Existing v1 teacher: `runtime\hawk-dove-million\teacher.sqlite3`.
- Existing v1 local run containing `config.json`, `latest.pt`, `tier-gates.json`, and optional `mask-weights.json`: `$HOME\NanoJev\runs\hawk_dove_million_v1`.
- Existing source `runtime\game-transform-experiment\source-v4.pt`, with the **same SHA recorded in donor config**.
- Existing original fixture `examples\hawk_dove_million_DEMO_ONLY.json`, with the **same SHA recorded in the v1 teacher manifest**. The uploaded repository snapshot did not contain this fixture. The patch intentionally does **not** supply or overwrite it; changing or reconstructing it would undermine provenance.

**Stop the v1 trainer after a saved checkpoint** before forking. The cutover checks SHA before/after copying and refuses a changed donor. Never run both trainers against the same lineage. v2 creates its own checkpoint and never uses the historical parent to initialize the head: the donor supplies the complete learned decoder, game-operator head, optimizer, CPU/CUDA RNG states, step, and focus. The donor's parent SHA is retained for provenance, even if `$parent` has changed on disk since training.

## End-to-end command (database → full audit → checkpoint fork → before-probe → training → after-probe)

Run from the repo root:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'

& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 `
  -Stage All `
  -Python $py `
  -Steps 61500 `
  -ChunkSteps 300 `
  -DiagnoseTier T01
```

`-Steps` is the **absolute total optimizer step in the fork**, not additional steps. If the donor checkpoint advanced beyond step 61,500, increase this value before running. To observe the value of the donor checkpoint:

```powershell
& $py -c "import torch; from pathlib import Path; p=Path.home()/'NanoJev/runs/hawk_dove_million_v1/latest.pt'; c=torch.load(p,map_location='cpu',weights_only=True); print('step=',c['step'],'focus=',c['focus'])"
```

**Do not tune mask weights before measuring the initial annotation-only effect.** The complete runner intentionally excludes the withheld test split.

The idempotent stages for separately controlled execution are:

```powershell
# One-time source audit and new teacher generation:
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 -Stage Build -Python $py

# One-time model/optimizer fork; automatic before-training T01 diagnosis:
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 -Stage Cutover -Python $py

# Additional training from the forked state (preserves donor T00 weights):
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 -Stage Train -Python $py -Steps 61500 -ChunkSteps 300

# Read-only dev advancement probe, exhaustive T01 masks, scale/bias diagnosis:
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 -Stage Probe -Python $py

# One-time final withheld test after training policy is frozen:
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 -Stage Test -Python $py
```

The database and destination run are intentionally **create-only**. If a build or fork stage has completed, continue with the next stage rather than rerunning `All`. No database or checkpoint is silently overwritten by build/cutover.

## Direct Python commands, if preferred

```powershell
$oldDb = 'runtime\hawk-dove-million\teacher.sqlite3'
$newDb = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3'
$newRun = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2"

& $py -m unittest tests.test_nanojev_hawk_dove_million tests.test_nanojev_hawk_dove_scientific -v
& $py -m tools.nanojev_hawk_dove_million build `
    --fixture examples/hawk_dove_million_DEMO_ONLY.json `
    --out $newDb --instances 201 --mutation-mode lattice `
    --representation raw_logbar_scientific
& $py -m tools.nanojev_hawk_dove_million_test `
    --db $newDb --fixture examples/hawk_dove_million_DEMO_ONLY.json
& $py -m tools.nanojev_hawk_dove_scientific_train preflight --teacher $newDb
& $py -m tools.nanojev_hawk_dove_scientific_cutover `
    --old-db $oldDb --new-db $newDb `
    --source runtime/game-transform-experiment/source-v4.pt

# Same weights as donor; no optimization yet:
& $py -m tools.nanojev_hawk_dove_scientific_train diagnose `
    --teacher $newDb --output $newRun --device cuda --diagnose-tier T01

# Preserve 70/70/70/60/55/50/45/40/35 gates from the donor's policy file:
& $py -m tools.nanojev_hawk_dove_scientific_train train `
    --teacher $newDb --output $newRun --device cuda `
    --steps 61500 --chunk-steps 300

& $py -m tools.nanojev_hawk_dove_scientific_train probe `
    --teacher $newDb --output $newRun --device cuda
& $py -m tools.nanojev_hawk_dove_scientific_train diagnose `
    --teacher $newDb --output $newRun --device cuda --diagnose-tier T01
```

The original `tools.nanojev_hawk_dove_million_train` remains unchanged and continues using its v1 teacher and old run. The **new** `tools.nanojev_hawk_dove_scientific_train` requires a verified fork; it cannot silently initialize a fresh v2 run or resume v1 under the wrong database. The new run stores `cutover.json`, `config.json`, `latest.pt`, `tier-gates.json`, `mask-weights.json`, probe reports, and exhaustive diagnosis reports.

## Why the experiment produces causal evidence

At the fork point, the numerical head, frozen gamehead, RNG, optimizer state, exact oracle values, input-instance partition, and raw-only strings are inherited. Full masks get additional exact scientific tokens. The only immediate changed input is that representation. If the scientific full masks improve *before* optimization while mask 0 remains unchanged, the difference comes from altered inference inputs. If training later improves mask 0 as well, that indicates possible cross-representation transfer. If high-Y outputs remain systematically underpredicted and low-Y outputs overpredicted, the bias is not solved by adding the annotation alone; inspect learned multiplication/generalization and numeric head decoding rather than masking the problem with an aggregate gate.

A per-mask failure is **not proof of missing training exposure**. Examine error sign, magnitudes, saturation, and *whether the same numeric instances fail under different masks*.

### Before/after numeric forensic comparison

The full-diagnosis file has all cases (not merely the console summary). The analysis reports mask-specific accuracy and signed Y residual, predicted-Y versus actual-Y linear slope/intercept, and numerical `b` ranges:

```powershell
$baseline = Join-Path $newRun 'diagnose-T01-step-058500.json'
$after    = Join-Path $newRun 'diagnose-T01-step-061500.json'
& $py -m tools.nanojev_hawk_dove_scientific_probe `
   --teacher $newDb --report $after --compare-to $baseline
```

Replace the example step numbers with the actual files. The cutover runner runs the forensic comparison automatically when two science diagnosis files exist. At initial cutover, if the donor contains `diagnose-T01-step-<same-step>.json`, it also compares pre-training old-vs-scientific cases to isolate the changed representation.

Only after the uniform-sampling experiment, if masks remain distinctly weak, override the *v2-only* mask sampler:

```powershell
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 -Stage Train `
   -Python $py -Steps 64500 -ChunkSteps 300 `
   -MaskWeights @('T01:7=2','T01:0=1.5','T01:3=1.5','T01:4=1.5','T01:5=1.5')
```

Mask weights alter training presentation selection, **not** dev/test evaluation. Subsequent stage runs use the persisted policy. If most masks share the same magnitude-dependent Y bias, further over-weighting masks is not a substitute for arithmetic supervision research.

## Completeness and validation boundary

A 201-instance teacher has **30,552 presentation rows** and **70,752 exact single-slot annotation-strip edges**. A full independent CPU audit passes. The original v1 unit tests and the new scientific tests pass locally, including a mocked CPU optimizer resume through the new trainer. This does **not** establish the actual CUDA/Qwen test accuracy, transfer improvement, or that the enlarged prompts all fit the local model context; the read-only before-probe must establish real model behavior on the workstation. No live NanoJev checkpoint or fixture was present in the source ZIP, so actual model-level cutover cannot be executed here.
