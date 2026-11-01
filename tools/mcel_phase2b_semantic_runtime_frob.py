"""MCEL Phase 2B executable semantic browser runtime Frob.

Explicitly distinguishes real calculator core integration from simulated
filesystem capability responses. Does not claim four production app parity.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from tools.mcel_phase2b_browser import BrowserUnavailable, launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "tools/fixtures/mcel_phase2_reference_apps"


def run() -> int:
    checks = {}
    for app in ("calculator", "file-manager", "task-manager", "code-editor"):
        report = compile_dsl_application(APP_ROOT / app / "application.js")
        checks[app + ".pairedCompilation"] = report.valid
        checks[app + ".hasSemanticHierarchy"] = bool(report.normalized_ir and report.normalized_ir["surfaces"][0].get("interfaceIntent", {}).get("hierarchy"))
    browser_backend = None
    browser_diagnostic = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser, browser_backend = launch_semantic_browser(playwright)
            browser.close()
        checks["browserLaunch"] = True
    except (BrowserUnavailable, ImportError, OSError, RuntimeError) as exc:
        checks["browserLaunch"] = False
        browser_diagnostic = str(exc)

    if checks["browserLaunch"]:
        command = [sys.executable, "-m", "pytest", "-q", "-ra", "tests/test_mcel_phase2b_semantic_runtime.py"]
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
            checks["browserRuntimeTestSuite"] = (completed.returncode == 0 and
                " passed" in completed.stdout and " skipped" not in completed.stdout)
            if not checks["browserRuntimeTestSuite"]:
                browser_diagnostic = (completed.stdout + "\n" + completed.stderr)[-4000:]
                print(browser_diagnostic)
            else:
                print(completed.stdout.strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks["browserRuntimeTestSuite"] = False
            browser_diagnostic = str(exc)
    else:
        checks["browserRuntimeTestSuite"] = False
        print("Browser launch failed: " + (browser_diagnostic or "unknown error"))
    passed = all(checks.values())
    out = ROOT / "runtime/reports/mcel-phase2b"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({
        "schema": "mcel.phase2b-semantic-runtime-frob.v1",
        "status": "PASS_BROWSER_VERTICAL_SLICE_ONLY" if passed else "INCOMPLETE",
        "checks": checks,
        "browserBackend": browser_backend,
        "browserDiagnostic": browser_diagnostic,
        "runtimeScope": "isolated browser mount with host-supplied authority port",
        "productionRuntimePromoted": False,
        "existingCalculatorCoreUsed": checks.get("browserRuntimeTestSuite", False),
        "fileManagerBackend": "controlled-test-adapter",
        "fourAppParity": "NOT_VERIFIED",
        "expertLayoutSynthesis": "NOT_VERIFIED",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("MCEL_PHASE2B_SEMANTIC_RUNTIME " + ("PASS_BROWSER_VERTICAL_SLICE_ONLY" if passed else "INCOMPLETE"))
    for key, value in sorted(checks.items()):
        print("  " + key + ": " + ("PASS" if value else "FAIL"))
    if browser_backend:
        print("  browser backend: " + browser_backend)
    print("  hosted production runtime: NOT_VERIFIED")
    print("  four real apps parity: NOT_VERIFIED")
    print("  expert layout: NOT_VERIFIED")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(run())
