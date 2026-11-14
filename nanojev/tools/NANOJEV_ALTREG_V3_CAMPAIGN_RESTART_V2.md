# NanoJev V3 Campaign Restart V2

This patch fixes the actual Windows failure (`TypeError ... not int`) and the mixed-state training resume failure.

### Installation
The ZIP is rooted at `main_computer_test/`, **not** directly at `tools/`. Copy `main_computer_test/tools/nanojev_altreg_math_campaign_v3.py` over your existing repository's `tools/nanojev_altreg_math_campaign_v3.py`. Preserve the checkpoint directories. The patch includes no checkpoints and does not alter the decoder trainer.

From `C:\Users\subsi\main_computer`, after unzipping to a staging directory, verify:

```
& $py .\tools\nanojev_altreg_math_campaign_v3.py --version
# v3-campaign-restart-v2
```

Run `--stage pilot --backbones qwen --arms factorized attention --steps 2000 --dry-run` to inspect the generated tasks without starting training. It should emit `altreg_campaign_pilot_plan` with `resume` for partial runs, `start` for missing runs, and `already-complete` for finished runs.

Both with and without `--resume` now have the same **absolute** `--steps 2000` target, never 2000 additional steps. Explicit relative continuation requires a *single* selected arm and both `--resume --additional-steps N`.

Existing nonempty output folders lacking `latest.pt`, invalid checkpoints, and runs beyond target safely fail without modification.

### Test

```
& $py -m pytest .\tests\test_nanojev_altreg_math_campaign_restart_v2.py -q
```
