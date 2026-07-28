# MCEL Calculator semantic surface

## Status

Calculator now exposes a reusable MCEL semantic surface contract:

- Surface ID: `calculator.surface.workspace`
- Contract: `calculator.contract.semantic-surface-v1`
- Surface module: `main_computer/web/applications/scripts/mcel-calculator-surface.js`
- Runtime projection: `calculator.runtime-dom`
- Registry maturity: `semantic-runtime`

The surface covers the stable Calculator application structure while preserving the execution-lane boundary established by `CalculatorSemanticAdapter`.

## Stable semantic structure

The static graph describes:

- the active calculation mode;
- the deterministic arithmetic lane;
- the deterministic graphing lane;
- the explicit Mathics lane;
- the result-question lane;
- the explicit model-helper lane;
- the embedded calculation chat context.

The shared layout grammar maps those stable semantics into five regions:

```text
mode switch
arithmetic
graphing
Mathics
chat/context
```

## Dynamic-output boundary

Runtime values are content projected inside the stable surface. They are not copied into the static application-layout grammar.

That includes:

- arithmetic results;
- provider-generated expression text;
- result Q&A answers;
- graph pixels and graph status;
- Mathics output and status;
- embedded chat messages.

Those outputs declare fit policies such as `wrap`, `scroll`, or `decorative`, but they do not receive static `data-mcel-node-id` records. This prevents changing results or chat history from changing the semantic graph.

## Conformance

Calculator now requires all five app-surface layers:

```text
semantic-surface
layout-grammar
runtime-ownership
runtime-visual-fit
diagnostic-no-throw
```

Promotion to `semantic-runtime` is therefore tied to actual static extraction and layout validation, not only to the domain adapter.

## Verification

Run:

```powershell
python -m pytest tests/test_mcel_calculator_surface.py -q
python main_computer/flog_mcel_runtime_smoke.py
python main_computer/mcel_acceptance_runner.py
python main_computer/mcel_truth_audit.py --release-gate
```

The final repository truth audit remains the combined authority because it joins runtime, acceptance, adapter, requirements, and repository-provenance evidence.
