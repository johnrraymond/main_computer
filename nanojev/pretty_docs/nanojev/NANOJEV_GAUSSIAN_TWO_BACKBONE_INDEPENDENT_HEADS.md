# Independent TinyStories and Pythia Gaussian heads

This experiment trains **two new, separate Gaussian heads from random initialization**. It does **not** reuse Qwen, the existing 60.94%-DEV Gaussian checkpoint, or frozen-reference residual corrections. TinyStories-33M and Pythia-70M remain frozen; the respective Gaussian numerical heads and backbone projections are trainable.

## Input, architecture, and scientific control

Each model receives exactly the same canonical Hawk–Dove prompt, its own frozen LM activations, and the 14 visible, parsed oracle-blind numerical/tier features used by the original Gaussian experiment. No oracle X/Y targets appear among inference inputs. The Gaussian density architecture is the original `BlindDensityNetwork` (2,000 banks, 256 bins, 128 hidden units by default). The 14 numerical features go through its original first encoder layer; the frozen backbone activations go through a separate, trainable LayerNorm + linear projection **to that same hidden layer**, multiplied by a fixed scale of 0.1. They are summed before the original SiLU and remaining encoder layers. All Gaussian head weights, including the bank gains and proposal width, are randomly initialized and trainable from the beginning. This is a different experiment from raw-text-only decoding: the visible numerical inputs remain explicitly bound.

The matrix/count/mask generator, Gaussian target density, KL + MSE + continuous-location training loss, balanced tier batches, AdamW, cosine schedule, DEV checkpoint promotion, and TEST-only-after-selection semantics come directly from the existing `nanojev_hawk_dove_gaussian_population_blind_matrix.py` trainer. Each process gets its own optimizer, feature cache, weights, best checkpoint, and reports. No state is transferred between the two runs.

The two backbone embedding dimensions differ; only the shape of the backbone-specific first-layer projection changes. The numerical Gaussian head architecture is the same.

## Installation and running

Patch only adds files. Install from the repository root:

```text
python new_patch.py nanojev_gaussian_two_backbone_fresh_heads_patch.zip --dry-run
python new_patch.py nanojev_gaussian_two_backbone_fresh_heads_patch.zip
```

Default full training (sequential, recommended for 6 GB GPUs):

```text
python -m tools.nanojev_hawk_dove_gaussian_two_backbone train-all --device cuda --banks 2000 --grid-bins 256 --steps 15000 --seed 20261022 --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_two_backbone_fresh_v1"
```

Train individually with `train --backbone tinystories` or `train --backbone pythia` and an individual fresh `--output` folder. To reuse FP32 activation captures **only** (never weights), add `--reuse-cache-root "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_three_backbone_v2_direct"`. Missing caches are captured automatically; each provided cache must match its model, all ordered prompt/split hashes, FP32 dtype, dimensions, and finite values. Reusing a frozen activation cache is compatible with learning the Gaussian head from scratch. Existing output folders with files are refused.

For example, TinyStories can reuse the earlier FP32 cache if available:

```text
python -m tools.nanojev_hawk_dove_gaussian_two_backbone train --backbone tinystories --reuse-cache-root "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_three_backbone_v2_direct" --device cuda --steps 15000 --output "C:\Users\subsi\NanoJev\runs\hawk_dove_fresh_tinystories_v1"
```

**Windows dependency preflight:** Your last Pythia capture failed while importing a scikit-learn compiled DLL blocked by Windows Application Control. The combined command checks that `from transformers import AutoModelForCausalLM, AutoTokenizer` imports successfully before starting training if either cache is absent. It does not modify Windows policy or bypass an application-control rule. If preflight fails, arrange an approved Python environment/package install or policy change before retrying. Test manually with:

```text
python -c "from transformers import AutoModelForCausalLM, AutoTokenizer; print('IMPORT_OK')"
```

## Artifacts and comparisons

Each `tinystories/` and `pythia/` directory contains `feature_cache.pt`, `config.json`, `matrix_split_manifest.json`, `best.pt`, `history.json`, `summary.json`, `predictions_dev.jsonl`, and `predictions_test.jsonl`. Predictions are keyed by `instance`, `tier`, `mask` and contain `predicted_X`, `predicted_Y`, and `predicted_Z` (`HAWK`, `DOVE`, or `TIE`). These are suitable for a later **key-aligned** ensemble. Combining numerical X/Y means and then deriving Z from the resulting X/Y is distinct from majority-voting the Z labels; neither method is automatically part of this training patch.

The combined command emits `comparison.json`, verifying both runs used identical held-out matrix identities. DEV and TEST weakest-tier joint X/Y accuracy are reported separately. The TEST labels are not used for gradients or promotion. Neither a 60% initial score nor passing the 80%-within-5%-on-both-outputs criterion is guaranteed. The original 60.94% control starts from already-trained numerical weights; these fresh heads start at random initialization.

Audit an independent checkpoint:

```text
python -m tools.nanojev_hawk_dove_gaussian_two_backbone audit --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_two_backbone_fresh_v1\pythia\best.pt" --device cpu
```

## Boundaries

- The backbone language-model weights remain frozen. **Head from scratch** does not mean pretraining the language model.
- `--parallel` is opt-in and unsuitable for a 6 GB laptop GPU; default sequential training avoids model-loading contention.
- Only FP32 activation caches from the validated current/previous capture schema are reusable; FP16 or mismatched caches fail closed.
- The prediction files do not silently merge across backbones; ensembling should require exact instance/tier/mask alignment and matching matrix manifests.
