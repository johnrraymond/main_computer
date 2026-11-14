# NanoJev T02 — numerical information pathway probe (V1)

## Question

The T02 continuous benchmark found a sharp train/dev gap when an output readout
consumes the already-trained V3 penultimate layer. It also found that a linear
readout fitted on **literal numerical operands** generalizes to held-out T02
instances at approximately numerical precision. That alone cannot tell whether
Qwen's frozen activations, V3's feature preprocessing, or V3's body caused the
failure. The existing baseline additionally decoded with the legacy numeric cap
rather than scientific-v2's explicitly configured 10,000,000 cap.

This companion experiment performs **no optimizer steps on Qwen, V3, or the
scientific donor**. It reuses the immutable T02 benchmark train/dev instances,
labels, and cached penultimate activations, and collects the missing upstream
features in a **new run directory**. It never reads test examples.

## Instruments

Five locations along the existing inference computation:

| Plane | What is recorded | Uses teacher answers in features? |
|---|---|---|
| `qwen_last4` | Frozen Qwen mid+last-layer activations of the last four prompt tokens | No |
| `qwen_mean` | Masked average of Qwen mid+last-layer token activations | No |
| `qwen_operand_tokens` | Qwen activations aligned to the **literal** `a` and `b` operand-token spans | No; role/location from prompt syntax only |
| `v3_input` | Actual input to the already-trained V3 body, before its MLP layers | No |
| `v3_output` | Actual output of that body; must match the previous benchmark features | No |

For each plane train a **linear** and **nonlinear** small readout from the
**train** examples alone to recover `[a/1e6,b/1e6]`.

- Report errors in encounter counts and the proportions for which **both**
  counts lie within 5,000 and 25,000 of their true values.
- Independently fit a linear map from the exact train operand pairs to train
  X/Y labels (identical concept to the original successful operand control).
  Apply **that fixed train-only map** to the readouts' estimated operands to
  produce X/Y development accuracy. No target is used at inference.
- Report the explicit numerical-channel control with exact input counts.
- Re-run the original V3 decoder with the scientific-v2 numerical cap of
  10,000,000. Save original and corrected baseline scores side by side.

No readout is trained using dev labels. `test` does not appear in the capture,
training, reporting, or CLI stage options. Eight T02 masks stay together in
existing instance-wise train/dev splits. Existing checkpoint/DB/benchmark files
are read-only and SHA-verified, and saved intermediate features have their own
SHA and identity envelope.

## Install

Place the patch ZIP in `C:\Users\subsi\main_computer`.

```powershell
Expand-Archive `
  .\nanojev_hawk_dove_numeric_pathway_probe_v1_patch.zip `
  .\_numeric_pathway_patch -Force
Copy-Item .\_numeric_pathway_patch\main_computer_test\* . -Recurse -Force
$py = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe'
& $py -m unittest tests.test_nanojev_hawk_dove_numeric_pathway_probe -v
```

**Prerequisite:** Install and complete
`nanojev_hawk_dove_t02_continuous_benchmark_v1_patch.zip` first, so the
`features-train.npz`, `features-dev.npz` and its manifest exist in
`C:\Users\subsi\NanoJev\runs\hawk_dove_t02_continuous_benchmark_v1`.
Do **not** overwrite or edit the original V1 benchmark module, because its SHA
is part of the previous experiment identity.

## Run

```powershell
& .\tools\run-nanojev-hawk-dove-numeric-pathway-probe.ps1 `
  -Stage All `
  -Python $py `
  -Device cuda `
  -TrainingDevice cpu `
  -CaptureBatchSize 4 `
  -Epochs 80
```

Or stage separately (`Prepare`, `Capture`, `Fit`, `Report`):

```powershell
& .\tools\run-nanojev-hawk-dove-numeric-pathway-probe.ps1 -Stage Prepare -Python $py
& .\tools\run-nanojev-hawk-dove-numeric-pathway-probe.ps1 -Stage Capture -Python $py -Device cuda
& .\tools\run-nanojev-hawk-dove-numeric-pathway-probe.ps1 -Stage Fit -Python $py -TrainingDevice cpu -Epochs 80
& .\tools\run-nanojev-hawk-dove-numeric-pathway-probe.ps1 -Stage Report -Python $py -TrainingDevice cpu -Epochs 80
```

`Stage All` captures frozen Qwen features from 1,320 train and 184 dev
presentations. This is a new Qwen pass because the old benchmark did not save
the upstream activations. After capture, all readout fitting and reports work
locally without calling Qwen. Training 10 tiny readouts is **far smaller than
resuming the million-step original T02 experiment**.

## Outputs

New files live ONLY under
`C:\Users\subsi\NanoJev\runs\hawk_dove_numeric_pathway_probe_v1`:

- `manifest.json` — source/DB/checkpoint and prior benchmark SHA provenance.
- `pathway-train.npz`, `pathway-dev.npz` — frozen features at five planes;
  these files also contain the **corrected V3 predictions**.
- `probe-{plane}-{linear|mlp}.pt` — fitted readouts and train-only normalization.
- `pathway-results.json` — side-by-side scores for each plane and readout,
  original V3 cap, corrected V3 cap, and learned explicit numerical channel.

The scientific donor's `latest.pt`, original continuous benchmark manifest,
previous readouts, and original features are never overwritten.

## Interpretation

1. **Upstream Qwen probe succeeds, V3 body output fails:** Evidence of a
   *probe-accessible* precision drop inside V3, especially if `v3_input`
   succeeds and `v3_output` fails with identical readout methods.
2. **Numeric-token Qwen probe succeeds but last-four or global-pool fails:**
   Information exists in particular Qwen token states, and extraction/pooling is
   a strong suspect. A targeted numerical attention channel is worth testing.
3. **All frozen-feature probes fail, explicit operand control succeeds:**
   Existing feature representations and **these particular linear/MLP probes**
   do not reliably generalize operand recovery. This does *not* prove Qwen has
   irreversibly lost information; stronger nonlinear readouts and explicit
   operand routing remain candidates.
4. **Earlier features recover both operands but predicted X/Y still fails:**
   The decoder or numerical transformation is the likely limiting stage.
5. **Corrected original V3 changes markedly:** The previous V1 baseline was
   confounded by the decoder cap. Use the corrected value in comparisons and
   keep the old score visible for auditing.

**Precision qualification:** With T02's known million-encounter scale,
recovering `a` and `b` to 5,000 counts does *not* mathematically guarantee X
is within 5% near zero. The report therefore includes both count-error
metrics and exact 5% X/Y accuracy after a training-only fitted arithmetic map.

**Generalization qualification:** Fitting train but failing development can
occur for either inadequate representations, insufficient numerical readout
capacity, optimization, or limited case coverage. These probes identify where
simple recoverability changes; they are not proof of mathematical impossibility
for a frozen backbone.

## Limitations

- Only synthetic CPU tests can run in the patch environment. Real Qwen and CUDA
  feature capture must execute on the target Windows machine.
- Qwen tokenizer must provide accurate character offset maps, needed for the
  operand-token location probe. The benchmark **fails closed** if unsupported.
- The donor must remain byte-identical to the completed V1 benchmark's donor;
  otherwise identity checks stop the experiment rather than silently compare
  different checkpoints.
