"""Three opt-in semantic workbench candidates. Never asserts production parity."""
from __future__ import annotations
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from main_computer.mcel_semantic_other_apps_host import APPS, ROUTE_PREFIX, compile_semantic_app, render_semantic_page

ROOT=Path(__file__).resolve().parents[1]
# Preserve the original direction of the Calculator integration:
# AI model -> executor tool loop -> declared MCEL intent -> existing JS core -> typed receipt.
# These suites exercise the actual dispatcher/core and fail-closed receipt validation,
# not merely the Calculator's generated presentation.
CALCULATOR_AI_TOOL_TESTS = (
    "tests/test_mcel_calculator_ai_tool.py",
    "tests/test_mcel_calculator_live_ollama_smoke.py",
    "tests/test_mcel_calculator_completion_gate.py",
)
TESTS = [
    "tests/test_mcel_migration_preservation_patch.py",
    "tests/test_mcel_semantic_other_apps.py",
    "tests/test_mcel_semantic_editor_migration.py",
    "tests/test_mcel_semantic_parity_recovery.py",
    "tests/test_mcel_calculator_expert_presentation.py",
    *CALCULATOR_AI_TOOL_TESTS,
    "tests/test_mcel_semantic_other_apps_frob_runner.py",
]

def _diagnostic_output(value: str | bytes | None) -> str:
    # TimeoutExpired can carry bytes even when text=True was requested.
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_browser_regressions(timeout_seconds: int = 180) -> tuple[dict[str, bool], list[str]]:
    """Run browser suites separately, retaining the last test's identity on timeout.

    A single global deadline used to conflate slow Windows/Playwright startup
    with an application regression and discarded test progress on timeout.
    Keep every suite mandatory; no test is skipped or treated as passed on a
    timeout, nonzero exit, or empty collection.
    """
    if timeout_seconds < 1:
        raise ValueError("suite timeout must be a positive number of seconds")
    results: dict[str, bool] = {}
    issues: list[str] = []
    for test_path in TESTS:
        suite = Path(test_path).stem
        key = f"browserSuite.{suite}"
        command = [sys.executable, "-m", "pytest", "-v", "--tb=short", test_path]
        print(f"  RUN {key}: {test_path} (timeout {timeout_seconds}s)", flush=True)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True,
                timeout=timeout_seconds, check=False,
            )
            output = _diagnostic_output(proc.stdout)
            stderr = _diagnostic_output(proc.stderr)
            # Pytest exits 0 for a complete suite. Reject an anomalous empty
            # collection so a missing/renamed test file never becomes a pass.
            passed = proc.returncode == 0 and re.search(r"\b[1-9]\d* passed\b", output) is not None
            # An AI tool suite with skipped Node/JS-core tests cannot prove
            # that AI actually called the authoritative Calculator engine.
            if test_path in CALCULATOR_AI_TOOL_TESTS and re.search(
                r"\b(?:SKIPPED|[1-9]\d* skipped)\b", output
            ):
                passed = False
            results[key] = passed
            elapsed = time.monotonic() - started
            if passed:
                match = re.search(r"\b([1-9]\d*) passed\b", output)
                print(f"  PASS {key}: {match.group(1)} tests in {elapsed:.1f}s", flush=True)
            else:
                detail = (output + "\n" + stderr).strip()[-5000:]
                issues.append(f"{key}: pytest exit {proc.returncode}; {detail}")
                print(f"  FAIL {key}: exit {proc.returncode} in {elapsed:.1f}s\n{detail}", flush=True)
        except subprocess.TimeoutExpired as exc:
            results[key] = False
            elapsed = time.monotonic() - started
            detail = (_diagnostic_output(exc.stdout) + "\n" + _diagnostic_output(exc.stderr)).strip()[-5000:]
            issues.append(f"{key}: timeout after {timeout_seconds}s; last pytest output:\n{detail or '(none)'}")
            print(f"  TIMEOUT {key}: {elapsed:.1f}s; last pytest output:\n{detail or '(none)'}", flush=True)
        except OSError as exc:
            results[key] = False
            issues.append(f"{key}: could not start pytest: {exc}")
            print(f"  FAIL {key}: could not start pytest: {exc}", flush=True)
    return results, issues


def run(hosted_base: str | None = None, suite_timeout: int = 180) -> int:
    checks = {}
    issues = []
    for name in APPS:
        try:
            ir = compile_semantic_app(name)
            sf = ir['surfaces'][0]['interfaceIntent']
            checks[f'{name}.pairedSemanticSource'] = len(ir['provenance']['frontend']['sourceFiles']) == 2
            checks[f'{name}.layoutFree'] = not ir['layouts']
            checks[f'{name}.semanticHierarchy'] = bool(sf['hierarchy'])
            checks[f'{name}.explicitAuthorityScope'] = ({e['risk'] for e in ir['effects']} ==
                ({'external-read', 'source-write', 'consequential'} if name == 'code-editor' else
                 {'external-read', 'consequential'} if name == 'task-manager' else {'external-read'}))
            checks[f'{name}.pageGenerated'] = 'McelSemanticOperationalRuntime.mount' in render_semantic_page(name)
        except Exception as exc:
            issues.append(f'{name}: {exc}')
            for attr in ('pairedSemanticSource', 'layoutFree', 'semanticHierarchy', 'explicitAuthorityScope', 'pageGenerated'):
                checks[f'{name}.{attr}'] = False
    suite_checks, suite_issues = run_browser_regressions(suite_timeout)
    checks.update(suite_checks)
    checks['browserInteractionAndRegression'] = len(suite_checks) == len(TESTS) and all(suite_checks.values())
    # Keep the AI's callable computation contract as an independent Promise
    # preservation gate. An attractive calculator UI cannot substitute for it.
    checks['calculator.aiToolPreservedByRegression'] = (
        shutil.which('node') is not None
        and all(suite_checks.get('browserSuite.' + Path(path).stem) is True
                for path in CALCULATOR_AI_TOOL_TESTS)
    )
    issues.extend(suite_issues)
    if hosted_base:
        from playwright.sync_api import sync_playwright
        from tools.mcel_phase2b_browser import launch_semantic_browser
        with sync_playwright() as p:
            browser,_=launch_semantic_browser(p)
            try:
                for name in APPS:
                    url=hosted_base.rstrip('/')+ROUTE_PREFIX+name
                    page=browser.new_page(viewport={'width':1100,'height':850})
                    try:
                        res=page.goto(url,timeout=25000)
                        checks[f'{name}.hostedRoute']=bool(res and res.status==200 and page.locator('#mcel-workbench-status').inner_text()=='Semantic candidate ready')
                    except Exception as exc:
                        checks[f'{name}.hostedRoute']=False
                        issues.append(f'{name} host: {exc}')
                    finally:page.close()
            finally:browser.close()
    good=all(checks.values())
    print('MCEL_SEMANTIC_OTHER_APPS '+('PASS_SEMANTIC_CANDIDATES_PARITY_INCOMPLETE' if good else 'INCOMPLETE'))
    for key,val in checks.items():print(f'  {key}: {"PASS" if val else "FAIL"}')
    if hosted_base is None:print('  live Windows hosted routes: NOT_VERIFIED')
    print('  File Manager: retained multi-level hierarchy, nested navigation, parent and search exercised under controlled transport; live Windows parity NOT_VERIFIED')
    print('  Task Manager: four data-backed views and five-second auto-refresh/pause browser-tested; consequential semantics declared and deterministic gate tested; live operations NOT_VERIFIED')
    print('  Code Editor: Monaco multi-tabs and guarded saves exercised; Aider job/context contract simulated; live Windows source writes and full Aider parity NOT_VERIFIED')
    print('  Calculator AI tool: model -> governed calculator.evaluate -> authoritative JS core -> typed receipt; scripted-model and integrity regressions REQUIRED; live Ollama NOT_VERIFIED by this Frob')
    print('  all three production parity and human design acceptance: NOT_VERIFIED')
    for issue in issues:print('  '+issue)
    return 0 if good else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--hosted-base')
    parser.add_argument('--suite-timeout', type=int, default=180, metavar='SECONDS',
                        help='Deadline per required browser regression suite (default: 180 seconds).')
    args=parser.parse_args()
    raise SystemExit(run(args.hosted_base, args.suite_timeout))
