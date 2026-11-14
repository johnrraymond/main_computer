# Economical joint-frontier monitoring (v1)

The joint trainer in `tools.nanojev_qwen_lm_radius_ladder` supports four independent monitoring frequencies. Model/optimizer state, training examples and fixed evaluation decks are unchanged.

## CLI

Add to your established `--stage run --frontier --frontier-hdb` command:

```powershell
--joint-heartbeat-every 25 --joint-probe-every 250 --joint-save-every 100 --joint-check-every 1000
```

These flags are optional. Default heartbeat/probe/periodic-save is **off** (0), and default full audit is **every 100 steps**. The full-audit interval may be changed on checkpoint resume while other optimizer/curriculum parameters remain pinned. No manual migration is needed for the current v3 joint checkpoint; older v2 migration remains supported. Preserve existing model, tokenizer, geometry, radii, and evaluation-deck arguments.

- **Heartbeat (cheap):** every N steps, writes `joint_heartbeat.json` and emits `QWEN_JOINT_HEARTBEAT` containing the live step, latest loss, accumulated *training* seconds, direct examples by population, and current focus. **No inference, no model/optimizer save.** A live heartbeat is **not a resumable checkpoint**: an interrupted run reverts to the latest authoritative full audit checkpoint.
- **Probe (limited inference):** every N steps, when a full audit is not due, takes a deterministic subset **from the already-frozen heldout evaluation decks**: one case per T rung per radius (18), HDB tier per negative/zero/positive side (27), and math family per level (18, under the default two levels). This totals 63 problems in the usual geometry, versus 441 individual problems **plus** full T/HDB chain rollouts at an audit. Writes `joint_probe_report.json` and emits `QWEN_JOINT_PROBE`. It reports differences from the last saved authoritative full audit, not confidence intervals, and never alters scheduler priorities or stall state. **No chain inference** is performed in the probe. The subset is frozen and disjoint from training.
- **Periodic checkpoint (no inference):** every `--joint-save-every` steps, writes `working.pt` and `progress.json` using the existing LoRA/optimizer state, without scoring any examples and without changing training priorities. This is a resumable checkpoint, unlike a heartbeat. Saves are omitted at a step already due for a full audit. A checkpoint save costs I/O, but it is much cheaper than complete autoregressive evaluation and prevents losing up to a thousand optimizer steps in a crash.
- **Full audit (authoritative):** every `--joint-check-every` steps **and always at the configured final optimizer step**, measures full fixed heldout decks, T chains and HDB chains; updates training priorities/learning-state classifications and writes `working.pt`, `progress.json`, `joint_frontier_report.json`, `hdb_difficulty_report.json` and history. A final checkpoint is not redundantly serialized twice.

Read persisted monitoring with no Qwen inference:

```powershell
python -m tools.nanojev_qwen_lm_radius_ladder --stage status --frontier --frontier-hdb --output "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1"
python -m tools.nanojev_qwen_lm_radius_ladder --stage competency --frontier --frontier-hdb --output "$HOME\NanoJev\runs\qwen_lm_numeric_terminal_v1"
```

`status` differentiates `checkpoint_step` from `live_heartbeat.step`, and exposes `full_audit_step` and the latest valid small probe. `competency` remains a read-only view of the most recent complete audit, **not an independent evaluation**. Live monitoring does not reduce the cost of individual training steps. The existing full report already contains `training_seconds` and `evaluation_seconds_cumulative`, which should be compared before attributing slow progress to evaluation alone.

**Running-process caveat:** changing source files or flags on disk cannot alter an already-running Python process. New monitoring flags only take effect when the trainer is launched/resumed with them. A live training run can be monitored without changing it by reading the most recent saved files.

**Important resume caveat:** relaxing full checks also increases the interval between adaptive changes guided by complete competency measurements. `--joint-save-every 100` preserves resumability without running those audits. If delayed adaptive evaluation is unacceptable, choose a shorter full-audit interval (e.g., 500) instead. For production, monitor actual wall-clock and crash risk, not just optimizer step counts.
