# Hawk–Dove one-question ladder smoke v3 — raw story first

This patch replaces `tools/nanojev_hawk_dove_ladder_smoke.py` **in place**. It does not modify the model, training scheduler, database, or checkpoints. It keeps one fixed crew-first captain jacket, one supply-cache Hawk–Dove question, and eight categorical state variables.

## Why the output changed

The previous smoke printed L00 first and L10 last, and L01–L09 mostly repeated the complete story. That hid the actual transformation and made the output difficult to examine.

The text report now prints:

1. **L10 first:** the exact captain-facing story, including the full jacket, all state information in natural language, opponent intelligence, and the question. No numeric teacher targets appear before it.
2. **L09–L07:** condensed natural language with progressively more explicit X/Y/OP hints.
3. **L06–L04:** a truly structured condensation: the fixed jacket's dispositions and all eight categorical state fields are expressed as key/value evidence for J/K, with partial numerical assistance.
4. **L03–L00:** mostly numerical registers and LogBars, with minimal or no situation text.
5. **Finally:** rubric-generated X/Y/OP/Z values and a state summary, explicitly marked **teacher only**.

Each rung contains a **complete actual prompt**. It is not an abbreviated display with `[same story]` placeholders. The more structured renderings are deterministic projections of the same canonical state. The underlying teacher targets do not change across rungs. We do **not** claim that difficulty is monotonic without model evaluation.

## Usage (from the repository root)

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --self-test
python -m tools.nanojev_hawk_dove_ladder_smoke
```

Compare the raw problem directly with one condensed rung (the raw problem still prints first):

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --level 6
```

Change the state using only qualitative values:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke `
  --resource-priority high `
  --crew-danger low `
  --mission-dependency on `
  --friendly-support in-range `
  --decision-horizon now `
  --opponent-pressure low `
  --opponent-escalating off `
  --opponent-support out-of-range `
  --focus x
```

Show the synthetic rubric after the ladder:

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --show-rubric
```

Get machine-readable output (levels retain canonical ascending numerical keys in JSON):

```powershell
python -m tools.nanojev_hawk_dove_ladder_smoke --format json
```

## Categorical state

| CLI option | Allowed values | Rubric projection |
|---|---|---|
| `--resource-priority` | low, medium, high | X |
| `--crew-danger` | low, medium, high | X |
| `--mission-dependency` | on, off | X |
| `--friendly-support` | in-range, out-of-range | X |
| `--decision-horizon` | now, soon, later | X |
| `--opponent-pressure` | low, medium, high | Y |
| `--opponent-escalating` | on, off | Y |
| `--opponent-support` | in-range, out-of-range | Y |

The rubric is a deliberately simple deterministic teaching oracle, **not** empirical evidence of how real or simulated captains behave. It maps the eight state variables to bounded X and Y, applies `Z = X - Y`, and converts Z to a bounded logistic preference index. The raw captain-facing question contains no digits, rubric weights, or numerical outputs. You can inspect rubric coefficients with `--show-rubric`.

## Smoke tests

```powershell
python -m unittest discover -s tests -p 'test_nanojev_hawk_dove_ladder_smoke.py' -v
```

Tests cover: raw-first print order, no repeated full jacket in condensed levels, inclusion of all eight categorical states in the two condensed renderers, X/Y/OP/Z consistency, X/Y sensitivity, valid CLI choices, repeatability, 1,296 categorical combinations, and the original bounded-logistic behavior.
