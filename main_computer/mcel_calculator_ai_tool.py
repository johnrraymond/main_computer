"""Read-only, IR-validated AI invocation of the existing JavaScript Calculator core."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from main_computer.mcel_app_compile import compile_application

TOOL_ACTION = "calculator.evaluate"
TOOL_SCHEMA = "mcel.calculator.ai-tool-receipt.v1"
INTENT_ID = "intent:calculator.evaluate-expression"
CORE_REL = Path("main_computer/web/applications/scripts/calculator-core.js")
RUNNER_REL = Path("main_computer/web/applications/scripts/calculator-ai-tool.js")


class CalculatorAiToolError(ValueError):
    """A Calculator request could not be authorized or executed."""


def execute_calculator_ai_tool(
    request: dict[str, Any], *, repo_root: Path | None = None, timeout_s: float = 5.0
) -> dict[str, Any]:
    """Execute only the declared read-only Calculator expression intent.

    Compile the authoritative source afresh; never cache a fingerprint across
    source changes or substitute a separate Python arithmetic implementation.
    """
    if not isinstance(request, dict) or set(request) != {"action", "expression"}:
        raise CalculatorAiToolError("Expected only action and expression arguments.")
    if request["action"] != TOOL_ACTION:
        raise CalculatorAiToolError("Unsupported Calculator tool action.")
    expression = request["expression"]
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 4096:
        raise CalculatorAiToolError("Expression must be a nonempty string of at most 4096 characters.")
    repo = (repo_root or Path(__file__).resolve().parent.parent).resolve()
    try:
        compilation = compile_application(app_id="calculator", repo_root=repo)
    except Exception as exc:
        raise CalculatorAiToolError(f"Authoritative MCEL compilation failed: {exc}") from exc
    if (not compilation.valid or compilation.status != "pass"
        or compilation.report.get("sourceAuthority") != "mcel.dsl.v1"
        or compilation.report.get("appId") != "calculator"):
        raise CalculatorAiToolError("Calculator source is not a valid MCEL application.")
    ir = compilation.report.get("normalizedIr")
    if not isinstance(ir, dict):
        raise CalculatorAiToolError("Calculator compilation did not yield a normalized IR.")
    intents = [i for i in ir.get("intents", ()) if isinstance(i, dict) and i.get("id") == INTENT_ID]
    if len(intents) != 1:
        raise CalculatorAiToolError("The required Calculator expression intent is not uniquely declared.")
    intent = intents[0]
    if (intent.get("lane") != "local-arithmetic" or intent.get("risk") != "read-only"
        or intent.get("operationKind") != "interaction" or intent.get("runtimeMethod") != "evaluateExpression"
        or intent.get("executionBinding") != "calculator-runtime.evaluate-expression"
        or intent.get("effectRefs") or intent.get("writes")):
        raise CalculatorAiToolError("Calculator expression intent does not authorize a local read-only evaluation.")
    source_fingerprint = compilation.report.get("semanticFingerprint")
    source_binding = compilation.report.get("sourceBindingFingerprint")
    if not isinstance(source_fingerprint, str) or not source_fingerprint.startswith("sha256:"):
        raise CalculatorAiToolError("Missing semantic source fingerprint.")
    core = repo / CORE_REL
    runner = repo / RUNNER_REL
    node = shutil.which("node")
    if not node or not core.is_file() or not runner.is_file():
        raise CalculatorAiToolError("Existing JavaScript Calculator core or Node runtime is unavailable.")
    payload = json.dumps({"operation": "evaluate-expression", "expression": expression}, ensure_ascii=False)
    try:
        process = subprocess.run(
            [node, str(runner)], input=payload, capture_output=True, text=True,
            encoding="utf-8", cwd=repo, timeout=max(0.1, min(float(timeout_s), 10.0)), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CalculatorAiToolError(f"Calculator core process unavailable: {type(exc).__name__}") from exc
    if process.returncode != 0:
        raise CalculatorAiToolError("Calculator core rejected the request: " + process.stderr.strip()[:500])
    try:
        output = json.loads(process.stdout)
    except (ValueError, TypeError) as exc:
        raise CalculatorAiToolError("Calculator core returned invalid JSON.") from exc
    if (not isinstance(output, dict) or output.get("schema") != "mcel.calculator.headless-core-result.v1"
        or output.get("engine") != "main-computer-calculator-core-v1"
        or not isinstance(output.get("evaluation"), dict)):
        raise CalculatorAiToolError("Calculator core result schema mismatch.")
    result = output["evaluation"]
    if result.get("ok") is not True and result.get("ok") is not False:
        raise CalculatorAiToolError("Calculator core did not report its evaluation status.")
    core_sha = "sha256:" + hashlib.sha256(core.read_bytes()).hexdigest()
    runner_sha = "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest()
    canonical = json.dumps({
        "action": TOOL_ACTION, "expression": expression, "evaluation": result,
        "semanticFingerprint": source_fingerprint, "sourceBindingFingerprint": source_binding,
        "coreSha256": core_sha, "runnerSha256": runner_sha,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "schema": TOOL_SCHEMA,
        "ok": result["ok"],
        "action": TOOL_ACTION,
        "intentId": INTENT_ID,
        "executionLane": "local-arithmetic",
        "effectStatus": "none",
        "engine": output["engine"],
        "coreSha256": core_sha,
        "runnerSha256": runner_sha,
        "semanticFingerprint": source_fingerprint,
        "sourceBindingFingerprint": source_binding,
        "receiptSha256": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "evaluation": result,
    }
