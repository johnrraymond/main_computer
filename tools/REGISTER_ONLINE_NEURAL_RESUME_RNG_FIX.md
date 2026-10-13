# NanoJev online neural decoder: CUDA checkpoint resume fix

## Symptom

Resuming `nanojev_register_online_neural.py` with `--device cuda --resume` raises:

```
TypeError: RNG state must be a torch.ByteTensor
```

The failure arises when `torch.load(..., map_location=device)` moves saved CUDA RNG state tensors to CUDA and `torch.cuda.set_rng_state_all` rejects them.

## Fix

Load the checkpoint with `map_location='cpu'`; restore network and optimizer with their ordinary `load_state_dict()` methods, and ensure CPU/CUDA generator state tensors are CPU `torch.uint8` before restoring the generators. The serialization format, experiment config, checkpoint paths, and training loop are unchanged. Existing `latest.pt` and `best.pt` files remain compatible; no re-saving or Qwen inference required for conversion.

## Apply

Copy `main_computer_test/tools/nanojev_register_online_neural.py` to `C:\Users\subsi\main_computer\tools\nanojev_register_online_neural.py` (overwrite the previous version). Optionally copy the test file to `tests/`.

Test with the NanoJev venv:

```
& 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe' -m pytest -q .\tests\test_nanojev_register_online_neural.py
```

Resume with **the same command** that failed, including `--steps 5000 --resume`. It will restore the latest checkpoint's model weights, optimizer, fresh-example stream, and CPU/CUDA RNG state before moving to subsequent steps.

The CUDA resume path was tested by mocking CUDA-state restoration while checking its type and CPU placement. Full GPU resume was not available in the test environment.
