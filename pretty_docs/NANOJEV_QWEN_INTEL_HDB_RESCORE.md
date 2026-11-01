# NanoJev trained Qwen HDB rescore on Intel Iris Xe (experimental)

## Scope

Source snapshot: `main_computer_test-20261031-222255.zip`.  Modifies only
`tools/nanojev_qwen_lm_radius_ladder.py`, `tools/nanojev_qwen_joint_frontier.py`,
`tools/nanojev_qwen_competency_diagnostic.py`, with a new helper and tests.

This is actual read-only Qwen3-0.6B + restored learned LoRA HDB inference,
not the 568,405-parameter proxy and NOT training. It does not run the separately
trained numerical sidecar head; `hdb-rescore` uses Qwen's vocabulary LM head.

The frozen Qwen source/lineage is loaded and verified on CPU, then explicitly
moved to the Intel DirectML adapter; no CUDA kernel or CUDA PyTorch process is
started.  Uses float32 and eager attention on DirectML. If an operation is not
supported, it fails visibly; no CPU fallback is implemented by this patch.
DirectML itself may internally use implementation fallbacks; this test does
not certify that every shader executes on the GPU.

## Environment (existing DirectML venv)

From `C:\Users\subsi\main_computer`:

```powershell
$intelPy = "C:\Users\subsi\.venv-nanojev-directml-smoke\Scripts\python.exe"
& $intelPy -m pip install "transformers==4.56.2" safetensors
& $intelPy -m tools.nanojev_qwen_lm_radius_ladder `
  --stage hdb-rescore --rescore-live --frontier --frontier-hdb `
  --device dml --dml-index 1 --intel-smoke-per-tier 1 `
  --terminal-mode numeric `
  --stage1-checkpoint "$HOME\NanoJev\runs\qwen_problem_answer_stage1_v1\best.pt" `
  --text-checkpoint "$HOME\NanoJev\runs\hybridlink_micro_text_v1\best.pt" `
  --output "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1" `
  --radii 0,1,2,3,4 --frontier-max-radius 64 `
  --operand-limit 1024 --max-rung 8 --batch-size 4
```

To run the entire existing HDB deck, use `--intel-smoke-per-tier 0` (potentially
very slow). The default Intel smoke selects **one frozen heldout example per HDB
tier**. It is explicitly **not** a full competency certificate. The selected
rows are unchanged from the frozen deck, and have no inference oracle.

Because this path uses a different GPU/backend from the existing CUDA training,
output accuracy may differ. This is a feasibility experiment, not a claim that
DirectML or the Intel GPU will outperform CUDA on Qwen3.

Check the `INTEL_DML_QWEN_READY` line for backend and the `QWEN_HDB_RESCORE`
summary. Reports go beneath `..._joint_t_hdb_numeric_frontier_v2/concurrent_hdb_rescores/rescore_*`.

**Source checkpoint cannot be updated or overwritten.** In live mode the
snapshot is taken by a same-volume NTFS hardlink, which is deleted on exit.
The loaded model exists only in memory and performs no optimizer steps.

### Stop conditions

- `INTEL_DML_ENVIRONMENT_MISSING`: use isolated DirectML Python with torch-directml.
- `INTEL_DML_REFUSES_NON_INTEL_ADAPTER`: incorrect device index (0 is NVIDIA on your machine).
- `INTEL_DML_QWEN_TRANSFER_FAILED`: DirectML does not accept a model tensor.
- `INTEL_DML_QWEN_INFERENCE_FAILED`: incompatible Qwen attention, indexing,
  cache, memory allocation, or other operator. Record full traceback; do not
  silently return to CUDA. For an optional non-cached decoder attempt add
  `--competency-legacy-decode`, which is slower.
- A missing checkpoint, parent fingerprint drift, or frozen-deck change
  remains a failure and is not bypassed by Intel mode.

The head-sized proxy result (82,669 inputs/sec) is **not** an estimate for the
full Qwen HDB rescore. Its benchmark was separate and much smaller.
