#!/usr/bin/env python3
"""Live Ollama → Executor AI loop → MCEL Calculator acceptance smoke.

The provider is real when invoked from the CLI. The only authorized tool is
Calculator: shell/container execution is neither approved nor executed.
This proves tool selection and use, not overall Calculator certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from main_computer.config import MainComputerConfig
from main_computer.executor_tool_loop import (
    ExecutorToolLoopConfig, extract_executor_tool_request, run_executor_tool_loop,
)
from main_computer.mcel_calculator_ai_tool import CORE_REL, RUNNER_REL
from main_computer.providers import OllamaProvider

SCHEMA = "mcel.calculator.live-ollama-ai-smoke.v1"
EXPRESSION = "3 m + 40 cm"
EXPECTED_DISPLAY = "3.4 m"
EXPECTED_INTENT = "intent:calculator.evaluate-expression"
_TOOL_RESULT_PATTERN = re.compile(r"(?<![\w.])3\.4\s*m(?![\w])", re.IGNORECASE)


class NoShellExecutor:
    """Defense-in-depth: the smoke cannot run model-proposed commands."""

    def run(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Shell execution is prohibited in Calculator live smoke.")


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_calculator_receipt(receipt: Any, repo: Path) -> bool:
    if not isinstance(receipt, dict):
        return False
    evaluation = receipt.get("evaluation")
    if (receipt.get("schema") != "mcel.calculator.ai-tool-receipt.v1"
            or receipt.get("ok") is not True
            or receipt.get("action") != "calculator.evaluate"
            or receipt.get("intentId") != EXPECTED_INTENT
            or receipt.get("executionLane") != "local-arithmetic"
            or receipt.get("effectStatus") != "none"
            or receipt.get("engine") != "main-computer-calculator-core-v1"
            or not isinstance(evaluation, dict)
            or evaluation.get("ok") is not True
            or evaluation.get("displayValue") != EXPECTED_DISPLAY):
        return False
    for key in ("semanticFingerprint", "sourceBindingFingerprint", "coreSha256", "runnerSha256", "receiptSha256"):
        if not isinstance(receipt.get(key), str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", receipt[key]):
            return False
    if (receipt["coreSha256"] != _digest(repo / CORE_REL)
            or receipt["runnerSha256"] != _digest(repo / RUNNER_REL)):
        return False
    canonical = json.dumps({
        "action": "calculator.evaluate", "expression": EXPRESSION,
        "evaluation": evaluation,
        "semanticFingerprint": receipt["semanticFingerprint"],
        "sourceBindingFingerprint": receipt["sourceBindingFingerprint"],
        "coreSha256": receipt["coreSha256"], "runnerSha256": receipt["runnerSha256"],
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return receipt["receiptSha256"] == "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_live_calculator_smoke(
    *, provider: Any, repo_root: Path = REPO, max_steps: int = 3,
) -> dict[str, Any]:
    """Execute the genuine production tool loop and adjudicate independent evidence."""
    repo = repo_root.resolve()
    report: dict[str, Any] = {
        "schema": SCHEMA, "status": "fail", "passed": False,
        "provider": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "expression": EXPRESSION, "expectedDisplay": EXPECTED_DISPLAY,
        "shellExecutionPermitted": False,
        "proofScope": "live-ollama-executor-tool-loop-only",
        "fullSemanticRuntimeProven": False,
        "stages": {"modelSelection": "not-run", "calculatorReceipt": "not-run", "modelFinal": "not-run"},
    }
    try:
        result = run_executor_tool_loop(
            provider=provider,
            prompt=("Use the MCEL Calculator tool to evaluate exactly 3 m + 40 cm. "
                    "You MUST invoke calculator.evaluate; do not compute it mentally or request shell execution. "
                    "After calculator_result arrives, return a final JSON object with the exact displayed answer and unit."),
            context_text="Live acceptance test: a model answer without a real calculator_result is a failure.",
            executor_backend=NoShellExecutor(),
            config=ExecutorToolLoopConfig(max_steps=max(2, min(int(max_steps), 5)),
                                          auto_run=False, allow_network=False),
        )
    except Exception as exc:
        report["failureStage"] = "provider_or_tool_loop"
        report["error"] = f"{type(exc).__name__}: {str(exc)[:700]}"
        return report

    report["loopStatus"] = result.status
    report["provider"] = result.provider
    report["model"] = result.model
    report["modelCalls"] = sum(step.kind == "model" for step in result.steps)
    report["toolCalls"] = sum(step.kind == "calculator_result" for step in result.steps)
    selection = [step for step in result.steps if step.kind == "calculator_result"]
    if len(selection) != 1:
        report["failureStage"] = "model_selection"
        report["stages"]["modelSelection"] = "fail"
        report["error"] = "Model did not select exactly one executed calculator.evaluate action."
        if result.error:
            report["loopError"] = result.error[:500]
        return report
    step = selection[0]
    request = step.tool_request
    if (not isinstance(request, dict) or set(request) != {"action", "expression"}
            or request.get("action") != "calculator.evaluate"
            or request.get("expression") != EXPRESSION):
        report["failureStage"] = "model_selection"
        report["stages"]["modelSelection"] = "fail"
        report["error"] = "Model chose incorrect Calculator arguments."
        return report
    report["stages"]["modelSelection"] = "pass"

    if not _verified_calculator_receipt(step.executor_result, repo):
        report["failureStage"] = "calculator_receipt"
        report["stages"]["calculatorReceipt"] = "fail"
        report["error"] = "Calculator receipt, source bytes, or evaluation did not verify."
        return report
    receipt = step.executor_result
    report["stages"]["calculatorReceipt"] = "pass"
    report["intentId"] = receipt["intentId"]
    report["semanticFingerprint"] = receipt["semanticFingerprint"]
    report["sourceBindingFingerprint"] = receipt["sourceBindingFingerprint"]
    report["receiptSha256"] = receipt["receiptSha256"]
    report["actualDisplay"] = receipt["evaluation"]["displayValue"]
    model_steps = [step for step in result.steps if step.kind == "model"]
    final_action = None
    if model_steps:
        try:
            final_action = extract_executor_tool_request(model_steps[-1].content)
        except ValueError:
            final_action = None
    if (not result.ok or result.status != "complete" or result.needs_approval
            or not isinstance(final_action, dict) or final_action.get("action") != "final"
            or not _TOOL_RESULT_PATTERN.search(result.final_content or "")):
        report["failureStage"] = "model_final"
        report["stages"]["modelFinal"] = "fail"
        report["error"] = "Model did not return a completed answer using the Calculator's display value."
        report["finalAnswer"] = (result.final_content or "")[:300]
        return report

    report["stages"]["modelFinal"] = "pass"
    report["finalAnswer"] = result.final_content[:500]
    report["status"] = "pass"
    report["passed"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    config = MainComputerConfig.from_env()
    parser.add_argument("--model", default=config.model, help="Installed Ollama model name")
    parser.add_argument("--base-url", default=config.ollama_base_url, help="Ollama service URL")
    parser.add_argument("--timeout-s", type=float, default=config.ollama_timeout_s)
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report (default format)")
    args = parser.parse_args()
    if not (1 <= args.timeout_s <= 3600 and 2 <= args.max_steps <= 5):
        parser.error("--timeout-s must be 1..3600 and --max-steps must be 2..5")
    provider = OllamaProvider(model=args.model, base_url=args.base_url,
                              timeout_s=args.timeout_s, think=config.ollama_think)
    report = run_live_calculator_smoke(provider=provider, repo_root=REPO, max_steps=args.max_steps)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
