# NanoJev Hawk–Dove: scientific v2.1 safe resume

## Verified user run, October 19, 2026

The scientific database successfully built and audited: 201 instances, 30,552 presentations, 70,752 mutation edges. Donor fork succeeded from v1 checkpoint step **68,400**. A saved v2 scientific checkpoint now exists at `$HOME\NanoJev\runs\hawk_dove_million_scientific_v2\latest.pt`. Before-training T01 dev diagnosis exists at step 68,400, and it shows that the annotated representations perform far worse than raw-only: T01 joint accuracy for mask 0 is 56.52%, while mask 7 is 8.70%. No post-cutover optimizer steps have occurred in the attached log.

The original command requested `-Steps 61500`, an absolute target, not an additional budget. The scientific checkpoint was already at 68,400; the trainer rejected the operation with `TRAIN_STEPS_MUST_EXCEED_CURRENT_LOCAL_STEP`. The scientific V2 cutover, data, and donor are not corrupted. **Do not rebuild or recutover.** The program reports first failing tier **T00**, at 37.5% against a 70% gate. Automatic curriculum advancement will prioritize T00 until it qualifies; do not imply that all future steps will train T01.

## Apply V2.1 incremental patch

Copy just the updated `tools\cutover-nanojev-hawk-dove-scientific.ps1` and this documentation over the repository. **Do not replace** `tools\nanojev_hawk_dove_scientific_train.py`: the existing saved checkpoint pins its trainer SHA. This patch deliberately changes only the non-pinned orchestrator and documentation.

## Baseline diagnostics for both T00 and T01 (read only)

In the existing repository root, in PowerShell:

```powershell
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 `
  -Stage Probe -Python $py -DiagnoseBoth
```

The T01 diagnosis at step 68,400 already exists. This stage also saves the full T00 mask diagnosis at that step. The script uses development examples, not the withheld test set.

## Resume training with a relative budget

```powershell
& .\tools\cutover-nanojev-hawk-dove-scientific.ps1 `
  -Stage Train -Python $py -AdditionalSteps 6000 `
  -ChunkSteps 300 -DiagnoseBoth
```

If the scientific checkpoint remains at step 68,400, this schedules training through **74,400**. If that checkpoint has advanced, this computes an appropriately later absolute target. It reads the checkpoint step on CPU *before* initializing the model. The script displays the current, target, and additional steps. It runs the development probe and exhaustive T00 and T01 diagnoses automatically at the conclusion. Follow-up training simply repeats the same command with a new additional budget.

The older `-Steps` option is retained for absolute targets; it is incompatible with `-AdditionalSteps`. Invalid absolute targets now fail early and explain the condition, without loading Qwen. If neither option is given for training, the default is **3,000 additional steps**. Build, Cutover, Probe, and Test stages are unchanged except that diagnostics can cover both T00 and T01. Existing donor checkpoint, v1/v2 database, and v2 saved checkpoint remain untouched by orchestrator replacement.

## Evaluate why the full annotation initially hurts

Observe T00 and T01 **per-mask Y accuracy**, signed error, the same numerical instance under different masks, and the time series of `probe-step-*.json`. Compare the T01 before-training `diagnose-T01-step-068400.json` to the subsequent new-step diagnosis using `tools.nanojev_hawk_dove_scientific_probe`. **Mask 0** remains an unannotated numerical control. The initial loss of accuracy is an out-of-distribution effect consistent with the new unfamiliar annotation syntax; the log alone cannot identify tokenization, attention, optimizer, or regression-head effects as the unique root cause.

If automatic curriculum focuses on T00, track T00 alongside T01. Recovery of the annotated masks with simultaneous preservation of raw-mask performance would support scientific representation learning; failure to improve would motivate a deeper inspection of teacher binding, tokenizer representation, input embedding and regression-head behavior. Do not change mask weights until the uniform-sampling baseline has been characterized.

**Verification scope:** This small patch has no scientific trainer or checkpoint schema changes. It can be statically inspected in this environment; full Windows PowerShell/CUDA validation still requires the target workstation.
