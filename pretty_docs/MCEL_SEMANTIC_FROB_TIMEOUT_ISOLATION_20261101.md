# MCEL Semantic Other Apps Frob — browser regression isolation

## Finding

The prior Frob invoked four browser-heavy pytest modules inside **one subprocess with one 120-second deadline**. On Windows, this yielded `browserInteractionAndRegression: FAIL` when the combined command exceeded 120 seconds, without a reported failing test. That result was **not evidence of a feature failure**.

The same 55 tests passed together on Linux in 47.25 seconds against the repository snapshot plus `mcel_promise_tabs_tree_monitor_20261101.zip`. These results do not certify Windows execution or production parity.

## Correction

`tools/mcel_semantic_other_apps_frob.py` now:

- Launches each required pytest module in its own subprocess with an independent 180-second default deadline.
- Reports `RUN`, `PASS`, `FAIL`, or `TIMEOUT` per module with elapsed time and a test count when successful.
- Uses verbose pytest reporting so any failing or timed-out module includes the last reached test identity and diagnostic output.
- Keeps all browser suites mandatory, now including regression tests for this Frob's runner.
- Does not accept zero tests, a nonzero pytest exit, or an expired deadline as a pass.
- Aggregates the named suite results into the existing `browserInteractionAndRegression` acceptance check.
- Supports an optional `--suite-timeout SECONDS` override without changing application behavior.

No MCEL runtime, application semantic definition, authority scope, or Windows backend control has been modified.

## Commands

```powershell
python -m tools.mcel_semantic_other_apps_frob
python -m tools.mcel_semantic_other_apps_frob --suite-timeout 240
python -m tools.mcel_semantic_other_apps_frob --hosted-base http://127.0.0.1:8765
python -m pytest -q tests/test_mcel_semantic_other_apps_frob_runner.py
```

Use the default deadline first. Only override it to accommodate unusually slow browser launches; report individual slow suites or failing tests so they can be diagnosed.

## Boundaries

Passing the Frob means that semantic candidates compiled and their required controlled-browser regressions passed. It **does not** certify live Windows hosted routes (unless separately tested), complete production behavioral parity, Monaco tabs against the live Windows backend, Aider mutations, or consequential Task Manager controls.

`PASS_SEMANTIC_CANDIDATES_PARITY_INCOMPLETE` intentionally retains the parity caveat.
