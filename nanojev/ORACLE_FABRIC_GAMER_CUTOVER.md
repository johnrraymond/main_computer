# GAMER oracle-fabric cutover — coordinated retraining candidate

This changes the initialization and coupling of the **existing seven-stage `GamerGraphHead`** only. The old G1 model and the separate G2-0062 step-4900 set predictor remain immutable references. There is no production promotion, sealed evaluation, legacy weight warm-start, or reuse of an old-G1 capture as evidence for a new-G1 architecture.

## What is constructed

- Trainable parameters are created on the PyTorch `meta` device and filled with a deterministic spectral lattice. No random weight samples are needed even transiently.
- A dyadic numerical oracle establishes exact 50/50 binary partition coverage on the defined reference grid. A continuous sinusoidal sidecar has constant pairwise derivative-vector magnitude across numerical positions. This does **not** establish globally smooth training or correct numerical inference from untrained Qwen hidden states.
- Null/non-null class priors encode an intentional *abstain* default; zero-input G1 and G2 smoke oracles are measured before training.
- All six new interstage couplers start with a frozen identity base and zero learned correction: `G1_G2`, `G2_G3`, `G3_G4`, `G4_G5`, `G5_G6`, and `G6_G7`. Each receiving stage can train its correction against its own oracle. G7 can jointly refine corrections; identity bases remain frozen.
- New cutover checkpoints carry the campaign ID, initialization digest, architecture flag, and training lineage. Legacy heads continue to load without new coupler parameters.

The underlying pretrained Qwen/LoRA feature generator is held frozen. It was not retrained from these defaults. General-purpose GPU kernels and that historical pretrained network should not be described as entirely free of historical random initialization or guaranteed bit-identical across hardware.

## Commands

All commands below are run from `main_computer\nanojev` using the NanoJev Python interpreter `$py`.

1. Discover legacy assets without changing anything:

```powershell
& $py -u -m tools.nanojev_oracle_fabric_cutover discover --root ..\runtime\reports\gamer-v1
```

2. Prepare and freeze the new defaults (works automatically only if **one** G1 baseline, **one** hash-confirmed step-4900 G2 baseline, and **one** matching G1 training dataset are found):

```powershell
& $py -u -m tools.nanojev_oracle_fabric_cutover prepare-auto --root ..\runtime\reports\gamer-v1 --output ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001
```

If discovery finds multiple G1 baselines or no unambiguous dataset, **do not guess**. Use the explicit command with the actual paths returned by discovery:

```powershell
& $py -u -m tools.nanojev_oracle_fabric_cutover prepare --dataset PATH_TO_G1_DATASET.jsonl --source-adapter PATH_TO_ADAPTER_DIRECTORY --legacy-g1 PATH_TO_G1_CHECKPOINT_DIRECTORY --legacy-g2 PATH_TO_HASH_VERIFIED_STEP_4900.safetensors --output ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001
```

3. Reopen and validate the entire prepared baseline without training:

```powershell
& $py -u -m tools.nanojev_oracle_fabric_cutover verify --campaign ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001
```

4. Train and evaluate all stages in one versioned campaign, stopping at the first failed gate:

```powershell
& $py -u -m tools.nanojev_oracle_fabric_cutover run --campaign ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001 --device cuda --steps 400 --accum 4 --g1-lr 0.0003 --lr 0.0001
```

The new G1 begins without legacy learned weights. Subsequent stages inherit **only earlier stages of the same campaign**, and a stage can begin only after the previous developmental oracle gate passes. Every downstream stage re-evaluates previous competencies on the current weights. Failures stop the campaign; there is no unsafe bypass or automatic promotion. Completed stages can be verified/reused in a later invocation with the identical run configuration. Training interrupted before a stage checkpoint is finalized cannot be claimed complete.

## Nonregression and limitations

Historical evidence remains pinned by hash when available: G1 checkpoint, G2 step-4900 checkpoint, and G2-0067/0068/0069 reports. G2-0062 established 81.23% edge F1 and 4/96 exact graphs on a historical cohort, **not** a system-certification result. Earlier candidate reachability deficits, sign/zero failures, role reversals, and unstable learning-rate results are retained in the oracle contract.

The single-head G1–G7 curriculum has native stage oracles, dev and challenge evaluation, gate certification, and prior-stage retention probes. It is an actual executable path for its seven stages, but it is **not** an end-to-end retrain of the *separate* G2-0062 `TripleSetPredictor` on a new-G1 capture. Its new deterministic defaults are prepared as a separate comparator, with execution blocked until its inputs can be regenerated from the newly trained G1. No absent G3 or external consumer is declared certified.

The first run has not been executed against the user's Qwen weights or full datasets in this delivery. Passing local unit and synthetic integration tests only verifies code mechanics; it does not establish learning success, system-level parity, or that any gate will pass. The legacy system remains authoritative until an independently verified replacement is ready for a separately authorized production cutover.
