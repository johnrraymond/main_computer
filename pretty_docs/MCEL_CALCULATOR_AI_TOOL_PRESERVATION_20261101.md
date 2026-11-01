# MCEL Calculator AI-tool preservation gate — 2026-11-01

## Governing direction

The calculator is a computational tool available **to the AI**, not a calculator whose arithmetic is delegated to AI. The preserved invocation path is:

`AI model: calculator.evaluate` → `run_executor_tool_loop` → `execute_calculator_ai_tool` → authoritative MCEL `intent:calculator.evaluate-expression` → headless `calculator-ai-tool.js` → existing deterministic `calculator-core.js` → typed result/provenance receipt → AI final response.

The tool is read-only and does not grant shell execution, hidden mutation, an alternative Python math engine, or external network authority. Optional model-helper UI features in the production Calculator are separate functionality and do not replace this tool direction.

## Audit

Seven core files have identical SHA-256 digests across the supplied 2026-10-31 snapshots at 17:23, 17:50, 18:29, and 18:39: `main_computer/executor_tool_loop.py`, `main_computer/mcel_calculator_ai_tool.py`, `main_computer/web/applications/scripts/calculator-ai-tool.js`, `main_computer/web/applications/scripts/calculator-core.js`, `mcel_apps/calculator/application.js`, `tests/test_mcel_calculator_ai_tool.py`, and `tools/mcel_calculator_live_ollama_smoke.py`. Subsequent MCEL patches do not modify these files.

## Required Frob verification

`tools/mcel_semantic_other_apps_frob.py` now requires three independent existing test suites:

- `tests/test_mcel_calculator_ai_tool.py` — actual compiled intent, deterministic JavaScript core, AI tool-dispatch roundtrip, typed receipt, no-shell, and forbidden-request refusals.
- `tests/test_mcel_calculator_live_ollama_smoke.py` — **scripted provider** regression of the existing Ollama smoke harness, including rejection of mental math/shell substitution and tampered or incorrect receipts. This suite does not call a live Ollama model.
- `tests/test_mcel_calculator_completion_gate.py` — host-bound proof and provenance boundaries, without upgrading them to full semantic runtime proof.

These suites must pass without skipped tests; the Frob reports `calculator.aiToolPreservedByRegression` independently from browser UI parity. If Node or the core is unavailable, the gate fails closed.

Run:

```powershell
python -m tools.mcel_semantic_other_apps_frob
```

For fresh live-model proof, separately run with a configured local Ollama instance:

```powershell
python -m tools.mcel_calculator_live_ollama_smoke --json
```

Local regression and source identity establish preservation of the direction and implementation. They do not prove that Ollama was invoked in this current session or that the broader Calculator semantic runtime and production parity are certified.
