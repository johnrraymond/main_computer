# NanoJev — Hawk–Dove × Tit-for-Tat smoke

A **read-only diagnostic**, not a new training curriculum or promotion gate. It tests whether the current Gamer system can carry exact game mathematics from natural-language prompts across immediate action incentives, repeated reciprocity, and changes in payoff rules. It does **not** treat tit-for-tat as a separate normal-form game.

## Mathematics / target

For a two-action symmetric game, the player's payoff is `R` after CC, `S` after CD, `T` after DC, `P` after DD. All 12 bounded ordinal profiles are constructed from explicit, synthetic payoff matrices; no personality, moral, or human-motive label is inferred from the matrix.

- **Immediate Hawk–Dove layer:** `A = T-R`, `B = P-S`; cooperation-belief `q` gives a defection advantage `Δ(q)=q*A+(1-q)*B`. Its sign determines the best response; exact zero is a tie (coded as cooperate). The four sign combinations identify incentive families, without claiming they identify every game in the source catalog.
- **Repeated tit-for-tat layer:** cooperate first; thereafter copy the other actor's preceding move. Compute exact discounted reward sums, test one mistaken move and the retaliation cycle, then test an *explicit bilateral reset*, not automatic forgiveness.
- **Rule-change layer:** a nonnegative cost `k` subtracted from both payoffs involving **own** defection changes the two incentive gaps to `A-k` and `B-k`; the smallest cost making cooperation a weak best response to either move is `max(0,A,B)`.
- **Other structures:** small, separately bounded trust, public-goods, ultimatum, attrition and Bayesian-signal cases. These are **extensions**, not false reductions of every game to 2×2.

The deck contains **289 exact-rational questions**, including positive, negative, and **64 exact-zero targets**. Every gold value is recomputed independently from the public question text. Full model performance remains unverified until a model mode runs.

## Commands (repository root)

The source-only oracle has no model dependencies and does not write to the repository:

```powershell
python -m tools.nanojev_two_game_spine_smoke
python -m unittest tests.test_nanojev_two_game_spine_smoke -v
```

A short real model run samples 24 cases across the twelve profiles plus repetition, rule changes, and extensions; `--limit 0` evaluates all 289. Both neural modes require the **correct frozen adapter/head pair** and leave weights unchanged. The optional `--run` flag finds matching files using the *actual staircase layout*; there is no `RUN/to_numerical/wide.pt` in a staircase source run.

### Run the latest 100-vector expanded head (current Gamer experiment)

The `head-100` trainer stores `best_model.pt` and `provenance.json` in the sibling expanded run, **not** a C0 `wide.pt`. Use `--mode expanded`:

```powershell
$run = 'runtime\reports\gamer-v1\transition-005'
$head100 = 'runtime\reports\gamer-v1\transition-005-expanded-head-100-0001'
& $py -m tools.nanojev_two_game_spine_smoke `
  --mode expanded --run $run --expanded-run $head100 `
  --device cuda --limit 24 --capture-batch 2 --wide-batch 4
```

This loads the exact frozen adapter by checking its SHA-256 against the expanded run's provenance, captures 100 chronologically selected full-token hidden vectors **from each original prompt** using the existing expanded head's sampler, releases the Qwen model from the GPU, then loads the saved 100-vector head and evaluates it. It checks checkpoint identity and frozen bytes before and after the probe. This is inference only—no trainer, optimizer updates, or target leakage. If your experiment is still running and has **not produced** `best_model.pt`, the smoke fails closed rather than treating a resumable training file as a finalized model.

### Run the earlier staircase C0 Wide head

```powershell
$run = 'runtime\reports\gamer-v1\transition-005'
& $py -m tools.nanojev_two_game_spine_smoke `
  --mode wide --run $run `
  --device cuda --limit 24
```

The smoke finds the newest completed `RUN/staircase/head_cycle_NNNN/to_numerical/wide.pt` and takes the frozen `RUN/staircase/head_cycle_NNNN/lora/best_adapter/` **from that same cycle**. You may instead pass `--adapter-dir` and `--wide-checkpoint` explicitly; they must match by the fingerprint embedded in the Wide checkpoint.

**Do not substitute** the expanded `best_model.pt` for `--wide-checkpoint`. The 100-vector model has a different feature width and architecture. Conversely, `--mode expanded` must not use a legacy Wide checkpoint.

The run prints the precise model/head status, errors, zero-line sign agreement, numerical MAE, and sampled-game coverage. A small bounded smoke is not full retention or promotion evidence. Model downloads are disabled unless `--allow-model-download` is explicitly passed. No trained checkpoint is bundled with this source snapshot, so the model result must be obtained on the actual training host.

Alternatively, `--mode live --adapter-dir PATH` probes numeric text completions from a frozen Gamer LoRA without either head. That is **not** a test of the numerical route. Low performance in that mode alone is not a reason to resume LoRA training.

### Blind deck / externally obtained replies

```powershell
python -m tools.nanojev_two_game_spine_smoke --deck 'runtime\reports\gamer-two-game-spine\deck.jsonl'
python -m tools.nanojev_two_game_spine_smoke --mode score `
  --responses 'runtime\reports\gamer-two-game-spine\answers.jsonl'
```

The deck contains `id`, `prompt`, `game`, `layer`, `skill`, and `deck_sha256`—**not the answer**. The independent system must write one JSONL record per answer: `{"id":"...","answer":"-3/2","deck_sha256":"<copied from deck>"}`. Unknown/duplicate/stale IDs fail closed. Missing or invalid answers never count as a pass. The optional model scores are **diagnostic**; a 24-case bounded PASS cannot certify retention or promotion of the 289-case deck, let alone the full NanoJev game catalog.

## Explicit limits

- The 12 profiles are ordinal mathematical exemplars; name conventions vary, and some non-standard labels cannot identify a unique game independently of the payoff matrix.
- Repeated games add horizon, history, and opponent policy; they are *not* equivalent to their one-shot stage games.
- Payoff-rule changes are a synthetic model of incentives, not an endorsement of any specific legal penalty or a claim about real human preferences.
- A new prompt format may be out of the current Gamer training distribution. When Wide fails, inspect numeric-head errors and representation diagnostics before deciding whether any LoRA repair is justified. Do not automatically resume LoRA training.
